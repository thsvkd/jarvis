"""Orca CLI 와 같은 로컬 RPC. 인증은 런타임 파일에서만 읽고 HTTP 응답·명령 인자에 넣지 않는다.

공식 계약: src/cli/runtime/transport.ts (JSON 한 줄, keepalive, 요청·런타임 ID).
Windows 파이프의 동기 I/O 는 자식 프로세스에서 실행해 시간 초과 때 확실히 회수한다.
"""
import json
import os
import socket
import subprocess
import sys
import time
import uuid

from . import orca

METHODS = {"updater.getStatus", "updater.check", "updater.download", "updater.install", "worktree.ps",
           "terminal.list", "orcad.terminalCensus", "accounts.list"}
MAX_FRAME = 8 << 20


def _response(stream, request, runtime_id):
    while True:
        line = stream.readline(MAX_FRAME + 1)
        if not line or len(line) > MAX_FRAME or not line.endswith(b"\n"):
            raise RuntimeError("Orca RPC 응답이 끊겼거나 너무 큽니다")
        body = json.loads(line)
        if body.get("_keepalive") is True:
            continue
        if body.get("id") != request["id"] or (body.get("_meta") or {}).get("runtimeId") != runtime_id:
            raise RuntimeError("Orca RPC 요청 또는 런타임이 바뀌었습니다")
        if body.get("ok") is not True:
            # 서버 오류에 인증 자료가 섞이지 않게 코드만 보낸다.
            raise RuntimeError("Orca RPC 실패: " + str((body.get("error") or {}).get("code", "unknown")))
        return body.get("result")


def _pipe(endpoint, request, runtime_id):
    with open(endpoint, "r+b", buffering=0) as pipe:
        pipe.write((json.dumps(request) + "\n").encode())
        return _response(pipe, request, runtime_id)


def call(method, params=None, timeout=12):
    if method not in METHODS:
        raise ValueError("허용되지 않은 Orca RPC")
    d = orca.find_dir(orca.DIRS)
    metadata = orca._read(d, "orca-runtime.json") if d else None
    if not metadata or not metadata.get("authToken") or not metadata.get("runtimeId"):
        raise RuntimeError("실행 중인 Orca 의 로컬 RPC 정보를 찾지 못했습니다")
    transport = next((t for t in metadata.get("transports", []) if t.get("kind") in ("unix", "named-pipe")), None)
    if not transport:
        raise RuntimeError("Orca 로컬 소켓이 없습니다")
    request = {"id": str(uuid.uuid4()), "authToken": metadata["authToken"], "method": method, "params": params}
    if transport["kind"] == "named-pipe":
        child = "import json,sys; from jarvis.orca_rpc import _pipe; print(json.dumps(_pipe(**json.load(sys.stdin))))"
        p = subprocess.run([sys.executable, "-c", child],
                           input=json.dumps({"endpoint": transport["endpoint"], "request": request, "runtime_id": metadata["runtimeId"]}),
                           capture_output=True, text=True, timeout=timeout,
                           cwd=os.path.dirname(os.path.dirname(__file__)),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode:
            raise RuntimeError("Orca 로컬 파이프에 연결하지 못했습니다")
        return json.loads(p.stdout)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(transport["endpoint"])
        sock.sendall((json.dumps(request) + "\n").encode())
        with sock.makefile("rb") as stream:
            return _response(stream, request, metadata["runtimeId"])
