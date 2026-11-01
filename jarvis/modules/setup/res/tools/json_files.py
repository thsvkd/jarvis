"""설정 파일 보조 함수: 덮어쓰기 전 타임스탬프 백업, JSON 쓰기.

mods·omc·output_style 워커가 쓴다. 훅이 jarvis 로 옮겨 가면서 res/hooks/merge_and_diff.py 에서 본문 그대로 옮겼다.
"""
import datetime
import json
import os
import shutil


def backup_file(path):
    """덮어쓰기 전 타임스탬프 백업을 만든다. 생성한 백업 경로(없으면 None)를 반환."""
    if not os.path.exists(path):
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"{path}.bak.{ts}"
    shutil.copy2(path, backup)
    return backup


def write_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
