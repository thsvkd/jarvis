"""Windows 실행기: 창 없이(pythonw) jarvis 를 띄우고 출력은 %LOCALAPPDATA%\\jarvis\\jarvis.log 로 보낸다.
예약 작업(로그온 시)이 이 파일을 부른다. 설치는 README 의 'Windows' 절."""
import os
import runpy
import sys

os.environ.setdefault("JARVIS_TRAY", "1")  # 알림 영역 아이콘(jarvis/tray.py)

repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or repo, "jarvis")
os.makedirs(log_dir, exist_ok=True)
log = os.path.join(log_dir, "jarvis.log")
try:
    if os.path.getsize(log) > 5 << 20:  # 5 MB 넘으면 하나만 남기고 새로 쓴다
        os.replace(log, log + ".1")
except OSError:  # 없거나 다른 프로세스가 열고 있다: 그대로 이어 쓴다
    pass
sys.stdout = sys.stderr = open(log, "a", buffering=1, encoding="utf-8", errors="replace")
os.chdir(repo)
sys.path.insert(0, repo)
sys.argv = ["jarvis"]
runpy.run_module("jarvis", run_name="__main__", alter_sys=True)
