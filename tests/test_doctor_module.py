"""doctor 모듈의 계약 층: 모듈 CLI 로 격리된 홈과 가짜 실행 파일만 본다(실제 홈·하네스는 보지 않는다)."""
import json
import os
import site
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-test-0123456789abcdef"


class DoctorModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.bin = Path(tmp.name) / "bin"
        self.bin2 = Path(tmp.name) / "bin2"
        for d in (self.home, self.bin, self.bin2):
            d.mkdir()
        for d in (self.bin, self.bin2):
            stub = d / "claude"
            stub.write_text("#!/bin/sh\necho '9.9.9 (Claude Code)'\n")
            stub.chmod(0o755)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(f'api_key = "{SECRET}"\n')

    def cli(self, *args):
        env = {**os.environ, "HOME": str(self.home), "PATH": f"{self.bin}{os.pathsep}{self.bin2}{os.pathsep}/usr/bin:/bin",
               "JARVIS_CONFIG": os.devnull, "PYTHONUSERBASE": site.getuserbase()}  # 사용자 site 의 psutil
        r = subprocess.run([sys.executable, "-m", "jarvis", "module", "doctor", *args, "--json"], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        return r.returncode, json.loads(r.stdout), r.stdout + r.stderr

    @unittest.skipIf(sys.platform == "win32", "가짜 실행 파일이 POSIX 셸 스크립트다")
    def test_status_reports_tools_and_findings_by_kind_without_secret_values(self):
        """covers: DOCTOR-7"""
        code, out, raw = self.cli("status")
        self.assertEqual(code, 0, raw)
        self.assertEqual(out["health"], "degraded")
        claude = next(t for t in out["details"]["tools"] if t["id"] == "claude")
        self.assertEqual(claude["version"], "9.9.9 (Claude Code)")
        self.assertEqual(claude["found"], [str(self.bin / "claude"), str(self.bin2 / "claude")])
        missing = next(t for t in out["details"]["tools"] if t["id"] == "codex")
        self.assertEqual(missing["found"], [])
        self.assertTrue(missing["hint"])
        kinds = [f["kind"] for f in out["details"]["findings"]]
        self.assertEqual(kinds, ["duplicate", "secret"])
        self.assertIn("~/.codex/config.toml:1 key api_key", out["details"]["findings"][1]["message"])
        self.assertNotIn(SECRET, raw)

    @unittest.skipIf(sys.platform == "win32", "가짜 실행 파일이 POSIX 셸 스크립트다")
    def test_paths_under_home_are_shown_with_a_tilde(self):
        """covers: DOCTOR-7"""
        local = self.home / ".local" / "bin"
        local.mkdir(parents=True)
        (self.bin / "claude").rename(local / "claude")
        self.bin = local
        code, out, raw = self.cli("status")
        self.assertEqual(code, 0, raw)
        claude = next(t for t in out["details"]["tools"] if t["id"] == "claude")
        self.assertEqual(claude["found"][0], "~/.local/bin/claude")
        self.assertNotIn(str(self.home), raw)

    def test_plan_has_no_steps_and_apply_is_current_or_stale(self):
        """covers: DOCTOR-7"""
        code, plan, raw = self.cli("plan")
        self.assertEqual((code, plan["steps"], plan["blocked"]), (0, [], []), raw)
        code, result, raw = self.cli("apply", "--plan-id", plan["plan_id"])
        self.assertEqual((code, result["status"]), (0, "current"), raw)
        code, result, raw = self.cli("apply", "--plan-id", "0" * 64)
        self.assertEqual((code, result["status"]), (3, "stale"), raw)

    def test_status_writes_nothing_under_home(self):
        """covers: DOCTOR-1"""
        before = sorted(p.relative_to(self.home) for p in self.home.rglob("*"))
        self.cli("status")
        self.assertEqual(sorted(p.relative_to(self.home) for p in self.home.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
