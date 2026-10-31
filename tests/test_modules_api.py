"""AI 셋업 화면 API: 게이트웨이의 모듈 실행(가짜 run)과 core 라우트(가짜 게이트웨이). 실제 모듈은 돌리지 않는다."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jarvis import config, core, modules_api, orca_module, server  # noqa: E402
from test_core import Gateway, serve  # noqa: E402
from test_orca import FakeCollector  # noqa: E402

PLAN_ID = "a" * 64
RUN = modules_api.run  # 패치하기 전의 원래 함수(테스트가 import 될 때 잡는다)


def wait_until(check):
    for _ in range(100):
        if check():
            return True
        threading.Event().wait(0.02)
    return False


class GatewayTest(unittest.TestCase):
    def setUp(self):
        modules_api._cache.clear()
        modules_api._plans.clear()
        modules_api.job.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.calls = []

        def fake(*args, timeout):
            self.calls.append(args)
            return 0, {"module": args[0], "health": "ok"}

        for p in (mock.patch.object(modules_api, "run", side_effect=fake),
                  mock.patch.object(modules_api, "MARK", os.path.join(tmp.name, "module-apply.json")),
                  mock.patch.object(modules_api, "_busy", return_value=None)):
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

    def test_plans_asked_together_run_once(self):
        """covers: SETUP-1"""
        self.assertEqual(modules_api.plan("orca")[0], 200)
        modules_api.plan("orca")
        self.assertEqual(self.calls, [("orca", "plan")])
        self.assertEqual(modules_api.plan("nope")[0], 404)

    def test_apply_checks_its_input_and_runs_one_job_at_a_time(self):
        """covers: SETUP-2"""
        self.assertEqual(modules_api.apply("nope", PLAN_ID)[0], 404)
        for bad in ("not-a-plan-id", PLAN_ID + "\n", PLAN_ID.upper()):
            self.assertEqual(modules_api.apply("orca", bad)[0], 400, repr(bad))
        gate = threading.Event()

        def slow(*args, timeout):
            if args[1] == "apply":
                gate.wait(5)
                return 3, {"status": "stale", "steps": []}
            return 0, {}

        modules_api.run.side_effect = slow
        modules_api.status()  # 캐시를 채운다
        code, body = modules_api.apply("orca", PLAN_ID)
        self.assertEqual((code, body["state"], body["plan_id"]), (202, "running", PLAN_ID))
        self.assertTrue(os.path.exists(modules_api.MARK))
        self.assertTrue(modules_api.running())
        self.assertEqual(modules_api.apply("doctor", PLAN_ID)[0], 409)
        gate.set()
        self.assertTrue(wait_until(lambda: modules_api.job.get("state") == "finished"))
        self.assertEqual((modules_api.job["code"], modules_api.job["result"]["status"]), (3, "stale"))
        self.assertNotIn("orca", modules_api._cache)  # 적용 뒤 상태는 다시 읽는다
        self.assertFalse(os.path.exists(modules_api.MARK))
        self.assertEqual(modules_api.status()["job"]["state"], "finished")

    def test_other_work_on_the_device_blocks_apply_and_apply_blocks_it(self):
        """covers: SETUP-2"""
        modules_api._busy.return_value = "jarvis 갱신이 끝난 뒤에 적용하세요"
        self.assertEqual(modules_api.apply("orca", PLAN_ID), (409, {"error": "jarvis 갱신이 끝난 뒤에 적용하세요"}))
        self.assertEqual(self.calls, [])
        from jarvis import self_update
        modules_api.job.update(state="running", module="orca")
        self.assertEqual(self_update.start()[0], 409)

    def test_an_apply_cut_by_a_restart_is_reported_as_interrupted(self):
        """covers: SETUP-2"""
        with open(modules_api.MARK, "w") as f:
            json.dump({"module": "orca", "plan_id": PLAN_ID, "state": "running", "started_at": 1}, f)
        job = modules_api.status()["job"]
        self.assertEqual((job["state"], job["module"], job["plan_id"]), ("interrupted", "orca", PLAN_ID))
        self.assertFalse(os.path.exists(modules_api.MARK))

    def test_output_that_is_not_json_reports_only_the_exit_code(self):
        """covers: SETUP-1"""
        def fake_run(argv, stdout, stderr, **kw):
            stderr.write(b"Traceback\npairing links are in: /home/x/secret\n")
            return mock.Mock(returncode=1)

        with mock.patch.object(modules_api.subprocess, "run", side_effect=fake_run), \
                mock.patch("builtins.print"):
            code, body = RUN("orca", "status", timeout=1)
        self.assertEqual(code, 1)
        self.assertIn("종료 코드 1", body["error"])
        self.assertNotIn("secret", body["error"])

    def test_public_shape_hides_details_and_result_messages(self):
        """covers: SETUP-1"""
        body = {"modules": {"doctor": {"module": "doctor", "health": "degraded", "details": {"findings": [{"message": "~/.x:1 key K"}]}}},
                "job": {"module": "orca", "state": "finished", "plan_id": PLAN_ID,
                        "result": {"status": "failed", "steps": [{"message": "orca://pair?x"}]}}}
        out = modules_api.public(body)
        self.assertEqual(out["modules"]["doctor"], {"module": "doctor", "health": "degraded"})
        self.assertEqual(out["job"], {"module": "orca", "state": "finished", "status": "failed"})


class ApplyMessageTest(unittest.TestCase):
    def test_a_timed_out_command_does_not_leak_its_arguments(self):
        """covers: SETUP-2"""
        import subprocess
        err = subprocess.TimeoutExpired(["/opt/orca/bin/orca", "environment", "add", "--pairing-code", "orca://pair?SECRET"], 60)
        plan = {"plan_id": PLAN_ID, "action": "reconfigure", "steps": [{"id": "server", "summary": "s", "risk": "config"}],
                "blocked": []}
        with mock.patch.object(orca_module, "plan", return_value=plan), \
                mock.patch.object(orca_module, "_server", side_effect=err), \
                mock.patch.object(orca_module, "_installed", return_value="1.0.0"), \
                mock.patch.object(orca_module, "tools"):
            out = orca_module.apply(PLAN_ID)
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertIn("60초", json.dumps(out, ensure_ascii=False))


class CoreRouteTest(unittest.TestCase):
    def setUp(self):
        Gateway.routes = {"/api/snapshot": (200, b'{"meta": {"host": "gw"}}', False),
                          "/api/modules": (200, json.dumps({"modules": {"doctor": {"health": "ok", "details": {"x": 1}}},
                                                            "job": None}).encode(), False),
                          "/api/modules/orca/plan": (200, json.dumps({"plan_id": PLAN_ID, "steps": []}).encode(), False),
                          f"/api/modules/orca/apply?plan_id={PLAN_ID}": (202, b'{"state": "running"}', False)}
        Gateway.posted = None
        self.gw = serve(Gateway)
        c = core.Core([{"id": "gw", "url": f"http://127.0.0.1:{self.gw.server_address[1]}"}])
        c.poll("gw")
        self.httpd = server.Server(("127.0.0.1", 0), server.make_handler(FakeCollector(), c))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        p = mock.patch("builtins.print")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        for h in (self.httpd, self.gw):
            h.shutdown()
            h.server_close()

    def call(self, path, headers=None, post=False, method=None):
        req = urllib.request.Request(self.base + path, headers=headers or {}, data=b"" if post else None,
                                     method=method or ("POST" if post else "GET"))
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def on_tailnet(self, viewers):
        kind, vw = server.client_kind, config.VIEWERS
        server.client_kind, config.VIEWERS = (lambda ip: "tailnet"), viewers
        self.addCleanup(lambda: (setattr(server, "client_kind", kind), setattr(config, "VIEWERS", vw)))

    def test_everyone_on_the_tailnet_sees_health_but_only_viewers_see_details_and_plans(self):
        """covers: SETUP-1"""
        st, body = self.call("/api/devices/modules")
        dev = body["devices"][0]
        self.assertEqual((st, body["full"], dev["id"], dev["modules"]["doctor"]), (200, False, "gw", {"health": "ok"}))
        self.assertEqual(self.call("/api/devices/modules?refresh=1")[0], 403)  # 강제 새로고침은 맞춤 헤더가 있어야 한다
        self.assertEqual(self.call("/api/devices/gw/modules/orca/plan")[0], 403)
        self.on_tailnet(["127.0.0.1"])
        st, body = self.call("/api/devices/modules")
        self.assertEqual((body["full"], body["devices"][0]["modules"]["doctor"]["details"]), (True, {"x": 1}))
        self.assertEqual(self.call("/api/devices/gw/modules/orca/plan"), (200, {"plan_id": PLAN_ID, "steps": []}))
        self.assertEqual(self.call("/api/devices/gw/modules/nope/plan")[0], 404)
        self.assertEqual(self.call("/api/devices/nope/modules/orca/plan")[0], 404)
        self.assertEqual(self.call("/api/devices/gw/modules/orca/plan", method="HEAD")[0], 200)

    def test_apply_needs_a_viewer_and_the_exact_action_header_and_passes_the_plan_id(self):
        """covers: SETUP-2"""
        path = f"/api/devices/gw/modules/orca/apply?plan_id={PLAN_ID}"
        self.assertEqual(self.call(path, {"X-Jarvis-Action": "module-apply"}, post=True)[0], 403)  # 루프백은 viewer 가 아니다
        self.on_tailnet(["100.64.0.99"])  # tailnet 이지만 viewers 가 아니다
        self.assertEqual(self.call(path, {"X-Jarvis-Action": "module-apply"}, post=True)[0], 403)
        config.VIEWERS = ["127.0.0.1"]
        self.assertEqual(self.call(path, post=True)[0], 403)  # 맞춤 헤더 없음
        self.assertEqual(self.call(path, {"X-Jarvis-Action": "orca-issue"}, post=True)[0], 403)  # 다른 동작의 헤더
        self.assertIsNone(Gateway.posted)
        st, body = self.call(path, {"X-Jarvis-Action": "module-apply"}, post=True)
        self.assertEqual((st, body["state"]), (202, "running"))
        self.assertEqual(Gateway.posted, (f"/api/modules/orca/apply?plan_id={PLAN_ID}", "module-apply"))

    def test_gateway_routes_answer_only_the_configured_core(self):
        """covers: SETUP-2"""
        self.on_tailnet(["127.0.0.1"])  # viewers 여도 게이트웨이 경로는 설정 core 만이다
        for path, post in (("/api/modules", False), ("/api/modules/orca/plan", False),
                           (f"/api/modules/orca/apply?plan_id={PLAN_ID}", True)):
            self.assertEqual(self.call(path, {"X-Jarvis-Action": "module-apply"}, post=post)[0], 403, path)

    def test_a_device_called_modules_keeps_its_own_routes(self):
        self.assertFalse(server._modules_path("/api/devices/modules/snapshot"))
        self.assertTrue(server._modules_path("/api/devices/modules"))
        self.assertTrue(server._modules_path("/api/devices/gw/modules/orca/plan"))


if __name__ == "__main__":
    unittest.main()
