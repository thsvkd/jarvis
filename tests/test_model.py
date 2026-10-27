"""스냅샷 조립(model.build) 회귀 테스트: 재부팅 가드 계약 필드와 최근 고친 동작."""

import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis import config, model, tailnet  # noqa: E402


def unit(name, pid):
    return {"name": name, "manager": "systemd", "scope": "system", "description": name, "active": "active",
            "sub": "running", "since": time.time() - 9999, "uptime_s": 9999, "main_pid": pid, "restarts": 0,
            "enabled": "enabled", "cgroup": f"/system.slice/{name}", "cpu_pct": 0.1, "mem_bytes": 1 << 20}


class Build(unittest.TestCase):
    @patch.dict(config.INFRA_UNITS, pm2="pm2-pi.service")  # 이 기기 설정 파일이 pm2 를 빼도 같은 결과
    def test_pm2_app_listener_not_buried(self):
        """154f27c: pm2 앱은 pm2-<사용자>.service cgroup 안이지만 infra:pm2 에 묻히지 않고 앱 서비스로 나온다."""
        pm2_unit = config.INFRA_UNITS["pm2"]
        now = time.time()
        raw = {
            "units": [unit(pm2_unit, 500)],
            "pm2": {"available": True, "daemon_pid": 500, "apps": [
                {"name": "chat", "pm_id": 0, "pid": 4319, "status": "online", "restarts": 0, "unstable_restarts": 0,
                 "uptime_s": 100, "started_at": now - 100, "cpu_pct": 0, "mem_bytes": 1, "exec": "/x"}]},
            "listeners": [{"proto": "tcp", "port": 4319, "addrs": ["0.0.0.0"], "process": "node", "pid": 4319,
                           "owner_kind": "unit", "owner": pm2_unit, "scope": "all", "reach": ["lan", "tailnet"],
                           "cwd": None}],
            "collected_at": {"docker": now}, "errors": {}, "self": {"started_at": now},
        }
        snap = model.build(raw)
        ids = {s["id"]: s for s in snap["services"]}
        self.assertIn("host:pm2:chat", ids)
        self.assertEqual(ids["host:pm2:chat"]["runtime"]["type"], "pm2")
        self.assertIn("infra:pm2", ids)
        for s in snap["services"]:  # 재부팅 가드가 읽는 필드
            for k in ("id", "status", "probes", "runtime"):
                self.assertIn(k, s)

    def test_self_upstream(self):
        """다른 기기의 :9300(pit5 Caddy → s2 core)은 이 기기 jarvis 라우트가 아니다."""
        from jarvis.util import self_upstream
        own = {"100.64.0.2"}
        self.assertTrue(self_upstream(f"host.docker.internal:{config.PORT}", own))
        self.assertTrue(self_upstream(f"100.64.0.2:{config.PORT}", own))
        self.assertFalse(self_upstream(f"100.64.0.1:{config.PORT}", own))
        self.assertFalse(self_upstream("127.0.0.1:1", own))

    def test_failed_unit_level_by_platform(self):
        """macOS launchd 의 감시 대상이 아닌 '실패'는 참고(info), Linux systemd 의 failed 는 주의(warn)."""
        now = time.time()
        u = {**unit("com.example.updater", None), "manager": "launchd", "scope": "user", "active": "failed", "sub": "exited"}
        raw = {"units": [u], "collected_at": {}, "errors": {}, "self": {"started_at": now}}
        def lvl(row):
            r = {**raw, "units": [row]}
            return next(a["level"] for a in model.build(r)["alerts"] if row["name"] in a["message"])
        self.assertEqual(lvl(u), "info")
        self.assertEqual(lvl({**u, "manager": "systemd"}), "warn")  # systemd 의 failed 는 실제 실패
        self.assertEqual(lvl({**u, "name": "dev.jarvis"}), "warn")  # 모니터 자신은 낮추지 않는다
        old = config.WATCH_UNITS
        try:
            config.WATCH_UNITS = ("com.openssh.sshd",)
            self.assertEqual(lvl({**u, "name": "com.openssh.sshd"}), "warn")  # 감시 대상도 낮추지 않는다
        finally:
            config.WATCH_UNITS = old

    def test_never_collected(self):
        """기동 2분 뒤에도 한 번도 돌지 않은 수집기는 크래시 없이 '수집 지연' 경보가 된다."""
        now = time.time()
        snap = model.build({"collected_at": {}, "errors": {}, "self": {"started_at": now - 120}})
        self.assertTrue(any("한 번도 수집 못 함" in a["message"] for a in snap["alerts"]))

    def test_hermes_gateway(self):
        """Hermes 상태 API 를 본다. 원격(runtime.type remote)이라 재부팅 가드와 무관, 경보는 두 번 연속일 때만."""
        ok = {"gateway_running": True, "gateway_state": "running", "gateway_platforms": {
            "telegram": {"state": "connected", "needs_attention": False},
            "claude:telegram": {"state": "connected", "needs_attention": False}}}
        self.assertEqual(tailnet.hermes_judge(ok), ("up", None))
        bad = {**ok, "gateway_platforms": {**ok["gateway_platforms"],
                                           "claude:telegram": {"state": "disconnected", "needs_attention": True}}}
        self.assertEqual(tailnet.hermes_judge(bad), ("degraded", "어댑터 이상: claude:telegram"))
        self.assertEqual(tailnet.hermes_judge({"gateway_running": False, "gateway_state": "stopped"})[0], "down")

        now = time.time()
        h = {"url": "https://hermes.example/api/status", "ok": True, "ms": 40, "status": "degraded",
             "status_reason": "어댑터 이상: claude:telegram", "version": "0.21.6", "gateway_state": "running",
             "platforms": [], "bad_streak": 1}
        raw = {"hermes": h, "collected_at": {}, "errors": {}, "self": {"started_at": now}}
        snap = model.build(raw)
        svc = {s["id"]: s for s in snap["services"]}["hermes:gateway"]
        self.assertEqual((svc["category"], svc["status"], svc["runtime"]["type"]), ("hermes", "degraded", "remote"))
        self.assertEqual(svc["urls"][0]["url"], "https://hermes.example/")
        self.assertFalse([a for a in snap["alerts"] if a["source"] == "hermes"])
        h["bad_streak"] = 2
        levels = lambda: [a["level"] for a in model.build(raw)["alerts"] if a["source"] == "hermes"]
        self.assertEqual(levels(), ["warn"])
        h.update(status="down", ok=False)
        self.assertEqual(levels(), ["crit"])

    def test_hermes_container_category(self):
        """이름·이미지가 HERMES_RE 에 맞는 컨테이너는 내부 백엔드가 아니라 Hermes 카테고리로 간다."""
        def ctr(name, image):
            return {"name": name, "project": None, "service": None, "image": image, "state": "running",
                    "health": None, "uptime_s": 1, "started_at": time.time() - 1, "restart_count": 0, "cpu_pct": 0,
                    "mem_bytes": 1, "exit_code": 0, "network_mode": "bridge", "networks": [], "aliases": [], "ports": [],
                    "id": name, "restart_policy": "no"}
        raw = {"containers": [ctr("hermes-web", "hermes-app:local"), ctr("pg", "postgres:16")],
               "collected_at": {}, "errors": {}, "self": {"started_at": time.time()}}
        snap = model.build(raw)
        cat = {s["id"]: s["category"] for s in snap["services"]}
        self.assertEqual((cat.get("hermes:ctr:hermes-web"), cat.get("ctr:pg")), ("hermes", "internal"))
        self.assertEqual(snap["summary"]["hermes"], 1)
        self.assertNotIn("openclaw", snap)


if __name__ == "__main__":
    unittest.main()
