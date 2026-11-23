"""현재 Codex 로그인 사용량을 로그인 갱신 없이 읽는다.

https://github.com/Dicklesworthstone/coding_agent_account_manager/blob/v0.1.24/internal/usage/codex.go
"""
import base64
import json
import math
import os
import time
import urllib.request

from . import caam, claude, common

STALL_S = 7200  # 접근 토큰이 만료된 뒤 이만큼 지나도 그대로면 갱신이 멈춘 것으로 본다(USAGE-19)
# shortcut: 공개된 규칙이 아니라 pit5 실측 하나다(auth_time 2026-10-14 13:31, refresh token has expired 2026-11-13 10:26 = 29.9일). 다른 기한이 관측되면 고친다
LOGIN_S = 30 * 86400


def _http(token, account):
    req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers={
        "Authorization": "Bearer " + token, "ChatGPT-Account-Id": account, "Accept": "application/json"})
    return common.fetch_json(req)


def _email(tokens, account):
    """id_token 의 검증된 이메일. 같은 로그인(chatgpt_account_id 가 account_id 와 같음)일 때만 쓴다. 모르면 기본 계정."""
    try:
        part = tokens.get("id_token").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        auth = claims.get("https://api.openai.com/auth") or {}
        if claims.get("email_verified") is True and auth.get("chatgpt_account_id") == account:
            email = common.email(claims)
            return common.DEFAULT if email == common.UNNAMED else email
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return common.DEFAULT


def _home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _claim(token, key):
    """JWT 본문의 시각 값(epoch 초). 없거나 수가 아니면 None. 서명은 보지 않는다(표시용)."""
    part = token.split(".")[1]
    value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0 else None


def login(skipped):
    """USAGE-19: 기기 단위 Codex login. 시각에서 계산한 경과·남은 시간만 내보낸다(토큰·계정 ID·시각 원값은 내보내지 않는다).
    stalled: 로그인 유지가 돌았는데(skipped 가 None 이 아님) 접근 토큰이 STALL_S 넘게 만료된 채다. skipped: 마지막 hswap codex refresh 가 건너뛰었는가(hswap._skipped).
    expiring: id_token 의 auth_time + LOGIN_S 가 LOGIN_WARN_S 안이거나 지났다. 갱신으로는 늘어나지 않는다.
    파일이 없거나(키링 저장 방식 포함) 읽지 못했거나 해당 시각이 없으면 None."""
    try:
        tokens = common.private_json(os.path.join(_home(), "auth.json"))["tokens"]
    except Exception:  # 형식이 어긋난 파일 하나가 사용량 응답을 막지 않게 하고, 메시지(경로·값)는 남기지 않는다
        return None
    try:  # 접근 토큰을 읽지 못하면 멈춤 판정만 건너뛰고 기한 경고는 그대로 본다
        exp = _claim(tokens["access_token"], "exp")
    except Exception:
        exp = None
    try:
        auth = _claim(tokens["id_token"], "auth_time")
    except Exception:
        auth = None
    now = time.time()
    if skipped is not None and exp is not None and now - exp > STALL_S:
        note = (f"접근 토큰이 {claude._span(now - exp)} 전에 만료됐습니다. 다시 로그인하세요. 상주 Codex(데몬·앱)가 있으면 먼저 끝내야 새 로그인을 씁니다."
                + (" hswap 은 실행 중인 Codex 때문에 갱신을 건너뜁니다." if skipped else ""))
        return {"state": "stalled", "text": "Codex 다시 로그인 필요", "note": note, "attention": True, "slot": None, "other": False, "store": None}
    if auth is not None and auth + LOGIN_S - now <= claude.LOGIN_WARN_S:
        left = auth + LOGIN_S - now
        return {"state": "expiring", "text": f"Codex 로그인 만료 임박(약 {claude._span(left)})" if left > 0 else "Codex 로그인 기한이 지났습니다",
                "note": "Codex 로그인은 로그인한 지 약 30일이 지나면 끊기고, 사용해도 늘어나지 않습니다. 다시 로그인하세요.",
                "attention": True, "slot": None, "other": False, "store": None}
    return None


def read():
    """현재 자격이 없거나 만료되면 빈 목록. 갱신 토큰은 사용하지 않는다."""
    try:
        body = common.private_json(os.path.join(_home(), "auth.json"))
        tokens = body.get("tokens") if isinstance(body, dict) else None
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        account = tokens.get("account_id") if isinstance(tokens, dict) else None
        for value in (token, account):
            if not isinstance(value, str) or not value or len(value) > 8192 or any(c in value for c in "\r\n"):
                return []
        response = _http(token, account)
        limits = response.get("rate_limit") if isinstance(response, dict) else None
        if not isinstance(limits, dict):
            return []
        windows = []
        for field in ("primary_window", "secondary_window"):
            w = limits.get(field)
            if not isinstance(w, dict):
                continue
            used, duration, reset = w.get("used_percent"), w.get("limit_window_seconds"), w.get("reset_at")
            if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
                continue
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                continue
            reset = reset if isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) and reset > 0 else None
            windows.append({"key": caam._key(duration), "used": round(used), "resets_at": reset})
        if not windows:
            return []
        return [{"label": _email(tokens, account), "org": None, "active": True, "status": "ok", "error": None,
                 "note": None, "attention": False, "failure": None, "plan": None, "updated_at": time.time(),
                 "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "codex"}]
    except (OSError, ValueError, TypeError):
        return []
