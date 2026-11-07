"""cswap 상태를 읽기 전용으로 해석한다."""
import json
import os
import tempfile
import unittest
from unittest import mock

from jarvis import claude_accounts

LIVE_IDENTITY = claude_accounts._live_identity  # setUp 이 가짜로 바꾸기 전의 실제 함수


class CswapCachedTest(unittest.TestCase):
    def setUp(self):  # 실제 홈의 Claude Code 설정을 읽지 않는다
        p = mock.patch.object(claude_accounts, "_live_identity", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_cached_usage_is_identity_guarded_and_does_not_run_cswap(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp):
            os.mkdir(os.path.join(tmp, "cache"))
            def write(name, body):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(body, f)
                os.chmod(p, 0o600)
            write("sequence.json", {"sequence": [1, 2], "accounts": {"1": {"email": "one@example.com", "organizationUuid": "private-org", "alias": "synthetic-secret"}, "2": {"email": "two@example.com", "organizationUuid": "other"}}})
            write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": {"email": "one@example.com", "organizationUuid": "private-org", "fetchedAt": 1900000000, "lastGood": {"five_hour": {"pct": 20}, "seven_day": {"pct": 42}, "scoped": [{"name": "Fable", "pct": 51}, {"name": "synthetic-secret", "pct": 34}]}}, "2": {"email": "old@example.com", "organizationUuid": "other", "lastGood": {"five_hour": {"pct": 99}}}}})
            with mock.patch("subprocess.run", side_effect=AssertionError("조회 명령 실행 금지")):
                rows = claude_accounts._store_read()["accounts"]
            self.assertEqual(rows[0]["label"], "one@example.com")
            self.assertEqual([w["used"] for w in rows[0]["windows"]], [20, 42, 51])
            self.assertEqual(rows[0]["buckets"][0]["used"], 34)
            self.assertEqual(rows[1]["status"], "error")
            self.assertNotIn("synthetic-secret", json.dumps(rows))
            self.assertNotIn("private-org", json.dumps(rows))

    def test_org_name_matches_the_orca_rule(self):
        """covers: USAGE-1. 개인 조직은 None, 팀 조직은 이름: 기기의 모드가 달라도 같은 계정 키가 된다"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp):
            os.mkdir(os.path.join(tmp, "cache"))
            def write(name, body):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(body, f)
                os.chmod(p, 0o600)
            write("sequence.json", {"sequence": [1, 2], "accounts": {
                "1": {"email": "me@example.com", "organizationUuid": "u1", "organizationName": "me@example.com's Organization"},
                "2": {"email": "me@example.com", "organizationUuid": "u2", "organizationName": "Team"}}})
            write("cache/usage.json", {"schemaVersion": 2, "accounts": {
                "1": {"email": "me@example.com", "organizationUuid": "u1", "lastGood": {"five_hour": {"pct": 1}}},
                "2": {"email": "me@example.com", "organizationUuid": "u2", "lastGood": {"five_hour": {"pct": 2}}}}})
            rows = claude_accounts._store_read()["accounts"]
        self.assertEqual([r["org"] for r in rows], [None, "Team"])

    def test_rows_carry_the_slot_and_the_current_account(self):
        """covers: USAGE-3. '이 계정 사용' 버튼에 필요한 슬롯 번호와, 실제 로그인과 같은 계정을 사용 중으로 준다.
        activeAccountNumber 는 계정을 더할 때도 바뀌어 실제 로그인과 어긋날 수 있어 쓰지 않는다(엔진과 같은 규칙)."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp), \
                mock.patch.object(claude_accounts, "_live_identity", return_value=("b@example.com", "")):
            os.mkdir(os.path.join(tmp, "cache"))
            for name, body in (("sequence.json", {"sequence": [4, 6], "activeAccountNumber": 4, "accounts": {
                    "4": {"email": "a@example.com"}, "6": {"email": "b@example.com"}}}),
                    ("cache/usage.json", {"schemaVersion": 2, "accounts": {}})):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(body, f)
                os.chmod(p, 0o600)
            with mock.patch("subprocess.run", side_effect=AssertionError("조회 명령 실행 금지")):
                rows = claude_accounts._store_read()["accounts"]
        self.assertEqual([(r["slot"], r["active"]) for r in rows], [("4", False), ("6", True)])
        self.assertTrue(all(r["note"] is None and "dedupe" not in r for r in rows))

    def test_no_live_login_marks_no_account_in_use(self):
        """covers: USAGE-3. 로그인이 없거나 저장소에 없는 계정이면 기록값을 짐작해 쓰지 않는다"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp):
            p = os.path.join(tmp, "sequence.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"sequence": [1], "activeAccountNumber": 1, "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o"}}}, f)
            os.chmod(p, 0o600)
            for live in (None, ("a@example.com", "other-org"), ("x@example.com", "o")):
                with mock.patch.object(claude_accounts, "_live_identity", return_value=live):
                    self.assertFalse(claude_accounts._store_read()["accounts"][0]["active"])

    def test_live_identity_follows_claude_code_config_rules(self):
        """covers: USAGE-3. CLAUDE_CONFIG_DIR 의 .claude.json, 비어 있지 않은 예전 .config.json 이 있으면 그것"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
            live = LIVE_IDENTITY
            self.assertIsNone(live())  # 설정 파일 없음
            def write(name, body):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    f.write(body if isinstance(body, str) else json.dumps(body))
            write(".claude.json", {"projects": {}})
            self.assertIsNone(live())  # 로그인 없음
            write(".claude.json", {"oauthAccount": {"emailAddress": "a@example.com", "organizationUuid": None}})
            self.assertEqual(live(), ("a@example.com", ""))
            write(".config.json", "")
            self.assertEqual(live(), ("a@example.com", ""))  # 빈 예전 파일은 쓰지 않는다
            write(".config.json", {"oauthAccount": {"emailAddress": "b@example.com", "organizationUuid": "o"}})
            self.assertEqual(live(), ("b@example.com", "o"))
            write(".config.json", "{broken")
            self.assertIsNone(live())

    def test_quarantined_slot_says_why_its_value_is_old(self):
        """covers: USAGE-3. hswap 이 갱신 토큰을 죽었다고 본 슬롯은 남은 값에 재로그인 안내를 붙이고 '조치 필요'로 센다"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp):
            os.mkdir(os.path.join(tmp, "cache"))
            for name, body in (("sequence.json", {"sequence": [1, 2, 3], "accounts": {
                    "1": {"email": "a@example.com"}, "2": {"email": "b@example.com"}, "3": {"email": "c@example.com"}}}),
                    ("cache/usage.json", {"schemaVersion": 2, "accounts": {
                        "1": {"email": "a@example.com", "authDeadStrikes": 1, "lastGood": {"seven_day": {"pct": 100}}},
                        "2": {"email": "b@example.com", "authDeadStrikes": 1},
                        "3": {"email": "c@example.com", "authDeadStrikes": 0, "lastGood": {"seven_day": {"pct": 5}}}}})):
                p = os.path.join(tmp, name)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(body, f)
                os.chmod(p, 0o600)
            rows = claude_accounts._store_read()["accounts"]
        relogin = claude_accounts.STATUS["relogin_required"][0]
        self.assertEqual((rows[0]["status"], rows[0]["note"], rows[0]["attention"]), ("ok", relogin, True))
        self.assertEqual((rows[1]["status"], rows[1]["error"], rows[1]["attention"]), ("error", relogin, True))
        self.assertEqual((rows[2]["note"], rows[2]["attention"]), (None, False))

    def test_missing_cache_does_not_invent_usage(self):
        """covers: USAGE-3"""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(claude_accounts, "_root", return_value=tmp):
            self.assertIsNone(claude_accounts._store_read())
