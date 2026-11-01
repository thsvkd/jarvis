#!/usr/bin/env bash
#
# oh-my-claudecode(OMC) 설치 워커 (macOS/Linux).
# scripts/setup.sh 가 --omc 로 호출한다. 플러그인은 claude CLI 로 설치하고,
# HUD 에 캐시 TTL 을 띄우는 statusLine 설정은 res/omc/apply.py 가 한다.
#
# 사용법:
#   bash setup_omc.sh
#   bash setup_omc.sh --dry-run
#   bash setup_omc.sh --uninstall   # 캐시 TTL HUD 설정만 되돌린다 (OMC 플러그인은 유지)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/setup_common.sh
source "$SCRIPT_DIR/lib/setup_common.sh"

OMC_MARKETPLACE_REPO="Yeachan-Heo/oh-my-claudecode"
OMC_PLUGIN="oh-my-claudecode@omc"

DRY_RUN=0
UNINSTALL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if ! PY="$(resolve_python_command)"; then
    echo "❌ Python 3 not found." >&2
    print_python_install_hint
    exit 1
fi

FLAGS=()
[[ "$DRY_RUN" -eq 1 ]] && FLAGS+=(--dry-run)

if [[ "$UNINSTALL" -eq 1 ]]; then
    echo "🧹 Reverting OMC HUD (cache TTL)"
    exec "$PY" -X utf8 "$REPO_ROOT/res/omc/apply.py" --home "$HOME" --uninstall ${FLAGS[@]+"${FLAGS[@]}"}
fi

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  • (dry-run) $*"
    else
        "$@"
    fi
}

if ! command -v claude &>/dev/null; then
    echo "❌ claude CLI not found. Install Claude Code first." >&2
    exit 1
fi

echo "🔧 OMC plugin"
# 출력을 먼저 받아 두고 검사한다. pipefail 아래에서 grep -q 가 일찍 끝나면 claude 가 EPIPE 로
# 실패해 '미설치' 로 오판할 수 있다.
plugins="$(claude plugin list 2>/dev/null || true)"
if [[ "$plugins" == *"oh-my-claudecode@"* ]]; then
    echo "  • Already installed: $OMC_PLUGIN"
else
    marketplaces="$(claude plugin marketplace list 2>/dev/null || true)"
    if [[ "$marketplaces" != *"$OMC_MARKETPLACE_REPO"* ]]; then
        run claude plugin marketplace add "$OMC_MARKETPLACE_REPO"
    fi
    run claude plugin install "$OMC_PLUGIN"
fi

echo "🔧 OMC HUD (cache TTL)"
"$PY" -X utf8 "$REPO_ROOT/res/omc/apply.py" --home "$HOME" ${FLAGS[@]+"${FLAGS[@]}"}

echo ""
echo "  ℹ️  For the rest of the OMC setup (CLAUDE.md, etc.), run /oh-my-claudecode:omc-setup inside Claude Code."
echo "     If that rewrites statusLine, just run this script once more."
