import unittest

from jarvis import fleet


def snap(host, ip, services=(), routes=(), tunnels=()):
    return {"meta": {"host": host},
            "tailscale": {"self": {"hostname": host, "dns_name": host.lower() + ".tail.ts.net.", "ips": [ip]}},
            "services": list(services), "ssh_tunnels": list(tunnels),
            "ingress": {"caddy": {"routes": list(routes)}}}


def listen(id, port, status="up"):
    return {"id": id, "status": status, "listeners": [{"proto": "tcp", "port": port}]}


class InboundTest(unittest.TestCase):
    """covers: MAP-6"""

    def setUp(self):
        self.snaps = {
            "mac": snap("Mac-mini", "100.64.0.9", services=[listen("hermes:dash", 9119), listen("host:orb", 8899),
                                                            listen("infra:monitor", 9300)]),
            "s2": snap("pi5-server2", "100.64.0.2",
                       services=[{"id": "route:yt.example", "status": "up", "listeners": []}],
                       routes=[{"host": "yt.example", "upstreams": ["127.0.0.1:28899"], "public": False},
                               {"host": "local.example", "upstreams": ["127.0.0.1:3000"], "public": False}],
                       tunnels=[{"port": 28899, "host": "100.64.0.9", "to_port": 8899}]),
            "pit5": snap("pit5", "100.64.0.5",
                         routes=[{"host": "jarvis.example", "upstreams": ["mac-mini.tail.ts.net:9300"], "public": True},
                                 {"host": "llm.example", "upstreams": ["macstudio.tail.ts.net:8445"], "public": True}]),
        }
        self.devices = {"mac": {"id": "mac", "url": "http://100.64.0.9:9300"}, "s2": {"id": "s2"},
                        "pit5": {"id": "pit5", "ssh": "pit5-alias", "url": "ssh://pit5-alias"}}

    def test_joins_ssh_tunnels_and_tailnet_upstreams_to_the_target_ports(self):
        out = fleet.inbound_paths("mac", self.devices, self.snaps)
        self.assertEqual([(p["from"], p["host"], p["via"], p["port"], p["to_port"], p["service_id"]) for p in out], [
            ("pit5", "jarvis.example", "tailnet", None, 9300, "infra:monitor"),
            ("s2", "yt.example", "ssh", 28899, 8899, "host:orb"),
        ])
        self.assertTrue(out[0]["public"])
        self.assertEqual(out[1]["status"], "up")

    def test_a_port_held_by_orbstack_is_matched_to_the_container_by_name_and_marked_a_guess(self):
        mac = self.snaps["mac"]["services"]
        mac[1]["name"] = "OrbStack Helper"
        mac += [{"id": "hermes:ctr:hermes-ytdoc", "name": "hermes-ytdoc", "listeners": [], "runtime": {"type": "container"}}]
        p = [x for x in fleet.inbound_paths("mac", self.devices, self.snaps) if x["host"] == "yt.example"][0]
        self.assertEqual((p["service_id"], p["guess"]), ("hermes:ctr:hermes-ytdoc", True))

    def test_a_name_two_devices_share_matches_neither(self):
        for d, dns, ip in (("pa", "raspberrypi", "100.64.0.21"), ("pb", "raspberrypi-1", "100.64.0.22")):
            self.snaps[d] = snap("raspberrypi", ip, services=[listen("host:app", 8080)])
            self.snaps[d]["tailscale"]["self"]["dns_name"] = dns + ".tail.ts.net."
        self.snaps["s2"]["ingress"]["caddy"]["routes"] = [
            {"host": "a.example", "upstreams": ["raspberrypi:8080"], "public": False},
            {"host": "b.example", "upstreams": ["raspberrypi-1.tail.ts.net:8080"], "public": False}]
        self.assertEqual([p["host"] for p in fleet.inbound_paths("pa", self.devices, self.snaps)], [])
        self.assertEqual([p["host"] for p in fleet.inbound_paths("pb", self.devices, self.snaps)], ["b.example"])

    def test_other_devices_and_unknown_hosts_get_nothing(self):
        self.assertEqual(fleet.inbound_paths("s2", self.devices, self.snaps), [])
        self.assertEqual(fleet.inbound_paths("missing", self.devices, self.snaps), [])

    def test_tunnel_destination_may_be_an_ssh_alias(self):
        self.snaps["s2"]["ssh_tunnels"] = [{"port": 28899, "host": "macmini-alias", "to_port": 9119}]
        self.devices["mac"]["ssh"] = "macmini-alias"
        out = fleet.inbound_paths("mac", self.devices, self.snaps)
        self.assertIn(("s2", "hermes:dash"), [(p["from"], p["service_id"]) for p in out])


if __name__ == "__main__":
    unittest.main()
