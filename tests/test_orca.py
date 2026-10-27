"""Orca 페어링 코드(GET /api/orca): v2 코드 형식, 대기 기기 우선·없으면 최근 페어링 기기, 비밀 키를 내보내지 않는지, 접근 통제."""
import base64
import email.message
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from jarvis import config, orca, orca_issue, server


def decode(url):
    code = url.split("code=", 1)[1]
    return json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))


def orca_dir(root):
    files = {
        "orca-runtime.json": {"pid": os.getpid(), "transports": [{"kind": "unix", "endpoint": "/x.sock"},
                                                                 {"kind": "websocket", "endpoint": "ws://0.0.0.0:6768"}]},
        "orca-e2ee-keypair.json": {"publicKeyB64": "PUB", "secretKeyB64": "SECRET"},
        "orca-devices.json": [
            {"deviceId": "old", "token": "OLD", "scope": "runtime", "lastSeenAt": 5, "pairedAt": 1},
            {"deviceId": "used", "token": "USED", "scope": "runtime", "lastSeenAt": 17, "pairedAt": 2, "name": "CLI"},
            {"deviceId": "m0", "token": "MOBILE-USED", "scope": "mobile", "lastSeenAt": 9, "pairedAt": 9},
            {"deviceId": "m1", "token": "tm", "scope": "mobile", "lastSeenAt": 0},
        ],
    }
    for name, body in files.items():
        with open(os.path.join(root, name), "w") as f:
            json.dump(body, f)


class StatusTest(unittest.TestCase):
    def test_codes(self):
        with tempfile.TemporaryDirectory() as d:
            orca_dir(d)
            s = orca.status("100.64.0.2", dirs=(d,))
        self.assertEqual((s["installed"], s["running"], s["endpoint"]), (True, True, "ws://100.64.0.2:6768"))
        dsk = s["codes"]["desktop"]  # 대기 runtime 기기가 없다: 가장 최근에 페어링된 기기의 코드
        self.assertEqual((dsk["device_id"], dsk["pending"], dsk["name"]), ("used", False, "CLI"))
        self.assertEqual(decode(dsk["url"])["deviceToken"], "USED")
        m = s["codes"]["mobile"]
        self.assertEqual((m["device_id"], m["pending"]), ("m1", True))  # 대기 기기가 있으면 그것이 먼저
        self.assertNotIn("=", m["url"].split("code=", 1)[1])  # base64url, 패딩 없음
        self.assertEqual(decode(m["url"]), {"v": 2, "endpoint": "ws://100.64.0.2:6768", "deviceToken": "tm",
                                            "publicKeyB64": "PUB", "pairedDeviceId": "m1", "scope": "mobile"})
        self.assertNotIn("SECRET", json.dumps(s))
        self.assertNotIn("OLD", json.dumps(s))
        self.assertEqual([orca._ts(v) for v in (5, True, float("nan"), float("inf"), "7", None)], [5, 0, 0, 0, 0, 0])
        self.assertNotIn("MOBILE-USED", json.dumps(s))

    def test_desktop_code_from_unused_runtime_device(self):
        with tempfile.TemporaryDirectory() as d:
            orca_dir(d)
            path = os.path.join(d, "orca-devices.json")
            with open(path, "w") as f:
                json.dump([{"deviceId": "r1", "token": "tr", "scope": "runtime", "lastSeenAt": 0}], f)
            s = orca.status("100.64.0.2", dirs=(d,))
        self.assertEqual(decode(s["codes"]["desktop"]["url"])["scope"], "runtime")
        self.assertIsNone(s["codes"]["mobile"])

    def test_not_installed_or_no_address(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(orca.status("1.2.3.4", dirs=(d,))["installed"])
            orca_dir(d)
            s = orca.status(None, dirs=(d,))  # tailnet 주소를 모르면 쓸 수 없는 코드를 만들지 않는다
        self.assertEqual(s["codes"], {"desktop": None, "mobile": None})


class IssueTest(unittest.TestCase):
    def test_flags(self):
        cli = ["xvfb-run", "-a", "orca-ide", "serve", "--port", "6768", "--mobile-pairing", "--json"]
        self.assertEqual(orca_issue.flags(cli, "desktop"), ["xvfb-run", "-a", "orca-ide", "serve", "--port", "6768", "--json"])
        self.assertEqual(orca_issue.flags(cli, "mobile")[-1], "--mobile-pairing")
        self.assertEqual(orca_issue.flags(cli, "mobile").count("--mobile-pairing"), 1)
        ele = ["orca-ide", "--no-sandbox", "--serve", "--serve-json"]
        self.assertEqual(orca_issue.flags(ele, "mobile"), ele + ["--serve-mobile-pairing"])
        # 운영자가 페어링을 끈 Orca 는 발급 대상이 아니다
        self.assertIsNone(orca_issue._ok({"argv": ele + ["--serve-no-pairing"]}))
        self.assertIsNotNone(orca_issue._ok({"argv": ele}))

    def test_issuable_only_when_scope_never_paired(self):
        with tempfile.TemporaryDirectory() as d:
            orca_dir(d)  # runtime·mobile 기기가 모두 있다
            old = orca_issue.manager, orca.DIRS
            orca_issue.manager, orca.DIRS = (lambda: {"kind": "systemd"}), (d,)
            try:
                st = orca.status("100.1.2.3", dirs=(d,))
                self.assertEqual(orca_issue.issuable(st), [])
                with open(os.path.join(d, "orca-devices.json"), "w") as f:
                    json.dump([{"deviceId": "r", "token": "t", "scope": "runtime", "lastSeenAt": 5}], f)
                self.assertEqual(orca_issue.issuable(orca.status("100.1.2.3", dirs=(d,))), ["mobile"])
                orca_issue.manager = lambda: None  # 띄우는 방법을 모르면 버튼을 내지 않는다
                self.assertEqual(orca_issue.issuable(orca.status("100.1.2.3", dirs=(d,))), [])
                self.assertEqual(orca_issue.start("bogus", "100.1.2.3", 6768)[0], 400)
                self.assertEqual(orca_issue.start("mobile", "100.1.2.3", 6768)[0], 409)  # manager 없음
            finally:
                orca_issue.manager, orca.DIRS = old


class FakeCollector:
    def allowed_hosts(self):
        return {"127.0.0.1"}

    def caddy_ips(self):
        return {"172.18.0.2"}

    def tailnet_v4(self):
        return "100.64.0.2"


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.httpd = server.Server(("127.0.0.1", 0), server.make_handler(FakeCollector()))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/orca"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, headers=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url, headers=headers or {})) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_body_rejected(self):
        """본문이 붙은 GET 은 400 하나로 끝내고 연결을 닫는다(본문 안의 두 번째 요청을 파싱하지 않는다)."""
        import socket
        inner = b"GET /api/orca HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        port = self.httpd.server_address[1]
        for extra in (b"Content-Length: %d\r\n" % len(inner), b"Transfer-Encoding: chunked\r\n"):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                c.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n" + extra + b"\r\n" + inner)
                data = b""
                while chunk := c.recv(65536):
                    data += chunk
            self.assertEqual(data.count(b"HTTP/1."), 1, extra)
            self.assertTrue(data.startswith(b"HTTP/1.0 400") or data.startswith(b"HTTP/1.1 400"), data[:40])

    def test_post_body_rejected_before_header_check(self):
        """헤더 없는 POST 도 본문 검사를 먼저 거친다(403 뒤에 밀수된 두 번째 요청이 파싱되지 않게)."""
        import socket
        inner = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        with socket.create_connection(("127.0.0.1", self.httpd.server_address[1]), timeout=5) as c:
            c.sendall(b"POST /api/orca/issue HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: %d\r\n\r\n" % len(inner) + inner)
            data = b""
            while chunk := c.recv(65536):
                data += chunk
        self.assertEqual(data.count(b"HTTP/1."), 1)
        self.assertIn(b" 400 ", data.split(b"\r\n", 1)[0])

    def test_issue_post_guards(self):
        req = urllib.request.Request(self.url + "/issue?scope=mobile", data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:  # 맞춤 헤더 없음
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        req.add_header("X-Jarvis-Action", "orca-issue")
        with self.assertRaises(urllib.error.HTTPError) as e:  # 루프백은 core 가 아니다
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        self.assertIn("core", json.loads(e.exception.read())["error"])
        upd = urllib.request.Request(self.url.rsplit("/", 1)[0] + "/orca/update", data=b"", method="POST",
                                     headers={"X-Jarvis-Action": "orca-update"})
        with self.assertRaises(urllib.error.HTTPError) as e:  # 루프백은 core 가 아니다. 헤더는 통과한다
            urllib.request.urlopen(upd)
        self.assertEqual(e.exception.code, 403)
        self.assertIn("core", json.loads(e.exception.read())["error"])
        with self.assertRaises(urllib.error.HTTPError) as e:  # 확인 GET 도 같다. 루프백에서는 매니페스트를 받지 않는다
            urllib.request.urlopen(self.url + "/update")
        self.assertEqual(e.exception.code, 403)
        self.assertIn("core", json.loads(e.exception.read())["error"])

    def test_no_loopback(self):
        old, config.CORE = config.CORE, ["127.0.0.1"]
        try:
            self.assertEqual(self.get(), 403)
        finally:
            config.CORE = old  # 루프백은 core 가 아니다(같은 기기의 컨테이너·프록시도 루프백으로 온다)


class CheckTest(unittest.TestCase):
    """소켓 없이 핸들러의 접근 검사만 부른다(서버는 127.0.0.1 에만 붙으므로 다른 출발지는 이렇게 본다)."""

    def setUp(self):
        self._core, config.CORE = config.CORE, ["100.64.0.1"]

    def tearDown(self):
        config.CORE = self._core

    def handler(self, peer, **headers):
        cls = server.make_handler(FakeCollector())
        h = cls.__new__(cls)
        h.client_address = (peer, 50000)
        h.headers = email.message.Message()
        for k, v in headers.items():
            h.headers[k.replace("_", "-")] = v
        return h

    def test_core_ok(self):
        self.assertTrue(self.handler("100.64.0.1")._trusted(config.CORE))
        self.assertFalse(self.handler("100.64.0.11")._trusted(config.CORE))  # 같은 tailnet 의 다른 기기
        self.assertFalse(self.handler("172.18.0.2", X_Forwarded_For="100.64.0.1")._trusted(config.CORE))  # Caddy 경유
        for h in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "Via"):  # host 네트워크 프록시가 tailnet IP 로 넘겨 준 요청
            self.assertFalse(self.handler("100.64.0.1", **{h.replace("-", "_"): ""})._trusted(config.CORE), h)
        config.CORE = ["100.64.0.1", "127.0.0.1"]
        self.assertFalse(self.handler("127.0.0.1")._trusted(config.CORE))  # 루프백은 목록에 적어도 받지 않는다
        config.CORE = ["192.168.0.12"]
        self.assertFalse(self.handler("192.168.0.12")._trusted(config.CORE))  # LAN IP 는 core 로 적어도 받지 않는다

    def test_viewer_through_proxy(self):
        """도메인(pit5 Caddy) 경유: proxies 의 프록시가 proxy_key 를 붙였을 때만 XFF 마지막 항목을 viewer 판정에 쓴다."""
        v, px, K = ["100.64.0.5"], ["100.64.0.2"], "k" * 32
        old, config.PROXY_KEY = config.PROXY_KEY, K
        try:
            def h(peer="100.64.0.2", key=K, **hd):
                if key is not None:
                    hd["X_Jarvis_Proxy_Key"] = key
                return self.handler(peer, **hd)
            self.assertTrue(h(X_Forwarded_For="100.64.0.5")._trusted(v, px))
            self.assertTrue(h(X_Forwarded_For="1.2.3.4, 100.64.0.5")._trusted(v, px))
            self.assertFalse(h(key=None, X_Forwarded_For="100.64.0.5")._trusted(v, px))  # 같은 기기의 다른 컨테이너
            self.assertFalse(h(key="x" * 32, X_Forwarded_For="100.64.0.5")._trusted(v, px))
            self.assertFalse(h(X_Forwarded_For="100.64.0.1")._trusted(v, px))  # viewer 아님
            self.assertFalse(h()._trusted(v, px))  # XFF 없음
            self.assertFalse(h(X_Forwarded_For="100.64.0.5")._trusted(v))  # proxies 미설정
            self.assertFalse(h(X_Forwarded_For="100.64.0.2")._trusted(v + px, px))  # 프록시 자신
            self.assertFalse(h("100.1.1.1", X_Forwarded_For="100.64.0.5")._trusted(v, px))  # 다른 기기가 직접 XFF
            self.assertFalse(h("127.0.0.1", X_Forwarded_For="100.64.0.5")._trusted(v, ["127.0.0.1"]))
            for bad in ("100.64.0.5:5", "100.64.0.5%x", "::ffff:100.64.0.5"):
                self.assertFalse(h(X_Forwarded_For=bad)._trusted(v, px), bad)
            two = h(X_Forwarded_For="100.64.0.5")
            two.headers["X-Forwarded-For"] = "100.64.0.5"  # 두 줄
            self.assertFalse(two._trusted(v, px))
            self.assertFalse(h(X_Forwarded_For="192.168.0.5")._trusted(["192.168.0.5"], px))
            config.PROXY_KEY = ""  # 키를 정하지 않으면 proxies 를 믿지 않는다
            self.assertFalse(h(key="", X_Forwarded_For="100.64.0.5")._trusted(v, px))
        finally:
            config.PROXY_KEY = old

    def test_client_disconnect_not_logged(self):
        """클라이언트가 먼저 끊은 연결은 traceback 을 남기지 않고, 다른 예외는 남긴다."""
        import contextlib, io
        srv = server.Server.__new__(server.Server)
        for exc, logged in ((ConnectionAbortedError(10053, "aborted"), False), (TimeoutError("slow client"), False),
                            (ValueError("boom"), True)):
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                try:
                    raise exc
                except Exception:
                    srv.handle_error(None, ("100.1.2.3", 1))
            self.assertEqual(bool(buf.getvalue()), logged, exc)

    def test_peer_ok_uses_last_forwarded_address(self):
        self.assertTrue(self.handler("172.18.0.2", X_Forwarded_For="8.8.8.8, 192.168.0.5")._peer_ok())
        self.assertFalse(self.handler("172.18.0.2", X_Forwarded_For="192.168.0.5, 8.8.8.8")._peer_ok())
        self.assertFalse(self.handler("172.18.0.3", X_Forwarded_For="192.168.0.5")._peer_ok())  # Caddy 가 아닌 컨테이너


if __name__ == "__main__":
    unittest.main()
