#!/usr/bin/env bash
#
# scripts/setup_*.sh, update-ai.sh, test.sh 가 source 하는 공용 함수 모음.
# Python 실행 명령 찾기와, 없을 때 설치 명령 안내를 담당한다.
# 훅 설치·병합·제거 함수는 훅과 함께 jarvis 훅 모듈로 옮겨 갔다.

resolve_python_command() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v python &>/dev/null; then
        echo "python"
    else
        return 1
    fi
}

# resolve_python_command 가 실패했을 때 이 환경에서 Python 3 을 설치하는 명령을 stderr 로 안내한다.
# 설치는 하지 않는다 — 런타임 설치는 되돌리기 어렵고 uninstall 대칭 밖이라 사용자가 직접 한다.
print_python_install_hint() {
    local cmd=""
    if [[ "$(uname -s)" == "Darwin" ]]; then
        cmd="brew install python"
    elif command -v apt-get &>/dev/null; then
        cmd="sudo apt-get update && sudo apt-get install -y python3"
    elif command -v dnf &>/dev/null; then
        cmd="sudo dnf install -y python3"
    elif command -v yum &>/dev/null; then
        cmd="sudo yum install -y python3"
    elif command -v pacman &>/dev/null; then
        cmd="sudo pacman -S --needed python"
    elif command -v zypper &>/dev/null; then
        cmd="sudo zypper install -y python3"
    elif command -v apk &>/dev/null; then
        cmd="sudo apk add python3"
    fi
    {
        echo "   How to install Python 3:"
        if [[ -n "$cmd" ]]; then
            echo "     $cmd"
        else
            echo "     Install python3 with your distribution package manager (https://www.python.org/downloads/)."
        fi
        echo "   Run again after installing."
    } >&2
}
