"""The VMBox toolstack: VM inventory, policy enforcement, USB and switching.

VMBox specific metadata (colour, slot, policy...) is stored as VirtualBox
extradata under the ``VMBox/`` prefix, so VirtualBox stays the single
source of truth for every machine.
"""

import logging
import os
import re
import threading
import time

from .display import DisplayError
from .templates import BY_ID as TEMPLATES, COLORS
from .vbox import VBoxError

log = logging.getLogger(__name__)

P = "VMBox/"
CLIPBOARD_MODES = {
    "off": "disabled",
    "in": "hosttoguest",
    "out": "guesttohost",
    "both": "bidirectional",
}
NAME_RE = re.compile(r"^[\w][\w .()+-]{0,63}$", re.UNICODE)
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
USB_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
DISK_CTL = "SATA"

# Global VirtualBox GUI settings: no update checks, no nag dialogs, no
# automatic keyboard capture (so the VMBox switch hotkeys keep working).
GLOBAL_EXTRADATA = {
    "GUI/UpdateDate": "never",
    "GUI/SuppressMessages": "all",
    "GUI/Input/AutoCapture": "false",
    "GUI/NotificationCenter/Alignment": "Bottom",
}

# Per-VM window settings: VMBox owns the chrome, VirtualBox shows only the guest.
VM_GUI_EXTRADATA = {
    "GUI/MenuBar/Enabled": "false",
    "GUI/StatusBar/Enabled": "false",
    "GUI/ShowMiniToolBar": "false",
    "GUI/RestrictedRuntimeMenus": "All",
    "GUI/RestrictedCloseActions": "SaveState,PowerOffRestoringSnapshot,Detach",
    "GUI/AutoresizeGuest": "true",
    "GUI/Fullscreen": "false",
    "GUI/Seamless": "false",
}

STATE_MAP = {
    "poweroff": "stopped", "aborted": "stopped", "aborted-saved": "saved",
    "running": "running", "paused": "paused", "saved": "saved",
    "starting": "busy", "stopping": "busy", "saving": "busy", "restoring": "busy",
    "settingup": "busy", "deletingsnapshotlive": "busy", "livesnapshotting": "busy",
}


class ToolstackError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def _flag(value):
    return "1" if value else "0"


class Toolstack:
    def __init__(self, vbox, display, iso_dir, cache_ttl=1.5):
        self.vbox = vbox
        self.display = display
        self.iso_dir = iso_dir
        self.cache_ttl = cache_ttl
        self.lock = threading.RLock()
        self._cache = None
        self._usb_failures = {}
        self._frontend_launched = {}
        self.is_locked = lambda: False

    # ------------------------------------------------------------------ #
    # inventory
    # ------------------------------------------------------------------ #
    def invalidate(self):
        self._cache = None

    def apply_global_settings(self):
        for key, value in GLOBAL_EXTRADATA.items():
            try:
                self.vbox.set_extradata("global", key, value)
            except VBoxError as exc:
                log.warning("could not set %s: %s", key, exc)

    def _describe(self, uuid, info, extra):
        def x(key, default=None):
            return extra.get(P + key, default)

        state = STATE_MAP.get(info.get("VMState", "poweroff"), "busy")
        disk = info.get("%s-0-0" % DISK_CTL, "none")
        dvd = info.get("%s-1-0" % DISK_CTL, "none")
        nic = info.get("nic1", "none")
        if nic == "bridged":
            network = "bridged:" + info.get("bridgeadapter1", "")
        elif nic == "intnet":
            network = "intnet:" + info.get("intnet1", "")
        elif nic == "hostonly":
            network = "hostonly:" + info.get("hostonlyadapter1", "")
        else:
            network = nic if nic in ("nat", "none") else "none"
        sticky = [s for s in x("USB/Sticky", "").split(",") if s]
        return {
            "uuid": uuid,
            "name": info.get("name", "?"),
            "description": info.get("description", ""),
            "state": state,
            "running": state in ("running", "paused"),
            "type": x("Type", "user"),
            "template": x("Template", ""),
            "ostype": info.get("ostype", ""),
            "color": x("Color", COLORS[0]),
            "slot": int(x("Slot", "0") or 0),
            "hidden": _bool(x("Hidden")),
            "start_on_boot": _bool(x("StartOnBoot")),
            "resume_on_boot": _bool(x("ResumeOnBoot")),
            "disposable": _bool(x("Disposable")),
            "memory": int(info.get("memory", 0) or 0),
            "cpus": int(info.get("cpus", 1) or 1),
            "firmware": info.get("firmware", "BIOS").lower(),
            "accelerate3d": info.get("accelerate3d", "off") == "on",
            "vram": int(info.get("vram", 0) or 0),
            "network": network,
            "disk": "" if disk in ("none", "emptydrive") else disk,
            "iso": "" if dvd in ("none", "emptydrive") else dvd,
            "policy": {
                "usb": _bool(x("Policy/USB"), True),
                "cd": _bool(x("Policy/CD"), True),
                "audio": _bool(x("Policy/Audio"), True),
                "clipboard": x("Policy/Clipboard", "off"),
            },
            "sticky_usb": sticky,
            "usb_attached": sorted({v for k, v in info.items() if k.startswith("USBAttachedUUID")}),
            "managed": _bool(x("Managed")),
        }

    def list_vms(self, include_hidden=True):
        with self.lock:
            now = time.monotonic()
            if self._cache and now - self._cache[0] < self.cache_ttl:
                vms = self._cache[1]
            else:
                vms = []
                for _name, uuid in self.vbox.list_vms():
                    try:
                        vms.append(self._describe(uuid, self.vbox.vm_info(uuid), self.vbox.extradata(uuid, P)))
                    except VBoxError as exc:
                        log.warning("skipping VM %s: %s", uuid, exc)
                vms.sort(key=lambda v: (v["type"] != "user", v["slot"] or 99, v["name"].lower()))
                self._cache = (now, vms)
        return [v for v in vms if include_hidden or not v["hidden"]]

    def get_vm(self, uuid):
        for vm in self.list_vms():
            if vm["uuid"] == uuid:
                return vm
        raise ToolstackError("Unbekannte VM", 404)

    def find_vm(self, ref):
        """Resolve a VM by uuid, slot number or (case insensitive) name."""
        vms = self.list_vms()
        for vm in vms:
            if vm["uuid"] == ref:
                return vm
        if str(ref).isdigit():
            for vm in vms:
                if vm["slot"] == int(ref):
                    return vm
        for vm in vms:
            if vm["name"].lower() == str(ref).lower():
                return vm
        raise ToolstackError("Unbekannte VM: %s" % ref, 404)

    # ------------------------------------------------------------------ #
    # creation / modification
    # ------------------------------------------------------------------ #
    def _validate_name(self, name, exclude=None):
        if not isinstance(name, str) or not NAME_RE.match(name.strip()):
            raise ToolstackError("Ungültiger Name (1-64 Zeichen, Buchstaben, Ziffern, Leerzeichen, . _ - ( ) +)")
        name = name.strip()
        for vm in self.list_vms():
            if vm["name"].lower() == name.lower() and vm["uuid"] != exclude:
                raise ToolstackError("Es gibt bereits eine VM mit diesem Namen", 409)
        return name

    def _validate_slot(self, slot, exclude=None):
        slot = int(slot or 0)
        if not 0 <= slot <= 9:
            raise ToolstackError("Der Slot muss zwischen 1 und 9 liegen (0 = keiner)")
        if slot:
            for vm in self.list_vms():
                if vm["slot"] == slot and vm["uuid"] != exclude:
                    raise ToolstackError("Slot %d ist bereits durch „%s“ belegt" % (slot, vm["name"]), 409)
        return slot

    def _free_slot(self):
        used = {vm["slot"] for vm in self.list_vms()}
        for slot in range(1, 10):
            if slot not in used:
                return slot
        return 0

    def _resolve_iso(self, iso):
        if not iso:
            return ""
        path = iso if os.path.isabs(iso) else os.path.join(self.iso_dir, iso)
        path = os.path.realpath(path)
        if not path.startswith(os.path.realpath(self.iso_dir) + os.sep) or not os.path.isfile(path):
            raise ToolstackError("ISO-Abbild nicht gefunden: %s" % iso)
        return path

    def _network_args(self, network):
        network = network or "nat"
        kind, _, arg = network.partition(":")
        if kind == "nat":
            return ["--nic1", "nat", "--nat-localhostreachable1", "off"]
        if kind == "none":
            return ["--nic1", "none"]
        if kind == "bridged" and arg:
            return ["--nic1", "bridged", "--bridge-adapter1", arg]
        if kind == "intnet" and re.match(r"^[\w.-]{1,64}$", arg or ""):
            return ["--nic1", "intnet", "--intnet1", arg]
        if kind == "hostonly" and arg:
            return ["--nic1", "hostonly", "--host-only-adapter1", arg]
        raise ToolstackError("Ungültige Netzwerkzuweisung: %s" % network)

    @staticmethod
    def _usb_kind(template_id):
        return TEMPLATES.get(template_id, {}).get("usb", "xhci")

    def _policy_args(self, policy, usb_kind="xhci"):
        clip = CLIPBOARD_MODES.get(policy.get("clipboard", "off"))
        if clip is None:
            raise ToolstackError("Ungültiger Zwischenablage-Modus")
        usb = policy.get("usb", True)
        # USB 3.0 (xHCI) for current guests, USB 2.0 (EHCI + OHCI) for e.g. Windows 7.
        xhci = usb and usb_kind == "xhci"
        ehci = usb and usb_kind == "ehci"
        return [
            "--usb-xhci", "on" if xhci else "off",
            "--usb-ehci", "on" if ehci else "off",
            "--usb-ohci", "on" if ehci else "off",
            "--audio-enabled", "on" if policy.get("audio", True) else "off",
            "--clipboard-mode", clip,
            "--drag-and-drop", "disabled",
        ]

    def _hardening_args(self):
        # Never expose a remote display, never let a NAT guest reach host services.
        return ["--vrde", "off"]

    def create_vm(self, spec):
        template = TEMPLATES.get(spec.get("template") or "linux")
        if template is None:
            raise ToolstackError("Unbekannte Vorlage")
        with self.lock:
            name = self._validate_name(spec.get("name"))
            slot = self._validate_slot(spec["slot"]) if spec.get("slot") else self._free_slot()
            memory = int(spec.get("memory") or template["memory"])
            cpus = int(spec.get("cpus") or template["cpus"])
            disk_gb = int(spec.get("disk_gb") or template["disk_gb"])
            if not 256 <= memory <= 262144:
                raise ToolstackError("Arbeitsspeicher muss zwischen 256 MB und 256 GB liegen")
            if not 1 <= cpus <= 64:
                raise ToolstackError("Ungültige Anzahl an CPUs")
            if not 1 <= disk_gb <= 8192:
                raise ToolstackError("Ungültige Festplattengröße")
            color = spec.get("color") or COLORS[((slot or 1) - 1) % len(COLORS)]
            if not HEX_COLOR_RE.match(color):
                raise ToolstackError("Ungültige Farbe")
            vmtype = template.get("type", "user")
            policy = {"usb": True, "cd": True, "audio": vmtype == "user", "clipboard": "off"}
            policy.update(spec.get("policy") or {})
            iso = self._resolve_iso(spec.get("iso"))
            network_args = self._network_args(spec.get("network") or "nat")
            policy_args = self._policy_args(policy, template.get("usb", "xhci"))

            uuid = self.vbox.create_vm(name, template["ostype"])
            self.invalidate()
            try:
                self.vbox.modify(
                    uuid,
                    "--memory", memory, "--cpus", cpus,
                    "--firmware", template["firmware"],
                    "--graphicscontroller", template["graphics"], "--vram", template["vram"],
                    "--accelerate-3d", "on" if template.get("accelerate3d") else "off",
                    "--nictype1", template.get("nic", "82540EM"),
                    "--ioapic", "on", "--rtc-use-utc", "on" if template["family"] != "windows" else "off",
                    "--mouse", "usbtablet",
                    "--description", str(spec.get("description", ""))[:500],
                    *network_args, *policy_args, *self._hardening_args(),
                )
                if template.get("tpm"):
                    self.vbox.modify(uuid, "--tpm-type", "2.0")
                cfg = self.vbox.vm_info(uuid).get("CfgFile", "")
                vm_dir = os.path.dirname(cfg) if cfg else os.path.join(os.path.dirname(self.iso_dir), "vms", name)
                disk = os.path.join(vm_dir, "%s.vdi" % name)
                self.vbox.create_disk(disk, disk_gb * 1024)
                self.vbox.storagectl(uuid, DISK_CTL, "sata", controller="IntelAhci", ports=2)
                self.vbox.storageattach(uuid, DISK_CTL, 0, "hdd", disk)
                if policy.get("cd", True):
                    self.vbox.storageattach(uuid, DISK_CTL, 1, "dvddrive", iso or "emptydrive")
                if template.get("secure_boot"):
                    self.vbox.modifynvram(uuid, "inituefivarstore")
                    self.vbox.modifynvram(uuid, "enrollmssignatures")
                    self.vbox.modifynvram(uuid, "enrollorclpk")
                meta = {
                    "Managed": "1", "Type": vmtype, "Template": template["id"], "Color": color,
                    "Slot": str(slot), "Hidden": _flag(vmtype == "service"),
                    "StartOnBoot": _flag(spec.get("start_on_boot", vmtype == "service")),
                    "Disposable": "0",
                    "Policy/USB": _flag(policy.get("usb", True)), "Policy/CD": _flag(policy.get("cd", True)),
                    "Policy/Audio": _flag(policy.get("audio", True)),
                    "Policy/Clipboard": policy.get("clipboard", "off"),
                }
                for key, value in meta.items():
                    self.vbox.set_extradata(uuid, P + key, value)
                for key, value in VM_GUI_EXTRADATA.items():
                    self.vbox.set_extradata(uuid, key, value)
            except (VBoxError, ToolstackError):
                log.exception("VM creation failed, cleaning up %s", uuid)
                try:
                    self.vbox.unregister(uuid, delete=True)
                except VBoxError:
                    log.exception("cleanup of %s failed", uuid)
                raise
            finally:
                self.invalidate()
        return self.get_vm(uuid)

    def update_vm(self, uuid, changes):
        with self.lock:
            vm = self.get_vm(uuid)
            stopped = vm["state"] in ("stopped",)
            meta, modify = {}, []

            def needs_stop(what):
                if not stopped:
                    raise ToolstackError("%s kann nur bei ausgeschalteter VM geändert werden" % what, 409)

            if "name" in changes and changes["name"] != vm["name"]:
                needs_stop("Der Name")
                modify += ["--name", self._validate_name(changes["name"], exclude=uuid)]
            if "description" in changes:
                needs_stop("Die Beschreibung")
                modify += ["--description", str(changes["description"])[:500]]
            if "memory" in changes and int(changes["memory"]) != vm["memory"]:
                needs_stop("Der Arbeitsspeicher")
                if not 256 <= int(changes["memory"]) <= 262144:
                    raise ToolstackError("Arbeitsspeicher muss zwischen 256 MB und 256 GB liegen")
                modify += ["--memory", int(changes["memory"])]
            if "cpus" in changes and int(changes["cpus"]) != vm["cpus"]:
                needs_stop("Die CPU-Anzahl")
                if not 1 <= int(changes["cpus"]) <= 64:
                    raise ToolstackError("Ungültige Anzahl an CPUs")
                modify += ["--cpus", int(changes["cpus"])]
            if "accelerate3d" in changes and bool(changes["accelerate3d"]) != vm["accelerate3d"]:
                needs_stop("Die 3D-Beschleunigung")
                modify += ["--accelerate-3d", "on" if changes["accelerate3d"] else "off"]
                if changes["accelerate3d"] and vm["vram"] < 128:
                    modify += ["--vram", 128]
            if "color" in changes:
                if not HEX_COLOR_RE.match(str(changes["color"])):
                    raise ToolstackError("Ungültige Farbe")
                meta["Color"] = changes["color"]
            if "slot" in changes:
                meta["Slot"] = str(self._validate_slot(changes["slot"], exclude=uuid))
            if "hidden" in changes:
                meta["Hidden"] = _flag(changes["hidden"])
            if "start_on_boot" in changes:
                meta["StartOnBoot"] = _flag(changes["start_on_boot"])
            if "type" in changes:
                if changes["type"] not in ("user", "service"):
                    raise ToolstackError("Ungültiger VM-Typ")
                meta["Type"] = changes["type"]
            if "network" in changes and changes["network"] != vm["network"]:
                args = self._network_args(changes["network"])
                if stopped:
                    modify += args
                else:
                    self._live_network(uuid, changes["network"])
            if "policy" in changes:
                policy = dict(vm["policy"])
                policy.update(changes["policy"] or {})
                hw_changed = any(policy[k] != vm["policy"][k] for k in ("usb", "audio", "cd"))
                if hw_changed:
                    needs_stop("Diese Richtlinie")
                    modify += self._policy_args(policy, self._usb_kind(vm["template"]))
                elif policy["clipboard"] != vm["policy"]["clipboard"]:
                    if policy["clipboard"] not in CLIPBOARD_MODES:
                        raise ToolstackError("Ungültiger Zwischenablage-Modus")
                    if stopped:
                        modify += ["--clipboard-mode", CLIPBOARD_MODES[policy["clipboard"]]]
                    else:
                        self.vbox.control(uuid, "clipboard", "mode", CLIPBOARD_MODES[policy["clipboard"]])
                if policy["cd"] != vm["policy"]["cd"]:
                    if policy["cd"]:
                        self.vbox.storageattach(uuid, DISK_CTL, 1, "dvddrive", "emptydrive")
                    else:
                        self.vbox.storageattach(uuid, DISK_CTL, 1, "dvddrive", "none")
                meta.update({
                    "Policy/USB": _flag(policy["usb"]), "Policy/CD": _flag(policy["cd"]),
                    "Policy/Audio": _flag(policy["audio"]), "Policy/Clipboard": policy["clipboard"],
                })
            if "disposable" in changes and bool(changes["disposable"]) != vm["disposable"]:
                needs_stop("Der Wegwerf-Modus")
                if not vm["disk"]:
                    raise ToolstackError("Die VM hat keine Festplatte")
                self.vbox.storageattach(uuid, DISK_CTL, 0, "hdd", "none")
                self.vbox.run("modifymedium", "disk", vm["disk"], "--type",
                              "immutable" if changes["disposable"] else "normal")
                self.vbox.storageattach(uuid, DISK_CTL, 0, "hdd", vm["disk"])
                meta["Disposable"] = _flag(changes["disposable"])
            if "sticky_usb" in changes:
                ids = [str(s).lower() for s in changes["sticky_usb"] or []]
                if not all(USB_ID_RE.match(s) for s in ids):
                    raise ToolstackError("Ungültige USB-ID (erwartet vvvv:pppp)")
                meta["USB/Sticky"] = ",".join(sorted(set(ids)))
            if "iso" in changes:
                if not vm["policy"]["cd"] and changes["iso"]:
                    raise ToolstackError("CD/DVD ist für diese VM per Richtlinie deaktiviert", 403)
                if vm["policy"]["cd"]:
                    medium = self._resolve_iso(changes["iso"]) or "emptydrive"
                    self.vbox.storageattach(uuid, DISK_CTL, 1, "dvddrive", medium)

            if modify:
                self.vbox.modify(uuid, *modify)
            for key, value in meta.items():
                self.vbox.set_extradata(uuid, P + key, value)
            self.invalidate()
        return self.get_vm(uuid)

    def _live_network(self, uuid, network):
        kind, _, arg = network.partition(":")
        if kind == "none":
            self.vbox.control(uuid, "setlinkstate1", "off")
            return
        args = {"nat": ["nat"], "bridged": ["bridged", arg], "intnet": ["intnet", arg],
                "hostonly": ["hostonly", arg]}.get(kind)
        if args is None:
            raise ToolstackError("Ungültige Netzwerkzuweisung")
        self.vbox.control(uuid, "nic1", *args)
        self.vbox.control(uuid, "setlinkstate1", "on")

    def delete_vm(self, uuid):
        with self.lock:
            vm = self.get_vm(uuid)
            if vm["state"] not in ("stopped", "saved"):
                raise ToolstackError("Die VM muss zuerst ausgeschaltet werden", 409)
            if vm["state"] == "saved":
                self.vbox.run("discardstate", uuid)
            self.vbox.unregister(uuid, delete=True)
            self.invalidate()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def _enforce(self, vm):
        """Re-apply hardening and policy before every start, in case somebody
        changed the machine behind our back with VBoxManage."""
        args = self._hardening_args() + self._policy_args(vm["policy"], self._usb_kind(vm["template"]))
        if vm["network"] == "nat":
            args += ["--nat-localhostreachable1", "off"]
        self.vbox.modify(vm["uuid"], *args)
        if not vm["policy"]["cd"] and vm["iso"]:
            self.vbox.storageattach(vm["uuid"], DISK_CTL, 1, "dvddrive", "none")
        for key, value in VM_GUI_EXTRADATA.items():
            self.vbox.set_extradata(vm["uuid"], key, value)

    def action(self, uuid, action):
        with self.lock:
            vm = self.get_vm(uuid)
            state = vm["state"]
            if action == "start":
                if self.is_locked() and vm["type"] == "user":
                    raise ToolstackError("Der Bildschirm ist gesperrt", 423)
                if state == "paused":
                    self.vbox.control(uuid, "resume")
                elif state in ("stopped", "saved"):
                    if state == "stopped":
                        self._enforce(vm)
                    self.vbox.start(uuid, headless=vm["type"] == "service")
                    self._frontend_launched[uuid] = time.monotonic()
                    if vm["type"] == "user":
                        threading.Thread(target=self._focus_when_ready, args=(uuid,), daemon=True).start()
                else:
                    raise ToolstackError("Die VM läuft bereits", 409)
            elif action in ("shutdown", "poweroff", "pause", "resume", "reboot", "save"):
                if action == "resume":
                    if state != "paused":
                        raise ToolstackError("Die VM ist nicht pausiert", 409)
                elif state not in ("running", "paused"):
                    raise ToolstackError("Die VM läuft nicht", 409)
                command = {"shutdown": "acpipowerbutton", "poweroff": "poweroff", "pause": "pause",
                           "resume": "resume", "reboot": "reset", "save": "savestate"}[action]
                self.vbox.control(uuid, command)
                if action in ("shutdown", "poweroff", "save"):
                    self._show_ui_if_focused(uuid)
            elif action == "switch":
                self.switch_to(uuid)
            else:
                raise ToolstackError("Unbekannte Aktion: %s" % action, 404)
            self.invalidate()

    def _focus_when_ready(self, uuid, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_locked():
                return
            try:
                self.display.show_vm(uuid)
                return
            except DisplayError:
                time.sleep(0.5)

    def _show_ui_if_focused(self, uuid):
        try:
            if self.display.focused([uuid]) == uuid:
                self.display.show_ui()
        except DisplayError:
            pass

    def autostart(self, types=("service", "user")):
        """Start VMs flagged "start on boot" and resume the ones that were saved
        when the host shut down: service VMs first, then by slot."""
        vms = [v for v in self.list_vms() if v["type"] in types and (v["start_on_boot"] or v["resume_on_boot"])
               and v["state"] in ("stopped", "saved")]
        vms.sort(key=lambda v: (v["type"] != "service", v["slot"] or 99))
        for vm in vms:
            if vm["resume_on_boot"]:
                self.vbox.set_extradata(vm["uuid"], P + "ResumeOnBoot", None)
                self.invalidate()
            if vm["type"] == "user":
                for _ in range(60):
                    if self.display.available():
                        break
                    time.sleep(1)
            try:
                log.info("autostart %s", vm["name"])
                self.action(vm["uuid"], "start")
            except (VBoxError, ToolstackError) as exc:
                log.error("autostart of %s failed: %s", vm["name"], exc)

    def save_all(self):
        """Save the state of every running VM (used when the host shuts down)."""
        saved = []
        with self.lock:
            for vm in self.list_vms():
                if not vm["running"]:
                    continue
                try:
                    self.vbox.control(vm["uuid"], "savestate")
                    self.vbox.set_extradata(vm["uuid"], P + "ResumeOnBoot", "1")
                    saved.append(vm["name"])
                except VBoxError as exc:
                    log.error("could not save %s: %s", vm["name"], exc)
            self.invalidate()
        return saved

    def ensure_frontends(self, grace=20):
        """Give every running user VM a window again, e.g. after the X session restarted."""
        now = time.monotonic()
        for vm in self.list_vms():
            if vm["type"] != "user" or not vm["running"]:
                continue
            if now - self._frontend_launched.get(vm["uuid"], 0) < grace:
                continue
            if not self.display.has_window(vm["uuid"]):
                log.info("re-attaching window for %s", vm["name"])
                self._frontend_launched[vm["uuid"]] = now
                self.display.launch_frontend(vm["uuid"])

    # ------------------------------------------------------------------ #
    # switching
    # ------------------------------------------------------------------ #
    def focus(self):
        running = [v["uuid"] for v in self.list_vms() if v["running"] and v["type"] == "user"]
        try:
            return self.display.focused(running)
        except DisplayError:
            return None

    def switch_to(self, ref):
        if self.is_locked():
            raise ToolstackError("Der Bildschirm ist gesperrt", 423)
        vm = self.find_vm(ref)
        if vm["type"] != "user":
            raise ToolstackError("Service-VMs haben keine Anzeige", 409)
        if not vm["running"]:
            raise ToolstackError("„%s“ läuft nicht" % vm["name"], 409)
        try:
            self.display.show_vm(vm["uuid"])
        except DisplayError as exc:
            raise ToolstackError(str(exc), 409) from exc

    def switch_ui(self):
        try:
            self.display.show_ui()
        except DisplayError as exc:
            raise ToolstackError(str(exc), 409) from exc

    def switch_cycle(self, step):
        targets = [None] + [v["uuid"] for v in self.list_vms() if v["running"] and v["type"] == "user"]
        current = self.focus()
        index = targets.index(current) if current in targets else 0
        target = targets[(index + step) % len(targets)]
        if target is None:
            self.switch_ui()
        else:
            self.switch_to(target)

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #
    def snapshots(self, uuid):
        self.get_vm(uuid)
        return self.vbox.snapshots(uuid)

    def take_snapshot(self, uuid, name, description=""):
        vm = self.get_vm(uuid)
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ToolstackError("Ungültiger Snapshot-Name")
        if vm["disposable"]:
            raise ToolstackError("Wegwerf-VMs unterstützen keine Snapshots", 409)
        self.vbox.take_snapshot(uuid, name.strip(), description, live=vm["running"])
        self.invalidate()

    def _snapshot_ref(self, uuid, snap):
        for s in self.vbox.snapshots(uuid):
            if snap in (s["uuid"], s["name"]):
                return s["uuid"]
        raise ToolstackError("Unbekannter Snapshot", 404)

    def restore_snapshot(self, uuid, snap):
        vm = self.get_vm(uuid)
        if vm["state"] not in ("stopped", "saved"):
            raise ToolstackError("Die VM muss zum Wiederherstellen ausgeschaltet sein", 409)
        self.vbox.restore_snapshot(uuid, self._snapshot_ref(uuid, snap))
        self.invalidate()

    def delete_snapshot(self, uuid, snap):
        self.get_vm(uuid)
        self.vbox.delete_snapshot(uuid, self._snapshot_ref(uuid, snap))
        self.invalidate()

    # ------------------------------------------------------------------ #
    # USB
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_hid(device):
        """Keyboards and mice stay with the host unless explicitly forced -
        handing them to a VM would take the input away from VMBox itself."""
        addr = device.get("address", "")
        if not addr.startswith("sysfs:"):
            return False
        path = addr[len("sysfs:"):].split("//device:")[0]
        try:
            for entry in os.listdir(path):
                cls = os.path.join(path, entry, "bInterfaceClass")
                if os.path.exists(cls):
                    with open(cls) as fh:
                        if fh.read().strip() == "03":
                            return True
        except OSError:
            pass
        return False

    def usb_devices(self):
        owners = {}
        for vm in self.list_vms():
            for dev in vm["usb_attached"]:
                owners[dev] = vm["uuid"]
        devices = []
        for dev in self.vbox.usb_devices():
            dev = dict(dev)
            dev["id"] = "%s:%s" % (dev["vendor_id"], dev["product_id"])
            dev["vm"] = owners.get(dev["uuid"])
            dev["hid"] = self._is_hid(dev)
            dev["sticky_vms"] = [v["uuid"] for v in self.list_vms() if dev["id"] in v["sticky_usb"]]
            devices.append(dev)
        return devices

    def _usb_device(self, dev_uuid):
        for dev in self.usb_devices():
            if dev["uuid"] == dev_uuid:
                return dev
        raise ToolstackError("Unbekanntes USB-Gerät", 404)

    def usb_assign(self, dev_uuid, vm_uuid, force=False):
        with self.lock:
            dev = self._usb_device(dev_uuid)
            vm = self.get_vm(vm_uuid)
            if not vm["running"]:
                raise ToolstackError("Die VM muss laufen, um ein USB-Gerät zuzuweisen", 409)
            if not vm["policy"]["usb"]:
                raise ToolstackError("USB ist für „%s“ per Richtlinie deaktiviert" % vm["name"], 403)
            if dev["hid"] and not force:
                raise ToolstackError("Eingabegeräte bleiben beim Host (mit force erzwingbar)", 403)
            if dev["vm"] == vm_uuid:
                return
            if dev["vm"]:
                self.vbox.control(dev["vm"], "usbdetach", dev_uuid)
            self.vbox.control(vm_uuid, "usbattach", dev_uuid)
            self.invalidate()

    def usb_release(self, dev_uuid):
        with self.lock:
            dev = self._usb_device(dev_uuid)
            if dev["vm"]:
                self.vbox.control(dev["vm"], "usbdetach", dev_uuid)
                self.invalidate()

    def usb_set_sticky(self, dev_uuid, vm_uuid, sticky):
        dev = self._usb_device(dev_uuid)
        vm = self.get_vm(vm_uuid)
        ids = set(vm["sticky_usb"])
        (ids.add if sticky else ids.discard)(dev["id"])
        return self.update_vm(vm_uuid, {"sticky_usb": sorted(ids)})

    def usb_autoassign(self):
        """Attach newly plugged devices that are "sticky" to a running VM."""
        vms = [v for v in self.list_vms() if v["running"] and v["sticky_usb"] and v["policy"]["usb"]]
        if not vms:
            return
        now = time.monotonic()
        for dev in self.vbox.usb_devices():
            if dev["state"] == "captured":
                continue
            ident = "%s:%s" % (dev["vendor_id"], dev["product_id"])
            for vm in vms:
                key = (dev["uuid"], vm["uuid"])
                if ident in vm["sticky_usb"] and self._usb_failures.get(key, 0) < now:
                    try:
                        self.vbox.control(vm["uuid"], "usbattach", dev["uuid"])
                        log.info("auto-assigned USB %s to %s", ident, vm["name"])
                        self.invalidate()
                    except VBoxError as exc:
                        log.warning("auto-assign of %s failed: %s", ident, exc)
                        self._usb_failures[key] = now + 60
                    break

    # ------------------------------------------------------------------ #
    # networks & media
    # ------------------------------------------------------------------ #
    def networks(self):
        options = [{"id": "nat", "label": "NAT (isoliert, empfohlen)"},
                   {"id": "none", "label": "Kein Netzwerk"}]
        try:
            for iface in self.vbox.bridged_interfaces():
                options.append({"id": "bridged:" + iface["name"],
                                "label": "Netzwerkbrücke: %s%s" % (iface["name"], " (WLAN)" if iface["wireless"] else "")})
        except VBoxError as exc:
            log.warning("could not list bridged interfaces: %s", exc)
        intnets = {"vmbox-lan"}
        try:
            intnets.update(self.vbox.internal_networks())
        except VBoxError:
            pass
        for vm in self.list_vms():
            if vm["network"].startswith("intnet:"):
                intnets.add(vm["network"].split(":", 1)[1])
        for net in sorted(intnets):
            options.append({"id": "intnet:" + net, "label": "Internes Netz: " + net})
        return options

    def isos(self):
        result = []
        try:
            names = sorted(os.listdir(self.iso_dir))
        except OSError:
            names = []
        for name in names:
            path = os.path.join(self.iso_dir, name)
            if name.lower().endswith(".iso") and os.path.isfile(path):
                result.append({"name": name, "size": os.path.getsize(path)})
        return result

    @staticmethod
    def safe_iso_name(name):
        name = os.path.basename(str(name or "")).strip()
        name = re.sub(r"[^\w.+-]", "_", name)
        if not name or name.startswith(".") or not name.lower().endswith(".iso"):
            raise ToolstackError("Der Dateiname muss auf .iso enden")
        return name

    def delete_iso(self, name):
        name = self.safe_iso_name(name)
        path = os.path.join(self.iso_dir, name)
        for vm in self.list_vms():
            if vm["iso"] == path:
                raise ToolstackError("Das Abbild ist noch in „%s“ eingelegt" % vm["name"], 409)
        try:
            os.unlink(path)
        except FileNotFoundError as exc:
            raise ToolstackError("Abbild nicht gefunden", 404) from exc
        try:
            self.vbox.run("closemedium", "dvd", path, check=False)
        except VBoxError:
            pass
