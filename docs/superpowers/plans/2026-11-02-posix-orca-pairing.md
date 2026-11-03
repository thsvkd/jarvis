# POSIX Orca 페어링 구현 계획

**Goal:** 기존 SPEC·ORCA-1·2·3·9·11·13에 따라 POSIX 신규 설치의 데스크톱 페어링을 완성한다.

**Architecture:** ai-setup에서 옮긴 페어링 워커를 재사용한다. 읽기 전용 계획과 복구 기록은 작은 POSIX 페어링 모듈이 맡고, 기존 Orca 계약 층이 페어링 → 직접 실행 서비스 작성·활성화 순서로 조정한다.

**Tech Stack:** Python 표준 라이브러리, unittest. 새 의존성 없음.

**Spec:** `docs/SPEC.md` Orca 모듈, `docs/TEST_SPEC.md` ORCA-1·2·3·9·11·13.

## 제약과 결정

- 확정된 설계의 구현을 이어간다. SSoT 케이스를 추가하거나 변경하지 않는다.
- 실제 사용자 홈·Orca 서비스·설치기·페어링을 자동 테스트에서 쓰지 않는다.
- 기존 서버·설정·사용자 환경은 덮어쓰거나 몰래 중지하지 않는다.
- 페어링 값은 출력·로그·영수증·계획에 넣지 않는다. 서버 공개 키로 기존 연결을 식별한다.
- 임시 페어링 서버를 종료한 뒤에만 영구 서비스를 활성화한다. 연결 확인에 실패하면 복구 기록을 남긴다.
- 새 서비스는 `serve --supervise` 직접 실행 형태다. 기존 Python 러너를 POSIX에 재도입하지 않는다.
- 기존 클라이언트 프로필은 uninstall에서도 보존한다(ai-setup 동작 유지). 불완전한 페어링 체크포인트는 섣불리 버리지 않는다.
- 사용자 승인 없이 커밋·푸시·릴리스·배포·실제 서비스 중지·실제 페어링은 하지 않는다.
- 현재 작업 트리의 다른 세션 변경과 겹치지 않는 파일만 수정한다.

## 검토 초점

- 기존 서버가 실행 중이고 클라이언트 연결이 없음: 계획에 blocked, 서버를 중지하지 않음.
- environment add 이후 영수증 기록이 끊김: 이름과 사전 ID 목록으로 생성한 연결을 찾아 중복 생성 방지.
- 서비스 활성화·접속 확인 실패: 페어링 ID와 서비스 다이제스트를 남겨 재실행 가능.
- 계획 이후 클라이언트 설정·서버 키 변경: plan_id가 달라져 stale.
- 잘못된 JSON·연결된 프로필·tailnet 밖 주소: 쓰기 전에 거절하고 비밀 값을 내보내지 않음.

## Task 1: 신규 설치와 복구

Files: 새 `jarvis/orca_pairing.py`, 수정 `jarvis/orca_module.py`·`jarvis/orca_serve.py`, 새 `tests/test_orca_posix_pairing.py`.

- [x] 가짜 Orca CLI와 임시 HOME으로 페어링 등록 → 서비스 파일 → 접속 확인의 동작 테스트 작성.
- [x] `JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests -p test_orca_posix_pairing.py -v`로 RED 확인.
- [x] 읽기 전용 프로필 검사, 기존 연결 수용, 영수증 체크포인트·복구, 명시적인 pairing 계획 단계 구현.
- [x] 서비스 작성이 페어링 기록을 보존하고 접속 확인 뒤에만 configured를 기록하도록 연결.
- [x] 같은 명령으로 GREEN 확인.

## Task 2: 기존 설치·오류 경로와 회귀

Files: `tests/test_orca_posix_pairing.py`, 기존 `tests/test_orca_module_contract.py`의 기초 픽스처.

- [x] 기존 연결·실행 중 서버·변경된 계획·부분 실패·잘못된 프로필·주소·uninstall 보존 테스트 작성 및 RED 확인.
- [x] 기존 계약 픽스처를 이미 페어링된 설치로 명시하고 새 경로를 검증.
- [x] Orca 관련 단위 테스트, 전체 unittest, 공용 JS 검증 실행.
- [x] diff와 원본 워커 동작을 대조하고 결과·실측이 남은 범위를 흡수 진행 기록에 적기.

## 근거

공식 Orca main `3aa633d389e62128bfdb1c5405a74a5b5c2f19de`:
`src/shared/runtime-environments.ts`(version 1, endpoints[].publicKeyB64),
`src/shared/runtime-environment-store.ts`(같은 서버 키 식별),
`src/cli/handlers/environment.ts`·`src/cli/runtime/environments.ts`(environment list/add).
키 비교는 같은 커밋의 `src/shared/runtime-environment-host-key-match.ts`와 같이 padded·URL-safe 표기를 정규화한다.

## 실행 기록

- 작업 시작: 기존 계약과 원본 페어링 워커를 확인. 실제 기기 변경 없음.
- RED→GREEN: 신규 설치 누락, 등록 뒤 ID 기록 중단, 활성화·접속 확인 실패, 막힌 계획의 current/done 오판을 재현하고 수정.
- 별도 리뷰 반영: 소유 서비스의 adopted 전이, loaded/inactive Linux의 활성화 재시도, 같은 키의 다른 Base64 표기, 살아 있는 임시 서버의 완료 오판, 사라진 서비스 재작성. 각 재현을 회귀 테스트에 추가.
- 승인한 서비스 재작성 주소가 바뀌면 stale인지 별도 RED→GREEN 확인.
- `JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests -p test_orca_posix_pairing.py -v`: 종료 0, 신규 25건 통과. 실제 워커와 가짜 CLI가 임시 HOME의 프로필·서비스·영수증을 읽고 쓴다. OS 서비스 명령만 대체한다.
- `JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests`: 종료 0, 1,186건 중 1,175 통과·11 건너뜀. 기존 `test_npm_release.py::test_update_ai_updates_an_npm_release_install_while_its_session_runs`의 BufferedReader ResourceWarning이 관찰되었고, 그 파일만 재실행해도 같은 경고와 종료 0을 확인했다(이번 수정 대상 아님).
- `node tests/test_jarvis_js.mjs`: 종료 0, `ok`. `git diff --check`: 종료 0.
- 임시 코드 사본의 변이 5종(등록 생략, 접속 확인 생략, 소유 서비스 수용 처리, blocked 결과 누락, 서비스 주소 결박 제거)을 관련 테스트가 모두 assertion 실패로 잡음. 원본 작업 트리는 변이하지 않음.
- 실제 Linux/macOS 서비스·실제 페어링·새 Mac/Windows의 전체 설치 실측, 커밋·릴리스·배포는 하지 않음. TEST_SPEC의 상태 문구는 사용자 승인 없이 수정하지 않음.
