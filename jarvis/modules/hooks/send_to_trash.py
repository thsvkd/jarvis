# -*- coding: utf-8 -*-
"""
send_to_trash.py (Windows 전용)
지정된 경로들을 OS 휴지통으로 이동시킨다 (복구 가능한 삭제).
send2trash 가 없거나 오류가 나면 "아무것도 지우지 않고" 실패한다 (fail-closed).
"""
import os
import sys


def report(message):
    """옮긴 결과는 이미 성공이다. 읽는 쪽이 닫힌 파이프(`rm x | head -0`)로 출력이 실패해도 실패로 세지 않는다.
    즉시 flush 해야 종료 시점의 flush 오류(exit 120)로 번지지 않는다. 파이썬 문서의 SIGPIPE 처리법을 따른다."""
    try:
        print(message, flush=True)
    except OSError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def main() -> int:
    # 한국어 Windows 콘솔(cp949)은 🗑 를 인코딩하지 못한다. 출력이 깨져도 옮긴 결과는 성공이다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = sys.argv[1:]
    # `rm -f` / `del /f` 의 의미 보존: 없는 파일은 조용히 넘어간다. 호출자가 이 플래그를
    # 항상 경로들 앞에 붙이므로 첫 인자만 본다.
    force = bool(argv) and argv[0] in ("-f", "--force")
    paths = argv[1:] if force else argv
    # `--` 뒤는 전부 경로다. `-` 로 시작하는 파일 이름을 옵션으로 오인하지 않게 한다.
    if paths and paths[0] == "--":
        paths = paths[1:]

    if not paths:
        print("send_to_trash: no target paths given; nothing to do.", file=sys.stderr)
        return 0

    try:
        from send2trash import send2trash
    except ImportError:
        # 127 은 '휴지통으로 옮길 수단이 없다'는 뜻으로 호출자(rm 셰임)와 약속된 코드다.
        # 셰임은 이 코드를 보고 경고 후 원본 rm 으로 넘긴다 — 아무것도 못 지우게 만드는
        # 쪽이 더 해롭기 때문이다.
        print("send_to_trash: the send2trash module is missing, so nothing can be moved to the trash.", file=sys.stderr)
        print("Install: pip install send2trash", file=sys.stderr)
        return 127

    had_failure = False
    for p in paths:
        if force and not os.path.exists(p):
            continue
        try:
            # Git Bash 는 /c/x 를 C:/x 로 넘기는데 Windows send2trash 는 슬래시 경로를 E_INVALIDARG 로 거부한다.
            # Win32 는 원래 경로를 문자열로 정규화하므로 abspath 가 의미를 바꾸지 않는다.
            send2trash(os.path.abspath(p) if os.name == "nt" else p)
        except Exception as exc:  # noqa: BLE001 - 사용자에게 그대로 보여줘야 함
            had_failure = True
            print(f"FAILED: {p} ({exc})", file=sys.stderr)
            continue
        report(f"🗑️  Moved to trash: {p}")

    return 1 if had_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
