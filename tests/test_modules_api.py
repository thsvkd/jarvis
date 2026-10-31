"""AI 셋업 화면 API: 게이트웨이의 모듈 실행(가짜 run)과 core 라우트(가짜 게이트웨이). 실제 모듈은 돌리지 않는다."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from jarvis import config, core, modules_api, server
from test_core import Gateway, serve
from test_orca import FakeCollector

PLAN_ID = "a" * 64


class GatewayTest(unittest.TestCase):
    def setUp(self):
        modules_api._cache.clear()
        modules_api.job.clear()
        self.calls = []

        def fake(*args, timeout):
            self.calls.append(args)
            return 0, {"module": args[0], "health": "ok"}

        p = mock.patch.object(modules_api, "run", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)

    def test_status_is_cached_per_module_until_forced(self):
        """covers: SETUP-1"""
        first = modules_api.status()
        self.assertEqual(set(first["modules"]), set(modules_api.module.MODULES))
        self.assertTrue(all(m["ok"] for m in first["modules"].values()))
        modules_api.status()
        self.assertEqual(len(self.calls), len(modules_api.module.MODULES))
        modules_api.status(force=True)
        self.assertEqual(len(self.calls), 2 * len(modules_api.module.MODULES))

    def test_apply_checks_its_input_and_runs_one_job_at_a_time(self):
        """covers: SETUP-2"""
        self.assertEqual(modules_api.apply("nope", PLAN_ID)[0], 404)
        self.assertEqual(modules_api.apply("orca", "not-a-plan-id")[0], 400)
        gate, done = threading.Event(), threading.Event()

        def slow(*args, timeout):
            if args[1] == "apply":
                gate.wait(5)
                done.set()
                return 3, {"status": "stale", "steps": []}
            return 0, {}

        modules_api.run.side_effect = slow
        modules_api.status()  # 캐시를 채운다
        code, body = modules_api.apply("orca", PLAN_ID)
        self.assertEqual((code, body["state"], body["plan_id"]), (202, "running", PLAN_ID))
        self.assertEqual(modules_api.apply("doctor", PLAN_ID)[0], 409)
        gate.set()
        done.wait(5)
        for _ in range(50):
            if modules_api.job.get("state") == "finished":
                break
            threading.Event().wait(0.02)
        self.assertEqual((modules_api.job["code"], modules_api.job["result"]["status"]), (3, "stale"))
        self.assertNotIn("orca", modules_api._cache)  # 적용 뒤 상태는 다시 읽는다
        self.assertEqual(modules_api.status()["job"]["state"], "finished")

    def test_run_reports_the_last_stderr_line_when_the_output_is_not_json(self):
        """covers: SETUP-1"""
        done = mock.Mock(returncode=1, stdout="", stderr="Traceback\nModuleNotFoundError: psutil\n")
        with mock.patch.object(modules_api.subprocess, "run", return_value=done):
            code, body = RUN("orca", "status", timeout=1)
        self.assertEqual((code, body["error"]), (1, "ModuleNotFoundError: psutil"))


RUN = modules_api.run  # 패치하기 전의 원래 함수(테스트가 import 될 때 잡는다)


class CoreRouteTest(unittest.TestCase):
    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, b'{"meta": {"host": "gw"}}', False),
                          "/api/modules": (200, json.dumps({"modules": {"doctor": {"health": "ok"}}, "job": None}).encode(), False),
                          "/api/modules/orca/plan": (200, json.dumps({"plan_id": PLAN_ID, "steps": []}).encode(), False),
                          f"/api/modules/orca/apply?plan_id={PLAN_ID}": (202, b'{"state": "running"}', False)}
        Gateway.posted = None
        self.gw = serve(Gateway)
        c = core.Core([{"id": "gw", "url": f"http://127.0.0.1:{self.gw.server_address[1]}"}])
        c.poll("gw")
        self.httpd = server.Server(("127.0.0.1", 0), server.make_handler(FakeCollector(), c))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        for h in (self.httpd, self.gw):
            h.shutdown()
            h.server_close()

    def call(self, path, headers=None, post=False):
        req = urllib.request.Request(self.base + path, headers=headers or {}, data=b"" if post else None,
                                     method="POST" if post else "GET")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def as_viewer(self):
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), ["127.0.0.1"]
        self.addCleanup(lambda: (setattr(server, "client_kind", kind), setattr(config, "VIEWERS", vw)))

    def test_core_collects_modules_and_relays_the_plan(self):
        """covers: SETUP-1"""
        st, body = self.call("/api/devices/modules")
        self.assertEqual((st, body["devices"][0]["id"], body["devices"][0]["modules"]["doctor"]["health"]), (200, "gw", "ok"))
        self.assertEqual(self.call("/api/devices/modules?refresh=1")[0], 403)  # 강제 새로고침은 맞춤 헤더가 있어야 한다
        self.assertEqual(self.call("/api/devices/gw/modules/orca/plan"), (200, {"plan_id": PLAN_ID, "steps": []}))
        self.assertEqual(self.call("/api/devices/gw/modules/nope/plan")[0], 404)
        self.assertEqual(self.call("/api/devices/nope/modules/orca/plan")[0], 404)

    def test_apply_needs_a_viewer_and_the_action_header_and_passes_the_plan_id(self):
        """covers: SETUP-2"""
        path = f"/api/devices/gw/modules/orca/apply?plan_id={PLAN_ID}"
        self.assertEqual(self.call(path, {"X-Jarvis-Action": "module-apply"}, post=True)[0], 403)  # 루프백은 viewer 가 아니다
        self.as_viewer()
        self.assertEqual(self.call(path, post=True)[0], 403)  # 맞춤 헤더 없음
        self.assertIsNone(Gateway.posted)
        st, body = self.call(path, {"X-Jarvis-Action": "module-apply"}, post=True)
        self.assertEqual((st, body["state"]), (202, "running"))
        self.assertEqual(Gateway.posted, (f"/api/modules/orca/apply?plan_id={PLAN_ID}", "module-apply"))

    def test_gateway_routes_answer_only_the_configured_core(self):
        """covers: SETUP-2"""
        for path, post in (("/api/modules", False), ("/api/modules/orca/plan", False),
                           (f"/api/modules/orca/apply?plan_id={PLAN_ID}", True)):
            self.assertEqual(self.call(path, {"X-Jarvis-Action": "module-apply"}, post=post)[0], 403, path)


if __name__ == "__main__":
    unittest.main()
