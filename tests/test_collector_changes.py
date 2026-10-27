import socket
import tempfile
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from jarvis import config, ingress
from jarvis.collector import Collector
from test_discovery import route


class CollectorChanges(unittest.TestCase):
    def test_caddy_source_change_waits_for_valid_inventory_before_recording_removals(self):
        self.c.raw.update(caddy={"routes": {"demo.example.test": route()}}, collected_at={"caddy": time.time()})
        self.c.journal.observe(self.c.raw)
        with patch.object(config, "reload_if_changed", return_value={"CADDY_CONTAINER"}):
            self.c._refresh_config()
        self.c.journal.observe(self.c.raw)
        self.assertEqual(self.c.journal.events(), [])
        self.assertNotIn("caddy", self.c.raw["collected_at"])

    def test_new_listener_owner_invalidates_previous_http_verdict(self):
        old = {"proto": "tcp", "port": 8080, "pid": 10, "process": "python", "owner": "app", "owner_kind": "scope", "addrs": ["0.0.0.0"]}
        self.c.raw.update(listeners=[old], http_ports={8080: {"http": True, "scheme": "http"}})
        self.c._publish_listeners([{**old, "pid": 20, "process": "other"}], True)
        self.assertNotIn(8080, self.c.raw["http_ports"])
        self.assertTrue(self.c._dns_wake.is_set())

    def test_dns_result_for_old_route_generation_is_discarded(self):
        self.c.raw["caddy"] = {"routes": {"old.example.test": route()}}
        def lookup(hosts):
            self.c._set(caddy={"routes": {"new.example.test": route()}})
            return {"old.example.test": {"points_to": "other"}}
        with patch.object(ingress, "dns_lookup", side_effect=lookup):
            self.c._very_slow()
        self.assertNotIn("old.example.test", self.c.raw.get("dns", {}))

    def setUp(self):
        self.stack = ExitStack()
        td = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(config, "STATE_DIR", td))
        self.stack.enter_context(patch.object(config, "CADDY_CONTAINER", "test-caddy"))
        self.stack.enter_context(patch("jarvis.collector.SystemCollector", return_value=SimpleNamespace(static={})))
        self.stack.enter_context(patch("jarvis.collector._git_commit", return_value="test"))
        self.c = Collector()
        self.stack.enter_context(patch.object(ingress, "parse_caddyfile", return_value=None))
        self.stack.enter_context(patch.object(ingress, "tunnel_script_hosts", return_value=None))

    def tearDown(self):
        self.stack.close()

    def test_retarget_clears_old_probe_dns_and_failure_streak(self):
        host = "demo.example.test"
        self.c.raw.update(caddy={"routes": {host: route("old:8080")}}, probes={host: {"public": {"code": 503}}},
                          dns={host: {"points_to": "other"}})
        self.c._streak[(host, "internal")] = 5
        cfg = {"apps": {"http": {"servers": {"s": {"listen": [":443"], "routes": [
            {"match": [{"host": [host]}], "handle": [
                {"handler": "reverse_proxy", "upstreams": [{"dial": "new:8080"}]}]}]}}}}}

        def probes(*args):
            self.assertNotIn(host, self.c.raw["probes"])
            self.assertNotIn(host, self.c.raw["dns"])
            self.assertNotIn((host, "internal"), self.c._streak)
            return {host: {"internal": {"ok": True, "code": 200, "t": time.time()}}}

        with patch.object(ingress, "caddy_live_config", return_value=cfg), \
                patch.object(ingress, "caddy_file_routes", return_value={}), \
                patch.object(ingress, "caddy_version", return_value="new"), \
                patch.object(ingress, "probe_routes", side_effect=probes):
            self.c._slow()
        self.assertEqual(self.c.raw["probes"][host]["internal"]["fail_streak"], 0)
        self.assertEqual(self.c.raw["caddy"]["version"], "new")
        self.assertTrue(self.c._dns_wake.is_set())

    def test_file_adaptation_failure_preserves_live_inventory_but_marks_drift_unknown(self):
        with patch.object(ingress, "caddy_live_config", return_value={}), \
                patch.object(ingress, "caddy_file_routes", side_effect=RuntimeError("bad file")), \
                patch.object(ingress, "caddy_version", return_value="vnew"), \
                patch.object(ingress, "probe_routes", return_value={}):
            self.c._slow()
        self.assertTrue(self.c.raw["caddy"]["config_ok"])
        self.assertIsNotNone(self.c.raw["caddy"]["caddyfile_error"])
        self.assertNotIn("routes", self.c.raw["caddy"]["caddyfile"])

    def test_removed_http_port_does_not_leave_old_link_cache(self):
        self.c.raw.update(http_ports={1234: {"http": True}}, listeners=[])
        self.c._very_slow()
        self.assertEqual(self.c.raw["http_ports"], {})

    def test_log_reconnect_unblocks_existing_stream(self):
        a, b = socket.socketpair()
        try:
            log = ingress.CaddyLog()
            log._conn = SimpleNamespace(sock=a)
            log.reconnect()
            b.settimeout(1)
            self.assertEqual(b.recv(1), b"")
        finally:
            a.close()
            b.close()
