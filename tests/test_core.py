"""core 수집기: 가짜 게이트웨이로 성공·실패·미지원·크기 상한·설정 검증과 core 라우트."""
import gzip
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis import config, core, server
from test_orca import FakeCollector

SNAP = {"meta": {"host": "gw", "version": "1.0.0"}, "summary": {"status": "warn", "alerts": {"crit": 0, "warn": 1}},
        "system": {"host": {"model": "Pi"}, "cpu": {"usage_pct": 12.5}, "memory": {"usage_pct": 40},
                   "disks": [{"mount": "/boot", "usage_pct": 1}, {"mount": "/", "usage_pct": 75.9}]}}
ORCA = {"installed": True, "running": True, "endpoint": "ws://100.1.2.3:6768",
        "codes": {"desktop": None, "mobile": {"url": "orca://pair?code=abc", "device_id": "m1"}}}


class Gateway(BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        code, body, gz = self.routes.get(self.path, (404, b"{}", False))
        if gz:
            body = gzip.compress(body)
        self.send_response(code)
        if code == 302:
            self.send_header("Location", body.decode())
            body = b""
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class CoreTest(unittest.TestCase):
    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, json.dumps(SNAP).encode(), True),
                          "/api/orca": (200, json.dumps(ORCA).encode(), False),
                          "/api/changes": (200, b'{"changes":[]}', False)}
        self.gw = serve(Gateway)
        url = f"http://127.0.0.1:{self.gw.server_address[1]}"
        self.core = core.Core(core.devices_from([{"id": "gw", "url": url}, {"id": "down", "url": "http://127.0.0.1:1"}]))

    def tearDown(self):
        self.gw.shutdown()
        self.gw.server_close()

    def test_devices_from(self):
        self.assertEqual(core.devices_from([{"id": "a", "url": "http://1.2.3.4:9300/"}, {"id": "a/b", "url": "http://x"},
                                            {"id": "c", "url": "http://x/path"}, {"id": "a", "url": "http://y"}, "x"]),
                         [{"id": "a", "url": "http://1.2.3.4:9300"}])

    def test_poll(self):
        for i in ("gw", "down"):
            self.core.poll(i)
        rows = {r["id"]: r for r in self.core.list()["devices"]}
        gw, down = rows["gw"], rows["down"]
        self.assertTrue(gw["reachable"])
        self.assertEqual((gw["host"], gw["status"], gw["cpu_pct"], gw["mem_pct"], gw["disk_pct"], gw["model"]),
                         ("gw", "warn", 12.5, 40, 75.9, "Pi"))
        raw, gz = self.core.snapshot("gw")  # 마지막 성공본
        self.assertEqual(json.loads(raw), SNAP)
        self.assertEqual(gzip.decompress(gz), raw)
        self.assertFalse(down["reachable"])
        self.assertEqual(down["fails"], 1)
        self.assertTrue(down["error"])
        # 실패해도 마지막 성공본은 남는다
        Gateway.routes["/api/snapshot"] = (500, b"", False)
        self.core.poll("gw")
        row = next(r for r in self.core.list()["devices"] if r["id"] == "gw")
        self.assertEqual((row["reachable"], row["fails"], row["error"], row["host"]), (False, 1, "HTTP 500", "gw"))
        self.assertIsNotNone(self.core.snapshot("gw")[0])

    def test_orca(self):
        o = self.core.orca("gw")
        self.assertTrue(o["supported"])
        self.assertIsNone(o["codes"]["desktop"])
        self.assertTrue(o["codes"]["mobile"]["qr"] is None or set("".join(o["codes"]["mobile"]["qr"])) <= {"0", "1"})
        for code, word in ((404, "지원하지"), (403, "core")):
            Gateway.routes["/api/orca"] = (code, b"{}", False)
            o = self.core.orca("gw")
            self.assertFalse(o["supported"])
            self.assertIn(word, o["error"])
        with self.assertRaises(urllib.error.URLError):
            self.core.orca("down")

    def test_usage(self):
        Gateway.routes["/api/orca/usage"] = (200, b'{"installed":true,"providers":[{"id":"claude"}]}', False)
        Gateway.routes["/api/orca/usage?refresh=1"] = (200, b'{"installed":true,"refreshed":true,"providers":[]}', False)
        self.assertEqual(self.core.usage("gw"), (200, {"installed": True, "providers": [{"id": "claude"}]}))
        self.assertTrue(self.core.usage("gw", force=True)[1]["refreshed"])
        rows = {r["id"]: r for r in self.core.usage_all()["devices"]}
        self.assertEqual((rows["gw"]["status"], rows["gw"]["providers"]), (200, [{"id": "claude"}]))
        self.assertEqual(rows["down"]["status"], 502)  # 한 대가 죽어도 나머지는 준다
        del Gateway.routes["/api/orca/usage"]
        code, body = self.core.usage("gw")
        self.assertEqual(code, 501)
        self.assertIn("갱신 필요", body["error"])

    def test_no_redirect(self):
        # 장악된 게이트웨이가 core 를 자기 자신(루프백)의 /api/orca 로 보내도 따라가지 않는다
        Gateway.routes["/api/changes"] = (302, b"/api/orca", False)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.core.relay("gw", "/api/changes")
        self.assertEqual(e.exception.code, 302)
        Gateway.routes["/api/snapshot"] = (302, b"/api/orca", False)
        self.core.poll("gw")
        self.assertIsNone(self.core.snapshot("gw")[0])

    def test_mac_disk(self):
        mac = {"system": {"disks": [{"mount": "/System/Volumes/Data", "usage_pct": 65.6}, {"mount": "/Volumes/x", "usage_pct": 97}]}}
        self.assertEqual(core._summary(mac)["disk_pct"], 65.6)
        both = {"system": {"disks": [{"mount": "/System/Volumes/Data", "usage_pct": 1}, {"mount": "/", "usage_pct": 2},
                                     {"mount": "/", "usage_pct": 3}, {"mount": ["odd"]}]}}
        self.assertEqual(core._summary(both)["disk_pct"], 2)  # "/" 가 먼저, 같은 mount 는 첫 항목
        self.assertIsNone(core._summary({"system": {"disks": [{"mount": "/boot", "usage_pct": 9}]}})["disk_pct"])
        self.assertEqual(core._summary({"system": {"disks": [{"mount": "C:\\", "usage_pct": 41}]}})["disk_pct"], 41)

    def test_stale_snapshot(self):
        self.core.poll("gw")
        self.assertIsNotNone(self.core.snapshot("gw")[0])
        self.core.state["gw"]["ok_at"] -= core.STALE_S + 1
        self.assertIsNone(self.core.snapshot("gw")[0])

    def test_odd_orca(self):
        Gateway.routes["/api/orca"] = (200, b"[]", False)
        with self.assertRaises(ValueError):
            self.core.orca("gw")

    def test_size_limit(self):
        old, core.MAX_BYTES = core.MAX_BYTES, 1000
        try:
            Gateway.routes["/api/changes"] = (200, b"x" * 100000, True)  # 압축하면 작지만 풀면 넘친다
            with self.assertRaises(ValueError):
                self.core.relay("gw", "/api/changes")
        finally:
            core.MAX_BYTES = old


class RouteTest(unittest.TestCase):
    """core 라우트: 스냅샷 전달, 없는 기기 404, 페어링 버튼은 viewers 만."""

    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, json.dumps(SNAP).encode(), False),
                          "/api/orca": (200, json.dumps(ORCA).encode(), False)}
        self.gw = serve(Gateway)
        c = core.Core([{"id": "gw", "url": f"http://127.0.0.1:{self.gw.server_address[1]}"}])
        c.poll("gw")
        self.httpd = server.Server(("127.0.0.1", 0), server.make_handler(FakeCollector(), c))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.core = c

    def tearDown(self):
        for h in (self.httpd, self.gw):
            h.shutdown()
            h.server_close()

    def get(self, path, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers or {})) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        self.assertEqual(self.get("/api/devices")[1]["devices"][0]["host"], "gw")
        self.assertEqual(self.get("/api/devices/gw/snapshot"), (200, SNAP))
        self.assertEqual(self.get("/api/devices/nope/snapshot")[0], 404)
        self.assertEqual(self.get("/api/devices/gw/history?range=7d")[0], 400)
        self.assertEqual(self.get("/api/devices/gw/history")[0], 502)  # 가짜 게이트웨이에 history 가 없다
        st, o = self.get("/api/devices/gw/orca")  # 루프백은 viewer 가 아니다
        self.assertEqual(st, 403)
        self.assertNotIn("orca://", json.dumps(o))
        # viewers 에 적힌 tailnet 기기로 보이게 한다(소켓은 127.0.0.1 에만 붙는다)
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
        try:
            st, o = self.get("/api/devices/gw/orca")
        finally:
            server.client_kind, config.VIEWERS = kind, vw
        self.assertEqual((st, o["codes"]["mobile"]["url"]), (200, "orca://pair?code=abc"))

    def test_usage_routes(self):
        Gateway.routes["/api/orca/usage"] = (200, b'{"installed":true,"providers":[]}', False)
        self.assertEqual(self.get("/api/devices/gw/usage"), (200, {"installed": True, "providers": []}))
        st, o = self.get("/api/devices/usage")
        self.assertEqual((st, o["devices"][0]["id"], o["devices"][0]["status"]), (200, "gw", 200))
        self.assertEqual(self.get("/api/orca/usage")[0], 403)  # 게이트웨이 경로는 설정 core 만
        # 강제 새로고침은 맞춤 헤더가 있어야 한다(다른 사이트가 이미지·링크로 부르지 못하게)
        Gateway.routes["/api/orca/usage?refresh=1"] = (200, b'{"installed":true,"refreshed":true,"providers":[]}', False)
        for path in ("/api/devices/usage?refresh=1", "/api/devices/gw/usage?refresh=1"):
            self.assertEqual(self.get(path)[0], 403)
        st, o = self.get("/api/devices/gw/usage?refresh=1", {"X-Jarvis-Action": "usage-refresh"})
        self.assertEqual((st, o["refreshed"]), (200, True))
        self.assertTrue(self.get("/api/devices/usage?refresh=1", {"X-Jarvis-Action": "usage-refresh"})[1]["devices"][0]["refreshed"])
        self.core.state["gw"].update(fails=1, error="timed out")  # 폴링 실패 중이면 묻지 않고 바로 502
        self.assertEqual(self.get("/api/devices/usage")[1]["devices"][0]["status"], 502)

    def test_viewers(self):
        cls = server.make_handler(FakeCollector(), self.core)
        h = cls.__new__(cls)
        h.client_address, h.headers = ("100.64.0.5", 1), {}
        old, config.VIEWERS = config.VIEWERS, ["100.64.0.5"]
        try:
            self.assertTrue(h._trusted(config.VIEWERS))
            config.VIEWERS = []
            self.assertFalse(h._trusted(config.VIEWERS))
        finally:
            config.VIEWERS = old


if __name__ == "__main__":
    unittest.main()
