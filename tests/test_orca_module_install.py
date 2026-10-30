"""Orca installer: canonical packages, isolated receipts and integrity before execution."""
import base64
import hashlib
import importlib.util
import io
import os
import json
import sys
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pytest_compat import collect, pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'jarvis' / 'modules' / 'orca' / 'orca.py'
spec = importlib.util.spec_from_file_location('orca_installer', WORKER)
orcasetup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orcasetup)


def configured_orca_server_setup(home):
    return {
        'state': 'configured',
        'server_profile': str(home / 'orca'),
        'client_profile': str(home / 'orca-desktop'),
        'desktop_environment_id': 'env-1',
        'pairing_codes_path': str(home / 'pairing-codes.txt'),
        'service_manager': 'task-scheduler',
        'service_root': str(home / 'service'),
        'service_path': str(home / 'service.xml'),
        'service_task': 'ai-setup Orca Server',
    }


@pytest.mark.parametrize('plat,arch,method,manifest,name', [
    ('linux','aarch64','deb','latest-linux-arm64.yml','orca-ide_1.4.237_arm64.deb'),
    ('linux','x86_64','deb','latest-linux.yml','orca-ide_1.4.237_amd64.deb'),
    ('darwin','arm64','app','latest-mac.yml','Orca-1.4.237-arm64-mac.zip'),
    ('win32','AMD64','user-setup','latest.yml','orca-windows-setup.exe'),
])
def test_official_package_selection(plat, arch, method, manifest, name):
    # covers: ORCA-1 (AI-18)
    plan = orcasetup.package_plan(plat, arch)
    assert plan['method'] == method
    assert plan['manifest'] == manifest
    assert orcasetup.asset_name(plan, '1.4.237') == name


def test_dry_run_never_contacts_network_or_creates_receipt(tmp_path, monkeypatch):
    # covers: ORCA-9 (AI-17)
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'existing_install', lambda p: False)
    monkeypatch.setattr(orcasetup, 'fetch_manifest', lambda p: pytest.fail('dry run requested network'))
    assert orcasetup.main(['--dry-run']) == 0
    assert not list(tmp_path.iterdir())


def test_existing_install_is_preserved_without_receipt(tmp_path, monkeypatch):
    # covers: ORCA-1 (AI-14)
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'existing_install', lambda p: True)
    monkeypatch.setattr(orcasetup, 'fetch_manifest', lambda p: pytest.fail('existing app requested download'))
    monkeypatch.setattr(orcasetup, 'configure_orca_host', lambda *a, **k: {'state': 'active-preserved'})
    assert orcasetup.main([]) == 1
    assert not list(tmp_path.iterdir())


def test_existing_install_configures_server_and_records_before_open(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:True)
    setup={
        'state':'configured','server_profile':str(tmp_path/'orca'),'client_profile':str(tmp_path/'orca-desktop'),
        'desktop_environment_id':'env-1','pairing_codes_path':str(tmp_path/'pairing-codes.txt'),
        'service_manager':'task-scheduler','service_root':str(tmp_path/'service'),
        'service_path':str(tmp_path/'service.xml'),'service_task':'ai-setup Orca Server',
    }
    monkeypatch.setattr(orcasetup,'configure_orca_host',lambda *a,**k:setup)
    def open_client(cli,record):
        saved=json.loads((tmp_path/'.claude/.claude-code-setup.json').read_text())
        assert saved['tool_installs']['orca_server']['desktop_environment_id']=='env-1'
        calls.append((cli,record))
    monkeypatch.setattr(orcasetup,'open_configured_orca_client',open_client)
    assert orcasetup.main(['--desktop-app'])==0
    receipt=json.loads((tmp_path/'.claude/.claude-code-setup.json').read_text())
    assert receipt['tool_installs']['orca_server']==setup
    assert calls and calls[0][1]['desktop_environment_id']=='env-1'


def test_default_setup_pairs_the_desktop_without_opening_the_gui(tmp_path, monkeypatch):
    """서버·데스크톱·모바일 페어링은 기본이다. GUI 실행과 바로가기 변경은 --desktop-app 일 때만 한다."""
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:True)
    monkeypatch.setattr(orcasetup,'configure_orca_host',lambda *a,**k:configured_orca_server_setup(tmp_path))
    monkeypatch.setattr(orcasetup,'open_configured_orca_client',lambda *a:pytest.fail('GUI opened without --desktop-app'))
    assert orcasetup.main([])==0
    receipt=json.loads((tmp_path/'.claude/.claude-code-setup.json').read_text())
    assert receipt['tool_installs']['orca_server']['desktop_environment_id']


def test_server_setup_failure_keeps_recoverable_ownership_receipt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'package_plan', lambda *a: {'method': 'app'})
    monkeypatch.setattr(orcasetup, 'existing_install', lambda p: True)
    monkeypatch.setattr(orcasetup, 'install_paths', lambda p: (tmp_path / 'Orca.app', tmp_path / 'orca.exe'))
    configuring = configured_orca_server_setup(tmp_path)
    configuring['state'] = 'configuring'

    def fail_setup(*args, on_record=None, **kwargs):
        assert on_record is not None
        on_record(configuring)
        raise RuntimeError('mobile pairing did not become ready')

    monkeypatch.setattr(orcasetup, 'configure_orca_host', fail_setup)
    assert orcasetup.main([]) == 1
    receipt = json.loads((tmp_path / '.claude/.claude-code-setup.json').read_text())
    assert receipt['tool_installs']['orca_server']['state'] == 'configuring'


def test_missing_tailscale_skips_only_the_server_and_succeeds(tmp_path, monkeypatch, capsys):
    """Tailscale 주소가 없으면 서버만 건너뛰고 경고한다. exit 0 이라 단계형 설치의 뒤 단계(가드 훅)가 계속된다."""
    sys.path.insert(0, str(WORKER.parent))
    import orca_server
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'package_plan', lambda *a: {'method': 'app'})
    monkeypatch.setattr(orcasetup, 'existing_install', lambda p: True)
    monkeypatch.setattr(orcasetup, 'install_paths', lambda p: (tmp_path / 'Orca.app', tmp_path / 'orca.exe'))
    def no_tailscale():
        raise RuntimeError('No Tailscale IPv4 address found.')
    monkeypatch.setattr(orca_server, 'discover_pairing_address', no_tailscale)
    monkeypatch.setattr(orca_server, 'setup_server_client', lambda *a, **k: pytest.fail('server configured without an address'))
    assert orcasetup.main([]) == 0
    assert 'Orca server not configured' in capsys.readouterr().err
    path = tmp_path / '.claude/.claude-code-setup.json'
    assert not path.exists() or 'orca_server' not in json.loads(path.read_text()).get('tool_installs', {})


def test_bad_hash_never_reaches_package_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(orcasetup, 'open_release', lambda url: io.BytesIO(b'corrupt'))
    meta = {'name':'p.deb','size':7,'sha512':base64.b64encode(hashlib.sha512(b'correct').digest()).decode()}
    with pytest.raises(RuntimeError, match='integrity'):
        orcasetup.download('1.4.237', meta, tmp_path)
    assert not list(tmp_path.iterdir())


def test_noninteractive_sudo_never_waits_for_a_password(tmp_path, monkeypatch):
    # covers: ORCA-1 (AI-35 을 jarvis 규칙으로 바꿈: 비대화형 sudo 는 sudo -n 으로만 시도한다)
    monkeypatch.setattr(orcasetup.sys, 'stdin', io.StringIO())
    calls = []
    monkeypatch.setattr(orcasetup.subprocess, 'run', lambda argv, **k: calls.append(argv))
    orcasetup.run_command(['sudo','apt','install','-y','--',str(tmp_path/'p.deb')], needs_sudo=True)
    assert calls == [['sudo','-n','apt','install','-y','--',str(tmp_path/'p.deb')]]


def test_no_server_installs_the_package_and_skills_without_configuring_a_server(tmp_path, monkeypatch):
    # covers: ORCA-1 — POSIX 서버는 jarvis 가 serve --supervise 형태로 따로 구성한다
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'existing_install', lambda plan: True)
    monkeypatch.setattr(orcasetup, 'configure_orca_host', lambda *a, **k: pytest.fail('server configured with --no-server'))
    assert orcasetup.main(['--no-server']) == 0


def test_uninstall_without_receipt_preserves_existing_install(tmp_path, monkeypatch):
    # covers: ORCA-11 (AI-34)
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setattr(orcasetup, 'run_command', lambda *a, **k: pytest.fail('unowned installation removed'))
    assert orcasetup.main(['--uninstall']) == 0
    assert not list(tmp_path.iterdir())




def test_success_records_package_and_skills_without_changing_other_receipt_keys(tmp_path, monkeypatch):
    # covers: ORCA-1 (AI-15, AI-37)
    manifest=tmp_path/'.claude/.claude-code-setup.json'
    manifest.parent.mkdir();manifest.write_text(json.dumps({'mods':{'user':'preserved'}}))
    cli=tmp_path/'orca-ide';cli.write_text('stub')
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:False)
    monkeypatch.setattr(orcasetup,'package_plan',lambda *a:{'method':'app','manifest':'latest-mac.yml'})
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(tmp_path/'Orca.app',cli))
    monkeypatch.setattr(orcasetup,'fetch_manifest',lambda p:('1.4.237',{}))
    monkeypatch.setattr(orcasetup,'download',lambda v,m,d:d/'verified.zip')
    monkeypatch.setattr(orcasetup,'install_package',lambda *a:None)
    monkeypatch.setattr(orcasetup,'installed_version',lambda p:'1.4.237')
    monkeypatch.setattr(orcasetup,'install_skills',lambda p,h,owned,checkpoint=None: owned['skills_added'].update({'orca-cli':'digest'}))
    monkeypatch.setattr(orcasetup,'configure_orca_host',lambda *a,**k:configured_orca_server_setup(tmp_path))
    monkeypatch.setattr(orcasetup,'open_configured_orca_client',lambda *a:None)
    assert orcasetup.main([])==0
    receipt=json.loads(manifest.read_text())
    assert receipt['mods']=={'user':'preserved'}
    assert receipt['tool_installs']['orca']=={'method':'app','version':'1.4.237','skills_added':{'orca-cli':'digest'},'skills_state':'done'}






def test_partial_skills_are_recorded_after_failed_install(tmp_path, monkeypatch):
    plan={'method':'app','manifest':'latest-mac.yml'}
    cli=tmp_path/'orca';cli.write_text('stub')
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:False)
    monkeypatch.setattr(orcasetup,'configure_orca_host',lambda *a,**k:configured_orca_server_setup(tmp_path))
    monkeypatch.setattr(orcasetup,'open_configured_orca_client',lambda *a:None)
    monkeypatch.setattr(orcasetup,'package_plan',lambda *a:plan)
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(tmp_path/'Orca.app',cli))
    monkeypatch.setattr(orcasetup,'fetch_manifest',lambda p:('1.4.237',{}))
    monkeypatch.setattr(orcasetup,'download',lambda v,m,d:d/'verified.zip')
    monkeypatch.setattr(orcasetup,'install_package',lambda *a:None)
    monkeypatch.setattr(orcasetup,'installed_version',lambda p:'1.4.237')
    monkeypatch.setattr(orcasetup,'available_skills',lambda cli:['orca-cli'])
    def command(*a,**k):
        skill=tmp_path/'.agents/skills/orca-cli';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('official')
        raise RuntimeError('skills interrupted')
    monkeypatch.setattr(orcasetup,'run_command',command)
    assert orcasetup.main([])==1
    receipt=json.loads((tmp_path/'.claude/.claude-code-setup.json').read_text())['tool_installs']['orca']
    assert receipt['skills_state']=='pending'
    assert 'orca-cli' in receipt['skills_added']
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:True)
    def complete(*a,**k):
        skill=tmp_path/'.agents/skills/orca-cli';skill.mkdir(parents=True,exist_ok=True);(skill/'SKILL.md').write_text('official')
        (tmp_path/'.agents/.skill-lock.json').write_text(json.dumps({'version':3,'skills':{'orca-cli':{'source':'official'}}}))
    monkeypatch.setattr(orcasetup,'run_command',complete)
    assert orcasetup.main([])==0
    receipt=json.loads((tmp_path/'.claude/.claude-code-setup.json').read_text())['tool_installs']['orca']
    assert receipt['skills_state']=='done'












def test_existing_skills_links_and_lock_entries_are_preserved(tmp_path, monkeypatch):
    shared=tmp_path/'.agents/skills';shared.mkdir(parents=True)
    (shared/'orca-cli').mkdir();(shared/'orca-cli/SKILL.md').write_text('user content')
    (shared/'orchestration').symlink_to(tmp_path/'missing')
    lock=tmp_path/'.agents/.skill-lock.json';lock.write_text(json.dumps({'version':3,'skills':{'computer-use':{'source':'user'}}}))
    monkeypatch.setattr(orcasetup,'available_skills',lambda cli:['orca-cli','orchestration','computer-use','orca-jira'])
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(tmp_path,tmp_path/'cli'))
    calls=[]
    def command(argv,**kw):
        calls.append([str(a) for a in argv])
        (shared/'orca-jira').mkdir();(shared/'orca-jira/SKILL.md').write_text('official')
        data=json.loads(lock.read_text());data['skills']['orca-jira']={'source':'official'};lock.write_text(json.dumps(data))
    monkeypatch.setattr(orcasetup,'run_command',command)
    owned={};orcasetup.install_skills({},tmp_path,owned)
    assert calls==[[str(tmp_path/'cli'),'skills','install','--agent','universal','--skill','orca-jira']]
    assert (shared/'orca-cli/SKILL.md').read_text()=='user content'
    assert (shared/'orchestration').is_symlink()
    assert set(owned['skills_added'])=={'orca-jira'}
    assert json.loads(lock.read_text())['skills']['computer-use']=={'source':'user'}


def test_native_skill_install_without_a_lock_file_is_owned_and_removable(tmp_path, monkeypatch):
    # Orca 1.4.239+ 는 npx skills 없이 SKILL.md 만 쓰고 .skill-lock.json 을 만들지 않는다.
    shared=tmp_path/'.agents/skills'
    monkeypatch.setattr(orcasetup,'available_skills',lambda cli:['orca-cli','orchestration'])
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(tmp_path,tmp_path/'cli'))
    def command(argv,**kw):
        for name in ('orca-cli','orchestration'):
            (shared/name).mkdir(parents=True);(shared/name/'SKILL.md').write_text('official '+name)
    monkeypatch.setattr(orcasetup,'run_command',command)
    owned={};orcasetup.install_skills({},tmp_path,owned)
    assert set(owned['skills_added'])=={'orca-cli','orchestration'}
    assert 'skills_attempting' not in owned and not owned.get('skills_lock')
    assert not (tmp_path/'.agents/.skill-lock.json').exists()
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    owned.update(method='app',package_removed=True,skills_state='complete')
    receipt={'tool_installs':{'orca':owned}};path=tmp_path/'.claude/.claude-code-setup.json'
    assert orcasetup.uninstall({'method':'app'},receipt,path,False)==0
    assert not (shared/'orca-cli').exists() and not (shared/'orchestration').exists()
    assert not (tmp_path/'.agents/.skill-lock.json').exists()


def test_malformed_skill_lock_blocks_install_without_overwrite(tmp_path, monkeypatch):
    lock=tmp_path/'.agents/.skill-lock.json';lock.parent.mkdir();lock.write_text('{broken')
    monkeypatch.setattr(orcasetup,'run_command',lambda *a,**k:pytest.fail('invalid lock was overwritten'))
    with pytest.raises((RuntimeError,ValueError)):
        orcasetup.install_skills({},tmp_path,{})
    assert lock.read_text()=='{broken'


def test_failed_package_stage_is_not_reported_as_existing_success(tmp_path, monkeypatch):
    plan={'method':'app','manifest':'latest-mac.yml'};root=tmp_path/'Orca.app';cli=root/'cli'
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.setattr(orcasetup,'existing_install',lambda p:root.exists())
    monkeypatch.setattr(orcasetup,'package_plan',lambda *a:plan)
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(root,cli))
    monkeypatch.setattr(orcasetup,'fetch_manifest',lambda p:('1.4.237',{}))
    monkeypatch.setattr(orcasetup,'download',lambda v,m,d:d/'verified.zip')
    def install(*a):root.mkdir();raise RuntimeError('package interrupted')
    monkeypatch.setattr(orcasetup,'install_package',install)
    assert orcasetup.main([])==1
    assert orcasetup.main([])==1


def test_dangling_package_link_is_preserved(tmp_path,monkeypatch):
    root=tmp_path/'Orca.app';root.symlink_to(tmp_path/'disconnected-volume')
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(root,root/'cli'))
    assert orcasetup.existing_install({'method':'app'}) is True


def test_uninstall_removes_incomplete_pairing_receipt_when_runtime_is_stopped(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setitem(sys.modules, 'orca_server', SimpleNamespace(
        profile_paths=lambda *args: {'server': tmp_path / 'server', 'client': tmp_path / 'client'},
        runtime_is_active=lambda profile: False,
        find_new_environment_id=lambda *args: None,
    ))
    service_root = tmp_path / 'service'
    receipt = {'tool_installs': {'orca_server': {
        'state': 'pairing', 'cli': 'orca.exe', 'service_root': str(service_root),
        'desktop_environment_name': 'host Orca server', 'client_environment_ids_before_pairing': [],
    }}}
    path = tmp_path / '.claude/.claude-code-setup.json'
    path.parent.mkdir()

    assert orcasetup.uninstall({'method': 'app'}, receipt, path, False) == 0
    assert 'orca_server' not in receipt['tool_installs']
    assert json.loads(path.read_text())['tool_installs'] == {}


def test_uninstall_preserves_incomplete_pairing_receipt_while_runtime_is_active(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setitem(sys.modules, 'orca_server', SimpleNamespace(
        profile_paths=lambda *args: {'server': tmp_path / 'server', 'client': tmp_path / 'client'},
        runtime_is_active=lambda profile: True,
        find_new_environment_id=lambda *args: None,
    ))
    receipt = {'tool_installs': {'orca_server': {
        'state': 'pairing', 'cli': 'orca.exe', 'service_root': str(tmp_path / 'service'),
        'desktop_environment_name': 'host Orca server', 'client_environment_ids_before_pairing': [],
    }}}
    path = tmp_path / '.claude/.claude-code-setup.json'
    path.parent.mkdir()

    with pytest.raises(RuntimeError, match='pairing runtime is still active'):
        orcasetup.uninstall({'method': 'app'}, receipt, path, False)
    assert receipt['tool_installs']['orca_server']['state'] == 'pairing'
    assert not path.exists()


def test_uninstall_preserves_pairing_receipt_when_new_desktop_environment_may_exist(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.setitem(sys.modules, 'orca_server', SimpleNamespace(
        profile_paths=lambda *args: {'server': tmp_path / 'server', 'client': tmp_path / 'client'},
        runtime_is_active=lambda profile: False,
        find_new_environment_id=lambda *args: 'env-new',
    ))
    record = {
        'state': 'pairing', 'cli': 'orca.exe', 'service_root': str(tmp_path / 'service'),
        'desktop_environment_name': 'host Orca server', 'client_environment_ids_before_pairing': [],
    }
    receipt = {'tool_installs': {'orca_server': record}}
    path = tmp_path / '.claude/.claude-code-setup.json'
    path.parent.mkdir()

    with pytest.raises(RuntimeError, match='desktop environment may have been created'):
        orcasetup.uninstall({'method': 'app'}, receipt, path, False)
    assert receipt['tool_installs']['orca_server'] == record
    assert not path.exists()


def test_uninstall_cleans_only_owned_skill_link_and_lock_entry(tmp_path,monkeypatch):
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.delenv('XDG_STATE_HOME',raising=False);monkeypatch.delenv('XDG_CONFIG_HOME',raising=False)
    shared=tmp_path/'.agents/skills';shared.mkdir(parents=True)
    skill=shared/'orca-cli';skill.mkdir();(skill/'SKILL.md').write_text('official')
    user=shared/'user-skill';user.mkdir();(user/'SKILL.md').write_text('user')
    link=tmp_path/'.config/agents/skills/orca-cli';link.parent.mkdir(parents=True);link.symlink_to(skill)
    lock=tmp_path/'.agents/.skill-lock.json';entry={'source':'stablyai/orca'}
    lock.write_text(json.dumps({'version':3,'skills':{'orca-cli':entry,'user-skill':{'source':'user'}}}))
    root=tmp_path/'Orca.app';root.mkdir();(root/'app').write_text('package')
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(root,root/'cli'))
    receipt={'tool_installs':{'orca':{'method':'app','skills_added':{'orca-cli':hashlib.sha256(b'SKILL.mdofficial').hexdigest()},
        'skills_links':{'orca-cli':{'link':os.readlink(str(link))}},'skills_lock':{'orca-cli':entry},'skills_lock_path':str(lock)}}}
    path=tmp_path/'.claude/.claude-code-setup.json';path.parent.mkdir()
    assert orcasetup.uninstall({'method':'app'},receipt,path,False)==0
    assert not root.exists() and not skill.exists() and not link.is_symlink()
    assert (user/'SKILL.md').read_text()=='user'
    assert json.loads(lock.read_text())['skills']=={'user-skill':{'source':'user'}}


def test_changed_skill_retry_does_not_reinvoke_removed_windows_uninstaller(tmp_path,monkeypatch):
    monkeypatch.setattr(orcasetup,'home_dir',lambda:tmp_path)
    monkeypatch.delenv('XDG_STATE_HOME',raising=False);monkeypatch.delenv('XDG_CONFIG_HOME',raising=False)
    skill=tmp_path/'.agents/skills/orca-cli';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('edited')
    root=tmp_path/'Orca';root.mkdir()
    monkeypatch.setattr(orcasetup,'install_paths',lambda p:(root,root/'cli'))
    calls=[]
    def remove(argv,**kw):calls.append(argv);root.rmdir()
    monkeypatch.setattr(orcasetup,'run_command',remove)
    receipt={'tool_installs':{'orca':{'method':'user-setup','skills_added':{'orca-cli':hashlib.sha256(b'SKILL.mdofficial').hexdigest()}}}}
    path=tmp_path/'.claude/.claude-code-setup.json';path.parent.mkdir()
    assert orcasetup.uninstall({'method':'user-setup'},receipt,path,False)==1
    (skill/'SKILL.md').write_text('official')
    assert orcasetup.uninstall({'method':'user-setup'},receipt,path,False)==0
    assert len(calls)==1


@pytest.mark.parametrize('artifact', ['skill', 'link', 'lock'])
def test_uninstall_keeps_unverified_crash_checkpoint_before_package_removal(tmp_path, monkeypatch, artifact):
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.delenv('XDG_STATE_HOME', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    root = tmp_path / 'Orca.app'; root.mkdir()
    monkeypatch.setattr(orcasetup, 'install_paths', lambda p: (root, root / 'cli'))
    shared, universal, lock = orcasetup.skill_locations(tmp_path)
    if artifact == 'skill':
        target = shared / 'orca-cli'; target.mkdir(parents=True)
        (target / 'SKILL.md').write_text('unverified')
    elif artifact == 'link':
        universal.mkdir(parents=True)
        (universal / 'orca-cli').symlink_to(shared / 'orca-cli')
    else:
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({'version': 3, 'skills': {'orca-cli': {'source': 'unknown'}}}))
    owned = {'method': 'app', 'skills_state': 'pending', 'skills_attempting': ['orca-cli'], 'skills_added': {}}
    receipt = {'tool_installs': {'orca': owned}}
    path = tmp_path / '.claude/.claude-code-setup.json'
    orcasetup.save_receipt(path, receipt)
    with pytest.raises(RuntimeError, match='Preserving changed'):
        orcasetup.uninstall({'method': 'app'}, receipt, path, False)
    assert root.exists()
    assert json.loads(path.read_text())['tool_installs']['orca'] == owned


def test_uninstall_cleans_recorded_lock_when_partial_skill_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(orcasetup, 'home_dir', lambda: tmp_path)
    monkeypatch.delenv('XDG_STATE_HOME', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    _, _, lock = orcasetup.skill_locations(tmp_path)
    lock.parent.mkdir(parents=True)
    entry = {'source': 'official'}
    lock.write_text(json.dumps({'version': 3, 'skills': {'orca-cli': entry, 'other': {'source': 'user'}}}))
    owned = {'method': 'app', 'package_removed': True, 'skills_state': 'pending',
             'skills_attempting': ['orca-cli'], 'skills_added': {}, 'skills_lock': {'orca-cli': entry}}
    receipt = {'tool_installs': {'orca': owned}}
    path = tmp_path / '.claude/.claude-code-setup.json'
    assert orcasetup.uninstall({'method': 'app'}, receipt, path, False) == 0
    assert json.loads(lock.read_text())['skills'] == {'other': {'source': 'user'}}


@pytest.mark.parametrize('interruption', ['before_offer', 'after_desktop_added'])
def test_real_host_setup_persists_partial_pairing_checkpoints(tmp_path, monkeypatch, interruption):
    # covers: ORCA-1 (AI-48)
    monkeypatch.syspath_prepend(str(ROOT / 'jarvis' / 'modules' / 'orca'))
    import orca_server

    monkeypatch.setenv('APPDATA', str(tmp_path / 'Roaming'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local'))
    active = {'value': False}
    monkeypatch.setattr(orca_server, 'runtime_is_active', lambda profile: active['value'])
    monkeypatch.setattr(orca_server, 'list_remote_environments', lambda *args: [{'id': 'env-old', 'name': 'old'}])

    def issue_offer(*args):
        if interruption == 'before_offer':
            raise RuntimeError('simulated interruption')
        active['value'] = True
        return object(), {'url': 'orca://pair?code=test-only'}

    monkeypatch.setattr(orca_server, 'issue_pairing_offer', issue_offer)
    monkeypatch.setattr(orca_server, 'add_remote_environment', lambda *args: 'env-new')
    monkeypatch.setattr(orca_server, 'stop_server_process', lambda process: None)
    path = tmp_path / '.claude' / '.claude-code-setup.json'
    receipt = {'unrelated': {'preserved': True}}
    with pytest.raises(RuntimeError, match='simulated interruption|Temporary Orca pairing server'):
        orcasetup.configure_orca_host(
            {'method': 'user-setup'}, tmp_path, 'orca.exe', '100.64.1.20',
            on_record=lambda record: orcasetup.save_orca_server_record(receipt, path, record),
        )
    saved = json.loads(path.read_text())
    checkpoint = saved['tool_installs']['orca_server']
    assert saved['unrelated'] == {'preserved': True}
    assert checkpoint['state'] == 'pairing'
    assert checkpoint['desktop_environment_name']
    assert checkpoint['client_environment_ids_before_pairing'] == ['env-old']
    assert checkpoint['cli'] == 'orca.exe'
    assert checkpoint.get('desktop_environment_id') == (None if interruption == 'before_offer' else 'env-new')
    assert 'orca://pair?' not in path.read_text()







load_tests = collect(globals())
