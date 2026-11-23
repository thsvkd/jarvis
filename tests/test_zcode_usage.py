"""Z.ai 구독 자격과 한도 수집."""
import json
import os
import unittest
from unittest import mock

from jarvis.usage import zcode


class ZCodeUsageTest(unittest.TestCase):
    def test_explicit_provider_key_queries_quota_without_leaking_it(self):
        """covers: USAGE-3"""
        body = {"data": {"limits": [{"type": "TOKENS_LIMIT", "percentage": 64}, {"type": "TIME_LIMIT", "percentage": 30}]}}
        with mock.patch.dict(os.environ, {"ZAI_API_KEY": "synthetic-secret"}, clear=True), \
                mock.patch.object(zcode, "_http", return_value=body) as http:
            result = zcode.read()
        http.assert_called_once_with("https://api.z.ai", "synthetic-secret")
        self.assertEqual(result["accounts"][0]["windows"][0]["used"], 64)
        self.assertEqual(result["accounts"][0]["buckets"][0]["used"], 30)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_unrelated_anthropic_token_is_not_sent(self):
        """covers: USAGE-3"""
        with mock.patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "other-secret", "ANTHROPIC_BASE_URL": "https://evil.example"}, clear=True), \
                mock.patch.object(zcode, "_http") as http:
            self.assertEqual(zcode.read()["status"], "unavailable")
        http.assert_not_called()

    def test_missing_percent_is_not_reported_as_zero(self):
        """covers: USAGE-3"""
        self.assertEqual(zcode._limits({"data": {"limits": [{"type": "TOKENS_LIMIT"}]}}), ([], []))


if __name__ == "__main__":
    unittest.main()
