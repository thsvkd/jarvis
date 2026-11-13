"""SSH bridge IPC(jarvis/ipc.py): 게이트웨이의 소유자 전용 소켓과 `python -m jarvis.ipc` 가 core 전용 조작 경로만 core 로 받아 나른다.
POSIX 만 실제 소켓으로 돈다. Windows named pipe 는 이 테스트로 검증하지 않는다(실기기 확인이 따로 필요하다)."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis import config, ipc, launch, server  # noqa: E402
from test_server_access import FakeCollector  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(sys.platform == "win32", "POSIX 유닉스 소켓 경로")
class BridgeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="jv-", dir="/tmp")  # AF_UNIX 경로 길이(macOS 104바이트) 안에 둔다
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", tmp]))
        self.root = tmp
        for p in (mock.patch.object(launch, "ROOT", tmp), mock.patch.object(config, "CORE", ["100.64.0.1"])):
            p.start()
            self.addCleanup(p.stop)
        handler = server.make_handler(FakeCollector())
        self.srv = ipc.serve(type("IpcHandler", (handler,), {"ipc": True}), None)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def bridge(self, req):
        r = subprocess.run([sys.executable, "-m", "jarvis.ipc"], input=req, capture_output=True, cwd=ROOT, timeout=20,
                           env={**os.environ, "JARVIS_RELEASES": self.root, "JARVIS_CONFIG": os.devnull})
        return r.returncode, r.stdout, r.stderr

    def get(self, path, extra=b""):
        return self.bridge(b"GET " + path + b" HTTP/1.0\r\nHost: 127.0.0.1:9300\r\n" + extra + b"Connection: close\r\n\r\n")

    def test_usage_paths_count_as_core(self):
        """covers: SSH-4"""
        with mock.patch.object(server.cli_login, "status", return_value={"state": "idle"}) as st:
            code, out, _ = self.get(b"/api/usage/login?provider=claude")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(b"HTTP/1.1 200"), out[:40])
        self.assertTrue(out.endswith(b'{"state":"idle"}'), out[-40:])
        st.assert_called_once_with("claude")

    def test_module_and_update_paths_count_as_core(self):
        """covers: SSH-4 (Windows SSH 기기의 모듈·jarvis 갱신 조작)"""
        with mock.patch.object(server.modules_api, "status", return_value={"modules": {}}), \
                mock.patch.object(server.self_update, "status", return_value={"state": "idle"}):
            for path, body in ((b"/api/modules", b'{"modules":{}}'), (b"/api/jarvis/update", b'{"state":"idle"}')):
                code, out, _ = self.get(path)
                self.assertTrue(out.startswith(b"HTTP/1.1 200"), (path, out[:40]))
                self.assertTrue(out.endswith(body), out[-40:])

    def test_other_paths_are_404(self):
        """covers: SSH-4"""
        for path in (b"/api/snapshot", b"/api/changes", b"/api/devices", b"/api/modulesx", b"/api/jarvis/updatex"):
            code, out, _ = self.get(path)
            self.assertTrue(out.startswith(b"HTTP/1.1 404"), (path, out[:40]))

    def test_socket_is_owner_only(self):
        """covers: SSH-4"""
        d = os.path.dirname(ipc.address())
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(ipc.address()).st_mode), 0o600)

    def test_jarvis_bridge_goes_through_the_entry_point(self):
        """covers: SSH-4 (core 가 원격에서 부르는 `python -m jarvis bridge`)"""
        req = b"GET /api/usage/login?provider=claude HTTP/1.0\r\nHost: 127.0.0.1:9300\r\nConnection: close\r\n\r\n"
        with mock.patch.object(server.cli_login, "status", return_value={"state": "idle"}):
            r = subprocess.run([sys.executable, "-m", "jarvis", "bridge"], input=req, capture_output=True, cwd=ROOT,
                               timeout=20, env={**os.environ, "JARVIS_RELEASES": self.root, "JARVIS_CONFIG": os.devnull})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith(b"HTTP/1.1 200"), r.stdout[:60])  # 표준 출력에 다른 줄이 섞이지 않는다

    def test_no_gateway_is_92(self):
        """covers: SSH-4"""
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(ipc.address())
        code, out, err = self.get(b"/api/usage/independent")
        self.assertEqual((code, out), (92, b""))
        self.assertIn("게이트웨이 IPC 에 닿지 못했습니다".encode(), err)


if __name__ == "__main__":
    unittest.main()
