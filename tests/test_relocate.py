"""실행본 이전(relocate): 임시 HOME·임시 ROOT 와 가짜 launchctl·systemctl·powershell 로 돌린다.
실제 홈·launchd·systemd·예약 작업은 건드리지 않는다. 가짜 명령은 부를 때마다 $LOG 에 한 줄을 남긴다."""

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jarvis import launch, relocate  # noqa: E402

# 가짜 서비스 관리자. 로드된 작업 폴더는 $STATE/<종류>-wd 에 둔다
FAKES = {
    "launchctl": r'''
if sys.argv[1] == "print":
    wd = state("launchd-wd")
    if wd is None:
        sys.exit(113)
    print("gui/501/dev.jarvis = {\n\tworking directory = " + wd + "\n}")
''',
    "systemctl": r'''
if "daemon-reload" in sys.argv:
    if state("reload-fails") is not None:
        sys.exit(1)
    unit = open(os.path.join(os.environ["HOME"], ".config/systemd/user/jarvis.service")).read()
    wd = [l.split("=", 1)[1] for l in unit.splitlines() if l.startswith("WorkingDirectory=")][0]
    put("systemd-wd", wd.replace("%h", os.environ["HOME"]))
elif "show" in sys.argv:
    print(state("systemd-wd") or "")
''',
    "powershell": r'''
script = sys.argv[-1]
if "Set-ScheduledTask" in script:
    if state("deny") is not None:
        print("Access is denied.", file=sys.stderr)
        sys.exit(1)
    put("task.json", json.dumps([{"Execute": os.environ["JARVIS_TASK_EXE"], "Arguments": os.environ["JARVIS_TASK_ARGS"],
                                  "WorkingDirectory": os.environ["JARVIS_TASK_WD"]}]))
elif "Get-ScheduledTask" in script:
    print(state("task.json") or "")
''',
}
FAKE_HEAD = '''import json, os, sys
with open(os.environ["LOG"], "a") as f:
    f.write(os.path.basename(sys.argv[0]) + " " + " ".join(sys.argv[1:]) + "\\n")
def state(name):
    try:
        return open(os.path.join(os.environ["STATE"], name)).read().strip()
    except OSError:
        return None
def put(name, value):
    with open(os.path.join(os.environ["STATE"], name), "w") as f:
        f.write(value)
'''


class Base(unittest.TestCase):
    platform = "darwin"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        # $HOME 은 심링크 이름이고 등록 파일은 실제 경로를 적는다(Mac mini 의 /home/x = /Users/x)
        self.link = os.path.join(self.tmp, "homelink")
        os.makedirs(self.home)
        os.symlink(self.home, self.link)
        self.state = os.path.join(self.tmp, "state")
        self.log = os.path.join(self.tmp, "log")
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.state)
        os.makedirs(bin_dir)
        for name, body in FAKES.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write(f"#!{sys.executable}\n" + FAKE_HEAD + body)
            os.chmod(path, 0o755)
        self.root = os.path.join(self.link, ".local", "share", "jarvis")
        self.old = os.path.join(self.home, "Workspace", "jarvis")  # 실제 경로
        self.app = os.path.join(self.root, "app")
        for p in (mock.patch.dict(os.environ, {"HOME": self.link, "USERPROFILE": self.link, "LOG": self.log,
                                               "STATE": self.state, "PATH": bin_dir + os.pathsep + os.environ["PATH"]}),
                  mock.patch.multiple(launch, ROOT=self.root, VERSIONS=os.path.join(self.root, "versions"),
                                      CURRENT=os.path.join(self.root, "current.json")),
                  mock.patch.object(relocate, "PLATFORM", self.platform),
                  mock.patch.object(relocate, "HERE", os.path.join(self.root, "versions", "1.8.0"))):
            p.start()
            self.addCleanup(p.stop)
        self.make_old()

    def make_old(self, git=None):
        os.makedirs(os.path.join(self.old, "jarvis", "__pycache__"))
        os.makedirs(os.path.join(self.old, "deploy"))
        for rel, body in (("jarvis/__init__.py", '__version__ = "1.7.9"\n'), ("jarvis/__pycache__/x.pyc", ""),
                          ("deploy/jarvis-win.pyw", "")):
            with open(os.path.join(self.old, rel), "w") as f:
                f.write(body)
        os.symlink("deploy/jarvis-win.pyw", os.path.join(self.old, "link.pyw"))
        if git == "dir":
            os.makedirs(os.path.join(self.old, ".git"))
        elif git == "file":  # worktree 의 .git 은 파일이다
            with open(os.path.join(self.old, ".git"), "w") as f:
                f.write("gitdir: /elsewhere\n")

    def calls(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def put(self, name, value):
        with open(os.path.join(self.state, name), "w") as f:
            f.write(value)

    def assert_copied(self):
        self.assertTrue(os.path.isfile(os.path.join(self.app, "jarvis", "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "jarvis", "__pycache__")))
        self.assertEqual(os.readlink(os.path.join(self.app, "link.pyw")), "deploy/jarvis-win.pyw")
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(relocate.TMP)], [])

    def assert_moved(self):
        self.assertFalse(os.path.exists(self.old))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "workspace-old", "jarvis", "__init__.py")))


class Mac(Base):
    def setUp(self):
        super().setUp()
        self.agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        os.makedirs(os.path.dirname(self.agent))
        with open(os.path.join(ROOT, "deploy", "jarvis.plist")) as f:
            text = f.read().replace("/Users/USER", self.home)
        data = plistlib.loads(text.replace(f"{self.home}/.local/share/jarvis/app", self.old).encode())
        data["EnvironmentVariables"]["JARVIS_EXTRA"] = self.old + "/static"
        with open(self.agent, "wb") as f:
            plistlib.dump(data, f)
        with open(self.agent, "rb") as f:
            self.original = f.read()
        self.put("launchd-wd", self.old)  # launchd 가 옛 plist 를 로드해 두었다

    def plist(self):
        with open(self.agent, "rb") as f:
            return plistlib.load(f)

    def test_old_layout_is_copied_and_registered_but_kept_until_launchd_reloads(self):
        """covers: RELOC-1, RELOC-3"""
        r = relocate.run()
        self.assertEqual(r["state"], "pending_reload", r)
        self.assert_copied()
        p = self.plist()
        self.assertTrue(relocate.same(p["WorkingDirectory"], self.app))
        self.assertEqual(p["EnvironmentVariables"]["JARVIS_EXTRA"], self.app + "/static")
        self.assertEqual(p["ProgramArguments"][1:], ["-m", "jarvis"])
        with open(self.agent + ".bak-relocate", "rb") as f:
            self.assertEqual(f.read(), self.original)
        self.assertTrue(os.path.isfile(os.path.join(self.old, "jarvis", "__init__.py")))  # 로드된 WD 라 남긴다
        self.assertFalse([c for c in self.calls() if "bootout" in c or "bootstrap" in c or "kickstart" in c])
        # 다시 돌아도 같다(백업을 덮지 않고 복사도 다시 하지 않는다)
        self.assertEqual(relocate.run()["state"], "pending_reload")
        with open(self.agent + ".bak-relocate", "rb") as f:
            self.assertEqual(f.read(), self.original)
        # launchd 가 plist 를 다시 읽으면(로그인·재부팅) 옛 폴더를 옮긴다
        self.put("launchd-wd", self.app)
        r = relocate.run()
        self.assertEqual(r["state"], "cleaned", r)
        self.assert_moved()
        self.assertEqual(relocate.run()["state"], "noop")

    def test_dry_run_changes_nothing(self):
        """covers: RELOC-6"""
        r = relocate.run(dry_run=True)
        self.assertEqual((r["state"], r["dry_run"]), ("planned", True))
        self.assertFalse(os.path.exists(self.app))
        with open(self.agent, "rb") as f:
            self.assertEqual(f.read(), self.original)

    def test_a_registration_elsewhere_is_left_alone(self):
        """covers: RELOC-4"""
        data = self.plist()
        data["WorkingDirectory"] = os.path.join(self.home, "src", "jarvis")
        with open(self.agent, "wb") as f:
            plistlib.dump(data, f)
        self.assertEqual(relocate.run()["state"], "noop")
        os.remove(self.agent)  # pm2·손으로 띄운 경우: 등록 없음
        self.assertEqual(relocate.run()["state"], "noop")
        self.assertFalse(os.path.exists(self.app))
        self.assertTrue(os.path.isdir(self.old))

    def test_a_half_copied_tmp_is_recovered(self):
        """covers: RELOC-5"""
        stale = os.path.join(self.root, relocate.TMP + "dead")
        os.makedirs(os.path.join(stale, "jarvis"))
        self.assertEqual(relocate.run()["state"], "pending_reload")
        self.assert_copied()

    def test_an_app_without_code_is_not_used(self):
        """covers: RELOC-5"""
        os.makedirs(self.app)
        self.assertEqual(relocate.run()["state"], "failed")
        with open(self.agent, "rb") as f:
            self.assertEqual(f.read(), self.original)
        self.assertEqual(os.listdir(self.app), [])


class Linux(Base):
    platform = "linux"
    UNIT = ("[Unit]\nDescription=jarvis\n\n[Service]\nWorkingDirectory=%h/Workspace/jarvis\r\n"
            "ExecStart=%h/.local/share/jarvis/venv/bin/python -m jarvis\n# 사용자가 고친 줄 %h/Workspace/jarvis\n")

    def setUp(self):
        super().setUp()
        self.unit = os.path.join(self.home, ".config", "systemd", "user", "jarvis.service")
        os.makedirs(os.path.dirname(self.unit))
        with open(self.unit, "wb") as f:
            f.write(self.UNIT.encode())
        self.put("systemd-wd", self.old)

    def read_unit(self):
        with open(self.unit, "rb") as f:
            return f.read().decode()

    def test_unit_is_repointed_reloaded_and_cleaned_when_running_from_a_release(self):
        """covers: RELOC-1, RELOC-3"""
        r = relocate.run()
        self.assertEqual(r["state"], "cleaned", r)
        self.assert_copied()
        self.assert_moved()
        self.assertEqual(self.read_unit(), self.UNIT.replace("WorkingDirectory=%h/Workspace/jarvis",
                                                             "WorkingDirectory=" + self.app))
        self.assertIn("systemctl --user daemon-reload", self.calls())
        self.assertEqual(relocate.run()["state"], "noop")

    def test_a_failed_daemon_reload_restores_the_unit_and_removes_the_copy(self):
        """covers: RELOC-1"""
        self.put("reload-fails", "1")
        r = relocate.run()
        self.assertEqual(r["state"], "failed", r)
        self.assertEqual(self.read_unit(), self.UNIT)
        self.assertFalse(os.path.exists(self.app))
        self.assertTrue(os.path.isdir(self.old))

    def test_not_cleaned_while_this_process_runs_the_old_code(self):
        """covers: RELOC-7"""
        with mock.patch.object(relocate, "HERE", self.old):
            r = relocate.run()
        self.assertEqual(r["state"], "registered", r)
        self.assertTrue(os.path.isdir(self.old))
        self.assertTrue(relocate.same(relocate.registered()["dir"], self.app))
        r = relocate.run()  # 다음 기동(이번엔 릴리스에서 돈다)
        self.assertEqual(r["state"], "cleaned", r)

    def test_not_cleaned_while_another_process_works_in_the_old_folder(self):
        """covers: RELOC-7"""
        p = subprocess.Popen(["sleep", "30"], cwd=self.old)
        try:
            for _ in range(50):  # 프로세스의 작업 폴더가 보일 때까지
                if relocate._users(self.old):
                    break
                time.sleep(0.05)
            r = relocate.run()
            self.assertEqual(r["state"], "registered", r)
            self.assertIn(str(p.pid), r["detail"])
        finally:
            p.kill()
            p.wait()
        self.assertEqual(relocate.run()["state"], "cleaned")

    def test_an_absolute_working_directory_is_recognized_too(self):
        """covers: RELOC-1"""
        with open(self.unit, "w") as f:
            f.write(f"[Service]\nWorkingDirectory={self.old}\n")
        self.assertEqual(relocate.run()["state"], "cleaned")


class Windows(Base):
    platform = "win32"

    def setUp(self):
        super().setUp()
        self.task = [{"Execute": "C:/Python313/pythonw.exe", "Arguments": f'"{self.old}/deploy/jarvis-win.pyw"',
                      "WorkingDirectory": self.old}]
        self.put("task.json", json.dumps(self.task))

    def task_now(self):
        with open(os.path.join(self.state, "task.json")) as f:
            return json.load(f)

    def test_task_action_is_repointed(self):
        """covers: RELOC-1, RELOC-3"""
        r = relocate.run()
        self.assertEqual(r["state"], "cleaned", r)
        self.assertEqual(self.task_now(), [{"Execute": "C:/Python313/pythonw.exe",
                                            "Arguments": f'"{self.app}/deploy/jarvis-win.pyw"',
                                            "WorkingDirectory": self.app}])
        self.assert_copied()
        self.assert_moved()
        self.assertTrue(all(c.startswith("powershell -NoProfile") for c in self.calls()))

    def test_a_refused_task_change_leaves_everything_as_it_was(self):
        """covers: RELOC-2"""
        self.put("deny", "1")
        r = relocate.run()
        self.assertEqual(r["state"], "failed", r)
        self.assertIn("Access is denied", r["detail"])
        self.assertEqual(self.task_now(), self.task)
        self.assertFalse(os.path.exists(self.app))
        self.assertTrue(os.path.isdir(self.old))


class DevCheckout(Base):
    """개발 체크아웃(.git)인 기기 7대: 새 릴리스가 아무것도 바꾸지 않아야 한다."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.old)
        self.agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        self.unit = os.path.join(self.home, ".config", "systemd", "user", "jarvis.service")
        for path, body in ((self.agent, plistlib.dumps({"Label": "dev.jarvis", "WorkingDirectory": self.old})),
                           (self.unit, f"[Service]\nWorkingDirectory=%h/Workspace/jarvis\n".encode())):
            os.makedirs(os.path.dirname(path))
            with open(path, "wb") as f:
                f.write(body)
        self.put("task.json", json.dumps([{"Execute": "pythonw.exe", "Arguments": f'"{self.old}/deploy/jarvis-win.pyw"',
                                          "WorkingDirectory": self.old}]))
        for name in ("launchd-wd", "systemd-wd"):
            self.put(name, self.old)

    def snapshot(self):
        out = {}
        for top in (self.home, self.state):
            for d, dirs, files in os.walk(top):
                for n in dirs + files:
                    p = os.path.join(d, n)
                    out[p] = os.lstat(p).st_mtime_ns
        return out

    def test_a_git_checkout_is_never_touched_on_any_platform(self):
        """covers: RELOC-8"""
        for git in ("dir", "file"):
            for platform in ("darwin", "linux", "win32"):
                with self.subTest(git=git, platform=platform):
                    shutil.rmtree(self.old, ignore_errors=True)
                    self.make_old(git)
                    before = self.snapshot()
                    with mock.patch.object(relocate, "PLATFORM", platform):
                        r = relocate.run()
                        self.assertEqual(relocate.run(dry_run=True)["state"], "noop")
                    self.assertEqual(r["state"], "noop")
                    self.assertIn(".git", r["detail"])
                    self.assertEqual(self.calls(), [])
                    self.assertEqual(self.snapshot(), before)
                    self.assertFalse(os.path.exists(self.root))


class Entry(unittest.TestCase):
    def test_confirm_then_relocate_once_and_the_gateway_survives_a_failure(self):
        """covers: RELOC-9"""
        with mock.patch.dict(os.environ, {"JARVIS_NO_RELEASE": "1"}):
            import jarvis.__main__ as m
        order = []
        with mock.patch.object(m.time, "sleep"), \
                mock.patch.object(m.launch, "confirm", side_effect=lambda: order.append("confirm")), \
                mock.patch.object(m.relocate, "run", side_effect=lambda: order.append("relocate") or {"state": "noop", "detail": ""}):
            m._confirm()
        self.assertEqual(order, ["confirm", "relocate"])
        with mock.patch.object(m.time, "sleep"), mock.patch.object(m.launch, "confirm"), \
                mock.patch.object(m.relocate, "run", side_effect=OSError("boom")), \
                mock.patch("builtins.print") as log:
            m._confirm()  # 예외가 스레드 밖으로 나가지 않는다
        self.assertIn("boom", str(log.call_args))


class UpdatePath(Base):
    """옛 배치(~/Workspace/jarvis = 1.7.x 저장소) + 업데이트 버튼이 받은 versions/1.8.0: 저장소의 런처가 새 판으로 넘어가
    새 판의 relocate 가 돈다."""

    @unittest.skipUnless(sys.platform == "darwin", "실제 플랫폼으로 돈다: macOS LaunchAgent 흐름")
    def test_the_old_base_hops_to_the_new_release_which_relocates(self):
        """covers: RELOC-9"""
        shutil.rmtree(self.old)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(os.path.join(ROOT, "jarvis"), os.path.join(self.old, "jarvis"), ignore=ignore)
        with open(os.path.join(self.old, "jarvis", "__init__.py"), "w") as f:
            f.write('__version__ = "1.7.9"\n')
        new = os.path.join(self.root, "versions", "1.8.0")
        shutil.copytree(os.path.join(ROOT, "jarvis"), os.path.join(new, "jarvis"), ignore=ignore)
        with open(os.path.join(new, "jarvis", "__init__.py"), "w") as f:
            f.write('__version__ = "1.8.0"\n')
        launch.write({"version": "1.8.0", "previous": None, "pending": False, "tries": 0})
        agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        os.makedirs(os.path.dirname(agent))
        with open(agent, "wb") as f:
            plistlib.dump({"Label": "dev.jarvis", "WorkingDirectory": self.old}, f)
        self.put("launchd-wd", self.old)
        env = {**os.environ, "JARVIS_CONFIG": os.devnull}
        env.pop("JARVIS_RELEASES", None)
        env.pop("JARVIS_NO_RELEASE", None)

        def cli():
            out = subprocess.run([sys.executable, "-m", "jarvis", "relocate", "--json"], cwd=self.old,
                                 capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout.strip().splitlines()[-1])

        self.assertEqual(cli()["state"], "pending_reload")
        with open(agent, "rb") as f:
            self.assertTrue(relocate.same(plistlib.load(f)["WorkingDirectory"], self.app))
        self.put("launchd-wd", self.app)
        self.assertEqual(cli()["state"], "cleaned")  # 새 판(versions)에서 돌므로 옛 폴더를 옮긴다
        self.assertFalse(os.path.exists(self.old))


if __name__ == "__main__":
    unittest.main()
