#!/usr/bin/env bash
#
# 줄바꿈 키바인딩 설치 워커 (macOS/Linux).
# scripts/setup.sh 가 --keybinds 로 호출한다. 실제 병합은 res/keybinds/apply.py.
#
# 사용법:
#   bash setup_keybinds.sh
#   bash setup_keybinds.sh --newline ctrl-enter
#   bash setup_keybinds.sh --dry-run
#   bash setup_keybinds.sh --uninstall
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/setup_common.sh
source "$SCRIPT_DIR/lib/setup_common.sh"

DRY_RUN=0
UNINSTALL=0
NEWLINE=""
NEWLINE_GIVEN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --newline)
            if [[ -z "${2:-}" ]]; then
                echo "--newline must be shift-enter or ctrl-enter." >&2
                exit 1
            fi
            NEWLINE="$2"; NEWLINE_GIVEN=1; shift 2 ;;
        --newline=*) NEWLINE="${1#*=}"; NEWLINE_GIVEN=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# 빈 값(--newline=)도 잘못된 값이다. PowerShell 워커와 판정을 맞춘다.
if [[ "$NEWLINE_GIVEN" -eq 1 ]]; then
    case "$NEWLINE" in
        shift-enter|ctrl-enter) ;;
        *)
            echo "--newline must be shift-enter or ctrl-enter (got: $NEWLINE)." >&2
            exit 1 ;;
    esac
fi

if ! PY="$(resolve_python_command)"; then
    echo "❌ Python 3 not found." >&2
    print_python_install_hint
    exit 1
fi

APPLY="$REPO_ROOT/res/keybinds/apply.py"
if [[ ! -f "$APPLY" ]]; then
    echo "❌ Keybinding script not found: $APPLY" >&2
    exit 1
fi

# bash 3.2 는 set -u 에서 빈 배열의 "${arr[@]}" 를 unbound 로 본다.
FLAGS=()
[[ "$DRY_RUN" -eq 1 ]] && FLAGS+=(--dry-run)
[[ "$UNINSTALL" -eq 1 ]] && FLAGS+=(--uninstall)
NEWLINE_FLAG=()
[[ -n "$NEWLINE" ]] && NEWLINE_FLAG=(--newline "$NEWLINE")

"$PY" -X utf8 "$APPLY" --home "$HOME" \
    ${FLAGS[@]+"${FLAGS[@]}"} \
    ${NEWLINE_FLAG[@]+"${NEWLINE_FLAG[@]}"}
