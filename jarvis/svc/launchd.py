"""launchd 작업(macOS). systemd 유닛과 같은 모양으로 내놓는다. 자원은 주 프로세스(psutil)로 센다.
실동작은 Mac Studio 에서 한 번 확인해야 한다(README 참고). 파서는 tests/ 의 fixture 로 검증한다."""

import re
import time

import psutil

from .. import config
from ..util import Rate, run

_SVC_LINE = re.compile(r"^\s*(-|\d+)\s+(-|-?\d+|\(.*?\))\s+(\S.*?)\s*$")


def _num(s):
    try:
        return int(s.strip("()"))
    except ValueError:
        return None


def parse_print(out):
    """`launchctl print system` 의 services = { PID 마지막종료코드 라벨 } 블록 → [(label, pid, status)]"""
    rows, inside = [], False
    for line in (out or "").splitlines():
        if not inside:
            inside = line.strip() == "services = {"
            continue
        if line.strip() == "}":
            break
        m = _SVC_LINE.match(line)
        if m:
            pid = _num(m.group(1)) if m.group(1) != "-" else None
            rows.append((m.group(3), pid or None, _num(m.group(2)) if m.group(2) != "-" else None))
    return rows


def parse_list(out):
    """`launchctl list` (PID\\tStatus\\tLabel) → [(label, pid, status)]"""
    rows = []
    for line in (out or "").splitlines()[1:]:
        p = line.split("\t")
        if len(p) == 3:
            rows.append((p[2], _num(p[0]) if p[0] != "-" else None, _num(p[1]) if p[1] != "-" else None))
    return rows


class UnitCollector:
    def __init__(self):
        self.rate = Rate()

    def _jobs(self):
        rc, out, _ = run(["launchctl", "print", "system"], timeout=8)
        yield from (("system", *r) for r in parse_print(out))
        rc, out, _ = run(["launchctl", "list"], timeout=8)
        yield from (("user", *r) for r in parse_list(out))

    def collect(self, rss_by_pid):
        now = time.time()
        out, seen = [], set()
        for scope, label, pid, status in self._jobs():
            if (scope, label) in seen:
                continue
            seen.add((scope, label))
            watched = label in config.WATCH_UNITS
            failed = not pid and status not in (None, 0)
            # Apple 기본 작업 수백 개는 빼고, 떠 있거나 실패한 서드파티 작업과 감시 대상만 남긴다
            if not watched and (label.startswith("com.apple.") or not (pid or failed)):
                continue
            # 목록에 있다 = 로드됨. sshd 처럼 연결이 올 때만 뜨는(on-demand) 작업은 대기 중에 PID 가 없다
            active = "failed" if failed else "active"
            since = cpu_pct = mem = None
            if pid:
                try:
                    p = psutil.Process(pid)
                    since = p.create_time()
                    mem = rss_by_pid.get(pid) or p.memory_info().rss
                    ct = p.cpu_times()
                    r = self.rate.per_s(f"{scope}:{label}", (ct.user + ct.system) * 1e6, now)
                    cpu_pct = round(r / 1e6 * 100, 1) if r is not None else None
                except (psutil.Error, OSError):
                    pass
            out.append({
                "name": label, "manager": "launchd", "scope": scope, "description": None,
                "active": active, "sub": "running" if pid else (f"exit {status}" if status else "waiting"),
                "since": since, "uptime_s": round(now - since) if since else None, "main_pid": pid,
                "restarts": 0, "enabled": None, "cgroup": "", "cpu_pct": cpu_pct, "mem_bytes": mem,
            })
        self.rate.prune({f"{u['scope']}:{u['name']}" for u in out})
        return out
