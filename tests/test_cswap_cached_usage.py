"""cswap 상태를 읽기 전용으로 해석한다."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import cswap_cached_usage


class CswapCachedTest(unittest.TestCase):
    def test_cached_usage_is_identity_guarded_and_does_not_run_cswap(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cswap_cached_usage, "_root", return_value=tmp):
            os.mkdir(os.path.join(tmp, "cache"))
            def write(name, body):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(body, f)
                os.chmod(p, 0o600)
            write("sequence.json", {"sequence": [1, 2], "accounts": {"1": {"email": "one@example.com", "organizationUuid": "private-org", "alias": "synthetic-secret"}, "2": {"email": "two@example.com", "organizationUuid": "other"}}})
            write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": {"email": "one@example.com", "organizationUuid": "private-org", "fetchedAt": 1900000000, "lastGood": {"five_hour": {"pct": 20}, "seven_day": {"pct": 42}, "scoped": [{"name": "Fable", "pct": 51}, {"name": "synthetic-secret", "pct": 34}]}}, "2": {"email": "old@example.com", "organizationUuid": "other", "lastGood": {"five_hour": {"pct": 99}}}}})
            with mock.patch("subprocess.run", side_effect=AssertionError("조회 명령 실행 금지")):
                rows = cswap_cached_usage.read()["accounts"]
            self.assertEqual(rows[0]["label"], "one@example.com")
            self.assertEqual([w["used"] for w in rows[0]["windows"]], [20, 42, 51])
            self.assertEqual(rows[0]["buckets"][0]["used"], 34)
            self.assertEqual(rows[1]["status"], "error")
            self.assertNotIn("synthetic-secret", json.dumps(rows))
            self.assertNotIn("private-org", json.dumps(rows))

    def test_missing_cache_does_not_invent_usage(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cswap_cached_usage, "_root", return_value=tmp):
            self.assertIsNone(cswap_cached_usage.read())
