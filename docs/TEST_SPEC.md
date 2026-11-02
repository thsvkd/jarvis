# TEST_SPEC

> 상태: 확정(2026-10-30 사용자 확인 K1, ORCA-8 은 사용자 결정으로 고침, Windows 갱신 방식 K4·K10 은 5라운드). 셋업·하네스 갱신·AGENTS.md 절과 BOOT-2·3 은 2026-11-01 사용자가 확정했다. 목록의 추가·수정·삭제는 사용자만 닫는다.
> 모든 케이스는 격리된 임시 HOME 과 대체한 명령으로 검증한다. 실제 사용자 홈, 실제 Orca 서비스, 실제 설치기, 실제 페어링은 쓰지 않는다.
> 테스트는 docstring·주석에 `covers: ORCA-n` 을 적는다. ai-setup 에서 옮긴 테스트는 괄호 안에 원래 ID(AI-n)를 남긴다.

## Orca

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| ORCA-1 | WHEN Orca 모듈을 설치하면 THE SYSTEM SHALL 크기·SHA-512 를 통과한 공식 패키지만 설치하고, 서버 프로필의 서버에 데스크톱 프로필의 클라이언트를 등록하며, 패키지·스킬·서버의 소유를 영수증에 기록한다. 모바일 링크는 Windows 는 서비스 디렉터리에 보관하고, POSIX 는 core 의 1회 발급에 맡긴다(잠정) | `tests/test_orca_module_install.py` (AI-14·15·18·35·37·48) | 부분 (계약·`sudo -n`·`--no-server` 반영, 단위; POSIX 새 설치의 데스크톱 페어링 미구현, 실측 전) |
| ORCA-2 | WHEN Linux 에서 서버를 구성하면 THE SYSTEM SHALL SPEC 형태의 `orca-serve.service` 를 만들고, 같은 형태의 기존 유닛은 받아들여 기록하며, 형태가 다른 유닛은 바꾸지 않고 보고한다 | `tests/test_orca_serve.py`, `tests/test_orca_module_contract.py`(계획·apply 의 받아들임) | 부분 (단위; 새 유닛 작성 뒤 데스크톱 페어링 미구현, 실측 전) pi5-s3 실측 통과(2026-10-30, 1.4.237 → 1.4.244). |
| ORCA-3 | WHEN macOS 에서 서버를 구성하면 THE SYSTEM SHALL `serve --supervise` 를 직접 실행하는 LaunchAgent 를 만든다 | `tests/test_orca_serve.py::Write::test_launch_agent_runs_the_app_cli_directly` | 부분 (단위; macOS 실측 전) |
| ORCA-4 | WHEN Windows 에서 서버를 구성·실행·정지하면 THE SYSTEM SHALL ai-setup AI-49~AI-54 의 동작을 유지한다 | `tests/test_orca_module_server.py`, `tests/test_orca_module_windows.py` | 부분 (단위; Windows 전용 테스트는 kgolf 에서 통과, 2026-10-30. 실제 구성은 실측 전) |
| ORCA-5 | WHEN Linux·macOS 에서 갱신하면 THE SYSTEM SHALL 매니페스트 크기·SHA-512 를 통과한 패키지만 쓰고, `validate_runtime` 을 통과한 경우에만 갱신하며, Linux 는 `sudo -n` 으로만 설치하고, 세션 연속성(데몬 PID·시작 시각·PTY 식별자)을 확인하고 데몬 밖 터미널이 있으면 보류하며, 실패하면 서비스를 원래대로 다시 띄운 뒤 실패로 보고한다 | `tests/test_orca_update.py`, `tests/test_orca_sessions.py` (기존) | 대조 전 pi5-s3 실측 통과(2026-10-30, 1.4.237 → 1.4.244). |
| ORCA-6 | WHEN Windows 에서 갱신하면 THE SYSTEM SHALL ai-setup AI-55a~AI-55c 의 동작을 유지하고, S4U 서버 기록이 없는 기기는 갱신하지 않는다(K4) | `tests/test_orca_module_update_windows.py`(AI-55a~c), `tests/test_orca_update.py`(S4U 경로·데스크톱 앱 보류·작업 연결·S4U 없는 기기) | 부분 (단위; 셀 수 없는 터미널이면 300초까지 기다림, K10) kgolf 실측(2026-10-30): 데스크톱 앱이 열려 있어 apply 가 아무것도 바꾸지 않고 deferred·종료 코드 2. 앱을 닫은 뒤 apply: 설치기 `/S --updated` 로 1.4.243 → 1.4.245, S4U 서버 전환, done·종료 코드 0, 다시 plan 하면 단계 없음. |
| ORCA-7 | WHEN 설치본이 최신 릴리스와 같거나 더 새로우면 THE SYSTEM SHALL 최신으로 판정하고 내려받거나 설치하지 않는다 | `tests/test_orca_module_update_windows.py::Cases::test_an_install_newer_than_the_release_manifest_is_not_downgraded`, `tests/test_orca_update.py`(check 의 `>=`) | GREEN (단위) |
| ORCA-8 | WHEN Orca 를 갱신하면 THE SYSTEM SHALL `orca skills update --all` 로 번들 스킬을 모두 새 판의 사본으로 바꾸고(사용자가 고친 스킬도 덮어쓴다), 맞춘 판을 기록해 같은 판에서는 다시 계획하지 않으며, modules/orca 가 깐 스킬은 새 사본의 다이제스트·링크·lock 을 다시 기록한다 | `tests/test_orca_install.py::SkillsTest`, `tests/test_orca_module_contract.py`(`test_plan_updates_skills_on_an_orca_jarvis_did_not_install`, skills 만 있는 계획, 같은 판의 current) | GREEN (단위; 실측 전) pi5-s3 실측 통과(2026-10-30, 1.4.237 → 1.4.244). |
| ORCA-9 | WHEN `status`·`plan` 을 부르면 THE SYSTEM SHALL 파일·설정·패키지를 바꾸지 않고 SPEC 형식으로 답한다 | `tests/test_orca_module_contract.py` | GREEN (단위; pi5-server2·pi5-s3 실측) |
| ORCA-10 | WHEN `apply` 가 열린 터미널 때문에 서버 전환을 미루면 THE SYSTEM SHALL 종료 코드 2 와 그 이유를 돌려준다 | `tests/test_orca_module_contract.py::Apply::test_an_update_held_for_open_terminals_is_deferred_with_exit_code_2` | GREEN (단위; Linux·macOS 갱신 경로. Windows 는 단위 5) kgolf 실측(2026-10-30): 데스크톱 앱이 열려 있어 apply 가 아무것도 바꾸지 않고 deferred·종료 코드 2. 앱을 닫은 뒤 apply: 설치기 `/S --updated` 로 1.4.243 → 1.4.245, S4U 서버 전환, done·종료 코드 0, 다시 plan 하면 단계 없음. |
| ORCA-11 | WHEN uninstall 하면 THE SYSTEM SHALL 영수증에 기록한 패키지·스킬·서버만 지우고 나머지는 보존한다 | `tests/test_orca_module_install.py` (AI-34), `tests/test_orca_serve.py::Remove`, `tests/test_orca_module_contract.py::Apply`(받아들인 유닛·바뀐 유닛) | GREEN (단위) |
| ORCA-12 | WHEN Orca 모듈이 jarvis 에 들어가면 THE SYSTEM SHALL ai-setup 의 `--tools orca` 와 `update-ai orca` 가 Orca 를 바꾸지 않고 jarvis 를 안내한다 | ai-setup `tests/test_stages.py::test_orca_desktop_points_to_jarvis_and_changes_nothing`, `tests/test_update_ai.py`(orca 대상 안내·exit 1), bats·Pester(`--tools`/`-Tools` 거부·영수증 보존) | GREEN (ai-setup 단위: a5a3353·2a9d5cc·b2bf4de, `scripts/test.sh` 종료 코드 0. kgolf 사본에서 안내 문구 실측) |
| ORCA-13 | WHEN `apply` 가 받은 `--plan-id` 가 실행 직전에 다시 세운 계획과 다르면 THE SYSTEM SHALL 아무것도 실행하지 않고 `stale` 과 종료 코드 3 을 돌려준다 | `test_plan_id_is_bound_to_the_device`, `Apply::test_a_plan_id_that_no_longer_matches_runs_nothing_and_is_stale` | GREEN (단위; pi5-s3 실측에서 승인한 plan_id 로 실행) |

## AI 구독 사용량

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| USAGE-1 | WHEN core 홈에서 AI 구독 사용량을 보면 THE SYSTEM SHALL 기기별 표 위에 프로바이더별 계정과 각 한도 창·오류·값의 기기와 확인 시각을 표시하고 기존 필터를 적용한다. 같은 프로바이더의 이메일·조직이 같은 계정은 사용량이 있는 값 중 최근 값을 한 번 표시하며, 이메일이 없으면 기기별로 구분한다. 서로 다른 한도 창은 합산하지 않는다. | `tests/test_usage_summary.mjs` | 단위 검증 |
| USAGE-2 | WHEN Grok·Antigravity 사용량에 연결된 계정 이메일을 안전하게 확인할 수 있으면 THE SYSTEM SHALL 이메일을 표시하고, 확인할 수 없으면 기본 계정으로 표시하며 자격 증명·계정 ID 는 내보내지 않는다. | `tests/test_provider_usage.py`, `tests/test_antigravity_usage.py`, `tests/test_orca_usage.py::NormalizeTest` | 부분 (Antigravity 활성 계정 s2 실측; 다른 기기와 Grok 실계정 미검증) |
| USAGE-3 | WHEN core 홈에서 AI 구독 사용량을 조회하면 THE SYSTEM SHALL Orca 의 실행·계정 설정·응답과 독립적으로 각 프로바이더와 계정의 사용량을 표시하고, 계정·한도를 확인할 수 없는 경우 그 상태를 명시하며, 자격 증명과 계정 ID 를 내보내거나 로그인을 변경하지 않는다. | `tests/test_provider_usage.py`, `tests/test_codex_native_usage.py`, `tests/test_cswap_cached_usage.py`, `tests/test_opencode_usage.py`, `tests/test_antigravity_usage.py`, `tests/test_gemini_usage.py`, `tests/test_kimi_usage.py`, `tests/test_minimax_usage.py`, `tests/test_zcode_usage.py`, `tests/test_independent_usage.py`, `tests/test_core.py`, `tests/test_usage_summary.mjs` | 부분 (독립 API 와 웹 연결; Gemini·Kimi·MiniMax·ZCode·OpenCode Go 및 기기별 실증 미완료) |
| USAGE-4 | WHEN 사용량 화면에서 기기 행의 새로 로그인 버튼을 보면 THE SYSTEM SHALL 저장한 자격이 없다고 확인된 프로바이더에만 버튼을 표시하고, 자격이 있거나 유무를 확인하지 못했으면 표시하지 않는다. 자격 확인은 저장한 자격 파일과 CLI 자체의 답으로만 한다. | `tests/test_cli_login.py::FreshLoginTest`, `tests/test_independent_usage.py`, `tests/test_orca_usage.py::ReadTest`, `tests/test_usage_summary.mjs` | 부분 (판정 로직·화면 단위 검증; 실제 기기에서 로그인 없음→버튼 노출 실증 미완료) |

## 화면

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| UI-1 | WHEN 헤더의 탭을 고르거나 `/#<섹션>` 주소로 홈을 열면 THE SYSTEM SHALL 다시 불러오지 않고 그 섹션 하나만 표시하고(해시가 없거나 그 기기에 없는 섹션이면 개요), 섹션 밖 해시(`#svc=…`, `#main`)는 지금 탭을 유지하며, 기기·AI 사용량 탭은 core 에서만 표시한다. | `tests/test_jarvis_js.mjs`(탭 바 블록) | GREEN (단위; s2 실데이터 브라우저 확인, 360px 가로 스크롤 없음) |
| UI-2 | WHEN 다른 화면(개요·지도·셋업)으로 옮겨 헤더를 다시 그리면 THE SYSTEM SHALL 그 기기에서 마지막으로 받은 호스트 표시를 첫 응답 전에 먼저 보여 주고, 저장된 값이 없을 때만 확인 중 문구를 보인다. | `tests/test_jarvis_js.mjs`(호스트 블록) | GREEN (단위; s2 브라우저에서 지도·셋업 첫 페인트 확인) |
| UI-3 | WHEN 수치가 갱신되면 THE SYSTEM SHALL 숫자 텍스트를 중간 값 없이 바로 최종 값으로 바꾼다. | `tests/test_number_update.mjs` | GREEN (단위) |

## 설치기

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| BOOT-2 | WHEN Linux 에서 `install.sh --core <tailnet IP>` 를 실행하면 THE SYSTEM SHALL macOS 와 같은 릴리스·설정·모듈 흐름에 LaunchAgent 대신 venv 의 python 을 부르는 systemd 사용자 유닛을 없을 때만 만들어 켜고(linger 를 시도), 다시 실행해도 유닛을 바꾸거나 돌고 있는 게이트웨이를 다시 띄우지 않으며, 그 밖의 시스템은 아무것도 쓰기 전에 거절한다 | `tests/test_install_sh.py` | GREEN (단위. 실제 새 Linux 기기 전체 실행 미검증) |
| BOOT-3 | WHEN Windows 관리자 PowerShell 에서 `install.ps1 -Core <tailnet IP>` 를 실행하면 THE SYSTEM SHALL Python 3.11+ 가 없을 때만 winget 으로 깔고, sha256 을 통과한 최신 릴리스를 없을 때만 풀며, 설정을 없을 때만 BOM 없는 UTF-8 로 만들고, 게이트웨이 예약 작업이 없을 때만 `deploy\install-windows.ps1` 을 실행한 뒤(다시 실행해도 돌고 있는 게이트웨이를 다시 띄우지 않는다) 모듈 plan → apply 를 실행한다. tailnet 이 아닌 core·관리자 아님은 아무것도 쓰기 전에 거절하고, `-DryRun` 은 할 일만 보이며, `irm \| iex` 로 실행해도 셸을 닫지 않는다 | kgolf 실측(Windows PowerShell 5.1, scriptblock 실행) | 부분 (kgolf(5.1) 실측: -DryRun 은 이미 있는 저장소·설정·게이트웨이 작업을 건너뛰고 모듈 단계만 보임, tailnet 밖·자리 범위 밖 core 거절, scriptblock 실행에서 셸 유지, 설정 쓰기는 CRLF·LF 원본 모두 BOM 없는 UTF-8 로 tomllib 이 core 를 읽고 한국어 보존. Pester 는 BOM 없음·파싱만. 새 Windows 기기 전체 실행 미검증) |
| BOOT-1 | WHEN macOS 에서 `install.sh --core <tailnet IP>` 를 실행하면 THE SYSTEM SHALL sha256 을 통과한 최신 릴리스를 풀고, 설정·LaunchAgent 를 없을 때만 만들어 띄우며, 모듈을 plan 의 plan_id 로 apply 한다. 다시 실행해도 기존 설정을 덮어쓰지 않는다. | `tests/test_install_sh.py` | 단위 GREEN (Mac 도구 부분은 thsvkd-kgolf-macmini 임시 폴더에서 확인: uv venv, 릴리스 sha256·압축 해제, plist lint. 새 Mac 전체 실행 미검증) |

## doctor

ai-setup 의 AI-1~AI-6 을 옮긴 것이다. 테스트 본문은 원본과 같고(`tests/test_doctor.py`, pytest 형식을 `pytest_compat` 로 돌린다), 대상만 `jarvis/modules/doctor/doctor.py` 로 바꿨다.

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| DOCTOR-1 | WHEN doctor 를 실행하면 THE SYSTEM SHALL 홈 아래 어떤 파일도 만들거나 바꾸지 않는다 | `test_doctor_writes_nothing_under_home`, `tests/test_doctor_module.py::test_status_writes_nothing_under_home` | GREEN (단위) |
| DOCTOR-2 | WHEN 카탈로그의 하네스가 PATH 에 있으면 THE SYSTEM SHALL 그 이름과 버전을 보고하고, 없으면 그 플랫폼의 설치 명령을 안내한다 | `test_reports_installed_harness_version_and_install_hint_for_missing` | GREEN (단위) |
| DOCTOR-3 | WHEN 같은 하네스 실행 파일이 PATH 에 두 개 이상 있으면 THE SYSTEM SHALL 중복 설치로 보고하고 어느 쪽이 우선인지 표시한다 | `test_duplicate_harness_on_path_is_reported_with_the_winner`, `test_same_directory_twice_on_path_is_not_a_duplicate` | GREEN (단위) |
| DOCTOR-4 | WHEN 영수증이 은퇴로 표시한 훅이 어느 하네스 설정에 아직 연결되어 있으면 THE SYSTEM SHALL 드리프트로 보고한다 | `test_retired_hook_still_wired_in_another_harness_is_drift`, `test_current_hook_is_not_drift`, `test_hook_the_receipt_lists_as_retired_is_drift` | GREEN (단위) |
| DOCTOR-5 | WHEN 하네스 설정 파일에 토큰·키처럼 보이는 평문 값이 있으면 THE SYSTEM SHALL 파일 위치와 키 이름만 보고하고 값은 출력하지 않는다 | `test_plaintext_secret_reports_location_and_key_but_never_the_value` 외 4건 | GREEN (단위) |
| DOCTOR-6 | WHEN `~/.agents/skills` 에 lock 파일에 없는 스킬 디렉터리가 있으면 THE SYSTEM SHALL 그 이름을 보고하고 지우지 않는다 | `test_skill_outside_lock_is_reported_and_kept`, `test_a_skill_link_that_cannot_be_followed_is_reported_without_stopping` | GREEN (단위) |
| DOCTOR-7 | WHEN `python -m jarvis module doctor status --json` 을 실행하면 THE SYSTEM SHALL 도구별 설치 위치·버전·설치 안내와 종류별 발견 항목을 모듈 계약의 JSON 으로 내고, 홈 경로는 `~/` 로 줄이며, plan 은 단계가 없고 apply 는 current(다른 plan_id 면 stale)다 | `tests/test_doctor_module.py` | GREEN (단위. s2 에서 읽기 전용 실행: 9초, 중복 2·시크릿 위치 1·lock 밖 스킬 15) |

## AI 셋업 화면

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| SETUP-1 | WHEN core 의 AI 셋업 화면이 모듈 상태를 물으면 THE SYSTEM SHALL 기기마다 게이트웨이의 모듈 status 를 모으고(모듈별 캐시, 맞춤 헤더가 있을 때만 강제 새로고침), 닿지 않는 기기는 오류로 표시하며, viewers 가 아닌 기기에는 doctor 세부·작업 결과 문구를 빼고 주고, 모듈 출력이 JSON 이 아니면 종료 코드만 오류로 낸다 | `tests/test_modules_api.py` | GREEN (단위) |
| SETUP-2 | WHEN 화면에서 계획을 보거나 적용하면 THE SYSTEM SHALL viewers 만 계획을 받고, viewers 와 정확한 `X-Jarvis-Action: module-apply` 가 있을 때만 그 plan_id 를 그 기기의 게이트웨이에 전달하며, 게이트웨이는 설정 core 의 요청만 받아 한 번에 하나의 apply 를 뒤에서 돌린다(다른 적용·갱신·발급과 서로 배제, 재시작으로 끊긴 적용은 interrupted, 오류 문구에 페어링 링크를 남기지 않음) | `tests/test_modules_api.py` | GREEN (단위) |

## 훅

옮긴 훅 자체의 테스트(`tests/test_danger_guard.py`·`test_merge_and_diff.py`·`test_rm_shim.py`·`test_hook_compat.py`·`test_git_snapshot.py`, Pester `tests/RmShim.Tests.ps1`)는 ai-setup 원본 그대로이며 케이스 ID 없이 함께 돈다.

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| HOOKS-1 | WHEN 훅 모듈의 status·plan 을 실행하면 THE SYSTEM SHALL 홈 아래 아무것도 쓰지 않고, 필요한 단계(trash·trash_apt·files·settings·selftest)를 계획하며(휴지통 도구는 사용자 bin 에서도 찾고, sudo 는 별도 단계로 보임), 등록된 훅 명령이 동작하지 않으면 degraded·막힘으로 보고한다 | `tests/test_hooks_module.py` | GREEN (단위. s2 실측: 게이트웨이 PATH 로도 ai-setup 이 설치한 훅을 받아들여 health=ok·단계 없음, settings·영수증 해시 그대로) |
| HOOKS-2 | WHEN 훅 계획을 apply 하면 THE SYSTEM SHALL 막힌 단계가 있으면 아무것도 하지 않고 failed, 아니면 배포본과 다른 훅 파일만 쓰고(계획 뒤·도중에 바뀐 파일은 덮어쓰지 않음) settings.json 은 권한·훅만 병합(다른 키·사용자의 권한 모드 보존)하며, 휴지통 도구 설치 실패는 건너뛰고, 셀프테스트가 실패하면 failed, 계획이 바뀌었으면 stale 이다. 다시 plan 하면 단계가 없다 | `tests/test_hooks_module.py` | GREEN (단위. 1.4.2 실측: core AI 셋업 API 를 viewer(kgolf)에서 불러 thsvkd-ubuntu-1·thsvkd-kgolf-macmini·kgolf(Windows) 의 옛 판 훅을 갱신, 셋 다 done·셀프테스트 5건 통과, 다시 plan 하면 단계 없음) |
| HOOKS-3 | WHEN `~/.claude/hooks` 에 설치 기록에 없는 같은 이름 파일이 있으면 THE SYSTEM SHALL 그 파일을 덮어쓰지도 영수증에 넣지도 않고 막힘으로 보고한다(다른 파일이 모두 최신이어도, 몇 번을 apply 해도) | `tests/test_hooks_module.py` | GREEN (단위) |
| HOOKS-4 | WHEN 훅을 uninstall 하면 THE SYSTEM SHALL dry-run 의 plan_id 가 맞을 때만 기록한 파일과 설치가 넣은 settings 항목만 지우고 영수증의 다른 단계 기록은 남긴다 | `tests/test_hooks_module.py` | GREEN (단위) |

## 셋업

ai-setup 의 단계형 설치(`res/stages.py` 와 워커)를 `jarvis/modules/setup/` 로 옮긴 것이다. 옮긴 테스트(`tests/test_stages.py`·`test_keybinds.py`·`test_skills.py`·`test_mods.py`·`test_output_style.py`·`test_omc_hud.py`·`test_npm_release.py`·`test_setup_scripts.py`·`test_update_ai.py`, bats `tests/setup.bats`, Pester `tests/Setup.Tests.ps1`)는 원본 그대로(대상 경로와 아래 표시한 것만 바꿈) 함께 돈다. 옮기지 않은 것: 온보딩 TUI(`res/onboard.py`·`res/tui.py`, 원래 AI-45~47 은 core 의 AI 셋업 화면이 대신한다), `install.sh`·`install.ps1` 부트스트랩, ai-setup 진입점의 jarvis 안내(원래 AI-66·68·69 는 ai-setup 에 남는다).

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| SETUP-3 | WHEN setup 모듈의 status·plan 을 실행하면 THE SYSTEM SHALL 홈 아래 아무것도 쓰지 않고, ai-setup 의 `--stages all`(agentsmd 제외)과 같은 쓰기 액션을 계획한다. 하네스는 claude·codex 와 영수증의 하네스, 줄바꿈 키는 기록된 값을 쓴다 | `tests/test_setup_module.py` | GREEN (단위; s2 읽기 전용 실행 0.4초) |
| SETUP-4 | WHEN 영수증에 기록된 설정(줄바꿈 키·번들 스킬·출력 스타일·mod·OMC HUD)의 배포본이 마지막으로 적용한 판과 다르면 THE SYSTEM SHALL 그 워커를 다시 적용하는 단계를 계획하고(사용자가 고친 파일은 워커가 그대로 둔다, 플러그인을 설치할 수 있는 워커는 install 위험), 다시 적용이나 그 설정의 설치가 성공하면 그 판을 기록해 다시 계획하지 않는다 | `tests/test_setup_module.py::test_recorded_settings_are_refreshed_once_per_bundle`, `test_a_setting_installed_by_the_plan_is_not_refreshed_right_after` | GREEN (단위. v1.6.0 s2 실측: core API 로 plan → 같은 plan_id 로 apply, 다시 적용 5단계 done, 설정 파일 그대로(이미 최신), 다시 plan 하면 단계 없음) |
| SETUP-5 | WHEN setup 계획을 apply 하면 THE SYSTEM SHALL 계획이 바뀌었으면 아무것도 하지 않고 stale, 아니면 액션을 하나씩 실행하되 한 단계(stage)에서 실패하면 그 단계의 나머지와 그 단계에 기대는 뒤 단계만 건너뛰고 독립인 단계와 다시 적용은 계속하며 결과를 단계마다 낸다 | `tests/test_setup_module.py::test_apply_continues_past_a_failed_stage_and_rejects_a_changed_plan`, `test_a_failed_stage_skips_the_stages_that_need_it_but_not_the_independent_ones` | GREEN (단위) |
| SETUP-6 | WHEN setup 을 uninstall 하면 THE SYSTEM SHALL dry-run 의 plan_id 가 맞을 때만 엔진의 제거 계획(영수증에 기록한 것만)을 실행하고, jarvis 게이트웨이 유닛과 Tailscale 은 지우지 않는다 | `tests/test_setup_module.py::test_uninstall_needs_the_dry_run_plan_id_and_keeps_jarvis_and_tailscale` | GREEN (단위) |
| SETUP-7 | WHEN 선택한 하네스가 이미 설치되어 있으면 THE SYSTEM SHALL 설치 명령을 호출하지 않고 영수증에 기록하지 않는다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-14 |
| SETUP-8 | WHEN 선택한 하네스가 없으면 THE SYSTEM SHALL 현재 플랫폼의 카탈로그 설치 명령을 호출하고 성공하면 영수증에 기록한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-15 |
| SETUP-9 | WHEN 하네스의 선행 조건(예: Node 버전)이 맞지 않으면 THE SYSTEM SHALL 설치하지 않고 필요한 조건을 안내한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-16 |
| SETUP-10 | WHEN `--dry-run` 과 함께 설치 단계를 실행하면 THE SYSTEM SHALL 호출할 명령만 출력하고 아무것도 실행하지 않는다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-17 |
| SETUP-11 | WHEN `--install-harness` 를 `--uninstall` 과 함께 실행하면 THE SYSTEM SHALL 영수증에 기록된 항목만 기록된 설치 방식의 제거 명령으로 지우고, 원래 있던 항목과 설정·인증 디렉터리는 남긴다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-34 |
| SETUP-12 | WHEN setup 모듈이 sudo 가 필요한 단계를 계획·실행하면 THE SYSTEM SHALL 명령 전체를 계획에 보이고, 모든 sudo 를 `sudo -n` 으로 불러 비밀번호를 기다리지 않으며, 비밀번호 없는 sudo 를 쓸 수 없으면 실행하지 않고 건너뛴 것으로 보고(터미널에서 칠 명령을 남김)한 뒤 나머지 단계를 계속한다 | `tests/test_setup_module.py::test_sudo_steps_never_prompt_and_are_skipped_without_passwordless_sudo` | GREEN (단위. 원래 AI-35 의 "TTY 가 아니면 건너뜀"을 사용자 결정(2026-11-01 "sudo 단계도 안전하게 진행할 대책")으로 "비밀번호 없는 sudo 가 없으면 건너뜀"으로 바꿈. 옮긴 setup.sh 를 터미널에서 직접 부르는 경로는 2026-11-01 가짜 TTY 실측: git 이 없는 PATH 에서 `setup.sh --stages base` 가 TTY 를 붙인 채 `sudo apt-get install -y git` 을 실행) · 원래 AI-35 |
| SETUP-13 | WHEN 옮긴 `setup.sh`·`setup.ps1` 을 인자 없이 실행하면 THE SYSTEM SHALL 아무것도 설치하지 않고 jarvis setup 모듈(core 의 AI 셋업 화면)을 안내한 뒤 종료 코드 1 로 끝낸다 | `tests/test_keybinds.py::test_ci_skips_onboarding_and_installs_all_with_shift_enter`, bats `setup.sh with no args points to the jarvis setup module …`, Pester `no arguments points to the jarvis setup module …` | GREEN (단위. 온보딩을 옮기지 않아 종료 코드가 0 에서 1 로 바뀜) · 원래 AI-36·AI-42 |
| SETUP-14 | WHEN tools 단계나 `--stages all --uninstall` 을 실행하면 THE SYSTEM SHALL Orca 를 설치·제거하지 않고 영수증의 `tool_installs.orca`·`tool_installs.orca_server` 를 그대로 둔다(jarvis 의 Orca 모듈이 같은 키를 읽고 제거한다) | `tests/test_stages.py::test_tools_and_full_uninstall_leave_orca_and_its_receipt_records_to_jarvis`, bats `setup.sh --uninstall keeps the hooks …` | GREEN · 원래 AI-67 |
| SETUP-15 | WHEN 선택한 플러그인이 이미 설치되어 있으면 THE SYSTEM SHALL 다시 설치하지 않고 영수증에 기록하지 않는다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-19 |
| SETUP-16 | WHEN 플러그인의 마켓플레이스가 등록되어 있지 않으면 THE SYSTEM SHALL 마켓플레이스를 추가한 뒤 user scope 로 설치하고 둘 다 영수증에 기록한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-20 |
| SETUP-17 | WHEN `--plugins --uninstall` 을 실행하면 THE SYSTEM SHALL 영수증에 기록된 플러그인·마켓플레이스만 제거하고 원래 있던 것은 남긴다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-21 |
| SETUP-18 | WHEN 기존 `--omc` 또는 `--output-style` 로 실행하면 THE SYSTEM SHALL 이전과 같은 플러그인과 설정 변경을 만든다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-22 |
| SETUP-19 | WHEN `--skills` 를 실행하면 THE SYSTEM SHALL `sources.json` 의 선택된 스킬마다 `skills add` 를 고정 ref 와 비대화형 플래그로 호출한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-23 |
| SETUP-20 | WHEN `~/.agents/.skill-lock.json` 을 파싱할 수 없거나 version 이 3 미만이면 THE SYSTEM SHALL 외부 스킬 단계를 쓰기 전에 중단한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-24 |
| SETUP-21 | WHEN `npx` 가 없으면 THE SYSTEM SHALL 외부 스킬만 건너뛰고 번들 스킬 설치는 그대로 진행한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-25 |
| SETUP-22 | WHEN `--skills --uninstall` 을 실행하면 THE SYSTEM SHALL 영수증에 기록된 외부 스킬만 `skills remove` 로 제거하고 번들 스킬은 기존 규칙대로 되돌린다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-26 |
| SETUP-23 | WHEN `--prune-skills` 를 대화형으로 실행하면 THE SYSTEM SHALL lock 에 없는 실제 디렉터리만 후보로 보여 주고, 승인한 항목만 휴지통으로 옮기며 그 항목을 가리키던 하네스 링크를 지운다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-38 |
| SETUP-24 | WHEN `--prune-skills` 의 후보를 고를 때 THE SYSTEM SHALL lock 에 있는 스킬과 심볼릭 링크·junction 인 스킬을 후보에서 제외한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-39 |
| SETUP-25 | WHEN `--prune-skills` 를 비대화형으로 실행하면 THE SYSTEM SHALL 아무것도 옮기지 않고 exit 1 로 끝낸다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-40 |
| SETUP-26 | WHEN 플러그인 목록이 어떤 스킬을 `provides_skills` 로 제공하면 THE SYSTEM SHALL 그 스킬을 `skills` CLI 로 중복 설치하지 않는다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-27 |
| SETUP-27 | WHEN mcp 단계를 계획하면 THE SYSTEM SHALL 선택된 하네스마다 SPEC 의 CodeGraph user scope 등록 명령(`codegraph serve --mcp`)을 액션으로 내고, 패키지 액션보다 뒤에 둔다. 실제 등록 호출과 영수증 기록은 아직 이 케이스의 실행 경로가 아니다 | `test_mcp_registration_uses_codegraph_serve_for_each_selected_harness` | 미착수 (ai-setup 에서도 미착수) · 원래 AI-28 |
| SETUP-28 | WHEN 같은 이름의 MCP 서버가 이미 등록되어 있으면 THE SYSTEM SHALL 덮어쓰지 않고 영수증에 기록하지 않는다 | `tests/test_setup_module.py::test_existing_mcp_registrations_are_left_alone` | GREEN (단위; jarvis 에서 구현. claude·codex·gemini·opencode 설정만 본다) · 원래 AI-29 |
| SETUP-29 | WHEN OpenCode 설정에 MCP 를 등록하면 THE SYSTEM SHALL `mcp.<name>` 키만 추가하고 나머지 키·플러그인 항목을 보존하며, 파싱할 수 없으면 쓰기 전에 멈춘다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-30 |
| SETUP-30 | WHEN `--mcp --uninstall` 을 실행하면 THE SYSTEM SHALL 영수증에 기록된 하네스·서버 쌍만 제거한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-31 |
| SETUP-31 | WHEN 새 단계를 설치한 뒤 같은 명령을 다시 실행하면 THE SYSTEM SHALL 추가 변경 없이 끝난다 (멱등) | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-32 |
| SETUP-32 | WHEN 새 단계가 하네스 설정을 쓸 때 다른 도구가 넣은 훅·플러그인 항목이 있으면 THE SYSTEM SHALL 그 항목을 그대로 보존한다 | – | 미착수 (ai-setup 에서도 미착수) · 원래 AI-33 |
| SETUP-33 | WHEN 전체 단계 선택을 계획하면 THE SYSTEM SHALL CodeGraph npm 패키지 액션을 MCP 등록보다 앞에 두고 graft 또는 Trail 설치 명령을 내지 않는다 | `test_full_selection_orders_codegraph_package_before_mcp` | GREEN · 원래 AI-41 |
| SETUP-34 | WHEN 같은 단계 선택을 첫 계획이 만든 상태에 다시 계획하면 THE SYSTEM SHALL 추가 쓰기 액션을 내지 않는다 | `test_second_plan_against_post_state_does_not_write` | GREEN · 원래 AI-43 |
| SETUP-35 | WHEN Jarvis 역할을 고르지 않으면 THE SYSTEM SHALL Jarvis 설치를 계획하지 않고, Tailscale 제거는 logout 을 포함하지 않으며, 기록하지 않은 Jarvis 유닛은 지우지 않는다 | `test_jarvis_herdr_and_setcap_follow_explicit_choice`, `test_uninstall_removes_only_recorded_units_and_packages` | GREEN · 원래 AI-44 |

## 하네스 갱신

ai-setup 의 update-ai(`res/update/update_ai.py`)를 옮긴 것이다. 아래 HARNESS-4 부터는 옮긴 엔진의 케이스이고, 테스트는 원본 그대로인 `tests/test_update_ai.py`·`tests/test_npm_release.py` 다.

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| HARNESS-1 | WHEN harness 모듈의 status·plan 을 실행하면 THE SYSTEM SHALL 갱신 명령을 부르지 않고 설치된 것의 설치본·최신 판을 읽어, 최신이 아닌 것만 `update` 위험의 단계로 계획하고(최신을 확인할 수 없으면 보고만 한다), codex 관리형 데몬·실행 중 서버가 설치된 CLI 보다 오래되면(더 새로우면 두기) 데몬을 다시 띄우는 단계를 승인이 필요한 `config` 로 계획한다. codex 의 최신 판은 GitHub 최신 릴리스 태그로 읽는다 | `tests/test_harness_module.py`, `tests/test_update_ai.py::test_codex_latest_is_read_from_its_github_release_tag` | GREEN (단위; s2 읽기 전용 실행 6초, gemini 0.65.0 → 0.66.0 발견. 4대 읽기 전용 실측에서 데몬이 CLI 보다 새 기기의 거꾸로 된 단계를 찾아 고침. v1.6.0 배포 뒤 core 로 9대 status 를 모두 읽음) |
| HARNESS-2 | WHEN harness 계획을 apply 하면 THE SYSTEM SHALL 계획이 바뀌었으면 stale, 아니면 승인한 데몬 재시작만 `--allow-interrupt` 로 따로 돌리고 나머지 대상은 그 플래그 없이 `update-ai --only` 로 올리며, 결과는 다시 읽은 판으로 판정하고 세션 때문에 보류한 대상은 deferred 다 | `tests/test_harness_module.py` | GREEN (단위. v1.6.0 s2 실측: core API 로 apply, gemini 0.65.0 → 0.66.0 done. v1.6.2 8대 실측: 7대 done·다시 plan 단계 없음. 관리형 설치 없이 Orca 가 띄운 app-server 만 있는 pit5 는 데몬 단계를 보고만 하게 고침(v1.6.3)) |
| HARNESS-3 | WHEN 게이트웨이처럼 로그인 셸의 PATH 가 없는 곳에서 돌면 THE SYSTEM SHALL update-ai.sh 와 같이 사용자 bin·nvm 의 node(높은 판 먼저)·Homebrew 를 PATH 앞에 둔다 | `tests/test_harness_module.py::test_user_path_puts_user_bins_and_the_newest_nvm_first` | GREEN (단위) |
| HARNESS-4 | WHEN `update-ai --check` 를 실행하면 THE SYSTEM SHALL 현재·최신 버전만 보고하고 업데이트 명령을 호출하지 않는다 | `test_check_only_reads_versions_and_never_updates` | GREEN · 원래 AI-7 |
| HARNESS-5 | WHEN 카탈로그의 도구가 설치되어 있지 않으면 THE SYSTEM SHALL 그 도구를 건너뛰고 설치 명령을 호출하지 않는다 | `test_missing_tools_are_skipped_without_installing` | GREEN · 원래 AI-8 |
| HARNESS-6 | WHEN 대상의 세션이 실행 중이고 그 설치가 버전별 배치(native 버전 디렉터리, npm-release, brew)이면 THE SYSTEM SHALL `--allow-interrupt` 없이 업데이트한다 | `test_running_harness_with_a_versioned_install_is_updated_without_allow_interrupt`, `tests/test_npm_release.py::test_update_ai_updates_an_npm_release_install_while_its_session_runs` | GREEN (Linux; Windows 미검증) · 원래 AI-9 |
| HARNESS-7 | WHEN 다른 update-ai 가 lock 을 잡고 있으면 THE SYSTEM SHALL 아무것도 업데이트하지 않고 종료하며, lock 의 PID 가 죽어 있으면 lock 을 회수하고 진행한다 | `test_live_lock_stops_the_run_and_dead_lock_is_reclaimed` | GREEN · 원래 AI-10 |
| HARNESS-8 | WHEN `--only a,b` 를 주면 THE SYSTEM SHALL a, b 만 업데이트한다 | `test_only_updates_the_chosen_targets`, `test_unknown_only_target_is_rejected` | GREEN · 원래 AI-11 |
| HARNESS-9 | WHEN 어느 한 항목이라도 업데이트에 실패하면 THE SYSTEM SHALL 나머지를 계속 진행하고 결과 표를 출력한 뒤 exit 1 로 끝낸다 | `test_one_failure_does_not_stop_the_rest_and_exits_1` | GREEN · 원래 AI-12 |
| HARNESS-10 | WHEN npm 으로 설치된 하네스를 업데이트하면 THE SYSTEM SHALL 그 실행 파일을 설치한 npm(같은 node 접두 경로)을 사용한다 | `test_npm_harness_is_updated_with_the_npm_that_installed_it` | GREEN · 원래 AI-13 |
| HARNESS-11 | WHEN 실행 중 세션이 쓰는 설치가 버전별 배치가 아니면(npm -g, winget portable) THE SYSTEM SHALL 그 대상을 바꾸지 않고 보류로 보고하며 옮기는 방법을 안내한다 | `test_running_session_of_an_in_place_npm_install_defers_the_update`, `test_session_detection` | GREEN (Linux; Windows 미검증) · 원래 AI-56 |
| HARNESS-12 | WHEN npm-release 로 설치하거나 업데이트하면 THE SYSTEM SHALL 새 버전을 버전 디렉터리에 완성한 뒤에만 `current` 를 원자적으로 바꾸고, 이미 있는 셔임은 다시 쓰지 않는다 | `tests/test_npm_release.py::test_update_switches_current_only_after_the_new_version_is_complete_and_never_rewrites_the_shim` | GREEN (Linux; Windows 미검증) · 원래 AI-57 |
| HARNESS-13 | WHEN npm-release 가 이전 버전을 정리하면 THE SYSTEM SHALL current, previous, 그리고 실행 중인 프로세스가 쓰는 버전을 남기고, 프로세스 목록을 읽을 수 없으면 아무것도 지우지 않는다 | `tests/test_npm_release.py::test_prune_keeps_current_previous_and_versions_a_running_session_uses` | GREEN (Linux; Windows 미검증) · 원래 AI-58 |
| HARNESS-14 | WHEN npm-release 설치 시 같은 패키지의 npm -g 사본이 있으면 THE SYSTEM SHALL 그 사본을 쓰는 프로세스가 없을 때만 제거하고, 있으면 남긴 뒤 보고한다 | `tests/test_npm_release.py::test_old_global_copy_is_removed_only_when_no_process_uses_it` | GREEN (POSIX 만; Windows 는 셔임 위치가 달라 해당 테스트 건너뜀) · 원래 AI-59 |
| HARNESS-15 | WHEN 셔임 경로에 ai-setup 셔임도 아니고 그 패키지의 npm 링크도 아닌 파일이 있으면 THE SYSTEM SHALL 덮어쓰지 않고 실패한다 | `tests/test_npm_release.py::test_a_file_ai_setup_did_not_write_at_the_shim_path_is_never_overwritten` | GREEN (Linux; Windows 미검증) · 원래 AI-60 |
| HARNESS-16 | WHEN npm-release 대상을 uninstall 하면 THE SYSTEM SHALL 셔임과 사용 중이 아닌 버전만 지우고, 남긴 버전을 보고한다 | `tests/test_npm_release.py::test_uninstall_removes_the_shim_and_keeps_only_a_version_still_in_use` | GREEN (Linux; Windows 미검증) · 원래 AI-61 |
| HARNESS-17 | WHEN brew 로 설치된 대상을 업데이트하면 THE SYSTEM SHALL `HOMEBREW_NO_INSTALL_CLEANUP=1` 을 주고 실행한다 | `test_brew_update_keeps_the_old_version_for_running_sessions` | GREEN (단위; macOS 실측 전) · 원래 AI-62 |
| HARNESS-18 | WHEN `update-ai` 가 확인한 설치본이 최신 릴리스와 같거나 더 새로우면 THE SYSTEM SHALL `--check` 와 갱신 뒤 검사 모두에서 최신으로 판정한다 | `tests/test_update_ai.py::test_an_install_newer_than_the_latest_release_counts_as_up_to_date` | GREEN (단위) · 원래 AI-65 |

## AGENTS.md

| ID | 케이스 | 테스트 | 상태 |
| --- | --- | --- | --- |
| AGENTSMD-1 | WHEN `python -m jarvis agentsmd [--harness L] [--force] [--dry-run]` 를 실행하면 THE SYSTEM SHALL 지금 폴더에 ai-setup 의 setup_agentsmd 워커와 같은 결과(고른 하네스의 AGENTS.md·CLAUDE.md, 있는 파일은 --force 없이 두기)를 만든다 | `tests/test_setup_module.py::AgentsMdCliTest`, bats `setup_agentsmd.sh …` 10건, Pester `project instructions harness selection` | GREEN (단위. kgolf(Windows) 임시 폴더 실측: dry-run 은 아무것도 쓰지 않음, claude,codex 는 AGENTS.md·CLAUDE.md(`@AGENTS.md`), 모르는 하네스는 종료 코드 1) |
