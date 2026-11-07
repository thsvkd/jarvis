"""core-trust CLI: 임시 폴더의 JARVIS_CONFIG 만 고친다. tailscale 조회는 대체하고 실제 설정·tailscale·기기는 쓰지 않는다."""
import contextlib
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

from jarvis import core_trust, module

ROOT = pathlib.Path(__file__).resolve().parents[1]
OLD, NEW, OTHER = "100.64.0.1", "100.100.1.2", "100.90.0.3"
SECRET = "pk-test-very-secret-0123456789"
STATUS = {"Self": {"HostName": "old-core", "TailscaleIPs": [OLD, "fd7a:115c:a1e0::1"]},
          "Peer": {"n1": {"HostName": "thsvkd-macmini", "DNSName": "thsvkd-macmini.tail-test.ts.net.",
                          "TailscaleIPs": [NEW]},
                   "n2": {"HostName": "pit5", "TailscaleIPs": [OTHER]}}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.path = self.dir / "config.toml"
        env = mock.patch.dict(os.environ, {"JARVIS_CONFIG": str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("JARVIS_CORE", None)
        self.ts = mock.patch.object(core_trust, "run", return_value=(0, json.dumps(STATUS), ""))
        self.run_mock = self.ts.start()
        self.addCleanup(self.ts.stop)

    def write(self, text, mode=0o600):
        self.path.write_bytes(text.encode())
        os.chmod(self.path, mode)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = core_trust.cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def plan_apply(self, op, ip):
        p = core_trust.plan(op, ip)
        return p, core_trust.apply(op, ip, p["plan_id"])

    def reasons(self, p):
        return " ".join(b["reason"] for b in p["blocked"])


class Targets(Base):
    def test_add_rejects_non_canonical_tailnet_ipv4_and_unknown_host(self):
        """covers: CORE-1"""
        self.write(f'core = ["{OLD}"]\n')
        before = self.path.read_bytes()
        for bad in ("fd7a:115c:a1e0::1", "127.0.0.1", "192.168.0.5", "100.64.0.01", "100.064.0.1", "not-an-ip", "8.8.8.8"):
            p, r = self.plan_apply("add", bad)
            self.assertIn("정규형", self.reasons(p), bad)
            self.assertEqual(r["status"], "failed", bad)
        self.run_mock.return_value = (0, json.dumps({"Self": {}, "Peer": {}}), "")
        p, r = self.plan_apply("add", NEW)
        self.assertIn("호스트명", self.reasons(p))
        self.assertEqual(r["status"], "failed")
        self.run_mock.return_value = (1, "", "tailscaled down")
        self.assertIn("호스트명", self.reasons(core_trust.plan("add", NEW)))
        self.assertEqual(self.path.read_bytes(), before)

    def test_host_is_in_plan_and_plan_id(self):
        """covers: CORE-1"""
        self.write(f'core = ["{OLD}"]\n')
        p = core_trust.plan("add", NEW)
        self.assertEqual(p["blocked"], [])
        self.assertEqual(p["target"], {"ip": NEW, "host": "thsvkd-macmini.tail-test.ts.net"})  # 유일한 MagicDNS 이름
        self.assertEqual(p["after"], [NEW, OLD])
        self.assertEqual(core_trust.plan("add", OTHER)["target"]["host"], "pit5")  # DNSName 이 없으면 HostName
        other = json.loads(json.dumps(STATUS))
        other["Peer"]["n1"]["DNSName"] = "thsvkd-macmini-1.tail-test.ts.net."
        self.run_mock.return_value = (0, json.dumps(other), "")
        self.assertNotEqual(core_trust.plan("add", NEW)["plan_id"], p["plan_id"])
        self.run_mock.return_value = (0, json.dumps({"Self": {}, "Peer": {}}), "")
        r = core_trust.plan("remove", OLD)  # 사라진 옛 core 는 호스트명 없이도 뺄 수 있다
        self.assertEqual(r["target"], {"ip": OLD, "host": None})

    def test_remove_without_host_is_not_blocked(self):
        """covers: CORE-1"""
        self.write(f'core = ["{OLD}", "{OTHER}"]\n')
        self.run_mock.return_value = (0, json.dumps({"Self": {}, "Peer": {}}), "")
        p, r = self.plan_apply("remove", OLD)
        self.assertEqual((p["blocked"], p["target"]), ([], {"ip": OLD, "host": None}))
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(self.path.read_text(), f'core = ["{OTHER}"]\n')

    def test_result_without_valid_tailnet_entry_is_blocked(self):
        """covers: CORE-1"""
        self.write(f'core = ["{OLD}", "192.168.0.5"]\n')
        before = self.path.read_bytes()
        p, r = self.plan_apply("remove", OLD)
        self.assertEqual(p["after"], ["192.168.0.5"])
        self.assertIn("유효한 IPv4 tailnet", self.reasons(p))
        self.assertEqual(r["status"], "failed")
        self.write('core = ["100.64.0.01"]\n')
        self.assertIn("유효한 IPv4 tailnet", self.reasons(core_trust.plan()))
        self.write("port = 9300\n")
        self.assertIn("유효한 IPv4 tailnet", self.reasons(core_trust.plan()))
        self.write(f'core = ["{OLD}", "192.168.0.5"]\n')
        self.assertEqual(self.path.read_bytes(), before)


class Blocked(Base):
    def assert_blocked(self, word, op="add", ip=NEW):
        snap = self.path.read_bytes() if self.path.exists() and not self.path.is_symlink() else None
        p, r = self.plan_apply(op, ip)
        self.assertIn(word, self.reasons(p))
        self.assertEqual(r["status"], "failed")
        if snap is not None:
            self.assertEqual(self.path.read_bytes(), snap)

    def test_unusable_files_are_blocked(self):
        """covers: CORE-2"""
        self.assert_blocked("없습니다")
        real = self.dir / "real.toml"
        real.write_text(f'core = ["{OLD}"]\n')
        self.path.symlink_to(real)
        self.assert_blocked("심볼릭 링크")
        self.assertEqual(real.read_text(), f'core = ["{OLD}"]\n')
        self.path.unlink()
        self.write('core = ["' + OLD + '"\n')
        self.assert_blocked("TOML 오류")
        self.path.write_bytes(b"\xef\xbb\xbf" + f'core = ["{OLD}"]\n'.encode())
        self.assert_blocked("TOML 오류")

    def test_unsupported_core_lines_are_blocked(self):
        """covers: CORE-2"""
        cases = {
            "여러 줄": f'core = [\n  "{OLD}",\n]\n',
            "둘 이상": f'note = """\ncore = ["{OTHER}"]\n"""\ncore = ["{OLD}"]\n',
            "꼬리 주석": f'core = ["{OLD}"]  # 옛 core\n',
            "맞지 않": f'[core]\nx = 1\n',
        }
        for word, text in cases.items():
            self.write(text)
            self.assert_blocked(word)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "POSIX 일반 사용자 전용")
    def test_unwritable_directory_and_env_override_are_blocked(self):
        """covers: CORE-2"""
        self.write(f'core = ["{OLD}"]\n')
        os.chmod(self.dir, 0o500)
        try:
            self.assert_blocked("쓰기 권한")
        finally:
            os.chmod(self.dir, 0o700)
        with mock.patch.dict(os.environ, {"JARVIS_CORE": OLD}):
            self.assert_blocked("JARVIS_CORE")

    @unittest.skipUnless(hasattr(os, "geteuid"), "POSIX 전용")
    def test_running_as_another_user_is_blocked(self):
        """covers: CORE-2"""
        self.write(f'core = ["{OLD}"]\n')
        with mock.patch.object(core_trust.os, "geteuid", return_value=os.stat(self.path).st_uid + 1):
            self.assert_blocked("소유자")


class Write(Base):
    def test_replaces_only_top_level_core_line(self):
        """covers: CORE-3"""
        text = (f'# 기기 설정\r\nport = 9300\r\ncore = ["{OLD}"]\r\n# 남길 주석\r\nproxy_key = "{SECRET}"\r\n'
                '\r\n[infra_units]\r\ndocker = ""\r\n')
        self.write(text, 0o640)
        p, r = self.plan_apply("add", NEW)
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(r["after"], [NEW, OLD])
        self.assertEqual(self.path.read_bytes(), text.replace(f'core = ["{OLD}"]', f'core = ["{NEW}", "{OLD}"]').encode())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)
        self.assertEqual(os.listdir(self.dir), ["config.toml"])  # 임시 파일·백업이 남지 않는다

    def test_inserts_before_first_table_and_rewrites_comma_string(self):
        """covers: CORE-3"""
        self.write('port = 9300\n[infra_units]\ndocker = ""')
        self.assertEqual(self.plan_apply("add", NEW)[1]["status"], "done")
        self.assertEqual(self.path.read_text(), f'port = 9300\ncore = ["{NEW}"]\n[infra_units]\ndocker = ""')
        self.write(f'viewers = ["{OTHER}"]')
        self.assertEqual(self.plan_apply("add", NEW)[1]["status"], "done")
        self.assertEqual(self.path.read_text(), f'viewers = ["{OTHER}"]\ncore = ["{NEW}"]\n')
        self.write(f'core = "{OLD}, {OTHER}"\n')
        self.assertEqual(self.plan_apply("add", NEW)[1]["status"], "done")
        self.assertEqual(self.path.read_text(), f'core = ["{NEW}", "{OLD}", "{OTHER}"]\n')
        self.assertEqual(self.plan_apply("remove", OLD)[1]["status"], "done")
        self.assertEqual(self.path.read_text(), f'core = ["{NEW}", "{OTHER}"]\n')

    def test_crlf_insert_before_indented_table(self):
        """covers: CORE-3"""
        text = 'port = 9300\r\n  [infra_units]\r\n  docker = ""\r\n'
        self.write(text)
        self.assertEqual(self.plan_apply("add", NEW)[1]["status"], "done")
        self.assertEqual(self.path.read_bytes(), f'port = 9300\r\ncore = ["{NEW}"]\r\n  [infra_units]\r\n  docker = ""\r\n'.encode())
        text = f'core = ["{OLD}"]\n  [infra_units]\n  core = "x"\n'  # 테이블 안의 core 는 최상위가 아니다
        self.write(text)
        self.assertEqual(self.plan_apply("add", NEW)[1]["status"], "done")
        self.assertEqual(self.path.read_text(), text.replace(f'core = ["{OLD}"]', f'core = ["{NEW}", "{OLD}"]'))

    def test_failed_verification_keeps_original(self):
        """covers: CORE-3"""
        text = f'core = ["{OLD}"]\nport = 9300\n'
        self.write(text)
        real = core_trust._write

        def corrupt(ctx, after):
            ctx = {**ctx, "lines": [l.replace(b"9300", b"9301") for l in ctx["lines"]]}
            return real(ctx, after)

        with mock.patch.object(core_trust, "_write", corrupt):
            r = self.plan_apply("add", NEW)[1]
        self.assertEqual(r["status"], "failed")
        self.assertEqual(self.path.read_text(), text)
        self.assertEqual(os.listdir(self.dir), ["config.toml"])


class Stale(Base):
    def test_file_changed_just_before_replace_is_kept(self):
        """covers: CORE-4"""
        self.write(f'core = ["{OLD}"]\n')
        changed = f'core = ["{OLD}"]\nport = 9301\n'
        real = core_trust._write

        def racing(ctx, after):
            self.path.write_text(changed)  # 계획·재계획 뒤, 교체 직전에 다른 손이 고친다
            return real(ctx, after)

        with mock.patch.object(core_trust, "_write", racing):
            r = self.plan_apply("add", NEW)[1]
        self.assertEqual(r["status"], "failed")
        self.assertIn("교체 직전", r["steps"][0]["message"])
        self.assertEqual(self.path.read_text(), changed)
        self.assertEqual(os.listdir(self.dir), ["config.toml"])


    def test_changed_file_or_other_device_is_stale_and_target_is_current(self):
        """covers: CORE-4"""
        self.write(f'core = ["{OLD}"]\nproxy_key = "{SECRET}"\n')
        p = core_trust.plan("add", NEW)
        self.write(f'core = ["{OLD}"]\nproxy_key = "{SECRET}"\nport = 9300\n')
        snap = self.path.read_bytes()
        self.assertEqual(core_trust.apply("add", NEW, p["plan_id"])["status"], "stale")
        self.assertEqual(self.path.read_bytes(), snap)
        p = core_trust.plan("add", NEW)
        with mock.patch.object(core_trust.socket, "gethostname", return_value="other-device"):
            self.assertEqual(core_trust.apply("add", NEW, p["plan_id"])["status"], "stale")
        self.assertEqual(self.path.read_bytes(), snap)
        code, out, _ = self.cli("apply", "--add", NEW, "--plan-id", "0" * 64, "--json")
        self.assertEqual((code, json.loads(out)["status"]), (module.EXIT["stale"], "stale"))
        # proxy_key 값만 바뀐 경우는 stale 이 아니고 바뀐 키를 보존한다
        self.write(f'core = ["{OLD}"]\nproxy_key = "rotated-key"\nport = 9300\n')
        self.assertEqual(core_trust.apply("add", NEW, p["plan_id"])["status"], "done")
        self.assertIn('proxy_key = "rotated-key"', self.path.read_text())
        p = core_trust.plan("add", NEW)
        snap = self.path.read_bytes()
        self.assertEqual(p["steps"], [])
        self.assertEqual(core_trust.apply("add", NEW, p["plan_id"])["status"], "current")
        self.assertEqual(self.path.read_bytes(), snap)


class Secrets(Base):
    def test_proxy_key_never_leaves_and_hash_ignores_its_value(self):
        """covers: CORE-5"""
        self.write(f'core = ["{OLD}"]\nproxy_key = "{SECRET}"\nhermes_url = "https://hermes.example/{SECRET}x"\n')
        code, out, err = self.cli("plan", "--add", NEW, "--json")
        self.assertEqual(code, 0)
        p = json.loads(out)
        self.assertEqual(p["proxy_key"], "set")
        self.write(f'core = ["{OLD}"]\nproxy_key = "{SECRET[:-1]}X"\nhermes_url = "https://hermes.example/{SECRET}x"\n')
        q = core_trust.plan("add", NEW)
        self.assertEqual((q["sha256"], q["plan_id"]), (p["sha256"], p["plan_id"]))
        code, out2, err2 = self.cli("apply", "--add", NEW, "--plan-id", p["plan_id"], "--json")
        self.assertEqual((code, json.loads(out2)["status"]), (0, "done"))
        self.write(f'core = ["{OLD}"]\nproxy_key = "{SECRET}"\nbad = "\\q{SECRET}"\n')  # TOML 오류
        code, out3, err3 = self.cli("plan", "--json")
        self.assertIn("TOML 오류", out3)
        with mock.patch.object(core_trust, "_analyze", side_effect=RuntimeError(SECRET)):
            code, out4, err4 = self.cli("plan", "--json")
        self.assertEqual(code, 1)
        for text in (out, err, out2, err2, out3, err3, out4, err4):
            self.assertNotIn(SECRET, text)
            self.assertNotIn("hermes.example", text)
        self.assertEqual(os.listdir(self.dir), ["config.toml"])  # 백업 없음

    def test_multiline_proxy_key_value_is_not_in_hash(self):
        """covers: CORE-5"""
        def ids(key, tail="", q='"""'):
            self.write(f'core = ["{OLD}"]\nproxy_key = {q}\n{key}\n{q}\n{tail}')
            p = core_trust.plan("add", NEW)
            return p["sha256"], p["plan_id"]
        self.assertEqual(ids(SECRET), ids(SECRET + "2"))
        self.assertEqual(ids(SECRET, q="\x27" * 3), ids("x" + SECRET, q="\x27" * 3))
        self.assertNotEqual(ids(SECRET), ids(SECRET, "port = 9300\n"))  # 값 뒤의 줄은 그대로 반영한다


class NoHttpPath(unittest.TestCase):
    def test_core_trust_is_not_a_module_or_route(self):
        """covers: CORE-6"""
        self.assertNotIn("core-trust", module.MODULES)
        self.assertNotIn("core_trust", module.MODULES)
        for f in [*(ROOT / "jarvis").glob("*.py"), *(ROOT / "jarvis" / "modules").glob("*/contract.py")]:
            if f.name in ("core_trust.py", "__main__.py"):
                continue
            self.assertIsNone(re.search(r"core[-_]trust", f.read_text(encoding="utf-8")), f.name)


class EntryPoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {"JARVIS_NO_RELEASE": "1"}):
            import jarvis.__main__ as m
        cls.m = m

    def main(self, *argv):
        m = self.m
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["jarvis", *argv]), \
                mock.patch.object(m.tray, "make_icon", return_value=None), \
                mock.patch.object(m, "Collector", side_effect=RuntimeError("게이트웨이 기동")), \
                mock.patch.object(m.self_update, "cli", return_value=11), \
                mock.patch("jarvis.module.cli", return_value=12), \
                mock.patch.object(m, "agentsmd", return_value=13), \
                mock.patch.object(core_trust, "cli", return_value=14), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            return m.main(), err.getvalue()

    def test_unknown_first_argument_does_not_start_gateway(self):
        """covers: CORE-8"""
        for typo in ("core-trsut", "updat", "--help", "plan", ""):
            code, err = self.main(typo, "--json")
            self.assertEqual(code, 1, typo)
            self.assertIn("사용법", err)
        for argv, code in ((["update"], 11), (["module", "orca", "plan"], 12), (["agentsmd"], 13),
                           (["core-trust", "plan"], 14), (["--version"], 0)):
            self.assertEqual(self.main(*argv)[0], code, argv)
        with self.assertRaisesRegex(RuntimeError, "게이트웨이 기동"):
            self.main()

    def test_core_trust_usage_errors(self):
        """covers: CORE-8"""
        for argv in ([], ["plan", "--add"], ["plan", "--add", NEW, "--remove", OLD], ["apply", "--add", NEW],
                     ["apply", "--plan-id", "x"], ["plan", "--plan-id", "x"], ["plan", "--bogus"]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(core_trust.cli(argv), 1, argv)
            self.assertIn("사용법", err.getvalue())
            self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
