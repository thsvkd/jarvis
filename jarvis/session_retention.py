"""Claude Code 로컬 대화 보존 설정의 계획과 소유권 관리."""
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

TARGET = 36500
KEY = "cleanupPeriodDays"
RECORD = "session_retention"


def _read(path):
    if path.is_symlink():
        raise ValueError(f"심볼릭 링크는 변경하지 않습니다: {path}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, "missing"
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 객체가 아닙니다: {path}")
    return data, hashlib.sha256(raw).hexdigest()


def inspect(home):
    directory = Path(home) / ".claude"
    try:
        if directory.is_symlink():
            raise ValueError(".claude가 심볼릭 링크입니다")
        settings, settings_hash = _read(directory / "settings.json")
        receipt, receipt_hash = _read(directory / ".claude-code-setup.json")
        record = receipt.get(RECORD)
        if RECORD in receipt and (not isinstance(record, dict) or record.get("state") != "applied" or record.get("value") != TARGET):
            raise ValueError("이전 보존 설정 작업의 영수증을 확인해야 합니다")
        if KEY not in settings:
            state = "missing" if record is None else "modified"
        elif record and settings[KEY] == TARGET and type(settings[KEY]) is int:
            state = "managed"
        else:
            state = "modified" if record else "user_configured"
        value = settings.get(KEY)
        if not isinstance(value, (str, int, float, bool, type(None))):
            value = "형식 오류"
        return {"state": state, "value": value, "settings_hash": settings_hash, "receipt_hash": receipt_hash}
    except (OSError, ValueError, UnicodeError) as error:
        return {"state": "error", "error": str(error)[:240]}


def _write(path, data, expected_hash):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        new_hash = hashlib.sha256(Path(tmp).read_bytes()).hexdigest()
        if path.parent.is_symlink() or _read(path)[1] != expected_hash:
            raise ValueError("파일이 교체 직전에 바뀌어 보존했습니다: " + str(path))
        os.replace(tmp, path)
        return new_hash
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def change(home, expected, remove=False):
    """중단된 두 파일 쓰기는 pending 영수증으로 남겨 다음 실행에서 멈춘다."""
    directory = Path(home) / ".claude"
    settings_path = directory / "settings.json"
    receipt_path = directory / ".claude-code-setup.json"
    if inspect(home) != expected:
        raise ValueError("계획 후 보존 설정이 바뀌었습니다")
    settings, _ = _read(settings_path)
    receipt, _ = _read(receipt_path)
    if remove and expected["state"] != "managed":
        raise ValueError("사용자가 바꾼 보존 설정을 그대로 둡니다")
    if not remove and expected["state"] != "missing":
        raise ValueError("기존 보존 설정을 그대로 둡니다")
    directory.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        backup = settings_path.with_name(f"settings.json.bak.{time.time_ns()}")
        shutil.copy2(settings_path, backup)
    if inspect(home) != expected:
        raise ValueError("백업 중 보존 설정이 바뀌었습니다")
    receipt[RECORD] = {"state": "pending", "value": TARGET, "action": "remove" if remove else "add"}
    pending_hash = _write(receipt_path, receipt, expected["receipt_hash"])
    _, settings_hash = _read(settings_path)
    if settings_hash != expected["settings_hash"]:
        raise ValueError("적용 중 설정이 바뀌어 보존했습니다; 보류 영수증을 확인하세요")
    if remove:
        del settings[KEY]
    else:
        settings[KEY] = TARGET
    _write(settings_path, settings, expected["settings_hash"])
    current_receipt, _ = _read(receipt_path)
    if current_receipt != receipt:
        raise ValueError("적용 중 영수증이 바뀌어 보존했습니다")
    if remove:
        del receipt[RECORD]
    else:
        receipt[RECORD] = {"state": "applied", "value": TARGET}
    _write(receipt_path, receipt, pending_hash)
