"""macOS LaunchDaemon 모드: 렌더(jarvis/macos_daemon.py)와 전환 스크립트(deploy/macos-daemon.sh).
임시 HOME 과 가짜 launchctl·sudo·plutil·curl·lsof 로 돌린다. 실제 launchd·sudo·/Library 는 건드리지 않는다."""
import contextlib
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jarvis import macos_daemon  # noqa: E402

SCRIPT = os.path.join(ROOT, "deploy", "macos-daemon.sh")
UID = "501"

# 상태는 $STATE 의 파일이다: daemon·agent(로드됨), 그 밖의 파일은 실패 주입
# $STATE/tamper(JSON)의 키로 렌더한 plist 를 바꾼다: sudo 비밀번호를 묻는 사이에 같은 uid 프로세스가 임시 파일을 바꾼 경우
TAMPER = """import glob, json, os, plistlib
over = json.load(open(os.path.join(os.environ["STATE"], "tamper")))
for path in glob.glob(os.path.join(os.environ["TMPDIR"], "*", "dev.jarvis.plist")):
    d = plistlib.load(open(path, "rb"))
    d.update(over)
    plistlib.dump(d, open(path, "wb"), sort_keys=False)
"""

STUBS = {
    "uname": 'echo "${FAKE_UNAME:-Darwin}"',
    "id": 'case "$1" in -u) echo "${FAKE_UID:-%s}" ;; *) exec /usr/bin/id "$@" ;; esac' % UID,
    "mktemp": 'exec /usr/bin/mktemp -d "$TMPDIR/x.XXXXXX"',  # macOS 의 mktemp -d 는 TMPDIR 을 따르지 않는다
    "sleep": 'exec /bin/sleep "${FAKE_SLEEP:-0.01}"',  # 대기 횟수는 그대로 세고 시간만 줄인다
    "launchctl": '''echo "launchctl $*" >> "$LOG"
S="$STATE"
case "$1" in
  print)
    case "$2" in
      system/dev.jarvis) [ -f "$S/daemon" ] || exit 113
        # 실제 출력처럼 작업의 path 가 먼저, 안쪽 블록의 path 가 뒤에 나온다
        if [ -f "$S/daemon-path" ]; then cat "$S/daemon-path"; else printf '\tpath = %s\n' "$JARVIS_DAEMON_PLIST"; fi
        printf '\t\tpath = /somewhere/nested\n'
        if [ -f "$S/daemon-not-running" ]; then printf '\\tstate = spawn scheduled\\n'; else printf '\\tstate = running\\n'; fi ;;
      gui/*/dev.jarvis) [ -f "$S/agent" ] || exit 113 ;;
    esac ;;
  bootstrap)
    case "$2" in
      system) [ -n "${AS_ROOT:-}" ] || exit 1
        if [ -f "$S/bootstrap-fails-once" ]; then rm "$S/bootstrap-fails-once"; exit 5; fi
        [ ! -f "$S/bootstrap-fails" ] || exit 5
        [ -f "$3" ] || exit 2
        touch "$S/daemon"
        if [ -f "$S/sudo-lost-after-bootstrap" ]; then touch "$S/sudo-lost"; fi ;;
      gui/*) [ ! -f "$S/no-gui" ] || exit 125
        [ -f "$3" ] || exit 2
        touch "$S/agent" ;;
    esac ;;
  bootout)
    case "$2" in
      system/*) [ -n "${AS_ROOT:-}" ] || exit 1; [ ! -f "$S/daemon-bootout-fails" ] || exit 1; rm -f "$S/daemon" ;;
      gui/*) [ ! -f "$S/agent-bootout-fails" ] || exit 1; rm -f "$S/agent" ;;
    esac ;;
esac''',
    # -v 외에는 -n 만 받는다: 비밀번호를 다시 묻는 호출을 잡는다
    "sudo": '''echo "sudo $*" >> "$LOG"
if [ "$1" = -v ]; then
  if [ -f "$STATE/tamper" ]; then "$TEST_PY" "$STATE/tamper.py"; fi
  [ ! -f "$STATE/sudo-denied" ]; exit
fi
[ "$1" = -n ] || { echo "sudo 가 다시 물었습니다" >&2; exit 1; }
[ ! -f "$STATE/sudo-lost" ] || exit 1
shift
[ "$1" = -v ] && exit 0
export AS_ROOT=1
case "$1" in
  /bin/launchctl) shift; exec "$JARVIS_DAEMON_TEST_BIN/launchctl" "$@" ;;
  /bin/rm) exec "$@" ;;
  /usr/bin/install) [ ! -f "$STATE/install-fails" ] || exit 1; shift 7; exec /bin/cp "$1" "$2" ;;
  *) echo "모르는 sudo 명령: $*" >&2; exit 1 ;;
esac''',
    "plutil": '''case "$1" in
  -lint) [ ! -f "$STATE/lint-fails" ] ;;
  -extract) exec "$TEST_PY" -c 'import plistlib, sys
d = plistlib.load(open(sys.argv[2], "rb"))
if sys.argv[1] not in d: sys.exit(1)
v = d[sys.argv[1]]
print(str(v).lower() if isinstance(v, bool) else v)' "$2" "$6" ;;
  -convert) exec "$TEST_PY" -c 'import plistlib, sys
sys.stdout.buffer.write(plistlib.dumps(plistlib.load(open(sys.argv[1], "rb"))))' "$5" ;;
  *) exit 2 ;;
esac''',
    # 가짜 sudo 는 root 가 아니므로 설치된 파일의 소유·권한은 실패를 주입할 때만 다르게 보인다
    "stat": '''if [ -f "$STATE/stat-output" ]; then cat "$STATE/stat-output"; else echo "root:wheel 644"; fi''',
    "lsof": '''echo "lsof $*" >> "$LOG"
[ -f "$STATE/agent" ] || [ -f "$STATE/daemon" ] || [ -f "$STATE/port-held" ]''',
    "curl": '''case "$*" in
  *api/health*) echo "curl $*" >> "$LOG"
    if [ -f "$STATE/daemon" ]; then [ ! -f "$STATE/daemon-unhealthy" ]; else [ -f "$STATE/agent" ]; fi ;;
  *) exec /usr/bin/curl "$@" ;;
esac''',
}


class Render(unittest.TestCase):
    AGENT = {
        "Label": "dev.jarvis",
        "ProgramArguments": ["/custom/python", "-m", "jarvis"],
        "WorkingDirectory": "/custom/app",
        "EnvironmentVariables": {"PATH": "/custom/bin", "JARVIS_PORT": "9411", "JARVIS_TRAY": "1", "HOME": "/wrong"},
        "ThrottleInterval": 5,
        "RunAtLoad": False,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": "/custom/out.log",
        "StandardErrorPath": "/custom/err.log",
    }

    def render(self, agent=None):
        return macos_daemon.render(agent or self.AGENT, "someone", "staff", "/Users/someone", "/Users/someone/Library/Logs/jarvis.log")

    def test_keeps_user_values_and_changes_only_what_a_daemon_needs(self):
        """covers: DAEMON-1"""
        d = self.render()
        self.assertEqual(d["Label"], "dev.jarvis")
        self.assertEqual(d["WorkingDirectory"], "/custom/app")
        self.assertEqual(d["ThrottleInterval"], 5)
        self.assertEqual((d["UserName"], d["GroupName"], d["InitGroups"]), ("someone", "staff", True))
        self.assertIs(d["RunAtLoad"], True)
        self.assertIs(d["KeepAlive"], True)
        self.assertEqual((d["StandardOutPath"], d["StandardErrorPath"]), ("/dev/null", "/dev/null"))
        env = d["EnvironmentVariables"]
        self.assertEqual(env["PATH"], "/custom/bin")
        self.assertEqual(env["JARVIS_PORT"], "9411")
        self.assertNotIn("JARVIS_TRAY", env)
        self.assertEqual((env["HOME"], env["USER"], env["LOGNAME"]), ("/Users/someone", "someone", "someone"))
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(self.AGENT["EnvironmentVariables"]["JARVIS_TRAY"], "1")  # 입력을 고치지 않는다

    def test_only_the_allowed_keys_reach_the_daemon(self):
        """covers: DAEMON-1"""
        allowed = {"ProcessType": "Background", "SoftResourceLimits": {"NumberOfFiles": 4096}, "HardResourceLimits": {"NumberOfFiles": 8192},
                   "Nice": 5, "ExitTimeOut": 8}
        extra = {"Sockets": {"a": {"SockServiceName": "22"}}, "MachServices": {"x": True}, "UserName": "root", "GroupName": "wheel",
                 "Umask": 0, "RootDirectory": "/tmp", "LaunchOnlyOnce": True}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = self.render({**self.AGENT, **allowed, **extra})
        for k, v in allowed.items():
            self.assertEqual(d[k], v)
        for k in ("Sockets", "MachServices", "Umask", "RootDirectory", "LaunchOnlyOnce"):
            self.assertNotIn(k, d)
            self.assertIn(k, err.getvalue())
        self.assertEqual((d["UserName"], d["GroupName"]), ("someone", "staff"))  # 입력의 값이 아니다
        self.assertIn("UserName", err.getvalue())
        self.assertLessEqual(set(d), macos_daemon.KEEP | {"UserName", "GroupName", "InitGroups", "StandardOutPath", "StandardErrorPath"})

    def test_keys_a_normal_agent_plist_has_are_dropped_silently(self):
        """covers: DAEMON-1"""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.render()
            self.render(macos_daemon.template("/Users/someone"))
            self.render({"Label": "dev.jarvis", "Program": "/custom/python"})
        self.assertEqual(err.getvalue(), "")

    def test_an_existing_lang_is_kept(self):
        """covers: DAEMON-1"""
        agent = {**self.AGENT, "EnvironmentVariables": {"LANG": "ko_KR.UTF-8"}}
        self.assertEqual(self.render(agent)["EnvironmentVariables"]["LANG"], "ko_KR.UTF-8")

    def test_home_and_log_come_from_the_account_not_from_the_shell(self):
        """covers: DAEMON-1"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        agent = {**self.AGENT, "ProgramArguments": ["/home/someone/venv/bin/python", "-m", "jarvis"],
                 "WorkingDirectory": "/home/someone/app", "EnvironmentVariables": {"PATH": "/home/someone/bin"}}
        path = os.path.join(tmp, "agent.plist")
        with open(path, "wb") as f:
            plistlib.dump(agent, f)
        stdout = io.TextIOWrapper(io.BytesIO())
        account = types.SimpleNamespace(pw_name="someone", pw_dir="/Users/someone")
        # 셸의 HOME 은 autofs 경로다(macOS auto_home)
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}), mock.patch("pwd.getpwuid", return_value=account), \
                mock.patch.object(sys, "stdout", stdout):
            self.assertEqual(macos_daemon.main(["render", "--agent", path]), 0)
        stdout.flush()
        d = plistlib.loads(stdout.buffer.getvalue())
        env = d["EnvironmentVariables"]
        self.assertEqual((env["HOME"], env["JARVIS_LOG"]), ("/Users/someone", "/Users/someone/Library/Logs/jarvis.log"))
        self.assertEqual((env["USER"], env["LOGNAME"], d["UserName"]), ("someone", "someone", "someone"))
        # 에이전트 plist 의 다른 값은 손대지 않는다
        self.assertEqual(env["PATH"], "/home/someone/bin")
        self.assertEqual(d["WorkingDirectory"], "/home/someone/app")
        self.assertEqual(d["ProgramArguments"][4:], agent["ProgramArguments"])

    def test_a_plist_round_trip_is_identical(self):
        """covers: DAEMON-1"""
        d = self.render()
        self.assertEqual(plistlib.loads(plistlib.dumps(d, sort_keys=False)), d)

    def test_program_key_is_folded_into_the_wrapped_argv(self):
        """covers: DAEMON-1"""
        d = self.render({"Label": "dev.jarvis", "Program": "/custom/python", "ProgramArguments": ["argv0", "-m", "jarvis"]})
        self.assertNotIn("Program", d)
        self.assertEqual(d["ProgramArguments"][4:], ["/custom/python", "-m", "jarvis"])
        with self.assertRaises(ValueError):
            self.render({"Label": "dev.jarvis"})

    def test_secret_looking_environment_is_refused(self):
        """covers: DAEMON-1"""
        for name in ("JARVIS_PROXY_KEY", "MY_TOKEN", "API_KEY", "OPENAI_APIKEY", "AWS_ACCESS_KEY_ID", "PRIVATE_KEY_PEM",
                     "GITHUB_PAT", "BASIC_AUTH", "DB_PASS", "SESSION_COOKIE", "db_password"):
            with self.subTest(name), self.assertRaisesRegex(ValueError, name):
                self.render({**self.AGENT, "EnvironmentVariables": {name: "x"}})
        for name in ("JARVIS_PORT", "SSH_AUTH_SOCK", "KEYCHAIN_PATH", "BYPASS_CACHE", "PATH", "HOMEBREW_PREFIX"):
            with self.subTest(name):
                self.render({**self.AGENT, "EnvironmentVariables": {name: "x"}})

    def test_secret_looking_arguments_are_refused_without_printing_them(self):
        """covers: DAEMON-1"""
        with self.assertRaises(ValueError) as e:
            self.render({**self.AGENT, "ProgramArguments": ["/custom/python", "--token=hunter2"]})
        self.assertNotIn("hunter2", str(e.exception))

    def test_the_wrapper_execs_with_the_command_line_core_checks_and_appends_the_log(self):
        """covers: DAEMON-1"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, "jarvis"))
        with open(os.path.join(tmp, "jarvis", "__main__.py"), "w") as f:
            f.write("import os, subprocess, sys\n"
                    "print(subprocess.check_output(['ps', '-o', 'command=', '-p', str(os.getpid())], text=True).strip())\n"
                    "print('stderr', file=sys.stderr)\n"
                    "print('stdin=' + repr(sys.stdin.read()))\n")
        log = os.path.join(tmp, "jarvis.log")
        with open(log, "w") as f:
            f.write("before\n")
        d = macos_daemon.render({"ProgramArguments": [sys.executable, "-m", "jarvis"]}, "someone", "staff", tmp, log)
        argv = d["ProgramArguments"]
        self.assertEqual(argv[:2], ["/bin/sh", "-c"])
        r = subprocess.run(argv, env={"JARVIS_LOG": log, "PATH": "/usr/bin:/bin"}, cwd=tmp, capture_output=True, text=True,
                           input="ignored", timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        with open(log) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "before")
        self.assertTrue([line for line in lines if line.endswith("-m jarvis")], lines)  # core 의 `grep -- "-m jarvis$"`
        self.assertFalse([line for line in lines if "sh -c" in line], lines)
        self.assertIn("stderr", lines)
        self.assertIn("stdin=''", lines)

    def test_the_template_gets_the_same_substitution_as_install_sh(self):
        """covers: DAEMON-2"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        data = macos_daemon.base(None, tmp)  # 에이전트 plist 가 없다
        app = os.path.join(tmp, ".local/share/jarvis/app")
        self.assertEqual(data["WorkingDirectory"], app)
        self.assertEqual(data["ProgramArguments"], [os.path.join(tmp, ".local/share/jarvis/venv/bin/python"), "-m", "jarvis"])
        self.assertNotIn("/Users/USER", plistlib.dumps(data).decode())
        d = macos_daemon.render(data, "someone", "staff", tmp, os.path.join(tmp, "x.log"))
        self.assertNotIn("JARVIS_TRAY", d["EnvironmentVariables"])
        self.assertEqual(d["EnvironmentVariables"]["JARVIS_PORT"], "9300")

    def test_port_prefers_the_plist_then_the_config_file(self):
        """covers: DAEMON-2"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        self.assertEqual(macos_daemon.port({}, tmp), "9300")
        os.makedirs(os.path.join(tmp, ".config", "jarvis"))
        with open(os.path.join(tmp, ".config", "jarvis", "config.toml"), "w") as f:
            f.write("port = 9500\n")
        self.assertEqual(macos_daemon.port({}, tmp), "9500")
        self.assertEqual(macos_daemon.port({"EnvironmentVariables": {"JARVIS_PORT": "9411"}}, tmp), "9411")


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.tmp, "state")
        self.log = os.path.join(self.tmp, "log")
        self.bin = os.path.join(self.tmp, "bin")
        self.tmpdir = os.path.join(self.tmp, "t")
        self.daemon = os.path.join(self.tmp, "LaunchDaemons", "dev.jarvis.plist")
        for d in (self.home, self.state, self.bin, self.tmpdir, os.path.dirname(self.daemon)):
            os.makedirs(d)
        for name, body in STUBS.items():
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
        self.share = os.path.join(self.home, ".local", "share", "jarvis")
        self.agent = os.path.join(self.home, "Library", "LaunchAgents", "dev.jarvis.plist")
        self.kept = os.path.join(self.share, "dev.jarvis.plist.agent")
        self.env = {"HOME": self.home, "LOG": self.log, "STATE": self.state, "PATH": self.bin + ":/usr/bin:/bin",
                    "JARVIS_DAEMON_TEST": "1", "JARVIS_DAEMON_TEST_BIN": self.bin, "JARVIS_DAEMON_PLIST": self.daemon,
                    "TMPDIR": self.tmpdir, "TEST_PY": sys.executable}

    def _cleanup(self):
        for d, _, _ in os.walk(self.tmp):
            os.chmod(d, 0o755)
        shutil.rmtree(self.tmp)

    def calls(self):
        try:
            with open(self.log) as f:
                return [line for line in f.read().splitlines() if line != "sudo -n -v"]
        except FileNotFoundError:
            return []

    def set(self, *names):
        for name in names:
            open(os.path.join(self.state, name), "w").close()

    def has(self, name):
        return os.path.exists(os.path.join(self.state, name))

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()


class Script(Harness):
    def setUp(self):
        super().setUp()
        python = os.path.join(self.share, "venv", "bin", "python")
        os.makedirs(os.path.dirname(python))
        # $STATE/render-override(JSON): 렌더 코드 자체가 바뀌어 sha256 도 일치하는 경우
        with open(python, "w") as f:
            f.write('''#!/bin/sh
case "$*" in *"macos_daemon render"*) if [ -f "$STATE/render-override" ]; then
  "$TEST_PY" "$@" | "$TEST_PY" -c 'import json, os, plistlib, sys
d = plistlib.loads(sys.stdin.buffer.read())
d.update(json.load(open(os.path.join(os.environ["STATE"], "render-override"))))
plistlib.dump(d, sys.stdout.buffer, sort_keys=False)'
  exit
fi ;; esac
exec "$TEST_PY" "$@"
''')
        os.chmod(python, 0o755)
        with open(os.path.join(self.state, "tamper.py"), "w") as f:
            f.write(TAMPER)
        self.app = os.path.join(self.share, "app")
        os.makedirs(os.path.join(self.app, "jarvis"))
        open(os.path.join(self.app, "jarvis", "__init__.py"), "w").close()
        os.makedirs(os.path.dirname(self.agent))
        with open(self.agent, "wb") as f:
            plistlib.dump(macos_daemon.template(self.home), f)
        self.original = self.read(self.agent)
        self.set("agent")

    def run_script(self, *args):
        return subprocess.run(["bash", SCRIPT, *args], env=self.env, capture_output=True, text=True, timeout=120)

    def assert_daemon_mode(self):
        self.assertTrue(self.has("daemon"))
        self.assertFalse(self.has("agent"))
        self.assertFalse(os.path.exists(self.agent))
        self.assertEqual(self.read(self.kept), self.original)
        with open(self.daemon, "rb") as f:
            return plistlib.load(f)

    def assert_rolled_back(self, r):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("LaunchAgent 로 되돌립니다", r.stdout)
        self.assertFalse(self.has("daemon"))
        self.assertFalse(os.path.exists(self.daemon))
        self.assertEqual(self.read(self.agent), self.original)
        self.assertFalse(os.path.exists(self.kept))

    def test_install_moves_the_agent_to_a_running_daemon_in_order(self):
        """covers: DAEMON-3"""
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.assert_daemon_mode()
        import pwd  # Windows 에는 없다
        self.assertEqual(d["UserName"], pwd.getpwuid(os.getuid()).pw_name)
        self.assertEqual(d["ProgramArguments"][4:], [os.path.join(self.share, "venv/bin/python"), "-m", "jarvis"])
        self.assertEqual(d["EnvironmentVariables"]["JARVIS_LOG"], os.path.join(pwd.getpwuid(os.getuid()).pw_dir, "Library/Logs/jarvis.log"))
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Library", "Logs")))
        calls = [c for c in self.calls() if not c.startswith(("launchctl print", "lsof", "curl"))]
        self.assertRegex(calls[2], r"^sudo -n /usr/bin/install -m 0644 -o root -g wheel \S+/dev\.jarvis\.plist " + re.escape(self.daemon) + "$")
        self.assertEqual(calls[:2] + calls[3:], [
            "sudo -v",
            f"launchctl bootout gui/{UID}/dev.jarvis",
            f"sudo -n /bin/launchctl bootstrap system {self.daemon}",
            f"launchctl bootstrap system {self.daemon}",
        ])
        all_calls = self.calls()
        self.assertLess(all_calls.index(f"launchctl bootout gui/{UID}/dev.jarvis"), all_calls.index("lsof -nP -iTCP:9300 -sTCP:LISTEN"))
        self.assertLess(all_calls.index("lsof -nP -iTCP:9300 -sTCP:LISTEN"), all_calls.index(calls[2]))
        self.assertIn("curl -fsS --max-time 1 http://127.0.0.1:9300/api/health", all_calls)
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_privileged_commands_are_absolute_and_path_is_not_trusted(self):
        """covers: DAEMON-12"""
        evil = os.path.join(self.tmp, "evil")
        os.makedirs(evil)
        pwned = os.path.join(self.tmp, "pwned")
        for name in ("sudo", "launchctl", "install", "rm", "plutil", "curl", "lsof", "cmp", "diff", "grep", "seq", "mktemp", "mv", "cp", "stat", "shasum", "sh", "dirname"):
            path = os.path.join(evil, name)
            with open(path, "w") as f:
                f.write(f"#!/bin/sh\ntouch {pwned}\nexit 1\n")
            os.chmod(path, 0o755)
        self.env["PATH"] = evil + ":" + self.env["PATH"]
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(pwned))
        privileged = {c.split()[2] for c in self.calls() if c.startswith("sudo -n ")}
        self.assertEqual(privileged, {"/bin/launchctl", "/bin/rm", "/usr/bin/install"})
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        self.assertEqual({x for x in re.findall(r'"\$SUDO" -n (\S+)', source) if not x.startswith("-v")}, privileged)
        self.assertNotRegex(source, r"(?m)^\s*(if .*; then )?sudo ")

    def trace(self, flag):
        """인자 오류로 외부 명령을 부르기 전에 끝내고, 경로를 정하는 대입만 본다."""
        env = {k: v for k, v in self.env.items() if k != "JARVIS_DAEMON_TEST"}
        if flag is not None:
            env["JARVIS_DAEMON_TEST"] = flag
        r = subprocess.run(["bash", "-x", SCRIPT, "frobnicate"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        return r.stderr.splitlines()

    def test_without_the_test_flag_overrides_are_ignored(self):
        """covers: DAEMON-12"""
        for flag in (None, "0", "yes"):
            with self.subTest(flag):
                lines = self.trace(flag)
                for line in ("+ DAEMON=/Library/LaunchDaemons/dev.jarvis.plist", "+ SUDO=/usr/bin/sudo",
                             "+ LAUNCHCTL=/bin/launchctl", "+ PLUTIL=/usr/bin/plutil", "+ PATH=/usr/bin:/bin:/usr/sbin:/sbin"):
                    self.assertIn(line, lines)
                self.assertFalse([x for x in lines if x.startswith("+ ") and "=" in x and (self.daemon in x or self.bin in x)], lines)

    def test_the_test_flag_turns_on_the_overrides(self):
        """covers: DAEMON-12"""
        lines = self.trace("1")
        self.assertIn(f"+ DAEMON={self.daemon}", lines)
        self.assertIn(f"+ SUDO={self.bin}/sudo", lines)

    def test_sudo_is_asked_once_and_the_refresh_loop_stops(self):
        """covers: DAEMON-3"""
        self.assertEqual(self.run_script("install").returncode, 0)
        self.assertEqual(self.calls().count("sudo -v"), 1)
        time.sleep(0.2)  # 루프를 멈출 때 이미 돌던 가짜 sudo 한 번이 늦게 기록할 수 있다
        size = os.path.getsize(self.log)
        time.sleep(0.2)
        self.assertEqual(os.path.getsize(self.log), size)

    def test_a_bootstrap_that_fails_once_is_retried(self):
        """covers: DAEMON-3"""
        self.set("bootstrap-fails-once")
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_daemon_mode()

    def test_install_works_without_a_loaded_agent(self):
        """covers: DAEMON-3"""
        os.remove(os.path.join(self.state, "agent"))  # SSH 로 처음 설치: GUI 세션이 없다
        self.set("no-gui")
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_daemon_mode()
        self.assertNotIn(f"launchctl bootout gui/{UID}/dev.jarvis", self.calls())

    def test_a_failure_at_each_step_rolls_back_to_the_agent(self):
        """covers: DAEMON-4"""
        for name, fails in (("port-held", ":9300"), ("install-fails", "plist 를 설치"), ("bootstrap-fails", "bootstrap"),
                            ("daemon-not-running", "running"), ("daemon-unhealthy", "health")):
            with self.subTest(name):
                self.set(name)
                r = self.run_script("install")
                self.assert_rolled_back(r)
                self.assertIn(fails, r.stderr)
                self.assertIn("롤백했습니다", r.stderr)
                self.assertTrue(self.has("agent"))  # 다시 로드됐다
                self.assertIn(f"launchctl bootstrap gui/{UID} {self.agent}", self.calls())
                if name.startswith("daemon-"):  # daemon 이 로드된 뒤의 실패는 sudo 로 내린다
                    self.assertIn("sudo -n /bin/launchctl bootout system/dev.jarvis", self.calls())
                os.remove(os.path.join(self.state, name))
                os.remove(self.log)

    def assert_never_loaded(self, r, why):
        self.assert_rolled_back(r)
        self.assertIn(why, r.stderr)
        calls = self.calls()
        self.assertFalse([c for c in calls if "bootstrap system" in c], calls)
        self.assertIn(f"sudo -n /bin/rm -f {self.daemon}", calls)
        self.assertTrue(self.has("agent"))

    def put(self, name, value):
        with open(os.path.join(self.state, name), "w") as f:
            f.write(value)

    def test_a_plist_swapped_while_sudo_asks_is_removed_before_bootstrap(self):
        """covers: DAEMON-13"""
        for over in ({"UserName": "root"}, {"ProgramArguments": ["/tmp/evil"]}, {"EnvironmentVariables": {"PATH": "/tmp"}}):
            with self.subTest(over):
                self.put("tamper", json.dumps(over))
                self.assert_never_loaded(self.run_script("install"), "믿을 수 없습니다")
                os.remove(os.path.join(self.state, "tamper"))
                os.remove(self.log)

    def test_the_sha256_catches_a_swap_that_keeps_every_checked_field(self):
        """covers: DAEMON-13"""
        self.put("tamper", json.dumps({"ProgramArguments": ["/tmp/evil"]}))
        self.assert_never_loaded(self.run_script("install"), "sha256")

    def test_a_wrong_owner_or_mode_on_the_installed_plist_is_removed_before_bootstrap(self):
        """covers: DAEMON-13"""
        for got in ("someone:staff 644", "root:staff 644", "root:wheel 664", "root:wheel 600", ""):
            with self.subTest(got):
                self.put("stat-output", got)
                self.assert_never_loaded(self.run_script("install"), "root:wheel 0644")
                os.remove(os.path.join(self.state, "stat-output"))
                os.remove(self.log)

    def test_bad_fields_are_caught_even_when_the_sha256_matches(self):
        """covers: DAEMON-13"""
        for over, why in (({"UserName": "root"}, "UserName"), ({"UserName": "0"}, "UserName"), ({"UserName": ""}, "UserName"),
                          ({"UserName": "someone-else"}, "UserName"), ({"StandardOutPath": "/tmp/x"}, "StandardOutPath"),
                          ({"StandardErrorPath": "/tmp/x"}, "StandardErrorPath"), ({"Label": "dev.other"}, "Label"),
                          ({"GroupName": "wheel"}, "GroupName"), ({"GroupName": ""}, "GroupName"),
                          ({"InitGroups": False}, "InitGroups"), ({"Sockets": {"a": {"SockServiceName": "22"}}}, "Sockets"),
                          ({"MachServices": {"x": True}}, "MachServices"), ({"Umask": 0}, "Umask"), ({"Program": "/tmp/evil"}, "Program"),
                          ({"Label Nice": 1}, "Label Nice"), ({"Weird<Key": 1}, "Weird")):
            with self.subTest(over):
                self.put("render-override", json.dumps(over))
                self.assert_never_loaded(self.run_script("install"), why)
                os.remove(os.path.join(self.state, "render-override"))
                os.remove(self.log)

    def test_every_allowed_key_passes_the_installed_check(self):
        """covers: DAEMON-13"""
        with open(self.agent, "rb") as f:
            data = plistlib.load(f)
        data.update(ProcessType="Background", SoftResourceLimits={"NumberOfFiles": 4096}, HardResourceLimits={"NumberOfFiles": 8192}, Nice=5)
        with open(self.agent, "wb") as f:
            plistlib.dump(data, f)
        self.original = self.read(self.agent)
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = self.assert_daemon_mode()
        self.assertEqual(d["Nice"], 5)
        self.assertEqual(d["SoftResourceLimits"], {"NumberOfFiles": 4096})

    def test_the_script_allows_the_same_keys_as_the_renderer(self):
        """covers: DAEMON-13"""
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        allowed = set()
        for line in re.findall(r"^\s+((?:\w+\|)+\w+)\) ;;$", source, re.M):
            allowed.update(line.split("|"))
        self.assertEqual(allowed, macos_daemon.KEEP | {"UserName", "GroupName", "InitGroups", "StandardOutPath", "StandardErrorPath"})

    def test_an_agent_that_will_not_stop_stays_as_it_is(self):
        """covers: DAEMON-4"""
        self.set("agent-bootout-fails")
        r = self.run_script("install")
        self.assert_rolled_back(r)
        self.assertTrue(self.has("agent"))
        self.assertFalse([c for c in self.calls() if c.startswith("sudo -n /usr/bin/install")])

    def test_a_failed_move_of_the_agent_plist_rolls_back(self):
        """covers: DAEMON-4"""
        os.chmod(os.path.dirname(self.agent), 0o555)
        r = self.run_script("install")
        os.chmod(os.path.dirname(self.agent), 0o755)
        self.assert_rolled_back(r)
        self.assertTrue(self.has("agent"))

    def test_rollback_without_a_gui_session_says_so(self):
        """covers: DAEMON-4"""
        os.remove(os.path.join(self.state, "agent"))
        self.set("no-gui", "daemon-unhealthy")
        r = self.run_script("install")
        self.assert_rolled_back(r)
        self.assertFalse(self.has("agent"))
        self.assertEqual(self.calls().count(f"launchctl bootstrap gui/{UID} {self.agent}"), 5)
        self.assertIn("GUI 로그인", r.stdout)
        self.assertIn("롤백이 완전하지 않습니다", r.stderr)

    def start_and_wait_for_the_port_loop(self):
        self.set("port-held")
        self.env["FAKE_SLEEP"] = "0.1"  # 신호를 보내는 동안 포트 대기(30회)가 끝나지 않게
        p = subprocess.Popen(["bash", SCRIPT, "install"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        for _ in range(500):
            if "lsof -nP -iTCP:9300 -sTCP:LISTEN" in self.calls():
                return p
            time.sleep(0.01)
        self.fail("포트 대기에 들어가지 않았습니다")

    def test_an_interrupt_rolls_back(self):
        """covers: DAEMON-4"""
        p = self.start_and_wait_for_the_port_loop()
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=60)
        self.assert_rolled_back(subprocess.CompletedProcess(p.args, p.returncode, out, err))
        self.assertIn("중단되었습니다", err)
        self.assertTrue(self.has("agent"))

    def test_a_dropped_connection_finishes_the_switch(self):
        """covers: DAEMON-4"""
        p = self.start_and_wait_for_the_port_loop()
        p.send_signal(signal.SIGHUP)
        os.remove(os.path.join(self.state, "port-held"))
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, out + err)
        self.assert_daemon_mode()

    def test_lost_sudo_before_the_daemon_is_installed_restores_the_agent(self):
        """covers: DAEMON-4"""
        self.set("sudo-lost")
        r = self.run_script("install")
        self.assert_rolled_back(r)
        self.assertTrue(self.has("agent"))
        self.assertEqual([c for c in self.calls() if c.startswith("sudo -n /bin/rm")], [])  # 지울 것이 없으면 sudo 가 필요 없다

    def test_a_daemon_that_cannot_be_stopped_is_not_joined_by_the_agent(self):
        """covers: DAEMON-4"""
        self.set("sudo-lost-after-bootstrap", "daemon-unhealthy")
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.has("daemon"))
        self.assertTrue(os.path.exists(self.daemon))
        self.assertFalse(self.has("agent"))  # 두 벌이 같은 포트를 잡으려 하지 않는다
        self.assertFalse(os.path.exists(self.agent))  # 다음 GUI 로그인 때도
        self.assertEqual(self.read(self.kept), self.original)
        self.assertFalse([c for c in self.calls() if "bootstrap gui" in c])
        self.assertIn("uninstall", r.stdout)
        self.assertIn("롤백이 완전하지 않습니다", r.stderr)
        # sudo 를 되찾으면 안내대로 되돌릴 수 있다
        for name in ("sudo-lost", "sudo-lost-after-bootstrap"):
            os.remove(os.path.join(self.state, name))
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.has("daemon"))
        self.assertTrue(self.has("agent"))
        self.assertEqual(self.read(self.agent), self.original)

    def test_a_failed_daemon_bootout_keeps_the_plist_and_the_agent_down(self):
        """covers: DAEMON-4"""
        self.set("daemon-bootout-fails", "daemon-unhealthy")  # sudo 는 살아 있고 bootout 만 실패한다
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.has("daemon"))
        self.assertTrue(os.path.exists(self.daemon))  # 재부팅해도 daemon 이 다시 뜬다
        calls = self.calls()
        self.assertIn("sudo -n /bin/launchctl bootout system/dev.jarvis", calls)
        self.assertFalse([c for c in calls if c.startswith("sudo -n /bin/rm")], calls)
        self.assertFalse(self.has("agent"))
        self.assertFalse([c for c in calls if "bootstrap gui" in c], calls)
        self.assertFalse(os.path.exists(self.agent))
        self.assertEqual(self.read(self.kept), self.original)
        self.assertIn("uninstall", r.stdout)
        os.remove(os.path.join(self.state, "daemon-bootout-fails"))
        os.remove(self.log)
        r = self.run_script("uninstall")  # 안내대로 다시 실행하면 지운다
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.daemon))
        self.assertEqual(self.read(self.agent), self.original)
        self.assertLess(self.calls().index("sudo -n /bin/launchctl bootout system/dev.jarvis"),
                        self.calls().index(f"sudo -n /bin/rm -f {self.daemon}"))

    def test_a_port_held_by_something_else_is_refused_before_sudo(self):
        """covers: DAEMON-4"""
        os.remove(os.path.join(self.state, "agent"))  # 에이전트를 꺼 두었고 다른 프로세스가 포트를 쓴다
        self.set("port-held")
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertIn(":9300", r.stderr)
        self.assertFalse(self.has("agent"))
        self.assertFalse([c for c in self.calls() if c.startswith("sudo") or "bootstrap" in c])

    def test_refused_sudo_or_a_bad_plist_changes_nothing(self):
        """covers: DAEMON-4"""
        for name in ("sudo-denied", "lint-fails"):
            with self.subTest(name):
                self.set(name)
                r = self.run_script("install")
                self.assertEqual(r.returncode, 1)
                self.assertIn("아무것도 바꾸지 않았습니다", r.stderr)
                self.assertTrue(self.has("agent"))
                self.assertFalse(os.path.exists(self.daemon))
                self.assertEqual(self.read(self.agent), self.original)
                self.assertFalse([c for c in self.calls() if "bootout" in c or "bootstrap" in c])
                os.remove(os.path.join(self.state, name))

    def test_missing_pieces_are_refused_before_sudo(self):
        """covers: DAEMON-4"""
        shutil.move(os.path.join(self.app, "jarvis"), os.path.join(self.app, "x"))
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("jarvis 코드가 없습니다", r.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("sudo")])
        shutil.move(os.path.join(self.app, "x"), os.path.join(self.app, "jarvis"))
        os.remove(self.agent)
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("에이전트 plist 가 없습니다", r.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("sudo")])

    def snapshot(self):
        found = {}
        for d, _, files in os.walk(self.tmp):
            for name in files:
                path = os.path.join(d, name)
                if path != self.log:
                    found[path] = self.read(path)
        return found

    def test_dry_run_changes_nothing_and_does_not_ask_for_sudo(self):
        """covers: DAEMON-5"""
        before = self.snapshot()
        r = self.run_script("install", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)
        calls = self.calls()
        self.assertFalse([c for c in calls if c.startswith("sudo") or "bootout" in c or "bootstrap" in c])
        self.assertIn("<key>UserName</key>", r.stdout)
        self.assertIn(f"launchctl bootstrap system {self.daemon}", r.stdout)
        self.assertEqual(os.listdir(self.tmpdir), [])

        self.assertEqual(self.run_script("install").returncode, 0)
        before = self.snapshot()
        os.remove(self.log)
        r = self.run_script("uninstall", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse([c for c in self.calls() if c.startswith("sudo") or "bootout" in c or "bootstrap" in c])
        self.assertIn(f"launchctl bootstrap gui/{UID} {self.agent}", r.stdout)

    def test_install_again_with_the_same_render_does_nothing(self):
        """covers: DAEMON-6"""
        self.assertEqual(self.run_script("install").returncode, 0)
        installed = self.read(self.daemon)
        os.remove(self.log)
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("바꿀 것이 없습니다", r.stdout)
        self.assertEqual(self.read(self.daemon), installed)
        self.assertFalse([c for c in self.calls() if c.startswith("sudo") or "bootout" in c or "bootstrap" in c])
        self.assert_daemon_mode()

    def test_a_changed_render_replaces_the_loaded_daemon(self):
        """covers: DAEMON-6"""
        self.assertEqual(self.run_script("install").returncode, 0)
        with open(self.kept, "rb") as f:
            data = plistlib.load(f)
        data["EnvironmentVariables"]["JARVIS_PORT"] = "9411"
        with open(self.kept, "wb") as f:
            plistlib.dump(data, f)
        self.original = self.read(self.kept)
        os.remove(self.log)
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls()
        self.assertLess(calls.index("sudo -n /bin/launchctl bootout system/dev.jarvis"), calls.index(f"sudo -n /bin/launchctl bootstrap system {self.daemon}"))
        self.assertIn("curl -fsS --max-time 1 http://127.0.0.1:9411/api/health", calls)
        self.assertEqual(self.assert_daemon_mode()["EnvironmentVariables"]["JARVIS_PORT"], "9411")

    def test_a_left_over_agent_plist_is_moved_out_on_the_next_run(self):
        """covers: DAEMON-6"""
        self.assertEqual(self.run_script("install").returncode, 0)
        shutil.copy(self.kept, self.agent)  # 지난 실행이 mv 전에 끊겼다
        r = self.run_script("install")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_daemon_mode()

    def test_root_and_other_systems_are_refused(self):
        """covers: DAEMON-7"""
        self.env["FAKE_UID"] = "0"
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("root", r.stderr)
        self.assertEqual(self.calls(), [])
        self.env["FAKE_UID"] = UID
        self.env["FAKE_UNAME"] = "Linux"
        r = self.run_script("install")
        self.assertEqual(r.returncode, 1)
        self.assertIn("macOS", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_unknown_arguments_are_refused(self):
        """covers: DAEMON-7"""
        for args in ((), ("frobnicate",), ("install", "--force")):
            r = self.run_script(*args)
            self.assertEqual(r.returncode, 1)
            self.assertIn("사용법", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_uninstall_returns_to_the_same_agent(self):
        """covers: DAEMON-8"""
        self.assertEqual(self.run_script("install").returncode, 0)
        os.remove(self.log)
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.has("daemon"))
        self.assertTrue(self.has("agent"))
        self.assertFalse(os.path.exists(self.daemon))
        self.assertFalse(os.path.exists(self.kept))
        self.assertEqual(self.read(self.agent), self.original)
        calls = self.calls()
        self.assertEqual(calls.count("sudo -v"), 1)
        for line in ("sudo -n /bin/launchctl bootout system/dev.jarvis", f"sudo -n /bin/rm -f {self.daemon}",
                     f"launchctl bootstrap gui/{UID} {self.agent}"):
            self.assertIn(line, calls)
        self.assertEqual(self.run_script("uninstall").returncode, 0)  # 다시 실행해도 된다

    def test_uninstall_without_a_gui_session_reports_failure_but_restores_the_plist(self):
        """covers: DAEMON-8"""
        self.assertEqual(self.run_script("install").returncode, 0)
        self.set("no-gui")
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(self.agent), self.original)
        self.assertFalse(os.path.exists(self.daemon))
        self.assertIn("GUI 로그인", r.stdout)

    def test_uninstall_without_a_kept_plist_changes_nothing(self):
        """covers: DAEMON-8"""
        self.assertEqual(self.run_script("install").returncode, 0)
        os.remove(self.kept)
        os.remove(self.log)
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("되돌릴 에이전트 plist", r.stderr)
        self.assertTrue(os.path.exists(self.daemon))
        self.assertFalse([c for c in self.calls() if c.startswith("sudo")])

    def test_another_users_daemon_is_refused_and_left_alone(self):
        """covers: DAEMON-14"""
        for body in (plistlib.dumps({"Label": "dev.jarvis", "UserName": "someone-else"}), plistlib.dumps({"Label": "dev.jarvis"}), b"not a plist"):
            with open(self.daemon, "wb") as f:
                f.write(body)
            self.set("daemon")  # 다른 사용자의 작업이 돌고 있다
            for args in (("install",), ("install", "--dry-run"), ("uninstall",), ("uninstall", "--dry-run")):
                with self.subTest(args=args, body=body[:20]):
                    r = self.run_script(*args)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    self.assertIn("다른 사용자", r.stderr)
                    self.assertIn("아무것도 바꾸지 않았습니다", r.stderr)
                    self.assertEqual(self.read(self.daemon), body)
                    self.assertEqual(self.read(self.agent), self.original)
                    self.assertTrue(self.has("daemon") and self.has("agent"))
                    self.assert_only_looked()

    def assert_only_looked(self):
        self.assertFalse([c for c in self.calls() if not c.startswith("launchctl print")], self.calls())

    def test_a_loaded_job_that_does_not_come_from_the_plist_is_refused(self):
        """covers: DAEMON-14"""
        mine = plistlib.dumps({"Label": "dev.jarvis", "UserName": subprocess.check_output(["id", "-un"], text=True).strip()})
        other = "\tpath = /Users/someone-else/dev.jarvis.plist\n"
        for name, body, printed in (("plist 없음", None, None), ("다른 path", mine, other),
                                    ("path 줄 없음", mine, "\ttype = LaunchDaemon\n")):
            if body is None:
                if os.path.exists(self.daemon):
                    os.remove(self.daemon)
            else:
                with open(self.daemon, "wb") as f:
                    f.write(body)
            if printed is not None:
                self.put("daemon-path", printed)
            self.set("daemon")
            for args in (("install",), ("install", "--dry-run"), ("uninstall",), ("uninstall", "--dry-run")):
                with self.subTest(name, args=args):
                    r = self.run_script(*args)
                    self.assertEqual(r.returncode, 1, r.stdout)
                    self.assertIn("소유를 모르는 작업", r.stderr)
                    self.assertTrue(self.has("daemon") and self.has("agent"))
                    self.assertEqual(self.read(self.agent), self.original)
                    self.assert_only_looked()  # sudo 를 묻지 않는다

    def test_a_loaded_job_from_this_users_plist_is_still_replaced(self):
        """covers: DAEMON-14"""
        self.assertEqual(self.run_script("install").returncode, 0)
        self.assertTrue(self.has("daemon"))
        r = self.run_script("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.has("daemon"))

    def test_status_names_what_is_loaded_and_the_health(self):
        """covers: DAEMON-8"""
        r = self.run_script("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("daemon: 로드 안 됨", r.stdout)
        self.assertIn("agent: 로드됨", r.stdout)
        self.assertIn("health: ok (:9300)", r.stdout)
        self.assertEqual(self.run_script("install").returncode, 0)
        r = self.run_script("status")
        self.assertIn("daemon: 로드됨", r.stdout)
        self.assertIn("agent: 로드 안 됨", r.stdout)
        self.assertIn(self.kept, r.stdout)
        self.set("daemon-unhealthy")
        self.assertIn("health: 응답 없음", self.run_script("status").stdout)


FAKE_MAIN = '''import json, os, sys
with open(os.environ["LOG"], "a") as f:
    f.write("jarvis " + " ".join(sys.argv[1:]) + "\\n")
print(json.dumps({"plan_id": "abc", "steps": [], "blocked": []} if sys.argv[3] == "plan" else {"status": "done"}))
'''


class InstallShEndToEnd(Harness):
    """install.sh --daemon 이 받은 릴리스의 실제 스크립트로 넘어가는 전체 흐름."""

    def setUp(self):
        super().setUp()
        src = os.path.join(self.tmp, "src", "jarvis-9.9.9")
        os.makedirs(os.path.join(src, "jarvis"))
        os.makedirs(os.path.join(src, "deploy"))
        for rel in ("deploy/jarvis.plist", "deploy/macos-daemon.sh", "jarvis/macos_daemon.py"):
            shutil.copy(os.path.join(ROOT, rel), os.path.join(src, rel))
        with open(os.path.join(src, "jarvis", "__init__.py"), "w") as f:
            f.write('__version__ = "9.9.9"\n')
        with open(os.path.join(src, "jarvis", "__main__.py"), "w") as f:
            f.write(FAKE_MAIN)
        tgz = os.path.join(self.tmp, "jarvis.tar.gz")
        with tarfile.open(tgz, "w:gz") as t:
            t.add(src, arcname="jarvis-9.9.9")
        with open(tgz, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        manifest = os.path.join(self.tmp, "manifest.json")
        with open(manifest, "w") as f:
            f.write('{"version": "9.9.9", "url": "file://%s", "sha256": "%s"}' % (tgz, digest))
        self.env["JARVIS_MANIFEST"] = "file://" + manifest
        uv = os.path.join(self.bin, "uv")
        with open(uv, "w") as f:
            f.write('''#!/bin/sh
if [ "$1" = venv ]; then mkdir -p "$4/bin" && printf '#!/bin/sh\\nexec "%s" "$@"\\n' "$TEST_PY" > "$4/bin/python" && chmod +x "$4/bin/python"; fi
''')
        os.chmod(uv, stat.S_IRWXU)

    def install(self, *args):
        return subprocess.run(["sh", os.path.join(ROOT, "install.sh"), *args], env=self.env, capture_output=True, text=True, timeout=120)

    def test_daemon_flag_stops_at_a_loaded_job_of_unknown_owner(self):
        """covers: DAEMON-14"""
        self.set("daemon")  # plist 없이 로드된 작업
        r = self.install("--core", "100.64.0.1", "--daemon")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("소유를 모르는 작업", r.stderr)
        self.assertIn("LaunchDaemon 으로 옮기지 못했습니다", r.stderr)
        self.assertTrue(self.has("daemon"))
        self.assertFalse([c for c in self.calls() if c.startswith("sudo") or "bootout" in c or "bootstrap" in c], self.calls())

    def test_a_fresh_ssh_install_ends_as_a_daemon_and_a_rerun_changes_nothing(self):
        """covers: DAEMON-9"""
        self.set("no-gui")  # SSH 세션: gui 도메인에 bootstrap 할 수 없다
        r = self.install("--core", "100.64.0.1", "--daemon")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.has("daemon"))
        self.assertFalse(os.path.exists(self.agent))
        with open(self.kept, "rb") as f:
            self.assertEqual(plistlib.load(f)["EnvironmentVariables"]["JARVIS_TRAY"], "1")
        with open(self.daemon, "rb") as f:
            d = plistlib.load(f)
        self.assertEqual(d["WorkingDirectory"], os.path.join(self.share, "app"))
        self.assertNotIn("/Users/USER", plistlib.dumps(d).decode())
        calls = self.calls()
        self.assertFalse([c for c in calls if "bootstrap gui" in c])
        self.assertLess(calls.index(f"sudo -n /bin/launchctl bootstrap system {self.daemon}"), calls.index("jarvis module hooks plan --json"))

        installed = self.read(self.daemon)
        os.remove(self.log)
        r = self.install()  # 플래그 없이 다시 실행: 에이전트를 만들지 않는다
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LaunchAgent 를 만들지 않습니다", r.stdout)
        self.assertFalse(os.path.exists(self.agent))
        r = self.install("--daemon")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("바꿀 것이 없습니다", r.stdout)
        self.assertFalse(os.path.exists(self.agent))
        self.assertEqual(self.read(self.daemon), installed)
        self.assertFalse([c for c in self.calls() if c.startswith("sudo") or "bootout" in c or "bootstrap" in c])


if __name__ == "__main__":
    unittest.main()
