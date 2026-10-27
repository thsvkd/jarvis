"""실제 로컬 소켓으로 프레임·인증·런타임 교체 경계를 검증한다."""
import json
import os
import socket
import tempfile
import subprocess
import threading
import unittest
from unittest.mock import patch

from jarvis import orca_rpc


class RpcTest(unittest.TestCase):
    def test_named_pipe_child_imports_from_package_directory(self):
        metadata = {"runtimeId": "r1", "authToken": "PRIVATE", "transports": [{"kind": "named-pipe", "endpoint": "pipe"}]}
        with patch("jarvis.orca_rpc.orca.find_dir", return_value="x"), \
                patch("jarvis.orca_rpc.orca._read", return_value=metadata), \
                patch("jarvis.orca_rpc.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"appVersion":"1.4.237"}', '')) as child:
            self.assertEqual(orca_rpc.call("updater.getStatus"), {"appVersion": "1.4.237"})
        self.assertEqual(child.call_args.kwargs["cwd"], os.path.dirname(os.path.dirname(orca_rpc.__file__)))
        self.assertNotIn("PRIVATE", str(child.call_args.args[0]))

    def request(self, change=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.sock")
            srv = socket.socket(socket.AF_UNIX)
            srv.bind(path)
            srv.listen()
            metadata = {"runtimeId": "r1", "authToken": "PRIVATE", "transports": [{"kind": "unix", "endpoint": path}]}
            with open(os.path.join(d, "orca-runtime.json"), "w") as f:
                json.dump(metadata, f)
            def serve():
                with srv, srv.accept()[0] as conn, conn.makefile("rb") as f:
                    req = json.loads(f.readline())
                    result = {"method": req["method"], "authenticated": req["authToken"] == "PRIVATE"}
                    response = {"id": req["id"], "ok": True, "result": result, "_meta": {"runtimeId": "r1"}}
                    if change:
                        change(response)
                    conn.sendall(b'{"_keepalive":true}\n' + json.dumps(response).encode() + b'\n')
            t = threading.Thread(target=serve)
            t.start()
            try:
                with patch("jarvis.orca_rpc.orca.find_dir", return_value=d):
                    return orca_rpc.call("updater.getStatus", timeout=2)
            finally:
                t.join(3)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix socket transport")
    def test_authenticated_request_and_keepalive(self):
        self.assertEqual(self.request(), {"method": "updater.getStatus", "authenticated": True})

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix socket transport")
    def test_wrong_response_id_rejected(self):
        with self.assertRaises(RuntimeError):
            self.request(lambda r: r.update(id="other"))

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix socket transport")
    def test_replaced_runtime_rejected(self):
        with self.assertRaises(RuntimeError):
            self.request(lambda r: r.update(_meta={"runtimeId": "other"}))
