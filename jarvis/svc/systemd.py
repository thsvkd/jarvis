"""systemd 유닛(시스템 + --user). 자원은 유닛 cgroup 으로 센다."""

import json
import time

from .. import config
from ..platform.linux import cgroup_cpu_usec, cgroup_mem
from ..util import Rate, run

_SHOW_PROPS = "Id,LoadState,Description,ActiveState,SubState,MainPID,ActiveEnterTimestamp,NRestarts,ControlGroup,UnitFileState,FragmentPath"


def _list_units(user):
    args = ["systemctl"] + (["--user"] if user else []) + [
        "list-units", "--type=service", "--all", "--output=json", "--no-pager", "--plain"]
    rc, out, _ = run(args)
    if rc:
        raise RuntimeError("systemd list-units failed")
    try:
        return json.loads(out or "[]")
    except ValueError as e:
        raise RuntimeError("invalid systemd inventory") from e


def _show(units, user):
    if not units:
        return {}
    args = ["systemctl"] + (["--user"] if user else []) + ["show", "--timestamp=unix", "-p", _SHOW_PROPS] + units
    rc, out, _ = run(args, timeout=8)
    if rc:
        raise RuntimeError("systemd show failed")
    res, cur = {}, {}
    for line in (out or "").splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                res[cur["Id"]] = cur
            cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    return res


class UnitCollector:
    def __init__(self):
        self.rate = Rate()

    def collect(self, rss_by_pid):
        now = time.time()
        out = []
        for user in (False, True):
            listed = [u for u in _list_units(user) if u.get("load") == "loaded"]
            if not user:
                found = {u["unit"] for u in listed}
                listed += [{"unit": n, "active": "inactive", "sub": "not-found"}
                           for n in config.EXPECTED_UNITS if n not in found]
            props = _show([u["unit"] for u in listed], user)
            for u in listed:
                p = props.get(u["unit"], {})
                cg = p.get("ControlGroup") or ""
                cpu_pct = mem = None
                if cg and u.get("active") == "active":
                    usage = cgroup_cpu_usec(cg)
                    if usage is not None:
                        r = self.rate.per_s(u["unit"] + ("@u" if user else ""), usage, now)
                        cpu_pct = round(r / 1e6 * 100, 1) if r is not None else None
                    mem = cgroup_mem(cg, rss_by_pid)
                ts = p.get("ActiveEnterTimestamp", "")
                since = float(ts[1:]) if ts.startswith("@") else None
                main_pid = int(p.get("MainPID") or 0) or None
                out.append({
                    "name": u["unit"],
                    "manager": "systemd",
                    "scope": "user" if user else "system",
                    "description": u.get("description"),
                    "active": u.get("active"),
                    "sub": u.get("sub"),
                    "since": since,
                    "uptime_s": round(now - since) if since and u.get("active") == "active" else None,
                    "main_pid": main_pid,
                    "restarts": int(p.get("NRestarts") or 0),
                    "enabled": p.get("UnitFileState") or None,
                    "cgroup": cg,
                    "cpu_pct": cpu_pct,
                    "mem_bytes": mem,
                })
        self.rate.prune({u["name"] + ("@u" if u["scope"] == "user" else "") for u in out})
        return out
