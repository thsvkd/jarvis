#!/usr/bin/env python3
"""게이트웨이를 설치된 서비스와 겹치지 않게 띄운다. core 로 띄우려면 devices 가 있는 설정을 --config 로 넘긴다.

    python3 scripts/run.py [--port 9390] [--config 설정 파일]

- 시험 포트·루프백에서만 연다.
- 상태와 릴리스 폴더를 .tmp-run/ 에 따로 둔다. 릴리스 폴더에는 IPC 소켓과 current.json 이 있어서, 같이 쓰면
  서비스의 소켓을 지우거나(ipc.serve 가 기존 소켓을 지운다) 받아 둔 릴리스로 넘어간다. 넘어가지 않게 JARVIS_NO_RELEASE 도 준다.
"""

import argparse
import os
import subprocess
import sys

from deps import ROOT, venv_python

TMP = os.path.join(ROOT, ".tmp-run")


def run_env(port, config):
    return {
        **os.environ,
        "JARVIS_CONFIG": config,
        "JARVIS_PORT": str(port),
        "JARVIS_BIND": "127.0.0.1",
        "JARVIS_STATE": os.path.join(TMP, "state"),
        "JARVIS_RELEASES": os.path.join(TMP, "releases"),
        "JARVIS_NO_RELEASE": "1",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="게이트웨이를 시험 포트로 띄운다.")
    ap.add_argument("--port", type=int, default=9390)
    ap.add_argument("--config", default=os.devnull, help="설정 파일(기본: 빈 설정 = gateway)")
    a = ap.parse_args(argv)
    print(f"[run] http://127.0.0.1:{a.port}/ (Ctrl+C 로 끝낸다)", flush=True)
    try:
        return subprocess.run([venv_python(), "-m", "jarvis"], cwd=ROOT, env=run_env(a.port, os.path.abspath(a.config))).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
