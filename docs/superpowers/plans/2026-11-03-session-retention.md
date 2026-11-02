# Session Retention Implementation Plan

> 실행은 승인된 설계에 따라 현재 세션에서 진행한다. 커밋과 실제 기기 적용은 별도 사용자 지시가 필요하다.

**Goal:** Claude Code 로컬 대화의 장기 보존 설정을 Jarvis setup의 plan/apply/status/uninstall에 통합한다.

**Architecture:** setup 계약 층에서 별도 보존 설정 관리자를 호출한다. 계획에 설정과 영수증의 상태 및 코드 다이제스트를 봉인하고, 다른 setup 워커보다 먼저 실행한다. 사용자 설정과 Codex는 변경하지 않는다.

**Tech Stack:** Python 표준 라이브러리, unittest, 기존 setup 모듈 계약.

**Spec:** `docs/superpowers/specs/2026-11-03-session-retention-design.md`

## 제약과 검토 사항

- 값은 36,500일이며, 기존 `cleanupPeriodDays`는 언제나 보존한다.
- 임시 홈 테스트만 사용하고 실제 설정과 실행 중 서비스는 건드리지 않는다.
- 잘못된 JSON·사용자 변경·계획 후 변경·중단된 쓰기·파일 링크를 안전하게 처리한다.
- plan/status/uninstall dry-run은 읽기 전용이며 Codex 기록을 조작하지 않는다.
- 영수증과 설정의 다른 키를 보존하고, Jarvis가 추가한 값만 제거한다.

## 작업

1. [x] 임시 홈에서 plan/apply, 기존 값, 손상된 파일, stale plan, 제거 및 재실행을 검증하는 실패 테스트를 먼저 작성한다.
2. [x] 보존 설정 관리자와 setup 계약 통합을 구현하고 테스트를 통과시킨다.
3. [x] 승인된 수용 기준을 `docs/SPEC.md`, `docs/TEST_SPEC.md`와 사용자 문서에 반영한다.
4. [x] 관련 테스트와 전체 unittest suite를 실행하고 diff와 실제 버전의 설정 수용 가능성을 별도로 확인한다.
