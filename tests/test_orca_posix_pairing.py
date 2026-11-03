"""POSIX의 실제 계약 층과 옮긴 페어링 워커를 임시 HOME·가짜 Orca CLI로 함께 검증한다."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from jarvis import orca_module as m


FAKE_ORCA = r'''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ['HOME'])
profile = Path(os.environ['ORCA_USER_DATA_PATH'])
profile.mkdir(parents=True, exist_ok=True)
with (home / 'commands').open('a') as f:
    f.write(' '.join(sys.argv[1:3]) + '\n')
store = profile / 'orca-environments.json'
data = json.loads(store.read_text()) if store.exists() else {'version': 1, 'environments': []}
args = sys.argv[1:]
if args[0] == 'serve':
    (profile / 'orca-e2ee-keypair.json').write_text(json.dumps({'publicKeyB64': 'synthetic-server-key'}))
    (profile / 'orca-runtime.json').write_text(json.dumps({'pid': os.getpid()}))
    print(json.dumps({'type': 'orca_server_ready', 'runtimeId': 'synthetic-runtime',
        'pairing': {'available': True, 'scope': 'runtime', 'url': 'orca://pair?code=synthetic'}}), flush=True)
    while True: time.sleep(1)
elif args[:2] == ['environment', 'list']:
    print(json.dumps({'ok': True, 'result': {'environments': data['environments']}}))
elif args[:2] == ['environment', 'add']:
    item = {'id': 'new-desktop', 'name': args[args.index('--name') + 1], 'createdAt': 1, 'updatedAt': 1,
        'lastUsedAt': None, 'runtimeId': 'synthetic-runtime', 'preferredEndpointId': 'ws-new-desktop',
        'endpoints': [{'id': 'ws-new-desktop', 'kind': 'websocket', 'label': 'server',
        'endpoint': 'ws://100.64.0.5:6768', 'publicKeyB64': 'synthetic-server-key', 'deviceToken': 'synthetic-token'}]}
    data['environments'].append(item)
    store.write_text(json.dumps(data))
    print(json.dumps({'ok': True, 'result': {'environment': item}}))
elif args[0] == 'status':
    found = any(x['id'] == args[args.index('--environment') + 1] for x in data['environments'])
    print(json.dumps({'ok': True, 'result': {'runtime': {'reachable': found and (home / 'activated').exists()}}}))
else:
    sys.exit(2)
'''


@unittest.skipIf(sys.platform == 'win32', 'POSIX 페어링·서비스 경로')
class PairingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jarvis-posix-pairing-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.patch(mock.patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home),
                                               'XDG_CONFIG_HOME': str(self.home / '.config')}))
        self.cli = self.home / 'orca'
        self.cli.write_text('#!' + sys.executable + '\n' + FAKE_ORCA)
        self.cli.chmod(0o755)
        self.helpers = m._server_tools()
        self.server, self.client = (self.home / '.config' / n for n in ('orca', 'orca-desktop'))
        self.receipt = self.home / '.claude/.claude-code-setup.json'
        self.receipt.parent.mkdir()
        self.receipt.write_text(json.dumps({'tool_installs': {}, 'hooks': {'keep': True}}))
        self.patch(mock.patch.object(m.sys, 'platform', 'linux'))
        self.patch(mock.patch.object(m.orca_install, 'profile', return_value={'kind': 'linux-deb', 'cli': str(self.cli)}))
        self.patch(mock.patch.object(m, '_installed', return_value='1.4.245'))
        self.patch(mock.patch.object(m.orca_update, 'check', return_value={
            'supported': True, 'current': True, 'latest': '1.4.245', 'error': None}))
        self.patch(mock.patch.object(m.orca_install, 'skills_version', return_value='1.4.245'))
        self.patch(mock.patch.object(m.orca_serve, 'linger', return_value=True))
        self.patch(mock.patch.object(m.orca_serve, 'login_env', return_value={'PATH': '/usr/bin', 'SHELL': '/bin/sh'}))
        self.patch(mock.patch.object(self.helpers, 'discover_pairing_address', return_value='100.64.0.5'))
        self.patch(mock.patch.object(self.helpers, 'port_is_listening', return_value=False))
        self.patch(mock.patch.object(m.orca_serve.orca_issue, 'manager', side_effect=self.manager))
        self.patch(mock.patch.object(m.orca_serve, 'run', side_effect=self.activate))
        self.service_commands = []

    def patch(self, p):
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def manager(self, pairing=True):
        if (self.home / 'activated').exists():
            return {'kind': 'systemd', 'argv': [str(self.cli), 'serve', '--supervise'],
                    'env': {'ORCA_USER_DATA_PATH': str(self.server)}}

    def activate(self, argv, **kwargs):
        self.service_commands.append(argv)
        if argv[:4] == ['systemctl', '--user', 'enable', '--now']:
            if not self.environments():
                return 1, '', '데스크톱 연결 없이 서버 활성화'
            (self.home / 'activated').touch()
        if argv[:2] == ['launchctl', 'bootstrap']:
            if (self.home / 'activated').exists():
                return 1, '', '이미 적재된 LaunchAgent'
            (self.home / 'activated').touch()
        return 0, '', ''

    def record(self):
        return json.loads(self.receipt.read_text())['tool_installs'].get('orca_server', {})

    def environments(self):
        path = self.client / 'orca-environments.json'
        return json.loads(path.read_text())['environments'] if path.exists() else []

    def apply(self):
        return m.apply(m.plan()['plan_id'])

    def commands(self):
        path = self.home / 'commands'
        return path.read_text().splitlines() if path.exists() else []

    def test_new_install_pairs_before_activating_and_reaches_current(self):
        """covers: ORCA-1, ORCA-2, ORCA-9 — 페어링 없는 done과 plan의 쓰기를 잡는다."""
        before = self.receipt.read_bytes()
        plan = m.plan()
        self.assertFalse(self.server.exists() or self.client.exists())
        self.assertEqual(self.receipt.read_bytes(), before)
        self.assertEqual([s['id'] for s in plan['steps']], ['pairing', 'server'])
        output = self.apply()
        self.assertEqual(output['status'], 'done', output)
        self.assertEqual([e['id'] for e in self.environments()], ['new-desktop'])
        record = self.record()
        self.assertEqual(record['state'], 'configured')
        self.assertEqual(record['desktop_environment_id'], 'new-desktop')
        self.assertIn('service_digest', record)
        self.assertEqual(json.loads(self.receipt.read_text())['hooks'], {'keep': True})
        self.assertEqual(m.plan()['steps'], [])
        public = json.dumps(output) + json.dumps(plan) + self.receipt.read_text()
        self.assertNotIn('synthetic-token', public)
        self.assertNotIn('orca://pair', public)

    def test_interrupted_environment_registration_is_recovered_without_another_pairing(self):
        """covers: ORCA-1 — environment add 뒤 ID 기록이 끊겨도 이중 등록하지 않는다."""
        save = m._save_server
        def interrupted(record):
            if record.get('desktop_environment_id'):
                raise RuntimeError('연결을 만든 뒤 ID 기록 중단을 모의')
            save(record)
        with mock.patch.object(m, '_save_server', side_effect=interrupted):
            first = self.apply()
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(len(self.environments()), 1)
        self.assertIn('client_environment_ids_before_pairing', self.record())
        self.assertNotIn('desktop_environment_id', self.record())
        self.assertEqual(self.apply()['status'], 'done')
        self.assertEqual(self.commands().count('environment add'), 1)

    def test_failed_activation_keeps_pairing_and_retries_without_rewriting_the_unit(self):
        """covers: ORCA-1, ORCA-2 — 활성화 실패가 연결 소유·재개 정보를 잃지 않는다."""
        with mock.patch.object(m.orca_serve, 'run', return_value=(1, '', 'synthetic activation failure')):
            first = self.apply()
        self.assertEqual(first['status'], 'failed')
        record = self.record()
        self.assertEqual(record['state'], 'configuring')
        self.assertIn('desktop_environment_id', record)
        self.assertEqual(record['desktop_environment_id'], 'new-desktop')
        unit = Path(record['service_path'])
        before = unit.read_bytes()
        self.assertEqual(self.apply()['status'], 'done')
        self.assertEqual(unit.read_bytes(), before)
        self.assertEqual(self.commands().count('environment add'), 1)

    def test_new_package_install_also_pairs_before_starting_the_service(self):
        """covers: ORCA-1 — 패키지만 설치하고 신규 설치를 완료로 보고하지 않는다."""
        def install(argv):
            self.assertEqual(argv, ['--expect-version', '1.4.245', '--no-server'])
            (self.home / 'package-installed').touch()
            return 0
        with mock.patch.object(m, '_installed', side_effect=lambda: '1.4.245' if (self.home / 'package-installed').exists() else None), \
                mock.patch.object(m.orca_update.Live, 'manifest', return_value=('1.4.245', {})), \
                mock.patch.object(m.tools(), 'main', side_effect=install):
            plan = m.plan()
            self.assertEqual([s['id'] for s in plan['steps']], ['package', 'skills', 'pairing', 'server'])
            self.assertEqual(m.apply(plan['plan_id'])['status'], 'done')
            self.assertEqual(m.plan()['steps'], [])
        self.assertEqual(self.record()['desktop_environment_id'], 'new-desktop')

    def test_running_unpaired_server_is_blocked_without_writes_or_stops(self):
        """covers: ORCA-2, ORCA-9 — 기존 서버를 몰래 멈춰 페어링하지 않는다."""
        (self.home / 'activated').touch()
        before = self.receipt.read_bytes()
        with mock.patch.object(self.helpers, 'runtime_is_active', return_value=True):
            plan = m.plan()
            self.assertIn('pairing', [b['step'] for b in plan['blocked']])
            output = m.apply(plan['plan_id'])
        self.assertEqual(output['status'], 'failed')
        self.assertEqual(self.service_commands, [])
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_a_paired_existing_service_and_client_config_are_adopted_unchanged(self):
        """covers: ORCA-2, ORCA-11 — 사용자 연결과 기존 서비스의 소유를 빼앗지 않는다."""
        self.assertEqual(self.apply()['status'], 'done')
        state = json.loads(self.receipt.read_text())
        state['tool_installs'].pop('orca_server')
        self.receipt.write_text(json.dumps(state))
        config = self.client / 'orca-environments.json'
        before = config.read_bytes()
        self.service_commands.clear()
        calls = self.commands()
        self.assertEqual(self.apply()['status'], 'done')
        self.assertTrue(self.record()['adopted'])
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.commands(), calls)
        self.assertEqual(self.service_commands, [])
        out = m.uninstall(plan_id=m.uninstall(dry_run=True)['plan_id'])
        self.assertEqual(out['status'], 'done')
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.service_commands, [])

    def test_changed_client_config_invalidates_the_approved_plan(self):
        """covers: ORCA-13 — 승인 뒤 프로필이 바뀌면 등록하지 않고 stale이다."""
        plan = m.plan()
        self.client.mkdir(parents=True)
        (self.client / 'orca-environments.json').write_text(json.dumps({'version': 1, 'environments': []}))
        output = m.apply(plan['plan_id'])
        self.assertEqual(output['status'], 'stale')
        self.assertEqual(self.commands(), [])

    def test_invalid_profile_json_is_blocked_and_never_reported_as_current(self):
        """covers: ORCA-9 — 잘못된 JSON을 덮어쓰거나 막힌 계획을 current로 보고하지 않는다."""
        self.client.mkdir(parents=True)
        config = self.client / 'orca-environments.json'
        config.write_text('{"token": "synthetic-secret", INVALID')
        before = config.read_bytes()
        plan = m.plan()
        self.assertTrue(plan['blocked'])
        output = m.apply(plan['plan_id'])
        self.assertEqual(output['status'], 'failed')
        self.assertNotIn('synthetic-secret', json.dumps(plan) + json.dumps(output))
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.commands(), [])

    def test_non_tailnet_pairing_addresses_are_rejected_before_writing(self):
        """covers: ORCA-1 — loopback·LAN·공인 주소로 페어링 서버를 띄우지 않는다."""
        for address in ['127.0.0.1', '192.168.0.1', '8.8.8.8', '100.128.0.1', 'bad-address']:
            with self.subTest(address=address), mock.patch.object(self.helpers, 'discover_pairing_address', return_value=address):
                self.assertEqual(self.apply()['status'], 'failed')
        self.assertEqual(self.commands(), [])
        self.assertFalse(self.server.exists() or self.client.exists())

    def test_mac_launch_agent_preserves_pairing_and_uses_the_direct_cli(self):
        """covers: ORCA-1, ORCA-3 — Mac에서도 직접 실행 서비스와 페어링 기록이 함께 남는다."""
        self.server, self.client = (self.home / 'Library/Application Support' / n for n in ('orca', 'orca-desktop'))
        with mock.patch.object(m.sys, 'platform', 'darwin'):
            output = self.apply()
            self.assertEqual(output['status'], 'done', output)
            record = self.record()
            import plistlib
            doc = plistlib.loads(Path(record['service_path']).read_bytes())
            self.assertEqual(doc['ProgramArguments'][:3], [str(self.cli), 'serve', '--supervise'])
            self.assertEqual(record['desktop_environment_id'], 'new-desktop')
            self.assertEqual(m.plan()['steps'], [])

    def test_unfinished_pairing_receipt_is_preserved_on_uninstall(self):
        """covers: ORCA-11 — 중단된 페어링의 복구 기록을 섣불리 버리지 않는다."""
        with mock.patch.object(self.helpers, 'issue_pairings', side_effect=RuntimeError('synthetic failure')):
            self.assertEqual(self.apply()['status'], 'failed')
        before = self.receipt.read_bytes()
        dry = m.uninstall(dry_run=True)
        self.assertEqual(m.uninstall(plan_id=dry['plan_id'])['status'], 'deferred')
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_missing_public_key_in_an_existing_key_file_is_preserved(self):
        """covers: ORCA-1, ORCA-9 — 손상된 서버 키 파일을 새 키로 대체하지 않는다."""
        self.server.mkdir(parents=True)
        keyfile = self.server / 'orca-e2ee-keypair.json'
        keyfile.write_text('{}')
        self.assertEqual(self.apply()['status'], 'failed')
        self.assertEqual(keyfile.read_text(), '{}')
        self.assertEqual(self.commands(), [])

    def test_mac_connection_check_failure_resumes_without_bootstrapping_a_loaded_agent(self):
        """covers: ORCA-1, ORCA-3 — 활성화 뒤 접속 확인이 끊겨도 LaunchAgent를 다시 적재하지 않는다."""
        self.server, self.client = (self.home / 'Library/Application Support' / n for n in ('orca', 'orca-desktop'))
        with mock.patch.object(m.sys, 'platform', 'darwin'):
            with mock.patch.object(self.helpers, 'wait_for_remote_environment', side_effect=RuntimeError('접속 확인 중단')):
                self.assertEqual(self.apply()['status'], 'failed')
            self.assertEqual(self.record()['state'], 'configuring')
            self.assertEqual(self.apply()['status'], 'done')
        self.assertEqual(sum(argv[:2] == ['launchctl', 'bootstrap'] for argv in self.service_commands), 1)

    def test_empty_environment_store_is_not_treated_as_an_unconfigured_profile(self):
        """covers: ORCA-9 — 존재하지만 잘못된 연결 목록을 초기 목록으로 덮어쓰지 않는다."""
        self.client.mkdir(parents=True)
        config = self.client / 'orca-environments.json'
        config.write_text('{}')
        self.assertEqual(self.apply()['status'], 'failed')
        self.assertEqual(config.read_text(), '{}')
        self.assertEqual(self.commands(), [])

    def test_linked_client_profile_is_preserved_without_starting_pairing(self):
        """covers: ORCA-9 — 연결된 사용자 설정을 따라가서 바꾸지 않는다."""
        target = self.home / 'other-profile'
        target.mkdir()
        self.client.parent.mkdir(parents=True)
        self.client.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.apply()['status'], 'failed')
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.commands(), [])

    def test_pairing_closes_the_temporary_server_output(self):
        """covers: ORCA-1 — 임시 페어링 서버를 종료할 때 출력 파이프도 닫는다."""
        import gc
        import warnings
        gc.collect()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', ResourceWarning)
            self.assertEqual(self.apply()['status'], 'done')
            gc.collect()
        self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_a_blocked_pairing_is_not_hidden_by_a_successful_package_update(self):
        """covers: ORCA-9 — 패키지 갱신이 성공해도 계획의 막힌 페어링을 done으로 보고하지 않는다."""
        self.client.mkdir(parents=True)
        (self.client / 'orca-environments.json').write_text('invalid json')
        check = {'supported': True, 'current': False, 'latest': '1.4.246', 'error': None}
        with mock.patch.object(m.orca_update, 'check', return_value=check), \
                mock.patch.object(m, '_update', return_value=('ok', 'synthetic update')):
            output = self.apply()
        self.assertEqual(output['status'], 'failed')
        self.assertIn('pairing', [s['id'] for s in output['steps'] if s['result'] == 'skipped'])

    def test_temporary_server_that_did_not_exit_prevents_service_activation(self):
        """covers: ORCA-1, ORCA-2 — 임시 서버의 종료 실패를 무시하고 영구 서버를 시작하지 않는다."""
        processes = []
        issue = self.helpers.issue_pairing_offer
        stop = self.helpers.stop_server_process
        def capture(*args, **kwargs):
            process, offer = issue(*args, **kwargs)
            processes.append(process)
            return process, offer
        try:
            with mock.patch.object(self.helpers, 'issue_pairing_offer', side_effect=capture), \
                    mock.patch.object(self.helpers, 'stop_server_process', return_value=None):
                self.assertEqual(self.apply()['status'], 'failed')
            self.assertEqual(self.service_commands, [])
            self.assertFalse(Path(m.orca_serve.service_path()).exists())
            self.assertEqual(self.record()['desktop_environment_id'], 'new-desktop')
        finally:
            for process in processes:
                stop(process)
        self.assertEqual(self.apply()['status'], 'done')
        self.assertEqual(self.commands().count('environment add'), 1)

    def test_adding_pairing_to_an_owned_service_keeps_its_ownership(self):
        """covers: ORCA-1, ORCA-11 — 이전 판이 만든 서비스에 페어링을 더해도 adopted로 바꾸지 않는다."""
        self.assertEqual(self.apply()['status'], 'done')
        state = json.loads(self.receipt.read_text())
        record = state['tool_installs']['orca_server']
        state['tool_installs']['orca_server'] = {k: record[k] for k in (
            'state', 'cli', 'server_profile', 'service_digest', 'service_manager', 'service_path')}
        self.receipt.write_text(json.dumps(state))
        config = self.client / 'orca-environments.json'
        config.write_text(json.dumps({'version': 1, 'environments': []}))
        plan = m.plan()
        server = next(s for s in plan['steps'] if s['id'] == 'server')
        # 적용은 소유 서비스를 다시 활성화한다. 계획이 '바꾸지 않음'으로 보이면 승인한 내용과 실행이 어긋난다
        self.assertEqual((server['risk'], server['argv']), ('config', m.orca_serve.activate_argv()[-1]))
        ran = len(self.service_commands)
        self.assertEqual(m.apply(plan['plan_id'])['status'], 'done')
        self.assertIn(server['argv'], self.service_commands[ran:])
        self.assertFalse(self.record().get('adopted', False))
        self.assertEqual(self.record()['service_digest'], record['service_digest'])
        out = m.uninstall(plan_id=m.uninstall(dry_run=True)['plan_id'])
        self.assertEqual(out['status'], 'done')
        self.assertFalse(Path(record['service_path']).exists())
        self.assertTrue(config.exists())

    def test_a_malformed_server_record_is_left_alone(self):
        """covers: ORCA-2, ORCA-11 — 형식을 모르는 서버 기록은 다른 도구의 기록처럼 두고, 계획은 예외 없이 그 단계를 빼고 세운다."""
        state = json.loads(self.receipt.read_text()) if self.receipt.exists() else {}
        state.setdefault('tool_installs', {})['orca_server'] = 'not-a-record'
        self.receipt.write_text(json.dumps(state))
        plan = m.plan()
        self.assertEqual([s['id'] for s in plan['steps'] if s['id'] in ('pairing', 'server')], [])
        self.assertEqual(json.loads(self.receipt.read_text())['tool_installs']['orca_server'], 'not-a-record')

    def test_a_loaded_but_inactive_linux_unit_is_enabled_again_on_resume(self):
        """covers: ORCA-2 — daemon-reload 뒤 enable 실패 시 loaded만 보고 활성화를 건너뛰지 않는다."""
        with mock.patch.object(m.orca_serve, 'run', return_value=(1, '', 'activation interrupted')):
            self.assertEqual(self.apply()['status'], 'failed')
        manager = {'kind': 'systemd', 'argv': [str(self.cli), 'serve', '--supervise'],
                   'env': {'ORCA_USER_DATA_PATH': str(self.server)}}
        wait = self.helpers.wait_for_remote_environment
        with mock.patch.object(m.orca_serve.orca_issue, 'manager', return_value=manager), \
                mock.patch.object(self.helpers, 'wait_for_remote_environment', side_effect=lambda *a: wait(*a, timeout=.1)):
            self.assertEqual(self.apply()['status'], 'done')
        self.assertTrue((self.home / 'activated').exists())

    def test_equivalent_server_key_encodings_reuse_the_existing_pairing(self):
        """covers: ORCA-2 — padded·URL-safe 표기가 달라도 같은 공개 키의 연결은 그대로 쓴다."""
        import base64
        self.assertEqual(self.apply()['status'], 'done')
        key = base64.b64encode(b'\xff' * 32).decode()
        (self.server / 'orca-e2ee-keypair.json').write_text(json.dumps({'publicKeyB64': key}))
        config = self.client / 'orca-environments.json'
        data = json.loads(config.read_text())
        data['environments'][0]['endpoints'][0]['publicKeyB64'] = key.replace('/', '_').rstrip('=')
        config.write_text(json.dumps(data))
        with mock.patch.object(self.helpers, 'runtime_is_active', return_value=True):
            plan = m.plan()
        self.assertEqual(plan['blocked'], [])
        self.assertEqual(plan['steps'], [])

    def test_a_surviving_temporary_server_is_not_adopted_on_retry(self):
        """covers: ORCA-1, ORCA-2 — 같은 형태의 기존 유닛도 살아 있는 임시 서버를 완료로 오인하지 않는다."""
        manager = {'kind': 'systemd', 'argv': [str(self.cli), 'serve', '--supervise'],
                   'env': {'ORCA_USER_DATA_PATH': str(self.server)}}
        processes = []
        issue, stop = self.helpers.issue_pairing_offer, self.helpers.stop_server_process
        def capture(*args, **kwargs):
            process, offer = issue(*args, **kwargs)
            processes.append(process)
            return process, offer
        try:
            with mock.patch.object(m.orca_serve.orca_issue, 'manager', return_value=manager), \
                    mock.patch.object(self.helpers, 'issue_pairing_offer', side_effect=capture), \
                    mock.patch.object(self.helpers, 'stop_server_process', return_value=None):
                self.assertEqual(self.apply()['status'], 'failed')
                self.assertIsNone(processes[0].poll())
                self.assertEqual(self.apply()['status'], 'failed')
                self.assertEqual(self.record()['state'], 'pairing')
                self.assertFalse(self.record().get('adopted', False))
                self.assertEqual(self.service_commands, [])
        finally:
            for process in processes:
                stop(process)

    def test_a_missing_owned_service_is_recreated_with_its_real_configuration(self):
        """covers: ORCA-2 — 소유 서비스 파일이 사라지면 승인한 재작성에 올바른 설정을 쓴다."""
        self.assertEqual(self.apply()['status'], 'done')
        unit = Path(self.record()['service_path'])
        unit.unlink()
        (self.home / 'activated').unlink()
        self.assertEqual(self.apply()['status'], 'done')
        self.assertIn(str(self.cli), unit.read_text())
        self.assertEqual(self.record()['state'], 'configured')
        self.assertFalse(self.record().get('adopted', False))

    def test_recreating_a_missing_adopted_service_records_our_new_ownership(self):
        """covers: ORCA-2, ORCA-11 — 받아들였던 파일이 없고 새로 작성하면 새 파일은 우리 소유다."""
        self.assertEqual(self.apply()['status'], 'done')
        state = json.loads(self.receipt.read_text())
        state['tool_installs'].pop('orca_server')
        self.receipt.write_text(json.dumps(state))
        self.assertEqual(self.apply()['status'], 'done')
        unit = Path(self.record()['service_path'])
        self.assertTrue(self.record()['adopted'])
        unit.unlink()
        (self.home / 'activated').unlink()
        self.assertEqual(self.apply()['status'], 'done')
        self.assertFalse(self.record().get('adopted', False))
        self.assertEqual(m.uninstall(plan_id=m.uninstall(dry_run=True)['plan_id'])['status'], 'done')
        self.assertFalse(unit.exists())

    def test_changed_tailnet_address_invalidates_a_service_only_plan(self):
        """covers: ORCA-13 — 이미 페어링된 서비스 재작성도 승인한 tailnet 주소에 묶인다."""
        self.assertEqual(self.apply()['status'], 'done')
        Path(self.record()['service_path']).unlink()
        (self.home / 'activated').unlink()
        plan = m.plan()
        with mock.patch.object(self.helpers, 'discover_pairing_address', return_value='100.64.0.6'):
            output = m.apply(plan['plan_id'])
        self.assertEqual(output['status'], 'stale')
        self.assertFalse(Path(m.orca_serve.service_path()).exists())


if __name__ == '__main__':
    unittest.main()
