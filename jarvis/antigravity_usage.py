"""Antigravity 사용량과 현재 계정을 로그인 변경 없이 읽는다.

https://github.com/google-antigravity/antigravity-cli/blob/main/CHANGELOG.md
https://github.com/stablyai/orca/blob/v1.4.245/src/main/rate-limits/antigravity-usage-response.ts
https://developers.google.com/identity/openid-connect/reference
"""
import json
import math
import os
import stat
import time
import urllib.request

from .util import parse_iso
from .provider_usage import _email


def _http(url, token, post=False):
    """Google 고정 주소에만 토큰을 전송하며 프록시와 리다이렉트는 따르지 않는다."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    allowed = ("https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
               "https://openidconnect.googleapis.com/v1/userinfo")
    if url not in allowed:
        raise ValueError("unsupported endpoint")
    req = urllib.request.Request(url, data=b"{}" if post else None,
                                 headers={"Authorization": "Bearer " + token, "User-Agent": "antigravity", "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(data)


def _token():
    root = os.environ.get("GEMINI_HOME") or os.path.expanduser("~/.gemini")
    path = os.path.join(root, "antigravity-cli", "antigravity-oauth-token")
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("invalid credential")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        now = os.fstat(f.fileno())
        if not stat.S_ISREG(now.st_mode) or now.st_size > 65536 or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise ValueError("invalid credential")
        if os.name != "nt" and (now.st_uid != os.getuid() or now.st_mode & 0o077):
            raise ValueError("unsafe credential")
        body = json.load(f)
    entry = body.get("token") if isinstance(body, dict) else None
    token = entry.get("access_token") if isinstance(entry, dict) else None
    expiry = parse_iso(entry.get("expiry")) if isinstance(entry, dict) and isinstance(entry.get("expiry"), str) else None
    if not isinstance(token, str) or not token or len(token) > 8192 or any(c in token for c in "\r\n") or not expiry or expiry <= time.time():
        raise ValueError("expired or invalid credential")
    return token


def _quota(body):
    groups = body.get("groups") if isinstance(body, dict) else None
    if not isinstance(groups, list):
        return None
    buckets, selected = [], {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("displayName"), str):
            continue
        name = group["displayName"][:80]
        for b in group.get("buckets") if isinstance(group.get("buckets"), list) else []:
            if not isinstance(b, dict) or b.get("disabled") is True:
                continue
            fraction = b.get("remainingFraction")
            window = b.get("window")
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
                continue
            used = round((1 - fraction) * 100)
            reset = b.get("resetTime")
            at = parse_iso(reset) if isinstance(reset, str) else None
            key = {"5h": "session", "weekly": "weekly"}.get(window) if isinstance(window, str) else None
            buckets.append({"name": name, "used": used, "resets_at": at})
            if key and (key not in selected or used > selected[key]["used"]):
                selected[key] = {"key": key, "used": used, "resets_at": at}
    return {"windows": [selected[k] for k in ("session", "weekly") if k in selected], "buckets": buckets[:12]} if buckets else None


def read():
    """활성 CLI 자격의 사용량만 조회한다. 만료 시 갱신하지 않는다."""
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        token = _token()
        quota = _quota(_http("https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary", token, True))
        if not quota:
            return unavailable
        try:
            identity = _http("https://openidconnect.googleapis.com/v1/userinfo", token)
            label = _email(identity) if isinstance(identity, dict) and identity.get("email_verified") is True else "이름 없는 계정"
        except (OSError, ValueError, TypeError):
            label = "이름 없는 계정"
        account = {"label": "기본 계정" if label == "이름 없는 계정" else label, "org": None, "active": True,
                   "status": "ok", "error": None, "note": None, "attention": False, "failure": None, "plan": None,
                   "updated_at": time.time(), **quota, "extra": None, "credits": None, "source": "antigravity"}
        return {"status": "ok", "accounts": [account]}
    except (OSError, ValueError, TypeError):
        return unavailable
