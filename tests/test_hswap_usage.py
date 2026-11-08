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

    def test_grok_without_windows_shows_plan_and_balance(self):
        """covers: USAGE-3, USAGE-8"""
        # s2 실측(2026-11-08): 창 없이 요금제·잔액·측정 시각만 있다
        grok = acct("grok", state="unknown", source="grok-acp", plan="SuperGrok", detail="Grok reported no included-credit usage",
                    balance={"currency": "USD", "prepaidBalance": 0.0, "onDemandCap": 0.0, "onDemandUsed": 0.0})
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [grok]}], "errors": {}})
        row, = read("grok")
        self.assertEqual((row["status"], row["plan"], row["windows"]), ("ok", "SuperGrok", []))
        self.assertEqual(row["note"], hswap_usage.DETAILS["Grok reported no included-credit usage"])
        self.assertEqual(row["extra"], {"unit": "currency", "spent": 0.0, "limit": 0.0, "currency": "USD", "enabled": False})

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
                    mock.patch.object(hswap_usage.sys, "platform", platform):
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


if __name__ == "__main__":
    unittest.main()
