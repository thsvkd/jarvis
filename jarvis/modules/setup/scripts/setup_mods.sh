#!/usr/bin/env bash
#
# Claude Code mod 설치 워커 (macOS/Linux).
# scripts/setup.sh 가 --mods 로 호출한다. 번들 mod(res/mods/*)를 ~/.claude/mods 에 복사하고
# ~/.claude/settings.json 의 env.CLAUDE_CODE_PLUGIN_DIRS 에 등록하는 일은 res/mods/apply.py 가 한다.
#
# 사용법:
#   bash setup_mods.sh
#   bash setup_mods.sh --dry-run
#   bash setup_mods.sh --uninstall
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/setup_common.sh
source "$SCRIPT_DIR/lib/setup_common.sh"

FLAGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--uninstall) FLAGS+=("$1"); shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if ! PY="$(resolve_python_command)"; then
    echo "❌ Python 3 not found." >&2
    print_python_install_hint
    exit 1
fi

echo "🔧 Claude Code mod"
exec "$PY" -X utf8 "$REPO_ROOT/res/mods/apply.py" --home "$HOME" ${FLAGS[@]+"${FLAGS[@]}"}
