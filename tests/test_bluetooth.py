import os
import sys
import tempfile
import time
import unittest

import context  # noqa: F401
from vmboxd import host as hostmod
from vmboxd.host import BluetoothSession, Host, HostError

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_bluetoothctl.py")

SHOW = """Controller 3C:21:9C:00:11:22 (public)
\tName: vmbox
\tAlias: vmbox
\tPowered: yes
\tDiscoverable: no
\tDiscovering: no
"""
DEVICES = """Device D4:12:43:7A:01:22 Keyboard K380
Device F2:9C:31:0B:5E:10 MX Master 3
Device 5C:12:34:56:78:9A 5C-12-34-56-78-9A
"""
INFO = {
    "D4:12:43:7A:01:22": "Device D4:12:43:7A:01:22 (random)\n\tName: Keyboard K380\n\tAlias: Keyboard K380\n\tIcon: input-keyboard\n"
                         "\tPaired: yes\n\tTrusted: yes\n\tConnected: yes\n\tBattery Percentage: 0x46 (70)\n",
    "F2:9C:31:0B:5E:10": "Device F2:9C:31:0B:5E:10 (random)\n\tName: MX Master 3\n\tAlias: MX Master 3\n\tIcon: input-mouse\n"
                         "\tPaired: no\n\tTrusted: no\n\tConnected: no\n",
    "5C:12:34:56:78:9A": "Device 5C:12:34:56:78:9A (random)\n\tAlias: 5C-12-34-56-78-9A\n\tPaired: no\n\tConnected: no\n",
}


class BluetoothTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.outputs = {}
        self._orig = hostmod._run

        def run(*argv, timeout=30):
            self.calls.append(argv)
            args = argv[1:] if argv[0] == "bluetoothctl" else argv
            if argv[0] == "rfkill":
                return ""
            key = " ".join(a for a in args if a not in ("--agent", "NoInputNoOutput"))
            if key in self.outputs:
                value = self.outputs[key]
                if isinstance(value, Exception):
                    raise value
                return value
            if args[0] == "show":
                return SHOW
            if args[0] == "devices":
                return DEVICES
            if args[0] == "info":
                return INFO[args[1]]
            return "%s successful\n" % args[0].capitalize()
        hostmod._run = run
        self.host = Host("/tmp")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        hostmod._run = self._orig

    def test_adapter_and_devices(self):
        adapter = self.host.bluetooth_adapter()
        self.assertEqual((adapter["available"], adapter["powered"], adapter["address"]), (True, True, "3C:21:9C:00:11:22"))
        devices = self.host.bluetooth_devices()
        self.assertEqual([d["name"] for d in devices], ["Keyboard K380", "MX Master 3"])   # anonymous one skipped
        self.assertEqual(devices[0]["battery"], 70)
        self.assertTrue(devices[0]["connected"])
        self.assertEqual(devices[1]["icon"], "input-mouse")

    def test_no_adapter(self):
        self.outputs["show"] = "No default controller available\n"
        adapter = self.host.bluetooth_adapter()
        self.assertFalse(adapter["available"])
        self.assertEqual(adapter["error"], "Kein Bluetooth-Adapter gefunden")

    def _session(self, mode):
        log = os.path.join(self.tmp.name, "bt.log")
        os.environ["FAKE_BT_LOG"] = log
        os.environ["FAKE_BT_MODE"] = mode
        self.host._bt_session = BluetoothSession([sys.executable, FAKE])
        self.addCleanup(lambda: self.host._bt_session.proc and self.host._bt_session.proc.kill())
        return log

    def test_le_mouse_pairs_in_one_session_while_scanning(self):
        log = self._session("ok")
        self.host.bluetooth_pair("f2:9c:31:0b:5e:10")
        for _ in range(50):                      # "scan off" is sent after the result
            with open(log) as fh:
                commands = fh.read().split("\n")
            if "scan off" in commands:
                break
            time.sleep(0.05)
        self.assertEqual(commands[:6], ["agent NoInputNoOutput", "default-agent", "scan on",
                                        "pair F2:9C:31:0B:5E:10", "trust F2:9C:31:0B:5E:10", "connect F2:9C:31:0B:5E:10"])
        self.assertIn("scan off", commands)

    def test_pairing_confirmation_is_answered(self):
        log = self._session("confirm")
        self.host.bluetooth_pair("F2:9C:31:0B:5E:10")
        with open(log) as fh:
            self.assertIn("yes", fh.read().split("\n"))

    def test_pairing_failure_message(self):
        self._session("authfail")
        with self.assertRaises(HostError) as ctx:
            self.host.bluetooth_pair("F2:9C:31:0B:5E:10")
        self.assertIn("Kopplungsmodus", str(ctx.exception))

    def test_device_not_in_pairing_mode(self):
        self._session("invisible")
        original = BluetoothSession.seen
        BluetoothSession.seen = lambda self, mac, timeout: original(self, mac, 1)
        self.addCleanup(lambda: setattr(BluetoothSession, "seen", original))
        with self.assertRaises(HostError) as ctx:
            self.host.bluetooth_pair("F2:9C:31:0B:5E:10")
        self.assertIn("nicht gefunden", str(ctx.exception))

    def test_power_on_unblocks_rfkill(self):
        self.host.bluetooth_power(True)
        self.assertIn(("rfkill", "unblock", "bluetooth"), self.calls)
        self.assertIn(("bluetoothctl", "power", "on"), self.calls)

    def test_invalid_address(self):
        with self.assertRaises(HostError):
            self.host.bluetooth_remove("00:11; rm -rf /")


if __name__ == "__main__":
    unittest.main()
