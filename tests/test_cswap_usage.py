"""hswap claude list --json(claude-swap 엔진) → Claude 계정별 사용량."""
import json
import subprocess
import unittest
from unittest import mock

from jarvis import claude_accounts

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
        # 픽스처를 받은 시점(usageFetchedAt)에 시계를 고정한다: 실제 시계면 loginExpiresAt 3일 전부터 만료 경고가 붙는다
        with mock.patch.object(claude_accounts.time, "time", return_value=claude_accounts.parse_iso("2026-10-27T16:00:02Z")):
            me, work, old, gone, key = [claude_accounts.account(r) for r in ROWS["accounts"]]
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

    def test_login_expiry_warning(self):
        row = {**ROWS["accounts"][0], "loginExpiresAt": "2026-11-05T23:41:25Z"}
        exp = 1793922085.0
        with mock.patch.object(claude_accounts.time, "time", return_value=exp - 2.2 * 86400):
            a = claude_accounts.account(row)
        self.assertEqual((a["status"], a["attention"]), ("ok", True))  # 값은 그대로 보이고 경고가 붙는다
        self.assertIn("로그인 만료 2일 남음", a["note"])
        with mock.patch.object(claude_accounts.time, "time", return_value=exp - 5 * 3600):
            self.assertIn("5시간 남음", claude_accounts.account(row)["note"])
        for now in (exp - 4 * 86400, exp + 60):  # 아직 멀었거나 이미 지났으면(그때는 cswap 이 relogin_required 를 준다) 경고하지 않는다
            with mock.patch.object(claude_accounts.time, "time", return_value=now):
                a = claude_accounts.account(row)
            self.assertEqual((a["note"], a["attention"]), (None, False))

    def test_odd_numbers(self):
        a = claude_accounts.account({"email": "x@example.com", "usageStatus": "ok",
                                 "usage": {"fiveHour": {"pct": float("inf")}, "sevenDay": {"pct": True}}})
        self.assertEqual((a["windows"], a["status"]), ([], "ok"))


@mock.patch.dict(vars(claude_accounts._state), clear=True)
@mock.patch.object(claude_accounts, "binary", return_value="/home/alice/.local/bin/cswap")
class RunTest(unittest.TestCase):
    def test_missing(self, _):
        # 자동 설치를 끈다(이 기기에 Claude Code 가 있으면 실제로 설치를 시작한다)
        with mock.patch.object(claude_accounts, "binary", return_value=None), \
                mock.patch.object(claude_accounts.config, "CSWAP_INSTALL", False):
            self.assertIsNone(claude_accounts.live(force=True))

    def test_errors_hide_home(self, _):
        cases = ((proc("nope", 2), "종료 코드 2"),
                 (proc({"schemaVersion": 1, "error": {"message": "locked /home/alice/.claude-swap"}}, 1), "hswap 오류(알 수 없는 종류)"),
                 (subprocess.TimeoutExpired(["/home/alice/.local/bin/cswap"], 15), "15초"),
                 (PermissionError(13, "denied", "C:\\Users\\alice\\x"), "PermissionError"))
        for result, want in cases:
            kw = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with mock.patch.object(claude_accounts.subprocess, "run", **kw):
                err = claude_accounts.live(force=True)["error"]
            self.assertIn(want, err)
            self.assertNotIn("alice", err)

    def test_cache_and_empty(self, _):
        with mock.patch.object(claude_accounts.subprocess, "run", return_value=proc(ROWS)) as run:
            a = claude_accounts.live()
            b = claude_accounts.live()
            claude_accounts.live(force=True)
        self.assertIs(a, b)
        self.assertEqual((run.call_count, len(a["accounts"])), (2, 5))  # 30초 안의 두 번째는 캐시, 강제는 다시
        self.assertEqual(run.call_args[0][0][1:], ["claude", "list", "--json"])  # hswap 의 Claude 엔진을 부른다
        with mock.patch.object(claude_accounts.subprocess, "run", return_value=proc({"schemaVersion": 1, "accounts": []})):
            self.assertIsNone(claude_accounts.live(force=True))  # 계정이 없으면 cswap 이 없는 것과 같다

    def test_switch(self, _):
        self.assertEqual([claude_accounts.account(r)["slot"] for r in ROWS["accounts"]], ["1", "2", "3", "4", "5"])
        for bad in ("", "1;rm", "-1", "12345", None):  # 슬롯 번호 말고는 cswap 에 넘기지 않는다
            with mock.patch.object(claude_accounts.subprocess, "run") as run:
                self.assertEqual(claude_accounts.switch(bad)[0], 400)
            run.assert_not_called()
        with mock.patch.object(claude_accounts.subprocess, "run", return_value=proc({"schemaVersion": 1, "switched": True})) as run:
            claude_accounts._state.cache = (1e18, {"accounts": []})
            self.assertEqual(claude_accounts.switch("2"), (200, {"switched": True}))
        self.assertEqual(run.call_args[0][0][1:], ["claude", "switch", "2", "--json"])
        self.assertIsNone(claude_accounts._state.cache)  # 다음 읽기는 바뀐 사용 중 계정을 cswap 에 다시 묻는다
        err = {"schemaVersion": 1, "error": {"type": "AccountNotFoundError", "message": "No account /home/alice/x"}}
        with mock.patch.object(claude_accounts.subprocess, "run", return_value=proc(err, 1)):
            code, body = claude_accounts.switch("9")
        self.assertEqual(code, 409)
        self.assertNotIn("alice", body["error"])


@mock.patch.dict(vars(claude_accounts._state), clear=True)
class InstallTest(unittest.TestCase):
    def setUp(self):
        claude_accounts.installer.state, claude_accounts.installer.error, claude_accounts.installer.at = None, None, 0.0
        self.which = {"claude": "/home/alice/.local/bin/claude", "uv": "/home/alice/.local/bin/uv", "pipx": None, "hswap": None}
        p = mock.patch.object(claude_accounts, "which_local", side_effect=lambda n: self.which[n])
        p.start()
        self.addCleanup(p.stop)

        class Now:  # 설치 스레드를 그 자리에서 실행한다
            def __init__(self, target, **k):
                self.target = target

            def start(self):
                self.target()
        p = mock.patch("jarvis.util.threading.Thread", Now)
        p.start()
        self.addCleanup(p.stop)

    def test_installs_pinned_with_uv_once(self):
        def run(cmd, **k):
            self.which["hswap"] = "/home/alice/.local/bin/hswap"
            return mock.Mock(returncode=0, stderr="", stdout=f"hswap {claude_accounts.VERSION}\n")
        with mock.patch.object(claude_accounts.subprocess, "run", side_effect=run) as r, mock.patch.object(claude_accounts, "_version", None):
            self.assertIsNone(claude_accounts.ensure())  # 그 자리에서 끝났다(실제로는 뒤에서 돌아 '설치 중'이 먼저 보인다)
            self.assertEqual(claude_accounts.status(), {"version": claude_accounts.VERSION, "pinned": claude_accounts.VERSION, "note": None})
        self.assertEqual(r.call_args_list[0][0][0], ["/home/alice/.local/bin/uv", "tool", "install", claude_accounts.PACKAGE])
        # PyPI 배포본을 버전으로 고정한다(최신판을 저절로 받지 않게)
        self.assertEqual(claude_accounts.PACKAGE, f"harness-swap=={claude_accounts.VERSION}")
        self.assertRegex(claude_accounts.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(claude_accounts.installer.state, "done")

    def test_upgrades_only_an_older_hswap(self):
        """covers: USAGE-5. 고정 버전보다 낮으면 같은 설치 명령으로 올리고, 같거나 높거나 버전을 읽지 못했거나 설치를 껐으면 그대로 둔다"""
        self.which["hswap"] = "/home/alice/.local/bin/hswap"
        installed = {}

        def run(cmd, **k):
            if cmd[1:] == ["--version"]:
                return mock.Mock(returncode=0, stderr="", stdout=installed["out"])
            installed["out"] = f"hswap {claude_accounts.VERSION}\n"
            return mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch.object(claude_accounts, "_version", None):
            for out, install in (("hswap 0.0.1\n", True), (f"hswap {claude_accounts.VERSION}\n", False), ("hswap 999.0.0\n", False),
                                 ("", False), ("hswap 0.0.1\n", "off")):
                claude_accounts.installer.state, claude_accounts.installer.error, claude_accounts.installer.at = None, None, 0.0
                installed["out"] = out
                with mock.patch.object(claude_accounts.subprocess, "run", side_effect=run) as r, \
                        mock.patch.object(claude_accounts.config, "CSWAP_INSTALL", install != "off"):
                    self.assertIsNone(claude_accounts.check())
                cmds = [c[0][0] for c in r.call_args_list if c[0][0][1:] != ["--version"]]
                self.assertEqual(cmds, [["/home/alice/.local/bin/uv", "tool", "install", claude_accounts.PACKAGE]] if install is True else [], out)
                after = (installed["out"].split() or [None, None])[1]
                self.assertEqual(claude_accounts.status(), {"version": after, "pinned": claude_accounts.VERSION, "note": None}
                                 if after or install is True else None, out)

    def test_status_reports_a_failed_upgrade(self):
        """covers: USAGE-5. 올리기에 실패하면 기기 행에 그 이유를 보인다(홈 경로는 가린다)"""
        self.which["hswap"] = "/home/alice/.local/bin/hswap"
        old = mock.Mock(returncode=0, stderr="", stdout="hswap 0.0.1\n")
        bad = mock.Mock(returncode=2, stderr="error: /home/alice/.local/bin/hswap.exe is in use\n", stdout="")
        with mock.patch.object(claude_accounts.subprocess, "run", side_effect=[old, bad]), mock.patch.object(claude_accounts, "_version", None):
            note = claude_accounts.check()
            st = claude_accounts.status()
        self.assertEqual((st["version"], st["pinned"], st["note"]), ("0.0.1", claude_accounts.VERSION, note))
        self.assertIn("종료 코드 2", note)
        self.assertNotIn("alice", note)
        with mock.patch.object(claude_accounts, "_version", claude_accounts.VERSION):  # 사람이 직접 올린 뒤에는 지난 실패를 보이지 않는다
            self.assertIsNone(claude_accounts.status()["note"])

    def test_an_install_that_leaves_an_older_hswap_running_is_a_failure(self):
        """covers: USAGE-5. 설치는 끝났는데 실행되는 hswap 이 여전히 낮으면(PATH 앞의 다른 hswap) 실패로 보이고 6시간 뒤에야 다시 한다"""
        self.which["hswap"] = "/home/alice/.local/bin/hswap"
        old = mock.Mock(returncode=0, stderr="", stdout="hswap 0.0.1\n")
        with mock.patch.object(claude_accounts.subprocess, "run", side_effect=lambda cmd, **k: old) as r, \
                mock.patch.object(claude_accounts, "_version", None):
            note = claude_accounts.check()
            self.assertEqual(claude_accounts.check(), note)
            self.assertEqual(claude_accounts.status()["note"], note)
        self.assertEqual(sum(1 for c in r.call_args_list if c[0][0][1:] != ["--version"]), 1)
        self.assertIn("0.0.1", note)
        self.assertEqual(claude_accounts.installer.state, "failed")

    def test_failure_is_reported_and_retried_later(self):
        self.which.update(uv=None, pipx="/usr/bin/pipx")
        bad = mock.Mock(returncode=1, stderr="error: /home/alice/x not writable\n")
        with mock.patch.object(claude_accounts.subprocess, "run", return_value=bad) as r:
            err = claude_accounts.ensure()
            self.assertEqual(claude_accounts.ensure(), err)  # 6시간 안에는 다시 시도하지 않고 실패 이유만 준다
            self.assertEqual(r.call_count, 1)
            self.assertEqual(r.call_args[0][0], ["/usr/bin/pipx", "install", "--force", claude_accounts.PACKAGE])  # 낮은 버전도 다시 깐다
            self.assertIn("종료 코드 1", err)
            self.assertNotIn("alice", err)
            claude_accounts.installer.at -= claude_accounts.installer.RETRY_S + 1
            claude_accounts.ensure()
            self.assertEqual(r.call_count, 2)
        self.assertEqual(err.split("(")[0], "hswap 설치 실패")

    def test_running_message(self):
        class Later:  # 뒤에서 아직 도는 설치
            def __init__(self, target, **k):
                pass

            def start(self):
                pass
        with mock.patch("jarvis.util.threading.Thread", Later):
            self.assertEqual(claude_accounts.ensure(), "hswap 을 설치하는 중입니다(Claude 계정별 사용량)")
            self.assertEqual(claude_accounts.ensure(), "hswap 을 설치하는 중입니다(Claude 계정별 사용량)")

    def test_skipped(self):
        with mock.patch.object(claude_accounts.subprocess, "run") as r:
            self.which["claude"] = None  # Claude Code 가 없으면 cswap 이 쓸모없다
            self.assertIsNone(claude_accounts.ensure())
            self.which["claude"] = "/x/claude"
            with mock.patch.object(claude_accounts.config, "CSWAP_INSTALL", False):
                self.assertIsNone(claude_accounts.ensure())
        r.assert_not_called()
        self.which.update(uv=None, pipx=None)
        self.assertIn("uv·pipx", claude_accounts.ensure())


if __name__ == "__main__":
    unittest.main()
