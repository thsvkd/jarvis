"""merge_and_diff.py 병합 엔진 테스트 (자리표시자 치환 + 외과적 병합 + 멱등성)."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MERGE = ROOT / "jarvis" / "modules" / "hooks" / "merge_and_diff.py"
FRAGMENT = ROOT / "jarvis" / "modules" / "hooks" / "settings.json"


def run_merge(target: Path, dry="0", py="python3", hooks_dir="/home/u/.claude/hooks",
              manifest_path=None, hook_files="danger_guard.py,shims/rm", windows=False):
    args = [sys.executable, "-X", "utf8", str(MERGE)] + (["--windows"] if windows else [])
    args += [str(FRAGMENT), str(target), dry, py, hooks_dir]
    if manifest_path is not None:
        args += [str(manifest_path), hook_files]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    result = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    return proc, result


def run_uninstall(target: Path, manifest: Path, dry="0"):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(MERGE), "--uninstall", str(target), dry, str(manifest)],
        capture_output=True, text=True, encoding="utf-8",
    )
    result = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    return proc, result


def hook_commands(result):
    return [h["hooks"][0]["command"] for h in result["hooks"]["PreToolUse"]]


def test_merge_into_empty(tmp_path):
    proc, result = run_merge(tmp_path / "settings.json")
    assert proc.returncode == 0
    assert result["permissions"]["defaultMode"] == "auto"
    assert "Bash" in result["permissions"]["allow"]
    # reset --hard 를 permissions.deny 로 막지 않는다 — 훅(danger_guard.py)이 스냅샷을 남긴 뒤
    # 통과시키는데, deny 는 훅보다 우선한다. push 는 auto mode 분류기가 맡는다. 그래서
    # settings.json 자체에는 git 관련 deny/ask 항목이 없어야 한다.
    assert "deny" not in result["permissions"] or not any(
        "git" in rule for rule in result["permissions"].get("deny", [])
    )
    assert not any("git" in rule for rule in result["permissions"].get("ask", []))


def test_placeholders_substituted(tmp_path):
    _, result = run_merge(tmp_path / "settings.json")
    cmds = hook_commands(result)
    assert any("danger_guard.py" in c for c in cmds)
    for c in cmds:
        assert "{{PY}}" not in c and "{{HOOKS_DIR}}" not in c
        assert "/home/u/.claude/hooks/" in c


def test_preserves_other_keys(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "model": "sonnet",
        "env": {"X": "1"},
        "statusLine": {"type": "command", "command": "foo"},
        "permissions": {"allow": ["CustomTool"]},
    }), encoding="utf-8")
    proc, result = run_merge(target)
    assert proc.returncode == 0
    assert result["model"] == "sonnet"
    assert result["env"]["X"] == "1"                            # 사용자 env 키는 보존
    assert result["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "3"     # 우리가 관리하는 env 키는 추가
    assert result["statusLine"]["command"] == "foo"
    assert "CustomTool" in result["permissions"]["allow"]   # 사용자 항목 보존
    assert "Bash" in result["permissions"]["allow"]         # 우리 항목 추가


def test_comment_not_propagated(tmp_path):
    _, result = run_merge(tmp_path / "settings.json")
    assert "_comment" not in result


def test_idempotent(tmp_path):
    target = tmp_path / "settings.json"
    run_merge(target)
    proc2, _ = run_merge(target)
    assert "no changes" in proc2.stdout


def test_dry_run_does_not_write(tmp_path):
    target = tmp_path / "settings.json"
    proc, result = run_merge(target, dry="1")
    assert proc.returncode == 0
    assert result is None   # 파일이 생성되지 않아야 함


def test_no_duplicate_hooks_on_rerun(tmp_path):
    target = tmp_path / "settings.json"
    run_merge(target)
    run_merge(target)
    _, result = run_merge(target)
    matchers = [h["matcher"] for h in result["hooks"]["PreToolUse"]]
    # 세 번 실행해도 훅은 한 번씩만 등록되어야 한다.
    assert matchers.count("Bash|PowerShell") == 1
    assert matchers == ["Bash|PowerShell"]


def test_backup_created_on_change(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    run_merge(target)  # 변경 발생 → 백업 생성
    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"model": "sonnet"}


def test_uninstall_deregisters_our_hooks(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"model": "x"}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    proc, result = run_uninstall(target, manifest)
    assert proc.returncode == 0
    assert not result.get("hooks", {}).get("PreToolUse")


def test_uninstall_deletes_settings_it_created(tmp_path):
    # 설치 전 settings.json 이 없었고 되돌린 뒤 빈 {} 만 남으면 파일 자체를 제거 → 설치 전 상태로 정확히 복원.
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    assert not target.exists()
    run_merge(target, manifest_path=manifest)
    assert target.exists()
    run_uninstall(target, manifest)
    assert not target.exists()


def test_uninstall_keeps_settings_it_did_not_create(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    run_uninstall(target, manifest)
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"model": "sonnet"}


def test_uninstall_precisely_restores_prior_state(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({
        "model": "sonnet",
        "permissions": {"allow": ["CustomTool"], "defaultMode": "default"},
    }), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    installed = json.loads(target.read_text(encoding="utf-8"))
    assert "Bash" in installed["permissions"]["allow"]
    assert installed["permissions"]["defaultMode"] == "auto"

    _, result = run_uninstall(target, manifest)
    assert result["model"] == "sonnet"                          # 무관 키 보존
    assert result["permissions"]["allow"] == ["CustomTool"]     # 정확히 원복 (우리 항목만 제거)
    assert result["permissions"]["defaultMode"] == "default"    # defaultMode 원복
    assert not result["permissions"].get("deny")               # 우리가 추가한 deny 제거
    assert not result.get("hooks", {}).get("PreToolUse")       # 훅 제거


def test_uninstall_removes_defaultmode_when_absent_before(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"allow": ["X"]}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert "defaultMode" not in result.get("permissions", {})   # 설치 전 없었으므로 제거
    assert result["permissions"]["allow"] == ["X"]


def test_uninstall_does_not_clobber_user_changed_defaultmode(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)                   # auto 로 설정, before=None
    s = json.loads(target.read_text(encoding="utf-8"))
    s["permissions"]["defaultMode"] = "acceptEdits"             # 설치 후 사용자가 바꿈
    target.write_text(json.dumps(s), encoding="utf-8")
    _, result = run_uninstall(target, manifest)
    assert result["permissions"]["defaultMode"] == "acceptEdits"  # 우리 값이 아니므로 안 건드림


def test_uninstall_keeps_preexisting_item_we_also_use(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)                   # Bash 는 이미 있어 추가 안 됨
    _, result = run_uninstall(target, manifest)
    assert "Bash" in result["permissions"]["allow"]            # 사용자가 원래 갖고 있던 Bash 보존


def test_uninstall_keeps_foreign_hooks(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python other_hook.py"}]}
        ]}
    }), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    cmds = [h["hooks"][0]["command"] for h in result["hooks"]["PreToolUse"]]
    assert any("other_hook.py" in c for c in cmds)             # 남의 훅 보존
    assert not any("danger_guard.py" in c for c in cmds)       # 우리 것만 제거


def test_uninstall_keeps_user_hook_sharing_our_matcher_entry(tmp_path):
    """사용자가 우리 훅과 같은 matcher 항목 안에 자기 훅을 넣었으면, 우리 훅만 빼고 그 항목은 남긴다."""
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    settings = json.loads(target.read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"][0]["hooks"].append({"type": "command", "command": "bash /h/my-audit.sh"})
    target.write_text(json.dumps(settings), encoding="utf-8")

    proc, result = run_uninstall(target, manifest)
    assert proc.returncode == 0
    entries = result["hooks"]["PreToolUse"]
    cmds = [h["command"] for e in entries for h in e["hooks"]]
    assert cmds == ["bash /h/my-audit.sh"]
    assert entries[0]["matcher"] == "Bash|PowerShell"


@pytest.mark.parametrize("receipt", ['{"harness_installs": {"codex": true}, broken', ''])
def test_install_stops_on_unparseable_receipt(tmp_path, receipt):
    """영수증은 하네스·도구 설치 기록도 담는다. 읽지 못한 영수증을 훅 영수증으로 덮어쓰면 그 기록이 사라진다."""
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text('{"model": "x"}', encoding="utf-8")
    manifest.write_text(receipt, encoding="utf-8")

    proc, _ = run_merge(target, manifest_path=manifest)
    assert proc.returncode != 0
    assert str(manifest) in proc.stderr
    assert target.read_text(encoding="utf-8") == '{"model": "x"}'
    assert manifest.read_text(encoding="utf-8") == receipt
    assert not list(tmp_path.glob("settings.json.bak.*"))


def test_uninstall_stops_on_unparseable_receipt(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    before = target.read_text(encoding="utf-8")
    manifest.write_text('{"version": 1, broken', encoding="utf-8")

    proc, _ = run_uninstall(target, manifest)
    assert proc.returncode != 0
    assert str(manifest) in proc.stderr
    assert target.read_text(encoding="utf-8") == before
    assert manifest.read_text(encoding="utf-8") == '{"version": 1, broken'


def test_install_reads_a_receipt_with_bom(tmp_path):
    """PowerShell 5.1 은 BOM 을 붙여 쓸 수 있다. BOM 하나로 다른 섹션을 잃으면 안 된다."""
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    manifest.write_text('\ufeff{"harness_installs": {"codex": true}}', encoding="utf-8")

    proc, _ = run_merge(target, manifest_path=manifest)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(manifest.read_text(encoding="utf-8"))["harness_installs"] == {"codex": True}


def test_uninstall_without_manifest_only_removes_hooks(tmp_path):
    # 매니페스트가 없으면(구버전 설치) 권한은 되돌리지 못하고 훅 등록만 해제한다.
    target, manifest = tmp_path / "settings.json", tmp_path / "missing.json"
    run_merge(target)   # 매니페스트 없이 설치
    _, result = run_uninstall(target, manifest)
    assert not result.get("hooks", {}).get("PreToolUse")       # 훅은 제거됨
    assert "Bash" in result["permissions"]["allow"]            # 권한은 그대로 (영수증 없음)


def test_uninstall_dry_run_does_not_write(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    before = target.read_text(encoding="utf-8")
    run_uninstall(target, manifest, dry="1")
    assert target.read_text(encoding="utf-8") == before


def test_install_writes_manifest_receipt(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"defaultMode": "default"}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest, hook_files="danger_guard.py,shims/rm")
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["hook_files"] == ["danger_guard.py", "shims/rm"]
    assert "Bash" in m["added_allow"]
    assert m["default_mode_before"] == "default"
    assert m["default_mode_after"] == "auto"
    assert m["default_mode_changed"] is True


# --- ask 규칙 병합 (스킬 게이팅) — allow/deny 와 동일한 union + 정밀 복원 ---

def test_ask_merged_from_fragment(tmp_path):
    # 조각(res/settings.json)의 ask 항목(스킬 게이팅)이 빈 settings 에 합쳐진다.
    _, result = run_merge(tmp_path / "settings.json")
    perms = result["permissions"]
    assert "Skill(loop)" in perms.get("ask", [])   # 위험 스킬은 ask 로 게이팅
    assert "Skill" in perms["allow"]               # 그 외 스킬은 전체 자동 허용
    assert "Artifact" in perms["allow"]


def test_ask_union_preserves_user_items(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "permissions": {"ask": ["Skill(my-own *)"]},
    }), encoding="utf-8")
    _, result = run_merge(target)
    ask = result["permissions"]["ask"]
    assert "Skill(my-own *)" in ask                # 사용자 항목 보존
    assert "Skill(loop)" in ask                    # 우리 항목 추가


def test_install_manifest_records_added_ask(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert "Skill(loop)" in m["added_ask"]


def test_uninstall_removes_only_added_ask(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({
        "permissions": {"ask": ["Skill(my-own *)"]},
    }), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert result["permissions"].get("ask", []) == ["Skill(my-own *)"]  # 우리 것만 제거


# --- 스칼라 최상위 설정(model/effortLevel/language) — '없을 때만 추가' ---

def test_scalars_set_when_absent(tmp_path):
    # 아무 값도 없던 settings.json 에는 우리의 오피니언 기본값이 설치된다.
    _, result = run_merge(tmp_path / "settings.json")
    assert result["model"] == "opus"
    assert result["effortLevel"] == "xhigh"
    assert result["language"] == "korean"


def test_scalars_preserve_existing(tmp_path):
    # 사용자가 이미 고른 값은 절대 덮어쓰지 않는다. 없는 것만 채운다.
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"model": "sonnet", "effortLevel": "high"}), encoding="utf-8")
    proc, result = run_merge(target)
    assert proc.returncode == 0
    assert result["model"] == "sonnet"        # 보존
    assert result["effortLevel"] == "high"    # 보존
    assert result["language"] == "korean"     # 없던 것만 추가


def test_scalars_idempotent(tmp_path):
    target = tmp_path / "settings.json"
    run_merge(target)
    proc2, _ = run_merge(target)
    assert "no changes" in proc2.stdout   # 두 번째 실행은 이미 다 있어 변경 없음


def test_install_manifest_records_added_scalars(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert "model" not in m["added_scalars"]              # 이미 있던 model 은 추가 목록에 없음
    assert m["added_scalars"]["effortLevel"] == "xhigh"
    assert m["added_scalars"]["language"] == "korean"


def test_uninstall_removes_only_added_scalars(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert result["model"] == "sonnet"                   # 사용자 것 보존
    assert "effortLevel" not in result                   # 우리가 추가한 것만 제거
    assert "language" not in result


def test_uninstall_does_not_clobber_user_changed_scalar(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)            # effortLevel=xhigh 설치 (없던 값)
    s = json.loads(target.read_text(encoding="utf-8"))
    s["effortLevel"] = "high"                            # 설치 후 사용자가 바꿈
    target.write_text(json.dumps(s), encoding="utf-8")
    _, result = run_uninstall(target, manifest)
    assert result["effortLevel"] == "high"              # 우리 값이 아니므로 안 건드림
    assert "language" not in result                      # 안 바꾼 것은 정상 제거


# --- env 하위 관리 키(CLAUDE_CODE_SCROLL_SPEED) — scalar 와 동일한 '없을 때만 추가' + 정밀 복원 ---

def test_env_scroll_speed_set_when_absent(tmp_path):
    # env 가 아예 없던 settings 에는 우리의 스크롤 속도 env 키가 설치된다.
    _, result = run_merge(tmp_path / "settings.json")
    assert result["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "3"


def test_env_preserves_user_value(tmp_path):
    # 사용자가 이미 값을 갖고 있으면 절대 덮어쓰지 않는다.
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"env": {"CLAUDE_CODE_SCROLL_SPEED": "10"}}), encoding="utf-8")
    proc, result = run_merge(target)
    assert proc.returncode == 0
    assert result["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "10"   # 보존


def test_env_preserves_other_env_keys(tmp_path):
    # 우리 키는 추가하되, 사용자의 다른 env 키는 그대로 둔다 (returncode 0 — env 위반 아님).
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"env": {"MY_VAR": "keep"}}), encoding="utf-8")
    proc, result = run_merge(target)
    assert proc.returncode == 0
    assert result["env"]["MY_VAR"] == "keep"
    assert result["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "3"


def test_install_manifest_records_added_env(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"env": {"CLAUDE_CODE_SCROLL_SPEED": "10"}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    # 이미 있던 값은 추가 목록에 없어야 한다 (없을 때만 추가하므로 이번엔 추가한 게 없다).
    assert "CLAUDE_CODE_SCROLL_SPEED" not in m["added_env"]


def test_uninstall_removes_only_added_env(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"env": {"MY_VAR": "keep"}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert result["env"] == {"MY_VAR": "keep"}                # 우리가 추가한 스크롤 키만 제거


def test_uninstall_deletes_env_object_when_it_only_held_our_key(tmp_path):
    # env 가 원래 없었고 우리 키만 넣었다면, 되돌린 뒤 env 객체 자체가 사라져야 한다.
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"model": "x"}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    assert json.loads(target.read_text(encoding="utf-8"))["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "3"
    _, result = run_uninstall(target, manifest)
    assert "env" not in result


def test_uninstall_does_not_clobber_user_changed_env(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)            # scroll=3 설치 (없던 값)
    s = json.loads(target.read_text(encoding="utf-8"))
    s["env"]["CLAUDE_CODE_SCROLL_SPEED"] = "7"            # 설치 후 사용자가 바꿈
    target.write_text(json.dumps(s), encoding="utf-8")
    _, result = run_uninstall(target, manifest)
    assert result["env"]["CLAUDE_CODE_SCROLL_SPEED"] == "7"   # 우리 값이 아니므로 안 건드림


# --- Windows 전용 env 키(GRAFT_TEST_SYNC_RUN) — Windows 설치기(--windows)에서만 병합 ---

def test_windows_sets_graft_sync_wrapper_env(tmp_path):
    # graft 의 Stop 훅이 sync-run.js 대신 숨김 콘솔 래퍼를 띄우도록 훅 디렉터리의 래퍼를 가리킨다.
    _, result = run_merge(tmp_path / "settings.json", windows=True)
    assert result["env"]["GRAFT_TEST_SYNC_RUN"] == "/home/u/.claude/hooks/graft_sync_hidden.cjs"


def test_posix_skips_graft_sync_wrapper_env(tmp_path):
    # POSIX 에는 래퍼가 설치되지 않으므로, 키를 넣으면 graft 동기화가 멈춘다 — 넣지 않는다.
    _, result = run_merge(tmp_path / "settings.json")
    assert "GRAFT_TEST_SYNC_RUN" not in result["env"]


# --- 재실행(멱등)이 설치 영수증을 파괴하지 않는다 ---
#
# 두 번째 실행의 merge() 는 "추가한 것이 없다"는 빈 영수증을 낸다. 그걸 매니페스트에 그대로
# 덮어쓰면 첫 설치가 무엇을 바꿨는지 기억이 사라져, --uninstall 이 아무것도 되돌리지 못한다.

def test_rerun_keeps_added_allow_in_manifest(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    run_merge(target, manifest_path=manifest)            # 멱등 재실행
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert "Bash" in m["added_allow"]
    assert not m["added_deny"]           # git 관련 deny 규칙은 더 이상 없다(훅이 직접 판단)
    assert "Skill(loop)" in m["added_ask"]


def test_rerun_keeps_default_mode_before(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"defaultMode": "acceptEdits"}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["default_mode_before"] == "acceptEdits"     # 두 번째 실행은 auto->auto 라 changed=False 였다
    assert m["default_mode_changed"] is True


def test_rerun_keeps_added_scalars(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    run_merge(target, manifest_path=manifest)
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["added_scalars"] == {"model": "opus", "effortLevel": "xhigh", "language": "korean"}
    # env 하위 관리 키도 재실행(멱등) 후에도 영수증에 보존돼야 uninstall 이 되돌릴 수 있다.
    assert m["added_env"] == {"CLAUDE_CODE_SCROLL_SPEED": "3"}


def test_rerun_keeps_settings_existed_before(tmp_path):
    # 첫 설치 시점에 settings.json 이 없었다는 사실이 유지되어야 uninstall 이 파일을 지운다.
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    assert not target.exists()
    run_merge(target, manifest_path=manifest)
    run_merge(target, manifest_path=manifest)            # 이제는 존재한다
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["settings_existed_before"] is False
    run_uninstall(target, manifest)
    assert not target.exists()


def test_uninstall_after_rerun_restores_prior_state(tmp_path):
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({
        "model": "sonnet",
        "permissions": {"allow": ["CustomTool"], "defaultMode": "acceptEdits"},
    }), encoding="utf-8")
    for _ in range(3):
        run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert result["model"] == "sonnet"
    assert result["permissions"]["allow"] == ["CustomTool"]
    assert result["permissions"]["defaultMode"] == "acceptEdits"
    assert "effortLevel" not in result
    assert not result.get("hooks", {}).get("PreToolUse")


def test_reinstall_after_uninstall_starts_fresh_receipt(tmp_path):
    # uninstall 이 매니페스트를 지우므로, 재설치는 그 시점의 상태를 새 기준선으로 삼는다.
    target, manifest = tmp_path / "settings.json", tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"defaultMode": "plan"}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    run_uninstall(target, manifest)
    manifest.unlink(missing_ok=True)                     # setup_hooks.sh 가 실제로 지운다
    run_merge(target, manifest_path=manifest)
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["default_mode_before"] == "plan"


# --- 구버전 git deny/ask 규칙 마이그레이션 ---
# 배경: 예전 res/settings.json 은 Bash(git push *) 등을 permissions.deny·ask 로 막았다. 지금은
# 훅이 브랜치 이름을 보고 판단하지만, Claude Code 는 deny/ask 를 훅 판정보다 우선 적용하므로
# 기존 설치본에 그 규칙이 남아 있으면 새 git 정책이 통째로 죽는다. merge 는 원래 '추가만'
# 하므로, 이 규칙만은 설치 중에 제거해야 업그레이드한 사용자에게도 기능이 살아난다.

LEGACY = {
    "deny": [
        "Bash(git push *)",
        "Bash(git push --force*)",
        "Bash(git push -f*)",
        "Bash(git reset --hard *)",
        "PowerShell(git push *)",
        "PowerShell(git push --force*)",
        "PowerShell(git push -f*)",
        "PowerShell(git reset --hard *)",
    ],
    "ask": [
        "Bash(git push *)",
        "PowerShell(git push *)",
    ],
}


def legacy_settings():
    return json.dumps({"permissions": {k: list(v) for k, v in LEGACY.items()}})


def _receipt_that_added(tmp_path, rules_by_key):
    """예전 설치가 이 규칙들을 넣었다고 적힌 영수증."""
    manifest = tmp_path / "manifest.json"
    receipt = {"version": 1}
    receipt.update({"added_" + k: list(v) for k, v in rules_by_key.items()})
    manifest.write_text(json.dumps(receipt), encoding="utf-8")
    return manifest


def test_legacy_git_rules_the_receipt_says_we_added_are_removed_on_install(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(legacy_settings(), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, LEGACY)
    proc, result = run_merge(target, manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout
    for key in LEGACY:
        assert not any("git" in rule for rule in result["permissions"].get(key, [])), \
            "a leftover legacy git %s rule would defeat the hook's git policy" % key
    # 걷어낸 규칙은 영수증의 '우리가 더한 것'에서도 빠진다. 남으면 사용자가 나중에 직접 넣은 같은
    # 문자열을 uninstall 이 지운다.
    recorded = json.loads(manifest.read_text(encoding="utf-8"))
    for key, rules in LEGACY.items():
        assert not set(rules) & set(recorded.get("added_" + key) or []), key


def test_legacy_rules_without_a_receipt_are_kept_and_reported(tmp_path):
    """영수증이 우리가 넣었다고 말하지 않으면 사용자가 직접 쓴 같은 문자열일 수 있다. 지우지 않고 알린다."""
    target = tmp_path / "settings.json"
    target.write_text(legacy_settings(), encoding="utf-8")
    proc, result = run_merge(target, manifest_path=tmp_path / "manifest.json")
    assert proc.returncode == 0, proc.stdout
    for key, rules in LEGACY.items():
        assert set(rules) <= set(result["permissions"][key]), key
    assert "overrides the guard hook's git policy" in proc.stdout, proc.stdout


def test_legacy_removal_leaves_user_own_rules(tmp_path):
    """영수증에 있는 것만 지운다. 우리가 배포했던 문자열과 같아도 영수증에 없으면 남는다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"permissions": {"deny": [
        "Bash(git push *)",            # 영수증에 있다 → 제거
        "Bash(git push -f*)",          # 배포했던 문자열이지만 영수증에 없다 → 보존
        "Bash(git push --force *)",    # 사용자가 직접 넣은 것 → 보존
        "Bash(curl *)",                # 무관 → 보존
    ]}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"deny": ["Bash(git push *)"]})
    run_merge(target, manifest_path=manifest)
    deny = json.loads(target.read_text(encoding="utf-8"))["permissions"]["deny"]
    assert "Bash(git push *)" not in deny
    assert {"Bash(git push -f*)", "Bash(git push --force *)", "Bash(curl *)"} <= set(deny)


def test_legacy_removal_when_no_such_rules_is_a_noop(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"permissions": {"deny": ["Bash(curl *)"]}}), encoding="utf-8")
    proc, result = run_merge(target)
    assert proc.returncode == 0
    assert result["permissions"]["deny"] == ["Bash(curl *)"]


def test_retired_rules_stay_removed_after_reinstall_and_uninstall(tmp_path):
    """우리가 넣었다가 거둔 규칙이다. uninstall 이 되살리면 설치 전보다 상태가 나빠진다."""
    target = tmp_path / "settings.json"
    target.write_text(legacy_settings(), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, LEGACY)
    run_merge(target, manifest_path=manifest)
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    for key in LEGACY:
        assert not any("git" in rule for rule in (result or {}).get("permissions", {}).get(key, [])), key


def test_uninstall_restores_legacy_rules_an_old_receipt_says_install_removed(tmp_path):
    """예전 설치기는 영수증 근거 없이 지우고 removed_legacy 에 남겼다. 그 영수증의 uninstall 은 계속 되살린다."""
    target = tmp_path / "settings.json"
    manifest = tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    manifest.write_text(json.dumps({"version": 1, "added_allow": ["Bash"], "removed_legacy": LEGACY}),
                        encoding="utf-8")
    _, result = run_uninstall(target, manifest)
    for key, rules in LEGACY.items():
        assert sorted(result["permissions"][key]) == sorted(rules)


def test_dry_run_reports_retirement_without_writing(tmp_path):
    target = tmp_path / "settings.json"
    original = legacy_settings()
    target.write_text(original, encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, LEGACY)
    receipt = manifest.read_text(encoding="utf-8")
    proc, _ = run_merge(target, dry="1", manifest_path=manifest)
    assert proc.returncode == 0
    assert "no longer shipped" in proc.stdout, proc.stdout
    assert target.read_text(encoding="utf-8") == original, "dry-run must not modify the file"
    assert manifest.read_text(encoding="utf-8") == receipt, "dry-run must not modify the receipt"


def test_retired_hook_registration_is_pruned(tmp_path):
    """우리가 배포했다가 그만둔 훅은 설치 중에 등록을 걷어낸다.

    그러지 않으면 새로 설치한 사람에게는 없고 업그레이드한 사람에게만 남는 유령 훅이 된다.
    """
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Read|Write|Edit|MultiEdit",
             "hooks": [{"type": "command", "command": "python -X utf8 \"/h/secrets_guard.py\""}]},
            {"matcher": "Notification",
             "hooks": [{"type": "command", "command": "python /h/mine.py"}]},
        ]}
    }), encoding="utf-8")

    _, result = run_merge(target)
    cmds = hook_commands(result)
    assert not any("secrets_guard.py" in c for c in cmds)
    # 사용자가 직접 넣은 훅은 그대로 둔다.
    assert any("mine.py" in c for c in cmds)
    assert any("danger_guard.py" in c for c in cmds)


def test_retired_hook_prune_keeps_user_hook_in_the_same_entry(tmp_path):
    """그만둔 훅을 걷어낼 때도 같은 항목 안의 사용자 훅은 남긴다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Read|Write|Edit|MultiEdit",
             "hooks": [{"type": "command", "command": "python -X utf8 \"/h/secrets_guard.py\""},
                       {"type": "command", "command": "bash /h/my-audit.sh"}]},
        ]}
    }), encoding="utf-8")

    _, result = run_merge(target)
    entries = [e for e in result["hooks"]["PreToolUse"] if e["matcher"] == "Read|Write|Edit|MultiEdit"]
    assert [h["command"] for e in entries for h in e["hooks"]] == ["bash /h/my-audit.sh"]


def test_receipt_forgets_retired_rules_already_gone_so_a_user_copy_survives(tmp_path):
    """예전 설치기가 지운 규칙이 영수증의 '우리가 더한 것'에 남아 있으면, 사용자가 나중에 같은 규칙을
    직접 넣었을 때 다음 설치가 그것을 지운다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"ask": ["Bash(git push *)"]})
    run_merge(target, manifest_path=manifest)
    s = json.loads(target.read_text(encoding="utf-8"))
    s["permissions"].setdefault("ask", []).append("Bash(git push *)")   # 사용자가 직접 넣는다
    target.write_text(json.dumps(s), encoding="utf-8")
    _, result = run_merge(target, manifest_path=manifest)
    assert "Bash(git push *)" in result["permissions"]["ask"]
    _, result = run_uninstall(target, manifest)
    assert "Bash(git push *)" in result["permissions"]["ask"]


def test_uninstall_does_not_restore_a_removed_legacy_rule_the_receipt_says_was_ours(tmp_path):
    target = tmp_path / "settings.json"
    manifest = tmp_path / "manifest.json"
    target.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
    manifest.write_text(json.dumps({"version": 1, "added_allow": ["Bash"], "added_deny": ["Bash(git push *)"],
                                    "removed_legacy": {"deny": ["Bash(git push *)"]}}), encoding="utf-8")
    run_merge(target, manifest_path=manifest)
    _, result = run_uninstall(target, manifest)
    assert "Bash(git push *)" not in (result or {}).get("permissions", {}).get("deny", [])


@pytest.mark.parametrize("receipt", ["[]", "null", '"text"'])
def test_a_receipt_that_is_not_an_object_is_treated_as_missing(tmp_path, receipt):
    target = tmp_path / "settings.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(receipt, encoding="utf-8")
    proc, result = run_merge(target, manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert isinstance(json.loads(manifest.read_text(encoding="utf-8")), dict)


# --- autoMode.allow ---
# 예전 버전은 '훅이 가역성을 확인한 push 는 허용'이라는 auto mode 예외를 넣었다. 훅이 push 를 판정하지
# 않게 된 지금 그 예외는 거짓 전제로 Git Destructive 규칙을 약하게 만든다. 설치는 autoMode 를 더하지
# 않고, 영수증이 우리가 넣었다고 말하는 항목만 걷어낸다.

OLD_PUSH_EXCEPTION = "Reversible git push (clears only Git Destructive): ..."


def test_install_adds_no_auto_mode(tmp_path):
    proc, result = run_merge(tmp_path / "settings.json")
    assert proc.returncode == 0, proc.stdout
    assert "autoMode" not in result


def test_old_push_exception_already_removed_by_hand_is_forgotten(tmp_path):
    """설정에서는 이미 손으로 지웠고 영수증에만 남은 경우. 남은 "$defaults" 를 걷고 영수증도 비운다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"autoMode": {"allow": ["$defaults"]}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    _, result = run_merge(target, manifest_path=manifest)
    assert "autoMode" not in result
    assert json.loads(manifest.read_text(encoding="utf-8"))["added_auto_allow"] == []
    _, again = run_merge(target, manifest_path=manifest)
    assert again == result


def test_old_push_exception_and_its_defaults_are_retired(tmp_path):
    """우리가 넣은 항목만 남았으면 "$defaults" 와 빈 autoMode 까지 걷어낸다. 다른 하위 키는 남긴다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"autoMode": {"allow": ["$defaults", OLD_PUSH_EXCEPTION],
                                               "environment": ["env"]}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    proc, result = run_merge(target, manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout     # autoMode 가 바뀌어도 '예상 밖 변경'으로 실패하면 안 된다
    assert result["autoMode"] == {"environment": ["env"]}
    assert json.loads(manifest.read_text(encoding="utf-8"))["added_auto_allow"] == []

    target.write_text(json.dumps({"autoMode": {"allow": ["$defaults", OLD_PUSH_EXCEPTION]}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    _, result = run_merge(target, manifest_path=manifest)
    assert "autoMode" not in result


def test_retiring_keeps_user_rules_and_the_defaults_they_rely_on(tmp_path):
    """"$defaults" 가 빠지면 남은 사용자 규칙이 기본 allow 목록을 통째로 대체한다."""
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"autoMode": {"allow": ["$defaults", OLD_PUSH_EXCEPTION, "mine"]}}),
                      encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    _, result = run_merge(target, manifest_path=manifest)
    assert result["autoMode"]["allow"] == ["$defaults", "mine"]


def test_auto_allow_without_a_receipt_is_left_alone(tmp_path):
    """영수증이 없으면 같은 문구라도 사용자가 직접 넣은 것일 수 있다."""
    target = tmp_path / "settings.json"
    original = {"autoMode": {"allow": ["$defaults", OLD_PUSH_EXCEPTION]}}
    target.write_text(json.dumps(original), encoding="utf-8")
    _, result = run_merge(target, manifest_path=tmp_path / "manifest.json")
    assert result["autoMode"] == original["autoMode"]


def test_uninstall_removes_only_auto_allow_an_old_install_added(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"permissions": {"allow": ["Bash"]},
                                  "autoMode": {"allow": ["$defaults", OLD_PUSH_EXCEPTION, "mine"]}}),
                      encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"allow": ["Bash"], "auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    _, result = run_uninstall(target, manifest)
    assert result["autoMode"]["allow"] == ["$defaults", "mine"]


def test_malformed_auto_mode_is_left_untouched(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"autoMode": {"allow": "not a list"}}), encoding="utf-8")
    manifest = _receipt_that_added(tmp_path, {"auto_allow": ["$defaults", OLD_PUSH_EXCEPTION]})
    proc, result = run_merge(target, manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout
    assert result["autoMode"] == {"allow": "not a list"}


load_tests = collect(globals())
