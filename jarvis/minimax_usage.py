"""MiniMax CLI 의 유효한 Token Plan 자격으로 사용량을 읽는다.

https://github.com/MiniMax-AI/cli/blob/main/src/client/endpoints.ts
https://github.com/MiniMax-AI/cli/blob/main/src/utils/quota.ts
"""
import json
import math
import os
import time
import urllib.request

from . import opencode_usage
from .util import parse_iso


def _credential():
    home = os.environ.get("MMX_CONFIG_DIR") or os.path.expanduser("~/.mmx")
    cfg = opencode_usage._private_json(os.path.join(home, "config.json"))
    if not isinstance(cfg, dict):
        raise ValueError("invalid configuration")
    oauth = cfg.get("oauth")
    if isinstance(oauth, dict):
        expiry = parse_iso(oauth.get("expires_at")) if isinstance(oauth.get("expires_at"), str) else None
        if not expiry or expiry <= time.time():
            raise ValueError("expired credential")
        token = oauth.get("access_token")
        resource = oauth.get("resource_url")
    else:
        token = cfg.get("api_key")
        if not isinstance(token, str) or not token.startswith("sk-cp-"):
            raise ValueError("not a token plan credential")
        resource = None
    region = os.environ.get("MINIMAX_REGION") or cfg.get("region") or "global"
    if region not in ("global", "cn"):
        raise ValueError("unsupported region")
    base = os.environ.get("MINIMAX_BASE_URL") or cfg.get("base_url") or resource or ("https://api.minimax.io" if region == "global" else "https://api.minimax.cn")
    if not isinstance(base, str) or base.rstrip("/") not in ("https://api.minimax.io", "https://api.minimax.cn"):
        raise ValueError("unsupported endpoint")
    if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in "\r\n"):
        raise ValueError("invalid credential")
    return base.rstrip("/"), token


def _http(base, token):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    if base not in ("https://api.minimax.io", "https://api.minimax.cn"):
        raise ValueError("unsupported endpoint")
    req = urllib.request.Request(base + "/v1/token_plan/remains", headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(data)


def _windows(body):
    if not isinstance(body, dict) or not isinstance(body.get("base_resp"), dict) or body["base_resp"].get("status_code") != 0:
        return []
    rows = body.get("model_remains")
    selected = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("model_name"), str) or not row["model_name"].startswith("MiniMax-M"):
            continue
        for field, key, start, end, duration in (("current_interval_remaining_percent", "session", "start_time", "end_time", 18000000),
                                                  ("current_weekly_remaining_percent", "weekly", "weekly_start_time", "weekly_end_time", 604800000)):
            remaining, a, b = row.get(field), row.get(start), row.get(end)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (remaining, a, b)):
                continue
            if not 0 <= remaining <= 100 or abs((b - a) - duration) > 60000:
                continue
            used = round(100 - remaining)
            if key not in selected or used > selected[key]["used"]:
                selected[key] = {"key": key, "used": used, "resets_at": b / 1000 if b > 0 else None}
    # usage_count 는 서버 버전에 따라 사용량 또는 잔량이므로 명시된 잔량 비율이 없으면 추측하지 않는다.
    return [selected[k] for k in ("session", "weekly") if k in selected]


def read():
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        windows = _windows(_http(*_credential()))
        if not windows:
            return unavailable
        return {"status": "ok", "accounts": [{"label": "기본 계정", "org": None, "active": True, "status": "ok",
                "error": None, "note": None, "attention": False, "failure": None, "plan": None,
                "updated_at": time.time(), "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "minimax"}]}
    except (OSError, ValueError, TypeError):
        return unavailable
