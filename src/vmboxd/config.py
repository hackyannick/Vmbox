"""Configuration loading for vmboxd."""

import configparser
import os
from dataclasses import dataclass, fields

DEFAULT_CONFIG_PATH = "/etc/vmbox/vmbox.conf"


@dataclass
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8470
    socket_path: str = "/run/vmbox/vmboxd.sock"
    state_dir: str = "/var/lib/vmbox"
    iso_dir: str = "/var/lib/vmbox/isos"
    ui_dir: str = "/usr/share/vmbox/ui"
    vboxmanage: str = "VBoxManage"
    display: str = ":0"
    xauthority: str = ""
    # "vbox" drives the real VirtualBox, "fake" simulates it (development/demo).
    backend: str = "vbox"
    session_timeout: int = 12 * 3600
    # Seconds of inactivity in the dashboard after which the screen locks (0 = never).
    idle_lock: int = 900
    live: bool = False

    @property
    def auth_file(self):
        return os.path.join(self.state_dir, "auth.json")


def _coerce(value, typ):
    if typ is bool:
        return str(value).strip().lower() in ("1", "yes", "true", "on")
    if typ is int:
        return int(value)
    return str(value)


def load(path=None, overrides=None):
    """Load the [vmboxd] section of ``path`` and apply ``overrides``."""
    cfg = Config()
    path = path or os.environ.get("VMBOX_CONFIG", DEFAULT_CONFIG_PATH)
    values = {}
    if path and os.path.exists(path):
        parser = configparser.ConfigParser()
        parser.read(path)
        if parser.has_section("vmboxd"):
            values.update(parser["vmboxd"])
    values.update({k: v for k, v in (overrides or {}).items() if v is not None})
    types = {f.name: f.type for f in fields(Config)}
    for key, value in values.items():
        if key not in types:
            continue
        typ = types[key]
        if isinstance(typ, str):
            typ = {"int": int, "bool": bool, "str": str}[typ]
        setattr(cfg, key, _coerce(value, typ))
    if not cfg.xauthority:
        cfg.xauthority = os.path.join(os.path.expanduser("~"), ".Xauthority")
    return cfg
