#!/bin/sh
# jarvis 설치기(macOS): 게이트웨이를 설치해 띄우고 모듈(지금은 Orca)을 적용한다. 다시 실행해도 된다.
#
#   curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --core <core 의 tailnet IP>
#
# git·Homebrew 가 없는 새 Mac 에서도 돈다: Python 은 uv 로 받은 venv(~/.local/share/jarvis/venv), 코드는 최신 릴리스 묶음.
# 이미 있는 설정·LaunchAgent·저장소는 덮어쓰지 않는다.
set -eu

CORE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --core) CORE="${2:?--core 에 tailnet IP 를 주세요}"; shift 2 ;;
    *) echo "모르는 인자: $1" >&2; exit 1 ;;
  esac
done

[ "$(uname -s)" = Darwin ] || { echo "지금은 macOS 만 지원합니다. Linux·Windows 는 README 의 운영 절을 따르세요" >&2; exit 1; }

SHARE="$HOME/.local/share/jarvis"
REPO="$HOME/Workspace/jarvis"
CONFIG="$HOME/.config/jarvis/config.toml"
AGENT="$HOME/Library/LaunchAgents/dev.jarvis.plist"
PY="$SHARE/venv/bin/python"
MANIFEST="${JARVIS_MANIFEST:-https://github.com/thsvkd/jarvis/releases/latest/download/manifest.json}"
export PATH="$HOME/.local/bin:$PATH"

if [ ! -f "$CONFIG" ] && [ -z "$CORE" ]; then
  echo "처음 설치에는 --core <core 의 tailnet IP> 가 필요합니다(이 기기의 Orca 페어링을 받아 갈 기기)" >&2
  exit 1
fi

# 1. Python 3.11+ 와 의존성(psutil, 메뉴 막대 아이콘의 pystray·pillow)
if ! command -v uv >/dev/null 2>&1; then
  echo "uv 설치"
  curl -fsSL https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
fi
[ -x "$PY" ] || uv venv --python 3.13 "$SHARE/venv"
uv pip install --quiet --python "$PY" psutil pystray pillow

# 2. 코드: 최신 릴리스 묶음. 이후 갱신은 core 의 jarvis 업데이트 버튼이 한다(versions/).
if [ ! -d "$REPO" ]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$MANIFEST" -o "$tmp/manifest.json"
  url=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$tmp/manifest.json")
  sum=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$tmp/manifest.json")
  echo "jarvis 받기: $url"
  curl -fsSL "$url" -o "$tmp/jarvis.tar.gz"
  echo "$sum  $tmp/jarvis.tar.gz" | shasum -a 256 -c - >/dev/null
  mkdir -p "$REPO"
  tar -xzf "$tmp/jarvis.tar.gz" -C "$REPO" --strip-components 1
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

[infra_units]
docker = ""
containerd = ""
tailscale = ""
ssh = "com.openssh.sshd"
pm2 = ""
EOF
fi

# 4. LaunchAgent(로그인한 사용자 세션에서 돈다)
if [ ! -f "$AGENT" ]; then
  mkdir -p "$(dirname "$AGENT")"
  sed -e "s#/opt/homebrew/bin/python3#$PY#" -e "s#/Users/USER#$HOME#g" "$REPO/deploy/jarvis.plist" > "$AGENT"
fi
launchctl print "gui/$(id -u)/dev.jarvis" >/dev/null 2>&1 || launchctl bootstrap "gui/$(id -u)" "$AGENT"

# 5. 모듈: 계획을 보이고 그 plan_id 로 적용한다
cd "$REPO"
# shellcheck disable=SC2043  # ai-setup 에서 옮긴 모듈을 여기에 더한다
for m in orca; do
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
