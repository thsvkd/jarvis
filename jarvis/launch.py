"""릴리스 런처: 서비스는 저장소에서 `python -m jarvis` 를 띄우고, 여기서 받아 둔 릴리스(versions/<버전>)로 넘어간다.
표준 라이브러리만 쓴다(config 등을 읽기 전에 돈다). 받기·검증·전환은 self_update 가 한다.

ROOT/current.json = {"version": 지금 쓸 릴리스, "previous": 되돌아갈 릴리스(None 이면 저장소), "pending": 아직 확인 전, "tries": 기동 횟수}
- 저장소의 버전이 같거나 새로우면 저장소를 띄운다(s2 처럼 개발하는 저장소는 그대로 돈다).
- 새 릴리스가 HEALTHY_S 동안 돌면 confirm() 이 pending 을 지운다. 그 전에 MAX_TRIES 번 넘게 다시 뜨면 previous 로 되돌린다.
- JARVIS_NO_RELEASE=1 이면 넘어가지 않는다(갱신 경로가 망가진 릴리스를 건너 저장소 코드로 `python -m jarvis update` 하는 비상구).

이 파일은 사실상 고정이다: 기기의 저장소는 마이그레이션 때 받은 뒤로 다시 pull 하지 않으므로, 여기와 current.json 형식을 바꾸면
이전 기기의 런처가 새 릴리스를 못 띄울 수 있다. 바꿀 때는 옛 런처가 읽는 키(version·previous·pending·tries)를 그대로 둔다.
"""

import json
import os
import runpy
import sys
import tempfile

ROOT = os.environ.get("JARVIS_RELEASES") or (os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "jarvis")
                                            if sys.platform == "win32" else os.path.expanduser("~/.local/share/jarvis"))
VERSIONS = os.path.join(ROOT, "versions")
CURRENT = os.path.join(ROOT, "current.json")
MAX_TRIES = 2  # systemd 기본 StartLimitBurst(10초에 5번) 전에 되돌린다
HEALTHY_S = 60
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def key(v):
    try:
        return tuple(int(p) for p in v.split("."))
    except (AttributeError, ValueError):
        return None


def read():
    try:
        with open(CURRENT, encoding="utf-8") as f:
            cur = json.load(f)
        return cur if isinstance(cur, dict) else {}
    except (OSError, ValueError):
        return {}


def write(cur):
    os.makedirs(ROOT, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".current-", dir=ROOT)  # 서비스와 CLI 가 동시에 써도 임시 파일이 겹치지 않게
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cur, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CURRENT)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def release():
    """지금 도는 코드가 받은 릴리스면 그 버전, 저장소면 None."""
    parent, name = os.path.split(HERE)
    return name if os.path.normcase(os.path.realpath(parent)) == os.path.normcase(os.path.realpath(VERSIONS)) else None


def path(v):
    d = os.path.join(VERSIONS, v) if key(v) else None
    return d if d and os.path.isfile(os.path.join(d, "jarvis", "__init__.py")) else None


def target(repo_version, boot=True):
    """띄울 릴리스 폴더. None 이면 저장소 그대로. boot: 서비스 기동(되돌리기 횟수를 센다)."""
    cur = read()
    v = cur.get("version")
    d = path(v) if key(v) and key(repo_version) and key(v) > key(repo_version) else None
    if boot and cur.get("pending") and v:
        if not d:  # 저장소가 더 새롭거나 폴더가 없다: 이 릴리스는 뜨지 않으므로 세지 않는다
            write({**cur, "pending": False, "tries": 0})
            return None
        cur["tries"] = int(cur.get("tries") or 0) + 1
        if cur["tries"] > MAX_TRIES:
            print(f"[jarvis] 릴리스 {v} 가 {MAX_TRIES}번 넘게 다시 떴다: {cur.get('previous') or '저장소'} 로 되돌린다", flush=True)
            cur = {"version": cur.get("previous"), "previous": None, "pending": False, "tries": 0, "bad": v}
            write(cur)
            return target(repo_version, boot=False)
        write(cur)
    return d


def run(repo_version):
    """저장소에서 떴으면 받아 둔 릴리스로 넘어간다(같은 프로세스에서: 서비스 관리자가 보는 PID 가 그대로다).
    넘어가면 그 릴리스의 main 이 끝날 때 SystemExit 으로 빠져나온다. 넘어가지 않으면 그냥 돌아온다."""
    if release() or os.environ.get("JARVIS_NO_RELEASE") == "1":
        return
    try:
        d = target(repo_version, boot=len(sys.argv) <= 1)  # `python -m jarvis update` 는 기동으로 세지 않는다
    except Exception as e:  # 상태 파일을 못 읽거나 쓰면(손으로 고친 값 포함) 저장소로 뜬다
        print(f"[jarvis] 릴리스 상태를 읽지 못해 저장소로 뜬다: {e}", flush=True)
        return
    if not d:
        return
    for name in [m for m in sys.modules if m == "jarvis" or m.startswith("jarvis.")]:
        del sys.modules[name]
    sys.path[:] = [d] + [p for p in sys.path if os.path.normcase(os.path.realpath(p or ".")) != os.path.normcase(HERE)]
    os.chdir(d)
    runpy.run_module("jarvis", run_name="__main__", alter_sys=True)
    sys.exit(0)


def confirm():
    """HEALTHY_S 동안 돈 릴리스를 확정한다(다음 기동부터 되돌리지 않는다)."""
    cur, v = read(), release()
    if v and cur.get("version") == v and cur.get("pending"):
        write({**cur, "pending": False, "tries": 0})
