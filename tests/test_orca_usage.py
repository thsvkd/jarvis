"""AI 구독 사용량: Orca accounts.list 정규화(계정별·프로바이더별, 비밀 필드 제외)와 새로고침 간격."""
import json
import unittest
from unittest import mock

from jarvis import orca_usage

NOW = 1_800_000_000_000
W = lambda used, mins=300: {"usedPercent": used, "windowMinutes": mins, "resetsAt": NOW + 3600_000, "resetDescription": "x"}
SNAP = {
    "claude": {"accounts": [
        {"id": "c1", "email": "me@example.com", "home": "/home/me/.config/orca/claude/c1", "organizationUuid": "org-secret"},
        {"id": "c2", "email": "work@example.com", "home": "/home/me/.config/orca/claude/c2", "organizationName": "Acme"},
        {"id": "c3", "email": "new@example.com"}],
        "activeAccountId": "c1", "activeAccountIdsByRuntime": {"host": "c1", "wsl": {}}},
    "codex": {"accounts": [], "activeAccountId": None, "activeAccountIdsByRuntime": {"host": None, "wsl": {}},
              "systemDefault": {"hasAuth": True, "email": "sys@example.com", "providerAccountId": "acct-secret"}},
    "rateLimits": {
        "claude": {"provider": "claude", "session": W(64.4), "weekly": W(41, 10080), "fableWeekly": W(12, 10080),
                   "extraUsage": {"unit": "currency", "balance": None, "currencyCode": "USD", "enabled": False,
                                  "disabledReason": "out_of_credits", "spent": 5.13, "spendLimit": 100, "spentPercent": 5},
                   "updatedAt": NOW, "error": None, "status": "ok", "usageMetadata": {"credentialSource": "keychain"}},
        "codex": {"provider": "codex", "session": None, "weekly": W(12, 10080), "planType": "pro",
                  "rateLimitResetCredits": {"availableCount": 1}, "updatedAt": NOW, "error": None, "status": "ok"},
        "grok": {"provider": "grok", "session": None, "weekly": None, "updatedAt": NOW, "status": "error",
                 "error": "Grok sign-in expired", "usageMetadata": {"failureKind": "delegated-refresh-required"}},
        "cursor": {"provider": "cursor", "status": "unavailable", "error": "No Cursor sign-in", "updatedAt": NOW},
        "minimax": {"provider": "minimax", "status": "error", "error": "x", "updatedAt": NOW,
                    "usageMetadata": {"failureKind": "missing-credentials"}},
        "antigravity": {"provider": "antigravity", "weekly": W(22, 10080), "status": "ok", "updatedAt": NOW,
                        "buckets": [{"name": "Gemini Models", "usedPercent": 4, "resetsAt": NOW}, {"name": "bad"}]},
        "kimi": None,
        "futureProvider": {"status": "ok", "weekly": W(1)},
        "inactiveClaudeAccounts": [
            {"accountId": "c2", "rateLimits": {"session": W(93), "weekly": W(150, 10080), "status": "ok", "updatedAt": NOW - 60_000},
             "updatedAt": NOW - 60_000, "isFetching": False}],
        "inactiveCodexAccounts": []}}


class NormalizeTest(unittest.TestCase):
    def test_providers_and_accounts(self):
        p = {x["id"]: x for x in orca_usage.normalize(SNAP)}
        self.assertEqual(list(p), ["claude", "codex", "grok", "antigravity"])  # 미설정·모르는 프로바이더는 뺀다
        me, work, new = p["claude"]["accounts"]
        self.assertEqual((me["label"], me["active"], [(w["key"], w["used"]) for w in me["windows"]]),
                         ("me@example.com", True, [("session", 64), ("weekly", 41), ("fable", 12)]))
        self.assertEqual(me["extra"]["spent"], 5.13)
        self.assertEqual(me["windows"][0]["resets_at"], (NOW + 3600_000) / 1000)  # ms → 초
        self.assertEqual((work["label"], work["org"], work["active"], [w["used"] for w in work["windows"]]),
                         ("work@example.com", "Acme", False, [93, 100]))  # 100 을 넘는 값은 자른다
        self.assertEqual((new["label"], new["status"], new["windows"]), ("new@example.com", "idle", []))
        codex = p["codex"]["accounts"]
        self.assertEqual([(a["label"], a["plan"], a["credits"]) for a in codex], [("sys@example.com", "pro", 1)])
        self.assertEqual((p["grok"]["accounts"][0]["status"], p["grok"]["accounts"][0]["error"]), ("error", "Grok sign-in expired"))
        self.assertEqual(p["antigravity"]["accounts"][0]["buckets"], [{"name": "Gemini Models", "used": 4, "resets_at": NOW / 1000}])

    def test_system_default_and_wsl(self):
        claude = {"accounts": [{"id": "h", "email": "host@example.com"}, {"id": "w", "email": "wsl@example.com"}],
                  "activeAccountId": None, "activeAccountIdsByRuntime": {"host": "h", "wsl": {"Ubuntu": "w"}},
                  "systemDefaultEmail": "sys@example.com", "systemDefaultHome": "/home/me/.claude"}
        rl = {"claude": {"session": W(5), "status": "ok"}}
        # WSL 이 대상이면 rateLimits.claude 는 WSL 의 사용 중 계정이다
        acc = orca_usage.normalize({"claude": claude, "rateLimits": {**rl, "claudeTarget": {"runtime": "wsl", "wslDistro": "Ubuntu"}}})[0]["accounts"]
        self.assertEqual([(a["label"], a["active"]) for a in acc], [("wsl@example.com", True), ("host@example.com", False)])
        # 관리 계정을 고르지 않은 Claude 의 시스템 기본 계정은 systemDefaultEmail 로 이름을 붙인다
        claude.update(activeAccountIdsByRuntime={"host": None, "wsl": {}}, accounts=[])
        acc = orca_usage.normalize({"claude": claude, "rateLimits": rl})[0]["accounts"]
        self.assertEqual([(a["label"], a["org"], a["active"]) for a in acc], [("sys@example.com", None, True)])
        claude.update(accounts=[{"id": "m", "email": "sys@example.com", "organizationUuid": "u1", "organizationName": "Acme"}],
                      systemDefaultOrganizationUuid="u1")
        acc = orca_usage.normalize({"claude": claude, "rateLimits": rl})[0]["accounts"]
        self.assertEqual(acc[0]["org"], "Acme")  # 같은 로그인을 관리 계정으로 쓰는 기기와 한 계정으로 묶인다
        self.assertNotIn("u1", json.dumps(acc))
        self.assertNotIn("/home/me", json.dumps(acc))

    def test_error_paths_scrubbed(self):
        acc = orca_usage.normalize({"rateLimits": {"grok": {"status": "error", "error": "cannot read /home/alice/.grok/auth.json"}}})
        self.assertEqual(acc[0]["accounts"][0]["error"], "cannot read /home/~/.grok/auth.json")

    def test_no_secrets(self):
        out = json.dumps(orca_usage.normalize(SNAP))
        for s in ("/home/me", "org-secret", "acct-secret", "keychain", "c1", "c2"):
            self.assertNotIn(s, out)

    def test_odd_input(self):
        self.assertEqual(orca_usage.normalize(None), [])
        odd = {"claude": {"accounts": 3, "activeAccountIdsByRuntime": []},
               "rateLimits": {"inactiveClaudeAccounts": 7, "grok": {"status": "ok", "monthly": W(3), "usageMetadata": "x", "buckets": 5}}}
        self.assertEqual([p["id"] for p in orca_usage.normalize(odd)], ["grok"])
        self.assertEqual(orca_usage.normalize({"rateLimits": {"claude": {"session": {"usedPercent": float("nan")},
                                                                             "status": "weird"}}}),
                         [{"id": "claude", "name": "Claude", "accounts": [
                             {"label": "시스템 기본 계정", "org": None, "active": True, "status": "error", "error": None, "failure": None,
                              "plan": None, "updated_at": None, "windows": [], "buckets": [], "extra": None, "credits": None}]}])


class ReadTest(unittest.TestCase):
    def setUp(self):  # 이 기기에 설치된 cswap·caam 을 읽거나 설치하지 않게
        for m in (orca_usage.cswap_usage, orca_usage.caam_usage):
            p = mock.patch.object(m, "read", return_value=None)
            p.start()
            self.addCleanup(p.stop)

    def test_refresh_interval(self):
        calls = []

        class Now:  # 뒤에서 돌리는 갱신도 바로 실행해 순서를 본다
            def __init__(self, target, **k):
                self.target = target

            def start(self):
                self.target()

            def join(self, timeout=None):
                pass

            def is_alive(self):
                return False

        with mock.patch.object(orca_usage.orca, "find_dir", return_value="/x"), \
                mock.patch.object(orca_usage.orca_rpc, "call", side_effect=lambda m, p, timeout: calls.append(p["refreshUsage"]) or SNAP), \
                mock.patch.object(orca_usage.threading, "Thread", Now), \
                mock.patch.object(orca_usage, "_refreshed", 0.0), mock.patch.object(orca_usage, "_last", None), \
                mock.patch.object(orca_usage.time, "monotonic") as mono:
            seen = []
            for t, force in ((1000, False), (1010, False), (1020, True), (1050, True), (1400, False)):
                mono.return_value = t
                code, body = orca_usage.read(force)
                self.assertEqual((code, len(body["providers"])), (200, 4))
                seen.append((body["refreshed"], body["refreshing"]))
        # 처음: 보관값을 읽고 뒤에서 갱신 · 10초 뒤: 보관값 · 강제는 30초 지나야 그 자리에서 · 300초 뒤: 마지막 값 + 뒤에서 갱신
        self.assertEqual(calls, [False, True, False, False, True, True])
        self.assertEqual(seen, [(False, True), (False, False), (False, False), (True, False), (False, True)])

    def test_slow_forced_refresh_falls_back(self):
        def call(m, p, timeout):
            if p["refreshUsage"]:
                raise TimeoutError("timed out")
            return SNAP
        with mock.patch.object(orca_usage.orca, "find_dir", return_value="/x"), \
                mock.patch.object(orca_usage.orca_rpc, "call", side_effect=call), \
                mock.patch.object(orca_usage, "_refreshed", 0.0), mock.patch.object(orca_usage, "_last", None):
            code, body = orca_usage.read(force=True)
            self.assertEqual((body["running"], len(body["providers"])), (True, 4))
            self.assertGreater(orca_usage._refreshed, 0)  # 실패해도 시도 시각을 남겨 곧바로 다시 부르지 않는다

    def test_busy_pipe_serves_last(self):
        # 뒤에서 다시 읽는 동안(RPC 잠김)은 파이프를 다시 열지 않고 마지막 값을 준다
        last = (123.0, [{"id": "claude"}])
        with mock.patch.object(orca_usage.orca, "find_dir", return_value="/x"), \
                mock.patch.object(orca_usage.orca_rpc, "call", side_effect=AssertionError("RPC 를 부르면 안 된다")), \
                mock.patch.object(orca_usage, "_refreshed", 1e12), mock.patch.object(orca_usage, "_last", last), \
                mock.patch.object(orca_usage.time, "monotonic", return_value=1e12 + 1):
            with orca_usage._rpc:
                body = orca_usage.read()[1]
        self.assertEqual((body["fetched_at"], body["providers"], body["refreshing"]), (123.0, [{"id": "claude"}], True))

    def test_not_installed_and_rpc_error(self):
        with mock.patch.object(orca_usage.orca, "find_dir", return_value=None):
            self.assertEqual(orca_usage.read()[1], {"installed": False, "running": False, "providers": []})
        with mock.patch.object(orca_usage.orca, "find_dir", return_value="/x"), \
                mock.patch.object(orca_usage, "_refreshed", 1e12), mock.patch.object(orca_usage, "_last", None), \
                mock.patch.object(orca_usage.orca_rpc, "call", side_effect=RuntimeError("Orca 로컬 소켓이 없습니다")):
            code, body = orca_usage.read()
        self.assertEqual((code, body["running"], body["error"]), (200, False, "Orca 로컬 소켓이 없습니다"))


if __name__ == "__main__":
    unittest.main()
