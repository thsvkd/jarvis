"""MiniMax Token Plan 의 독립 사용량 조회."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import minimax_usage


class MiniMaxUsageTest(unittest.TestCase):
    def test_current_oauth_quota_uses_explicit_remaining_percentage(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"MMX_CONFIG_DIR": tmp}, clear=True):
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"region": "global", "oauth": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expires_at": "2099-01-01T00:00:00Z"}}, f)
            os.chmod(path, 0o600)
            body = {"base_resp": {"status_code": 0}, "model_remains": [{"model_name": "MiniMax-M*", "current_interval_remaining_percent": 70, "start_time": 4070890800000, "end_time": 4070908800000}]}
            with mock.patch.object(minimax_usage, "_http", return_value=body) as http:
                result = minimax_usage.read()
            http.assert_called_once_with("https://api.minimax.io", "synthetic-access")
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 30)
            self.assertNotIn("synthetic", json.dumps(result))

    def test_ambiguous_count_is_not_guessed_and_expired_oauth_is_not_refreshed(self):
        """covers: USAGE-3"""
        self.assertEqual(minimax_usage._windows({"base_resp": {"status_code": 0}, "model_remains": [{"model_name": "MiniMax-M*", "current_interval_total_count": 100, "current_interval_usage_count": 20}]}), [])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"MMX_CONFIG_DIR": tmp}, clear=True):
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"oauth": {"access_token": "expired", "refresh_token": "must-not-use", "expires_at": "2000-01-01T00:00:00Z"}}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(minimax_usage, "_http") as http:
                self.assertEqual(minimax_usage.read()["status"], "unavailable")
            http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
