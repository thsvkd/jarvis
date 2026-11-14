"""macOS LaunchDaemon plist 생성. GUI 로그인 없이 도는 기기를 위해 LaunchAgent plist 를 시스템 도메인용으로 바꾼다.
deploy/macos-daemon.sh 가 부른다. 표준 라이브러리만 쓰고 config 를 읽지 않는다(전환 전 venv python 으로 돈다).

    python -m jarvis.macos_daemon render [--agent PATH]    plist 를 stdout 으로
    python -m jarvis.macos_daemon check [--agent PATH]     기준 plist 의 python·코드 확인
    python -m jarvis.macos_daemon port [--agent PATH]      게이트웨이 포트
    python -m jarvis.macos_daemon store [--report]         Claude 계정 저장 방식(hswap doctor). 0 파일, 3 파일이 아님·모름, 4 Claude Code 없음,
                                                           5 hswap 이 아직 없음(새 기기: 게이트웨이가 설치한 뒤 옮길 일이 남는다)
                                                           --report 는 설치기 끝의 보고라 늘 0 이다
    python -m jarvis.macos_daemon reconcile                파일 저장 방식이면 hswap 의 reconcile 예약(gui)을 설치한다. 늘 0

--agent 를 주지 않으면 LaunchAgents 의 plist, LaunchAgents 밖으로 옮겨 둔 plist, deploy/jarvis.plist 템플릿 순으로 쓴다.
"""

import argparse
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tomllib

LABEL = "dev.jarvis"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join("Library", "Logs", "jarvis.log")  # 홈 아래. tray.LOG 와 같은 파일
DEFAULT_PORT = "9300"  # config.PORT 의 기본값
# launchd 는 Standard*Path 를 root 로 열 수 있어 사용자 폴더를 줄 수 없다. 사용자 권한으로 바뀐 뒤 셸이 로그를 연다.
# exec 이라 최종 명령줄은 `<python> -m jarvis` 그대로다(core 의 SSH 갱신이 ps 로 이 끝을 확인한다)
WRAPPER = 'exec "$@" >>"$JARVIS_LOG" 2>&1 </dev/null'
# /Library/LaunchDaemons 의 plist 는 누구나 읽는다
# shortcut: 이름과 인자만 보므로 평범한 이름에 든 비밀은 못 잡는다. 더 잡아야 하면 값의 형태까지 본다
SECRET = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|APIKEY|COOKIE|(^|_)(PASS|KEY|PAT)($|_)|AUTH$", re.I)
# 이 키만 daemon 으로 옮긴다. Sockets·MachServices·UserName 같은 나머지 키는 root 가 읽는 plist 에 들어가면 안 된다
KEEP = {"Label", "ProgramArguments", "WorkingDirectory", "EnvironmentVariables", "RunAtLoad", "KeepAlive", "ThrottleInterval",
        "ExitTimeOut", "ProcessType", "SoftResourceLimits", "HardResourceLimits", "Nice"}
REPLACED = {"Program", "StandardOutPath", "StandardErrorPath"}  # 아래에서 대신 쓰므로 버려도 알리지 않는다
# hswap claude doctor(0.3.0, claude_swap/doctor.py run_doctor) 의 problems 코드 → 화면 문구. 모르는 코드는 코드 그대로 보인다
PROBLEMS = {"not_file_store": "저장 방식이 파일이 아닙니다", "keychain_item_present": "키체인에 Claude 로그인이 있습니다(로그인 세션에서 reconcile 이 정리합니다)",
            "file_missing": "자격 증명 파일이 없습니다", "file_mode_insecure": "자격 증명 파일을 다른 사용자가 읽을 수 있습니다(0600 이 아님)",
            "slot_without_backup": "백업이 없는 슬롯이 있습니다", "session_profiles": "세션 프로필(sessions/)이 남아 있습니다",
            "profile_env": "CLAUDE_CONFIG_DIR 같은 프로필 환경 변수가 있습니다", "reconcile_service_missing": "reconcile 예약이 없습니다",
            # 사람이 골라야 한다: reconcile 은 증거로만 정하고 시각으로 고르지 않는다
            "reconcile_ambiguous": "키체인과 파일 중 어느 쪽이 새 로그인인지 알 수 없습니다. 로그인 세션에서 어느 쪽이 최근에 쓰였는지 확인하고 "
                                   "hswap claude migrate-store --to file --keep file|keychain 로 직접 고르세요",
            "keepalive_ahead_too_short": "keepalive 예약의 갱신 여유가 너무 짧습니다. 다시 설치하세요(hswap claude keepalive --install-service)",
            # hswap 은 jarvis tick 을 보지 못한다. tick 이 대신 도는 기기에서는 claude_accounts.status 가 이 문제를 빼고 그 사실을 보인다
            "keepalive_service_missing": "keepalive 예약이 없습니다. Claude Code 가 스스로 토큰을 갱신하면 키체인과 갈라질 수 있습니다"
                                         "(설치: hswap claude keepalive --install-service --ahead 7200)"}
LOGINWINDOW = "/Library/Preferences/com.apple.loginwindow.plist"  # 자동 로그인 사용자(autoLoginUser). 누구나 읽는다
MIGRATE = "hswap claude migrate-store --to file --keep keychain"  # hswap 은 --keep 없는 --to file 을 거절한다. 화면(usage.js)도 같은 문자열을 쓴다
HSWAP_TIMEOUT_S = 60
HINT_MAX = 300  # doctor 의 lastReconcileHint(사람용 영문)를 화면에 보일 최대 길이
# hswap 0.3.0 의 migrate-store 는 옮겼지만 설치한 예약이 다음 로그인 때 적재되면 3 으로 끝난다
MIGRATE_EXIT = "종료 코드 3 도 성공입니다(옮겼고, 예약은 다음 로그인 때 적재됩니다)"


def _program(agent):
    """launchd 가 실제로 실행할 argv. Program 이 있으면 ProgramArguments[0] 은 argv[0] 일 뿐이다."""
    argv = list(agent.get("ProgramArguments") or [])
    if agent.get("Program"):
        argv[:1] = [agent["Program"]]
    if not argv:
        raise ValueError("plist 에 ProgramArguments 가 없습니다")
    return argv


def render(agent, user, group, home, log):
    """LaunchAgent plist(dict) → LaunchDaemon plist(dict). KEEP 의 값은 사용자가 고친 그대로 두고, 나머지 키는 버리고 stderr 로 알린다."""
    env = dict(agent.get("EnvironmentVariables") or {})
    secrets = sorted(k for k in env if SECRET.search(k))
    if secrets:
        raise ValueError(f"비밀로 보이는 환경 변수를 누구나 읽는 plist 에 넣지 않습니다: {', '.join(secrets)}")
    if any(SECRET.search(a) for a in _program(agent)):  # 값이 들어 있을 수 있어 인자는 출력하지 않는다
        raise ValueError("ProgramArguments 에 비밀로 보이는 인자가 있어 누구나 읽는 plist 에 넣지 않습니다")
    env.pop("JARVIS_TRAY", None)  # GUI 세션이 없으면 AppKit 이 프로세스를 죽일 수 있다
    env.update(HOME=home, USER=user, LOGNAME=user, JARVIS_LOG=log)
    env.setdefault("LANG", "en_US.UTF-8")
    dropped = sorted(set(agent) - KEEP - REPLACED)
    if dropped:
        print(f"LaunchDaemon plist 에 넣지 않는 키를 버립니다: {', '.join(dropped)}", file=sys.stderr)
    data = {k: v for k, v in agent.items() if k in KEEP}
    data.update(UserName=user, GroupName=group, InitGroups=True, EnvironmentVariables=env,
                ProgramArguments=["/bin/sh", "-c", WRAPPER, "sh", *_program(agent)],
                RunAtLoad=True, KeepAlive=True, StandardOutPath="/dev/null", StandardErrorPath="/dev/null")
    return data


def template(home):
    """deploy/jarvis.plist 를 install.sh 4단계와 같게 치환한 plist(dict)."""
    share = os.path.join(home, ".local", "share", "jarvis")
    app = os.path.join(share, "app")
    with open(os.path.join(HERE, "deploy", "jarvis.plist"), encoding="utf-8") as f:
        text = f.read()
    for old, new in (("/opt/homebrew/bin/python3", os.path.join(share, "venv", "bin", "python")),
                     ("/Users/USER/Workspace/jarvis", app), ("/Users/USER/.local/share/jarvis/app", app),
                     ("/Users/USER", home)):
        text = text.replace(old, new)
    return plistlib.loads(text.encode("utf-8"))


def base(path, home):
    """렌더 기준 plist(dict). path 를 주면 그 파일만 읽는다."""
    agent = os.path.join(home, "Library", "LaunchAgents", LABEL + ".plist")
    kept = os.path.join(home, ".local", "share", "jarvis", LABEL + ".plist.agent")
    path = path or next((p for p in (agent, kept) if os.path.isfile(p)), None)
    if not path:
        return template(home)
    with open(path, "rb") as f:
        return plistlib.load(f)


def problems(data):
    """이 plist 로 띄울 수 없는 이유 목록."""
    found = []
    python = _program(data)[0]
    if not os.access(python, os.X_OK):
        found.append(f"python 을 실행할 수 없습니다: {python}")
    wd = data.get("WorkingDirectory")
    if not (isinstance(wd, str) and os.path.isfile(os.path.join(wd, "jarvis", "__init__.py"))):
        found.append(f"작업 폴더에 jarvis 코드가 없습니다: {wd}")
    return found


def port(data, home):
    """config 와 같은 우선순위: plist 의 JARVIS_PORT > 설정 파일 > 기본값."""
    env = data.get("EnvironmentVariables") or {}
    if env.get("JARVIS_PORT"):
        return str(env["JARVIS_PORT"])
    try:
        with open(env.get("JARVIS_CONFIG") or os.path.join(home, ".config", "jarvis", "config.toml"), "rb") as f:
            return str(tomllib.load(f).get("port", DEFAULT_PORT))
    except (OSError, ValueError):
        return DEFAULT_PORT


def summary(body, settings=None):
    """hswap claude doctor --json(schemaVersion 1) → {store, problems, keychain_item_present, keepalive_loaded, keepalive_domain, reconcile_installed,
    reconcile_loaded, keychain_backups·last_reconcile_hint·keepalive_ahead(0.3.0 의 뒤 수정에서 생긴 필드. 없으면 None)}.
    없는 필드·다른 모양은 모름(None·[])으로 둔다. doctor 를 모르는 옛 hswap 은 settings({키: 값}, hswap claude config --json)의
    credentials.store 만 쓴다(값이 없으면 hswap 기본값 auto). doctor 가 오류로 답했어도(error 봉투) 같다. 둘 다 없으면 None."""
    if not answered(body):
        if not settings:
            return None
        store = settings.get("credentials.store")
        return {"store": store if store in ("auto", "file") else "auto", "problems": [], "keychain_item_present": None,
                "keepalive_loaded": None, "keepalive_domain": None, "reconcile_installed": None, "reconcile_loaded": None, "keychain_backups": None,
                "last_reconcile_hint": None, "keepalive_ahead": None}
    flag = lambda d, k: d.get(k) if isinstance(d.get(k), bool) else None
    obj = lambda k: body.get(k) if isinstance(body.get(k), dict) else {}
    word = lambda v, pat: v if isinstance(v, str) and re.fullmatch(pat, v) else None
    raw = body.get("problems") if isinstance(body.get("problems"), list) else []
    codes = [word(p.get("code") if isinstance(p, dict) else p, r"[a-z_]{1,40}") for p in raw]
    return {"store": word(body.get("store"), r"[a-z]{1,16}"), "problems": [c for c in codes if c][:20],
            "keychain_item_present": flag(body, "keychainItemPresent"), "keepalive_loaded": flag(obj("keepaliveService"), "loaded"),
            "keepalive_domain": word(obj("keepaliveService").get("domain"), r"[a-z]{1,16}"), "reconcile_installed": flag(obj("reconcileService"), "installed"),
            "reconcile_loaded": flag(obj("reconcileService"), "loaded"),
            "keychain_backups": n if isinstance(n := body.get("keychainBackups"), int) and not isinstance(n, bool) and n >= 0 else None,
            # 사람용 문자열: 제어 문자를 빼고 한 줄로 잘라 둔다(화면은 이스케이프해 보인다)
            "last_reconcile_hint": " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", h).split())[:HINT_MAX] or None
            if isinstance(h := body.get("lastReconcileHint"), str) else None,
            "keepalive_ahead": a if isinstance(a := obj("keepaliveService").get("ahead"), (int, float)) and not isinstance(a, bool) and a > 0 else None}


def answered(body):
    """doctor 가 보고서로 답했는가(오류 봉투 {schemaVersion, error} 는 아니다)."""
    return isinstance(body, dict) and body.get("schemaVersion") == 1 and not body.get("error")


def _hswap(exe, *args):
    """hswap claude <args> --json 의 JSON 객체. 실행하지 못했거나 HSWAP_TIMEOUT_S 를 넘기거나 JSON 이 아니면 None(문제가 있으면 doctor 는 0 이 아닌 코드로 끝난다).
    새 세션으로 띄워 시간 초과 때 hswap 이 띄운 자식까지 함께 끝낸다(hswap_usage._run 과 같은 정리)."""
    try:
        proc = subprocess.Popen([exe, "claude", *args, "--json"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, errors="replace", start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=HSWAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # 이미 끝났거나 macOS 가 좀비 그룹에 EPERM 을 준다
            pass
        proc.kill()
        proc.wait()
        proc.stdout.close()
        return None
    try:
        body = json.loads(out)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _tools(home):
    """(claude 실행 파일 또는 None, hswap 실행 파일 또는 None). hswap 은 게이트웨이(util.which_local)와 같이 PATH 다음에 ~/.local/bin 에서 찾는다.
    테스트(JARVIS_DAEMON_TEST=1)는 PATH·시스템 폴더를 보지 않는다(deploy/macos-daemon.sh 의 재정의와 같은 조건)."""
    local = os.path.join(home, ".local", "bin")
    test = os.environ.get("JARVIS_DAEMON_TEST") == "1"
    dirs = [local] if test else [local, "/opt/homebrew/bin", "/usr/local/bin"]
    found = lambda d, n: os.path.join(d, n) if os.access(os.path.join(d, n), os.X_OK) else None
    return next((p for d in dirs if (p := found(d, "claude"))), None), (None if test else shutil.which("hswap")) or found(local, "hswap")


def inspect(home):
    """(Claude Code 가 있는가, summary() 또는 None)."""
    claude, hswap = _tools(home)
    if not claude:
        return False, None
    if not hswap:
        return True, None
    body = _hswap(hswap, "doctor")
    if answered(body):
        return True, summary(body)
    cfg = _hswap(hswap, "config") or {}
    return True, summary(None, {s["key"]: s.get("value") for s in cfg.get("settings") or [] if isinstance(s, dict) and isinstance(s.get("key"), str)})


def auto_login(path=LOGINWINDOW):
    """macOS 자동 로그인이 켜져 있는가. 읽지 못하면 꺼진 것으로 본다."""
    try:
        with open(path, "rb") as f:
            user = plistlib.load(f).get("autoLoginUser")
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException):
        return False
    return isinstance(user, str) and bool(user)


def store(home, report=False):
    """store 명령. 반환은 종료 코드(모듈 docstring)."""
    has_claude, d = inspect(home)
    if not has_claude:
        print("Claude Code 가 없어 Claude 계정 저장 방식을 검사하지 않습니다")
        return 0 if report else 4
    now = (d or {}).get("store")
    print("Claude 계정 저장 방식: " + ("파일" if now == "file" else f"키체인({now})" if now else "확인하지 못함(hswap 이 없거나 답하지 않았습니다)"))
    for p in (d or {}).get("problems", []):
        print(f"  문제: {PROBLEMS.get(p, p)}({p})")
    if now == "file":
        return 0
    if d is None and not _tools(home)[1] and not report:  # 새 기기: hswap 은 게이트웨이가 처음 돌 때 설치한다. 거절하지 않고 남은 일로 알린다
        print("hswap 이 아직 없습니다. 게이트웨이가 hswap 을 설치하면(몇 분) 키체인이 열린 곳(화면 세션, 또는 security unlock-keychain 을 실행한 셸)에서 "
              f"파일 저장 방식으로 옮기세요: {MIGRATE}")
        return 5
    if report:
        # 테스트(JARVIS_DAEMON_TEST=1)만 자동 로그인 설정 파일을 바꾼다
        lw = os.environ.get("JARVIS_LOGINWINDOW", LOGINWINDOW) if os.environ.get("JARVIS_DAEMON_TEST") == "1" else LOGINWINDOW
        if not auto_login(lw):
            print("주의: 자동 로그인이 꺼져 있고 Claude 계정이 키체인에 있습니다. 재부팅 뒤 로그인 전에는 Claude 사용량·전환이 되지 않습니다")
            print(f"  키체인이 열린 곳(화면 세션, 또는 security unlock-keychain 을 실행한 셸)에서 파일 저장 방식으로 옮기면 됩니다: {MIGRATE}")
        return 0
    print(f"Claude 계정 저장 방식이 파일이 아닙니다(지금: {now or '확인하지 못함'}). LaunchDaemon 은 로그인 세션 밖에서 돌아 키체인을 읽지 못하므로,\n"
          "이대로 옮기면 Claude 사용량·계정 전환이 되지 않습니다. 먼저 키체인이 열린 곳(그 기기의 화면 세션, 또는 security unlock-keychain 을\n"
          "실행한 셸)에서 파일 저장 방식으로 옮기세요:\n"
          f"  {MIGRATE} --dry-run   # 계획 확인\n"
          f"  {MIGRATE}   # {MIGRATE_EXIT}\n"
          "그래도 키체인 저장 방식 그대로 옮기려면 --allow-keychain-store 를 붙입니다", file=sys.stderr)
    return 3


def reconcile(home):
    """파일 저장 방식이면 hswap 의 reconcile 예약(gui LaunchAgent, 키체인에 로그인이 다시 생기면 로그인 세션에서 정리)을 설치한다.
    이미 있거나 파일 저장 방식이 아니면 하지 않는다. 실패해도 0 이다(daemon 전환은 이미 끝났다)."""
    _, d = inspect(home)
    if not d or d.get("store") != "file" or d.get("reconcile_installed") is True:
        if d and d.get("reconcile_installed") is True:
            print("reconcile 예약이 이미 있습니다")
        return 0
    try:
        rc = subprocess.run([_tools(home)[1], "claude", "reconcile", "--install-service", "--json"], capture_output=True, timeout=60,
                            stdin=subprocess.DEVNULL).returncode
    except (OSError, subprocess.SubprocessError):
        rc = None
    if rc == 0:
        print("reconcile 예약을 설치했습니다")
    elif rc == 3:  # hswap keepalive 예약과 같은 관례: GUI 세션이 없어 다음 로그인 때 적재된다
        print("reconcile 예약을 설치했습니다. 다음 GUI 로그인 때 적재됩니다")
    else:
        print(f"reconcile 예약을 설치하지 못했습니다(종료 코드 {rc}). 그 기기의 화면 세션에서 실행하세요: hswap claude reconcile --install-service")
    return 0


def main(argv=None):
    import grp  # Windows 에는 없다. 테스트가 render 를 어디서나 부를 수 있게 여기서 읽는다
    import pwd
    p = argparse.ArgumentParser(prog="python -m jarvis.macos_daemon")
    p.add_argument("command", choices=("render", "check", "port", "store", "reconcile"))
    p.add_argument("--agent", help="기준 LaunchAgent plist")
    p.add_argument("--report", action="store_true", help="store: 설치기 끝의 보고(거절하지 않는다)")
    args = p.parse_args(argv)
    home = os.path.expanduser("~")
    if args.command == "store":
        return store(home, args.report)
    if args.command == "reconcile":
        return reconcile(home)
    try:
        data = base(args.agent, home)
        if args.command == "render":
            # $HOME 은 autofs 경로(/home/<user>)일 수 있다. daemon 은 부팅 직후 autofs 에 기대면 안 된다
            pw = pwd.getpwuid(os.getuid())
            out = render(data, pw.pw_name, grp.getgrgid(os.getgid()).gr_name, pw.pw_dir, os.path.join(pw.pw_dir, LOG_PATH))
            sys.stdout.buffer.write(plistlib.dumps(out, sort_keys=False))
        elif args.command == "port":
            print(port(data, home))
        else:
            found = problems(data)
            for x in found:
                print(x, file=sys.stderr)
            return 1 if found else 0
    except Exception as e:  # 깨진 plist(ExpatError 등)도 같은 안내로 끝낸다. 호출한 스크립트는 아무것도 바꾸기 전이다
        print(f"LaunchDaemon plist 를 만들지 못했습니다: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
