"""구성 변경·원격 라우트·배포 감지 회귀 테스트."""
import json
import time
import unittest
import tempfile
import pathlib
import os
import runpy
import threading
from unittest.mock import patch

from jarvis import config, ingress, model, runtime
from jarvis.svc import systemd


def route(upstream="100.70.0.2:8080"):
    return {"http": False, "https": True, "listen": [":443"], "upstreams": [upstream],
            "header_inject": False, "static": False}


def peer():
    return {"id": "new-node", "hostname": "new-server", "dns_short": "new-server", "online": True,
            "ip": "100.70.0.2", "ips": ["100.70.0.2"], "dns_name": "new-server.example.ts.net",
            "os": "linux", "cur_addr": "192.168.0.20:41641"}


def raw():
    now = time.time()
    return {"self": {"started_at": now}, "collected_at": {"docker": now}, "errors": {},
            "tailscale": {"backend_state": "Running", "peers": [peer()]},
            "caddy": {"config_ok": True, "routes": {"demo.example.test": route()}},
            "probes": {"demo.example.test": {"internal": {"ok": True, "code": 200, "t": now}}}}


class RemoteRoutes(unittest.TestCase):
    def test_caddy_fallback_is_not_remote_application_success(self):
        r = raw()
        r["probes"]["demo.example.test"]["internal"].update(code=404, caddy_fallback=True)
        web = next(x for x in model.build(r)["services"] if x["id"].startswith("route:"))
        self.assertNotEqual(web["status"], "up")

    def test_shared_public_nat_does_not_identify_upstream_owner(self):
        r = raw()
        a, b = peer(), {**peer(), "id": "second", "hostname": "second", "ip": "100.70.0.3", "ips": ["100.70.0.3"]}
        a["cur_addr"] = b["cur_addr"] = "203.0.113.20:41641"
        r["tailscale"]["peers"] = [a, b]
        r["caddy"]["routes"]["demo.example.test"] = route("203.0.113.20:8080")
        web = next(x for x in model.build(r)["services"] if x["id"].startswith("route:"))
        self.assertIsNone(web["runtime"])
    def test_successful_remote_route_has_runtime_and_physical_location(self):
        s = model.build(raw())
        web = next(x for x in s["services"] if x["id"] == "route:demo.example.test")
        self.assertEqual(web["status"], "up")
        self.assertEqual(web["runtime"]["type"], "remote")
        self.assertEqual(web["runtime"]["name"], "new-server")
        n = next(n for n in s["topology_physical"]["nodes"] if n["id"] == "peer:new-server")
        self.assertIn(web["id"], n["service_ids"])

    def test_stale_success_does_not_make_remote_route_healthy(self):
        r = raw()
        r["probes"]["demo.example.test"]["internal"]["t"] -= 1000
        web = next(x for x in model.build(r)["services"] if x["id"].startswith("route:"))
        self.assertEqual(web["status"], "unknown")

    def test_same_public_nat_does_not_merge_physical_devices(self):
        a, b = peer(), {**peer(), "id": "second", "hostname": "second", "dns_short": "second"}
        a["cur_addr"] = b["cur_addr"] = "203.0.113.20:41641"
        r = raw()
        r["tailscale"]["peers"] = [a, b]
        nodes = [n for n in model.build(r)["topology_physical"]["nodes"] if n["type"] == "peer"]
        self.assertTrue(all(n["parent"] is None for n in nodes))


class CaddyDrift(unittest.TestCase):
    def test_desired_routes_use_caddy_adapter(self):
        cfg = {"apps": {"http": {"servers": {"s": {"listen": [":443"], "routes": [
            {"match": [{"host": ["demo.example.test"]}], "handle": [
                {"handler": "reverse_proxy", "upstreams": [{"dial": "new-app:8080"}]}]}]}}}}}
        with patch.object(ingress, "run", return_value=(0, json.dumps(cfg), "")):
            routes = ingress.caddy_file_routes()
        self.assertEqual(routes["demo.example.test"]["upstreams"], ["new-app:8080"])
    def test_changed_upstream_is_a_drift_with_the_same_hostname(self):
        r = raw()
        r["caddy"]["caddyfile"] = {"hosts": {"demo.example.test": {}},
                                      "routes": {"demo.example.test": route("new-app:8080")}}
        alerts = model.build(r)["alerts"]
        self.assertTrue(any(a["target"] == "caddyfile-drift" for a in alerts))

    def test_empty_desired_routes_are_not_skipped(self):
        r = raw()
        r["caddy"]["caddyfile"] = {"hosts": {}, "routes": {}}
        self.assertTrue(any(a["target"] == "caddyfile-drift" for a in model.build(r)["alerts"]))


class UnitInventory(unittest.TestCase):
    def test_missing_expected_service_is_reported_down(self):
        with patch.object(config, "EXPECTED_SERVICES", ["route:missing.example.test"], create=True):
            s = model.build(raw())
        missing = next((x for x in s["services"] if x["id"] == "route:missing.example.test"), None)
        self.assertIsNotNone(missing)
        self.assertEqual(missing["status"], "down")
        self.assertTrue(any(a["target"] == missing["id"] for a in s["alerts"]))
    def test_stopped_loaded_application_stays_in_inventory(self):
        u = {"unit": "demo.service", "load": "loaded", "active": "inactive", "sub": "dead"}
        with patch.object(systemd, "_list_units", side_effect=lambda user: [] if user else [u]), \
                patch.object(systemd, "_show", return_value={}):
            self.assertEqual([x["name"] for x in systemd.UnitCollector().collect({})], ["demo.service"])


class DeploymentIdentity(unittest.TestCase):
    def test_image_digest_is_collected(self):
        c = {"Id": "a" * 64, "Names": ["/demo"], "State": "exited", "Status": "Exited",
             "Image": "demo:latest", "ImageID": "sha256:new", "Labels": {"org.opencontainers.image.revision": "commit123"}}
        with patch.object(runtime, "docker_api", side_effect=lambda p, **kw: [c] if "?all" in p else {}):
            x = runtime.DockerCollector().collect({})[0]
        self.assertEqual(x["image_id"], "sha256:new")
        self.assertEqual(x["revision"], "commit123")


class TunnelCache(unittest.TestCase):
    def test_restart_with_lower_config_version_replaces_old_ingress(self):
        t = ingress.TunnelCollector()
        old_start = time.time() - 100
        t.process_start = old_start
        t.last_ingress = {"version": 10, "rules": [{"hostname": "old.example.test"}]}
        inner = json.dumps({"ingress": [{"hostname": "new.example.test"}]})
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = stamp + ' INF Updated to new configuration config=' + json.dumps(inner) + ' version=1'
        metrics = 'cloudflared_tunnel_ha_connections 4\nprocess_start_time_seconds ' + str(time.time()-2)
        with patch.object(t, "_metrics", return_value=(metrics, {"status": 200})), \
                patch.object(ingress, "run", return_value=(0, line, "")):
            out = t.collect()
        self.assertEqual(out["hostnames"], ["new.example.test"])


class ReloadConfig(unittest.TestCase):
    def test_valid_change_and_invalid_change_retains_last_good_values(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "config.toml"
            p.write_text('domain="first.test"\n')
            with patch.dict(os.environ, {"JARVIS_CONFIG": str(p)}):
                ns = runpy.run_path(config.__file__)
                reload = ns["reload_if_changed"]
                actual = reload.__globals__
                p.write_text('domain="second.test"\nhermes_re="hermes|agent"\n')
                reload()
                self.assertEqual(actual["DOMAIN"], "second.test")
                self.assertEqual(actual["HERMES_RE"].pattern, "hermes|agent")
                p.write_text('domain="broken\n')
                with self.assertRaises(ValueError):
                    reload()
                self.assertEqual(actual["DOMAIN"], "second.test")
                p.write_text('domain="third.test"\nport=9400\n')
                reload()
                self.assertEqual(actual["DOMAIN"], "third.test")
                self.assertEqual(actual["PORT"], 9300)
                self.assertIn("PORT", actual["CONFIG_RESTART_REQUIRED"])


class Journal(unittest.TestCase):
    def test_system_and_user_units_with_same_name_are_independent(self):
        from jarvis.changes import InventoryJournal
        with tempfile.TemporaryDirectory() as td:
            j = InventoryJournal(str(pathlib.Path(td) / "inventory.json"))
            r = {"collected_at": {"systemd": time.time()}, "units": [
                {"name": "demo.service", "scope": "system", "active": "active"},
                {"name": "demo.service", "scope": "user", "active": "active"}]}
            j.observe(r)
            r["units"][0]["active"] = "inactive"
            j.observe(r)
            self.assertEqual(j.events()[0]["id"], "system:demo.service")
            self.assertEqual(j.events()[0]["after"]["active"], "inactive")

    def test_shutdown_and_periodic_save_are_serialized(self):
        from jarvis.changes import InventoryJournal
        with tempfile.TemporaryDirectory() as td:
            j = InventoryJournal(str(pathlib.Path(td) / "inventory.json"))
            entered = threading.Event()
            release = threading.Event()
            second = threading.Event()
            def save_body():
                if entered.is_set():
                    second.set()
                entered.set()
                release.wait(1)
            with patch.object(j, "_save", side_effect=save_body):
                a = threading.Thread(target=j.save)
                b = threading.Thread(target=j.save)
                a.start()
                self.assertTrue(entered.wait(1))
                b.start()
                try:
                    self.assertFalse(second.wait(0.05))
                finally:
                    release.set()
                    a.join(1)
                    b.join(1)
                self.assertTrue(second.is_set())

    def test_same_tag_redeployment_and_removal_persist_without_failure_deletions(self):
        from jarvis.changes import InventoryJournal
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "inventory.json"
            journal = InventoryJournal(str(p))
            r = raw()
            r["containers"] = [{"name": "demo", "full_id": "one", "image": "demo:latest", "image_id": "sha256:one", "state": "running"}]
            journal.observe(r)
            self.assertEqual(journal.events(), [])
            r["containers"][0] = {**r["containers"][0], "full_id": "two", "image_id": "sha256:two"}
            journal.observe(r)
            self.assertEqual(journal.events()[0]["kind"], "changed")
            r["errors"]["docker"] = "unavailable"
            r["containers"] = []
            journal.observe(r)
            self.assertEqual(len(journal.events()), 1)
            r["errors"].clear()
            journal.observe(r)
            self.assertEqual(journal.events()[0]["kind"], "removed")
            journal.save()
            restored = InventoryJournal(str(p))
            self.assertEqual(restored.events(), journal.events())


class Neighbors(unittest.TestCase):
    def test_only_lan_neighbors_and_no_failed_or_docker_devices(self):
        from jarvis.discovery import parse_neighbors
        rows = [{"dst": "192.168.0.20", "dev": "eth0", "lladdr": "aa:bb:cc:dd:ee:ff", "state": ["STALE"]},
                {"dst": "192.168.0.21", "dev": "eth0", "state": ["FAILED"]},
                {"dst": "172.18.0.2", "dev": "docker0", "lladdr": "aa:bb:cc:dd:ee:fe", "state": ["REACHABLE"]}]
        out = parse_neighbors(rows, {"eth0": {"ipv4_cidr": ["192.168.0.10/24"]}})
        self.assertEqual([x["ip"] for x in out], ["192.168.0.20"])
        self.assertIsNone(out[0]["online"])


if __name__ == "__main__":
    unittest.main()


class ProbeSkipTest(unittest.TestCase):
    def test_skips_hosts_matching_a_glob(self):
        routes = {"admin.example.com": route(), "review.admin.example.com": route(), "auto-review.example.com": route()}
        self.assertEqual(list(ingress.probe_targets(routes, ["admin.example.com", "*.admin.example.com"])), ["auto-review.example.com"])
        self.assertEqual(len(ingress.probe_targets(routes, [])), 3)
