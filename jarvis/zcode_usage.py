"""Z.ai Coding Plan 사용량을 프로바이더에 직접 묻는다.

https://github.com/zai-org/zai-coding-plugins/blob/main/plugins/glm-plan-usage/skills/usage-query-skill/scripts/query-usage.mjs
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request


def _credentials():
    token = os.environ.get("ZAI_API_KEY")
    base = "https://api.z.ai"
    if not token:
        token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        url = urllib.parse.urlsplit(os.environ.get("ANTHROPIC_BASE_URL", ""))
        if url.scheme != "https" or url.hostname not in ("api.z.ai", "open.bigmodel.cn") or url.port not in (None, 443) or url.username or url.password:
            return None
        base = "https://" + url.hostname
    if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in "\r\n"):
        return None
    return base, token


def _http(base, token):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    if base not in ("https://api.z.ai", "https://open.bigmodel.cn"):
        raise ValueError("unsupported endpoint")
    auth = token if token.lower().startswith("bearer ") else "Bearer " + token
    req = urllib.request.Request(base + "/api/monitor/usage/quota/limit", headers={"Authorization": auth, "Accept": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(data)


def _limits(body):
    data = body.get("data") if isinstance(body, dict) else None
    limits = data.get("limits") if isinstance(data, dict) else None
    windows, buckets = [], []
    for row in limits if isinstance(limits, list) else []:
        if not isinstance(row, dict):
            continue
        used = row.get("percentage")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        reset = row.get("nextResetTime")
        at = reset / 1000 if isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) and reset > 0 else None
        if row.get("type") == "TOKENS_LIMIT":
            windows.append({"key": "session", "used": round(used), "resets_at": at})
        elif row.get("type") == "CREDIT_LIMIT" and (row.get("unit"), row.get("number")) in ((3, 5), (6, 1)):
            windows.append({"key": "session" if row["unit"] == 3 else "weekly", "used": round(used), "resets_at": at})
        elif row.get("type") == "TIME_LIMIT":
            buckets.append({"name": "MCP 월간", "used": round(used), "resets_at": at})
    return windows, buckets[:12]


def read():
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        creds = _credentials()
        if not creds:
            return unavailable
        windows, buckets = _limits(_http(*creds))
        if not windows and not buckets:
            return unavailable
        return {"status": "ok", "accounts": [{"label": "기본 계정", "org": None, "active": True, "status": "ok",
                "error": None, "note": None, "attention": False, "failure": None, "plan": None,
                "updated_at": time.time(), "windows": windows, "buckets": buckets, "extra": None, "credits": None, "source": "zcode"}]}
    except (OSError, ValueError, TypeError):
        return unavailable
