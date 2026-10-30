"""Linux deb 로 설치된 Orca 를 공식 GitHub 릴리스로 올린 뒤 전역 스킬을 갱신한다.

electron-updater 는 headless serve 와 deb 에서 설치를 거부한다. jarvis 는 serve 밖이므로
패키지를 받아 해시가 맞는 뒤에만 orca-serve 를 멈추고, Orca 가 쓰는 것과 같은 명령으로 설치한 뒤 다시 띄운다.

    sudo -n /usr/bin/apt install -y -- <검증한 deb>

앱이 이미 최신이어도 스킬은 갱신한다. 설치 도중 jarvis 가 죽으면 다음 기동 때 서비스를 다시 띄운다.
Mac·Windows 는 공식 앱의 로컬 업데이터를 쓴다. sudo 없는 Linux 도 같은 공식 deb 를 사용자 경로에 교체한다.
"""

import base64
import hashlib
import hmac
import json
import ntpath
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import psutil

from . import config, orca, orca_issue, orca_install, orca_rpc, orca_sessions
from .util import run

RELEASE = "https://github.com/stablyai/orca/releases"
HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
         "github-releases.githubusercontent.com"}
ARCH_YML = {"arm64": "latest-linux-arm64.yml", "amd64": "latest-linux.yml"}
MARK = os.path.join(config.STATE_DIR, "orca-update.json")
RESULT = os.path.join(config.STATE_DIR, "orca-update-result.json")
LOCK = os.path.join(config.STATE_DIR, "orca-update.lock")  # 프로세스 사이(gateway 버튼과 모듈 CLI apply)의 배타 잠금
VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_lock = orca_issue._lock  # 발급과 갱신은 같은 재진입 잠금으로 배타적으로 시작한다.
job = None  # {state, phase, error, from_version, to_version, app, skills, started_at, finished_at}


class _GitHub(urllib.request.HTTPRedirectHandler):
    """릴리스 다운로드는 GitHub 와 그 릴리스 저장소로만 따라간다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname
        if host not in HOSTS:
            raise urllib.error.URLError(f"릴리스 리다이렉트 호스트가 아닙니다: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_open = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GitHub).open


def version_key(v):
    """1.4.220 같은 릴리스 번호. 그 형식이 아니면 None."""
    if not isinstance(v, str) or not VER_RE.match(v):
        return None
    return tuple(int(p) for p in v.split("."))


def parse_manifest(text, arch):
    """electron-builder yml 에서 그 아키텍처 deb 하나만. (version, {name, sha512, size})."""
    version, cur, files = None, None, []
    for line in text.splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip("'\"")
        elif line.startswith("  - url:"):
            if cur:
                files.append(cur)
            cur = {"url": line.split(":", 1)[1].strip()}
        elif cur and line.startswith("    sha512:"):
            cur["sha512"] = line.split(":", 1)[1].strip()
        elif cur and line.startswith("    size:"):
            try:
                cur["size"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                cur["size"] = None
        elif line and not line.startswith(" "):
            if cur:
                files.append(cur)
                cur = None
    if cur:
        files.append(cur)
    if version_key(version) is None:
        raise RuntimeError("릴리스 매니페스트의 버전이 이상합니다")
    name = f"orca-ide_{version}_{arch}.deb"
    meta = next((f for f in files if f.get("url") == name and f.get("sha512") and isinstance(f.get("size"), int) and f["size"] > 0), None)
    if not meta:
        raise RuntimeError(f"매니페스트에 {name} 이 없습니다")
    return version, {"name": name, "sha512": meta["sha512"], "size": meta["size"]}


def check_file(path, sha512_b64, size):
    """파일이 매니페스트의 크기·sha512(base64) 와 같은지."""
    h, got = hashlib.sha512(), 0
    with open(path, "rb") as f:
        while buf := f.read(1 << 20):
            got += len(buf)
            if got > size:
                raise RuntimeError("deb 가 매니페스트보다 큽니다")
            h.update(buf)
    digest = base64.b64encode(h.digest()).decode()
    if got != size or not hmac.compare_digest(digest, sha512_b64):
        raise RuntimeError("deb 해시가 매니페스트와 다릅니다")


def _expected(target):
    """모듈 apply 가 승인한 목표 버전과 지금 매니페스트가 다르면 아무것도 바꾸지 않는다(승인한 계획 밖의 설치를 막는다)."""
    expect = (job or {}).get("expect")
    if expect and target != expect:
        raise RuntimeError(f"계획한 Orca {expect} 와 지금 최신 {target} 이 다릅니다. 다시 계획하세요")


def execute(ops):
    """한 기기 갱신. 앱을 올릴 때만 서비스를 멈추고, 멈추면 실패해도 다시 띄운다. 스킬은 그 다음이다."""
    ops.phase("manifest")
    target, meta = ops.manifest()
    _expected(target)
    installed = ops.installed()
    if version_key(installed) is None or version_key(target) is None:
        raise RuntimeError(f"버전을 비교할 수 없습니다 ({installed} → {target})")
    if hasattr(ops, "note"):
        ops.note(installed, target)
    app = "current"
    if version_key(installed) < version_key(target):
        if hasattr(ops, "idle"):
            ops.idle()
        ops.phase("download")
        path = ops.download(target, meta)
        try:
            if not ops.sudo_ok():
                raise RuntimeError("비밀번호 없는 sudo 가 없어 패키지를 설치하지 못했습니다")
            if hasattr(ops, "prepare"):
                ops.prepare(path)
            if hasattr(ops, "idle"):
                ops.idle()
            ops.phase("install")
            ops.mark_stopped()
            try:
                ops.stop()
                ops.install(path)
            finally:
                # stop 이 프로세스를 내렸다면 설치 실패여도 서비스를 되살린다. 띄우기에 실패하면 표시를 남겨 다음 기동·다음 시도가 다시 띄운다.
                ops.start()
            got = ops.installed()
            if got != target:
                raise RuntimeError(f"설치 뒤 패키지 버전이 {got} 입니다")
            if ops.running_version() != target:
                raise RuntimeError(f"패키지는 {target} 인데 다시 뜬 Orca 가 그 버전을 보고하지 않습니다")
            if hasattr(ops, "verify_sessions"):
                ops.verify_sessions()
            ops.mark_started()
            app = "updated"
        finally:
            ops.cleanup(path)
    ops.phase("skills")
    try:
        ops.skills()
    except Exception as e:
        return {"app": app, "skills": "failed", "from_version": installed, "to_version": target,
                "error": f"{type(e).__name__}: {e}"[:400]}
    return {"app": app, "skills": "updated", "from_version": installed, "to_version": target, "error": None}


def snapshot():
    with _lock:
        return dict(job) if job else None


def check():
    """설치본과 공식 릴리스만 비교한다. 패키지를 받거나 서비스를 멈추지 않는다."""
    ok, why = _supported()
    installed = None
    try:
        installed = _installed()
    except Exception:
        installed = None
    body = {"supported": ok, "installed": installed, "latest": None, "current": False, "reason": why}
    try:
        target, _meta = Live().manifest()
    except Exception as e:
        return {**body, "error": str(e)[:300]}
    if version_key(installed) is None or version_key(target) is None:
        return {**body, "latest": target,
                "error": f"버전을 비교할 수 없습니다 ({installed} → {target})"}
    return {**body, "latest": target,
            "current": version_key(installed) >= version_key(target), "error": None}


RECEIPT = "~/.claude/.claude-code-setup.json"


def _s4u_server():
    """Windows 에서 Orca 모듈(modules/orca)이 구성한 S4U 서버의 영수증 기록. 없으면 None.
    그 서버는 설치 폴더 밖 복사본에서 돌아 설치기가 끝내지 않고, 러너가 터미널이 없을 때 새 판으로 옮긴다."""
    if sys.platform != "win32":
        return None
    try:
        with open(os.path.expanduser(RECEIPT), encoding="utf-8") as f:
            rec = (json.load(f).get("tool_installs") or {}).get("orca_server")
    except (OSError, ValueError, AttributeError):
        return None
    return rec if isinstance(rec, dict) and rec.get("service_manager") == "task-scheduler" and rec.get("state") == "configured" else None


def _windows_tools():
    """modules/orca/orca_update_windows.py(ai-setup 에서 옮긴 S4U 갱신). 형제 모듈을 import 하므로 그 폴더를 경로에 둔다."""
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules", "orca")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import orca_update_windows
    return orca_update_windows


def _desktop_app_open():
    """설치 폴더의 데스크톱 앱(Orca.exe)이 떠 있는지. 설치기 `/S` 가 이 앱을 닫는다.
    S4U 서버 복사본(app\\build-*)과 터미널 호스트(daemon-host-profiles)는 설치기가 끝내지 않으므로 세지 않는다.
    권한이 높은 프로세스(S4U 서버)의 exe 는 일반 권한에서 읽지 못해 None 이 되고, 세지 않는다."""
    app = os.path.normcase(os.path.join(orca_install.profile()["root"], "Orca.exe"))
    return any(os.path.normcase(p.info.get("exe") or "") == app for p in psutil.process_iter(["exe"]))


def _s4u_switch():
    """설치 뒤 S4U 서버가 새 설치본의 복사본으로 옮겨 갔는지(AI-55c). 미뤘으면 그 이유를 남긴다."""
    server, tools = _s4u_server(), _windows_tools()
    if not server:
        return {}
    state = tools.orca_server.running_runner_state(server["service_root"]) or {}
    identity = tools.orca_server.app_identity(orca_install.profile()["root"])
    if identity and ntpath.basename(state.get("app") or "") == identity:  # Windows 경로(\ 와 / 모두)
        return {"server": "switched"}
    terminals = tools.orca_server.live_terminal_count(tools.orca_server.server_query_cli(server), server["server_profile"])
    reason = tools.orca_server.terminal_count_problem(terminals) if terminals != 0 else "it did not switch in time"
    return {"server": "pending", "server_reason": reason + "; it switches by itself once it has no live terminals"}


def _run_s4u():
    """S4U 서버가 있는 Windows: 공식 설치기를 `/S --updated` 로 실행하고 서버 전환을 보고한 뒤 소유 스킬을 갱신한다."""
    before = _installed()
    if _desktop_app_open():
        raise orca_sessions.Deferred("Orca 데스크톱 앱이 열려 있어 갱신을 보류합니다(설치기가 앱을 닫습니다). 앱을 닫은 뒤 다시 하세요")
    _set(phase="install", from_version=before)
    if _windows_tools().update_orca_windows(expect=(job or {}).get("expect")) != "ok":
        raise RuntimeError("Windows Orca 갱신 실패(진행 로그를 보세요)")
    after = _installed()
    result = {"app": "current" if after == before else "updated", "from_version": before, "to_version": after, "error": None,
              **_s4u_switch()}
    _set(phase="skills")
    try:
        orca_install.skills()
        result["skills"] = "updated"
    except Exception as e:
        result.update(skills="failed", error=f"{type(e).__name__}: {e}"[:400])
    return result


_sudo_checked = [0.0, None]  # (시각, 비밀번호 없는 sudo 가 되는지)


def _sudo_probe():
    """status·plan·check 는 자주 부른다: sudo 가 안 되는 기기의 auth 로그에 실패가 쌓이지 않게 한 시간 묶는다.
    이 묶음은 프로세스 안에서만 유효하다(gateway 는 묶이지만 모듈 CLI 의 plan·status 는 부를 때마다 확인한다).
    실제 설치 직전의 확인(Live.sudo_ok)은 묶지 않는다."""
    if _sudo_checked[1] is None or time.monotonic() - _sudo_checked[0] > 3600:
        _sudo_checked[:] = [time.monotonic(), run(["/usr/bin/true"], sudo=True, timeout=10)[0] == 0]
    return _sudo_checked[1]


def _supported():
    """이 프로세스가 deb Orca 를 올리고 다시 띄울 수 있는지. (가능, 이유)."""
    if _s4u_server():
        return True, None
    p = orca_install.profile()
    if not os.path.isfile(p["cli"]):
        return False, "공식 Orca 설치본의 CLI 를 찾지 못했습니다"
    if p["kind"] in ("mac-app", "windows-user"):
        try:
            status = orca_rpc.call("updater.getStatus")
            support = status.get("support") or {}
            if p["kind"] == "mac-app" and status.get("appVersion") == "1.4.224" \
                    and support.get("installMode") == "supervised-headless-serve":
                return False, "Orca 1.4.224 macOS 서버 업데이터의 대기 충돌로 공식 서명 패키지를 통한 최초 이전이 필요합니다"
            return (True, None) if support.get("automatic") else (False, "Orca 공식 업데이터 사용 불가: " + str(support.get("reason")))
        except Exception:
            return False, "Orca 를 실행하면 공식 업데이터를 사용할 수 있습니다"
    try:
        with open(os.path.join(p["resources"], "package-type"), encoding="utf-8") as f:
            kind = f.read().strip()
    except OSError:
        kind = ""
    if kind != "deb":
        return False, "이 Orca 는 deb 설치가 아니라 여기서 올리지 않습니다"
    m = orca_issue.manager(pairing=False)
    if not m or m.get("kind") != "systemd":
        return False, "orca-serve 사용자 유닛이 없어 설치 뒤 다시 띄울 수 없습니다"
    arch = _arch()
    if arch not in ARCH_YML:
        return False, f"지원하지 않는 아키텍처입니다 ({arch})"
    if p["kind"] == "linux-deb" and not _sudo_probe():
        return False, "공식 deb 설치에는 비밀번호 없는 sudo 가 필요합니다"
    if p["kind"] == "linux-user-deb" and not os.access(orca_install.USER_DEB, os.W_OK):
        return False, "사용자 Orca 설치 폴더에 쓰기 권한이 없습니다"
    return True, None


def _arch():
    rc, out, _ = run(["dpkg", "--print-architecture"], timeout=10)
    return out.strip() if rc == 0 else {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}.get(platform.machine(), "")


def _installed():
    return orca_install.installed()


def _cli():
    p = orca_install.profile()["cli"]
    if os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    raise RuntimeError("orca-ide CLI 를 찾지 못했습니다")


def _port():
    d = orca.find_dir()
    rt = orca._read(d, "orca-runtime.json") if d else None
    ws = next((t.get("endpoint") for t in (rt or {}).get("transports") or [] if isinstance(t, dict) and t.get("kind") == "websocket"), "")
    m = re.search(r":(\d+)/?$", ws) if isinstance(ws, str) else None
    return int(m.group(1)) if m else 6768


class Live:
    """이 기기에서 실제로 받고, 멈추고, 설치한다."""

    def __init__(self):
        self.path = None
        self.manager = None
        self.backup = None
        self.verified = False
        self.sessions = orca_sessions.Sessions()
        self.protocols = None
        self.meta = None
        self.target = None
        self.proof = None
        self.transaction = uuid.uuid4().hex

    def idle(self):
        orca_install.validate_runtime()
        self.sessions.capture(fence=self.protocols is not None)
        if self.protocols is not None:
            self.sessions.compatible(self.protocols)
        self.sessions.before_stop()

    def phase(self, name):
        _set(phase=name)

    def manifest(self):
        p = orca_install.profile()
        arch = _arch() if p["kind"].startswith("linux-") else None
        yml = p.get("manifest") or ARCH_YML[arch]
        url = f"{RELEASE}/latest/download/{yml}"
        try:
            with _open(urllib.request.Request(url, headers={"User-Agent": "jarvis-orca-update"}), timeout=60) as r:
                text = r.read(1 << 20).decode()
        except Exception as e:
            raise RuntimeError(f"릴리스 매니페스트를 받지 못했습니다: {e}") from e
        if arch:
            version, meta = parse_manifest(text, arch)
        else:
            match = re.search(r"^version:\s*['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", text, re.M)
            if not match:
                raise RuntimeError("공식 매니페스트의 버전이 이상합니다")
            version = match.group(1)
            arch = platform.machine()
            name = (f"Orca-{version}-{'arm64-' if arch in ('arm64', 'aarch64') else ''}mac.zip"
                    if p["kind"] == "mac-app" else "orca-windows-setup.exe")
            entries = re.findall(r"^  - url:\s*(\S+)\s*\n    sha512:\s*(\S+)\s*\n    size:\s*(\d+)\s*$", text, re.M)
            meta = next(({"name": n, "sha512": h, "size": int(s)} for n, h, s in entries if n == name), None)
            if not meta:
                raise RuntimeError("공식 매니페스트에 이 플랫폼의 패키지가 없습니다")
            self.target, self.meta = version, meta
            return version, meta
        meta["url"] = f"{RELEASE}/download/v{version}/{meta['name']}"
        self.target, self.meta = version, meta
        return version, meta

    def prepare(self, path):
        # 다운로드 무결성 검증 뒤, 서비스 중지 전에 들어오는 빌드의 계약을 읽는다.
        with tempfile.TemporaryDirectory(prefix="jarvis-orca-compat-") as directory:
            rc, out, err = run(["dpkg-deb", "-x", path, directory], timeout=180)
            if rc:
                raise RuntimeError((err or out).strip()[:200] or "새 Orca 패키지 검사 실패")
            if orca_install.asar_version(os.path.join(directory, "opt/Orca/resources")) != self.target:
                raise RuntimeError("새 Orca 패키지의 버전이 매니페스트와 다릅니다")
            self.protocols = orca_sessions.read_contract(os.path.join(directory,
                "opt/Orca/resources/app.asar.unpacked/out/shared/local-build-compatibility-contract.js"))
        self.sessions.compatible(self.protocols)

    def prepare_native(self):
        if self.sessions.terminals:
            self.protocols = orca_install.native_contract(self.target, self.meta)
            self.sessions.compatible(self.protocols)
        else:
            # 최종 조사에서 새 PTY 가 나타나면 계약을 읽기 전에는 진행하지 않는다.
            self.protocols = set()

    def verify_sessions(self):
        self.proof = self.sessions.verify()
        self.verified = True

    def installed(self):
        return _installed()

    def download(self, target, meta):
        os.makedirs(config.STATE_DIR, exist_ok=True)
        dest = os.path.join(config.STATE_DIR, meta["name"])
        tmp = dest + ".partial"
        req = urllib.request.Request(meta["url"], headers={"User-Agent": "jarvis-orca-update", "Accept": "application/octet-stream"})
        end, got, h = time.monotonic() + 1200, 0, hashlib.sha512()
        try:
            with _open(req, timeout=120) as r, open(tmp, "wb") as f:
                while buf := r.read(1 << 20):
                    if time.monotonic() > end:
                        raise TimeoutError("deb 다운로드가 너무 오래 걸립니다")
                    got += len(buf)
                    if got > meta["size"]:
                        raise RuntimeError("deb 가 매니페스트보다 큽니다")
                    h.update(buf)
                    f.write(buf)
        except Exception:
            _unlink(tmp)
            raise
        digest = base64.b64encode(h.digest()).decode()
        if got != meta["size"] or not hmac.compare_digest(digest, meta["sha512"]):
            _unlink(tmp)
            raise RuntimeError("deb 해시가 매니페스트와 다릅니다")
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
        self.path = dest
        return dest

    def sudo_ok(self):
        if orca_install.profile()["kind"] == "linux-user-deb":
            return os.access(orca_install.USER_DEB, os.W_OK)
        return run(["/usr/bin/true"], sudo=True, timeout=15)[0] == 0

    def mark_stopped(self):
        self.sessions.before_stop()
        self.manager = orca_issue.manager(pairing=False)
        _write_mark({"stopped": True, "started": False, "at": time.time(),
                     "transaction": self.transaction,
                     "native": orca_install.profile()["kind"] in ("mac-app", "windows-user"), "target_version": self.target,
                     "sessions": {"terminals": self.sessions.terminals, "daemons": self.sessions.daemons}})

    def mark_started(self):
        _clear_mark(self.transaction)

    def stop(self):
        orca_issue._stop(self.manager)
        if not orca_issue._wait(lambda: not orca_issue._port_open(_port()), 40):
            raise RuntimeError("Orca 가 포트를 놓지 않아 패키지를 설치하지 않았습니다")

    def install(self, path):
        path = os.path.realpath(path)
        name = os.path.basename(path)
        if os.path.dirname(path) != os.path.realpath(config.STATE_DIR) or not name.startswith("orca-ide_") or not name.endswith(".deb"):
            raise RuntimeError("설치할 deb 경로가 이상합니다")
        if orca_install.profile()["kind"] == "linux-user-deb":
            self.backup = orca_install.install_user_deb(path)
            return
        if os.path.isfile("/usr/bin/apt"):
            args = ["/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "/usr/bin/apt", "install", "-y", "--", path]
        elif os.path.isfile("/usr/bin/dpkg"):
            args = ["/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "/usr/bin/dpkg", "-i", "--", path]
        else:
            raise RuntimeError("apt 도 dpkg 도 없습니다")
        rc, out, err = run(args, sudo=True, timeout=900)
        if rc != 0:
            raise RuntimeError((err or out).strip()[:300] or f"패키지 설치 실패 rc={rc}")

    def start(self):
        orca_issue._start(self.manager or orca_issue.manager(pairing=False))

    def note(self, installed, target):
        _set(from_version=installed, to_version=target)

    def running_version(self):
        """serve 가 뜬 뒤 런타임이 버전을 보고할 때까지. 패키지 교체 직후에는 1~2분 넘게 not_running 이다."""
        cli = _cli()
        end = time.monotonic() + 240
        last = "응답 없음"
        while time.monotonic() < end:
            rc, out, err = run([cli, "status", "--json"], timeout=25)
            try:
                body = json.loads(out or "")
                rt = ((body.get("result") or body).get("runtime") or {})
                ver = rt.get("appVersion")
                last = f"state={rt.get('state')} version={ver} rc={rc}"
                if version_key(ver):
                    return ver.strip() if isinstance(ver, str) else ver
            except ValueError:
                last = (err or out or "").strip()[:120]
            time.sleep(3)
        raise RuntimeError(f"Orca 가 240초 안에 버전을 보고하지 않았습니다 ({last})")

    def skills(self):
        orca_install.skills()

    def cleanup(self, path):
        _unlink(path)
        if self.backup and self.verified:
            shutil.rmtree(self.backup)
            self.backup = None
        self.path = None


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _write_mark(body):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    fd = os.open(MARK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(body, f)


def _clear_mark(transaction):
    with _lock:
        try:
            with open(MARK, encoding="utf-8") as f:
                current = json.load(f)
        except FileNotFoundError:
            return
        if current.get("transaction") != transaction:
            raise RuntimeError("다른 Orca 업데이트의 복구 기록은 지우지 않습니다")
        os.remove(MARK)


def _set(**kw):
    global job
    with _lock:
        job = {**job, **kw}


def recover():
    """복구도 갱신·페어링과 같은 시작 게이트를 차지한다. 검증 전에는 기록을 남긴다."""
    global job
    with _lock:
        if job and job.get("state") == "running":
            return
        try:
            with open(MARK, encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, ValueError):
            return
        if not (isinstance(mark, dict) and mark.get("stopped") and not mark.get("started")):
            return
        if not _lock_file():  # 다른 프로세스(모듈 apply)가 갱신하는 중이면 그 MARK 는 끊긴 갱신이 아니다
            return
        # 구형 기록에도 트랜잭션을 부여해 새 작업의 기록을 지우는 일을 막는다.
        if not mark.get("transaction"):
            mark["transaction"] = uuid.uuid4().hex
            _write_mark(mark)
        job = {"state": "running", "phase": "recovery", "error": None,
               "started_at": time.time(), "to_version": mark.get("target_version")}
    error, proof = None, None
    try:
        if not mark.get("native"):
            orca_install.recover_user_deb()
            orca_issue._start(orca_issue.manager(pairing=False) or {"kind": "systemd"})
        if isinstance(mark.get("sessions"), dict):
            expected = mark.get("target_version") if mark.get("native") else _installed()
            orca_install.wait_running(expected or _installed())
            sessions = orca_sessions.Sessions()
            sessions.terminals = mark["sessions"].get("terminals", {})
            sessions.daemons = mark["sessions"].get("daemons", {})
            proof = sessions.verify()
        _clear_mark(mark["transaction"])
        actual = _installed()
        if mark.get("target_version") and actual != mark["target_version"]:
            error = f"이전 업데이트는 완료되지 않았습니다. 실행 가능한 Orca {actual} 와 기존 세션을 복구했습니다"
        print("[jarvis] 끊긴 Orca 업데이트 뒤 orca-serve 를 다시 띄웠다", flush=True)
    except Exception as e:
        error = f"Orca 복구 검증 실패: {type(e).__name__}: {e}"[:400]
        print(f"[jarvis] 끊긴 Orca 업데이트 뒤 재기동 실패: {type(e).__name__}: {e}", flush=True)
    finally:
        _unlock_file()
        with _lock:
            job = {**job, "state": "failed" if error else "done", "error": error, "sessions": proof,
                   "finished_at": time.time()}


_lock_fd = None


def _lock_file():
    """프로세스 사이의 배타 잠금(fcntl.flock / msvcrt.locking). 잡은 프로세스가 죽으면 OS 가 푼다:
    남은 잠금을 회수하는 단계가 없으므로 두 프로세스가 함께 잡는 경쟁도 없다. 파일은 지우지 않는다."""
    global _lock_fd
    if _lock_fd is not None:
        return False
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _lock_fd = fd
    return True


def _unlock_file():
    global _lock_fd
    fd, _lock_fd = _lock_fd, None
    if fd is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)  # POSIX 의 flock 은 닫으면 풀린다


def start(expect=None):
    """(http 상태, 응답). 받기·설치는 스레드에서 돈다. expect 를 주면 그 버전이 지금 최신일 때만 바꾼다."""
    global job
    ok, why = _supported()
    if not ok:
        return 409, {"error": why}
    with _lock:
        if os.path.exists(MARK):
            return 409, {"error": "이전 Orca 업데이트의 복구·세션 검증 기록이 남아 있습니다. 복구 결과를 먼저 확인하세요"}
        if job and job["state"] == "running":
            return 409, {"error": "이미 업데이트하고 있습니다", "update": dict(job)}
        if orca_issue.job and orca_issue.job.get("state") == "running":
            return 409, {"error": "페어링 코드 발급이 끝난 뒤에 업데이트하세요", "issue": orca_issue.job}
        from . import self_update  # 받은 뒤 재시작하면 이 작업이 끊긴다
        if self_update._lock.locked():
            return 409, {"error": "jarvis 갱신이 끝난 뒤에 업데이트하세요"}
        if not _lock_file():
            return 409, {"error": "다른 프로세스가 Orca 를 업데이트하고 있습니다"}
        job = {"state": "running", "phase": "manifest", "error": None, "from_version": None, "to_version": None,
               "app": None, "skills": None, "started_at": time.time(), "finished_at": None, "expect": expect}
        try:
            threading.Thread(target=_run, name="jarvis-orca-update", daemon=True).start()
        except Exception:
            _unlock_file()
            raise
        return 202, {"update": dict(job)}


def _run():
    try:
        _run_job()
    finally:
        _unlock_file()  # 어떤 이유로 끝나든 프로세스 사이 잠금을 푼다


def _run_job():
    global job
    error, result, ops, deferred = None, {}, None, False
    try:
        ops = Live()
        if _s4u_server():
            result = _run_s4u()
        elif orca_install.profile()["kind"] in ("mac-app", "windows-user"):
            target, _ = ops.manifest()
            _expected(target)
            before = _installed()
            if version_key(before) is None or version_key(target) is None:
                raise RuntimeError("Orca 버전을 비교할 수 없습니다")
            target = max(before, target, key=version_key)
            ops.note(before, target)
            result = orca_install.native_update(before, target, ops.phase, ops.idle,
                                               prepare=ops.prepare_native, verify=ops.verify_sessions, checkpoint=ops.mark_stopped)
        else:
            result = execute(ops)
        if ops.proof is not None:
            result["sessions"] = ops.proof
            ops.mark_started()
        error = result.get("error")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"[:400]
        deferred = isinstance(e, orca_sessions.Deferred)
    finally:
        if ops is not None:
            try:
                ops.sessions.release()
            except Exception as e:
                error = error or f"세션 생성 차단 해제 실패(5분 뒤 자동 해제): {type(e).__name__}"
    with _lock:
        done = {**(job or {}), **result, "state": "failed" if error else "done", "error": error, "deferred": deferred,
                "finished_at": time.time()}
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        fd = os.open(RESULT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(done, f)
    except OSError:
        pass
    tail = json.dumps({k: (result or {}).get(k) for k in ("app", "skills", "from_version", "to_version")}, ensure_ascii=False)
    print(f"[jarvis] Orca 업데이트 {'실패: ' + error if error else '완료'} {tail}", flush=True)
    # 기다리는 쪽(모듈 apply)은 state 가 바뀌면 표준 출력을 되돌린다: 기록·로그·잠금 해제를 모두 마친 뒤에 바꾼다
    _unlock_file()
    with _lock:
        job = done


def ensure_idle():
    """기존 호출부용: 열린 PTY 대신 보존 가능성을 검사한다."""
    sessions = orca_sessions.Sessions()
    sessions.capture()
    return sessions
