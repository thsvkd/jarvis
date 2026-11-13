"""SSH 기기(docs/SPEC.md 의 "SSH 기기"): core.SSH_BIN 자리에 가짜 ssh 를 넣는다.

가짜 ssh 는 argv·표준 입력을 기록하고, -W 나 원격 bridge(`-m jarvis bridge`·bridge.cmd)면 통조림 HTTP 응답을 내거나(-W 는 테스트가
띄운 게이트웨이로 잇기도 한다), 아니면 마지막 인자(원격 명령)를 임시 HOME 으로 실제 /bin/sh 에서 돌린다. 그 HOME 의 venv python 도 가짜다.
실제 ssh·실제 홈·실제 기기·실제 서비스는 쓰지 않는다.
"""
import contextlib
import gzip
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis import config, core, modules_api, self_update, server  # noqa: E402
from test_server_access import FakeCollector  # noqa: E402

PLAN_ID = "b" * 64

FAKE_SSH = r'''
import json, os, socket, subprocess, sys, threading, time
d = os.environ["FAKE_SSH_DIR"]
argv = sys.argv[1:]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": argv, "stdin_null": os.path.samestat(os.fstat(0), os.stat(os.devnull))}) + "\n")
mode = os.environ.get("FAKE_SSH_MODE", "")
if mode == "255":
    sys.stderr.write("debug1: 잡음\nPermission denied (publickey).\n")
    sys.exit(255)
if mode == "hang":  # 표준 출력·오류를 쥔 손자를 남기고 멈춘다
    g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.path.join(d, "grandchild"), "w") as f:
        f.write(str(g.pid))
    time.sleep(60)
if "-W" in argv or "jarvis bridge" in argv[-1] or "bridge.cmd" in argv[-1]:
    port = os.environ.get("FAKE_SSH_PORT")
    if port and "-W" in argv:  # 테스트가 띄운 게이트웨이로 잇는다
        s = socket.create_connection(("127.0.0.1", int(port)))
        def up():
            while b := os.read(0, 65536):
                s.sendall(b)
            s.shutdown(socket.SHUT_WR)
        threading.Thread(target=up, daemon=True).start()
        while b := s.recv(65536):
            os.write(1, b)
        sys.exit(0)
    req = sys.stdin.buffer.read()  # 입력이 닫혀야 돌아온다
    with open(os.path.join(d, "request"), "ab") as f:
        f.write(req)
    with open(os.path.join(d, "response"), "rb") as f:
        sys.stdout.buffer.write(f.read())
    sys.stdout.flush()
    if mode == "linger":  # 응답 뒤에도 끝나지 않는다: Content-Length 만큼만 읽어야 돌아온다
        time.sleep(60)
    sys.exit(0)
env = dict(os.environ, HOME=os.environ["FAKE_SSH_HOME"])
sys.exit(subprocess.run(["/bin/sh", "-c", argv[-1]], env=env, stdin=subprocess.DEVNULL).returncode)
'''

FAKE_PY = r'''
import json, os, sys, time
home = os.environ["HOME"]
args = sys.argv[3:]  # -m jarvis 뒤
with open(os.path.join(home, "py.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "enc": os.environ.get("PYTHONIOENCODING"),
                        "path": os.environ.get("PATH"),
                        "stdin_null": os.path.samestat(os.fstat(0), os.stat(os.devnull))}) + "\n")
with open(os.path.join(home, "py.json"), encoding="utf-8") as f:
    spec = json.load(f)
r = spec.get(" ".join(args[:3])) or spec.get(args[0]) or {}
sys.stderr.write(r.get("err", "원격 진행 로그\n"))
time.sleep(r.get("sleep", 0))
sys.stdout.write(r.get("out", json.dumps({"module": args[1]}) + "\n" if args[0] == "module" else ""))
sys.exit(r.get("code", 0))
'''


def script(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"#!{sys.executable}\n{body}")
    os.chmod(path, 0o755)


def http(body, status="200 OK", gz=False, length=None):
    if gz:
        body = gzip.compress(body)
    head = f"HTTP/1.0 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body) if length is None else length}\r\n"
    return (head + ("Content-Encoding: gzip\r\n" if gz else "") + "\r\n").encode() + body


def wait_until(check, n=250):
    for _ in range(n):
        if check():
            return True
        time.sleep(0.02)
    return False


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as f:  # 부모가 거두기 전의 좀비도 끝난 것이다
            return f.read().split(") ")[1][0] == "Z"
    except OSError:  # 그사이 거둬졌다
        return True


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "rec")
        self.home = os.path.join(tmp.name, "home")
        os.makedirs(self.dir)
        self.app = os.path.join(self.home, ".local", "share", "jarvis", "app")
        os.makedirs(os.path.join(self.app, "jarvis"))
        open(os.path.join(self.app, "jarvis", "__init__.py"), "w").close()
        venv = os.path.join(self.home, ".local", "share", "jarvis", "venv", "bin")
        os.makedirs(venv)
        self.python = os.path.join(venv, "python")
        script(self.python, FAKE_PY)
        ssh = os.path.join(tmp.name, "ssh")
        script(ssh, FAKE_SSH)
        self.py({})
        for p in (mock.patch.object(core, "SSH_BIN", ssh),
                  mock.patch.dict(os.environ, {"FAKE_SSH_DIR": self.dir, "FAKE_SSH_HOME": self.home})):
            p.start()
            self.addCleanup(p.stop)
        modules_api.ssh_jobs.clear()
        modules_api.ssh_updating.clear()
        self.addCleanup(modules_api.ssh_jobs.clear)
        self.addCleanup(modules_api.ssh_updating.clear)
        self.core = core.Core(core.devices_from([{"id": "kg", "ssh": "kg-alias"}]))
        self.mods = modules_api.CoreModules(self.core)

    def mode(self, m):
        os.environ["FAKE_SSH_MODE"] = m

    def response(self, raw):
        with open(os.path.join(self.dir, "response"), "wb") as f:
            f.write(raw)

    def py(self, spec):
        with open(os.path.join(self.home, "py.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f)

    def calls(self):
        try:
            with open(os.path.join(self.dir, "calls.jsonl")) as f:
                return [json.loads(x) for x in f]
        except FileNotFoundError:
            return []

    def py_calls(self):
        try:
            with open(os.path.join(self.home, "py.jsonl")) as f:
                return [json.loads(x) for x in f]
        except FileNotFoundError:
            return []

    def polled(self, version="1.7.8", pid=None, i="kg", os_name="Darwin 25.0"):
        """폴링이 성공한 상태로 둔다(스냅샷 판·monitor.pid)."""
        snap = {"meta": {"host": "kg-host", "version": version}, "monitor": {"pid": pid},
                "system": {"host": {"os": os_name}}}
        raw = json.dumps(snap).encode()
        with self.core.lock:
            self.core.state[i].update(ok_at=time.time(), error=None, fails=0, summary=core._summary(snap),
                                      snap=(raw, gzip.compress(raw)))


class DevicesFrom(unittest.TestCase):
    def test_ssh_alias_entries(self):
        """covers: SSH-1"""
        bad = [{"id": "a", "ssh": "h", "url": "http://1.2.3.4:9300"}, {"id": "b"}, {"id": "c", "ssh": "-oProxyCommand=x"},
               {"id": "d", "ssh": "u@h"}, {"id": "e", "ssh": "h h"}, {"id": "f", "ssh": "h=x"}, {"id": "g", "ssh": "h:22"},
               {"id": "h", "ssh": "kgolf\n"}, {"id": "i", "ssh": ["h"]}, {"id": "j", "ssh": ""}, {"id": "k", "ssh": "x" * 65}]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            got = core.devices_from([{"id": "kg", "ssh": "kgolf-macmini-m1"}, *bad, {"id": "web", "url": "http://1.2.3.4:9300/"}])
        self.assertEqual(got, [{"id": "kg", "url": "ssh://kgolf-macmini-m1", "ssh": "kgolf-macmini-m1"},
                               {"id": "web", "url": "http://1.2.3.4:9300"}])
        self.assertEqual(out.getvalue().count("잘못된 devices 항목은 건너뛴다"), len(bad))


class Fetch(Base):
    def test_poll_and_relay_over_ssh_w(self):
        """covers: SSH-2"""
        snap = {"meta": {"host": "kg-host", "version": "1.7.8"}, "summary": {"status": "ok"}}
        self.response(http(json.dumps(snap).encode(), gz=True))
        self.core.poll("kg")
        st = self.core.state["kg"]
        self.assertEqual((st["fails"], st["error"], st["summary"]["host"]), (0, None, "kg-host"))
        self.assertEqual(json.loads(self.core.snapshot("kg")[0]), snap)
        self.assertEqual(self.core.list()["devices"][0]["url"], "ssh://kg-alias")
        (call,) = self.calls()
        self.assertEqual(call["argv"], [*core.SSH_OPTS, *core.SSH_MUX, "-W", "127.0.0.1:9300", "--", "kg-alias"])
        self.assertIn("ControlMaster=auto", call["argv"])
        for o in ("BatchMode=yes", "ConnectTimeout=5", "ServerAliveInterval=5", "ServerAliveCountMax=2", "LogLevel=ERROR"):
            self.assertIn(o, call["argv"])
        self.assertFalse(any("StrictHostKeyChecking" in a or "UserKnownHostsFile" in a for a in call["argv"]))
        with open(os.path.join(self.dir, "request"), "rb") as f:  # 가짜는 입력이 닫혀야 요청을 기록한다
            self.assertEqual(f.read(), b"GET /api/snapshot HTTP/1.0\r\nHost: 127.0.0.1:9300\r\nAccept-Encoding: gzip\r\n"
                                       b"Connection: close\r\n\r\n")
        self.response(http(b'{"changes":[]}'))
        self.assertEqual(self.core.relay("kg", "/api/changes"), b'{"changes":[]}')
        self.assertEqual(self.core.relay("kg", "/api/history?range=24h"), b'{"changes":[]}')
        for path in ("/api/usage/independent", "/api/history?range=7d", "/api/changes\n", "/api/modules"):
            with self.assertRaises(ValueError):
                self.core.relay("kg", path)
        self.assertEqual(len(self.calls()), 3)

    def test_reads_only_content_length(self):
        """covers: SSH-2"""
        self.mode("linger")
        self.response(http(b'{"changes":[1]}'))
        t = time.monotonic()
        self.assertEqual(self.core.relay("kg", "/api/changes"), b'{"changes":[1]}')
        self.assertLess(time.monotonic() - t, 8)

    def test_relay_is_bounded_per_device(self):
        """covers: SSH-2"""
        sem = self.core._relays["kg"]
        for _ in range(4):
            self.assertTrue(sem.acquire(blocking=False))
        with self.assertRaises(RuntimeError):
            self.core.relay("kg", "/api/changes")
        self.assertEqual(self.calls(), [])
        for _ in range(4):
            sem.release()

    def test_macos_uses_system_ssh(self):
        """covers: SSH-2"""
        spec = importlib.util.spec_from_file_location("jarvis._core_darwin", core.__file__)
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "platform", "darwin"):
            spec.loader.exec_module(mod)
        self.assertEqual(mod.SSH_BIN, "/usr/bin/ssh")


class FetchFailures(Base):
    def error(self):
        self.core.poll("kg")
        st = self.core.state["kg"]
        self.assertEqual(st["fails"], 1)
        self.assertNotIn("kg-alias", st["error"])
        return st["error"]

    def test_failures_record_short_reason(self):
        """covers: SSH-3"""
        self.mode("255")
        self.assertEqual(self.error(), "Permission denied (publickey).")
        self.mode("")
        self.response(http(b'{"error":"x"}', status="500 Internal Server Error"))
        self.core.state["kg"]["fails"] = 0
        self.assertEqual(self.error(), "HTTP 500")
        self.response(http(json.dumps({"meta": {}}).encode() + b" " * 64))
        self.core.state["kg"]["fails"] = 0
        with mock.patch.object(core, "MAX_BYTES", 32):
            self.assertEqual(self.error(), "응답이 너무 큽니다")
        raw = gzip.compress(b'{"meta":{"host":"x"}}' * 50)
        self.response(b"HTTP/1.0 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 20\r\n\r\n" + raw[:20])
        self.core.state["kg"]["fails"] = 0
        self.assertEqual(self.error(), "gzip 응답이 잘렸습니다")
        self.response(b"garbage secret\r\n\r\n")  # 원격이 보낸 바이트는 error 에 넣지 않는다
        self.core.state["kg"]["fails"] = 0
        self.assertEqual(self.error(), "BadStatusLine")

    def test_timeout_kills_the_process_group(self):
        """covers: SSH-3"""
        self.mode("hang")
        t = time.monotonic()
        with mock.patch.object(core, "SSH_DEADLINE_S", 1):
            self.assertEqual(self.error(), "응답이 너무 느립니다")
        self.assertLess(time.monotonic() - t, 6)
        with open(os.path.join(self.dir, "grandchild")) as f:
            pid = int(f.read())
        self.assertTrue(wait_until(lambda: gone(pid)))


class Bridge(Base):
    def requests(self):
        with open(os.path.join(self.dir, "request"), "rb") as f:
            return f.read().split(b"\r\n\r\n")[:-1]

    def test_usage_ops_go_through_the_remote_bridge(self):
        """covers: SSH-4"""
        self.polled(core.BRIDGE_MIN)
        c = self.core
        self.response(http(b'{"ok": 1}'))
        with mock.patch.object(core, "_open", side_effect=AssertionError("HTTP 를 보냈다")):
            for call in (lambda: c.usage("kg", True), lambda: c.usage_login("kg", "claude", "abcdefgh12"),
                         lambda: c.usage_login_status("kg", "claude"), lambda: c.usage_switch("kg", "1"),
                         lambda: c.usage_remove("kg", "1")):
                self.assertEqual(call(), (200, {"ok": 1}))
        reqs = self.requests()
        self.assertEqual([r.split(b"\r\n")[0] for r in reqs],
                         [b"GET /api/usage/independent?refresh=1 HTTP/1.0", b"POST /api/usage/login?provider=claude HTTP/1.0",
                          b"GET /api/usage/login?provider=claude HTTP/1.0", b"POST /api/usage/account?slot=1 HTTP/1.0",
                          b"POST /api/usage/account/remove?slot=1 HTTP/1.0"])
        self.assertIn(b"X-jarvis-login-code: abcdefgh12", reqs[1])  # 코드는 원격 명령줄이 아니라 bridge 의 표준 입력으로
        calls = self.calls()
        self.assertEqual(len(calls), 5)
        for x in calls:
            self.assertEqual(x["argv"][-1], core.BRIDGE)
            self.assertNotIn("abcdefgh12", " ".join(x["argv"]))
            self.assertIn("ControlMaster=no", x["argv"])
            self.assertIn("-T", x["argv"])
            self.assertFalse(x["stdin_null"])

    def test_windows_uses_the_gateway_written_launcher(self):
        """covers: SSH-4"""
        self.polled(core.BRIDGE_MIN, os_name="Windows 11")
        self.response(http(b'{"ok": 1}'))
        self.assertEqual(self.core.usage("kg"), (200, {"ok": 1}))
        self.assertEqual(self.calls()[0]["argv"][-1], 'cmd /c "%LOCALAPPDATA%\\jarvis\\bridge.cmd"')

    def test_windows_modules_and_update_go_through_the_bridge(self):
        """covers: SSH-4 (Windows 는 POSIX 셸이 없어 모듈·갱신도 게이트웨이 API 로)"""
        self.polled(core.BRIDGE_MIN, os_name="Windows 11")
        self.response(http(b'{"ok": 1}'))
        name = next(iter(modules_api.module.MODULES))
        for call in (lambda: self.mods.all(full=True)["devices"][0]["status"], lambda: self.mods.plan("kg", name)[0],
                     lambda: self.mods.apply("kg", name, "a" * 64)[0], lambda: self.core.jarvis_update("kg")[0],
                     lambda: self.core.jarvis_update_progress("kg")[0]):
            self.assertEqual(call(), 200)
        self.assertEqual([r.split(b"\r\n")[0] for r in self.requests()],
                         [b"GET /api/modules HTTP/1.0", f"GET /api/modules/{name}/plan HTTP/1.0".encode(),
                          f"POST /api/modules/{name}/apply?plan_id={'a' * 64} HTTP/1.0".encode(),
                          b"POST /api/jarvis/update HTTP/1.0", b"GET /api/jarvis/update HTTP/1.0"])
        self.assertEqual({c["argv"][-1] for c in self.calls()}, {core.BRIDGE_WIN})
        self.assertEqual(self.py_calls(), [])  # 원격 셸 명령(sh -c)은 없다

    def test_gateway_answers_are_relayed(self):
        """covers: SSH-4"""
        self.polled(core.BRIDGE_MIN)
        self.response(http(b'{"error": "busy"}', status="409 Conflict"))
        self.assertEqual(self.core.usage_switch("kg", "1"), (409, {"error": "busy"}))
        self.response(http(b'{"error": "not found"}', status="404 Not Found"))
        self.assertEqual(self.core.usage("kg")[0], 501)  # bridge 가 없는 옛 게이트웨이
        self.mode("255")
        code, body = self.core.usage("kg")
        self.assertEqual(code, 502)
        self.assertIn("Permission denied (publickey).", body["error"])

    def test_line_breaks_never_reach_ssh(self):
        """covers: SSH-4"""
        self.polled(core.BRIDGE_MIN)
        req = core.urllib.request.Request("http://x/api/usage/login?provider=a%0d%0ab", headers={"X-A": "1\r\nX-B: 2"})
        code, body = self.core._ask("kg", req, 5, "")
        self.assertEqual(code, 502)
        self.assertEqual(self.calls(), [])

    def test_old_gateways_and_update_progress_are_501_without_ssh(self):
        """covers: SSH-4"""
        self.polled("1.12.2")
        code, body = self.core.usage("kg")
        self.assertEqual(code, 501)
        self.assertIn("1.12.2", body["error"])
        self.polled(core.BRIDGE_MIN)
        self.assertEqual(self.core.jarvis_update_progress("kg"), (501, {"error": core.SSH_ONLY}))
        self.assertEqual(self.calls(), [])

    def test_bulk_order(self):
        """covers: SSH-4"""
        c = core.Core(core.devices_from([{"id": "old", "ssh": "a"}, {"id": "down", "ssh": "b"},
                                         {"id": "web", "url": "http://127.0.0.1:1"}]))
        self.core = c
        self.polled("1.12.2", i="old")
        with c.lock:
            c.state["down"].update(fails=2, error="Permission denied (publickey).")
        rows = {r["id"]: r for r in c.usage_all()["devices"]}
        self.assertEqual(rows["old"]["status"], 501)
        self.assertEqual(rows["down"]["status"], 502)
        self.assertEqual(rows["web"]["status"], 502)  # HTTP 기기는 지금처럼 부른다(닿지 않는 주소)
        self.assertIn("닿지 못했습니다", rows["web"]["error"])
        self.assertEqual(self.calls(), [])


class LoopbackGateway(Base):
    def test_gateway_answers_public_paths_over_ssh_w(self):
        """covers: SSH-5 (403 쪽은 test_server_access.AccessTest.test_no_loopback,
        test_modules_api 의 test_gateway_routes_answer_only_the_configured_core)"""

        class Journal:
            def events(self):
                return [{"kind": "x"}]

        class Collector(FakeCollector):
            journal = Journal()

            def snapshot(self):
                raw = json.dumps({"meta": {"host": "gw", "version": "1.7.8"}}).encode()
                return raw, gzip.compress(raw)

            def history(self, rng):
                return {"range": rng}

        httpd = server.Server(("127.0.0.1", 0), server.make_handler(Collector()))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        os.environ["FAKE_SSH_PORT"] = str(httpd.server_address[1])
        self.addCleanup(os.environ.pop, "FAKE_SSH_PORT", None)
        raw, gz = core.ssh_fetch("kg-alias", "/api/snapshot")
        self.assertEqual(json.loads(raw)["meta"]["host"], "gw")
        self.assertIsNotNone(gz)
        self.assertEqual(json.loads(core.ssh_fetch("kg-alias", "/api/history?range=1h")[0]), {"range": "1h"})
        self.assertEqual(json.loads(core.ssh_fetch("kg-alias", "/api/changes")[0]), {"changes": [{"kind": "x"}]})


class Commands(Base):
    def test_operations_use_a_fresh_connection_constant_script_and_devnull(self):
        """covers: SSH-6"""
        self.polled()
        self.assertEqual(self.mods.all(full=True)["devices"][0]["status"], 200)
        calls = self.calls()
        self.assertEqual(len(calls), len(modules_api.module.MODULES))
        for c in calls:
            self.assertEqual(c["argv"][:-1], [*core.SSH_OPTS, *core.SSH_NOMUX, "-T", "--", "kg-alias"])
            for opt in ("-a", "-x", "ClearAllForwardings=yes", "ControlMaster=no", "ControlPath=none"):
                self.assertIn(opt, c["argv"])
            self.assertTrue(c["argv"][-1].startswith("sh -c '"))
            self.assertNotIn("ControlMaster=auto", c["argv"])
            self.assertTrue(c["stdin_null"])
        py = self.py_calls()
        self.assertEqual(sorted(p["argv"][3] for p in py), sorted(modules_api.module.MODULES))
        for p in py:
            self.assertEqual(p["argv"][:2] + p["argv"][4:], ["-m", "jarvis", "status", "--json"])
            self.assertEqual(p["cwd"], self.app)
            self.assertEqual(p["enc"], "utf-8")
            self.assertTrue(p["path"].startswith(os.path.join(self.home, ".local", "bin") + ":/opt/homebrew/bin:"))
            self.assertTrue(p["stdin_null"])

    def test_checked_before_ssh(self):
        """covers: SSH-6"""
        self.polled()
        self.assertEqual(self.mods.plan("kg", "nope")[0], 404)
        for bad in ("b" * 63, "B" * 64, PLAN_ID + "\n", PLAN_ID + "x", "", None):
            self.assertEqual(self.mods.apply("kg", "hooks", bad)[0], 400, repr(bad))
        self.assertEqual(self.mods.apply("kg", "nope", PLAN_ID)[0], 404)
        for v in ("1.7.7", "abc", None, "1.7"):
            self.polled(version=v)
            code, body = self.mods.plan("kg", "hooks")
            self.assertEqual(code, 501, v)
            self.assertIn("SSH 조작을 지원하지 않습니다", body["error"])
            self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID)[0], 501)
            self.assertEqual(self.core.jarvis_update("kg")[0], 501)
            self.assertEqual(self.mods.all()["devices"][0]["status"], 501)
        with self.core.lock:
            self.core.state["kg"]["summary"] = None
        self.assertEqual(self.mods.plan("kg", "hooks")[0], 502)
        self.assertEqual(self.core.jarvis_update("kg")[0], 502)
        self.assertEqual(self.calls(), [])
        for args in (("core-trust", "plan"), ("module", "hooks;rm"), ("module", "hooks", "x y"), (), ("module\n",),
                     ("module", "hooks", "uninstall"), ("module", "hooks")):
            with self.assertRaises(ValueError):
                core.jarvis_line(*args)

    def test_layout_and_stderr(self):
        """covers: SSH-6"""
        self.assertEqual(core.ssh_run("kg-alias", core.PRE + core.jarvis_line("module", "hooks", "status", "--json"), 20)[2],
                         "원격 진행 로그")  # 읽기 동작의 원격 표준 오류는 core 가 받는다
        apply = core.PRE + core.jarvis_line("module", "hooks", "apply", "--json", "--plan-id", PLAN_ID, write=True)
        self.assertIn(f"--plan-id {PLAN_ID} </dev/null 2>/dev/null", apply)
        self.assertEqual(core.ssh_run("kg-alias", apply, 20)[2], "")  # 쓰기 동작은 버린다
        self.assertEqual(core.ssh_run("kg-alias", core.PRE + core.jarvis_line("update", write=True), 20)[2], "")
        self.py({"module hooks status": {"out": "x" * 64, "err": "y" * 10000 + "\n끝 줄\n"}})
        with mock.patch.object(core, "MAX_BYTES", 16):  # 원격 출력은 상한까지만 읽는다
            code, out, err = core.ssh_run("kg-alias", core.PRE + core.jarvis_line("module", "hooks", "status", "--json"), 20)
        self.assertEqual((code, out, err), (0, "x" * 16, "끝 줄"))
        self.py({})
        os.remove(self.python)
        self.polled()
        self.assertEqual(self.mods.plan("kg", "hooks"), (502, {"error": core.SSH_EXIT[90]}))
        script(self.python, FAKE_PY)
        os.remove(os.path.join(self.app, "jarvis", "__init__.py"))  # 코드가 없는 app 은 배치가 아니다
        self.assertEqual(self.mods.plan("kg", "hooks"), (502, {"error": core.SSH_EXIT[90]}))
        old = os.path.join(self.home, "Workspace", "jarvis")  # 옛 배치·개발 체크아웃(kgolf-macmini)
        os.makedirs(os.path.join(old, "jarvis"))
        open(os.path.join(old, "jarvis", "__init__.py"), "w").close()
        self.py({"module hooks plan": {"out": json.dumps({"plan_id": PLAN_ID, "steps": []}) + "\n"}})
        self.assertEqual(self.mods.plan("kg", "hooks")[0], 200)
        self.assertEqual(os.path.realpath(self.py_calls()[-1]["cwd"]), os.path.realpath(old))
        open(os.path.join(self.app, "jarvis", "__init__.py"), "w").close()  # 둘 다 있으면 app 이 먼저다
        self.assertEqual(self.mods.plan("kg", "hooks")[0], 200)
        self.assertEqual(os.path.realpath(self.py_calls()[-1]["cwd"]), os.path.realpath(self.app))


class Results(Base):
    def test_last_json_line_and_errors(self):
        """covers: SSH-7"""
        self.polled()
        plan = {"module": "hooks", "plan_id": PLAN_ID, "steps": []}
        self.py({"module hooks plan": {"out": "rc 배너 {괄호}\n" + json.dumps(plan) + "\n"}})
        self.assertEqual(self.mods.plan("kg", "hooks"), (200, plan))
        self.py({"module hooks plan": {"out": "{깨진 json\n", "err": "Traceback\nboom\n", "code": 0}})
        with contextlib.redirect_stdout(io.StringIO()) as log:
            code, body = self.mods.plan("kg", "hooks")
        self.assertEqual(code, 502)
        self.assertIn("종료 코드 0", body["error"])
        self.assertNotIn("boom", json.dumps(body))
        self.assertIn("boom", log.getvalue())  # 표준 오류 마지막 줄은 core 로그에만
        self.assertNotIn("sh -c", log.getvalue())
        self.mode("255")
        code, body = self.mods.plan("kg", "hooks")
        self.assertEqual((code, body), (502, {"error": "kg 에 닿지 못했습니다: Permission denied (publickey)."}))

    def test_timeout_kills_the_process_group(self):
        """covers: SSH-7"""
        self.mode("hang")
        self.polled()
        t = time.monotonic()
        self.assertEqual(self.core.ssh("kg", "true", 1), (None, {"error": "1초 안에 끝나지 않았습니다"}))
        self.assertLess(time.monotonic() - t, 6)
        with open(os.path.join(self.dir, "grandchild")) as f:
            pid = int(f.read())
        self.assertTrue(wait_until(lambda: gone(pid)))


class Status(Base):
    def test_cached_like_the_gateway(self):
        """covers: SSH-8"""
        n = len(modules_api.module.MODULES)
        self.py({"module doctor status": {"out": json.dumps({"module": "doctor", "health": "ok", "details": ["비밀 위치"]})},
                 "module hooks status": {"out": json.dumps({"module": "hooks", "installed": True}), "code": 1}})
        self.polled()
        row = self.mods.all(full=True)["devices"][0]
        self.assertEqual((row["id"], row["host"], row["status"], row["job"]), ("kg", "kg-host", 200, None))
        self.assertEqual(set(row["modules"]), set(modules_api.module.MODULES))
        self.assertEqual(row["modules"]["doctor"]["details"], ["비밀 위치"])
        self.assertEqual((row["modules"]["doctor"]["ok"], row["modules"]["hooks"]["ok"]), (True, False))
        self.assertIn("checked_at", row["modules"]["hooks"])
        self.assertNotIn("details", self.mods.all()["devices"][0]["modules"]["doctor"])  # viewers 가 아니면 public()
        self.assertEqual(len(self.calls()), n)  # 두 번째 조회는 캐시
        self.mods.all(force=True)
        self.assertEqual(len(self.calls()), 2 * n)
        cache = self.mods._ssh["kg"]["cache"]
        at, code, body = cache["doctor"]
        cache["doctor"] = (at - 10 ** 6, code, {**body, "health": "old"})
        self.assertEqual(self.mods.all(full=True)["devices"][0]["modules"]["doctor"]["health"], "old")  # 지난 값을 바로
        self.assertTrue(wait_until(lambda: cache["doctor"][2]["health"] == "ok"))
        self.assertTrue(wait_until(lambda: not self.mods._ssh["kg"]["bg"].locked()))
        self.assertEqual(len(self.calls()), 2 * n + 1)
        with self.core.lock:
            self.core.state["kg"].update(fails=1, error="Permission denied (publickey).")
        self.assertEqual(self.mods.all()["devices"][0]["status"], 502)
        self.assertEqual(len(self.calls()), 2 * n + 1)


class Apply(Base):
    def test_plan_and_apply(self):
        """covers: SSH-9"""
        self.polled()
        self.py({"module hooks plan": {"out": json.dumps({"plan_id": PLAN_ID}), "code": 0},
                 "module setup plan": {"out": json.dumps({"error": "막힘"}), "code": 1},
                 "module hooks apply": {"out": json.dumps({"module": "hooks", "status": "stale"}), "code": 3, "sleep": 1.5}})
        self.assertEqual(self.mods.plan("kg", "hooks"), (200, {"plan_id": PLAN_ID}))
        self.assertEqual(self.mods.plan("kg", "setup"), (502, {"error": "막힘"}))
        self.mods.all()
        self.assertIn("hooks", self.mods._ssh["kg"]["cache"])
        with contextlib.redirect_stdout(io.StringIO()) as log:
            code, job = self.mods.apply("kg", "hooks", PLAN_ID)
            self.assertEqual((code, job["module"], job["plan_id"], job["state"]), (202, "hooks", PLAN_ID, "running"))
            self.assertTrue(modules_api.running())
            self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID)[0], 409)
            self.assertEqual(self.core.jarvis_update("kg"), (409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"}))
            self.assertEqual(self_update.start(), (409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"}))  # core 자신의 갱신
            self.assertTrue(wait_until(lambda: modules_api.ssh_jobs["kg"]["state"] != "running", n=500))
        j = modules_api.ssh_jobs["kg"]
        self.assertEqual((j["state"], j["code"], j["result"]["status"]), ("finished", 3, "stale"))
        self.assertFalse(modules_api.running())
        self.assertNotIn("hooks", self.mods._ssh["kg"]["cache"])
        self.assertIn(PLAN_ID[:12], log.getvalue())
        self.assertNotIn(PLAN_ID, log.getvalue())
        argv = [c["argv"][-1] for c in self.calls() if "apply" in c["argv"][-1]]
        self.assertEqual(len(argv), 1)
        self.assertIn(f"module hooks apply --json --plan-id {PLAN_ID} </dev/null 2>/dev/null", argv[0])
        self.assertEqual(self.mods.all(full=True)["devices"][0]["job"]["state"], "finished")
        modules_api.ssh_updating.add("kg")
        self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID), (409, {"error": "jarvis 갱신이 끝난 뒤에 적용하세요"}))
        modules_api.ssh_updating.clear()
        self.mode("255")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID)[0], 202)
            self.assertTrue(wait_until(lambda: modules_api.ssh_jobs["kg"]["state"] != "running"))
        self.assertEqual(modules_api.ssh_jobs["kg"]["state"], "interrupted")
        self.mode("")
        with mock.patch.object(core, "SSH_BIN", "/nonexistent/ssh"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.mods.plan("kg", "hooks")[0], 502)  # 500 이 아니다
            self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID)[0], 202)
            self.assertTrue(wait_until(lambda: modules_api.ssh_jobs["kg"]["state"] != "running"))
            with mock.patch.object(self.core, "ssh", side_effect=RuntimeError("버그")):  # 어떤 예외든 running 으로 남지 않는다
                self.assertEqual(self.mods.apply("kg", "hooks", PLAN_ID)[0], 202)
                self.assertTrue(wait_until(lambda: modules_api.ssh_jobs["kg"]["state"] != "running"))
        self.assertEqual(modules_api.ssh_jobs["kg"]["state"], "interrupted")
        self.assertFalse(modules_api.running())


class Update(Base):
    def child(self, *argv):
        p = subprocess.Popen(argv or [sys.executable, "-c", "import time; time.sleep(60)", "-m", "jarvis"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def updated(self, to="1.7.11", tail=" (릴리스)"):
        self.py({"update": {"out": f"jarvis 1.7.8 → {to} 을 받았습니다. 서비스를 재시작하면 새 버전이 뜹니다.\n"},
                 "--version": {"out": f"rc 잡음\njarvis {to}{tail}\nxx jarvis 9.9.9 접두가 아닌 줄\n"}})

    def test_update_and_restart(self):
        """covers: SSH-10"""
        p = self.child()
        self.polled(pid=p.pid)
        self.updated(tail=" (��)")  # 판 줄은 접두만 본다(로케일이 꼬인 괄호)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.core.jarvis_update("kg"), (200, {"running": "1.7.8", "to": "1.7.11", "restart": True}))
        self.assertEqual(p.wait(10), -signal.SIGTERM)
        first, restart = [c["argv"][-1] for c in self.calls()]
        self.assertIn("-m jarvis update </dev/null 2>/dev/null && ", first)
        self.assertIn(f"ps -o command= -p {p.pid}", restart)
        self.assertEqual(modules_api.ssh_updating, set())

    def test_same_version_does_not_restart(self):
        """covers: SSH-10"""
        p = self.child()
        self.polled(pid=p.pid)
        self.updated(to="1.7.8")
        self.assertEqual(self.core.jarvis_update("kg"), (200, {"running": "1.7.8", "to": "1.7.8", "restart": False}))
        self.assertEqual(len(self.calls()), 1)
        self.assertIsNone(p.poll())

    def test_unusable_pid_or_command_line(self):
        """covers: SSH-10"""
        self.updated()
        for pid in (True, False, 0, 1, -1, None, "123", 1.5):
            self.polled(pid=pid)
            self.assertEqual(self.core.jarvis_update("kg"), (502, {"error": core.NO_RESTART}), repr(pid))
        self.assertEqual(len(self.calls()), 8)  # 재시작 스크립트는 보내지 않았다
        p = self.child("sleep", "60")
        self.polled(pid=p.pid)
        self.assertEqual(self.core.jarvis_update("kg"), (502, {"error": core.NO_RESTART}))  # 91
        self.assertEqual(len(self.calls()), 10)
        self.assertIsNone(p.poll())

    def test_failed_update(self):
        """covers: SSH-10"""
        self.polled()
        self.py({"update": {"out": "릴리스를 받지 못했습니다: 막힘\n", "code": 1}})
        self.assertEqual(self.core.jarvis_update("kg"), (502, {"error": "릴리스를 받지 못했습니다: 막힘"}))
        self.py({"update": {"out": "받음\n"}, "--version": {"out": "버전 모름\n"}})
        self.assertEqual(self.core.jarvis_update("kg"), (502, {"error": "버전 모름"}))
        self.mode("255")
        self.assertEqual(self.core.jarvis_update("kg")[0], 502)
        modules_api.ssh_updating.add("kg")
        self.mode("")
        self.assertEqual(self.core.jarvis_update("kg"), (409, {"error": "이미 갱신하고 있습니다"}))

    def test_bulk_updates_ssh_devices_before_core_itself(self):
        """covers: SSH-10"""
        me = core.socket.gethostname().split(".")[0]
        c = core.Core(core.devices_from([{"id": "me", "url": "http://127.0.0.1:1"}, {"id": "kg", "ssh": "kg-alias"}]))
        for i, host in (("me", me), ("kg", "kg-host")):
            c.state[i].update(ok_at=time.time(), summary={"host": host, "version": "1.7.8"})
        order = []
        with mock.patch.object(c, "jarvis_update", side_effect=lambda i: (order.append(i), (200, {}))[1]):
            c.jarvis_update_all()
        self.assertEqual(order, ["kg", "me"])


if __name__ == "__main__":
    unittest.main()
