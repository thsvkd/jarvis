"""Antigravity CLI 의 구조화된 사용량 응답."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import antigravity_usage


class ParseTest(unittest.TestCase):
    def test_active_account_uses_same_access_token_for_verified_email_and_quota(self):
        """covers: USAGE-2, USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMINI_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "antigravity-cli"))
            path = os.path.join(tmp, "antigravity-cli", "antigravity-oauth-token")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"token": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expiry": "2099-01-01T00:00:00Z"}}, f)
            os.chmod(path, 0o600)
            quota = {"groups": [{"displayName": "Gemini", "buckets": [{"window": "weekly", "remainingFraction": .2, "resetTime": "2099-01-01T00:00:00Z"}]}]}
            identity = {"email": "person@example.com", "email_verified": True, "sub": "private-id"}
            with mock.patch.object(antigravity_usage, "_http", side_effect=[quota, identity]) as http:
                result = antigravity_usage.read()
            self.assertEqual(http.call_count, 2)
            self.assertEqual(result["accounts"][0]["label"], "person@example.com")
            self.assertEqual(result["accounts"][0]["windows"][0]["used"], 80)
            self.assertNotIn("synthetic", json.dumps(result))
            self.assertNotIn("private-id", json.dumps(result))

    def test_expired_token_is_not_refreshed_or_sent(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GEMINI_HOME": tmp}):
            os.mkdir(os.path.join(tmp, "antigravity-cli"))
            path = os.path.join(tmp, "antigravity-cli", "antigravity-oauth-token")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"token": {"access_token": "expired", "refresh_token": "must-not-use", "expiry": "2000-01-01T00:00:00Z"}}, f)
            os.chmod(path, 0o600)
            with mock.patch.object(antigravity_usage, "_http") as http:
                self.assertEqual(antigravity_usage.read()["status"], "unavailable")
            http.assert_not_called()

    def test_unverified_or_unavailable_identity_uses_default_account(self):
        """covers: USAGE-2, USAGE-3"""
        quota = {"groups": [{"displayName": "Gemini", "buckets": [{"window": "weekly", "remainingFraction": .4}]}]}
        for identity in ({"email": "person@example.com", "email_verified": False}, OSError("identity unavailable")):
            with self.subTest(identity=identity), mock.patch.object(antigravity_usage, "_token", return_value="synthetic"), \
                    mock.patch.object(antigravity_usage, "_http", side_effect=[quota, identity]):
                self.assertEqual(antigravity_usage.read()["accounts"][0]["label"], "기본 계정")

    def test_token_is_never_sent_to_an_unapproved_host(self):
        """covers: USAGE-3"""
        with mock.patch.object(antigravity_usage.urllib.request, "build_opener") as opener:
            with self.assertRaises(ValueError):
                antigravity_usage._http("https://example.org/collect", "synthetic-secret")
        opener.assert_not_called()

    def test_quota_groups_and_disabled_buckets(self):
        """covers: USAGE-3"""
        payload = {"groups": [
            {"displayName": "Gemini", "buckets": [{"window": "5h", "remainingFraction": .6}, {"window": "weekly", "remainingFraction": .2}]},
            {"displayName": "Disabled", "buckets": [{"window": "weekly", "remainingFraction": 0, "disabled": True}]}]}
        result = antigravity_usage._quota(payload)
        self.assertEqual([(w["key"], w["used"]) for w in result["windows"]], [("session", 40), ("weekly", 80)])
        self.assertEqual(len(result["buckets"]), 2)

    def test_invalid_payload_is_not_zero_usage(self):
        """covers: USAGE-3"""
        for value in ({}, {"groups": [{"displayName": "x", "buckets": [{"remainingFraction": None}]}]}):
            with self.subTest(value=value):
                self.assertIsNone(antigravity_usage._quota(value))

    def test_unknown_window_shape_does_not_break_other_usage(self):
        """covers: USAGE-3"""
        payload = {"groups": [{"displayName": "Gemini", "buckets": [{"window": {}, "remainingFraction": .5}]}]}
        self.assertEqual(antigravity_usage._quota(payload)["buckets"][0]["used"], 50)


if __name__ == "__main__":
    unittest.main()
