#!/bin/sh
# jarvis 설치기(macOS·Linux): 게이트웨이를 설치해 띄우고 모듈(훅·셋업)을 적용한다. 다시 실행해도 된다.
# Windows 는 install.ps1 을 쓴다(README 의 운영 절).
#
#   curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --core <core 의 tailnet IP> [--daemon]
#
# git·Homebrew 가 없는 새 Mac 에서도 돈다: Python 은 uv 로 받은 venv(~/.local/share/jarvis/venv), 코드는 최신 릴리스 묶음.
# Linux 는 LaunchAgent 대신 systemd 사용자 유닛(deploy/jarvis.service, python 만 venv 로)을 쓴다.
# 코드는 ~/.local/share/jarvis/app 에 둔다(~/Workspace/jarvis 는 개발 저장소 자리). 1.8.0 이전 배치는 app 으로 옮긴다.
# 이미 있는 설정·LaunchAgent·유닛·코드는 덮어쓰지 않는다.
# 지우기: `sh -s -- --uninstall`. 서비스 등록과 실행본(app·venv·versions)만 지운다. 설정(config.toml)은 남기므로 다시 설치하면 이어진다.
set -eu

CORE=""
DAEMON=""  # --daemon: macOS 에서 LaunchAgent 대신 LaunchDaemon 으로 돈다(GUI 로그인이 없는 기기. README 의 macOS 절)
UNINSTALL=""  # --uninstall: 서비스 등록과 실행본(app·venv·versions)을 지운다. 설정·모듈이 쓴 것은 남긴다
while [ $# -gt 0 ]; do
  case "$1" in
    --core) CORE="${2:?--core 에 tailnet IP 를 주세요}"; shift 2 ;;
    --daemon) DAEMON=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "모르는 인자: $1" >&2; exit 1 ;;
  esac
done
[ -z "$UNINSTALL" ] || [ -z "$CORE$DAEMON" ] || { echo "--uninstall 은 다른 인자와 함께 쓰지 않습니다" >&2; exit 1; }

OS=$(uname -s)
case "$OS" in
  Darwin|Linux) ;;
  *) echo "macOS·Linux 만 지원합니다. Windows 는 install.ps1 을 쓰세요(README 의 운영 절)" >&2; exit 1 ;;
esac
if [ -n "$DAEMON" ] && [ "$OS" != Darwin ]; then
  echo "--daemon 은 macOS 전용입니다" >&2
  exit 1
fi

SHARE="$HOME/.local/share/jarvis"
APP="$SHARE/app"
OLD="$HOME/Workspace/jarvis"  # 1.8.0 이전 배치
CONFIG="$HOME/.config/jarvis/config.toml"
AGENT="$HOME/Library/LaunchAgents/dev.jarvis.plist"
DAEMON_PLIST=/Library/LaunchDaemons/dev.jarvis.plist
if [ "${JARVIS_DAEMON_TEST:-}" = 1 ]; then DAEMON_PLIST="${JARVIS_DAEMON_PLIST:-$DAEMON_PLIST}"; fi  # 재정의는 테스트에서만 받는다
UNIT="$HOME/.config/systemd/user/jarvis.service"
PY="$SHARE/venv/bin/python"
MANIFEST="${JARVIS_MANIFEST:-https://github.com/thsvkd/jarvis/releases/latest/download/manifest.json}"
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME"  # 이 셸과 자식(파이프의 python 등)이 옛 폴더를 작업 폴더로 쥐지 않게(relocate 가 사용 중으로 본다)

if [ -n "$UNINSTALL" ]; then
  if [ "$OS" = Darwin ]; then
    # 이 사용자의 LaunchDaemon 이면 sudo 가 필요한 되돌리기를 먼저 한다. 실패하면 도는 서비스 밑의 코드를 지우지 않고 멈춘다
    if [ -f "$DAEMON_PLIST" ]; then
      owner=$(python3 -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb"))["UserName"])' "$DAEMON_PLIST" 2>/dev/null) || owner=""
      if [ "$owner" = "$(id -un)" ]; then
        S="$APP/deploy/macos-daemon.sh"; [ -f "$S" ] || S="$SHARE/daemon-src/deploy/macos-daemon.sh"
        [ -f "$S" ] || { echo "LaunchDaemon 을 되돌릴 deploy/macos-daemon.sh 가 없습니다. 지우지 않고 멈춥니다" >&2; exit 1; }
        /bin/bash "$S" uninstall </dev/null || { echo "LaunchDaemon 을 되돌리지 못했습니다. 지우지 않고 멈춥니다" >&2; exit 1; }
      else
        echo "이 기기의 LaunchDaemon 은 다른 사용자(${owner:-알 수 없음})의 것이라 손대지 않습니다" >&2
      fi
    fi
    launchctl bootout "gui/$(id -u)/dev.jarvis" 2>/dev/null || true
    rm -f "$AGENT" "$SHARE/dev.jarvis.plist.agent"
  else
    systemctl --user disable --now jarvis 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  # 개발 저장소($OLD)와 옛 배치는 지우지 않는다
  rm -rf "$APP" "$SHARE/venv" "$SHARE/versions" "$SHARE/daemon-src" "$SHARE/current.json"
  echo "jarvis 서비스와 실행본을 지웠습니다. 남긴 것: $CONFIG(설정), 훅·셋업 모듈이 쓴 ~/.claude 설정, 하네스, Tailscale"
  exit 0
fi

if [ ! -f "$CONFIG" ] && [ -z "$CORE" ]; then
  echo "처음 설치에는 --core <core 의 tailnet IP> 가 필요합니다(이 기기의 사용량·모듈을 모아 볼 기기)" >&2
  exit 1
fi

# 1. Python 3.11+ 와 의존성(psutil, macOS 메뉴 막대 아이콘의 pystray·pillow)
if ! command -v uv >/dev/null 2>&1; then
  echo "uv 설치"
  curl -fsSL https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
[ -x "$PY" ] || uv venv --python 3.13 "$SHARE/venv"
if [ "$OS" = Darwin ]; then uv pip install --quiet --python "$PY" psutil pystray pillow
else uv pip install --quiet --python "$PY" psutil; fi

# 시스템 도메인의 dev.jarvis 는 기기에 하나다. plist 의 UserName 이 이 사용자일 때만 이 사용자의 daemon 으로 본다
MY_DAEMON=""
if [ "$OS" = Darwin ] && [ -f "$DAEMON_PLIST" ]; then
  owner=$("$PY" -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb"))["UserName"])' "$DAEMON_PLIST" 2>/dev/null) || owner=""
  if [ "$owner" = "$(id -un)" ]; then
    MY_DAEMON=1
  else
    echo "이 기기의 LaunchDaemon 은 다른 사용자(${owner:-알 수 없음})의 것이라 손대지 않습니다. 이 사용자는 LaunchAgent 로 설치합니다" >&2
    DAEMON=""
  fi
fi

# 2. 코드: 최신 릴리스 묶음. 이후 갱신은 core 의 jarvis 업데이트 버튼이 한다(versions/).
if [ "$OS" = Darwin ]; then REG="$AGENT"; else REG="$UNIT"; fi
# 개발 체크아웃(~/Workspace/jarvis/.git)으로 이미 등록된 기기는 그대로 둔다(받지도 옮기지도 않는다)
if [ ! -d "$APP" ] && [ -e "$OLD/.git" ] && [ -f "$REG" ]; then APP="$OLD"; fi
# LaunchDaemon 이 옛 폴더를 가리키는 기기도 같다: root 소유 등록은 고칠 수 없다
if [ ! -d "$APP" ] && [ -f "$OLD/jarvis/__init__.py" ] && [ -n "$MY_DAEMON" ]; then APP="$OLD"; fi
# 1.8.0 이전 배치(~/Workspace/jarvis 에 푼 실행본, .git 없음)로 등록된 기기: 옛 base 를 그대로 app 으로 옮긴다(relocate).
# 새 릴리스를 app 에 풀지 않는다(base 가 바뀌면 런처의 롤백 기준 previous=None → base 가 바뀐다)
RELOC=""
if [ "$APP" != "$OLD" ] && [ -f "$OLD/jarvis/__init__.py" ] && [ ! -e "$OLD/.git" ] && [ -f "$REG" ]; then RELOC=1; fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fetch() {  # 최신 릴리스를 sha256 으로 확인해 $1 에 푼다
  curl -fsSL "$MANIFEST" -o "$tmp/manifest.json"
  url=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$tmp/manifest.json")
  sum=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$tmp/manifest.json")
  echo "jarvis 받기: $url"
  curl -fsSL "$url" -o "$tmp/jarvis.tar.gz"
  if command -v sha256sum >/dev/null 2>&1; then echo "$sum  $tmp/jarvis.tar.gz" | sha256sum -c - >/dev/null
  else echo "$sum  $tmp/jarvis.tar.gz" | shasum -a 256 -c - >/dev/null; fi
  mkdir -p "$1"
  tar -xzf "$tmp/jarvis.tar.gz" -C "$1" --strip-components 1
}
if [ -n "$RELOC" ]; then
  # 옛 base 에는 relocate 가 없다. 이전은 새 코드(app 에 있으면 그것, 없으면 임시 폴더에 받은 릴리스)로 부른다
  if [ -f "$APP/jarvis/relocate.py" ]; then SRC="$APP"; else fetch "$tmp/src"; SRC="$tmp/src"; fi
elif [ ! -d "$APP" ]; then
  # 다 푼 뒤에만 app 이름을 준다(끊기면 남는 .app-tmp-* 는 relocate 가 치운다. 반쯤 푼 app 을 쓰지 않게)
  mkdir -p "$SHARE"
  part=$(mktemp -d "$SHARE/.app-tmp-XXXXXX")
  fetch "$part"
  chmod 755 "$part"
  mv "$part" "$APP"
fi

# 3. 설정
if [ ! -f "$CONFIG" ]; then
  mkdir -p "$(dirname "$CONFIG")"
  cat > "$CONFIG" <<EOF
port = 9300
bind = "0.0.0.0"
caddy = ""
tunnel = ""
core = ["$CORE"]
docker_sock = ""
EOF
  # Linux 는 기본 유닛 이름(docker.service·tailscaled.service 등)을 그대로 쓴다. macOS 는 launchd 데몬이 아닌 것을 끈다
  if [ "$OS" = Darwin ]; then
    printf '\n[infra_units]\ndocker = ""\ncontainerd = ""\ntailscale = ""\nssh = "com.openssh.sshd"\npm2 = ""\n' >> "$CONFIG"
  fi
fi

# 4. 서비스: macOS 는 LaunchAgent(로그인한 사용자 세션), Linux 는 systemd 사용자 유닛
if [ -n "$MY_DAEMON" ]; then
  # 에이전트를 새로 만들면 다음 GUI 로그인 때 두 번째 jarvis 가 같은 포트를 잡으려 한다
  [ -n "$DAEMON" ] || echo "LaunchDaemon 으로 등록된 기기라 LaunchAgent 를 만들지 않습니다(되돌리기: README 의 LaunchDaemon 모드 절)"
elif [ "$OS" = Darwin ]; then
  if [ ! -f "$AGENT" ]; then
    mkdir -p "$(dirname "$AGENT")"
    # 작업 폴더는 템플릿과 상관없이 app 이다(1.8.0 이전 릴리스의 템플릿은 ~/Workspace/jarvis 를 적는다)
    sed -e "s#/opt/homebrew/bin/python3#$PY#" -e "s#/Users/USER/Workspace/jarvis#$APP#" \
      -e "s#/Users/USER/.local/share/jarvis/app#$APP#" -e "s#/Users/USER#$HOME#g" "$APP/deploy/jarvis.plist" > "$AGENT"
  fi
  # --daemon 은 GUI 세션 없이(SSH) 실행할 수 있어 에이전트를 띄우지 않는다. 전환은 5단계 뒤에 한다
  [ -n "$DAEMON" ] || launchctl print "gui/$(id -u)/dev.jarvis" >/dev/null 2>&1 || launchctl bootstrap "gui/$(id -u)" "$AGENT"
else
  if [ ! -f "$UNIT" ]; then
    mkdir -p "$(dirname "$UNIT")"
    sed -e "s#^ExecStart=/usr/bin/python3 #ExecStart=$PY #" -e "s#^WorkingDirectory=.*#WorkingDirectory=$APP#" \
      "$APP/deploy/jarvis.service" > "$UNIT"
  fi
  # 로그아웃해도·부팅 때도 돌게 한다. 막혀 있으면(polkit 없음) 안내만 하고 계속한다
  loginctl enable-linger "$(id -un)" 2>/dev/null || echo "linger 를 켜지 못했습니다: sudo loginctl enable-linger $(id -un)" >&2
  # 사용자 버스가 없는 세션(su·컨테이너)이면 유닛만 남기고 안내한다. 로그인 세션에서 다시 실행하면 이어진다
  if systemctl --user daemon-reload; then
    systemctl --user is-active --quiet jarvis || systemctl --user enable --now jarvis
  else
    echo "systemd 사용자 세션에 닿지 못했습니다. 로그인 세션에서 다시 실행하세요" >&2
  fi
fi

# 5. 1.8.0 이전 배치를 쓰던 등록을 app 으로 옮긴다(옛 base 를 복사하고 등록을 고친다. 자동 이전 뒤 재실행이면 정리만)
if [ -n "$RELOC" ]; then
  relocate() { (cd "$SRC" && "$PY" -m jarvis.relocate "$@"); }
  before=$(cksum < "$REG")
  state=$(relocate --json | "$PY" -c 'import json, sys; r = json.load(sys.stdin); print("이전:", r["state"], r["detail"], file=sys.stderr); print(r["state"])') || state=failed
  # macOS 는 launchd 가 plist 를 다시 읽어야 app 에서 뜬다. 사용자가 실행한 설치기라 여기서 다시 읽힌 뒤 옛 폴더를 옮긴다
  # LaunchDaemon 등록이 남아 있으면 에이전트를 띄우지 않는다(두 벌이 같은 포트를 잡으려 한다)
  if [ "$OS" = Darwin ] && [ "$state" = pending_reload ] && [ -n "$MY_DAEMON" ]; then
    echo "LaunchDaemon 등록이 남아 있어 LaunchAgent 를 다시 읽지 않습니다" >&2
  elif [ "$OS" = Darwin ] && [ "$state" = pending_reload ]; then
    launchctl bootout "gui/$(id -u)/dev.jarvis" 2>/dev/null || true
    # bootout 직후의 bootstrap 은 작업이 아직 내려가는 중이면 실패할 수 있다(5: Input/output error). 몇 번 다시 한다
    for i in 1 2 3 4 5; do launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null && break; sleep 1; done
    if launchctl print "gui/$(id -u)/dev.jarvis" >/dev/null 2>&1; then relocate || true
    else echo "jarvis 를 다시 띄우지 못했습니다. 직접 실행하세요: launchctl bootstrap gui/$(id -u) $AGENT" >&2; fi
  # Linux: daemon-reload 로 유닛은 app 이지만 돌고 있는 게이트웨이는 옛 폴더에서 돈다(사용 중이라 registered).
  # 옛 base 에는 relocate 가 없어 아무도 치우지 않으므로, 이번에 등록을 바꾼 경우에만 다시 띄우고 한 번 더 부른다
  elif [ "$OS" != Darwin ] && [ "$state" = registered ] && [ "$before" != "$(cksum < "$REG")" ]; then
    systemctl --user try-restart jarvis || true
    port=$(sed -n 's/^port *= *\([0-9][0-9]*\).*/\1/p' "$CONFIG" | head -n 1)
    if command -v curl >/dev/null 2>&1; then
      i=0
      while [ $i -lt 20 ] && ! curl -fsS "http://127.0.0.1:${port:-9300}/api/health" >/dev/null 2>&1; do sleep 1; i=$((i + 1)); done
    else sleep 3; fi
    relocate || true
  fi
fi

[ -d "$APP" ] || APP="$OLD"  # 이전이 실패했으면 아직 옛 배치가 실행본이다

# --daemon: 이전(5단계)이 에이전트 plist 를 고친 뒤에 넘긴다. 전환하면 그 plist 가 LaunchAgents 밖으로 옮겨진다
if [ -n "$DAEMON" ]; then
  DSRC="$APP"
  # 이 설치기보다 오래된 app 에는 스크립트가 없다. 나중에 uninstall 을 부를 수 있게 지워지지 않는 곳에 받는다
  if [ ! -f "$DSRC/deploy/macos-daemon.sh" ]; then DSRC="$SHARE/daemon-src"; fetch "$DSRC"; fi
  # 이 셸이 파이프(curl | sh)로 읽는 스크립트를 자식이 먹지 않게 한다. sudo 는 /dev/tty 로 묻는다
  /bin/bash "$DSRC/deploy/macos-daemon.sh" install </dev/null || {
    echo "LaunchDaemon 으로 옮기지 못했습니다. 위 결과를 확인한 뒤 다시 실행하세요" >&2
    exit 1
  }
fi

# 6. 모듈: 계획을 보이고 그 plan_id 로 적용한다. app 의 런처가 받아 둔 릴리스(versions/)로 넘긴다
cd "$APP"
for m in hooks setup; do
  plan=$("$PY" -m jarvis module "$m" plan --json) || { echo "$m: 계획을 세우지 못했습니다" >&2; continue; }
  id=$(printf '%s' "$plan" | "$PY" -c '
import json, sys
p, m = json.load(sys.stdin), sys.argv[1]
for s in p["steps"]: print(f"  {m}: " + s["summary"], file=sys.stderr)
for b in p.get("blocked", []): print(f"  {m}: 막힘 ", b.get("step"), b["reason"], file=sys.stderr)
print(p["plan_id"] if p["steps"] else "")' "$m")
  [ -n "$id" ] || { echo "  $m: 바꿀 것 없음"; continue; }
  rc=0; "$PY" -m jarvis module "$m" apply --json --plan-id "$id" || rc=$?
  case $rc in
    0) echo "$m: 적용했습니다" ;;
    2) echo "$m: 보류(열린 터미널·앱이 닫히면 다시 실행하세요)" ;;
    *) echo "$m: 적용하지 못했습니다(종료 코드 $rc)" >&2 ;;
  esac
done

cat <<EOF

jarvis 를 설치했습니다: http://127.0.0.1:9300/api/health
남은 일(계정·네트워크라 직접 합니다):
- Tailscale 에 로그인하고, core 설정의 devices 에 이 기기를 넣은 뒤 core 를 재시작합니다
- 하네스(claude·codex 등)에 로그인합니다
EOF
