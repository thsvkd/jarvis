# 문서 지도

`AGENTS.md` 의 문서 구조 표가 가리키는 문서의 상태다.

- `authoritative`: 사용자가 확인한 계약이다. 코드와 어긋나면 둘 중 하나가 버그다.
- `draft`: 작성 중이다. 참고는 하되 확정 계약처럼 쓰지 않는다.
- `historical`: 당시의 결정 기록이다. 현재 동작의 근거가 아니라 배경 자료다.

| 문서 | 답하는 질문 | 상태 |
| --- | --- | --- |
| `PRD.md` | 무엇을 왜 만드는가 | `authoritative` (ai-setup 흡수 범위, 2026-10-30 K1) |
| `SPEC.md` | 모듈 계약 | `authoritative` (2026-10-30 K1, K4·K10. setup·harness 절은 2026-11-01, core 전환·SSH 기기 절은 2026-11-04) |
| `TEST_SPEC.md` | 무엇이 완료인가 | `authoritative` (2026-10-30 K1. 셋업·하네스 갱신·AGENTS.md·BOOT-2·3 은 2026-11-01, CORE-1~8·SSH-1~10 은 2026-11-04) |
| `2026-10-30-ai-setup-absorption.md` | 흡수 결정·기각한 대안·열린 문제·진행 기록 | `draft` |
| `guard-policy.md` | 훅(danger_guard·rm 셔임)이 왜 그렇게 판정하는가 | `reference` (ai-setup README 에서 옮김) |
| `setup-guide.md` | setup·harness 모듈이 무엇을 설치하고 바꾸는가(사용량에 영향을 주는 mod, 줄바꿈 키, update-ai) | `reference` (ai-setup README 에서 옮김) |
| `architecture.md` | 무엇을 수집하고 코드가 어떻게 놓여 있는가, 운영 홈 | `reference` |
| `configuration.md` | 설정 키, SSH 기기 | `reference` |
| `operations.md` | 릴리스·갱신, 접속과 접근 통제, 운영체제별 설치와 운영, 실행본 이전 | `reference` |
| `core-migration.md` | core 를 다른 기기로 옮기는 순서 | `reference` |
| `usage.md` | AI 구독 사용량 수집, 로그인 상태와 다시 로그인 | `reference` |
| `modules.md` | 모듈 CLI(plan·apply) | `reference` |
| `verification.md` | 테스트와 스냅샷 스키마 비교 | `reference` |

- 완료 판정은 `TEST_SPEC.md` 하나가 소유한다.
- `SPEC.md` 는 자주 바뀌지 않는 계약만 담는다. 구조·설정·운영 방법은 위 표의 `reference` 문서가 소유한다(2026-11-10 `README.md` 에서 옮김. `README.md` 는 소개와 빠른 시작만 담는다).
