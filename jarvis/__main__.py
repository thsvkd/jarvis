"""python3 -m jarvis · python3 -m jarvis update [--check] · python3 -m jarvis module <이름> <동작> --json
· python3 -m jarvis agentsmd · python3 -m jarvis core-trust <plan|apply> · python3 -m jarvis relocate [--dry-run] [--json]
· python3 -m jarvis bridge · python3 -m jarvis --version"""

import os
import signal
import sys
import threading
import time

from . import __version__, launch

launch.run(__version__)  # 받아 둔 새 릴리스가 있으면 그쪽으로 넘어가고 여기로 돌아오지 않는다

if sys.argv[1:] == ["bridge"]:  # core 의 SSH bridge(jarvis/ipc.py): 표준 출력은 HTTP 응답이므로 설정·수집기를 읽기 전에 끝낸다
    from .ipc import bridge
    sys.exit(bridge())

from . import config, hswap_usage, relocate, self_update, tray  # noqa: E402
from .util import hide_child_consoles  # noqa: E402
from .collector import Collector  # noqa: E402
from .core import Core, devices_from  # noqa: E402
from .server import serve  # noqa: E402


def _confirm():
    """HEALTHY_S 동안 돌면 새 릴리스를 확정한다. 실패하면(Windows 백신이 파일을 잡는 등) 될 때까지 다시 한다:
    확정하지 못하면 평범한 재시작 몇 번에 멀쩡한 릴리스가 되돌려진다.
    확정한 뒤(건강한 판에서만) 옛 배치(~/Workspace/jarvis)를 한 번 옮긴다(relocate)."""
    time.sleep(launch.HEALTHY_S)
    while True:
        try:
            with self_update._lock:  # 버튼이 새 버전을 적는 중에 옛 값을 덮어쓰지 않게
                launch.confirm()
            break
        except Exception as e:
            print(f"[jarvis] 릴리스 확정 실패, 1분 뒤 다시: {e}", flush=True)
            time.sleep(60)
    try:
        r = relocate.run()
        print(f"[jarvis] 실행본 이전: {r['state']} {r['detail']}", flush=True)
    except Exception as e:  # 이전이 실패해도 게이트웨이는 계속 돈다
        print(f"[jarvis] 실행본 이전 실패: {type(e).__name__}: {e}", flush=True)


def agentsmd(args):
    """지금 폴더(프로젝트)에 AGENTS.md·CLAUDE.md 를 쓴다. ai-setup 의 setup_agentsmd 워커를 그대로 부른다.

    프로젝트 폴더 단위라 기기 모듈(setup)에 넣지 않았다. 인자는 워커와 같다: --harness L, --force, --dry-run.
    """
    import subprocess
    scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "setup", "scripts")
    if os.name == "nt":  # PowerShell 워커는 -Harness·-Force·-DryRun 이름만 받는다(엔진의 _worker 와 같은 변환)
        args = ["-" + "".join(p.capitalize() for p in a[2:].split("-")) if a.startswith("--") else a for a in args]
        argv = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(scripts, "setup_agentsmd.ps1"), *args]
    else:
        argv = ["bash", os.path.join(scripts, "setup_agentsmd.sh"), *args]
    return subprocess.run(argv).returncode


def main():
    hide_child_consoles()
    if sys.argv[1:2] == ["update"]:
        return self_update.cli(sys.argv[2:])
    if sys.argv[1:2] == ["module"]:
        from . import module
        return module.cli(sys.argv[2:])
    if sys.argv[1:2] == ["agentsmd"]:
        return agentsmd(sys.argv[2:])
    if sys.argv[1:2] == ["--version"]:
        print(f"jarvis {__version__}" + (" (릴리스)" if launch.release() else " (저장소)"))
        return 0
    if sys.argv[1:2] == ["core-trust"]:
        from . import core_trust
        return core_trust.cli(sys.argv[2:])
    if sys.argv[1:2] == ["relocate"]:
        return relocate.cli(sys.argv[2:])
    if sys.argv[1:]:  # 오타를 게이트웨이 기동으로 받지 않는다(같은 포트에 게이트웨이를 하나 더 띄우지 않게)
        print("사용법: python -m jarvis [update [--check] | module <이름> <동작> --json | agentsmd [인자] "
              "| core-trust <plan|apply> … | relocate [--dry-run] [--json] | --version]", file=sys.stderr)
        return 1
    sigs = {signal.SIGTERM, signal.SIGINT}
    masked = os.environ.get("JARVIS_TRAY") == "1" and hasattr(signal, "pthread_sigmask")
    if masked:
        # 아이콘 이벤트 루프가 메인 스레드를 잡고 있으면 파이썬 시그널 처리기가 돌지 못한다.
        # 아이콘(AppKit 이 스레드를 만들 수 있다)과 다른 스레드를 만들기 전에 막아 두고 아래 전용 스레드가 sigwait 로 받는다.
        signal.pthread_sigmask(signal.SIG_BLOCK, sigs)
    icon = tray.make_icon()
    if masked and not icon:  # 아이콘이 없으면 평소처럼 메인 스레드의 처리기로
        signal.pthread_sigmask(signal.SIG_UNBLOCK, sigs)
    col = Collector()
    col.start()
    threading.Thread(target=hswap_usage.maintain, name="jarvis-hswap", daemon=True).start()
    devices = devices_from(config.DEVICES)
    core = Core(devices) if devices else None
    if core:
        core.start()
    httpd = serve(col, core)
    threading.Thread(target=_confirm, name="jarvis-confirm", daemon=True).start()

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        if icon:
            icon.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if icon:
            # pystray 는 메인 스레드에서 돌아야 하므로(macOS) HTTP 서버를 스레드로 옮긴다.
            def serve_http():
                try:
                    httpd.serve_forever(0.5)
                finally:  # 서버가 죽으면 아이콘도 내려 프로세스가 끝나게 한다(launchd·예약 작업이 다시 띄운다)
                    icon.stop()

            http = threading.Thread(target=serve_http, name="jarvis-http", daemon=True)
            http.start()
            if hasattr(signal, "sigwait"):
                threading.Thread(target=lambda: stop(signal.sigwait(sigs)), name="jarvis-signal", daemon=True).start()
            try:
                icon.run()
                httpd.shutdown()
            except Exception as e:  # 아이콘이 못 떠도 서버는 계속 돈다(stop 이 httpd 를 끝낼 때까지)
                print(f"[jarvis] 트레이 아이콘 실패, 아이콘 없이 계속: {type(e).__name__}: {e}", flush=True)
                http.join()
        else:
            httpd.serve_forever(poll_interval=0.5)
    finally:
        col.shutdown()
        if core:
            core.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
