"""macOS 게이트웨이 로그의 크기를 제한한다(DAEMON-17). Linux(journal)와 Windows 는 건드리지 않는다.

LaunchAgent 는 launchd 가 StandardOutPath(~/Library/Logs/jarvis.log)를, LaunchDaemon 은 macos_daemon.WRAPPER 의 `>>"$JARVIS_LOG"` 가 연다.
그 파일은 jarvis 가 연 것이 아니라 이름을 바꿔도 쓰는 쪽은 옛 파일에 계속 쓴다. 그래서 복사한 뒤 비운다(copytruncate):
둘 다 덧붙이기로 열어 비운 뒤의 줄은 앞에서부터 쌓인다. 복사와 비우기 사이에 쓰인 몇 줄은 잃을 수 있다.
"""
import os
import shutil
import sys
import threading
import time

LIMIT = 10 << 20  # 이보다 크면 돌린다
KEEP = 3  # jarvis.log.1 … .3
EVERY_S = 600


def path():
    """지금 게이트웨이의 로그. LaunchDaemon 은 plist 의 JARVIS_LOG, 아니면 LaunchAgent 의 자리(tray.LOG 와 같다)."""
    return os.environ.get("JARVIS_LOG") or os.path.join(os.path.expanduser("~"), "Library", "Logs", "jarvis.log")


def rotate(log, limit=LIMIT, keep=KEEP):
    """log 가 limit 보다 크면 .1 로 복사하고(.1→.2 … keep 개까지) 비운다. 돌렸으면 True. 모든 파일을 0600 으로 둔다.
    심볼릭 링크나 없는 파일은 건드리지 않는다."""
    try:
        if os.path.islink(log) or not os.path.isfile(log):
            return False
        os.chmod(log, 0o600)
        if os.path.getsize(log) <= limit:
            return False
        for i in range(keep - 1, 0, -1):
            if os.path.lexists(f"{log}.{i}"):
                os.replace(f"{log}.{i}", f"{log}.{i + 1}")
        fd = os.open(f"{log}.1", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as dst, open(log, "rb") as src:
            shutil.copyfileobj(src, dst)
        os.chmod(f"{log}.1", 0o600)
        os.truncate(log, 0)
        return True
    except OSError as e:  # 로그를 돌리지 못해도 게이트웨이는 그대로 돈다
        print(f"[jarvis] 로그 회전 실패: {type(e).__name__}", flush=True)
        return False


def _watch():
    while True:
        rotate(path())
        time.sleep(EVERY_S)


def start():
    """macOS 에서만 시작할 때와 EVERY_S 마다 확인하는 스레드를 띄운다."""
    if sys.platform == "darwin":
        threading.Thread(target=_watch, name="jarvis-logrotate", daemon=True).start()
