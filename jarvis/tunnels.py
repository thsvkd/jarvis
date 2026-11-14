"""이 기기에서 도는 ssh 로컬 포워딩(-L)을 읽는다(10초 주기).

네트워크 지도가 "다른 기기의 프록시 → SSH 터널 → 이 기기 포트" 길을 그리는 데 쓴다(MAP-5·6).
스냅샷에는 로컬 포트·ssh 대상 호스트·대상 포트만 넣는다. 키 경로·옵션·명령 전체는 넣지 않는다.
"""
import os
import re

import psutil

from .util import cmdline

LOOP = ("127.0.0.1", "localhost", "::1", "[::1]")
_SPEC = re.compile(r"^(?:(?P<bind>\[[^\]]*\]|[^:]*):)?(?P<port>\d+):(?P<host>\[[^\]]*\]|[^:]+):(?P<to>\d+)$")
# 값을 하나 받는 ssh 옵션(대상 호스트를 찾을 때 건너뛴다). man ssh 의 SYNOPSIS 기준.
_ARG = set("BbcDEeFIiJLlmOoPpQRSWw")


def parse(argv):
    """ssh 명령줄 → [{port, host, to_port}]. 원격 쪽 루프백으로 가는 -L 만 본다(그 기기의 포트로 닿는 길)."""
    if not argv or os.path.basename(str(argv[0])).lower() not in ("ssh", "ssh.exe"):
        return []
    specs, dest, i = [], None, 1
    while i < len(argv):
        a = str(argv[i])
        if a.startswith("-") and len(a) > 1 and dest is None:
            opt = a[1:]
            # -L 스펙은 붙여 쓸 수도 있다(-L8080:127.0.0.1:80). 묶은 플래그(-NT)는 값이 없다.
            j = 0
            while j < len(opt):
                c = opt[j]
                if c in _ARG:
                    val = opt[j + 1:] or (str(argv[i + 1]) if i + 1 < len(argv) else "")
                    if not opt[j + 1:]:
                        i += 1
                    if c == "L":
                        specs.append(val)
                    break  # ponytail: -o LocalForward·ssh_config 의 LocalForward 는 읽지 않는다(쓰는 기기가 생기면 추가)
                j += 1
        elif dest is None:
            dest = a
        i += 1
    if not dest:
        return []
    host = dest.rsplit("@", 1)[-1]
    out = []
    for s in specs:
        m = _SPEC.match(s.strip())
        if not m or m.group("host").lower() not in LOOP:
            continue
        bind = (m.group("bind") or "").strip("[]")
        if bind and bind not in ("127.0.0.1", "localhost", "::1"):
            continue  # 밖으로 연 포워딩은 지도의 "이 기기 안 프록시 → 터널" 길이 아니다
        out.append({"port": int(m.group("port")), "host": host, "to_port": int(m.group("to"))})
    return out


def collect():
    seen, out = set(), []
    for p in psutil.process_iter(["name"]):  # 명령줄은 ssh 만, 하나씩 읽는다(util.cmdline)
        name = (p.info.get("name") or "").lower()
        if name not in ("ssh", "ssh.exe"):
            continue
        for t in parse(cmdline(p)):
            k = (t["port"], t["host"], t["to_port"])
            if k not in seen:
                seen.add(k)
                out.append(t)
    return sorted(out, key=lambda t: t["port"])
