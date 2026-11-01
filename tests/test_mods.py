"""번들 mod 설치 (res/mods/apply.py).

실제 홈을 건드리지 않도록 임시 HOME 으로만 검증한다. mod 자체의 동작은
`claude plugin test res/mods/<mod>` 가 검증한다.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "jarvis" / "modules" / "setup"
APPLY = ROOT / "res" / "mods" / "apply.py"
KEY = "CLAUDE_CODE_PLUGIN_DIRS"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    return h


def apply(home, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(APPLY), "--home", str(home), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def claude(home):
    return home / ".claude"


def settings(home):
    return json.loads((claude(home) / "settings.json").read_text(encoding="utf-8"))


def write_settings(home, data):
    (claude(home) / "settings.json").write_text(json.dumps(data), encoding="utf-8")


MOD_NAMES = ("bash-diff-filter", "cache-ttl-status", "korean-reply-rewrite")


def mod_dir(home, name):
    return str(claude(home) / "mods" / name)


def our_ref(home):
    """설치기가 env 에 넣는 값: 번들 mod 경로들을 OS 구분자로 이은 것."""
    return os.pathsep.join(mod_dir(home, n) for n in MOD_NAMES)


def test_copies_mod_and_puts_path_in_env(home):
    proc = apply(home)
    assert proc.returncode == 0, proc.stderr
    for name in MOD_NAMES:
        assert (claude(home) / "mods" / name / ".claude-plugin" / "plugin.json").is_file()
        assert (claude(home) / "mods" / name / "hooks" / "register.ts").is_file()
    mod = claude(home) / "mods" / "bash-diff-filter"
    assert (mod / ".claude-plugin" / "plugin.json").is_file()
    assert (mod / "hooks" / "hooks.json").is_file()
    assert (mod / "hooks" / "register.ts").is_file()
    # 테스트 파일은 사용자 홈에 깔지 않는다
    assert not (mod / "hooks" / "register.test.ts").exists()
    assert settings(home)["env"][KEY] == our_ref(home)


def test_preserves_existing_env_value_and_other_keys_and_uninstall_restores(home):
    other = os.pathsep.join(["/opt/a", "/opt/b"])
    before = {"model": "opus", "env": {"FOO": "1", KEY: other}}
    write_settings(home, before)

    assert apply(home).returncode == 0
    assert settings(home)["env"][KEY] == os.pathsep.join([other, our_ref(home)])
    assert settings(home)["env"]["FOO"] == "1"

    proc = apply(home, "--uninstall")
    assert proc.returncode == 0, proc.stderr
    assert settings(home) == before
    assert not (claude(home) / "mods").exists()
    assert not (claude(home) / ".claude-code-setup.json").exists()


def test_uninstall_removes_everything_it_created_if_settings_was_absent(home):
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert not (claude(home) / "settings.json").exists()
    assert not (claude(home) / "mods").exists()


def test_uninstall_removes_env_if_it_was_absent(home):
    write_settings(home, {"model": "opus"})
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home) == {"model": "opus"}


def test_rerun_rewrites_nothing(home):
    write_settings(home, {"model": "opus"})
    assert apply(home).returncode == 0
    snapshot = sorted(p.name for p in claude(home).iterdir())
    proc = apply(home)
    assert proc.returncode == 0, proc.stderr
    assert "already installed" in proc.stdout
    assert sorted(p.name for p in claude(home).iterdir()) == snapshot  # 새 .bak 없음


def test_dry_run_writes_nothing(home):
    proc = apply(home, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "DRY RUN" in proc.stdout
    assert list(claude(home).iterdir()) == []

    assert apply(home).returncode == 0
    before = (claude(home) / "settings.json").read_bytes()
    assert apply(home, "--uninstall", "--dry-run").returncode == 0
    assert (claude(home) / "settings.json").read_bytes() == before
    assert (claude(home) / "mods" / "bash-diff-filter" / "hooks" / "register.ts").is_file()


def test_user_edited_mod_file_is_neither_overwritten_nor_removed(home):
    write_settings(home, {"model": "opus"})
    assert apply(home).returncode == 0
    edited = claude(home) / "mods" / "bash-diff-filter" / "hooks" / "register.ts"
    edited.write_text("// mine\n", encoding="utf-8")

    proc = apply(home)
    assert proc.returncode == 1
    assert edited.read_text(encoding="utf-8") == "// mine\n"

    assert apply(home, "--uninstall").returncode == 0
    assert edited.read_text(encoding="utf-8") == "// mine\n"
    assert KEY not in settings(home).get("env", {})


def test_existing_foreign_mod_folder_is_not_overwritten(home):
    foreign = claude(home) / "mods" / "bash-diff-filter" / "hooks" / "register.ts"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("// someone else\n", encoding="utf-8")
    proc = apply(home)
    assert proc.returncode == 1
    assert foreign.read_text(encoding="utf-8") == "// someone else\n"
    assert not (claude(home) / "settings.json").exists()


def test_unparseable_settings_stops_without_writing(home):
    (claude(home) / "settings.json").write_text("{ broken", encoding="utf-8")
    assert apply(home).returncode == 1
    assert not (claude(home) / "mods").exists()


def test_manifest_preserves_other_sections(home):
    (claude(home) / ".claude-code-setup.json").write_text(
        json.dumps({"omc_hud": {"refresh_interval_added": True}}), encoding="utf-8")
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert json.loads((claude(home) / ".claude-code-setup.json").read_text(encoding="utf-8")) == {
        "omc_hud": {"refresh_interval_added": True}}


def test_engine_generated_types_are_not_copied_and_uninstall_removes_them(home):
    assert apply(home).returncode == 0
    mod = claude(home) / "mods" / "bash-diff-filter"
    assert not (mod / ".claude-plugin" / "types").exists()
    # 엔진은 mod 를 로드할 때마다 이 자리에 타입과 tsconfig 를 쓴다
    gen = mod / ".claude-plugin" / "types" / "claude-code"
    gen.mkdir(parents=True)
    (gen / "index.d.ts").write_text("// generated\n", encoding="utf-8")
    (mod / ".claude-plugin" / "types" / "tsconfig.json").write_text("{}", encoding="utf-8")
    (mod / "tsconfig.json").write_text(
        '{\n  "extends": "./.claude-plugin/types/tsconfig.json"\n}\n', encoding="utf-8")
    assert apply(home).returncode == 0  # 재설치는 생성 파일 때문에 멈추지 않는다
    assert apply(home, "--uninstall").returncode == 0
    assert not (claude(home) / "mods").exists()


def test_identical_file_left_by_interrupted_install_is_claimed_on_rerun(home):
    write_settings(home, {"model": "opus"})
    src = ROOT / "res" / "mods" / "bash-diff-filter" / "hooks" / "register.ts"
    dst = claude(home) / "mods" / "bash-diff-filter" / "hooks" / "register.ts"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(src.read_bytes())  # 영수증을 쓰기 전에 끊긴 설치
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert not (claude(home) / "mods").exists()


def test_same_path_with_different_notation_is_not_added_twice(home):
    first = mod_dir(home, MOD_NAMES[0])
    variant = first.upper() if os.name == "nt" else first + "/"
    write_settings(home, {"env": {KEY: variant}})
    assert apply(home).returncode == 0
    assert settings(home)["env"][KEY] == os.pathsep.join([variant] + [mod_dir(home, n) for n in MOD_NAMES[1:]])


def test_reinstall_after_env_block_removal_lets_uninstall_remove_env(home):
    write_settings(home, {"model": "opus", "env": {"FOO": "1"}})
    assert apply(home).returncode == 0
    write_settings(home, {"model": "opus"})  # 사용자가 env 블록을 통째로 지웠다
    assert apply(home).returncode == 0
    assert apply(home, "--uninstall").returncode == 0
    assert settings(home) == {"model": "opus"}


def test_malformed_manifest_stops_without_writing(home):
    (claude(home) / ".claude-code-setup.json").write_text(
        json.dumps({"mods": {"env_dirs": "not-a-list"}}), encoding="utf-8")
    assert apply(home).returncode == 1
    assert apply(home, "--uninstall").returncode == 1
    assert not (claude(home) / "mods").exists()


def test_mod_source_working_byproducts_are_not_copied(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mods_apply_under_test", APPLY)
    mods = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mods)
    # 레포 원본을 건드리지 않도록 번들을 임시 폴더에 복사해 그곳을 원본으로 삼는다.
    shutil.copytree(APPLY.parent / "bash-diff-filter", tmp_path / "bash-diff-filter")
    mods.HERE = tmp_path
    stray = tmp_path / "bash-diff-filter" / ".omc" / "state" / "x.json"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}", encoding="utf-8")
    files = mods.source_files("bash-diff-filter")
    assert ".omc/state/x.json" not in files
    assert ".claude-plugin/plugin.json" in files


def test_uninstall_keeps_root_tsconfig_made_by_user(home):
    write_settings(home, {"model": "opus"})
    assert apply(home).returncode == 0
    mine = claude(home) / "mods" / "bash-diff-filter" / "tsconfig.json"
    mine.write_text('{"compilerOptions": {"strict": true}}', encoding="utf-8")
    assert apply(home, "--uninstall").returncode == 0
    assert mine.is_file()


load_tests = collect(globals())
