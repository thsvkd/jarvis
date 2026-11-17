"""현재 Codex 로그인 사용량을 로그인 갱신 없이 읽는다.

https://github.com/Dicklesworthstone/coding_agent_account_manager/blob/v0.1.24/internal/usage/codex.go
"""
import base64
import json
import math
import os
import time
import urllib.request

from . import caam_usage, claude_accounts, opencode_usage, provider_usage

STALL_S = 7200  # 접근 토큰이 만료된 뒤 이만큼 지나도 그대로면 갱신이 멈춘 것으로 본다(USAGE-19)


def _http(token, account):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers={
        "Authorization": "Bearer " + token, "ChatGPT-Account-Id": account, "Accept": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(data)


def _email(tokens, account):
    """id_token 의 검증된 이메일. 같은 로그인(chatgpt_account_id 가 account_id 와 같음)일 때만 쓴다. 모르면 기본 계정."""
    try:
        part = tokens.get("id_token").split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        auth = claims.get("https://api.openai.com/auth") or {}
        if claims.get("email_verified") is True and auth.get("chatgpt_account_id") == account:
            email = provider_usage._email(claims)
            return "기본 계정" if email == "이름 없는 계정" else email
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return "기본 계정"


def _home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def login(skipped):
    """USAGE-19: 로그인 유지가 돌았는데(skipped 가 None 이 아님) auth.json 의 접근 토큰이 STALL_S 넘게 만료된 채면 기기 단위 login.
    skipped: 마지막 hswap codex refresh 가 건너뛰었는가(hswap_usage._skipped). exp 에서 계산한 경과 시간만 내보낸다(토큰·계정 ID 는 내보내지 않는다).
    파일이 없거나(키링 저장 방식 포함) 읽지 못했거나 exp 가 없으면 None."""
    if skipped is None:
        return None
    try:
        part = opencode_usage._private_json(os.path.join(_home(), "auth.json"))["tokens"]["access_token"].split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"]
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp) or exp <= 0:
            return None
        late = time.time() - exp
    except Exception:  # 형식이 어긋난 파일 하나가 사용량 응답을 막지 않게 하고, 메시지(경로·값)는 남기지 않는다
        return None
    if late <= STALL_S:
        return None
    note = (f"접근 토큰이 {claude_accounts._span(late)} 전에 만료됐습니다. 다시 로그인하세요. 상주 Codex(데몬·앱)가 있으면 먼저 끝내야 새 로그인을 씁니다."
            + (" hswap 은 실행 중인 Codex 때문에 갱신을 건너뜁니다." if skipped else ""))
    return {"state": "stalled", "text": "Codex 다시 로그인 필요", "note": note, "attention": True, "slot": None, "other": False, "store": None}


def read():
    """현재 자격이 없거나 만료되면 빈 목록. 갱신 토큰은 사용하지 않는다."""
    try:
        body = opencode_usage._private_json(os.path.join(_home(), "auth.json"))
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
            windows.append({"key": caam_usage._key(duration), "used": round(used), "resets_at": reset})
        if not windows:
            return []
        return [{"label": _email(tokens, account), "org": None, "active": True, "status": "ok", "error": None,
                 "note": None, "attention": False, "failure": None, "plan": None, "updated_at": time.time(),
                 "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "codex"}]
    except (OSError, ValueError, TypeError):
        return []
