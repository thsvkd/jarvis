"""res/hooks/shims/rm 이 원본 rm 과 '관측 가능한 결과'가 같은지 대조한다.

이 셰임의 존재 이유는 훅이 명령 문자열을 재작성하지 않게 하는 것이다. 그래서 검증도
"치환된 문자열이 어떻게 생겼나"가 아니라 "같은 명령을 원본 rm 으로 돌렸을 때와 결과가
같은가"로 한다. 각 케이스를 동일한 픽스처 두 벌에서 실행하고 (a) 살아남은 파일 목록과
(b) 종료 코드를 비교한다.

픽스처는 /tmp 밖에 만든다. 셰임은 /tmp 아래를 의도적으로 '실제 삭제'로 넘기므로(tmpfs 는
휴지통도 같은 램이라 옮겨봐야 회수되지 않는다) 거기서 재면 셰임 경로를 전혀 검증하지 못한다.
"""
import os
from functools import lru_cache
import shutil
import sys
import shlex
import site
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SHIM_DIR = REPO / "jarvis" / "modules" / "hooks" / "shims"
BASE = REPO / ".tmp-rmshim"
BASH = shutil.which("bash") or "bash"


@lru_cache(maxsize=256)
def shell_path(path):
    if os.name == "nt":
        cygpath = Path(BASH).parents[1] / "usr/bin/cygpath.exe"
        return subprocess.run([str(cygpath), "-u", str(path)], check=True,
                              capture_output=True, text=True).stdout.strip()
    return str(path)


def bash_environment(env):
    env = dict(env)
    env["PATH"] = ":".join(shell_path(part) for part in env["PATH"].split(os.pathsep))
    for key in ("HOME", "D", "T", "LOG", "BIN_DIR"):
        if key in env:
            env[key] = shell_path(env[key])
    env["RMSHIM_TEST_PATH"] = env["PATH"]
    return env


def bash_receives_env(bash=BASH):
    """bash 가 넘겨준 환경을 받는가. Windows 의 WSL bash 는 받지 않는다."""
    probe = subprocess.run([bash, "-c", 'printf %s "$RMSHIM_PROBE"'], capture_output=True,
                           env=dict(os.environ, RMSHIM_PROBE="ok"))
    return probe.stdout == b"ok"


@pytest.fixture(scope="module", autouse=True)
def base_dir():
    # 이 모듈의 bash 는 넘겨준 환경(PATH·HOME·D)을 받는다고 가정하고 rm 을 돌린다. Windows 에서
    # `bash` 가 WSL 로 잡히면 환경이 넘어가지 않아 엉뚱한 곳을 지운다. 그러면 아무것도 돌리지 않는다.
    if not bash_receives_env():
        pytest.fail("bash does not receive the environment we pass (WSL bash?) - not running the rm tests: %r"
                    % shutil.which("bash"))
    shutil.rmtree(str(BASE), ignore_errors=True)
    BASE.mkdir(parents=True, exist_ok=True)
    yield
    shutil.rmtree(str(BASE), ignore_errors=True)


def make_fixture(d):
    shutil.rmtree(str(d), ignore_errors=True)
    (d / "sub" / "deep").mkdir(parents=True)
    for name in ("a.txt", "b.txt", "c.log", "name with spaces.txt", "-weird"):
        (d / name).write_text("x", encoding="utf-8")
    (d / "sub" / "deep" / "x.log").write_text("x", encoding="utf-8")
    (d / "emptydir").mkdir()
    os.symlink("a.txt", str(d / "link"))


def path_without_shims():
    """PATH 에서 셰임 디렉터리를 뺀다. 훅이 설치된 세션에서는 ~/.claude/hooks/shims 가 맨 앞에 있다."""
    return os.pathsep.join(x for x in os.environ["PATH"].split(os.pathsep)
                           if os.path.basename(x.rstrip("/\\")) != "shims")


def observe(script, use_shim):
    """스크립트를 돌리고 (종료 코드, 남은 파일 목록)을 돌려준다."""
    d = BASE / ("shim" if use_shim else "real")
    make_fixture(d)
    env = dict(os.environ)
    env.pop("CLAUDE_NO_TRASH", None)
    env["D"] = str(d)
    env["PATH"] = path_without_shims()
    if use_shim:
        # 실제 trash-put·HOME 을 쓰면 픽스처가 사용자 휴지통에 쌓인다. 대상을 지우기만 하는 가짜로
        # 바꾼다 — 비교하는 것은 남은 파일 목록뿐이다.
        fake = BASE / "fake-trash-bin"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "trash-put").write_text('#!/bin/sh\n[ "$1" = -- ] && shift\nexec /bin/rm -rf -- "$@"\n',
                                        encoding="utf-8")
        (fake / "trash-put").chmod(0o755)
        env["HOME"] = str(_guarded_home())
        env["PATH"] = os.pathsep.join([str(SHIM_DIR), str(fake), env["PATH"]])
    proc = subprocess.run([BASH, "-c", 'export PATH="$RMSHIM_TEST_PATH";\n' + (SHIM_GUARD if use_shim else "") + script], cwd=str(d), env=bash_environment(env),
                          capture_output=True, text=True, encoding="utf-8")
    survivors = sorted(
        str(p.relative_to(d)) for p in d.rglob("*")
    )
    return proc.returncode, survivors


CASES = [
    ("plain file", "rm a.txt"),
    ("directory -r", "rm -r sub"),
    ("directory -rf", "rm -rf sub"),
    ("empty directory -d", "rm -d emptydir"),
    ("nonexistent file with -f", "rm -f nope.txt"),
    ("nonexistent file (no flags)", "rm nope.txt"),
    ("variable expansion", 'V="$D/sub"; rm -rf "$V"'),
    ("variable + slash", 'V="$D"; rm -rf "$V/sub"'),
    ("variable + glob", 'V="$D"; rm -f "$V"/*.txt'),
    ("unmatched glob with -f", "rm -f ./*.nomatch"),
    ("unmatched glob (no flags)", "rm ./*.nomatch"),
    ("matched glob", "rm ./*.txt"),
    ("dash file after --", "rm -- -weird"),
    ("name with spaces", 'rm "name with spaces.txt"'),
    ("name with newline", 'touch "$D/nl\nname"; rm "$D/nl\nname"'),
    ("symbolic link", "rm link"),
    ("multiple targets", "rm a.txt b.txt c.log"),
    ("only some exist with -f", "rm -f a.txt nope.txt b.txt"),
    ("only some exist (no flags)", "rm a.txt nope.txt b.txt"),
    ("find -exec +", 'find . -name "*.log" -exec rm -f {} +'),
    ("find -exec ;", 'find . -name "*.log" -exec rm -f {} \\;'),
    ("xargs -0", 'find . -name "*.log" -print0 | xargs -0 rm -f'),
    ("chaining", "cd sub && rm -f deep/x.log; echo done"),
    ("multiline", "cd sub\nrm -rf deep\necho done"),
    ("rm inside a heredoc (data)", "cat > note.md <<'EOT'\nrm -rf /\nEOT\nwc -l < note.md"),
    ("rm inside a script", "printf '#!/bin/sh\\nrm -f \"$1\"\\n' > s.sh; chmod +x s.sh; ./s.sh a.txt"),
    ("inside sh -c", 'sh -c "rm -f a.txt"'),
    ("timeout wrapper", "timeout 5 rm -f a.txt"),
    ("no arguments", "rm"),
    ("missing parent with .. and -f", "rm -f nodir/../a.txt"),
    ("missing parent with .. (no flags)", "rm nodir/../a.txt"),
    ("dot target", "rm -rf ."),
    ("parent dot target", "rm -rf sub/.."),
    ("CLAUDE_NO_TRASH escape hatch", "CLAUDE_NO_TRASH=1 rm -rf sub"),
]


@pytest.mark.parametrize("name,script", CASES, ids=[c[0] for c in CASES])
def test_matches_real_rm(name, script):
    real = observe(script, use_shim=False)
    shim = observe(script, use_shim=True)
    assert real == shim, "result differs from the real rm: %s" % name


GNU_RM = subprocess.run(["rm", "--version"], capture_output=True).returncode == 0


def observe_with_logging_trash(script, tmp_path):
    """셰임을 기록하는 가짜 휴지통과 함께 돌린다. (종료 코드, 남은 파일 목록, 휴지통에 간 대상).
    가짜 휴지통은 대상을 옮기기만 하므로 남은 파일 목록을 원본 rm 결과와 그대로 비교할 수 있다.
    stdin 은 /dev/null 이다 — 원본 rm 이 묻는다면 '아니오'로 끝난다."""
    d = BASE / "logged"
    make_fixture(d)
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "trash-put").write_text(
        '#!/bin/sh\n[ "$1" = -- ] && shift\n'
        'for a; do echo "$a" >> "$LOG"; mv -- "$a" "$BIN_DIR/"; done\n', encoding="utf-8")
    (fake / "trash-put").chmod(0o755)
    bin_dir = tmp_path / "trashbin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "log"
    log.write_text("", encoding="utf-8")
    env = dict(os.environ, HOME=str(_guarded_home()), D=str(d), LOG=str(log), BIN_DIR=str(bin_dir))
    env.pop("CLAUDE_NO_TRASH", None)
    env["PATH"] = os.pathsep.join([str(SHIM_DIR), str(fake), "/usr/bin", "/bin"])
    proc = run_via_shim(script, cwd=str(d), env=env, stdin=subprocess.DEVNULL)
    survivors = sorted(str(p.relative_to(d)) for p in d.rglob("*"))
    return proc.returncode, survivors, log.read_text(encoding="utf-8").split()


# 원본 rm 이 실제로는 묻지 않는 조합이다. 그대로 넘기면 휴지통 없이 영구 삭제된다.
# 뒤 넷은 rm 이 거부하는 디렉터리 삭제다 — 셰임이 rm 보다 더 지우면 안 된다.
TRASHED = [
    ("-I with one operand", "rm -I a.txt", ["a.txt"], False),
    ("-I with three operands", "rm -I a.txt b.txt c.log", ["a.txt", "b.txt", "c.log"], False),
    ("-if (f last)", "rm -if a.txt", ["a.txt"], False),
    ("-i then -f", "rm -i -f a.txt", ["a.txt"], False),
    ("-I then --force", "rm -I --force a.txt", ["a.txt"], True),
    ("--interactive=never", "rm --interactive=never a.txt", ["a.txt"], True),
    ("--interactive=no", "rm --interactive=no a.txt", ["a.txt"], True),
    ("abbreviated --inter=ne", "rm --inter=ne a.txt", ["a.txt"], True),
    ("--interactive=once with one operand", "rm --interactive=once a.txt", ["a.txt"], True),
    # BSD/macOS rm 과 POSIXLY_CORRECT 의 GNU rm 은 첫 대상에서 옵션 읽기를 멈춘다. 뒤의 -i 는 파일 이름이다.
    ("prompt flag after an operand under POSIXLY_CORRECT", "POSIXLY_CORRECT=1 rm a.txt -i", ["a.txt"], True),
    ("directory without -r", "rm sub a.txt", ["a.txt"], False),
    ("directory without -r, with -f", "rm -f sub a.txt", ["a.txt"], False),
    ("non-empty directory with -d", "rm -d sub a.txt", ["a.txt"], False),
    ("empty directory with -d", "rm -d emptydir", ["emptydir"], False),
]

# 원본 rm 이 실제로 묻는 조합이다. 묻는 동작을 지키려면 그대로 넘겨야 한다.
PROMPTED = [
    ("-i", "rm -i a.txt", False),
    ("-fi (i last)", "rm -fi a.txt", False),
    ("-I with four operands", "rm -I a.txt b.txt c.log -- -weird", False),
    ("-rI", "rm -rI sub", False),
    ("-I -R", "rm -I -R sub", False),
    ("--interactive", "rm --interactive a.txt", True),
    ("--interactive=always", "rm --interactive=always a.txt", True),
    ("--interactive=once recursive", "rm --interactive=once --recursive sub", True),
]


@pytest.mark.parametrize("name,script,trashed,gnu_only", TRASHED, ids=[c[0] for c in TRASHED])
def test_non_prompting_calls_go_to_the_trash(name, script, trashed, gnu_only, tmp_path):
    if gnu_only and not GNU_RM:
        pytest.skip("long options need GNU rm")
    rc, survivors, log = observe_with_logging_trash(script, tmp_path)
    assert sorted(log) == trashed, "trash calls differ: %s" % name
    assert (rc, survivors) == observe(script, use_shim=False), "result differs from the real rm: %s" % name


def test_bash_as_sh_keeps_gnu_option_order(tmp_path):
    """/bin/sh 가 bash 인 배포판(Fedora·Arch)에서 bash 는 POSIXLY_CORRECT 를 셸 안에만 정의하고 내보내지
    않는다. 그 값으로 BSD 순서를 고르면 원본 GNU rm 과 달리 `rm a.txt -f` 의 -f 를 파일로 본다."""
    if os.name == "nt" or not GNU_RM:
        pytest.skip("needs bash and GNU rm")
    sh = tmp_path / "sh"
    sh.symlink_to(BASH)
    d = BASE / "bash-as-sh"
    make_fixture(d)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "trash-put").write_text('#!/bin/sh\n[ "$1" = -- ] && shift\nfor a; do echo "$a" >> "$LOG"; done\n',
                                    encoding="utf-8")
    (fake / "trash-put").chmod(0o755)
    log = tmp_path / "log"
    env = dict(os.environ, HOME=str(_guarded_home()), LOG=str(log),
               PATH=os.pathsep.join([str(fake), "/usr/bin", "/bin"]))
    env.pop("POSIXLY_CORRECT", None)
    env.pop("CLAUDE_NO_TRASH", None)
    proc = subprocess.run([str(sh), str(SHIM_DIR / "rm"), "a.txt", "-f", "missing"], cwd=str(d), env=env,
                          capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert log.read_text(encoding="utf-8").split() == ["a.txt"]


@pytest.mark.parametrize("name,script,gnu_only", PROMPTED, ids=[c[0] for c in PROMPTED])
def test_prompting_calls_reach_the_real_rm(name, script, gnu_only, tmp_path):
    if gnu_only and not GNU_RM:
        pytest.skip("long options need GNU rm")
    rc, survivors, log = observe_with_logging_trash(script + " < /dev/null", tmp_path)
    assert log == [], "a prompting call went to the trash: %s" % name
    assert (rc, survivors) == observe(script + " < /dev/null", use_shim=False)


# 가운데 세 개는 rm 에게 그대로 넘기는 형태(`.` 대상, -i)가 앞에 와도 홈 검사가 먼저임을 본다.
# 마지막 둘은 `..` 로 /tmp 를 거쳐 홈에 닿는 경로다. 텍스트만 보면 /tmp 대상으로 분류돼 원본 rm 으로 갔다.
REFUSED = ["rm -rf ~", 'rm -rf "$HOME"', "rm -rf /", "rm -rf x/. ~", "rm -rf ./ ~/", "rm -i -rf x ~",
           'rm -rf "/tmp/..$HOME"', 'cd /tmp && rm -rf "..$HOME"']

# 셰임을 거쳐야 안전한 명령은 같은 셸 안에서 rm 이 셰임으로 잡히는지 먼저 확인한다. Windows 의
# PATH 에서 `bash` 가 WSL 로 잡히면 PATH·HOME 이 넘어가지 않아, 여기 있는 명령이 WSL 의 진짜
# rm 으로 실제 홈을 지웠다(2026-10-11). 확인에 실패하면 아무것도 지우지 않고 97 로 끝난다.
SHIM_GUARD = ('case "$(command -v rm)" in */shims/rm) ;; '
              '*) echo "rm is not the shim: $(command -v rm)" >&2; exit 97 ;; esac\n')


def run_via_shim(script, **kwargs):
    if "env" in kwargs:
        kwargs["env"] = bash_environment(kwargs["env"])
    proc = subprocess.run([BASH, "-c", 'export PATH="$RMSHIM_TEST_PATH";\n' + SHIM_GUARD + script],
                          capture_output=True, text=True, encoding="utf-8", **kwargs)
    assert proc.returncode != 97, "bash is not using the shim - %s" % proc.stderr
    return proc


def test_env_probe_rejects_a_bash_that_drops_the_environment(tmp_path):
    """가드 자체의 검증. WSL bash 처럼 환경을 버리는 bash 를 걸러내지 못하면 모듈이 실제 홈을 지운다."""
    if os.name == "nt":
        pytest.skip("a fake bash script needs a POSIX shell to run it")
    fake = tmp_path / "bash"
    fake.write_text('#!/bin/sh\nexec env -i /bin/sh "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    assert bash_receives_env()
    assert not bash_receives_env(str(fake))


def test_shim_guard_stops_before_rm_when_rm_is_not_the_shim():
    """가드 자체의 검증. rm 이 셰임이 아니면 아무것도 지우지 않고 97 로 끝나야 한다."""
    target = BASE / "guard-target"
    target.write_text("x", encoding="utf-8")
    env = dict(os.environ, T=str(target), PATH=path_without_shims())
    proc = subprocess.run([BASH, "-c", SHIM_GUARD + 'rm -f "$T"'], env=env, capture_output=True)
    assert proc.returncode == 97
    assert target.is_file()


def _guarded_home():
    """tmp 밖의 가짜 홈. 셰임은 tmp 아래의 HOME 은 보호하지 않으므로 BASE 아래에 둔다."""
    home = BASE / "guarded-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "keep").write_text("x", encoding="utf-8")
    return home


def _fake_trash_path(tmp_path):
    """아무것도 지우지 않는 trash-put 을 앞세운 PATH. 거부 테스트가 실제 홈을 대상으로 하므로,
    셰임이 퇴행해도 실제로는 아무것도 옮겨지지 않게 한다."""
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "trash-put").write_text('#!/bin/sh\necho "TRASHED $*"\n', encoding="utf-8")
    (fake / "trash-put").chmod(0o755)
    return os.pathsep.join([str(SHIM_DIR), str(fake), "/usr/bin", "/bin"])


@pytest.mark.parametrize("script", REFUSED)
def test_root_and_home_are_refused(script, tmp_path):
    # 실제 홈을 대상으로 삼지 않는다. 셰임이 퇴행하면 /bin/rm 이 그대로 지우기 때문이다.
    home = _guarded_home()
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_NO_TRASH", None)
    env["PATH"] = _fake_trash_path(tmp_path)
    proc = run_via_shim(script, cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert (home / "keep").is_file()


def _tmp_link_to_home(home):
    """/tmp 아래에 홈을 가리키는 링크를 만든다. /tmp 대상은 원본 rm 으로 가는 빠른 길을 탄다."""
    link_dir = Path(tempfile.mkdtemp(prefix="rmshim-link-", dir="/tmp"))
    os.symlink(str(home), str(link_dir / "h"))
    return link_dir


def _drop_tmp_link(link_dir):
    # 링크부터 떼어 낸다. 정리가 링크 너머의 홈을 건드리지 않게 한다.
    if os.path.islink(str(link_dir / "h")):
        os.unlink(str(link_dir / "h"))
    shutil.rmtree(str(link_dir), ignore_errors=True)


def test_trailing_slash_through_a_tmp_link_to_home_is_refused(tmp_path):
    """`/tmp/h -> ~` 에서 `rm -rf /tmp/h/` 는 GNU rm 이 링크 너머의 홈을 비운다."""
    if os.name == "nt":
        pytest.skip("creating symlinks on Windows needs extra privileges")
    home = _guarded_home()
    link_dir = _tmp_link_to_home(home)
    try:
        env = dict(os.environ, HOME=str(home), T=str(link_dir), PATH=_fake_trash_path(tmp_path))
        env.pop("CLAUDE_NO_TRASH", None)
        proc = run_via_shim('rm -rf "$T/h/"', cwd=str(BASE), env=env)
        assert proc.returncode != 0
        assert "refusing" in proc.stderr
        assert (home / "keep").is_file()
    finally:
        _drop_tmp_link(link_dir)


def test_tmp_link_to_home_without_trailing_slash_removes_only_the_link(tmp_path):
    """슬래시가 없으면 rm 은 링크만 지운다. 이것까지 거부하면 안 된다."""
    if os.name == "nt":
        pytest.skip("creating symlinks on Windows needs extra privileges")
    home = _guarded_home()
    link_dir = _tmp_link_to_home(home)
    try:
        env = dict(os.environ, HOME=str(home), T=str(link_dir), PATH=_fake_trash_path(tmp_path))
        env.pop("CLAUDE_NO_TRASH", None)
        proc = run_via_shim('rm -rf "$T/h"', cwd=str(BASE), env=env)
        assert proc.returncode == 0, proc.stderr
        assert not os.path.lexists(str(link_dir / "h"))
        assert (home / "keep").is_file()
    finally:
        _drop_tmp_link(link_dir)


@pytest.mark.parametrize("script", ['rm -rf "$REAL_HOME"', 'cd "$REAL_HOME" && rm -rf ../home/'])
def test_home_behind_a_linked_parent_is_refused(script, tmp_path):
    """HOME 이 링크를 거친 경로여도(HOME=link/home, link -> real) 실제 경로로 지우면 홈이다."""
    if os.name == "nt":
        pytest.skip("creating symlinks on Windows needs extra privileges")
    real = BASE / "linked-real"
    (real / "home").mkdir(parents=True, exist_ok=True)
    (real / "home" / "keep").write_text("x", encoding="utf-8")
    link = BASE / "linked"
    if not os.path.lexists(str(link)):
        os.symlink("linked-real", str(link))
    env = dict(os.environ, HOME=str(link / "home"), REAL_HOME=str(real / "home"),
               PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    proc = run_via_shim(script, cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert (real / "home" / "keep").is_file()


def _fake_cygpath(tmp_path):
    """cygpath -u 흉내: 역슬래시를 슬래시로, `C:` 를 $FAKE_C 로 바꾼다. fakebin 은 _fake_trash_path 가 만든다."""
    stub = tmp_path / "fakebin" / "cygpath"
    stub.write_text('#!/bin/sh\n[ "$1" = -u ] || exit 2\n'
                    'printf \'%s\\n\' "$2" | sed -e \'s#\\\\#/#g\' -e "s#^[A-Za-z]:#$FAKE_C#"\n',
                    encoding="utf-8")
    stub.chmod(0o755)


def _fake_uname(tmp_path, name):
    """uname -s 흉내. 대소문자 무시 비교와 드라이브 루트 보호는 OS 에 따라 갈린다."""
    stub = tmp_path / "fakebin" / "uname"
    stub.write_text("#!/bin/sh\necho %s\n" % name, encoding="utf-8")
    stub.chmod(0o755)


@pytest.mark.parametrize("sep", ["\\", "/"])
def test_windows_drive_operand_for_home_is_refused(sep, tmp_path):
    """Git Bash 에서 "$USERPROFILE"(C:\\Users\\me) 같은 드라이브 경로를 상대경로로 보면 홈 검사를
    빠져나가 프로필이 통째로 휴지통에 간다. POSIX 에서는 C: 를 BASE 로 옮기는 가짜 cygpath 로 흉내 낸다."""
    home = _guarded_home()
    env = dict(os.environ, HOME=str(home), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    if os.name == "nt":
        operand = str(home).replace("\\", sep)
    else:
        operand = "C:" + sep + home.name
        _fake_cygpath(tmp_path)
        env["FAKE_C"] = str(BASE)
    proc = run_via_shim("rm -rf %s" % shlex.quote(operand), cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert (home / "keep").is_file()


@pytest.mark.parametrize("os_name", ["Darwin", "MINGW64_NT-10.0-19045"])
def test_home_in_another_case_is_refused_on_case_insensitive_systems(os_name, tmp_path):
    """APFS·NTFS 는 대소문자를 구분하지 않는다. `rm -rf /Users/ME` 도 홈 `/Users/me` 다."""
    home = _guarded_home()
    env = dict(os.environ, HOME=str(home), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    _fake_uname(tmp_path, os_name)
    operand = shell_path(home)
    operand = operand[:-len(home.name)] + home.name.upper()
    proc = run_via_shim("rm -rf %s" % shlex.quote(operand), cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert (home / "keep").is_file()


def test_home_in_another_case_is_a_different_directory_on_linux(tmp_path):
    """대소문자를 구분하는 파일 시스템에서 GUARDED-HOME 은 홈이 아니다. 거부하면 안 된다."""
    if sys.platform in ("darwin", "win32"):
        pytest.skip("needs a case-sensitive file system")
    home = _guarded_home()
    env = dict(os.environ, HOME=str(home), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    _fake_uname(tmp_path, "Linux")
    proc = run_via_shim("rm -rf %s" % shlex.quote(str(BASE / home.name.upper())), cwd=str(BASE), env=env)
    assert proc.returncode == 0, proc.stderr
    assert "refusing" not in proc.stderr


@pytest.mark.parametrize("operand", ["/c", "/c/", "/C", "C:\\", "C:/"])
def test_drive_root_is_refused_on_git_bash(operand, tmp_path):
    """Git Bash 의 /c 는 C: 드라이브 전체다. `/` 만 막으면 `rm -rf /c` 가 통과한다."""
    env = dict(os.environ, HOME=str(_guarded_home()), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    _fake_uname(tmp_path, "MINGW64_NT-10.0-19045")
    if os.name != "nt":
        _fake_cygpath(tmp_path)
        env["FAKE_C"] = "/c"
    proc = run_via_shim("rm -rf %s" % shlex.quote(operand), cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr


def test_one_letter_top_level_dir_is_not_a_drive_root_on_linux(tmp_path):
    """Linux·macOS 의 /c 는 그냥 디렉터리다. 드라이브 루트로 보고 거부하면 안 된다."""
    if os.name == "nt":
        pytest.skip("on Git Bash /c is the drive root")
    env = dict(os.environ, HOME=str(_guarded_home()), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    _fake_uname(tmp_path, "Linux")
    proc = run_via_shim("rm -rf /c", cwd=str(BASE), env=env)
    assert "refusing" not in proc.stderr


def test_home_is_refused_even_without_a_trash_tool(tmp_path):
    """휴지통 도구가 없으면 실제 rm 으로 넘기지만, 홈 검사는 그보다 먼저다.
    셰임만 복사해 옆의 send_to_trash.py 를 떼어 낸다."""
    if os.name == "nt":
        pytest.skip("Git Bash needs its own /usr/bin on PATH, which may hold a trash tool")
    shims = tmp_path / "shims"
    shims.mkdir()
    shutil.copy(SHIM_DIR / "rm", shims / "rm")
    (shims / "rm").chmod(0o755)
    # 셰임이 쓰는 도구만 둔다. /usr/bin 을 통째로 넣으면 거기 있는 trash-put 이 이 분기를 가린다.
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("dirname", "id", "uname"):
        (tools / name).symlink_to(shutil.which(name))
    home = _guarded_home()
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_NO_TRASH", None)
    env["PATH"] = os.pathsep.join([str(shims), str(tools)])
    proc = run_via_shim("rm -rf ~", cwd=str(BASE), env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert (home / "keep").is_file()


def test_send_to_trash_succeeds_on_a_console_that_cannot_print_its_message(tmp_path):
    """한국어 Windows 콘솔(cp949)은 🗑 를 인코딩하지 못한다. 옮긴 뒤의 출력 오류를 실패로 세면
    rm 이 휴지통으로 옮겨 놓고 exit 1 을 낸다(Windows 실측)."""
    fake = tmp_path / "mods"
    fake.mkdir()
    (fake / "send2trash.py").write_text("import os\ndef send2trash(p):\n    os.remove(p)\n", encoding="utf-8")
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(fake), PYTHONIOENCODING="cp949")
    proc = subprocess.run([sys.executable, str(SHIM_DIR.parent / "send_to_trash.py"), "--", str(target)],
                          env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Win32 path normalization; POSIX keeps the path as given")
def test_send_to_trash_hands_send2trash_a_normalized_absolute_path(tmp_path):
    """Git Bash 는 /c/x 를 C:/x 로 넘긴다. Windows send2trash 는 슬래시 경로를 E_INVALIDARG 로 거부한다(실측)."""
    fake = tmp_path / "mods"
    fake.mkdir()
    seen = tmp_path / "seen"
    (fake / "send2trash.py").write_text(
        "import os\ndef send2trash(p):\n    open(%r, 'w', encoding='utf-8').write(p)\n    os.remove(p)\n" % str(seen),
        encoding="utf-8")
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    given = str(tmp_path) + "/./a.txt"
    env = dict(os.environ, PYTHONPATH=str(fake))
    proc = subprocess.run([sys.executable, str(SHIM_DIR.parent / "send_to_trash.py"), "--", given],
                          env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    passed = seen.read_text(encoding="utf-8")
    assert passed == str(target)
    assert "/" not in passed


def test_send_to_trash_succeeds_when_its_output_pipe_is_closed(tmp_path):
    """`rm x | head -0` 처럼 읽는 쪽이 먼저 닫혀도, 옮긴 결과는 성공이다."""
    fake = tmp_path / "mods"
    fake.mkdir()
    (fake / "send2trash.py").write_text("import os\ndef send2trash(p):\n    os.remove(p)\n", encoding="utf-8")
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for target in targets:
        target.write_text("x", encoding="utf-8")
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        proc = subprocess.run([sys.executable, str(SHIM_DIR.parent / "send_to_trash.py"), "--", *map(str, targets)],
                              env=dict(os.environ, PYTHONPATH=str(fake)), stdout=write_end, stderr=subprocess.PIPE)
    finally:
        os.close(write_end)
    assert proc.returncode == 0, proc.stderr
    assert not any(target.exists() for target in targets)


def test_tmp_targets_are_really_deleted(tmp_path):
    """/tmp 는 tmpfs 라 휴지통 자리도 같은 램이다 — 옮기면 램이 회수되지 않는다."""
    if os.name == "nt":
        tmp_path = Path(tempfile.mkdtemp(prefix="rmshim-target-"))
    target = tmp_path / "big"
    target.mkdir()
    (target / "f").write_text("x", encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = str(SHIM_DIR) + os.pathsep + env["PATH"]
    run_via_shim("rm -rf %s" % shlex.quote(shell_path(target)), env=env)
    assert not target.exists()


@pytest.mark.skipif(shutil.which("trash-put") is None, reason="trash-put not installed")
def test_deleted_content_is_recoverable():
    # 휴지통도 실제 홈의 것을 쓰지 않는다. 같은 파일 시스템의 가짜 홈을 주면 trash-cli 는 그 아래
    # Trash 를 쓴다.
    d = BASE / "rec"
    make_fixture(d)
    home = BASE / "trash-home"
    # `pip --user` 로 깔린 trash-cli 는 HOME 을 바꾸면 자기 모듈을 못 찾는다. 모듈 위치만 알려 준다.
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / ".local" / "share"),
               PYTHONUSERBASE=site.getuserbase())
    env.pop("CLAUDE_NO_TRASH", None)
    env["PATH"] = str(SHIM_DIR) + os.pathsep + env["PATH"]
    run_via_shim("rm -rf sub", cwd=str(d), env=env)
    assert not (d / "sub").exists()
    listing = subprocess.run(["trash-list"], capture_output=True, text=True, encoding="utf-8", env=env).stdout
    assert str(d / "sub") in listing
    assert (home / ".local" / "share" / "Trash" / "files" / "sub").is_dir()


def test_macos_builtin_trash_is_used_when_trash_put_is_absent(tmp_path):
    """macOS 14.4+ 는 /usr/bin/trash 를 기본 제공한다. 설치기(setup_hooks.sh)는 이것을
    휴지통 도구로 인정하므로, 셰임도 이것을 써야 한다 — 아니면 '휴지통 있음'으로 설치해 놓고
    실제로는 삭제한다. trash-put 이 없는 PATH 에 가짜 uname(Darwin)·trash 를 둬서 흉내 낸다.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "uname").write_text("#!/bin/sh\necho Darwin\n", encoding="utf-8")
    # 대시로 시작하는 인자를 받으면 옵션으로 오인했다는 뜻이다 — 실패로 기록한다.
    (fake / "trash").write_text(
        '#!/bin/sh\nfor a; do case "$a" in -*) echo "OPT:$a" >> "$LOG"; exit 2 ;; esac\n'
        'echo "$a" >> "$LOG"; mv -- "$a" "$BIN_DIR/"; done\n', encoding="utf-8")
    for f in fake.iterdir():
        f.chmod(0o755)
    d = BASE / "mac"
    make_fixture(d)
    bin_dir = tmp_path / "trashbin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    env = dict(os.environ, LOG=str(log), BIN_DIR=str(bin_dir))
    env.pop("CLAUDE_NO_TRASH", None)
    env["PATH"] = os.pathsep.join([str(SHIM_DIR), str(fake), "/usr/bin", "/bin"])
    proc = subprocess.run([BASH, "-c", 'export PATH="$RMSHIM_TEST_PATH";\n' + SHIM_GUARD + "rm -rf sub a.txt -- -weird"], cwd=str(d), env=bash_environment(env),
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert "without the trash" not in proc.stderr
    assert "OPT:" not in log.read_text(encoding="utf-8")
    assert sorted(p.name for p in bin_dir.iterdir()) == ["-weird", "a.txt", "sub"]


def test_sandboxed_home_under_tmp_can_be_cleaned_up(tmp_path):
    """테스트·스크립트가 HOME 을 임시 디렉터리로 바꿔 두고 `rm -rf "$HOME"` 으로 정리하는 것은
    홈 삭제가 아니다 — bats 의 샌드박스 teardown 이 이 오인으로 막혔다."""
    if os.name == "nt":
        tmp_path = Path(tempfile.mkdtemp(prefix="rmshim-home-"))
    sandbox = tmp_path / "sandbox-home"
    sandbox.mkdir()
    env = dict(os.environ, HOME=str(sandbox), PATH=_fake_trash_path(tmp_path))
    if os.name == "nt":
        # MSYS synthesizes the current account's passwd home from HOME. This case
        # exercises the temporary ENV_HOME exemption independently of that mapping.
        fake_id = tmp_path / "fakebin/id"
        fake_id.write_text("#!/bin/sh\necho rmshim-fixture-no-account\n", encoding="utf-8")
        fake_id.chmod(0o755)
    env.pop("CLAUDE_NO_TRASH", None)
    proc = run_via_shim('rm -rf "$HOME"', env=env)
    assert proc.returncode == 0, proc.stderr
    assert "refusing" not in proc.stderr


def test_overridden_home_outside_tmp_is_still_refused(tmp_path):
    """컨테이너(-e HOME=…)·`sudo -E` 처럼 passwd 홈과 $HOME 이 다르면 $HOME 도 진짜 홈이다."""
    app_home = BASE / "app-home"
    app_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=str(app_home), PATH=_fake_trash_path(tmp_path))
    env.pop("CLAUDE_NO_TRASH", None)
    proc = run_via_shim('rm -rf "$HOME"', env=env)
    assert proc.returncode != 0
    assert "refusing" in proc.stderr
    assert app_home.is_dir()


def test_username_is_not_evaluated_as_shell(tmp_path):
    """실제 홈 조회에 `~이름` 을 eval 한다. 이름에 셸 문법이 섞여도 실행되면 안 된다."""
    mark = tmp_path / "PWNED"
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "id").write_text('#!/bin/sh\necho "x;touch %s"\n' % mark, encoding="utf-8")
    (fake / "id").chmod(0o755)
    (fake / "trash-put").write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
    (fake / "trash-put").chmod(0o755)
    d = BASE / "evalcheck"
    d.mkdir(parents=True, exist_ok=True)
    (d / "f").write_text("x", encoding="utf-8")
    env = dict(os.environ, PATH=os.pathsep.join([str(SHIM_DIR), str(fake), "/usr/bin", "/bin"]))
    env.pop("CLAUDE_NO_TRASH", None)
    subprocess.run([BASH, "-c", 'export PATH="$RMSHIM_TEST_PATH";\n' + SHIM_GUARD + "rm f"], cwd=str(d), env=bash_environment(env), capture_output=True)
    assert not mark.exists()


load_tests = collect(globals())
