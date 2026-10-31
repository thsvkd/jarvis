"""현재 Codex 로그인 사용량을 로그인 갱신 없이 읽는다.

https://github.com/Dicklesworthstone/coding_agent_account_manager/blob/v0.1.24/internal/usage/codex.go
"""
import json
import math
import os
import time
import urllib.request

from . import caam_usage, opencode_usage


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


def read():
    """현재 자격이 없거나 만료되면 빈 목록. 갱신 토큰은 사용하지 않는다."""
    try:
        home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        body = opencode_usage._private_json(os.path.join(home, "auth.json"))
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
        return [{"label": "기본 계정", "org": None, "active": True, "status": "ok", "error": None,
                 "note": None, "attention": False, "failure": None, "plan": None, "updated_at": time.time(),
                 "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "codex"}]
    except (OSError, ValueError, TypeError):
        return []
