"""A simulated ``VBoxManage`` for development, demos and the test-suite.

It understands the subset of VBoxManage commands that vmboxd uses and
prints output in the same formats, so the real parsing code is exercised.
Start vmboxd with ``--backend fake`` to explore the UI without VirtualBox.
"""

import shlex
import threading
import uuid as uuidlib


class FakeError(Exception):
    pass


def _q(value):
    return '"%s"' % str(value).replace('"', '\\"')


class FakeVBoxManage:
    def __init__(self, seed=False, base="/tmp/vmbox-fake"):
        self.base = base
        self.vms = {}
        self.order = []
        self.global_extradata = {}
        self.media = set()
        self.calls = []
        self.lock = threading.RLock()
        self.usb = [
            {"UUID": "8a6c2f8e-6d3b-4a59-9b8e-1f2a3b4c5d01", "VendorId": "0x0781 (0781)",
             "ProductId": "0x5583 (5583)", "Manufacturer": "SanDisk", "Product": "Ultra Fit",
             "SerialNumber": "4C530001", "Address": "sysfs:/nonexistent/1-1//device:/dev/vboxusb/001/002",
             "Current State": "Busy"},
            {"UUID": "8a6c2f8e-6d3b-4a59-9b8e-1f2a3b4c5d02", "VendorId": "0x1050 (1050)",
             "ProductId": "0x0407 (0407)", "Manufacturer": "Yubico", "Product": "YubiKey OTP+FIDO+CCID",
             "SerialNumber": "", "Address": "sysfs:/nonexistent/1-2//device:/dev/vboxusb/001/003",
             "Current State": "Available"},
            {"UUID": "8a6c2f8e-6d3b-4a59-9b8e-1f2a3b4c5d03", "VendorId": "0x046d (046d)",
             "ProductId": "0x0825 (0825)", "Manufacturer": "Logitech", "Product": "Webcam C270",
             "SerialNumber": "", "Address": "sysfs:/nonexistent/1-3//device:/dev/vboxusb/001/004",
             "Current State": "Available"},
        ]
        if seed:
            self._seed()

    # ------------------------------------------------------------------ #
    def _seed(self):
        demo = [
            ("Büro", "Windows11_64", 8192, 4, "running", {"Color": "#1e88e5", "Slot": "1", "Template": "windows11",
             "Policy/Clipboard": "both"}),
            ("Online-Banking", "Ubuntu_64", 4096, 2, "poweroff", {"Color": "#43a047", "Slot": "2",
             "Template": "ubuntu", "Disposable": "1", "Policy/USB": "0"}),
            ("Entwicklung", "Fedora_64", 8192, 4, "running", {"Color": "#fb8c00", "Slot": "3", "Template": "fedora",
             "USB/Sticky": "1050:0407"}),
            ("Privat", "Windows10_64", 4096, 2, "saved", {"Color": "#8e24aa", "Slot": "4", "Template": "windows10"}),
            ("Netzwerk-VM", "Linux_64", 512, 1, "running", {"Color": "#00acc1", "Slot": "0", "Template": "service",
             "Type": "service", "Hidden": "1", "StartOnBoot": "1"}),
        ]
        for name, ostype, mem, cpus, state, meta in demo:
            uid = self._createvm(name, ostype)
            vm = self.vms[uid]
            vm["info"].update({"memory": str(mem), "cpus": str(cpus), "VMState": state,
                               "accelerate3d": "on" if meta.get("Type") != "service" else "off",
                               "vram": "256" if ostype.startswith("Windows") else "128",
                               "SATA-0-0": "%s/%s/%s.vdi" % (self.base, name, name), "SATA-1-0": "emptydrive"})
            if name == "Online-Banking":
                vm["info"]["nic1"] = "intnet"
                vm["info"]["intnet1"] = "vmbox-lan"
            base = {"Managed": "1", "Type": "user", "Hidden": "0", "StartOnBoot": "0", "Disposable": "0",
                    "Policy/USB": "1", "Policy/CD": "1", "Policy/Audio": "1", "Policy/Clipboard": "off"}
            base.update(meta)
            for key, value in base.items():
                vm["extradata"]["VMBox/" + key] = value
        office = self._vm("Büro")
        office["snapshots"] = [{"name": "Frisch installiert", "uuid": str(uuidlib.uuid4()), "description": "",
                                "children": [{"name": "Nach Office-Installation", "uuid": str(uuidlib.uuid4()),
                                              "description": "Office 2024", "children": []}]}]
        office["current"] = office["snapshots"][0]["children"][0]["uuid"]
        self.usb[1]["Current State"] = "Captured"
        self._vm("Entwicklung")["info"]["USBAttachedUUID1"] = self.usb[1]["UUID"]

    def _createvm(self, name, ostype):
        uid = str(uuidlib.uuid4())
        self.vms[uid] = {
            "info": {"name": name, "UUID": uid, "ostype": ostype, "memory": "128", "cpus": "1",
                     "VMState": "poweroff", "firmware": "BIOS", "nic1": "nat", "description": "",
                     "CfgFile": "%s/%s/%s.vbox" % (self.base, name, name)},
            "extradata": {}, "snapshots": [], "current": None, "controllers": set(),
        }
        self.order.append(uid)
        return uid

    def _vm(self, ref):
        for uid, vm in self.vms.items():
            if ref in (uid, vm["info"]["name"]):
                return vm
        raise FakeError("Could not find a registered machine named '%s'" % ref)

    def _require_off(self, vm):
        if vm["info"]["VMState"] in ("running", "paused"):
            raise FakeError("The machine '%s' is already locked for a session (or being unlocked)"
                            % vm["info"]["name"])

    # ------------------------------------------------------------------ #
    def __call__(self, argv, env=None, timeout=None):
        args = [a for a in argv[1:] if a != "-q"]
        with self.lock:
            self.calls.append(args)
            try:
                return 0, self._dispatch(args) or "", ""
            except FakeError as exc:
                return 1, "", "VBoxManage: error: %s\n" % exc

    def _dispatch(self, args):
        cmd, rest = args[0], args[1:]
        handler = getattr(self, "cmd_" + cmd.lstrip("-").replace("-", "_"), None)
        if handler is None:
            raise FakeError("unsupported command %s" % shlex.join(args))
        return handler(rest)

    def cmd_version(self, _):
        return "7.2.4r170995\n"

    def cmd_list(self, rest):
        what = rest[0]
        if what == "vms":
            return "".join('"%s" {%s}\n' % (self.vms[u]["info"]["name"], u) for u in self.order)
        if what == "runningvms":
            return "".join('"%s" {%s}\n' % (self.vms[u]["info"]["name"], u) for u in self.order
                           if self.vms[u]["info"]["VMState"] in ("running", "paused"))
        if what == "usbhost":
            out = "Host USB Devices:\n\n"
            for dev in self.usb:
                out += "".join("%-20s%s\n" % (k + ":", v) for k, v in dev.items()) + "\n"
            return out
        if what == "bridgedifs":
            return ("Name:            enp3s0\nStatus:          Up\nMediumType:      Ethernet\nWireless:        No\n\n"
                    "Name:            wlp2s0\nStatus:          Up\nMediumType:      Ethernet\nWireless:        Yes\n\n")
        if what == "intnets":
            nets = {vm["info"].get("intnet1") for vm in self.vms.values()} - {None}
            return "".join("Name:        %s\n\n" % n for n in sorted(nets))
        if what == "cpu-profiles":
            return "".join("Name:        %s\nFull Name:   %s\n\n" % (n, n) for n in (
                "host", "AMD Ryzen 7 1800X Eight-Core", "AMD FX-8150 Eight-Core", "Intel Core i7-6700K",
                "Intel Core i5-3570", "Intel Pentium 4 3.00GHz"))
        if what == "hostinfo":
            return "Processor count: 8\nMemory size: 32000 MByte\n"
        raise FakeError("unknown list %s" % what)

    def cmd_showvminfo(self, rest):
        vm = self._vm(rest[0])
        return "".join("%s=%s\n" % (_q(k) if "-" in k else k, _q(v)) for k, v in vm["info"].items())

    def cmd_getextradata(self, rest):
        data = self.global_extradata if rest[0] == "global" else self._vm(rest[0])["extradata"]
        return "".join("Key: %s, Value: %s\n" % (k, v) for k, v in sorted(data.items()))

    def cmd_setextradata(self, rest):
        data = self.global_extradata if rest[0] == "global" else self._vm(rest[0])["extradata"]
        if len(rest) > 2:
            data[rest[1]] = rest[2]
        else:
            data.pop(rest[1], None)

    def cmd_createvm(self, rest):
        opts = dict(zip(rest[::2], rest[1::2]))
        name = opts["--name"]
        if any(vm["info"]["name"] == name for vm in self.vms.values()):
            raise FakeError("Machine settings file '%s.vbox' already exists" % name)
        uid = self._createvm(name, opts.get("--ostype", "Other"))
        return ("Virtual machine '%s' is created and registered.\nUUID: %s\nSettings file: '%s'\n"
                % (name, uid, self.vms[uid]["info"]["CfgFile"]))

    MODIFY_KEYS = {
        "--memory": "memory", "--cpus": "cpus", "--name": "name", "--description": "description",
        "--nic1": "nic1", "--bridge-adapter1": "bridgeadapter1", "--intnet1": "intnet1",
        "--host-only-adapter1": "hostonlyadapter1", "--clipboard-mode": "clipboard",
        "--usb-xhci": "xhci", "--audio-enabled": "audio_enabled", "--vrde": "vrde",
        "--nat-localhostreachable1": "natlocalhostreachable1", "--tpm-type": "tpm_type",
        "--graphicscontroller": "graphicscontroller", "--vram": "vram", "--drag-and-drop": "draganddrop",
        "--accelerate-3d": "accelerate3d", "--nictype1": "nictype1",
        "--usb-ehci": "ehci", "--usb-ohci": "usb", "--audio-controller": "audiocontroller",
        "--mouse": "mouse", "--ioapic": "ioapic", "--cpu-profile": "cpuprofile", "--pae": "pae",
        "--spec-ctrl": "specctrl",
    }

    def cmd_modifyvm(self, rest):
        vm = self._vm(rest[0])
        self._require_off(vm)
        opts = rest[1:]
        for key, value in zip(opts[::2], opts[1::2]):
            if key == "--firmware":
                vm["info"]["firmware"] = value.upper()
            elif key in self.MODIFY_KEYS:
                vm["info"][self.MODIFY_KEYS[key]] = value

    def cmd_createmedium(self, rest):
        opts = dict(zip(rest[1::2], rest[2::2]))
        self.media.add(opts["--filename"])
        return "Medium created. UUID: %s\n" % uuidlib.uuid4()

    def cmd_storagectl(self, rest):
        self._vm(rest[0])["controllers"].add(rest[rest.index("--name") + 1])

    def cmd_storageattach(self, rest):
        vm = self._vm(rest[0])
        opts = dict(zip(rest[1::2], rest[2::2]))
        ctl = opts["--storagectl"]
        if ctl not in vm["controllers"] and not any(k.startswith(ctl + "-") for k in vm["info"]):
            raise FakeError("Could not find a controller named '%s'" % ctl)
        if opts["--type"] == "hdd" and vm["info"]["VMState"] in ("running", "paused"):
            raise FakeError("machine is locked")
        vm["info"]["%s-%s-0" % (ctl, opts["--port"])] = opts["--medium"]

    def cmd_unregistervm(self, rest):
        vm = self._vm(rest[0])
        self._require_off(vm)
        uid = vm["info"]["UUID"]
        del self.vms[uid]
        self.order.remove(uid)

    def cmd_startvm(self, rest):
        vm = self._vm(rest[0])
        if vm["info"]["VMState"] in ("running", "paused"):
            raise FakeError("The machine is already running")
        vm["info"]["VMState"] = "running"
        return "Waiting for VM \"%s\" to power on...\nVM has been successfully started.\n" % vm["info"]["name"]

    def cmd_discardstate(self, rest):
        self._vm(rest[0])["info"]["VMState"] = "poweroff"

    def cmd_controlvm(self, rest):
        vm = self._vm(rest[0])
        action, opts = rest[1], rest[2:]
        state = vm["info"]["VMState"]
        if state not in ("running", "paused"):
            raise FakeError("Machine '%s' is not currently running" % vm["info"]["name"])
        if action in ("acpipowerbutton", "poweroff"):
            vm["info"]["VMState"] = "poweroff"
            self._release_usb(vm)
        elif action == "savestate":
            vm["info"]["VMState"] = "saved"
            self._release_usb(vm)
        elif action == "pause":
            vm["info"]["VMState"] = "paused"
        elif action == "resume":
            vm["info"]["VMState"] = "running"
        elif action == "usbattach":
            dev = self._usb(opts[0])
            if dev["Current State"] == "Captured":
                raise FakeError("USB device is busy")
            dev["Current State"] = "Captured"
            n = 1 + sum(1 for k in vm["info"] if k.startswith("USBAttachedUUID"))
            vm["info"]["USBAttachedUUID%d" % n] = dev["UUID"]
        elif action == "usbdetach":
            dev = self._usb(opts[0])
            for key in [k for k, v in vm["info"].items() if k.startswith("USBAttachedUUID") and v == dev["UUID"]]:
                del vm["info"][key]
            dev["Current State"] = "Available"
        elif action == "clipboard":
            vm["info"]["clipboard"] = opts[-1]
        elif action == "nic1":
            vm["info"]["nic1"] = opts[0]
        elif action == "setvideomodehint":
            vm["info"]["VideoModeHint"] = "%sx%sx%s" % tuple(opts[:3])
        elif action in ("reset", "setlinkstate1"):
            pass
        else:
            raise FakeError("unsupported controlvm action %s" % action)

    def _usb(self, ref):
        for dev in self.usb:
            if dev["UUID"] == ref:
                return dev
        raise FakeError("USB device '%s' not found" % ref)

    def _release_usb(self, vm):
        for key in [k for k in vm["info"] if k.startswith("USBAttachedUUID")]:
            self._usb(vm["info"].pop(key))["Current State"] = "Available"

    def cmd_modifynvram(self, rest):
        self._vm(rest[0])

    def cmd_modifymedium(self, rest):
        pass

    def cmd_closemedium(self, rest):
        pass

    # -- snapshots ----------------------------------------------------------
    def _flatten(self, vm):
        """Flatten the snapshot tree using VBoxManage's key suffixes
        ("" for the root, "-1", "-2" for its children, "-1-1"...)."""
        out = []

        def rec(nodes, prefix, root):
            for i, node in enumerate(nodes):
                suffix = prefix if root else "%s-%d" % (prefix, i + 1)
                out.append((suffix, node))
                rec(node["children"], suffix, False)
        rec(vm["snapshots"][:1], "", True)
        return out

    def cmd_snapshot(self, rest):
        vm = self._vm(rest[0])
        sub, opts = rest[1], rest[2:]
        flat = self._flatten(vm)
        if sub == "list":
            if not flat:
                raise FakeError("This machine does not have any snapshots")
            lines = []
            for suffix, node in flat:
                lines += ['SnapshotName%s="%s"' % (suffix, node["name"]),
                          'SnapshotUUID%s="%s"' % (suffix, node["uuid"])]
                if node["description"]:
                    lines.append('SnapshotDescription%s="%s"' % (suffix, node["description"]))
            for suffix, node in flat:
                if node["uuid"] == vm["current"]:
                    lines += ['CurrentSnapshotName="%s"' % node["name"],
                              'CurrentSnapshotUUID="%s"' % node["uuid"],
                              'CurrentSnapshotNode="SnapshotName%s"' % suffix]
            return "\n".join(lines) + "\n"
        if sub == "take":
            desc = opts[opts.index("--description") + 1] if "--description" in opts else ""
            node = {"name": opts[0], "uuid": str(uuidlib.uuid4()), "description": desc, "children": []}
            parent = next((n for _, n in flat if n["uuid"] == vm["current"]), None)
            (parent["children"] if parent else vm["snapshots"]).append(node)
            vm["current"] = node["uuid"]
            return "Snapshot taken. UUID: %s\n" % node["uuid"]
        node = next((n for _, n in flat if opts[0] in (n["uuid"], n["name"])), None)
        if node is None:
            raise FakeError("Could not find a snapshot named '%s'" % opts[0])
        if sub == "restore":
            self._require_off(vm)
            vm["current"] = node["uuid"]
            return
        if sub == "delete":
            if len(node["children"]) > 1:
                raise FakeError("Snapshot has more than one child snapshot")

            def remove(nodes):
                for i, n in enumerate(nodes):
                    if n is node:
                        nodes[i:i + 1] = n["children"]
                        return True
                    if remove(n["children"]):
                        return True
                return False
            remove(vm["snapshots"])
            if vm["current"] == node["uuid"]:
                vm["current"] = None
            return
        raise FakeError("unsupported snapshot command %s" % sub)
