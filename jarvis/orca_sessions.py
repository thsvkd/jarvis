"""공개 RPC 와 OS 프로세스 증거로 기존 PTY 세션의 갱신 전후 연속성을 확인한다.

데몬의 내부 프로토콜에는 연결하지 않는다. 구형 런타임은 admission fence 를 무시할 수
있으므로 fenced 여부와 관찰한 기존 세션의 보존을 구분한다.
"""
import glob
import json
import os
import re
import sys
import time

import psutil

from . import orca, orca_rpc
from .util import run


class Deferred(RuntimeError):
    """세션을 지키려고 갱신을 미룬다(실패가 아니라 보류다). 모듈 계약의 deferred(종료 코드 2)."""


REMOTE_HOSTS = ("runtime:", "ssh:")


def complete(body, key):
    """로컬 호스트를 빠짐없이 조사했는지. 원격 호스트(runtime·ssh)는 조사에서 빠져도 된다:
    runtime 세션은 다른 기기의 Orca 서버에 있고, ssh 세션은 원격의 relay 가 로컬 앱 재시작 뒤에도
    relayGracePeriodSeconds(기본 10800초) 동안 붙들고 있다가 다시 붙는다(Orca 1.4.244 main 번들)."""
    scope = body.get("hostScope") if isinstance(body, dict) else None
    rows = body.get(key) if isinstance(body, dict) else None
    omitted = scope.get("omittedHostIds") if isinstance(scope, dict) else None
    if not isinstance(scope, dict) or scope.get("hostIds") != ["local"] or not isinstance(omitted, list) \
            or not all(isinstance(h, str) and h.startswith(REMOTE_HOSTS) for h in omitted) \
            or not isinstance(rows, list) or body.get("truncated") is not False \
            or type(body.get("totalCount")) is not int or body["totalCount"] != len(rows):
        raise Deferred("Orca 세션 조사 범위가 불완전하여 업데이트를 보류합니다")
    return rows


def process_identity(pid):
    p = psutil.Process(pid)
    if p.status() == psutil.STATUS_ZOMBIE:
        raise RuntimeError("Orca 데몬이 종료된 상태입니다")
    return {"pid": p.pid, "created": p.create_time()}


def cgroup(pid):
    with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
        rows = [line.strip().split(":", 2) for line in f]
    # cgroup v2 를 사용하지 않는 호스트는 격리를 증명할 수 없다.
    paths = [row[2] for row in rows if len(row) == 3 and row[:2] == ["0", ""]]
    if len(paths) != 1 or not paths[0].startswith("/"):
        raise RuntimeError("Orca 데몬의 systemd 격리를 확인할 수 없습니다")
    return paths[0]


def daemon_records():
    directory = orca.find_dir(orca.DIRS)
    records = []
    for path in sorted(glob.glob(os.path.join(directory or "", "daemon", "daemon-v*.pid"))):
        match = re.fullmatch(r"daemon-v(\d+)\.pid", os.path.basename(path))
        if not match:
            continue
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        if not isinstance(record, dict) or type(record.get("pid")) is not int:
            raise RuntimeError("Orca 데몬의 프로세스 기록을 확인할 수 없습니다")
        try:
            identity = process_identity(record["pid"])
        except psutil.NoSuchProcess:
            continue  # 종료된 세대의 남은 기록은 살아 있는 세션이 아니다.
        entry = record.get("entryPath")
        p = psutil.Process(record["pid"])
        argv = p.cmdline()
        if not isinstance(entry, str) or os.path.basename(entry) != "daemon-entry.js" or entry not in argv \
                or not isinstance(record.get("startedAtMs"), (int, float)) \
                or abs(identity["created"] - record["startedAtMs"] / 1000) > 2:
            raise RuntimeError("Orca 데몬 PID 의 재사용 또는 실행 경로 변경을 감지했습니다")
        nonce = record.get("launchNonce")
        if nonce and ("--launch-nonce" not in argv or argv[argv.index("--launch-nonce") + 1:][:1] != [nonce]):
            raise RuntimeError("Orca 데몬의 실행 식별자가 일치하지 않습니다")
        records.append({**identity, "protocol": int(match.group(1))})
    return records


def assert_isolated(records):
    if not sys.platform.startswith("linux") or not records:
        return
    rc, out, _ = run(["systemctl", "--user", "show", "orca-serve", "-p", "ControlGroup"], timeout=10)
    group = out.strip().removeprefix("ControlGroup=")
    if rc or not group.startswith("/") or group == "/":
        raise RuntimeError("Orca 서비스의 종료 범위를 확인할 수 없습니다")
    for record in records:
        p = psutil.Process(record["pid"])
        for child in [p] + p.children(recursive=True):
            try:
                path = cgroup(child.pid)
            except (FileNotFoundError, psutil.NoSuchProcess):
                continue
            if path == group or path.startswith(group + "/"):
                raise Deferred("터미널 데몬 또는 작업이 Orca 서비스 종료 범위에 있어 업데이트를 보류합니다")


def read_contract(path):
    """검증한 패키지의 고정 경로만 읽는다. 패키지 JavaScript 를 실행하지 않는다."""
    with open(path, encoding="utf-8") as f:
        text = f.read(64 << 10)
    current = re.findall(r"\bdaemonProtocolVersion\s*:\s*(\d+)\b", text)
    versions = re.findall(r"\battachableDaemonProtocolVersions\s*:\s*\[([\d,\s]+)\]", text)
    if len(current) != 1 or len(versions) != 1:
        raise RuntimeError("새 Orca 패키지의 데몬 호환성 계약을 확인할 수 없습니다")
    allowed = {int(v.strip()) for v in versions[0].split(",") if v.strip()}
    if int(current[0]) not in allowed or not allowed or min(allowed) < 1:
        raise RuntimeError("새 Orca 패키지의 데몬 호환성 계약이 잘못되었습니다")
    return allowed


class Sessions:
    def __init__(self):
        self.terminals = {}
        self.daemons = {}
        self.fenced = False
        self.fence_until = 0
        self.checked_at = 0

    def capture(self, fence=False):
        params = {"activatedAt": 0}
        if fence:
            params["closeAdmissionForMs"] = 300000
        facts = orca_rpc.call("orcad.terminalCensus", params, timeout=25)
        if not isinstance(facts, dict) or type(facts.get("liveSessions")) is not int or facts["liveSessions"] < 0 \
                or type(facts.get("inProcessSessions")) is not int or facts["inProcessSessions"] != 0:
            raise Deferred("데몬 밖 터미널 또는 확인할 수 없는 세션이 있어 업데이트를 보류합니다")
        until = facts.get("admissionClosedUntil", 0)
        self.fenced = isinstance(until, (int, float)) and until > time.time() * 1000 + 15000
        self.fence_until = until if self.fenced else 0
        trees = complete(orca_rpc.call("worktree.ps", {"limit": 10000}), "worktrees")
        for row in trees:
            if not isinstance(row, dict) or any(type(row.get(k)) is not int or row[k] < 0 for k in ("liveTerminalCount", "unverifiableTerminalCount")) \
                    or type(row.get("hasAttachedPty")) is not bool or not isinstance(row.get("agents"), list) \
                    or any(not isinstance(a, dict) or not isinstance(a.get("state"), str) for a in row["agents"]):
                raise Deferred("Orca 작업 상태가 불완전하여 업데이트를 보류합니다")
            if row["unverifiableTerminalCount"]:
                raise Deferred("확인할 수 없는 터미널이 있어 업데이트를 보류합니다")
            if any(a.get("structuredHostOwned") is True and a["state"] not in ("done", "idle", "exited") for a in row["agents"]):
                raise Deferred("실행 중인 네이티브 채팅 작업은 런타임과 함께 종료되므로 업데이트를 보류합니다")
        listed = complete(orca_rpc.call("terminal.list", {"limit": 10000, "requireFreshPtyLiveness": True}), "terminals")
        live = {}
        for t in listed:
            if not isinstance(t, dict) or t.get("executionHostId") != "local" or not isinstance(t.get("ptyId"), str) \
                    or not isinstance(t.get("incarnationId"), str) or not t["incarnationId"] \
                    or type(t.get("connected")) is not bool:
                raise Deferred("터미널의 데몬 식별 정보를 확인할 수 없습니다")
            if t["connected"]:
                if t["ptyId"] in live:
                    raise Deferred("터미널 식별자가 중복되어 업데이트를 보류합니다")
                live[t["ptyId"]] = t["incarnationId"]
        if len(live) != facts["liveSessions"] or sum(w["liveTerminalCount"] for w in trees) > len(live) \
                or any(live.get(k) != v for k, v in self.terminals.items()):
            raise Deferred("조사 중 세션이 바뀌었거나 누락되어 업데이트를 보류합니다")
        panes = {f"{t.get('tabId')}:{t.get('leafId')}" for t in listed if t.get("connected") is True}
        for row in trees:
            for agent in row["agents"]:
                if agent["state"] in ("done", "idle", "exited") or agent.get("paneKey") in panes:
                    continue
                raise Deferred("에이전트가 영속 터미널에 연결되어 있는지 확인할 수 없어 업데이트를 보류합니다")
        records = daemon_records() if live else []
        if live and not records:
            raise Deferred("세션을 유지하는 별도 터미널 데몬을 확인할 수 없습니다")
        assert_isolated(records)
        for record in records:
            if record["pid"] in self.daemons and record != self.daemons[record["pid"]]:
                raise Deferred("조사 중 터미널 데몬의 프로세스가 바뀌었습니다")
        self.terminals.update(live)
        self.daemons.update({r["pid"]: r for r in records})
        self.checked_at = time.monotonic()

    def before_stop(self):
        if not self.checked_at or time.monotonic() - self.checked_at > 10 \
                or (self.fenced and self.fence_until < time.time() * 1000 + 15000):
            raise Deferred("세션 조사 뒤 시간이 지나 다시 확인해야 합니다")

    def compatible(self, versions):
        if any(d["protocol"] not in versions for d in self.daemons.values()):
            raise Deferred("새 Orca 가 기존 터미널 데몬에 재연결할 수 없어 업데이트를 보류합니다")

    def verify(self):
        """바꾼 뒤의 확인이다. 여기서 확인하지 못하면 보류(Deferred)가 아니라 실패다."""
        try:
            return self._verify()
        except Deferred as e:
            raise RuntimeError(str(e)) from None

    def _verify(self):
        for d in self.daemons.values():
            if process_identity(d["pid"]) != {k: d[k] for k in ("pid", "created")}:
                raise RuntimeError("업데이트 후 기존 터미널 데몬이 유지되지 않았습니다")
        listed = complete(orca_rpc.call("terminal.list", {"limit": 10000, "requireFreshPtyLiveness": True}), "terminals")
        now = {t.get("ptyId"): t.get("incarnationId") for t in listed if t.get("connected") is True}
        if any(now.get(k) != v for k, v in self.terminals.items()):
            raise RuntimeError("업데이트 후 기존 PTY 세션의 재연결을 확인하지 못했습니다")
        return {"state": "preserved", "terminals": len(self.terminals), "daemons": len(self.daemons),
                "admission_fenced": self.fenced}

    def release(self):
        if self.fenced:
            orca_rpc.call("orcad.terminalCensus", {"activatedAt": 0, "closeAdmissionForMs": 0}, timeout=25)
            self.fence_until = 0
