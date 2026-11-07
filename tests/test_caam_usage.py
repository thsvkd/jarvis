"""caam(coding_agent_account_manager) 호출: 프로필과 한도를 읽는 범위와 오류 문구."""
import json
import subprocess
import unittest
from unittest import mock

from jarvis import caam_usage, provider_usage

LS = {"profiles": [
    {"tool": "codex", "name": "work", "type": "vault", "active": False, "identity": {"email": "work@example.com", "account_id": "acct-secret"}},
    {"tool": "codex", "name": "old", "type": "vault", "active": False, "identity": {"email": "old@example.com"}},
    {"tool": "codex", "name": "iso", "type": "isolated", "active": False, "identity": {"email": "iso@example.com"}}],
    "count": 3}
LIMITS = [
    {"provider": "codex", "profile_name": "work", "usage": {"account_id": "acct-secret", "primary_window": {"used_percent": 47}}},
    {"provider": "codex", "profile_name": "old", "usage": {"error": "unauthorized: token expired or invalid"}},
    {"provider": "codex", "profile_name": "gone", "usage": {}}]


def proc(body, rc=0):
    return mock.Mock(stdout=json.dumps(body), returncode=rc)


class ReadTest(unittest.TestCase):
    def test_reads_vault_profiles_only(self):
        with mock.patch.object(caam_usage.subprocess, "run", side_effect=[proc(LS), proc(LIMITS)]) as r:
            rows = provider_usage.read_provider("/fake/caam", "codex")
        self.assertEqual([c[0][0][1:] for c in r.call_args_list], [["ls", "codex", "--json"], ["limits", "codex", "--format", "json"]])
        self.assertEqual([a["label"] for a in rows], ["work@example.com", "old@example.com"])  # ls 에 없는 gone 과 isolated 는 뺀다
        self.assertNotIn("acct-secret", json.dumps(rows))

    def test_no_profiles_skips_api(self):
        with mock.patch.object(caam_usage.subprocess, "run", return_value=proc({"profiles": [], "count": 0})) as r:
            self.assertEqual(provider_usage.read_provider("/fake/caam", "codex"), [])
        self.assertEqual(r.call_count, 1)


class JsonTest(unittest.TestCase):
    def test_errors_hide_home(self):
        for side, want in ((subprocess.TimeoutExpired(["/home/alice/.local/bin/caam"], 10), "10초"),
                           (PermissionError(13, "denied", "/home/alice/x"), "PermissionError")):
            with mock.patch.object(caam_usage.subprocess, "run", side_effect=side), self.assertRaises(RuntimeError) as e:
                caam_usage._json("/home/alice/.local/bin/caam", "ls", "codex", "--json", timeout=10)
            self.assertIn(want, str(e.exception))
            self.assertNotIn("alice", str(e.exception))

    def test_output_that_is_not_json_reports_only_the_exit_code(self):
        with mock.patch.object(caam_usage.subprocess, "run", return_value=mock.Mock(stdout="/home/alice 깨진 출력", returncode=3)), \
                self.assertRaises(RuntimeError) as e:
            caam_usage._json("/fake/caam", "limits")
        self.assertIn("종료 코드 3", str(e.exception))
        self.assertNotIn("alice", str(e.exception))

    def test_window_length_names_the_window(self):
        for seconds, want in ((5 * 3600, "session"), (7 * 86400, "weekly"), (30 * 86400, "monthly"), (0, "weekly"), (None, "weekly")):
            self.assertEqual(caam_usage._key(seconds), want, seconds)


if __name__ == "__main__":
    unittest.main()
