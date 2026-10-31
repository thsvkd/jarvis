"""Read-only health report for AI harnesses and tools.

설치 여부·버전, PATH 의 중복 설치, 은퇴한 훅의 잔존(드리프트), 하네스 설정의 평문 시크릿 위치,
lock 밖의 스킬을 보고한다. 아무것도 쓰거나 지우지 않는다(SPEC 불변식). 시크릿은 키 이름과
파일 위치만 찍고 값은 절대 출력하지 않는다.

exit: 0 = 문제 없음, 1 = 경고가 하나 이상, 2 = 카탈로그를 읽지 못했거나 점검 중 예기치 못한 오류.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"
FRAGMENT = HERE / "settings.json"
RECEIPT = Path(".claude") / ".claude-code-setup.json"

# 하네스가 사용자 설정을 두는 파일. 인증 전용 파일(auth.json, .credentials.json)은 원래 자격 증명을
# 담는 곳이라 넣지 않는다 — 여기서 찾는 것은 '설정에 섞여 들어간' 평문 값이다.
CONFIG_FILES = (
    ".claude/settings.json",
    ".claude.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".grok/config.toml",
    ".gemini/settings.json",
    ".config/opencode/opencode.json",
    ".config/opencode/opencode.jsonc",
    ".copilot/mcp-config.json",
)

# 시크릿 패턴. 모두 group(1) = 보고할 이름, group(2) = 값(출력하지 않음).
SECRET_WORDS = r"(?:token|secret|passw(?:or)?d|api[_-]?key|apikey|access[_-]?key|private[_-]?key|authorization|bearer|credentials?)"
SECRET_RES = (
    # JSON("key": "v")·TOML(key = "v", 인라인 테이블 포함). 키가 시크릿 단어로 '끝나야' 한다 —
    # bearer_token_env_var(변수 이름), claudeCodeFirstTokenDate(날짜), tokenizer 는 시크릿이 아니다.
    re.compile(r"""(?<![A-Za-z0-9_.-])["']?([A-Za-z0-9_.-]*?""" + SECRET_WORDS + r""")["']?\s*[:=]\s*["']([^"'\n]*)["']""", re.IGNORECASE),
    # 값이 Bearer 토큰이면 키 이름과 상관없다: AUTH = "Bearer x", "Authorization: Bearer x"(헤더 인자)
    re.compile(r"""["']?([A-Za-z0-9_.-]+)["']?\s*[:=]\s*["']Bearer\s+([^"'\s]*)""", re.IGNORECASE),
    re.compile(r"""["']([A-Za-z0-9-]+):\s*Bearer\s+([^"'\s]*)""", re.IGNORECASE),
    # URL 쿼리(?token=x), CLI 인자(--api-key=x, "--token", "x"), 문자열 안의 NAME=x(docker -e)
    re.compile(r"""[?&]([A-Za-z0-9_-]*(?:token|key|secret|passw(?:or)?d))=([^&"'\s]+)""", re.IGNORECASE),
    re.compile(r"""--([A-Za-z0-9-]*(?:token|api-?key|secret|passw(?:or)?d))(?:=|["']\s*,\s*["'])([^"'\s]+)""", re.IGNORECASE),
    re.compile(r"""["']([A-Za-z0-9_]*""" + SECRET_WORDS + r""")=([^"'\s]+)""", re.IGNORECASE),
    # 접속 문자열의 비밀번호: postgres://user:pass@host
    re.compile(r"""([A-Za-z][A-Za-z0-9+.-]*://)[^:/@\s"']+:([^@/\s"']+)@"""),
)
# 값 전체가 환경 변수 참조이면 평문이 아니다: ${X}, $X, {env:X}, %X%. 중간의 $ 는 참조로 보지 않는다.
ENV_REF_RE = re.compile(r"^(?:\$\{?\w+\}?|\{env:\w+\}|%\w+%)$")
# 대문자 변수 이름만 담긴 값(env_http_headers 의 "MY_SERVICE_TOKEN_VAR")은 참조다.
VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


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


def probe_env(scratch):
    """버전 확인용 환경. gemini·opencode 는 --version 만 불러도 홈에 상태 파일을 쓰므로,
    홈과 그 아래 설정·캐시 위치를 버리는 임시 디렉터리로 돌린다(SPEC: doctor 는 쓰지 않는다)."""
    env = dict(os.environ)
    # ponytail: 알려진 설정 경로 변수만 돌린다. 새 하네스가 다른 변수를 쓰면 여기에 더한다.
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
                 "CLAUDE_CONFIG_DIR", "CODEX_HOME", "npm_config_cache"):
        env[name] = scratch
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        env[name] = os.path.join(scratch, name.lower())
    return env


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


def version_of(path, args, env, scratch):
    """출력은 파이프가 아니라 파일로 받는다. 파이프는 남은 손자가 잡고 있으면 타임아웃 뒤에도 막힌다."""
    out_fd, out_path = tempfile.mkstemp(dir=scratch, suffix=".out")
    err_fd, err_path = tempfile.mkstemp(dir=scratch, suffix=".err")
    try:
        try:
            proc = subprocess.Popen([path] + list(args), stdout=out_fd, stderr=err_fd,
                                    stdin=subprocess.DEVNULL, env=env, cwd=scratch,
                                    start_new_session=(os.name != "nt"))
        finally:
            os.close(out_fd)
            os.close(err_fd)
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            return "version unknown (timed out)"
        if code != 0:
            return "version unknown"
        for name in (out_path, err_path):
            with open(name, encoding="utf-8", errors="replace") as handle:
                lines = [line.strip() for line in handle if line.strip()]
            if lines:
                return lines[0]
    except OSError:
        pass
    return "version unknown"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def tilde(path, home):
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def inventory(catalog, env, scratch):
    """카탈로그 항목마다 {id, kind, found: PATH 순서의 실행 파일들, version, hint: 없을 때의 설치 명령}."""
    plat = platform_key()
    rows = []
    for entry in (e for e in catalog if e.get("kind") in ("harness", "tool")):
        found = locations(entry["detect"]["bin"])
        hints = entry.get("install", {}).get(plat) or []
        rows.append({"id": entry["id"], "kind": entry["kind"], "found": found,
                     "version": version_of(found[0], entry["detect"].get("version_args", []), env, scratch) if found else None,
                     "hint": None if found else (hints[0]["cmd"] if hints else "no install method for this platform")})
    return rows


def check_installs(catalog, warn, env, scratch):
    rows = inventory(catalog, env, scratch)
    for kind, title in (("harness", "Harnesses"), ("tool", "Tools")):
        print("── " + title + " ──")
        for row in (r for r in rows if r["kind"] == kind):
            found = row["found"]
            if not found:
                print("ℹ️  {}: not installed; install with: {}".format(row["id"], row["hint"]))
                continue
            print("✅ {} {} ({})".format(row["id"], row["version"], found[0]))
            if len(found) > 1:
                warn("{}: duplicate installs on PATH; first wins: {}; also: {}".format(
                    row["id"], found[0], ", ".join(found[1:])))


def check_hook_drift(home, warn):
    receipt = read_json(home / RECEIPT)
    if not isinstance(receipt, dict):
        return
    print("── Hook drift ──")
    try:
        fragment = FRAGMENT.read_text(encoding="utf-8")
    except OSError:
        fragment = ""
    # 영수증이 은퇴로 기록한 훅 + 예전에 설치했지만 지금 조각(res/settings.json)에 없는 훅.
    retired = set(receipt.get("retired_hooks") or [])
    retired.update(n for n in receipt.get("hook_files") or [] if n.endswith(".py") and n not in fragment)
    for relative in CONFIG_FILES:
        path = home / relative
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in sorted(retired):
            if name in text:
                warn("retired hook {} is still referenced in {}".format(name, tilde(path, home)))


def check_secrets(home, warn):
    print("── Plaintext secrets ──")
    for relative in CONFIG_FILES:
        path = home / relative
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            keys = []
            for pattern in SECRET_RES:
                for match in pattern.finditer(line):
                    value = match.group(2)
                    if value.lower().startswith("bearer "):
                        value = value[7:].strip()
                    if (len(value) >= 8 and not ENV_REF_RE.match(value) and not VAR_NAME_RE.match(value)
                            and match.group(1) not in keys):
                        keys.append(match.group(1))
            for key in keys:
                warn("plaintext secret: {}:{} key {} holds a literal value (value hidden); move it to an environment variable".format(
                    tilde(path, home), number, key))


def check_skills(home, warn):
    skills = home / ".agents" / "skills"
    if not skills.is_dir():
        return
    print("── Skills outside the lock ──")
    lock_path = home / ".agents" / ".skill-lock.json"
    lock = read_json(lock_path)
    if lock_path.exists() and not (isinstance(lock, dict) and isinstance(lock.get("skills"), dict)):
        warn("cannot read {}; skipped the skill check".format(tilde(lock_path, home)))
        return
    locked = set((lock or {}).get("skills") or {})
    for entry in sorted(skills.iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.name in locked:
            continue
        try:
            if not entry.is_dir():
                continue
            # 홈 경로 자체가 링크를 지나는 경우(macOS /var -> /private/var)를 오판하지 않게 부모의 실체와 비교한다.
            # junction 은 Python 3.8 의 is_symlink 로 보이지 않아 realpath 로 잡는다.
            link = " (link)" if os.path.realpath(entry) != os.path.join(os.path.realpath(skills), entry.name) else ""
        except OSError:
            # Windows 는 신뢰하지 않는 탑재 지점을 가리키는 junction 을 따라가지 못한다(WinError 448).
            warn("cannot inspect skill {} (left in place)".format(entry.name))
            continue
        warn("skill {}{} is not in .skill-lock.json (left in place)".format(entry.name, link))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only health report for AI harnesses and tools.")
    parser.add_argument("--home", default=str(Path.home()))
    args = parser.parse_args(argv)
    home = Path(args.home)

    catalog = read_json(CATALOG)
    if not isinstance(catalog, list):
        print("❌ cannot read {}".format(CATALOG), file=sys.stderr)
        return 2

    warnings = []

    def warn(message):
        warnings.append(message)
        print("⚠️  " + message)

    print("🔧 Doctor (read-only)")
    try:
        # 3.8·3.9 의 TemporaryDirectory 는 정리 실패(남은 손자가 잡은 파일)에 예외를 던져 나머지 점검을 막는다.
        scratch = tempfile.mkdtemp(prefix="ai-setup-doctor-")
        try:
            check_installs(catalog, warn, probe_env(scratch), scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        check_hook_drift(home, warn)
        check_secrets(home, warn)
        check_skills(home, warn)
    except Exception as error:  # 예외 메시지에 설정 내용이 섞일 수 있어 종류만 찍는다
        print("❌ doctor stopped on an unexpected {}; the report above is incomplete".format(type(error).__name__), file=sys.stderr)
        return 2
    if warnings:
        print("⚠️  {} warning(s) found; nothing was changed".format(len(warnings)))
        return 1
    print("✅ No problems found; nothing was changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
