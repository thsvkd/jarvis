"""Gemini Code Assist 의 현재 계정 사용량."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import gemini_usage


class GeminiUsageTest(unittest.TestCase):
    def test_active_quota_and_verified_identity_without_secret_output(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMINI_HOME": tmp, "GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True):
            path = os.path.join(tmp, "oauth_creds.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expiry_date": 4070908800000}, f)
            os.chmod(path, 0o600)
            body = {"buckets": [{"modelId": "gemini-model", "remainingFraction": .7, "resetTime": "2099-01-01T00:00:00Z"}]}
            with mock.patch.object(gemini_usage, "_http", return_value=body) as http, \
                    mock.patch.object(gemini_usage.antigravity_usage, "_http", return_value={"email": "test@example.com", "email_verified": True, "sub": "private-id"}):
                result = gemini_usage.read()
            http.assert_called_once_with("synthetic-access", "test-project")
            self.assertEqual(result["accounts"][0]["label"], "test@example.com")
            self.assertEqual(result["accounts"][0]["buckets"][0]["used"], 30)
            self.assertNotIn("synthetic", json.dumps(result))
            self.assertNotIn("private-id", json.dumps(result))
            self.assertNotIn("test-project", json.dumps(result))

    def test_expired_or_missing_project_never_calls_provider(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMINI_HOME": tmp}, clear=True):
            path = os.path.join(tmp, "oauth_creds.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"access_token": "expired", "refresh_token": "must-not-use", "expiry_date": 1}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(gemini_usage, "_http") as http:
                self.assertEqual(gemini_usage.read()["status"], "unavailable")
                with mock.patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
                    self.assertEqual(gemini_usage.read()["status"], "unavailable")
            http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
