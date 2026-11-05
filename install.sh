#!/bin/sh
# jarvis 설치기(macOS·Linux): 게이트웨이를 설치해 띄우고 모듈(Orca·훅·셋업)을 적용한다. 다시 실행해도 된다.
# Windows 는 install.ps1 을 쓴다(README 의 운영 절).
#
#   curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --core <core 의 tailnet IP>
#
# git·Homebrew 가 없는 새 Mac 에서도 돈다: Python 은 uv 로 받은 venv(~/.local/share/jarvis/venv), 코드는 최신 릴리스 묶음.
# Linux 는 LaunchAgent 대신 systemd 사용자 유닛(deploy/jarvis.service, python 만 venv 로)을 쓴다.
# 코드는 ~/.local/share/jarvis/app 에 둔다(~/Workspace/jarvis 는 개발 저장소 자리). 1.8.0 이전 배치는 app 으로 옮긴다.
# 이미 있는 설정·LaunchAgent·유닛·코드는 덮어쓰지 않는다.
set -eu

CORE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --core) CORE="${2:?--core 에 tailnet IP 를 주세요}"; shift 2 ;;
    *) echo "모르는 인자: $1" >&2; exit 1 ;;
  esac
done

OS=$(uname -s)
case "$OS" in
  Darwin|Linux) ;;
  *) echo "macOS·Linux 만 지원합니다. Windows 는 install.ps1 을 쓰세요(README 의 운영 절)" >&2; exit 1 ;;
esac

SHARE="$HOME/.local/share/jarvis"
APP="$SHARE/app"
OLD="$HOME/Workspace/jarvis"  # 1.8.0 이전 배치
CONFIG="$HOME/.config/jarvis/config.toml"
AGENT="$HOME/Library/LaunchAgents/dev.jarvis.plist"
UNIT="$HOME/.config/systemd/user/jarvis.service"
PY="$SHARE/venv/bin/python"
MANIFEST="${JARVIS_MANIFEST:-https://github.com/thsvkd/jarvis/releases/latest/download/manifest.json}"
export PATH="$HOME/.local/bin:$PATH"

if [ ! -f "$CONFIG" ] && [ -z "$CORE" ]; then
  echo "처음 설치에는 --core <core 의 tailnet IP> 가 필요합니다(이 기기의 Orca 페어링을 받아 갈 기기)" >&2
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

# 2. 코드: 최신 릴리스 묶음. 이후 갱신은 core 의 jarvis 업데이트 버튼이 한다(versions/).
# 개발 체크아웃(~/Workspace/jarvis/.git)으로 이미 등록된 기기는 그대로 둔다(받지도 옮기지도 않는다)
if [ ! -d "$APP" ] && [ -e "$OLD/.git" ] && { [ -f "$AGENT" ] || [ -f "$UNIT" ]; }; then APP="$OLD"; fi
if [ ! -d "$APP" ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$MANIFEST" -o "$tmp/manifest.json"
  url=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$tmp/manifest.json")
  sum=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$tmp/manifest.json")
  echo "jarvis 받기: $url"
  curl -fsSL "$url" -o "$tmp/jarvis.tar.gz"
  if command -v sha256sum >/dev/null 2>&1; then echo "$sum  $tmp/jarvis.tar.gz" | sha256sum -c - >/dev/null
  else echo "$sum  $tmp/jarvis.tar.gz" | shasum -a 256 -c - >/dev/null; fi
  mkdir -p "$APP"
  tar -xzf "$tmp/jarvis.tar.gz" -C "$APP" --strip-components 1
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
if [ "$OS" = Darwin ]; then
  if [ ! -f "$AGENT" ]; then
    mkdir -p "$(dirname "$AGENT")"
    sed -e "s#/opt/homebrew/bin/python3#$PY#" -e "s#/Users/USER#$HOME#g" "$APP/deploy/jarvis.plist" > "$AGENT"
  fi
  launchctl print "gui/$(id -u)/dev.jarvis" >/dev/null 2>&1 || launchctl bootstrap "gui/$(id -u)" "$AGENT"
else
  if [ ! -f "$UNIT" ]; then
    mkdir -p "$(dirname "$UNIT")"
    sed -e "s#^ExecStart=/usr/bin/python3 #ExecStart=$PY #" "$APP/deploy/jarvis.service" > "$UNIT"
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

# 5. 1.8.0 이전 배치(~/Workspace/jarvis, .git 없음)를 쓰던 등록을 app 으로 옮긴다. 개발 체크아웃이면 relocate 가 아무것도 하지 않는다.
# 옛 폴더의 코드에는 relocate 가 없을 수 있으므로 방금 받은 app 의 코드로 부른다
cd "$APP"
if [ -d "$OLD" ] && [ "$APP" != "$OLD" ]; then
  state=$("$PY" -m jarvis.relocate --json | "$PY" -c 'import json, sys; r = json.load(sys.stdin); print("이전:", r["state"], r["detail"], file=sys.stderr); print(r["state"])') || state=failed
  # macOS 는 launchd 가 plist 를 다시 읽어야 app 에서 뜬다. 사용자가 실행한 설치기라 여기서 다시 읽힌 뒤 옛 폴더를 옮긴다
  if [ "$OS" = Darwin ] && [ "$state" = pending_reload ]; then
    launchctl bootout "gui/$(id -u)/dev.jarvis" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENT"
    "$PY" -m jarvis.relocate || true
  fi
fi

# 6. 모듈: 계획을 보이고 그 plan_id 로 적용한다
for m in orca hooks setup; do
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
- Orca 와 하네스(claude·codex 등)에 로그인합니다
EOF
