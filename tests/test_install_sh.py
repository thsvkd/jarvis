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

# 설치기의 Linux 이전 흐름을 플랫폼과 상관없이 본다: 첫 호출은 유닛을 app 으로 바꾸고(또는 바꾸지 않고) registered,
# 다음 호출은 cleaned. 실제 판정은 tests/test_relocate.py 가 본다
FAKE_RELOCATE = '''import json, os, sys
log = os.environ["LOG"]
n = sum(1 for l in open(log) if l.startswith("relocate ")) if os.path.exists(log) else 0
with open(log, "a") as f:
    f.write("relocate cwd=%s %s\\n" % (os.getcwd(), " ".join(sys.argv[1:])))
unit = os.path.expanduser("~/.config/systemd/user/jarvis.service")
if n == 0 and os.environ.get("FAKE_RELOCATE") == "changes":
    text = open(unit).read().replace("%h/Workspace/jarvis", os.path.expanduser("~/.local/share/jarvis/app"))
    open(unit, "w").write(text)
state = "registered" if n == 0 else "cleaned"
print(json.dumps({"state": state, "detail": "fake", "steps": []}) if "--json" in sys.argv else state + ": fake")
'''

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
        # 게이트웨이 health 는 바로 답한다고 본다. 그 밖(file:// 매니페스트·묶음)은 실제 curl
        curl = shutil.which("curl", path="/usr/bin:/bin")
        stubs = {**STUBS, "curl": 'case "$*" in *api/health*) echo "curl $*" >> "$LOG" ;; *) exec %s "$@" ;; esac' % curl}
        for name, body in stubs.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
        self.manifest = os.path.join(self.tmp, "manifest.json")
        self.release()
        self.log = os.path.join(self.tmp, "log")
        self.env = {"HOME": self.home, "LOG": self.log, "PATH": bin_dir + ":/usr/bin:/bin",
                    "JARVIS_MANIFEST": "file://" + self.manifest, "TEST_PY": sys.executable,
                    "JARVIS_DAEMON_TEST": "1", "JARVIS_DAEMON_PLIST": os.path.join(self.tmp, "LaunchDaemons", "dev.jarvis.plist")}
        self.share = os.path.join(self.home, ".local", "share", "jarvis")
        self.app = os.path.join(self.share, "app")
        self.old = os.path.join(self.home, "Workspace", "jarvis")

    def release(self, old_templates=False, fake_relocate=False):
        """가짜 최신 릴리스 묶음과 매니페스트. old_templates: 1.8.0 이전 릴리스처럼 템플릿의 작업 폴더가 ~/Workspace/jarvis."""
        src = os.path.join(self.tmp, "src", "jarvis-9.9.9")
        shutil.rmtree(src, ignore_errors=True)
        os.makedirs(os.path.join(src, "jarvis"))
        os.makedirs(os.path.join(src, "deploy"))
        for name, old in (("jarvis.plist", ("/Users/USER/.local/share/jarvis/app", "/Users/USER/Workspace/jarvis")),
                          ("jarvis.service", ("%h/.local/share/jarvis/app", "%h/Workspace/jarvis"))):
            with open(os.path.join(ROOT, "deploy", name)) as f:
                text = f.read()
            with open(os.path.join(src, "deploy", name), "w") as f:
                f.write(text.replace(*old) if old_templates else text)
        with open(os.path.join(src, "jarvis", "__init__.py"), "w") as f:
            f.write('__version__ = "9.9.9"\n')
        with open(os.path.join(src, "jarvis", "__main__.py"), "w") as f:
            f.write(FAKE_MAIN)
        with open(os.path.join(src, "deploy", "macos-daemon.sh"), "w") as f:  # 실제 전환은 tests/test_macos_daemon.py 가 본다
            f.write('echo "macos-daemon cwd=$PWD $*" >> "$LOG"\nexit "${FAKE_DAEMON_RC:-0}"\n')
        for name in ("launch.py", "relocate.py"):  # 설치기가 부르는 실제 이전 코드
            shutil.copy(os.path.join(ROOT, "jarvis", name), os.path.join(src, "jarvis"))
        if fake_relocate:
            with open(os.path.join(src, "jarvis", "relocate.py"), "w") as f:
                f.write(FAKE_RELOCATE)
        tgz = os.path.join(self.tmp, "jarvis.tar.gz")
        with tarfile.open(tgz, "w:gz") as t:
            t.add(src, arcname="jarvis-9.9.9")
        with open(tgz, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(self.manifest, "w") as f:
            f.write('{"version": "9.9.9", "url": "file://%s", "sha256": "%s"}' % (tgz, digest))

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
        self.assertIn(f"WorkingDirectory={self.app}\n", unit)
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
        """1.8.0 이전 배치: ~/Workspace/jarvis(릴리스 묶음, .git 없음)를 가리키는 등록과 설정이 이미 있다.
        옛 base 처럼 relocate.py 가 없다."""
        os.makedirs(os.path.join(self.old, "jarvis"))
        os.makedirs(os.path.join(self.old, "deploy"))
        for name, body in (("__init__.py", '__version__ = "1.7.9"\n'), ("__main__.py", FAKE_MAIN)):
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
        with open(os.path.join(self.app, "jarvis", "__init__.py")) as f:  # app 은 옛 base 의 사본이다(새 릴리스가 아니다)
            self.assertEqual(f.read(), '__version__ = "1.7.9"\n')
        self.assertFalse(os.path.exists(os.path.join(self.app, "jarvis", "relocate.py")))
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

    @unittest.skipUnless(sys.platform == "darwin", "relocate 는 실제 플랫폼으로 돈다: macOS 흐름")
    def test_rerun_after_automatic_relocation_finishes_it_with_new_code(self):
        """covers: RELOC-10"""
        agent = self.old_layout()
        # 게이트웨이가 스스로 옮긴 뒤(app = relocate.py 없는 옛 base, plist = app, launchd 는 아직 옛 plist)
        shutil.copytree(self.old, self.app)
        with open(agent, "rb") as f:
            data = plistlib.load(f)
        data["WorkingDirectory"] = self.app
        with open(agent, "wb") as f:
            plistlib.dump(data, f)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ModuleNotFoundError", r.stderr)
        self.assertIn("pending_reload", r.stderr)
        with open(self.log) as f:
            log = f.read().splitlines()
        self.assertIn(f"launchctl bootout gui/{os.getuid()}/dev.jarvis", log)
        self.assertFalse(os.path.exists(self.old))
        self.assertTrue(os.path.isdir(os.path.join(self.share, "workspace-old")))
        with open(os.path.join(self.app, "jarvis", "__init__.py")) as f:
            self.assertIn("1.7.9", f.read())
        self.assertEqual([n for n in os.listdir(self.share) if n.startswith(".app-tmp-")], [])

    def test_before_the_1_8_release_a_fresh_install_still_points_at_app(self):
        """covers: BOOT-1, BOOT-2"""
        self.release(old_templates=True)  # main 의 install.sh 가 1.7.13 릴리스(템플릿 WD = ~/Workspace/jarvis)를 받는다
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"<key>WorkingDirectory</key><string>{self.app}</string>", self.read("Library", "LaunchAgents", "dev.jarvis.plist"))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Workspace")))
        shutil.rmtree(self.home)
        os.makedirs(self.home)
        self.linux()
        r = self.run_install("--core", "100.115.245.91")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"WorkingDirectory={self.app}\n", self.read(".config", "systemd", "user", "jarvis.service"))

    def old_linux(self, change):
        self.linux()
        self.old_layout()
        unit = os.path.join(self.home, ".config", "systemd", "user", "jarvis.service")
        os.makedirs(os.path.dirname(unit))
        with open(os.path.join(ROOT, "deploy", "jarvis.service")) as f:
            body = f.read().replace("%h/.local/share/jarvis/app", "%h/Workspace/jarvis")
        with open(unit, "w") as f:
            f.write(body)
        with open(os.path.join(self.home, ".config", "jarvis", "config.toml"), "a") as f:
            f.write("port = 9411\n")
        self.release(fake_relocate=True)
        self.env["FAKE_RELOCATE"] = change
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.log) as f:
            return f.read().splitlines()

    def test_linux_restarts_and_relocates_again_when_the_installer_moved_the_unit(self):
        """covers: RELOC-10"""
        log = self.old_linux("changes")
        relocations = [i for i, line in enumerate(log) if line.startswith("relocate ")]
        self.assertEqual(len(relocations), 2)
        restart = log.index("systemctl --user try-restart jarvis")
        health = log.index("curl -fsS http://127.0.0.1:9411/api/health")
        self.assertTrue(relocations[0] < restart < health < relocations[1])
        self.assertTrue(all(log[i].split()[1].endswith("/src") for i in relocations), log)  # 임시 폴더의 새 코드로 돈다
        self.assertNotIn("launchctl bootout", "\n".join(log))

    def test_linux_does_not_restart_when_the_unit_was_not_changed_now(self):
        """covers: RELOC-10, BOOT-2"""
        log = self.old_linux("")
        self.assertEqual(len([line for line in log if line.startswith("relocate ")]), 1)
        self.assertFalse([line for line in log if "restart" in line or "api/health" in line])

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
    def test_an_old_linux_install_moves_to_app_and_restarts_once(self):
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
        self.assertIn("systemctl --user try-restart jarvis", log)  # 이번에 유닛을 바꿨으므로 옛 폴더에서 도는 게이트웨이를 다시 띄운다

    def lines(self):
        with open(self.log) as f:
            return f.read().splitlines()

    def daemon_plist(self):
        path = self.env["JARVIS_DAEMON_PLIST"]
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("daemon")
        return path

    def test_the_daemon_path_override_needs_the_test_flag(self):
        """covers: DAEMON-12"""
        del self.env["JARVIS_DAEMON_TEST"]
        # 첫 설치에 --core 가 없으면 경로를 정한 뒤 아무것도 쓰지 않고 끝난다
        r = subprocess.run(["sh", "-x", os.path.join(ROOT, "install.sh")], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        trace = [line for line in r.stderr.splitlines() if "DAEMON_PLIST=" in line]
        self.assertEqual(trace[-1].lstrip("+ ").strip("'"), "DAEMON_PLIST=/Library/LaunchDaemons/dev.jarvis.plist")

    def test_without_the_flag_the_launchctl_calls_are_the_same_as_before(self):
        """covers: DAEMON-9"""
        self.assertEqual(self.run_install("--core", "100.64.0.1").returncode, 0)
        uid = os.getuid()
        self.assertEqual([line for line in self.lines() if line.startswith("launchctl ")],
                         [f"launchctl print gui/{uid}/dev.jarvis",
                          f"launchctl bootstrap gui/{uid} {self.home}/Library/LaunchAgents/dev.jarvis.plist"])
        self.assertFalse([line for line in self.lines() if line.startswith("macos-daemon")])

    def test_daemon_flag_makes_the_agent_without_loading_it_and_hands_off_before_the_modules(self):
        """covers: DAEMON-9"""
        r = self.run_install("--core", "100.64.0.1", "--daemon")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"<key>WorkingDirectory</key><string>{self.app}</string>", self.read("Library", "LaunchAgents", "dev.jarvis.plist"))
        log = self.lines()
        self.assertFalse([line for line in log if line.startswith("launchctl ")])
        i = log.index(f"macos-daemon cwd={self.home} install")
        self.assertLess(i, log.index("jarvis module orca plan --json"))

    def test_a_failed_hand_off_stops_with_an_error(self):
        """covers: DAEMON-9"""
        self.env["FAKE_DAEMON_RC"] = "1"
        r = self.run_install("--core", "100.64.0.1", "--daemon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("LaunchDaemon 으로 옮기지 못했습니다", r.stderr)
        self.assertFalse([line for line in self.lines() if line.startswith("jarvis ")])

    def test_an_app_older_than_the_installer_hands_off_to_the_downloaded_script(self):
        """covers: DAEMON-9"""
        self.assertEqual(self.run_install("--core", "100.64.0.1").returncode, 0)
        os.remove(os.path.join(self.app, "deploy", "macos-daemon.sh"))
        os.remove(self.log)
        r = self.run_install("--daemon")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"macos-daemon cwd={self.home} install", self.lines())
        self.assertFalse(os.path.exists(os.path.join(self.app, "deploy", "macos-daemon.sh")))  # app 은 그대로 둔다
        self.assertTrue(os.path.isfile(os.path.join(self.share, "daemon-src", "deploy", "macos-daemon.sh")))  # 나중의 uninstall 을 위해 남는다

    def test_a_device_registered_as_a_daemon_gets_no_agent(self):
        """covers: DAEMON-9"""
        self.daemon_plist()
        agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        r = self.run_install("--core", "100.64.0.1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(agent))
        self.assertIn("LaunchAgent 를 만들지 않습니다", r.stdout)
        log = self.lines()
        self.assertFalse([line for line in log if line.startswith(("launchctl ", "macos-daemon"))])
        self.assertIn("jarvis module orca plan --json", log)
        os.remove(self.log)
        r = self.run_install("--daemon")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(agent))
        self.assertIn(f"macos-daemon cwd={self.home} install", self.lines())

    def test_a_dev_checkout_registered_as_a_daemon_downloads_nothing(self):
        """covers: DAEMON-9, BOOT-1"""
        os.remove(self.old_layout(git=True))
        self.daemon_plist()
        os.remove(self.manifest)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.app))
        self.assertIn("jarvis module orca plan --json", self.lines())

    def test_an_old_layout_registered_as_a_daemon_gets_no_release_in_app(self):
        """covers: DAEMON-9"""
        os.remove(self.old_layout())
        self.daemon_plist()
        os.remove(self.manifest)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.app))
        self.assertIn("jarvis module orca plan --json", self.lines())

    @unittest.skipUnless(sys.platform == "darwin", "relocate 는 실제 플랫폼으로 돈다: macOS 흐름")
    def test_a_pending_reload_does_not_load_the_agent_next_to_a_daemon(self):
        """covers: DAEMON-9"""
        agent = self.old_layout()
        shutil.copytree(self.old, self.app)  # 자동 이전 뒤: plist 는 app, launchd 는 아직 옛 plist
        with open(agent, "rb") as f:
            data = plistlib.load(f)
        data["WorkingDirectory"] = self.app
        with open(agent, "wb") as f:
            plistlib.dump(data, f)
        self.daemon_plist()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pending_reload", r.stderr)
        self.assertFalse([line for line in self.lines() if "bootout" in line or "bootstrap" in line])
        self.assertTrue(os.path.isdir(self.old))

    def test_linux_refuses_the_daemon_flag_before_writing(self):
        """covers: DAEMON-9, BOOT-2"""
        self.linux()
        r = self.run_install("--core", "100.64.0.1", "--daemon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("macOS 전용", r.stderr)
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse(os.path.exists(self.log))

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
