"""harness 모듈의 계약 층: update-ai 엔진을 가짜로 바꿔 status·plan·apply 가 무엇을 읽고 부르는지 본다.
실제 하네스·레지스트리·update-ai 는 부르지 않는다."""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis.modules.harness import contract as harness


class FakeUpdateAi:
    """update_ai.py 의 확인 함수들. installed: {id: 버전}, latest: {id: 최신}, daemon: (관리형 판, 실행 중 서버 판)."""

    def __init__(self, catalog, installed, latest, daemon=None):
        self.CATALOG = catalog
        self.installed, self.latest, self.daemon = installed, latest, daemon
        self.calls, self.homes = [], []

    def locations(self, name):
        return [f"/bin/{name}"] if name in self.installed else []

    def method_for(self, entry, path):
        return "default", None

    VERSION_RE = re.compile(r"\d+\.\d+\.\d+[-.\w]*")

    def run_quiet(self, argv, timeout=60, env=None):
        """`<bin> --version` 만 온다. 어느 홈으로 불렀는지 기록한다."""
        self.homes.append((env or os.environ).get("HOME"))
        return subprocess.CompletedProcess(argv, 0, self.installed[os.path.basename(argv[0])] + "\n", "")

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

    def test_the_engine_path_points_at_the_setup_module(self):
        """계약 파일의 위치에서 계산한 경로라 옮기면 깨질 수 있다"""
        self.assertTrue(os.path.isfile(harness.UPDATE_AI), harness.UPDATE_AI)
        self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(harness.UPDATE_AI)))), "setup")

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

    def test_versions_are_read_in_a_scratch_home(self):
        """covers: HARNESS-1
        gemini·opencode 는 --version 만 불러도 홈에 상태 파일을 쓴다. status 는 실제 홈을 건드리지 않는다."""
        self.use(installed={"claude": "2.0.0", "gemini": "0.65.0"}, latest={})
        harness.status()
        self.assertEqual(len(self.fake.homes), 2)
        self.assertTrue(all(h and h != os.path.expanduser("~") and "jarvis-harness-" in h for h in self.fake.homes), self.fake.homes)
        self.assertFalse(any(os.path.exists(h) for h in self.fake.homes))  # 끝나면 지운다

    def test_a_version_the_scratch_home_cannot_read_falls_back_to_the_real_home(self):
        """covers: HARNESS-1
        래퍼를 거치는 codex·설정이 있어야 도는 grok 은 임시 홈에서 판을 내지 못한다. update-ai 와 같은 실제 홈으로
        다시 읽어야 계획과 갱신 뒤 판정이 update-ai 와 갈리지 않는다(빈 판을 outdated 로 보면 늘 실패한다)."""
        self.use(installed={"gemini": "0.66.0"}, latest={"gemini": "0.66.0"})
        self.fake.run_quiet = lambda argv, timeout=60, env=None: subprocess.CompletedProcess(argv, 1, "", "no config")
        self.fake.version_of = lambda path: self.fake.installed[os.path.basename(path)]
        self.assertEqual(harness.status()["details"]["tools"][0]["state"], "current")
        self.assertEqual(harness.plan()["steps"], [])
        self.fake.version_of = lambda path: ""  # 어디서도 판을 모르면 계획하지 않는다
        self.assertEqual(harness.status()["details"]["tools"][0]["state"], "unknown")
        self.assertEqual(harness.plan()["steps"], [])

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
        # 관리형 설치가 없으면 실행 중 서버는 다른 것이 띄운 것이라 update-ai 가 맞출 수 없다(pit5 실측). 보고만 한다
        rows = {r["id"]: r["state"] for r in harness.status()["details"]["tools"]}
        self.assertEqual(rows["codex-server"], "unknown")
        self.assertEqual(harness.plan()["steps"], [])

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
