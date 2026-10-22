"""Runs the real installer in a pseudo terminal - with stderr *not* on the
terminal, exactly like systemd started it - and checks that the dialogs after
the welcome screen are visible.  Nothing is written to disk: the test cancels
at the disk selection."""

import os
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

import context  # noqa: F401

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INSTALLER = os.path.join(ROOT, "src", "bin", "vmbox-installer")

FAKE_LSBLK = """#!/bin/sh
case "$*" in
  *PKNAME*) echo "" ;;
  *NAME,SIZE,TYPE*) echo "/dev/vdtest 107374182400 disk" ;;
  *MODEL*) echo "Testplatte" ;;
  *TRAN*) echo "sata" ;;
  *SIZE*) echo "107374182400" ;;
esac
"""


@unittest.skipUnless(shutil.which("whiptail") and os.geteuid() == 0, "needs whiptail and root")
class InstallerTerminalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        medium = os.path.join(self.tmp.name, "medium", "live")
        os.makedirs(medium)
        open(os.path.join(medium, "filesystem.squashfs"), "w").close()
        bindir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "lsblk"), "w") as fh:
            fh.write(FAKE_LSBLK)
        os.chmod(os.path.join(bindir, "lsblk"), 0o755)
        self.env = dict(os.environ, TERM="linux", PATH=bindir + ":" + os.environ["PATH"],
                        VMBOX_MEDIUM=os.path.dirname(medium),
                        VMBOX_INSTALLER_LOG=os.path.join(self.tmp.name, "install.log"))

    def tearDown(self):
        self.tmp.cleanup()

    def _wait_for(self, fd, text, timeout=15):
        """Read terminal output until ``text`` appears (slow CI runners!)."""
        out, deadline = b"", time.time() + timeout
        while time.time() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
                if text.encode() in out:
                    # give whiptail a moment to finish drawing the dialog
                    time.sleep(0.3)
                    while select.select([fd], [], [], 0.1)[0]:
                        out += os.read(fd, 65536)
                    return out.decode(errors="replace")
        self.fail("%r did not appear on the terminal; got: %r" % (text, out[-400:]))

    def test_dialogs_visible_when_stderr_is_not_a_terminal(self):
        master, slave = pty.openpty()
        stderr = open(os.path.join(self.tmp.name, "journal.txt"), "w")
        proc = subprocess.Popen(["bash", INSTALLER], stdin=slave, stdout=slave, stderr=stderr,
                                env=self.env, start_new_session=True)
        try:
            self._wait_for(master, "Willkommen")
            os.write(master, b"\r")                      # "Weiter"
            screen = self._wait_for(master, "Auf welche Festplatte")
            self.assertIn("/dev/vdtest", screen)
            # Tab Tab -> <Cancel>, Enter. (Escape would hit newt's escape-sequence delay.)
            os.write(master, b"\t\t\r")
            self._wait_for(master, "abgebrochen")
            os.write(master, b"\r")
            self.assertEqual(proc.wait(timeout=15), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
            stderr.close()
            os.close(master)
            os.close(slave)
        with open(os.path.join(self.tmp.name, "journal.txt")) as fh:
            self.assertNotIn("Festplatte", fh.read())


if __name__ == "__main__":
    unittest.main()
