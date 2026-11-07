# SPEC

> 상태: 확정(2026-10-30 사용자 확인 K1, K4·K10 은 5라운드). setup·harness 모듈 절은 2026-11-01 확정. 2026-11-07 사용자 결정으로 Orca 모듈을 제거했다. 범위: ai-setup 흡수의 모듈 계약과 각 모듈. 결정의 배경은 `2026-10-30-ai-setup-absorption.md` 가 소유한다.

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

## doctor 모듈

코드는 `jarvis/modules/doctor/` 다. `doctor.py` 는 ai-setup `res/doctor/doctor.py` 를 옮긴 것으로 단독 실행(사람용 보고, 종료 코드 0·1·2)도 그대로 된다. 하네스·도구 목록은 setup 모듈의 `jarvis/modules/setup/res/catalog.json` 한 벌을 읽는다. 현재 훅 조각은 훅 모듈의 `jarvis/modules/hooks/settings.json` 을 읽는다.

- 홈 아래 어떤 파일도 만들거나 바꾸거나 지우지 않는다. 버전 확인은 홈·설정·캐시 위치를 임시 폴더로 돌린 환경에서 한다.
- `status.details` 는 `{tools: [{id, kind: harness/tool, found: [PATH 순서의 실행 파일], version, hint}], findings: [{kind, message}]}` 다. `found` 의 홈 아래 경로는 `~/` 로 줄인다. `hint` 는 없는 항목의 그 플랫폼 설치 명령이다.
- `findings.kind` 는 `duplicate`(PATH 의 중복 설치, 첫 번째가 우선), `hook_drift`(은퇴한 훅이 하네스 설정에 남음), `secret`(평문 값이 있는 파일 위치와 키 이름. 값은 내보내지 않는다), `skill`(`~/.agents/skills` 의 lock 밖 스킬. 지우지 않는다)이다. 발견 항목이 있으면 `health` 는 `degraded`, 없으면 `ok` 다.
- `plan` 은 단계가 없고, `apply` 는 그 plan_id 면 `current`, 아니면 `stale` 이다. `uninstall` 은 지울 것이 없다.


## AI 셋업 화면

core 홈의 AI 셋업 화면(디자인 랩 05 의 C 안)은 기기마다 모듈 계약을 부른다. 코드는 `jarvis/modules_api.py` 다.

| 경로 | 누가 | 동작 |
| --- | --- | --- |
| 게이트웨이 `GET /api/modules[?refresh=1]` | 설정 core 만 | 모듈마다 `status` 와 `checked_at`·`ok`, 마지막 apply 작업(`job`). status 는 모듈별로 캐시하고(hooks·setup 300초, doctor 600초, harness 1800초), 오래된 것만 모듈마다 병렬로 다시 읽는다. 캐시가 없을 때(재시작·apply 뒤)와 `refresh=1` 만 점검을 기다리고, 기간만 지난 것은 지난 결과를 바로 주고 뒤에서 다시 읽는다. `refresh=1` 은 `X-Jarvis-Action: modules-refresh` 가 있어야 한다 |
| 게이트웨이 `GET /api/modules/<이름>/plan` | 설정 core 만 | 그 모듈의 `plan`. 10초 동안 캐시해 몰린 요청도 하위 프로세스 하나로 처리한다 |
| 게이트웨이 `POST /api/modules/<이름>/apply?plan_id=<sha256>` | 설정 core 만, `X-Jarvis-Action: module-apply` | 뒤에서 `apply --plan-id` 를 돌리고 바로 202 와 작업을 준다. 한 번에 하나만 돌리고, 다른 적용이나 jarvis 갱신이 진행 중이면 409 다(jarvis 갱신도 적용 중에는 409). 시작할 때 `module-apply.json` 표시를 남겨, 적용 중 재시작하면 다음 조회의 작업이 `interrupted` 다. 끝나면 그 모듈의 status·plan 캐시를 버린다 |
| core `GET /api/devices/modules[?refresh=1]` | tailnet | 기기마다 위의 응답에 `id`·`host`·`status`(HTTP)를 붙여 모은다. 폴링이 실패 중인 기기는 묻지 않는다. viewers 가 아니면(`full: false`) 모듈은 `module·installed·owned·version·latest·health·checked_at·ok·error` 만, 작업은 상태와 결과 종류만 준다(doctor 의 시크릿 위치·경로, 결과 문구는 viewers 만) |
| core `GET /api/devices/<id>/modules/<이름>/plan` | viewers 만 | 그 기기의 plan 을 전달한다 |
| core `POST /api/devices/<id>/modules/<이름>/apply?plan_id=…` | viewers 만, `X-Jarvis-Action: module-apply` | 그 기기에 전달한다. 화면에 보인 계획의 plan_id 만 보내므로, 그사이 계획이 바뀌었으면 apply 는 stale 이 된다 |

- 게이트웨이는 모듈 명령을 하위 프로세스(`python -m jarvis module <이름> <동작> --json`)로 돌리고 출력은 임시 파일로 받는다. 모듈 CLI 는 fd 1 을 바꾸므로 서버 스레드에서 부르지 않는다. 출력이 JSON 이 아니면 종료 코드만 `error` 로 내고 표준 오류의 마지막 줄은 게이트웨이 로그에만 남긴다.
- HEAD 요청은 점검·계획을 돌리지 않는다. 적용 요청(core: viewer IP·기기·모듈·plan_id 앞 12자, 게이트웨이: 시작·끝)은 로그에 한 줄씩 남긴다. 결과 본문은 남기지 않는다.
- 승인 대기 목록과 휴대폰 알림(D7, O2)은 아직 없다. 지금은 viewer 가 화면에서 계획을 보고 실행을 누르는 것이 승인이다.

## 훅 모듈

코드는 `jarvis/modules/hooks/` 다. `danger_guard.py`(PreToolUse 판정), `merge_and_diff.py`(settings.json 병합), `send_to_trash.py`, `graft_sync_hidden.cjs`, `shims/rm`·`shims/RmShim.psm1`(rm 대체 래퍼)과 조각 `settings.json` 은 ai-setup `res/hooks`·`res/settings.json` 을 옮긴 것이다. 설치 흐름(ai-setup 의 `setup_hooks.sh`·`setup_hooks.ps1`)은 계약 층 `jarvis/modules/hooks/contract.py` 가 대신한다.

- 설치하는 파일: POSIX 는 `danger_guard.py`·`shims/rm`, Windows 는 여기에 `send_to_trash.py`·`shims/RmShim.psm1`·`graft_sync_hidden.cjs` 를 더한다. 위치는 `~/.claude/hooks/`.
- 단계: `trash`(install, 휴지통 도구가 없을 때. POSIX 는 `pip --user trash-cli`, Windows 는 `pip send2trash`) → `trash_apt`(sudo, POSIX 에 apt-get 이 있을 때만 계획에 보인다. pip 로 깔리지 않았을 때만 `sudo -n apt-get install -y trash-cli`) → `files`(config, 배포본과 다이제스트가 다른 파일만. 계획은 지금 파일의 다이제스트도 묶고, 쓰기 직전에 다시 확인한다) → `settings`(config, `merge_and_diff` 로 권한·훅 등록과 비어 있는 기본값(model 등)만 병합, 나머지 최상위 키 보존, 바뀌면 백업, 영수증에는 지금 배포본과 같은 파일만 기록) → `selftest`(read, 세션이 훅을 부르는 python 으로 정해진 입력의 판정을 확인. 실패하면 apply 는 failed).
- 휴지통 도구 설치가 실패해도 훅 설치는 계속한다(그 단계는 skipped, status 는 degraded). 휴지통 도구는 게이트웨이 PATH 에 `~/.local/bin` 과 사용자 site 의 bin 을 더해 찾는다(서비스 PATH 에는 이것들이 없다).
- 막힌 단계가 하나라도 있으면 apply 는 어떤 단계도 실행하지 않고 failed 다. 막힘: 영수증 `hook_files` 에 없는 같은 이름 파일(덮어쓰지도, 영수증에 넣지도 않는다), 훅을 실행할 python 이 없음, settings.json 에 등록된 danger_guard 명령의 python 이나 스크립트가 없음(같은 이름의 훅은 병합기가 다시 넣지 않으므로 직접 고쳐야 한다), 병합이 예상 밖의 최상위 키를 바꿈.
- 훅을 실행할 명령(조각의 `{{PY}}`)은 ai-setup 과 같은 순서로 찾는다(POSIX: `python3` → `python`, Windows: `python` → `python3` → `py -3` → `py`). 권한 모드(`permissions.defaultMode`)는 사용자가 지금 쓰는 값을 유지하고(병합기가 모르는 값이면 조각에서 defaultMode 를 빼고 병합한다), 없으면 조각의 값(auto)이다. 계획 요약은 권한 모드·훅 등록·권한 목록 변경을 앞에 둔다.
- status·plan 은 아무것도 쓰지 않는다. 병합은 메모리에서 미리 돌려 보고, 셀프테스트(guard.log 를 쓴다)는 apply 에서만 돈다.
- ai-setup 이 설치한 기기는 영수증의 같은 키(`hook_files`, 권한 기록)를 읽어 이어서 관리한다. uninstall 은 영수증에 기록한 파일과 설치가 넣은 settings 항목만 걷어내고, 영수증의 다른 단계 기록은 남긴다. 영수증에 `hook_files` 가 없는 옛 설치는 ai-setup 과 같은 정해진 목록을 지운다. status 의 `version` 은 설치본, `latest` 는 배포본의 다이제스트다.

## setup 모듈

코드는 `jarvis/modules/setup/` 이다. ai-setup 의 `res/`(단계 엔진 `stages.py`, 카탈로그, 플러그인 목록, 번들 스킬, 키바인드·출력 스타일·mod·OMC HUD 워커, npm-release, update-ai, AGENTS.md 틀)와 `scripts/`(워커와 `setup.sh`·`setup.ps1`)를 같은 배치로 옮겼다. 온보딩 TUI 와 `install.sh`·`install.ps1` 부트스트랩은 옮기지 않았다. 옮긴 `setup.sh`·`setup.ps1` 은 개별 옵션(`--keybinds`·`--skills` 등)을 그대로 받고, 인자가 없으면 아무것도 설치하지 않고 이 모듈을 안내한다. 계약 층은 `jarvis/modules/setup/contract.py` 다.

- 계획: 엔진의 `plan_actions` 에 단계 `base·harness·plugins·skills·codegraph·mcp·newline·claude_special·tools`(agentsmd 제외)를 넘긴 쓰기 액션이다. 하네스는 claude·codex 와 영수증 `harness_installs` 의 하네스, 줄바꿈 키는 영수증에 기록된 값이다. Jarvis 역할·herdr 는 고르지 않는다. 단계 id 는 `<순번>-<stage>`, risk 는 sudo 가 들어간 명령(Linux 의 Tailscale 설치기 포함)이면 `sudo`, 설치(base·harness·codegraph·tools·플러그인 설치)는 `install`, 나머지는 `config` 다(uninstall 계획은 모두 `remove`).
- 다시 적용(refresh): 영수증에 기록된 설정(줄바꿈 키·번들 스킬·출력 스타일·mod·OMC HUD)은 그 워커 배포본(`res/<워커>` 와 워커 스크립트, 점으로 시작하는 경로·`__pycache__` 제외)의 다이제스트가 jarvis 상태 폴더의 `setup-refresh.json` 과 다를 때 `refresh-<id>` 단계(argv 는 워커 스크립트)로 다시 적용한다. 출력 스타일·OMC HUD 워커는 플러그인이 없으면 마켓플레이스에서 설치하므로 `install`, 나머지는 `config` 다. 다시 적용이나 그 설정의 설치 액션이 성공하면 그 다이제스트를 기록한다. 기록이 없는 기기는 처음 한 번 모두 다시 적용한다(2026-11-01 사용자 결정). ai-setup 에서 `--keybinds`·`--skills` 등을 다시 실행하던 갱신 경로이며, 사용자가 고친 파일은 워커가 그대로 둔다. 옮긴 `setup.sh --keybinds` 를 직접 실행해도 이 기록은 바뀌지 않는다(다음 계획에 한 번 더 나온다).
- MCP: 같은 이름(codegraph)의 서버가 이미 등록된 하네스(claude `~/.claude.json` 의 mcpServers, codex `~/.codex/config.toml` 의 `[mcp_servers.codegraph]`, gemini `~/.gemini/settings.json`, opencode 설정의 `mcp`)에는 등록을 계획하지도 기록하지도 않는다. grok·copilot·agy 는 설정 위치가 문서에 없어 보지 않는다.
- apply: 액션을 하나씩 엔진의 `execute` 로 돌린다. 한 stage 안에서 실패하면 그 stage 의 나머지는 `skipped` 다. 그 stage 에 기대는 뒤 stage(`codegraph·harness←base`, `plugins·skills←harness`, `mcp←harness·codegraph`, `claude_special←harness·plugins`)도 앞 stage 가 실패하거나 건너뛰었으면 `skipped` 이고, 독립인 stage 와 refresh 단계는 계속한다. 결과는 단계마다 낸다(실패가 하나라도 있으면 `failed`). 엔진이 성공한 액션의 효과를 영수증에 기록한다. 게이트웨이 PATH 에는 `~/.local/bin`·nvm 이 없으므로 엔진의 PATH 보강과 harness 의 PATH 보강을 함께 쓴다.
- sudo(2026-11-01 사용자 결정 "sudo 단계도 안전하게"): 게이트웨이에는 TTY 가 없으므로 계획의 모든 sudo 는 `sudo -n` 으로 바꿔 비밀번호를 기다리며 멈추지 않게 한다. plan 은 `sudo -n true` 로 비밀번호 없는 sudo(NOPASSWD 또는 유효한 캐시)를 쓸 수 있는지 보고 sudo 단계에 `sudo: ok|password` 를 붙인다(plan_id 에 묶인다). status 는 sudo 를 부르지 않는다(읽을 때마다 auth 로그에 남지 않게). `password` 인 단계는 apply 가 실행하지 않고 `skipped` 와 터미널에서 칠 명령을 남기며, 나머지 단계는 계속한다(원래 AI-35).
- uninstall: 엔진의 제거 계획(영수증에 기록한 것만)이다. dry-run 의 plan_id 를 받아야 실행한다. jarvis 게이트웨이 유닛과 Tailscale 은 지우지 않고 영수증 기록도 남긴다(원격으로 지우면 연결이 끊긴다, 2026-11-01 사용자 결정).
- status: `details` 는 `{harnesses: [PATH 에 있는 하네스], recorded: {영수증 키: 기록}, pending: [단계 요약], sudo_steps: <sudo 단계 수>}`, 할 일이 있으면 `health=degraded` 다. 읽기만 한다(npm 확인은 로그·알림 캐시를 끄고 부른다). macOS 의 Tailscale 앱(앱 번들 안의 CLI)은 설치된 것으로 본다.
- 하네스 설치 단계는 카탈로그의 설치 명령(`cmd`)을, 설정 워커의 설치 단계는 그 워커 배포본의 다이제스트(`digest`)를 단계에 넣어 plan_id 에 묶는다(승인한 계획과 다른 본문이 돌면 stale).
- AGENTS.md 틀은 `res/templates/*.md.tmpl` 이다(이름이 AGENTS.md·CLAUDE.md 면 그 폴더를 만지는 에이전트가 지시로 읽는다).
- AGENTS.md 는 프로젝트 폴더 단위라 이 모듈에 넣지 않았다. `python -m jarvis agentsmd [--harness L] [--force] [--dry-run]` 이 지금 폴더에 옮긴 `setup_agentsmd` 워커를 부른다.
- 세션 보존: setup 계약 층은 Claude 사용자 설정에 `cleanupPeriodDays` 가 없을 때만 36,500일을 `config` 단계로 계획한다. 기존 키는 값에 관계없이 보존하고 status 와 AI 셋업 화면에 표시한다. 설정·영수증의 해시와 워커 다이제스트를 계획에 묶고 다른 setup 단계보다 먼저 실행하며, 계획 뒤 바뀌거나 JSON 객체가 아니거나 심볼릭 링크면 쓰지 않는다. 기존 설정을 백업하고 임시 파일로 교체하며 교체 직전 해시를 다시 확인한다. 외부 프로세스가 같은 잠금을 따르지 않으면 마지막 확인과 교체 사이 경쟁을 원자적으로 막을 수는 없다. 영수증의 `session_retention` 은 중단 시 `pending`, 성공 시 `applied` 이다. pending 은 확인 전 자동 재시도하지 않는다. 제거는 기록된 값이 그대로일 때만 하고 사용자가 바꾼 값과 영수증을 남기면 deferred 다. Codex 전체 세션 보존 설정은 확인 불가로 표시하며 쓰지 않는다. 로컬 파일 기록은 유효 설정·서버 보존의 증명이 아니다.

## harness 모듈

ai-setup 의 update-ai(`jarvis/modules/setup/res/update/update_ai.py`)를 감싼다. 계약 층은 `jarvis/modules/harness/contract.py` 다. 설치는 하지 않는다(setup 모듈).

- status·plan: update-ai `--check` 와 같은 함수로 설치된 카탈로그 항목의 설치 방식·설치본·최신 판을 읽는다. 최신이 아닌 것은 `update` 단계(`update-ai --only <id>`, `target` 은 최신 판), 최신을 확인할 수 없으면 보고만 한다. codex 의 최신 판은 Codex 자신의 갱신 확인과 같은 GitHub 최신 릴리스(`rust-v<판>` 태그)로 읽는다. codex 관리형 데몬이나 실행 중 서버가 설치된 CLI 보다 오래되면(더 새로우면 그대로 둔다) `codex-daemon` 단계(`--allow-interrupt`, 돌고 있는 Codex 작업을 끊을 수 있어 `config`)다.
- apply: 데몬 단계가 있으면 먼저 `update_ai.py --only codex --allow-interrupt` 를 따로 돌린다(이 플래그는 실행 전체에 걸리므로 다른 대상과 함께 넘기지 않는다). 나머지 대상은 플래그 없이 `--only <id,...>` 로 올린다. 결과는 다시 읽은 판으로 단계마다 판정한다(데몬 단계는 데몬·실행 중 서버 행 모두). 세션이 돌아 update-ai 가 보류한 대상은 `deferred` 다. update-ai 는 3000초 안에 끝내고, 넘으면 프로세스 그룹째 끊는다.
- 설치본 판(`--version`)은 doctor 와 같이 홈·설정·캐시를 임시 폴더로 돌린 환경에서 먼저 읽는다(gemini·opencode 는 `--version` 만 불러도 홈에 쓴다). 그 환경에서 판을 내지 못하는 것(래퍼를 거치는 codex, 설정이 필요한 grok)은 update-ai 와 같은 실제 홈으로 다시 읽는다(판정이 update-ai 와 갈리지 않게). 설치본 판을 모르면 계획하지 않는다. 최신 판과 codex 데몬은 실제 홈으로 읽는다.
- PATH 는 update-ai.sh 와 같이 사용자 bin·nvm 의 node(높은 판 먼저)·Homebrew 를 앞에 둔다(Windows 는 그대로).
- uninstall 은 지울 것이 없다(하네스 제거는 setup 의 uninstall).

## 실행본 배치와 이전

- 실행본(서비스의 작업 폴더)은 `launch.ROOT/app` 이다: macOS·Linux `~/.local/share/jarvis/app`, Windows `%LOCALAPPDATA%\jarvis\app`. `~/Workspace/jarvis`(Windows `%USERPROFILE%\Workspace\jarvis`)는 개발 저장소 자리다. `versions/`·`current.json` 의 위치와 형식은 그대로다(2026-11-05 손팡님 A안).
- `jarvis/relocate.py` 하나가 1.8.0 이전 배치(옛 폴더 = `~/Workspace/jarvis`)를 옮긴다. 게이트웨이는 `launch.confirm()` 뒤 한 번(`_confirm`, 건강하게 `HEALTHY_S` 를 넘긴 판에서만), CLI 는 `python -m jarvis relocate [--dry-run] [--json]`, 설치기는 새 코드(임시 폴더에 받은 최신 릴리스, app 에 `relocate.py` 가 있으면 그것)로 `python -m jarvis.relocate` 를 부른다(옛 base 에는 relocate 가 없다). 경로는 둘 다 있으면 파일시스템 정체(`os.path.samefile`), 아니면 `realpath`+`normcase`, 그것도 실패하면(Windows 의 신뢰할 수 없는 탑재 지점 WinError 448 등) `normcase(abspath)` 문자열로 비교하며 비교 함수는 예외를 내지 않는다. 사용 중 판정은 프로세스마다 따로 하고, 판정 자체가 실패하면 문자열로 옛 폴더 아래인 경로를 사용 중으로 본다($HOME 심링크·macOS 대소문자 별칭). 'p 가 d 아래인가'는 p 의 조상을 거슬러 올라가며 같은 비교로 본다.
- 판정(멱등):
  1. 옛 폴더가 없거나, `.git`(파일·폴더)이 있거나, `jarvis/__init__.py` 가 없으면 아무것도 하지 않는다(noop). `.git` 이면 등록도 읽지 않고 외부 명령도 부르지 않는다. Windows 는 관리자 토큰(`shell32.IsUserAnAdmin`, 알 수 없으면 아님)이 아니면 PowerShell·잠금·복사 없이 noop(`관리자 권한이 필요합니다 …`)이다. 게이트웨이는 RunLevel Limited 로 돌아 관리자가 등록한 작업을 고칠 수 없으므로(`Set-ScheduledTask` 0x80070005), **Windows 는 업데이트 버튼만으로는 옮겨지지 않고** 관리자 셸의 install.ps1 재실행이나 `python -m jarvis relocate` 가 필요하다. dry-run 도 같은 판정을 보인다.
  2. 등록: macOS `~/Library/LaunchAgents/dev.jarvis.plist` 의 `WorkingDirectory`, Linux `~/.config/systemd/user/jarvis.service` 의 `WorkingDirectory=`(`%h`·절대 경로), Windows 예약 작업(`relocate.TASK`, 기본 `jarvis`. PowerShell 에는 환경 변수 `JARVIS_TASK_NAME` 으로 넘긴다)의 action 하나. 예약 작업은 설치기 모양만 받는다: Execute 가 `pythonw.exe`, Arguments 가 정확히 `"<폴더>\deploy\jarvis-win.pyw"`(따옴표는 없어도 됨, 다른 인자 없음), WorkingDirectory 가 `<폴더>` 또는 비어 있음. `<폴더>` 를 실제 경로로 비교하고, 모양이 다르면 noop 이다. 새 Arguments 는 치환하지 않고 `"<app>\deploy\jarvis-win.pyw"` 로 새로 만든다. 등록이 옛 폴더도 app 도 가리키지 않으면(pm2·수동·없음) noop.
  3. 등록이 옛 폴더면: app 이 없으면 옛 폴더를 `ROOT/.app-tmp-*` 에 복사(심링크는 링크로, `__pycache__` 제외)한 뒤 `os.replace` 로 app 이름을 준다. `app/jarvis/__init__.py` 가 있으면 다시 쓰고, app 이 있는데 코드가 없으면 failed. 그다음 등록을 고친다: plist 는 옛 경로가 든 모든 값, 유닛은 `WorkingDirectory=` 줄만(다른 바이트는 그대로) 바꾸고 원본을 `ROOT/<파일 이름>.bak-relocate` 로 한 번만 남긴 뒤(LaunchAgents·유닛 폴더 밖) 원자적으로 쓴다. Linux 는 `systemctl --user daemon-reload`, Windows 는 `Set-ScheduledTask -Action`(principal·trigger·settings 는 그대로, `CREATE_NO_WINDOW`). 실패하면 등록을 원상태로 두고 failed 를 낸다. 이번에 만든 app 은 다시 읽은 등록과 서비스 관리자가 로드한 작업 폴더가 모두 옛 폴더임을 확인했을 때만 지운다. 확인하지 못하면(조회 실패·알 수 없음, 시간 초과 뒤 받아들여졌을 수 있음) app 을 남긴다. Windows 는 등록을 다시 읽어 옛 폴더임을 확인했을 때만 지운다. 지울 때는 먼저 `ROOT/.app-tmp-*` 로 이름을 바꾼다. 설치기도 묶음을 `.app-tmp-*` 에 다 푼 뒤에만 app 이름을 준다.
  4. 옛 폴더 정리: 등록이 app 을 가리키고 등록 내용(유닛은 주석 제외)에 옛 경로가 더 없으며, 서비스 관리자가 로드한 설정(macOS `launchctl print gui/<uid>/dev.jarvis` 의 `working directory`, Linux `systemctl --user show jarvis -p WorkingDirectory`, Windows 는 등록된 작업)이 app 이며, 지금 프로세스의 코드(`launch.HERE`)가 옛 폴더 아래가 아니고, 옛 폴더를 작업 폴더로 쓰거나 명령줄에 옛 폴더 아래 경로가 있는 다른 프로세스가 없을 때(psutil, 없으면 정리하지 않음. relocate 자신과 그 조상, 곧 이전을 기다리는 설치기 셸·서비스 관리자는 뺀다)만 옛 폴더를 `ROOT/workspace-old`(있으면 `-<시각>` 접미사)로 옮긴다(cleaned). 지우지 않는다. 옮기지 못하면(다른 파일시스템 등) 그대로 둔다(registered).
- 지연 완료(macOS): launchd 는 plist 를 다시 읽기 전까지(로그인·재부팅·사용자의 bootout/bootstrap) 옛 폴더에서 띄운다. 그동안은 `pending_reload` 로 옛 폴더를 남긴다(지우면 KeepAlive 재시작이 chdir 에 실패한다). jarvis 는 스스로 bootout/bootstrap 하지 않는다. 다시 읽힌 뒤의 첫 기동이 정리한다. `install.sh` 는 사용자가 실행한 셸이므로 `pending_reload` 이면 `launchctl bootout`·`bootstrap`(실패하면 1초 간격으로 다섯 번까지) 으로 다시 읽힌 뒤 relocate 를 한 번 더 부른다. 다시 로드하지 못하면 복구 명령을 보인다.
- Linux·Windows 는 재시작을 강제하지 않는다. 다음 기동부터 app 에서 뜨고, 게이트웨이가 `versions/` 에서 돌면 그 자리에서 정리까지 한다. 예외로 `install.sh` 는 이번 실행의 relocate 가 유닛을 바꿨는데(실행 전후 cksum 비교) `registered` 로 끝나면(옛 폴더에서 도는 게이트웨이) `systemctl --user try-restart jarvis` 하고 `/api/health`(설정의 port, 최대 약 20초, curl 이 없으면 3초)를 기다린 뒤 relocate 를 한 번 더 부른다. 이전이 없는 평소 재실행은 다시 띄우지 않는다. install.sh 는 옛 폴더를 작업 폴더로 쥐지 않게 `$HOME` 으로 옮겨 돈다.
- 한 번에 하나만 돈다: 1번 판정(.git 등)을 통과하면 `ROOT/.relocate.lock` 을 기다리지 않고 잡는다(POSIX `fcntl.flock`, Windows `msvcrt.locking`). 못 잡으면 아무것도 바꾸지 않고 `busy` 로 끝난다(게이트웨이는 로그만). 판정·복사·등록·실패 정리(app 을 지울지의 판단 포함)는 모두 잠금 안의 관찰로 한다. dry-run 은 읽기만 하므로 잠그지 않는다.
- 설치기(install.sh·install.ps1): `~/Workspace/jarvis` 가 .git 없는 실행본이고 서비스 등록이 있으면 새 릴리스를 app 에 풀지 않는다. 옛 base 를 그대로 app 으로 옮긴다(base 가 바뀌면 런처의 롤백 기준 `previous=None` → base 가 바뀐다). 최신 릴리스는 임시 폴더에 받아 sha256 을 확인하고 그 코드로 relocate 만 부른다. 자동 이전 뒤(app = 옛 base) 다시 실행해도 같다. 그 밖에 app 이 없으면 `ROOT/.app-tmp-*` 에 다 푼 뒤 app 이름을 준다. install.sh 는 새로 만드는 plist·유닛의 작업 폴더를 템플릿과 상관없이 app 으로 적는다(1.8.0 이전 릴리스의 템플릿은 `~/Workspace/jarvis`). install.ps1 은 app 의 `deploy\install-windows.ps1` 로 작업을 만든다.
- 결과: `{"state": "noop"|"busy"|"registered"|"pending_reload"|"cleaned"|"failed", "detail", "steps"}`. `--dry-run` 은 아무것도 바꾸지 않고 `state: "planned"`(할 일이 있을 때)와 `dry_run: true` 를 낸다. CLI 종료 코드는 failed 면 1, 그 밖은 0 이다.

## core 전환

> 상태: 2026-11-04 사용자 승인으로 추가했다(설계 비평 반영). CLI 구현됨, 단위 GREEN(Windows·실제 기기 미검증). 모듈이 아니라 머리말 범위(모듈 계약) 밖의 로컬 CLI 다.

코드는 `jarvis/core_trust.py` 다. 게이트웨이 설정(`$JARVIS_CONFIG` 또는 `~/.config/jarvis/config.toml`)의 최상위 `core` 목록 한 키만 고친다. `core` 는 셸 권한급 신뢰(사용량·로그인·계정 전환·모듈 apply·업데이트를 부를 수 있는 tailnet IP)라서 바꾸는 권한은 그 기기의 셸 사용자에게만 둔다. 그래서 각 기기에서 로컬로 도는 CLI 로만 있고, HTTP 경로·화면 버튼·`module.MODULES` 등록은 없다.

    python -m jarvis core-trust plan  [--add <IP> | --remove <IP>] --json
    python -m jarvis core-trust apply (--add <IP> | --remove <IP>) --plan-id <sha256> --json

표준 출력은 JSON 한 개다. 종료 코드는 모듈 계약의 `EXIT` 를 따르고, 인자 오류는 사용법을 표준 오류에 내고 1 이다.

| 동작 | 부작용 | 출력 | 종료 코드 |
| --- | --- | --- | --- |
| `plan` | 없음 | `{module: "core-trust", device, path, sha256, mode, proxy_key: set/unset, before, after, target: {ip, host}, steps: [{id: "core", summary, risk: "config", argv: []}], blocked: [{step, reason}], plan_id}` | 0 계획을 세움(막힌 단계 포함), 1 세우지 못함 |
| `apply` | 설정 파일의 최상위 `core` 한 줄 | `{status: done/current/stale/failed, before, after, steps: [{id, result, message}]}` | 0 done·current, 1 failed(blocked 포함), 3 stale |

- 진입점: `python -m jarvis` 는 인자가 없으면 게이트웨이를 기동하고, 첫 인자가 `update`·`module`·`agentsmd`·`core-trust`·`relocate`·`--version` 이면 그 명령을 돈다. 그 밖의 첫 인자(오타 포함)는 게이트웨이를 기동하지 않고 사용법을 표준 오류에 내고 종료 코드 1 이다. 런처가 받아 둔 릴리스(`versions/<버전>`)로 넘어가므로 실제로 도는 것은 그 릴리스의 코드다. `core-trust` 가 없는 옛 릴리스에서는 이 명령이 게이트웨이를 하나 더 띄우므로, 실행 전에 `python -m jarvis --version` 으로 버전을 확인한다(절차는 `README.md`).
- 연산
  - `--add` 는 IP 를 목록 맨 앞에 둔다(이미 있으면 맨 앞으로 옮긴다). 트레이의 core 주소는 `CORE[0]` 이다. IP 는 IPv4 `100.64.0.0/10` 이고 `str(ipaddress.ip_address(x)) == x` 인 정규형만 받는다(서버는 IPv4 로만 bind 하고 `_trusted` 는 문자열이 정확히 같아야 통과시킨다).
  - `--remove` 는 목록에서 같은 문자열 항목을 뺀다.
  - 인자가 없는 `plan` 은 지금 상태만 보인다.
  - 결과가 지금 목록과 같으면 단계가 없고 `apply` 는 `current` 다.
- 대상 확인: `plan` 은 `config.TAILSCALE_BIN` 의 `tailscale status --json`(`Self` 와 `Peer`)에서 그 IP 를 `TailscaleIPs` 에 가진 노드의 호스트명을 찾아 `target.host` 에 넣는다(plan_id 에 묶인다). 호스트명은 tailnet 에서 유일한 MagicDNS 이름(`DNSName`, 끝의 `.` 제외)이고, 없으면 노드가 스스로 정한 `HostName` 이다. 승인자는 IP 가 아니라 호스트명을 보고 승인한다. `--add` 에서 찾지 못하거나 조회가 실패하면 막힘이다. `--remove` 는 이미 tailnet 에서 사라진 옛 core 를 빼야 할 수 있으므로 찾지 못해도 `host: null` 로 두고 막지 않는다.
- `plan_id` 는 모듈 계약과 같은 정규화 sha256(`module.seal`)이다. 계획에 `device`·`path`·`sha256`·`before`·`after`·`target` 이 들어가므로 다른 기기에서 받은 승인이나 계획 뒤에 바뀐 파일은 `stale` 이다.
  - `sha256` 은 키가 `proxy_key` 인 줄(`^\s*["']?proxy_key["']?\s*=`. 값이 여러 줄 문자열이면 그 값이 끝나는 줄까지)을 고정 문자열 `proxy_key=<숨김>` 으로 바꾼 바이트의 해시다. 키의 존재·위치만 반영하고 값은 반영하지 않으므로, 출력된 해시나 plan_id 로 키를 맞춰 볼 수 없다. 계획 뒤 `proxy_key` 값만 바뀐 경우는 `stale` 이 아니고, `apply` 는 지금 파일을 기준으로 `core` 줄만 바꾸므로 바뀐 키를 보존한다.
- 막힘(쓰지 않음)
  - 설정 파일이 없음, 심볼릭 링크임(`os.replace` 가 링크를 일반 파일로 바꿔 버린다)
  - 설정을 읽지 못함(`CONFIG_ERROR`: TOML 오류, BOM 포함)
  - 이 프로세스에 `JARVIS_CORE` 가 있음(파일보다 환경 변수가 이긴다)
  - `--add` 의 IP 가 위 정규형 IPv4 tailnet 이 아님(IPv6·루프백·LAN·형식 오류), 또는 tailnet 에서 호스트명을 찾지 못함
  - 결과 목록에 유효한 IPv4 tailnet 항목(위 정규형)이 하나도 없음(`--remove` 와 인자 없는 `plan` 에도 적용한다). 그런 목록은 빈 목록과 같이 아무도 믿지 않는 상태다.
  - 첫 테이블 머리줄(`^\s*\[`) 앞의 `core =` 줄이 둘 이상이거나 여러 줄 배열임
  - 그 `core` 줄에 꼬리 주석(`#`)이 있음. 꼬리 주석은 보존하지 않고 막는다(IP 에는 `#` 이 없으므로 줄에 `#` 이 있으면 주석이다).
  - tomllib 이 읽은 최상위 `core` 가 그 줄과 맞지 않음
  - 설정 파일이 있는 디렉터리에 쓰기 권한이 없음(`os.access(dirname, W_OK)`. 임시 파일과 `os.replace` 에 필요한 것은 디렉터리 권한이다)
  - POSIX 에서 실행 사용자(euid)가 설정 파일 소유자가 아님(`sudo` 로 바꾸면 교체한 파일이 root 소유가 되어 게이트웨이가 읽지 못하고, 재시작하면 빈 신뢰가 된다)
- 쓰기 순서
  1. 첫 테이블 머리줄 앞의 `core =` 한 줄만 바꾼다. 없으면 첫 테이블 머리줄 바로 앞에 넣는다(테이블이 없으면 끝).
     - 파일 끝의 `[infra_units]` 같은 테이블 안으로 들어가지 않게 하려는 것이다.
     - 값이 쉼표 문자열(`core = "a,b"`, `config.env_list` 가 받는 형태)이면 한 줄 배열(`core = ["a", "b"]`)로 다시 쓴다.
     - 줄 끝(CRLF/LF)과 다른 줄·다른 줄의 주석은 그대로 둔다. 넣는 줄은 파일의 줄 끝을 따른다.
  2. 같은 폴더의 임시 파일에 쓴다. POSIX 에서는 원래 mode 를 그대로 준다. Windows 에서는 권한을 상속된 ACL 이 정하므로 mode 보존·출력(`mode`)은 POSIX 에서만 의미가 있고, 원래 파일에 따로 준 ACL 은 교체 뒤 남지 않는다.
  3. tomllib 으로 다시 읽어 `core` 외 모든 값이 같고 `core` 가 `after` 인지 확인한다.
  4. 교체 직전에 원본 바이트가 이번 `apply` 가 읽은 것과 같은지 다시 확인한다(이 해시는 출력하지 않는다).
  5. `os.replace` 로 바꾼다.
  - 어느 확인이든 실패하면 임시 파일을 지우고 원본을 두며 `failed` 다(임시 파일을 지우지 못하면 그 경로를 메시지에 넣는다). 백업 파일은 만들지 않는다(proxy_key 사본을 늘리지 않는다).
- 사용자 설정 보존의 예외: `AGENTS.md` 는 사용자 설정을 덮어쓰지 않는다고 정한다. 이 기능은 그 예외로, 사용자가 승인한 plan_id 와 지금 다시 세운 계획이 같을 때만 최상위 `core` 한 줄을 바꾼다. 다른 키·줄은 바꾸지 않는다.
- 반영: `core` 는 자동 반영 키다(`config._LIVE_KEYS`). 바뀐 파일은 다음 설정 확인(10초) 뒤 재시작 없이 `_trusted` 판정에 쓰인다. `apply` 의 `done` 은 파일 기록까지이고, 반영은 core 쪽에서 확인한다. 설정 파일이 깨지면 실행 중에는 마지막 정상 설정을 유지하지만 재시작하면 `CONFIG_ERROR` 로 빈 신뢰가 된다. 그래서 CLI 는 다시 파싱해 검증된 파일만 교체한다.
- 비밀: `proxy_key` 는 값 없이 `set`/`unset` 만 출력한다. 그 값은 표준 출력·표준 오류·로그·예외 메시지 어디에도 나가지 않고(TOML 오류는 위치만 낸다), `core` 외의 설정 값도 출력하지 않는다.
- 신뢰를 바꾸는 HTTP 경로는 없다.
  - 어떤 HTTP 경로도 게이트웨이 설정 파일을 쓰지 않는다. `core-trust` 는 `module.MODULES` 와 서버 라우트에 없다.
  - 전환의 확인에는 core 의 `GET /api/devices/modules` 기기별 `status` 를 쓴다.

    | 코드 | 뜻 |
    | --- | --- |
    | 200 | 그 기기가 이 core 를 믿음 |
    | 403 | 믿지 않음 |
    | 501 | 판단 불가(그 기기 게이트웨이에 `/api/modules` 가 없음, 갱신 필요) |
    | 502 | 미도달(연결 실패·연속 실패·첫 점검 시간 초과) |

  - 옛 IP 를 빼는 좁히기(`--remove`)는 새 core 에서 200 을 받은 기기에서만 한다. 501·502 를 받은 기기는 미반영으로 남긴다.
  - 서비스가 읽는 설정 경로는 `/api/snapshot` 의 `config_path` 로 대조한다.
- 전환 순서(넓히기 → 확인 → 인그레스 → 좁히기)와 수동 단계(새 core 설정·proxy_key 회전·재시작, pit5 Caddy, 옛 core 정리), 기기별 실행 위치와 python, 최소 버전 확인은 `README.md` 가 소유한다.

## SSH 기기

> 상태: 2026-11-04 사용자 승인으로 추가했다(설계 비평 반영). 구현됨, 단위 GREEN(가짜 ssh. 실제 ssh·대상 기기 미검증). core 쪽 확장이며, 게이트웨이 계약(`server.py` `_trusted`·`_peer_ok`·Host 검사)은 바꾸지 않는다. 코드는 `jarvis/core.py`·`jarvis/modules_api.py` 다.

tailnet 으로는 닿지 않고 SSH 로만 닿는 기기(예: 다른 tailnet 의 기기에 ProxyJump 로 닿는 경우)는 설정 `devices` 에 `ssh` 로 적는다. core 는 그 기기를 SSH 로 조회하고, AI 셋업 모듈과 jarvis 갱신만 원격 CLI 로 조작한다.

    [[devices]]
    id = "thsvkd-kgolf-macmini"
    ssh = "kgolf-macmini-m1"

### 형식

- 항목은 `{id, url}` 또는 `{id, ssh}` 중 정확히 하나다. 둘 다 있거나, 둘 다 없거나, 형식이 틀리면 한 줄 경고를 내고 건너뛴다.
- `ssh` 는 core 를 실행하는 사용자의 `~/.ssh/config` Host 별칭이다. `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")` 를 통과하는 것만 받는다(끝 개행도 거절한다).
- 접속 세부(HostName·User·ProxyJump·키)는 그 파일이 소유한다. jarvis 는 그 파일을 읽지도 쓰지도 않는다.
- 내부 url 은 `ssh://<별칭>` 이다. `GET /api/devices` 의 `url` 도 같고, 화면은 이 스킴을 보고 'SSH 경유' 를 표시한다.
- 재시작 키이고, 새 환경 변수는 없다.
- 대상의 배치는 install.sh 규약으로 고정한다. 저장소는 `~/.local/share/jarvis/app`(없으면 옛 배치·개발 체크아웃 `~/Workspace/jarvis`. 둘 다 `jarvis/__init__.py` 가 있어야 한다), python 은 `~/.local/share/jarvis/venv/bin/python` 이고, 대상은 macOS 또는 Linux 여야 한다. 규약과 다른 기기는 조작이 502 로 끝난다.

### ssh 실행 공통

- 실행 파일은 macOS 면 `/usr/bin/ssh`(Homebrew openssh 는 `UseKeychain` 을 거절한다), 그 밖은 `PATH` 의 `ssh` 다.
- 공통 옵션은 `-o BatchMode=yes -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 -o LogLevel=ERROR` 다. `StrictHostKeyChecking`·`UserKnownHostsFile` 은 주지 않는다(사용자 known_hosts 를 쓴다).
- 연결 공유(`-o ControlMaster=auto -o ControlPath=~/.ssh/jarvis-%C -o ControlPersist=60`)는 조회(`-W`)에만, Windows 가 아닐 때만 붙인다. 조작에는 `-o ControlMaster=no -o ControlPath=none` 을 줘서 연결마다 따로 띄우고, `-a -x -o ClearAllForwardings=yes` 로 core 사용자 `~/.ssh/config` 의 에이전트·X11·포트 전달이 대상 기기로 넘어가지 않게 한다. 그래서 한 호출의 시간 초과가 진행 중인 apply·update 를 끊지 않는다.
- ssh 는 새 세션(`start_new_session`)으로 띄우고 표준 오류는 임시 파일로 받는다. 상한을 넘으면 프로세스 그룹 전체를 끝낸다(ProxyJump 자식이 고아로 남지 않게).
- ssh 의 표준 입력은 물려주지 않는다. 조회는 파이프로 요청을 쓴 직후 닫고, 조작은 `/dev/null` 이다(표준 입력을 물려받은 ssh 는 호출자의 입력을 먹는다).

### 조회

- 다음을 실행하고, 그 표준 입출력으로 HTTP/1.0 GET 하나를 `Host: 127.0.0.1:9300` 으로 보낸다. 원격 셸 명령은 없다.

      ssh … -W 127.0.0.1:9300 -- <별칭>

- 요청을 쓴 직후 표준 입력을 닫고, 응답은 `Content-Length` 만큼만 읽는다.
- 대상은 그 기기의 루프백으로 고정한다. 그 기기 게이트웨이는 출발지를 `local` 로 보므로 신뢰 경로는 언제나 403 이다.
- 보내는 경로는 `/api/snapshot`·`/api/history?range=1h|24h`·`/api/changes` 뿐이다. 다른 경로는 ssh 를 실행하지 않고 오류로 처리한다.
- 상한은 HTTP 와 같다. 압축 해제 기준 16 MiB, gzip 잘림 검사, 전체 `DEADLINE_S + 10` 초다.
- relay(history·changes)는 SSH 기기마다 동시에 4개까지 띄운다. 넘치면 ssh 를 실행하지 않고 바로 502 다.
- 실패하면 그 기기의 `fails`·`error` 에 남긴다. `error` 는 ssh 표준 오류의 마지막 줄(200자) 또는 `HTTP n` 이다.

### 조작: 원격 CLI

조작은 다음 형태로 실행한다. 스크립트는 상수이고, 끼우는 값은 `module.MODULES` 키, 64자 hex `plan_id`, 정수 pid 뿐이다. 모든 토큰은 끼우기 전에 `re.fullmatch(r"[A-Za-z0-9._-]+")` 를 다시 검사하고, 전체를 `shlex.quote` 로 감싼다.

    ssh … -o ControlMaster=no -o ControlPath=none -a -x -o ClearAllForwardings=yes -T -- <별칭> "sh -c '<스크립트>'"

- 스크립트 머리(`PRE`)는 저장소로 이동하고 venv python 을 확인한다. 둘 중 하나라도 없으면 종료 코드 90 이다.
- `PRE` 는 `PATH` 를 `deploy/jarvis.plist` 와 같게 두고 `PYTHONIOENCODING=utf-8` 을 export 한다(원격 로케일과 무관하게 출력을 읽는다).
- 보내는 하위 명령은 `module`·`update`·`--version` 과 재시작 스크립트뿐이다. `core-trust` 와 `module … uninstall` 은 원격으로 실행하지 않는다.
- 모듈 apply 는 모듈을 가리지 않고 허용한다. GUI 없는 SSH 세션에서 실패하는 단계는 모듈 결과(`failed`·`deferred`)로 보인다.
- ssh 를 실행하기 전에 스냅샷의 `meta.version` 을 본다(게이트). 근거는 시험한 최저 판 이상만 허용한다는 것이다.
  - 스냅샷이 없으면 502 다.
  - `launch.key` 로 읽지 못하거나 1.7.8 보다 낮으면 501 `"이 기기의 jarvis <v> 는 SSH 조작을 지원하지 않습니다(그 기기에서 직접 갱신)"` 다.

| core 요청 | 원격 실행 | 응답 |
| --- | --- | --- |
| `GET /api/devices/modules[?refresh=1]` 의 그 기기 몫 | 모듈마다 `python -m jarvis module <이름> status --json`. 병렬로 돈다 | 게이트웨이 `GET /api/modules` 와 같은 모양 `{modules, job}`. 모듈별 캐시 기간(`TTL`)도 같다. 처음과 `refresh=1` 일 때만 기다리고, 기간만 지났으면 지난 값을 바로 주고 기기마다 하나만 뒤에서 다시 읽는다. `job` 은 core 가 그 기기에 시작한 마지막 apply 다 |
| `GET /api/devices/<id>/modules/<이름>/plan` | `module <이름> plan --json` | 종료 0 이고 JSON 이면 200, 아니면 502 |
| `POST /api/devices/<id>/modules/<이름>/apply?plan_id=…` | `module <이름> apply --json --plan-id <sha256> 2>/dev/null` | 바로 202 와 작업을 준다(아래 규칙) |
| `POST /api/devices/<id>/jarvis/update` (일괄 포함) | `update 2>/dev/null && --version`. `to` 가 스냅샷 판보다 크면 재시작 스크립트를 따로 보낸다 | `{running, to, restart}` (HTTP 기기와 같다) |

#### apply 규칙

1. 이름이 `module.MODULES` 에 없으면 404 이고, `plan_id` 가 `PLAN_ID_RE.fullmatch` 를 통과하지 못하면 400 이다. 둘 다 ssh 를 실행하기 전에 판단한다.
2. 그 기기에서 core 가 시작한 apply 가 진행 중이면 409 다. jarvis 갱신이 진행 중이어도 409 다.
3. SSH 작업 목록은 `modules_api` 모듈 수준에 두고 `modules_api.running()` 이 그것까지 본다. 그래서 SSH apply 가 진행 중이면 core 기기 자신의 jarvis 갱신(`self_update.start`, 단일·일괄)도 409 다.
4. 끝나면 작업을 `finished`(`code`, `result`) 로 바꾸고, 그 모듈의 status 캐시를 버린다.
5. ssh 가 255 로 끝나거나 시간 상한(`APPLY_TIMEOUT_S + 60`)을 넘기면 작업을 `interrupted` 로 둔다. 원격은 끝까지 돌았을 수 있다.
6. 승인한 계획과의 일치는 원격 모듈이 다시 세운 계획으로 확인한다. 다르면 stale(종료 3)이다.
7. 로그에는 시작과 끝, `plan_id` 앞 12자만 남긴다.

#### jarvis 갱신 규칙

- core 가 그 기기에 시작한 apply 가 진행 중이거나 이미 갱신 중이면 409 다.
- 받지 못하면 502 이고, 오류는 원격 출력의 마지막 줄이다.
- `to` 는 표준 출력에서 `^jarvis (\d+\.\d+\.\d+)` 로 시작하는 마지막 줄의 판이다. 그 줄이 없으면 502 다.
- `to` 가 스냅샷 판보다 크면 재시작한다. 같거나 작으면 재시작하지 않고 `restart: false` 다.
  - pid 는 `STALE_S` 안 스냅샷의 `monitor.pid` 이고, `type(pid) is int and pid > 1` 일 때만 쓴다(bool·0·1·음수는 거절).
  - 재시작 스크립트는 상수다. `ps -o command= -p <pid>` 의 명령줄이 `-m jarvis` 로 끝날 때만 `kill -TERM <pid>` 를 보내고, 아니면 종료 코드 91 이다.
  - pid 를 쓸 수 없거나, 91 이거나, kill 이 실패하면 502 `"받았지만 재시작하지 못했습니다(그 기기에서 재시작하세요)"` 다.
- 재시작은 서비스 관리자에 맡긴다. launchd `KeepAlive`, systemd `Restart=always` 가 다시 띄우고, 롤백과 확정은 `launch` 가 그대로 한다.

#### 종료 코드와 오류 문구

| 결과 | 오류 문구 |
| --- | --- |
| 255 | `"<id> 에 닿지 못했습니다: <ssh 표준 오류 마지막 줄>"` |
| 90 | `"install.sh 배치(~/.local/share/jarvis/app 또는 ~/Workspace/jarvis, ~/.local/share/jarvis/venv)가 아닙니다"` |
| 91 | `"받았지만 재시작하지 못했습니다(그 기기에서 재시작하세요)"` |
| 시간 초과 | `"<n>초 안에 끝나지 않았습니다"` |
| 그 밖 | 표준 출력에서 `{` 로 시작하는 마지막 줄을 JSON 으로 읽는다. JSON 이 아니면 종료 코드만 `error` 로 낸다 |

- 읽기 동작(status·plan·`--version`)은 원격 표준 오류를 core 로그에만 남긴다. 마지막 줄 300자다.
- 쓰기 동작(apply·update)은 원격 표준 오류를 버린다. 대상 디스크에도 남기지 않는다.
- 명령줄과 응답 본문은 오류 문구와 로그에 넣지 않는다.

### 지원하지 않는 조작

다음 요청은 ssh 도 HTTP 도 보내지 않고 501 `{error: "SSH 로 닿는 기기는 이 조작을 지원하지 않습니다(그 기기에서 직접)"}` 를 준다.

- 사용량
- 로그인 상태·시작
- 계정 전환

이유는 둘이다. 이 조작들은 CLI 가 없다. 그리고 로그인 코드·계정 이메일이 SSH 경로를 지나지 않게 하기 위해서다.

### 일괄 동작의 순서

일괄 동작은 지금 코드 순서를 그대로 둔다.

- `usage_all`·`jarvis_update_all`·`/api/devices/modules` 는 폴링 실패(502)를 SSH 가드(501)보다 먼저 본다. 폴링이 실패 중인 기기는 묻지 않고 502 다. 그래서 SSH 기기의 501 은 폴링이 성공 중일 때만 나온다.
- `jarvis_update_all` 에서 SSH 기기는 core 자신보다 먼저 처리한다.

### 전제 (사용자 쪽, jarvis 는 설정하지 않는다)

- core 기기
  - 별칭과 ProxyJump 는 `~/.ssh/config` 에 둔다.
  - 점프 호스트 Host 블록에도 `BatchMode yes`·`ConnectTimeout`·`ServerAliveInterval` 을 둔다. 명령줄 옵션은 목적지 홉에만 걸린다.
  - known_hosts 에 점프 호스트와 대상의 호스트 키를 등록한다.
  - core 서비스 세션에서 BatchMode 로(passphrase 입력 없이) 쓸 수 있는 키가 있어야 한다.
- 대상 기기
  - sshd 가 명령 실행과 TCP 포워딩(`direct-tcpip`)을 허용한다.
  - 게이트웨이가 루프백 9300 에서 받는다.
  - install.sh 배치다.
  - 재시작 버튼은 서비스 정의가 `KeepAlive=true`(launchd) 또는 `Restart=always`(systemd)이고 스냅샷 `monitor.pid` 가 서비스 관리자가 띄운 pid 일 때만 쓴다. 아니면 kill 뒤 게이트웨이가 꺼진 채 남는다.
- 신뢰의 뿌리는 core 사용자의 SSH 키다. 조작에는 원격 명령이 필요하므로 그 키를 `permitopen` 전용으로 좁힐 수 없다.
- SSH 기기 스냅샷(피어 목록·명령줄)과 ssh 오류 마지막 줄은 core 의 `/api/devices*` 로 core 의 tailnet 에 보인다. 서로 다른 tailnet 의 100.64/10 IP 는 겹칠 수 있다.

### 위험

- 프로세스 사이 잠금이 없다. core 의 잠금은 core 가 시작한 조작만 막고, 대상 게이트웨이가 스스로 하는 작업(그 기기 화면에서 시작한 작업)과는 겹칠 수 있다.
- apply 중 연결이 끊기면 core 는 `interrupted` 로 두지만 원격은 계속 돌 수 있다. 원격 잠금이 없으므로 다시 apply 하면 원격에서 겹칠 수 있다. core 가 재시작하면 SSH 작업 목록은 사라진다.
- 연결 공유 마스터는 setsid 로 살아남지만, ProxyJump 의 점프 프로세스는 마스터를 만든 조회 호출의 프로세스 그룹에 남는다. 그 조회가 시간 초과로 끝나면 마스터 연결이 끊기고 다음 조회가 다시 만든다. 조작은 연결을 공유하지 않으므로 영향이 없다.
- 판 차이: 게이트는 서비스의 판(스냅샷)을 보지만 원격 CLI 는 `current.json` 이 가리키는 판으로 돈다. 받아만 두고 재시작하지 않은 기기는 둘이 다를 수 있다.
- `_confirm` 경합: 원격 update 가 원격 기동 후 60초 안의 `_confirm` 과 겹치면 `current.json` 을 서로 덮어쓸 수 있다. 다음 갱신에서 회복된다.
