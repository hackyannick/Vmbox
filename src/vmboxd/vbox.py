"""Thin, well-tested wrapper around the ``VBoxManage`` command line tool.

Everything VMBox does to a virtual machine goes through this module, so
that the rest of the daemon never has to deal with VBoxManage's text
output formats.
"""

import logging
import os
import re
import subprocess
import time

log = logging.getLogger(__name__)

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class VBoxError(RuntimeError):
    """Raised when VBoxManage reports an error."""


def subprocess_runner(argv, env=None, timeout=120):
    """Default runner: executes ``argv`` and returns (rc, stdout, stderr)."""
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run(argv, capture_output=True, text=True, env=full_env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def parse_machinereadable(text):
    """Parse ``key="value"`` lines as printed by ``--machinereadable``."""
    result = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        if line.startswith('"'):
            end = line.find('"=', 1)
            if end < 0:
                continue
            key, value = line[1:end], line[end + 2:]
        else:
            key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        result[key.strip()] = value
    return result


def parse_blocks(text):
    """Parse blank-line separated ``Key: value`` blocks (list usbhost, bridgedifs...)."""
    blocks, current = [], {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not value and key.endswith("Devices"):
            continue  # section header such as "Host USB Devices:"
        current[key] = value
    if current:
        blocks.append(current)
    return blocks


def parse_snapshots(text):
    """Turn ``snapshot list --machinereadable`` output into a flat, ordered list."""
    data = parse_machinereadable(text)
    current = data.get("CurrentSnapshotUUID")
    snaps = []
    for key, name in data.items():
        m = re.fullmatch(r"SnapshotName((?:-\d+)*)", key)
        if not m:
            continue
        suffix = m.group(1)
        parts = [int(p) for p in suffix.split("-") if p]
        parent_suffix = suffix.rsplit("-", 1)[0] if suffix else None
        uuid = data.get("SnapshotUUID" + suffix, "")
        snaps.append({
            "name": name,
            "uuid": uuid,
            "description": data.get("SnapshotDescription" + suffix, ""),
            "depth": len(parts),
            "parent": data.get("SnapshotUUID" + parent_suffix) if parent_suffix is not None else None,
            "current": uuid == current,
            "_order": parts,
        })
    snaps.sort(key=lambda s: s["_order"])
    for s in snaps:
        del s["_order"]
    return snaps


def _hex_id(value):
    """'0x0781 (0781)' -> '0781'."""
    m = re.match(r"0x([0-9a-fA-F]{4})", value or "")
    return m.group(1).lower() if m else (value or "").lower()


class VBoxManage:
    """Object oriented access to VBoxManage."""

    def __init__(self, binary="VBoxManage", runner=None, gui_env=None):
        self.binary = binary
        self.runner = runner or subprocess_runner
        self.gui_env = gui_env or {}
        self.sleep = time.sleep

    # -- plumbing ---------------------------------------------------------
    def run(self, *args, env=None, timeout=120, check=True):
        argv = [self.binary, "-q", *[str(a) for a in args]]
        log.debug("run %s", " ".join(argv))
        rc, out, err = self.runner(argv, env=env, timeout=timeout)
        if check and rc != 0:
            msg = (err or out or "").strip().splitlines()
            msg = [line for line in msg if line.strip()]
            text = msg[0] if msg else "VBoxManage %s failed (rc=%s)" % (args[0], rc)
            text = re.sub(r"^VBoxManage(\.exe)?: error: ", "", text)
            raise VBoxError(text)
        return out

    # -- inventory ----------------------------------------------------------
    def version(self):
        return self.run("--version").strip()

    def list_vms(self):
        vms = []
        for line in self.run("list", "vms").splitlines():
            m = re.match(r'^"(.*)" \{([0-9a-fA-F-]{36})\}$', line.strip())
            if m:
                vms.append((m.group(1), m.group(2)))
        return vms

    def running_vms(self):
        return {uuid for _, uuid in self._parse_vm_list(self.run("list", "runningvms"))}

    @staticmethod
    def _parse_vm_list(text):
        for line in text.splitlines():
            m = re.match(r'^"(.*)" \{([0-9a-fA-F-]{36})\}$', line.strip())
            if m:
                yield m.group(1), m.group(2)

    def vm_info(self, uuid):
        return parse_machinereadable(self.run("showvminfo", uuid, "--machinereadable"))

    def extradata(self, uuid, prefix=None):
        out = self.run("getextradata", uuid, "enumerate")
        data = {}
        for line in out.splitlines():
            m = re.match(r"^Key: (.*?), Value: (.*)$", line.strip())
            if m and (prefix is None or m.group(1).startswith(prefix)):
                data[m.group(1)] = m.group(2)
        return data

    def set_extradata(self, uuid, key, value):
        if value is None:
            self.run("setextradata", uuid, key)
        else:
            self.run("setextradata", uuid, key, value)

    # -- lifecycle ----------------------------------------------------------
    def create_vm(self, name, ostype, basefolder=None):
        args = ["createvm", "--name", name, "--ostype", ostype, "--register"]
        if basefolder:
            args += ["--basefolder", basefolder]
        out = self.run(*args)
        m = re.search(r"UUID: ([0-9a-fA-F-]{36})", out)
        if not m:
            raise VBoxError("could not determine UUID of new VM")
        return m.group(1)

    def modify(self, uuid, *args):
        if args:
            self.run("modifyvm", uuid, *args)

    def create_disk(self, path, size_mb):
        self.run("createmedium", "disk", "--filename", path, "--size", int(size_mb),
                 "--format", "VDI", timeout=600)

    def storagectl(self, uuid, name, bus, controller=None, ports=None):
        args = ["storagectl", uuid, "--name", name, "--add", bus]
        if controller:
            args += ["--controller", controller]
        if ports:
            args += ["--portcount", ports]
        self.run(*args)

    def storageattach(self, uuid, ctl, port, kind, medium, mtype=None):
        args = ["storageattach", uuid, "--storagectl", ctl, "--port", port,
                "--device", 0, "--type", kind, "--medium", medium]
        if mtype:
            args += ["--mtype", mtype]
        self.run(*args)

    def unregister(self, uuid, delete=True):
        args = ["unregistervm", uuid]
        if delete:
            args.append("--delete")
        self.run(*args, timeout=600)

    def start(self, uuid, headless=False):
        # "separate": the VM runs in its own headless process and the window is
        # only a detachable frontend - a crash of the X session does not kill VMs.
        # Right after modifyvm VirtualBox may still be releasing the session
        # lock ("already locked by a session (or being locked or unlocked)").
        for attempt in range(5):
            try:
                self.run("startvm", uuid, "--type", "headless" if headless else "separate",
                         env=None if headless else self.gui_env, timeout=180)
                return
            except VBoxError as exc:
                if "already locked" not in str(exc) or attempt == 4:
                    raise
                log.info("machine %s still locked, retrying start", uuid)
                self.sleep(0.5 * (attempt + 1))

    def control(self, uuid, *args):
        self.run("controlvm", uuid, *args, timeout=300)

    def modifynvram(self, uuid, *args):
        self.run("modifynvram", uuid, *args)

    # -- snapshots ----------------------------------------------------------
    def snapshots(self, uuid):
        out = self.run("snapshot", uuid, "list", "--machinereadable", check=False)
        return parse_snapshots(out)

    def take_snapshot(self, uuid, name, description="", live=False):
        args = ["snapshot", uuid, "take", name]
        if description:
            args += ["--description", description]
        if live:
            args.append("--live")
        self.run(*args, timeout=900)

    def restore_snapshot(self, uuid, snapshot):
        self.run("snapshot", uuid, "restore", snapshot, timeout=900)

    def delete_snapshot(self, uuid, snapshot):
        self.run("snapshot", uuid, "delete", snapshot, timeout=900)

    # -- host ---------------------------------------------------------------
    def usb_devices(self):
        devices = []
        for block in parse_blocks(self.run("list", "usbhost")):
            if "UUID" not in block:
                continue
            devices.append({
                "uuid": block["UUID"],
                "vendor_id": _hex_id(block.get("VendorId")),
                "product_id": _hex_id(block.get("ProductId")),
                "manufacturer": block.get("Manufacturer", ""),
                "product": block.get("Product", ""),
                "serial": block.get("SerialNumber", ""),
                "address": block.get("Address", ""),
                "state": block.get("Current State", "").lower(),
            })
        return devices

    def bridged_interfaces(self):
        return [
            {
                "name": b.get("Name", ""),
                "status": b.get("Status", ""),
                "wireless": b.get("Wireless", "No").lower() == "yes",
                "medium": b.get("MediumType", ""),
            }
            for b in parse_blocks(self.run("list", "bridgedifs"))
            if b.get("Name")
        ]

    def cpu_profiles(self):
        """Names of the CPU profiles this VirtualBox can present to a guest."""
        return [b["Name"] for b in parse_blocks(self.run("list", "cpu-profiles")) if b.get("Name")]

    def internal_networks(self):
        nets = []
        for line in self.run("list", "intnets").splitlines():
            if line.startswith("Name:"):
                nets.append(line.split(":", 1)[1].strip())
        return nets

    def host_info(self):
        info = {}
        for line in self.run("list", "hostinfo").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                info[key.strip()] = value.strip()
        return info
