"""Removable media: import ISO images from USB sticks.

The host never mounts anything on its own.  A stick is only mounted when
the user opens it in the dashboard - read-only and with noexec, via
udisks2 (exFAT, NTFS, FAT, ext4, ISO 9660 ...).  ISO files are copied into
the ISO library in the background, then the stick can be ejected.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid as uuidlib

from .toolstack import Toolstack, ToolstackError

log = logging.getLogger(__name__)

SUPPORTED_FS = {"vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext3", "ext4", "btrfs", "xfs", "iso9660", "udf"}
FS_LABEL = {"vfat": "FAT32", "exfat": "exFAT", "ntfs": "NTFS", "ntfs3": "NTFS", "iso9660": "ISO 9660"}
MAX_DEPTH = 4
CHUNK = 4 << 20


def _run(argv, timeout=60):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolstackError("%s: %s" % (argv[0], exc), 502) from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise ToolstackError(msg[-1] if msg else "%s fehlgeschlagen" % argv[0], 502)
    return proc.stdout


class Media:
    def __init__(self, iso_dir, runner=_run, live_medium="/run/live/medium"):
        self.iso_dir = iso_dir
        self.run = runner
        self.live_medium = live_medium
        self.jobs = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #
    def _lsblk(self):
        out = self.run(["lsblk", "-J", "-b", "-o",
                        "NAME,PATH,TYPE,FSTYPE,LABEL,SIZE,RM,HOTPLUG,TRAN,VENDOR,MODEL,MOUNTPOINTS"])
        return json.loads(out).get("blockdevices", [])

    def devices(self):
        """Filesystems on removable/USB disks that VMBox may open."""
        result = []
        for disk in self._lsblk():
            removable = bool(disk.get("rm")) or bool(disk.get("hotplug")) or disk.get("tran") == "usb"
            if disk.get("type") != "disk" or not removable:
                continue
            parts = disk.get("children") or [disk]
            mounts = [m for p in parts for m in (p.get("mountpoints") or []) if m]
            # Never touch the stick VMBox itself was booted from.
            if any(m == self.live_medium or m.startswith("/run/live") for m in mounts):
                continue
            model = " ".join(x.strip() for x in (disk.get("vendor"), disk.get("model")) if x and x.strip())
            for part in parts:
                fstype = (part.get("fstype") or "").lower()
                if fstype not in SUPPORTED_FS:
                    continue
                mountpoints = [m for m in (part.get("mountpoints") or []) if m]
                result.append({
                    "device": part["path"],
                    "disk": disk["path"],
                    "label": part.get("label") or model or os.path.basename(part["path"]),
                    "model": model,
                    "fstype": FS_LABEL.get(fstype, fstype),
                    "size": int(part.get("size") or 0),
                    "mountpoint": mountpoints[0] if mountpoints else None,
                })
        return result

    def _device(self, device):
        for dev in self.devices():
            if dev["device"] == device:
                return dev
        raise ToolstackError("USB-Stick nicht gefunden – wurde er entfernt?", 404)

    # ------------------------------------------------------------------ #
    # mount / eject
    # ------------------------------------------------------------------ #
    def mount(self, device):
        dev = self._device(device)
        if dev["mountpoint"]:
            return dev["mountpoint"]
        out = self.run(["udisksctl", "mount", "--no-user-interaction", "-b", device, "-o", "ro,noexec"])
        m = re.search(r" at (/.+?)\.?\s*$", out.strip())
        if not m:
            raise ToolstackError("Der Stick konnte nicht eingebunden werden", 502)
        log.info("mounted %s at %s", device, m.group(1))
        return m.group(1)

    def eject(self, device):
        dev = self._device(device)
        if any(j["device"] == device and j["state"] == "running" for j in self.jobs.values()):
            raise ToolstackError("Es wird noch kopiert – bitte warten", 409)
        if dev["mountpoint"]:
            self.run(["udisksctl", "unmount", "--no-user-interaction", "-b", device])
        try:
            self.run(["udisksctl", "power-off", "--no-user-interaction", "-b", dev["disk"]])
        except ToolstackError as exc:
            log.info("power-off of %s not possible: %s", dev["disk"], exc)

    # ------------------------------------------------------------------ #
    # browsing & import
    # ------------------------------------------------------------------ #
    def isos(self, device):
        root = os.path.realpath(self.mount(device))
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith((".", "$")) and d != "System Volume Information"
                                 and depth < MAX_DEPTH)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                if name.lower().endswith(".iso") and not os.path.islink(path):
                    try:
                        found.append({"file": os.path.relpath(path, root), "size": os.path.getsize(path)})
                    except OSError:
                        continue
        return found

    def start_import(self, device, rel_path):
        root = os.path.realpath(self.mount(device))
        src = os.path.realpath(os.path.join(root, str(rel_path or "")))
        if not src.startswith(root + os.sep) or not os.path.isfile(src) or not src.lower().endswith(".iso"):
            raise ToolstackError("ISO-Datei nicht gefunden")
        name = Toolstack.safe_iso_name(os.path.basename(src))
        dest = os.path.join(self.iso_dir, name)
        if os.path.exists(dest):
            raise ToolstackError("„%s“ ist bereits in der Bibliothek" % name, 409)
        size = os.path.getsize(src)
        os.makedirs(self.iso_dir, exist_ok=True)
        if shutil.disk_usage(self.iso_dir).free < size + (1 << 30):
            raise ToolstackError("Nicht genügend freier Speicher", 507)
        with self._lock:
            if any(j["name"] == name and j["state"] == "running" for j in self.jobs.values()):
                raise ToolstackError("„%s“ wird bereits kopiert" % name, 409)
            job = {"id": str(uuidlib.uuid4()), "device": device, "name": name, "size": size,
                   "copied": 0, "state": "running", "error": "", "started": time.time()}
            self.jobs[job["id"]] = job
        threading.Thread(target=self._copy, args=(job, src, dest), daemon=True, name="iso-import").start()
        return dict(job)

    def _copy(self, job, src, dest):
        tmp = dest + ".part"
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    chunk = fin.read(CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    job["copied"] += len(chunk)
                fout.flush()
                os.fsync(fout.fileno())
            os.replace(tmp, dest)
            job["state"] = "done"
            log.info("imported %s (%d bytes)", job["name"], job["copied"])
        except OSError as exc:
            job["state"] = "failed"
            job["error"] = "Kopieren fehlgeschlagen: %s" % (exc.strerror or exc)
            log.error("import of %s failed: %s", job["name"], exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def job_list(self):
        now = time.time()
        with self._lock:
            # finished jobs are forgotten after an hour
            for jid in [j for j, job in self.jobs.items() if job["state"] != "running" and now - job["started"] > 3600]:
                del self.jobs[jid]
            return sorted((dict(j) for j in self.jobs.values()), key=lambda j: j["started"])


class FakeMedia(Media):
    """Demo stick for ``--demo``: a directory with a few small fake ISOs."""

    def __init__(self, iso_dir, base):
        super().__init__(iso_dir, runner=None)
        self.base = base
        os.makedirs(os.path.join(base, "Software"), exist_ok=True)
        for rel, size in (("Windows7_SP1_x64.iso", 3 << 20), ("Software/debian-13-amd64.iso", 2 << 20),
                          ("Software/ubuntu-24.04-desktop.iso", 2 << 20)):
            path = os.path.join(base, rel)
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    fh.truncate(size)
        self.present = True

    def devices(self):
        if not self.present:
            return []
        return [{"device": "/dev/sdb1", "disk": "/dev/sdb", "label": "STICK", "model": "SanDisk Ultra Fit",
                 "fstype": "exFAT", "size": 64 << 30, "mountpoint": self.base}]

    def mount(self, device):
        self._device(device)
        return self.base

    def eject(self, device):
        self._device(device)
        self.present = False
