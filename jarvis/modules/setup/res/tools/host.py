"""실행 환경 보조 함수: 플랫폼 키, WSL 판별, PATH 의 실행 파일 위치, 프로세스 트리 종료.

update-ai 와 npm-release 가 쓴다. doctor 가 jarvis 로 옮겨 가면서 res/doctor/doctor.py 에서 본문 그대로 옮겼다.
"""
import os
import re
import signal
import subprocess
import sys


def platform_key():
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def on_wsl():
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def locations(name):
    """PATH 순서대로 name 실행 파일을 찾는다. 같은 디렉터리·같은 실체(심볼릭 링크)는 한 번만 센다.

    WSL 은 Windows PATH(/mnt/c/...)를 이어 붙인다. 그쪽은 다른 실행 환경이라 세지 않는다.
    """
    skip_windows = os.name != "nt" and on_wsl()
    exts = [""]
    if os.name == "nt":
        exts = [e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    seen_dirs, seen_real, found = set(), set(), []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or (skip_windows and re.match(r"^/mnt/[a-z]/", directory)):
            continue
        key = os.path.normcase(os.path.abspath(directory))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        for ext in exts:
            candidate = os.path.join(directory, name + ext)
            if os.name == "nt":
                # WinGet\Links 의 심볼릭 링크는 세션에 따라 대상을 따라갈 수 없다(WinError 448,
                # 신뢰할 수 없는 탑재 지점). isfile 은 False 가 되지만 설치는 맞으므로 링크 자체로 센다.
                present = os.path.isfile(candidate) or os.path.islink(candidate)
            else:
                present = os.path.isfile(candidate) and os.access(candidate, os.X_OK)
            if present:
                try:
                    real = os.path.normcase(os.path.realpath(candidate))
                except OSError:
                    real = os.path.normcase(candidate)
                if real not in seen_real:
                    seen_real.add(real)
                    found.append(candidate)
                break
    return found


def kill_tree(proc):
    # .cmd 셔임은 cmd.exe 가 node.exe 를 손자로 띄운다. 자식만 죽이면 손자가 남는다.
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
