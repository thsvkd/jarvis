"""프로바이더 사용량 수집기들이 함께 쓰는 도구: 자격 파일 읽기·토큰 검사·HTTP·계정 행."""
import json
import os
import re
import stat
import time
import urllib.request

EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
UNNAMED = "이름 없는 계정"


def email(ident):
    ident = ident if isinstance(ident, dict) else {}
    value = ident.get("email")
    return value if isinstance(value, str) and len(value) <= 200 and EMAIL.fullmatch(value) else UNNAMED


def private_json(path):
    """본인만 읽을 수 있는 일반 파일(64KiB 이하)만 JSON 으로 읽는다. 심볼릭 링크와 읽는 사이 바뀐 파일은 거부한다."""
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


def valid_token(value):
    """헤더에 실어도 되는 토큰인가: 비어 있지 않은 8KiB 이하 문자열이고 줄바꿈이 없다."""
    return isinstance(value, str) and bool(value) and len(value) <= 8192 and not any(c in value for c in "\r\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_json(req, timeout=20):
    """프록시·리다이렉트를 따르지 않고(토큰을 다른 곳에 넘기지 않게) 1MB 이하 JSON 응답만 읽는다. 주소 허용 검사는 부르는 쪽이 한다."""
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("oversized response")
    return json.loads(raw)


def account(source, windows=(), buckets=(), label="기본 계정"):
    """한 계정만 있는 수집기의 정상 계정 행."""
    return {"label": label, "org": None, "active": True, "status": "ok", "error": None, "note": None, "attention": False,
            "failure": None, "plan": None, "updated_at": time.time(), "windows": list(windows), "buckets": list(buckets),
            "extra": None, "credits": None, "source": source}
