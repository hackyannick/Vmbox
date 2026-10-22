"""Display switching - the VirtualBox equivalent of OpenXT's surfman/input server.

Every running user VM owns exactly one maximised, undecorated VirtualBox
window on the X display.  "Switching" to a VM means raising and focusing
that window; switching to the UI raises the VMBox dashboard.  Windows are
matched to VMs through the process that owns them: VirtualBoxVM is always
started with ``--startvm <uuid>`` on its command line.
"""

import logging
import os
import re
import subprocess

log = logging.getLogger(__name__)

UI_WINDOW_TITLE = "VMBox Dashboard"


class DisplayError(RuntimeError):
    pass


class X11Display:
    def __init__(self, display=":0", xauthority=None):
        self.env = dict(os.environ, DISPLAY=display)
        if xauthority:
            self.env["XAUTHORITY"] = xauthority

    def _run(self, *argv):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, env=self.env, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DisplayError(str(exc)) from exc
        if proc.returncode != 0:
            raise DisplayError(proc.stderr.strip() or "%s failed" % argv[0])
        return proc.stdout

    def available(self):
        try:
            self._run("xprop", "-root", "_NET_SUPPORTING_WM_CHECK")
            return True
        except DisplayError:
            return False

    def windows(self):
        """Return [(window_id, pid, title)] for all managed windows."""
        result = []
        for line in self._run("wmctrl", "-lp").splitlines():
            parts = line.split(None, 4)
            if len(parts) < 4:
                continue
            title = parts[4] if len(parts) > 4 else ""
            # wmctrl prints "<id> <desktop> <pid> <host> <title>"; host may be "N/A".
            try:
                result.append((int(parts[0], 16), int(parts[2]), title))
            except ValueError:
                continue
        return result

    @staticmethod
    def _cmdline(pid):
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as fh:
                return fh.read().decode(errors="replace").split("\0")
        except OSError:
            return []

    def window_for_vm(self, uuid):
        uuid = uuid.lower()
        candidates = []
        for wid, pid, title in self.windows():
            if pid <= 0:
                continue
            argv = [a.lower() for a in self._cmdline(pid)]
            if uuid in argv:
                candidates.append((wid, title))
        # Prefer the main machine window ("<name> [Running] - Oracle VirtualBox").
        candidates.sort(key=lambda c: "[" not in c[1])
        return candidates[0][0] if candidates else None

    def ui_window(self):
        for wid, _, title in self.windows():
            if title == UI_WINDOW_TITLE:
                return wid
        return None

    def activate(self, wid):
        self._run("wmctrl", "-i", "-a", "0x%08x" % wid)

    def active_window(self):
        out = self._run("xprop", "-root", "-notype", "_NET_ACTIVE_WINDOW")
        m = re.search(r"0x([0-9a-fA-F]+)", out)
        return int(m.group(1), 16) if m else None

    def vm_for_window(self, wid, uuids):
        for w, pid, _ in self.windows():
            if w != wid:
                continue
            argv = [a.lower() for a in self._cmdline(pid)]
            for uuid in uuids:
                if uuid.lower() in argv:
                    return uuid
        return None

    def launch_frontend(self, uuid):
        """(Re)attach a VirtualBox window to an already running VM."""
        proc = subprocess.Popen(["VirtualBoxVM", "--startvm", uuid, "--separate"], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        self._children = [p for p in getattr(self, "_children", []) if p.poll() is None] + [proc]

    def has_window(self, uuid):
        try:
            return self.window_for_vm(uuid) is not None
        except DisplayError:
            return True  # cannot tell - do not spawn frontends blindly

    # -- high level -------------------------------------------------------
    def show_vm(self, uuid):
        wid = self.window_for_vm(uuid)
        if wid is None:
            raise DisplayError("Kein Fenster für diese VM gefunden")
        self.activate(wid)

    def show_ui(self):
        wid = self.ui_window()
        if wid is None:
            raise DisplayError("Dashboard-Fenster nicht gefunden")
        self.activate(wid)

    def focused(self, uuids):
        """Return the uuid of the VM that has focus, or None for the UI."""
        try:
            wid = self.active_window()
        except DisplayError:
            return None
        if not wid:
            return None
        return self.vm_for_window(wid, uuids)

    def idle_ms(self):
        try:
            return int(self._run("xprintidle").strip())
        except (DisplayError, ValueError):
            return 0


class VirtualDisplay:
    """In-memory display used by the fake backend and the test-suite."""

    def __init__(self, running=None):
        self.current = None
        self._running = running or (lambda: set())

    def available(self):
        return True

    def show_vm(self, uuid):
        if uuid not in self._running():
            raise DisplayError("Kein Fenster für diese VM gefunden")
        self.current = uuid

    def show_ui(self):
        self.current = None

    def launch_frontend(self, uuid):
        pass

    def has_window(self, uuid):
        return True

    def focused(self, uuids):
        if self.current and self.current in uuids and self.current in self._running():
            return self.current
        self.current = None
        return None

    def idle_ms(self):
        return 0
