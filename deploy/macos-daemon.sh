#!/bin/bash
# macOS: jarvis 를 LaunchAgent(로그인 세션)와 LaunchDaemon(시스템 도메인, GUI 로그인 불필요) 사이에서 옮긴다.
# 사용자로 실행한다. sudo 는 처음에 한 번만 묻는다. README 의 macOS 절 참고.
#
#   bash deploy/macos-daemon.sh install|uninstall|status [--dry-run]
#
# install 이 어느 단계에서든 실패하면 LaunchAgent 로 되돌리고 0 이 아닌 코드로 끝난다.
# 에이전트 plist 는 LaunchAgents 밖(~/.local/share/jarvis/dev.jarvis.plist.agent)에 둔다. 남겨 두면 다음 GUI 로그인 때
# 두 번째 jarvis 가 같은 포트를 잡으려다 KeepAlive 재시작을 되풀이한다.
set -u
# ~/.local/bin·/opt/homebrew/bin 은 사용자가 쓸 수 있고 macOS 기본 sudoers 에는 secure_path 가 없다. 이름으로 찾으면 그 파일이 root 로 돈다
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

LABEL=dev.jarvis
REPO=$(cd "$(dirname "$0")/.." && pwd)
SHARE="$HOME/.local/share/jarvis"
PY="$SHARE/venv/bin/python"
AGENT="$HOME/Library/LaunchAgents/$LABEL.plist"
KEPT="$SHARE/$LABEL.plist.agent"
DAEMON=/Library/LaunchDaemons/$LABEL.plist
SUDO=/usr/bin/sudo
LAUNCHCTL=/bin/launchctl
PLUTIL=/usr/bin/plutil
STAT=/usr/bin/stat
# 테스트만 가짜 sudo·launchctl·plutil·stat 과 임시 plist 경로를 쓴다. sudo 에 넘기는 명령은 테스트에서도 아래의 절대경로 그대로다
if [ "${JARVIS_DAEMON_TEST:-}" = 1 ]; then
  T="${JARVIS_DAEMON_TEST_BIN:?}"
  PATH="$T:$PATH"
  SUDO="$T/sudo"
  LAUNCHCTL="$T/launchctl"
  PLUTIL="$T/plutil"
  STAT="$T/stat"
  DAEMON="${JARVIS_DAEMON_PLIST:-$DAEMON}"
fi

say() { printf '%s\n' "$*"; }
die() { say "$*" >&2; exit 1; }
usage() { die "사용법: bash deploy/macos-daemon.sh install|uninstall|status [--dry-run]"; }

CMD="${1:-}"
[ $# -gt 0 ] && shift
DRY=""
for a in "$@"; do
  case "$a" in --dry-run) DRY=1 ;; *) usage ;; esac
done
case "$CMD" in install|uninstall|status) ;; *) usage ;; esac
[ "$(uname -s)" = Darwin ] || die "macOS 전용입니다"
[ "$(id -u)" != 0 ] || die "root 로 실행하지 마세요: 게이트웨이를 돌릴 사용자로 실행합니다"

GUI="gui/$(id -u)"
TMP=$(mktemp -d)
NEW="$TMP/$LABEL.plist"
KEEP=""
trap '[ -n "$KEEP" ] && kill "$KEEP" 2>/dev/null; rm -rf "$TMP"' EXIT

py() { (cd "$REPO" && PYTHONDONTWRITEBYTECODE=1 "$PY" -m jarvis.macos_daemon "$@"); }
loaded() { "$LAUNCHCTL" print "$1/$LABEL" >/dev/null 2>&1; }
wait_unloaded() {  # bootout 뒤 작업이 내려갈 때까지(ExitTimeOut 8초)
  for _ in $(seq 10); do loaded "$1" || return 0; sleep 1; done
  return 1
}
listening() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }
health_once() { curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }
healthy() {  # 최대 40초
  for _ in $(seq 40); do health_once && return 0; sleep 1; done
  return 1
}
agent_plist() {  # 렌더 기준: LaunchAgents 의 것이 없으면 옮겨 둔 것
  if [ -f "$AGENT" ]; then say "$AGENT"; elif [ -f "$KEPT" ]; then say "$KEPT"; else return 1; fi
}

sha() { /usr/bin/shasum -a 256 "$1" | cut -d' ' -f1; }
field() { "$PLUTIL" -extract "$1" raw -o - "$DAEMON" 2>/dev/null; }
# 설치된 root 소유 사본만 믿을 수 있다: $NEW 는 같은 uid 프로세스가 sudo 를 묻는 사이에 바꿀 수 있다. bootstrap 전에 부른다
verify_installed() {
  local got user
  user=$(id -un)
  got=$("$STAT" -f '%Su:%Sg %Lp' "$DAEMON" 2>/dev/null)
  if [ "$got" != "root:wheel 644" ]; then say "소유·권한이 root:wheel 0644 가 아닙니다: $got"; return 1; fi
  got=$(field UserName)
  case "$got" in ""|root|0) say "UserName 이 root 입니다: $got"; return 1 ;; esac
  if [ "$got" != "$user" ]; then say "UserName 이 $user 가 아닙니다: $got"; return 1; fi
  for key in StandardOutPath StandardErrorPath; do
    got=$(field "$key")
    if [ "$got" != /dev/null ]; then say "$key 가 /dev/null 이 아닙니다: $got"; return 1; fi
  done
  got=$(field Label)
  if [ "$got" != "$LABEL" ]; then say "Label 이 $LABEL 이 아닙니다: $got"; return 1; fi
  if [ "$(sha "$DAEMON")" != "$NEW_SHA" ]; then say "렌더한 내용과 다릅니다(sha256)"; return 1; fi
}

# 시스템 도메인의 dev.jarvis 는 기기에 하나다. 다른 사용자의 등록은 채택·교체·삭제하지 않는다
refuse_foreign() {
  local owner path
  # 로드된 작업이 이 plist 에서 온 것이 아니면 UserName 을 읽을 곳이 없다: 소유를 모르는 작업은 내리지 않는다
  if loaded system; then
    path=$("$LAUNCHCTL" print "system/$LABEL" 2>/dev/null | sed -n 's/^[[:space:]]*path = //p' | head -n 1)
    if [ ! -f "$DAEMON" ] || [ "$path" != "$DAEMON" ]; then
      die "system/$LABEL 이 로드돼 있지만(path: ${path:-알 수 없음}) $DAEMON 에서 온 작업인지 확인할 수 없습니다. 소유를 모르는 작업이라 아무것도 바꾸지 않았습니다"
    fi
  fi
  [ -f "$DAEMON" ] || return 0
  owner=$(field UserName)
  [ "$owner" = "$(id -un)" ] || die "$DAEMON 은 다른 사용자(${owner:-알 수 없음})의 LaunchDaemon 입니다. 아무것도 바꾸지 않았습니다"
}

sudo_begin() {  # 이후의 sudo 는 모두 -n 이다: 다시 묻지 않고, 시간이 지나 끊기면 실패로 다룬다
  "$SUDO" -v || die "sudo 권한을 얻지 못했습니다. 아무것도 바꾸지 않았습니다"
  ( exec >/dev/null 2>&1 </dev/null; while kill -0 $$ && "$SUDO" -n -v; do sleep 30; done ) &
  KEEP=$!
}

rollback() {  # daemon 을 내리고 LaunchAgent 로 되돌린다. 0 = 에이전트가 로드됐고 health 가 응답한다
  trap '' HUP INT TERM PIPE
  local ok=0
  say "LaunchAgent 로 되돌립니다"
  if loaded system; then "$SUDO" -n /bin/launchctl bootout "system/$LABEL" 2>/dev/null; wait_unloaded system; fi
  # 내려간 것을 확인한 뒤에만 지운다: 로드된 채 plist 만 지우면 재부팅 뒤에 아무것도 뜨지 않는다
  if ! loaded system && [ -f "$DAEMON" ]; then "$SUDO" -n /bin/rm -f "$DAEMON" 2>/dev/null; fi
  if loaded system || [ -f "$DAEMON" ]; then
    # 에이전트까지 올리면 두 벌이 같은 포트를 두고 KeepAlive 재시작을 되풀이한다
    if [ -f "$AGENT" ]; then mv "$AGENT" "$KEPT"; fi
    say "  daemon 등록을 내리지 못했습니다(sudo 권한이 끊겼을 수 있습니다). LaunchAgent 는 띄우지 않았습니다"
    say "  다시 실행합니다: bash $REPO/deploy/macos-daemon.sh uninstall"
    return 1
  fi
  say "  daemon: 내렸습니다, plist 를 지웠습니다"
  [ -f "$AGENT" ] || mv "$KEPT" "$AGENT" || { say "  에이전트 plist 를 복원하지 못했습니다: $KEPT"; return 1; }
  say "  에이전트 plist: $AGENT"
  # bootout 직후의 bootstrap 은 작업이 아직 내려가는 중이면 실패할 수 있다. 몇 번 다시 한다
  for _ in 1 2 3 4 5; do
    loaded "$GUI" && break
    "$LAUNCHCTL" bootstrap "$GUI" "$AGENT" 2>/dev/null
    sleep 1
  done
  if ! loaded "$GUI"; then
    say "  에이전트를 띄우지 못했습니다(GUI 로그인 세션이 없을 수 있습니다). 다음 GUI 로그인 때 뜹니다"
    ok=1
  elif healthy; then
    say "  에이전트: 로드됨, health 응답함"
  else
    say "  에이전트: 로드됨, health 응답 없음"
    ok=1
  fi
  return $ok
}

fail() {
  say "실패: $*" >&2
  if rollback; then say "롤백했습니다: LaunchAgent 로 돌고 있습니다" >&2
  else say "롤백이 완전하지 않습니다: 위 결과를 확인하세요. 게이트웨이가 돌지 않을 수 있습니다" >&2; fi
  exit 1
}

install_plan() {
  say "렌더한 plist:"
  cat "$NEW"
  say "실행할 명령:"
  say "  /usr/bin/sudo -v"
  say "  /bin/launchctl bootout $GUI/$LABEL   # 로드돼 있으면"
  say "  /usr/bin/sudo -n /bin/launchctl bootout system/$LABEL   # 로드돼 있으면"
  say "  # :$PORT 가 풀릴 때까지 대기"
  say "  /usr/bin/sudo -n /usr/bin/install -m 0644 -o root -g wheel <렌더한 plist> $DAEMON"
  say "  # 설치된 plist 검사: root:wheel 0644, UserName, Standard*Path, Label, sha256 $NEW_SHA"
  say "  /usr/bin/sudo -n /bin/launchctl bootstrap system $DAEMON"
  say "  /bin/launchctl print system/$LABEL   # state = running 확인"
  say "  curl http://127.0.0.1:$PORT/api/health   # 최대 40초"
  say "  mv $AGENT $KEPT"
}

cmd_install() {
  local base why
  refuse_foreign
  base=$(agent_plist) || die "에이전트 plist 가 없습니다: $AGENT (install.sh 로 먼저 설치합니다)"
  [ -x "$PY" ] || die "venv python 이 없습니다: $PY (install.sh 로 먼저 설치합니다)"
  py check --agent "$base" || die "사전 점검에 실패했습니다. 아무것도 바꾸지 않았습니다"
  py render --agent "$base" > "$NEW" || die "아무것도 바꾸지 않았습니다"
  NEW_SHA=$(sha "$NEW")
  [ -n "$NEW_SHA" ] || die "렌더한 plist 의 sha256 을 구하지 못했습니다. 아무것도 바꾸지 않았습니다"
  "$PLUTIL" -lint "$NEW" >/dev/null || die "렌더한 plist 가 plutil -lint 를 통과하지 못했습니다. 아무것도 바꾸지 않았습니다"
  PORT=$(py port --agent "$base") || die "포트를 읽지 못했습니다. 아무것도 바꾸지 않았습니다"

  if [ -f "$DAEMON" ] && cmp -s "$DAEMON" "$NEW" && loaded system && [ ! -f "$AGENT" ]; then
    say "같은 내용의 LaunchDaemon 이 이미 로드돼 있습니다. 바꿀 것이 없습니다"
    return 0
  fi
  # 롤백이 띄울 에이전트도 같은 포트에 막힌다. 내릴 것이 없는데 포트가 잡혀 있으면 시작하지 않는다
  if ! loaded "$GUI" && ! loaded system && listening; then
    die "다른 프로세스가 :$PORT 를 쓰고 있습니다. 아무것도 바꾸지 않았습니다"
  fi
  local before="$base"
  [ -f "$DAEMON" ] && before="$DAEMON"
  say "비교: $before → 렌더 결과"
  diff -u "$before" "$NEW"
  if [ -n "$DRY" ]; then install_plan; return 0; fi

  sudo_begin
  # SSH 가 끊기면 터미널에 묶인 sudo 권한도 끊겨 롤백이 안 될 수 있다. 끊겨도 멈추지 않고 전환을 끝낸다
  trap '' HUP PIPE
  trap 'fail "중단되었습니다"' INT TERM
  mkdir -p "$HOME/Library/Logs" "$SHARE" || fail "로그·보관 폴더를 만들지 못했습니다"
  if loaded "$GUI"; then "$LAUNCHCTL" bootout "$GUI/$LABEL" 2>/dev/null; fi
  wait_unloaded "$GUI" || fail "LaunchAgent 를 내리지 못했습니다"
  if loaded system; then "$SUDO" -n /bin/launchctl bootout "system/$LABEL" 2>/dev/null; fi
  wait_unloaded system || fail "기존 daemon 을 내리지 못했습니다"
  for _ in $(seq 30); do listening || break; sleep 1; done
  if listening; then fail ":$PORT 가 풀리지 않습니다"; fi
  "$SUDO" -n /usr/bin/install -m 0644 -o root -g wheel "$NEW" "$DAEMON" || fail "plist 를 설치하지 못했습니다: $DAEMON"
  why=$(verify_installed) || fail "설치된 plist 를 믿을 수 없습니다: $why"
  for _ in 1 2 3 4 5; do
    "$SUDO" -n /bin/launchctl bootstrap system "$DAEMON" && break
    sleep 1
  done
  loaded system || fail "launchctl bootstrap system 에 실패했습니다"
  for _ in $(seq 10); do
    "$LAUNCHCTL" print "system/$LABEL" 2>/dev/null | grep -q '^[[:space:]]*state = running' && break
    sleep 1
  done
  "$LAUNCHCTL" print "system/$LABEL" 2>/dev/null | grep -q '^[[:space:]]*state = running' || fail "daemon 이 running 이 아닙니다"
  healthy || fail "health 가 40초 안에 응답하지 않습니다: http://127.0.0.1:$PORT/api/health"
  if [ -f "$AGENT" ]; then mv "$AGENT" "$KEPT" || fail "에이전트 plist 를 옮기지 못했습니다"; fi
  say "LaunchDaemon 으로 옮겼습니다: $DAEMON"
  say "에이전트 plist 는 $KEPT 에 있습니다(되돌리기: bash $REPO/deploy/macos-daemon.sh uninstall)"
}

cmd_uninstall() {
  local base
  refuse_foreign
  base=$(agent_plist) || die "되돌릴 에이전트 plist 가 없습니다: $KEPT"
  PORT=$(py port --agent "$base" 2>/dev/null) || PORT=9300
  if [ ! -f "$DAEMON" ] && ! loaded system && [ -f "$AGENT" ]; then
    say "LaunchDaemon 으로 등록돼 있지 않습니다. 바꿀 것이 없습니다"
    return 0
  fi
  if [ -n "$DRY" ]; then
    say "실행할 명령:"
    say "  /usr/bin/sudo -v"
    say "  /usr/bin/sudo -n /bin/launchctl bootout system/$LABEL"
    say "  /usr/bin/sudo -n /bin/rm -f $DAEMON"
    say "  mv $KEPT $AGENT"
    say "  /bin/launchctl bootstrap $GUI $AGENT"
    say "  curl http://127.0.0.1:$PORT/api/health   # 최대 40초"
    return 0
  fi
  sudo_begin
  rollback || { say "되돌리기가 완전하지 않습니다: 위 결과를 확인하세요" >&2; exit 1; }
  say "LaunchAgent 로 되돌렸습니다"
}

cmd_status() {
  local p
  p=$(agent_plist) || p="$DAEMON"
  PORT=$(py port --agent "$p" 2>/dev/null) || PORT=9300
  if loaded system; then say "daemon: 로드됨"; else say "daemon: 로드 안 됨"; fi
  if [ -f "$DAEMON" ]; then say "  plist: $DAEMON"; fi
  if loaded "$GUI"; then say "agent: 로드됨"; else say "agent: 로드 안 됨"; fi
  if [ -f "$AGENT" ]; then say "  plist: $AGENT"; elif [ -f "$KEPT" ]; then say "  옮겨 둔 plist: $KEPT"; fi
  if health_once; then say "health: ok (:$PORT)"; else say "health: 응답 없음 (:$PORT)"; fi
}

"cmd_$CMD"
