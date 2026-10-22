"""Host (the "dom0" of VMBox) information, WLAN and power management."""

import os
import platform
import re
import shutil
import socket
import subprocess
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
            "live": self.live,
            "time": int(time.time()),
        }

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
