"""harness 모듈의 계약 층: update-ai 엔진을 가짜로 바꿔 status·plan·apply 가 무엇을 읽고 부르는지 본다.
실제 하네스·레지스트리·update-ai 는 부르지 않는다."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import harness_module as harness


class FakeUpdateAi:
    """update_ai.py 의 확인 함수들. installed: {id: 버전}, latest: {id: 최신}, daemon: (관리형 판, 실행 중 서버 판)."""

    def __init__(self, catalog, installed, latest, daemon=None):
        self.CATALOG = catalog
        self.installed, self.latest, self.daemon = installed, latest, daemon
        self.calls = []

    def locations(self, name):
        return [f"/bin/{name}"] if name in self.installed else []

    def method_for(self, entry, path):
        return "default", None

    def version_of(self, path):
        return self.installed[os.path.basename(path)]

    def latest_of(self, entry, path, method, value):
        self.calls.append(("latest", entry["id"]))
        return self.latest.get(entry["id"])

    def is_current(self, installed, latest):
        return [int(x) for x in installed.split(".")] >= [int(x) for x in latest.split(".")]

    def process_table(self):
        return []

    def codex_check_rows(self, path, target, table):
        if not self.daemon:
            return []
        managed, server = self.daemon  # update-ai 처럼 관리형 설치가 없으면 데몬 행이 없다
        return ([("codex-daemon", "up to date" if managed == target else "needs sync", managed, target)] if managed else []) + \
            [("codex-server", "up to date" if server == target else "needs sync", server, target)]


class HarnessModuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = Path(tmp.name) / "catalog.json"
        self.catalog.write_text('[{"id": "claude", "detect": {"bin": "claude"}}, {"id": "codex", "detect": {"bin": "codex"}},'
                                ' {"id": "gemini", "detect": {"bin": "gemini"}}]')
        self.updates = []
        self.held = set()  # 세션이 돌아 update-ai 가 보류하는 대상

        def fake_update(ids, allow_interrupt, timeout=None):
            self.updates.append((ids, allow_interrupt))
            for i in ids:  # 올렸다고 치고 설치본을 최신으로
                if i in self.fake.latest:
                    self.fake.installed[i] = self.fake.latest[i]
            if allow_interrupt and self.fake.daemon:
                managed = self.fake.daemon[0] and self.fake.installed["codex"]
                self.fake.daemon = (managed, self.fake.installed["codex"])
            held = self.held if not allow_interrupt else set()
            for i in held:
                self.fake.installed[i] = self.before[i]
            return (1 if held else 0), held

        self.patch = mock.patch.object(harness, "_update", side_effect=fake_update)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def use(self, **kw):
        self.fake = FakeUpdateAi(str(self.catalog), **kw)
        self.before = dict(self.fake.installed)
        p = mock.patch.object(harness, "tools", return_value=self.fake)
        p.start()
        self.addCleanup(p.stop)

    def test_status_and_plan_only_read_and_plan_the_outdated_targets(self):
        """covers: HARNESS-1 (AI-7, AI-8)"""
        self.use(installed={"claude": "2.0.0", "gemini": "0.65.0"}, latest={"claude": "2.0.0", "gemini": "0.66.0"})
        s = harness.status()
        self.assertEqual(s["health"], "degraded")
        self.assertEqual([(t["id"], t["state"]) for t in s["details"]["tools"]], [("claude", "current"), ("gemini", "outdated")])
        p = harness.plan()
        self.assertEqual([(st["id"], st["risk"], st["argv"], st["target"]) for st in p["steps"]],
                         [("gemini", "update", ["update-ai", "--only", "gemini"], "0.66.0")])
        self.assertNotIn(("latest", "codex"), self.fake.calls)  # 설치되지 않은 것은 묻지도 않는다
        self.assertEqual(self.updates, [])

    def test_unknown_latest_is_reported_but_not_planned(self):
        """covers: HARNESS-1"""
        self.use(installed={"claude": "2.0.0"}, latest={})
        self.assertEqual(harness.status()["details"]["tools"][0]["state"], "unknown")
        self.assertEqual(harness.plan()["steps"], [])

    def test_a_daemon_behind_the_cli_needs_approval_to_restart(self):
        """covers: HARNESS-1"""
        self.use(installed={"codex": "0.166.0"}, latest={"codex": "0.166.0"}, daemon=("0.159.3", "0.159.3"))
        p = harness.plan()
        self.assertEqual([(st["id"], st["risk"], st["argv"]) for st in p["steps"]],
                         [("codex-daemon", "config", ["update-ai", "--only", "codex", "--allow-interrupt"])])
        r = harness.apply(p["plan_id"])
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(self.updates, [(["codex"], True)])

    def test_apply_updates_only_the_planned_targets_and_judges_by_the_versions_it_reads_back(self):
        """covers: HARNESS-2 (AI-11)"""
        self.use(installed={"claude": "2.0.0", "gemini": "0.65.0"}, latest={"claude": "2.0.1", "gemini": "0.66.0"})
        self.assertEqual(harness.apply("0" * 64)["status"], "stale")
        self.assertEqual(self.updates, [])
        p = harness.plan()
        r = harness.apply(p["plan_id"])
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(self.updates, [(["claude", "gemini"], False)])
        self.assertEqual(harness.plan()["steps"], [])
        # update-ai 가 0 으로 끝나도 다시 읽은 판이 최신이 아니면 실패다
        self.fake.latest["gemini"] = "0.67.0"
        p = harness.plan()
        self.patch.stop()
        with mock.patch.object(harness, "_update", return_value=(0, set())):
            r = harness.apply(p["plan_id"])
        self.patch.start()
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["steps"][0]["result"], "failed")

    def test_the_daemon_restart_runs_alone_with_allow_interrupt(self):
        """covers: HARNESS-2 (AI-56)
        데몬 재시작 승인이 다른 대상의 보류(npm -g·winget 세션)를 풀면 안 된다."""
        self.use(installed={"codex": "0.166.0", "gemini": "0.65.0"}, latest={"codex": "0.166.0", "gemini": "0.66.0"},
                 daemon=("0.159.3", "0.166.0"))
        r = harness.apply(harness.plan()["plan_id"])
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(self.updates, [(["codex"], True), (["gemini"], False)])

    def test_a_target_held_for_a_running_session_is_deferred(self):
        """covers: HARNESS-2 (AI-56)"""
        self.use(installed={"claude": "2.0.0", "gemini": "0.65.0"}, latest={"claude": "2.0.1", "gemini": "0.66.0"})
        self.held = {"gemini"}
        r = harness.apply(harness.plan()["plan_id"])
        self.assertEqual(r["status"], "deferred", r)
        self.assertEqual({s["id"]: s["result"] for s in r["steps"]}, {"claude": "ok", "gemini": "deferred"})

    def test_daemon_rows_follow_the_installed_cli_not_the_latest_release(self):
        """covers: HARNESS-1"""
        self.use(installed={"codex": "0.165.0"}, latest={"codex": "0.166.0"}, daemon=("0.165.0", "0.165.0"))
        self.assertEqual([s["id"] for s in harness.plan()["steps"]], ["codex"])  # CLI 만 뒤처지면 데몬 재시작은 없다
        self.use(installed={"codex": "0.166.0"}, latest={"codex": "0.166.0"}, daemon=("0.166.0", "0.159.3"))
        p = harness.plan()  # 실행 중 서버만 어긋나도 데몬 단계이고, 두 행이 맞으면 ok 다
        self.assertEqual([s["id"] for s in p["steps"]], ["codex-daemon"])
        self.assertEqual(harness.apply(p["plan_id"])["status"], "done")
        self.use(installed={"codex": "0.166.0"}, latest={"codex": "0.166.0"}, daemon=(None, "0.159.3"))
        p = harness.plan()  # 관리형 설치 없이 실행 중 서버만 있어도 그 행으로 판정한다
        self.assertEqual([s["id"] for s in p["steps"]], ["codex-daemon"])
        self.assertEqual(harness.apply(p["plan_id"])["status"], "done")  # 서버가 CLI 판으로 다시 뜨면 성공이다
        self.fake.daemon = (None, "0.159.3")
        p = harness.plan()
        self.patch.stop()
        with mock.patch.object(harness, "_update", return_value=(0, set())):
            r = harness.apply(p["plan_id"])
        self.patch.start()
        self.assertEqual(r["status"], "failed", r)  # 서버가 그대로면 실패다(데몬 행이 없다고 성공으로 보지 않는다)

    def test_a_daemon_newer_than_the_cli_is_left_alone(self):
        """covers: HARNESS-1"""
        self.use(installed={"codex": "0.164.0"}, latest={}, daemon=("0.166.0", "0.166.0"))
        self.assertEqual(harness.plan()["steps"], [])  # update-ai 처럼 데몬을 내리지 않는다

    def test_deferred_targets_are_read_from_the_update_ai_result_table(self):
        """covers: HARNESS-2 (AI-56)"""
        table = ("── Results ──\n  v claude     2.1.316  (up to date)\n"
                 "  x gemini     0.65.0  (failed: deferred (in use))\n"
                 "  x a-very-long-name 1.0.0  (failed: deferred (in use))\n"
                 "  x codex      0.166.0  (partly failed: daemon-deferred)\n")
        self.assertEqual(set(harness.DEFERRED.findall(table)), {"gemini", "a-very-long-name"})

    def test_update_runs_update_ai_in_its_own_group_and_kills_it_on_timeout(self):
        """covers: HARNESS-2"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake_update_ai.py"
            script.write_text("import sys, time\nprint('  x gemini     0.65.0  (failed: deferred (in use))', flush=True)\n"
                              "time.sleep(30 if '--allow-interrupt' in sys.argv else 0)\n")
            self.patch.stop()
            try:
                with mock.patch.object(harness, "UPDATE_AI", str(script)):
                    self.assertEqual(harness._update(["gemini"], False), (0, {"gemini"}))
                    code, _ = harness._update(["codex"], True, timeout=1)
            finally:
                self.patch.start()
        self.assertEqual(code, 124)

    def test_user_path_puts_user_bins_and_the_newest_nvm_first(self):
        """covers: HARNESS-3"""
        with tempfile.TemporaryDirectory() as home:
            for d in (".local/bin", ".nvm/versions/node/v24.9.0/bin", ".nvm/versions/node/v24.19.0/bin"):
                os.makedirs(os.path.join(home, d))
            with mock.patch.dict(os.environ, {"HOME": home}):
                parts = harness.user_path("/usr/bin").split(os.pathsep)
        self.assertEqual(parts[0], os.path.join(home, ".local/bin"))
        self.assertLess(parts.index(os.path.join(home, ".nvm/versions/node/v24.19.0/bin")),
                        parts.index(os.path.join(home, ".nvm/versions/node/v24.9.0/bin")))
        self.assertEqual(parts[-1], "/usr/bin")


if __name__ == "__main__":
    unittest.main()
