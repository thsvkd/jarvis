"""Kimi Code 활성 자격과 한도 수집."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import kimi_usage


class KimiUsageTest(unittest.TestCase):
    def test_selected_valid_account_is_read_without_secret_output(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"KIMI_CODE_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "credentials"))
            with open(os.path.join(tmp, "config.toml"), "w", encoding="utf-8") as f:
                f.write('[providers."managed:kimi-code".oauth]\nstorage = "file"\nkey = "profile"\n')
            path = os.path.join(tmp, "credentials", "profile.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expires_at": 4102444800}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(kimi_usage, "_http", return_value={"usages": {"limit_5h": {"used_ratio": .4}}}) as http:
                result = kimi_usage.read()
            http.assert_called_once_with("synthetic-access")
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 40)
            self.assertNotIn("synthetic", json.dumps(result))

    def test_quota_prefers_enforced_counters_over_zeroed_summary(self):
        """covers: USAGE-3"""
        body = {"usage": {"limit": "100", "used": "29", "resetTime": "2099-01-01T00:00:00Z"},
                "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                            "detail": {"limit": "100", "used": "88"}}],
                "usages": {"limit_5h": {"used_ratio": 0}, "limit_7d": {"used_ratio": 0, "reset_time": "2099-01-01T00:00:00Z"}}}
        windows = kimi_usage._windows(body)
        self.assertEqual([(w["key"], w["used"]) for w in windows], [("session", 88), ("weekly", 29)])

    def test_expired_oauth_is_not_refreshed_or_sent(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"KIMI_CODE_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "credentials"))
            path = os.path.join(tmp, "credentials", "kimi-code.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"access_token": "expired", "refresh_token": "must-not-use", "expires_at": 1}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(kimi_usage, "_http") as http:
                self.assertEqual(kimi_usage.read()["status"], "unavailable")
            http.assert_not_called()

    def test_unverifiable_zero_ratio_is_not_assumed_to_be_unused(self):
        """covers: USAGE-3"""
        self.assertEqual(kimi_usage._windows({"usages": {"limit_5h": {"used_ratio": 0}}}), [])

    def test_unidentified_counter_is_not_labeled_weekly(self):
        """covers: USAGE-3"""
        body = {"usage": {"limit": "100", "used": "29", "resetTime": "2099-01-01T00:00:00Z"},
                "usages": {"limit_7d": {"used_ratio": 0, "reset_time": "2099-01-08T00:00:00Z"}}}
        self.assertEqual(kimi_usage._windows(body), [])


if __name__ == "__main__":
    unittest.main()
