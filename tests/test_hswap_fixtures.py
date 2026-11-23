"""hswap 고정 버전의 실측 list 출력(tests/data/hswap/<버전>/)과 계약 테스트. 파일은 tools/hswap_fixture.py 가 기기 안에서 가린 것이다.
실제 hswap·기기·네트워크에 닿지 않는다. 화면 코드 표(static/app/usage.js)와 서버 코드 집합이 어긋나면 여기서 실패한다."""
import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from jarvis.usage import caam, claude, hswap
from jarvis.util import parse_iso

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data" / "hswap" / claude.VERSION
TOOL = ROOT / "tools" / "hswap_fixture.py"
HARNESSES = ("claude", "codex", "grok", "opencode")

spec = importlib.util.spec_from_file_location("hswap_fixture", TOOL)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def files(harness=None):
    return sorted(p for p in DATA.glob("*.json") if harness is None or p.name.startswith(harness + "-"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def accounts(path, body):
    """픽스처 한 파일 → 수집 시각을 고정하고 진짜 account() 를 태운 (원본 행, 화면 계정) 목록. 하네스 이름은 파일 이름 앞부분이다."""
    harness = path.name.split("-")[0]
    with mock.patch("time.time", return_value=body["_fixture"]["collected_at"]):
        if harness == "claude":
            return [(r, claude.account(r)) for r in body["accounts"]]
        return [(a, hswap.account(a)) for h in body["harnesses"] for a in h["accounts"]]


def fetched(path, body):
    """hswap 파일을 진짜 _fetch 로(_run 만 파일 내용으로 바꿔) 태운 화면 계정들. 거르는 행(OpenCode 의 다른 프로바이더 등)은 빠진다."""
    harness = path.name.split("-")[0]
    with mock.patch("time.time", return_value=body["_fixture"]["collected_at"]), \
            mock.patch.object(hswap, "_run", return_value=(0, path.read_text(encoding="utf-8"))):
        return hswap._fetch("hswap", harness) or []


class ContractTest(unittest.TestCase):
    def test_every_harness_has_a_fixture_for_the_pinned_version(self):
        """covers: USAGE-17 (고정 버전 디렉터리와 하네스마다 하나 이상)"""
        self.assertTrue(DATA.is_dir(), f"{DATA} 이 없다")
        for harness in HARNESSES:
            self.assertTrue(files(harness), f"{harness} 픽스처가 없다")

    def test_every_row_failure_is_in_the_closed_set(self):
        """covers: USAGE-17 (모든 행의 failure 는 코드 표 안이거나 null)"""
        allowed = set(caam.FAILURES) | {None}
        for path in files():
            body = load(path)
            rows = [a for _, a in accounts(path, body)] + (fetched(path, body) if not path.name.startswith("claude-") else [])
            for a in rows:
                self.assertIn(a["failure"], allowed, path.name)

    def test_states_that_were_measured_get_their_codes(self):
        """covers: USAGE-3, USAGE-7, USAGE-17, USAGE-18 (실제로 뜬 상태에만 건 기대값. 파일에 그 상태가 없으면 단언도 없다)"""
        for path in files():
            body = load(path)
            now = body["_fixture"]["collected_at"]
            for raw, a in accounts(path, body):
                where = f"{path.name} {a['label']}"
                if path.name.startswith("claude-"):
                    status, reason = raw.get("usageStatus"), raw.get("usageError")
                    good = parse_iso(raw.get("lastGoodFetchedAt"))
                    if status == "relogin_required" or status == "unavailable" and claude.USAGE_ERRORS.get(reason, (None,))[0] == "auth":
                        self.assertEqual((a["failure"], a["attention"]), ("auth", True), where)
                    elif status == "unavailable" and reason in ("http-429", "refresh-failed") and good and now - good > claude.DEAD_S:
                        self.assertEqual((a["failure"], a["attention"]), ("auth", True), where)
                    elif status == "keychain_unavailable":
                        self.assertEqual(a["failure"], "keychain", where)
                    elif status == "ok" and not 0 < (parse_iso(raw.get("loginExpiresAt") or "") or 0) - now <= claude.LOGIN_WARN_S:
                        self.assertIsNone(a["failure"], where)
                    continue
                u = raw.get("usage") or {}
                state, measured = u.get("state"), parse_iso(u.get("measuredAt"))
                if state == "authentication_required":
                    self.assertEqual(a["failure"], "auth", where)
                elif state == "rate_limited":
                    self.assertEqual((a["failure"], a["attention"]), ("limit", False), where)
                elif state == "unsupported":
                    self.assertEqual(a["failure"], "unsupported", where)
                elif state == "stale" and raw.get("active") is True and (a["status"] == "error" or measured and now - measured > claude.DEAD_S):
                    self.assertEqual((a["failure"], a["attention"]), ("wait", True), where)
                elif state == "available":
                    self.assertIsNone(a["failure"], where)

    def test_every_state_and_status_key_maps_to_a_code(self):
        """covers: USAGE-17 (hswap 상태와 Claude usageStatus 의 모든 키가 코드로 간다)"""
        allowed = set(caam.FAILURES)
        self.assertEqual(set(hswap.CODES), set(hswap.STATES))
        self.assertLessEqual(set(claude.STATUS), set(claude.CODES))
        self.assertLessEqual(set(hswap.CODES.values()), allowed)
        self.assertLessEqual(set(claude.CODES.values()) - {None}, allowed)
        self.assertLessEqual({v[0] for v in claude.USAGE_ERRORS.values()}, allowed)

    def test_screen_code_table_matches_server_codes(self):
        """covers: USAGE-17 (usage.js 의 CODES 키 집합 = FAILURES)"""
        js = (ROOT / "static" / "app" / "usage.js").read_text(encoding="utf-8")
        table = re.search(r"const CODES=\{(.*?)\};", js, re.S)
        self.assertTrue(table, "usage.js 에 const CODES={…}; 가 없다")
        self.assertEqual(set(re.findall(r"(?:^|[{,])\s*([A-Za-z_]+)\s*:\s*\[", table.group(1))), set(caam.FAILURES))

    def test_fixtures_pass_the_output_checks(self):
        """covers: USAGE-17 (픽스처에 토큰·UUID·긴 16진 문자열·실제 이메일 꼴이 없다)"""
        for path in files():
            self.assertEqual(fixture.violations(path.read_text(encoding="utf-8")), {}, path.name)


class RedactTest(unittest.TestCase):
    def run_tool(self, kind, body, **kw):
        return fixture.run(kind, body if isinstance(body, str) else json.dumps(body), {"version": "0.3.1", "argv": "x", "device": "d"}, **kw)

    def hswap_body(self, **usage):
        return {"schemaVersion": 1, "harnesses": [{"harness": "codex", "accounts": [
            {"provider": "openai", "active": True, "label": "Real.Person@gmail.com", "identity": "user-1/acct-2", "details": {"userId": "u"},
             "usage": {"state": "available", "measuredAt": "2026-11-16T00:00:00Z", "windows": [], **usage}}]}]}

    def test_token_shapes_fail_closed(self):
        """covers: USAGE-17 (원본에 토큰 꼴이 있으면 종료 코드 3 이고 표준 출력이 비어 있다)"""
        for bad in ("sk-ant-oat01-abcdef", "eyJhbGciOiJIUzI1NiJ9", "Bearer abc"):
            code, out, err = self.run_tool("hswap", self.hswap_body(detail=bad))
            self.assertEqual((code, out), (3, ""), bad)
            self.assertNotIn(bad, err)
        code, out, _ = self.run_tool("claude", {"schemaVersion": 1, "accounts": [{"number": 1, "refreshToken": "x"}]})
        self.assertEqual((code, out), (3, ""))

    def test_output_only_rules_catch_what_survives_the_allowlist(self):
        """covers: USAGE-17 (남긴 값에 긴 문자열·UUID·실제 이메일이 있으면 3)"""
        for plan in ("A" * 45, "9f1c2d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f", "a@gmail.com", "ab" * 17):
            code, out, _ = self.run_tool("hswap", self.hswap_body(plan=plan))
            self.assertEqual((code, out), (3, ""), plan)

    def test_empty_or_broken_input_exits_2(self):
        """covers: USAGE-17"""
        for bad in ("", "  \n", "{not json", "[1]"):
            self.assertEqual(self.run_tool("hswap", bad)[:2], (2, ""), bad)

    def test_same_email_becomes_the_same_fake_value(self):
        """covers: USAGE-17 (같은 이메일은 같은 가짜 값, 개인 조직 이름도 같은 가짜 이메일을 따른다)"""
        rows = [{"number": n, "email": e, "organizationName": f"{e}'s Organization" if n < 3 else "Acme Corp", "organizationUuid": "11111111-2222-3333-4444-555555555555"}
                for n, e in ((1, "Real@gmail.com"), (2, "real@gmail.com"), (3, "other@corp.io"))]
        code, out, _ = self.run_tool("claude", {"schemaVersion": 1, "accounts": rows})
        self.assertEqual(code, 0)
        a = json.loads(out)["accounts"]
        self.assertEqual(a[0]["email"], a[1]["email"])
        self.assertNotEqual(a[0]["email"], a[2]["email"])
        self.assertEqual(a[0]["organizationName"], f"{a[0]['email']}'s Organization")
        self.assertEqual(a[2]["organizationName"], "Org 1")
        self.assertEqual({x["organizationUuid"] for x in a}, {"00000000-0000-4000-8000-000000000001"})
        self.assertNotIn("gmail", out)

    def test_keys_outside_the_allowlist_are_dropped(self):
        """covers: USAGE-17 (허용 목록 밖의 키·alias 값·새로 생긴 키가 사라진다)"""
        body = self.hswap_body(detail="boom at /Users/someone/.codex/x", plan="Plus", futureKey=1)
        body["harnesses"][0]["errors"] = ["x"]
        code, out, _ = self.run_tool("hswap", body)
        self.assertEqual(code, 0)
        for gone in ("errors", "identity", "details", "futureKey", "Real.Person", "/Users/someone"):
            self.assertNotIn(gone, out)
        got = json.loads(out)
        self.assertEqual(got["harnesses"][0]["accounts"][0]["usage"]["detail"], "")
        self.assertEqual(got["_fixture"]["device"], "d")
        code, out, _ = self.run_tool("hswap", self.hswap_body(detail=hswap.NO_CREDIT_USAGE))
        self.assertEqual(json.loads(out)["harnesses"][0]["accounts"][0]["usage"]["detail"], hswap.NO_CREDIT_USAGE)
        code, out, _ = self.run_tool("claude", {"schemaVersion": 1, "accounts": [{"number": 1, "alias": "내 계정", "futureKey": 1}]})
        self.assertEqual(json.loads(out)["accounts"], [{"number": 1, "alias": None}])

    def test_paths_in_kept_strings_are_masked(self):
        """covers: USAGE-17"""
        code, out, _ = self.run_tool("hswap", self.hswap_body(plan="see /Users/someone/x/y"))
        self.assertEqual(json.loads(out)["harnesses"][0]["accounts"][0]["usage"]["plan"], "see <path>")

    def test_check_mode_echoes_clean_input_and_rejects_dirty(self):
        """covers: USAGE-17 (--check 는 검사만 하고 입력을 그대로 낸다)"""
        clean = files("opencode")[0].read_text(encoding="utf-8")
        self.assertEqual(self.run_tool("hswap", clean, check=True), (0, clean, ""))
        self.assertEqual(self.run_tool("hswap", self.hswap_body(), check=True)[:2], (3, ""))  # 실제 이메일 꼴

    def test_tool_copies_stay_in_step_with_jarvis(self):
        """covers: USAGE-17 (PATH_RE·센티널 문장 복사본이 jarvis 와 같고, 도구는 jarvis 를 import 하지 않는다)"""
        self.assertEqual(fixture.PATH_RE.pattern, hswap.PATH_RE.pattern)
        self.assertEqual(fixture.NO_CREDIT_USAGE, hswap.NO_CREDIT_USAGE)
        self.assertEqual(fixture.INACTIVE_CONSOLE, hswap.INACTIVE_CONSOLE)
        self.assertFalse(re.search(r"^\s*(?:import|from)\s+jarvis", TOOL.read_text(encoding="utf-8"), re.M))


if __name__ == "__main__":
    unittest.main()
