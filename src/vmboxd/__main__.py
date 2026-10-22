"""Entry point: ``python3 -m vmboxd``."""

import argparse
import logging
import os
import sys

from . import __version__, config
from .auth import Auth
from .display import VirtualDisplay, X11Display
from .fakevbox import FakeVBoxManage
from .host import FakeHost, Host, kvm_loaded
from .media import FakeMedia, Media
from .server import App, serve
from .toolstack import Toolstack
from .vbox import VBoxManage


def _is_live():
    try:
        with open("/proc/cmdline") as fh:
            return "boot=live" in fh.read().split()
    except OSError:
        return False


def build_app(cfg):
    if cfg.backend == "fake":
        fake = FakeVBoxManage(seed=True, base=os.path.join(cfg.state_dir, "vms"))
        vbox = VBoxManage(runner=fake)
        display = VirtualDisplay(running=vbox.running_vms)
        host = FakeHost(cfg.state_dir, vbox.version, live=cfg.live)
        media = FakeMedia(cfg.iso_dir, os.path.join(cfg.state_dir, "demo-stick"))
        ga_paths = (os.path.join(cfg.state_dir, "VBoxGuestAdditions.iso"),)
        open(ga_paths[0], "a").close()
    else:
        vbox = VBoxManage(cfg.vboxmanage, gui_env={"DISPLAY": cfg.display, "XAUTHORITY": cfg.xauthority})
        display = X11Display(cfg.display, cfg.xauthority)
        host = Host(cfg.state_dir, vbox.version, live=cfg.live)
        media = Media(cfg.iso_dir)
        ga_paths = None
    os.makedirs(cfg.iso_dir, exist_ok=True)
    toolstack = Toolstack(vbox, display, cfg.iso_dir,
                          kvm_loaded=kvm_loaded if cfg.backend != "fake" else (lambda: False),
                          **({"guest_additions_paths": ga_paths} if ga_paths else {}))
    auth = Auth(cfg.auth_file, cfg.session_timeout)
    auth.locked = True
    return App(cfg, toolstack, auth, host, media)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vmboxd", description="VMBox toolstack daemon")
    parser.add_argument("--config", help="Konfigurationsdatei (Standard: %s)" % config.DEFAULT_CONFIG_PATH)
    parser.add_argument("--backend", choices=["vbox", "fake"])
    parser.add_argument("--demo", action="store_true", help="simuliertes VirtualBox mit Beispiel-VMs")
    parser.add_argument("--port", type=int, dest="listen_port")
    parser.add_argument("--state-dir", dest="state_dir")
    parser.add_argument("--ui-dir", dest="ui_dir")
    parser.add_argument("--socket", dest="socket_path")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--version", action="version", version="vmboxd " + __version__)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    overrides = {k: getattr(args, k) for k in ("listen_port", "state_dir", "ui_dir", "socket_path", "backend")}
    if args.demo:
        overrides["backend"] = "fake"
    if args.state_dir:
        overrides["iso_dir"] = os.path.join(args.state_dir, "isos")
    cfg = config.load(args.config, overrides)
    cfg.live = cfg.live or _is_live()
    app = build_app(cfg)
    app.start_workers()
    try:
        serve(app)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        logging.error("cannot start: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
