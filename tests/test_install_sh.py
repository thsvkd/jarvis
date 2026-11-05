"""설치기(install.sh): 가짜 uname·uv·launchctl·systemctl 과 가짜 jarvis 릴리스로 돌린다. 실제 홈·launchd·systemd·Orca 는 건드리지 않는다."""
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUBS = {
    "uname": 'echo Darwin',
    # venv 대신 이 테스트의 python(psutil 이 있다)을 부르는 껍데기. 심링크면 venv 를 벗어난다
    "uv": '''echo "uv $*" >> "$LOG"
if [ "$1" = venv ]; then mkdir -p "$4/bin" && printf '#!/bin/sh\\nexec "%s" "$@"\\n' "$TEST_PY" > "$4/bin/python" && chmod +x "$4/bin/python"; fi''',
    # 로드된 plist 의 작업 폴더를 launchctl print 처럼 보인다
    "launchctl": '''echo "launchctl $*" >> "$LOG"
case "$1" in
  print) [ -f "$HOME/.loaded" ] && cat "$HOME/.loaded" ;;
  bootstrap) if [ -f "$HOME/.bootstrap-fails-once" ]; then rm "$HOME/.bootstrap-fails-once"; exit 5; fi
    python3 -c 'import plistlib, sys; print("working directory = " + plistlib.load(open(sys.argv[1], "rb"))["WorkingDirectory"])' "$3" > "$HOME/.loaded" ;;
  bootout) rm -f "$HOME/.loaded" ;;
esac''',
    "systemctl": '''echo "systemctl $*" >> "$LOG"
case "$2" in is-active) [ -f "$HOME/.active" ] ;; enable) touch "$HOME/.active" ;; esac''',
    "loginctl": 'echo "loginctl $*" >> "$LOG"',
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
        shutil.copy(os.path.join(ROOT, "deploy", "jarvis.service"), os.path.join(src, "deploy"))
        with open(os.path.join(src, "jarvis", "__init__.py"), "w"):
            pass
        with open(os.path.join(src, "jarvis", "__main__.py"), "w") as f:
            f.write(FAKE_MAIN)
        for name in ("launch.py", "relocate.py"):  # 설치기가 부르는 실제 이전 코드
            shutil.copy(os.path.join(ROOT, "jarvis", name), os.path.join(src, "jarvis"))
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
                    "JARVIS_MANIFEST": "file://" + self.manifest, "TEST_PY": sys.executable}
        self.share = os.path.join(self.home, ".local", "share", "jarvis")
        self.app = os.path.join(self.share, "app")
        self.old = os.path.join(self.home, "Workspace", "jarvis")

    def linux(self):
        with open(os.path.join(self.env["PATH"].split(":")[0], "uname"), "w") as f:
            f.write("#!/bin/sh\necho Linux\n")

    def run_install(self, *args):
        return subprocess.run(["sh", os.path.join(ROOT, "install.sh"), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def read(self, *parts):
        with open(os.path.join(self.home, *parts)) as f:
            return f.read()

    def test_fresh_mac_gets_gateway_agent_orca_hooks_and_setup(self):
        """covers: BOOT-1"""
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('core = ["100.115.245.91"]', self.read(".config", "jarvis", "config.toml"))
        agent = self.read("Library", "LaunchAgents", "dev.jarvis.plist")
        self.assertIn(f"<string>{self.home}/.local/share/jarvis/venv/bin/python</string>", agent)
        self.assertIn(f"<key>WorkingDirectory</key><string>{self.app}</string>", agent)
        self.assertNotIn("/Users/USER", agent)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "jarvis", "__main__.py")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Workspace")))
        with open(self.log) as f:
            log = f.read().splitlines()
        self.assertIn("launchctl bootstrap gui/%d %s/Library/LaunchAgents/dev.jarvis.plist" % (os.getuid(), self.home), log)
        self.assertEqual([line for line in log if line.startswith("jarvis ")],
                         ["jarvis module orca plan --json", "jarvis module orca apply --json --plan-id abc123",
                          "jarvis module hooks plan --json", "jarvis module hooks apply --json --plan-id abc123",
                          "jarvis module setup plan --json", "jarvis module setup apply --json --plan-id abc123"])
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
        self.assertFalse(os.path.exists(self.app))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))

    def test_first_install_without_core_writes_nothing(self):
        """covers: BOOT-1"""
        r = self.run_install()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse(os.path.exists(self.log))

    def test_fresh_linux_gets_a_user_unit_on_the_venv_python_and_the_modules(self):
        """covers: BOOT-2"""
        self.linux()
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = self.read(".config", "systemd", "user", "jarvis.service")
        self.assertIn(f"ExecStart={self.home}/.local/share/jarvis/venv/bin/python -m jarvis", unit)
        self.assertIn("WorkingDirectory=%h/.local/share/jarvis/app\n", unit)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "jarvis", "__main__.py")))
        self.assertNotIn("/usr/bin/python3", unit)
        config = self.read(".config", "jarvis", "config.toml")
        self.assertIn('core = ["100.115.245.91"]', config)
        self.assertNotIn("[infra_units]", config)  # Linux 는 기본 유닛 이름을 쓴다
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))
        with open(self.log) as f:
            log = f.read().splitlines()
        self.assertIn("loginctl enable-linger " + os.environ.get("USER", subprocess.check_output(["id", "-un"], text=True).strip()), log)
        self.assertIn("systemctl --user enable --now jarvis", log)
        self.assertNotIn("uv pip install --quiet --python %s/.local/share/jarvis/venv/bin/python psutil pystray pillow" % self.home, log)
        self.assertEqual([line for line in log if line.startswith("jarvis ")],
                         ["jarvis module orca plan --json", "jarvis module orca apply --json --plan-id abc123",
                          "jarvis module hooks plan --json", "jarvis module hooks apply --json --plan-id abc123",
                          "jarvis module setup plan --json", "jarvis module setup apply --json --plan-id abc123"])

    def test_linux_rerun_keeps_the_unit_and_does_not_restart_a_running_gateway(self):
        """covers: BOOT-2"""
        self.linux()
        self.assertEqual(self.run_install("--core", "100.115.245.91").returncode, 0)
        unit = os.path.join(self.home, ".config", "systemd", "user", "jarvis.service")
        with open(unit, "a") as f:
            f.write("# 사용자가 고친 줄\n")
        before = self.read(".config", "systemd", "user", "jarvis.service")
        os.remove(self.log)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(".config", "systemd", "user", "jarvis.service"), before)
        with open(self.log) as f:
            self.assertNotIn("systemctl --user enable --now jarvis", f.read())

    def old_layout(self, git=False):
        """1.8.0 이전 배치: ~/Workspace/jarvis(릴리스 묶음, .git 없음)를 가리키는 등록과 설정이 이미 있다."""
        os.makedirs(os.path.join(self.old, "jarvis"))
        os.makedirs(os.path.join(self.old, "deploy"))
        for name, body in (("__init__.py", ""), ("__main__.py", FAKE_MAIN)):
            with open(os.path.join(self.old, "jarvis", name), "w") as f:
                f.write(body)
        if git:
            os.makedirs(os.path.join(self.old, ".git"))
        config = os.path.join(self.home, ".config", "jarvis", "config.toml")
        os.makedirs(os.path.dirname(config))
        with open(config, "w") as f:
            f.write('core = ["100.115.245.91"]\n')
        with open(os.path.join(ROOT, "deploy", "jarvis.plist")) as f:
            text = f.read().replace("/Users/USER/.local/share/jarvis/app", self.old).replace("/Users/USER", self.home)
        agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        os.makedirs(os.path.dirname(agent))
        with open(agent, "w") as f:
            f.write(text)
        with open(os.path.join(self.home, ".loaded"), "w") as f:
            f.write(f"working directory = {self.old}\n")
        return agent

    @unittest.skipUnless(sys.platform == "darwin", "relocate 는 실제 플랫폼으로 돈다: macOS 흐름")
    def test_an_old_mac_install_moves_to_app_and_launchd_rereads_the_agent(self):
        """covers: BOOT-1, RELOC-10"""
        agent = self.old_layout()
        open(os.path.join(self.home, ".bootstrap-fails-once"), "w").close()  # bootout 직후 bootstrap 이 한 번 실패한다
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "jarvis", "relocate.py")))  # 새 릴리스를 app 에 받았다
        with open(agent, "rb") as f:
            self.assertEqual(plistlib.load(f)["WorkingDirectory"], self.app)
        self.assertTrue(os.path.isfile(os.path.join(self.share, "dev.jarvis.plist.bak-relocate")))
        with open(self.log) as f:
            log = f.read().splitlines()
        uid = os.getuid()
        i = log.index(f"launchctl bootout gui/{uid}/dev.jarvis")
        self.assertEqual(log[i + 1:i + 3], [f"launchctl bootstrap gui/{uid} {agent}"] * 2)  # 한 번 더 했다
        self.assertFalse(os.path.exists(self.old))  # 다시 읽힌 뒤라 옮겼다
        self.assertTrue(os.path.isfile(os.path.join(self.share, "workspace-old", "jarvis", "__init__.py")))
        self.assertIn("jarvis module orca apply --json --plan-id abc123", log)
        self.assertIn("pending_reload", r.stderr)
        self.assertIn("cleaned", r.stdout)

    def test_a_dev_checkout_install_is_left_as_it_is(self):
        """covers: BOOT-1, RELOC-8"""
        agent = self.old_layout(git=True)
        with open(agent, "rb") as f:
            before = f.read()
        os.remove(self.manifest)  # 받지 않는다
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(agent, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(self.app))
        self.assertTrue(os.path.isdir(os.path.join(self.old, ".git")))
        with open(self.log) as f:
            log = f.read()
        self.assertNotIn("bootout", log)
        self.assertNotIn("bootstrap", log)
        self.assertIn("jarvis module orca apply --json --plan-id abc123", log)  # 모듈은 체크아웃의 코드로 돈다

    @unittest.skipUnless(sys.platform.startswith("linux"), "relocate 는 실제 플랫폼으로 돈다: Linux 흐름")
    def test_an_old_linux_install_moves_to_app_without_restarting(self):
        """covers: BOOT-2, RELOC-10"""
        self.linux()
        self.old_layout()
        unit = os.path.join(self.home, ".config", "systemd", "user", "jarvis.service")
        os.makedirs(os.path.dirname(unit))
        with open(os.path.join(ROOT, "deploy", "jarvis.service")) as f:
            body = f.read().replace("%h/.local/share/jarvis/app", "%h/Workspace/jarvis")
        with open(unit, "w") as f:
            f.write(body)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(unit) as f:
            self.assertIn(f"WorkingDirectory={self.app}\n", f.read())
        with open(self.log) as f:
            log = f.read()
        self.assertIn("systemctl --user daemon-reload", log)
        self.assertNotIn("restart", log)

    def test_other_systems_are_refused_before_writing(self):
        """covers: BOOT-2"""
        with open(os.path.join(self.env["PATH"].split(":")[0], "uname"), "w") as f:
            f.write("#!/bin/sh\necho MINGW64_NT\n")
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 1)
        self.assertIn("install.ps1", r.stderr)
        self.assertEqual(os.listdir(self.home), [])


if __name__ == "__main__":
    unittest.main()
