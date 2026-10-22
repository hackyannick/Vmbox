import json
import os
import tempfile
import time
import unittest

import context  # noqa: F401
from vmboxd.media import Media
from vmboxd.toolstack import ToolstackError


def lsblk_fixture(mounted_at=None):
    return {"blockdevices": [
        # the VMBox live stick itself - must never be offered
        {"name": "sda", "path": "/dev/sda", "type": "disk", "rm": True, "hotplug": True, "tran": "usb",
         "vendor": "Kingston", "model": "DataTraveler", "size": 16000000000, "mountpoints": [None],
         "children": [{"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "iso9660",
                       "label": "VMBox 1.0.0", "size": 1000000000, "mountpoints": ["/run/live/medium"]}]},
        # internal NVMe disk - not removable
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "rm": False, "hotplug": False, "tran": "nvme",
         "size": 512000000000, "mountpoints": [None],
         "children": [{"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "fstype": "ntfs",
                       "label": "Windows", "size": 512000000000, "mountpoints": [None]}]},
        # the user's USB stick with an exFAT and a swap partition
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "rm": True, "hotplug": True, "tran": "usb",
         "vendor": "SanDisk ", "model": "Ultra Fit", "size": 64000000000, "mountpoints": [None],
         "children": [{"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "exfat",
                       "label": "STICK", "size": 63000000000, "mountpoints": [mounted_at]},
                      {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "fstype": "swap",
                       "label": None, "size": 1000000000, "mountpoints": [None]}]},
        # NTFS USB disk without partition table
        {"name": "sdc", "path": "/dev/sdc", "type": "disk", "rm": False, "hotplug": True, "tran": "usb",
         "vendor": "WD", "model": "Elements", "fstype": "ntfs", "label": "", "size": 1000000000000,
         "mountpoints": [None]},
    ]}


class FakeSystem:
    def __init__(self, stick_dir):
        self.stick_dir = stick_dir
        self.mounted = None
        self.calls = []

    def __call__(self, argv, timeout=60):
        self.calls.append(argv)
        if argv[0] == "lsblk":
            return json.dumps(lsblk_fixture(self.mounted))
        if argv[:2] == ["udisksctl", "mount"]:
            assert "ro,noexec" in argv
            self.mounted = self.stick_dir
            return "Mounted %s at %s\n" % (argv[argv.index("-b") + 1], self.stick_dir)
        if argv[:2] == ["udisksctl", "unmount"]:
            self.mounted = None
            return "Unmounted /dev/sdb1.\n"
        if argv[:2] == ["udisksctl", "power-off"]:
            return ""
        raise AssertionError(argv)


class MediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stick = os.path.join(self.tmp.name, "stick")
        self.isos = os.path.join(self.tmp.name, "isos")
        os.makedirs(os.path.join(self.stick, "Software", "Linux"))
        os.makedirs(os.path.join(self.stick, ".Trash-1000"))
        os.makedirs(os.path.join(self.stick, "System Volume Information"))
        for rel, size in (("Win7 SP1.iso", 5000), ("Software/Linux/debian.ISO", 3000),
                          (".Trash-1000/old.iso", 10), ("System Volume Information/x.iso", 10), ("notes.txt", 5)):
            with open(os.path.join(self.stick, rel), "wb") as fh:
                fh.write(b"x" * size)
        os.symlink("/etc/passwd", os.path.join(self.stick, "link.iso"))
        self.system = FakeSystem(self.stick)
        self.media = Media(self.isos, runner=self.system)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_removable_supported_filesystems_are_offered(self):
        devices = {d["device"]: d for d in self.media.devices()}
        self.assertEqual(sorted(devices), ["/dev/sdb1", "/dev/sdc"])
        self.assertEqual(devices["/dev/sdb1"]["fstype"], "exFAT")
        self.assertEqual(devices["/dev/sdb1"]["model"], "SanDisk Ultra Fit")
        self.assertEqual(devices["/dev/sdc"]["fstype"], "NTFS")

    def test_internal_and_live_disks_are_rejected(self):
        for device in ("/dev/nvme0n1p1", "/dev/sda1", "/dev/sdb2"):
            with self.assertRaises(ToolstackError):
                self.media.mount(device)

    def test_list_isos_mounts_read_only(self):
        files = self.media.isos("/dev/sdb1")
        self.assertEqual([f["file"] for f in files], ["Win7 SP1.iso", "Software/Linux/debian.ISO"])
        self.assertEqual(files[0]["size"], 5000)
        self.assertIn(["udisksctl", "mount", "--no-user-interaction", "-b", "/dev/sdb1", "-o", "ro,noexec"],
                      self.system.calls)

    def test_import_copies_into_library(self):
        job = self.media.start_import("/dev/sdb1", "Win7 SP1.iso")
        self.assertEqual(job["name"], "Win7_SP1.iso")
        for _ in range(100):
            if self.media.job_list()[0]["state"] != "running":
                break
            time.sleep(0.02)
        done = self.media.job_list()[0]
        self.assertEqual((done["state"], done["copied"]), ("done", 5000))
        self.assertEqual(os.path.getsize(os.path.join(self.isos, "Win7_SP1.iso")), 5000)
        with self.assertRaises(ToolstackError):
            self.media.start_import("/dev/sdb1", "Win7 SP1.iso")  # already in the library

    def test_import_rejects_paths_outside_the_stick(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "link.iso", "notes.txt"):
            with self.assertRaises(ToolstackError):
                self.media.start_import("/dev/sdb1", bad)

    def test_eject(self):
        self.media.isos("/dev/sdb1")
        self.media.eject("/dev/sdb1")
        self.assertIn(["udisksctl", "unmount", "--no-user-interaction", "-b", "/dev/sdb1"], self.system.calls)
        self.assertIn(["udisksctl", "power-off", "--no-user-interaction", "-b", "/dev/sdb"], self.system.calls)


if __name__ == "__main__":
    unittest.main()
