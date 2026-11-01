#!/usr/bin/env bash
#
# fluent-korean 출력 스타일 설치 워커 (macOS/Linux).
# scripts/setup.sh 가 --output-style 로 호출한다. 플러그인은 claude CLI 로 설치하고,
# ~/.claude/settings.json 의 outputStyle 지정은 res/output_style/apply.py 가 한다.
#
# 사용법:
#   bash setup_output_style.sh
#   bash setup_output_style.sh --dry-run
#   bash setup_output_style.sh --uninstall   # outputStyle 만 되돌린다 (플러그인은 유지)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/setup_common.sh
source "$SCRIPT_DIR/lib/setup_common.sh"

MARKETPLACE_REPO="snflkd/fluent-korean"
PLUGIN="fluent-korean@fluent-korean"

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
    echo "🧹 Reverting output style"
    exec "$PY" -X utf8 "$REPO_ROOT/res/output_style/apply.py" --home "$HOME" --uninstall ${FLAGS[@]+"${FLAGS[@]}"}
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

echo "🔧 fluent-korean plugin"
# 출력을 먼저 받아 두고 검사한다. pipefail 아래에서 grep -q 가 일찍 끝나면 claude 가 EPIPE 로
# 실패해 '미설치' 로 오판할 수 있다.
plugins="$(claude plugin list 2>/dev/null || true)"
if [[ "$plugins" == *"$PLUGIN"* ]]; then
    echo "  • Already installed: $PLUGIN"
else
    marketplaces="$(claude plugin marketplace list 2>/dev/null || true)"
    if [[ "$marketplaces" != *"$MARKETPLACE_REPO"* ]]; then
        run claude plugin marketplace add "$MARKETPLACE_REPO"
    fi
    run claude plugin install "$PLUGIN"
fi

echo "🔧 Output style"
"$PY" -X utf8 "$REPO_ROOT/res/output_style/apply.py" --home "$HOME" ${FLAGS[@]+"${FLAGS[@]}"}
