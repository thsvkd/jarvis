"""jarvis 자체 갱신: `claude update` 처럼 GitHub 릴리스를 받는다. core 의 버튼과 `python -m jarvis update` 가 부른다.

1. 최신 릴리스의 manifest.json({version, url, sha256})을 읽어 지금 도는 버전과 비교한다.
2. 새 버전이면 소스 묶음을 받아 SHA256 을 확인하고 launch.VERSIONS/<버전> 에 푼다(지금 도는 코드는 건드리지 않는다).
3. launch.CURRENT 를 새 버전으로 바꾸고 HTTP 서버를 끝낸다. 서비스 관리자(systemd Restart=always · launchd KeepAlive ·
   예약 작업 재시작 도우미 · pm2 autorestart)가 저장소의 런처를 다시 띄우고, 런처가 새 버전으로 넘어간다.
   새 버전이 기동하자마자 거듭 죽으면 런처가 이전 버전으로 되돌린다(launch.py).
릴리스는 s2 에서 tools/release.py 로 만든다. 저장소가 public 이라 인증이 필요 없다.
"""

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request

from . import __version__, launch, orca_issue, orca_update, tray

REPO = "thsvkd/jarvis"
MANIFEST = f"https://github.com/{REPO}/releases/latest/download/manifest.json"
MAX_BYTES = 64 << 20
MAX_UNPACKED = 256 << 20
KEEP = 2  # 지금 버전과 되돌아갈 버전
VER_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
_lock = threading.Lock()


def latest():
    """최신 릴리스 manifest. 형식이 다르거나 이 저장소의 릴리스 주소가 아니면 예외."""
    req = urllib.request.Request(MANIFEST, headers={"User-Agent": "jarvis-self-update"})
    with orca_update._open(req, timeout=30) as r:  # GitHub·릴리스 저장소로만 리다이렉트를 따른다
        m = json.loads(r.read(1 << 16))
    v = m.get("version") if isinstance(m, dict) else None
    if not (isinstance(v, str) and VER_RE.match(v) and isinstance(m.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])
            and m.get("url") == f"https://github.com/{REPO}/releases/download/v{v}/jarvis-{v}.tar.gz"):
        raise ValueError("릴리스 manifest.json 형식이 다릅니다")
    return m


def _extract(data, v, dest):
    """jarvis-<v>/ 아래의 보통 파일·폴더만 dest 에 푼다(경로 탈출·링크·장치 파일은 거부한다)."""
    prefix = f"jarvis-{v}/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        members = t.getmembers()
        if sum(m.size for m in members) > MAX_UNPACKED:
            raise ValueError("릴리스 묶음을 풀면 너무 큽니다")
        for m in members:
            rel = m.name[len(prefix):] if m.name.startswith(prefix) else None
            if m.name == prefix.rstrip("/"):
                continue
            # 역슬래시·드라이브 문자는 Windows 에서 경로를 벗어날 수 있다
            if rel is None or rel.startswith("/") or "\\" in rel or ":" in rel or ".." in rel.split("/") \
                    or not (m.isfile() or m.isdir()):
                raise ValueError(f"릴리스 묶음에 이상한 항목이 있습니다: {m.name[:100]}")
            out = os.path.join(dest, *rel.split("/"))
            if m.isdir():
                os.makedirs(out, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with t.extractfile(m) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
    if not os.path.isfile(os.path.join(dest, "jarvis", "__init__.py")):
        raise ValueError("릴리스 묶음에 jarvis 패키지가 없습니다")


def install(m):
    """받아서 검증하고 VERSIONS/<버전> 에 둔다. 이미 있으면 그대로 쓴다."""
    v = m["version"]
    if launch.path(v):
        return
    req = urllib.request.Request(m["url"], headers={"User-Agent": "jarvis-self-update"})
    with orca_update._open(req, timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("릴리스 묶음이 너무 큽니다")
    if hashlib.sha256(data).hexdigest() != m["sha256"]:
        raise ValueError("릴리스 묶음의 SHA256 이 manifest 와 다릅니다")
    os.makedirs(launch.VERSIONS, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".tmp-", dir=launch.VERSIONS)
    try:
        _extract(data, v, tmp)
        shutil.rmtree(os.path.join(launch.VERSIONS, v), ignore_errors=True)  # 지난번에 반쯤 남은 폴더
        for i in range(10):  # Windows 는 실시간 검사가 막 푼 파일을 잠시 잡고 있을 수 있다
            try:
                os.replace(tmp, os.path.join(launch.VERSIONS, v))  # 다 푼 뒤에만 이름을 준다(반쯤 푼 폴더를 띄우지 않게)
                break
            except PermissionError:
                if i == 9:
                    raise
                time.sleep(0.5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prune(keep):
    for name in os.listdir(launch.VERSIONS):
        if name not in keep and not name.startswith(".tmp-"):  # 다른 프로세스가 푸는 중일 수 있다
            shutil.rmtree(os.path.join(launch.VERSIONS, name), ignore_errors=True)


def start(running=__version__):
    """(http 상태, 응답). 응답의 restart 가 참이면 호출자가 restart() 를 부른다."""
    if orca_update.job and orca_update.job.get("state") == "running":
        return 409, {"error": "Orca 업데이트가 끝난 뒤에 갱신하세요"}
    if orca_issue.job and orca_issue.job.get("state") == "running":
        return 409, {"error": "페어링 코드 발급이 끝난 뒤에 갱신하세요"}
    from . import modules_api  # 적용 중에 재시작·갱신하면 설치가 반쯤 남는다
    if modules_api.running():
        return 409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"}
    if not _lock.acquire(blocking=False):
        return 409, {"error": "이미 갱신하고 있습니다"}
    try:
        try:
            m = latest()
            if launch.key(m["version"]) <= launch.key(running):
                return 200, {"running": running, "to": running, "restart": False}
            if m["version"] == launch.read().get("bad"):  # 고친 릴리스(새 버전)가 나올 때까지 같은 실패를 되풀이하지 않는다
                return 409, {"error": f"{m['version']} 는 이 기기에서 기동에 실패해 되돌렸습니다. 다음 릴리스를 기다리세요"}
            install(m)
        except Exception as e:
            return 502, {"error": f"릴리스를 받지 못했습니다: {str(e)[:200] or type(e).__name__}"}
        cur = launch.read()
        # 되돌아갈 곳: 마지막으로 확정된 버전(없으면 저장소). CLI 는 서비스와 다른 버전에서 돌 수 있어 release() 를 쓰지 않는다
        prev = cur.get("previous") if cur.get("pending") else cur.get("version")
        launch.write({"version": m["version"], "previous": prev, "pending": True, "tries": 0})
        _prune({m["version"], prev, cur.get("version"), launch.release()})  # 서비스가 지금 도는 버전은 지우지 않는다
        return 200, {"running": running, "to": m["version"], "restart": True}
    finally:
        _lock.release()


def restart(httpd):
    """응답을 보낸 뒤 서버를 끝낸다. main 이 정리하고 끝나면 서비스 관리자가 다시 띄운다."""
    time.sleep(1)
    tray.respawn()
    print(f"[jarvis] 자체 갱신: 새 버전으로 다시 시작한다 (pid {os.getpid()})", flush=True)
    httpd.shutdown()


def cli(args):
    """python -m jarvis update [--check]. 받기만 한다: 도는 서비스는 재시작해야 새 버전이 된다."""
    if args not in ([], ["--check"]):
        print("사용법: python -m jarvis update [--check]")
        return 2
    if args:
        try:
            v = latest()["version"]
        except Exception as e:
            print(f"릴리스를 읽지 못했습니다: {e}")
            return 1
        newer = launch.key(v) > launch.key(__version__)
        print(f"jarvis {__version__} · 최신 {v}" + (" · 업데이트가 있습니다" if newer else " · 최신입니다"))
        return 0
    code, body = start()
    if code != 200:
        print(body["error"])
        return 1
    if not body["restart"]:
        print(f"jarvis {__version__} 는 최신입니다")
        return 0
    print(f"jarvis {__version__} → {body['to']} 을 받았습니다. 서비스를 재시작하면 새 버전이 뜹니다.")
    return 0
