"""danger_guard.py 의 git 스냅샷 백업 '실측' 테스트.

가짜 이벤트만 넣고 판정 문자열을 보는 것으로는 이 기능을 검증할 수 없다. 실제로 검증해야
하는 것은 "파괴적 git 명령을 실행한 뒤 잃어버린 내용을 정말 되찾을 수 있는가"다. 그래서
임시 저장소를 진짜로 만들고, 훅을 돌리고, 파괴 명령을 실제로 실행한 뒤, 백업 ref 에서
복구되는지를 본다.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "jarvis" / "modules" / "hooks" / "danger_guard.py"
BACKUP_PREFIX = "refs/claude-backups/"


def git(repo, *args):
    # 로캘 인코딩(Windows 한국어는 cp949)으로 읽으면 UTF-8 내용이 디코딩에 실패해 stdout 이 None 이 된다.
    return subprocess.run(["git"] + list(args), cwd=str(repo),
                          capture_output=True, text=True, encoding="utf-8")


def run_hook(command, cwd):
    event = json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                        "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, "-X", "utf8", str(HOOK)],
                          input=event, capture_output=True, text=True, encoding="utf-8")
    out = (proc.stdout or "").strip()
    return json.loads(out) if out else None


def latest_backup(repo):
    r = git(repo, "for-each-ref", "--sort=-refname", "--count=1",
            "--format=%(refname)", BACKUP_PREFIX)
    return r.stdout.strip()


def backup_count(repo):
    r = git(repo, "for-each-ref", "--format=%(refname)", BACKUP_PREFIX)
    return len(r.stdout.split())


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q", ".")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "tracked.txt").write_text("committed\n", encoding="utf-8")
    (d / ".gitignore").write_text("*.env\n", encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    return d


def restore_from_backup(repo, ref):
    """백업 ref 의 내용을 워킹트리로 되돌린다 — 사용자가 안내받는 복구 절차 그대로."""
    return git(repo, "checkout", ref, "--", ".")


# --- 파괴 → 복구 ------------------------------------------------------------------


def test_reset_hard_backs_up_tracked_modification(repo):
    (repo / "tracked.txt").write_text("WIP 편집\n", encoding="utf-8")
    run_hook("git reset --hard HEAD", repo)
    ref = latest_backup(repo)
    assert ref, "a backup ref must be created"

    git(repo, "reset", "--hard", "HEAD")
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"

    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "WIP 편집\n"


def test_clean_backs_up_untracked_files(repo):
    (repo / "untracked.txt").write_text("새 파일\n", encoding="utf-8")
    run_hook("git clean -fd", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "clean", "-fdq")
    assert not (repo / "untracked.txt").exists()

    restore_from_backup(repo, ref)
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "새 파일\n"


def test_clean_x_backs_up_gitignored_files(repo):
    """`git clean -fdx` 는 .gitignore 대상(.env, 로컬 설정)까지 지운다.

    git 어디에도 남지 않는 파일이라 -x 일 때는 스냅샷에 무시된 파일까지 담아야 한다.
    """
    (repo / "local.env").write_text("SECRET=1\n", encoding="utf-8")
    run_hook("git clean -fdx", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "clean", "-fdxq")
    assert not (repo / "local.env").exists()

    restore_from_backup(repo, ref)
    assert (repo / "local.env").read_text(encoding="utf-8") == "SECRET=1\n"


def test_clean_without_x_does_not_capture_ignored(repo):
    """-x 가 없으면 무시된 파일은 지워지지도, 담기지도 않는다 (불필요한 시크릿 사본 방지)."""
    (repo / "local.env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    run_hook("git clean -fd", repo)
    ref = latest_backup(repo)
    listing = git(repo, "ls-tree", "-r", "--name-only", ref).stdout
    assert "untracked.txt" in listing
    assert "local.env" not in listing


def test_checkout_path_backs_up_worktree(repo):
    (repo / "tracked.txt").write_text("되돌려질 편집\n", encoding="utf-8")
    run_hook("git checkout -- tracked.txt", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "checkout", "--", "tracked.txt")
    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "되돌려질 편집\n"


def test_checkout_pathspec_without_dashdash_backs_up(repo):
    """`git checkout <path>` 는 `--` 없이도 미커밋 편집을 덮어쓴다."""
    (repo / "tracked.txt").write_text("되돌려질 편집\n", encoding="utf-8")
    run_hook("git checkout tracked.txt", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "checkout", "tracked.txt")
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "되돌려질 편집\n"


@pytest.mark.parametrize("cmd", ["git rm -f tracked.txt", "git rm --force tracked.txt",
                                 "git rm -fr tracked.txt"])
def test_rm_force_backs_up_tracked_modification(repo, cmd):
    (repo / "tracked.txt").write_text("지워질 편집\n", encoding="utf-8")
    run_hook(cmd, repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, *cmd.split()[1:])
    assert not (repo / "tracked.txt").exists()
    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "지워질 편집\n"


def test_checkout_combined_force_flag_backs_up(repo):
    """`-fq` 처럼 f 뒤에 다른 짧은 옵션이 붙어도 강제 checkout 이다."""
    (repo / "tracked.txt").write_text("버려질 편집\n", encoding="utf-8")
    run_hook("git checkout -fq HEAD", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "checkout", "-fq", "HEAD")
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "버려질 편집\n"


def test_switch_discard_changes_backs_up(repo):
    git(repo, "branch", "side")
    (repo / "tracked.txt").write_text("버려질 편집\n", encoding="utf-8")
    run_hook("git switch --discard-changes side", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "switch", "--discard-changes", "side")
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    restore_from_backup(repo, ref)
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "버려질 편집\n"


def test_clean_long_force_backs_up_untracked_files(repo):
    (repo / "untracked.txt").write_text("새 파일\n", encoding="utf-8")
    run_hook("git clean --force -d", repo)
    ref = latest_backup(repo)
    assert ref

    git(repo, "clean", "--force", "-dq")
    assert not (repo / "untracked.txt").exists()
    restore_from_backup(repo, ref)
    assert (repo / "untracked.txt").read_text(encoding="utf-8") == "새 파일\n"


@pytest.mark.parametrize("cmd", ["git checkout side", "git checkout -b fresh", "git switch side"])
def test_branch_checkout_with_dirty_tree_makes_no_backup(repo, cmd):
    """브랜치 전환은 미커밋 변경을 옮기거나 거부할 뿐 버리지 않는다 — 스냅샷할 이유가 없다."""
    git(repo, "branch", "side")
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    run_hook(cmd, repo)
    assert backup_count(repo) == 0


@pytest.mark.parametrize("cmd", ["git switch side && ls -lf", "git checkout side; ls -lf",
                                 "git clean -n && ls -lf", "git rm --cached x || ls -lf",
                                 "git reset HEAD x && echo --hard"])
def test_wipe_flag_of_a_later_command_makes_no_backup(repo, cmd):
    """`-f`·`--hard` 가 뒤따르는 다른 명령의 것이면 git 명령은 손실이 없다."""
    git(repo, "branch", "side")
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    run_hook(cmd, repo)
    assert backup_count(repo) == 0


@pytest.mark.parametrize("form", ["absolute", "relative"])
def test_git_dash_c_targets_that_repo(repo, tmp_path, form):
    """`git -C <dir>` 는 cwd 가 아니라 <dir> 에서 실행된다 — 백업도 거기서 해야 한다."""
    (repo / "tracked.txt").write_text("다른 디렉터리 편집\n", encoding="utf-8")
    session = _other_repo_with_changes(tmp_path)
    target = str(repo) if form == "absolute" else os.path.join("..", repo.name)
    run_hook("git -C %s reset --hard" % target, session)
    assert backup_count(repo) == 1
    assert backup_count(session) == 0


def test_git_dash_c_quoted_path_with_space(tmp_path):
    (tmp_path / "my repo").mkdir()
    spaced = _other_repo_with_changes(tmp_path / "my repo")
    session = tmp_path / "session"
    session.mkdir()
    run_hook('git -C "%s" reset --hard' % spaced, session)
    assert backup_count(spaced) == 1


def test_git_dash_c_is_cumulative(repo, tmp_path):
    """git 처럼 `-C a -C b` 는 a 기준의 b 다."""
    (repo / "tracked.txt").write_text("누적 -C\n", encoding="utf-8")
    session = _other_repo_with_changes(tmp_path)
    run_hook("git -C %s -C %s reset --hard" % (tmp_path, repo.name), session)
    assert backup_count(repo) == 1
    assert backup_count(session) == 0


def test_unresolvable_dash_c_falls_back_to_cwd(repo):
    """-C 대상을 풀 수 없으면 아무것도 안 하느니 cwd 를 백업한다(오탐 비용은 ref 하나)."""
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    run_hook("git -C missing-dir reset --hard", repo)
    assert backup_count(repo) == 1


def test_worktree_remove_backs_up_the_target_worktree(repo, tmp_path):
    """`git worktree remove` 의 대상은 cwd 가 아니라 인자다 — 거기서 백업해야 한다."""
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    (wt / "tracked.txt").write_text("워크트리 미커밋\n", encoding="utf-8")

    run_hook("git worktree remove --force %s" % wt, repo)
    ref = latest_backup(repo)
    assert ref, "a worktree snapshot must remain in the main repository"
    blob = git(repo, "show", "%s:tracked.txt" % ref).stdout
    assert blob == "워크트리 미커밋\n"


def test_leading_cd_targets_that_repo(repo, tmp_path):
    (repo / "tracked.txt").write_text("다른 디렉터리 편집\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run_hook("cd %s && git reset --hard" % repo, elsewhere)
    assert backup_count(repo) == 1


def test_leading_cd_with_variable_is_skipped(repo, tmp_path):
    """대상 저장소를 특정할 수 없으면 엉뚱한 곳을 백업하느니 아무것도 하지 않는다."""
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    run_hook('cd "$TARGET" && git reset --hard', elsewhere)
    assert backup_count(repo) == 0


def _other_repo_with_changes(tmp_path):
    d = tmp_path / "session-cwd"
    d.mkdir()
    git(d, "init", "-q", ".")
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")
    (d / "f.txt").write_text("committed\n", encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    (d / "f.txt").write_text("세션 cwd 미커밋\n", encoding="utf-8")
    return d


def test_leading_cd_wins_even_if_line_has_variables_elsewhere(repo, tmp_path):
    """회귀: 경로 뒤 어딘가에 `$` 가 있으면 cd 를 무시하고 세션 cwd 를 백업했다."""
    (repo / "tracked.txt").write_text("다른 디렉터리 편집\n", encoding="utf-8")
    session = _other_repo_with_changes(tmp_path)
    run_hook('cd %s && git checkout -- . && echo "$HOME"' % repo, session)
    assert backup_count(repo) == 1
    assert backup_count(session) == 0


def test_leading_cd_with_variable_does_not_back_up_session_cwd(tmp_path):
    """cd 대상을 특정할 수 없을 때 세션 cwd 를 대신 백업하면 엉뚱한 저장소를 백업한다."""
    session = _other_repo_with_changes(tmp_path)
    run_hook('cd "$TARGET" && git reset --hard', session)
    assert backup_count(session) == 0


# --- 노이즈 비용 ------------------------------------------------------------------


def test_clean_worktree_makes_no_backup(repo):
    run_hook("git reset --hard HEAD", repo)
    assert backup_count(repo) == 0


def test_identical_tree_is_not_backed_up_twice(repo):
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    run_hook("git reset --hard", repo)
    run_hook("git reset --hard", repo)
    assert backup_count(repo) == 1


def test_safe_git_commands_make_no_backup(repo):
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    for cmd in ["git status --short", "git log --oneline -3", "git stash push -m x",
                "git rebase --continue", "git branch -D old", "git commit --amend"]:
        run_hook(cmd, repo)
    assert backup_count(repo) == 0


def test_restore_staged_still_backs_up(repo):
    """`git restore --staged` 자체는 손실이 없지만, 오탐 비용이 ref 하나뿐이라 굳이 가르지 않는다."""
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    run_hook("git restore --staged tracked.txt", repo)
    assert backup_count(repo) == 1


def test_non_git_directory_is_silent(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert run_hook("git reset --hard", d) is None


def test_command_is_never_rewritten(repo):
    """백업은 훅 프로세스가 직접 한다 — 명령 문자열에는 손대지 않는다."""
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    payload = run_hook("git reset --hard HEAD", repo)
    assert payload is not None
    out = payload["hookSpecificOutput"]
    assert "updatedInput" not in out
    assert "permissionDecision" not in out
    assert BACKUP_PREFIX in out["additionalContext"]


def test_old_backups_are_pruned(repo):
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    stale = BACKUP_PREFIX + "20200101T000000"
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "update-ref", stale, head)
    run_hook("git reset --hard", repo)
    refs = git(repo, "for-each-ref", "--format=%(refname)", BACKUP_PREFIX).stdout
    assert stale not in refs


# --- push 는 판정하지 않는다 -----------------------------------------------------
# 명령 문자열만 보고 push 가 원격 커밋을 되돌릴 수 없게 지우는지 가리는 판정은 두 차례 적대적 리뷰에서
# 무너졌다(2026-10-11). push 는 auto mode 의 Git Destructive 규칙이 맡는다. 훅은 push 에 대해 아무것도
# 하지 않는다 — 묻지 않고, 원격을 조회하지 않고, ref 를 만들지 않는다.


@pytest.mark.parametrize("cmd", [
    "git push -f origin main",
    "git push --mirror origin",
    "git push origin :main",
    "git push origin main",
])
def test_hook_does_not_judge_pushes(repo, tmp_path, cmd):
    # 원격이 없는 경로를 가리키게 둔다. 훅이 원격을 조회하거나 판정하면 실패가 ask 로 드러난다.
    git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))
    p = run_hook(cmd, repo)
    assert p is None, p
    assert backup_count(repo) == 0


def test_clean_x_skips_ignored_when_too_large(repo, monkeypatch):
    """.gitignore 대상이 상한을 넘으면 담지 않는다 — 담으면 훅이 수십 초를 잡아먹는다.

    상한을 넘겨도 추적/미추적 스냅샷은 그대로 남아야 한다.
    """
    import sys
    sys.path.insert(0, str(HOOK.parent))
    import danger_guard

    (repo / ".gitignore").write_text("junk/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("WIP\n", encoding="utf-8")
    junk = repo / "junk"
    junk.mkdir()
    for i in range(5):
        (junk / ("f%d.bin" % i)).write_text("x" * 100, encoding="utf-8")

    monkeypatch.setattr(danger_guard, "MAX_IGNORED_FILES", 1)
    notes = danger_guard.git_snapshot("git clean -fdx", str(repo))

    assert any("left out of the snapshot" in n for n in notes), notes
    ref = latest_backup(repo)
    assert ref, "the snapshot itself must remain even when ignored files are given up"
    listing = git(repo, "ls-tree", "-r", "--name-only", ref).stdout
    assert "tracked.txt" in listing
    assert "junk/" not in listing


def test_clean_x_includes_ignored_when_small(repo):
    """상한 안이면 종전대로 .gitignore 대상까지 담는다."""
    (repo / "local.env").write_text("SECRET=1\n", encoding="utf-8")
    run_hook("git clean -fdx", repo)
    ref = latest_backup(repo)
    assert "local.env" in git(repo, "ls-tree", "-r", "--name-only", ref).stdout


load_tests = collect(globals())
