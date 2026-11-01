#!/usr/bin/env bash
#
# Claude Code 사용자 레벨 설정 CLI 엔트리포인트 (macOS/Linux)
#
#   ai-setup 에서 옮겼다(jarvis 모듈 setup). 인자 없이 실행하면 jarvis 의 setup 모듈을 안내하고 끝낸다
#   (대화형 온보딩은 core 홈의 AI 셋업 화면이 대신한다).
#   --no-onboard 는 예전 기본 설치(AGENTS.md+키바인드+스킬)를 유지한다. 훅은 jarvis 가 맡는다.
#   --stages 는 순수 액션 목록(res/stages.py)을 따른다.
#
# 사용법:
#   bash setup.sh --agentsmd             # AGENTS.md만 (온보딩 없음)
#   bash setup.sh --keybinds             # 줄바꿈 키바인딩만 (온보딩 없음)
#   bash setup.sh --omc                  # oh-my-claudecode + 캐시 TTL HUD 만 (온보딩 없음)
#   bash setup.sh --output-style         # fluent-korean 출력 스타일만 (온보딩 없음)
#   bash setup.sh --mods                 # Claude Code mod 만 (온보딩 없음)
#   bash setup.sh --newline ctrl-enter   # 줄바꿈 키를 Ctrl+Enter 로
#   bash setup.sh --dry-run              # 미리보기 (전체, 온보딩 없음)
#   bash setup.sh --no-onboard           # 온보딩을 끄고 예전 기본 동작
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

print_usage() {
    cat <<'EOF'
ai-setup — Claude Code user-level setup

Usage: bash setup.sh [options]
  (no options)  installs nothing; choose and approve on the jarvis AI setup screen (module setup)
  --stages L    all or a comma list, in stage order. --dry-run prints the list only
  --jarvis-role gateway|core
                with --stages, clone and register Jarvis for that role
  --herdr       with --stages, include herdr (off unless chosen)
  --ss-caps     with --stages, grant the Jarvis ss capability
  --agentsmd    install project instructions into the current folder only
  --harness L   harnesses to install instructions for (comma-separated: claude,codex,grok,opencode,agy,gemini,copilot,all)
                choosing claude also installs CLAUDE.md. Default is all.
  --skills      install bundled skills only (~/.claude/skills)
  --keybinds    install newline keybindings only (VS Code/Cursor/Claude/Codex/tmux)
  --newline VAL newline key (shift-enter | ctrl-enter). Default shift-enter
  --omc         install the oh-my-claudecode plugin + show cache TTL in the HUD
                (not included in the default install without a scope)
  --output-style
                install the fluent-korean plugin + set outputStyle to fluent-korean
                (not included in the default install without a scope)
  --mods        install Claude Code mods (~/.claude/mods, v2.1.290 or later)
                · bash-diff-filter: hides OMC HUD cache files from the Bash changed-files display
                · cache-ttl-status: shows the remaining prompt-cache time in a status line below the prompt
                · korean-reply-rewrite: after a Korean prompt, stores English replies in Korean so later replies stay Korean
                (not included in the default install without a scope)
  --uninstall   remove managed keybindings/skills + cache TTL HUD + outputStyle + mod settings
                (your own entries and the OMC/fluent-korean plugins are kept). Guard hooks are left to jarvis.
                For a staged install (no options or --stages) name the stages, e.g.
                --stages newline,claude_special --uninstall (settings only).
                --stages all --uninstall also removes the programs this setup installed.
  --dry-run     print the change plan only, without changing anything
  --no-onboard  run with defaults without interactive onboarding
  --help, -h    this help
EOF
}

RUN_AGENTSMD=0
RUN_KEYBINDS=0
RUN_SKILLS=0
RUN_OMC=0
RUN_OUTPUT_STYLE=0
RUN_MODS=0
DRY_RUN=0
UNINSTALL=0
NO_ONBOARD=0
# 온보딩이 채우는 값들. 비어 있으면 워커가 각자의 기본값을 쓴다.
FORCE_AGENTSMD=0
HARNESSES=""
NEWLINE_CHORD=""
NEWLINE_GIVEN=0
STAGES=""
STAGE_RUN=0
JARVIS_ROLE=""
HERDR_FLAG=0
SS_CAPS=0

# Orca 관리는 jarvis 로 옮겨 갔다. 예전 진입점은 아무것도 바꾸지 않고 이 안내만 낸다.
# PowerShell 처럼 --help 보다 먼저 본다.
ORCA_MOVED="Orca is now managed by jarvis; ai-setup no longer installs, updates or removes it. From a jarvis checkout run: python3 -m jarvis module orca plan --json (uninstall: module orca uninstall --json --dry-run)."
# doctor 도 jarvis 로 옮겨 갔다(res/stages.py 의 doctor_moved 와 같은 문구).
DOCTOR_MOVED="Doctor is now run by jarvis; ai-setup no longer runs it. From a jarvis checkout run: python3 -m jarvis module doctor status --json."
# 훅도 jarvis 로 옮겨 갔다(res/stages.py 의 hooks_moved 와 같은 문구). --stages hooks 는 stages.py 가 같은 안내로 막는다.
HOOKS_MOVED="Hooks are now managed by jarvis; ai-setup no longer installs or removes them. From a jarvis checkout run: python3 -m jarvis module hooks plan --json (uninstall: module hooks uninstall --json --dry-run)."

for arg in "$@"; do
    case "$arg" in
        --tools|--tools=*|--orca-desktop) echo "$ORCA_MOVED" >&2; exit 1 ;;
        --doctor) echo "$DOCTOR_MOVED" >&2; exit 1 ;;
    esac
done
for arg in "$@"; do
    case "$arg" in
        --hooks) echo "$HOOKS_MOVED" >&2; exit 1 ;;
    esac
done

HAD_ARGS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agentsmd) RUN_AGENTSMD=1; HAD_ARGS=1; shift ;;
        --skills) RUN_SKILLS=1; HAD_ARGS=1; shift ;;
        --keybinds) RUN_KEYBINDS=1; HAD_ARGS=1; shift ;;
        --omc) RUN_OMC=1; HAD_ARGS=1; shift ;;
        --output-style) RUN_OUTPUT_STYLE=1; HAD_ARGS=1; shift ;;
        --mods) RUN_MODS=1; HAD_ARGS=1; shift ;;
        --harness)
            if [[ -z "${2:-}" ]]; then
                echo "--harness requires a value (claude|codex|grok|opencode|agy|gemini|copilot|all)" >&2
                exit 1
            fi
            HARNESSES="$2"; HAD_ARGS=1; shift 2 ;;
        --harness=*) HARNESSES="${1#*=}"; HAD_ARGS=1; shift ;;
        --newline)
            if [[ -z "${2:-}" ]]; then
                echo "--newline must be shift-enter or ctrl-enter." >&2
                exit 1
            fi
            NEWLINE_CHORD="$2"; NEWLINE_GIVEN=1; HAD_ARGS=1; shift 2 ;;
        --newline=*) NEWLINE_CHORD="${1#*=}"; NEWLINE_GIVEN=1; HAD_ARGS=1; shift ;;
        --uninstall) UNINSTALL=1; HAD_ARGS=1; shift ;;
        --dry-run) DRY_RUN=1; HAD_ARGS=1; shift ;;
        --no-onboard) NO_ONBOARD=1; HAD_ARGS=1; shift ;;
        --stages)
            if [[ -z "${2:-}" ]]; then echo "--stages requires all or a comma-separated list." >&2; exit 1; fi
            STAGES="$2"; STAGE_RUN=1; HAD_ARGS=1; shift 2 ;;
        --stages=*) STAGES="${1#*=}"; STAGE_RUN=1; HAD_ARGS=1; shift ;;
        --jarvis-role)
            if [[ -z "${2:-}" ]]; then echo "--jarvis-role requires gateway or core." >&2; exit 1; fi
            JARVIS_ROLE="$2"; HAD_ARGS=1; shift 2 ;;
        --jarvis-role=*) JARVIS_ROLE="${1#*=}"; HAD_ARGS=1; shift ;;
        --herdr) HERDR_FLAG=1; HAD_ARGS=1; shift ;;
        --ss-caps) SS_CAPS=1; HAD_ARGS=1; shift ;;
        --help|-h) print_usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; echo ""; print_usage >&2; exit 1 ;;
    esac
done

# 값이 비었는지가 아니라 '플래그가 주어졌는지' 로 판정한다. --newline= 처럼 빈 값을 준 경우도
# 잘못된 값이며, 조용히 기본값으로 흘려보내면 PowerShell 쪽(-Newline "")과 동작이 갈린다.
if [[ -n "$JARVIS_ROLE" ]]; then
    case "$JARVIS_ROLE" in
        gateway|core) ;;
        *)
            echo "--jarvis-role must be gateway or core (got: $JARVIS_ROLE)." >&2
            exit 1 ;;
    esac
fi
if [[ ( -n "$JARVIS_ROLE" || "$HERDR_FLAG" -eq 1 || "$SS_CAPS" -eq 1 ) && "$STAGE_RUN" -ne 1 ]]; then
    echo "--jarvis-role, --herdr, and --ss-caps require --stages." >&2
    exit 1
fi

if [[ "$NEWLINE_GIVEN" -eq 1 ]]; then
    case "$NEWLINE_CHORD" in
        shift-enter|ctrl-enter) ;;
        *)
            echo "--newline must be shift-enter or ctrl-enter (got: $NEWLINE_CHORD)." >&2
            exit 1 ;;
    esac
fi

resolve_python_command() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v python &>/dev/null; then
        echo "python"
    else
        return 1
    fi
}

# ── 출력 서식 ────────────────────────────────────────────────────────
# 워커는 단독으로도 실행되므로 각자 이모지 마커(🔧 ✅ ⚠️ 🧪 …)로 줄을 찍는다. 여기서 그 출력을
# 한 줄씩 받아 온보딩과 같은 clack 거터 모양(◇ │ ✓ ▲ └)으로 바꾼다. 문구는 그대로 둔다.
# stderr 는 손대지 않는다 — 오류는 그 스트림 그대로 보여야 한다.
UI_COLOR=0
[[ -t 1 && -z "${NO_COLOR:-}" ]] && UI_COLOR=1
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) UI_UNICODE=1 ;;
    *) UI_UNICODE=0 ;;
esac
[[ "${TERM:-}" == "linux" ]] && UI_UNICODE=0
if [[ "$UI_UNICODE" -eq 1 ]]; then
    G_BAR="│"; G_START="┌"; G_END="└"; G_SECTION="◇"
    G_OK="✓"; G_WARN="▲"; G_ERR="✗"; G_DRY="○"; G_DOT="•"
else
    G_BAR="|"; G_START="T"; G_END="-"; G_SECTION="o"
    G_OK="+"; G_WARN="!"; G_ERR="x"; G_DRY="o"; G_DOT="-"
fi

ui_paint() {    # SGR 코드, 문자열
    if [[ "$UI_COLOR" -eq 1 ]]; then
        printf '\033[%sm%s\033[0m' "$1" "$2"
    else
        printf '%s' "$2"
    fi
}

ui_format() {
    local line body pad mark color
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ -z "${line//[[:space:]]/}" ]]; then
            printf '%s\n' "$(ui_paint 90 "$G_BAR")"
            continue
        fi
        body="${line#"${line%%[! ]*}"}"
        pad="$(( ${#line} - ${#body} - 2 ))"
        [[ "$pad" -lt 0 ]] && pad=0
        mark=""
        color=""
        case "$body" in
            "🔧 "*|"🧹 "*)
                body="${body#* }"
                body="${body//.../}"
                body="${body#Starting }"
                printf '%s\n%s  %s\n' "$(ui_paint 90 "$G_BAR")" "$(ui_paint 32 "$G_SECTION")" "$(ui_paint 1 "$body")"
                continue ;;
            "── "*)
                body="${body#── }"
                body="${body%% ─*}"
                printf '%s  %s\n' "$(ui_paint 90 "$G_BAR")" "$(ui_paint 1 "$body")"
                continue ;;
            "["[0-9]"/"[0-9]"] "*)
                printf '%s  %s\n' "$(ui_paint 90 "$G_BAR")" "$(ui_paint 1 "${body%...}")"
                continue ;;
            "✅ "*|"🎉 "*) mark="$G_OK"; color=32 ;;
            "⚠️ "*|"⚠ "*) mark="$G_WARN"; color=33 ;;
            "❌ "*) mark="$G_ERR"; color=31 ;;
            "🧪 "*) mark="$G_DRY"; color=36 ;;
            "ℹ️ "*|"💾 "*|"🗑️ "*|"📦 "*|"🐍 "*|"🧾 "*|"🔁 "*|"• "*) mark="$G_DOT"; color=90 ;;
        esac
        # 닫는 가로줄(──────)만 있는 줄은 거터가 대신하므로 버린다.
        [[ -z "${body//─/}" ]] && continue
        if [[ -n "$mark" ]]; then
            body="${body#* }"
            body="${body#"${body%%[! ]*}"}"
            body="$(ui_paint "$color" "$mark") $body"
        fi
        printf '%s  %*s%s\n' "$(ui_paint 90 "$G_BAR")" "$pad" "" "$body"
    done
}

# 워커가 찍는 python 출력이 파이프 너머에서 블록 버퍼링되면 stderr 경고와 순서가 뒤바뀐다.
export PYTHONUNBUFFERED=1

# 대화형 온보딩(ai-setup 의 res/onboard.py)은 jarvis 로 옮기지 않았다. 고르고 승인하는 일은 core 홈의
# AI 셋업 화면과 `python3 -m jarvis module setup plan --json` 이 맡는다. 인자가 없으면 아무것도 설치하지 않는다.
if [[ "$HAD_ARGS" -eq 0 && "$NO_ONBOARD" -eq 0 ]]; then
    echo "Interactive onboarding moved to jarvis: use the AI setup screen on the core home, or run python3 -m jarvis module setup plan --json from the jarvis checkout. Nothing was installed." >&2
    exit 1
fi

run_stage_plan() {
    local py status
    py="$(resolve_python_command)" || { echo "Python 3 is required." >&2; exit 2; }
    local args=(--plan --stages "$STAGES" --project-dir "$(pwd)")
    [[ -n "$HARNESSES" ]] && args+=(--harness "$HARNESSES")
    [[ -n "$JARVIS_ROLE" ]] && args+=(--jarvis-role "$JARVIS_ROLE")
    [[ "$HERDR_FLAG" -eq 1 ]] && args+=(--herdr)
    [[ "$SS_CAPS" -eq 1 ]] && args+=(--ss-caps)
    [[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
    [[ "$UNINSTALL" -eq 1 ]] && args+=(--uninstall)
    [[ -n "$NEWLINE_CHORD" ]] && args+=(--newline "$NEWLINE_CHORD")
    [[ "$FORCE_AGENTSMD" -eq 1 ]] && args+=(--overwrite-agentsmd)
    set +e
    # curl | sh 로 실행되면 stdin 은 스크립트 본문 파이프다. 그대로 물려주면 sudo 가 필요한 단계(Linux 의
    # base 패키지 등)가 대화형 터미널이 없다며 실패하고 뒤 단계가 실행되지 않는다. 온보딩처럼 제어 터미널을
    # 직접 연결하고, 터미널이 없으면(CI) 그대로 둔다.
    if [[ ! -t 0 && "$DRY_RUN" -eq 0 ]] && (: </dev/tty) 2>/dev/null; then
        "$py" -B -X utf8 "$REPO_ROOT/res/stages.py" "${args[@]}" </dev/tty
    else
        "$py" -B -X utf8 "$REPO_ROOT/res/stages.py" "${args[@]}"
    fi
    status=$?
    set -e
    if [[ "$DRY_RUN" -eq 1 && "$status" -eq 0 ]]; then
        echo "DRY RUN complete — nothing was changed."
    fi
    exit "$status"
}

if [[ "$STAGE_RUN" -eq 1 ]]; then
    run_stage_plan
fi

# bash 3.2 (macOS 기본) 는 set -u 에서 빈 배열의 "${arr[@]}" 확장을 unbound variable 로
# 취급한다. ${arr[@]+"${arr[@]}"} 형태로 감싸야 빈 배열이 안전하게 0개 인자로 펼쳐진다.
DRY_FLAG=()
[[ "$DRY_RUN" -eq 1 ]] && DRY_FLAG=(--dry-run)

FORCE_FLAG=()
[[ "$FORCE_AGENTSMD" -eq 1 ]] && FORCE_FLAG=(--force)

HARNESS_FLAG=()
[[ -n "$HARNESSES" ]] && HARNESS_FLAG=(--harness "$HARNESSES")

NEWLINE_FLAG=()
[[ -n "$NEWLINE_CHORD" ]] && NEWLINE_FLAG=(--newline "$NEWLINE_CHORD")

# 설치 출력은 온보딩과 같은 모양의 블록 하나로 묶는다. 끝줄은 어떤 경로로 끝나든(set -e 로
# 워커 실패에 끊겨도) 종료 코드를 보고 찍는다.
ui_close() {
    local rc=$?
    local what="Install"
    [[ "$UNINSTALL" -eq 1 ]] && what="Uninstall"
    printf '%s\n' "$(ui_paint 90 "$G_BAR")"
    if [[ "$rc" -ne 0 ]]; then
        printf '%s  %s\n\n' "$(ui_paint 90 "$G_END")" "$(ui_paint 31 "$what failed (exit $rc) — check the messages above.")"
    elif [[ "$DRY_RUN" -eq 1 ]]; then
        printf '%s  %s\n\n' "$(ui_paint 90 "$G_END")" "DRY RUN complete — nothing was changed."
    else
        printf '%s  %s\n\n' "$(ui_paint 90 "$G_END")" "$what complete"
    fi
}
printf '\n%s  %s\n' "$(ui_paint 90 "$G_START")" "$(ui_paint 1 "ai-setup")"
trap ui_close EXIT

# uninstall: --keybinds --uninstall 은 키바인드만. 범위가 없으면 아래 워커 전부.
# 훅은 jarvis 가 맡으므로 지우지 않고, 영수증의 훅 기록(hook_files·권한 기록)도 jarvis 를 위해 남긴다.
if [[ "$UNINSTALL" -eq 1 ]]; then
    # 제거도 설치와 같은 범위 규칙을 따른다. 범위를 지정했으면 그것만 지운다.
    UN_KEYBINDS="$RUN_KEYBINDS"
    UN_SKILLS="$RUN_SKILLS"
    UN_OMC="$RUN_OMC"
    UN_OUTPUT_STYLE="$RUN_OUTPUT_STYLE"
    UN_MODS="$RUN_MODS"
    if [[ "$RUN_AGENTSMD" -eq 0 && "$RUN_KEYBINDS" -eq 0 && "$RUN_SKILLS" -eq 0 && "$RUN_OMC" -eq 0 && "$RUN_OUTPUT_STYLE" -eq 0 && "$RUN_MODS" -eq 0 ]]; then
        UN_KEYBINDS=1
        UN_SKILLS=1
        UN_OMC=1
        UN_OUTPUT_STYLE=1
        UN_MODS=1
    fi
    # set -e 아래라 한쪽이 실패해도 나머지가 돌도록 종료 코드를 받아 둔다.
    kb_exit=0
    skills_exit=0
    if [[ "$UN_SKILLS" -eq 1 ]]; then
        bash "$SCRIPT_DIR/setup_skills.sh" --uninstall ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format || skills_exit=$?
    fi
    # mod·출력 스타일·캐시 TTL HUD 는 설치(OMC → 출력 스타일 → mod)의 역순으로 되돌린다 — settings.json 을 처음 만든
    # 워커가 마지막에 와야, 다른 워커의 키가 다 빠진 뒤 빈 파일을 지울 수 있다.
    mods_exit=0
    if [[ "$UN_MODS" -eq 1 ]]; then
        bash "$SCRIPT_DIR/setup_mods.sh" --uninstall ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format || mods_exit=$?
    fi
    style_exit=0
    if [[ "$UN_OUTPUT_STYLE" -eq 1 ]]; then
        bash "$SCRIPT_DIR/setup_output_style.sh" --uninstall ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format || style_exit=$?
    fi
    omc_exit=0
    if [[ "$UN_OMC" -eq 1 ]]; then
        bash "$SCRIPT_DIR/setup_omc.sh" --uninstall ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format || omc_exit=$?
    fi
    if [[ "$UN_KEYBINDS" -eq 1 ]]; then
        bash "$SCRIPT_DIR/setup_keybinds.sh" --uninstall ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format || kb_exit=$?
    fi
    [[ "$skills_exit" -ne 0 ]] && exit "$skills_exit"
    [[ "$omc_exit" -ne 0 ]] && exit "$omc_exit"
    [[ "$style_exit" -ne 0 ]] && exit "$style_exit"
    [[ "$mods_exit" -ne 0 ]] && exit "$mods_exit"
    exit "$kb_exit"
fi

# 범위 플래그가 하나도 없으면 전체 설치 (AGENTS.md + 키바인드 + 스킬). 훅은 jarvis 가 맡는다.
# --agentsmd 만 / --keybinds 만 / --omc 만이면 그 범위만.
# OMC·fluent-korean 은 서드파티 플러그인이라, mod 는 최신 Claude Code 에서만 동작해서 기본 전체
# 설치에 끼우지 않는다 — 명시적으로 고른 경우에만.
if [[ "$RUN_AGENTSMD" -eq 0 && "$RUN_KEYBINDS" -eq 0 && "$RUN_SKILLS" -eq 0 && "$RUN_OMC" -eq 0 && "$RUN_OUTPUT_STYLE" -eq 0 && "$RUN_MODS" -eq 0 ]]; then
    RUN_AGENTSMD=1
    RUN_KEYBINDS=1
    RUN_SKILLS=1
fi

if [[ "$RUN_AGENTSMD" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_agentsmd.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} ${FORCE_FLAG[@]+"${FORCE_FLAG[@]}"} ${HARNESS_FLAG[@]+"${HARNESS_FLAG[@]}"} | ui_format
fi

if [[ "$RUN_KEYBINDS" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_keybinds.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} ${NEWLINE_FLAG[@]+"${NEWLINE_FLAG[@]}"} | ui_format
fi

if [[ "$RUN_OMC" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_omc.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format
fi

if [[ "$RUN_OUTPUT_STYLE" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_output_style.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format
fi

if [[ "$RUN_MODS" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_mods.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format
fi

if [[ "$RUN_SKILLS" -eq 1 ]]; then
    bash "$SCRIPT_DIR/setup_skills.sh" ${DRY_FLAG[@]+"${DRY_FLAG[@]}"} | ui_format
fi
