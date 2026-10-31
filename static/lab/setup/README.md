# Jarvis 디자인 랩 05 · AI 셋업

ai-setup 에서 옮겨 오는 모듈(Orca·doctor, 아직 ai-setup 이 관리하는 훅·하네스·스킬·플러그인·MCP)을 core 홈에서 보고 실행하는 화면의 배치 시안이다.
구조는 랩 04(`../usage/`)와 같다. 갤러리 `/lab/setup/`, 미리보기 `/lab/setup/preview.html?v=a|b|c`(`&embed=1` 은 상단 띠를 숨긴다).
운영 토큰·셸·카드(`/app/tokens.css`·`shell.css`·`bento.css`)와 상세 서랍(`.sheet`)을 그대로 쓰고, 이 기능의 조각만 `setup.css`(접두어 `su-`)에 더한다.

## 시안

| 시안 | 출발점 | 계획·실행이 열리는 곳 | 고려할 점 |
| --- | --- | --- | --- |
| A 기기 × 모듈 행렬 | 요약 칩, 승인 대기, 9행 × 7열 표(옮긴 모듈 / 아직 ai-setup 이 관리 열 묶음) | 누른 칸의 행 바로 아래 | 모듈이 늘면 열이 넓어진다. 옮기지 않은 열은 지금 정보가 거의 없다 |
| B 모듈별 탭 | 모듈 탭(할 일 수), 모듈 요약·분포 막대 | 기기 목록의 그 행 안. Orca 는 '할 일 있는 기기 모두 계획 세우기', doctor 는 발견 종류별 묶음 | 한 기기의 여러 모듈은 탭을 오가야 보인다 |
| C 할 일 먼저 + 기기 서랍 | KPI 4개, 승인 대기, 방금 실행한 작업, 할 일, 최근 결과, 기기 타일 | 오른쪽 서랍(모바일은 아래 시트), 운영 서비스 상세와 같은 `.sheet` | 기기 전체를 한 표로 비교할 수 없다 |

세 시안이 같은 조각을 쓴다: 진행 단계(상태 → 계획 → 승인 → 실행 → 결과), 계획(단계별 위험 등급과 자동/승인 필요, 막힌 단계와 이유, `plan_id` 앞 12자), 결과 배너(`done`·`current`·`deferred`·`stale`·`failed`)와 단계별 결과(`ok`·`skipped`·`deferred`·`failed`), doctor 발견 항목.

## 샘플과 시뮬레이션 (`data.js`)

모양은 `docs/SPEC.md` 의 모듈 계약을 따른다. 실제 서버 값이 아니며 실행은 시뮬레이션이다. 새로고침하면 처음 상태로 돌아간다.

- 위험 등급: `read`·`update` 는 자동, 나머지는 승인 필요. 승인 필요 단계가 하나라도 있으면 계획 전체(`plan_id`)가 승인 대기로 간다(D4·D7).
- 처음 승인 대기: pit5(sudo), pi5-s3(sudo + linger `config`).
- 결과를 정해 둔 기기:

| 기기 | 계획 | 결과 |
| --- | --- | --- |
| s2 | 매니페스트 확인 하나 | `current` |
| pit5 · pi5-s3 · cm5-1(설치) | 승인 필요 | `done` |
| thsvkd-ubuntu-1 | 승인 필요 | 처음 `deferred`(열린 터미널 2개), 다시 시도하면 `done` |
| kgolf (Windows S4U) | 읽기·갱신뿐이라 바로 실행 | 처음 `stale`(그사이 1.4.245 로 바뀜), 다시 세우면 `current` |
| thsvkd-kgolf-macmini | 재구성, 페어링 단계 `blocked` | `failed` |
| thsvkd-mac · thsvkd-win | 연결 안 됨 | 계획을 세울 수 없음 |

- doctor 발견 종류: 설치되지 않은 하네스(설치 명령), PATH 중복(우선 경로), 은퇴한 훅이 남은 설정, 평문 시크릿(파일:줄과 키 이름만, 값 없음), lock 밖의 스킬. 문구와 경로는 ai-setup `res/doctor/doctor.py` 의 점검을 따랐다.
- `plan_id` 는 sha256 이 아니라 모양만 같은 샘플 해시다. Orca CLI 처럼 확인하지 않은 명령은 `argv` 에 `<…>` 자리표시자로 둔다.

선택·의견은 `jarvis.lab05.` localStorage, 테마·선택한 칸·탭은 sessionStorage 에만 저장한다. 운영 설정 키는 건드리지 않는다.

## 검사

```sh
node tools/shot.mjs "http://127.0.0.1:9300/lab/setup/preview.html?v=a" <출력> --sizes 1440x900,390x844 --full --wait 3000 [--dark]
```

흐름 상태는 `--eval` 로 버튼을 눌러(`[data-act=plan|request|approve|run|close][data-id=<기기>]`) 만든다. `--full` 은 eval 전에 높이를 재므로 펼친 뒤의 화면은 스크롤 후 뷰포트 캡처로 본다.
