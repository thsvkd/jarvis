"""이 기기 Orca 의 페어링 코드(데스크톱 = runtime, 모바일 = mobile). GET /api/orca 로 core 에게만 준다.

Orca 는 serve 를 시작할 때나 데스크톱 UI 에서만 대기 기기(lastSeenAt == 0)를 만든다. 그 토큰으로 orca://pair 코드를 만든다.
대기 기기가 없으면 가장 최근에 페어링된 기기의 코드를 준다(pending false, 사용자 결정): 그 코드로 붙으면 그 기기와 같은 자격을
나눠 쓴다(새 기기가 생기지 않고, 해제하면 함께 끊긴다). 비밀 키는 읽지 않는다.
코드는 이 기기 Orca 런타임(셸)을 여는 비밀이다: 스냅샷·히스토리·로그에 넣지 않고 캐시하지 않는다.
"""

import base64
import json
import math
import os
import re

import psutil

DIRS = ("~/.config/orca", "~/Library/Application Support/orca") + \
    ((os.path.join(os.environ["APPDATA"], "orca"),) if os.environ.get("APPDATA") else ())  # Linux·macOS·Windows
# ai-setup 이 서버(위 폴더)와 따로 둔 데스크톱 클라이언트 프로필. 사용자 세션의 앱이라 구독 사용량을 읽는다
DESKTOP_DIRS = ("~/.config/orca-desktop", "~/Library/Application Support/orca-desktop") + \
    ((os.path.join(os.environ["APPDATA"], "orca-desktop"),) if os.environ.get("APPDATA") else ())
# Orca SSH 호스트 연결: 다른 기기의 Orca 데스크톱이 SSH 로 이 기기에 orcad 를 배포해 띄운 런타임의 프로필.
# 실행 파일은 ~/.orca-remote 에 있고 정식 서버(DIRS, orca-serve)와 따로 돈다. 출처: pi5-server2·thsvkd-kgolf 관찰(2026-11-04, Orca 1.4.247)
SSH_DIRS = ("~/.orca",)
REMOTE = "~/.orca-remote"  # SSH 호스트 연결이 orcad 실행 파일(orcad-<판>/orcad.js)을 두는 곳


def _read(d, name):
    try:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pair_url(endpoint, key, dev, scope):
    """Orca encodePairingOffer 와 같은 v2 코드: orca://pair?code=base64url(JSON), 패딩 없음."""
    body = {"v": 2, "endpoint": endpoint, "deviceToken": dev["token"], "publicKeyB64": key,
            "pairedDeviceId": dev["deviceId"], "scope": scope}
    code = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return "orca://pair?code=" + code


def _ts(v):
    """Orca 의 ms 타임스탬프. 숫자가 아니거나(bool·NaN·inf 포함) 이상하면 0."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0


def find_dir(dirs=DIRS):
    """이 기기 Orca 데이터 폴더(orca-runtime.json 이 있는 곳) 또는 None."""
    return next((p for p in map(os.path.expanduser, dirs) if os.path.exists(os.path.join(p, "orca-runtime.json"))), None)


def devices(d):
    """orca-devices.json 의 기기 목록(형식이 이상한 항목은 뺀다)."""
    v = _read(d, "orca-devices.json")
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def runtime_pid(d):
    """폴더 d 의 orca-runtime.json 에 적힌 pid 와 그 프로세스가 살아 있는지. (pid 또는 None, bool)."""
    rt = _read(d, "orca-runtime.json")
    try:
        p = int(rt.get("pid"))
        return p, psutil.pid_exists(p)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None, False


def is_orcad(pid):
    """pid 가 살아 있고 명령행에 ~/.orca-remote 아래의 orcad.js 가 있는지(SSH 호스트 연결이 배포한 orcad 인지).
    Orca 는 orcad 가 죽어도 런타임 파일을 남기므로, 재사용된 pid 를 orcad 로 보지 않으려고 명령행까지 본다."""
    root = os.path.join(os.path.realpath(os.path.expanduser(REMOTE)), "")
    try:
        cmd = psutil.Process(int(pid)).cmdline()  # int(None) 은 TypeError: Process(None) 은 자기 자신이다
    except (psutil.Error, TypeError, ValueError, OverflowError):
        return False
    return any(os.path.basename(a) == "orcad.js" and os.path.realpath(a).startswith(root) for a in cmd)


def ssh_host(dirs=SSH_DIRS):
    """이 기기에 Orca SSH 호스트 연결이 배포한 orcad 가 있으면 {dir(~ 경로), pid, running, clients(그 orcad 에 붙은 기기 이름)}.
    토큰은 읽어도 내보내지 않는다."""
    raw = next((p for p in dirs if os.path.exists(os.path.join(os.path.expanduser(p), "orca-runtime.json"))), None)
    if raw is None:
        return None
    d = os.path.expanduser(raw)
    p = runtime_pid(d)[0]
    return {"dir": raw, "pid": p, "running": is_orcad(p),
            "clients": [str(x.get("name") or x.get("deviceId") or "?") for x in devices(d) if x.get("scope") == "runtime"]}


def environments(dirs=DESKTOP_DIRS + DIRS):
    """이 기기 Orca 데스크톱이 아는 환경 [{name, via}]. via: ssh(SSH 호스트 연결, 원격에 orcad 배포)·ssh-tunnel(정식 페어링을
    SSH 터널로)·pairing(정식 페어링). 사용자 앱이 쓰는 데스크톱 프로필을 먼저 본다. 토큰·키는 읽어 내지 않는다."""
    d = next((p for p in map(os.path.expanduser, dirs) if os.path.exists(os.path.join(p, "orca-environments.json"))), None)
    envs = (_read(d, "orca-environments.json") or {}) if d else {}
    envs = envs.get("environments") if isinstance(envs, dict) else None
    if not isinstance(envs, list):
        return []
    side = _read(d, "orca-environment-sidecar.json")
    side = side.get("entries") if isinstance(side, dict) else None
    side = side if isinstance(side, dict) else {}
    out = []
    for e in envs:
        if not isinstance(e, dict):
            continue
        s = side.get(e.get("id")) if isinstance(e.get("id"), str) else None
        s = s if isinstance(s, dict) else {}
        via = "ssh" if s.get("orcadDeployment") or e.get("connectionDependency") == "ssh-tunnel" else \
            "ssh-tunnel" if s.get("sshAccess") else "pairing"
        out.append({"name": str(e.get("name") or e.get("id") or "?"), "via": via})
    return out


def status(address, dirs=DIRS, ssh_dirs=SSH_DIRS, env_dirs=DESKTOP_DIRS + DIRS):
    """address: 코드에 넣을 이 기기 주소(tailnet IPv4). 런타임 파일의 ws://0.0.0.0:<port> 에서는 포트만 쓴다.
    ssh_host·environments 는 정식 서버가 없어도 낸다."""
    none = {"desktop": None, "mobile": None}
    extra = {"ssh_host": ssh_host(ssh_dirs), "environments": environments(env_dirs)}
    d = find_dir(dirs)
    if not d:
        return {"installed": False, "running": False, "endpoint": None, "codes": none, **extra}
    rt = _read(d, "orca-runtime.json")
    rt = rt if isinstance(rt, dict) else {}
    ws = next((t.get("endpoint") for t in rt.get("transports") or [] if t.get("kind") == "websocket"), None)
    running = runtime_pid(d)[1]
    endpoint = None
    if isinstance(ws, str) and re.match(r"^\w+://[^/]*:\d+/?$", ws) and address:
        endpoint = f"{ws.split('://')[0]}://{address}:{ws.rsplit(':', 1)[1].strip('/')}"
    key = (_read(d, "orca-e2ee-keypair.json") or {}).get("publicKeyB64")
    devs = devices(d)
    codes = dict(none)
    for name, scope in (("desktop", "runtime"), ("mobile", "mobile")):
        cands = [x for x in devs if x.get("scope") == scope and x.get("token") and x.get("deviceId")]
        dev = next((x for x in cands if x.get("lastSeenAt") == 0), None) or \
            max(cands, key=lambda x: _ts(x.get("pairedAt")), default=None)
        if dev and endpoint and key:
            codes[name] = {"url": pair_url(endpoint, key, dev, scope), "device_id": dev["deviceId"],
                           "pending": dev.get("lastSeenAt") == 0, "name": dev.get("name"),
                           "paired_at": _ts(dev.get("pairedAt")) or None, "last_seen_at": _ts(dev.get("lastSeenAt")) or None}
    return {"installed": True, "running": running, "endpoint": endpoint, "codes": codes, **extra}
