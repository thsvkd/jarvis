# 문서 지도

`AGENTS.md` 의 문서 구조 표가 가리키는 문서의 상태다.

- `authoritative`: 사용자가 확인한 계약이다. 코드와 어긋나면 둘 중 하나가 버그다.
- `draft`: 작성 중이다. 참고는 하되 확정 계약처럼 쓰지 않는다.
- `historical`: 당시의 결정 기록이다. 현재 동작의 근거가 아니라 배경 자료다.

| 문서 | 답하는 질문 | 상태 |
| --- | --- | --- |
| `PRD.md` | 무엇을 왜 만드는가 | `authoritative` (ai-setup 흡수 범위, 2026-10-30 K1) |
| `SPEC.md` | 모듈 계약과 Orca 서버 형태 | `authoritative` (2026-10-30 K1, K4·K10) |
| `TEST_SPEC.md` | 무엇이 완료인가 | `authoritative` (Orca 케이스, 2026-10-30 K1, K4·K10) |
| `2026-10-30-ai-setup-absorption.md` | 흡수 결정·기각한 대안·열린 문제·진행 기록 | `draft` |
| `guard-policy.md` | 훅(danger_guard·rm 셔임)이 왜 그렇게 판정하는가 | `reference` (ai-setup README 에서 옮김) |

- 완료 판정은 `TEST_SPEC.md` 하나가 소유한다.
- `SPEC.md` 는 자주 바뀌지 않는 계약만 담는다. 구조·설정·운영 방법은 `README.md` 가 소유한다.
