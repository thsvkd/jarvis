"""jarvis 자체 갱신: core 의 버튼이 부르면 origin main 을 fast-forward 로만 받는다.
받은 코드가 지금 도는 코드와 다르면 HTTP 서버를 끝내 서비스 관리자(systemd Restart=always · launchd KeepAlive ·
예약 작업 재시작 도우미 · pm2 autorestart)가 새 코드로 다시 띄우게 한다. 저장소가 public 이라 인증이 필요 없다.
서비스 관리자 없이 손으로 띄운 jarvis 는 끝난 채로 남는다."""

import os
import shutil
import threading
import time

from . import orca_issue, orca_update, tray
from .util import run

_lock = threading.Lock()


def _head(repo):
    rc, out, _ = run(["git", "-C", repo, "rev-parse", "HEAD"], timeout=10)
    return out.strip() if rc == 0 else None


BOOT = _head(tray.REPO)  # 이 프로세스가 띄운 코드. push 로 작업 트리만 바뀐 경우도 재시작 대상으로 잡는다


def start(repo=tray.REPO, boot=None):
    """(http 상태, 응답). 응답의 restart 가 참이면 호출자가 restart() 를 부른다."""
    boot = boot or BOOT
    if not shutil.which("git"):
        return 409, {"error": "이 기기에는 git 이 없습니다"}
    if orca_update.job and orca_update.job.get("state") == "running":
        return 409, {"error": "Orca 업데이트가 끝난 뒤에 갱신하세요"}
    if orca_issue.job and orca_issue.job.get("state") == "running":
        return 409, {"error": "페어링 코드 발급이 끝난 뒤에 갱신하세요"}
    if not _lock.acquire(blocking=False):
        return 409, {"error": "이미 갱신하고 있습니다"}
    try:
        rc, out, _ = run(["git", "-C", repo, "symbolic-ref", "--short", "HEAD"], timeout=10)
        if rc != 0 or out.strip() != "main":
            return 409, {"error": f"main 브랜치가 아닙니다({out.strip() or '분리된 HEAD'})"}
        # 작업 중인 저장소(s2)를 반쯤 고친 코드로 재시작하지 않는다. push(updateInstead)와 같은 '깨끗한 트리' 조건
        rc, out, _ = run(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"], timeout=10)
        if rc != 0 or out.strip():
            return 409, {"error": "커밋 안 된 수정이 있어 받지 않습니다"}
        before = _head(repo)
        # 자격 도우미(Windows 는 GCM 창)가 뜨면 pythonw·서비스에서는 아무도 답하지 못해 시간 초과까지 멈춘다
        rc, out, err = run(["git", "-C", repo, "-c", "credential.helper=", "pull", "--ff-only", "--quiet", "origin", "main"],
                           timeout=90)
        if rc != 0:
            return 409, {"error": "git pull 실패: " + ((err or out).strip().splitlines() or ["?"])[-1][:300]}
        after = _head(repo)
        restart = bool(after and after != (boot or before))  # 기동 때 HEAD 를 못 읽었으면 이번에 받은 것만 본다
        return 200, {"from": (before or "")[:10], "to": (after or "")[:10], "running": (boot or "")[:10],
                     "restart": restart}
    finally:
        _lock.release()


def restart(httpd):
    """응답을 보낸 뒤 서버를 끝낸다. main 이 정리하고 끝나면 서비스 관리자가 다시 띄운다."""
    time.sleep(1)
    tray.respawn()
    print(f"[jarvis] 자체 갱신: 새 코드로 다시 시작한다 (pid {os.getpid()})", flush=True)
    httpd.shutdown()
