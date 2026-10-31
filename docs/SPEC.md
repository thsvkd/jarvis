# SPEC

> 상태: 확정(2026-10-30 사용자 확인 K1, K4·K10 은 5라운드). 범위: ai-setup 흡수의 모듈 계약과 Orca 모듈. 결정의 배경은 `2026-10-30-ai-setup-absorption.md` 가 소유한다.

## 모듈 계약

jarvis 본체는 모듈을 라이브러리로 부른다. 부트스트랩과 시험은 저장소 런처를 거친 CLI 로 같은 함수를 부른다.

    python -m jarvis module <이름> <status|plan|apply|uninstall> --json [--dry-run] [--plan-id <sha256>]

런처를 거쳐야 `launch.CURRENT` 가 가리키는 `versions/<버전>` 의 코드만 돈다. 출력은 표준 출력의 JSON 한 개이고, 사람용 진행 로그는 표준 오류로 보낸다.

| 동작 | 부작용 | 출력 | 종료 코드 |
| --- | --- | --- | --- |
| `status` | 없음 | `{module, installed, owned, version, latest, health: ok/degraded/down, details}` | 0 읽음(설치되지 않음 포함), 1 읽지 못함 |
| `plan` | 없음(dry-run) | `{module, device, plan_id, action: install/update/reconfigure/uninstall, steps: [{id, summary, risk, argv}], blocked: [{step, reason}]}` | 0 계획을 세움(막힌 단계 포함), 1 세우지 못함 |
| `apply` | 계획의 단계 | `{status: done/current/deferred/stale/failed, steps: [{id, result: ok/skipped/deferred/failed, message}]}` | 0 done·current, 1 failed(blocked 단계가 있을 때 포함), 2 deferred(기다리면 스스로 풀리는 보류: 열린 터미널·데스크톱 앱), 3 stale |
| `uninstall` | 영수증에 기록한 것만 지움. `--dry-run` 은 아무것도 바꾸지 않고 `plan_id` 를 내며, 실제 실행은 그 `--plan-id` 가 지금 영수증과 맞을 때만 한다 | `apply` 와 같음(+`plan_id`) | 0 모두 지움, 1 실패, 2 사용자가 바꾼 항목을 남기고 영수증 보존, 3 stale |

- `apply` 는 `--dry-run` 을 받지 않는다(미리 보기는 `plan` 이다). 막힌 단계가 있으면 `failed` 다. 바꾸는 단계는 목표 버전(`target`)을 갖고, 실행할 때 매니페스트가 다르면 아무것도 바꾸지 않는다.
- 모듈과 그 자식 프로세스의 출력은 표준 오류로 간다(CLI 가 fd 1 을 돌린다).
- `plan_id` 는 정규화한 계획 JSON(`plan_id` 를 뺀 것, 키 정렬, 구분자 `,`·`:`)의 sha256 이다. 계획에는 기기 식별자(`device`, hostname)가 들어가므로 한 기기의 승인을 다른 기기에 쓸 수 없다. 실행할 때 정해지는 값(검증한 패키지의 임시 경로 등)은 `argv` 에 `<검증한 deb>` 같은 자리표시자로 적는다. `apply` 는 실행 직전에 계획을 다시 세워 받은 `plan_id` 와 다르면 아무것도 실행하지 않고 `stale` 을 돌려준다.
- `risk` 는 `read`·`update`·`install`·`remove`·`sudo`·`config`·`external` 중 하나다. 기본 정책은 `read`·`update` 자동, 나머지는 승인이다.
- 영수증은 `~/.claude/.claude-code-setup.json` 이고 키(`tool_installs.*` 등)는 ai-setup 과 같다. 모듈만 쓰고, 임시 파일에 쓴 뒤 `os.replace` 로 바꾸며, 다른 키를 보존한다. 파싱할 수 없으면 쓰기 전에 멈춘다.

## Orca 모듈

코드는 `jarvis/modules/orca/` 다. `orca.py`(설치·스킬·uninstall), `orca_server.py`(서버·페어링·Windows 러너), `orca_windows.py`(Windows 보안 경계)는 Windows 러너가 서비스 디렉터리로 복사해 단독 실행하므로 패키지 import 없이 같은 디렉터리의 형제 모듈만 부른다.

### 플랫폼별 형태

| 플랫폼 | 설치본 | 서버 | 갱신 주체 |
| --- | --- | --- | --- |
| Debian/Ubuntu | 공식 deb `/opt/Orca` | 사용자 유닛 `orca-serve.service`: `ExecStart=/opt/Orca/resources/bin/orca-ide serve --supervise --port 6768 --pairing-address <tailnet IP>`(페어링 플래그는 O7), `KillMode=process`, `Restart=on-failure`, Python 러너 없음 | `jarvis/orca_update.py` |
| macOS | `/Applications/Orca.app` | `<앱 CLI> serve --supervise ...` 를 바로 실행하는 LaunchAgent | Orca 공식 supervised 업데이터 RPC(`jarvis/orca_update.py`) |
| Windows(S4U) | `%LOCALAPPDATA%\Programs\orca` | S4U 예약 작업과 러너, 서비스 디렉터리의 `app\build-<식별자>` 복사본 | 설치기 `/S --updated`(`modules/orca/orca_update_windows.py`), 러너의 자동 전환. 설치 폴더의 데스크톱 앱이 열려 있으면 보류(deferred). 일반 권한이라 터미널을 셀 수 없으면 최대 300초 동안 러너의 전환을 기다린다(K10) |
| Windows(데스크톱 전용) | 같음 | 데스크톱 앱이 서버를 겸함 | 갱신하지 않는다(K4: Windows 는 S4U 하나로 통일). 계획은 package 단계를 blocked 로 보고하고 skills 단계를 내지 않는다 |

- 기존 유닛이 같은 형태인지는 실행 파일을 realpath 로 비교해 판정하고, 페어링 플래그(`--no-pairing`·`--mobile-pairing` 등)는 판정에서 뺀다(잠정, O7). 형태가 다른 유닛은 바꾸지 않고 보고한다.
- 설치본 버전은 PATH 의 `orca` 가 아니라 설치본의 CLI·메타데이터로 읽는다(Linux 의 PATH 첫 `orca` 는 데스크톱 shim 일 수 있다).
- 설치본이 최신 매니페스트와 같거나 더 새로우면 최신으로 본다.
- 패키지는 공식 매니페스트의 크기·SHA-512 를 통과한 것만 쓴다. 검증 코드는 한 벌이다.
- 비대화형 sudo 는 `sudo -n` 으로만 시도한다.
- Windows S4U 서버는 High 무결성이라 일반 권한의 터미널·RPC 조회가 `runtime_access_denied` 를 받는다. 이때 터미널 수는 0 이 아니라 "셀 수 없음"이고, `status` 의 `health` 는 `down` 이 아니라 `degraded` 다.
- 페어링 링크·코드·토큰은 출력·로그·영수증에 넣지 않는다.

### 기존 서버의 수용과 소유 (잠정, 사용자 확인 전)

- 같은 형태의 기존 유닛(지금 jarvis 기기 전부)은 파일을 쓰지 않고 `tool_installs.orca_server` 에 `adopted: true` 로 기록한다. 갱신·상태 조회는 똑같이 다루고, uninstall 은 adopted 유닛을 지우지 않고 기록만 지운다.
- 새로 쓴 유닛은 쓴 파일의 sha256(`service_digest`)을 기록한다. uninstall 은 파일이 그 다이제스트와 같을 때만 지우고, 다르면 남긴 채 종료 코드 2 로 영수증을 보존한다.
- jarvis 가 설치하지 않은 패키지에는 `tool_installs.orca` 를 기록하지 않는다.
- adopted 기기에서 데스크톱 클라이언트 페어링은 임시 서버가 필요한데, 그 프로필을 실행 중인 서버가 이미 쓰므로 서버를 멈춰야 한다. 데스크톱 프로필에 그 서버의 환경이 이미 있으면 건너뛰고, 없으면 `blocked`(실행 중인 서버를 멈춰야 함)로 둔다. 실행 중인 서버를 몰래 멈추는 경로는 없다.
- 새로 쓰는 유닛·LaunchAgent 에는 사용자의 로그인 셸에서 얻은 `PATH`·`SHELL` 을 넣는다(Orca 터미널이 하네스를 찾도록). 쓴 뒤 `daemon-reload` 와 `enable --now` 를 한다. 실행 중인 adopted 유닛에는 아무것도 하지 않는다.
- systemd 사용자 매니저의 linger 가 꺼져 있으면 plan 에 `config` 단계로 보이고 승인 뒤에만 켠다.


## doctor 모듈

코드는 `jarvis/modules/doctor/` 다. `doctor.py` 는 ai-setup `res/doctor/doctor.py` 를 옮긴 것으로 단독 실행(사람용 보고, 종료 코드 0·1·2)도 그대로 된다. 하네스·도구 목록(`catalog.json`)은 ai-setup `res/catalog.json` 의 사본이다. 하네스 모듈을 옮길 때 원본도 jarvis 로 넘어오며, 그때까지는 ai-setup 의 것과 내용을 같게 유지한다. 현재 훅 조각은 훅 모듈의 `jarvis/modules/hooks/settings.json` 을 읽는다.

- 읽기 전용이다. 홈 아래 어떤 파일도 만들거나 바꾸지 않는다. 버전 확인은 홈·설정·캐시 위치를 임시 폴더로 돌린 환경에서 한다.
- `status.details` 는 `{tools: [{id, kind: harness/tool, found: [PATH 순서의 실행 파일], version, hint}], findings: [{kind, message}]}` 다. `found` 의 홈 아래 경로는 `~/` 로 줄인다. `hint` 는 없는 항목의 그 플랫폼 설치 명령이다.
- `findings.kind` 는 `duplicate`(PATH 의 중복 설치, 첫 번째가 우선), `hook_drift`(은퇴한 훅이 하네스 설정에 남음), `secret`(평문 값이 있는 파일 위치와 키 이름. 값은 내보내지 않는다), `skill`(`~/.agents/skills` 의 lock 밖 스킬. 지우지 않는다)이다. 발견 항목이 있으면 `health` 는 `degraded`, 없으면 `ok` 다.
- 바꾸는 것이 없으므로 `plan` 은 언제나 단계가 없고, `apply` 는 그 plan_id 면 `current`, 아니면 `stale` 이다. `uninstall` 은 지울 것이 없다.

## AI 셋업 화면

core 홈의 AI 셋업 화면(디자인 랩 05 의 C 안)은 기기마다 모듈 계약을 부른다. 코드는 `jarvis/modules_api.py` 다.

| 경로 | 누가 | 동작 |
| --- | --- | --- |
| 게이트웨이 `GET /api/modules[?refresh=1]` | 설정 core 만 | 모듈마다 `status` 와 `checked_at`·`ok`, 마지막 apply 작업(`job`). status 는 모듈별로 캐시한다(orca 60초, doctor 600초). `refresh=1` 은 `X-Jarvis-Action: modules-refresh` 가 있어야 한다 |
| 게이트웨이 `GET /api/modules/<이름>/plan` | 설정 core 만 | 그 모듈의 `plan`. 10초 동안 캐시해 몰린 요청도 하위 프로세스 하나로 처리한다 |
| 게이트웨이 `POST /api/modules/<이름>/apply?plan_id=<sha256>` | 설정 core 만, `X-Jarvis-Action: module-apply` | 뒤에서 `apply --plan-id` 를 돌리고 바로 202 와 작업을 준다. 한 번에 하나만 돌리고, 다른 적용·jarvis 갱신·Orca 업데이트·페어링 발급이 진행 중이면 409 다(그 작업들도 적용 중에는 409). 시작할 때 `module-apply.json` 표시를 남겨, 적용 중 재시작하면 다음 조회의 작업이 `interrupted` 다. 끝나면 그 모듈의 status·plan 캐시를 버린다 |
| core `GET /api/devices/modules[?refresh=1]` | tailnet | 기기마다 위의 응답에 `id`·`host`·`status`(HTTP)를 붙여 모은다. 폴링이 실패 중인 기기는 묻지 않는다. viewers 가 아니면(`full: false`) 모듈은 `module·installed·owned·version·latest·health·checked_at·ok·error` 만, 작업은 상태와 결과 종류만 준다(doctor 의 시크릿 위치·경로, 결과 문구는 viewers 만) |
| core `GET /api/devices/<id>/modules/<이름>/plan` | viewers 만 | 그 기기의 plan 을 전달한다 |
| core `POST /api/devices/<id>/modules/<이름>/apply?plan_id=…` | viewers 만, `X-Jarvis-Action: module-apply` | 그 기기에 전달한다. 화면에 보인 계획의 plan_id 만 보내므로, 그사이 계획이 바뀌었으면 apply 는 stale 이 된다 |

- 게이트웨이는 모듈 명령을 하위 프로세스(`python -m jarvis module <이름> <동작> --json`)로 돌리고 출력은 임시 파일로 받는다. 모듈 CLI 는 fd 1 을 바꾸므로 서버 스레드에서 부르지 않는다. 출력이 JSON 이 아니면 종료 코드만 `error` 로 내고 표준 오류의 마지막 줄은 게이트웨이 로그에만 남긴다.
- HEAD 요청은 점검·계획을 돌리지 않는다. 적용 요청(core: viewer IP·기기·모듈·plan_id 앞 12자, 게이트웨이: 시작·끝)은 로그에 한 줄씩 남긴다. 결과 본문과 페어링 값은 남기지 않는다.
- 모듈 apply 의 단계 오류 문구는 페어링 링크를 가리고(`redact_reason`), 시간 초과는 명령 이름과 시간만 적는다(argv 에 페어링 링크가 들어갈 수 있다).
- 승인 대기 목록과 휴대폰 알림(D7, O2)은 아직 없다. 지금은 viewer 가 화면에서 계획을 보고 실행을 누르는 것이 승인이다.

## 훅 모듈

코드는 `jarvis/modules/hooks/` 다. `danger_guard.py`(PreToolUse 판정), `merge_and_diff.py`(settings.json 병합), `send_to_trash.py`, `graft_sync_hidden.cjs`, `shims/rm`·`shims/RmShim.psm1`(rm 대체 래퍼)과 조각 `settings.json` 은 ai-setup `res/hooks`·`res/settings.json` 을 옮긴 것이다. 설치 흐름(ai-setup 의 `setup_hooks.sh`·`setup_hooks.ps1`)은 계약 층 `jarvis/hooks_module.py` 가 대신한다.

- 설치하는 파일: POSIX 는 `danger_guard.py`·`shims/rm`, Windows 는 여기에 `send_to_trash.py`·`shims/RmShim.psm1`·`graft_sync_hidden.cjs` 를 더한다. 위치는 `~/.claude/hooks/`.
- 단계: `trash`(install, 휴지통 도구가 없을 때. POSIX 는 `pip --user trash-cli`, Windows 는 `pip send2trash`) → `trash_apt`(sudo, POSIX 에 apt-get 이 있을 때만 계획에 보인다. pip 로 깔리지 않았을 때만 `sudo -n apt-get install -y trash-cli`) → `files`(config, 배포본과 다이제스트가 다른 파일만. 계획은 지금 파일의 다이제스트도 묶고, 쓰기 직전에 다시 확인한다) → `settings`(config, `merge_and_diff` 로 권한·훅 등록과 비어 있는 기본값(model 등)만 병합, 나머지 최상위 키 보존, 바뀌면 백업, 영수증에는 지금 배포본과 같은 파일만 기록) → `selftest`(read, 세션이 훅을 부르는 python 으로 정해진 입력의 판정을 확인. 실패하면 apply 는 failed).
- 휴지통 도구 설치가 실패해도 훅 설치는 계속한다(그 단계는 skipped, status 는 degraded). 휴지통 도구는 게이트웨이 PATH 에 `~/.local/bin` 과 사용자 site 의 bin 을 더해 찾는다(서비스 PATH 에는 이것들이 없다).
- 막힌 단계가 하나라도 있으면 apply 는 어떤 단계도 실행하지 않고 failed 다. 막힘: 영수증 `hook_files` 에 없는 같은 이름 파일(덮어쓰지도, 영수증에 넣지도 않는다), 훅을 실행할 python 이 없음, settings.json 에 등록된 danger_guard 명령의 python 이나 스크립트가 없음(같은 이름의 훅은 병합기가 다시 넣지 않으므로 직접 고쳐야 한다), 병합이 예상 밖의 최상위 키를 바꿈.
- 훅을 실행할 명령(조각의 `{{PY}}`)은 ai-setup 과 같은 순서로 찾는다(POSIX: `python3` → `python`, Windows: `python` → `python3` → `py -3` → `py`). 권한 모드(`permissions.defaultMode`)는 사용자가 지금 쓰는 값을 유지하고(병합기가 모르는 값이면 조각에서 defaultMode 를 빼고 병합한다), 없으면 조각의 값(auto)이다. 계획 요약은 권한 모드·훅 등록·권한 목록 변경을 앞에 둔다.
- status·plan 은 아무것도 쓰지 않는다. 병합은 메모리에서 미리 돌려 보고, 셀프테스트(guard.log 를 쓴다)는 apply 에서만 돈다.
- ai-setup 이 설치한 기기는 영수증의 같은 키(`hook_files`, 권한 기록)를 읽어 이어서 관리한다. uninstall 은 영수증에 기록한 파일과 설치가 넣은 settings 항목만 걷어내고, 영수증의 다른 단계 기록은 남긴다. 영수증에 `hook_files` 가 없는 옛 설치는 ai-setup 과 같은 정해진 목록을 지운다. status 의 `version` 은 설치본, `latest` 는 배포본의 다이제스트다.
