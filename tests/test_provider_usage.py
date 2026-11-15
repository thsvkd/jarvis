"""caam 의 프로필과 한도를 읽는다."""
import json
import unittest
from unittest import mock

from jarvis import provider_usage


class CaamProviderTest(unittest.TestCase):
    def test_grok_and_cursor_read_profile_usage(self):
        """covers: USAGE-3"""
        def read(_exe, *args, **_kwargs):
            provider = args[1]
            if args[0] == "ls":
                return {"profiles": [{"tool": provider, "type": "vault", "name": "p", "active": True,
                                      "identity": {"email": provider + "@example.com", "account_id": "secret-id"}}]}
            return [{"provider": provider, "profile_name": "p", "usage": {
                "quota_status": "ok", "fetched_at": "2026-10-31T00:00:00Z",
                "primary_window": {"used_percent": 25, "window_duration": 30 * 86400 * 10**9},
                "account_id": "secret-id"}}]

        with mock.patch.object(provider_usage.caam_usage, "_json", side_effect=read):
            rows = {p: provider_usage.read_provider("/fake/caam", p) for p in ("grok", "cursor")}
        for p in rows:
            account = rows[p][0]
            self.assertEqual((account["label"], account["active"], account["status"]), (p + "@example.com", True, "ok"))
            self.assertEqual(account["windows"], [{"key": "monthly", "used": 25, "resets_at": None}])
        self.assertNotIn("secret-id", json.dumps(rows))

    def test_unmeasured_or_unidentifiable_is_not_guessed(self):
        """covers: USAGE-3"""
        ls = {"profiles": [{"tool": "grok", "type": "vault", "name": "alias", "active": False,
                            "identity": {"email": "provider-account-id"}}]}
        limits = [{"provider": "grok", "profile_name": "alias", "usage": {
            "quota_status": "degraded", "primary_window": {"unmeasured": True, "used_percent": 0}}}]
        with mock.patch.object(provider_usage.caam_usage, "_json", side_effect=[ls, limits]):
            row = provider_usage.read_provider("/fake/caam", "grok")[0]
        self.assertEqual(row["label"], "기본 계정")
        self.assertEqual(row["windows"], [])
        self.assertNotEqual(row["status"], "ok")

    def test_malformed_time_and_restricted_quota_are_safe(self):
        """covers: USAGE-3"""
        ls = {"profiles": [{"tool": "cursor", "type": "vault", "name": "p",
                            "identity": {"email": "cursor@example.com"}}]}
        limits = [{"provider": "cursor", "profile_name": "p", "usage": {
            "quota_status": "degraded", "limit_stage": "blocked",
            "primary_window": {"used_percent": 30, "resets_at": 123}, "fetched_at": 123}}]
        with mock.patch.object(provider_usage.caam_usage, "_json", side_effect=[ls, limits]):
            row = provider_usage.read_provider("/fake/caam", "cursor")[0]
        self.assertIsNone(row["updated_at"])
        self.assertIsNone(row["windows"][0]["resets_at"])
        self.assertTrue(row["attention"])
        self.assertIsNotNone(row["note"])

    def test_conflicting_usage_identity_does_not_label_another_account(self):
        """covers: USAGE-3"""
        ls = {"profiles": [{"tool": "grok", "type": "vault", "name": "p",
                            "identity": {"email": "one@example.com"}}]}
        limits = [{"provider": "grok", "profile_name": "p", "usage": {
            "quota_status": "ok", "account_id": "other@example.com", "primary_window": {"used_percent": 20}}}]
        with mock.patch.object(provider_usage.caam_usage, "_json", side_effect=[ls, limits]):
            row = provider_usage.read_provider("/fake/caam", "grok")[0]
        self.assertEqual(row["label"], "기본 계정")
        self.assertTrue(row["attention"])

    def test_rows_carry_a_closed_failure_code(self):
        """covers: USAGE-17. caam 행의 failure 와 attention. 한도 창 소진(limit_stage)만으로는 attention 이 아니다"""
        cases = (("login", {"health": {"login_required": True}}, {}, ("auth", True)),
                 ("mismatch", {}, {"account_id": "other@example.com"}, ("unknown", True)),
                 ("limit_stage", {}, {"limit_stage": "blocked"}, ("limit", False)),
                 ("degraded", {}, {"quota_status": "degraded"}, ("unknown", True)),
                 ("ok", {}, {}, (None, False)))
        for name, ident, usage, want in cases:
            ls = {"profiles": [{"tool": "grok", "type": "vault", "name": "p", "identity": {"email": "one@example.com"}, **ident}]}
            limits = [{"provider": "grok", "profile_name": "p", "usage": {"quota_status": "ok", "primary_window": {"used_percent": 20}, **usage}}]
            with self.subTest(name), mock.patch.object(provider_usage.caam_usage, "_json", side_effect=[ls, limits]):
                row = provider_usage.read_provider("/fake/caam", "grok")[0]
                self.assertEqual((row["failure"], row["attention"]), want)
                self.assertIn(row["failure"], (*provider_usage.FAILURES, None))
                if name == "limit_stage":
                    self.assertEqual(row["note"], "사용 한도에 도달했습니다(리셋 시각에 풀립니다).")


if __name__ == "__main__":
    unittest.main()
