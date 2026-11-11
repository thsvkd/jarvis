"""core 수집기: 가짜 게이트웨이로 성공·실패·미지원·크기 상한·설정 검증과 core 라우트."""
import gzip
import json
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jarvis import config, core, server
from test_server_access import FakeCollector

SNAP = {"meta": {"host": "gw", "version": "1.0.0"}, "summary": {"status": "warn", "alerts": {"crit": 0, "warn": 1}},
        "system": {"host": {"model": "Pi"}, "cpu": {"usage_pct": 12.5}, "memory": {"usage_pct": 40},
                   "disks": [{"mount": "/boot", "usage_pct": 1}, {"mount": "/", "usage_pct": 75.9}]}}


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

    def do_POST(self):
        Gateway.posted = (self.path, self.headers.get("X-Jarvis-Action"))
        Gateway.posted_code = self.headers.get("X-Jarvis-Login-Code")
        self.do_GET()


def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class CoreTest(unittest.TestCase):
    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, json.dumps(SNAP).encode(), True),
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

    def test_usage(self):
        Gateway.routes["/api/usage/independent"] = (200, b'{"mode":"independent","providers":[{"id":"claude"}]}', False)
        Gateway.routes["/api/usage/independent?refresh=1"] = (200, b'{"mode":"independent","refreshed":true,"providers":[]}', False)
        self.assertEqual(self.core.usage("gw"), (200, {"mode": "independent", "providers": [{"id": "claude"}]}))
        self.assertTrue(self.core.usage("gw", force=True)[1]["refreshed"])
        rows = {r["id"]: r for r in self.core.usage_all()["devices"]}
        self.assertEqual((rows["gw"]["status"], rows["gw"]["providers"]), (200, [{"id": "claude"}]))
        self.assertEqual(rows["down"]["status"], 502)  # 한 대가 죽어도 나머지는 준다
        del Gateway.routes["/api/usage/independent"]
        code, body = self.core.usage("gw")
        self.assertEqual(code, 501)
        self.assertIn("갱신 필요", body["error"])

    def test_same_org_shows_the_newest_name_and_hides_the_key(self):
        """covers: USAGE-1. 기기마다 저장된 이름이 달라도 같은 조직은 가장 늦게 등록된 이름 하나로 보이고, 키는 화면으로 나가지 않는다"""
        row = lambda org, key, at: {"label": "a@example.com", "org": org, "org_key": key, "org_at": at}
        body = lambda *accounts: {"providers": [{"id": "claude", "accounts": list(accounts)}]}
        old, new, other, malformed = row("케이골프", "k1", 100), row("K-Golf", "k1", 200), row("Team", "k2", 50), row("Odd", ["x"], "y")
        self.core._same_org_names([body(old, other, malformed), body(new), "실패 응답", {"providers": "이상한 값"}])
        self.assertEqual([old["org"], new["org"], other["org"], malformed["org"]], ["K-Golf", "K-Golf", "Team", "Odd"])
        self.assertEqual({"org_key", "org_at"} & {k for a in (old, new, other, malformed) for k in a}, set())
        # 기본 이름이라 org 가 없는 기기도 같은 조직이면 이름을 받고, 범위를 벗어나거나 형식이 틀린 시각은 0 으로 본다
        unnamed, bad, good = row(None, "k1", 50), row("Bad", "k3", float("nan")), row("Good", "k3", 10)
        self.core._same_org_names([body(bad, unnamed), body(good, row("Text", "k3", "y"), row("Huge", "k3", 1e308))])
        self.assertEqual([unnamed["org"], bad["org"], good["org"]], ["K-Golf", "Good", "Good"])
        # 옛 이름을 가진 기기만 새로고침해도 앞서 본 새 이름을 쓴다
        Gateway.routes["/api/usage/independent"] = (200, json.dumps(body(row("케이골프", "k1", 100))).encode(), False)
        self.assertEqual(self.core.usage("gw")[1], body({"label": "a@example.com", "org": "K-Golf"}))
        rows = {r["id"]: r for r in self.core.usage_all()["devices"]}
        self.assertEqual(rows["gw"]["providers"][0]["accounts"], [{"label": "a@example.com", "org": "K-Golf"}])
        # 갱신 전 게이트웨이(키 없음)는 그대로 지나간다
        self.core._same_org_names([plain := body({"label": "a@example.com", "org": "케이골프"})])
        self.assertEqual(plain["providers"][0]["accounts"][0]["org"], "케이골프")

    def test_independent_usage_uses_only_independent_gateway(self):
        """covers: USAGE-3"""
        Gateway.routes["/api/usage/independent"] = (200, b'{"mode":"independent","providers":[]}', False)
        Gateway.routes["/api/usage/independent?refresh=1"] = (200, b'{"mode":"independent","providers":[],"refreshed":true}', False)
        self.assertEqual(self.core.usage("gw")[1]["mode"], "independent")
        self.assertTrue(self.core.usage("gw", force=True)[1]["refreshed"])
        rows = {r["id"]: r for r in self.core.usage_all()["devices"]}
        self.assertEqual(rows["gw"]["mode"], "independent")
        self.assertEqual(rows["down"]["status"], 502)
        del Gateway.routes["/api/usage/independent"]
        self.assertEqual(self.core.usage("gw")[0], 501)

    def test_no_redirect(self):
        # 장악된 게이트웨이가 core 를 자기 자신(루프백)의 core 전용 경로로 보내도 따라가지 않는다
        Gateway.routes["/api/changes"] = (302, b"/api/usage/independent", False)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.core.relay("gw", "/api/changes")
        self.assertEqual(e.exception.code, 302)
        Gateway.routes["/api/snapshot"] = (302, b"/api/usage/independent", False)
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

    def test_size_limit(self):
        old, core.MAX_BYTES = core.MAX_BYTES, 1000
        try:
            Gateway.routes["/api/changes"] = (200, b"x" * 100000, True)  # 압축하면 작지만 풀면 넘친다
            with self.assertRaises(ValueError):
                self.core.relay("gw", "/api/changes")
        finally:
            core.MAX_BYTES = old


class RouteTest(unittest.TestCase):
    """core 라우트: 스냅샷 전달, 없는 기기 404, 로그인·계정 전환은 viewers 만."""

    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, json.dumps(SNAP).encode(), False)}
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
        self.assertEqual(self.get("/api/devices/gw/orca")[0], 404)  # Orca 경로는 없다
        Gateway.routes["/api/usage/login?provider=codex"] = (200, b'{"state":"waiting"}', False)
        self.assertEqual(self.get("/api/devices/gw/login?provider=codex")[0], 403)  # 루프백은 viewer 가 아니다
        # viewers 에 적힌 tailnet 기기로 보이게 한다(소켓은 127.0.0.1 에만 붙는다)
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
        try:
            self.assertEqual(self.get("/api/devices/gw/login?provider=codex"), (200, {"state": "waiting"}))
        finally:
            server.client_kind, config.VIEWERS = kind, vw

    def test_update_progress_is_relayed_and_old_gateways_are_501(self):
        """covers: UI-4. 갱신 진행은 기기의 GET /api/jarvis/update 를 그대로 준다(읽기라 viewers 가 아니어도 된다)"""
        self.assertEqual(self.get("/api/devices/gw/jarvis/update")[0], 501)  # 그 경로가 없는 옛 게이트웨이
        Gateway.routes["/api/jarvis/update"] = (200, b'{"busy":true,"version":"1.11.0","stage":"download","got":5,"total":10}', False)
        self.assertEqual(self.get("/api/devices/gw/jarvis/update"),
                         (200, {"busy": True, "version": "1.11.0", "stage": "download", "got": 5, "total": 10}))
        self.assertEqual(self.get("/api/devices/nope/jarvis/update")[0], 404)
        self.assertEqual(self.get("/api/jarvis/update")[0], 403)  # 게이트웨이 경로는 설정 core 만

    def test_usage_routes(self):
        Gateway.routes["/api/usage/independent"] = (200, b'{"mode":"independent","providers":[]}', False)
        self.assertEqual(self.get("/api/devices/gw/usage"), (200, {"mode": "independent", "providers": []}))
        st, o = self.get("/api/devices/usage")
        self.assertEqual((st, o["devices"][0]["id"], o["devices"][0]["status"]), (200, "gw", 200))
        self.assertEqual(self.get("/api/usage/independent")[0], 403)  # 게이트웨이 경로는 설정 core 만
        # 강제 새로고침은 맞춤 헤더가 있어야 한다(다른 사이트가 이미지·링크로 부르지 못하게)
        Gateway.routes["/api/usage/independent?refresh=1"] = (200, b'{"mode":"independent","refreshed":true,"providers":[]}', False)
        for path in ("/api/devices/usage?refresh=1", "/api/devices/gw/usage?refresh=1"):
            self.assertEqual(self.get(path)[0], 403)
        st, o = self.get("/api/devices/gw/usage?refresh=1", {"X-Jarvis-Action": "usage-refresh"})
        self.assertEqual((st, o["refreshed"]), (200, True))
        self.assertTrue(self.get("/api/devices/usage?refresh=1", {"X-Jarvis-Action": "usage-refresh"})[1]["devices"][0]["refreshed"])
        # 다시 로그인은 viewers 만(루프백은 viewer 가 아니다), 맞춤 헤더도 있어야 한다
        req = lambda h: urllib.request.Request(self.base + "/api/devices/gw/login?provider=codex", data=b"", method="POST", headers=h)
        for h, code in (({}, 403), ({"X-Jarvis-Action": "usage-login"}, 403)):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req(h))
            self.assertEqual(e.exception.code, code)
        # 계정 전환도 viewers 만이고 맞춤 헤더가 있어야 한다. 게이트웨이에는 슬롯과 헤더를 그대로 전달한다
        Gateway.routes["/api/usage/account?slot=2"] = (200, b'{"switched":true}', False)
        sw = lambda h: urllib.request.Request(self.base + "/api/devices/gw/account?slot=2", data=b"", method="POST", headers=h)
        for h in ({}, {"X-Jarvis-Action": "usage-account"}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(sw(h))
            self.assertEqual(e.exception.code, 403)
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
        try:
            with urllib.request.urlopen(sw({"X-Jarvis-Action": "usage-account"})) as r:
                self.assertEqual(json.loads(r.read()), {"switched": True})
        finally:
            server.client_kind, config.VIEWERS = kind, vw
        self.assertEqual(Gateway.posted, ("/api/usage/account?slot=2", "usage-account"))
        self.core.state["gw"].update(fails=1, error="timed out")  # 폴링 실패 중이면 묻지 않고 바로 502
        self.assertEqual(self.get("/api/devices/usage")[1]["devices"][0]["status"], 502)

    def test_login_and_account_reach_the_gateway_usage_paths(self):
        """다시 로그인·계정 전환은 viewers 만, 게이트웨이의 /api/usage/* 로 경로와 헤더를 그대로 전달한다"""
        Gateway.routes["/api/usage/login?provider=codex"] = (200, b'{"url":"https://x","code":"ABCD-EFGH"}', False)
        Gateway.routes["/api/usage/account?slot=2"] = (200, b'{"switched":true}', False)
        Gateway.routes["/api/usage/account/remove?slot=2"] = (200, b'{"removed":true}', False)
        post = lambda path, action: urllib.request.Request(self.base + path, data=b"", method="POST", headers={"X-Jarvis-Action": action})
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
        try:
            with urllib.request.urlopen(post("/api/devices/gw/login?provider=codex", "usage-login")) as r:
                self.assertEqual(json.loads(r.read())["code"], "ABCD-EFGH")
            self.assertEqual(Gateway.posted, ("/api/usage/login?provider=codex", "usage-login"))
            with urllib.request.urlopen(post("/api/devices/gw/account?slot=2", "usage-account")) as r:
                self.assertEqual(json.loads(r.read()), {"switched": True})
            self.assertEqual(Gateway.posted, ("/api/usage/account?slot=2", "usage-account"))
            with urllib.request.urlopen(post("/api/devices/gw/account/remove?slot=2", "usage-account")) as r:  # covers: USAGE-12
                self.assertEqual(json.loads(r.read()), {"removed": True})
            self.assertEqual(Gateway.posted, ("/api/usage/account/remove?slot=2", "usage-account"))
            # 옛 게이트웨이는 그 경로가 없다(404): 다른 경로로 다시 묻지 않고 501 로 알린다
            for path, action in (("/api/devices/gw/login?provider=grok", "usage-login"), ("/api/devices/gw/account?slot=9", "usage-account"),
                                 ("/api/devices/gw/account/remove?slot=9", "usage-account")):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(post(path, action))
                self.assertEqual(e.exception.code, 501)
                self.assertIn("갱신 필요", json.loads(e.exception.read())["error"])
        finally:
            server.client_kind, config.VIEWERS = kind, vw

    def test_claude_login_code_travels_in_a_header_and_is_checked_first(self):
        """covers: USAGE-10. 인증 코드는 URL·본문이 아니라 헤더로 전달하고, 모양이 틀리면 게이트웨이로 보내지 않는다"""
        Gateway.routes["/api/usage/login?provider=claude"] = (200, b'{"submitted":true}', False)
        Gateway.posted = Gateway.posted_code = None
        self.assertEqual(self.core.usage_login("gw", "claude", "bad code\r\nX: y")[0], 400)
        self.assertIsNone(Gateway.posted)
        self.assertEqual(self.core.usage_login("gw", "claude", "abcd1234#efgh5678"), (200, {"submitted": True}))
        self.assertEqual((Gateway.posted, Gateway.posted_code),
                         (("/api/usage/login?provider=claude", "usage-login"), "abcd1234#efgh5678"))
        self.core.usage_login("gw", "claude")  # 코드가 없으면 로그인 시작이고 헤더도 없다
        self.assertIsNone(Gateway.posted_code)
        Gateway.routes["/api/usage/login?provider=codex&mode=add"] = (200, b'{"url":"u"}', False)
        self.assertEqual(self.core.usage_login("gw", "codex", None, True), (200, {"url": "u"}))  # 계정 추가는 mode=add 로 전달한다
        self.assertEqual(Gateway.posted, ("/api/usage/login?provider=codex&mode=add", "usage-login"))

    def test_gateway_claude_login_code_goes_to_submit_not_start(self):
        """covers: USAGE-10. 게이트웨이는 코드 헤더가 있으면 그 CLI 로 제출하고, 없으면 로그인을 시작한다"""
        post = lambda headers: urllib.request.Request(self.base + "/api/usage/login?provider=claude", data=b"", method="POST",
                                                      headers={"X-Jarvis-Action": "usage-login", **headers})
        with mock.patch.object(server.cli_login, "start", return_value=(200, {"url": "u"})) as start, \
                mock.patch.object(server.cli_login, "submit", return_value=(200, {"submitted": True})) as submit, \
                mock.patch.object(server, "client_kind", return_value="tailnet"), mock.patch.object(config, "CORE", ["127.0.0.1"]):
            with urllib.request.urlopen(post({})) as r:
                self.assertEqual(json.loads(r.read()), {"url": "u"})
            with urllib.request.urlopen(post({"X-Jarvis-Login-Code": "abcd1234#efgh5678"})) as r:
                self.assertEqual(json.loads(r.read()), {"submitted": True})
            with urllib.request.urlopen(urllib.request.Request(self.base + "/api/usage/login?provider=codex&mode=add", data=b"", method="POST",
                                                               headers={"X-Jarvis-Action": "usage-login"})) as r:
                self.assertEqual(json.loads(r.read()), {"url": "u"})
        self.assertEqual(start.call_args_list, [mock.call("claude", False), mock.call("codex", True)])  # mode=add 는 계정 추가다
        submit.assert_called_once_with("claude", "abcd1234#efgh5678")

    def test_gateway_usage_login_and_account_answer_only_the_configured_core(self):
        """게이트웨이의 /api/usage/login·account 는 설정 core 만 부르고, 맞춤 헤더 값은 그대로다"""
        post = lambda path, action: urllib.request.Request(self.base + path, data=b"", method="POST", headers={"X-Jarvis-Action": action})
        with mock.patch.object(server.cli_login, "start", return_value=(200, {"code": "C"})) as start, \
                mock.patch.object(server.cli_login, "status", return_value={"state": "done"}), \
                mock.patch.object(server.claude_accounts, "switch", return_value=(200, {"switched": True})) as switch:
            for path, action in (("/api/usage/login?provider=codex", "usage-login"), ("/api/usage/account?slot=2", "usage-account")):
                with self.assertRaises(urllib.error.HTTPError) as e:  # 루프백은 core 가 아니다
                    urllib.request.urlopen(post(path, action))
                self.assertEqual(e.exception.code, 403)
            self.assertEqual(self.get("/api/usage/login?provider=codex")[0], 403)
            start.assert_not_called()
            switch.assert_not_called()
            with mock.patch.object(server, "client_kind", return_value="tailnet"), mock.patch.object(config, "CORE", ["127.0.0.1"]):
                with urllib.request.urlopen(post("/api/usage/login?provider=codex", "usage-login")) as r:
                    self.assertEqual(json.loads(r.read()), {"code": "C"})
                with urllib.request.urlopen(post("/api/usage/account?slot=2", "usage-account")) as r:
                    self.assertEqual(json.loads(r.read()), {"switched": True})
                self.assertEqual(self.get("/api/usage/login?provider=codex"), (200, {"state": "done"}))
            start.assert_called_once_with("codex", False)
            switch.assert_called_once_with("2")

    def test_independent_usage_routes_are_core_only_and_never_fall_back(self):
        """covers: USAGE-3"""
        Gateway.routes["/api/usage/independent"] = (200, b'{"mode":"independent","providers":[]}', False)
        Gateway.routes["/api/usage/independent?refresh=1"] = (200, b'{"mode":"independent","providers":[],"refreshed":true}', False)
        self.assertEqual(self.get("/api/devices/gw/usage")[1]["mode"], "independent")
        self.assertEqual(self.get("/api/devices/usage")[1]["devices"][0]["mode"], "independent")
        self.assertEqual(self.get("/api/usage/independent")[0], 403)
        self.assertEqual(self.get("/api/devices/usage?refresh=1")[0], 403)
        self.assertTrue(self.get("/api/devices/usage?refresh=1", {"X-Jarvis-Action": "usage-refresh"})[1]["devices"][0]["refreshed"])
        with mock.patch.object(server, "client_kind", return_value="tailnet"), \
                mock.patch.object(config, "CORE", ["127.0.0.1"]), \
                mock.patch.object(server.independent_usage, "read", return_value={"mode": "independent", "providers": []}):
            self.assertEqual(self.get("/api/usage/independent")[1]["mode"], "independent")

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
