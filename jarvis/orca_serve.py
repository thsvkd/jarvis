"""Linux·macOS Orca 서버 서비스(docs/SPEC.md): systemd 사용자 유닛 orca-serve, LaunchAgent com.stablyai.orca.serve.

Python 러너 없이 공식 설치본의 CLI 가 `serve --supervise` 로 돈다. 여기서는 기존 서비스의 형태를 판정하고(같으면 받아들인다),
없으면 새로 쓰고, uninstall 때 우리가 쓴 그대로인 파일만 지운다. 실행 중인 서버를 몰래 멈추거나 고치지 않는다.
페어링 플래그(--no-pairing 등)는 열린 문제 O7 이 정해질 때까지 형태 판정에서 빼고, 새로 쓰는 서비스에는 넣지 않는다.
"""

import getpass
import hashlib
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile

from . import orca_issue
from .util import run

LABEL = "com.stablyai.orca.serve"
PORT = 6768
PAIRING_FLAGS = {"--no-pairing", "--serve-no-pairing", "--mobile-pairing", "--serve-mobile-pairing"}


def service_path(plat=None):
    plat = plat or sys.platform
    if plat == "darwin":
        return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
    return os.path.expanduser("~/.config/systemd/user/orca-serve.service")


def command(cli, address):
    return [cli, "serve", "--supervise", "--port", str(PORT), "--pairing-address", address]


def default_profile(plat=None):
    """Orca 가 ORCA_USER_DATA_PATH 없이 쓰는 서버 프로필."""
    if (plat or sys.platform) == "darwin":
        return os.path.expanduser("~/Library/Application Support/orca")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "orca")


def form(cli, profile, plat=None):
    """(missing|same|different, 이유). 실행 파일·프로필은 realpath 로 비교한다(심링크 ~/.local/bin/orca-ide 도 같은 설치본이다)."""
    m = orca_issue.manager(pairing=False)
    if not m:
        if os.path.lexists(service_path(plat)):
            return "different", "서비스 파일은 있지만 서비스 관리자에 적재되지 않았습니다"
        return "missing", None
    argv = [a for a in m["argv"] if a not in PAIRING_FLAGS]
    if not argv or os.path.realpath(argv[0]) != os.path.realpath(cli):
        return "different", "서비스가 공식 설치본의 CLI 를 바로 실행하지 않습니다(러너·감싸개)"
    if argv[1:3] != ["serve", "--supervise"]:
        return "different", "서비스가 serve --supervise 로 돌지 않습니다"
    served = (m.get("env") or {}).get("ORCA_USER_DATA_PATH") or default_profile(plat)
    if os.path.realpath(served) != os.path.realpath(profile):
        return "different", "서비스가 다른 프로필을 서비스합니다: " + served
    return "same", None


def adopted_record(cli, profile, plat=None):
    """같은 형태의 기존 서비스를 받아들인 기록. 실제로 적재된 파일·label 을 남긴다(LaunchAgent 이름이 다를 수 있다)."""
    m = orca_issue.manager(pairing=False) or {}
    record = {"state": "configured", "adopted": True, "cli": cli, "server_profile": str(profile),
              "service_manager": "launchd" if (plat or sys.platform) == "darwin" else "systemd-user",
              "service_path": m.get("plist") or service_path(plat)}
    if m.get("label"):
        record["service_label"] = m["label"]
    return record


def _shell(argv):
    """표준 입력 없이(대화형 셸의 질문에 걸리지 않게) 사용자 환경 그대로 실행한다. (rc, stdout)."""
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, errors="replace")
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def login_env():
    """새 서비스에 넣을 PATH·SHELL. Orca 터미널이 하네스(claude·codex 등)를 찾으려면 로그인 셸의 PATH 가 필요하다.
    셸은 jarvis 의 $SHELL 이 아니라 계정의 로그인 셸이다(서비스로 도는 jarvis 에는 SHELL 이 없거나 다를 수 있다)."""
    import pwd  # Linux·macOS 에만 있다(Windows 에서도 이 모듈은 import 할 수 있어야 한다)
    shell = pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    marker = "__jarvis_path__"
    for flags in ("-ilc", "-lc"):  # zsh 는 .zshrc 를 대화형에서만 읽는다
        rc, out = _shell([shell, flags, f'printf "\\n{marker}%s\\n" "$PATH"'])
        line = next((ln[len(marker):] for ln in out.splitlines() if ln.startswith(marker)), "") if rc == 0 else ""
        if line:
            return {"PATH": line, "SHELL": shell}
    raise RuntimeError("로그인 셸의 PATH 를 읽지 못했습니다")


def document(cli, address, profile, env, plat=None):
    if (plat or sys.platform) == "darwin":
        return plistlib.dumps({"Label": LABEL, "ProgramArguments": command(cli, address), "RunAtLoad": True, "KeepAlive": True,
                               "EnvironmentVariables": {**env, "ORCA_USER_DATA_PATH": str(profile)}}).decode()
    # systemd 는 값 안의 % 를 지정자로, ExecStart 의 $ 를 변수로 읽는다
    quote = lambda v: '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'  # noqa: E731
    run_quote = lambda v: quote(v).replace("$", "$$")  # noqa: E731
    return "\n".join([
        "[Unit]",
        "Description=Orca server (serve --supervise)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "ExecStart=" + " ".join(run_quote(a) for a in command(cli, address)),
        *(f"Environment={quote(f'{k}={v}')}" for k, v in {**env, "ORCA_USER_DATA_PATH": str(profile)}.items()),
        "KillMode=process",  # 서버만 멈춘다. 터미널 데몬과 PTY 는 따로 남는다(갱신이 세션을 잇는 전제)
        "Restart=on-failure",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def activate_argv(plat=None):
    if (plat or sys.platform) == "darwin":
        return [["launchctl", "bootstrap", f"gui/{os.getuid()}", service_path(plat)]]
    return [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "orca-serve"]]


def write(cli, address, profile, env, on_record, plat=None, record=None):
    """새 서비스 파일을 쓰고 띄운다. 이미 파일이 있으면 덮어쓰지 않는다.
    띄우기 전에 configuring 기록을 on_record 로 남긴다: 활성화가 실패해도 다음 실행이 우리 파일(다이제스트가 같은)을
    알아보고 활성화만 이어서 한다(record 로 그 기록을 넘긴다). 성공하면 configured 기록을 남긴다."""
    path = service_path(plat)
    resume = bool(record and record.get("state") == "configuring" and record.get("service_path") == path
                  and os.path.isfile(path) and not os.path.islink(path) and _digest(path) == record.get("service_digest"))
    if not resume:
        if os.path.lexists(path):
            raise RuntimeError("Orca 서버 서비스 파일이 이미 있어 덮어쓰지 않습니다: " + path)
        text = document(cli, address, profile, env, plat)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".orca-serve-", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        record = {"state": "configuring", "cli": cli, "server_profile": str(profile),
                  "service_manager": "launchd" if (plat or sys.platform) == "darwin" else "systemd-user",
                  "service_path": path, "service_digest": hashlib.sha256(text.encode()).hexdigest()}
        on_record(record)
    for argv in activate_argv(plat):
        rc, out, err = run(argv, timeout=60)
        if rc:
            raise RuntimeError(f"{shlex.join(argv)} 실패: {(err or out).strip()[:200]}")
    record = {**record, "state": "configured"}
    on_record(record)
    return record


def remove(record, dry_run=False, plat=None):
    """kept(받아들인 서비스는 지우지 않는다)·changed(우리가 쓴 뒤 바뀌었다)·removed. 받아들인 것은 기록만 지운다."""
    if record.get("adopted"):
        return "kept"
    path = record.get("service_path") or ""
    if path != service_path(plat):  # 손상된 영수증이 엉뚱한 파일을 가리켜도 지우지 않는다
        return "changed"
    if not os.path.lexists(path):
        return "removed"
    if os.path.islink(path) or _digest(path) != record.get("service_digest"):
        return "changed"
    if dry_run:
        return "removed"
    if (plat or sys.platform) == "darwin":
        steps = [["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]]
    else:
        steps = [["systemctl", "--user", "disable", "--now", "orca-serve"]]
    for argv in steps:
        run(argv, timeout=60)  # 이미 멈춘 서비스는 실패해도 된다
    os.remove(path)
    if (plat or sys.platform) != "darwin":
        run(["systemctl", "--user", "daemon-reload"], timeout=60)
    return "removed"


def linger():
    """systemd 사용자 매니저가 로그아웃 뒤에도 사는지. Linux 가 아니거나 모르면 None."""
    if not sys.platform.startswith("linux"):
        return None
    rc, out, _ = run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger"], timeout=10)
    return None if rc else out.strip() == "Linger=yes"
