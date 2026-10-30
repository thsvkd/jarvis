"""플랫폼마다 공식 설치본 하나를 읽는다. Mac·Windows 갱신은 Orca 자체 업데이터에 맡긴다."""
import json
import os
import plistlib
import shutil
import struct
import sys
import tempfile
import time
import zipfile
import platform
import subprocess
import psutil

from . import config, orca, orca_rpc
from .util import run

MAC_APP = "/Applications/Orca.app"
USER_DEB = os.path.expanduser("~/.local/opt/orca")
SKILLS_MARK = os.path.join(config.STATE_DIR, "orca-skills-version")  # 번들 스킬을 마지막으로 맞춘 Orca 판


def profile():
    if sys.platform == "darwin":
        return {"kind": "mac-app", "root": MAC_APP, "resources": MAC_APP + "/Contents/Resources",
                "cli": MAC_APP + "/Contents/Resources/bin/orca", "manifest": "latest-mac.yml"}
    if sys.platform == "win32":
        root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "orca")
        return {"kind": "windows-user", "root": root, "resources": os.path.join(root, "resources"),
                "cli": os.path.join(root, "resources", "bin", "orca.exe"), "manifest": "latest.yml"}
    if sys.platform.startswith("linux"):
        root = "/opt/Orca"
        kind = "linux-deb"
        if not os.path.isfile(root + "/resources/package-type") and os.path.isfile(USER_DEB + "/opt/Orca/resources/package-type"):
            root, kind = USER_DEB + "/opt/Orca", "linux-user-deb"
        return {"kind": kind, "root": root, "resources": root + "/resources", "cli": root + "/resources/bin/orca-ide"}
    raise RuntimeError("지원하지 않는 운영체제입니다")


def asar_version(resources):
    """앱을 실행하지 않고 ASAR 안 package.json 의 버전을 읽는다(실행본·설치본 구분)."""
    with open(os.path.join(resources, "app.asar"), "rb") as f:
        _, header_size, _, json_size = struct.unpack("<4I", f.read(16))
        if not 0 < json_size < 32 << 20:
            raise RuntimeError("Orca ASAR 헤더가 이상합니다")
        entry = json.loads(f.read(json_size))["files"]["package.json"]
        if not 0 < entry["size"] < 1 << 20:
            raise RuntimeError("Orca package.json 크기가 이상합니다")
        f.seek(8 + header_size + int(entry["offset"]))
        return json.loads(f.read(entry["size"]))["version"]


def installed():
    p = profile()
    if p["kind"] == "mac-app":
        with open(p["root"] + "/Contents/Info.plist", "rb") as f:
            return plistlib.load(f)["CFBundleShortVersionString"]
    if p["kind"] == "linux-deb":
        rc, out, _ = run(["dpkg-query", "-W", "-f=${Version}", "orca-ide"], timeout=10)
        if rc == 0:
            return out.strip()
    return asar_version(p["resources"])


def skills_version():
    try:
        with open(SKILLS_MARK, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def skills():
    """ORCA-8: 번들 스킬을 모두 지금 판의 사본으로 맞춘다(`skills update --all`, 사용자가 고친 스킬도 덮어쓴다).
    맞춘 판을 남겨 같은 판에서는 다시 계획하지 않는다. modules/orca 가 깐 스킬은 새 사본의 다이제스트를 다시 기록해
    uninstall 이 계속 우리 것으로 알아보게 한다(실패해도 남은 상태를 기록한다)."""
    rc, out, err = run([profile()["cli"], "skills", "update", "--all"], timeout=900)  # npx 로 받는다: npx·네트워크가 없으면 실패다
    _record_owned_skills()
    if rc:
        raise RuntimeError("스킬 갱신 실패: " + ((err or out).strip()[:300] or f"rc={rc}"))
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(SKILLS_MARK, "w", encoding="utf-8") as f:
        f.write(installed())


def _record_owned_skills():
    from . import orca_module
    t = orca_module.tools()
    path = t.home_dir() / ".claude/.claude-code-setup.json"
    receipt = t.read_receipt(path)
    owned = (receipt.get("tool_installs") or {}).get("orca")
    if isinstance(owned, dict) and owned.get("skills_added"):
        t.record_skills(list(owned["skills_added"]), owned, *t.skill_locations(t.home_dir()))
        t.save_receipt(path, receipt)


def validate_runtime():
    """RPC 가 다른 설치본을 갱신하거나 새 실행본을 구버전으로 덮어쓰지 않게 한다."""
    p = profile()
    d = orca.find_dir(orca.DIRS)
    metadata = orca._read(d, "orca-runtime.json") if d else None
    if not metadata or not metadata.get("pid"):
        raise RuntimeError("실행 중인 Orca 설치 경로를 확인할 수 없습니다")
    expected = os.path.join(p["root"], "Contents", "MacOS", "Orca") if p["kind"] == "mac-app" else \
        os.path.join(p["root"], "Orca.exe" if p["kind"] == "windows-user" else "orca-ide")
    process = psutil.Process(int(metadata["pid"]))
    # 새 Orca 는 로컬 RPC 를 orcad(Node) 자식으로 옮긴다. 그 감독 부모까지 같은 설치본인지 확인한다.
    processes = [process] + process.parents()[:8]
    if not any(os.path.normcase(os.path.realpath(proc.exe())) == os.path.normcase(os.path.realpath(expected)) for proc in processes):
        raise RuntimeError("실행 중인 Orca 가 공식 설치 경로와 다릅니다")
    if orca_rpc.call("updater.getStatus").get("appVersion") != installed():
        raise RuntimeError("Orca 설치본과 실행본 버전이 다릅니다. 서비스 경로·재시작 상태를 확인하세요")
    if p["kind"].startswith("linux-"):
        from . import orca_issue
        manager = orca_issue.manager(pairing=False)
        paths = {os.path.normcase(os.path.realpath(a)) for a in (manager or {}).get("argv", []) if os.path.isfile(a)}
        if not paths & {os.path.normcase(os.path.realpath(expected)), os.path.normcase(os.path.realpath(p["cli"]))}:
            raise RuntimeError("orca-serve 유닛이 선택한 공식 설치본을 가리키지 않습니다")


def wait_running(target, timeout=240):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            got = orca_rpc.call("updater.getStatus").get("appVersion")
            if got == target:
                return got
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"실행 중인 Orca 가 {target} 을 보고하지 않았습니다")


def contract_versions(data, target, arch):
    if not isinstance(data, dict) or data.get("version") != target or data.get("appId") != "com.stablyai.orca" \
            or data.get("architecture") != arch:
        raise RuntimeError("새 Orca 패키지의 대상 버전·아키텍처가 일치하지 않습니다")
    versions = data.get("attachableDaemonProtocolVersions")
    if not isinstance(versions, list) or not versions or any(type(n) is not int or n < 1 for n in versions) \
            or type(data.get("daemonProtocolVersion")) is not int or data["daemonProtocolVersion"] not in versions:
        raise RuntimeError("새 Orca 패키지의 데몬 호환성 계약이 잘못되었습니다")
    return set(versions)


def archive_contract(path, target, arch):
    with zipfile.ZipFile(path) as archive:
        name = "Orca.app/Contents/Resources/orca-local-build.json"
        entry = archive.getinfo(name)
        if entry.file_size > 64 << 10:
            raise RuntimeError("새 Orca 패키지의 호환성 계약이 너무 큽니다")
        return contract_versions(json.loads(archive.read(name)), target, arch)


def native_contract(target, meta):
    """공식 업데이터가 받은 바로 그 패키지의 해시와 버전별 계약을 설치 전에 확인한다."""
    from .orca_update import check_file
    cache = (os.path.expanduser("~/Library/Caches/orca-updater/pending") if sys.platform == "darwin" else
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "orca-updater", "pending"))
    with open(os.path.join(cache, "update-info.json"), encoding="utf-8") as f:
        info = json.load(f)
    name = info.get("fileName")
    if name != meta["name"] or os.path.basename(name) != name or info.get("sha512") != meta["sha512"]:
        raise RuntimeError("공식 업데이터의 캐시가 이번 대상 패키지와 일치하지 않습니다")
    path = os.path.join(cache, name)
    check_file(path, meta["sha512"], meta["size"])
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
    if sys.platform == "darwin":
        return archive_contract(path, target, arch)
    # NSIS 는 app-64.7z 에 앱을 담는다. 기존 세션이 있을 때 검사 도구가 없으면 보류한다.
    seven = shutil.which("7z") or shutil.which("7z.exe")
    if not seven:
        candidate = os.path.join(os.environ.get("ProgramFiles", ""), "7-Zip", "7z.exe")
        seven = candidate if os.path.isfile(candidate) else None
    if not seven:
        raise RuntimeError("열린 세션을 보존하며 Windows 패키지를 검사하려면 7-Zip 이 필요합니다")
    with tempfile.TemporaryDirectory(prefix="jarvis-orca-compat-") as directory:
        inner = os.path.join(directory, "app.7z")
        with open(inner, "wb") as f:
            subprocess.run([seven, "e", "-so", path, "$PLUGINSDIR/app-64.7z"], stdout=f,
                           stderr=subprocess.PIPE, check=True, timeout=120)
        p = subprocess.run([seven, "e", "-so", inner, "resources/orca-local-build.json"],
                           capture_output=True, check=True, timeout=60)
        return contract_versions(json.loads(p.stdout), target, arch)


def native_update(before, target, phase, idle, prepare=None, verify=None, checkpoint=None):
    """공식 RPC 는 비동기다. downloaded 뒤에만 install, 설치본·재연결 버전까지 확인한다."""
    app = "current"
    if before != target:
        idle()
        phase("manifest")
        orca_rpc.call("updater.check", {"includePrerelease": False, "includePerfPrerelease": False})
        end, requested = time.monotonic() + 1200, False
        while time.monotonic() < end:
            s = orca_rpc.call("updater.getStatus")
            status = s.get("status") or {}
            state = status.get("state")
            if state == "error":
                raise RuntimeError(str(status.get("message") or status.get("error") or "Orca 업데이터 실패")[:300])
            if state in ("available", "downloaded") and status.get("version") != target:
                raise RuntimeError("공식 업데이터 대상 버전이 릴리스와 다릅니다. 다시 확인하세요")
            if state == "available" and not requested:
                phase("download")
                orca_rpc.call("updater.download")
                requested = True
            if state == "downloaded":
                if prepare:
                    prepare()
                idle()
                phase("install")
                if checkpoint:
                    checkpoint()
                accepted = orca_rpc.call("updater.install")
                if accepted.get("accepted") is not True or accepted.get("targetVersion") != target:
                    raise RuntimeError("Orca 가 업데이트 설치를 수락하지 않았습니다")
                wait_running(target)
                if installed() != target:
                    raise RuntimeError("업데이트 뒤 설치본 버전이 일치하지 않습니다")
                if verify:
                    verify()
                app = "updated"
                break
            time.sleep(2)
        else:
            raise RuntimeError("Orca 업데이터가 20분 안에 다운로드를 마치지 않았습니다")
    phase("skills")
    try:
        skills()
    except Exception as e:
        return {"app": app, "skills": "failed", "from_version": before, "to_version": target, "error": str(e)[:300]}
    return {"app": app, "skills": "updated", "from_version": before, "to_version": target, "error": None}


def install_user_deb(path):
    """sudo 없는 호스트: 같은 공식 deb 를 새 폴더에 풀어 검증하고 기존 경로를 교체한다."""
    parent = os.path.dirname(USER_DEB)
    staged = tempfile.mkdtemp(prefix=".orca-new-", dir=parent)
    backup = USER_DEB + ".previous"
    try:
        rc, out, err = run(["dpkg-deb", "-x", path, staged], timeout=180)
        if rc or not os.path.isfile(staged + "/opt/Orca/resources/bin/orca-ide"):
            raise RuntimeError((err or out).strip()[:200] or "deb 압축 해제 실패")
        asar_version(staged + "/opt/Orca/resources")
        if os.path.exists(backup):
            raise RuntimeError("이전 Orca 백업이 남아 있어 교체를 보류합니다")
        os.rename(USER_DEB, backup)
        try:
            os.rename(staged, USER_DEB)
        except Exception:
            os.rename(backup, USER_DEB)
            raise
        return backup
    finally:
        if os.path.isdir(staged):
            shutil.rmtree(staged)


def recover_user_deb():
    """두 rename 사이에 전원이 꺼졌다면 원래 설치 경로를 먼저 복구한다."""
    backup = USER_DEB + ".previous"
    if not os.path.exists(USER_DEB) and os.path.isdir(backup):
        asar_version(backup + "/opt/Orca/resources")
        os.rename(backup, USER_DEB)
