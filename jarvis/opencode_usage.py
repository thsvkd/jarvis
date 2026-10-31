"""OpenCode 의 자격으로 Go 사용량을 읽는다.

https://github.com/anomalyco/opencode/blob/v2.2.0/packages/protocol/src/groups/usage.ts
https://github.com/anomalyco/opencode/blob/v2.2.0/packages/cli/src/commands/handlers/service/status.ts
"""
import ipaddress
import base64
import contextlib
import json
import math
import os
import sqlite3
import stat
import subprocess
import time
import urllib.request
import urllib.parse
from .util import parse_iso


def _local(value):
    try:
        u = urllib.parse.urlsplit(value.strip())
        return (u.scheme == "http" and ipaddress.ip_address(u.hostname).is_loopback and u.port is not None
                and u.username is None and u.password is None and u.path in ("", "/") and not u.query and not u.fragment)
    except (ValueError, TypeError):
        return False


def _run(args, timeout):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _private_json(path):
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("invalid registration")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > 65536 or (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("invalid registration")
        if os.name != "nt" and (st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise ValueError("unsafe registration")
        return json.loads(f.read(65537))


def _registration(endpoint):
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    body = _private_json(os.path.join(state, "opencode", "service.json"))
    if not isinstance(body, dict) or not isinstance(body.get("url"), str) or body["url"].rstrip("/") != endpoint.rstrip("/"):
        raise ValueError("registration changed")
    if not isinstance(body.get("pid"), int) or isinstance(body["pid"], bool) or body["pid"] <= 0:
        raise ValueError("invalid service")
    password = body.get("password")
    if password is not None and (not isinstance(password, str) or len(password) > 1024):
        raise ValueError("invalid password")
    return password


def _legacy_key():
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    body = _private_json(os.path.join(data, "opencode", "auth.json"))
    entry = body.get("opencode-go") if isinstance(body, dict) else None
    key = entry.get("key") if isinstance(entry, dict) and entry.get("type") == "api" else None
    if not isinstance(key, str) or not key or len(key) > 8192 or any(c in key for c in "\r\n"):
        raise ValueError("invalid credential")
    return key


def _v2_credential():
    """선택된 Console 계정을 읽기 전용으로 읽는다. 갱신·전환은 하지 않는다."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(data, "opencode", os.environ.get("OPENCODE_DB") or "opencode.db")
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or (os.name != "nt" and (st.st_uid != os.getuid() or st.st_mode & 0o077)):
        raise ValueError("unsafe database")
    uri = "file:" + urllib.parse.quote(os.path.abspath(path), safe="/:") + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        row = db.execute("SELECT value FROM credential WHERE integration_id = ? ORDER BY active DESC, time_created DESC, id DESC LIMIT 1", ("opencode",)).fetchone()
        go_key = row is None
        if go_key:
            row = db.execute("SELECT value FROM credential WHERE integration_id = ? ORDER BY active DESC, time_created DESC, id DESC LIMIT 1", ("opencode-go",)).fetchone()
    value = json.loads(row[0]) if row else None
    metadata = value.get("metadata") if isinstance(value, dict) else None
    if go_key:
        if not isinstance(value, dict) or value.get("type") != "key":
            raise ValueError("unsupported credential")
        org = None
    else:
        if not isinstance(metadata, dict) or metadata.get("server", "https://opencode.ai/console") not in ("https://opencode.ai/console", "https://console.opencode.ai"):
            raise ValueError("unsupported console")
        org = metadata.get("orgID")
        if not isinstance(org, str) or not org or len(org) > 1024 or any(c in org for c in "\r\n"):
            raise ValueError("invalid workspace")
    if value.get("type") == "oauth":
        expiry = value.get("expires")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time() * 1000:
            raise ValueError("expired credential")
        key = value.get("access")
    elif value.get("type") == "key":
        key = value.get("key")
    else:
        raise ValueError("unsupported credential")
    if not isinstance(key, str) or not key or len(key) > 8192 or any(c in key for c in "\r\n"):
        raise ValueError("invalid credential")
    return key, org


def _go_api(key, org=None):
    """OpenCode Go 의 고정 HTTPS 엔드포인트만 부른다. 리다이렉트·프록시에는 키를 넘기지 않는다.

    https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/go/v1/usage.ts
    """
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    headers = {"Accept": "application/json", "Authorization": "Bearer " + key}
    if org is not None:
        headers["x-opencode-org-id"] = org
    req = urllib.request.Request("https://opencode.ai/zen/go/v1/usage", headers=headers)
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("response too large")
    return json.loads(raw)


def _request(endpoint, password):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    headers = {"Accept": "application/json"}
    if password is not None:
        token = base64.b64encode(("opencode:" + password).encode()).decode("ascii")
        headers["Authorization"] = "Basic " + token
    req = urllib.request.Request(endpoint.rstrip("/") + "/api/experimental/usage/limits", headers=headers)
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=20) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("response too large")
    return json.loads(raw)


def read(exe, use_service=False):
    """기본적으로 저장된 접근 자격만 읽고 원문 오류를 내보내지 않는다."""
    unavailable = {"status": "unavailable", "accounts": []}
    try:
        # 독립 경로는 서비스 탐색이나 OAuth resolve 를 거치지 않는다.
        endpoint = None
        legacy = True
        if use_service:
            status = _run([exe, "service", "status"], 6)
            endpoint = status.stdout.strip()
            legacy = bool(status.returncode) or endpoint == "stopped"
        if legacy:
            version = _run([exe, "--version"], 6)
            version_text = version.stdout.strip().removeprefix("opencode ").removeprefix("v")
            if version.returncode:
                return unavailable
            if version_text.startswith("1."):
                body = _go_api(_legacy_key())
            elif version_text.startswith("2."):
                key, org = _v2_credential()
                body = _go_api(key, org)
            else:
                return unavailable
            go = body.get("usage") if isinstance(body, dict) else None
        else:
            if not _local(endpoint):
                return unavailable
            password = _registration(endpoint)
            body = _request(endpoint, password)
            data = body.get("data") if isinstance(body, dict) else None
            go = data.get("go") if isinstance(data, dict) else None
        if not isinstance(go, dict):
            return unavailable
        windows, limited = [], False
        for field, key in (("rolling", "session"), ("weekly", "weekly"), ("monthly", "monthly")):
            w = go.get(field)
            if not isinstance(w, dict):
                continue
            value, reset = w.get("percent"), w.get("resetsAt")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 100:
                continue
            if legacy:
                reset = parse_iso(reset) if isinstance(reset, str) else None
            else:
                reset = reset / 1000 if isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) and reset > 0 else None
            windows.append({"key": key, "used": round(value), "resets_at": reset})
            limited = limited or w.get("limited") is True or w.get("status") == "rate-limited"
        if not windows:
            return unavailable
        account = {"label": "기본 계정", "org": None, "active": True, "status": "ok", "error": None,
                   "note": "프로바이더가 요청을 제한하고 있습니다." if limited else None, "attention": limited,
                   "failure": None, "plan": None, "updated_at": time.time(), "windows": windows, "buckets": [],
                   "extra": None, "credits": None, "source": "opencode"}
        return {"status": "ok", "accounts": [account]}
    except (OSError, sqlite3.Error, subprocess.TimeoutExpired, ValueError, TypeError):
        return unavailable
