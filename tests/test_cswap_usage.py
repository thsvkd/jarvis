"""cswap(claude-swap) list --json → Claude 계정별 사용량, Orca 값과 합치기."""
import json
import subprocess
import unittest
from unittest import mock

from jarvis import cswap_usage, orca_usage

ISO = "2026-11-03T01:00:00+00:00"
ROWS = {"schemaVersion": 1, "activeAccountNumber": 1, "accounts": [
    {"number": 1, "email": "me@example.com", "organizationName": "me@example.com's Organization",
     "organizationUuid": "org-secret", "isOrganization": True, "active": True, "usageStatus": "ok",
     "usage": {"fiveHour": {"pct": 31.4, "resetsAt": ISO}, "sevenDay": {"pct": 12.0, "resetsAt": ISO},
               "scoped": [{"pct": 0.0, "resetsAt": ISO, "name": "Fable"}, {"pct": 7, "resetsAt": ISO, "name": "Opus"}]},
     "loginExpiresAt": "2026-11-05T23:41:25Z", "usageFetchedAt": "2026-10-27T16:00:02Z", "usageAgeSeconds": 64.0},
    {"number": 2, "email": "work@example.com", "organizationName": "Acme", "organizationUuid": "u2", "active": False,
     "usageStatus": "unavailable", "usage": None, "usageError": "http-429", "alias": "work", "disabled": True,
     "lastGoodUsage": {"fiveHour": {"pct": 98, "resetsAt": ISO}}, "lastGoodFetchedAt": "2026-10-27T15:00:00Z"},
    {"number": 3, "email": "old@example.com", "organizationName": "", "active": False, "usageStatus": "relogin_required",
     "usage": None, "lastGoodUsage": {"sevenDay": {"pct": 40, "resetsAt": ISO}}, "lastGoodFetchedAt": "2026-10-24T15:00:00Z"},
    {"number": 4, "email": "gone@example.com", "active": False, "usageStatus": "no_credentials", "usage": None},
    {"number": 5, "email": "key@example.com", "active": False, "usageStatus": "api_key", "usage": None}]}


def proc(body, rc=0):
    return mock.Mock(stdout=body if isinstance(body, str) else json.dumps(body), returncode=rc)


class AccountTest(unittest.TestCase):
    def test_rows(self):
        me, work, old, gone, key = [cswap_usage.account(r) for r in ROWS["accounts"]]
        self.assertEqual((me["label"], me["active"], me["status"], me["note"], me["attention"]),
                         ("me@example.com", True, "ok", None, False))
        self.assertEqual([(w["key"], w["used"]) for w in me["windows"]], [("session", 31), ("weekly", 12), ("fable", 0)])
        self.assertEqual(me["buckets"], [{"name": "Opus", "used": 7, "resets_at": 1793667600.0}])
        self.assertEqual(me["updated_at"], 1793116802.0)
        # 조회 실패: 마지막으로 읽은 값을 그 시각·이유와 함께 보여 준다(일시 실패는 주의로 세지 않는다)
        self.assertEqual((work["status"], work["plan"], [w["used"] for w in work["windows"]], work["updated_at"], work["attention"]),
                         ("ok", "work · 자동 전환 제외", [98], 1793113200.0, False))
        self.assertIn("http-429", work["note"])
        # 재로그인이 필요하면 마지막 값이 있어도 '조치 필요'다
        self.assertEqual((old["status"], [w["used"] for w in old["windows"]], old["attention"]), ("ok", [40], True))
        self.assertIn("다시 로그인", old["note"])
        self.assertEqual((gone["status"], gone["attention"]), ("error", True))
        self.assertIn("자격 증명", gone["error"])
        self.assertEqual((key["status"], key["error"]), ("ok", None))
        self.assertIn("API 키", key["note"])
        self.assertNotIn("org-secret", json.dumps([me, work, old]))

    def test_odd_numbers(self):
        a = cswap_usage.account({"email": "x@example.com", "usageStatus": "ok",
                                 "usage": {"fiveHour": {"pct": float("inf")}, "sevenDay": {"pct": True}}})
        self.assertEqual((a["windows"], a["status"]), ([], "ok"))


@mock.patch.object(cswap_usage, "_cache", None)
@mock.patch.object(cswap_usage, "binary", return_value="/home/alice/.local/bin/cswap")
class RunTest(unittest.TestCase):
    def test_missing(self, _):
        # 자동 설치를 끈다(이 기기에 Claude Code 가 있으면 실제로 설치를 시작한다)
        with mock.patch.object(cswap_usage, "binary", return_value=None), \
                mock.patch.object(cswap_usage.config, "CSWAP_INSTALL", False):
            self.assertIsNone(cswap_usage.read(force=True))

    def test_errors_hide_home(self, _):
        cases = ((proc("nope", 2), "종료 코드 2"),
                 (proc({"schemaVersion": 1, "error": {"message": "locked /home/alice/.claude-swap"}}, 1), "locked /home/~/"),
                 (subprocess.TimeoutExpired(["/home/alice/.local/bin/cswap"], 15), "15초"),
                 (PermissionError(13, "denied", "C:\\Users\\alice\\x"), "PermissionError"))
        for result, want in cases:
            kw = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with mock.patch.object(cswap_usage.subprocess, "run", **kw):
                err = cswap_usage.read(force=True)["error"]
            self.assertIn(want, err)
            self.assertNotIn("alice", err)

    def test_cache_and_empty(self, _):
        with mock.patch.object(cswap_usage.subprocess, "run", return_value=proc(ROWS)) as run:
            a = cswap_usage.read()
            b = cswap_usage.read()
            cswap_usage.read(force=True)
        self.assertIs(a, b)
        self.assertEqual((run.call_count, len(a["accounts"])), (2, 5))  # 30초 안의 두 번째는 캐시, 강제는 다시
        with mock.patch.object(cswap_usage.subprocess, "run", return_value=proc({"schemaVersion": 1, "accounts": []})):
            self.assertIsNone(cswap_usage.read(force=True))  # 계정이 없으면 cswap 이 없는 것과 같다


class MergeTest(unittest.TestCase):
    def merged(self, orca_body, cs):
        with mock.patch.object(orca_usage, "_orca", return_value=orca_body), \
                mock.patch.object(orca_usage.cswap_usage, "read", return_value=cs):
            return orca_usage.read()[1]

    def test_cswap_replaces_orca_system_default(self):
        cs = {"accounts": [cswap_usage.account(r) for r in ROWS["accounts"][:3]], "fetched_at": 5.0}
        orca_claude = {"id": "claude", "name": "Claude", "accounts": [
            {"label": "시스템 기본 계정", "org": None, "active": True},
            {"label": "orca-only@example.com", "org": None, "active": False},
            {"label": "ME@example.com", "org": None, "active": False},     # 대소문자만 다른 같은 계정
            {"label": "me@example.com", "org": "Team", "active": False}]}  # 같은 이메일의 다른 조직
        body = self.merged({"installed": True, "fetched_at": 9.0, "providers": [{"id": "codex", "accounts": []}, orca_claude]}, cs)
        self.assertEqual([p["id"] for p in body["providers"]], ["claude", "codex"])
        self.assertEqual([(a["label"], a["org"]) for a in body["providers"][0]["accounts"]],
                         [("me@example.com", None), ("work@example.com", "Acme"), ("old@example.com", None),
                          ("orca-only@example.com", None), ("me@example.com", "Team")])
        self.assertEqual(cs["accounts"][0]["org"], "me@example.com's Organization")  # cswap 캐시는 그대로
        self.assertEqual(body["fetched_at"], 9.0)

    def test_team_login_not_doubled(self):
        # Orca 는 시스템 기본 계정의 조직 이름을 모르면 None 이다. cswap 의 같은 로그인(Acme)과 한 줄로 본다
        row = {**ROWS["accounts"][1], "active": True}
        cs = {"accounts": [cswap_usage.account(row)], "fetched_at": 5.0}
        orca_claude = {"id": "claude", "accounts": [{"label": "work@example.com", "org": None, "active": True}]}
        acc = self.merged({"installed": True, "providers": [orca_claude]}, cs)["providers"][0]["accounts"]
        self.assertEqual([(a["label"], a["org"]) for a in acc], [("work@example.com", "Acme")])

    def test_system_default_kept_without_cswap_active(self):
        cs = {"accounts": [cswap_usage.account(ROWS["accounts"][1])], "fetched_at": 5.0}
        orca_claude = {"id": "claude", "accounts": [{"label": "시스템 기본 계정", "org": None, "active": True}]}
        labels = [a["label"] for a in self.merged({"installed": True, "providers": [orca_claude]}, cs)["providers"][0]["accounts"]]
        self.assertEqual(labels, ["work@example.com", "시스템 기본 계정"])

    def test_without_orca_and_cswap_error(self):
        cs = {"accounts": [cswap_usage.account(ROWS["accounts"][0])], "fetched_at": 5.0}
        body = self.merged({"installed": False, "running": False, "providers": []}, cs)
        self.assertEqual((body["installed"], body["providers"][0]["id"], body["fetched_at"]), (False, "claude", 5.0))
        body = self.merged({"installed": True, "providers": []}, {"error": "locked"})
        self.assertEqual((body["providers"], body["cswap_error"]), ([], "locked"))
        self.assertNotIn("cswap_error", self.merged({"installed": True, "providers": []}, None))


if __name__ == "__main__":
    unittest.main()


@mock.patch.object(cswap_usage, "_cache", None)
class InstallTest(unittest.TestCase):
    def setUp(self):
        cswap_usage.install.update(state=None, error=None, at=0.0)
        self.which = {"claude": "/home/alice/.local/bin/claude", "uv": "/home/alice/.local/bin/uv", "pipx": None, "cswap": None}
        p = mock.patch.object(cswap_usage, "_which", side_effect=lambda n: self.which[n])
        p.start()
        self.addCleanup(p.stop)

        class Now:  # 설치 스레드를 그 자리에서 실행한다
            def __init__(self, target, **k):
                self.target = target

            def start(self):
                self.target()
        p = mock.patch.object(cswap_usage.threading, "Thread", Now)
        p.start()
        self.addCleanup(p.stop)

    def test_installs_pinned_with_uv_once(self):
        def run(cmd, **k):
            self.which["cswap"] = "/home/alice/.local/bin/cswap"
            return mock.Mock(returncode=0, stderr="")
        with mock.patch.object(cswap_usage.subprocess, "run", side_effect=run) as r:
            self.assertEqual(cswap_usage.ensure(), "cswap 을 설치하는 중입니다(Claude 계정별 사용량)")
        self.assertEqual(r.call_args[0][0], ["/home/alice/.local/bin/uv", "tool", "install", "claude-swap==0.28.0"])
        self.assertEqual((cswap_usage.install["state"], cswap_usage.ensure()), ("done", None))

    def test_failure_is_reported_and_retried_later(self):
        self.which.update(uv=None, pipx="/usr/bin/pipx")
        bad = mock.Mock(returncode=1, stderr="error: /home/alice/x not writable\n")
        with mock.patch.object(cswap_usage.subprocess, "run", return_value=bad) as r:
            cswap_usage.ensure()
            err = cswap_usage.ensure()  # 6시간 안에는 다시 시도하지 않고 실패 이유만 준다
            self.assertEqual(r.call_count, 1)
            self.assertEqual(r.call_args[0][0][:2], ["/usr/bin/pipx", "install"])
            self.assertIn("종료 코드 1", err)
            self.assertNotIn("alice", err)
            cswap_usage.install["at"] -= cswap_usage.INSTALL_RETRY_S + 1
            cswap_usage.ensure()
            self.assertEqual(r.call_count, 2)
        self.assertEqual(cswap_usage.read()["error"].split("(")[0], "cswap 설치 실패")

    def test_skipped(self):
        with mock.patch.object(cswap_usage.subprocess, "run") as r:
            self.which["claude"] = None  # Claude Code 가 없으면 cswap 이 쓸모없다
            self.assertIsNone(cswap_usage.ensure())
            self.assertIsNone(cswap_usage.read())
            self.which["claude"] = "/x/claude"
            with mock.patch.object(cswap_usage.config, "CSWAP_INSTALL", False):
                self.assertIsNone(cswap_usage.ensure())
        r.assert_not_called()
        self.which.update(uv=None, pipx=None)
        cswap_usage.ensure()  # 시작하는 호출은 '설치 중'이다(실제로는 뒤에서 돈다)
        self.assertIn("uv·pipx", cswap_usage.ensure())
