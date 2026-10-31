"""Gemini Code Assist 의 기존 자격으로 모델별 한도를 읽는다.

https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/server.ts
https://developers.google.com/identity/openid-connect/reference
"""
import json
import math
import os
import re
import time
import urllib.request

from . import antigravity_usage, opencode_usage
from .provider_usage import _email
from .util import parse_iso


def _http(token, project):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    req = urllib.request.Request("https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
                                 data=json.dumps({"project": project}).encode(), headers={"Authorization": "Bearer " + token,
                                 "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(raw)


def read():
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{1,199}", project):
            return unavailable
        home = os.environ.get("GEMINI_HOME") or os.path.expanduser("~/.gemini")
        auth = opencode_usage._private_json(os.path.join(home, "oauth_creds.json"))
        token = auth.get("access_token") if isinstance(auth, dict) else None
        expiry = auth.get("expiry_date") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in "\r\n"):
            return unavailable
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time() * 1000:
            return unavailable
        body = _http(token, project)
        raw = body.get("buckets") if isinstance(body, dict) else None
        buckets = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            name, fraction = row.get("modelId"), row.get("remainingFraction")
            if not isinstance(name, str) or not name or isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
                continue
            reset = row.get("resetTime")
            buckets.append({"name": name[:80], "used": round((1 - fraction) * 100), "resets_at": parse_iso(reset) if isinstance(reset, str) else None})
        if not buckets:
            return unavailable
        try:
            info = antigravity_usage._http("https://openidconnect.googleapis.com/v1/userinfo", token)
            label = _email(info) if isinstance(info, dict) and info.get("email_verified") is True else "이름 없는 계정"
        except (OSError, ValueError, TypeError):
            label = "이름 없는 계정"
        return {"status": "ok", "accounts": [{"label": "기본 계정" if label == "이름 없는 계정" else label,
                "org": None, "active": True, "status": "ok", "error": None, "note": None, "attention": False,
                "failure": None, "plan": None, "updated_at": time.time(), "windows": [], "buckets": buckets[:12],
                "extra": None, "credits": None, "source": "gemini"}]}
    except (OSError, ValueError, TypeError):
        return unavailable
