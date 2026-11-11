"""hswap 이 재는 Codex·Grok·OpenCode 사용량. 임시 HOME 과 가짜 hswap 실행 파일만 쓴다(실제 hswap·하네스에 닿지 않는다)."""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from jarvis import hswap_usage, independent_usage

NOW = time.time()


def iso(at):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))


def acct(harness, label="a@example.com", **usage):
    """s2 의 hswap 0.2.1 실측 모양(계정 키·usage 키)."""
    return {"active": True, "alias": None, "authMode": "chatgpt", "details": {"userId": "user-SECRETID", "accountId": "acct-SECRETID"},
            "harness": harness, "identity": "user-SECRETID/acct-SECRETID", "label": label, "provider": "openai", "slot": 1,
            "usage": {"balance": None, "detail": "", "measuredAt": iso(NOW - 30), "plan": None, "source": "codex-app-server",
                      "state": "available", "windows": [], **usage}}


def read(harness, fallback=list):
    return hswap_usage.read(harness, fallback)


CODEX_7D = {"name": "7d", "usedPercent": 27.0, "resetsAt": iso(NOW + 3 * 86400), "windowSeconds": 604800, "remaining": None}


class FakeHswapCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.exe = os.path.join(self.dir, "hswap")
        for p in (mock.patch.dict(os.environ, {"HOME": self.dir}),
                  mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=self.exe),
                  mock.patch.object(hswap_usage, "_jobs", {}), mock.patch.object(hswap_usage, "_last", {})):
            p.start()
            self.addCleanup(p.stop)

    def fake(self, body, code=0, sleep=0):
        """argv 를 남기고 body 를 JSON 으로 출력한 뒤 code 로 끝나는 가짜 hswap."""
        with open(os.path.join(self.dir, "body.json"), "w", encoding="utf-8") as f:
            json.dump(body, f)
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport json,sys,time\n"
                    f"open({os.path.join(self.dir, 'argv')!r},'a').write(' '.join(sys.argv[1:])+'\\n')\n"
                    f"time.sleep({sleep})\nprint(open({os.path.join(self.dir, 'body.json')!r}).read())\nsys.exit({code})\n")
        os.chmod(self.exe, stat.S_IRWXU)

    def calls(self):
        try:
            with open(os.path.join(self.dir, "argv"), encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


class ReadTest(FakeHswapCase):
    def test_exit_code_1_with_valid_json_is_read_and_ids_are_not_exported(self):
        """covers: USAGE-2, USAGE-3, USAGE-8"""
        balance = {"balance": "0", "hasCredits": False, "unlimited": False}
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", plan="prolite", windows=[CODEX_7D], balance=balance)]}],
                   "errors": {"opencode": "account_list is not supported: OpenCode is not installed"}}, code=1)
        rows = read("codex")
        self.assertEqual(self.calls(), ["list codex --json"])  # 하네스 하나만 묻는다(claude·agy 를 재지 않는다)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["label"], row["status"], row["active"], row["plan"], row["source"]), ("a@example.com", "ok", True, "prolite", "hswap"))
        self.assertEqual(row["windows"], [{"key": "weekly", "used": 27, "resets_at": float(int(NOW + 3 * 86400))}])
        self.assertAlmostEqual(row["updated_at"], NOW - 30, delta=1)
        self.assertEqual(row["extra"], {"unit": "credits", "balance": "0", "unlimited": False, "enabled": False})
        self.assertIsNone(row["note"])
        self.assertEqual(set(row), {"label", "org", "active", "status", "error", "note", "attention", "failure", "plan", "updated_at",
                                    "windows", "buckets", "extra", "credits", "source"})
        self.assertNotIn("SECRETID", json.dumps(rows))

    def test_harness_not_installed_gives_no_rows(self):
        """covers: USAGE-3, USAGE-8"""
        self.fake({"schemaVersion": 1, "harnesses": [], "errors": {"opencode": "account_list is not supported: OpenCode is not installed"}}, code=1)
        self.assertEqual(read("opencode"), [])

    def test_top_level_error_garbage_and_odd_fields_do_not_block_fallback(self):
        """covers: USAGE-3, USAGE-8"""
        old = [{"label": "기본 계정", "status": "ok"}]
        self.fake({"schemaVersion": 1, "error": {"type": "HarnessNotFound", "message": "/Users/someone/secret"}}, code=1)
        self.assertEqual(read("grok", lambda: old), old)
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho not json\n")
        hswap_usage._jobs.clear()
        self.assertEqual(read("grok", lambda: old), old)
        # 문자열이어야 할 필드가 dict·list 여도 예외 없이 무시한다
        odd = acct("grok", state={"x": 1}, detail=["y"], plan=7, windows=[{**CODEX_7D, "name": {}, "windowSeconds": None}])
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [odd]}], "errors": {}})
        hswap_usage._jobs.clear()
        row, base = read("grok", lambda: old)
        self.assertEqual((row["note"], row["plan"], row["windows"][0]["key"], base), (None, None, "기간 미확인", old[0]))
        with mock.patch.object(hswap_usage, "account", side_effect=TypeError("형식 오류")):
            hswap_usage._jobs.clear()
            self.assertEqual(read("grok", lambda: old), old)

    def test_states_become_fixed_korean_notes(self):
        """covers: USAGE-3, USAGE-8"""
        stale = acct("grok", state="stale", measuredAt=None, detail="token_expiring /Users/someone", windows=[CODEX_7D])
        auth = acct("grok", "b@example.com", state="authentication_required", measuredAt=None, detail="rejected")
        unsupported = acct("grok", "c@example.com", state="unsupported", measuredAt=None, detail="enable with `hswap config set usage.remote`")
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [stale, auth, unsupported]}], "errors": {}})
        s, a, u = read("grok")
        self.assertEqual((s["status"], s["note"], s["attention"]), ("ok", hswap_usage.STATES["stale"][0], False))  # 이전 값이지 오류가 아니다
        self.assertEqual((a["status"], a["error"], a["attention"]), ("error", hswap_usage.STATES["authentication_required"][0], True))
        self.assertEqual((u["status"], u["error"], u["attention"]), ("error", hswap_usage.STATES["unsupported"][0], False))
        self.assertNotIn("/Users/someone", json.dumps([s, a, u]))  # detail 원문은 내보내지 않는다
        # 잰 적이 없는 stale(토큰 갱신 대기)은 "이전 값"이 아니다
        empty = hswap_usage.account(acct("grok", state="stale", measuredAt=None, detail="the active login's token is due for refresh"))
        self.assertEqual((empty["status"], empty["error"]), ("error", "아직 잰 값이 없습니다(hswap 이 지금은 재지 않았습니다)."))

    def test_grok_without_credit_percent_is_zero_percent(self):
        """covers: USAGE-3, USAGE-8"""
        # s2 실측(2026-11-08): 창 없이 요금제·잔액·측정 시각만 있다. Grok 의 /usage 는 같은 때 주간 0% 를 보였다(2026-11-09)
        grok = acct("grok", state="unknown", source="grok-acp", plan="SuperGrok", detail="Grok reported no included-credit usage",
                    balance={"currency": "USD", "prepaidBalance": 0.0, "onDemandCap": 0.0, "onDemandUsed": 0.0})
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [grok]}], "errors": {}})
        row, = read("grok")
        self.assertEqual((row["status"], row["plan"], row["note"]), ("ok", "SuperGrok", None))
        self.assertEqual(row["windows"], [{"key": "weekly", "used": 0, "resets_at": None}])
        self.assertEqual(row["extra"], {"unit": "currency", "spent": 0.0, "limit": 0.0, "currency": "USD", "enabled": False})

    def test_grok_without_plan_or_measure_time_stays_unknown(self):
        """covers: USAGE-3, USAGE-8"""
        # 요금제나 측정 시각이 없으면 0% 라고 말할 근거가 없다
        for extra in ({"plan": None}, {"measuredAt": None}):
            grok = acct("grok", **{"state": "unknown", "source": "grok-acp", "plan": "SuperGrok", "detail": "Grok reported no included-credit usage", **extra})
            row = hswap_usage.account(grok)
            self.assertEqual((row["windows"], row["status"]), ([], "error"), extra)

    def test_opencode_keeps_console_logins_and_names_unsized_windows(self):
        """covers: USAGE-3, USAGE-8"""
        go = acct("opencode", "fixture-label", source="opencode", windows=[
            {"name": "5h", "usedPercent": 10.0, "resetsAt": None, "windowSeconds": 18000, "remaining": None},
            {"name": "monthly", "usedPercent": 40.0, "resetsAt": None, "windowSeconds": None, "remaining": None}])
        go["provider"] = "opencode"
        other = acct("opencode", "x@example.com", state="unsupported", measuredAt=None)
        other["provider"] = "anthropic"
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "opencode", "accounts": [go, other]}], "errors": {}})
        row, = read("opencode")
        self.assertEqual(row["label"], "이름 없는 계정")  # 이메일이 아닌 라벨(자격 ID 일 수 있다)은 내보내지 않는다
        self.assertEqual([w["key"] for w in row["windows"]], ["session", "monthly"])

    def test_inactive_console_login_without_a_measurement_is_not_listed(self):
        """covers: USAGE-8. 쉬는 Console 계정은 활성일 때만 재진다: 값 없는 그 행은 오류로 보이지 않게 뺀다(값이 있으면 둔다)"""
        def go(**kw):
            row = acct("opencode", "x", source="opencode", state="stale", measuredAt=None,
                       detail=hswap_usage.INACTIVE_CONSOLE, **kw)
            row["provider"], row["active"] = "opencode", False
            return row
        live = acct("opencode", "y", source="opencode", windows=[
            {"name": "5h", "usedPercent": 2.0, "resetsAt": None, "windowSeconds": 18000, "remaining": None}])
        live["provider"] = "opencode"
        kept = go(windows=[{"name": "5h", "usedPercent": 9.0, "resetsAt": None, "windowSeconds": 18000, "remaining": None}])
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "opencode", "accounts": [live, go(), kept]}], "errors": {}})
        rows = read("opencode")
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"])
        self.assertEqual([r["windows"][0]["used"] for r in rows], [2, 9])

    def test_measured_time_alone_is_not_a_value(self):
        """covers: USAGE-3, USAGE-8"""
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [acct("grok", state="unknown")]}], "errors": {}})
        row, = read("grok")
        self.assertEqual(row["status"], "error")

    def test_alias_is_not_exported(self):
        """covers: USAGE-3, USAGE-8"""
        row = hswap_usage.account({**acct("codex", plan="prolite", windows=[CODEX_7D]), "alias": "private-name"})
        self.assertEqual(row["plan"], "prolite")
        self.assertNotIn("private-name", json.dumps(row))

    def test_slow_hswap_answers_last_value_and_keeps_reading(self):
        """covers: USAGE-3, USAGE-8"""
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[CODEX_7D])]}], "errors": {}})
        first = read("codex")
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[{**CODEX_7D, "usedPercent": 50.0}])]}], "errors": {}}, sleep=1.5)
        with mock.patch.object(hswap_usage, "WAIT_S", 0.2):
            started = time.monotonic()
            self.assertEqual(read("codex"), first)  # 기다리지 않고 마지막 답
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(read("codex"), first)  # 진행 중인 읽기에 얹힌다
            with mock.patch.object(hswap_usage, "LAST_MAX_S", 0):
                self.assertEqual(read("codex", lambda: [{"label": "기본 계정"}]), [{"label": "기본 계정"}])  # 너무 오래된 마지막 답은 버린다
        self.assertEqual(hswap_usage._jobs["codex"].result(timeout=10)[0]["windows"][0]["used"], 50)
        self.assertEqual(len(self.calls()), 2)

    def test_forced_read_refreshes_tokens_before_measuring(self):
        """covers: USAGE-3, USAGE-8"""
        # 화면의 새로고침은 측정 전에 hswap refresh 로 기한이 된 토큰을 먼저 갱신한다(Codex·Grok 만, CLI 가 있을 때만)
        body = {"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [acct("grok", windows=[CODEX_7D])]}], "errors": {}}
        self.fake(body)
        with mock.patch.object(hswap_usage, "which_tool", side_effect=lambda n: f"/x/{n}" if n != "codex" else None):
            read("grok")
            self.assertEqual(self.calls(), ["list grok --json"])  # 보통 읽기는 refresh 하지 않는다
            hswap_usage._jobs.clear()
            hswap_usage.read("grok", list, True)
            self.assertEqual(self.calls()[1:], ["grok refresh --json", "list grok --json"])
            hswap_usage.read("codex", list, True)  # CLI 가 없으면 refresh 하지 않는다
            hswap_usage.read("opencode", list, True)  # OpenCode 는 스스로 로그인을 갱신한다
        self.assertEqual(self.calls()[3:], ["list codex --json", "list opencode --json"])

    def test_forced_read_waits_for_running_read_then_refreshes(self):
        """covers: USAGE-3, USAGE-8"""
        body = {"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [acct("grok", windows=[CODEX_7D])]}], "errors": {}}
        self.fake(body, sleep=0.4)
        with mock.patch.object(hswap_usage, "which_tool", return_value="/x/grok"), mock.patch.object(hswap_usage, "_forced", {}):
            hswap_usage._start("grok")  # 새로고침이 없는 읽기가 도는 중
            started = time.monotonic()
            self.assertEqual(hswap_usage.read("grok", list, True)[0]["windows"][0]["used"], 27)
            self.assertGreater(time.monotonic() - started, 0.8)  # 도는 읽기 뒤에 refresh 와 측정이 이어진다
            self.assertEqual(self.calls(), ["list grok --json", "grok refresh --json", "list grok --json"])
            hswap_usage.read("grok", list, True)  # 이미 새로고침을 담아 끝난 읽기는 새 읽기로 이어진다
            hswap_usage._jobs["grok"].result(timeout=10)

    def test_fallback_runs_while_hswap_is_measuring(self):
        """covers: USAGE-3, USAGE-8"""
        # 첫 읽기: 마지막 답이 없다. 기존 수집기를 hswap 과 동시에 돌려 대기가 더해지지 않는다
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[CODEX_7D])]}], "errors": {}}, sleep=1)
        def slow():
            time.sleep(.5)
            return [{"label": "기본 계정", "status": "ok"}]
        with mock.patch.object(hswap_usage, "WAIT_S", 0.2):
            started = time.monotonic()
            self.assertEqual(read("codex", slow), [{"label": "기본 계정", "status": "ok"}])
            self.assertLess(time.monotonic() - started, 0.9)
            hswap_usage._jobs["codex"].result(timeout=10)
            # 기존 수집기가 hswap 보다 오래 걸리면 그동안 끝난 hswap 답을 쓴다
            def slower():
                time.sleep(1.5)
                return []
            hswap_usage._jobs.clear()
            self.assertEqual(read("codex", slower)[0]["label"], "a@example.com")

    def test_process_timeout_kills_the_tree_without_waiting_for_pipes(self):
        """covers: USAGE-3, USAGE-8"""
        # 가짜 hswap 이 stdout 을 물려받은 손자(하네스 CLI 역할)를 띄운 채 멈춘다
        pid = os.path.join(self.dir, "child.pid")
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport subprocess,sys,time\n"
                    f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                    f"open({pid!r}, 'w').write(str(c.pid))\ntime.sleep(30)\n")
        os.chmod(self.exe, stat.S_IRWXU)
        with mock.patch.object(hswap_usage, "TIMEOUT_S", 1):
            started = time.monotonic()
            self.assertEqual(read("codex"), [])
            self.assertLess(time.monotonic() - started, 5)
        with open(pid, encoding="utf-8") as f:
            child = int(f.read())
        for _ in range(50):  # 고아가 된 손자는 init 이 거둔다
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(.1)
        else:
            self.fail("손자 프로세스가 남았다")

    def test_child_path_starts_with_user_tool_folder(self):
        """covers: USAGE-3, USAGE-8"""
        # 서비스의 짧은 PATH 에서도 hswap 이 사용자 설치 위치의 codex·grok·opencode 를 찾게 한다
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport os\nopen({os.path.join(self.dir, 'argv')!r},'w').write(os.environ['PATH'])\n")
        os.chmod(self.exe, stat.S_IRWXU)
        grok = os.path.join(self.dir, "grok-home")
        for platform, mac in (("linux", []), ("darwin", ["/opt/homebrew/bin"])):  # 이미 PATH 에 있는 /usr/local/bin 은 다시 넣지 않는다
            with mock.patch.dict(os.environ, {"PATH": "/usr/local/bin:/usr/bin:/bin", "GROK_HOME": grok}), \
                    mock.patch.object(sys, "platform", platform):
                hswap_usage._jobs.clear()
                read("codex")
            want = [os.path.join(self.dir, ".local", "bin"), os.path.join(grok, "bin"), os.path.join(self.dir, ".opencode", "bin"),
                    *mac, "/usr/local/bin", "/usr/bin", "/bin"]
            self.assertEqual(self.calls(), [os.pathsep.join(want)])

    def test_without_hswap_nothing_runs(self):
        """covers: USAGE-3, USAGE-8"""
        with mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=None), \
                mock.patch.object(hswap_usage.subprocess, "run", side_effect=AssertionError("실행 금지")):
            self.assertEqual(read("codex"), [])


class MergeTest(unittest.TestCase):
    def row(self, label, status="ok", **kw):
        return {"label": label, "status": status, **kw}

    def test_same_email_uses_hswap_value_and_keeps_other_accounts(self):
        """covers: USAGE-3, USAGE-8"""
        h = self.row("A@example.com", updated_at=10)
        old = [self.row("a@example.com", updated_at=20), self.row("vault@example.com"), self.row("기본 계정")]
        self.assertEqual(hswap_usage.merge([h], old), [h, old[1], old[2]])

    def test_prior_hswap_value_gives_way_to_newer_fallback_value(self):
        """covers: USAGE-3, USAGE-8"""
        # stale 과 error 는 마지막 측정값(창)을 그대로 싣고 온다. detail 이 note 를 바꿔도 상태로 판정한다
        newer, older = self.row("a@example.com", updated_at=NOW), self.row("a@example.com", updated_at=NOW - 7200)
        for state in ("stale", "error"):
            prior = hswap_usage.account(acct("grok", state=state, measuredAt=iso(NOW - 3600), windows=[CODEX_7D],
                                             detail="Grok reported no included-credit usage"))
            shown = {k: v for k, v in prior.items() if k != "_prior"}
            self.assertEqual(hswap_usage.merge([prior], [newer]), [newer], state)
            self.assertEqual(hswap_usage.merge([prior], [older]), [shown], state)
            self.assertIn("_prior", prior, "판정 필드는 사본에서만 지운다(마지막 답을 다시 쓴다)")
        fresh = hswap_usage.account(acct("codex", measuredAt=iso(NOW - 3600), windows=[CODEX_7D]))
        self.assertEqual(hswap_usage.merge([fresh], [newer])[0]["source"], "hswap")  # 지금 잰 값은 시각과 상관없이 hswap 을 쓴다

    def test_valueless_side_gives_way(self):
        """covers: USAGE-3, USAGE-8"""
        h, o = self.row("a@example.com", "error"), self.row("a@example.com")
        self.assertEqual(hswap_usage.merge([h], [o]), [o])
        self.assertEqual(hswap_usage.merge([o], [self.row("a@example.com", "error")]), [o])
        self.assertEqual(hswap_usage.merge([h, self.row("b@example.com", "error")], []), [h, self.row("b@example.com", "error")])

    def test_non_email_labels_are_never_merged(self):
        """covers: USAGE-3, USAGE-8"""
        h, o = self.row("이름 없는 계정"), self.row("이름 없는 계정")
        self.assertEqual(hswap_usage.merge([h], [o]), [h, o])

    def test_active_default_account_is_the_active_hswap_login(self):
        """covers: USAGE-3, USAGE-8"""
        # 네이티브 수집기는 이메일을 모르면 "기본 계정"으로 적는다. hswap 에 활성 계정이 있으면 같은 로그인이다
        h = self.row("a@example.com", active=True, updated_at=10)
        native = self.row("기본 계정", active=True, updated_at=20)
        self.assertEqual(hswap_usage.merge([h, self.row("b@example.com", active=False)], [native]), [h, self.row("b@example.com", active=False)])
        self.assertEqual(hswap_usage.merge([self.row("b@example.com", active=False)], [native]), [self.row("b@example.com", active=False), native])


NATIVE = {"label": "기본 계정", "status": "ok", "active": True, "source": "codex"}  # 네이티브 Codex 수집기: 지금 로그인, 이메일 모름


class IndependentTest(FakeHswapCase):
    def setUp(self):
        super().setUp()
        empty = {"status": "unavailable", "accounts": []}
        for p in (mock.patch.object(independent_usage, "_claude", return_value={"accounts": []}),
                  mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("caam 없음")),
                  mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[NATIVE]),
                  mock.patch.object(independent_usage, "_opencode", return_value=empty),
                  *[mock.patch.object(m, "read", return_value=empty) for m in (independent_usage.antigravity_usage, independent_usage.kimi_usage,
                                                                              independent_usage.zcode_usage, independent_usage.gemini_usage, independent_usage.minimax_usage)],
                  mock.patch.object(independent_usage.cli_login, "available", return_value=[]),
                  mock.patch.object(independent_usage.cli_login, "fresh", return_value=[]),
                  mock.patch.object(independent_usage.claude_accounts, "status", return_value=None)):
            p.start()
            self.addCleanup(p.stop)
        independent_usage._tasks.clear()
        self.addCleanup(independent_usage._tasks.clear)

    def test_hswap_rows_replace_collectors_and_missing_harness_falls_back(self):
        """covers: USAGE-3, USAGE-8"""
        lock = threading.Lock()
        bodies = {"codex": {"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[CODEX_7D])]}], "errors": {}}}
        real = hswap_usage._fetch

        def fetch(exe, harness):  # 하네스마다 다른 답: grok·opencode 는 설치되지 않았다
            with lock:
                self.fake(bodies.get(harness, {"schemaVersion": 1, "harnesses": [], "errors": {harness: "not installed"}}), code=0 if harness in bodies else 1)
                return real(exe, harness)
        with mock.patch.object(hswap_usage, "_fetch", side_effect=fetch):
            data = independent_usage.read(force=True)
        codex = next(p for p in data["providers"] if p["id"] == "codex")
        self.assertEqual([(a["label"], a["source"]) for a in codex["accounts"]], [("a@example.com", "hswap")])  # 같은 로그인의 네이티브 기본 계정 행과 겹치지 않는다
        self.assertEqual(sorted(self.calls()), ["list codex --json", "list grok --json", "list opencode --json"])
        self.assertIn("grok", data["unavailable"])  # hswap 도 caam 도 없다
        self.assertIn("opencodeGo", data["unavailable"])

    def test_without_hswap_existing_collectors_answer(self):
        """covers: USAGE-3, USAGE-8"""
        with mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=None):
            data = independent_usage.read(force=True)
        codex = next(p for p in data["providers"] if p["id"] == "codex")
        self.assertEqual(codex["accounts"], [NATIVE])
        self.assertEqual(self.calls(), [])

class KeepTest(FakeHswapCase):
    def script(self, answers):
        """인자 문자열 → (출력, 종료 코드). 없는 인자는 빈 출력과 0. 받은 인자를 남긴다."""
        with open(os.path.join(self.dir, "answers.json"), "w", encoding="utf-8") as f:
            json.dump(answers, f)
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport json,sys\na=' '.join(sys.argv[1:])\n"
                    f"open({os.path.join(self.dir, 'argv')!r},'a').write(a+'\\n')\n"
                    f"out,code=json.load(open({os.path.join(self.dir, 'answers.json')!r})).get(a,['',0])\nprint(out)\nsys.exit(code)\n")
        os.chmod(self.exe, stat.S_IRWXU)

    def keep(self, clis):
        with mock.patch.object(hswap_usage, "which_tool", side_effect=lambda n: f"/x/{n}" if n in clis else None):
            return hswap_usage.keep(self.exe)

    @staticmethod
    def status(active, known=True):
        return [json.dumps({"schemaVersion": 1, "activeKnown": known, "active": active}), 0]

    def test_sets_empty_remote_registers_current_login_and_refreshes(self):
        """covers: USAGE-9"""
        ok = [json.dumps({"schemaVersion": 1, "accounts": []}), 0]
        self.script({"codex status --json": self.status([]), "grok status --json": self.status([{"slot": 1}]),
                     "codex refresh --json": ok, "grok refresh --json": ok})
        self.assertEqual(self.keep({"codex", "grok", "opencode"}), {"codex": "확인함", "grok": "확인함"})
        self.assertEqual(self.calls(), ["config get usage.remote", "config set usage.remote codex,grok,opencode",
                                        "codex status --json", "codex add", "codex refresh --json",  # 로그인이 hswap 에 없으면 등록한다
                                        "grok status --json", "grok refresh --json"])
        # OpenCode 는 스스로 갱신하고, 네이티브 모드의 add 는 대화형 로그인을 띄워 건드리지 않는다
        self.assertFalse([c for c in self.calls() if c.startswith("opencode")])
        self.assertNotIn("switch", " ".join(self.calls()))

    def test_keeps_the_users_remote_and_does_not_guess_unknown_logins(self):
        """covers: USAGE-9"""
        err = [json.dumps({"schemaVersion": 1, "error": {"type": "Unsupported Operation!", "message": "/Users/someone/x"}}), 1]
        dead = [json.dumps({"schemaVersion": 1, "accounts": [{"ok": True, "result": "token still valid"},
                                                             {"ok": False, "result": "sign in again: /Users/someone"}]}), 1]
        self.script({"config get usage.remote": ["codex", 0], "codex status --json": self.status([], known=False),
                     "codex refresh --json": err, "grok status --json": self.status([{"slot": 1}]), "grok refresh --json": dead})
        done = self.keep({"codex", "grok"})
        self.assertEqual(done, {"codex": "오류(UnsupportedOperation)", "grok": "실패 1개"})  # 원문(message·result)은 쓰지 않는다
        self.assertEqual(self.calls(), ["config get usage.remote", "codex status --json", "codex refresh --json",
                                        "grok status --json", "grok refresh --json"])

    def test_once_waits_for_install_and_missing_hswap(self):
        """covers: USAGE-9"""
        self.script({})
        with mock.patch.object(hswap_usage.claude_accounts.installer, "state", "running"):
            hswap_usage._keep_once()
        with mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=None):
            hswap_usage._keep_once()
        self.assertEqual(self.calls(), [])
        with mock.patch.object(hswap_usage.claude_accounts.installer, "state", None), \
                mock.patch.object(hswap_usage, "which_tool", return_value=None):
            hswap_usage._keep_once()
        self.assertEqual(self.calls(), ["config get usage.remote", "config set usage.remote codex,grok,opencode"])

    def test_maintain_checks_then_keeps_then_reads_claude_each_round(self):
        """covers: USAGE-5, USAGE-9"""
        order = []

        class Stop(Exception):
            pass

        def sleep(s):
            order.append(("sleep", s))
            raise Stop

        for claude, want in (("/x/claude", ["check", "keep", "claude", ("sleep", hswap_usage.KEEP_S)]),
                             (None, ["check", "keep", ("sleep", hswap_usage.KEEP_S)])):
            order.clear()
            with mock.patch.object(hswap_usage.claude_accounts, "check", side_effect=lambda: order.append("check") or 1 / 0), \
                    mock.patch.object(hswap_usage, "_keep_once", side_effect=lambda: order.append("keep")), \
                    mock.patch.object(hswap_usage.claude_accounts, "read", side_effect=lambda: order.append("claude")), \
                    mock.patch.object(hswap_usage, "which_tool", return_value=claude), \
                    mock.patch.object(hswap_usage.time, "sleep", side_effect=sleep):
                with self.assertRaises(Stop):
                    hswap_usage.maintain()
            self.assertEqual(order, want)  # 앞 단계가 실패해도(check 의 예외) 다음 단계를 한다




class ScheduleTest(FakeHswapCase):
    """covers: USAGE-11. macOS 에서 Claude 자동 갱신 예약이 없으면 hswap 이 설치하게 하고, 적재된 예약과 hswap 이 만들지 않은 같은 이름의 서비스는 건드리지 않는다."""

    script = KeepTest.script

    STATUS = "claude keepalive --service-status --json"
    INSTALL = "claude keepalive --install-service --ahead 7200 --json"

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(hswap_usage.sys, "platform", "darwin"),
                  mock.patch.object(hswap_usage, "which_tool", side_effect=lambda n: "/x/" + n),
                  mock.patch.object(hswap_usage.claude_accounts, "_version", "0.2.8"),
                  mock.patch.object(hswap_usage.claude_accounts, "_store_read", return_value={"accounts": [{}]}),
                  mock.patch.object(hswap_usage.claude_accounts, "service", None)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def st(**kw):
        return [json.dumps({"schemaVersion": 1, "platform": "launchd", "installed": True, "managed": True, "loaded": True, **kw}), 0]

    def test_installs_when_missing(self):
        self.script({self.STATUS: self.st(installed=False, managed=None, loaded=False), self.INSTALL: ['{"schemaVersion": 1, "ok": true}', 0]})
        hswap_usage.schedule()
        self.assertEqual(self.calls(), [self.STATUS, self.INSTALL, self.STATUS])

    def test_loaded_managed_service_keeps_the_settings_people_chose(self):
        self.script({self.STATUS: self.st(ahead=3600, thenAuto=True)})
        self.assertEqual(hswap_usage.schedule()["loaded"], True)
        self.assertEqual(self.calls(), [self.STATUS])  # 적재돼 있으면 다시 설치하지 않는다(사람이 고른 ahead·then-auto 를 덮지 않는다)
        self.assertEqual(hswap_usage.claude_accounts.service["managed"], True)

    def test_unloaded_managed_service_is_reinstalled_with_its_own_settings(self):
        again = "claude keepalive --install-service --ahead 3600 --then-auto --json"
        self.script({self.STATUS: self.st(loaded=False, ahead=3600, thenAuto=True), again: ['{"schemaVersion": 1, "ok": true}', 3]})
        hswap_usage.schedule()
        self.assertEqual(self.calls(), [self.STATUS, again, self.STATUS])

    def test_switch_off_and_stale_answers(self):
        self.script({self.STATUS: self.st(installed=False)})
        hswap_usage.claude_accounts.service = {"installed": False}
        with mock.patch.object(hswap_usage.config, "CSWAP_INSTALL", False):
            self.assertIsNone(hswap_usage.schedule())
        self.assertIsNone(hswap_usage.claude_accounts.service)  # 할 일이 아니면 지난 답도 쓰지 않는다
        self.assertEqual(self.calls(), [])

    def test_foreign_service_is_left_alone(self):
        self.script({self.STATUS: self.st(managed=False)})
        hswap_usage.schedule()
        self.assertEqual(self.calls(), [self.STATUS])

    def test_skips_old_hswap_other_platforms_and_empty_stores(self):
        self.script({self.STATUS: self.st(installed=False)})
        for patch in (mock.patch.object(hswap_usage.claude_accounts, "_version", "0.2.7"),
                      mock.patch.object(hswap_usage.sys, "platform", "linux"),
                      mock.patch.object(hswap_usage.claude_accounts, "_store_read", return_value=None)):
            with patch:
                self.assertIsNone(hswap_usage.schedule())
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
