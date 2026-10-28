"""Windows 알림 영역·macOS 메뉴 막대 아이콘: jarvis 가 백그라운드에서 돌고 있다는 표시와 자주 쓰는 동작.

JARVIS_TRAY=1 일 때만 켠다(deploy/jarvis-win.pyw·jarvis.plist). 그래픽 세션이 없는 SSH·systemd 실행에서는
AppKit 이 프로세스를 죽일 수 있어서다. pystray·Pillow(macOS 는 pyobjc 도 따라온다)가 없으면 아이콘 없이 돈다.
종료 항목은 없다: 서비스 관리자(launchd KeepAlive·예약 작업 5분 반복)가 다시 띄우므로, 끄려면 README 의 명령을 쓴다."""

import base64
import datetime
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from . import config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = (os.path.join(os.environ.get("LOCALAPPDATA") or REPO, "jarvis", "jarvis.log") if sys.platform == "win32"
       else os.path.expanduser("~/Library/Logs/jarvis.log"))  # jarvis-win.pyw·jarvis.plist 가 출력을 보내는 곳


def commit(repo=REPO):
    """작업 트리 HEAD 의 짧은 해시. git 이 없는 Windows 도 있어 .git 을 직접 읽는다."""
    g = os.path.join(repo, ".git")
    try:
        with open(os.path.join(g, "HEAD")) as f:
            head = f.read().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        try:
            with open(os.path.join(g, ref)) as f:
                return f.read().strip()[:7]
        except FileNotFoundError:  # gc 뒤에는 packed-refs 에만 있다
            with open(os.path.join(g, "packed-refs")) as f:
                return next((line[:7] for line in f if line.rstrip().endswith(" " + ref)), "")
    except OSError:
        return ""


def error_text(err, n=50):
    """메뉴 한 줄에 들어갈 설정 오류 사유. 앞의 파일 경로는 떼고(로그에 있다), Windows 메뉴의 단축키 표시 & 는 겹친다."""
    text = (err or "").rsplit(": ", 1)[-1]
    text = text if len(text) <= n else text[:n - 1] + "…"
    return text.replace("&", "&&") if sys.platform == "win32" else text


def _open(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open", path])
    except OSError as e:  # 연결 프로그램이 없다 등
        print(f"[jarvis] 트레이: {path} 를 열지 못했다: {e}", flush=True)


def restart_helper(pid):
    """pid 가 끝나면 예약 작업을 다시 시작하는 PowerShell 명령. 작업이 막 끝난 참이면 시작이 무시될 수 있어 몇 번 되풀이한다."""
    script = (f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; foreach ($i in 1..30) {{ "
              "Start-ScheduledTask -TaskName jarvis -ErrorAction SilentlyContinue; Start-Sleep 2; "
              "if ((Get-ScheduledTask -TaskName jarvis).State -eq 'Running') { break } }")
    inner = "powershell -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + \
        base64.b64encode(script.encode("utf-16-le")).decode()
    # 작업의 job 안에 남으면 작업이 계속 '실행 중'으로 보여 시작이 막히므로, WMI 로 job 밖에서 띄운다.
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{inner}'}}"]


def respawn():
    """이 프로세스가 끝난 뒤 다시 뜨게 한다. systemd·launchd(KeepAlive)·pm2 는 스스로 다시 띄운다.
    Windows 예약 작업은 끝난 뒤 5분 반복 트리거까지 기다리므로, 이 프로세스가 끝나면 작업을 다시 시작하는 도우미를 남긴다.
    (os.execv 는 쓰지 않는다: pyw 가 sys.argv 를 바꿔 두었고, Windows 에서는 작업 밖의 두 번째 인스턴스가 된다.)"""
    if sys.platform == "win32":
        try:
            subprocess.run(restart_helper(os.getpid()), creationflags=subprocess.CREATE_NO_WINDOW, timeout=30,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError) as e:  # 도우미 없이도 5분 반복 트리거가 다시 띄운다
            print(f"[jarvis] 재시작 도우미 실패, 반복 트리거를 기다린다: {e}", flush=True)


def _restart(icon):
    """아이콘을 내리면 main 이 서버를 닫고 끝난다."""
    respawn()
    icon.stop()


def make_icon():
    if os.environ.get("JARVIS_TRAY") != "1":
        return None
    try:
        import pystray
        from PIL import Image, ImageDraw

        if sys.platform == "darwin":
            import AppKit  # pystray 가 쓰는 pyobjc. Dock 에는 띄우지 않는다.
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory)

        def dot(fill):
            img = Image.new("RGBA", (64, 64))
            ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=fill)
            return img

        ok, bad = dot((46, 160, 67)), dot((230, 160, 30))  # 정상 초록, 설정 오류 호박색
        status = " · ".join(x for x in (socket.gethostname().split(".")[0],
                                        datetime.datetime.now().strftime("%m/%d %H:%M 기동"), commit()) if x)
        url = f"http://localhost:{config.PORT}/"
        # ponytail: core 도 이 기기와 같은 포트에서 돈다고 본다. 다르면 설정 키를 더한다.
        core_url = f"http://{config.CORE[0]}:{config.PORT}/" if config.CORE else None
        conf_dir = lambda: os.path.dirname(config.CONFIG_SOURCE)
        M = pystray.MenuItem
        menu = pystray.Menu(
            M(status, None, enabled=False),
            M(lambda _: "설정 오류: " + error_text(config.CONFIG_ERROR), lambda: _open(conf_dir()),
              visible=lambda _: bool(config.CONFIG_ERROR)),
            pystray.Menu.SEPARATOR,
            M("이 기기 대시보드 열기", lambda: webbrowser.open(url), default=True),
            M("전체 기기 대시보드 열기", lambda: webbrowser.open(core_url), visible=bool(core_url)),
            pystray.Menu.SEPARATOR,
            M("로그 열기", lambda: _open(LOG), visible=lambda _: os.path.exists(LOG)),
            M("설정 폴더 열기", lambda: _open(conf_dir()), visible=lambda _: bool(config.CONFIG_PATH)),
            pystray.Menu.SEPARATOR,
            M("재시작", _restart))
        icon = pystray.Icon("jarvis", bad if config.CONFIG_ERROR else ok, f"jarvis :{config.PORT}", menu)

        state = lambda: (config.CONFIG_ERROR, os.path.exists(LOG), bool(config.CONFIG_PATH))

        def apply(err):
            icon.icon = bad if err else ok
            icon.update_menu()

        def refresh():
            """설정은 수집기 루프가 다시 읽는다. 그 결과를 아이콘 색과 메뉴(오류 줄·숨김 항목)에 옮긴다.
            바뀌었을 때만 건드리고, macOS 는 AppKit 이 메인 스레드에서만 안전하므로 그쪽으로 넘긴다
            (메뉴가 열려 있는 동안에는 닫힐 때까지 미뤄져 클릭 중인 메뉴가 바뀌지 않는다)."""
            last = state()
            while True:
                time.sleep(10)
                now = state()
                if now == last:
                    continue
                last = now
                try:
                    if sys.platform == "darwin":
                        from PyObjCTools import AppHelper
                        AppHelper.callAfter(apply, now[0])
                    else:
                        apply(now[0])
                except Exception as e:  # 아이콘이 내려가는 중 등. 다음 변화 때 다시 한다.
                    print(f"[jarvis] 트레이 갱신 실패: {type(e).__name__}: {e}", flush=True)

        threading.Thread(target=refresh, name="jarvis-tray", daemon=True).start()
        return icon
    except Exception as e:
        print(f"[jarvis] 트레이 아이콘 없이 실행: {type(e).__name__}: {e}", flush=True)
        return None
