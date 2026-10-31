"""현재 Codex 로그인에서 갱신 없이 사용량을 읽는다."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import codex_native_usage


class NativeCodexTest(unittest.TestCase):
    def test_live_account_without_vault_is_read_without_secret_output(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
            path = os.path.join(tmp, "auth.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tokens": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "account_id": "private-id"}}, f)
            os.chmod(path, 0o600)
            body = {"rate_limit": {"primary_window": {"used_percent": 27, "limit_window_seconds": 18000, "reset_at": 1900000000},
                                   "secondary_window": {"used_percent": 61, "limit_window_seconds": 604800, "reset_at": 1900000001}}}
            with mock.patch.object(codex_native_usage, "_http", return_value=body) as http:
                rows = codex_native_usage.read()
            http.assert_called_once_with("synthetic-access", "private-id")
            self.assertEqual([(w["key"], w["used"]) for w in rows[0]["windows"]], [("session", 27), ("weekly", 61)])
            self.assertNotIn("synthetic", json.dumps(rows))
            self.assertNotIn("private-id", json.dumps(rows))

    def test_missing_or_invalid_credentials_do_not_trigger_refresh(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CODEX_HOME": tmp}), \
                mock.patch.object(codex_native_usage, "_http") as http:
            self.assertEqual(codex_native_usage.read(), [])
        http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
