"""HTTP/JSON API of vmboxd.

Two listeners share one request handler:

* TCP on 127.0.0.1 for the VMBox user interface (session cookie required),
* a Unix socket for ``vmbox-ctl`` and the hotkeys (trusted via file
  permissions of the socket).
"""

import http.cookies
import json
import logging
import mimetypes
import os
import re
import socket
import socketserver
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .auth import AuthError
from .display import DisplayError
from .host import HostError
from .templates import COLORS, TEMPLATES
from .toolstack import ToolstackError
from .vbox import UUID_RE, VBoxError

log = logging.getLogger(__name__)

COOKIE = "vmbox_session"
MAX_JSON = 1 << 20
MAX_ISO = 32 << 30
ROUTES = []


def route(method, pattern, public=False):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, public))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _uuid(value):
    if not UUID_RE.match(value):
        raise ApiError("Ungültige UUID", 400)
    return value


class App:
    """Glue between HTTP handlers, the toolstack, authentication and the host."""

    def __init__(self, cfg, toolstack, auth, host, media=None):
        self.cfg = cfg
        self.toolstack = toolstack
        self.auth = auth
        self.host = host
        self.media = media
        self.started = time.time()
        self._autostarted_user_vms = False
        toolstack.is_locked = lambda: self.auth.locked

    def on_unlock(self):
        """Called after a successful login: start "start on boot" user VMs once."""
        if not self._autostarted_user_vms:
            self._autostarted_user_vms = True
            threading.Thread(target=self.toolstack.autostart, daemon=True, name="autostart").start()

    # -- background workers ---------------------------------------------------
    def start_workers(self):
        def service_autostart():
            try:
                self.toolstack.apply_global_settings()
                self.toolstack.autostart(types=("service",))
            except (VBoxError, ToolstackError) as exc:
                log.error("service VM autostart failed: %s", exc)

        def usb_loop():
            while True:
                time.sleep(3)
                try:
                    self.toolstack.usb_autoassign()
                except Exception:  # noqa: BLE001 - keep the worker alive
                    log.exception("USB auto-assignment failed")

        def guard_loop():
            """While locked, the dashboard (lock screen) must stay in front; after
            ``idle_lock`` seconds without input the screen is locked."""
            ticks = 0
            while True:
                time.sleep(0.5)
                ticks += 1
                try:
                    if self.auth.locked:
                        if self.toolstack.focus() is not None:
                            self.toolstack.switch_ui()
                        continue
                    if self.cfg.idle_lock and self.toolstack.display.idle_ms() > self.cfg.idle_lock * 1000:
                        log.info("idle timeout - locking the screen")
                        self.lock()
                    elif ticks % 10 == 0 and self.toolstack.display.available():
                        self.toolstack.ensure_frontends()
                except Exception:  # noqa: BLE001
                    time.sleep(5)

        for target in (service_autostart, usb_loop, guard_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def lock(self):
        self.auth.lock()
        try:
            self.toolstack.switch_ui()
        except ToolstackError:
            pass

    def state(self, authenticated):
        data = {
            "version": __version__,
            "setup_required": self.auth.setup_required,
            "authenticated": authenticated,
            "locked": self.auth.locked,
            "live": self.cfg.live,
            "time": int(time.time()),
            "idle_lock": self.cfg.idle_lock,
        }
        if authenticated:
            data["focus"] = self.toolstack.focus()
            data["vms"] = self.toolstack.list_vms()
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = "vmboxd/" + __version__
    protocol_version = "HTTP/1.1"
    trusted = False  # True for the Unix socket listener

    @property
    def app(self):
        return self.server.app

    def address_string(self):
        return "unix" if self.trusted else super().address_string()

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- helpers --------------------------------------------------------------
    def _send(self, status, body=b"", ctype="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                         "frame-ancestors 'none'; form-action 'none'")
        if ctype == "application/json":
            self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, data, headers=None):
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), headers=headers)

    def _body(self):
        self._consumed = True
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_JSON:
            raise ApiError("Anfrage zu groß", 413)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ApiError("Ungültiges JSON") from exc
        if not isinstance(data, dict):
            raise ApiError("JSON-Objekt erwartet")
        return data

    def _token(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        morsel = cookie.get(COOKIE)
        return morsel.value if morsel else None

    def _authenticated(self):
        return self.trusted or self.app.auth.valid(self._token())

    def _session_cookie(self, token, max_age=None):
        value = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (COOKIE, token)
        if max_age is not None:
            value += "; Max-Age=%d" % max_age
        return {"Set-Cookie": value}

    def _check_origin(self):
        """Defend the localhost API against DNS rebinding and CSRF."""
        if self.trusted:
            return
        port = self.server.server_address[1]
        allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host") not in allowed_hosts:
            raise ApiError("Ungültiger Host", 403)
        if self.command in ("GET", "HEAD"):
            return
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc not in allowed_hosts:
            raise ApiError("Ungültiger Ursprung", 403)
        if self.command == "DELETE":
            return  # not a "simple" request - browsers require a CORS preflight
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json" and self.headers.get("X-VMBox-Upload") != "1":
            raise ApiError("Content-Type application/json erwartet", 415)

    # -- dispatch -------------------------------------------------------------
    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _dispatch(self):
        self._consumed = False
        try:
            self._route()
        finally:
            self._discard_body()

    def _discard_body(self):
        """Keep-alive safety: never leave an unread request body on the socket."""
        if self._consumed or self.close_connection:
            return
        length = int(self.headers.get("Content-Length") or 0)
        if 0 < length <= MAX_JSON:
            self.rfile.read(length)
        elif length:
            self.close_connection = True

    def _route(self):
        url = urllib.parse.urlsplit(self.path)
        self.query = dict(urllib.parse.parse_qsl(url.query))
        method = "GET" if self.command == "HEAD" else self.command
        try:
            self._check_origin()
            if not url.path.startswith("/api/"):
                if method != "GET":
                    raise ApiError("Nicht gefunden", 404)
                return self._static(url.path)
            for m, pattern, fn, public in ROUTES:
                match = pattern.match(url.path)
                if match and m == method:
                    if not public and not self._authenticated():
                        raise ApiError("Anmeldung erforderlich", 401)
                    return fn(self, *match.groups())
            raise ApiError("Nicht gefunden", 404)
        except (ApiError, ToolstackError) as exc:
            self._error(exc.status, str(exc))
        except AuthError as exc:
            self._error(403, str(exc))
        except (VBoxError, HostError, DisplayError) as exc:
            self._error(502, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # noqa: BLE001
            log.exception("internal error handling %s %s", self.command, self.path)
            self._error(500, "Interner Fehler")

    def _error(self, status, message):
        # The request body may not have been consumed - do not reuse the connection.
        self.close_connection = True
        self._json(status, {"error": message}, {"Connection": "close"})

    def _static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        elif path == "/bar":
            path = "/bar.html"
        root = os.path.realpath(self.app.cfg.ui_dir)
        full = os.path.realpath(os.path.join(root, path.lstrip("/")))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            raise ApiError("Nicht gefunden", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype, {"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------- #
# session
# ---------------------------------------------------------------------- #
@route("GET", r"/api/state", public=True)
def api_state(h):
    h._json(200, h.app.state(h._authenticated()))


@route("POST", r"/api/setup", public=True)
def api_setup(h):
    token = h.app.auth.setup(h._body().get("password"))
    h.app.on_unlock()
    h._json(200, {"ok": True}, h._session_cookie(token))


@route("POST", r"/api/login", public=True)
def api_login(h):
    token = h.app.auth.login(h._body().get("password"))
    h.app.on_unlock()
    h._json(200, {"ok": True}, h._session_cookie(token))


@route("POST", r"/api/logout")
def api_logout(h):
    h.app.auth.logout(h._token())
    h.app.lock()
    h._json(200, {"ok": True}, h._session_cookie("", 0))


@route("POST", r"/api/lock")
def api_lock(h):
    h.app.lock()
    h._json(200, {"ok": True})


@route("POST", r"/api/password")
def api_password(h):
    body = h._body()
    h.app.auth.change_password(body.get("old"), body.get("new"))
    h._json(200, {"ok": True})


# ---------------------------------------------------------------------- #
# VMs
# ---------------------------------------------------------------------- #
@route("GET", r"/api/templates")
def api_templates(h):
    h._json(200, {"templates": TEMPLATES, "colors": COLORS})


@route("GET", r"/api/vms")
def api_vms(h):
    h._json(200, {"vms": h.app.toolstack.list_vms()})


@route("POST", r"/api/vms")
def api_create_vm(h):
    h._json(201, h.app.toolstack.create_vm(h._body()))


@route("GET", r"/api/vms/([^/]+)")
def api_vm(h, ref):
    h._json(200, h.app.toolstack.find_vm(urllib.parse.unquote(ref)))


@route("PATCH", r"/api/vms/([0-9a-fA-F-]{36})")
def api_update_vm(h, uuid):
    h._json(200, h.app.toolstack.update_vm(_uuid(uuid), h._body()))


@route("DELETE", r"/api/vms/([0-9a-fA-F-]{36})")
def api_delete_vm(h, uuid):
    h.app.toolstack.delete_vm(_uuid(uuid))
    h._json(200, {"ok": True})


@route("POST", r"/api/vms/([^/]+)/(start|shutdown|poweroff|pause|resume|reboot|save|discard|switch)")
def api_vm_action(h, ref, action):
    vm = h.app.toolstack.find_vm(urllib.parse.unquote(ref))
    h.app.toolstack.action(vm["uuid"], action)
    h._json(200, {"ok": True})


@route("POST", r"/api/vms/([^/]+)/guest-additions")
def api_guest_additions(h, ref):
    vm = h.app.toolstack.find_vm(urllib.parse.unquote(ref))
    h._json(200, h.app.toolstack.insert_guest_additions(vm["uuid"]))


@route("GET", r"/api/vms/([0-9a-fA-F-]{36})/snapshots")
def api_snapshots(h, uuid):
    h._json(200, {"snapshots": h.app.toolstack.snapshots(_uuid(uuid))})


@route("POST", r"/api/vms/([0-9a-fA-F-]{36})/snapshots")
def api_take_snapshot(h, uuid):
    body = h._body()
    h.app.toolstack.take_snapshot(_uuid(uuid), body.get("name"), str(body.get("description", ""))[:500])
    h._json(201, {"ok": True})


@route("POST", r"/api/vms/([0-9a-fA-F-]{36})/snapshots/([0-9a-fA-F-]{36})/restore")
def api_restore_snapshot(h, uuid, snap):
    h.app.toolstack.restore_snapshot(_uuid(uuid), snap)
    h._json(200, {"ok": True})


@route("DELETE", r"/api/vms/([0-9a-fA-F-]{36})/snapshots/([0-9a-fA-F-]{36})")
def api_delete_snapshot(h, uuid, snap):
    h.app.toolstack.delete_snapshot(_uuid(uuid), snap)
    h._json(200, {"ok": True})


# ---------------------------------------------------------------------- #
# switching
# ---------------------------------------------------------------------- #
@route("POST", r"/api/switch/ui")
def api_switch_ui(h):
    h.app.toolstack.switch_ui()
    h._json(200, {"ok": True})


@route("POST", r"/api/switch/(next|prev)")
def api_switch_cycle(h, direction):
    h.app.toolstack.switch_cycle(1 if direction == "next" else -1)
    h._json(200, {"ok": True})


@route("POST", r"/api/switch/slot/([1-9])")
def api_switch_slot(h, slot):
    h.app.toolstack.switch_to(slot)
    h._json(200, {"ok": True})


# ---------------------------------------------------------------------- #
# USB
# ---------------------------------------------------------------------- #
@route("GET", r"/api/usb")
def api_usb(h):
    h._json(200, {"devices": h.app.toolstack.usb_devices()})


@route("POST", r"/api/usb/([0-9a-fA-F-]{36})/assign")
def api_usb_assign(h, dev):
    body = h._body()
    vm = h.app.toolstack.find_vm(str(body.get("vm", "")))
    h.app.toolstack.usb_assign(dev, vm["uuid"], force=bool(body.get("force")))
    if "sticky" in body:
        h.app.toolstack.usb_set_sticky(dev, vm["uuid"], bool(body["sticky"]))
    h._json(200, {"ok": True})


@route("POST", r"/api/usb/([0-9a-fA-F-]{36})/release")
def api_usb_release(h, dev):
    h.app.toolstack.usb_release(dev)
    h._json(200, {"ok": True})


@route("POST", r"/api/usb/([0-9a-fA-F-]{36})/sticky")
def api_usb_sticky(h, dev):
    body = h._body()
    vm = h.app.toolstack.find_vm(str(body.get("vm", "")))
    h.app.toolstack.usb_set_sticky(dev, vm["uuid"], bool(body.get("sticky", True)))
    h._json(200, {"ok": True})


# ---------------------------------------------------------------------- #
# networks, media, host
# ---------------------------------------------------------------------- #
@route("GET", r"/api/cpu-profiles")
def api_cpu_profiles(h):
    h._json(200, {"profiles": h.app.toolstack.cpu_profiles()})


@route("GET", r"/api/networks")
def api_networks(h):
    h._json(200, {"networks": h.app.toolstack.networks()})


@route("GET", r"/api/wifi")
def api_wifi(h):
    h._json(200, {"networks": h.app.host.wifi_networks(), "devices": h.app.host.connections()})


@route("POST", r"/api/wifi/connect")
def api_wifi_connect(h):
    body = h._body()
    ssid = body.get("ssid")
    if not isinstance(ssid, str) or not 0 < len(ssid) <= 32:
        raise ApiError("Ungültige SSID")
    h.app.host.wifi_connect(ssid, body.get("password") or None)
    h._json(200, {"ok": True})


@route("GET", r"/api/isos")
def api_isos(h):
    h._json(200, {"isos": h.app.toolstack.isos()})


@route("POST", r"/api/isos")
def api_upload_iso(h):
    name = h.app.toolstack.safe_iso_name(h.query.get("name"))
    length = int(h.headers.get("Content-Length") or 0)
    if not 0 < length <= MAX_ISO:
        raise ApiError("Ungültige Dateigröße", 413)
    iso_dir = h.app.cfg.iso_dir
    os.makedirs(iso_dir, exist_ok=True)
    if os.statvfs(iso_dir).f_bavail * os.statvfs(iso_dir).f_frsize < length + (1 << 30):
        raise ApiError("Nicht genügend freier Speicher", 507)
    target = os.path.join(iso_dir, name)
    if os.path.exists(target):
        raise ApiError("Ein Abbild mit diesem Namen existiert bereits", 409)
    tmp = target + ".part"
    remaining = length
    h._consumed = True
    try:
        with open(tmp, "wb") as fh:
            while remaining:
                chunk = h.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    raise ApiError("Upload abgebrochen")
                fh.write(chunk)
                remaining -= len(chunk)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    h._json(201, {"name": name, "size": length})


@route("DELETE", r"/api/isos/([^/]+)")
def api_delete_iso(h, name):
    h.app.toolstack.delete_iso(urllib.parse.unquote(name))
    h._json(200, {"ok": True})


def _media(h):
    if h.app.media is None:
        raise ApiError("Wechseldatenträger werden nicht unterstützt", 404)
    return h.app.media


@route("GET", r"/api/media")
def api_media(h):
    media = _media(h)
    h._json(200, {"devices": media.devices(), "jobs": media.job_list()})


@route("POST", r"/api/media/isos")
def api_media_isos(h):
    h._json(200, {"isos": _media(h).isos(str(h._body().get("device", "")))})


@route("POST", r"/api/media/import")
def api_media_import(h):
    body = h._body()
    h._json(202, _media(h).start_import(str(body.get("device", "")), body.get("file")))


@route("POST", r"/api/media/eject")
def api_media_eject(h):
    _media(h).eject(str(h._body().get("device", "")))
    h._json(200, {"ok": True})


@route("GET", r"/api/host")
def api_host(h):
    h._json(200, h.app.host.info())


@route("POST", r"/api/host/save-vms")
def api_save_vms(h):
    h._json(200, {"saved": h.app.toolstack.save_all()})


@route("POST", r"/api/host/(poweroff|reboot|install)")
def api_host_power(h, action):
    if action == "install":
        h.app.host.start_installer()
    else:
        getattr(h.app.host, action)()
    h._json(200, {"ok": True})


# ---------------------------------------------------------------------- #
# servers
# ---------------------------------------------------------------------- #
class TCPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class UnixHandler(Handler):
    trusted = True


def serve(app):
    cfg = app.cfg
    tcp = TCPServer((cfg.listen_host, cfg.listen_port), Handler)
    tcp.app = app
    servers = [tcp]
    if cfg.socket_path:
        os.makedirs(os.path.dirname(cfg.socket_path), exist_ok=True)
        if os.path.exists(cfg.socket_path):
            os.unlink(cfg.socket_path)
        old_umask = os.umask(0o077)
        try:
            unix = UnixServer(cfg.socket_path, UnixHandler)
        finally:
            os.umask(old_umask)
        unix.app = app
        servers.append(unix)
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("vmboxd %s listening on http://%s:%d and %s", __version__,
             cfg.listen_host, cfg.listen_port, cfg.socket_path or "(no socket)")
    try:
        tcp.serve_forever()
    finally:
        for srv in servers:
            srv.server_close()
        if cfg.socket_path and os.path.exists(cfg.socket_path):
            os.unlink(cfg.socket_path)


def unix_request(socket_path, method, path, body=None, timeout=60):
    """Minimal HTTP client over the Unix socket (used by vmbox-ctl)."""
    data = json.dumps(body).encode() if body is not None else b""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path)
    try:
        request = ("%s %s HTTP/1.1\r\nHost: vmboxd\r\nContent-Type: application/json\r\n"
                   "Content-Length: %d\r\nConnection: close\r\n\r\n" % (method, path, len(data))).encode() + data
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    raw = b"".join(chunks)
    head, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(payload or b"{}")
