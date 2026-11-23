"""scripts/ 의 개발용 스크립트(lint·run)를 확인한다."""

import importlib.util
import os
import sys
import unittest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, _SCRIPTS)  # run.py 가 deps 를 import 한다


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lint, run = _load("lint"), _load("run")


class LintTest(unittest.TestCase):
    def test_files_go_to_their_vscode_formatter(self):
        got = lint.group(["a.py", "b.mjs", "c.html", "d.sh", "e.ts", "f.json", "g.md"])
        self.assertEqual(got, {"ruff": ["a.py"], "prettier": ["b.mjs", "c.html"], "shfmt": ["d.sh"]})

    def test_ruff_uses_vscode_line_length(self):
        self.assertIn("150", lint.command("ruff", check=False))


class RunTest(unittest.TestCase):
    def test_dev_gateway_never_shares_the_installed_service_paths(self):
        """서비스와 릴리스 폴더를 같이 쓰면 ipc.serve 가 서비스의 IPC 소켓을 지운다(2026-11-23)."""
        env = run.run_env(9390, os.devnull)
        tmp = os.path.join(os.path.dirname(_SCRIPTS), ".tmp-run")
        for key in ("JARVIS_STATE", "JARVIS_RELEASES"):
            self.assertTrue(env[key].startswith(tmp + os.sep), key)
        self.assertEqual((env["JARVIS_BIND"], env["JARVIS_NO_RELEASE"], env["JARVIS_PORT"]), ("127.0.0.1", "1", "9390"))


if __name__ == "__main__":
    unittest.main()
