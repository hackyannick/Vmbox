import unittest

import context  # noqa: F401
from vmboxd.display import DisplayError, X11Display

XRANDR = """Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x 16384
HDMI-1 connected 1920x1080+1920+0 (normal left inverted right x axis y axis) 527mm x 296mm
   1920x1080     60.00*+
eDP-1 connected primary 1920x1200+0+0 (normal left inverted right x axis y axis) 302mm x 189mm
   1920x1200     60.00*+
DP-1 disconnected (normal left inverted right x axis y axis)
"""


class ScreenSizeTest(unittest.TestCase):
    def display(self, outputs):
        d = X11Display(":0")

        def run(*argv):
            if argv[0] not in outputs:
                raise DisplayError("missing")
            return outputs[argv[0]]
        d._run = run
        return d

    def test_primary_monitor_wins(self):
        self.assertEqual(self.display({"xrandr": XRANDR}).screen_size(), (1920, 1200))

    def test_first_connected_monitor_without_primary(self):
        out = XRANDR.replace(" primary", "")
        self.assertEqual(self.display({"xrandr": out}).screen_size(), (1920, 1080))

    def test_fallback_to_xdpyinfo(self):
        d = self.display({"xdpyinfo": "screen #0:\n  dimensions:    1366x768 pixels (361x203 millimeters)\n"})
        self.assertEqual(d.screen_size(), (1366, 768))

    def test_no_display(self):
        self.assertIsNone(self.display({}).screen_size())


if __name__ == "__main__":
    unittest.main()
