"""core 전용: 세션 원본 수집(session-vault). 기기별 세션 원본을 이 기기 ~/session-vault/mirror/<기기> 로 모으고,
restic 으로 그 사본을 백업한다. 2026-11-16 ~/.local/share/session-vault/vault.py·nightly.sh 에서 옮겼다.

- 원격 기기에는 아무것도 설치하지 않는다. SSH(BatchMode)로 표준 라이브러리 python 페이로드를 보내 tar 로 받는다.
  각 기기의 게이트웨이(:9300)로는 세션을 주고받지 않는다(jarvis 에는 인증이 없다).
- 사본에서는 파일을 지우지 않는다(원본이 사라져도 남긴다). 받은 파일은 임시 파일에 쓴 뒤 교체한다.
- 기본은 꺼짐이다. core 설정 파일의 [session_vault] enabled = true 일 때만 동작한다(docs/configuration.md).
- API 는 상태 요약만 낸다. 사본(mirror)의 파일 목록이나 원문은 읽어 내보내지 않는다.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

from . import config, core

try:
    import fcntl
except ImportError:  # Windows: 이 기능을 켜지 않는다(enabled)
    fcntl = None

ROOT = os.path.expanduser("~/session-vault")  # 경로를 바꾸지 않는다: AgentsView 색인이 mirror·state.json 을 가리킨다
PASSWORD_FILE = os.path.expanduser("~/.config/session-vault/restic-password")
STALE_S = 3 * 86400  # 마지막 성공이 이보다 오래면 강조한다
LOG_TAIL = 1 << 20  # 로그는 끝에서 이만큼만 읽는다(restic 출력이 길어져도)
ERROR_MAX = 200
MARGIN_S = 86400  # 기기 간 시계 차이와 수집 중 수정을 덮는 여유
MAX_DEVICE_BYTES = 20 << 30  # 기기 하나가 한 번에 보내는 tar 상한(장악·폭주한 기기가 core 디스크를 채우지 못하게)
MIN_FREE_BYTES = 5 << 30  # 사본 디스크에 이만큼은 남긴다
SYNC_TIMEOUT_S = 2 * 3600  # 기기 하나의 수집 상한(멈춘 ssh 가 다음 실행을 영영 막지 않게)
RESTIC_TIMEOUT_S = 3 * 3600
SCHEDULE_WINDOW_S = 6 * 3600  # 예약 시각에 꺼져 있었으면 이 안에 켜졌을 때 그날 몫을 돈다
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", *core.SSH_NOMUX]
# ssh 대상(별칭·호스트·IP, 사용자@ 허용)과 원격 python 명령의 낱말. 원격 셸이 해석하므로 셸 특수 문자를 받지 않는다
TARGET_RE = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
PY_WORD_RE = re.compile(r"[A-Za-z0-9_./][A-Za-z0-9_./-]{0,63}|-[A-Za-z0-9]{1,8}")
SCHEDULE_RE = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
SCHEDULED_ENV = "JARVIS_SESSION_VAULT_SCHEDULED"  # 예약이 띄운 자식에게 그 날짜를 넘긴다
LEGACY_RE = r"session-vault/(vault\.py|nightly\.sh)"  # 옛 launchd 작업이 도는 중이면 같은 사본에 쓰지 않는다

# 원격에서 실행하는 부분. 첫 줄에 MODE, SINCE 를 붙여 보낸다. 표준 라이브러리만 쓴다(vault.py 와 같다).
PAYLOAD = r'''
import hashlib, json, os, stat, sys, tarfile, tempfile
SESSION_ROOTS = (".claude/projects", ".codex/sessions", ".codex/archived_sessions")
ORCA_ROOTS = (".config/orca", ".config/orca-desktop", "Library/Application Support/orca",
              "Library/Application Support/orca-desktop", ".orca", "AppData/Roaming/orca", "AppData/Roaming/orca-desktop")
# 실행 코드 사본과 캐시(다시 받을 수 있다), 자격 증명(세션 복원에 필요 없고 평문 사본에 두면 안 된다)
SKIP_DIRS = {"Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache", "Crashpad", "orcad-artifacts",
             "serve-supervisor", "daemon-host", "daemon-host-profiles", "node_modules", "plugins",
             "claude-runtime-auth", "claude-accounts"}
SKIP_FILES = {"auth.json", ".credentials.json", "orca-e2ee-keypair.json", "orca-devices.json", "orca-runtime.json",
              "Cookies", "Cookies-journal", "Trust Tokens", "Trust Tokens-journal", "orcad.lock", ".updaterId"}

def skip_file(name):
    low = name.lower()
    return name in SKIP_FILES or name.endswith((".key", ".sock", ".lock")) or name.startswith("Singleton") \
        or any(w in low for w in ("secret", "credential", "token"))

def walk(home):
    profiles = os.path.join(home, ".hermes", "profiles")
    hermes = [".hermes/sessions"] + ([".hermes/profiles/" + e + "/sessions" for e in sorted(os.listdir(profiles))] if os.path.isdir(profiles) else [])
    for group, roots in ((False, SESSION_ROOTS + tuple(hermes)), (True, ORCA_ROOTS)):
        for root in roots:
            base = os.path.join(home, *root.split("/"))
            for r, ds, fs in os.walk(base):
                if group:
                    ds[:] = [d for d in ds if d not in SKIP_DIRS]
                for f in fs:
                    if group and skip_file(f):
                        continue
                    p = os.path.join(r, f)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        yield p, os.path.relpath(p, home).replace(os.sep, "/"), st

def stream(home, since):
    # 스풀에 먼저 복사한다: 읽는 사이 파일이 줄거나 늘어도 tar 헤더와 내용 길이가 어긋나지 않는다
    count = size = 0
    failed = []
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT) as t:
        for p, rel, st in walk(home):
            if max(st.st_mtime, st.st_ctime) < since:  # codex 는 압축 파일에 옛 mtime 을 남긴다
                continue
            try:
                with open(p, "rb") as src, tempfile.SpooledTemporaryFile(max_size=64 << 20) as spool:
                    while True:
                        b = src.read(1 << 20)
                        if not b:
                            break
                        spool.write(b)
                    n = spool.tell()
                    spool.seek(0)
                    ti = tarfile.TarInfo(rel)
                    ti.size, ti.mtime, ti.mode = n, st.st_mtime, 0o600
                    t.addfile(ti, spool)
                    count += 1
                    size += n
            except OSError as e:
                failed.append([rel, type(e).__name__])
    sys.stderr.write(json.dumps({"files": count, "bytes": size, "failed": failed}) + "\n")

def digests(home):
    out = sys.stdout
    for p, rel, st in walk(home):
        h = hashlib.sha256()
        try:
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
        except OSError as e:
            out.write(json.dumps({"rel": rel, "error": type(e).__name__}) + "\n")
            continue
        out.write(json.dumps({"rel": rel, "sha256": h.hexdigest(), "size": st.st_size, "mtime": st.st_mtime}) + "\n")

if __name__ == "__main__" and MODE:
    home = os.path.expanduser("~")
    stream(home, SINCE) if MODE == "stream" else digests(home)
'''


def settings():
    s = config._FILE.get("session_vault")
    return s if isinstance(s, dict) else {}


def enabled():
    """core 설정 파일에 [session_vault] enabled = true 가 있을 때만. 부르는 쪽이 core 인지(devices) 따로 본다."""
    # root 로 돌면 켜지 않는다: 원격이 보낸 tar 를 root 가 풀고 root 의 ssh 키를 쓰게 된다
    return fcntl is not None and os.geteuid() != 0 and settings().get("enabled") is True


def devices():
    """({기기: (ssh 대상 또는 None(이 기기), python 명령)}, [설정 오류]). jarvis devices 를 따르고
    [session_vault.devices.<기기>] 의 ssh·python 이 덮어쓴다(ssh = "" 는 이 기기). 그 표에만 있는 기기도 모은다."""
    out, errors = {}, []
    for d in config.DEVICES if isinstance(config.DEVICES, list) else []:
        if isinstance(d, dict) and isinstance(d.get("id"), str):
            target = d.get("ssh") if isinstance(d.get("ssh"), str) else urllib.parse.urlsplit(str(d.get("url") or "")).hostname
            if target:  # 닿을 곳을 모르는 항목은 모으지 않는다(이 기기는 덮어쓰기 표의 ssh = "" 로만)
                out[d["id"]] = (target, "python3")
    over = settings().get("devices")
    for i, o in (over.items() if isinstance(over, dict) else ()):
        if not isinstance(o, dict):
            errors.append(f"{i}: 테이블이 아닙니다")
            continue
        if i not in out and "ssh" not in o:
            errors.append(f"{i}: jarvis devices 에 없는 기기는 ssh 가 필요합니다")
            continue
        target, python = out.get(i, (None, "python3"))
        out[i] = (o.get("ssh", target), o.get("python", python))
    good = {}
    for i, (target, python) in out.items():
        # 기기 이름은 사본 폴더 이름이 된다: '.'·'..' 처럼 영숫자로 시작하지 않는 이름은 받지 않는다
        if not core.ALIAS_RE.fullmatch(i):
            errors.append(f"{i!r}: 기기 이름 형식이 아닙니다")
        elif target is not None and not isinstance(target, str) or target and not TARGET_RE.fullmatch(target):
            errors.append(f"{i}: ssh 대상 형식이 아닙니다")
        elif not isinstance(python, str) or not python.split() or not all(PY_WORD_RE.fullmatch(w) for w in python.split()) \
                or python.startswith("-"):
            errors.append(f"{i}: python 명령 형식이 아닙니다")
        else:
            good[i] = (target or None, python)
    return good, errors


def command(target, python, mode, since):
    script = f"MODE={mode!r}\nSINCE={since!r}\n{PAYLOAD}"
    if target is None:  # 이 기기: 게이트웨이와 같은 python 으로(launchd 의 PATH 에 기대지 않는다)
        return [sys.executable, "-I", "-"], script.encode()
    return [core.SSH_BIN, *SSH_OPTS, "--", target, *python.split(), "-I", "-"], script.encode()


def safe_path(device, rel, root=None):
    """원격이 준 경로가 그 기기의 사본 폴더 밖으로 나가면 거부한다."""
    base = os.path.join(root or ROOT, "mirror", device)
    path = os.path.normpath(os.path.join(base, rel))
    if os.path.isabs(rel) or not path.startswith(base + os.sep):
        raise ValueError("안전하지 않은 경로")
    return path


class _Capped:
    """tar 스트림을 MAX_DEVICE_BYTES 까지만 읽는다."""

    def __init__(self, f, cap):
        self.f, self.left = f, cap

    def read(self, n=-1):
        b = self.f.read(n if n is not None and n >= 0 else 1 << 20)
        self.left -= len(b)
        if self.left < 0:
            raise ValueError("한 번에 받을 수 있는 크기를 넘었습니다")
        return b


_BIG_HEADERS = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)


class _Info(tarfile.TarInfo):
    # tarfile 은 PAX·GNU 긴 이름 헤더의 본문을 한 번에 메모리로 읽는다: 원격이 적은 크기를 1 MiB 로 막는다.
    # shortcut: tarfile 내부 메서드(_proc_member) 재정의, Python 이 이름을 바꾸면 테스트가 깨진다
    def _proc_member(self, tarfile_):
        if self.type in _BIG_HEADERS and self.size > 1 << 20:
            raise tarfile.HeaderError("tar 헤더가 너무 큽니다")
        return super()._proc_member(tarfile_)


def _mkdirs(base, path):
    """base 아래 path 까지 한 단계씩 만든다. 이미 있는 단계가 링크면(jarvis 는 링크를 만들지 않는다) 거부한다."""
    d = base
    for part in os.path.relpath(path, base).split(os.sep):
        if part == ".":
            break
        d = os.path.join(d, part)
        if os.path.islink(d):
            raise ValueError("안전하지 않은 경로")
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            pass


def extract(device, stream, root=None):
    mirror = os.path.join(root or ROOT, "mirror")
    os.makedirs(mirror, mode=0o700, exist_ok=True)
    with tarfile.open(fileobj=_Capped(stream, MAX_DEVICE_BYTES), mode="r|", tarinfo=_Info) as t:
        for m in t:
            if not m.isfile():
                continue
            path = safe_path(device, m.name, root)
            if shutil.disk_usage(mirror).free < m.size + MIN_FREE_BYTES:
                raise OSError("사본 디스크 여유가 부족합니다")
            _mkdirs(mirror, os.path.dirname(path))  # 기기 폴더부터 확인한다
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".part-")
            try:
                with os.fdopen(fd, "wb") as out, t.extractfile(m) as src:
                    for b in iter(lambda: src.read(1 << 20), b""):
                        out.write(b)
                os.utime(tmp, (m.mtime, m.mtime))
                os.replace(tmp, path)  # 끊겨도 이전 사본은 온전하다
            except BaseException:
                os.unlink(tmp)
                raise


def _spawn(cmd, stdin, stdout, stderr):
    p = subprocess.Popen(cmd, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
    timer = threading.Timer(SYNC_TIMEOUT_S, core._kill, (p,))
    timer.daemon = True
    timer.start()
    return p, timer


def sync(device, target, python, since, root=None):
    started = time.time()
    cmd, script = command(target, python, "stream", since)
    tail = bytearray()  # 원격 stderr 는 끝 1 MiB 만 메모리에(요약 JSON 이 마지막 줄이다)

    def drain():
        for b in iter(lambda: p.stderr.read(65536), b""):
            tail.extend(b)
            del tail[:-(1 << 20)]

    with tempfile.TemporaryFile() as src:
        src.write(script)
        src.seek(0)
        p, timer = _spawn(cmd, src, subprocess.PIPE, subprocess.PIPE)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            try:
                extract(device, p.stdout, root)
            except Exception as e:
                core._kill(p)
                p.wait()
                return {"ok": False, "error": _why(e)}
            rc = p.wait()
        finally:
            timer.cancel()
            p.stdout.close()
            reader.join(10)
            p.stderr.close()
        text = bytes(tail).decode("utf-8", "replace").strip()
    summary = None
    for line in reversed(text.splitlines()):
        try:
            summary = json.loads(line)
            break
        except ValueError:
            continue
    if rc or not isinstance(summary, dict):
        return {"ok": False, "error": f"rc={rc} {core._last(text.encode())}"[:300]}
    return {"ok": True, "started": started, "seconds": round(time.time() - started), **summary}


def verify(device, target, python, root=None):
    """원본의 sha256 과 사본을 대조한다. 실행 중인 프로그램이 쓰는 파일은 달라질 수 있다."""
    cmd, script = command(target, python, "digest", 0)
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p, timer = _spawn(cmd, subprocess.PIPE, out, err)
        try:
            p.communicate(script)
        finally:
            timer.cancel()
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(), err.read()
    if p.returncode:
        return {"ok": False, "error": stderr.decode("utf-8", "replace")[-300:]}
    total = match = 0
    bad = []
    for line in stdout.decode("utf-8", "replace").split("\n"):
        if not line.strip():
            continue
        total += 1
        try:
            d = json.loads(line)
            if not isinstance(d, dict) or not isinstance(d.get("rel"), str):
                raise ValueError
        except ValueError:
            bad.append(["?", "형식 오류"])
            continue
        if "error" in d:
            bad.append([d["rel"], d["error"]])
            continue
        try:
            with open(safe_path(device, d["rel"], root), "rb") as f:
                h = hashlib.sha256()
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            if h.hexdigest() == d["sha256"]:
                match += 1
            else:
                bad.append([d["rel"], "다름"])
        except (OSError, ValueError):
            bad.append([d["rel"], "사본 없음"])
    return {"ok": not bad, "total": total, "match": match, "bad_count": len(bad), "bad": bad}


def _why(e):
    """오류 한 줄. OSError 는 경로를 빼고 이유만(경로에는 원격 세션 폴더 이름이 들어 있다)."""
    msg = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
    return f"{type(e).__name__}: {msg}"[:300]


def _load_state(root):
    try:
        with open(os.path.join(root, "state.json"), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _restic(args, log):
    """restic 종료 코드. 저장소 설정·실행 파일·비밀번호 파일이 없으면 이유를 로그에 쓰고 1."""
    repo = settings().get("restic_repository")
    exe = shutil.which("restic", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))
    why = ("[session_vault] restic_repository 가 없습니다" if not isinstance(repo, str) or not repo else
           "restic 이 없습니다" if not exe else f"{PASSWORD_FILE} 가 없습니다" if not os.path.isfile(PASSWORD_FILE) else None)
    if why:
        log.write(f"restic {args[0]}: {why}\n")
        log.flush()
        return 1
    # 비밀번호는 파일에서만 읽는다(환경 변수·인자에 값을 넣지 않는다)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
    env.update(RESTIC_PASSWORD_FILE=PASSWORD_FILE, RESTIC_REPOSITORY=repo)
    try:
        return subprocess.run([exe, *args], stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                              timeout=RESTIC_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        log.write(f"restic {args[0]}: {RESTIC_TIMEOUT_S}초 안에 끝나지 않았습니다\n")
        return 1


class Busy(Exception):
    pass


_run_lock = threading.Lock()
_child = None  # 게이트웨이가 띄운 수집 프로세스


def _locked(root=None):
    """다른 실행(게이트웨이가 띄운 수집, CLI)이 .jarvis.lock 을 쥐고 있으면 참."""
    try:
        f = open(os.path.join(root or ROOT, ".jarvis.lock"), "a")
    except OSError:
        return False
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


def _legacy_running():
    try:
        return subprocess.run(["pgrep", "-f", LEGACY_RE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(names=None, root=None, backup=True, echo=None):
    """수집 → restic backup → check(--read-data-subset=2%). forget·prune 은 하지 않는다.
    로그는 옛 nightly.sh 와 같은 형식으로 logs/nightly-YYYYMMDD.log 에 덧붙인다. 다른 실행이 있으면 Busy."""
    root = root or ROOT
    devs, errors = devices()
    names = names or list(devs)
    unknown = [n for n in names if n not in devs]
    if unknown:
        raise ValueError(f"모르는 기기: {unknown}")
    if _legacy_running():
        raise Busy("옛 session-vault 작업이 도는 중입니다")
    os.makedirs(os.path.join(root, "mirror"), mode=0o700, exist_ok=True)
    os.makedirs(os.path.join(root, "logs"), mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    lock = open(os.path.join(root, ".jarvis.lock"), "a")
    try:
        for i in range(20):  # 상태 확인(_locked)이 잠깐 쥐는 것과 겹쳐도 시작하도록 2초까지 다시
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if i == 19:
                    raise Busy("다른 수집이 도는 중입니다") from None
                time.sleep(0.1)
        day = os.environ.get(SCHEDULED_ENV)
        if day:  # 예약 실행: 잠금을 잡은 뒤에야 그날 몫을 했다고 적는다(Busy 로 끝나면 다음 분에 다시)
            with open(os.path.join(root, ".jarvis-scheduled"), "w", encoding="utf-8") as f:
                f.write(day + "\n")
        path = os.path.join(root, "logs", f"nightly-{datetime.now():%Y%m%d}.log")
        with open(path, "a", encoding="utf-8") as log:
            log.write(f"== 시작 {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            for e in errors:
                log.write(f"설정 오류: {e}\n")
            log.flush()
            state = _load_state(root)
            failed = False
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = {pool.submit(sync, n, *devs[n], max(0, (_num(state.get(n)) or 0) - MARGIN_S), root): n for n in names}
                for job in as_completed(jobs):
                    n = jobs[job]
                    try:
                        result = job.result()
                    except Exception as e:
                        result = {"ok": False, "error": _why(e)}
                    line = f"{n} {json.dumps(result, ensure_ascii=False)}"
                    log.write(line + "\n")
                    log.flush()
                    if echo:
                        echo(line)
                    failed = failed or not result["ok"]
                    if result["ok"]:
                        state[n] = result["started"]
                        fd, tmp = tempfile.mkstemp(dir=root, prefix=".state-")
                        with os.fdopen(fd, "w") as f:
                            json.dump(state, f)
                        os.replace(tmp, os.path.join(root, "state.json"))
            rc = {"sync": int(failed)}
            if backup:
                rc["backup"] = _restic(["backup", "--tag", "nightly", "--quiet", os.path.join(root, "mirror")], log)
                rc["check"] = _restic(["check", "--read-data-subset=2%", "--quiet"], log)
            log.write(f"== 끝 {datetime.now():%Y-%m-%d %H:%M:%S} " + " ".join(f"{k}={v}" for k, v in rc.items()) + "\n")
        return rc
    finally:
        lock.close()


def start(trigger="manual", day=None):
    """수집·백업을 자식 프로세스(python -m jarvis session-vault sync)로 시작한다. (HTTP 상태, 본문).
    원격이 보낸 데이터를 게이트웨이 프로세스 밖에서 풀어, 그쪽이 메모리를 다 써도 대시보드는 살아 있게 한다."""
    global _child
    with _run_lock:
        if _child is not None and _child.poll() is None or _locked():
            return 409, {"error": "이미 수집 중입니다"}
        if _legacy_running():
            return 409, {"error": "옛 session-vault 작업이 도는 중입니다"}
        code = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {k: v for k, v in os.environ.items() if k != SCHEDULED_ENV}
        if day:
            env[SCHEDULED_ENV] = day
        _child = subprocess.Popen([sys.executable, "-m", "jarvis", "session-vault", "sync"], cwd=code, env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=_child.wait, name="jarvis-session-vault", daemon=True).start()  # 좀비를 남기지 않게
        return 202, {"started": time.time(), "trigger": trigger}


def _schedule_at(now):
    m = SCHEDULE_RE.fullmatch(str(settings().get("schedule") or ""))
    return now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0) if m else None


def tick(now=None, root=None):
    """예약 시각이 지났고 그 날 예약 실행을 아직 안 했으면 시작한다. 마지막 예약일은 사본 폴더의 .jarvis-scheduled 에 둔다."""
    root = root or ROOT
    now = now or datetime.now()
    at = enabled() and _schedule_at(now)
    if not at or not at <= now < at + timedelta(seconds=SCHEDULE_WINDOW_S):
        return False
    mark = os.path.join(root, ".jarvis-scheduled")
    try:
        with open(mark, encoding="utf-8") as f:
            if f.read().strip() == f"{now:%Y-%m-%d}":
                return False
    except OSError:
        pass
    # 날짜는 자식이 잠금을 잡은 뒤 적는다(run). 그 전까지는 잠금·자식 확인으로 409 가 되어 다시 띄우지 않는다
    return start("schedule", f"{now:%Y-%m-%d}")[0] == 202


def schedule_loop(stop=None):
    stop = stop or threading.Event()
    while not stop.wait(60):
        try:
            tick()
        except Exception as e:  # 예약 하나의 실패가 루프를 멈추지 않게
            print(f"[jarvis] 세션 수집 예약 확인 실패: {type(e).__name__}: {e}", flush=True)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _error_line(v):
    """API 로 내보내는 오류는 마지막 줄 한 줄만(원격 traceback 의 경로 목록을 통째로 내보내지 않게)."""
    return (str(v).strip().splitlines() or [""])[-1][:ERROR_MAX]


def _run(path):
    """가장 최근 실행(마지막 `== 시작` 뒤)의 {name, started, ended, rc, results}."""
    with open(path, "rb") as f:
        f.seek(max(0, os.fstat(f.fileno()).st_size - LOG_TAIL))
        # \n 으로만 나눈다: splitlines 는 원격 오류 문자열 안의 U+2028 등에서도 줄을 나눠 가짜 '== 시작' 을 만들 수 있다
        lines = f.read().decode("utf-8", "replace").split("\n")
    starts = [n for n, s in enumerate(lines) if s.startswith("== 시작 ")]
    lines = lines[starts[-1]:] if starts else lines
    run = {"name": os.path.basename(path), "started": None, "ended": None, "rc": None}
    results = {}
    for s in lines:
        if s.startswith("== 시작 "):
            run["started"] = s[len("== 시작 "):].strip()
        elif s.startswith("== 끝 "):
            # == 끝 2026-11-15 04:30:17 sync=0 backup=0 check=0
            words = s[len("== 끝 "):].split()
            run["ended"] = " ".join(w for w in words if "=" not in w)
            run["rc"] = {k: int(v) for k, _, v in (w.partition("=") for w in words if "=" in w) if v.lstrip("-").isdigit()}
        else:
            dev, _, rest = s.partition(" ")
            if not rest.startswith("{"):
                continue
            try:
                r = json.loads(rest)
            except ValueError:
                continue
            if not isinstance(r, dict) or not isinstance(r.get("ok"), bool):
                continue
            failed = r.get("failed")
            results[dev] = {"ok": r["ok"], "files": _num(r.get("files")), "bytes": _num(r.get("bytes")),
                            "seconds": _num(r.get("seconds")), "failed": len(failed) if isinstance(failed, list) else 0,
                            "error": _error_line(r["error"]) if r.get("error") is not None else None}
    return run, results


def status(ids=None, root=None, now=None, full=True):
    """기기마다 마지막 성공 시각과 최근 실행 결과. ids 가 없으면 devices() 순서. 수집기 폴더가 없으면 available=False.
    full 이 아니면(viewers 가 아님) 오류의 따옴표 안 값(경로 등)을 가린다."""
    now = time.time() if now is None else now
    root = root or ROOT
    errors = []
    if ids is None:
        devs, errors = devices()
        ids = list(devs)
    running = _child is not None and _child.poll() is None or fcntl is not None and _locked(root)
    base = {"running": running, "progress": None,
            "schedule": settings().get("schedule") if SCHEDULE_RE.fullmatch(str(settings().get("schedule") or "")) else None,
            "stale_s": STALE_S}
    if not os.path.isdir(root):
        return {"available": False, "error": " · ".join(errors) or None, "run": None, "devices": [], **base}
    state, run, results = {}, None, {}
    try:
        with open(os.path.join(root, "state.json"), encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("객체가 아닙니다")
    except FileNotFoundError:
        state = {}
    except Exception as e:
        state = {}
        errors.append(f"state.json 을 읽지 못했습니다: {type(e).__name__}")
    logs = sorted(glob.glob(os.path.join(root, "logs", "nightly-[0-9]*.log")))
    if logs:
        try:
            run, results = _run(logs[-1])
        except OSError as e:
            errors.append(f"{os.path.basename(logs[-1])} 를 읽지 못했습니다: {type(e).__name__}")
    else:
        errors.append("nightly 로그가 없습니다")
    if running and run and not run["ended"]:
        base["progress"] = [len(results), len(ids)]
    rows = []
    for i in ids:
        r = results.get(i)
        if r and r["error"] and not full:
            r = {**r, "error": re.sub(r"'[^']*(?:'|$)|\"[^\"]*(?:\"|$)", "'…'", r["error"])}
        last_ok = _num(state.get(i))
        rows.append({"id": i, "last_ok": last_ok, "missing": last_ok is None,
                     "stale": last_ok is not None and now - last_ok > STALE_S, "last": r})
    return {"available": True, "error": " · ".join(errors) or None, "run": run, "devices": rows, **base}


USAGE = "사용법: python -m jarvis session-vault <sync|verify> [기기...]"


def cli(args):
    """core 에서 손으로: sync(백업까지) · verify(원본 sha256 대조). 기능이 꺼져 있으면 아무것도 하지 않는다."""
    if not args or args[0] not in ("sync", "verify"):
        print(USAGE, file=sys.stderr)
        return 1
    if not enabled() or not config.DEVICES:
        print("세션 수집이 꺼져 있습니다(core 의 설정 파일에 devices 와 [session_vault] enabled = true)", file=sys.stderr)
        return 1
    devs, errors = devices()
    for e in errors:
        print(f"설정 오류: {e}", file=sys.stderr)
    names = args[1:] or list(devs)
    unknown = [n for n in names if n not in devs]
    if unknown:
        print(f"모르는 기기: {unknown}", file=sys.stderr)
        return 1
    if args[0] == "sync":
        try:
            rc = run(names, echo=print)
        except Busy as e:
            print(str(e), file=sys.stderr)
            return 1
        return 1 if any(rc.values()) else 0
    failed = False
    for n in names:
        r = verify(n, *devs[n])
        print(n, json.dumps(r, ensure_ascii=False), flush=True)
        failed = failed or not r["ok"]
    return 1 if failed else 0
