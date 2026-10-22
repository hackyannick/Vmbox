import unittest

import context  # noqa: F401
from vmboxd.fakevbox import FakeVBoxManage
from vmboxd.vbox import VBoxError, VBoxManage, parse_blocks, parse_machinereadable, parse_snapshots


class ParserTest(unittest.TestCase):
    def test_machinereadable(self):
        data = parse_machinereadable(
            'name="Büro"\nmemory=4096\n"SATA-0-0"="/vms/a b.vdi"\ndescription="x=y"\nnic1="nat"\n')
        self.assertEqual(data["name"], "Büro")
        self.assertEqual(data["memory"], "4096")
        self.assertEqual(data["SATA-0-0"], "/vms/a b.vdi")
        self.assertEqual(data["description"], "x=y")

    def test_blocks(self):
        text = ("Host USB Devices:\n\nUUID:               1111\nVendorId:           0x0781 (0781)\n"
                "Current State:      Busy\n\nUUID:               2222\nVendorId:           0x1050 (1050)\n")
        blocks = parse_blocks(text)
        self.assertEqual([b["UUID"] for b in blocks], ["1111", "2222"])
        self.assertEqual(blocks[0]["Current State"], "Busy")

    def test_snapshot_tree(self):
        text = ('SnapshotName="root"\nSnapshotUUID="u0"\nSnapshotName-1="child"\nSnapshotUUID-1="u1"\n'
                'SnapshotName-1-1="grandchild"\nSnapshotUUID-1-1="u11"\nSnapshotName-2="second"\nSnapshotUUID-2="u2"\n'
                'CurrentSnapshotName="child"\nCurrentSnapshotUUID="u1"\n')
        snaps = parse_snapshots(text)
        self.assertEqual([s["name"] for s in snaps], ["root", "child", "grandchild", "second"])
        self.assertEqual([s["depth"] for s in snaps], [0, 1, 2, 1])
        self.assertEqual(snaps[2]["parent"], "u1")
        self.assertEqual(snaps[3]["parent"], "u0")
        self.assertTrue(snaps[1]["current"])


class VBoxManageTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeVBoxManage()
        self.vbox = VBoxManage(runner=self.fake)

    def test_create_and_list(self):
        uuid = self.vbox.create_vm("Test", "Linux_64")
        self.assertEqual(self.vbox.list_vms(), [("Test", uuid)])
        self.assertEqual(self.vbox.vm_info(uuid)["ostype"], "Linux_64")

    def test_error_message_is_cleaned(self):
        with self.assertRaises(VBoxError) as ctx:
            self.vbox.vm_info("does-not-exist")
        self.assertTrue(str(ctx.exception).startswith("Could not find"))

    def test_extradata_roundtrip(self):
        uuid = self.vbox.create_vm("Test", "Linux_64")
        self.vbox.set_extradata(uuid, "VMBox/Color", "#123456")
        self.vbox.set_extradata(uuid, "Other/Key", "x")
        self.assertEqual(self.vbox.extradata(uuid, "VMBox/"), {"VMBox/Color": "#123456"})
        self.vbox.set_extradata(uuid, "VMBox/Color", None)
        self.assertEqual(self.vbox.extradata(uuid, "VMBox/"), {})

    def test_usb_devices(self):
        devices = self.vbox.usb_devices()
        self.assertEqual(devices[0]["vendor_id"], "0781")
        self.assertEqual(devices[0]["product_id"], "5583")
        self.assertEqual(devices[0]["state"], "busy")

    def test_start_uses_separate_frontend(self):
        uuid = self.vbox.create_vm("Test", "Linux_64")
        self.vbox.start(uuid)
        self.assertIn(["startvm", uuid, "--type", "separate"], self.fake.calls)
        self.assertEqual(self.vbox.running_vms(), {uuid})


if __name__ == "__main__":
    unittest.main()
