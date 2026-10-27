"""성공적으로 관측한 구성의 차이만 기록한다. 장애 중 빈 목록은 삭제가 아니다."""
import json
import os
import threading
import time
from collections import deque


class InventoryJournal:
    LIMIT = 200

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.baseline = {}
        self.log = deque(maxlen=self.LIMIT)
        self.load_error = None
        try:
            if os.path.getsize(path) > 2 * 1024 * 1024:
                raise ValueError("inventory too large")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("schema") != 1 or not isinstance(data.get("baseline"), dict) or not isinstance(data.get("events"), list):
                raise ValueError("invalid inventory schema")
            self.baseline = data["baseline"]
            self.log.extend(data["events"][-self.LIMIT:])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as e:
            self.load_error = type(e).__name__ + ": inventory history unavailable"

    def observe(self, raw):
        sources = (
            ("containers", "docker", raw.get("containers") or [], "name",
             ("full_id", "image", "image_id", "revision", "state", "health", "started_at", "networks")),
            ("devices", "tailscale", (raw.get("tailscale") or {}).get("peers") or [], "id",
             ("hostname", "ips", "online", "os")),
            ("units", "systemd", raw.get("units") or [], "name", ("active", "sub", "enabled", "since", "main_pid")),
            ("lan", "neighbors", raw.get("neighbors") or [], "mac", ("ip", "interface")),
            ("pm2", "pm2", (raw.get("pm2") or {}).get("apps") or [], "name", ("status", "started_at", "version")),
        )
        collected, errors = raw.get("collected_at") or {}, raw.get("errors") or {}
        samples = {}
        for section, collector, rows, key, fields in sources:
            if collected.get(collector) and not errors.get(collector):
                if collector == "tailscale" and (raw.get("tailscale") or {}).get("backend_state") != "Running":
                    continue
                samples[section] = {
                    ((r.get("scope", "system") + ":") if section == "units" else "") + str(r[key]):
                    {k: r.get(k) for k in fields} for r in rows}
        if collected.get("caddy") and not errors.get("caddy"):
            samples["routes"] = {h: {k: r.get(k) for k in ("upstreams", "listen", "static", "header_inject")}
                                  for h, r in ((raw.get("caddy") or {}).get("routes") or {}).items()}
        now = time.time()
        with self.lock:
            for section, current in samples.items():
                old = self.baseline.get(section)
                if old is not None:
                    for key in sorted(set(old) | set(current)):
                        if old.get(key) == current.get(key):
                            continue
                        kind = "added" if key not in old else "removed" if key not in current else "changed"
                        self.log.append({"t": now, "source": section, "id": key, "kind": kind,
                                         "before": old.get(key), "after": current.get(key)})
                self.baseline[section] = current

    def events(self):
        with self.lock:
            return list(reversed(self.log))

    def save(self):
        with self.io_lock:
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self.lock:
            body = json.dumps({"schema": 1, "baseline": self.baseline, "events": list(self.log)}, ensure_ascii=False)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
