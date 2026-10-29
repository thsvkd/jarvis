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


def status(address, dirs=DIRS):
    """address: 코드에 넣을 이 기기 주소(tailnet IPv4). 런타임 파일의 ws://0.0.0.0:<port> 에서는 포트만 쓴다."""
    none = {"desktop": None, "mobile": None}
    d = find_dir(dirs)
    if not d:
        return {"installed": False, "running": False, "endpoint": None, "codes": none}
    rt = _read(d, "orca-runtime.json")
    rt = rt if isinstance(rt, dict) else {}
    ws = next((t.get("endpoint") for t in rt.get("transports") or [] if t.get("kind") == "websocket"), None)
    try:
        running = psutil.pid_exists(int(rt.get("pid")))
    except (TypeError, ValueError, OverflowError):
        running = False
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
    return {"installed": True, "running": running, "endpoint": endpoint, "codes": codes}
