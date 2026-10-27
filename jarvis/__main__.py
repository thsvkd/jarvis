"""python3 -m jarvis"""

import os
import signal
import sys
import threading

from . import config, orca_issue, orca_update, tray
from .collector import Collector
from .core import Core, devices_from
from .server import serve


def main():
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
    threading.Thread(target=lambda: (orca_issue.recover(), orca_update.recover()),
                     name="jarvis-orca-recover", daemon=True).start()
    devices = devices_from(config.DEVICES)
    core = Core(devices) if devices else None
    if core:
        core.start()
    httpd = serve(col, core)

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
