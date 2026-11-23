#!/usr/bin/env python3
"""단위 테스트(unittest)와 화면 공용 JS 테스트를 한 번에 돌린다.

    python3 scripts/test.py [unittest 인자...]

- 인자가 없으면 tests/ 전체를 discover 하고 node tests/test_jarvis_js.mjs 도 돌린다.
- 인자가 있으면 그대로 unittest 에 넘기고 JS 테스트는 건너뛴다(예: tests.test_core -v).
- make deps 로 만든 .venv 의 Python 으로(없으면 실행한 Python 으로) 테스트한다. 사용자 설정이 끼지 않도록 JARVIS_CONFIG 는 빈 파일로 둔다.
- bats·Pester 테스트는 셸·pwsh 가 필요해 여기서 돌리지 않는다(AGENTS.md 커맨드 표 참고).
"""

import os
import shutil
import subprocess
import sys

from deps import ROOT, venv_python


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    env = {**os.environ, "JARVIS_CONFIG": os.devnull}
    unit = [venv_python(), "-m", "unittest"] + (args or ["discover", "-s", "tests"])
    rc = subprocess.run(unit, cwd=ROOT, env=env).returncode
    if args:
        return rc
    if not shutil.which("node"):
        print("[test] node 가 없어 tests/test_jarvis_js.mjs 를 건너뜀", file=sys.stderr)
        return rc or 2
    js = subprocess.run(["node", "tests/test_jarvis_js.mjs"], cwd=ROOT, env=env).returncode
    return rc or js


if __name__ == "__main__":
    sys.exit(main())
