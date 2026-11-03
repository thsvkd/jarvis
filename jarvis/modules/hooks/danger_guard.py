#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
danger_guard.py — PreToolUse hook (Claude Code, 유저 레벨, POSIX/Windows 공용)

Bash 툴(POSIX 셸·Git Bash)과 PowerShell 툴 양쪽을 대상으로 한다. 둘 다 tool_input.command
에 실행할 명령 문자열을 담는다.

설계 원칙 — 이 훅의 가치는 '차단'이 아니라 '복구'다.
  실사용 로그 10,329건 전수 분석(2026-09):
    · deny/ask 53건 중 진짜 위험한 명령은 0건. 전부 테스트 픽스처거나 오탐이었다
      (평범한 셸 함수 정의를 포크밤으로, 히어독 본문을 curl|sh 로 오인 등).
    · 반대로 휴지통 치환 311건과 git 백업 22건은 실제로 작동했다.
  게다가 오탐의 상당수는 훅이 '명령 문자열을 재작성'하다 생겼다. 훅은 셸이 아니어서
  $VAR·글로브·따옴표 확장을 흉내 낼 수 없는데도 인자를 다시 따옴표로 감싸 넘겼다
  (`rm -rf "$FOO/bar"` → `trash-put '$FOO/bar'` → 확장이 막혀 실패).
  그래서 재작성을 버리고, 판정은 최소화하고, 복구만 남긴다.

정책:
  1) 삭제 → 휴지통. 인자를 건드리지 않는다.
     Bash      : SessionStart 에서 CLAUDE_ENV_FILE 에 'PATH 맨 앞에 shims/' 를 적는다. Claude
                 Code 가 이 파일을 매 Bash 명령 전에 source 하므로 명령 문자열을 바꿀 필요가
                 없다. 종전처럼 명령 앞에 줄을 붙이면 권한 규칙이 바뀐 명령에 대해 평가돼
                 `Bash(rm *)` 같은 사용자 허용 규칙이 맞지 않게 되고, allow 를 붙이면 권한
                 체계를 통째로 우회한다(둘 다 실측, CC 2.1.283). shims/rm 은 셸이 확장을
                 끝낸 뒤의 argv 를 받으므로 해석이 원본 rm 과 어긋날 수 없고, `find -exec rm`·
                 `xargs rm`·스크립트 내부 rm 도 PATH 로 해석되므로 같은 경로를 탄다.
     PowerShell: CLAUDE_ENV_FILE 은 Bash 전용이라, shims/RmShim.psm1 을 Import-Module 하는
                 한 줄을 updatedInput 으로 붙인다(판정 없이 — allow 를 붙이면 권한 우회). 명령 우선순위가
                 별칭 > 함수 > cmdlet 이라 같은 이름의 함수가 Remove-Item cmdlet 을 가리고,
                 rm/del/erase/ri/rd/rmdir 별칭이 전부 그 함수로 해석된다.
     한계: `sudo rm` 은 sudo 의 secure_path 가 PATH 를 버려 셰임을 타지 않는다
     (실사용 10,329건 중 2건). 셰임별 규칙은 각 셰임 파일의 주석 참고.
  2) git 미커밋 변경 보호. 명령 문자열을 건드리지 않는다.
     훅 프로세스가 직접 스크래치 인덱스로 스냅샷 커밋을 만들어 refs/claude-backups/<ts>
     에 남기고, 원본 명령은 한 글자도 바꾸지 않고 통과시킨다. `git stash create` 는 추적
     안 된 파일을 담지 못해서(검증됨) 임시 GIT_INDEX_FILE + `add -A` + `write-tree` 를 쓴다.
     대상은 '미커밋 변경을 되돌릴 수 없게 만드는 것'뿐이다 — reflog/ORIG_HEAD 가 이미
     되돌려 주는 rebase·branch -D·commit --amend·filter-branch·gc 와, gc 유예기간 안에서
     `git fsck --unreachable` 로 살릴 수 있는 stash drop/clear 는 다루지 않는다.
     오탐의 대가가 '쓸데없는 백업 ref 한 개'뿐이라 패턴을 느슨하게 잡아도 안전하다.
  3) deny 는 없다. 위험이 자명한 셋만 ask 로 사람에게 묻고 나머지는 판정하지 않는다.
       - 루트/홈 전체 삭제 (rm 으로 / · /* · ~ · $HOME ; Remove-Item 류로 드라이브 루트·홈)
       - 디스크 파괴 (mkfs*·newfs*·wipefs -a, dd of=<디스크>, > <디스크>, /dev/ 대상 파티션 편집기
         (-l·-p 같은 목록 보기는 뺀다), diskutil erase*, format <드라이브>:, diskpart, Clear-Disk 류)
       - main/master 의 force push(+refspec, --force-with-lease 포함)·삭제, --mirror
     명령 문자열을 정규식으로 훑지 않고 셸처럼 따옴표를 묶어 낱말로 나눈 뒤 명령 자리의 낱말만
     본다(heredoc 본문은 버린다). 종전 정규식 deny 는 python -c·printf·heredoc·grep 검색어 안의 글자까지
     막아 2026-10 s2 로그 26건이 전부 오탐이었다.
  4) push 판정은 완전하지 않다. 같은 명령 줄의 앞선 checkout·config, 확장, `bash -c`, 별칭이 비껴간다
     (완전한 판정기는 2026-10-11 적대적 리뷰에서 두 차례 무너졌다). 그래서 자명한 꼴만 묻고, 나머지는
     auto mode 분류기의 Git Destructive 규칙과 원격의 브랜치 보호가 맡는다.

  종전에 있었으나 실측 근거로 제거한 것: 포크밤 패턴, curl|sh 패턴, find/xargs 대량 삭제
  ask, 체이닝 삭제 ask, 글로브 미일치 deny, `.`/`..` deny, 대상 100개 초과 ask,
  chmod 777 ask, shred ask, shutdown/reboot ask, 훅 내부 오류 ask, 기본 브랜치 push 확인,
  정규식 루트 삭제·디스크 파괴 deny, 그리고 시크릿 파일 접근 ask (secrets_guard — 파괴가 아니라
  유출 위험이고, 그쪽은 auto mode 분류기가 맡는다).

이 훅은 macOS 기본 인터프리터(/usr/bin/python3, 3.9.x)에서도 실행되어야 한다. 3.10+ 전용
문법(PEP 604 `X | None` 애노테이션 등)을 쓰지 않고, 애노테이션은 `from __future__ import
annotations` 로 지연 평가시킨다.
"""
from __future__ import annotations

import sys
import json
import shlex
import re
import os
import datetime
import fnmatch
import subprocess
import tempfile

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HOOKS_DIR, "guard.log")
SHIM_DIR = os.path.join(HOOKS_DIR, "shims")
PS_SHIM = os.path.join(SHIM_DIR, "RmShim.psm1")

# --- ask: 위험이 자명한 명령만 사용자에게 묻는다 ---------------------------------------
#
# 종전에는 루트/홈 삭제·디스크 파괴를 명령 문자열 전체에 정규식을 대어 deny 했다. 그래서 python -c·
# printf·heredoc·grep 검색어 '안'의 `rm -rf ~`·`mkfs` 글자까지 막았다(2026-10 s2 로그 26건이 전부
# 오탐, 진짜 위험한 명령은 0건). 이제는 셸처럼 따옴표를 묶어 낱말로 나누고(heredoc 본문은 버린다)
# 명령 자리에 온 실제 낱말만 본다. 따옴표 안은 데이터다. 막지 않고 묻는다.
# 루트/홈 삭제는 셰임(shims/rm, RmShim.psm1)이 argv 수준에서도 거부한다. 여기서 묻는 것은 셰임을
# 타지 않는 sudo rm 과 실행 전에 사람에게 알리기 위해서다. `bash -c "rm -rf /"` 처럼 따옴표 안에
# 숨은 명령은 묻지 않는다(셰임이 거부한다).
RM_WORDS = {"rm", "remove-item", "ri", "rd", "rmdir", "del", "erase"}
HOME_WORDS = {"~", "$home", "${home}", "$userprofile", "${userprofile}", "%userprofile%", "$env:userprofile",
              "${env:userprofile}"}
DRIVE_RE = re.compile(r"^[A-Za-z]:(?:[\\/]+Users[\\/]+[^\\/]+)?$", re.IGNORECASE)  # C: · C:\Users\<이름>
# Git Bash 의 드라이브 루트 `/c` 와 홈 `/c/Users/<이름>`. Linux·macOS 에서는 평범한 경로라
# 문자열만으로는 가를 수 없으므로 훅이 도는 플랫폼으로 정한다.
IS_WINDOWS = os.name == "nt" or sys.platform.startswith(("cygwin", "msys"))
GITBASH_DRIVE_RE = re.compile(r"^/[A-Za-z](?:/+Users/+[^/]+)?$", re.IGNORECASE)
DISK_DEV_RE = re.compile(r"^/dev/(?:sd|nvme|hd|r?disk|mmcblk|vd|xvd)[a-z0-9]*$")
WIPE_TOOLS_RE = re.compile(r"^(?:mkfs(?:\.\w+)?|newfs(?:_\w+)?)$")
PARTITION_TOOLS = {"fdisk", "gdisk", "sgdisk", "sfdisk", "cfdisk", "parted"}
LIST_FLAGS = {"-l", "--list", "-p", "--print", "print", "-d", "--dump"}  # 목록·백업만 하는 호출
DISKUTIL_ERASE = {"erasedisk", "erasevolume", "secureerase", "partitiondisk", "reformat", "zerodisk",
                  "deletecontainer"}
PROTECTED_BRANCHES = {"main", "master"}
# 명령 앞에 붙어 실제 명령을 뒤로 미루는 낱말. 뒤따르는 옵션·숫자(timeout 5, nice -n 5)도 건너뛴다.
WRAPPERS = {"sudo", "doas", "env", "command", "exec", "builtin", "nohup", "time", "nice", "ionice", "timeout",
            "stdbuf", "setsid", "taskset", "chrt", "if", "then", "else", "elif", "while", "until", "do", "{", "!"}
OPERATOR_CHARS = set("();<>|&")
HEREDOC_RE = re.compile(r"<<-?(?!<)\s*\\?['\"]?([^\s'\"<>;&|()]+)['\"]?")  # `<<<` here-string 은 아니다
# 래퍼마다 값을 받는 옵션(sudo -u root, timeout -s KILL 5, nice -n 5). 값을 명령으로 읽지 않게 함께 건너뛴다.
# 래퍼마다 따로 둔다 — sudo 의 -n·-S·-k·-s 는 값이 없다.
WRAPPER_VALUE_OPTS = {
    "sudo": {"-u", "-g", "-C", "-D", "-p", "-r", "-t", "-T", "-U", "--user", "--group", "--chdir", "--prompt"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata", "--pid"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
}
HOME_DIR = os.path.expanduser("~").rstrip("/\\")

# PowerShell 삭제 동사가 구간 맨 앞(= 명령 자리)에 있는지. 셰임 모듈을 불러올지 정할 때 쓴다.
PS_DELETE_RE = re.compile(
    r"""^\s*(?:\$[\w:]+\s*=\s*)?(?:&\s*)?['"]?(?:[\w:\\/.-]*[\\/])?"""
    r"""(?:Remove-Item|ri|rd|rmdir|del|erase|rm)\b""", re.IGNORECASE)
# 구간 나누기용. 따옴표를 보지 않는 거친 분할이라 잘못 쪼개도 검사가 좁아질 뿐 넓어지지 않는다.
SEGMENT_SPLIT_RE = re.compile(r"[\n;|&]+")
# 따옴표 밖의 괄호·중괄호도 나눈다 — `if (…) { Remove-Item … }`, `$(Remove-Item …)` 의 삭제 동사도
# 구간 맨 앞에 오게. 따옴표 안(`git commit -m "fix (del ~)"`)은 산문이라 그대로 둔다.
QUOTED_OR_BRACKET_RE = re.compile(r"""("[^"]*"|'[^']*')|[(){}]""")


def _segments(command):
    return SEGMENT_SPLIT_RE.split(
        QUOTED_OR_BRACKET_RE.sub(lambda m: m.group(1) or "\n", command))

# --- 셰임 선행 ------------------------------------------------------------------

# --- git 스냅샷 -----------------------------------------------------------------

BACKUP_PREFIX = "refs/claude-backups/"
PRUNE_DAYS = 14

# `git clean -fdx` 는 .gitignore 대상까지 지우므로 스냅샷도 그것들을 담아야 한다. 그런데
# 무시된 파일은 node_modules/.venv/빌드 산출물이라 규모가 다르다 — 실측(라즈베리파이 5):
# 한 저장소가 52,722개 1,244MB 였고, 이 기기의 해싱 처리량은 100MB/2,000파일당 4.4초다.
# 그대로 담으면 훅이 1~2분을 잡아먹고 SD 카드에 1.2GB 를 쓴다. 목록 나열은 169ms 로 싸니,
# 먼저 규모를 재고 상한을 넘으면 무시된 파일은 포기한다(추적/미추적 스냅샷은 그대로 남는다).
MAX_IGNORED_FILES = 10000
MAX_IGNORED_BYTES = 100 * 1024 * 1024

# git·하위 명령·플래그 사이는 구분자를 넘지 않는다 — `git add x && rm -f y` 를 `git rm -f` 로,
# `git switch main && ls -lf` 를 강제 전환으로 보지 않게.
GIT_WIPE_RE = re.compile(r"""\bgit\b[^\n;&|]*?\b(?:
      reset\b[^\n;&|]*?--hard
    | (?:checkout|switch)\b[^\n;&|]*?(?:--force\b|\s-[a-zA-Z]*f)
    | switch\b[^\n;&|]*?--discard-changes\b
    | checkout\b[^\n;&|]*?(?:\s--\s|\s\.(?:\s|$))
    | restore\b
    | clean\b[^\n;&|]*?(?:\s-[a-zA-Z]*f|--force\b)
    | rm\b[^\n;&|]*?(?:\s-[a-zA-Z]*f|--force\b)
    | worktree\b[^\n;&|]*?\bremove\b
)""", re.VERBOSE)

# `git checkout <path>` 는 `--` 없이도 미커밋 편집을 덮어쓰지만, 같은 꼴의 `git checkout <branch>` 는
# 잃는 것이 없다. 그래서 인자가 대상 디렉터리에 실제 경로로 있거나, 글로브·pathspec 매직(브랜치
# 이름에 올 수 없는 `*?[:`)이거나, 비옵션 인자가 둘 이상(<tree-ish> <path>)일 때만 스냅샷한다.
CHECKOUT_ARGS_RE = re.compile(r"\bgit\b[^\n;&|]*?\bcheckout\b([^\n;&|]*)")
PATHSPEC_MAGIC_RE = re.compile(r"[*?\[:]")

# -x/-X 는 .gitignore 대상(.env, 로컬 설정)까지 지운다 → 스냅샷에도 포함해야 한다.
CLEAN_IGNORED_RE = re.compile(r"\bclean\b[^\n;&|]*?\s-[a-zA-Z]*[xX]")
# `git worktree remove <path>` 의 대상은 cwd 가 아니라 인자다.
WORKTREE_RE = re.compile(r"\bworktree\s+remove\s+(?:(?:--force|-f)\s+)*(\S+)")
# 명령이 `cd <경로>` 로 시작하면 백업도 거기서 돌아야 한다. 경로에 변수가 섞이면 특정 불가 →
# 포기한다. 변수 검사는 경로 토큰에만 한다 — 줄 전체를 보면 뒤따르는 `echo "$X"` 때문에 cd 를
# 놓치고 세션 cwd(엉뚱한 저장소)를 백업한다.
LEADING_CD_RE = re.compile(r"^\s*cd\s+['\"]?([^\s'\";&|]+)")


def log(entry):
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        first_write = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            if first_write:
                try:
                    os.chmod(LOG_PATH, 0o600)
                except OSError:
                    pass
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def emit(decision, reason, updated_input=None, context=None):
    """판정을 내보내고 종료한다.

    decision 이 None 이면 permissionDecision 을 넣지 않는다 — 명령은 평소 권한 규칙대로
    흐르고 additionalContext 만 모델에게 전달된다. 백업 안내처럼 '알리되 승인을 대신하지는
    않아야 하는' 정보에 쓴다.
    """
    out = {"hookEventName": "PreToolUse"}
    if decision is not None:
        out["permissionDecision"] = decision
        out["permissionDecisionReason"] = reason
    # updatedInput 은 판정 없이도 적용된다. 여기에 allow 를 붙이면 권한 프롬프트와 auto mode
    # 분류기를 건너뛰므로, 셰임 선행 줄은 반드시 판정 없이 내보낸다(실측, CC 2.1.283).
    if updated_input is not None:
        out["updatedInput"] = updated_input
    if context:
        out["additionalContext"] = context
    print(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))
    sys.exit(0)


def _git(args, cwd, env=None, timeout=60):
    # 바이트로 받아 직접 푼다. 로캘(Windows 한국어는 cp949)로 풀면 UTF-8 경로가 깨지고,
    # surrogateescape 는 이름의 바이트를 보존한다.
    p = subprocess.run(["git"] + args, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    p.stdout = p.stdout.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    p.stderr = p.stderr.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    return p


# --- ask 판정 --------------------------------------------------------------------


def _clean(command, powershell):
    """따옴표 밖의 주석과 heredoc 본문을 버리고 줄 이음을 잇는다. 따옴표 안은 그대로 둔다.

    셸처럼 `#` 은 낱말 첫머리에서만 주석이다(`$#`, `${#a[@]}`, `x/#frag` 는 아니다).
    """
    esc = "`" if powershell else "\\"
    command = command.replace("\r\n", "\n")
    out, quote, ends, i, n = [], None, [], 0, len(command)
    while i < n:
        c, nxt = command[i], command[i + 1:i + 2]
        if c == esc and nxt == "\n" and quote != "'":  # 줄 이음
            i += 2
        elif quote:
            if c == esc and quote == '"' and nxt:
                out.append(c + nxt)
                i += 2
                continue
            quote = None if c == quote else quote
            out.append(c)
            i += 1
        elif c == esc and nxt:
            out.append(c + nxt)
            i += 2
        elif c in "'\"":
            quote = c
            out.append(c)
            i += 1
        elif c == "#" and (not out or out[-1][-1] in " \t\n;&|()"):
            while i < n and command[i] != "\n":
                i += 1
        elif c == "\n" and ends:  # heredoc 본문을 끝 표지 줄까지 건너뛴다(<<- 는 앞 탭을 허용)
            out.append(c)
            i += 1
            for end in ends:
                while i < n:
                    j = command.find("\n", i)
                    line, i = command[i:n if j < 0 else j], n if j < 0 else j + 1
                    if line.strip() == end:
                        break
            ends = []
        else:
            m = HEREDOC_RE.match(command, i) if c == "<" and not (out and out[-1][-1] == "<") else None
            recent = "".join(out[-60:])
            if m and recent.rfind("((") > recent.rfind("))"):  # 산술 $((a<<b)) 의 시프트다
                m = None
            if m:
                ends.append(m.group(1))
                out.append(m.group(0))
                i = m.end()
            else:
                out.append(c)
                i += 1
    return "".join(out)


def _tokens(command, powershell):
    """따옴표를 벗긴 낱말과 연산자. 주석·heredoc 본문은 데이터라 버린다. 토큰화하지 못하면 [] (묻지 않는다)."""
    try:
        lex = shlex.shlex(_clean(command, powershell).replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""  # 주석은 _clean 이 낱말 첫머리에서만 걷었다
        if powershell:
            lex.escape = ""  # 백슬래시는 경로 구분자다(`C:\temp`). 이어 붙은 따옴표 조각은 한 낱말이다
        tokens = list(lex)
    except ValueError:
        return []
    # PowerShell 배열은 쉼표로 잇는다(`"$HOME","x"`)
    return [p for t in tokens for p in t.split(",") if p] if powershell else tokens


def _is_op(token):
    return bool(token) and set(token) <= OPERATOR_CHARS


def _base(word):
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _argv(words):
    """앞의 대입(FOO=1, PowerShell `$x =`)과 래퍼(sudo·timeout 5·nice -n 5 …)를 떼어 낸 실제 argv."""
    i = 0
    while i < len(words):
        if re.match(r"^[A-Za-z_]\w*=", words[i]):
            i += 1
        elif words[i].startswith("$") and words[i + 1:i + 2] == ["="]:
            i += 2
        elif _base(words[i]) in WRAPPERS:
            takes_value = WRAPPER_VALUE_OPTS.get(_base(words[i]), ())
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^\d[\d.]*[smhd]?$", words[i])):
                i += 2 if words[i] in takes_value else 1
        else:
            break
    return words[i:]


def _root_or_home(word):
    w = re.sub(r"[\\/]*\*?$", "", word)  # 끝의 `/`·`/*` 를 뗀다: `/*`·`~/`·`"$HOME"/*` 도 전체다
    return ((w == "" and word[:1] in ("/", "\\")) or w.lower() in HOME_WORDS or (bool(w) and w == HOME_DIR)
            or bool(DRIVE_RE.match(w)) or (IS_WINDOWS and bool(GITBASH_DRIVE_RE.match(w))))


def _destroys_disk(name, args):
    on_dev = any(a.startswith("/dev/") for a in args)  # 이미지 파일·--help 는 디스크를 건드리지 않는다
    if name in ("diskpart", "clear-disk", "initialize-disk", "remove-partition", "clear-volume", "format-volume"):
        return True
    if WIPE_TOOLS_RE.match(name):
        return on_dev
    if name == "wipefs":  # -a 가 없으면 서명 목록만 보고, -n 은 시험 실행이다
        return on_dev and any(a == "--all" or re.match(r"^-[a-z]*a", a) for a in args) \
            and not any(a == "--no-act" or re.match(r"^-[a-z]*n", a) for a in args)
    if name == "dd":
        return any(a.startswith("of=") and DISK_DEV_RE.match(a[3:]) for a in args)
    if name in PARTITION_TOOLS:
        return any(DISK_DEV_RE.match(a) for a in args) and not LIST_FLAGS & set(args)
    if name == "diskutil":
        return any(a.lower() in DISKUTIL_ERASE for a in args)
    if name in ("format", "format.com"):  # `format` 은 흔한 낱말이라 드라이브 문자를 함께 요구한다
        return any(re.match(r"^[A-Za-z]:[\\/]?$", a) for a in args)
    return False


def _current_branch(command, cwd):
    dirs = _target_dirs(command, cwd)
    if not dirs:
        return None
    try:
        p = _git(["symbolic-ref", "--short", "-q", "HEAD"], dirs[0], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _pushes_over_protected(args, command, cwd):
    """main/master 를 force push·삭제하거나 --mirror 인 push 인가. args 는 `push` 뒤의 낱말."""
    opts = [a for a in args if a.startswith("-")]
    refs = [a for a in args if not a.startswith("-")][1:]  # 첫 위치 인자는 원격이다
    if "--dry-run" in opts or any(re.match(r"^-[a-zA-Z]*n", o) for o in opts):  # 시험 실행은 바꾸지 않는다
        return False
    if "--mirror" in opts:
        return True
    # --force-if-includes 는 --force-with-lease 없이는 아무 일도 하지 않는다
    force = any(o.startswith("--force") and o != "--force-if-includes" or re.match(r"^-[a-zA-Z]*f", o) for o in opts)
    delete = any(o == "--delete" or re.match(r"^-[a-zA-Z]*d", o) for o in opts)
    if "--all" in opts or "--branches" in opts:
        return force
    if "--tags" in opts and not refs:  # 태그만 민다
        return False
    current = []  # 지금 브랜치는 필요할 때 한 번만 묻는다(git 하위 프로세스)

    def dest(ref):
        d = ref.lstrip("+").rsplit(":", 1)[-1]
        d = d[len("refs/heads/"):] if d.startswith("refs/heads/") else d
        if d in ("HEAD", "@"):
            if not current:
                current.append(_current_branch(command, cwd))
            return current[0]
        return d

    def protected(d):  # 글로브 refspec(`refs/heads/*`)은 main/master 와 맞는지로 본다
        return d is not None and any(fnmatch.fnmatchcase(b, d) for b in PROTECTED_BRANCHES)

    if not refs:
        return force and protected(dest("HEAD"))
    return any(protected(dest(r)) and (force or delete or r.startswith(("+", ":"))) for r in refs)


def ask_reason(tool_name, command, cwd):
    """(로그 판정 이름, 사용자에게 보일 이유) 또는 None. 위험이 자명한 것만 묻고 나머지는 판정하지 않는다."""
    tokens = _tokens(command, powershell=tool_name == "PowerShell")
    for i, t in enumerate(tokens[:-1]):
        if _is_op(t) and ">" in t and DISK_DEV_RE.match(tokens[i + 1]):
            return "ask-disk-destroy", "Confirm: this writes over a whole disk or partition."
    words = []
    for t in tokens + [";"]:
        if not _is_op(t):
            words.append(t)
            continue
        argv, words = _argv(words), []
        if not argv:
            continue
        name, args = _base(argv[0]), argv[1:]
        # PowerShell 은 `-Path:$HOME` 처럼 매개변수에 값을 붙여 쓸 수 있다
        targets = [a.split(":", 1)[1] if a.startswith("-") else a for a in args if not a.startswith("-") or ":" in a]
        if name in RM_WORDS and any(_root_or_home(a) for a in targets):
            return "ask-root-delete", "Confirm: this deletes an entire root/home directory."
        if _destroys_disk(name, args):
            return "ask-disk-destroy", "Confirm: this destroys a disk or partition."
        if name == "git":
            j = 0
            while j < len(args) and args[j].startswith("-"):  # git 전역 옵션(-C 경로, -c 키=값, --no-pager)
                j += 2 if args[j] in ("-C", "-c") else 1
            if args[j:j + 1] == ["push"] and _pushes_over_protected(args[j + 1:], command, cwd):
                return "ask-protected-push", "Confirm: this force-pushes, deletes or mirrors main/master."
    return None


# --- git 스냅샷 백업 -------------------------------------------------------------


def _target_dirs(command, cwd):
    """백업을 돌릴 디렉터리들. 대개 cwd 하나지만 worktree remove 는 대상이 인자에 있다."""
    dirs = []
    base = cwd
    m = LEADING_CD_RE.match(command)
    if m:
        if "$" in m.group(1) or "`" in m.group(1):
            return []
        cand = os.path.expanduser(m.group(1))
        cand = cand if os.path.isabs(cand) else os.path.join(cwd, cand)
        if os.path.isdir(cand):
            base = cand
    for d in _git_c_dirs(command, base):
        if d not in dirs:
            dirs.append(d)
    for wt in WORKTREE_RE.findall(command):
        if "$" in wt or "`" in wt:
            continue
        wt = os.path.expanduser(wt.strip("'\""))
        wt = wt if os.path.isabs(wt) else os.path.join(base, wt)
        if os.path.isdir(wt) and wt not in dirs:
            dirs.append(wt)
    return dirs


def _git_c_dirs(command, base):
    """git 호출마다 실행될 디렉터리. `-C` 는 git 처럼 누적하고(`-C a -C b` = a/b), 따옴표는
    shlex 로 푼다. 풀 수 없는 -C(변수, 없는 경로)나 토큰화 실패는 base 로 대신한다 — 아무것도
    안 하느니 엉뚱한 곳일 수 있어도 백업하는 편이 낫다(오탐 비용은 ref 하나)."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return [base]
    out = []
    for i, t in enumerate(toks):
        if os.path.basename(t) not in ("git", "git.exe"):
            continue
        d, j = base, i + 1
        while j < len(toks) and toks[j].startswith("-"):
            if toks[j] in ("-C", "-c") and j + 1 < len(toks):
                if toks[j] == "-C":
                    d = os.path.join(d, os.path.expanduser(toks[j + 1]))
                j += 1
            j += 1
        d = os.path.normpath(d)
        out.append(d if os.path.isdir(d) else base)
    return out or [base]


def _checkout_names_path(command, d):
    for m in CHECKOUT_ARGS_RE.finditer(command):
        args = m.group(1).split()
        if {"-b", "-B", "--orphan"} & set(args):
            continue
        names = [a.strip("'\"") for a in args if not a.startswith("-")]
        if len(names) > 1 or any(PATHSPEC_MAGIC_RE.search(n) or os.path.exists(os.path.join(d, n))
                                 for n in names):
            return True
    return False


def _prune(repo):
    """오래된 백업 ref 를 지운다 — 안 지우면 무한히 쌓인다."""
    cutoff = (datetime.datetime.now()
              - datetime.timedelta(days=PRUNE_DAYS)).strftime("%Y%m%dT%H%M%S")
    out = _git(["for-each-ref", "--format=%(refname)", BACKUP_PREFIX], repo).stdout
    for ref in out.split():
        if ref[len(BACKUP_PREFIX):] < cutoff:
            _git(["update-ref", "-d", ref], repo)


def _ignored_volume(repo):
    """무시된 파일까지 담을 때의 규모. 반환: (감당 가능한가, 파일 수, 바이트)."""
    out = _git(["ls-files", "-o", "-z"], repo).stdout
    names = [n for n in out.split("\0") if n]
    if len(names) > MAX_IGNORED_FILES:
        return False, len(names), None
    total = 0
    for n in names:
        try:
            total += os.path.getsize(os.path.join(repo, n))
        except OSError:
            pass
        if total > MAX_IGNORED_BYTES:
            return False, len(names), total
    return True, len(names), total


def git_snapshot(command, cwd):
    """되돌릴 대상을 refs/claude-backups/ 에 고정한다. 반환: 사람에게 보여줄 문구 목록."""
    wipe = GIT_WIPE_RE.search(command)
    if not wipe and not CHECKOUT_ARGS_RE.search(command):
        return []
    dirs = _target_dirs(command, cwd)
    if not wipe:
        dirs = [d for d in dirs if _checkout_names_path(command, d)]

    wants_ignored = bool(CLEAN_IGNORED_RE.search(command))
    notes = []
    for d in dirs:
        r = _git(["rev-parse", "--show-toplevel"], d)
        if r.returncode != 0:
            continue
        repo = r.stdout.strip()

        include_ignored = wants_ignored
        if include_ignored:
            ok, count, size = _ignored_volume(repo)
            if not ok:
                include_ignored = False
                notes.append(
                    "%s has %d .gitignore'd entries%s, so they were left out of the snapshot; including them would "
                    "make the hook take tens of seconds to several minutes. Tracked/untracked files were backed up as usual."
                    % (repo, count, ("" if size is None else " %.0fMB" % (size / 1024 / 1024))))

        fd, idx = tempfile.mkstemp(prefix="claude-backup-idx-")
        os.close(fd)
        os.unlink(idx)                     # git 이 새로 만들게 둔다
        env = dict(os.environ, GIT_INDEX_FILE=idx)
        try:
            head = _git(["rev-parse", "--verify", "HEAD"], repo).stdout.strip()
            if head:
                _git(["read-tree", "HEAD"], repo, env)
            add = ["add", "-A"] + (["-f"] if include_ignored else []) + ["--", "."]
            if _git(add, repo, env).returncode != 0:
                continue
            tree = _git(["write-tree"], repo, env).stdout.strip()
            if not tree:
                continue

            # 되돌릴 것이 없으면(HEAD 와 같은 트리) 백업하지 않는다.
            if head and _git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip() == tree:
                continue
            # 직전 스냅샷과 트리가 같아도 새 ref 를 만들지 않는다.
            last = _git(["for-each-ref", "--sort=-refname", "--count=1",
                         "--format=%(objectname)", BACKUP_PREFIX], repo).stdout.strip()
            if last and _git(["rev-parse", last + "^{tree}"], repo).stdout.strip() == tree:
                continue

            msg = "claude-backup: " + command.splitlines()[0][:120]
            args = ["commit-tree", tree] + (["-p", head] if head else []) + ["-m", msg]
            commit = _git(args, repo, env).stdout.strip()
            if not commit:
                continue
            ref = BACKUP_PREFIX + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            if _git(["update-ref", ref, commit], repo).returncode == 0:
                _prune(repo)
                notes.append("Backed up uncommitted changes in %s to %s "
                             "(restore with git checkout %s -- .)." % (repo, ref, ref))
        finally:
            if os.path.exists(idx):
                os.unlink(idx)
    return notes


# --- 셰임 선행 줄 ----------------------------------------------------------------


def write_shim_path(env_file):
    """SessionStart: 이후 모든 Bash 명령의 PATH 맨 앞에 shims/ 를 둔다.

    stdout 에는 아무것도 쓰지 않는다 — SessionStart 의 stdout 은 모델 컨텍스트에 들어간다.
    """
    if not env_file or not os.path.isdir(SHIM_DIR):
        return
    d = SHIM_DIR
    if os.name == "nt":
        # Git Bash 의 PATH 는 ':' 구분이라 `C:\...` 를 그대로 넣으면 쪼개진다 → /c/... 로.
        drive, rest = os.path.splitdrive(d)
        d = "/" + drive.rstrip(":").lower() + rest.replace("\\", "/")
    q = shlex.quote(d)
    # startup/resume/clear/compact 마다 다시 불려 줄이 쌓여도 PATH 가 길어지지 않게 한다.
    # newline 고정: Windows 텍스트 모드는 \r\n 을 쓰고, bash 는 그 줄을 문법 오류로 버린다.
    with open(env_file, "a", encoding="utf-8", newline="\n") as f:
        f.write('case ":$PATH:" in *:%s:*) ;; *) PATH=%s:$PATH; export PATH ;; esac\n' % (q, q))


def shim_prefix(tool_name, command):
    """PowerShell 명령 앞에 붙일 한 줄. 붙일 이유가 없으면 None.

    원본 명령은 한 글자도 바꾸지 않는다 — 인자 해석은 전적으로 PowerShell 몫이다.
    """
    if tool_name == "PowerShell":
        # 명령 맨 앞만 보면 `ls | Remove-Item`, `cd x; del y` 의 삭제가 셰임 없이 영구 삭제된다.
        if (not any(PS_DELETE_RE.search(s) for s in _segments(command))
                or not os.path.isfile(PS_SHIM)):
            return None
        p = PS_SHIM.replace("'", "''")
        return ("if (-not (Get-Module RmShim)) { Import-Module '%s' -DisableNameChecking "
                "-Global -ErrorAction SilentlyContinue }\n" % p)

    return None


# --- 진입점 ----------------------------------------------------------------------


def read_event_json():
    """stdin 에서 훅 이벤트 JSON 을 읽는다. 선행 BOM(U+FEFF)은 벗겨낸다.

    BOM 이 남아 있으면 json.loads 가 실패하고, 이 훅은 파싱 실패를 '판정할 수 없는 입력'으로
    보고 조용히 exit 0 한다 — 가드가 통째로 무력화되는데 아무 신호도 남지 않는다. Windows
    PowerShell 5.1 의 Process.StandardInput 이 실제로 이 상황을 만든다(AutoFlush StreamWriter
    가 스트림 접근 시점에 인코딩 preamble 을 먼저 흘려보내는데, .NET Framework 에는
    StandardInputEncoding 이 없어 호출부에서 끌 수 없다). RFC 8259 도 파서가 BOM 을 무시하는
    것을 허용하므로, 받는 쪽에서 벗기는 것이 옳다."""
    return sys.stdin.read().lstrip("\ufeff")


def main():
    try:
        raw = read_event_json()
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if data.get("hook_event_name") == "SessionStart":
        write_shim_path(os.environ.get("CLAUDE_ENV_FILE"))
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Bash", "PowerShell"):
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command.strip():
        sys.exit(0)

    cwd = data.get("cwd") or os.getcwd()

    notes = git_snapshot(command, cwd)
    context = "\n".join(notes) if notes else None

    prefix = shim_prefix(tool_name, command)
    updated = dict(data.get("tool_input", {}), command=prefix + command) if prefix else None

    asked = ask_reason(tool_name, command, cwd)
    if asked:
        # 승인되면 셰임 선행 줄이 붙은 명령이 돈다(PowerShell 삭제도 휴지통·루트 거부를 탄다)
        log({"tool": tool_name, "command": command, "decision": asked[0]})
        emit("ask", asked[1], updated_input=updated, context=context)

    if prefix:
        log({"tool": tool_name, "command": command, "decision": "prepend-shim"})
        emit(None, "", updated_input=updated, context=context)

    if notes:
        log({"tool": tool_name, "command": command, "decision": "git-snapshot", "notes": notes})
        emit(None, "", context=context)

    sys.exit(0)


if __name__ == "__main__":
    # 훅이 예외로 죽으면 Claude Code 는 판정 없이 명령을 그대로 실행한다(fail-open).
    # 종전에는 이때 ask 로 승격했지만, 실측상 훅 내부 오류로 인한 ask 는 전부 마찰이기만
    # 했다. 이제 남은 판정은 위험이 자명한 ask 뿐이고 나머지는 복구 보조라, 오류는
    # 로그에 남기고 조용히 통과시킨다.
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        log({"decision": "guard-error", "error": repr(exc)})
        sys.exit(0)
