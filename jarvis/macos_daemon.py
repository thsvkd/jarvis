"""macOS LaunchDaemon plist 생성. GUI 로그인 없이 도는 기기를 위해 LaunchAgent plist 를 시스템 도메인용으로 바꾼다.
deploy/macos-daemon.sh 가 부른다. 표준 라이브러리만 쓰고 config 를 읽지 않는다(전환 전 venv python 으로 돈다).

    python -m jarvis.macos_daemon render [--agent PATH]    plist 를 stdout 으로
    python -m jarvis.macos_daemon check [--agent PATH]     기준 plist 의 python·코드 확인
    python -m jarvis.macos_daemon port [--agent PATH]      게이트웨이 포트

--agent 를 주지 않으면 LaunchAgents 의 plist, LaunchAgents 밖으로 옮겨 둔 plist, deploy/jarvis.plist 템플릿 순으로 쓴다.
"""

import argparse
import os
import plistlib
import re
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


def _program(agent):
    """launchd 가 실제로 실행할 argv. Program 이 있으면 ProgramArguments[0] 은 argv[0] 일 뿐이다."""
    argv = list(agent.get("ProgramArguments") or [])
    if agent.get("Program"):
        argv[:1] = [agent["Program"]]
    if not argv:
        raise ValueError("plist 에 ProgramArguments 가 없습니다")
    return argv


def render(agent, user, group, home, log):
    """LaunchAgent plist(dict) → LaunchDaemon plist(dict). 사용자가 고친 나머지 값은 그대로 둔다."""
    env = dict(agent.get("EnvironmentVariables") or {})
    secrets = sorted(k for k in env if SECRET.search(k))
    if secrets:
        raise ValueError(f"비밀로 보이는 환경 변수를 누구나 읽는 plist 에 넣지 않습니다: {', '.join(secrets)}")
    if any(SECRET.search(a) for a in _program(agent)):  # 값이 들어 있을 수 있어 인자는 출력하지 않는다
        raise ValueError("ProgramArguments 에 비밀로 보이는 인자가 있어 누구나 읽는 plist 에 넣지 않습니다")
    env.pop("JARVIS_TRAY", None)  # GUI 세션이 없으면 AppKit 이 프로세스를 죽일 수 있다
    env.update(HOME=home, USER=user, LOGNAME=user, JARVIS_LOG=log)
    env.setdefault("LANG", "en_US.UTF-8")
    data = {k: v for k, v in agent.items() if k != "Program"}
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


def main(argv=None):
    import grp  # Windows 에는 없다. 테스트가 render 를 어디서나 부를 수 있게 여기서 읽는다
    import pwd
    p = argparse.ArgumentParser(prog="python -m jarvis.macos_daemon")
    p.add_argument("command", choices=("render", "check", "port"))
    p.add_argument("--agent", help="기준 LaunchAgent plist")
    args = p.parse_args(argv)
    home = os.path.expanduser("~")
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
