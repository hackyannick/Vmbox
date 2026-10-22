import http.client
import json
import os
import tempfile
import threading
import unittest

import context  # noqa: F401
from vmboxd import config
from vmboxd.auth import Auth
from vmboxd.display import VirtualDisplay
from vmboxd.fakevbox import FakeVBoxManage
from vmboxd.host import FakeHost
from vmboxd.server import App, Handler, TCPServer, UnixHandler, UnixServer, unix_request
from vmboxd.toolstack import Toolstack
from vmboxd.vbox import VBoxManage

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "ui")


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = config.Config(state_dir=cls.tmp.name, iso_dir=os.path.join(cls.tmp.name, "isos"),
                            ui_dir=UI_DIR, listen_port=0, socket_path=os.path.join(cls.tmp.name, "s.sock"))
        os.makedirs(cfg.iso_dir)
        vbox = VBoxManage(runner=FakeVBoxManage(seed=True))
        toolstack = Toolstack(vbox, VirtualDisplay(running=vbox.running_vms), cfg.iso_dir, cache_ttl=0)
        auth = Auth(cfg.auth_file, iterations=1000)
        auth.locked = True
        cls.app = App(cfg, toolstack, auth, FakeHost(cfg.state_dir))
        cls.tcp = TCPServer(("127.0.0.1", 0), Handler)
        cls.tcp.app = cls.app
        cls.port = cls.tcp.server_address[1]
        cls.unix = UnixServer(cfg.socket_path, UnixHandler)
        cls.unix.app = cls.app
        for srv in (cls.tcp, cls.unix):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.cookie = None

    @classmethod
    def tearDownClass(cls):
        cls.tcp.shutdown()
        cls.unix.shutdown()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, cookie=True, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": "127.0.0.1:%d" % self.port}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        if cookie and ServerTest.cookie:
            hdrs["Cookie"] = ServerTest.cookie
        hdrs.update(headers or {})
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        setcookie = resp.getheader("Set-Cookie")
        conn.close()
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
        return resp.status, payload, setcookie

    def login(self):
        if not ServerTest.cookie:
            status, _, setcookie = self.request("POST", "/api/setup", {"password": "geheim123"})
            if status != 200:
                status, _, setcookie = self.request("POST", "/api/login", {"password": "geheim123"})
            self.assertEqual(status, 200)
            ServerTest.cookie = setcookie.split(";")[0]

    def test_01_setup_flow(self):
        status, state, _ = self.request("GET", "/api/state", cookie=False)
        self.assertEqual(status, 200)
        self.assertTrue(state["setup_required"])
        self.assertNotIn("vms", state)
        status, _, _ = self.request("POST", "/api/setup", {"password": "kurz"})
        self.assertEqual(status, 403)
        self.login()
        status, state, _ = self.request("GET", "/api/state")
        self.assertTrue(state["authenticated"])
        self.assertEqual(len(state["vms"]), 5)
        status, _, _ = self.request("POST", "/api/setup", {"password": "nochmal123"}, cookie=False)
        self.assertEqual(status, 403)

    def test_02_requires_auth(self):
        status, payload, _ = self.request("GET", "/api/vms", cookie=False)
        self.assertEqual(status, 401)
        status, _, _ = self.request("POST", "/api/login", {"password": "falsch"}, cookie=False)
        self.assertEqual(status, 403)

    def test_03_dns_rebinding_and_csrf(self):
        self.login()
        status, _, _ = self.request("GET", "/api/vms", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/lock", {}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/lock", raw=b"x", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_04_static_files(self):
        status, body, _ = self.request("GET", "/", cookie=False)
        self.assertEqual(status, 200)
        self.assertIn(b"VMBox Dashboard", body)
        status, _, _ = self.request("GET", "/../../etc/passwd", cookie=False)
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/bar", cookie=False)
        self.assertEqual(status, 200)

    def test_05_vm_crud(self):
        self.login()
        status, vm, _ = self.request("POST", "/api/vms", {"name": "Neu", "template": "debian"})
        self.assertEqual(status, 201, vm)
        status, _, _ = self.request("POST", "/api/vms/%s/start" % vm["uuid"], {})
        self.assertEqual(status, 200)
        status, info, _ = self.request("GET", "/api/vms/Neu")
        self.assertEqual(info["state"], "running")
        status, err, _ = self.request("DELETE", "/api/vms/%s" % vm["uuid"])
        self.assertEqual(status, 409)
        self.assertIn("ausgeschaltet", err["error"])
        self.request("POST", "/api/vms/%s/poweroff" % vm["uuid"], {})
        status, _, _ = self.request("DELETE", "/api/vms/%s" % vm["uuid"])
        self.assertEqual(status, 200)

    def test_06_iso_upload(self):
        self.login()
        status, res, _ = self.request("POST", "/api/isos?name=test.iso", raw=b"ISO" * 100,
                                      headers={"X-VMBox-Upload": "1", "Content-Type": "application/octet-stream"})
        self.assertEqual(status, 201, res)
        status, res, _ = self.request("GET", "/api/isos")
        self.assertEqual(res["isos"], [{"name": "test.iso", "size": 300}])
        status, _, _ = self.request("POST", "/api/isos?name=../evil.iso", raw=b"x",
                                    headers={"X-VMBox-Upload": "1"})
        self.assertEqual(status, 201)  # stored as "evil.iso" inside iso_dir
        self.assertTrue(os.path.exists(os.path.join(self.app.cfg.iso_dir, "evil.iso")))
        status, _, _ = self.request("POST", "/api/isos?name=x.exe", raw=b"x", headers={"X-VMBox-Upload": "1"})
        self.assertEqual(status, 400)

    def test_07_unix_socket_is_trusted_but_respects_lock(self):
        status, data = unix_request(self.app.cfg.socket_path, "GET", "/api/vms")
        self.assertEqual(status, 200)
        self.assertTrue(data["vms"])
        status, _ = unix_request(self.app.cfg.socket_path, "POST", "/api/lock", {})
        self.assertEqual(status, 200)
        status, err = unix_request(self.app.cfg.socket_path, "POST", "/api/switch/slot/1", {})
        self.assertEqual(status, 423)
        # the old browser session is gone after locking
        status, _, _ = self.request("GET", "/api/vms")
        self.assertEqual(status, 401)
        ServerTest.cookie = None
        self.login()
        status, _ = unix_request(self.app.cfg.socket_path, "POST", "/api/switch/slot/1", {})
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
