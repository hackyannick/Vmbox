"""Host (the "dom0" of VMBox) information, WLAN and power management."""

import os
import platform
import re
import shutil
import socket
import subprocess
import threading
import time


class HostError(RuntimeError):
    pass


def _run(*argv, timeout=30):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostError(str(exc)) from exc
    if proc.returncode != 0:
        raise HostError((proc.stderr or proc.stdout).strip() or "%s failed" % argv[0])
    return proc.stdout


MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
BT_ERRORS = [
    (r"No default controller|not available", "Kein Bluetooth-Adapter gefunden"),
    (r"Blocked|rfkill", "Bluetooth ist gesperrt (Funkschalter bzw. Flugmodus)"),
    (r"AuthenticationFailed|AuthenticationRejected|AuthenticationCanceled",
     "Koppeln fehlgeschlagen – das Gerät bitte in den Kopplungsmodus versetzen und erneut versuchen"),
    (r"ConnectionAttemptFailed|Page Timeout|Host is down|not connected|br-connection",
     "Gerät nicht erreichbar – ist es eingeschaltet und in der Nähe?"),
    (r"AlreadyExists", "Das Gerät ist bereits gekoppelt"),
    (r"DoesNotExist|not available", "Gerät nicht gefunden – bitte erneut suchen"),
]


def _bt_message(text):
    for pattern, message in BT_ERRORS:
        if re.search(pattern, text, re.I):
            return message
    return text.strip()


def _btctl(*args, timeout=20):
    """Run bluetoothctl; it sometimes exits 0 although the command failed."""
    try:
        out = _run("bluetoothctl", *args, timeout=timeout)
    except HostError as exc:
        raise HostError(_bt_message(str(exc))) from exc
    for line in out.splitlines():
        if re.search(r"Failed to|No default controller|org\.bluez\.Error", line):
            raise HostError(_bt_message(line))
    return out


def _bt_fields(text):
    fields = {}
    for line in text.splitlines():
        if ":" in line and line.startswith(("\t", " ")):
            key, value = line.strip().split(":", 1)
            fields.setdefault(key.strip(), value.strip())
    return fields


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x01\x02\r]")


class BluetoothSession:
    """One long-running ``bluetoothctl``, fed through a pipe.

    Bluetooth LE mice (e.g. Logitech M650) only pair reliably when discovery
    keeps running while pairing (BlueZ drops unpaired devices ~30 s after a
    scan ends) and an agent stays registered for the whole process - which
    single ``bluetoothctl <command>`` calls cannot do."""

    CONFIRM_RE = re.compile(r"Confirm passkey|Request confirmation|Authorize service|Accept pairing", re.I)

    def __init__(self, argv=("bluetoothctl",)):
        self.argv = list(argv)
        self.proc = None
        self.lines = []          # [(seq, text)]
        self.seq = 0
        self.cond = threading.Condition()
        self.lock = threading.Lock()
        self._scan_timer = None

    def _ensure(self):
        if self.proc and self.proc.poll() is None:
            return
        try:
            self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
        except OSError as exc:
            raise HostError("bluetoothctl nicht verfügbar: %s" % exc) from exc
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True, name="bt-session").start()
        self._write("agent NoInputNoOutput")
        self._write("default-agent")

    def _reader(self, proc):
        for raw in proc.stdout:
            text = ANSI_RE.sub("", raw).strip()
            if not text:
                continue
            with self.cond:
                self.seq += 1
                self.lines.append((self.seq, text))
                del self.lines[:-400]
                self.cond.notify_all()
            if self.CONFIRM_RE.search(text):
                self._write("yes")       # just works: accept the pairing request

    def _write(self, command):
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise HostError("bluetoothctl beendet: %s" % exc) from exc

    def run(self, command, ok, fail=r"Failed|org\.bluez\.Error|not available", timeout=20, accept=None):
        """Send ``command`` and wait for a line matching ``ok`` (or ``accept``,
        e.g. "already paired"); a line matching ``fail`` raises HostError."""
        with self.lock:
            self._ensure()
            with self.cond:
                start = self.seq
            self._write(command)
            deadline = time.monotonic() + timeout
            with self.cond:
                while True:
                    for seq, text in self.lines:
                        if seq <= start:
                            continue
                        if re.search(ok, text) or (accept and re.search(accept, text)):
                            return text
                        if re.search(fail, text):
                            raise HostError(_bt_message(text))
                    start = self.seq
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HostError("Keine Antwort vom Gerät – bitte erneut versuchen")
                    self.cond.wait(remaining)

    def seen(self, mac, timeout):
        """Wait until discovery reports ``mac`` (or it is already known)."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while time.monotonic() < deadline:
                if any(mac in text for _, text in self.lines):
                    return True
                self.cond.wait(max(0.1, deadline - time.monotonic()))
        return False

    def scan(self, seconds):
        self.run("scan on", ok=r"Discovery started|Discovering: yes|SetDiscoveryFilter success",
                 accept=r"InProgress|Already", timeout=10)
        if self._scan_timer:
            self._scan_timer.cancel()
        self._scan_timer = threading.Timer(seconds, self.stop_scan)
        self._scan_timer.daemon = True
        self._scan_timer.start()

    def stop_scan(self):
        try:
            with self.lock:
                if self.proc and self.proc.poll() is None:
                    self._write("scan off")
        except HostError:
            pass


def split_nmcli(line):
    """Split a ``nmcli -t`` line on unescaped colons."""
    fields, current, escape = [], [], False
    for ch in line:
        if escape:
            current.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError):
        pass
    return info


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _virt_extensions():
    try:
        with open("/proc/cpuinfo") as fh:
            flags = fh.read()
    except OSError:
        return False
    return bool(re.search(r"\b(vmx|svm)\b", flags))


def kvm_loaded():
    """KVM grabs VT-x/AMD-V and VirtualBox then fails with VERR_SVM_IN_USE / VERR_VMX_IN_USE."""
    return any(os.path.exists("/sys/module/" + m) for m in ("kvm_amd", "kvm_intel"))


class Host:
    def __init__(self, state_dir, vbox_version=lambda: "", live=False):
        self.state_dir = state_dir
        self.vbox_version = vbox_version
        self.live = live

    def info(self):
        mem = _meminfo()
        try:
            disk = shutil.disk_usage(self.state_dir)
            storage = {"total": disk.total, "free": disk.free}
        except OSError:
            storage = {"total": 0, "free": 0}
        try:
            with open("/proc/uptime") as fh:
                uptime = int(float(fh.read().split()[0]))
        except (OSError, ValueError):
            uptime = 0
        try:
            vbox = self.vbox_version()
        except Exception:  # noqa: BLE001 - informational only
            vbox = "?"
        return {
            "hostname": socket.gethostname(),
            "kernel": platform.release(),
            "cpu": _cpu_model(),
            "cpus": os.cpu_count(),
            "virtualization": _virt_extensions(),
            "memory_total": mem.get("MemTotal", 0),
            "memory_free": mem.get("MemAvailable", 0),
            "storage": storage,
            "uptime": uptime,
            "virtualbox": vbox,
            "tpm": os.path.exists("/sys/class/tpm/tpm0"),
            "encrypted": os.path.exists("/dev/mapper/vmbox-storage"),
            "secure_boot": self._secure_boot(),
            "kvm_loaded": kvm_loaded(),
            "graphics": self.graphics(),
            "battery": self.battery(),
            "live": self.live,
            "time": int(time.time()),
        }

    _graphics_cache = (0.0, None)

    def graphics(self):
        """OpenGL/Vulkan renderer of the host (what VirtualBox' 3D uses).
        Cached for 5 minutes - glxinfo/vulkaninfo take a moment."""
        stamp, value = self._graphics_cache
        if value is not None and time.time() - stamp < 300:
            return value
        result = {"opengl": "", "vulkan": [], "software": False}
        try:
            out = _run("glxinfo", "-B", timeout=10)
            m = re.search(r"OpenGL renderer string:\s*(.+)", out)
            result["opengl"] = m.group(1).strip() if m else ""
        except HostError:
            pass
        try:
            out = _run("vulkaninfo", "--summary", timeout=10)
            result["vulkan"] = sorted(set(m.strip() for m in re.findall(r"deviceName\s*=\s*(.+)", out)))
        except HostError:
            pass
        names = [result["opengl"]] + result["vulkan"]
        result["software"] = bool(names[0]) and all(re.search(r"llvmpipe|lavapipe|softpipe|software", n, re.I)
                                                    for n in names if n)
        self._graphics_cache = (time.time(), result)
        return result

    @staticmethod
    def battery():
        """Laptop battery as {"percent", "charging"}, or None on desktops."""
        base = "/sys/class/power_supply"
        try:
            names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
        except OSError:
            return None
        for name in names:
            try:
                with open(os.path.join(base, name, "capacity")) as fh:
                    percent = int(fh.read().strip())
                with open(os.path.join(base, name, "status")) as fh:
                    status = fh.read().strip()
            except (OSError, ValueError):
                continue
            return {"percent": max(0, min(100, percent)), "charging": status in ("Charging", "Full")}
        return None

    @staticmethod
    def _secure_boot():
        try:
            for name in os.listdir("/sys/firmware/efi/efivars"):
                if name.startswith("SecureBoot-"):
                    with open(os.path.join("/sys/firmware/efi/efivars", name), "rb") as fh:
                        return fh.read()[-1] == 1
        except OSError:
            pass
        return False

    # -- WLAN via NetworkManager -----------------------------------------------
    def wifi_networks(self):
        out = _run("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto")
        networks = {}
        for line in out.splitlines():
            in_use, ssid, signal, security = (split_nmcli(line) + ["", "", "", ""])[:4]
            if not ssid:
                continue
            entry = {"ssid": ssid, "signal": int(signal or 0), "security": security,
                     "connected": in_use.strip() == "*"}
            old = networks.get(ssid)
            if old is None or entry["connected"] or entry["signal"] > old["signal"]:
                networks[ssid] = entry
        return sorted(networks.values(), key=lambda n: (not n["connected"], -n["signal"]))

    def wifi_connect(self, ssid, password=None):
        args = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        _run(*args, timeout=60)

    def connections(self):
        out = _run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
        devices = []
        for line in out.splitlines():
            device, typ, state, conn = (split_nmcli(line) + ["", "", "", ""])[:4]
            if typ in ("ethernet", "wifi"):
                devices.append({"device": device, "type": typ, "state": state, "connection": conn})
        return devices

    # -- Bluetooth (BlueZ) -------------------------------------------------------
    _scan_until = 0.0

    def bluetooth_adapter(self):
        try:
            out = _btctl("show", timeout=10)
        except HostError as exc:
            return {"available": False, "error": str(exc)}
        m = re.search(r"Controller ([0-9A-F:]{17})", out)
        fields = _bt_fields(out)
        return {
            "available": True,
            "address": m.group(1) if m else "",
            "name": fields.get("Alias") or fields.get("Name", ""),
            "powered": fields.get("Powered") == "yes",
            "discovering": fields.get("Discovering") == "yes" or time.monotonic() < self._scan_until,
        }

    def bluetooth_devices(self, limit=40):
        devices = []
        out = _btctl("devices", timeout=10)
        for line in out.splitlines()[:limit]:
            m = re.match(r"Device ([0-9A-F:]{17}) ?(.*)", line.strip())
            if not m:
                continue
            mac = m.group(1)
            try:
                fields = _bt_fields(_btctl("info", mac, timeout=10))
            except HostError:
                continue
            name = fields.get("Alias") or fields.get("Name") or m.group(2) or mac
            paired = fields.get("Paired") == "yes"
            # skip anonymous neighbours whose name is only their address
            if not paired and re.fullmatch(r"([0-9A-F]{2}[-:]){5}[0-9A-F]{2}", name, re.I):
                continue
            battery = re.search(r"\((\d+)\)", fields.get("Battery Percentage", ""))
            devices.append({
                "address": mac, "name": name, "icon": fields.get("Icon", ""),
                "paired": paired, "trusted": fields.get("Trusted") == "yes",
                "connected": fields.get("Connected") == "yes",
                "battery": int(battery.group(1)) if battery else None,
            })
        devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return devices

    def bluetooth_power(self, on):
        if on:
            try:
                _run("rfkill", "unblock", "bluetooth", timeout=10)
            except HostError:
                pass  # no permission or no rfkill switch - power on may still work
        _btctl("power", "on" if on else "off")

    _bt_session = None

    @property
    def bt(self):
        if self._bt_session is None:
            self._bt_session = BluetoothSession()
        return self._bt_session

    def bluetooth_scan(self, seconds=60):
        """Search for devices; discovery keeps running for ``seconds`` so that
        found devices stay available for pairing."""
        self.bt.scan(seconds)
        self._scan_until = time.monotonic() + seconds

    @staticmethod
    def _mac(mac):
        mac = str(mac or "").upper()
        if not MAC_RE.match(mac):
            raise HostError("Ungültige Bluetooth-Adresse")
        return mac

    def bluetooth_pair(self, mac):
        """Pair (mice use "just works"), trust and connect - all in one
        bluetoothctl session with discovery running, as Bluetooth LE needs."""
        mac = self._mac(mac)
        bt = self.bt
        self.bluetooth_scan(60)
        if not bt.seen(mac, 15):
            raise HostError("Gerät nicht gefunden – bitte in den Kopplungsmodus versetzen und erneut suchen")
        bt.run("pair " + mac, ok=r"Pairing successful", accept=r"AlreadyExists|Paired: yes", timeout=60)
        bt.run("trust " + mac, ok=r"trust succeeded|Trusted: yes", timeout=15)
        bt.run("connect " + mac, ok=r"Connection successful|Connected: yes", accept=r"AlreadyConnected", timeout=30)
        bt.stop_scan()

    def bluetooth_connect(self, mac):
        _btctl("connect", self._mac(mac), timeout=25)

    def bluetooth_disconnect(self, mac):
        _btctl("disconnect", self._mac(mac))

    def bluetooth_remove(self, mac):
        _btctl("remove", self._mac(mac))

    # -- power ---------------------------------------------------------------
    def poweroff(self):
        _run("systemctl", "poweroff")

    def reboot(self):
        _run("systemctl", "reboot")

    def start_installer(self):
        if not self.live:
            raise HostError("Das System ist bereits installiert")
        _run("systemctl", "start", "--no-block", "vmbox-installer.service")


class FakeHost(Host):
    """Demo data for the development backend."""

    def __init__(self, state_dir, vbox_version=lambda: "", live=True):
        super().__init__(state_dir, vbox_version, live)
        self._wifi = [
            {"ssid": "Buero-5G", "signal": 82, "security": "WPA2", "connected": True},
            {"ssid": "Gaeste", "signal": 54, "security": "WPA2", "connected": False},
            {"ssid": "Labor", "signal": 31, "security": "WPA3", "connected": False},
        ]

    def wifi_networks(self):
        return list(self._wifi)

    _bt = None

    def _bt_list(self):
        if self._bt is None:
            self._bt = [
                {"address": "D4:12:43:7A:01:22", "name": "Keyboard K380", "icon": "input-keyboard", "paired": True,
                 "trusted": True, "connected": True, "battery": 70},
                {"address": "F2:9C:31:0B:5E:10", "name": "MX Master 3", "icon": "input-mouse", "paired": False,
                 "trusted": False, "connected": False, "battery": None},
            ]
            self._bt_powered = True
        return self._bt

    def bluetooth_adapter(self):
        self._bt_list()
        return {"available": True, "address": "3C:21:9C:00:11:22", "name": "vmbox", "powered": self._bt_powered,
                "discovering": time.monotonic() < self._scan_until}

    def bluetooth_devices(self, limit=40):
        devices = sorted(self._bt_list(), key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
        return [dict(d) for d in devices]

    def bluetooth_power(self, on):
        self._bt_list()
        self._bt_powered = bool(on)

    def bluetooth_scan(self, seconds=15):
        self._scan_until = time.monotonic() + seconds
        if not any(d["name"] == "Galaxy Buds" for d in self._bt_list()):
            self._bt.append({"address": "A0:B1:C2:D3:E4:F5", "name": "Galaxy Buds", "icon": "audio-headset",
                             "paired": False, "trusted": False, "connected": False, "battery": None})

    def _bt_device(self, mac):
        mac = self._mac(mac)
        for d in self._bt_list():
            if d["address"] == mac:
                return d
        raise HostError("Gerät nicht gefunden – bitte erneut suchen")

    def bluetooth_pair(self, mac):
        self._bt_device(mac).update(paired=True, trusted=True, connected=True)

    def bluetooth_connect(self, mac):
        self._bt_device(mac)["connected"] = True

    def bluetooth_disconnect(self, mac):
        self._bt_device(mac)["connected"] = False

    def bluetooth_remove(self, mac):
        d = self._bt_device(mac)
        self._bt.remove(d)

    @staticmethod
    def battery():
        return {"percent": 98, "charging": True}

    def graphics(self):
        return {"opengl": "AMD Radeon 780M Graphics (radeonsi, gfx1103, LLVM 19.1.7, DRM 3.61)",
                "vulkan": ["AMD Radeon 780M Graphics (RADV PHOENIX)", "llvmpipe (LLVM 19.1.7, 256 bits)"], "software": False}

    def wifi_connect(self, ssid, password=None):
        for net in self._wifi:
            net["connected"] = net["ssid"] == ssid

    def connections(self):
        return [{"device": "enp3s0", "type": "ethernet", "state": "connected", "connection": "Kabel"},
                {"device": "wlp2s0", "type": "wifi", "state": "connected", "connection": "Buero-5G"}]

    def poweroff(self):
        raise HostError("Im Demo-Modus nicht verfügbar")

    reboot = poweroff
    start_installer = poweroff
