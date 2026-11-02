"""setup 모듈의 계약 층: 임시 홈과 스텁 PATH 에서 status·plan·apply·uninstall. 실제 설치기·네트워크는 쓰지 않는다
(엔진의 execute 를 가짜로 바꿔 무엇을 부르는지만 본다)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import setup_module as setup


class SetupModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.claude = self.home / ".claude"
        self.claude.mkdir(parents=True)
        # 이 파일은 기존 단계 계약 테스트다. 보존 설정의 별도 동작은 test_session_retention.py에서 검증한다.
        (self.claude / "settings.json").write_text('{"cleanupPeriodDays": 36500}', encoding="utf-8")
        bin_dir = Path(tmp.name) / "bin"
        bin_dir.mkdir()
        for name in ("claude", "codex", "codegraph"):  # 이미 깔린 하네스·도구처럼 보이게 한다
            p = bin_dir / name
            p.write_text("#!/bin/sh\necho 1.0.0\n")
            p.chmod(0o755)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                           "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"})
        env.start()
        self.addCleanup(env.stop)
        state = mock.patch.object(setup, "REFRESH_STATE", str(Path(tmp.name) / "state" / "setup-refresh.json"))
        state.start()
        self.addCleanup(state.stop)
        self.calls = []

        def fake_execute(actions, selection, project_dir=None):
            self.calls.append(([(a.stage, a.argv) for a in actions], selection))
            return self.exit_code

        self.exit_code = 0
        sudo = mock.patch.object(setup, "sudo_ok", return_value=True)  # 실제 sudo 를 부르지 않는다(기기마다 결과가 다르다)
        sudo.start()
        self.addCleanup(sudo.stop)
        ex = mock.patch.object(setup.stages(), "execute", side_effect=fake_execute)
        ex.start()
        self.addCleanup(ex.stop)

    def receipt(self, **data):
        (self.claude / ".claude-code-setup.json").write_text(json.dumps(data), encoding="utf-8")

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}

    def test_status_and_plan_only_read_and_follow_the_staged_install(self):
        """covers: SETUP-3"""
        self.receipt(harness_installs={"grok": True}, keybinds={"newline_chord": "ctrl-enter"})
        before = self.snapshot()
        s, p = setup.status(), setup.plan()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(setup.REFRESH_STATE))
        self.assertEqual(s["module"], "setup")
        self.assertEqual(s["health"], "degraded" if p["steps"] else "ok")
        # 계획은 엔진의 --stages all(agentsmd 제외)과 같은 쓰기 액션이다. 하네스는 claude·codex 와 영수증의 grok,
        # 줄바꿈 키는 기록된 값을 쓴다(기본값으로 되돌리지 않는다)
        world = setup._snapshot()
        chosen = setup._selection(world)
        self.assertEqual(chosen["harnesses"], ["claude", "codex", "grok"])
        self.assertEqual(chosen["newline"], "ctrl-enter")
        self.assertNotIn("agentsmd", chosen["stages"])
        engine = [a.detail for a in setup.stages().plan_actions(chosen, world) if a.writes]
        self.assertEqual([st["summary"] for st in p["steps"] if not st["id"].startswith("refresh-")], engine)
        self.assertTrue(any("grok" in d for d in engine))  # 영수증의 하네스도 MCP 등록 대상이다
        self.assertEqual(self.calls, [])

    def test_recorded_settings_are_refreshed_once_per_bundle(self):
        """covers: SETUP-4"""
        self.receipt(keybinds={"newline_chord": "ctrl-enter"}, output_style=True, skills={"design-lab": True})
        p = setup.plan()
        refresh = {st["id"]: st for st in p["steps"] if st["id"].startswith("refresh-")}
        self.assertIn("refresh-newline", refresh)
        self.assertIn("refresh-output_style", refresh)
        self.assertNotIn("refresh-mods", refresh)  # 설치 기록이 없는 것은 다시 적용하지 않는다(위의 설치 액션이 맡는다)
        self.assertEqual(refresh["refresh-output_style"]["risk"], "install")  # 플러그인이 없으면 워커가 설치한다
        self.assertEqual(refresh["refresh-newline"]["argv"], ["scripts/setup_keybinds.ps1" if os.name == "nt" else "scripts/setup_keybinds.sh"])
        r = setup.apply(p["plan_id"])  # 다시 적용도 같은 엔진이, 기록된 줄바꿈 키로 돈다
        self.assertEqual(r["status"], "done", r)
        newline = [sel for acts, sel in self.calls if acts and acts[0][1] == ("newline",)]
        self.assertEqual(newline[0]["newline"], "ctrl-enter")
        recorded = json.loads(Path(setup.REFRESH_STATE).read_text())
        for rid, st in refresh.items():
            self.assertEqual(recorded[rid[8:]], st["digest"])
        self.assertFalse([st for st in setup.plan()["steps"] if st["id"].startswith("refresh-")])
        with mock.patch.object(setup, "_bundle_digest", return_value="new"):  # 배포본이 바뀌면 다시 계획한다
            self.assertIn("refresh-newline", [st["id"] for st in setup.plan()["steps"]])

    def test_a_setting_installed_by_the_plan_is_not_refreshed_right_after(self):
        """covers: SETUP-4"""
        p = setup.plan()  # 빈 홈: 줄바꿈 키·스킬·출력 스타일·mod 를 처음 설치한다
        heads = {st["argv"][0] for st in p["steps"] if st["argv"]}
        self.assertTrue({"newline", "install-skills", "mods"} <= heads, heads)
        self.assertEqual(setup.apply(p["plan_id"])["status"], "done")
        self.receipt(keybinds={"newline_chord": "shift-enter"}, mods=True, skills={"design-lab": True, "tailnet-report": True})
        self.assertEqual([st["id"] for st in setup.plan()["steps"] if st["id"].startswith("refresh-")], [])

    def test_apply_continues_past_a_failed_stage_and_rejects_a_changed_plan(self):
        """covers: SETUP-5"""
        p = setup.plan()
        self.assertEqual(setup.apply("0" * 64)["status"], "stale")
        self.assertEqual(self.calls, [])
        stages_in_plan = [st["stage"] for st in p["steps"] if "stage" in st]
        self.assertGreater(stages_in_plan.count("mcp"), 1, p["steps"])  # 스텁 홈에는 MCP 등록이 없다
        failing = next(st for st in p["steps"] if st.get("stage") == "mcp")

        def fail_first_mcp(actions, selection, project_dir=None):
            self.calls.append(([(a.stage, a.argv) for a in actions], selection))
            return 7 if list(actions[0].argv) == failing["argv"] else 0

        with mock.patch.object(setup.stages(), "execute", side_effect=fail_first_mcp):
            r = setup.apply(p["plan_id"])
        self.assertEqual(r["status"], "failed")
        got = {x["id"]: x["result"] for x in r["steps"]}
        self.assertEqual(got[failing["id"]], "failed")
        # 같은 단계의 나머지는 건너뛰고, 다른 단계는 계속 돈다
        self.assertTrue(all(got[st["id"]] == "skipped" for st in p["steps"] if st.get("stage") == "mcp" and st is not failing))
        self.assertTrue(all(got[st["id"]] == "ok" for st in p["steps"] if st.get("stage") not in (None, "mcp")))
        self.assertEqual(len(r["steps"]), len(p["steps"]))

    def test_a_failed_stage_skips_the_stages_that_need_it_but_not_the_independent_ones(self):
        """covers: SETUP-5"""
        engine = setup.stages()
        acts = [engine.Action("codegraph", "install CodeGraph", True, ("python3", "npm_release.py", "install", "codegraph"), ()),
                engine.Action("mcp", "claude mcp add", True, ("claude", "mcp", "add", "codegraph"), ()),
                engine.Action("newline", "configure newline", True, ("newline", "shift-enter"), ())]
        self.receipt(output_style=True, mods=True)

        def fail_codegraph(actions, selection, project_dir=None):
            self.calls.append(([(a.stage, a.argv) for a in actions], selection))
            return 1 if actions[0].stage == "codegraph" or actions[0].argv == ("output-style",) else 0

        with mock.patch.object(engine, "plan_actions", return_value=acts), \
                mock.patch.object(engine, "execute", side_effect=fail_codegraph):
            p = setup.plan()
            r = setup.apply(p["plan_id"])
        got = {x["id"]: (x["result"], x["message"]) for x in r["steps"]}
        self.assertEqual(got["1-codegraph"][0], "failed")
        self.assertEqual(got["2-mcp"][0], "skipped")  # 없는 바이너리를 MCP 에 등록하지 않는다
        self.assertIn("codegraph", got["2-mcp"][1])
        self.assertEqual(got["3-newline"][0], "ok")
        self.assertEqual(got["refresh-output_style"][0], "failed")
        self.assertEqual(got["refresh-mods"][0], "ok")  # 다시 적용은 서로 독립이다

    def test_sudo_steps_never_prompt_and_are_skipped_without_passwordless_sudo(self):
        """covers: SETUP-12 (AI-35)"""
        engine = setup.stages()
        sudo_action = engine.Action("base", "ensure git", True, engine._linux_package("git"), ("flag:git",))
        node = engine.Action("base", "install Node 20", True, ("sh", "-c", "nvm install 20"), ("node:20",))
        with mock.patch.object(engine, "plan_actions", return_value=[sudo_action, node]), \
                mock.patch.object(setup, "sudo_ok", side_effect=AssertionError("status 는 sudo 를 부르지 않는다")):
            self.assertEqual(setup.status()["details"]["sudo_steps"], 1)
        with mock.patch.object(engine, "plan_actions", return_value=[sudo_action, node]):
            with mock.patch.object(setup, "sudo_ok", return_value=False):
                r = setup.apply(setup.plan()["plan_id"])
            self.assertEqual([x["result"] for x in r["steps"]], ["skipped", "ok"])  # 같은 단계의 sudo 없는 액션은 돈다
            self.calls.clear()
        with mock.patch.object(engine, "plan_actions", return_value=[sudo_action]):
            with mock.patch.object(setup, "sudo_ok", return_value=False):
                p = setup.plan()
                step = p["steps"][0]
                self.assertEqual((step["risk"], step["sudo"]), ("sudo", "password"))
                self.assertNotRegex(" ".join(step["argv"]), r"\bsudo(?! -n)")  # 게이트웨이에서는 언제나 sudo -n
                r = setup.apply(p["plan_id"])
            self.assertEqual(r["status"], "done", r)
            self.assertEqual(r["steps"][0]["result"], "skipped")
            self.assertIn("sudo apt-get install", r["steps"][0]["message"])  # 터미널에서 칠 명령을 남긴다
            self.assertEqual(self.calls, [])
            with mock.patch.object(setup, "sudo_ok", return_value=True):
                p = setup.plan()
                self.assertEqual(p["steps"][0]["sudo"], "ok")
                self.assertEqual(setup.apply(p["plan_id"])["steps"][0]["result"], "ok")
            self.assertIn("sudo -n apt-get", " ".join(self.calls[0][0][0][1]))

    def test_the_plan_id_binds_the_install_command_and_worker_bundle(self):
        """covers: SETUP-5 (리뷰 L3)
        승인한 계획과 다른 설치 본문(카탈로그 명령·워커 배포본)이 돌면 stale 이어야 한다."""
        engine = setup.stages()
        acts = [engine.Action("harness", "install harness Grok", True, ("install-harness", "grok"), ()),
                engine.Action("newline", "configure newline", True, ("newline", "shift-enter"), ())]
        with mock.patch.object(engine, "plan_actions", return_value=acts):
            p = setup.plan()
            self.assertEqual(p["steps"][0]["cmd"], engine._catalog_command("grok", False))
            self.assertIn("digest", p["steps"][1])
            with mock.patch.object(engine, "_catalog_command", return_value="curl https://example.invalid | sh"):
                self.assertEqual(setup.apply(p["plan_id"])["status"], "stale")
            with mock.patch.object(setup, "_bundle_digest", return_value="other"):
                self.assertEqual(setup.apply(p["plan_id"])["status"], "stale")
        self.assertEqual(self.calls, [])

    def test_existing_mcp_registrations_are_left_alone(self):
        """covers: SETUP-3 (AI-29)"""
        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {"codegraph": {"command": "codegraph"}}}))
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('[mcp_servers.codegraph]\ncommand = "codegraph"\n')
        steps = setup.plan()["steps"]
        self.assertFalse([st for st in steps if st.get("stage") == "mcp"], steps)

    def test_uninstall_needs_the_dry_run_plan_id_and_keeps_jarvis_and_tailscale(self):
        """covers: SETUP-6"""
        self.receipt(mcp={"claude": True}, jarvis_unit=True, tool_installs={"tailscale": True, "jarvis": True})
        dry = setup.uninstall(dry_run=True)
        self.assertEqual(dry["status"], "done")
        self.assertTrue(dry["steps"])
        self.assertEqual(self.calls, [])
        self.assertEqual(setup.uninstall(plan_id="0" * 64)["status"], "stale")
        self.assertEqual(self.calls, [])
        r = setup.uninstall(plan_id=dry["plan_id"])
        self.assertEqual(r["status"], "done", r)
        self.assertTrue(any(acts and acts[0][1][:3] == ("claude", "mcp", "remove") for acts, _ in self.calls), self.calls)
        blob = json.dumps([st for st in dry["steps"]]) + json.dumps(self.calls)
        self.assertNotIn("jarvis", blob.lower().replace(setup.ROOT.lower(), ""))
        self.assertNotIn("tailscale", blob.lower())


class AgentsMdCliTest(unittest.TestCase):
    def test_agentsmd_writes_the_project_instructions_into_the_current_folder(self):
        """covers: AGENTSMD-1"""
        import site
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            # 홈을 바꾸면 사용자 site(psutil 등)를 못 찾으므로 PYTHONPATH 에 지금 사용자 site 를 함께 둔다
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), JARVIS_CONFIG="/dev/null",
                       PYTHONPATH=os.pathsep.join([str(root), site.getusersitepackages()]))
            r = subprocess.run([sys.executable, "-m", "jarvis", "agentsmd", "--harness", "codex"], cwd=tmp, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((Path(tmp) / "AGENTS.md").is_file())
            self.assertFalse((Path(tmp) / "CLAUDE.md").exists())  # codex 만 고르면 CLAUDE.md 는 쓰지 않는다
            r = subprocess.run([sys.executable, "-m", "jarvis", "agentsmd", "--harness", "nope"], cwd=tmp, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
