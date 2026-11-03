"""POSIX 데스크톱 페어링의 읽기 전용 점검과 재개. 서버 서비스 관리는 orca_serve가 맡는다."""
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import uuid


OWNER = 'jarvis-posix'


def _host_key(value):
    # Orca runtime-environment-host-key-match.ts와 같은 정규화: padding·URL-safe 표기 차이를 허용한다.
    return value.strip().replace('-', '+').replace('_', '/').rstrip('=') if isinstance(value, str) else ''


def tailnet_address(value):
    try:
        address = ipaddress.ip_address(value)
        if address in ipaddress.ip_network('100.64.0.0/10'):
            return str(address)
    except ValueError:
        pass
    raise RuntimeError('Orca 페어링 주소는 tailnet IPv4여야 합니다')


def inspect(server_profile, client_profile, record=None):
    """파일만 읽어 서버 공개 키와 같은 클라이언트 연결을 찾는다. 비밀 값 대신 다이제스트만 계획에 넣는다.

    공식 스키마: stablyai/orca src/shared/runtime-environments.ts (version 1, endpoints[].publicKeyB64).
    """
    digest = hashlib.sha256()

    def read(profile, name):
        profile = Path(profile)
        if any(p.is_symlink() for p in (profile, *profile.parents)):
            raise RuntimeError('연결된 Orca 프로필은 바꾸지 않습니다')
        path = profile / name
        if path.is_symlink():
            raise RuntimeError('연결된 Orca 설정 파일은 바꾸지 않습니다')
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = b''
        digest.update(name.encode() + b'\0' + str(profile).encode() + b'\0' + raw + b'\0')
        try:
            value = json.loads(raw.decode('utf-8-sig')) if raw else {}
        except (ValueError, UnicodeError):
            raise RuntimeError('Orca 프로필 JSON을 읽지 못해 보존합니다') from None
        if not isinstance(value, dict):
            raise RuntimeError('Orca 프로필 형식이 달라 보존합니다')
        return value

    key = read(server_profile, 'orca-e2ee-keypair.json').get('publicKeyB64')
    if (Path(server_profile) / 'orca-e2ee-keypair.json').exists() and (not isinstance(key, str) or not key):
        raise RuntimeError('Orca 서버 공개 키 형식이 달라 키 파일을 보존합니다')
    environments = []
    for profile in (server_profile, client_profile):
        store = read(profile, 'orca-environments.json')
        entries = store.get('environments', [])
        if (Path(profile) / 'orca-environments.json').exists() and (
                type(store.get('version')) is not int or store['version'] != 1 or not isinstance(entries, list)):
            raise RuntimeError('Orca 연결 목록 형식이 달라 보존합니다')
        if any(not isinstance(e, dict) or not isinstance(e.get('id'), str)
               or not isinstance(e.get('endpoints'), list) for e in entries):
            raise RuntimeError('Orca 연결 항목 형식이 달라 보존합니다')
        if Path(profile) == Path(client_profile):
            environments.extend(entries)
    digest.update(json.dumps(record or {}, sort_keys=True).encode())
    environment_id = next((e['id'] for e in environments if key and any(
        isinstance(ep, dict) and _host_key(ep.get('publicKeyB64')) == _host_key(key) and ep.get('deviceToken')
        for ep in e['endpoints'])), None)
    return {'environment_id': environment_id, 'fingerprint': digest.hexdigest()}


def pair(cli, record, helpers, on_record):
    """옮긴 워커로 등록하고, ID 기록이 끊긴 경우 사전 목록·이름으로 복구한다. 실제 서버는 중지하지 않는다."""
    record = dict(record)
    server, client = record['server_profile'], record['client_profile']
    state = inspect(server, client, record)
    if 'client_environment_ids_before_pairing' in record and helpers.runtime_is_active(server):
        raise RuntimeError('임시 페어링 서버가 남아 있어 기록을 보존합니다. 종료 확인 뒤 재개하십시오')
    saved_id = record.get('desktop_environment_id')
    if not saved_id and 'client_environment_ids_before_pairing' in record:
        saved_id = helpers.find_new_environment_id(
            cli, client, record['desktop_environment_name'], record['client_environment_ids_before_pairing'])
        if saved_id and saved_id != state['environment_id']:
            raise RuntimeError('중단 뒤 발견한 Orca 연결이 이 서버와 달라 기록을 보존합니다')
    if saved_id:
        if saved_id != state['environment_id']:
            raise RuntimeError('기록한 데스크톱 연결이 바뀌어 페어링 기록을 보존합니다')
        record['desktop_environment_id'] = saved_id
        on_record(record)
        return record
    if state['environment_id']:
        record['desktop_environment_id'] = state['environment_id']
        on_record(record)
        return record
    if helpers.runtime_is_active(server):
        raise RuntimeError('실행 중인 Orca 서버를 멈춰야 페어링할 수 있습니다. 기존 서버를 보존했습니다')
    address = tailnet_address(record['pairing_address'])
    helpers.preserve_client_environments(server, client)
    record.setdefault('desktop_environment_name', f'{socket.gethostname()} Orca server ({uuid.uuid4().hex[:8]})')
    on_record(record)

    def baseline(environments):
        record['client_environment_ids_before_pairing'] = [e['id'] for e in environments if isinstance(e, dict) and e.get('id')]
        on_record(record)

    def added(environment_id):
        record['desktop_environment_id'] = environment_id
        on_record(record)

    helpers.issue_pairings(cli, server, client, address, record['desktop_environment_name'],
                           on_pairing_started=baseline, on_desktop_added=added)
    if helpers.runtime_is_active(server):
        raise RuntimeError('임시 페어링 서버가 남아 있어 영구 서비스를 시작하지 않습니다')
    return record
