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
        """covers: USAGE-2, USAGE-3"""
        balance = {"balance": "0", "hasCredits": False, "unlimited": False}
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", plan="prolite", windows=[CODEX_7D], balance=balance)]}],
                   "errors": {"opencode": "account_list is not supported: OpenCode is not installed"}}, code=1)
        rows = hswap_usage.read("codex")
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

    def test_harness_not_installed_gives_none(self):
        """covers: USAGE-3"""
        self.fake({"schemaVersion": 1, "harnesses": [], "errors": {"opencode": "account_list is not supported: OpenCode is not installed"}}, code=1)
        self.assertIsNone(hswap_usage.read("opencode"))

    def test_top_level_error_and_garbage_give_none(self):
        """covers: USAGE-3"""
        self.fake({"schemaVersion": 1, "error": {"type": "HarnessNotFound", "message": "/Users/someone/secret"}}, code=1)
        self.assertIsNone(hswap_usage.read("grok"))
        with open(self.exe, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho not json\n")
        hswap_usage._jobs.clear()
        self.assertIsNone(hswap_usage.read("grok"))

    def test_states_become_fixed_korean_notes(self):
        """covers: USAGE-3"""
        stale = acct("grok", state="stale", measuredAt=None, detail="token_expiring /Users/someone", windows=[CODEX_7D])
        auth = acct("grok", "b@example.com", state="authentication_required", measuredAt=None, detail="rejected")
        unsupported = acct("grok", "c@example.com", state="unsupported", measuredAt=None, detail="enable with `hswap config set usage.remote`")
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [stale, auth, unsupported]}], "errors": {}})
        s, a, u = hswap_usage.read("grok")
        self.assertEqual((s["status"], s["note"], s["attention"]), ("ok", hswap_usage.STATES["stale"][0], False))  # 이전 값이지 오류가 아니다
        self.assertEqual((a["status"], a["error"], a["attention"]), ("error", hswap_usage.STATES["authentication_required"][0], True))
        self.assertEqual((u["status"], u["error"], u["attention"]), ("error", hswap_usage.STATES["unsupported"][0], False))
        self.assertNotIn("/Users/someone", json.dumps([s, a, u]))  # detail 원문은 내보내지 않는다

    def test_grok_without_windows_shows_plan_and_balance(self):
        """covers: USAGE-3"""
        # s2 실측(2026-11-08): 창 없이 요금제·잔액·측정 시각만 있다
        grok = acct("grok", state="unknown", source="grok-acp", plan="SuperGrok", detail="Grok reported no included-credit usage",
                    balance={"currency": "USD", "prepaidBalance": 0.0, "onDemandCap": 0.0, "onDemandUsed": 0.0})
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "grok", "accounts": [grok]}], "errors": {}})
        row, = hswap_usage.read("grok")
        self.assertEqual((row["status"], row["plan"], row["windows"]), ("ok", "SuperGrok", []))
        self.assertEqual(row["note"], hswap_usage.DETAILS["Grok reported no included-credit usage"])
        self.assertEqual(row["extra"], {"unit": "currency", "spent": 0.0, "limit": 0.0, "currency": "USD", "enabled": False})

    def test_opencode_keeps_console_logins_and_names_unsized_windows(self):
        """covers: USAGE-3"""
        go = acct("opencode", "fixture-label", source="opencode", windows=[
            {"name": "5h", "usedPercent": 10.0, "resetsAt": None, "windowSeconds": 18000, "remaining": None},
            {"name": "monthly", "usedPercent": 40.0, "resetsAt": None, "windowSeconds": None, "remaining": None}])
        go["provider"] = "opencode"
        other = acct("opencode", "x@example.com", state="unsupported", measuredAt=None)
        other["provider"] = "anthropic"
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "opencode", "accounts": [go, other]}], "errors": {}})
        row, = hswap_usage.read("opencode")
        self.assertEqual(row["label"], "이름 없는 계정")  # 이메일이 아닌 라벨(자격 ID 일 수 있다)은 내보내지 않는다
        self.assertEqual([w["key"] for w in row["windows"]], ["session", "monthly"])

    def test_slow_hswap_answers_last_value_and_keeps_reading(self):
        """covers: USAGE-3"""
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[CODEX_7D])]}], "errors": {}})
        first = hswap_usage.read("codex")
        self.fake({"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [acct("codex", windows=[{**CODEX_7D, "usedPercent": 50.0}])]}], "errors": {}}, sleep=1.5)
        with mock.patch.object(hswap_usage, "WAIT_S", 0.2):
            started = time.monotonic()
            self.assertEqual(hswap_usage.read("codex"), first)  # 기다리지 않고 마지막 답
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(hswap_usage.read("codex"), first)  # 진행 중인 읽기에 얹힌다
        self.assertEqual(hswap_usage._jobs["codex"].result(timeout=10)[0]["windows"][0]["used"], 50)
        self.assertEqual(len(self.calls()), 2)

    def test_process_timeout_gives_none(self):
        """covers: USAGE-3"""
        self.fake({"schemaVersion": 1, "harnesses": []}, sleep=5)
        with mock.patch.object(hswap_usage, "TIMEOUT_S", 0.3):
            self.assertIsNone(hswap_usage.read("codex"))

    def test_without_hswap_nothing_runs(self):
        """covers: USAGE-3"""
        with mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=None), \
                mock.patch.object(hswap_usage.subprocess, "run", side_effect=AssertionError("실행 금지")):
            self.assertIsNone(hswap_usage.read("codex"))


class MergeTest(unittest.TestCase):
    def row(self, label, status="ok"):
        return {"label": label, "status": status}

    def test_hswap_values_win_and_fallback_is_not_run(self):
        """covers: USAGE-3"""
        fallback = mock.Mock(side_effect=AssertionError("기존 수집기를 부르지 않는다"))
        rows = [self.row("a@example.com")]
        self.assertEqual(hswap_usage.merge(rows, fallback), rows)

    def test_without_hswap_rows_fallback_is_used_as_is(self):
        """covers: USAGE-3"""
        old = [self.row("기본 계정")]
        self.assertEqual(hswap_usage.merge(None, lambda: old), old)

    def test_valueless_hswap_rows_join_fallback_without_duplicates(self):
        """covers: USAGE-3"""
        rows = [self.row("A@example.com", "error"), self.row("b@example.com", "error")]
        old = [self.row("a@example.com")]
        self.assertEqual(hswap_usage.merge(rows, lambda: old), old + [rows[1]])

    def test_failing_fallback_keeps_hswap_rows(self):
        """covers: USAGE-3"""
        rows = [self.row("b@example.com", "error")]
        self.assertEqual(hswap_usage.merge(rows, mock.Mock(side_effect=RuntimeError("caam 없음"))), rows)


class IndependentTest(FakeHswapCase):
    def setUp(self):
        super().setUp()
        empty = {"status": "unavailable", "accounts": []}
        for p in (mock.patch.object(independent_usage, "_claude", return_value={"accounts": []}),
                  mock.patch.object(independent_usage, "_caam", side_effect=RuntimeError("caam 없음")),
                  mock.patch.object(independent_usage.codex_native_usage, "read", return_value=[{"label": "기본 계정", "status": "ok"}]),
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
        """covers: USAGE-3"""
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
        self.assertEqual([(a["label"], a["source"]) for a in codex["accounts"]], [("a@example.com", "hswap")])  # 기존 Codex 수집기 행과 겹치지 않는다
        self.assertEqual(sorted(self.calls()), ["list codex --json", "list grok --json", "list opencode --json"])
        self.assertIn("grok", data["unavailable"])  # hswap 도 caam 도 없다
        self.assertIn("opencodeGo", data["unavailable"])

    def test_without_hswap_existing_collectors_answer(self):
        """covers: USAGE-3"""
        with mock.patch.object(hswap_usage.claude_accounts, "binary", return_value=None):
            data = independent_usage.read(force=True)
        codex = next(p for p in data["providers"] if p["id"] == "codex")
        self.assertEqual(codex["accounts"], [{"label": "기본 계정", "status": "ok"}])
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
