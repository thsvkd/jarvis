"""Linux·macOS Orca 서버 서비스(orca_serve): 형태 판정, 새 서비스의 내용, 받아들인·바뀐 서비스를 지우지 않는 uninstall."""
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from jarvis import orca_serve


# orca_serve 는 Linux·macOS 전용이다(systemd·launchd·pwd·심링크). Windows 서버는 modules/orca 의 S4U 경로가 맡는다.
POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "Linux·macOS 전용 서비스")


@POSIX_ONLY
class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jarvis-orca-serve-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.cli = os.path.join(self.root, "opt/Orca/resources/bin/orca-ide")
        os.makedirs(os.path.dirname(self.cli))
        open(self.cli, "w").close()
        self.link = os.path.join(self.root, "bin/orca-ide")
        os.makedirs(os.path.dirname(self.link))
        os.symlink(self.cli, self.link)
        self.calls = []
        home = mock.patch.dict(os.environ, {"HOME": self.root, "USERPROFILE": self.root})  # Windows 의 expanduser 는 USERPROFILE
        run = mock.patch.object(orca_serve, "run", lambda argv, timeout=6, sudo=False: (self.calls.append(argv), (0, "", ""))[1])
        for p in (home, run):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("XDG_CONFIG_HOME", None)  # patch.dict 가 끝나면 되돌린다

    def manager(self, argv, env=None):
        m = argv and {"kind": "systemd", "argv": argv, "env": env or {}}
        return mock.patch.object(orca_serve.orca_issue, "manager", lambda pairing=True: m)

    @property
    def profile(self):
        return os.path.join(self.root, ".config/orca")

    def write(self, *args, plat="linux", record=None):
        self.records = []
        return orca_serve.write(*args, on_record=self.records.append, plat=plat, record=record)


class Form(Base):
    def test_a_symlinked_official_cli_with_pairing_flags_is_the_same_shape(self):
        """covers: ORCA-2 — pi5-server2 의 jarvis 유닛(~/.local/bin/orca-ide 심링크, --no-pairing)을 받아들인다."""
        with self.manager([self.link, "serve", "--supervise", "--no-pairing", "--port", "6768", "--pairing-address", "100.64.0.1"]):
            self.assertEqual(orca_serve.form(self.cli, self.profile, "linux"), ("same", None))

    def test_runners_wrappers_and_plain_serve_are_a_different_shape(self):
        """covers: ORCA-2"""
        for argv in (["/usr/bin/python3", "-B", "orca-server-runner.py", "--run", "c.json"],
                     ["/usr/bin/xvfb-run", self.cli, "serve", "--supervise"],
                     [self.cli, "serve", "--port", "6768"]):
            with self.manager(argv):
                self.assertEqual(orca_serve.form(self.cli, self.profile, "linux")[0], "different", argv)

    def test_no_loaded_service_is_missing_but_an_unloaded_file_is_different(self):
        """covers: ORCA-2 — 적재되지 않은 같은 이름의 파일은 고치지 않는다."""
        with self.manager(None):
            self.assertEqual(orca_serve.form(self.cli, self.profile, "linux"), ("missing", None))
            os.makedirs(os.path.dirname(orca_serve.service_path("linux")))
            open(orca_serve.service_path("linux"), "w").close()
            self.assertEqual(orca_serve.form(self.cli, self.profile, "linux")[0], "different")

    def test_a_service_for_another_profile_is_a_different_shape(self):
        """covers: ORCA-2 — 받아들인 서버와 status·갱신이 보는 프로필이 같아야 한다."""
        argv = [self.cli, "serve", "--supervise"]
        with self.manager(argv, {"ORCA_USER_DATA_PATH": os.path.join(self.root, "elsewhere")}):
            shape, why = orca_serve.form(self.cli, self.profile, "linux")
        self.assertEqual(shape, "different")
        self.assertIn("다른 프로필", why)
        with self.manager(argv, {"ORCA_USER_DATA_PATH": self.profile}):
            self.assertEqual(orca_serve.form(self.cli, self.profile, "linux")[0], "same")

    def test_adopted_record_names_the_loaded_launch_agent(self):
        """macOS 의 manager 는 첫 번째로 적재된 orca serve agent 를 준다. 그 파일·label 을 기록한다."""
        m = {"kind": "launchd", "argv": [self.cli, "serve", "--supervise"], "env": {}, "label": "com.example.orca", "plist": "/p.plist"}
        with mock.patch.object(orca_serve.orca_issue, "manager", lambda pairing=True: m):
            record = orca_serve.adopted_record(self.cli, self.profile, "darwin")
        self.assertEqual((record["adopted"], record["service_path"], record["service_label"]), (True, "/p.plist", "com.example.orca"))


class Write(Base):
    env = {"PATH": "/home/u/.local/bin:/usr/bin", "SHELL": "/usr/bin/zsh"}

    def test_systemd_unit_runs_serve_supervise_with_the_login_path_and_starts_it(self):
        """covers: ORCA-2 — 러너·--json 없이, KillMode=process, Restart=on-failure, 로그인 셸의 PATH·SHELL."""
        record = self.write(self.cli, "100.64.0.1", "/home/u/.config/orca", self.env)
        with open(record["service_path"], encoding="utf-8") as f:
            text = f.read()
        self.assertIn(f'ExecStart="{self.cli}" "serve" "--supervise" "--port" "6768" "--pairing-address" "100.64.0.1"', text)
        for line in ('Environment="PATH=/home/u/.local/bin:/usr/bin"', 'Environment="SHELL=/usr/bin/zsh"',
                     'Environment="ORCA_USER_DATA_PATH=/home/u/.config/orca"', "KillMode=process", "Restart=on-failure"):
            self.assertIn(line, text)
        self.assertNotIn("--json", text)
        self.assertNotIn("pairing", text.replace("--pairing-address", ""))
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "orca-serve"]])
        self.assertEqual(record["service_manager"], "systemd-user")
        self.assertEqual([r["state"] for r in self.records], ["configuring", "configured"])

    def test_percent_and_dollar_are_escaped_for_systemd(self):
        text = orca_serve.document("/opt/a$b/orca-ide", "100.64.0.1", "/p%h", {"PATH": "/x%i:/usr/bin"}, "linux")
        self.assertIn('Environment="PATH=/x%%i:/usr/bin"', text)
        self.assertIn('Environment="ORCA_USER_DATA_PATH=/p%%h"', text)
        self.assertIn('ExecStart="/opt/a$$b/orca-ide"', text)

    def test_a_failed_activation_keeps_the_record_and_the_rerun_finishes_it(self):
        """활성화가 실패해도 configuring 기록이 남고, 재실행은 같은 파일을 덮어쓰지 않고 활성화만 이어서 한다."""
        fail = mock.patch.object(orca_serve, "run", lambda argv, timeout=6, sudo=False: (1, "", "Failed to connect to bus"))
        with fail, self.assertRaises(RuntimeError):
            self.write(self.cli, "100.64.0.1", "/p", self.env)
        left = self.records[-1]
        self.assertEqual(left["state"], "configuring")
        self.assertTrue(os.path.isfile(left["service_path"]))
        done = self.write(self.cli, "100.64.0.1", "/p", self.env, record=left)
        self.assertEqual((done["state"], done["service_digest"]), ("configured", left["service_digest"]))
        self.assertEqual(self.calls[-1], ["systemctl", "--user", "enable", "--now", "orca-serve"])

    def test_launch_agent_runs_the_app_cli_directly(self):
        """covers: ORCA-3"""
        record = self.write(self.cli, "100.64.0.5", "/Users/u/Library/Application Support/orca", self.env, plat="darwin")
        with open(record["service_path"], "rb") as f:
            doc = plistlib.load(f)
        self.assertEqual(doc["Label"], "com.stablyai.orca.serve")
        self.assertEqual(doc["ProgramArguments"], [self.cli, "serve", "--supervise", "--port", "6768", "--pairing-address", "100.64.0.5"])
        self.assertEqual(doc["EnvironmentVariables"]["PATH"], self.env["PATH"])
        self.assertTrue(doc["KeepAlive"] and doc["RunAtLoad"])
        self.assertEqual(self.calls[0][:2], ["launchctl", "bootstrap"])

    def test_an_existing_service_file_is_never_overwritten(self):
        """covers: ORCA-2"""
        path = orca_serve.service_path("linux")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("user unit")
        with self.assertRaises(RuntimeError):
            self.write(self.cli, "100.64.0.1", "/p", self.env)
        with open(path) as f:
            self.assertEqual(f.read(), "user unit")
        self.assertEqual(self.calls, [])


class Remove(Base):
    def test_uninstall_keeps_adopted_and_changed_services_and_removes_only_its_own(self):
        """covers: ORCA-11 — 받아들인 서비스는 지우지 않고, 우리가 쓴 뒤 바뀐 파일도 남긴다."""
        self.assertEqual(orca_serve.remove({"adopted": True, "service_path": "/x"}), "kept")
        record = self.write(self.cli, "100.64.0.1", "/p", {"PATH": "/usr/bin"})
        with open(record["service_path"], "a") as f:
            f.write("# user edit\n")
        self.assertEqual(orca_serve.remove(record, plat="linux"), "changed")
        self.assertTrue(os.path.exists(record["service_path"]))
        self.assertEqual(orca_serve.remove({**record, "service_path": "/etc/passwd"}, plat="linux"), "changed")
        os.remove(record["service_path"])
        record = self.write(self.cli, "100.64.0.1", "/p", {"PATH": "/usr/bin"})
        self.calls.clear()
        self.assertEqual(orca_serve.remove(record, dry_run=True, plat="linux"), "removed")
        self.assertTrue(os.path.exists(record["service_path"]) and self.calls == [])
        self.assertEqual(orca_serve.remove(record, plat="linux"), "removed")
        self.assertFalse(os.path.exists(record["service_path"]))
        self.assertEqual(self.calls, [["systemctl", "--user", "disable", "--now", "orca-serve"], ["systemctl", "--user", "daemon-reload"]])


@POSIX_ONLY
class LoginEnv(unittest.TestCase):
    def test_reads_path_after_the_marker_and_ignores_shell_noise(self):
        out = "welcome banner\n\n__jarvis_path__/a/bin:/usr/bin\n"
        seen = []
        account = type("pw", (), {"pw_shell": "/usr/bin/zsh"})
        with mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}), \
                mock.patch("pwd.getpwuid", lambda uid: account), \
                mock.patch.object(orca_serve, "_shell", lambda argv: (seen.append(argv), (0, out))[1]):
            self.assertEqual(orca_serve.login_env(), {"PATH": "/a/bin:/usr/bin", "SHELL": "/usr/bin/zsh"})
        self.assertEqual(seen[0][:2], ["/usr/bin/zsh", "-ilc"])  # jarvis 의 $SHELL 이 아니라 계정의 로그인 셸

    def test_no_path_is_an_error_not_an_empty_service_path(self):
        with mock.patch.object(orca_serve, "_shell", lambda argv: (1, "")):
            with self.assertRaises(RuntimeError):
                orca_serve.login_env()


if __name__ == "__main__":
    unittest.main()
