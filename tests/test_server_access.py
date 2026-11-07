"""서버 접근 통제: 본문 거부, 맞춤 헤더, core·viewers 의 tailnet 판정, 프록시 경유. 이전 Orca 경로는 남지 않는다."""
import email.message
import json
import os
import site
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from jarvis import config, server

ROOT = Path(__file__).resolve().parents[1]


class FakeCollector:
    def allowed_hosts(self):
        return {"127.0.0.1"}

    def caddy_ips(self):
        return {"172.18.0.2"}


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.httpd = server.Server(("127.0.0.1", 0), server.make_handler(FakeCollector()))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, headers=None, post=False):
        """(http 상태, 응답)."""
        req = urllib.request.Request(self.base + path, headers=headers or {}, data=b"" if post else None, method="POST" if post else "GET")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_body_rejected(self):
        """본문이 붙은 GET 은 400 하나로 끝내고 연결을 닫는다(본문 안의 두 번째 요청을 파싱하지 않는다)."""
        import socket
        inner = b"GET /api/usage/independent HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
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
            c.sendall(b"POST /api/usage/account HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: %d\r\n\r\n" % len(inner) + inner)
            data = b""
            while chunk := c.recv(65536):
                data += chunk
        self.assertEqual(data.count(b"HTTP/1."), 1)
        self.assertIn(b" 400 ", data.split(b"\r\n", 1)[0])

    def test_action_posts_need_the_header_and_the_configured_core(self):
        """covers: SSH-5. 맞춤 헤더가 없으면 403, 헤더가 있어도 루프백은 core 가 아니다. 쓰기는 일어나지 않는다"""
        with mock.patch.object(server.cli_login, "start", side_effect=AssertionError("로그인 시작 금지")), \
                mock.patch.object(server.claude_accounts, "switch", side_effect=AssertionError("전환 금지")), \
                mock.patch.object(server.self_update, "start", side_effect=AssertionError("갱신 금지")):
            for path, action in (("/api/usage/login?provider=codex", "usage-login"), ("/api/usage/account?slot=2", "usage-account"),
                                 ("/api/jarvis/update", "jarvis-update")):
                code, body = self.call(path, post=True)
                self.assertEqual((code, "헤더" in body["error"]), (403, True), path)
                code, body = self.call(path, {"X-Jarvis-Action": action}, post=True)
                self.assertEqual((code, "core" in body["error"]), (403, True), path)

    def test_old_orca_actions_and_routes_are_gone(self):
        """이전 Orca 헤더는 맞춤 헤더로 인정하지 않고, 이전 경로는 없는 경로다."""
        for action in ("orca-issue", "orca-update"):
            self.assertEqual(self.call("/api/jarvis/update", {"X-Jarvis-Action": action}, post=True)[0], 403)
        for path in ("/api/orca", "/api/orca/update", "/api/orca/usage", "/api/orca/login"):
            self.assertEqual(self.call(path)[0], 404, path)
        for path, action in (("/api/orca/login", "usage-login"), ("/api/orca/account", "usage-account")):
            self.assertEqual(self.call(path, {"X-Jarvis-Action": action}, post=True)[0], 404, path)

    def test_no_loopback(self):
        """covers: SSH-5"""
        old, config.CORE = config.CORE, ["127.0.0.1"]
        try:
            self.assertEqual(self.call("/api/usage/independent")[0], 403)
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


class EntryPointTest(unittest.TestCase):
    """실제 진입점을 임시 홈과 빈 설정으로 돌린다(임시 홈이면 런처가 받아 둔 릴리스로 넘어가지 않고 이 코드가 돈다)."""

    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "JARVIS_CONFIG": os.devnull, "PYTHONUSERBASE": site.getuserbase()}
            return subprocess.run([sys.executable, "-m", "jarvis", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)

    def test_version_and_doctor_run_and_the_orca_module_is_unknown(self):
        r = self.run_cli("--version")
        self.assertEqual((r.returncode, r.stdout.startswith("jarvis ")), (0, True), r.stderr)
        r = self.run_cli("module", "doctor", "status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["module"], "doctor")
        r = self.run_cli("module", "orca", "status", "--json")
        self.assertEqual((r.returncode, r.stdout), (1, ""), r.stderr)
        self.assertIn("사용법", r.stderr)


if __name__ == "__main__":
    unittest.main()
