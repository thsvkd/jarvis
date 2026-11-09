# Claude 로그인 완료 검증 계획

> 실행: 이 세션에서 직접 수정하고 검증한다. 커밋·배포·서비스 재시작은 하지 않는다.

**목표:** 웹 로그인 후 키체인·파일 불일치가 남았는데 완료로 표시하는 문제를 막는다.

**승인 근거:** 2026-11-09 사용자가 키체인 기준 복구와 USAGE-10 검증 보강·수정을 승인했다.

**설계:** macOS에서 Claude CLI 로그인과 hswap 슬롯 저장이 성공하면
`hswap claude keepalive --json`의 정상 응답까지 확인한다.
실패·시간 초과·잘못된 JSON이면 `failed, unsynced: true`로 표시한다.
기존 슬롯 저장 실패(`unsaved`)와 구분하고 hswap 자유 문구는 응답에 넣지 않는다.
jarvis는 자격 파일을 읽거나 어느 저장소가 새 것인지 추측해 자동 adopt하지 않는다.
현재 기기 복구만 사용자 승인에 따라 `--adopt keychain`으로 수행한다.

**선택 근거:** add 종료 코드만 확인하면 이번 오류를 놓친다. 로그인 저장소를
일률적으로 adopt하면 로그인 중 키체인 접근 실패로 파일에 저장된 새 자격을 잃을 수 있다.
hswap 소유의 검증과 명시적 실패 표시를 사용한다.

**제약:** unittest·임시 가짜 CLI만 사용한다. 실제 홈·키체인은 수동 진단·승인된
복구에서만 접근한다. 기존 사용자 변경과 비밀 값을 보존한다.

## 작업

- [x] `tests/test_cli_login.py`: add 성공 후 keepalive 불일치·잘못된 응답·대기·성공을 검증하는 회귀 테스트를 먼저 작성하고 실패를 확인한다.
- [x] `jarvis/cli_login.py`: macOS 후속 검증, 최종 상태 게시 순서와 `unsynced` 응답을 구현한다.
- [x] `tests/test_usage_summary.mjs`, `static/app/usage.js`: 동기화 실패 안내가 슬롯 저장 실패와 구분되는지 검증하고 안내를 구현한다.
- [x] `docs/TEST_SPEC.md` USAGE-10과 `README.md`: 승인된 완료 조건과 수동 복구 조건을 기록한다.
- [x] 전체 unittest와 화면 JS 테스트를 실행하고 변경을 검토한다.

## 운영 확인

- Claude: 승인된 adopt 후 토큰을 출력하지 않고 두 저장소 일치와 keepalive 성공을 확인한다.
- Hermes: auth-watch의 과거 실패 구간, 현재 Codex 상태와 감시 스크립트 결과를 확인한다. 외부 알림을 보내는 cron 자체는 실행하지 않는다.

## 검토 초점

- CLI·add 실패 때 후속 검증이 실행되지 않는가.
- 검증 중 `done`이 먼저 노출되지 않는가.
- 종료 0이어도 실패 JSON·비정상 형식이면 실패하는가.
- macOS 이외·hswap 없는 기존 경로가 유지되는가.
- 외부 오류 원문과 로그인 코드가 응답·안내에 나오지 않는가.

## 실행 결과

- 승인된 기기 복구: adopt 종료 0·ok true. 이후 예약 keepalive도 19:12·19:17·19:22 연속 정상.
- 인증 회귀: `JARVIS_CONFIG=/dev/null python3 -m unittest tests.test_cli_login -v` 종료 0, 37건 통과.
- 화면: `node tests/test_usage_summary.mjs`, `node tests/test_jarvis_js.mjs` 각각 종료 0.
- 검토 지적: 원래 URL 유효 시간이 지나도 저장·검증 중인 작업을 재사용하도록 수정. 해당 경계 회귀 테스트의 실패→통과 확인.
- 기본 Python 전체 실행: 종료 1, 실패 19·오류 15·skip 29(psutil 미설치와 임시 경로 문제 포함).
- 기존 실행용 가상환경 전체 실행: `JARVIS_CONFIG=/dev/null TMPDIR=/private/tmp /Users/thsvkd/.local/share/jarvis/venv/bin/python -m unittest discover -s tests` 종료 1, 1,378건 중 실패 3·skip 29. 아래 3건은 변경 전 HEAD에서도 동일하게 실패했다.
  - `test_relocate.Mac.test_a_registration_spelled_in_another_case_is_relocated`
  - `test_rm_shim.Cases.test_root_and_home_are_refused[6]`
  - `test_rm_shim.Cases.test_root_and_home_are_refused[7]`
- 현재 기기의 Claude·Codex·Hermes 감시를 확인했으며, 실제 웹 OAuth 흐름은 다시 실행하지 않았다.
- 재발 시 거짓 완료를 막는 코드는 작업 브랜치에만 있다. 커밋·릴리스·서비스 배포는 하지 않았다.

## 후속 승인과 배포 계획

2026-11-09 사용자가 커밋·푸시·배포를 명시 승인했다. 인증 수정만 커밋하고, 기존 설치기 변경은 작업 폴더에 보존한다. 깨끗한 main 작업 트리에서 v1.9.10 릴리스를 만들고 현재 Mac mini의 jarvis를 업데이트·재시작한 뒤 서비스 버전과 60초 안정화 상태를 확인한다.
