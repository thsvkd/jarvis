"""OpenCode Go 사용량 조회와 자격 비노출."""
import json
import base64
import contextlib
import http.server
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from jarvis import opencode_usage


class OpenCodeUsageTest(unittest.TestCase):
    def test_v2_go_key_is_used_when_console_account_is_absent(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}, clear=True):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "opencode.db")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE credential (id TEXT, integration_id TEXT, value TEXT, active INTEGER, time_created INTEGER)")
                db.execute("INSERT INTO credential VALUES ('a', 'opencode-go', ?, 1, 1)", (json.dumps({"type": "key", "key": "synthetic-go-key"}),))
            os.chmod(path, 0o600)
            self.assertEqual(opencode_usage._v2_credential(), ("synthetic-go-key", None))

    def test_running_service_is_not_asked_to_refresh_credentials(self):
        """covers: USAGE-3"""
        body = {"usage": {"weekly": {"percent": 33, "resetsAt": "2099-01-01T00:00:00Z"}}}
        with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="2.2.0")) as run, \
                mock.patch.object(opencode_usage, "_request") as service, \
                mock.patch.object(opencode_usage, "_v2_credential", return_value=("synthetic", "private-org")), \
                mock.patch.object(opencode_usage, "_go_api", return_value=body):
            result = opencode_usage.read("/fake/opencode")
        service.assert_not_called()
        run.assert_called_once_with(["/fake/opencode", "--version"], 6)
        self.assertEqual(result["accounts"][0]["windows"][0]["used"], 33)

    def test_v2_uses_explicit_database_path(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "OPENCODE_DB": "named.db"}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "named.db")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE credential (id TEXT, integration_id TEXT, value TEXT, active INTEGER, time_created INTEGER)")
                db.execute("INSERT INTO credential VALUES ('a', 'opencode', ?, 1, 1)", (json.dumps({"type": "key", "key": "synthetic-secret", "metadata": {"orgID": "private-org"}}),))
            os.chmod(path, 0o600)
            self.assertEqual(opencode_usage._v2_credential(), ("synthetic-secret", "private-org"))

    def test_stopped_v2_reads_active_console_credential_without_refresh(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "opencode.db")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE credential (id TEXT, integration_id TEXT, value TEXT, active INTEGER, time_created INTEGER)")
                for ident, active, value in (("old", 0, {"type": "key", "key": "old-secret", "metadata": {"orgID": "old-org"}}),
                                             ("current", 1, {"type": "oauth", "access": "current-secret", "refresh": "refresh-secret", "expires": int(time.time()*1000)+600000, "metadata": {"orgID": "org-private"}})):
                    db.execute("INSERT INTO credential VALUES (?, 'opencode', ?, ?, 1)", (ident, json.dumps(value), active))
            os.chmod(path, 0o600)
            body = {"usage": {"weekly": {"percent": 46, "resetsAt": "2027-01-01T00:00:00Z", "status": "ok"}}}
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="2.2.0")), \
                    mock.patch.object(opencode_usage, "_go_api", return_value=body) as api:
                result = opencode_usage.read("/fake/opencode")
            api.assert_called_once_with("current-secret", "org-private")
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 46)
            self.assertNotIn("secret", json.dumps(result))
            self.assertNotIn("org-private", json.dumps(result))

    def test_expired_v2_credential_is_not_sent_or_refreshed(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "opencode.db")
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE credential (id TEXT, integration_id TEXT, value TEXT, active INTEGER, time_created INTEGER)")
                db.execute("INSERT INTO credential VALUES ('x', 'opencode', ?, 1, 1)", (json.dumps({"type": "oauth", "access": "expired", "expires": 1, "metadata": {"orgID": "org"}}),))
            os.chmod(path, 0o600)
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="2.2.0")), \
                    mock.patch.object(opencode_usage, "_go_api") as api:
                self.assertEqual(opencode_usage.read("/fake/opencode")["status"], "unavailable")
            api.assert_not_called()

    def test_go_key_from_independent_legacy_store(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "auth.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"opencode-go": {"type": "api", "key": "synthetic-secret"}}, f)
            os.chmod(path, 0o600)
            data = {"usage": {"weekly": {"status": "ok", "percent": 37, "resetsAt": "2027-01-01T00:00:00Z"}}}
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="1.2.27")), \
                    mock.patch.object(opencode_usage, "_go_api", return_value=data) as get:
                result = opencode_usage.read("/fake/opencode")
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 37)
            self.assertEqual(get.call_args.args[0], "synthetic-secret")
            self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_v2_stopped_service_does_not_use_stale_v1_key(self):
        """covers: USAGE-3"""
        with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="opencode v2.2.0")), \
                mock.patch.object(opencode_usage, "_legacy_key") as key, \
                mock.patch.object(opencode_usage, "_v2_credential", side_effect=ValueError("absent")), \
                mock.patch.object(opencode_usage, "_go_api") as get:
            self.assertEqual(opencode_usage.read("/fake/opencode")["status"], "unavailable")
        key.assert_not_called()
        get.assert_not_called()

    def test_end_to_end_with_synthetic_registered_service(self):
        """covers: USAGE-3"""
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                token = "Basic " + base64.b64encode(b"opencode:synthetic-secret").decode()
                if self.headers.get("Authorization") != token:
                    self.send_response(401)
                    self.end_headers()
                    return
                body = json.dumps({"data": {"go": {"weekly": {"percent": 42, "resetsAt": 1800000000000}}}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
                os.mkdir(os.path.join(tmp, "opencode"))
                endpoint = f"http://127.0.0.1:{server.server_port}"
                path = os.path.join(tmp, "opencode", "service.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"url": endpoint, "pid": 123, "password": "synthetic-secret"}, f)
                os.chmod(path, 0o600)
                with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout=endpoint)):
                    result = opencode_usage.read("/fake/opencode", use_service=True)
                self.assertEqual(result["accounts"][0]["windows"][0]["used"], 42)
                self.assertNotIn("synthetic-secret", json.dumps(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_local_request_uses_private_auth_and_does_not_follow_redirect(self):
        """covers: USAGE-3"""
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/other")
                self.end_headers()
            def log_message(self, *_args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(OSError):
                opencode_usage._request(f"http://127.0.0.1:{server.server_port}", "synthetic-secret")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "/api/experimental/usage/limits")
        self.assertEqual(seen[0][1], "Basic " + base64.b64encode(b"opencode:synthetic-secret").decode())

    def test_private_service_password_never_appears_in_command_or_result(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "service.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": "http://127.0.0.1:4321", "pid": 123, "password": "synthetic-secret"}, f)
            os.chmod(path, 0o600)
            info = {"data": {"go": {"weekly": {"percent": 20, "resetsAt": 1800000000000}}}}
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="http://127.0.0.1:4321")), \
                    mock.patch.object(opencode_usage, "_request", return_value=info) as request:
                result = opencode_usage.read("/fake/opencode", use_service=True)
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 20)
            self.assertEqual(request.call_args.args[1], "synthetic-secret")
            self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_malformed_registration_is_unavailable(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            path = os.path.join(tmp, "opencode", "service.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": 123, "pid": 12}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="http://127.0.0.1:4321")):
                self.assertEqual(opencode_usage.read("/fake/opencode", use_service=True)["status"], "unavailable")

    def test_registration_symlink_is_rejected(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "opencode"))
            source = os.path.join(tmp, "source.json")
            with open(source, "w", encoding="utf-8") as f:
                json.dump({"url": "http://127.0.0.1:4321", "pid": 123, "password": "synthetic-secret"}, f)
            os.chmod(source, 0o600)
            link = os.path.join(tmp, "opencode", "service.json")
            try:
                os.symlink(source, link)
                rejected_link = contextlib.nullcontext()
            except (OSError, NotImplementedError):
                # Windows 의 링크 생성 권한이 없으면 lstat 결과만 대체한다.
                rejected_link = mock.patch.object(opencode_usage.os, "lstat", return_value=mock.Mock(st_mode=0o120777))
            with mock.patch.object(opencode_usage, "_run", return_value=mock.Mock(returncode=0, stdout="http://127.0.0.1:4321")), \
                    mock.patch.object(opencode_usage, "_request") as request, rejected_link:
                self.assertEqual(opencode_usage.read("/fake/opencode", use_service=True)["status"], "unavailable")
                request.assert_not_called()

    def test_local_service_response_is_sanitized(self):
        """covers: USAGE-3"""
        data = {"data": {"workspace": {"id": "private-id", "name": "private-name"}, "go": {
            "rolling": {"percent": 12.4, "limited": False, "resetsAt": 1800000000000},
            "weekly": {"percent": 34, "limited": False, "resetsAt": 1800000000000},
            "monthly": {"percent": 56, "limited": False, "resetsAt": 1800000000000}}, "budgets": []}}
        status = mock.Mock(returncode=0, stdout="http://127.0.0.1:4321\n")
        with mock.patch.object(opencode_usage.subprocess, "run", return_value=status) as run, \
                mock.patch.object(opencode_usage, "_registration", return_value=None), \
                mock.patch.object(opencode_usage, "_request", return_value=data) as request:
            result = opencode_usage.read("/fake/opencode", use_service=True)
        self.assertEqual([c.args[0][1:] for c in run.call_args_list], [["service", "status"]])
        request.assert_called_once_with("http://127.0.0.1:4321", None)
        account = result["accounts"][0]
        self.assertEqual([(x["key"], x["used"]) for x in account["windows"]], [("session", 12), ("weekly", 34), ("monthly", 56)])
        self.assertNotIn("private-id", json.dumps(result))
        self.assertNotIn("private-name", json.dumps(result))

    def test_stopped_or_remote_service_is_not_started_or_contacted(self):
        """covers: USAGE-3"""
        for text in ("stopped", "http://example.com:1234", "http://127.0.0.1:1234@evil.example", "http://127.0.0.1:1234/path"):
            with self.subTest(text=text), mock.patch.object(opencode_usage.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=text)) as run:
                result = opencode_usage.read("/fake/opencode", use_service=True)
                self.assertEqual(run.call_count, 2 if text == "stopped" else 1)
                self.assertEqual(result["status"], "unavailable")

    def test_bad_response_is_not_reported_as_zero(self):
        """covers: USAGE-3"""
        status = mock.Mock(returncode=0, stdout="http://127.0.0.1:4321")
        usage = {"data": {"go": {"rolling": {"percent": None}}}}
        with mock.patch.object(opencode_usage.subprocess, "run", return_value=status), \
                mock.patch.object(opencode_usage, "_registration", return_value=None), \
                mock.patch.object(opencode_usage, "_request", return_value=usage):
            self.assertEqual(opencode_usage.read("/fake/opencode", use_service=True)["status"], "unavailable")
        with mock.patch.object(opencode_usage.subprocess, "run", side_effect=subprocess.TimeoutExpired("opencode", 5)):
            self.assertEqual(opencode_usage.read("/fake/opencode", use_service=True)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
