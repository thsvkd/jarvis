"""macOS 설치기(install.sh): 가짜 uname·uv·launchctl 과 가짜 jarvis 릴리스로 돌린다. 실제 홈·launchd·Orca 는 건드리지 않는다."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUBS = {
    "uname": 'echo Darwin',
    "uv": '''echo "uv $*" >> "$LOG"
if [ "$1" = venv ]; then mkdir -p "$4/bin" && ln -sf "$(command -v python3)" "$4/bin/python"; fi''',
    "launchctl": '''echo "launchctl $*" >> "$LOG"
case "$1" in print) [ -f "$HOME/.loaded" ] ;; bootstrap) touch "$HOME/.loaded" ;; esac''',
}

FAKE_MAIN = '''import json, os, sys
with open(os.environ["LOG"], "a") as f:
    f.write("jarvis " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[3] == "plan":
    print(json.dumps({"plan_id": "abc123", "steps": [{"id": "package", "summary": "install Orca"}], "blocked": []}))
else:
    print(json.dumps({"status": "done", "steps": []}))
'''


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.home)
        os.makedirs(bin_dir)
        for name, body in STUBS.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
        src = os.path.join(self.tmp, "src", "jarvis-9.9.9")
        os.makedirs(os.path.join(src, "jarvis"))
        os.makedirs(os.path.join(src, "deploy"))
        shutil.copy(os.path.join(ROOT, "deploy", "jarvis.plist"), os.path.join(src, "deploy"))
        with open(os.path.join(src, "jarvis", "__init__.py"), "w"):
            pass
        with open(os.path.join(src, "jarvis", "__main__.py"), "w") as f:
            f.write(FAKE_MAIN)
        tgz = os.path.join(self.tmp, "jarvis.tar.gz")
        with tarfile.open(tgz, "w:gz") as t:
            t.add(src, arcname="jarvis-9.9.9")
        with open(tgz, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.manifest = os.path.join(self.tmp, "manifest.json")
        with open(self.manifest, "w") as f:
            f.write('{"version": "9.9.9", "url": "file://%s", "sha256": "%s"}' % (tgz, digest))
        self.log = os.path.join(self.tmp, "log")
        self.env = {"HOME": self.home, "LOG": self.log, "PATH": bin_dir + ":/usr/bin:/bin",
                    "JARVIS_MANIFEST": "file://" + self.manifest}

    def run_install(self, *args):
        return subprocess.run(["sh", os.path.join(ROOT, "install.sh"), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def read(self, *parts):
        with open(os.path.join(self.home, *parts)) as f:
            return f.read()

    def test_fresh_mac_gets_gateway_agent_and_orca(self):
        """covers: BOOT-1"""
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('core = ["100.115.245.91"]', self.read(".config", "jarvis", "config.toml"))
        agent = self.read("Library", "LaunchAgents", "dev.jarvis.plist")
        self.assertIn(f"<string>{self.home}/.local/share/jarvis/venv/bin/python</string>", agent)
        self.assertIn(f"<string>{self.home}/Workspace/jarvis</string>", agent)
        self.assertNotIn("/Users/USER", agent)
        self.assertTrue(os.path.isfile(os.path.join(self.home, "Workspace", "jarvis", "jarvis", "__main__.py")))
        with open(self.log) as f:
            log = f.read().splitlines()
        self.assertIn("launchctl bootstrap gui/%d %s/Library/LaunchAgents/dev.jarvis.plist" % (os.getuid(), self.home), log)
        self.assertEqual([line for line in log if line.startswith("jarvis ")],
                         ["jarvis module orca plan --json", "jarvis module orca apply --json --plan-id abc123"])
        self.assertIn("install Orca", r.stderr)

    def test_rerun_keeps_existing_config_and_does_not_download_again(self):
        """covers: BOOT-1"""
        self.assertEqual(self.run_install("--core", "100.115.245.91").returncode, 0)
        config = os.path.join(self.home, ".config", "jarvis", "config.toml")
        with open(config, "a") as f:
            f.write("# 사용자가 고친 줄\n")
        before = self.read(".config", "jarvis", "config.toml")
        os.remove(self.manifest)  # 저장소가 있으면 다시 받지 않는다
        os.remove(self.log)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".config", "jarvis", "config.toml"), before)
        with open(self.log) as f:
            self.assertNotIn("bootstrap", f.read())

    def test_a_bundle_that_does_not_match_the_manifest_is_not_installed(self):
        """covers: BOOT-1"""
        with open(self.manifest) as f:
            body = json.load(f)
        with open(self.manifest, "w") as f:
            json.dump({**body, "sha256": "0" * 64}, f)
        r = self.run_install("--core", "100.115.245.91")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Workspace", "jarvis")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))

    def test_first_install_without_core_writes_nothing(self):
        """covers: BOOT-1"""
        r = self.run_install()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
