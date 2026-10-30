# TEST_SPEC

> 상태: 확정(2026-10-30 사용자 확인 K1, ORCA-8 은 사용자 결정으로 고침, Windows 갱신 방식 K4·K10 은 5라운드). 목록의 추가·수정·삭제는 사용자만 닫는다.
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
