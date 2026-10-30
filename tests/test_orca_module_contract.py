"""Orca 모듈 계약(docs/SPEC.md): status·plan 은 아무것도 바꾸지 않고 정해진 형식으로 답한다. plan_id 는 계획 내용에만 묶인다."""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from jarvis import module, orca_module


def tree(root):
    """경로 → 내용 해시(디렉터리는 None). 같은 크기로 다시 쓴 파일과 새 파일을 모두 잡는다."""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs:
            out[os.path.join(d, n)] = None
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[p] = hashlib.sha256(f.read()).hexdigest()
    return out


class Fixture(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-orca-contract-"))
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"tool_installs": {"orca": {"method": "deb", "version": "1.4.240", "skills_state": "done",
                                                                  "skills_added": {"orca-cli": "digest"}},
                                                         "orca_server": {"state": "configured", "service_manager": "systemd",
                                                                         "pairing_codes_path": "/secret/pairing-codes.txt",
                                                                         "desktop_environment_id": "env-1"}},
                                       "hooks": {"keep": True}}))
        state = self.home / "state"  # jarvis 상태 폴더도 비교 대상이다(실제 ~/.local/state/jarvis 대신)
        state.mkdir()
        (state / "orca-skills-version").write_text("1.4.240")  # 번들 스킬은 지금 설치본의 판으로 맞춰져 있다
        patches = [
            mock.patch.object(orca_module.orca_update.config, "STATE_DIR", str(state)),
            mock.patch.object(orca_module.orca_install, "SKILLS_MARK", str(state / "orca-skills-version")),
            mock.patch.object(orca_module.tools(), "home_dir", lambda: self.home),
            mock.patch.object(orca_module.orca_install, "installed", lambda: "1.4.240"),
            mock.patch.object(orca_module.orca_install, "profile",
                              lambda: {"kind": "linux-deb", "root": "/opt/Orca", "cli": "/opt/Orca/resources/bin/orca-ide"}),
            mock.patch.object(orca_module.orca_update, "check",
                              lambda: {"supported": True, "installed": "1.4.240", "latest": "1.4.242", "current": False,
                                       "reason": None, "error": None}),
            mock.patch.object(orca_module.orca_rpc, "call", lambda method, *a, **k: {"appVersion": "1.4.240"}),
            mock.patch.object(orca_module.orca_serve, "form", lambda cli, profile, plat=None: ("same", None)),
            mock.patch.object(orca_module.orca_serve, "linger", lambda: True),
            mock.patch.object(orca_module.sys, "platform", "linux"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class Contract(Fixture):
    def test_status_and_plan_change_nothing_and_answer_in_the_contract_shape(self):
        """covers: ORCA-9"""
        before = tree(self.home)
        st = orca_module.status()
        pl = orca_module.plan()
        self.assertEqual(tree(self.home), before)
        self.assertEqual({"module", "installed", "owned", "version", "latest", "health", "details"} - set(st), set())
        self.assertEqual((st["installed"], st["owned"], st["version"], st["latest"], st["health"]),
                         (True, True, "1.4.240", "1.4.242", "ok"))
        self.assertEqual(st["details"]["server"], {"state": "configured", "service_manager": "systemd"})
        self.assertNotIn("secret", json.dumps(st))
        self.assertEqual(pl["action"], "update")
        self.assertEqual(pl["plan_id"], module.plan_id(pl))
        self.assertEqual([s["id"] for s in pl["steps"]], ["package", "skills"])
        self.assertEqual(pl["steps"][0]["risk"], "sudo")
        self.assertEqual(pl["steps"][1]["argv"][1:], ["skills", "update", "--all"])
        for s in pl["steps"]:
            self.assertIn(s["risk"], ("read", "update", "install", "remove", "sudo", "config", "external"))

    def test_plan_id_is_stable_and_follows_the_plan_content(self):
        """covers: ORCA-9"""
        first, again = orca_module.plan(), orca_module.plan()
        self.assertEqual(first["plan_id"], again["plan_id"])
        with mock.patch.object(orca_module.orca_update, "check",
                               lambda: {"supported": False, "latest": "1.4.243", "current": False, "reason": "막힘", "error": None}):
            later = orca_module.plan()
        self.assertNotEqual(later["plan_id"], first["plan_id"])
        self.assertEqual(later["blocked"], [{"step": "package", "reason": "막힘"}])

    def test_an_elevated_server_is_degraded_not_down(self):
        """Windows S4U 서버는 살아 있지만 일반 권한의 RPC 가 거부된다(runtime_access_denied)."""
        def denied(method, *a, **k):
            raise orca_module.orca_rpc.Elevated("Orca RPC 실패: runtime_access_denied")
        with mock.patch.object(orca_module.orca_rpc, "call", denied):
            st = orca_module.status()
        self.assertEqual(st["health"], "degraded")
        self.assertIn("elevated server", st["details"]["error"])

    def test_plan_updates_skills_on_an_orca_jarvis_did_not_install(self):
        """covers: ORCA-8 (jarvis 가 깔지 않은 Orca 도 앱을 올리면 번들 스킬을 모두 갱신한다)"""
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {}}))
        self.assertEqual([s["id"] for s in orca_module.plan()["steps"]], ["package", "skills", "server"])

    def test_plan_adopts_a_same_shape_server_creates_a_missing_one_and_blocks_a_different_one(self):
        """covers: ORCA-2 (계획 단계)"""
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {}}))
        current = {"supported": True, "latest": "1.4.240", "current": True, "reason": None, "error": None}
        with mock.patch.object(orca_module.orca_update, "check", lambda: current):
            adopt = orca_module.plan()
            with mock.patch.object(orca_module.orca_serve, "form", lambda cli, profile, plat=None: ("missing", None)), \
                    mock.patch.object(orca_module.orca_serve, "linger", lambda: False):
                create = orca_module.plan()
            with mock.patch.object(orca_module.orca_serve, "form", lambda cli, profile, plat=None: ("different", "러너")):
                other = orca_module.plan()
        self.assertEqual((adopt["action"], [(s["id"], s["risk"]) for s in adopt["steps"]]), ("reconfigure", [("server", "update")]))
        self.assertEqual([(s["id"], s["risk"]) for s in create["steps"]], [("server", "config"), ("linger", "config")])
        self.assertEqual((other["steps"], other["blocked"]), ([], [{"step": "server", "reason": "러너"}]))

    def test_plan_id_is_bound_to_the_device(self):
        """covers: ORCA-13 (한 기기의 승인을 다른 기기에 쓰지 못한다)"""
        here = orca_module.plan()
        with mock.patch.object(orca_module.socket, "gethostname", lambda: "other-device"):
            there = orca_module.plan()
        self.assertEqual(there["device"], "other-device")
        self.assertNotEqual(here["plan_id"], there["plan_id"])

    def test_cli_prints_one_json_document_and_sends_module_logs_to_stderr(self):
        """covers: ORCA-9"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(orca_module, "status", lambda: (print("진행 로그"), {"module": "orca"})[1]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.cli(["orca", "status", "--json"])
        self.assertEqual((code, json.loads(out.getvalue())), (0, {"module": "orca"}))
        self.assertIn("진행 로그", err.getvalue())

    def test_cli_refuses_apply_without_a_plan_id(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.cli(["orca", "apply", "--json"]), 1)
        self.assertEqual(out.getvalue(), "")


class Apply(Fixture):
    """apply·uninstall: 승인한 계획과 같을 때만 실행하고, 받아들인 서버는 지우지 않는다."""

    def receipt(self):
        return json.loads((self.home / ".claude/.claude-code-setup.json").read_text())

    def updater(self, job):
        started = []
        return started, [mock.patch.object(orca_module.orca_update, "start", lambda expect=None: (started.append(expect), (202, {}))[1]),
                         mock.patch.object(orca_module.orca_update, "snapshot", lambda: job)]

    def test_a_plan_id_that_no_longer_matches_runs_nothing_and_is_stale(self):
        """covers: ORCA-13"""
        approved = orca_module.plan()["plan_id"]
        started, patches = self.updater({"state": "done"})
        with patches[0], patches[1]:
            with mock.patch.object(orca_module.orca_update, "check",
                                   lambda: {"supported": True, "latest": "1.4.243", "current": False, "reason": None, "error": None}):
                out = orca_module.apply(approved)
            with mock.patch.object(orca_module.socket, "gethostname", lambda: "other-device"):
                elsewhere = orca_module.apply(approved)
        self.assertEqual((out["status"], out["steps"], elsewhere["status"]), ("stale", [], "stale"))
        self.assertEqual(started, [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.cli(["orca", "apply", "--json", "--plan-id", "0" * 64]), 3)

    def test_an_update_held_for_open_terminals_is_deferred_with_exit_code_2(self):
        """covers: ORCA-10"""
        reason = "Deferred: 데몬 밖 터미널 또는 확인할 수 없는 세션이 있어 업데이트를 보류합니다"
        started, patches = self.updater({"state": "failed", "deferred": True, "error": reason})
        with patches[0], patches[1]:
            out = orca_module.apply(orca_module.plan()["plan_id"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = module.cli(["orca", "apply", "--json", "--plan-id", orca_module.plan()["plan_id"]])
        self.assertEqual((out["status"], out["steps"][0]["result"]), ("deferred", "deferred"))
        self.assertIn("보류", out["steps"][0]["message"])
        self.assertEqual((code, json.loads(buf.getvalue())["status"]), (2, "deferred"))

    def test_apply_adopts_the_running_server_without_touching_it_and_keeps_other_keys(self):
        """covers: ORCA-2"""
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {}, "hooks": {"keep": True}}))
        current = {"supported": True, "latest": "1.4.240", "current": True, "reason": None, "error": None}
        with mock.patch.object(orca_module.orca_update, "check", lambda: current), \
                mock.patch.object(orca_module.orca_serve, "adopted_record",
                                  lambda cli, profile, plat=None: {"state": "configured", "adopted": True, "service_path": "/u"}), \
                mock.patch.object(orca_module.orca_serve, "write", lambda *a, **k: (_ for _ in ()).throw(SystemExit("adopted unit rewritten"))):
            out = orca_module.apply(orca_module.plan()["plan_id"])
            again = orca_module.plan()
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.receipt()["tool_installs"]["orca_server"]["adopted"], True)
        self.assertEqual(self.receipt()["hooks"], {"keep": True})
        self.assertEqual(again["steps"], [])

    def test_a_failed_step_stops_the_steps_after_it(self):
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {}}))
        started, patches = self.updater({"state": "failed", "error": "해시가 다릅니다"})
        servers = []  # self.fail 은 apply 의 except Exception 에 삼켜지므로 호출을 기록해 단언한다
        with patches[0], patches[1], mock.patch.object(orca_module, "_server", lambda cli: servers.append(cli) or ("ok", "")):
            out = orca_module.apply(orca_module.plan()["plan_id"])
        self.assertEqual((out["status"], [s["id"] for s in out["steps"]], servers), ("failed", ["package"], []))

    def test_uninstall_keeps_an_adopted_unit_and_drops_only_its_record(self):
        """covers: ORCA-11"""
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {"orca_server": {"state": "configured", "adopted": True,
                                                                          "service_path": "/home/u/.config/systemd/user/orca-serve.service"}},
                                       "hooks": {"keep": True}}))
        with mock.patch.object(orca_module.orca_serve, "run", lambda *a, **k: self.fail("adopted unit stopped")):
            dry = orca_module.uninstall(dry_run=True)
            self.assertIn("orca_server", self.receipt()["tool_installs"])
            self.assertEqual(orca_module.uninstall(plan_id="0" * 64)["status"], "stale")  # 미리 본 계획 없이는 지우지 않는다
            out = orca_module.uninstall(plan_id=dry["plan_id"])
        self.assertEqual((dry["status"], out["status"]), ("done", "done"))
        self.assertIn("preserved adopted unit", out["steps"][0]["message"])
        self.assertEqual(self.receipt(), {"tool_installs": {}, "hooks": {"keep": True}})

    def test_uninstall_keeps_a_unit_changed_after_we_wrote_it_and_the_receipt(self):
        """covers: ORCA-11"""
        unit = self.home / ".config/systemd/user/orca-serve.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("edited by the user")
        record = {"state": "configured", "service_path": str(unit), "service_digest": "0" * 64}
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {"orca_server": record}}))
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            out = orca_module.uninstall(plan_id=orca_module.uninstall(dry_run=True)["plan_id"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(module.cli(["orca", "uninstall", "--json"]), 1)  # --plan-id 없이는 거절
                code = module.cli(["orca", "uninstall", "--json", "--plan-id", out["plan_id"]])
        self.assertEqual((out["status"], code), ("deferred", 2))
        self.assertEqual(unit.read_text(), "edited by the user")
        self.assertEqual(self.receipt()["tool_installs"]["orca_server"], record)

    CURRENT = {"supported": True, "latest": "1.4.240", "current": True, "reason": None, "error": None}

    def test_a_skills_only_plan_never_runs_the_package_update(self):
        """covers: ORCA-13 (X4) — 매니페스트가 바뀌어도 skills 만 승인한 계획은 패키지를 받거나 설치하지 않는다."""
        (self.home / "state/orca-skills-version").write_text("1.4.239")  # 번들 스킬을 마지막으로 맞춘 판이 설치본과 다르다
        started, patches = self.updater({"state": "done"})
        with mock.patch.object(orca_module.orca_update, "check", lambda: self.CURRENT), patches[0], patches[1], \
                mock.patch.object(orca_module.orca_install, "skills", lambda: (["orca-cli"], [])):
            pl = orca_module.plan()
            self.assertEqual([s["id"] for s in pl["steps"]], ["skills"])
            out = orca_module.apply(pl["plan_id"])
        self.assertEqual((out["status"], started), ("done", []))

    def test_the_package_step_is_bound_to_its_target_version(self):
        """covers: ORCA-13 (X4) — 갱신은 계획한 목표 버전을 orca_update 에 넘긴다."""
        started, patches = self.updater({"state": "done"})
        with patches[0], patches[1]:
            pl = orca_module.plan()
            orca_module.apply(pl["plan_id"])
        self.assertEqual((pl["steps"][0]["target"], started), ("1.4.242", ["1.4.242"]))

    def test_a_device_whose_skills_match_its_version_reaches_current(self):
        """covers: ORCA-8 (X5) — 같은 판에서 이미 맞춘 스킬은 다시 계획하지 않는다(맞춘 판은 Fixture 가 1.4.240 으로 둔다)."""
        with mock.patch.object(orca_module.orca_update, "check", lambda: self.CURRENT):
            pl = orca_module.plan()
            self.assertEqual((pl["steps"], orca_module.apply(pl["plan_id"])["status"]), ([], "current"))

    def test_a_blocked_package_runs_nothing_for_it_and_reports_failure(self):
        """covers: ORCA-9 (X5) — 막힌 패키지 단계는 갱신 작업을 시작하지 않고, 결과는 계획을 다 하지 못한 실패다."""
        blocked = {"supported": False, "latest": "1.4.242", "current": False, "reason": "비밀번호 없는 sudo 가 없습니다", "error": None}
        started, patches = self.updater({"state": "done"})
        with mock.patch.object(orca_module.orca_update, "check", lambda: blocked), patches[0], patches[1], \
                mock.patch.object(orca_module.orca_install, "skills", lambda: (["orca-cli"], [])):
            pl = orca_module.plan()
            out = orca_module.apply(pl["plan_id"])
        self.assertEqual(started, [])
        self.assertEqual([(s["id"], s["result"]) for s in out["steps"]], [("package", "skipped"), ("skills", "ok")])
        self.assertEqual(out["status"], "failed")

    def test_windows_plans_have_no_posix_server_step_and_install_passes_the_target(self):
        """covers: ORCA-1 (X2) — Windows 설치는 옮긴 설치기가 S4U 서버까지 한다. 목표 버전을 넘긴다."""
        calls = []
        with mock.patch.object(orca_module.sys, "platform", "win32"), \
                mock.patch.object(orca_module.platform, "machine", lambda: "AMD64"), \
                mock.patch.object(orca_module.orca_install, "installed", lambda: (_ for _ in ()).throw(OSError("없음"))), \
                mock.patch.object(orca_module.orca_update.Live, "manifest", lambda self: ("1.4.244", {})), \
                mock.patch.object(orca_module.orca_serve, "form", lambda *a, **k: (_ for _ in ()).throw(KeyError("argv"))), \
                mock.patch.object(orca_module.tools(), "main", lambda argv: calls.append(argv) or 0):
            pl = orca_module.plan()
            out = orca_module.apply(pl["plan_id"])
        self.assertEqual([s["id"] for s in pl["steps"]], ["package", "skills"])
        self.assertEqual((out["status"], calls), ("done", [["--expect-version", "1.4.244"]]))

    def test_install_does_not_overwrite_another_server_record(self):
        """X8 — 예전 ai-setup 서버 기록을 덮어쓰면 uninstall 이 그 서버를 더는 정리하지 못한다."""
        old = {"state": "configured", "service_manager": "systemd-user", "service_root": "/old", "desktop_environment_id": "env"}
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {"orca_server": old}}))
        self.assertEqual(orca_module._server("/opt/Orca/resources/bin/orca-ide")[0], "failed")
        self.assertEqual(self.receipt()["tool_installs"]["orca_server"], old)

    def test_linger_we_enabled_is_recorded_and_disabled_on_uninstall(self):
        """ORCA-11 — 우리가 켠 linger 만 다시 끈다."""
        calls = []
        fake = lambda argv, timeout=6, sudo=False: (calls.append(argv), (0, "", ""))[1]  # noqa: E731
        receipt = self.home / ".claude/.claude-code-setup.json"
        receipt.write_text(json.dumps({"tool_installs": {}}))
        with mock.patch.object(orca_module, "run", fake):
            self.assertEqual(orca_module._linger({"argv": ["loginctl", "enable-linger", "u"]})[0], "ok")
            self.assertTrue(self.receipt()["tool_installs"]["orca_linger"])
            orca_module.uninstall(plan_id=orca_module.uninstall(dry_run=True)["plan_id"])
        self.assertEqual(calls[-1][:2], ["loginctl", "disable-linger"])
        self.assertNotIn("orca_linger", self.receipt()["tool_installs"])
        receipt.write_text(json.dumps({"tool_installs": {}}))
        calls.clear()
        with mock.patch.object(orca_module, "run", fake):
            orca_module.uninstall(plan_id=orca_module.uninstall(dry_run=True)["plan_id"])
        self.assertEqual(calls, [])  # 기록이 없으면(남이 켠 linger) 그대로 둔다

    def test_apply_refuses_dry_run_and_child_output_never_reaches_stdout(self):
        """X6·X7 — apply --dry-run 은 거절한다. 자식 프로세스가 fd 1 에 쓴 것도 표준 출력의 JSON 을 깨지 않는다."""
        import subprocess
        import sys as _sys
        script = ("import json, subprocess, sys; from unittest import mock; from jarvis import module, orca_module\n"
                  "def status():\n    subprocess.run([sys.executable, '-c', 'print(\"apt: Removing orca-ide\")'])\n"
                  "    return {'module': 'orca'}\n"
                  "with mock.patch.object(orca_module, 'status', status):\n"
                  "    rc = module.cli(['orca', 'status', '--json'])\n"
                  "    rc2 = module.cli(['orca', 'apply', '--json', '--dry-run', '--plan-id', 'x'])\n"
                  "sys.stderr.write(f'rc={rc},{rc2}')\n")
        p = subprocess.run([_sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                           cwd=str(pathlib.Path(__file__).resolve().parents[1]), env={**os.environ, "JARVIS_CONFIG": "/dev/null"})
        self.assertEqual(json.loads(p.stdout), {"module": "orca"})
        self.assertIn("apt: Removing orca-ide", p.stderr)
        self.assertIn("rc=0,1", p.stderr)

    def test_the_module_imports_and_answers_without_pwd(self):
        """X1 — Windows 에는 pwd·os.getuid 가 없다. 그래도 모듈을 import 할 수 있다."""
        import subprocess
        import sys as _sys
        script = ("import os, sys; sys.modules['pwd'] = None; del os.getuid\n"
                  "from jarvis import module\n"
                  "import jarvis.orca_module\n")
        p = subprocess.run([_sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                           cwd=str(pathlib.Path(__file__).resolve().parents[1]), env={**os.environ, "JARVIS_CONFIG": "/dev/null"})
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
