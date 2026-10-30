"""Install one official Orca package per platform; preserve existing installs and user state.

Jarvis 와 같은 설치 경로·stable 매니페스트·SHA-512 검증을 쓴다.
설치는 새 설치에만 적용한다. 이후 갱신은 Jarvis 또는 공식 앱 업데이터가 맡는다.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

RELEASE = 'https://github.com/stablyai/orca/releases'
HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


def home_dir():
    return Path.home()


def package_plan(plat, arch):
    if plat.startswith('linux') and arch in ('aarch64', 'arm64', 'x86_64', 'AMD64'):
        arch = 'arm64' if arch in ('aarch64', 'arm64') else 'amd64'
        return {'method': 'deb', 'arch': arch, 'manifest': 'latest-linux-arm64.yml' if arch == 'arm64' else 'latest-linux.yml'}
    if plat == 'darwin' and arch in ('arm64', 'aarch64', 'x86_64'):
        return {'method': 'app', 'arch': 'arm64' if arch != 'x86_64' else 'x64', 'manifest': 'latest-mac.yml'}
    if plat == 'win32' and arch in ('AMD64', 'x86_64'):
        return {'method': 'user-setup', 'arch': 'x64', 'manifest': 'latest.yml'}
    raise RuntimeError('Unsupported Orca platform or architecture: {} {}'.format(plat, arch))


def install_paths(plan):
    if plan['method'] == 'deb':
        root = Path('/opt/Orca')
        return root, root / 'resources/bin/orca-ide'
    if plan['method'] == 'app':
        root = Path('/Applications/Orca.app')
        return root, root / 'Contents/Resources/bin/orca'
    root = Path(os.environ.get('LOCALAPPDATA') or home_dir() / 'AppData/Local') / 'Programs/orca'
    return root, root / 'resources/bin/orca.exe'


def existing_install(plan):
    root, cli = install_paths(plan)
    # Linux 의 /usr/bin/orca 는 GNOME 화면 읽기 도구일 수 있다. PATH 에서 bare orca 를 실행하지 않는다.
    candidates = [root, cli]
    if plan['method'] == 'deb':
        candidates += [home_dir() / '.local/opt/orca/opt/Orca']
    elif plan['method'] == 'app':
        candidates += [home_dir() / 'Applications/Orca.app']
    elif os.environ.get('ProgramFiles'):
        candidates += [Path(os.environ['ProgramFiles']) / 'Orca']
    return any(os.path.lexists(str(p)) for p in candidates)


def asset_name(plan, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise RuntimeError('Invalid Orca release version')
    if plan['method'] == 'deb':
        return 'orca-ide_{}_{}.deb'.format(version, plan['arch'])
    if plan['method'] == 'app':
        return 'Orca-{}-{}mac.zip'.format(version, 'arm64-' if plan['arch'] == 'arm64' else '')
    return 'orca-windows-setup.exe'


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != 'https' or url.hostname not in HOSTS:
            raise RuntimeError('Unexpected Orca release redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_release(url):
    return urllib.request.build_opener(ReleaseRedirect).open(
        urllib.request.Request(url, headers={'User-Agent': 'ai-setup-orca'}), timeout=90)


def fetch_manifest(plan):
    with open_release(RELEASE + '/latest/download/' + plan['manifest']) as response:
        text = response.read(1 << 20).decode('utf-8')
    match = re.search(r'^version:\s*[\'\"]?(\d+\.\d+\.\d+)[\'\"]?\s*$', text, re.M)
    if not match:
        raise RuntimeError('Invalid Orca release manifest')
    version, entries, entry = match.group(1), [], None
    for line in text.splitlines():
        if line.startswith('  - url:'):
            entry = {'name': line.split(':', 1)[1].strip()}
            entries.append(entry)
        elif entry and line.startswith('    sha512:'):
            entry['sha512'] = line.split(':', 1)[1].strip()
        elif entry and line.startswith('    size:'):
            entry['size'] = int(line.split(':', 1)[1].strip())
    name = asset_name(plan, version)
    meta = next((e for e in entries if e['name'] == name and e.get('sha512') and e.get('size', 0) > 0), None)
    if not meta:
        raise RuntimeError('Official manifest has no package for this platform')
    return version, meta


def download(version, meta, scratch):
    target = scratch / meta['name']
    total, digest = 0, hashlib.sha512()
    try:
        with open_release(RELEASE + '/download/v' + version + '/' + meta['name']) as response, target.open('wb') as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > meta['size']:
                    raise RuntimeError('Orca package integrity check failed: oversized file')
                digest.update(chunk)
                out.write(chunk)
        actual = base64.b64encode(digest.digest()).decode('ascii')
        if total != meta['size'] or not hmac.compare_digest(actual, meta['sha512']):
            raise RuntimeError('Orca package integrity check failed')
        return target
    except BaseException:
        if target.exists():
            target.unlink()
        raise


def run_command(argv, needs_sudo=False):
    if needs_sudo and not sys.stdin.isatty() and argv[:1] == ['sudo']:
        # 비대화형(jarvis 서비스)은 sudo -n 으로만 시도한다. 비밀번호가 필요하면 기다리지 않고 바로 실패한다.
        argv = ['sudo', '-n'] + list(argv[1:])
    print('Run: ' + subprocess.list2cmdline([str(a) for a in argv]), flush=True)
    subprocess.run([str(a) for a in argv], check=True, timeout=900)


def installed_version(plan):
    root, _ = install_paths(plan)
    if plan['method'] == 'app':
        with (root / 'Contents/Info.plist').open('rb') as f:
            return plistlib.load(f)['CFBundleShortVersionString']
    with (root / 'resources/app.asar').open('rb') as f:
        _, header_size, _, json_size = struct.unpack('<4I', f.read(16))
        if not 0 < json_size < 32 << 20:
            raise RuntimeError('Invalid Orca ASAR header')
        entry = json.loads(f.read(json_size))['files']['package.json']
        if not 0 < entry['size'] < 1 << 20:
            raise RuntimeError('Invalid Orca package metadata')
        f.seek(8 + header_size + int(entry['offset']))
        return json.loads(f.read(entry['size']))['version']


def install_package(plan, path, scratch):
    root, _ = install_paths(plan)
    if os.path.lexists(str(root)):
        raise RuntimeError('An Orca installation appeared; preserving it')
    if plan['method'] == 'deb':
        # Orca 의 deb 는 Electron 이 여는 libasound.so.2 를 Depends 에 적지 않는다. 데스크톱이 아닌
        # 머신에서는 그 때문에 orca-ide 가 127 로 죽으므로 같은 apt 호출에서 함께 깐다.
        # 제거는 orca-ide 만 지운다. libasound2 는 다른 패키지도 쓰는 공용 라이브러리다.
        run_command(['sudo', 'apt', 'install', '-y', '--', path, 'libasound2'], needs_sudo=True)
    elif plan['method'] == 'app':
        unpack = scratch / 'unpacked'
        run_command(['/usr/bin/ditto', '-x', '-k', path, unpack])
        app = unpack / 'Orca.app'
        run_command(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
        run_command(['/usr/sbin/spctl', '--assess', '--type', 'execute', app])
        with tempfile.TemporaryDirectory(prefix='.ai-setup-orca-', dir=str(root.parent)) as staging:
            staged = Path(staging) / 'Orca.app'
            run_command(['/usr/bin/ditto', app, staged])
            run_command(['/usr/bin/codesign', '--verify', '--deep', '--strict', staged])
            if os.path.lexists(str(root)):
                raise RuntimeError('An Orca installation appeared; preserving it')
            os.rename(str(staged), str(root))
    else:
        run_command([path, '/S', '/CURRENTUSER'])


def read_receipt(path):
    if not path.exists():
        return {}
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict) or not isinstance(receipt.get('tool_installs', {}), dict) or not isinstance(receipt.get('tool_attempts', {}), dict):
        raise RuntimeError('Invalid setup receipt; preserving it')
    return receipt


def save_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix='.orca-receipt-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as f:
            json.dump(receipt, f, indent=2)
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def skills_digest(path):
    if path.is_symlink() or not path.is_dir():
        return None
    digest = hashlib.sha256()
    for p in sorted(path.rglob('*')):
        if p.is_symlink():
            return None
        if p.is_file():
            digest.update(str(p.relative_to(path)).encode('utf-8'))
            digest.update(p.read_bytes())
    return digest.hexdigest()


def skill_locations(home):
    config_home = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    state_home = os.environ.get('XDG_STATE_HOME')
    lock = Path(state_home) / 'skills/.skill-lock.json' if state_home else home / '.agents/.skill-lock.json'
    return home / '.agents/skills', config_home / 'agents/skills', lock


def check_owned_path(path, home):
    try:
        relative = path.relative_to(home)
    except ValueError:
        raise RuntimeError('Setup path is outside the current home: ' + str(path))
    if os.path.normcase(str(home.resolve() / relative)) != os.path.normcase(str(path.resolve())):
        raise RuntimeError('Preserving linked setup path: ' + str(path))


def read_skill_lock(path):
    if not path.exists():
        return {'version': 3, 'skills': {}}
    lock = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(lock, dict) or type(lock.get('version')) is not int or lock['version'] < 3 or not isinstance(lock.get('skills'), dict):
        raise RuntimeError('Invalid or old skills lock; preserving it')
    return lock


def available_skills(cli):
    p = subprocess.run([str(cli), 'skills', 'install', '--json'], capture_output=True, text=True, check=True, timeout=30)
    names = json.loads(p.stdout).get('availableSkills')
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[a-z0-9-]+', n) for n in names):
        raise RuntimeError('Invalid bundled Orca skill registry')
    return names


def validate_pending_skills(owned, shared, universal, lock):
    attempted = set(owned.get('skills_attempting') or owned.get('skills_added') or [])
    if not attempted:
        return attempted
    for name in attempted:
        if not re.fullmatch(r'[a-z0-9-]+', name):
            raise RuntimeError('Invalid pending skill name')
        changed = changed_skill_part(name, owned, shared, universal, lock)
        if changed:
            raise RuntimeError({'skill': 'Preserving changed or unverified pending skill: ',
                                'link': 'Preserving changed pending skill link: ',
                                'lock': 'Preserving changed pending skill lock: '}[changed] + name)
    return attempted


def changed_skill_part(name, owned, shared, universal, lock):
    """영수증에 기록한 뒤 사용자가 바꾼 부분: 'skill'·'link'·'lock', 그대로면 ''. 없는 경로는 바뀐 것으로 보지 않는다."""
    path = shared / name
    if os.path.lexists(str(path)) and (name not in owned.get('skills_added', {}) or not owned['skills_added'][name] or skills_digest(path) != owned['skills_added'][name]):
        return 'skill'
    link = universal / name
    if link != path and os.path.lexists(str(link)):
        record = owned.get('skills_links', {}).get(name) or {}
        if not ((link.is_symlink() and record.get('link') == os.readlink(str(link))) or
                (not link.is_symlink() and record.get('digest') and skills_digest(link) == record['digest'])):
            return 'link'
    if name in lock['skills'] and lock['skills'][name] != owned.get('skills_lock', {}).get(name):
        return 'lock'
    return ''


def retry_owned_skills(home, owned, shared, universal, lock_path, lock):
    attempted = validate_pending_skills(owned, shared, universal, lock)
    changed = False
    for name in attempted:
        path, link = shared / name, universal / name
        if link != path and os.path.lexists(str(link)):
            if link.is_symlink():
                link.unlink()
            else:
                shutil.rmtree(str(link))
        if os.path.lexists(str(path)):
            shutil.rmtree(str(path))
        if name in lock['skills']:
            del lock['skills'][name]
            changed = True
        for key in ('skills_added', 'skills_links', 'skills_lock'):
            owned.get(key, {}).pop(name, None)
    if changed:
        save_receipt(lock_path, lock)


def install_skills(plan, home, owned, checkpoint=None):
    shared, universal, lock_path = skill_locations(home)
    for destination in (shared, universal, lock_path):
        check_owned_path(destination, home)
    lock = read_skill_lock(lock_path)
    retry_owned_skills(home, owned, shared, universal, lock_path, lock)
    _, cli = install_paths(plan)
    names = [n for n in available_skills(cli) if n not in lock['skills'] and
             not os.path.lexists(str(shared / n)) and not os.path.lexists(str(universal / n))]
    if not names:
        print('Existing Orca skills preserved; no missing bundled skills.')
        return
    argv = [cli, 'skills', 'install', '--agent', 'universal']
    for name in names:
        argv += ['--skill', name]
    owned['skills_attempting'] = names
    if checkpoint:
        checkpoint()
    try:
        run_command(argv)
        # Orca 1.4.239+ 는 lock 없이 직접 복사하므로 lock 항목은 판정에 쓰지 않는다. 있으면 아래에서 기록한다.
        if any(not (shared / n / 'SKILL.md').is_file() for n in names):
            raise RuntimeError('Bundled skill installation is incomplete')
        owned.pop('skills_attempting', None)
    finally:
        # CLI 의 부분 성공도 기록한다. 다른 스킬·에이전트 경로의 소유권은 가져오지 않는다.
        record_skills(names, owned, shared, universal, lock_path)


def record_skills(names, owned, shared, universal, lock_path):
    current_lock = read_skill_lock(lock_path)
    owned['skills_lock_path'] = str(lock_path)
    for name in names:
        target = shared / name
        if os.path.lexists(str(target)):
            owned.setdefault('skills_added', {})[name] = skills_digest(target)
        link = universal / name
        if link != target and os.path.lexists(str(link)):
            owned.setdefault('skills_links', {})[name] = ({'link': os.readlink(str(link))} if link.is_symlink() else {'digest': skills_digest(link)})
        if name in current_lock['skills']:
            owned.setdefault('skills_lock', {})[name] = current_lock['skills'][name]


def finish_skills(plan, home, owned, receipt, path):
    try:
        install_skills(plan, home, owned, lambda: save_receipt(path, receipt))
        owned['skills_state'] = 'done'
    finally:
        save_receipt(path, receipt)


def uninstall(plan, receipt, path, dry_run):
    server_owned = receipt.get('tool_installs', {}).get('orca_server')
    if server_owned:
        if not isinstance(server_owned, dict) or server_owned.get('state') not in ('configured', 'configuring', 'pairing'):
            raise RuntimeError('Invalid Orca server ownership receipt; preserving it')
        if server_owned.get('state') == 'pairing' and not (Path(server_owned.get('service_root', '')) / '.ai-setup-managed').is_file():
            if not dry_run:
                tool_dir = str(Path(__file__).resolve().parent)
                if tool_dir not in sys.path:
                    sys.path.insert(0, tool_dir)
                import orca_server
                profiles = orca_server.profile_paths(sys.platform, home_dir())
                if orca_server.runtime_is_active(profiles['server']):
                    raise RuntimeError('Orca pairing runtime is still active; preserving its partial setup receipt')
                if 'client_environment_ids_before_pairing' not in server_owned:
                    raise RuntimeError('Orca pairing checkpoint is incomplete; preserving its receipt for recovery')
                recovered_id = orca_server.find_new_environment_id(
                    server_owned.get('cli') or install_paths(plan)[1], profiles['client'],
                    server_owned.get('desktop_environment_name', ''),
                    server_owned['client_environment_ids_before_pairing'],
                )
                if recovered_id:
                    raise RuntimeError('A desktop environment may have been created; preserving its pairing receipt')
                del receipt['tool_installs']['orca_server']
                save_receipt(path, receipt)
            else:
                print('Remove incomplete Orca pairing setup receipt')
            server_owned = None
    if server_owned:
        print('Remove managed Orca server service: ' + str(server_owned.get('service_path', '')))
        if not dry_run:
            tool_dir = str(Path(__file__).resolve().parent)
            if tool_dir not in sys.path:
                sys.path.insert(0, tool_dir)
            import orca_server
            profiles = orca_server.profile_paths(sys.platform, home_dir())
            expected_plan = orca_server.service_plan(sys.platform, home_dir())
            orca_server.validate_service_record(
                server_owned, expected_plan, profiles["server"]
            )
            cleanup_record = dict(server_owned)
            cleanup_record.setdefault('cli', str(install_paths(plan)[1]))
            orca_server.remove_user_service(cleanup_record)
            del receipt['tool_installs']['orca_server']
            save_receipt(path, receipt)
    owned = receipt.get('tool_installs', {}).get('orca')
    if not owned:
        print('Orca has no setup receipt; preserving existing installations.')
        return 0
    if not isinstance(owned, dict) or owned.get('method') != plan['method']:
        raise RuntimeError('Receipt belongs to another installation method; preserving it')
    shared, universal, lock_path = skill_locations(home_dir())
    for destination in (shared, universal, lock_path):
        check_owned_path(destination, home_dir())
    lock = read_skill_lock(lock_path)
    original_lock = json.dumps(lock, sort_keys=True)
    if owned.get('skills_lock_path') and owned['skills_lock_path'] != str(lock_path):
        raise RuntimeError('Skills lock location changed; preserving the receipt')
    for key in ('skills_added', 'skills_links', 'skills_lock'):
        if not isinstance(owned.get(key, {}), dict) or any(not re.fullmatch(r'[a-z0-9-]+', n) for n in owned.get(key, {})):
            raise RuntimeError('Invalid skill ownership receipt')
    if owned.get('skills_attempting'):
        validate_pending_skills(owned, shared, universal, lock)
    root, _ = install_paths(plan)
    print('Remove managed Orca package: ' + str(root))
    if dry_run:
        return 0
    if not owned.get('package_removed'):
        if root.is_symlink():
            raise RuntimeError('Orca package is now linked; preserving it')
        if plan['method'] == 'deb':
            run_command(['sudo', 'apt', 'remove', '-y', '--', 'orca-ide'], needs_sudo=True)
        elif plan['method'] == 'app':
            if root.exists():
                shutil.rmtree(str(root))
        elif root.exists():
            run_command([root / 'Uninstall Orca.exe', '/S', '/CURRENTUSER'])
        owned['package_removed'] = True
        save_receipt(path, receipt)
    remaining, remaining_links = {}, {}
    for name, record in owned.get('skills_links', {}).items():
        link = universal / name
        if not os.path.lexists(str(link)):
            continue
        if link.is_symlink() and record.get('link') == os.readlink(str(link)):
            link.unlink()
        elif not link.is_symlink() and record.get('digest') and skills_digest(link) == record['digest']:
            shutil.rmtree(str(link))
        else:
            print('Preserving changed skill link: ' + name)
            remaining_links[name] = record
    for name, digest in owned.get('skills_added', {}).items():
        skill = shared / name
        if os.path.lexists(str(skill)):
            if name not in remaining_links and digest and skills_digest(skill) == digest:
                shutil.rmtree(str(skill))
            else:
                print('Preserving changed skill: ' + name)
                remaining[name] = digest
                continue
    for name, record in owned.get('skills_lock', {}).items():
        if name not in remaining and name not in remaining_links and lock['skills'].get(name) == record:
            del lock['skills'][name]
    if json.dumps(lock, sort_keys=True) != original_lock:
        save_receipt(lock_path, lock)
    if remaining or remaining_links:
        owned['skills_added'], owned['skills_links'] = remaining, remaining_links
    else:
        del receipt['tool_installs']['orca']
    save_receipt(path, receipt)
    return 1 if remaining or remaining_links else 0


def configure_orca_host(plan, home, cli, pairing_address=None, dry_run=False, existing_record=None, on_record=None):
    if dry_run:
        print('Orca: configure persistent server + paired desktop client; emit mobile/runtime pairing links.')
        return {"state": "planned"}
    tool_dir = str(Path(__file__).resolve().parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import orca_server

    try:
        address = pairing_address or orca_server.discover_pairing_address()
    except RuntimeError as error:
        # 서버만 건너뛴다. 실패로 끝내면 단계형 설치가 여기서 멈춰 뒤 단계(가드 훅)가 깔리지 않는다.
        print('Orca server not configured: ' + str(error), file=sys.stderr)
        return {"state": "skipped"}
    result = orca_server.setup_server_client(
        cli, home, address, sys.executable, Path(tool_dir) / "orca_server.py",
        plat=sys.platform, existing_record=existing_record, on_record=on_record,
    )
    if result.get("state") != "configured":
        raise RuntimeError("Orca server and desktop client setup did not complete")
    print('Orca server and desktop client configured; pairing links are in: ' + result["pairing_codes_path"])
    return result


def save_orca_server_record(receipt, path, setup):
    keys = ('state', 'cli', 'server_profile', 'client_profile', 'desktop_environment_id', 'pairing_codes_path', 'service_manager', 'service_root', 'service_path', 'service_task', 'desktop_environment_name', 'client_environment_ids_before_pairing', 'shortcut_receipt_path')
    receipt.setdefault('tool_installs', {})['orca_server'] = {
        key: setup[key] for key in keys if key in setup
    }
    save_receipt(path, receipt)


def open_configured_orca_client(cli, setup):
    tool_dir = str(Path(__file__).resolve().parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import orca_server
    orca_server.open_desktop_client(
        cli, setup["client_profile"], setup["desktop_environment_id"]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install the official Orca package and its bundled skills')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--pairing-address')
    # 데스크톱 GUI 실행과 Windows 바로가기 변경은 선택이다. 서버·데스크톱·모바일 페어링은 항상 한다.
    parser.add_argument('--desktop-app', action='store_true')
    # Linux·macOS 서버 서비스는 jarvis(orca_serve)가 serve --supervise 형태로 따로 구성한다. 패키지·스킬만 한다.
    parser.add_argument('--no-server', action='store_true')
    # (jarvis) 승인한 계획의 목표 버전. 지금 매니페스트가 다르면 내려받지 않는다.
    parser.add_argument('--expect-version')
    args = parser.parse_args(argv)
    if args.dry_run and args.desktop_app and not args.uninstall:
        print('Orca: open the paired desktop app after setup.')
    try:
        plan = package_plan(sys.platform, platform.machine())
        home = home_dir()
        path = home / '.claude/.claude-code-setup.json'
        check_owned_path(path, home)
        receipt = read_receipt(path)
        if receipt.get('tool_attempts', {}).get('orca') and existing_install(plan):
            raise RuntimeError('A previous Orca package attempt was interrupted. Inspect the installation before retrying; the existing path is preserved.')
        if args.uninstall:
            return uninstall(plan, receipt, path, args.dry_run)
        if existing_install(plan):
            owned = receipt.get('tool_installs', {}).get('orca')
            if isinstance(owned, dict) and owned.get('method') == plan['method'] and owned.get('skills_state') == 'pending':
                if not args.dry_run:
                    finish_skills(plan, home, owned, receipt, path)
                print('Orca package preserved; bundled skills retry ' + ('planned.' if args.dry_run else 'completed.'))
            else:
                print('Orca package already installed; preserving its package, settings and ownership.')
            setup = {'state': 'skipped'} if args.no_server else configure_orca_host(
                plan, home, install_paths(plan)[1], args.pairing_address, args.dry_run,
                receipt.get('tool_installs', {}).get('orca_server'),
                on_record=lambda record: save_orca_server_record(receipt, path, record),
            )
            if not args.dry_run and setup.get('state') not in ('configured', 'skipped'):
                raise RuntimeError('Orca server and desktop client setup did not complete')
            if setup.get('state') == 'configured':
                save_orca_server_record(receipt, path, setup)
                if args.desktop_app:
                    open_configured_orca_client(install_paths(plan)[1], setup)
            return 0
        root, cli = install_paths(plan)
        print('Orca: official {} -> {} ({}); bundled skills after install'.format(plan['method'], root, plan['manifest']))
        if args.dry_run:
            configure_orca_host(plan, home, cli, args.pairing_address, True)
            return 0
        for destination in skill_locations(home):
            check_owned_path(destination, home)
        read_skill_lock(skill_locations(home)[2])
        version, meta = fetch_manifest(plan)
        if args.expect_version and version != args.expect_version:
            raise RuntimeError('The latest Orca release is {} but the approved plan targets {}; plan again'.format(version, args.expect_version))
        with tempfile.TemporaryDirectory(prefix='ai-setup-orca-') as directory:
            scratch = Path(directory)
            asset = download(version, meta, scratch)
            receipt.setdefault('tool_attempts', {})['orca'] = {'method': plan['method'], 'version': version}
            save_receipt(path, receipt)
            install_package(plan, asset, scratch)
        if installed_version(plan) != version or not cli.is_file():
            raise RuntimeError('Installed Orca version does not match the verified release')
        owned = {'method': plan['method'], 'version': version, 'skills_added': {}, 'skills_state': 'pending'}
        receipt.setdefault('tool_installs', {})['orca'] = owned
        receipt.get('tool_attempts', {}).pop('orca', None)
        save_receipt(path, receipt)  # 스킬 실패여도 설치된 앱의 소유권은 잃지 않는다.
        finish_skills(plan, home, owned, receipt, path)
        setup = {'state': 'skipped'} if args.no_server else configure_orca_host(
            plan, home, cli, args.pairing_address, args.dry_run,
            receipt.get('tool_installs', {}).get('orca_server'),
            on_record=lambda record: save_orca_server_record(receipt, path, record),
        )
        if setup.get('state') not in ('configured', 'skipped'):
            raise RuntimeError('Orca server and desktop client setup did not complete')
        if setup.get('state') == 'configured':
            save_orca_server_record(receipt, path, setup)
            if args.desktop_app:
                open_configured_orca_client(cli, setup)
        print('Orca {} and bundled skills installed.'.format(version))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print('Orca setup failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
