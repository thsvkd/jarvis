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

from . import antigravity
from .common import UNNAMED, account, email, fetch_json, private_json, valid_token
from ..util import parse_iso


def _http(token, project):
    req = urllib.request.Request("https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
                                 data=json.dumps({"project": project}).encode(), headers={"Authorization": "Bearer " + token,
                                 "Content-Type": "application/json", "Accept": "application/json"})
    return fetch_json(req)


def read():
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        if not isinstance(project, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{1,199}", project):
            return unavailable
        home = os.environ.get("GEMINI_HOME") or os.path.expanduser("~/.gemini")
        auth = private_json(os.path.join(home, "oauth_creds.json"))
        token = auth.get("access_token") if isinstance(auth, dict) else None
        expiry = auth.get("expiry_date") if isinstance(auth, dict) else None
        if not valid_token(token):
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
            info = antigravity._http("https://openidconnect.googleapis.com/v1/userinfo", token)
            label = email(info) if isinstance(info, dict) and info.get("email_verified") is True else UNNAMED
        except (OSError, ValueError, TypeError):
            label = UNNAMED
        return {"status": "ok", "accounts": [account("gemini", buckets=buckets[:12], label="기본 계정" if label == UNNAMED else label)]}
    except (OSError, ValueError, TypeError):
        return unavailable
