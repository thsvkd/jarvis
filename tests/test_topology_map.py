import unittest
from unittest import mock

from jarvis import config, model

TS = {"backend_state": "Running", "peers_online": 1, "peers_total": 2}


def svc(id, category, status="up", exposure=None, listeners=(), runtime=None, hostnames=("a.example",)):
    return {"id": id, "name": id.split(":")[-1], "title": id, "category": category, "status": status,
            "hostnames": list(hostnames), "exposure": exposure or {"public": False, "tailnet": False, "lan": False,
                                                                     "local_only": True, "via": []},
            "listeners": list(listeners), "runtime": runtime or {"type": "systemd"}, "depends_on": []}


def ids(topo):
    return {n["id"] for n in topo["nodes"]}, {(e["from"], e["to"]) for e in topo["edges"]}


class CaddyNodeTest(unittest.TestCase):
    """covers: MAP-1"""

    def test_no_caddy_node_or_edges_when_caddy_is_turned_off(self):
        with mock.patch.object(config, "CADDY_CONTAINER", ""), mock.patch.object(config, "TUNNEL_CONTAINER", ""):
            nodes, edges = ids(model._topology([svc("infra:ssh", "infra")], {}, TS, []))
        self.assertNotIn("infra:caddy", nodes)
        self.assertFalse([e for e in edges if "infra:caddy" in e])
        self.assertIn(("zone:tailnet", "infra:ssh"), edges)

    def test_no_caddy_when_configured_but_the_service_is_missing(self):
        with mock.patch.object(config, "CADDY_CONTAINER", "caddy"), mock.patch.object(config, "TUNNEL_CONTAINER", "cloudflared"):
            nodes, edges = ids(model._topology([svc("infra:cloudflared", "infra")], {"connections": 2}, TS, []))
        self.assertNotIn("infra:caddy", nodes)
        self.assertNotIn(("infra:cloudflared", "infra:caddy"), edges)

    def test_caddy_and_its_routes_when_present(self):
        with mock.patch.object(config, "CADDY_CONTAINER", "caddy"), mock.patch.object(config, "TUNNEL_CONTAINER", "cloudflared"):
            nodes, edges = ids(model._topology([svc("infra:caddy", "infra"), svc("infra:cloudflared", "infra"),
                                                svc("route:a", "tailnet")], {"connections": 2}, TS, []))
        self.assertIn("infra:caddy", nodes)
        self.assertTrue({("zone:tailnet", "infra:caddy"), ("infra:cloudflared", "infra:caddy"),
                         ("infra:caddy", "route:a")} <= edges)


class LocalOnlyTest(unittest.TestCase):
    """covers: MAP-4 (지도가 묶음을 가르는 데 쓰는 표시)"""

    def test_host_nodes_carry_local_only(self):
        lan = {"public": False, "tailnet": True, "lan": True, "local_only": False, "via": ["direct-lan", "direct-tailnet"]}
        with mock.patch.object(config, "CADDY_CONTAINER", ""), mock.patch.object(config, "TUNNEL_CONTAINER", ""):
            topo = model._topology([svc("hermes:mem", "hermes", listeners=[{"proto": "tcp", "port": 18794, "scope": "loopback"}]),
                                    svc("hermes:stopped", "hermes"),
                                    svc("host:cc", "host", exposure=lan, listeners=[{"proto": "tcp", "port": 5000, "scope": "all"}])],
                                   {}, TS, [])
        by = {n["id"]: n for n in topo["nodes"]}
        self.assertTrue(by["hermes:mem"]["local_only"])
        self.assertFalse(by["hermes:stopped"]["local_only"])  # 포트가 없거나 멈춘 서비스는 루프백 묶음이 아니다
        self.assertFalse(by["host:cc"]["local_only"])
        _, edges = ids(topo)
        self.assertFalse([e for e in edges if e[1] == "hermes:mem"])


if __name__ == "__main__":
    unittest.main()
