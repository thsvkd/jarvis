"""Claude 계정 모듈: 사용량 경로의 신선도, 만료 경고, 상태 문구, 기기 로그인 상태. 임시 저장소와 가짜 hswap 응답만 쓴다."""
import json
import os
import subprocess
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from unittest import mock

from jarvis import claude_accounts, independent_usage, util

_GUARDS = []
LIVE_IDENTITY = claude_accounts._live_identity  # StoreCase 가 가짜로 바꾸기 전의 실제 함수


def setUpModule():
    """이 파일의 어떤 경로도 hswap 설치기, 실제 프로세스(codex·hswap 등), 외부 네트워크에 닿지 못한다. 닿으면 호출이 남아 tearDownModule 이 실패한다."""
    for target, name in ((util.Installer, "ensure"), (subprocess, "Popen"), (urllib.request.OpenerDirector, "open")):
        p = mock.patch.object(target, name, side_effect=AssertionError(f"테스트에서 {name} 호출 금지"))
        _GUARDS.append((name, p, p.start()))


def tearDownModule():
    calls = {name: m.call_count for name, _, m in _GUARDS}
    while _GUARDS:
        _GUARDS.pop()[1].stop()
    assert not any(calls.values()), f"설치기·프로세스·네트워크에 닿았다: {calls}"


def iso(at):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def hswap_row(**kw):
    now = time.time()
    return {"number": 1, "email": "a@example.com", "organizationName": "Org", "organizationUuid": "o1", "usageStatus": "ok", "active": True,
            "usage": {"fiveHour": {"pct": 12, "resetsAt": iso(now + 3600)}}, "usageFetchedAt": iso(now - 60), **kw}


class StoreCase(unittest.TestCase):
    """임시 저장소 하나. hswap 은 기본으로 없다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        os.mkdir(os.path.join(self.root, "cache"), 0o700)
        for name, kw in (("_root", {"return_value": self.root}), ("_live_identity", {"return_value": ("a@example.com", "o1")}),
                         ("binary", {"return_value": None}), ("_state", {"new": claude_accounts._State()}),
                         ("ensure", {"side_effect": AssertionError("독립 경로는 설치하지 않는다")})):
            p = mock.patch.object(claude_accounts, name, **kw)
            setattr(self, name.strip("_"), p.start())
            self.addCleanup(p.stop)
        self.root = tmp.name  # 위의 _root 패치가 같은 이름의 속성을 덮었다

    def write(self, name, body):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        os.chmod(path, 0o600)
        return path

    def store(self, fetched_at, **row):
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1", "organizationName": "Org"}}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": {
            "email": "a@example.com", "organizationUuid": "o1", "fetchedAt": fetched_at, "authDeadStrikes": 0,
            "lastGood": {"five_hour": {"pct": 12, "resets_at": iso(time.time() + 3600)},
                         "scoped": [{"name": "fable", "pct": 3}, {"name": "Opus", "pct": 4}, {"name": "private-name", "pct": 5}]}, **row}}})

    def hswap(self, *rows, **top):
        """가짜 hswap: 프로세스를 띄우지 않고 _json 응답만 준다."""
        self.binary.return_value = "/fake/hswap"
        p = mock.patch.object(claude_accounts, "_json", return_value={"accounts": list(rows), **top})
        self.addCleanup(p.stop)
        return p.start()

    def independent_mocks(self):
        """사용량 경로의 Claude 밖 수집기를 비운다."""
        empty = {"status": "unavailable", "accounts": []}
        patches = [mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("없음")),
                   mock.patch.object(independent_usage, "_opencode", return_value=empty),
                   mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]),
                   *[mock.patch.object(m, "read", return_value=empty) for m in (independent_usage.antigravity_usage, independent_usage.kimi_usage,
                                                                               independent_usage.zcode_usage, independent_usage.gemini_usage, independent_usage.minimax_usage)],
                   mock.patch.object(independent_usage.cli_login, "available", return_value=[]),
                   mock.patch.object(independent_usage.cli_login, "fresh", return_value=[])]

        class Together:
            def __enter__(self):
                for p in patches:
                    p.start()

            def __exit__(self, *exc):
                for p in patches:
                    p.stop()
        return Together()

    def full_body(self):
        """화면이 부르는 전체 경로(independent_usage.read)의 응답."""
        with self.independent_mocks():
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            return independent_usage.read(force=True)

    def full_read(self):
        """전체 경로의 Claude 프로바이더."""
        return next(p for p in self.full_body()["providers"] if p["id"] == "claude")

    def keepalive(self, **kw):
        self.write("cache/keepalive.json", {"schemaVersion": 1, "checkedAt": time.time() - 300, "ok": True, "action": "none", "note": None,
                                            "activeSlot": 1, "accessExpiresAt": iso(time.time() + 3 * 3600 + 30),
                                            "loginExpiresAt": iso(time.time() + 30 * 86400), "error": None, **kw})


class FreshnessTest(StoreCase):
    def test_old_cache_without_hswap_says_why_and_how_old(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        a = claude_accounts.read()["accounts"][0]
        self.assertTrue(a["attention"])
        self.assertIn("9시간 전 값", a["note"])
        self.assertIn("hswap 이 없습니다", a["note"])
        self.assertEqual(a["source"], "cswap-cache")
        self.ensure.assert_not_called()

    def test_old_cache_asks_hswap_and_uses_real_values(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        run = self.hswap(hswap_row())
        a = claude_accounts.read()["accounts"][0]
        run.assert_called_once()
        self.assertEqual(run.call_args.args, ("/fake/hswap", "list"))
        self.assertEqual((a["source"], a["note"], a["attention"]), ("cswap", None, False))

    def test_hswap_failure_falls_back_with_the_reason(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        self.hswap().side_effect = RuntimeError("hswap 이 15초 안에 답하지 않았습니다")
        a = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual(a["source"], "cswap-cache")
        self.assertIn("15초 안에 답하지 않았습니다. 저장된 값을 보입니다.", a["note"])
        self.assertTrue(a["attention"])

    def test_force_reaches_hswap_even_when_cache_is_fresh(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        run = self.hswap(hswap_row())
        claude_accounts.read()
        claude_accounts.read(force=True)
        self.assertEqual(run.call_count, 2)

    def test_recent_real_values_are_reused_until_the_threshold(self):
        """covers: USAGE-6. 예약 실행이 저장소를 늘 새로 써도 만료 경고가 실값으로 계속 보인다"""
        self.store(time.time() - 60)
        soon = iso(time.time() + 2 * 86400)
        run = self.hswap(hswap_row(loginExpiresAt=soon))
        for _ in range(3):
            self.assertIn("로그인 만료", claude_accounts.read()["accounts"][0]["note"])
        self.assertEqual(run.call_count, 1)
        claude_accounts._state.cache = (time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1, *claude_accounts._state.cache[1:])
        claude_accounts.read()
        self.assertEqual(run.call_count, 2)

    def test_a_healthy_scheduled_device_gets_no_false_alarm(self):
        """covers: USAGE-6. 대기 계정의 값이 조금 묵었어도 묶어 둔 실값을 그대로 늙히지 않고 hswap 에 다시 묻는다"""
        self.store(time.time() - 60)
        now = time.time()
        run = self.hswap(hswap_row(usageFetchedAt=iso(now - 590)))
        with mock.patch.object(claude_accounts.time, "time", return_value=now):
            claude_accounts.read()
        run.return_value = {"accounts": [hswap_row(usageFetchedAt=iso(now + 300))]}
        held = claude_accounts._state.cache
        claude_accounts._state.cache = (held[0] - 31, *held[1:])  # 30초 묶음이 지났다
        with mock.patch.object(claude_accounts.time, "time", return_value=now + 320):
            a = claude_accounts.read()["accounts"][0]
        self.assertEqual(run.call_count, 2)
        self.assertEqual((a["note"], a["attention"]), (None, False))

    def test_threshold_is_configurable(self):
        """covers: USAGE-6"""
        self.store(time.time() - 3600)
        self.assertTrue(claude_accounts.read()["accounts"][0]["attention"])
        with mock.patch.object(claude_accounts.config, "CLAUDE_STALE_S", 7200):
            self.assertFalse(claude_accounts.read()["accounts"][0]["attention"])

    def test_old_real_values_are_flagged_without_touching_the_held_result(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(usageFetchedAt=iso(time.time() - 2 * 3600)))
        self.assertIn("2시간 전 값", claude_accounts.read()["accounts"][0]["note"])
        self.assertIsNone(claude_accounts._state.cache[1]["accounts"][0]["note"])

    def test_no_store_and_no_force_does_not_run_hswap(self):
        """covers: USAGE-6"""
        run = self.hswap(hswap_row())
        self.assertIsNone(claude_accounts.read())
        run.assert_not_called()
        self.assertEqual(claude_accounts.read(force=True)["accounts"][0]["label"], "a@example.com")

    def test_no_store_and_hswap_error_is_an_error(self):
        """covers: USAGE-6. 대체값이 없으면 독립 경로의 unavailable 이 되도록 오류를 그대로 준다"""
        self.hswap().side_effect = RuntimeError("locked")
        self.assertEqual(claude_accounts.read(force=True), {"error": "locked"})

    def test_page_refresh_reaches_hswap_through_independent_read(self):
        """covers: USAGE-6"""
        self.store(time.time() - 9 * 3600)
        with mock.patch.object(claude_accounts, "live", return_value=None) as live:
            self.assertEqual(independent_usage._claude(True)["accounts"][0]["source"], "cswap-cache")
        live.assert_called_once()
        self.assertEqual(live.call_args.args, (True,))


class SameRulesTest(StoreCase):
    """두 어댑터와 전체 경로가 같은 규칙을 쓴다."""

    def test_window_and_bucket_names(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        cached = claude_accounts._store_read()["accounts"][0]
        real = claude_accounts.account(hswap_row(usage={"scoped": [{"name": "fable", "pct": 3}, {"name": "Opus", "pct": 4}, {"name": "private-name", "pct": 5}]}))
        for a in (cached, real):
            self.assertIn("fable", [w["key"] for w in a["windows"]])
            self.assertEqual([b["name"] for b in a["buckets"]], ["Opus", "모델별 한도 2"])
        self.assertNotIn("private-name", json.dumps([cached, real]))

    def test_org_key_is_the_same_on_both_adapters_and_hides_the_uuid(self):
        """covers: USAGE-1, USAGE-6. 조직 계정은 이름이 달라도 같은 키와 등록 시각을 core 에 주고, 개인 계정과 UUID 원문은 내보내지 않는다"""
        self.write("sequence.json", {"sequence": [1, 2], "accounts": {
            "1": {"email": "a@example.com", "organizationUuid": " O1 ", "organizationName": "케이골프", "added": "2026-11-05T09:34:05Z"},
            "2": {"email": "b@example.com", "organizationUuid": "", "organizationName": "b@example.com's Organization", "added": "2026-11-05T09:34:05Z"}}})
        at = claude_accounts.parse_iso("2026-11-05T09:34:05Z")
        key = claude_accounts._store_read()["orgs"]["1"]["org_key"]
        stored = claude_accounts.read()["accounts"]
        self.assertEqual((stored[0]["org_key"], stored[0]["org_at"]), (key, at))
        self.assertNotIn("org_key", stored[1])
        claude_accounts._state.cache = None
        self.hswap(hswap_row(organizationUuid="o1", organizationName="K-Golf"))
        live = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual((live["org"], live["org_key"], live["org_at"]), ("케이골프", key, at))  # 이름과 시각은 같은 저장소 읽기의 값이다
        self.assertNotIn("o1", [v for a in (*stored, live) for v in a.values()])

    def test_re_registered_name_is_not_paired_with_a_held_old_name(self):
        """covers: USAGE-1. 묶어 둔 hswap 답의 옛 이름이 재등록의 새 시각과 짝지어져 core 에 고정되지 않는다"""
        seq = lambda name, added: {"sequence": [1], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1", "organizationName": name, "added": added}}}
        self.write("sequence.json", seq("케이골프", "2026-11-02T00:00:00Z"))
        call = self.hswap(hswap_row(organizationName="케이골프"))
        self.assertEqual(claude_accounts.read()["accounts"][0]["org"], "케이골프")
        self.write("sequence.json", seq("K-Golf", "2026-11-05T00:00:00Z"))
        row = claude_accounts.read()["accounts"][0]
        self.assertEqual(call.call_count, 1)  # hswap 에 다시 묻지 않고 묶음을 그대로 썼다
        self.assertEqual((row["org"], row["org_at"]), ("K-Golf", claude_accounts.parse_iso("2026-11-05T00:00:00Z")))

    def test_org_key_only_goes_on_a_row_whose_slot_is_the_same_account(self):
        """covers: USAGE-1. 슬롯 번호만 같은 다른 계정의 행은 저장소의 조직 키와 이름을 받지 않는다"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(organizationUuid="other", organizationName="Other"))
        row = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual(row["org"], "Other")
        self.assertNotIn("org_key", row)

    def test_default_named_org_still_sends_its_key(self):
        """covers: USAGE-1. 이름이 기본값이라 화면에 안 보이는 조직도 개명한 기기와 묶이도록 키를 준다"""
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1", "organizationName": "a@example.com's Organization"}}})
        row = claude_accounts.read()["accounts"][0]
        self.assertIsNone(row["org"])
        self.assertIn("org_key", row)

    def test_a_store_without_an_org_name_keeps_the_cli_name(self):
        """covers: USAGE-1. 저장소에 조직 이름이 없으면 hswap 이 준 이름을 지우지 않고, 키는 그대로 준다"""
        for empty in ({"organizationName": ""}, {"organizationName": None}, {}):
            with self.subTest(empty):
                self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1", **empty}}})
                claude_accounts._state.cache = None
                self.hswap(hswap_row(organizationName="Team"))
                row = claude_accounts.read(force=True)["accounts"][0]
                self.assertEqual(row["org"], "Team")
                self.assertIn("org_key", row)

    def test_active_follows_the_real_login_on_both_adapters(self):
        """covers: USAGE-6. hswap 의 active 가 어긋나도 실제 로그인과 같은 슬롯이 사용 중이다. 로그인을 읽지 못하면 hswap 값"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(active=False))
        self.assertTrue(claude_accounts.read()["accounts"][0]["active"])
        self.live_identity.return_value = None
        claude_accounts._state.cache = None
        self.hswap(hswap_row(active=True))
        self.assertTrue(claude_accounts.read()["accounts"][0]["active"])

    def test_personal_org_is_dropped_on_the_real_path_too(self):
        """covers: USAGE-6"""
        self.hswap(hswap_row(organizationName="a@example.com's Organization"))
        self.assertIsNone(claude_accounts.read(force=True)["accounts"][0]["org"])

    def test_active_is_the_same_on_the_full_path(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(active=False))
        self.assertTrue(claude_accounts.read(force=True)["accounts"][0]["active"])
        claude_accounts._state.cache = None
        self.assertTrue(self.full_read()["accounts"][0]["active"])

    def test_bucket_name_with_a_version_keeps_its_name(self):
        """covers: USAGE-6"""
        a = claude_accounts.account(hswap_row(usage={"scoped": [{"name": "Sonnet 4.6", "pct": 1}, {"name": "sonnetish", "pct": 2}]}))
        self.assertEqual([b["name"] for b in a["buckets"]], ["Sonnet 4.6", "모델별 한도 2"])

    def test_same_old_value_gets_the_same_flag_on_the_full_path(self):
        """covers: USAGE-6"""
        self.store(time.time() - 60)
        self.hswap(hswap_row(usageFetchedAt=iso(time.time() - 2 * 3600)))
        mine = claude_accounts.read(force=True)["accounts"][0]
        claude_accounts._state.cache = None
        theirs = self.full_read()["accounts"][0]
        self.assertTrue(mine["attention"])
        self.assertIn("2시간 전 값", mine["note"])
        self.assertEqual((mine["note"], mine["attention"]), (theirs["note"], theirs["attention"]))

    def test_expiry_and_status_text_match_on_the_full_path(self):
        """covers: USAGE-6"""
        soon = iso(time.time() + 2 * 86400)
        for row in (hswap_row(loginExpiresAt=soon),
                    hswap_row(usageStatus="relogin_required", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60)),
                    hswap_row(usageStatus="keychain_unavailable", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60)),
                    hswap_row(usageStatus="foreign_credential", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60))):
            self.hswap(row)
            claude_accounts._state.cache = None
            mine = claude_accounts.read(force=True)["accounts"][0]
            claude_accounts._state.cache = None
            theirs = self.full_read()["accounts"][0]
            self.assertTrue(mine["note"])
            self.assertEqual((mine["note"], mine["attention"]), (theirs["note"], theirs["attention"]))

    def test_login_state_is_on_the_full_path(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "refresh_failed", "message": "network"})
        self.hswap(hswap_row())
        self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "failed")
        claude_accounts._state.cache = None
        self.assertEqual(self.full_read()["login"]["state"], "failed")


class LoginStateTest(StoreCase):
    def test_ok(self):
        """covers: USAGE-7"""
        self.keepalive()
        s = claude_accounts.login([{"active": True, "slot": "1"}])
        self.assertEqual((s["state"], s["attention"], s["slot"]), ("ok", False, "1"))
        self.assertEqual(s["text"], "로그인됨, 자동 갱신 5분 전")
        self.assertEqual(s["note"], "접근 토큰 3시간 뒤 만료(자동 갱신)")

    def test_expiring(self):
        """covers: USAGE-7"""
        self.keepalive(loginExpiresAt=iso(time.time() + 2 * 86400 + 30))
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["text"]), ("expiring", True, "로그인 만료 임박(2일)"))

    def test_relogin_from_keepalive_and_from_the_active_row(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "relogin_required", "message": "m"})
        self.assertEqual(claude_accounts.login()["state"], "relogin")
        os.remove(os.path.join(self.root, "cache", "keepalive.json"))
        # 저장소의 authDeadStrikes 는 사용 중 계정일 때만 기기 상태가 된다
        self.store(time.time() - 60, authDeadStrikes=1)
        rows = claude_accounts._store_read()["accounts"]
        self.assertEqual(claude_accounts.login(rows)["state"], "relogin")
        self.live_identity.return_value = ("other@example.com", "")
        self.assertIsNone(claude_accounts.login(claude_accounts._store_read()["accounts"]))

    def test_keychain_from_keepalive_and_from_hswap(self):
        """covers: USAGE-7"""
        self.keepalive(ok=False, error={"type": "keychain_unavailable", "message": "m"})
        self.assertEqual(claude_accounts.login()["state"], "keychain")
        os.remove(os.path.join(self.root, "cache", "keepalive.json"))
        self.hswap(hswap_row(), keychainUnavailable=True)
        s = claude_accounts.read(force=True)["login"]
        self.assertEqual((s["state"], s["attention"]), ("keychain", True))
        self.assertIn("키체인에 접근할 수 없는 곳에서 실행됨", s["text"])
        self.assertEqual(claude_accounts.read()["login"]["state"], "keychain")  # 보통 읽기에서도 남는다

    def test_keychain_field_is_ignored_when_absent(self):
        """covers: USAGE-7"""
        self.hswap(hswap_row())
        out = claude_accounts.read(force=True)
        self.assertNotIn("login", out)
        self.assertNotIn("login", claude_accounts.read())

    def test_stalled(self):
        """covers: USAGE-7"""
        self.keepalive(checkedAt=time.time() - 2 * 3600)
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["text"]), ("stalled", True, "자동 갱신 멈춤(마지막 2시간 전)"))
        with mock.patch.object(claude_accounts.config, "CLAUDE_KEEPALIVE_STALE_S", 3 * 3600):
            self.assertEqual(claude_accounts.login()["state"], "ok")
        self.keepalive(checkedAt="soon")
        self.assertEqual(claude_accounts.login()["state"], "stalled")

    def test_failed_shows_only_the_fixed_text_for_the_type(self):
        """covers: USAGE-7. 파일의 자유 문구는 내보내지 않고, 허용 목록의 type 만 고정 문구로 바꾼다. 나머지는 알 수 없는 오류다"""
        for kind, want in (("split_brain", "키체인과 파일의 로그인이 갈라짐"), ("refresh_failed", "토큰 갱신 실패"), ("no_credentials", "자격 증명 없음"),
                           ("locked", "다른 프로세스가 잠금을 쥐고 있음"), ("unmanaged", "저장 슬롯에 없는 로그인"),
                           ("something_new", "알 수 없는 오류"), (["list"], "알 수 없는 오류"), (None, "알 수 없는 오류")):
            self.keepalive(ok=False, error={"type": kind, "message": "x" * 500})
            s = claude_accounts.login()
            self.assertEqual((s["state"], s["attention"], s["text"]), ("failed", True, f"자동 갱신 실패({want})"), kind)
        self.keepalive(ok=False, error=None)
        self.assertEqual(claude_accounts.login()["text"], "자동 갱신 실패(알 수 없는 오류)")

    def test_every_allowed_type_has_a_fixed_text(self):
        """covers: USAGE-7"""
        self.assertEqual(set(claude_accounts.KEEPALIVE_ERRORS),
                         {"keychain_unavailable", "relogin_required", "split_brain", "refresh_failed", "no_credentials", "locked", "unmanaged",
                          "keychain_item_present", "config_conflict"})

    def test_ok_shows_the_file_store(self):
        """covers: USAGE-7"""
        self.keepalive(store="file", keychainItemPresent=False)
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"], s["store"]), ("ok", False, "file"))
        self.assertTrue(s["text"].startswith("로그인됨 · 파일 저장소, 자동 갱신 "), s["text"])

    def test_old_record_and_other_stores_read_as_before(self):
        """covers: USAGE-7. store 가 없는 옛 형식과 auto·keychain 은 키체인 경고를 그대로 보이고 저장소를 표시하지 않는다"""
        for extra in ({}, {"store": "auto"}, {"store": "keychain"}, {"store": ["file"]}):
            self.keepalive(**extra)
            s = claude_accounts.login()
            self.assertEqual((s["state"], s["store"]), ("ok", None), extra)
            self.assertTrue(s["text"].startswith("로그인됨, 자동 갱신 "), extra)
            self.assertEqual(claude_accounts.login(keychain=True)["state"], "keychain", extra)
            self.keepalive(**extra, ok=False, error={"type": "keychain_unavailable"})
            self.assertEqual(claude_accounts.login()["state"], "keychain", extra)

    def test_file_store_ignores_an_unreachable_keychain(self):
        """covers: USAGE-7. hswap list 의 keychainUnavailable 과 keepalive 의 같은 오류 모두 파일 저장소에서는 주의가 아니다"""
        self.keepalive(store="file", keychainItemPresent=False)
        self.store(time.time() - 60)
        self.hswap(hswap_row(), keychainUnavailable=True)
        out = claude_accounts.read(force=True)
        self.assertEqual((out["login"]["state"], out["login"]["attention"]), ("ok", False))
        self.assertNotIn("키체인", out["login"]["text"])
        # 다른 실패는 파일 저장소에서도 그대로 실패다
        self.keepalive(store="file", ok=False, error={"type": "refresh_failed"})
        self.assertEqual(claude_accounts.login()["text"], "자동 갱신 실패(토큰 갱신 실패)")

    def test_file_store_failure_never_reads_as_success(self):
        """covers: USAGE-7. 파일 저장소의 keychain_unavailable 실패는 경고가 아니지만 성공도 아니다. 기록이 새로워도 계속되면 attention 으로 올린다"""
        self.keepalive(store="file", ok=False, error={"type": "keychain_unavailable", "message": "m"})
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["text"], s["attention"], s["note"], s["store"]), ("unconfirmed", "자동 갱신 확인되지 않음", False, None, "file"))
        self.assertEqual(claude_accounts.login(keychain=True)["state"], "unconfirmed")
        # checkedAt 이 새로워도 시한은 처음 본 때부터 잰다
        claude_accounts._state.unconfirmed = time.time() - claude_accounts.config.CLAUDE_KEEPALIVE_STALE_S - 60
        self.keepalive(store="file", ok=False, error={"type": "keychain_unavailable"}, checkedAt=time.time())
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"]), ("unconfirmed", True))
        self.assertNotIn("로그인됨", s["text"])
        self.assertIn("자동 갱신 확인되지 않음", s["text"])
        # ok:true 가 오면 끝나고, 다시 실패하면 처음부터 잰다
        self.keepalive(store="file")
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["attention"]), ("ok", False))
        self.assertIn("로그인됨 · 파일 저장소", s["text"])
        self.assertEqual(claude_accounts.read()["login"]["state"], "ok")
        self.keepalive(store="file", ok=False, error={"type": "keychain_unavailable"})
        self.assertFalse(claude_accounts.login()["attention"])
        # 그 밖의 ok:false 와 ok 값이 없는 기록은 어떤 경우에도 성공 문구가 아니다
        for extra in ({"ok": False, "error": {"type": "refresh_failed"}}, {"ok": False, "error": {"type": "new_kind"}}, {"ok": False, "error": None}, {"ok": "true"}):
            self.keepalive(store="file", **extra)
            s = claude_accounts.login()
            self.assertEqual((s["state"], s["attention"]), ("failed", True), extra)
            self.assertNotIn("로그인됨", s["text"], extra)

    def reply(self, *rows, error=None):
        """가짜 hswap 프로세스: 실제 _json 을 거친다."""
        self.binary.return_value = "/fake/hswap"
        body = {"schemaVersion": 1, "error": error} if error is not None else {"schemaVersion": 1, "accounts": list(rows)}
        p = mock.patch.object(claude_accounts.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(body), returncode=1 if error is not None else 0))
        p.start()
        self.addCleanup(p.stop)

    def test_top_level_hswap_error_shows_only_fixed_text(self):
        """covers: USAGE-6. error.message 원문은 어느 응답에도 나오지 않는다. 표에 없는 type 은 소문자·밑줄 40자까지만 보인다"""
        secrets = ("sk-ant-oat01-SECRET", "Bearer abc.def", "/Users/alice/.claude", "C:\\Users\\alice\\x", "/opt/private/token.json")
        leak = " ".join(secrets)
        for error, want in (({"type": "config_conflict", "message": leak}, "hswap 설정 충돌"), ({"type": "LockError", "message": leak}, "잠금을 쥐고 있음"),
                            ({"type": "brand_new_error", "message": leak}, "hswap 오류(알 수 없는 종류)"),
                            ({"type": leak, "message": leak}, "hswap 오류(알 수 없는 종류)"), ({"type": "A" * 10, "message": leak}, "hswap 오류(알 수 없는 종류)"),
                            ({"type": "a" * 41, "message": leak}, "hswap 오류(알 수 없는 종류)"), ({"type": ["list"], "message": leak}, "hswap 오류(알 수 없는 종류)"),
                            ({"message": leak}, "hswap 오류(알 수 없는 종류)"), (leak, "hswap 오류(알 수 없는 종류)")):
            self.reply(error=error)
            for stored in (False, True):
                if stored:
                    self.store(time.time() - 60)
                claude_accounts._state.cache = None
                body = claude_accounts.read(force=True)
                claude_accounts._state.cache = None
                text = json.dumps([body, self.full_body()], ensure_ascii=False)
                self.assertIn(want, text, error)
                for s in (*secrets, "alice", "SECRET", "brand_new_error", "aaaaa"):
                    self.assertNotIn(s, text, error)
            code, answer = claude_accounts.switch("1")
            self.assertEqual(code, 409)
            self.assertIn(want, answer["error"])
            self.assertNotIn("SECRET", answer["error"])
            os.remove(os.path.join(self.root, "sequence.json"))

    def test_unknown_hswap_type_goes_only_to_the_log_filtered_and_cut(self):
        """covers: USAGE-6. 표에 있는 type 은 로그에 남기지 않는다"""
        for kind, want in (("brand_new_error", "brand_new_error"), ("Sk-1/ab_c", "kab_c"), ("a" * 41, "a" * 40), (["list"], ""), (None, "")):
            with mock.patch.object(claude_accounts, "print", create=True) as log:
                self.assertEqual(str(claude_accounts._HswapError(kind)), "hswap 오류(알 수 없는 종류)")
            self.assertEqual(log.call_args[0][0], f"[jarvis] hswap 의 모르는 오류 종류: {want}", kind)
        with mock.patch.object(claude_accounts, "print", create=True) as log:
            claude_accounts._HswapError("config_conflict")
        log.assert_not_called()

    def test_fatal_hswap_errors_reach_the_login_state_over_an_old_record(self):
        """covers: USAGE-7. keepalive 기록이 정상이어도 치명적 type 은 바로 attention 이다. hswap 이 다시 답하면 사라진다"""
        self.store(time.time() - 60)
        for kind, want in (("config_conflict", "config_conflict"), ("keychain_item_present", "keychain_item_present"), ("relogin_required", "relogin")):
            self.keepalive()
            self.reply(error={"type": kind, "message": "m"})
            s = claude_accounts.read(force=True)["login"]
            self.assertEqual((s["state"], s["attention"]), (want, True), kind)
            self.assertEqual(claude_accounts.read()["login"]["state"], want, kind)  # 보통 읽기에서도 남는다
            self.reply(hswap_row())
            s = claude_accounts.read(force=True)["login"]
            self.assertEqual((s["state"], s["attention"]), ("ok", False), kind)
        # 파일이 없어도 보인다. 답이 없는 실패는 판정을 지우지 않고, 치명적이지 않은 답은 지운다
        os.remove(os.path.join(self.root, "cache", "keepalive.json"))
        self.reply(error={"type": "config_conflict"})
        self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "config_conflict")
        with mock.patch.object(claude_accounts.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "config_conflict")
        self.reply(error={"type": "LockError"})
        self.assertNotIn("login", claude_accounts.read(force=True))
        # 다시 확인하지 못한 옛 답은 쓰지 않는다
        claude_accounts._state.fatal = (time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1, "config_conflict")
        self.assertNotIn("login", claude_accounts.read())

    def test_fatal_error_without_a_store_is_asked_again(self):
        """covers: USAGE-7. 저장소도 계정도 없는 기기에서도 보통 읽기로 사라지지 않고, 고쳐지면 다시 물어 지운다"""
        self.reply(error={"type": "config_conflict"})
        self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "config_conflict")
        self.assertEqual(claude_accounts.read()["login"]["state"], "config_conflict")
        self.reply()
        claude_accounts._state.cache = None
        self.assertIsNone(claude_accounts.read())
        self.assertIsNone(claude_accounts.read())

    def test_file_store_stops_are_reported_with_fixed_text(self):
        """covers: USAGE-7. 이전 계정의 기록이어도, 만료 임박이어도, 사용 중 계정이 재로그인 필요여도 기기의 사실이 먼저다"""
        relogin = [{"active": True, "slot": "2", "note": claude_accounts.STATUS["relogin_required"][0]}]
        for kind, want in (("keychain_item_present", "키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다"),
                           ("config_conflict", "hswap 설정 충돌(파일 저장소와 키체인 필수 모드)")):
            self.keepalive(store="file", ok=False, error={"type": kind, "message": "x" * 500}, loginExpiresAt=iso(time.time() + 3600))
            for rows in ((), relogin):
                s = claude_accounts.login(rows, signed_in=True)
                self.assertEqual((s["state"], s["text"], s["attention"], s["store"], s["note"]), (kind, want, True, "file", None), kind)
            self.assertEqual(claude_accounts.STATUS[kind], (want + ".", True))
            self.keepalive(ok=False, error={"type": kind})  # store 가 없는 기록에서도 같은 오류다
            self.assertEqual((claude_accounts.login()["state"], claude_accounts.login()["store"]), (kind, None))
        # 오류 없이 항목만 보고돼도 파일 저장소면 멈춤으로 보인다. 다른 저장소의 값은 뜻이 없다
        self.keepalive(store="file", keychainItemPresent=True)
        self.assertEqual(claude_accounts.login()["state"], "keychain_item_present")
        self.keepalive(store="auto", keychainItemPresent=True)
        self.assertEqual(claude_accounts.login()["state"], "ok")
        self.keepalive(store="file", keychainItemPresent="true")
        self.assertEqual(claude_accounts.login()["state"], "ok")

    def test_keychain_item_status_on_a_row_needs_a_person(self):
        """covers: USAGE-7"""
        for kind in ("keychain_item_present", "config_conflict"):
            a = claude_accounts.account(hswap_row(usageStatus=kind, usage=None))
            self.assertEqual((a["status"], a["error"], a["attention"]), ("error", claude_accounts.STATUS[kind][0], True))

    def test_unknown_when_the_file_is_missing(self):
        """covers: USAGE-7. keepalive 를 쓰지 않는 기기에는 경고하지 않는다"""
        self.assertIsNone(claude_accounts.login())

    def test_unsafe_or_foreign_files_are_ignored(self):
        """covers: USAGE-7. opencode_usage._private_json 과 같은 안전 규칙"""
        path = os.path.join(self.root, "cache", "keepalive.json")
        self.keepalive(ok=False, error={"type": "refresh_failed"})
        os.chmod(path, 0o644)
        self.assertIsNone(claude_accounts.login())
        os.remove(path)
        self.write("real.json", {"schemaVersion": 1, "ok": False})
        os.symlink(os.path.join(self.root, "real.json"), path)
        self.assertIsNone(claude_accounts.login())
        os.remove(path)
        self.write("cache/keepalive.json", json.dumps({"schemaVersion": 1, "pad": "x" * 70000}))
        self.assertIsNone(claude_accounts.login())
        for body in ("{broken", '["x"]', '{"schemaVersion": 2, "ok": false}'):
            self.write("cache/keepalive.json", body)
            self.assertIsNone(claude_accounts.login())

    def test_odd_values_never_break_the_response(self):
        """covers: USAGE-7. 선택 기능의 상태 파일 하나가 사용량 표시를 막지 않는다"""
        huge = "1" + "0" * 400
        for field in ("checkedAt", "loginExpiresAt", "accessExpiresAt"):
            self.write("cache/keepalive.json", json.dumps({"schemaVersion": 1, "ok": True, "checkedAt": time.time()})[:-1] + f', "{field}": {huge}}}')
            json.dumps(claude_accounts.login())
        self.write("cache/keepalive.json", '{"schemaVersion": 1, "x": ' + "[" * 30000 + "]" * 30000 + "}")
        self.assertIsNone(claude_accounts.login())
        self.keepalive(activeSlot=int(huge), checkedAt=-1e308, accessExpiresAt=1e308)
        s = claude_accounts.login()
        self.assertEqual((s["state"], s["slot"], s["note"]), ("stalled", None, None))
        self.assertEqual(s["text"], "자동 갱신 멈춤")

    def test_file_text_never_reaches_any_response(self):
        """covers: USAGE-7. 비밀처럼 보이는 문자열을 파일 곳곳과 hswap 의 usageError 에 넣어도 두 API 응답 어디에도 나오지 않는다"""
        secrets = ("123e4567-e89b-12d3-a456-426614174000", "Authorization: Bearer abc.def", "sk-ant-oat01-SECRET", "/home/alice/.claude", "C:\\Users\\alice\\x", "\ud800")
        leak = " ".join(secrets)
        self.store(time.time() - 60)
        kinds = ("split_brain", "refresh_failed", "no_credentials", "locked", "unmanaged", "relogin_required", "keychain_unavailable", "keychain_item_present", "config_conflict", leak)
        for kind in kinds:
            for slot in (1, 2):  # 지금 로그인의 기록과 이전 계정의 기록 모두
                self.write("cache/keepalive.json", json.dumps({"schemaVersion": 1, "checkedAt": time.time(), "ok": False, "action": leak, "note": leak, "store": leak, "activeSlot": slot,
                                                               "error": {"type": kind, "message": leak, "detail": leak}, "extra": leak}))
                self.hswap(hswap_row(usageStatus="unavailable", usage=None, usageError=leak, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60)))
                claude_accounts._state.cache = None
                mine = claude_accounts.read(force=True)
                claude_accounts._state.cache = None
                theirs = self.full_body()
                for body in (mine, theirs):
                    text = json.dumps(body, ensure_ascii=False)
                    text.encode("utf-8")  # server.py 의 응답 인코딩이 깨지지 않는다
                    for s in (*secrets[:-1], "alice", "SECRET", "Bearer"):
                        self.assertNotIn(s, text, (kind, slot))
                self.assertIn("사용량을 읽지 못했습니다.", mine["accounts"][0]["note"])

    def test_usage_error_only_passes_as_an_http_code(self):
        """covers: USAGE-3, USAGE-7. hswap 의 usageError 는 http-NNN 꼴만 받는다"""
        row = lambda err: hswap_row(usageStatus="unavailable", usage=None, usageError=err, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60))
        self.assertIn("(http-429)", claude_accounts.account(row("http-429"))["note"])
        for bad in ("http-429 Bearer abc", "Bearer abc", "http-999", "http-42", "\nhttp-429", ["http-429"], 429):
            self.assertEqual(claude_accounts.account(row(bad))["note"], "사용량을 읽지 못했습니다.", bad)

    def test_record_of_another_account_says_only_that(self):
        """covers: USAGE-7. 전환 뒤에는 이전 슬롯의 만료·오류·접근 토큰을 붙이지 않고, 옛 슬롯을 덮어쓰게 안내하지 않는다"""
        soon = iso(time.time() + 86400)
        for kw in ({}, {"loginExpiresAt": soon}, {"ok": False, "error": {"type": "relogin_required"}},
                   {"ok": False, "error": {"type": "refresh_failed", "message": "old slot"}}):
            self.keepalive(activeSlot=2, **kw)
            s = claude_accounts.login([{"active": True, "slot": "1"}])
            self.assertEqual((s["state"], s["attention"], s["slot"], s["note"], s["other"]), ("other_account", True, None, None, True), kw)
            self.assertEqual(s["text"], "자동 갱신 기록이 이전 계정의 것입니다")

    def unknown_slot_records(self, **kw):
        """activeSlot 이 없거나 슬롯 번호로 쓸 수 없는 기록들."""
        base = {"schemaVersion": 1, "checkedAt": time.time() - 60, **kw}
        yield "빠짐", base
        for bad in (None, "1", True, 0, -1, 1.0):
            yield repr(bad), {**base, "activeSlot": bad}

    def test_missing_credential_with_identity_left_keeps_its_failure(self):
        """covers: USAGE-7. 자격은 사라졌는데 .claude.json 에 신원이 남은 경우: 슬롯을 모르는 것이지 다른 계정이라는 증거가 아니다"""
        self.store(time.time() - 60)
        self.hswap(hswap_row())
        for name, record in self.unknown_slot_records(ok=False, error={"type": "no_credentials", "message": "m"}):
            self.write("cache/keepalive.json", record)
            for got in (claude_accounts.login([{"active": True, "slot": "1"}], signed_in=True), claude_accounts.read()["login"]):
                self.assertEqual((got["state"], got["text"], got["attention"], got["slot"], got["other"]),
                                 ("failed", "자동 갱신 실패(자격 증명 없음)", True, None, False), name)

    def test_unknown_slot_keeps_every_state_and_only_drops_the_slot(self):
        """covers: USAGE-7"""
        now = time.time()
        for kw, want in (({"ok": False, "error": {"type": "relogin_required"}}, "relogin"),
                         ({"ok": False, "error": {"type": "refresh_failed"}}, "failed"),
                         ({"ok": False, "error": {"type": "keychain_unavailable"}}, "keychain"),
                         ({"ok": True, "loginExpiresAt": iso(now + 86400)}, "expiring"),
                         ({"ok": True, "checkedAt": now - 9 * 3600}, "stalled"),
                         ({"ok": True}, "ok")):
            for name, record in self.unknown_slot_records(**kw):
                self.write("cache/keepalive.json", record)
                s = claude_accounts.login([{"active": True, "slot": "1"}], signed_in=True)
                self.assertEqual((s["state"], s["slot"], s["other"]), (want, None, False), (want, name))

    def test_only_a_differing_slot_number_means_another_account(self):
        """covers: USAGE-7"""
        self.keepalive(activeSlot=2, ok=False, error={"type": "refresh_failed"})
        self.assertEqual(claude_accounts.login([{"active": True, "slot": "1"}], signed_in=True)["state"], "other_account")
        self.keepalive(activeSlot=1, ok=False, error={"type": "refresh_failed"})
        s = claude_accounts.login([{"active": True, "slot": "1"}], signed_in=True)
        self.assertEqual((s["state"], s["slot"], s["other"]), ("failed", "1", False))

    def test_missing_credential_is_never_hidden_as_another_accounts_record(self):
        """covers: USAGE-7. 자격 부재는 add 만으로 고칠 수 없다: 슬롯이 달라도 /login 안내로 이어질 상태를 지킨다"""
        self.keepalive(activeSlot=2, ok=False, error={"type": "no_credentials", "message": "m"})
        s = claude_accounts.login([{"active": True, "slot": "1"}], signed_in=True)
        self.assertEqual((s["state"], s["text"], s["slot"], s["other"]), ("failed", "자동 갱신 실패(자격 증명 없음)", None, True))

    def test_row_slot_is_kept_when_there_is_no_record_at_all(self):
        """covers: USAGE-7. 파일이 없으면 슬롯을 흐리게 할 기록도 없다: 재로그인 판정의 사용 중 슬롯을 그대로 준다"""
        relogin = claude_accounts.STATUS["relogin_required"][0]
        s = claude_accounts.login([{"active": True, "slot": "3", "note": relogin}], signed_in=True)
        self.assertEqual((s["state"], s["slot"], s["other"]), ("relogin", "3", False))

    def test_device_failures_stay_visible_over_another_accounts_record(self):
        """covers: USAGE-7. 키체인·멈춤은 계정이 아니라 기기의 문제다. 슬롯은 여전히 빼고 새 슬롯 안내를 쓴다"""
        for kw, want in (({"ok": False, "error": {"type": "keychain_unavailable"}}, "keychain"), ({"checkedAt": time.time() - 9 * 3600}, "stalled")):
            self.keepalive(activeSlot=2, **kw)
            s = claude_accounts.login([{"active": True, "slot": "1"}])
            self.assertEqual((s["state"], s["slot"], s["other"]), (want, None, True))

    def test_login_saved_in_no_slot_never_gets_the_old_slot(self):
        """covers: USAGE-7. 현재 로그인이 저장 계정과 맞지 않으면 사용 중 슬롯이 없다. hswap 의 active 도 믿지 않는다"""
        self.store(time.time() - 60)
        self.live_identity.return_value = ("other@example.com", "")
        self.keepalive(loginExpiresAt=iso(time.time() + 86400))
        self.hswap(hswap_row(active=True))
        out = claude_accounts.read(force=True)
        self.assertFalse(out["accounts"][0]["active"])
        self.assertEqual((out["login"]["state"], out["login"]["slot"]), ("other_account", None))

    def test_unmanaged_login_keeps_its_own_record(self):
        """covers: USAGE-7. 저장 슬롯이 없는 로그인을 keepalive 도 슬롯 없이 기록했으면 그 기록은 지금 로그인의 것이다"""
        self.store(time.time() - 60)
        self.live_identity.return_value = ("other@example.com", "")
        self.keepalive(activeSlot=None, loginExpiresAt=iso(time.time() + 86400))
        s = claude_accounts.read()["login"]
        self.assertEqual((s["state"], s["slot"], s["other"]), ("expiring", None, False))

    def test_matching_record_keeps_slot_and_details(self):
        """covers: USAGE-7"""
        self.store(time.time() - 60)
        self.keepalive(activeSlot=1, ok=False, error={"type": "refresh_failed", "message": "m"})
        s = claude_accounts.read()["login"]
        self.assertEqual((s["state"], s["slot"], s["other"], s["text"]), ("failed", "1", False, "자동 갱신 실패(토큰 갱신 실패)"))

    def test_unknown_login_keeps_the_warning_without_a_slot(self):
        """covers: USAGE-7. 신원을 읽지 못하면(로그아웃 등) 맞는지 모른다: 경고는 남기고 슬롯은 권하지 않는다"""
        self.store(time.time() - 60)
        self.live_identity.return_value = None
        self.keepalive(activeSlot=1, ok=False, error={"type": "relogin_required"})
        s = claude_accounts.read()["login"]
        self.assertEqual((s["state"], s["slot"], s["other"]), ("relogin", None, False))

    def test_current_rows_relogin_is_not_hidden_by_another_record(self):
        """covers: USAGE-7. 재로그인 판정이 행에서 왔으면 지금 계정의 사실이다"""
        self.keepalive(activeSlot=2)
        relogin = claude_accounts.STATUS["relogin_required"][0]
        self.assertEqual(claude_accounts.login([{"active": True, "slot": "1", "note": relogin}])["state"], "relogin")

    def test_order_when_several_apply(self):
        """covers: USAGE-7"""
        soon = iso(time.time() + 86400 + 30)
        old = time.time() - 9 * 3600
        for kw, want in (({"loginExpiresAt": soon, "checkedAt": old}, "expiring"),
                         ({"loginExpiresAt": soon, "ok": False, "error": {"type": "keychain_unavailable"}}, "keychain"),
                         ({"loginExpiresAt": soon, "ok": False, "error": {"type": "relogin_required"}}, "relogin"),
                         ({"checkedAt": old, "ok": False, "error": {"type": "locked"}}, "stalled")):
            self.keepalive(**kw)
            self.assertEqual(claude_accounts.login()["state"], want, kw)

    def test_access_expiry(self):
        """covers: USAGE-7"""
        self.keepalive(accessExpiresAt=iso(time.time() - 60))
        self.assertEqual(claude_accounts.login()["note"], "접근 토큰 만료됨(자동 갱신 대기)")
        self.keepalive(accessExpiresAt=None)
        self.assertIsNone(claude_accounts.login()["note"])

    def test_provider_carries_login_only_when_there_is_something_to_say(self):
        """covers: USAGE-7"""
        self.store(time.time() - 60)
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("없음")), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.cli_login, "available", return_value=[]), \
                mock.patch.object(independent_usage.cli_login, "fresh", return_value=[]):
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            claude = next(p for p in independent_usage.read(force=True)["providers"] if p["id"] == "claude")
            self.assertNotIn("login", claude)
            self.keepalive()
            claude = next(p for p in independent_usage.read(force=True)["providers"] if p["id"] == "claude")
        self.assertEqual(claude["login"]["state"], "ok")
        self.assertNotIn("error", claude["login"])


class NoAccountsTest(StoreCase):
    """기기 로그인 상태는 계정 목록과 상관없이 보인다."""

    def test_login_survives_without_any_account(self):
        """covers: USAGE-7"""
        for kind, want in (("no_credentials", "failed"), ("relogin_required", "relogin")):
            self.keepalive(ok=False, activeSlot=None, error={"type": kind, "message": "m"})
            for force in (False, True):
                out = claude_accounts.read(force=force)
                self.assertEqual((out["accounts"], out["login"]["state"], out["login"]["attention"]), ([], want, True), (kind, force))
                self.assertNotIn("error", out)

    def test_hswap_error_and_login_are_both_kept(self):
        """covers: USAGE-7"""
        self.hswap().side_effect = RuntimeError("locked")
        self.keepalive(ok=False, error={"type": "relogin_required"})
        out = claude_accounts.read(force=True)
        self.assertEqual((out["accounts"], out["error"], out["login"]["state"]), ([], "locked", "relogin"))

    def test_no_accounts_with_unreachable_keychain_is_reported(self):
        """covers: USAGE-7"""
        self.hswap(keychainUnavailable=True)
        out = claude_accounts.read(force=True)
        self.assertEqual((out["accounts"], out["login"]["state"]), ([], "keychain"))

    def test_keychain_warning_survives_ordinary_reads_after_a_refresh(self):
        """covers: USAGE-7. 저장소도 keepalive 도 없는 기기: 새로고침 뒤의 보통 읽기에서 경고와 복구 버튼이 사라지지 않고, 그 사이 hswap 을 다시 부르지 않는다"""
        run = self.hswap(keychainUnavailable=True)
        self.assertEqual(claude_accounts.read(force=True)["login"]["state"], "keychain")
        for _ in range(3):
            out = claude_accounts.read()
            self.assertEqual((out["accounts"], out["login"]["state"], out["login"]["attention"]), ([], "keychain", True))
        run.assert_called_once()

    def test_keychain_state_is_asked_again_after_the_threshold(self):
        """covers: USAGE-7"""
        run = self.hswap(keychainUnavailable=True)
        claude_accounts.read(force=True)
        def age():
            ago = time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1
            claude_accounts._state.device, claude_accounts._state.cache = (ago, claude_accounts._state.device[1]), (ago, *claude_accounts._state.cache[1:])
        age()
        self.assertEqual(claude_accounts.read()["login"]["state"], "keychain")  # 아직 그렇다고 답한다
        self.assertEqual(run.call_count, 2)
        age()
        run.return_value = {"accounts": []}  # 이제 닿는다: 경고가 사라지고 더는 묻지 않는다
        self.assertIsNone(claude_accounts.read())
        self.assertEqual(run.call_count, 3)
        claude_accounts._state.cache = None
        self.assertIsNone(claude_accounts.read())
        self.assertEqual(run.call_count, 3)

    def test_keychain_state_is_not_kept_past_the_threshold_when_hswap_fails(self):
        """covers: USAGE-7. 다시 확인하지 못한 상태를 지금 사실처럼 보이지 않고 hswap 오류를 보인다"""
        run = self.hswap(keychainUnavailable=True)
        claude_accounts.read(force=True)
        ago = time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1
        claude_accounts._state.device, claude_accounts._state.cache = (ago, True), (ago, None, None)
        run.side_effect = RuntimeError("locked")
        self.assertEqual(claude_accounts.read(), {"error": "locked"})

    def test_hswap_failure_inside_the_threshold_keeps_the_device_state(self):
        """covers: USAGE-7. 기기 상태는 계정 행과 따로 둔다: 실패한 조회가 지우지 않는다"""
        run = self.hswap(keychainUnavailable=True)
        claude_accounts.read(force=True)
        run.side_effect = RuntimeError("locked")
        out = claude_accounts.read(force=True)
        self.assertEqual((out["accounts"], out["error"], out["login"]["state"]), ([], "locked", "keychain"))
        self.assertEqual(claude_accounts.read()["login"]["state"], "keychain")

    def test_keychain_warning_holds_over_held_rows_and_store_rows(self):
        """covers: USAGE-7"""
        run = self.hswap(hswap_row(), keychainUnavailable=True)
        claude_accounts.read(force=True)
        self.assertEqual(claude_accounts.read()["login"]["state"], "keychain")
        self.store(time.time() - 60)
        run.return_value = {"accounts": [], "keychainUnavailable": True}
        claude_accounts._state.cache = None
        out = claude_accounts.read()
        self.assertEqual((out["accounts"][0]["source"], out["login"]["state"]), ("cswap-cache", "keychain"))

    def test_nothing_to_say_stays_none(self):
        """covers: USAGE-7"""
        self.assertIsNone(claude_accounts.read())
        self.keepalive()
        self.assertEqual(claude_accounts.read(), {"accounts": [], "login": claude_accounts.login()})

    def test_independent_keeps_the_keychain_warning_after_a_refresh(self):
        """covers: USAGE-7. 화면 경로: 새로고침 다음의 보통 조회에도 Claude 칸의 경고가 남는다"""
        self.hswap(keychainUnavailable=True)
        with self.independent_mocks():
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            for force in (True, False, False):
                claude = next(p for p in independent_usage.read(force=force)["providers"] if p["id"] == "claude")
                self.assertEqual((claude["accounts"], claude["login"]["state"]), ([], "keychain"), force)

    def test_independent_keeps_the_provider_for_login(self):
        """covers: USAGE-7. 계정이 없어 확인 불가로 남더라도 칸은 남아 복구 안내를 보인다"""
        self.keepalive(ok=False, error={"type": "no_credentials", "message": "m"})
        empty = {"status": "unavailable", "accounts": []}
        with mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("없음")), \
                mock.patch.object(independent_usage, "_opencode", return_value=empty), \
                mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[]), \
                mock.patch.object(independent_usage.antigravity_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.kimi_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.zcode_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.gemini_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.minimax_usage, "read", return_value=empty), \
                mock.patch.object(independent_usage.cli_login, "available", return_value=[]), \
                mock.patch.object(independent_usage.cli_login, "fresh", return_value=[]):
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            body = independent_usage.read()
        claude = next(p for p in body["providers"] if p["id"] == "claude")
        self.assertEqual((claude["accounts"], claude["login"]["state"]), ([], "failed"))
        self.assertIn("claude", body["unavailable"])


class SwitchRaceTest(StoreCase):
    """계정 전환과 읽기가 끼어드는 순서. 전환이 끝난 뒤의 읽기는 전환 전 값을 내지 않는다."""

    def setUp(self):
        super().setUp()
        self.write("sequence.json", {"sequence": [1, 2], "accounts": {"1": {"email": "a@example.com", "organizationUuid": "o1"},
                                                                      "2": {"email": "b@example.com", "organizationUuid": "o2"}}})
        self.lists = 0
        self.gate = None
        self.binary.return_value = "/fake/hswap"
        p = mock.patch.object(claude_accounts, "_json", side_effect=self.fake_json)
        self.addCleanup(p.stop)
        p.start()
        self.real_store = claude_accounts._store_read

    def login_as_b(self):
        self.live_identity.return_value = ("b@example.com", "o2")

    def fake_json(self, exe, *args, timeout=None):
        if args[0] == "switch":
            if self.gate:
                self.gate.wait(5)
            self.login_as_b()
            return {"switched": True}
        self.lists += 1  # 몇 번째 list 의 값인지 사용량으로 구분한다
        b = self.live_identity.return_value[0] == "b@example.com"
        return {"accounts": [hswap_row(number=1, email="a@example.com", active=not b, usage={"fiveHour": {"pct": self.lists}}),
                             hswap_row(number=2, email="b@example.com", organizationUuid="o2", active=b, usage={"fiveHour": {"pct": self.lists}})]}

    def active(self, out):
        return [a["slot"] for a in out["accounts"] if a["active"]]

    def store_then(self, on_call):
        """진짜 저장소를 읽은 뒤 on_call(몇 번째 읽기) 를 실행한다: 잡아 둔 값이 그 뒤의 일을 모르는 순서를 만든다."""
        calls = []

        def run():
            calls.append(1)
            got = self.real_store()
            on_call(len(calls))
            return got
        p = mock.patch.object(claude_accounts, "_store_read", side_effect=run)
        self.addCleanup(p.stop)
        p.start()
        return calls

    def test_identity_is_read_after_hswap_answers(self):
        """covers: USAGE-6. hswap 을 기다리는 사이 로그인이 바뀌면 답을 받기 전의 사용 중 슬롯으로 덮지 않는다"""
        real = self.fake_json

        def changed_meanwhile(exe, *args, timeout=None):
            self.login_as_b()
            return real(exe, *args, timeout=timeout)
        claude_accounts._json.side_effect = changed_meanwhile
        self.assertEqual(self.active(claude_accounts.read(force=True)), ["2"])

    def test_reader_waiting_on_a_running_switch_sees_the_new_login(self):
        """covers: USAGE-6. 전환이 잠금을 쥔 동안 들어온 읽기가 먼저 잡은 저장소로 새 결과를 덮지 않는다"""
        self.gate = threading.Event()
        reader_read = threading.Event()
        self.store_then(lambda n: reader_read.set())
        sw = threading.Thread(target=claude_accounts.switch, args=("2",))
        sw.start()
        while not claude_accounts._lock.locked():
            time.sleep(.001)
        box = {}
        reader = threading.Thread(target=lambda: box.update(out=claude_accounts.read(force=True)))
        reader.start()
        self.assertTrue(reader_read.wait(5))
        self.gate.set()
        sw.join(10)
        reader.join(10)
        self.assertEqual(self.active(box["out"]), ["2"])

    def test_switch_finishing_after_the_answer_is_not_answered_with_old_values(self):
        """covers: USAGE-6. hswap 이 전환 전 값을 답한 뒤 전환이 끝나면 그 값을 내지 않고 다시 읽는다"""
        self.store_then(lambda n: claude_accounts.switch("2") if n == 2 else None)
        out = claude_accounts.read(force=True)
        self.assertEqual(self.active(out), ["2"])
        self.assertEqual(self.lists, 2)
        self.assertEqual(out["accounts"][0]["windows"][0]["used"], 2)

    def test_held_values_are_not_reused_across_a_switch(self):
        """covers: USAGE-6. 묶어 둔 전환 전 실값을 잡은 뒤 전환이 끝나도 그 값을 내지 않는다"""
        claude_accounts.read()
        self.assertEqual(self.lists, 1)
        pending = [lambda: claude_accounts.switch("2")]
        # login() 은 묶음을 잡은 뒤에 불린다: 그 자리에서 전환이 끝나는 순서를 만든다
        with mock.patch.object(claude_accounts, "_keepalive", side_effect=lambda: pending and pending.pop()() and None):
            out = claude_accounts.read()
        self.assertEqual(self.active(out), ["2"])
        self.assertEqual(self.lists, 2)
        self.assertEqual(out["accounts"][0]["windows"][0]["used"], 2)

    def test_failed_switch_also_invalidates_the_read(self):
        """covers: USAGE-6. 실패한 전환도 로그인을 바꿨을 수 있어 다시 묻는다"""
        claude_accounts.read()
        real = self.fake_json

        def fail(exe, *args, timeout=None):
            if args[0] == "switch":
                raise RuntimeError("locked")
            return real(exe, *args, timeout=timeout)
        claude_accounts._json.side_effect = fail
        self.assertEqual(claude_accounts.switch("2")[0], 409)
        claude_accounts.read()
        self.assertEqual(self.lists, 2)

    def test_endless_switching_does_not_hang_the_read(self):
        """covers: USAGE-6"""
        self.store_then(lambda n: claude_accounts.switch("2"))
        self.assertEqual(self.active(claude_accounts.read(force=True)), ["2"])

    def test_no_switch_no_extra_list(self):
        """covers: USAGE-6"""
        self.assertEqual(self.active(claude_accounts.read(force=True)), ["1"])
        self.assertEqual(self.lists, 1)


class DiscoveryTest(StoreCase):
    """저장소를 읽지 못하는 기기: CLI 로 계정을 찾은 적이 있는지와 찾은 적이 없는지를 나눈다."""

    def setUp(self):
        super().setUp()
        self.store(time.time() - 60)
        os.chmod(os.path.join(self.root, "sequence.json"), 0o644)  # _private_json 이 거부한다
        self.assertIsNone(claude_accounts._store_read())

    def age(self):
        claude_accounts._state.cache = (time.monotonic() - claude_accounts.config.CLAUDE_STALE_S - 1, *claude_accounts._state.cache[1:])

    def test_accounts_found_through_the_cli_do_not_vanish_after_the_threshold(self):
        """covers: USAGE-6"""
        run = self.hswap(hswap_row())
        self.assertEqual(len(claude_accounts.read(force=True)["accounts"]), 1)
        self.assertEqual(len(claude_accounts.read()["accounts"]), 1)  # 기준 안: 묶음을 쓰고 다시 부르지 않는다
        self.assertEqual(run.call_count, 1)
        self.age()
        self.assertEqual(len(claude_accounts.read()["accounts"]), 1)
        self.assertEqual(run.call_count, 2)

    def test_never_discovered_stays_none_without_spawning_hswap(self):
        """covers: USAGE-6"""
        run = self.hswap(hswap_row())
        for _ in range(3):
            self.assertIsNone(claude_accounts.read())
        run.assert_not_called()

    def test_failed_or_empty_discovery_is_not_discovery(self):
        """covers: USAGE-6"""
        run = self.hswap()
        run.side_effect = RuntimeError("locked")
        self.assertEqual(claude_accounts.read(force=True), {"error": "locked"})
        self.assertIsNone(claude_accounts.read())
        run.side_effect = None
        run.return_value = {"accounts": []}
        self.assertIsNone(claude_accounts.read(force=True))
        self.assertIsNone(claude_accounts.read())
        self.assertEqual(run.call_count, 2)

    def test_empty_answer_ends_discovery(self):
        """covers: USAGE-6. hswap 이 계정이 없다고 답하면 찾은 기록을 지워 다시 부르지 않는다"""
        run = self.hswap(hswap_row())
        claude_accounts.read(force=True)
        self.age()
        run.return_value = {"accounts": []}
        self.assertIsNone(claude_accounts.read())
        self.assertEqual(run.call_count, 2)
        self.age()
        self.assertIsNone(claude_accounts.read())
        self.assertEqual(run.call_count, 2)

    def test_failed_recheck_reports_the_error_and_tries_again(self):
        """covers: USAGE-6. 일시 실패가 찾은 기록을 지우지 않는다"""
        run = self.hswap(hswap_row())
        claude_accounts.read(force=True)
        self.age()
        run.side_effect = RuntimeError("locked")
        self.assertEqual(claude_accounts.read(), {"error": "locked"})
        self.age()
        run.side_effect = None
        self.assertEqual(len(claude_accounts.read()["accounts"]), 1)
        self.assertEqual(run.call_count, 3)

    def test_recheck_does_not_start_the_installer(self):
        """covers: USAGE-6"""
        self.hswap(hswap_row())
        claude_accounts.read(force=True)
        self.age()
        self.binary.return_value = None
        self.assertIsNone(claude_accounts.read())
        self.ensure.assert_not_called()


class StoreAuthFailureTest(StoreCase):
    """최근 CLI 행이 그 뒤에 저장소에 적힌 인증 실패를 가리지 않는다. 사용량 값은 CLI 값을 그대로 둔다."""

    RELOGIN = claude_accounts.STATUS["relogin_required"][0]

    def two_slots(self, strikes, b_email="b@example.com", b_org="Org"):
        self.write("sequence.json", {"sequence": [1, 2], "accounts": {
            "1": {"email": "a@example.com", "organizationUuid": "o1", "organizationName": "Org"},
            "2": {"email": b_email, "organizationUuid": "o2", "organizationName": b_org}}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {
            "1": {"email": "a@example.com", "organizationUuid": "o1", "fetchedAt": time.time() - 60, "authDeadStrikes": strikes[0], "lastGood": {"five_hour": {"pct": 99}}},
            "2": {"email": b_email, "organizationUuid": "o2", "fetchedAt": time.time() - 60, "authDeadStrikes": strikes[1], "lastGood": {"five_hour": {"pct": 99}}}}})

    def rows(self):
        return [hswap_row(number=1, active=True), hswap_row(number=2, email="b@example.com", organizationUuid="o2", active=False)]

    def test_failure_after_a_successful_list_reaches_the_row_and_the_device(self):
        """covers: USAGE-7. hswap 0.2.1, keepalive 없음: list 성공 뒤 수집기가 사용 중 계정에 authDeadStrikes 를 적으면 바로 경고한다"""
        self.two_slots((0, 0))
        run = self.hswap(*self.rows())
        before = claude_accounts.read()
        self.assertNotIn("login", before)
        self.assertFalse(before["accounts"][0]["attention"])
        self.two_slots((1, 0))
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)  # 인증 실패가 바뀌면 다시 묻는다. hswap 이 아직 정상으로 답해도 저장소의 실패를 붙인다
        a = out["accounts"][0]
        self.assertEqual((a["status"], a["note"], a["error"], a["attention"]), ("ok", self.RELOGIN, None, True))
        self.assertEqual((a["windows"], a["updated_at"], a["source"]), (before["accounts"][0]["windows"], before["accounts"][0]["updated_at"], "cswap"))  # 저장소의 99% 로 바꾸지 않는다
        self.assertEqual((out["login"]["state"], out["login"]["attention"], out["login"]["slot"]), ("relogin", True, "1"))
        self.assertFalse(out["accounts"][1]["attention"])

    def test_inactive_account_gets_the_row_warning_only(self):
        """covers: USAGE-7"""
        self.two_slots((0, 0))
        self.hswap(*self.rows())
        claude_accounts.read()
        self.two_slots((0, 1))
        out = claude_accounts.read()
        self.assertEqual((out["accounts"][1]["note"], out["accounts"][1]["attention"]), (self.RELOGIN, True))
        self.assertFalse(out["accounts"][0]["attention"])
        self.assertNotIn("login", out)

    def test_row_without_values_shows_the_failure_as_its_error(self):
        """covers: USAGE-7"""
        self.two_slots((1, 0))
        self.hswap(hswap_row(number=1, active=True, usage=None), hswap_row(number=2, email="b@example.com", organizationUuid="o2", active=False))
        a = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual((a["status"], a["error"], a["note"], a["attention"], a["windows"]), ("error", self.RELOGIN, None, True, []))

    def test_only_the_same_slot_and_identity_match(self):
        """covers: USAGE-7. 슬롯 번호가 같아도 다른 계정의 실패를 붙이지 않는다"""
        row = {"number": 2, "active": False, "organizationUuid": "o2"}
        for kw in ({"email": "other@example.com"}, {"email": "b@example.com", "organizationUuid": "other-org"}, {"email": "b@example.com", "number": 3}):
            self.two_slots((0, 1))
            self.hswap(self.rows()[0], hswap_row(**{**row, **kw}))
            claude_accounts._state.cache = None
            a = claude_accounts.read(force=True)["accounts"][1]
            self.assertEqual((a["note"], a["attention"]), (None, False), kw)

    def test_hswap_own_relogin_stays_as_it_is(self):
        """covers: USAGE-7"""
        row = hswap_row(usageStatus="relogin_required", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60))
        want = claude_accounts.account(row)
        for strikes in (0, 1):
            self.store(time.time() - 60, authDeadStrikes=strikes)
            self.hswap(row)
            claude_accounts._state.cache = None
            out = claude_accounts.read(force=True)
            a = out["accounts"][0]
            self.assertEqual((a["status"], a["note"], a["error"], a["attention"], a["windows"]), (want["status"], want["note"], want["error"], want["attention"], want["windows"]), strikes)
            self.assertEqual(out["login"]["state"], "relogin")

    def test_cleared_failure_clears_at_once_and_held_rows_are_untouched(self):
        """covers: USAGE-7. 묶음의 행은 고치지 않으므로 저장소가 고쳐지면 다음 읽기에서 경고가 사라진다"""
        self.two_slots((0, 0))
        self.hswap(*self.rows())
        claude_accounts.read()
        self.two_slots((1, 0))
        self.assertEqual(claude_accounts.read()["login"]["state"], "relogin")
        self.assertEqual((claude_accounts._state.cache[1]["accounts"][0]["note"], claude_accounts._state.cache[1]["accounts"][0]["attention"]), (None, False))
        self.two_slots((0, 0))
        out = claude_accounts.read()
        self.assertNotIn("login", out)
        self.assertFalse(out["accounts"][0]["attention"])

    def test_the_usage_api_shows_it(self):
        """covers: USAGE-7. 화면이 부르는 전체 경로"""
        self.two_slots((0, 0))
        self.hswap(*self.rows())
        claude_accounts.read()
        self.two_slots((1, 0))
        with self.independent_mocks():
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            claude = next(p for p in independent_usage.read()["providers"] if p["id"] == "claude")
        self.assertEqual((claude["login"]["state"], claude["accounts"][0]["attention"], claude["accounts"][0]["note"]), ("relogin", True, self.RELOGIN))


class SlotIdentityTest(StoreCase):
    """같은 슬롯에 다른 계정이 들어온 경우. 신원은 이메일과 조직 UUID 의 지문으로 비교하고 내보내지 않는다."""

    RELOGIN = claude_accounts.STATUS["relogin_required"][0]

    def slot_holds(self, email, uuid, strikes=0):
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": email, "organizationUuid": uuid, "organizationName": "Org"}}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": {"email": email, "organizationUuid": uuid, "fetchedAt": time.time() - 60,
                                                                               "authDeadStrikes": strikes, "lastGood": {"five_hour": {"pct": 99}}}}})

    def b_row(self):
        return hswap_row(email="b@example.com", organizationUuid="b-org", active=False)

    def test_new_account_in_the_slot_invalidates_held_rows_at_once(self):
        """covers: USAGE-6, USAGE-7. A 를 묶어 둔 뒤 그 슬롯에 B 가 들어오면 다음 보통 읽기가 hswap 을 다시 실행해 B 와 그 경고를 보인다"""
        self.slot_holds("a@example.com", "o1")
        run = self.hswap(hswap_row())
        self.assertEqual(claude_accounts.read()["accounts"][0]["label"], "a@example.com")
        self.slot_holds("b@example.com", "b-org", strikes=1)
        self.live_identity.return_value = ("b@example.com", "b-org")
        run.return_value = {"accounts": [self.b_row()]}
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)  # 30초 묶음 안이어도 다시 묻는다
        a = out["accounts"][0]
        self.assertEqual((a["label"], a["active"], a["note"], a["attention"]), ("b@example.com", True, self.RELOGIN, True))
        self.assertEqual((out["login"]["state"], out["login"]["slot"]), ("relogin", "1"))

    def test_org_uuid_alone_is_a_different_identity(self):
        """covers: USAGE-6. 같은 이메일의 다른 조직. 조직 이름이 같아도 UUID 로 가른다"""
        self.slot_holds("a@example.com", "o1")
        run = self.hswap(hswap_row())
        claude_accounts.read()
        self.slot_holds("a@example.com", "o2")
        self.live_identity.return_value = ("a@example.com", "o2")
        run.return_value = {"accounts": [hswap_row(organizationUuid="o2", active=False)]}
        self.assertTrue(claude_accounts.read()["accounts"][0]["active"])
        self.assertEqual(run.call_count, 2)

    def test_unchanged_identity_does_not_rerun_hswap(self):
        """covers: USAGE-6"""
        self.slot_holds("a@example.com", "o1")
        run = self.hswap(hswap_row())
        for _ in range(4):
            self.assertTrue(claude_accounts.read()["accounts"][0]["active"])
        run.assert_called_once()

    def test_missing_org_uuid_means_the_same_as_empty(self):
        """covers: USAGE-6. 개인 계정은 UUID 가 비어 있다: 빈 문자열·null·누락을 같은 신원으로 본다"""
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": {"email": "a@example.com"}}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {}})
        self.live_identity.return_value = ("a@example.com", "")
        for uuid in ("", None):
            claude_accounts._state.cache = None
            run = self.hswap(hswap_row(organizationUuid=uuid))
            for _ in range(2):
                self.assertTrue(claude_accounts.read()["accounts"][0]["active"], uuid)
            run.assert_called_once()

    def test_failed_recheck_falls_back_to_the_new_account_in_the_store(self):
        """covers: USAGE-6, USAGE-7"""
        self.slot_holds("a@example.com", "o1")
        run = self.hswap(hswap_row())
        claude_accounts.read()
        self.slot_holds("b@example.com", "b-org", strikes=1)
        self.live_identity.return_value = ("b@example.com", "b-org")
        run.side_effect = RuntimeError("locked")
        out = claude_accounts.read()
        a = out["accounts"][0]
        self.assertEqual((a["label"], a["source"], a["active"], a["attention"]), ("b@example.com", "cswap-cache", True, True))
        self.assertEqual(out["login"]["state"], "relogin")

    def test_flags_do_not_cross_to_another_identity(self):
        """covers: USAGE-6, USAGE-7. hswap 이 아직 A 를 답해도 B 의 사용 중 표시와 인증 실패를 A 에 옮기지 않는다"""
        self.slot_holds("a@example.com", "o1")
        run = self.hswap(hswap_row())
        claude_accounts.read()
        self.slot_holds("b@example.com", "b-org", strikes=1)
        self.live_identity.return_value = ("b@example.com", "b-org")
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)
        a = out["accounts"][0]
        self.assertEqual((a["label"], a["active"], a["note"], a["attention"]), ("a@example.com", False, None, False))
        self.assertNotIn("login", out)

    def test_identity_is_never_exported(self):
        """covers: USAGE-6, USAGE-7. 조직 UUID 와 지문은 비교에만 쓴다"""
        self.slot_holds("a@example.com", "org-uuid-secret")
        self.live_identity.return_value = ("a@example.com", "org-uuid-secret")
        self.hswap(hswap_row(organizationUuid="org-uuid-secret"))
        fingerprint = claude_accounts._fingerprint("a@example.com", "org-uuid-secret")
        with self.independent_mocks():
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            bodies = [claude_accounts.read(), claude_accounts.read(force=True), independent_usage.read(force=True)]
        self.assertEqual(claude_accounts._state.cache[1]["ids"]["1"], fingerprint)  # 묶음에는 있어 비교할 수 있다
        for body in bodies:
            text = json.dumps(body, ensure_ascii=False)
            for secret in ("org-uuid-secret", fingerprint, '"ids"'):
                self.assertNotIn(secret, text)


class StoreShapeTest(StoreCase):
    """슬롯 추가·삭제·신원 변경·사용 중 슬롯 변경은 묶어 둔 hswap 값을 무효로 한다."""

    RELOGIN = claude_accounts.STATUS["relogin_required"][0]
    A = ("a@example.com", "o1")
    B = ("b@example.com", "o2")

    def slots(self, *held):
        """held: (슬롯, 신원, strikes) 들로 저장소를 쓴다."""
        self.write("sequence.json", {"sequence": [n for n, _, _ in held], "accounts": {
            str(n): {"email": who[0], "organizationUuid": who[1], "organizationName": "Org"} for n, who, _ in held}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {
            str(n): {"email": who[0], "organizationUuid": who[1], "fetchedAt": time.time() - 60, "authDeadStrikes": strikes, "lastGood": {"five_hour": {"pct": 99}}}
            for n, who, strikes in held}})

    def row(self, n, who, active=False):
        return hswap_row(number=n, email=who[0], organizationUuid=who[1], active=active)

    def test_new_active_slot_with_auth_failure_shows_at_once(self):
        """covers: USAGE-6, USAGE-7. A 를 묶어 둔 뒤 새 슬롯에 B 를 더해 활성화하고 인증 실패가 적히면 다음 보통 읽기에 보인다"""
        self.slots((1, self.A, 0))
        run = self.hswap(self.row(1, self.A, active=True))
        self.assertEqual([a["label"] for a in claude_accounts.read()["accounts"]], ["a@example.com"])
        self.slots((1, self.A, 0), (2, self.B, 1))
        self.live_identity.return_value = self.B
        run.return_value = {"accounts": [self.row(1, self.A), self.row(2, self.B, active=True)]}
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)  # 30초 묶음 안이어도 다시 묻는다
        a, b = out["accounts"]
        self.assertEqual((a["label"], a["active"], a["attention"]), ("a@example.com", False, False))
        self.assertEqual((b["label"], b["active"], b["note"], b["attention"]), ("b@example.com", True, self.RELOGIN, True))
        self.assertEqual((out["login"]["state"], out["login"]["slot"]), ("relogin", "2"))

    def test_removed_slot_disappears(self):
        """covers: USAGE-6"""
        self.slots((1, self.A, 0), (2, self.B, 0))
        run = self.hswap(self.row(1, self.A, active=True), self.row(2, self.B))
        self.assertEqual(len(claude_accounts.read()["accounts"]), 2)
        self.slots((1, self.A, 0))
        run.return_value = {"accounts": [self.row(1, self.A, active=True)]}
        self.assertEqual([a["slot"] for a in claude_accounts.read()["accounts"]], ["1"])
        self.assertEqual(run.call_count, 2)

    def test_removed_slot_disappears_when_hswap_fails(self):
        """covers: USAGE-6. 다시 묻기가 실패하면 지금 저장소 값을 쓴다"""
        self.slots((1, self.A, 0), (2, self.B, 0))
        run = self.hswap(self.row(1, self.A, active=True), self.row(2, self.B))
        claude_accounts.read()
        self.slots((1, self.A, 0))
        run.side_effect = RuntimeError("locked")
        rows = claude_accounts.read()["accounts"]
        self.assertEqual([(a["slot"], a["source"]) for a in rows], [("1", "cswap-cache")])
        self.assertIn("locked", rows[0]["note"])

    def test_added_slot_appears_without_activation(self):
        """covers: USAGE-6"""
        self.slots((1, self.A, 0))
        run = self.hswap(self.row(1, self.A, active=True))
        claude_accounts.read()
        self.slots((1, self.A, 0), (2, self.B, 0))
        run.return_value = {"accounts": [self.row(1, self.A, active=True), self.row(2, self.B)]}
        self.assertEqual([a["slot"] for a in claude_accounts.read()["accounts"]], ["1", "2"])
        self.assertEqual(run.call_count, 2)

    def test_active_slot_change_alone_reruns_hswap(self):
        """covers: USAGE-6. 슬롯 구성이 같아도 사용 중 슬롯이 바뀌면 묶음을 쓰지 않는다"""
        self.slots((1, self.A, 0), (2, self.B, 0))
        run = self.hswap(self.row(1, self.A, active=True), self.row(2, self.B))
        claude_accounts.read()
        self.live_identity.return_value = self.B
        run.return_value = {"accounts": [self.row(1, self.A), self.row(2, self.B, active=True)]}
        self.assertEqual([a["active"] for a in claude_accounts.read()["accounts"]], [False, True])
        self.assertEqual(run.call_count, 2)

    def test_change_during_the_call_is_seen_by_the_same_read(self):
        """covers: USAGE-6. 호출 중에 저장소가 바뀌면 그 답을 내지 않고 바로 다시 묻는다"""
        self.slots((1, self.A, 0))
        run = self.hswap()
        answers = [{"accounts": [self.row(1, self.A, active=True)]}, {"accounts": [self.row(1, self.A, active=True), self.row(2, self.B)]}]

        def store_changes_meanwhile(*args, **kw):
            self.slots((1, self.A, 0), (2, self.B, 0))
            return answers.pop(0)
        run.side_effect = store_changes_meanwhile
        self.assertEqual(len(claude_accounts.read()["accounts"]), 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(claude_accounts.read()["accounts"]), 2)
        self.assertEqual(run.call_count, 2)

    def hold_first_list(self):
        """다음 list 를 gate 가 열릴 때까지 붙잡고 옛 답(A 만)을 준다. 그 뒤의 list 는 새 답(B 사용 중)을 준다."""
        self.entered, self.gate, self.holding = threading.Event(), threading.Event(), True
        self.addCleanup(self.gate.set)
        run = self.hswap()

        def answer(*args, **kw):
            if self.holding:
                self.holding = False
                self.entered.set()
                self.gate.wait(10)
                return {"accounts": [self.row(1, self.A, active=True)]}
            return {"accounts": [self.row(1, self.A), self.row(2, self.B, active=True)]}
        run.side_effect = answer
        return run

    def change_store_to_b(self):
        self.slots((1, self.A, 0), (2, self.B, 1))
        self.live_identity.return_value = self.B

    def assert_b_is_current(self, out):
        self.assertEqual([(a["slot"], a["active"]) for a in out["accounts"]], [("1", False), ("2", True)])
        self.assertEqual((out["accounts"][1]["note"], out["accounts"][1]["attention"]), (self.RELOGIN, True))
        self.assertEqual((out["login"]["state"], out["login"]["slot"]), ("relogin", "2"))

    def assert_cache_matches_the_store(self):
        self.assertEqual(claude_accounts._state.cache[2], claude_accounts._store_shape(claude_accounts._store_read()))
        self.assertEqual([a["slot"] for a in claude_accounts._state.cache[1]["accounts"]], ["1", "2"])

    def test_reader_does_not_coalesce_onto_a_call_from_an_older_store(self):
        """covers: USAGE-6, USAGE-7. A 의 호출이 진행 중일 때 저장소가 바뀌고 B 가 읽으면, B 는 A 의 답을 다시 쓰지 않고 새 호출을 한다"""
        self.slots((1, self.A, 0))
        run = self.hold_first_list()
        real, b_waits = claude_accounts._lock, threading.Event()

        class SpyLock:  # B 가 잠금을 기다리기 시작한 때를 안다
            def acquire(self, timeout=-1):
                if threading.current_thread().name == "reader-b":
                    b_waits.set()
                return real.acquire(timeout=timeout)

            def release(self):
                real.release()
        box = {}
        with mock.patch.object(claude_accounts, "_lock", SpyLock()):
            a = threading.Thread(target=lambda: box.update(a=claude_accounts.read(force=True)), name="reader-a")
            a.start()
            self.assertTrue(self.entered.wait(5))
            self.change_store_to_b()
            b = threading.Thread(target=lambda: box.update(b=claude_accounts.read()), name="reader-b")
            b.start()
            self.assertTrue(b_waits.wait(5))
            self.gate.set()  # A 의 옛 답이 B 가 잠금을 기다리는 중에 도착한다
            a.join(10)
            b.join(10)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(run.call_count, 2)
        self.assert_b_is_current(box["b"])
        self.assert_b_is_current(box["a"])  # A 도 옛 답을 내지 않는다
        self.assert_cache_matches_the_store()  # 늦게 온 옛 답이 새 결과를 덮지 않았다
        self.assert_b_is_current(claude_accounts.read())  # 다음 보통 읽기도 B 이고 다시 부르지 않는다
        self.assertEqual(run.call_count, 2)

    def test_old_answer_after_the_lock_wait_gave_up_is_not_taken_as_current(self):
        """covers: USAGE-6, USAGE-7. A 가 멈춰 B 가 잠금을 포기해도 옛 묶음을 쓰지 않고 저장소를 보이며, A 의 옛 답이 나중에 와도 유효로 치지 않는다"""
        self.slots((1, self.A, 0))
        run = self.hold_first_list()
        self.holding = False
        run.side_effect, later = (lambda *a, **k: {"accounts": [self.row(1, self.A, active=True)]}), run.side_effect
        claude_accounts.read()  # 지금 저장소의 값을 묶어 둔다
        self.holding = True
        run.side_effect = later
        box = {}
        a = threading.Thread(target=lambda: box.update(a=claude_accounts.read(force=True)), name="reader-a")
        a.start()
        self.assertTrue(self.entered.wait(5))
        self.change_store_to_b()
        with mock.patch.object(claude_accounts, "TIMEOUT_S", -4.9):  # 잠금을 0.1초만 기다린다
            out = claude_accounts.read()
        self.assertEqual([(r["slot"], r["active"], r["source"]) for r in out["accounts"]], [("1", False, "cswap-cache"), ("2", True, "cswap-cache")])
        self.assertIn("hswap 이 응답하지 않습니다", out["accounts"][0]["note"])
        self.assertEqual(out["login"]["state"], "relogin")
        self.gate.set()
        a.join(10)
        self.assertFalse(a.is_alive())
        self.assert_b_is_current(box["a"])
        self.assert_cache_matches_the_store()
        self.assert_b_is_current(claude_accounts.read())

    def test_page_reader_sharing_a_running_job_gets_current_values(self):
        """covers: USAGE-6, USAGE-7. 독립 경로는 진행 중인 Claude 작업을 함께 기다린다: 그 작업도 옛 답을 내지 않는다"""
        self.slots((1, self.A, 0))
        run = self.hold_first_list()
        box = {}
        with self.independent_mocks():
            independent_usage._tasks.clear()
            self.addCleanup(independent_usage._tasks.clear)
            pick = lambda body: next(p for p in body["providers"] if p["id"] == "claude")
            a = threading.Thread(target=lambda: box.update(a=pick(independent_usage.read())))
            a.start()
            self.assertTrue(self.entered.wait(5))
            self.change_store_to_b()
            b = threading.Thread(target=lambda: box.update(b=pick(independent_usage.read())))
            b.start()
            self.gate.set()
            a.join(20)
            b.join(20)
        self.assertFalse(a.is_alive() or b.is_alive())
        for page in (box["a"], box["b"]):
            self.assertEqual((page["login"]["state"], [r["active"] for r in page["accounts"]]), ("relogin", [False, True]))
        self.assertEqual(run.call_count, 2)

    def test_one_change_reruns_once_even_if_hswap_keeps_disagreeing(self):
        """covers: USAGE-6. hswap 답과 저장소가 계속 어긋나도 변경 한 번에 한 번만 다시 부른다"""
        self.slots((1, self.A, 0))
        run = self.hswap(self.row(1, self.A, active=True))
        claude_accounts.read()
        self.slots((1, self.A, 0), (2, self.B, 0))
        for _ in range(4):
            claude_accounts.read()
        self.assertEqual(run.call_count, 2)

    def test_new_usage_does_not_rerun(self):
        """covers: USAGE-6. 저장소의 사용량 값과 시각이 새로워진 것만으로는 묶음을 무효로 하지 않는다"""
        self.slots((1, self.A, 0))
        run = self.hswap(self.row(1, self.A, active=True))
        claude_accounts.read()
        self.slots((1, self.A, 0))
        self.assertEqual(claude_accounts.read()["accounts"][0]["source"], "cswap")
        run.assert_called_once()

    def test_unreadable_store_keeps_using_held_values(self):
        """covers: USAGE-6. 저장소를 처음부터 읽지 못하면 비교할 것이 같아 다시 묻지 않는다"""
        self.slots((1, self.A, 0))
        os.chmod(os.path.join(self.root, "sequence.json"), 0o644)
        run = self.hswap(self.row(1, self.A, active=True))
        claude_accounts.read(force=True)
        for _ in range(3):
            self.assertEqual(len(claude_accounts.read()["accounts"]), 1)
        run.assert_called_once()


class ReadDeadlineTest(StoreCase):
    """읽기 한 번의 전체 시한. 가상 시계와 계속 바뀌는 저장소로 잠금 대기와 CLI 호출이 시한을 함께 쓰는지 본다."""

    RELOGIN = claude_accounts.STATUS["relogin_required"][0]

    def setUp(self):
        super().setUp()
        self.now = [1000.0]
        p = mock.patch.object(claude_accounts.time, "monotonic", lambda: self.now[0])
        p.start()
        self.addCleanup(p.stop)
        self.live_identity.return_value = ("u1@example.com", "o1")
        self.slots = 1
        self.write_store()
        self.timeouts = []

    def write_store(self):
        numbers = range(1, self.slots + 1)
        self.write("sequence.json", {"sequence": list(numbers), "accounts": {str(n): {"email": f"u{n}@example.com", "organizationUuid": f"o{n}"} for n in numbers}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {str(n): {
            "email": f"u{n}@example.com", "organizationUuid": f"o{n}", "fetchedAt": time.time() - 60, "authDeadStrikes": 1 if n == 1 else 0,
            "lastGood": {"five_hour": {"pct": 99}}} for n in numbers}})

    def changing_cli(self, takes):
        """호출마다 takes 초(시한 안에서)가 지나고 그 사이 저장소에 슬롯이 하나 는다. 답은 호출 전 슬롯 구성이다."""
        run = self.hswap()

        def answer(exe, *args, timeout=None):
            self.timeouts.append(timeout)
            self.now[0] += min(takes, timeout)
            rows = [hswap_row(number=n, email=f"u{n}@example.com", organizationUuid=f"o{n}", active=n == 1) for n in range(1, self.slots + 1)]
            self.slots += 1
            self.write_store()
            return {"accounts": rows}
        run.side_effect = answer
        return run

    def assert_store_answer(self, out, run, most_calls):
        self.assertLessEqual(self.now[0] - 1000.0, claude_accounts.READ_BUDGET_S)
        self.assertLess(claude_accounts.READ_BUDGET_S, 55)  # independent_usage.read 의 대기 시간 안
        self.assertLessEqual(run.call_count, most_calls)
        self.assertEqual([a["label"] for a in out["accounts"]], [f"u{n}@example.com" for n in range(1, self.slots + 1)])  # 지금 저장소
        self.assertEqual({a["source"] for a in out["accounts"]}, {"cswap-cache"})
        self.assertEqual((out["accounts"][0]["active"], out["accounts"][0]["attention"], out["login"]["state"]), (True, True, "relogin"))
        self.assertNotIn("ids", out)

    def test_retries_run_out_on_a_changing_store(self):
        """covers: USAGE-6. 재시도는 2번까지이고, 확정하지 못한 CLI 값 대신 지금 저장소 값과 로그인 상태를 준다"""
        run = self.changing_cli(takes=1)
        out = claude_accounts.read(force=True)
        self.assertEqual(run.call_count, 3)
        self.assert_store_answer(out, run, 3)
        self.assertIn("확정하지 못했습니다", out["accounts"][0]["note"])

    def test_cli_calls_share_the_deadline(self):
        """covers: USAGE-6. 느린 CLI 는 남은 시간만 받는다"""
        run = self.changing_cli(takes=15)
        out = claude_accounts.read(force=True)
        self.assertEqual(self.timeouts, [15, 15, claude_accounts.READ_BUDGET_S - 30])
        self.assert_store_answer(out, run, 3)

    def test_lock_waits_share_the_deadline(self):
        """covers: USAGE-6. 잠금 대기도 같은 시한에서 빠지고, 시한에 닿으면 CLI 를 부르지 않는다"""
        real, now, waits = claude_accounts._lock, self.now, []

        class WaitsAllItsTimeout:
            def acquire(self, timeout=-1):
                waits.append(timeout)
                now[0] += timeout
                return real.acquire(timeout=1)

            def release(self):
                real.release()
        run = self.changing_cli(takes=15)
        with mock.patch.object(claude_accounts, "_lock", WaitsAllItsTimeout()):
            out = claude_accounts.read(force=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(waits, [claude_accounts.TIMEOUT_S + 5, claude_accounts.READ_BUDGET_S - claude_accounts.TIMEOUT_S - claude_accounts.TIMEOUT_S - 5])
        self.assert_store_answer(out, run, 3)

    def test_deadline_reached_without_a_store(self):
        """covers: USAGE-6. 대체값이 없으면 오류와 로그인 상태만 준다"""
        os.chmod(os.path.join(self.root, "sequence.json"), 0o644)
        self.keepalive(activeSlot=None, ok=False, error={"type": "relogin_required"})
        real, now = claude_accounts._lock, self.now

        class Stuck:
            def acquire(self, timeout=-1):
                now[0] += timeout
                return False

            def release(self):
                real.release()
        self.changing_cli(takes=1)
        with mock.patch.object(claude_accounts, "_lock", Stuck()):
            out = claude_accounts.read(force=True)
        self.assertEqual((out["accounts"], out["login"]["state"]), ([], "relogin"))
        self.assertIn("error", out)

    def test_settled_answer_is_still_the_cli_answer(self):
        """covers: USAGE-6"""
        run = self.hswap(hswap_row(email="u1@example.com", organizationUuid="o1"))
        out = claude_accounts.read(force=True)
        run.assert_called_once()
        self.assertEqual(out["accounts"][0]["source"], "cswap")


class ForcedTaskTest(StoreCase):
    """새로고침은 보통 읽기에 얹히지 않는다. 보통 읽기는 어느 쪽에든 얹힌다."""

    def setUp(self):
        super().setUp()
        self.store(time.time() - 60)
        self.calls = 0
        self.cli_gate = None
        self.entered = threading.Event()
        self.hswap().side_effect = self.answer

    def answer(self, *args, **kw):
        self.calls += 1
        n = self.calls
        self.entered.set()
        if self.cli_gate:
            self.cli_gate.wait(10)
        return {"accounts": [hswap_row(usage={"fiveHour": {"pct": n * 10}})]}

    def used(self, body):
        return next(p for p in body["providers"] if p["id"] == "claude")["accounts"][0]["windows"][0]["used"]

    def reader(self, box, key, force):
        t = threading.Thread(target=lambda: box.update({key: self.used(independent_usage.read(force=force))}))
        t.start()
        return t

    def run_page(self):
        p = self.independent_mocks()
        independent_usage._tasks.clear()
        self.addCleanup(independent_usage._tasks.clear)
        return p

    def test_refresh_does_not_ride_on_an_ordinary_read_of_cached_values(self):
        """covers: USAGE-6. 묶음만 다시 쓰는 보통 읽기가 진행 중이어도 새로고침은 hswap 에 묻고 새 값을 받는다"""
        box = {}
        with self.run_page():
            self.assertEqual(self.used(independent_usage.read()), 10)
            self.assertEqual(self.calls, 1)
            paused, gate, real = threading.Event(), threading.Event(), claude_accounts._store_read

            def hold_the_ordinary_read():
                paused.set()
                gate.wait(10)
                return real()
            self.addCleanup(gate.set)
            with mock.patch.object(claude_accounts, "_store_read") as store:
                def first_read_waits():
                    store.side_effect = real
                    return hold_the_ordinary_read()
                store.side_effect = first_read_waits
                ordinary = self.reader(box, "ordinary", False)
                self.assertTrue(paused.wait(5))
                forced = self.reader(box, "forced", True)
                time.sleep(.1)  # 새로고침이 진행 중인 보통 읽기를 본다
                self.assertEqual(self.calls, 1)
                gate.set()
                ordinary.join(20)
                forced.join(20)
        self.assertFalse(ordinary.is_alive() or forced.is_alive())
        self.assertEqual(self.calls, 2)
        self.assertEqual((box["ordinary"], box["forced"]), (10, 20))

    def test_refresh_after_an_ordinary_read_that_is_asking_hswap(self):
        """covers: USAGE-6. 보통 읽기가 hswap 호출 중이면 끝나기를 기다린 뒤 새로 묻는다"""
        box = {}
        self.cli_gate = threading.Event()
        self.addCleanup(self.cli_gate.set)
        with self.run_page():
            ordinary = self.reader(box, "ordinary", False)
            self.assertTrue(self.entered.wait(5))
            forced = self.reader(box, "forced", True)
            time.sleep(.1)
            self.cli_gate.set()
            ordinary.join(20)
            forced.join(20)
        self.assertEqual(self.calls, 2)
        self.assertEqual((box["ordinary"], box["forced"]), (10, 20))

    def test_refresh_shares_a_running_refresh(self):
        """covers: USAGE-6"""
        box = {}
        self.cli_gate = threading.Event()
        self.addCleanup(self.cli_gate.set)
        with self.run_page():
            one = self.reader(box, "one", True)
            self.assertTrue(self.entered.wait(5))
            two = self.reader(box, "two", True)
            time.sleep(.1)
            self.cli_gate.set()
            one.join(20)
            two.join(20)
        self.assertEqual(self.calls, 1)
        self.assertEqual((box["one"], box["two"]), (10, 10))

    def test_ordinary_read_shares_a_running_refresh(self):
        """covers: USAGE-6"""
        box = {}
        self.cli_gate = threading.Event()
        self.addCleanup(self.cli_gate.set)
        with self.run_page():
            forced = self.reader(box, "forced", True)
            self.assertTrue(self.entered.wait(5))
            ordinary = self.reader(box, "ordinary", False)
            time.sleep(.1)
            self.cli_gate.set()
            forced.join(20)
            ordinary.join(20)
        self.assertEqual(self.calls, 1)
        self.assertEqual((box["forced"], box["ordinary"]), (10, 10))


class IdentityNormalizationTest(StoreCase):
    """신원 비교는 한 규칙을 지난다: 조직 UUID 의 null·누락·빈 문자열은 같고, 이메일은 앞뒤 공백과 대소문자를 보지 않는다."""

    RELOGIN = claude_accounts.STATUS["relogin_required"][0]
    MISSING = object()

    def setUp(self):
        super().setUp()
        self.config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_dir.cleanup)
        p = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.config_dir.name})
        p.start()
        self.addCleanup(p.stop)
        self.live_identity.side_effect = LIVE_IDENTITY  # 임시 .claude.json 을 읽는 실제 규칙

    def with_uuid(self, body, uuid):
        return body if uuid is self.MISSING else {**body, "organizationUuid": uuid}

    def logged_in(self, email="a@example.com", uuid=None):
        with open(os.path.join(self.config_dir.name, ".claude.json"), "w", encoding="utf-8") as f:
            json.dump({"oauthAccount": self.with_uuid({"emailAddress": email}, uuid)}, f)

    def roster(self, email="a@example.com", uuid=None, usage_uuid=MISSING, strikes=0):
        """저장소 슬롯 1. usage_uuid 를 주지 않으면 사용량 캐시도 같은 값을 쓴다."""
        usage_uuid = uuid if usage_uuid is self.MISSING else usage_uuid
        self.write("sequence.json", {"sequence": [1], "accounts": {"1": self.with_uuid({"email": email, "organizationName": "Org"}, uuid)}})
        self.write("cache/usage.json", {"schemaVersion": 2, "accounts": {"1": self.with_uuid(
            {"email": email, "fetchedAt": time.time() - 60, "authDeadStrikes": strikes, "lastGood": {"five_hour": {"pct": 33}}}, usage_uuid)}})

    def cli_row(self, email="a@example.com", uuid=None, active=True):
        row = hswap_row(email=email, active=active)
        del row["organizationUuid"]
        return self.with_uuid(row, uuid)

    def test_null_org_account_stays_active_without_keepalive(self):
        """covers: USAGE-6, USAGE-7. 저장소의 organizationUuid 가 null 이어도 hswap 의 active 를 지우지 않는다. 전체 경로에서도"""
        self.roster(uuid=None)
        self.logged_in(uuid=None)
        self.hswap(self.cli_row(uuid=None))
        out = claude_accounts.read(force=True)
        self.assertTrue(out["accounts"][0]["active"])
        self.assertNotIn("login", out)
        claude_accounts._state.cache = None
        self.assertTrue(self.full_read()["accounts"][0]["active"])

    def test_null_org_account_keeps_login_and_slot_with_keepalive(self):
        """covers: USAGE-7. 사용 중 슬롯이 맞으면 keepalive 기록은 이 계정의 것이다: 이전 계정의 기록으로 보거나(/login 을 빼는 안내) 슬롯을 빼지 않는다"""
        self.roster(uuid=None)
        self.logged_in(uuid=None)
        self.hswap(self.cli_row(uuid=None))
        self.keepalive(activeSlot=1)
        s = claude_accounts.read(force=True)["login"]
        self.assertEqual((s["state"], s["slot"], s["other"]), ("ok", "1", False))
        self.keepalive(activeSlot=1, ok=False, error={"type": "no_credentials", "message": "m"})
        s = claude_accounts.read()["login"]
        self.assertEqual((s["state"], s["slot"], s["other"], s["text"]), ("failed", "1", False, "자동 갱신 실패(자격 증명 없음)"))
        self.keepalive(activeSlot=2)
        self.assertEqual(claude_accounts.read()["login"]["state"], "other_account")  # 정말 다른 슬롯의 기록은 그대로 가린다

    def test_null_org_account_gets_relogin_from_the_store(self):
        """covers: USAGE-7. CLI 행과 저장소의 신원이 맞아 인증 실패가 옮겨 가고 기기 상태가 재로그인이 된다"""
        self.roster(uuid=None, strikes=1)
        self.logged_in(uuid=None)
        self.hswap(self.cli_row(uuid=None))
        out = claude_accounts.read(force=True)
        a = out["accounts"][0]
        self.assertEqual((a["active"], a["note"], a["attention"]), (True, self.RELOGIN, True))
        self.assertEqual((out["login"]["state"], out["login"]["slot"], out["login"]["other"]), ("relogin", "1", False))

    def test_null_org_account_stays_active_on_the_store_path(self):
        """covers: USAGE-6. hswap 없이 저장소만 읽어도 같다"""
        self.roster(uuid=None)
        self.logged_in(uuid=None)
        a = claude_accounts.read()["accounts"][0]
        self.assertEqual((a["active"], a["source"]), (True, "cswap-cache"))

    def test_null_missing_and_empty_uuid_are_the_same_account(self):
        """covers: USAGE-6. 저장소·사용량 캐시·CLI·.claude.json 의 어떤 조합이든 같은 계정이다"""
        forms = (None, self.MISSING, "", "  ")
        for stored in forms:
            for cached in forms:
                for live in forms:
                    for cli in forms:
                        where = (stored, cached, live, cli)
                        self.roster(uuid=stored, usage_uuid=cached)
                        self.logged_in(uuid=live)
                        direct = claude_accounts._store_read()["accounts"][0]
                        self.assertEqual((direct["active"], [w["used"] for w in direct["windows"]]), (True, [33]), where)  # 저장소↔로그인, 저장소↔사용량 캐시
                        run = self.hswap(self.cli_row(uuid=cli, active=False))
                        claude_accounts._state.cache = None
                        for _ in range(2):
                            self.assertTrue(claude_accounts.read()["accounts"][0]["active"], where)  # CLI↔저장소
                        run.assert_called_once()  # 모습이 달라졌다고 보아 다시 부르지 않는다

    def test_email_ignores_case_and_surrounding_space(self):
        """covers: USAGE-6, USAGE-7"""
        self.roster(email="A@Example.com", uuid="ORG-1", strikes=1)
        self.logged_in(email=" a@EXAMPLE.com\n", uuid="org-1 ")
        self.hswap(self.cli_row(email="a@example.COM ", uuid="org-1", active=False))
        out = claude_accounts.read(force=True)
        a = out["accounts"][0]
        self.assertEqual((a["active"], a["note"]), (True, self.RELOGIN))
        self.assertEqual(out["login"]["state"], "relogin")

    def test_different_accounts_still_differ(self):
        """covers: USAGE-6. 규칙이 다른 계정을 같게 만들지 않는다"""
        self.roster(uuid=None, strikes=1)
        for email, uuid in (("other@example.com", None), ("a@example.com", "org-1")):
            self.logged_in(email=email, uuid=uuid)
            self.assertFalse(claude_accounts._store_read()["accounts"][0]["active"], (email, uuid))
            self.assertNotEqual(claude_accounts._fingerprint("a@example.com", None), claude_accounts._fingerprint(email, uuid or "x@"))
        self.logged_in(uuid=None)
        self.hswap(self.cli_row(uuid="org-1"))
        claude_accounts._state.cache = None
        a = claude_accounts.read(force=True)["accounts"][0]
        self.assertEqual((a["active"], a["note"]), (False, None))  # 같은 슬롯이라도 조직이 다른 CLI 행에 옮기지 않는다


class AuthRecordChangeTest(StoreCase):
    """/login 뒤 같은 계정을 다시 저장하면, 더 새로운 keepalive·저장소 기록이 묶어 둔 CLI 의 인증 값을 무효로 한다."""

    FAR = "2099-01-01T00:00:00Z"

    def healed(self, run):
        run.return_value = {"accounts": [hswap_row()]}

    def assert_healthy(self, out):
        a = out["accounts"][0]
        self.assertEqual((a["note"], a["error"], a["attention"], a["status"]), (None, None, False, "ok"))

    def relogin_row(self):
        return hswap_row(usageStatus="relogin_required", usage=None, lastGoodUsage={"sevenDay": {"pct": 1}}, lastGoodFetchedAt=iso(time.time() - 60))

    def test_relogin_clears_when_keepalive_and_store_recover(self):
        """covers: USAGE-6, USAGE-7. 재현 1: keepalive 가 정상이 되고 인증 실패가 지워지면 다음 보통 읽기에 relogin 이 남지 않는다"""
        self.store(time.time() - 60, authDeadStrikes=1)
        self.keepalive(activeSlot=1, ok=False, error={"type": "relogin_required"}, loginExpiresAt=self.FAR)
        run = self.hswap(self.relogin_row())
        self.assertEqual(claude_accounts.read()["login"]["state"], "relogin")
        self.store(time.time() - 60, authDeadStrikes=0)
        self.keepalive(activeSlot=1, loginExpiresAt=self.FAR)
        self.healed(run)
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)
        self.assert_healthy(out)
        self.assertEqual(out["login"]["state"], "ok")

    def test_each_record_alone_invalidates(self):
        """covers: USAGE-6, USAGE-7. 저장소 쪽만, keepalive 쪽만 바뀌어도 다시 묻는다"""
        self.store(time.time() - 60, authDeadStrikes=1)
        run = self.hswap(self.relogin_row())
        claude_accounts.read()
        self.store(time.time() - 60, authDeadStrikes=0)  # keepalive 없는 기기: 저장소의 인증 실패만 지워진다
        self.healed(run)
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)
        self.assert_healthy(out)
        self.assertNotIn("login", out)
        claude_accounts._state.cache = None
        self.keepalive(activeSlot=1, ok=False, error={"type": "refresh_failed"}, loginExpiresAt=self.FAR)
        run.return_value = {"accounts": [self.relogin_row()]}
        claude_accounts.read()
        self.assertEqual(run.call_count, 3)
        self.keepalive(activeSlot=1, loginExpiresAt=self.FAR)  # 실패 종류가 정상으로
        self.healed(run)
        self.assert_healthy(claude_accounts.read())
        self.assertEqual(run.call_count, 4)
        self.keepalive(activeSlot=1, ok=False, error={"type": "locked"}, loginExpiresAt=self.FAR)  # 정상이 실패로
        claude_accounts.read()
        self.keepalive(activeSlot=1, ok=False, error={"type": "refresh_failed"}, loginExpiresAt=self.FAR)  # 실패 종류끼리도 다르다
        claude_accounts.read()
        self.assertEqual(run.call_count, 6)

    def test_expiry_warning_clears_when_keepalive_has_a_new_deadline(self):
        """covers: USAGE-6, USAGE-7. 재현 2: login 은 ok 인데 '로그인 만료 2일 남음' 이 남지 않는다"""
        soon = iso(time.time() + 2 * 86400)
        self.store(time.time() - 60)
        self.keepalive(activeSlot=1, loginExpiresAt=soon)
        run = self.hswap(hswap_row(loginExpiresAt=soon))
        before = claude_accounts.read()
        self.assertIn("로그인 만료", before["accounts"][0]["note"])
        self.assertEqual(before["login"]["state"], "expiring")
        self.keepalive(activeSlot=1, loginExpiresAt=self.FAR)
        self.healed(run)
        out = claude_accounts.read()
        self.assertEqual(run.call_count, 2)
        self.assert_healthy(out)
        self.assertEqual(out["login"]["state"], "ok")

    def test_only_timestamps_changing_does_not_rerun(self):
        """covers: USAGE-6. checkedAt 같은 시각과 그 밖의 필드만 바뀐 keepalive 기록으로는 CLI 호출이 늘지 않는다"""
        self.store(time.time() - 60)
        self.keepalive(activeSlot=1, loginExpiresAt=self.FAR, checkedAt=time.time() - 300)
        run = self.hswap(hswap_row())
        self.assertEqual(claude_accounts.read()["login"]["text"], "로그인됨, 자동 갱신 5분 전")
        self.keepalive(activeSlot=1, loginExpiresAt=self.FAR, checkedAt=time.time() - 120, action="refreshed", note="split_brain",
                       accessExpiresAt=iso(time.time() + 7 * 3600))
        for _ in range(3):
            out = claude_accounts.read()
        self.assertEqual(out["login"]["text"], "로그인됨, 자동 갱신 2분 전")  # 문구는 읽을 때마다 새로 만든다
        run.assert_called_once()

    def test_repeated_strikes_do_not_rerun(self):
        """covers: USAGE-6. 이미 실패인 계정의 횟수만 늘면 보이는 값이 같아 다시 묻지 않는다"""
        self.store(time.time() - 60, authDeadStrikes=1)
        run = self.hswap(hswap_row())
        claude_accounts.read()
        self.store(time.time() - 60, authDeadStrikes=2)
        self.assertEqual(claude_accounts.read()["login"]["state"], "relogin")
        run.assert_called_once()

    def test_record_changes_without_a_readable_store(self):
        """covers: USAGE-6. 저장소를 못 읽어도 keepalive 변경은 다시 묻는다"""
        self.store(time.time() - 60)
        os.chmod(os.path.join(self.root, "sequence.json"), 0o644)
        self.keepalive(ok=False, activeSlot=None, error={"type": "relogin_required"}, loginExpiresAt=self.FAR)
        run = self.hswap(self.relogin_row())
        claude_accounts.read(force=True)
        self.keepalive(activeSlot=None, loginExpiresAt=self.FAR)
        self.healed(run)
        self.assert_healthy(claude_accounts.read())
        self.assertEqual(run.call_count, 2)

    def test_record_values_stay_internal(self):
        """covers: USAGE-7. 비교에 쓰는 실패 종류는 묶음 안에만 있고 응답에는 고정 문구만 나간다"""
        self.store(time.time() - 60)
        self.keepalive(activeSlot=1, ok=False, error={"type": "secret-kind", "message": "m"}, loginExpiresAt=self.FAR)
        self.hswap(hswap_row())
        text = json.dumps(claude_accounts.read(force=True), ensure_ascii=False)
        self.assertNotIn("secret-kind", text)
        self.assertIn("알 수 없는 오류", text)


NOW, MONO = 1_800_000_000.0, 5000.0
STALE, KEEPALIVE_STALE = claude_accounts.config.CLAUDE_STALE_S, claude_accounts.config.CLAUDE_KEEPALIVE_STALE_S
RELOGIN = claude_accounts.STATUS["relogin_required"][0]


def shown_row(slot="1", **kw):
    """화면 행 하나. 값이 있고 1분 전에 읽었다."""
    return {"slot": slot, "label": f"u{slot}@example.com", "org": "Org", "active": False, "status": "ok", "attention": False,
            "note": None, "error": None, "windows": [{"key": "session", "used": 1}], "buckets": [], "updated_at": NOW - 60, **kw}


def store_value(*rows, **kw):
    """_store_read() 의 값 모양. 슬롯 1·2 의 지문이 답과 같고 로그인을 읽었다."""
    return {"accounts": list(rows) or [shown_row(active=True), shown_row("2")], "signed_in": True, "ids": {"1": "f1", "2": "f2"}, "orgs": {}, **kw}


def cli_answer(*rows, **kw):
    return {"accounts": list(rows) or [shown_row(active=True), shown_row("2")], "fetched_at": NOW, "ids": {"1": "f1", "2": "f2"}, **kw}


def keepalive_value(**kw):
    return {"schemaVersion": 1, "checkedAt": NOW - 300, "ok": True, "activeSlot": 1, "accessExpiresAt": NOW + 3 * 3600 + 30, "loginExpiresAt": NOW + 30 * 86400, "error": None, **kw}


class JudgeRowsTest(unittest.TestCase):
    """행 판정을 값만으로 본다: 패치도 파일도 없다."""

    def judge(self, out, store, hswap=True):
        return claude_accounts._judge_rows(out, store, hswap, NOW)

    def test_nothing_to_show(self):
        """covers: USAGE-6"""
        for out in (None, {"error": "locked"}):
            self.assertEqual(self.judge(out, None), ([], None), out)

    def test_in_use_row(self):
        """covers: USAGE-6. 표의 칸: 답, 저장소, 슬롯 1·2 의 사용 중"""
        unread = store_value(signed_in=False)
        other_id = store_value(ids={"1": "other", "2": "f2"})
        cases = (("실제 로그인의 슬롯", cli_answer(), store_value(), [True, False]),
                 ("답과 달라도 실제 로그인을 따른다", cli_answer(shown_row(active=False), shown_row("2", active=True)), store_value(), [True, False]),
                 ("슬롯 번호만 같고 신원이 다르다", cli_answer(), other_id, [False, False]),
                 ("저장되지 않은 로그인", cli_answer(), store_value(shown_row(), shown_row("2")), [False, False]),
                 ("로그인을 읽지 못하면 hswap 의 값", cli_answer(shown_row(), shown_row("2", active=True)), unread, [False, True]),
                 ("저장소가 없으면 hswap 의 값", cli_answer(), None, [True, False]))
        for name, out, store, want in cases:
            with self.subTest(name):
                rows, accounts = self.judge(out, store)
                self.assertEqual([a["active"] for a in rows], want)
                self.assertEqual([a["active"] for a in accounts], want)

    def test_store_auth_failure_moves_to_the_same_login_only(self):
        """covers: USAGE-7. 표의 칸: 답의 행, 저장소의 지문, 기대하는 (status, attention, note, error, 값 유지)"""
        dead = store_value(shown_row(active=True, note=RELOGIN, attention=True))
        cases = (("값이 있는 행", shown_row(), dead, ("ok", True, RELOGIN, None, True)),
                 ("값이 없는 행", shown_row(windows=[]), dead, ("error", True, None, RELOGIN, False)),
                 ("모델별 한도만 있는 행도 값이 있다", shown_row(windows=[], buckets=[{"name": "Opus", "used": 1}]), dead, ("ok", True, RELOGIN, None, False)),
                 ("이미 재로그인 문구", shown_row(note=RELOGIN), dead, ("ok", False, RELOGIN, None, True)),
                 ("같은 번호의 다른 계정", shown_row(), {**dead, "ids": {"1": "other"}}, ("ok", False, None, None, True)),
                 ("저장소에 실패 없음", shown_row(), store_value(), ("ok", False, None, None, True)))
        for name, row, store, want in cases:
            with self.subTest(name):
                a = self.judge(cli_answer(row), store)[0][0]
                self.assertEqual((a["status"], a["attention"], a["note"], a["error"], bool(a["windows"])), want)

    def test_org_comes_from_the_store_for_the_same_login(self):
        """covers: USAGE-1, USAGE-6. 묶어 둔 답의 옛 이름을 저장소의 이름과 시각이 대신하고, 신원이 다른 행에는 붙지 않는다"""
        orgs = {"1": {"org": "새 이름", "org_key": "k", "org_at": 5.0}}
        cases = (("같은 신원", store_value(orgs=orgs), ("새 이름", "k", 5.0)),
                 ("다른 신원", store_value(orgs=orgs, ids={"1": "other"}), ("Org", None, None)),
                 ("저장소 없음", None, ("Org", None, None)))
        for name, store, want in cases:
            with self.subTest(name):
                a = self.judge(cli_answer(shown_row()), store)[1][0]
                self.assertEqual((a["org"], a.get("org_key"), a.get("org_at")), want)
        personal = shown_row(label="me@example.com", org="me@example.com's Organization")
        self.assertIsNone(self.judge(cli_answer(personal), None)[0][0]["org"])

    def test_store_rows_say_why_they_are_shown(self):
        """covers: USAGE-6. 표의 칸: 답, hswap 있음, 문구"""
        cases = ((None, False, "hswap 이 없습니다. 저장된 값을 보입니다."), (None, True, "hswap 이 계정을 주지 않았습니다. 저장된 값을 보입니다."),
                 ({"error": "locked."}, True, "locked. 저장된 값을 보입니다."))
        for out, hswap, want in cases:
            with self.subTest(want):
                rows, accounts = self.judge(out, store_value(shown_row(active=True)), hswap)
                self.assertEqual(accounts[0]["note"], want)
                self.assertIsNone(rows[0]["note"])  # login 판정은 이유가 붙기 전의 행을 본다
                self.assertFalse(accounts[0]["attention"])

    def test_age_is_measured_from_the_given_time(self):
        """covers: USAGE-6. 표의 칸: updated_at, 기대하는 (note, attention)"""
        cases = ((NOW - STALE, (None, False)), (NOW - STALE - 1, ("15분 전 값입니다.", True)), (NOW - 9 * 3600, ("9시간 전 값입니다.", True)),
                 (None, ("저장 시각을 모르는 값입니다.", True)))
        for at, want in cases:
            with self.subTest(at=at):
                a = self.judge(cli_answer(shown_row(updated_at=at)), None)[1][0]
                self.assertEqual((a["note"], a["attention"]), want)
        empty = self.judge(cli_answer(shown_row(windows=[], updated_at=None)), None)[1][0]
        self.assertEqual((empty["note"], empty["attention"]), (None, False))  # 값이 없으면 늙을 것도 없다
        bucket = self.judge(cli_answer(shown_row(windows=[], buckets=[{"name": "Opus", "used": 1}], updated_at=NOW - 9 * 3600)), None)[1][0]
        self.assertEqual((bucket["note"], bucket["attention"]), ("9시간 전 값입니다.", True))  # 모델별 한도만 있어도 값이다

    def test_the_held_answer_is_not_changed(self):
        """covers: USAGE-6"""
        out = cli_answer(shown_row(updated_at=None))
        before = json.dumps(out)
        self.judge(out, store_value(shown_row(active=True, note=RELOGIN)))
        self.assertEqual(json.dumps(out), before)


class JudgeAskTest(unittest.TestCase):
    """묶음을 다시 쓸지 hswap 에 물을지를 값만으로 본다."""

    SHAPE = "지금 모습"

    def judge(self, force=False, store=None, held=None, discovered=False, fatal=None, device=None):
        return claude_accounts._judge_ask(force, store, self.SHAPE, held, discovered, fatal, device, MONO, NOW)

    def test_table(self):
        """covers: USAGE-6. 표의 칸: 이름, 입력, 물을까, 묶음을 돌려주는가"""
        fresh = cli_answer()
        held = (MONO - 60, fresh, self.SHAPE)
        old = (MONO - STALE, fresh, self.SHAPE)
        store = store_value()
        cases = (("찾은 적도 저장소도 없음", {}, False, False),
                 ("강제", {"force": True}, True, False),
                 ("저장소가 있다", {"store": store}, True, False),
                 ("계정을 찾은 적이 있다", {"discovered": True}, True, False),
                 ("치명적 오류를 기억한다", {"fatal": (MONO - 1, "config_conflict")}, True, False),
                 ("키체인 답이 기준 안", {"device": (MONO - 1, True)}, False, False),
                 ("키체인 답이 기준 밖", {"device": (MONO - STALE, True)}, True, False),
                 ("키체인에 닿는다는 옛 답", {"device": (MONO - STALE, False)}, False, False),
                 ("묶음이 신선하고 모습이 같다", {"store": store, "held": held}, False, True),
                 ("저장소를 잃어도 묶음을 쓴다", {"held": held}, False, True),
                 ("강제면 묶음이 있어도 묻는다", {"store": store, "held": held, "force": True}, True, True),
                 ("모습이 달라졌다", {"store": store, "held": (MONO - 60, fresh, "옛 모습")}, True, True),
                 ("묶음이 기준 밖", {"store": store, "held": old}, True, False),
                 ("묶음에 계정이 없다", {"store": store, "held": (MONO - 60, {"error": "x"}, self.SHAPE)}, True, False),
                 ("보일 값 중 오래된 것", {"store": store, "held": (MONO - 60, cli_answer(shown_row(updated_at=NOW - STALE - 1)), self.SHAPE)}, True, True),
                 ("오래된 값이 모델별 한도뿐", {"store": store, "held": (MONO - 60, cli_answer(shown_row(windows=[], buckets=[{"name": "Opus", "used": 1}], updated_at=NOW - STALE - 1)), self.SHAPE)}, True, True))
        for name, kw, ask, reuse in cases:
            with self.subTest(name):
                asked, recent = self.judge(**kw)
                self.assertEqual(bool(asked), ask)
                self.assertEqual(recent is not None, reuse)

    def test_recent_fact_expires_at_the_threshold(self):
        """covers: USAGE-7"""
        self.assertEqual(claude_accounts._recent((MONO - STALE + 1, "config_conflict"), MONO), "config_conflict")
        self.assertIsNone(claude_accounts._recent((MONO - STALE, "config_conflict"), MONO))
        self.assertIsNone(claude_accounts._recent(None, MONO))


class JudgeShapeTest(unittest.TestCase):
    """묶음의 유효성을 가르는 값을 파일 없이 본다."""

    def test_only_meaningful_changes_change_the_shape(self):
        """covers: USAGE-6. 표의 칸: 이름, 저장소, keepalive, 모습이 달라지는가"""
        store, k = store_value(), keepalive_value()
        base = claude_accounts._judge_shape(store, k)
        cases = (("checkedAt 만 바뀜", store, {**k, "checkedAt": NOW}, False),
                 ("접근 토큰 만료만 바뀜", store, {**k, "accessExpiresAt": NOW + 9}, False),
                 ("슬롯이 는다", {**store, "ids": {**store["ids"], "3": "f3"}}, k, True),
                 ("신원이 바뀐다", {**store, "ids": {"1": "other", "2": "f2"}}, k, True),
                 ("사용 중 슬롯이 바뀐다", store_value(shown_row(), shown_row("2", active=True)), k, True),
                 ("인증 실패가 생긴다", store_value(shown_row(active=True, note=RELOGIN), shown_row("2")), k, True),
                 ("keepalive 등급이 바뀐다", store, {**k, "ok": False, "error": {"type": "refresh_failed"}}, True),
                 ("로그인 기한이 바뀐다", store, {**k, "loginExpiresAt": NOW + 86400}, True),
                 ("keepalive 가 사라진다", store, {}, True),
                 ("저장소가 사라진다", None, k, True))
        for name, s, keep, differs in cases:
            with self.subTest(name):
                self.assertEqual(claude_accounts._judge_shape(s, keep) != base, differs)


class JudgeLoginTest(unittest.TestCase):
    """로그인 판정을 값만으로 본다: 패치도 파일도 없다."""

    ACTIVE = [{"active": True, "slot": "1"}]

    def judge(self, k, rows=(), keychain=False, signed_in=False, fatal=None, first_seen=None):
        return claude_accounts._judge_login(k, rows, keychain, signed_in, fatal, first_seen, NOW)

    def test_states(self):
        """covers: USAGE-7. 표의 칸: 이름, keepalive, 나머지 입력, 기대하는 (state, attention, slot, other)"""
        relogin_row = [{"active": True, "slot": "1", "note": RELOGIN}]
        failed = lambda kind, **kw: keepalive_value(ok=False, error={"type": kind, "message": "비밀"}, **kw)
        cases = (("정상", keepalive_value(), {"rows": self.ACTIVE}, ("ok", False, "1", False)),
                 ("기록 없음: 파일이 없으면 슬롯만 있어도 알릴 것이 없다", {}, {"rows": self.ACTIVE}, None),
                 ("기록 없어도 사용 중 계정이 재로그인", {}, {"rows": relogin_row}, ("relogin", True, "1", False)),
                 ("기록 없어도 hswap 의 키체인 답", {}, {"keychain": True}, ("keychain", True, None, False)),
                 ("기록 없어도 최상위 오류", {}, {"fatal": "config_conflict"}, ("config_conflict", True, None, False)),
                 ("최상위 재로그인", {}, {"fatal": "relogin_required"}, ("relogin", True, None, False)),
                 ("설정 충돌이 재로그인보다 먼저", failed("config_conflict"), {"rows": relogin_row}, ("config_conflict", True, "1", False)),
                 ("키체인 항목이 이전 계정보다 먼저", failed("keychain_item_present", activeSlot=2), {"rows": self.ACTIVE}, ("keychain_item_present", True, None, True)),
                 ("파일 저장소의 항목 표시", keepalive_value(store="file", keychainItemPresent=True), {}, ("keychain_item_present", True, None, False)),
                 ("재로그인 기록", failed("relogin_required"), {"rows": self.ACTIVE}, ("relogin", True, "1", False)),
                 ("이전 계정의 재로그인 기록은 쓰지 않는다", failed("relogin_required", activeSlot=2), {"rows": self.ACTIVE}, ("other_account", True, None, True)),
                 ("키체인 실패", failed("keychain_unavailable"), {}, ("keychain", True, None, False)),
                 ("파일 저장소는 키체인 답을 보지 않는다", keepalive_value(store="file"), {"keychain": True}, ("ok", False, None, False)),
                 ("만료 임박", keepalive_value(loginExpiresAt=NOW + 2 * 86400), {"rows": self.ACTIVE}, ("expiring", True, "1", False)),
                 ("기한 지남", keepalive_value(loginExpiresAt=NOW - 60), {"rows": self.ACTIVE}, ("expiring", True, "1", False)),
                 ("이전 계정의 만료는 쓰지 않는다", keepalive_value(activeSlot=2, loginExpiresAt=NOW + 60), {"rows": self.ACTIVE}, ("other_account", True, None, True)),
                 ("멈춤", keepalive_value(checkedAt=NOW - KEEPALIVE_STALE - 1), {"rows": self.ACTIVE}, ("stalled", True, "1", False)),
                 ("멈춤은 이전 계정보다 먼저", keepalive_value(activeSlot=2, checkedAt=None), {"rows": self.ACTIVE}, ("stalled", True, None, True)),
                 ("저장되지 않은 로그인", keepalive_value(), {"signed_in": True}, ("other_account", True, None, True)),
                 ("사용 중 슬롯을 모르면 경고만", keepalive_value(), {}, ("ok", False, None, False)),
                 ("activeSlot 없음", keepalive_value(activeSlot=None), {"rows": self.ACTIVE}, ("ok", False, None, False)),
                 ("activeSlot 이 참거짓", keepalive_value(activeSlot=True), {"rows": self.ACTIVE}, ("ok", False, None, False)),
                 ("자격 부재는 이전 계정이어도 실패", failed("no_credentials", activeSlot=2), {"rows": self.ACTIVE}, ("failed", True, None, True)),
                 ("그 밖의 실패", failed("refresh_failed"), {"rows": self.ACTIVE}, ("failed", True, "1", False)),
                 ("ok 가 참이 아닌 기록", keepalive_value(ok="true"), {"rows": self.ACTIVE}, ("failed", True, "1", False)))
        for name, k, kw, want in cases:
            with self.subTest(name):
                state, _ = self.judge(k, **kw)
                self.assertEqual(state and (state["state"], state["attention"], state["slot"], state["other"]), want)

    def test_fixed_text_and_note(self):
        """covers: USAGE-7. 파일의 자유 문구는 나가지 않고, 시각은 받은 now 로 잰다"""
        cases = ((keepalive_value(), "로그인됨, 자동 갱신 5분 전", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(store="file"), "로그인됨 · 파일 저장소, 자동 갱신 5분 전", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(accessExpiresAt=NOW - 1), "로그인됨, 자동 갱신 5분 전", "접근 토큰 만료됨(자동 갱신 대기)"),
                 (keepalive_value(accessExpiresAt=None), "로그인됨, 자동 갱신 5분 전", None),
                 (keepalive_value(loginExpiresAt=NOW + 2 * 86400), "로그인 만료 임박(2일)", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(loginExpiresAt=NOW - 60), "로그인 기한이 지났습니다", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(checkedAt=None), "자동 갱신 멈춤", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(ok=False, error={"type": "refresh_failed", "message": "비밀"}), "자동 갱신 실패(토큰 갱신 실패)", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(ok=False, error={"type": "새 종류", "message": "비밀"}), "자동 갱신 실패(알 수 없는 오류)", "접근 토큰 3시간 뒤 만료(자동 갱신)"),
                 (keepalive_value(ok=False, error={"type": "config_conflict"}), "hswap 설정 충돌(파일 저장소와 키체인 필수 모드)", None))
        for k, text, note in cases:
            with self.subTest(text):
                state, _ = self.judge(k)
                self.assertEqual((state["text"], state["note"]), (text, note))
                self.assertNotIn("비밀", json.dumps(state, ensure_ascii=False))

    def test_first_seen_is_returned_not_kept(self):
        """covers: USAGE-7. 표의 칸: 이름, keepalive, 받은 first_seen, 기대하는 (state, attention, 새 first_seen)"""
        failed = keepalive_value(store="file", ok=False, error={"type": "keychain_unavailable"})
        long_ago = NOW - KEEPALIVE_STALE - 60
        cases = (("처음 본다", failed, None, ("unconfirmed", False, NOW)),
                 ("시한 안", failed, NOW - KEEPALIVE_STALE, ("unconfirmed", False, NOW - KEEPALIVE_STALE)),
                 ("시한을 넘겼다", failed, long_ago, ("unconfirmed", True, long_ago)),
                 ("성공하면 지운다", keepalive_value(store="file"), long_ago, ("ok", False, None)),
                 ("다른 실패면 지운다", {**failed, "error": {"type": "refresh_failed"}}, long_ago, ("failed", True, None)),
                 ("키체인 저장소면 쓰지 않는다", {**failed, "store": "keychain"}, long_ago, ("keychain", True, None)))
        for name, k, seen, want in cases:
            with self.subTest(name):
                state, kept = self.judge(k, first_seen=seen)
                self.assertEqual((state["state"], state["attention"], kept), want)
        self.assertEqual(self.judge({}, first_seen=long_ago), (None, None))
        state, _ = self.judge(failed, first_seen=long_ago)
        self.assertIsNone(state["note"])  # 확인되지 않았는데 자동 갱신을 말하지 않는다
        self.assertIn("째)", state["text"])
        self.assertEqual(state["store"], "file")


if __name__ == "__main__":
    unittest.main()
