"""이 기기 caam(coding_agent_account_manager)에 저장한 Codex 계정별 사용량. orca_usage 가 Codex 계정 목록에 합친다.

`caam ls codex --json`(프로필 → 이메일·상태)과 `caam limits codex --format json`(한도)만 부른다.
limits 는 caam 금고(vault)의 접근 토큰으로 ChatGPT 사용량 API 를 읽기만 하고 토큰을 갱신하지 않는다(v0.1.24 cmd/caam/cmd/limits.go).
caam refresh·daemon·activate 는 부르지 않는다: Codex 갱신 토큰은 쓸 때마다 바뀌어 같은 로그인을 두 곳이 갱신하면 계정이 막힐 수 있다.
계정은 이메일만 내보낸다(계정 ID·토큰·경로는 넣지 않는다).

공식 계약: https://github.com/Dicklesworthstone/coding_agent_account_manager (internal/usage/usage.go 의 UsageInfo)
라이선스(MIT + OpenAI/Anthropic 추가 조항)는 사용자가 확인하고 도입을 정했다(2026-10-28).
"""
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

from . import config, orca_update
from .util import Installer, parse_iso, scrub_home, which_local

CACHE_S = 120  # limits 는 부를 때마다 프로필마다 사용량 API 를 부른다
LS_TIMEOUT_S = 10
TIMEOUT_S = 35  # limits: caam 의 프로필당 HTTP 제한(30초)보다 길게. 한 프로필이 느려도 나머지를 잃지 않게
BUDGET_S = 50  # 잠금 대기 + ls + limits. orca_usage 가 core 의 70초 안에서 기다린다
VERSION = "0.1.24"
# 릴리스 자산의 SHA256 을 코드에 고정한다(같은 릴리스의 SHA256SUMS 만 믿지 않게). 올리기는 사람이 검증한 뒤 여기서
ASSETS = {
    ("linux", "arm64"): ("caam_0.1.24_linux_arm64.tar.gz", "ce8bdede52d7e2cfaf994adeed0cf4635026d9d00a28893cfb41a8f7834f2721"),
    ("linux", "amd64"): ("caam_0.1.24_linux_amd64.tar.gz", "25fa307058fd1db60cc6add04b690141b3aba05b381582176d00954e6e355fcf"),
    ("darwin", "arm64"): ("caam_0.1.24_darwin_arm64.tar.gz", "3e1af2b2eafb2219a401a005d2d104e1c012b6b4863cedc9c2f9d0f0315f2d02"),
    ("darwin", "amd64"): ("caam_0.1.24_darwin_amd64.tar.gz", "9b313621a58595f6fc615dedb7e3abd6d047921da943e399dd707c2ac597f94c"),
    ("windows", "amd64"): ("caam_0.1.24_windows_amd64.exe", "457c26406be704309f0b1baa70e7a721b178c416db139d1450eccfce7170c42d"),
}
URL = "https://github.com/Dicklesworthstone/coding_agent_account_manager/releases/download/v{v}/{name}"
_lock = threading.Lock()
_cache = None  # (monotonic, 결과)


def binary():
    return which_local("caam")


def asset():
    os_name = {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform)
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine().lower())
    return ASSETS.get((os_name, arch))


def _install():
    """고정 버전 릴리스를 받아 SHA256 을 확인하고 ~/.local/bin/caam(.exe) 로 둔다."""
    a = asset()
    if not a:
        raise RuntimeError(f"caam 릴리스가 없는 플랫폼입니다({sys.platform}/{platform.machine()})")
    name, digest = a
    # GitHub·릴리스 저장소로만 리다이렉트를 따른다(orca_update 와 같은 opener). 무결성은 고정 SHA256 이 맡는다
    req = urllib.request.Request(URL.format(v=VERSION, name=name), headers={"User-Agent": "jarvis-caam-install"})
    with orca_update._open(req, timeout=60) as r:
        data = r.read(64 << 20)
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError("caam 릴리스의 SHA256 이 고정한 값과 다릅니다")
    if name.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            try:
                data = t.extractfile(t.getmember("caam")).read()  # 이름이 정확히 caam 인 파일 하나만 꺼낸다
            except KeyError:
                raise RuntimeError("caam 릴리스 묶음에 caam 실행 파일이 없습니다")
    dest = os.path.join(os.path.expanduser("~"), ".local", "bin", "caam.exe" if sys.platform == "win32" else "caam")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".caam-", dir=os.path.dirname(dest))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # 이름을 바꾼 뒤 전원이 꺼져도 빈 파일이 남지 않게(남으면 다시 설치하지 않는다)
        os.chmod(tmp, 0o755)
        for i in range(10):  # Windows 는 실시간 검사가 새 exe 를 잠시 잡고 있을 수 있다
            try:
                os.replace(tmp, dest)
                break
            except PermissionError:
                if sys.platform != "win32" or i == 9:
                    raise
                time.sleep(0.5)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


installer = Installer("caam", _install)


def _codex_cli():
    """Codex CLI 는 npm·Homebrew 로도 깔린다: 서비스의 짧은 PATH 에 없을 수 있어 흔한 위치도 본다."""
    return which_local("codex") or next((p for p in ("/opt/homebrew/bin/codex", "/usr/local/bin/codex")
                                         if os.path.isfile(p)), None)


def ensure():
    """caam 이 없으면 뒤에서 설치한다. Codex CLI 가 없는 기기는 설치하지 않는다. None 또는 화면에 붙일 문구."""
    if not config.CAAM_INSTALL or not asset() or not _codex_cli():  # 릴리스가 없는 플랫폼은 조용히 건너뛴다
        return None
    return installer.ensure("Codex 계정별 사용량")


def _key(seconds):
    m = (seconds or 0) / 60
    return "session" if 0 < m <= 360 else "monthly" if m >= 40000 else "weekly"


def _windows(u):
    out = []
    for i, k in enumerate(("primary_window", "secondary_window", "tertiary_window")):
        w = u.get(k)
        if not isinstance(w, dict) or w.get("unmeasured") is True:
            continue
        used = w.get("used_percent")
        if not isinstance(used, (int, float)) or isinstance(used, bool) or used != used:
            continue
        dur = w.get("window_duration")  # Go time.Duration: 나노초
        key = _key(dur / 1e9) if isinstance(dur, (int, float)) and dur > 0 else ("session", "weekly", "monthly")[i]
        at = parse_iso(w.get("resets_at") or "")
        out.append({"key": key, "used": max(0, min(100, round(used))), "resets_at": at if at and at > 0 else None})
    return out


def account(row, ident):
    """limits 한 줄(+ ls 의 그 프로필) → 화면 계정."""
    u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    health = ident.get("health") if isinstance(ident.get("health"), dict) else {}
    email = str((ident.get("identity") or {}).get("email") or "")[:200]
    profile = str(row.get("profile_name") or "")[:60]
    windows = _windows(u)
    err = scrub_home(str(u.get("error") or ""))[:200]
    login = health.get("login_required") is True or err.startswith("unauthorized")
    if login:
        err = "caam 에 저장한 로그인이 만료되었거나 거부되었습니다. 그 계정을 caam 으로 다시 로그인하세요."
    c = u.get("credits") if isinstance(u.get("credits"), dict) else None
    plan = " · ".join(x for x in (str(u.get("plan_type") or "")[:40], profile if profile and profile != email else "") if x)
    return {"label": email or profile or "이름 없는 계정", "org": None, "active": ident.get("active") is True,
            "status": "ok" if windows else "error" if err else "idle",
            "error": None if windows else (err or None), "note": err if windows and err else None, "attention": login,
            "failure": None, "plan": plan or None, "updated_at": parse_iso(u.get("fetched_at") or ""),
            "windows": windows, "buckets": [],
            "extra": {"unit": "credits", "balance": c.get("balance"), "unlimited": c.get("unlimited") is True,
                      "enabled": c.get("has_credits") is True, "reason": None} if c else None,
            "credits": None, "source": "caam"}


def _json(exe, *args, timeout=TIMEOUT_S):
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"caam 이 {timeout}초 안에 답하지 않았습니다")
    except OSError as e:
        raise RuntimeError(f"caam 을 실행하지 못했습니다({type(e).__name__})")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"caam {args[0]} 응답을 읽지 못했습니다(종료 코드 {p.returncode})")


def _run(exe):
    ls = _json(exe, "ls", "codex", "--json", timeout=LS_TIMEOUT_S)
    vault = {x.get("name"): x for x in (ls.get("profiles") if isinstance(ls, dict) else None) or []
             if isinstance(x, dict) and x.get("tool") == "codex" and x.get("type") == "vault"}
    if not vault:  # 저장한 Codex 계정이 없으면 사용량 API 를 부르지 않는다
        return []
    rows = _json(exe, "limits", "codex", "--format", "json")
    rows = rows if isinstance(rows, list) else []
    return [account(r, vault[r["profile_name"]]) for r in rows
            if isinstance(r, dict) and r.get("provider") == "codex" and r.get("profile_name") in vault]


def read(force=False):
    """None(caam 없음·Codex 계정 없음) 또는 {"accounts": [...], "fetched_at": 초} 또는 {"error": "..."}."""
    global _cache
    exe = binary()
    if not exe:
        msg = ensure()
        return {"error": msg} if msg else None
    asked = time.monotonic()
    if not _lock.acquire(timeout=max(1, BUDGET_S - LS_TIMEOUT_S - TIMEOUT_S)):
        return _cache[1] if _cache else {"error": "caam 이 응답하지 않습니다"}
    try:
        if _cache and (_cache[0] >= asked or (not force and asked - _cache[0] < CACHE_S)):
            return _cache[1]
        try:
            accounts = _run(exe)
            out = {"accounts": accounts, "fetched_at": time.time()} if accounts else None
        except Exception as e:
            out = {"error": scrub_home(str(e))[:200] or type(e).__name__}
        _cache = (time.monotonic(), out)
        return out
    finally:
        _lock.release()
