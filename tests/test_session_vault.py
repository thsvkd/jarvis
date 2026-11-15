"""세션 수집(jarvis/session_vault.py). 실제 홈·실제 ssh·실제 restic 은 쓰지 않는다: 원격은 이 python 으로 도는
'이 기기' 경로(target None)와 임시 HOME 으로, restic 은 인자·환경을 기록하는 가짜 실행 파일로 바꿔 끼운다."""
import builtins
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from unittest import mock

from jarvis import config, core, server, session_vault as sv

NOW = 2_000_000_000.0
DAY = 86400


def vault(state=None, logs=None):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "logs"))
    os.makedirs(os.path.join(d, "mirror", "a"))
    with open(os.path.join(d, "mirror", "a", "s.jsonl"), "w") as f:
        f.write("세션 원문")
    if state is not None:
        with open(os.path.join(d, "state.json"), "w") as f:
            f.write(state if isinstance(state, str) else json.dumps(state))
    for name, text in (logs or {}).items():
        with open(os.path.join(d, "logs", name), "w") as f:
            f.write(text)
    return d


def read(p):
    with open(p) as f:
        return f.read()


def settings(**kw):
    return mock.patch.dict(config._FILE, {"session_vault": {"enabled": True, **kw}})


LOG = """== 시작 2026-11-14 04:30:05
a {"ok": false, "error": "옛 실행"}
== 끝 2026-11-14 04:30:10 sync=1 backup=0 check=0
== 시작 2026-11-15 04:30:05
a {"ok": true, "started": 1, "seconds": 1, "files": 63, "bytes": 153086972, "failed": [["p/x.jsonl", "OSError"], ["y", "z"]]}
b {"ok": false, "error": "Traceback\\n  File \\"/home/u/.claude/projects/-secret-proj/s.jsonl\\"\\nReadError: empty file"}
restic: 여기는 기기 줄이 아니다 {"ok": true}
c not-json
== 끝 2026-11-15 04:30:17 sync=1 backup=0 check=3
"""


class StatusTest(unittest.TestCase):
    def test_marks_missing_stale_and_latest_run(self):
        """covers: VAULT-6. 마지막 실행만 읽고, 3일 넘은 성공·state 에 없는 기기를 표시한다"""
        d = vault({"a": NOW - DAY, "b": NOW - 4 * DAY, "gone": NOW},
                  {"nightly-20261114.log": "== 시작 x\nb {\"ok\": true}\n", "nightly-20261115.log": LOG})
        o = sv.status(["a", "b", "c"], d, NOW)
        self.assertTrue(o["available"])
        self.assertIsNone(o["error"])
        self.assertEqual(o["run"], {"name": "nightly-20261115.log", "started": "2026-11-15 04:30:05",
                                    "ended": "2026-11-15 04:30:17", "rc": {"sync": 1, "backup": 0, "check": 3}})
        a, b, c = o["devices"]
        self.assertEqual([x["id"] for x in o["devices"]], ["a", "b", "c"])
        self.assertEqual((a["missing"], a["stale"], a["last_ok"]), (False, False, NOW - DAY))
        self.assertEqual(a["last"], {"ok": True, "files": 63, "bytes": 153086972, "seconds": 1, "failed": 2, "error": None})
        self.assertEqual((b["missing"], b["stale"]), (False, True))
        self.assertEqual(b["last"]["error"], "ReadError: empty file")  # 마지막 줄만: traceback 의 경로는 내보내지 않는다
        self.assertEqual((c["missing"], c["stale"], c["last_ok"], c["last"]), (True, False, None, None))
        self.assertNotIn("p/x.jsonl", json.dumps(o))
        self.assertNotIn("secret-proj", json.dumps(o))

    def test_unfinished_run_and_unreadable_state(self):
        """covers: VAULT-6"""
        d = vault("{깨짐", {"nightly-20261116.log": "== 시작 2026-11-16 04:30:05\n"})
        o = sv.status(["a"], d, NOW)
        self.assertEqual((o["run"]["ended"], o["run"]["rc"]), (None, None))
        self.assertIn("state.json", o["error"])
        self.assertTrue(o["devices"][0]["missing"])
        o = sv.status(["a"], vault({"a": NOW}), NOW)
        self.assertEqual((o["run"], o["error"]), (None, "nightly 로그가 없습니다"))
        self.assertFalse(sv.status(["a"], d + "-없음", NOW)["available"])

    def test_error_is_capped(self):
        d = vault({}, {"nightly-20261115.log": "== 시작 t\na " + json.dumps({"ok": False, "error": "x" * 500}) + "\n"})
        self.assertEqual(len(sv.status(["a"], d, NOW)["devices"][0]["last"]["error"]), sv.ERROR_MAX)

    def test_never_opens_mirror(self):
        """covers: VAULT-6. 상태는 사본(mirror)을 열지도 나열하지도 않는다"""
        d = vault({"a": NOW}, {"nightly-20261115.log": LOG})
        seen, real_open, real_glob = [], builtins.open, sv.glob.glob

        def spy(real):
            return lambda p, *a, **kw: seen.append(str(p)) or real(p, *a, **kw)

        with mock.patch("builtins.open", spy(real_open)), mock.patch.object(sv.glob, "glob", spy(real_glob)):
            sv.status(["a"], d, NOW)
        self.assertTrue(seen)
        self.assertFalse([p for p in seen if "mirror" in p], seen)


class PayloadTest(unittest.TestCase):
    """vault.py --self-test 를 옮긴 것."""

    def payload(self):
        g = {"__name__": "self_test", "MODE": "", "SINCE": 0}
        exec(sv.PAYLOAD, g)
        return g

    def test_skip_file(self):
        """covers: VAULT-3"""
        g = self.payload()
        for name in ("auth.json", "agent-session-authority.key", "SingletonLock", "orcad-secret-store-d55.json",
                     ".credentials.json", "my-token.txt", "x.sock"):
            self.assertTrue(g["skip_file"](name), name)
        for name in ("rollout-1.jsonl", "orca-data.json"):
            self.assertFalse(g["skip_file"](name), name)

    def test_walk(self):
        """covers: VAULT-3"""
        g = self.payload()
        with tempfile.TemporaryDirectory() as home:
            for rel in (".codex/sessions/a/s.jsonl", ".codex/sessions/auth.json", ".config/orca/terminal-history/x",
                        ".config/orca/auth.json", ".config/orca/Cache/c", ".config/orca/profiles/local-default/orca-data.json",
                        ".config/orca/codex-runtime-home/home/sessions/r.jsonl", ".config/orca/codex-runtime-home/home/plugins/p",
                        ".hermes/sessions/h.json", ".hermes/profiles/p1/sessions/h.json", ".hermes/profiles/p1/.env", ".hermes/.env",
                        "Documents/other"):
                os.makedirs(os.path.dirname(os.path.join(home, rel)), exist_ok=True)
                open(os.path.join(home, rel), "w").close()
            os.symlink("/etc/hosts", os.path.join(home, ".codex/sessions/link"))  # 정규 파일만
            got = {r for _, r, _ in g["walk"](home)}
        self.assertEqual(got, {".codex/sessions/a/s.jsonl", ".codex/sessions/auth.json", ".config/orca/terminal-history/x",
                               ".config/orca/profiles/local-default/orca-data.json",
                               ".config/orca/codex-runtime-home/home/sessions/r.jsonl",
                               ".hermes/sessions/h.json", ".hermes/profiles/p1/sessions/h.json"})

    def test_safe_path(self):
        """covers: VAULT-2"""
        root = tempfile.mkdtemp()
        self.assertTrue(sv.safe_path("d", "a/b", root).startswith(os.path.join(root, "mirror", "d") + os.sep))
        for bad in ("../x", "a/../../x", "/etc/passwd", "", "."):
            with self.assertRaises(ValueError, msg=bad):
                sv.safe_path("d", bad, root)


class DevicesTest(unittest.TestCase):
    def test_follow_jarvis_devices_and_overrides(self):
        """covers: VAULT-4"""
        devs = [{"id": "dev1", "url": "http://1.2.3.4:9300"}, {"id": "n2", "ssh": "alias"}, {"id": "self", "url": "http://5.6.7.8:9300"},
                {"id": "w", "url": "http://9.9.9.9:9300"}, {"id": "nourl", "url": "nope"}]
        over = {"self": {"ssh": ""}, "w": {"python": "py -3"}, "s2": {"ssh": "u@host-b"}, "s3": {"python": "python3"}}
        with mock.patch.object(config, "DEVICES", devs), settings(devices=over):
            got, errors = sv.devices()
        self.assertEqual(got, {"dev1": ("1.2.3.4", "python3"), "n2": ("alias", "python3"), "self": (None, "python3"),
                               "w": ("9.9.9.9", "py -3"), "s2": ("u@host-b", "python3")})
        self.assertEqual(errors, ["s3: jarvis devices 에 없는 기기는 ssh 가 필요합니다"])

    def test_rejects_injection_and_path_names(self):
        """covers: VAULT-1, VAULT-2. ssh 대상·python 명령·기기 이름에 셸·옵션·경로 문자를 받지 않는다"""
        over = {"a": {"ssh": "-oProxyCommand=touch /tmp/x"}, "b": {"ssh": "h;id"}, "c": {"ssh": "h $(id)"},
                "d": {"ssh": "h", "python": "python3; id"}, "e": {"ssh": "h", "python": "-c x"}, "f": {"ssh": "h", "python": "py `id`"},
                "..": {"ssh": "h"}, ".x": {"ssh": "h"}, "g": {"ssh": 5}, "h": {"ssh": "h", "python": ""}, "i": "x",
                "ok": {"ssh": "u@h.example", "python": "py -3"}}
        with mock.patch.object(config, "DEVICES", []), settings(devices=over):
            got, errors = sv.devices()
        self.assertEqual(got, {"ok": ("u@h.example", "py -3")})
        self.assertEqual(len(errors), len(over) - 1)

    def test_command(self):
        """covers: VAULT-1"""
        cmd, script = sv.command("pi@h", "py -3", "stream", 12.5)
        self.assertEqual(cmd[0], core.SSH_BIN)
        self.assertEqual(cmd[cmd.index("--"):], ["--", "pi@h", "py", "-3", "-I", "-"])
        for o in ("BatchMode=yes", "ControlMaster=no", "ClearAllForwardings=yes"):
            self.assertIn(o, cmd)
        self.assertIn("-a", cmd)
        self.assertTrue(script.startswith(b"MODE='stream'\nSINCE=12.5\n"))
        self.assertEqual(sv.command(None, "python3", "digest", 0)[0], [sys.executable, "-I", "-"])


def tar(members):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w") as t:
        for name, data, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                t.addfile(ti)
            else:
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
    b.seek(0)
    return b


class ExtractTest(unittest.TestCase):
    def test_rejects_escape_and_skips_links(self):
        """covers: VAULT-2"""
        root = tempfile.mkdtemp()
        sv.extract("d", tar([("ok/a", b"1", "f"), ("ok/link", b"", "sym")]), root)
        base = os.path.join(root, "mirror", "d")
        self.assertEqual(read(os.path.join(base, "ok/a")), "1")
        self.assertFalse(os.path.lexists(os.path.join(base, "ok/link")))
        for bad in ("../../evil", "/tmp/evil-abs", "a/../../../evil"):
            with self.assertRaises(ValueError):
                sv.extract("d", tar([(bad, b"x", "f")]), root)
        self.assertEqual(sorted(os.listdir(root)), ["mirror"])
        self.assertEqual(sorted(os.listdir(os.path.join(root, "mirror"))), ["d"])
        self.assertFalse([f for _, _, fs in os.walk(root) for f in fs if f.startswith(".part-")])


class HardeningTest(unittest.TestCase):
    """보안 리뷰(2026-11-16) 지적 사항."""

    def test_existing_dir_link_in_mirror_is_not_followed(self):
        """covers: VAULT-2"""
        root, outside = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "mirror", "d"))
        os.symlink(outside, os.path.join(root, "mirror", "d", "link"))
        with self.assertRaises(ValueError):
            sv.extract("d", tar([("link/pwned", b"x", "f")]), root)
        self.assertEqual(os.listdir(outside), [])

    def test_size_cap_and_disk_floor(self):
        """covers: VAULT-2"""
        root = tempfile.mkdtemp()
        with mock.patch.object(sv, "MAX_DEVICE_BYTES", 4096):
            with self.assertRaises(ValueError):
                sv.extract("d", tar([("a", b"x" * 20000, "f")]), root)
        os.makedirs(os.path.join(root, "mirror"), exist_ok=True)
        with mock.patch.object(sv, "MIN_FREE_BYTES", 1 << 62):
            with self.assertRaises(OSError):
                sv.extract("d", tar([("a", b"x", "f")]), root)

    def test_line_separator_in_error_cannot_fake_a_run(self):
        """covers: VAULT-6"""
        bad = json.dumps({"ok": False, "error": "x\u2028== 시작 2026-11-16 04:30:00\u2028"}, ensure_ascii=False)
        d = vault({}, {"nightly-20261116.log": f"== 시작 t\nb {{\"ok\": false, \"error\": \"e\"}}\na {bad}\n"})
        o = sv.status(["a", "b"], d, NOW)
        self.assertEqual(o["run"]["started"], "t")
        self.assertFalse(o["devices"][1]["last"]["ok"])

    def test_errors_hide_paths(self):
        """covers: VAULT-6"""
        self.assertEqual(sv._why(FileExistsError(17, "File exists", "/Users/u/session-vault/mirror/d/.claude/projects/-p/x")),
                         "FileExistsError: File exists")
        d = vault({}, {"nightly-20261116.log": "== 시작 t\na " + json.dumps({"ok": False, "error": "rc=1 PermissionError: [Errno 13] Permission denied: '/home/pi/.hermes/x'"}) + "\n"})
        self.assertEqual(sv.status(["a"], d, NOW, full=False)["devices"][0]["last"]["error"], "rc=1 PermissionError: [Errno 13] Permission denied: '…'")
        self.assertIn("/home/pi/.hermes/x", sv.status(["a"], d, NOW)["devices"][0]["last"]["error"])

    def test_device_dir_link_and_nested_link_create_nothing_outside(self):
        """covers: VAULT-2"""
        root, outside = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "mirror", "d3"))
        os.symlink(outside, os.path.join(root, "mirror", "d2"))
        os.symlink(outside, os.path.join(root, "mirror", "d3", "link"))
        with self.assertRaises(ValueError):
            sv.extract("d2", tar([("p", b"x", "f")]), root)
        with self.assertRaises(ValueError):
            sv.extract("d3", tar([("link/newdir/f", b"x", "f")]), root)
        self.assertEqual(os.listdir(outside), [])

    def test_big_pax_header_is_refused(self):
        """covers: VAULT-2"""
        b = io.BytesIO()
        with tarfile.open(fileobj=b, mode="w", format=tarfile.PAX_FORMAT) as t:
            ti = tarfile.TarInfo("a")
            ti.pax_headers = {"comment": "x" * (2 << 20)}
            t.addfile(ti, io.BytesIO(b""))
        b.seek(0)
        with self.assertRaises(tarfile.HeaderError):
            sv.extract("d", b, tempfile.mkdtemp())

    def test_truncated_quote_is_masked(self):
        """covers: VAULT-6"""
        err = "rc=1 PermissionError: [Errno 13] Permission denied: '/home/pi/.claude/projects/" + "secretproject-" * 20
        d = vault({}, {"nightly-20261116.log": "== 시작 t\na " + json.dumps({"ok": False, "error": err}) + "\n"})
        self.assertNotIn("secretproject", sv.status(["a"], d, NOW, full=False)["devices"][0]["last"]["error"])

    def test_scheduled_mark_written_only_after_lock(self):
        """covers: VAULT-9"""
        root = tempfile.mkdtemp()
        mark = os.path.join(root, ".jarvis-scheduled")
        with mock.patch.object(config, "DEVICES", []), settings(devices={"me": {"ssh": ""}}), \
                mock.patch.object(sv, "_legacy_running", return_value=False), \
                mock.patch.dict(os.environ, {sv.SCHEDULED_ENV: "2026-11-16"}), \
                mock.patch.object(sv, "sync", return_value={"ok": False, "error": "x"}):
            held = open(os.path.join(root, ".jarvis.lock"), "a")
            sv.fcntl.flock(held, sv.fcntl.LOCK_EX)
            try:
                with self.assertRaises(sv.Busy):
                    sv.run(root=root, backup=False)
            finally:
                held.close()
            self.assertFalse(os.path.exists(mark))
            sv.run(root=root, backup=False)
        self.assertEqual(read(mark).strip(), "2026-11-16")

    def test_off_as_root(self):
        """covers: VAULT-10"""
        with settings(), mock.patch.object(sv.os, "geteuid", return_value=0):
            self.assertFalse(sv.enabled())


class SyncTest(unittest.TestCase):
    """이 기기 경로(target None)로 페이로드를 실제로 돌린다: 원격과 같은 python -I - 표준 입력 실행."""

    def setUp(self):
        self.home, self.root = tempfile.mkdtemp(), tempfile.mkdtemp()
        for rel in (".claude/projects/p/s.jsonl", ".codex/sessions/r.jsonl.zst", ".config/orca/auth.json"):
            os.makedirs(os.path.dirname(os.path.join(self.home, rel)), exist_ok=True)
            with open(os.path.join(self.home, rel), "w") as f:
                f.write(rel)
        old = time.time() - 30 * DAY
        os.utime(os.path.join(self.home, ".codex/sessions/r.jsonl.zst"), (old, old))  # 옛 mtime, 새 ctime
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_incremental_by_ctime_and_never_deletes(self):
        """covers: VAULT-3, VAULT-5"""
        keep = os.path.join(self.root, "mirror", "me", "gone.jsonl")
        os.makedirs(os.path.dirname(keep))
        open(keep, "w").close()
        r = sv.sync("me", None, "python3", time.time() - DAY, self.root)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["files"], 2)  # .zst 는 mtime 이 옛것이어도 ctime 으로 들어온다. orca auth.json 은 빠진다
        base = os.path.join(self.root, "mirror", "me")
        self.assertEqual(read(os.path.join(base, ".codex/sessions/r.jsonl.zst")), ".codex/sessions/r.jsonl.zst")
        self.assertFalse(os.path.exists(os.path.join(base, ".config/orca/auth.json")))
        self.assertTrue(os.path.exists(keep))
        self.assertEqual(sv.sync("me", None, "python3", time.time() + DAY, self.root)["files"], 0)
        self.assertTrue(sv.verify("me", None, "python3", self.root)["ok"])

    def test_run_writes_log_state_and_restic(self):
        """covers: VAULT-5, VAULT-8"""
        bindir = tempfile.mkdtemp()
        rec = os.path.join(bindir, "rec")
        with open(os.path.join(bindir, "restic"), "w") as f:
            f.write(f"#!{sys.executable}\nimport json, os, sys\nopen({rec!r}, 'a').write(json.dumps([sys.argv[1:], "
                    "{k: v for k, v in os.environ.items() if k.startswith('RESTIC_')}]) + '\\n')\n")
        os.chmod(os.path.join(bindir, "restic"), 0o755)
        pw = os.path.join(bindir, "pw")
        with open(pw, "w") as f:
            f.write("secret-value")
        with mock.patch.object(config, "DEVICES", []), settings(devices={"me": {"ssh": ""}}, restic_repository="sftp:h:repo"), \
                mock.patch.object(sv, "PASSWORD_FILE", pw), mock.patch.object(sv, "_legacy_running", return_value=False), \
                mock.patch.dict(os.environ, {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""), "RESTIC_PASSWORD": "leak"}):
            rc = sv.run(root=self.root)
        self.assertEqual(rc, {"sync": 0, "backup": 0, "check": 0})
        calls = [json.loads(x) for x in read(rec).splitlines()]
        self.assertEqual([c[0] for c in calls], [["backup", "--tag", "nightly", "--quiet", os.path.join(self.root, "mirror")],
                                                ["check", "--read-data-subset=2%", "--quiet"]])
        self.assertEqual(calls[0][1], {"RESTIC_PASSWORD_FILE": pw, "RESTIC_REPOSITORY": "sftp:h:repo"})
        self.assertFalse([c for c in calls if "forget" in c[0] or "prune" in c[0]])
        log = read(os.path.join(self.root, "logs", f"nightly-{datetime.now():%Y%m%d}.log"))
        self.assertNotIn("secret-value", log)
        o = sv.status(["me"], self.root)
        self.assertEqual((o["run"]["rc"], o["devices"][0]["last"]["files"], o["devices"][0]["missing"]),
                         ({"sync": 0, "backup": 0, "check": 0}, 2, False))

    def test_restic_without_repository_fails_backup(self):
        """covers: VAULT-8"""
        with mock.patch.object(config, "DEVICES", []), settings(devices={"me": {"ssh": ""}}), \
                mock.patch.object(sv, "_legacy_running", return_value=False):
            self.assertEqual(sv.run(root=self.root), {"sync": 0, "backup": 1, "check": 1})


class ConcurrencyTest(unittest.TestCase):
    def test_lock_and_legacy_guard(self):
        """covers: VAULT-7"""
        root = tempfile.mkdtemp()
        with mock.patch.object(config, "DEVICES", []), settings(devices={"me": {"ssh": ""}}), \
                mock.patch.object(sv, "ROOT", root), mock.patch.object(sv.subprocess, "Popen") as popen:
            with mock.patch.object(sv, "_legacy_running", return_value=True):
                with self.assertRaises(sv.Busy):
                    sv.run(root=root, backup=False)
                self.assertEqual(sv.start()[0], 409)
            with mock.patch.object(sv, "_legacy_running", return_value=False):
                held = open(os.path.join(root, ".jarvis.lock"), "a")
                sv.fcntl.flock(held, sv.fcntl.LOCK_EX)
                try:
                    with self.assertRaises(sv.Busy):
                        sv.run(root=root, backup=False)
                    self.assertEqual(sv.start()[0], 409)
                    self.assertTrue(sv.status(["me"], root)["running"])
                finally:
                    held.close()
                self.assertFalse(sv.status(["me"], root)["running"])
                popen.return_value.poll.return_value = None
                self.assertEqual(sv.start()[0], 202)
                self.assertEqual(popen.call_args[0][0], [sys.executable, "-m", "jarvis", "session-vault", "sync"])
                self.assertEqual(sv.start()[0], 409)  # 띄운 자식이 아직 돈다
                popen.return_value.poll.return_value = 0
                sv._child = None
        self.assertEqual(popen.call_count, 1)


class ScheduleTest(unittest.TestCase):
    def test_tick(self):
        """covers: VAULT-9"""
        root = tempfile.mkdtemp()
        started = []
        def fake_start(trigger, day):  # 자식이 잠금을 잡은 뒤 날짜를 적는 것을 흉내 낸다
            started.append(trigger)
            with open(os.path.join(root, ".jarvis-scheduled"), "w") as f:
                f.write(day + "\n")
            return 202, {}

        with mock.patch.object(sv, "start", side_effect=fake_start):
            with settings(schedule="04:30"):
                self.assertFalse(sv.tick(datetime(2026, 11, 16, 4, 29), root))
                self.assertTrue(sv.tick(datetime(2026, 11, 16, 4, 31), root))
                self.assertFalse(sv.tick(datetime(2026, 11, 16, 5, 0), root))  # 그날은 한 번
                self.assertFalse(sv.tick(datetime(2026, 11, 17, 11, 0), root))  # 예약 뒤 6시간이 지나면 건너뛴다
                self.assertTrue(sv.tick(datetime(2026, 11, 18, 9, 0), root))
            with settings():  # 예약 없음
                self.assertFalse(sv.tick(datetime(2026, 11, 19, 4, 31), root))
            with mock.patch.dict(config._FILE, {"session_vault": {"enabled": False, "schedule": "04:30"}}):
                self.assertFalse(sv.tick(datetime(2026, 11, 19, 4, 31), root))
        self.assertEqual(started, ["schedule", "schedule"])
        with mock.patch.object(sv, "start", return_value=(409, {})) as st, settings(schedule="04:30"):
            self.assertFalse(sv.tick(datetime(2026, 11, 20, 4, 31), root))
        st.assert_called_once_with("schedule", "2026-11-20")
        self.assertEqual(read(os.path.join(root, ".jarvis-scheduled")).strip(), "2026-11-18")


class RouteTest(unittest.TestCase):
    def setUp(self):
        class Coll:
            def allowed_hosts(self):
                return {"127.0.0.1"}

        self.coll = Coll()
        self.root = vault({"gw": NOW}, {"nightly-20261115.log": LOG})
        self.patches = [mock.patch.object(sv, "ROOT", self.root), mock.patch.object(config, "DEVICES", [{"id": "gw", "url": "http://127.0.0.1:1"}])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def call(self, k, path, post=False, headers=None):
        httpd = server.Server(("127.0.0.1", 0), server.make_handler(self.coll, k))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", method="POST" if post else "GET",
                                         headers=headers or {})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"null")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_off_by_default_and_on_core_only(self):
        """covers: VAULT-10"""
        c = core.Core([{"id": "gw", "url": "http://127.0.0.1:1"}])
        act = {"X-Jarvis-Action": "session-vault"}
        self.assertEqual(self.call(c, "/api/devices/session-vault")[0], 404)  # 설정 없음 = 꺼짐
        self.assertEqual(self.call(c, "/api/devices/session-vault/sync", True, act)[0], 404)
        with settings():
            self.assertEqual(self.call(None, "/api/devices/session-vault")[0], 404)  # core 가 아니면 켜도 없다
            code, body = self.call(c, "/api/devices/session-vault")
            self.assertEqual((code, [x["id"] for x in body["devices"]]), (200, ["gw"]))
            self.assertNotIn("mirror", json.dumps(body))
            self.assertEqual(self.call(c, "/api/devices/session-vault/sync", True)[0], 403)  # 맞춤 헤더 없음
            self.assertEqual(self.call(c, "/api/devices/session-vault/sync", True, act)[0], 403)  # viewers 아님
            kind, vw = server.client_kind, config.VIEWERS
            server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
            try:
                with mock.patch.object(sv, "start", return_value=(202, {"started": 1})) as st:
                    self.assertEqual(self.call(c, "/api/devices/session-vault/sync", True, act), (202, {"started": 1}))
                st.assert_called_once_with()
            finally:
                server.client_kind, config.VIEWERS = kind, vw

    def test_cli_off(self):
        """covers: VAULT-10"""
        with mock.patch("sys.stderr", io.StringIO()), mock.patch.object(sv, "run") as run:
            self.assertEqual(sv.cli(["sync"]), 1)
            self.assertEqual(sv.cli(["nope"]), 1)
            with settings(devices={"me": {"ssh": ""}}), mock.patch.object(config, "DEVICES", []):  # core 가 아니다
                self.assertEqual(sv.cli(["sync"]), 1)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
