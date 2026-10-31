"""Kimi Code 의 유효한 활성 자격으로 사용량을 읽는다.

https://github.com/MoonshotAI/kimi-code/blob/main/packages/oauth/src/managed-usage.ts
https://github.com/MoonshotAI/kimi-code/blob/main/packages/oauth/src/storage.ts
https://github.com/MoonshotAI/kimi-code/issues/3908
"""
import json
import math
import os
import re
import time
import tomllib
import urllib.request

from . import opencode_usage
from .util import parse_iso


def _token():
    home = os.environ.get("KIMI_CODE_HOME") or os.path.expanduser("~/.kimi-code")
    name = "kimi-code"
    config = os.path.join(home, "config.toml")
    try:
        with open(config, "rb") as f:
            value = tomllib.loads(f.read(65537).decode("utf-8"))
        providers = value.get("providers") if isinstance(value, dict) else None
        provider = providers.get("managed:kimi-code") if isinstance(providers, dict) else None
        base = provider.get("base_url", provider.get("baseUrl")) if isinstance(provider, dict) else None
        if base is not None and (not isinstance(base, str) or base.rstrip("/") not in ("https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/v1")):
            raise ValueError("unsupported endpoint")
        oauth = provider.get("oauth") if isinstance(provider, dict) else None
        if isinstance(oauth, dict):
            if oauth.get("storage") not in (None, "file"):
                raise ValueError("unsupported storage")
            name = oauth.get("key", name)
    except FileNotFoundError:
        pass
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or name in (".", ".."):
        raise ValueError("invalid credential name")
    value = opencode_usage._private_json(os.path.join(home, "credentials", name + ".json"))
    token = value.get("access_token") if isinstance(value, dict) else None
    expiry = value.get("expires_at") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in "\r\n"):
        raise ValueError("invalid credential")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
        raise ValueError("expired credential")
    return token


def _http(token):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    base = os.environ.get("KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1").rstrip("/")
    if base not in ("https://api.kimi.com/coding/v1", "https://api.kimi.ai/coding/v1"):
        raise ValueError("unsupported endpoint")
    req = urllib.request.Request(base + "/usages", headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(data)


def _counter(value):
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("used"), bool) or isinstance(value.get("limit"), bool):
        return None
    try:
        used, limit = float(value.get("used")), float(value.get("limit"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(used) or not math.isfinite(limit) or limit <= 0 or not 0 <= used <= limit:
        return None
    return round(used / limit * 100)


def _windows(body):
    if not isinstance(body, dict):
        return []
    selected = {}
    for item in body.get("limits") if isinstance(body.get("limits"), list) else []:
        window, detail = (item.get("window"), item.get("detail")) if isinstance(item, dict) else (None, None)
        if not isinstance(window, dict) or window.get("duration") != 300 or window.get("timeUnit") != "TIME_UNIT_MINUTE":
            continue
        pct = _counter(detail)
        if pct is not None:
            reset = detail.get("resetTime")
            selected["session"] = {"key": "session", "used": pct, "resets_at": parse_iso(reset) if isinstance(reset, str) else None}
    weekly = body.get("usage")
    pct = _counter(weekly)
    if pct is not None:
        reset = weekly.get("resetTime")
        at = parse_iso(reset) if isinstance(reset, str) else None
        ratios = body.get("usages")
        summary = ratios.get("limit_7d") if isinstance(ratios, dict) else None
        summary_at = parse_iso(summary.get("reset_time")) if isinstance(summary, dict) and isinstance(summary.get("reset_time"), str) else None
        if at is not None and summary_at is not None and abs(at - summary_at) <= 2:
            selected["weekly"] = {"key": "weekly", "used": pct, "resets_at": at}
    ratios = body.get("usages")
    for field, key in (("limit_5h", "session"), ("limit_7d", "weekly"), ("limit_month_total", "monthly")):
        if key in selected or not isinstance(ratios, dict) or not isinstance(ratios.get(field), dict):
            continue
        raw = ratios[field]
        ratio = raw.get("used_ratio")
        # 공급자가 5시간·주간 비율을 0 으로 잘못 반환하는 사례가 있어 반증할 수 없는 0 은 표시하지 않는다.
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or not 0 <= ratio <= 1 or (ratio == 0 and key != "monthly"):
            continue
        reset = raw.get("reset_time")
        selected[key] = {"key": key, "used": round(ratio * 100), "resets_at": parse_iso(reset) if isinstance(reset, str) else None}
    return [selected[k] for k in ("session", "weekly", "monthly") if k in selected]


def read():
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        windows = _windows(_http(_token()))
        if not windows:
            return unavailable
        return {"status": "ok", "accounts": [{"label": "기본 계정", "org": None, "active": True, "status": "ok",
                "error": None, "note": None, "attention": False, "failure": None, "plan": None,
                "updated_at": time.time(), "windows": windows, "buckets": [], "extra": None, "credits": None, "source": "kimi"}]}
    except (OSError, ValueError, TypeError, UnicodeError):
        return unavailable
