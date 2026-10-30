# ai-setup 흡수 설계

상태: 2026-10-30 사용자 확인(5라운드에서 K4·K10 까지 닫음). 작성자는 ai-setup-49 세션이고, 검토와 구현은 jarvis-a3 세션이 맡는다. jarvis-a3 의 검토 의견 R1~R11 을 반영했다.

2026-10-30 사용자가 "둘이서 협업하여 자율적으로 진행"하라고 지시했다. 사용자 확인 전까지 두 세션은 다음 규칙을 지킨다.
- 하지 않는 것: 커밋, push, 릴리스, 실제 기기의 Orca 서비스 중지·재시작·재설치(두 세션이 Orca 터미널 안에서 돌 수 있다), 실제 페어링, ai-setup 코드 삭제, TEST_SPEC 케이스 확정.
- 하는 것: 이 문서 수정(ai-setup-49), jarvis 의 SSoT 골격과 Orca 모듈 이식·단위 테스트(jarvis-a3), 이식한 코드의 적대적 리뷰(ai-setup-49). 모두 작업 트리에만 둔다.
- 잠정값: O8 은 ORCA-8 이다. O11 은 S4U 를 이미 쓰는 Windows 기기는 S4U 로, 데스크톱 전용 기기는 jarvis 의 기존 공식 업데이터 경로로 둔다(코드를 지우지 않으므로 되돌리기 쉽다). O7 은 기존 유닛의 페어링 플래그를 바꾸지 않고 형태 판정에서 뺀다.
- 아침 보고는 이 문서 끝의 "진행 기록" 절에 적는다. 그 절은 두 세션이 함께 쓰고, 나머지 절은 ai-setup-49 가 쓴다.
결정의 원본은 grill-board 답변이다: `~/.claude/tools/grill-board/answers/ai-setup-2026-10-30-jarvis-merge{,-r2,-r3,-r4,-r5}.json`.

## 1. 배경

ai-setup(`thsvkd/ai-setup`)과 jarvis(`thsvkd/jarvis`)가 같은 Orca를 서로 다른 형태로 관리했다.

| 항목 | ai-setup | jarvis |
| --- | --- | --- |
| Linux 서버 | `ai-setup-orca.service`: Python 러너가 `orca serve` 를 실행 | `orca-serve.service`: `orca-ide serve --supervise` 를 바로 실행 |
| macOS 서버 | `com.ai-setup.orca-server` LaunchAgent가 Python 러너를 실행 | ProgramArguments에 `serve` 가 있는 LaunchAgent |
| Windows 서버 | S4U 예약 작업과 러너. 앱을 `app\build-<hex>` 에 복사해 실행 | 서비스 없이 데스크톱 앱이 서버를 겸함 |
| 갱신 | `update-ai orca` | jarvis 갱신기(데몬 연속성 대조, macOS·Windows는 공식 업데이터 RPC) |
| 프로필 | 서버 `~/.config/orca`, 데스크톱 `~/.config/orca-desktop` | 같은 두 경로를 읽음 |

두 형태가 공존하자 서로를 피하는 예외가 생겼다(jarvis `0dcf2de`, ai-setup `1853815`). 사용자는 "같은 Orca 를 설치했으면 같은 형태여야 하고, 이대로면 유지보수가 너무 어렵다"고 판단했다.

## 2. 결정

| ID | 결정 |
| --- | --- |
| D1 | ai-setup의 모든 기능을 jarvis로 흡수한다. |
| D2 | jarvis는 한 기기에 상주하며 사용자와 소통하는 유일한 에이전트다. 하위 에이전트는 보통 사용자와 직접 소통하지 않는다(사용자가 원하면 직접 소통할 수 있다). 에이전트 리팩토링은 이 문서 밖에서 따로 설계한다. |
| D3 | 부트스트랩은 jarvis 설치기가 맡는다. jarvis를 설치할 때 jarvis가 동작하는 데 필요한 하네스 바이너리와 셋업을 함께 설치한다. |
| D4 | 원격 작업은 Claude Code auto mode처럼 다룬다. 안전한 작업은 바로 실행하고, 막힌 작업만 사용자 승인을 받는다. |
| D5 | 판정은 규칙을 먼저 적용하고, 규칙 밖의 작업(하위 에이전트가 즉석에서 만든 명령 등)은 분류기에 맡긴다. Orca 시범에서는 규칙만 쓴다. |
| D6 | 규칙 엔진은 하나다. ai-setup의 `danger_guard` 를 jarvis 라이브러리 모듈로 옮겨, jarvis 작업 판정과 하네스 훅이 같은 엔진을 쓴다. 엔진은 표준 라이브러리만 쓰는 형태를 유지한다(훅은 하네스 세션 안에서 빨리 돌아야 한다). |
| D7 | 승인은 jarvis 대화의 승인 카드와 core 홈의 "승인 대기" 목록에 함께 띄우고, 휴대폰 알림을 보낸다. 어느 쪽에서 눌러도 결과는 같고, 기록은 core에 남는다. |
| D8 | jarvis가 훅, MCP, 플러그인, output-style(Claude 전용), mods(Claude 전용), 스킬을 관리한다. |
| D9 | 모든 모듈은 `status`·`plan`·`apply`·`uninstall` 공통 JSON 계약을 따른다(`docs/SPEC.md`). |
| D10 | 영수증은 모듈만 쓴다. jarvis 본체는 읽기만 한다. 기기 쪽 사실은 기기에 남아야 jarvis 없이도 uninstall 이 안전하다. |
| D11 | 모듈을 하나씩 jarvis로 옮긴다. 테스트도 함께 옮기고, 옮긴 기능은 ai-setup에서 지운다. 어느 시점에도 한 기능은 한 곳에만 있다. |
| D12 | 순서: Orca(시범) → doctor → 훅 → 하네스 설치·갱신 → 스킬·플러그인·MCP → 나머지(output-style, mods, 줄바꿈 키, 프로젝트 지침). Orca 시범을 마친 뒤 순서를 한 번 다시 확인한다. |
| D13 | ai-setup의 SSoT·TDD 체계(AGENTS.md, `docs/PRD.md`·`docs/SPEC.md`·`docs/TEST_SPEC.md`, 적대적 리뷰, 세 OS 검증)를 jarvis에 들여온다. 첫 단계에서는 문서 골격과 Orca 케이스만 만들고, 나머지는 모듈을 옮길 때마다 채운다. |
| D14 | 작업은 두 세션이 나눠 한다. ai-setup-49는 설계 문서, 옮길 코드의 안내, 이식한 코드의 리뷰, ai-setup 쪽 삭제, 배포를 맡는다. jarvis-a3는 jarvis의 SSoT 골격, 모듈 이식·구현, jarvis 테스트를 맡는다. 커밋은 사용자 확인을 받은 뒤에 한다. |
| D15 | 관리 기기 전부(thsvkd-win 포함)에 jarvis 게이트웨이를 설치하고 Orca 상태를 같게 맞춘다. grok-bot은 접속이 되면 같은 방식으로 맞춘다. |
| D16 | update-ai는 하네스 바이너리를 갱신하는 도구다. Orca는 update-ai에서 뺀다. |

## 3. 기각한 대안

| 대안 | 기각 이유 |
| --- | --- |
| ai-setup이 Orca를 모두 관리하고 jarvis는 표시만 | jarvis가 ai-setup에 의존하게 된다. 사용자가 흡수를 택했다. |
| jarvis가 ai-setup의 `orca.py --update` 를 호출(계획 v2) | 흡수가 끝날 때까지의 중간 단계로만 의미가 있고, 기기마다 ai-setup 사본을 남겨야 한다(kgolf에는 남은 체크아웃이 없다). |
| ai-setup이 없으면 jarvis 자체 갱신기로 대체 | 갱신 코드가 두 벌이 된다. |
| 승인 규칙을 하네스 훅과 따로 둠 | "안전한 작업"의 정의가 두 곳에서 갈라진다. |
| ai-setup 안에서 모든 모듈을 다듬은 뒤 한꺼번에 이전 | 그동안 Orca처럼 겹치는 기능이 두 곳에 남는다. |
| 승인을 core 홈에서만 받거나 메신저로 받음 | core만 쓰면 대화 도중 화면을 옮겨야 하고, 메신저를 쓰면 작업 내용이 외부 서비스로 나간다. |
| LLM 분류기만 사용 | 판정을 재현하고 테스트하기 어렵다. |

## 4. 모듈 계약

계약(CLI 진입점, 동작별 출력·종료 코드, `plan_id` 와 `stale`, 위험 등급, 영수증 규칙)은 `docs/SPEC.md` 의 "모듈 계약" 절이 소유한다. 같은 내용을 두 곳에 두면 갈라지므로 여기서는 반복하지 않는다. 이 절의 초안은 jarvis-a3 의 검토 의견 R1·R9·R10 을 반영한 뒤 SPEC 으로 옮겼다.

## 5. Orca 시범 모듈

### 5.1 구성 요소의 출처

| 구성 요소 | 가져올 곳 |
| --- | --- |
| 매니페스트 크기·SHA-512 검증 | 한 벌만 남긴다. ai-setup `orca.py` 와 jarvis `orca_update.parse_manifest`·`check_file` 이 같은 일을 하므로, 이식할 때 jarvis-a3 가 비교해 하나를 고르고 설치와 갱신이 모두 그것을 쓴다. 잠정 예외: 두 벌이 같은 규칙(크기·SHA-512, GitHub 호스트만 따라가는 리다이렉트)을 따르므로, 실제 기기에서 도는 Linux 갱신 경로(프록시 끄기, 전체 제한 1200초, `_apt` 가 읽을 0644 권한, `.partial` 처리)를 실측 없이 바꾸지 않으려고 통합은 실측 뒤 별도 단위로 미룬다 |
| 패키지 설치, 소유 스킬 기록, uninstall | ai-setup `res/tools/orca.py` |
| 서버 구성, 데스크톱 페어링, 서버 서비스 소유 기록 | ai-setup `res/tools/orca_server.py` |
| Windows S4U 러너와 갱신(설치기 `/S --updated`, 러너의 자동 전환) | ai-setup `res/tools/orca_server.py`, `res/update/update_ai.py` 의 `update_orca_windows` |
| Linux·macOS 갱신(데몬 연속성 대조, 공식 업데이터 RPC, 사용자 deb) | jarvis `orca_update.py`, `orca_install.py` |
| 페어링 코드 발급, 사용량, 세션 조사 | jarvis `orca_issue.py`, `orca_usage.py`, `orca_sessions.py` |
| 갱신 뒤 소유 스킬의 다이제스트 재기록 | ai-setup 로컬 브랜치 `wip/orca-skill-update` (커밋하지 않은 참고 구현, 리뷰 지적 1건 미반영: 새 Orca가 더 싣지 않는 스킬이 있으면 갱신 전체가 실패하므로 `available_skills` 와 교집합을 취해야 한다) |

### 5.2 서버 형태

- Linux: `~/.config/systemd/user/orca-serve.service` 다. `ExecStart=/opt/Orca/resources/bin/orca-ide serve --supervise --port 6768 --pairing-address <주소>`, `Environment=ORCA_USER_DATA_PATH=<서버 프로필>`, `KillMode=process`, `Restart=on-failure` 로 둔다. Python 러너는 없다. 같은 형태의 유닛이 이미 있으면(jarvis가 만든 것 포함) 실패로 처리하지 않고 영수증에 기록해 받아들인다. 형태가 다른 유닛은 바꾸지 않고 보고한다. 같은 형태인지는 실행 파일을 realpath 로 비교해 판정한다(`validate_runtime` 과 같은 규칙). 예를 들어 pi5-server2 의 jarvis 유닛은 `/home/pi/.local/bin/orca-ide` 를 실행하는데, 이 경로는 `/opt/Orca/resources/bin/orca-ide` 를 가리키는 심링크다. 페어링 플래그(`--no-pairing`, `--mobile-pairing` 등)는 O7 이 정해질 때까지 판정에서 뺀다.
- macOS: ProgramArguments가 `<앱 CLI> serve --supervise ...` 인 LaunchAgent로 둔다. Python 러너는 없다.
- ai-setup 이 예전 형태(`ai-setup-orca.service`, `com.ai-setup.orca-server`)로 만든 Linux·macOS 서버: 2026-10-29 에 확인한 결과, 이런 서버가 있는 기기는 없다(POSIX 관리 기기 7대의 영수증에 `tool_installs.orca_server` 가 없다. grok-bot 은 접속이 되지 않아 확인하지 못했다). 그래서 자동 전환 코드는 만들지 않는다. 배포 단계에서 grok-bot 을 확인하고, 그런 서버가 있으면 jarvis 설치 전에 ai-setup 의 uninstall 로 지운 뒤 새 형태로 구성한다(5.5).
- Windows: ai-setup의 S4U 예약 작업과 러너를 그대로 쓴다. Orca가 Windows에서 `--supervise` 를 지원하면 Linux·macOS와 같은 형태로 옮긴다.

### 5.3 갱신

- Linux: jarvis `orca_update.py` 의 기존 경로를 그대로 쓴다. 해시를 검증한 deb 를 받아 `orca-serve` 를 멈추고 `sudo -n apt install` 한 뒤 다시 띄우고, `orca_sessions.py` 로 PTY 세션이 이어지는지 확인한다. 5.2 의 유닛은 jarvis 가 이미 전제하는 이름(`orca-serve`)과 실행 파일(`/opt/Orca/resources/bin/orca-ide`, `validate_runtime` 이 realpath 로 대조한다)을 쓴다.
- macOS: jarvis 의 기존 경로인 Orca 공식 supervised 업데이터 RPC 에 맡긴다. 5.2 의 LaunchAgent 는 앱을 복사하지 않고 `/Applications/Orca.app` 의 CLI 를 바로 실행하므로 공식 업데이터가 그대로 적용된다. jarvis 가 macOS 갱신을 새로 구현할 필요는 없다(ai-setup 이 앱을 복사하는 것은 Windows 뿐이다).
- 갱신이 끝나면 `orca skills update --all` 로 번들 스킬을 모두 새 판으로 맞춘다(사용자 결정 K2, 2026-10-30). 맞춘 판은 jarvis 상태 폴더의 `orca-skills-version` 에 남기고, modules/orca 가 깐 스킬은 다이제스트를 다시 기록한다.
- Windows 갱신 경로는 하나만 남긴다(D11). 지금 jarvis 는 `%LOCALAPPDATA%\Programs\orca` 사용자 설치본(데스크톱 앱이 서버를 겸함)을 공식 업데이터 RPC 로 갱신하고(`orca_install.profile` 의 windows-user, `orca_update._supported`), ai-setup 은 S4U 서버가 있는 기기를 설치기로 갱신한다. 추천은 모든 Windows 관리 기기를 S4U 형태로 맞추고 jarvis 의 windows-user 갱신 코드를 지우는 것이다. 사용자 확인이 필요하다(O11).
- Windows(S4U): 설치기를 `/S --updated` 로 실행하고, 서버가 새 복사본으로 옮겨 가는지 보고한다. jarvis 예약 작업은 일반 권한이고 S4U 서버는 High 무결성이다. 그래서 일반 권한의 터미널 조회는 EPERM(`runtime_access_denied`)을 받는다(kgolf에서 실측). 이 경우 서버 전환은 러너가 스스로 하고, 모듈은 "셀 수 없음"으로 보고만 한다.
- 설치본이 최신 매니페스트보다 새로우면 최신으로 본다(내리지 않는다).
- 비대화형 sudo는 `sudo -n` 으로만 시도한다. 비밀번호가 필요하면 바로 실패하고 승인 카드로 안내한다.

### 5.4 Orca 수용 케이스 초안

jarvis `docs/TEST_SPEC.md` 에 옮길 후보다. 출처의 AI-n은 ai-setup TEST_SPEC의 행이다. 옮긴 케이스는 ai-setup TEST_SPEC에서 지운다.

| ID | 케이스 | 출처 |
| --- | --- | --- |
| ORCA-1 | WHEN Orca 모듈을 설치하면 THE SYSTEM SHALL 크기·SHA-512 를 통과한 공식 패키지만 설치하고, 서버 프로필의 서버에 데스크톱 프로필의 클라이언트를 등록하며, 패키지·스킬·서버의 소유를 영수증에 기록한다 | AI-48 |
| ORCA-2 | WHEN Linux 에서 서버를 구성하면 THE SYSTEM SHALL 5.2 형태의 `orca-serve.service` 를 만들고, 같은 형태의 기존 유닛은 받아들여 기록하며, 형태가 다른 유닛은 바꾸지 않고 보고한다 | 신규 |
| ORCA-3 | WHEN macOS 에서 서버를 구성하면 THE SYSTEM SHALL `serve --supervise` 를 직접 실행하는 LaunchAgent 를 만든다 | 신규 |
| ORCA-4 | WHEN Windows 에서 서버를 구성·실행·정지하면 THE SYSTEM SHALL ai-setup AI-49~AI-54 의 동작을 유지한다 | AI-49~AI-54 |
| ORCA-5 | WHEN Linux·macOS 에서 갱신하면 THE SYSTEM SHALL 매니페스트 크기·SHA-512 를 통과한 패키지만 쓰고, `validate_runtime`(실행본 realpath, 설치본과 실행본의 버전 일치, 유닛이 공식 설치본을 가리킴)을 통과한 경우에만 갱신하며, Linux 는 `sudo -n` 으로만 설치하고, 세션 연속성(데몬 PID·시작 시각·PTY 식별자 유지)을 확인하고 데몬 밖 터미널이 있으면 보류하며, 실패하면 서비스를 원래대로 다시 띄운 뒤 실패로 보고한다 | jarvis 기존 동작 |
| ORCA-6 | WHEN Windows 에서 갱신하면 THE SYSTEM SHALL ai-setup AI-55a~AI-55c 의 동작을 유지한다 | AI-55a~AI-55c |
| ORCA-7 | WHEN 설치본이 최신 릴리스와 같거나 더 새로우면 THE SYSTEM SHALL 최신으로 판정하고 내려받거나 설치하지 않는다 | AI-65, jarvis `orca_update.check()` 의 기존 동작 |
| ORCA-8 | WHEN Orca 를 갱신하면 THE SYSTEM SHALL 번들 스킬을 모두 새 판의 사본으로 바꾸고(`skills update --all`), 깐 스킬의 소유 기록을 다시 쓴다(K2 로 고침, 원문은 jarvis TEST_SPEC) | jarvis 단위 테스트 |
| ORCA-9 | WHEN `status`·`plan` 을 부르면 THE SYSTEM SHALL 파일·설정·패키지를 바꾸지 않고 SPEC 형식으로 답한다 | 신규 |
| ORCA-10 | WHEN `apply` 가 열린 터미널 때문에 서버 전환을 미루면 THE SYSTEM SHALL 종료 코드 2 와 그 이유를 돌려준다 | AI-64 일부 |
| ORCA-11 | WHEN uninstall 하면 THE SYSTEM SHALL 영수증에 기록한 패키지·스킬·서버만 지우고 나머지는 보존한다 | 기존 uninstall |
| ORCA-12 | WHEN Orca 모듈이 jarvis 에 들어가면 THE SYSTEM SHALL ai-setup 의 `--tools orca` 와 `update-ai orca` 가 Orca 를 바꾸지 않고 jarvis 를 안내한다 | D11, D16 |
| ORCA-13 | WHEN `apply` 가 받은 `--plan-id` 가 실행 직전에 다시 세운 계획과 다르면 THE SYSTEM SHALL 아무것도 실행하지 않고 `stale` 과 종료 코드 3 을 돌려준다 | jarvis-a3 제안(R1) |

### 5.5 순서

1. jarvis-a3가 이 문서를 검토하고, 사용자가 결정과 케이스를 확인한다.
2. jarvis-a3가 jarvis에 SSoT 골격과 Orca 케이스를 만든다.
3. jarvis-a3가 Orca 모듈을 이식·구현한다. ai-setup-49는 옮길 코드와 테스트를 안내하고, 이식한 코드를 적대적으로 리뷰한다.
4. 단위 테스트를 통과하면 pi5-s3에서 실측한다(Linux, jarvis가 만든 기존 `orca-serve` 를 받아들이는지). 이어서 kgolf에서 Windows를 실측한다.
5. jarvis 모듈이 Windows S4U 서버의 소유 기록을 넘겨받으면, jarvis-a3 가 `orca_update._ai_setup_managed`(0dcf2de)의 보류를 지운다. 이어서 ai-setup-49 가 ai-setup 의 Orca 코드·테스트·SSoT 행을 지운다.
6. 모든 관리 기기에 jarvis 게이트웨이를 설치하고 Orca 상태를 맞춘다(thsvkd-win 포함). grok-bot 은 접속되면 예전 형태 서버가 있는지 먼저 확인한다(5.2).

## 6. 열린 문제

| ID | 문제 |
| --- | --- |
| O1 | jarvis 에이전트 리팩토링(하네스, 하위 에이전트, 대화 창구)은 따로 설계한다. 이 설계가 정해질 때까지 모듈은 core 홈 화면에서 쓸 수 있게 만든다. |
| O2 | 승인 카드의 구체적인 모양, 알림 수단, 만료 처리를 정해야 한다. |
| O3 | 분류기를 무엇으로 할지(모델, 비용, 오프라인일 때의 동작) 정해야 한다. Orca 시범에는 필요 없다. |
| O4 | Windows S4U 작업을 등록하려면 관리자 권한이 필요하다. 그런데 jarvis 예약 작업은 일반 권한이다. 따라서 설치·재구성은 승격한 부트스트랩에서 하고, 일반 권한의 jarvis는 갱신과 상태 조회만 한다. 이 경계를 계약에 어떻게 드러낼지 정해야 한다. |
| O5 | 두 저장소가 모두 PUBLIC이다. 원격 실행 경로(승인, 감사 로그)의 인증을 jarvis-58의 보안 점검과 함께 설계해야 한다. |
| O6 | 영수증 파일 이름(`.claude-code-setup.json`)은 Claude 전용처럼 보인다. 지금은 호환을 위해 그대로 두지만, 바꿀지는 나중에 정한다. |
| O7 | 페어링을 하나로 정해야 한다. (1) Windows S4U 를 발급 경로에 넣는다. `orca_issue.manager()` 는 Windows 에서 데스크톱 앱만 알기 때문에, S4U 서버가 있는 기기에서 쓰면 엉뚱한 프로세스를 내린다. kgolf 는 모바일 페어링을 GUI 에서 발급한다는 기존 결정과도 맞춘다. (2) `--supervise` 와의 상호작용을 확인한다. `orca_issue` 는 유닛을 멈춘 뒤 같은 argv 로 임시 serve 를 띄우고 `_kill_tree` 로 자식까지 끝내는데, 데몬(orcad)이 임시 serve 의 자식이면 데몬과 PTY 가 함께 끊긴다. 갱신 경로는 데몬 연속성을 지키므로 두 경로의 기준을 하나로 맞춘다. (3) `orca-serve` 의 ExecStart 플래그를 맞춘다. pi5-s3 의 jarvis 유닛은 `--no-pairing` 을 쓰는데, `orca_issue.py` 는 이 플래그가 있으면 페어링 버튼을 내지 않는다. ai-setup 은 서버를 시작할 때 페어링 범위(runtime·mobile)를 정하고 `--json` 으로 ready 이벤트를 읽는다. 페어링 코드는 출력·로그에 남기지 않고, 테스트에서 실제 페어링을 하지 않으며, tailnet 허용 목록만 쓰고 루프백은 허용하지 않는다는 jarvis 의 제약 안에서 하나로 정한다. |
| O8 | jarvis 는 지금 앱이 최신이어도 `orca skills update --all` 을 실행한다. 그러면 사용자가 고친 소유 스킬과 소유하지 않은 Orca 스킬까지 바뀐다. ORCA-8 은 기록한 뒤 바뀌지 않은 소유 스킬만 바꾸게 한다. 추천은 ORCA-8 이었다. **닫힘(K2)**: 사용자가 "Orca 를 갱신할 때 관련 스킬·CLI 를 모두 갱신한다"로 정해 `--all` 을 유지한다. |
| O9 | ai-setup 테스트는 pytest(monkeypatch, tmp_path)를 쓰고, jarvis 테스트는 표준 라이브러리 unittest 만 쓴다(`JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests`). 옮기는 테스트는 unittest 로 바꾸고, 단언은 약하게 만들지 않는다. Windows 전용 테스트는 kgolf 에서 돌린다. |
| O10 | 마무리 배포는 jarvis 의 릴리스 경로를 쓴다. `tools/release.py` 가 GitHub 릴리스를 만들고, core 의 갱신 버튼이 `versions/` 로 전환한다(롤백 포함). 아직 게이트웨이가 없는 기기(thsvkd-win, grok-bot)는 처음 설치하는 경로가 따로 필요하다. |
| O11 | 닫힘(K4): Windows 는 S4U 하나로 통일하고 jarvis 의 windows-user 갱신 코드를 지웠다. |
| O8b | 지금 jarvis 기기에는 스킬 소유 기록이 없다. ORCA-8 을 그대로 적용하면, 갱신해도 번들 스킬이 옛 판에 머문다(예전의 `--all` 보다 퇴행). 받아들일 때 지금 판의 사본과 내용이 같은지로 소유를 판정하는 방법은 쓸 수 없다. pi5-server2 의 orca-cli·orchestration 이 `orca skills get` 출력과 달랐다. (A) 엄격한 ORCA-8: 갱신하지 않고 status 에 알린다. (B) 받아들일 때 번들 목록에 이름이 있는 스킬을 `skills_adopted` 로 기록하고, 예전처럼 갱신하되 uninstall 은 지우지 않는다. 추천은 B 였다. **닫힘(K2)**: `--all` 로 정해져 소유 기록이 없어도 갱신하므로 이 문제가 없어졌다. |
| O12 | 영수증 동시 쓰기(jarvis 갱신과 다른 셋업이 동시에 도는 경우)에는 잠금이 없다. |

## 7. 사용자 확인 목록 (2026-10-30 아침)

밤사이 두 세션이 잠정값으로 진행한 것과, 사용자만 닫을 수 있는 것이다. 각 항목에 추천을 붙였다.

| ID | 확인할 것 | 추천 | 결정(2026-10-30) |
| --- | --- | --- | --- |
| K1 | 이 문서의 결정 D1~D16 과 Orca 수용 케이스 ORCA-1~13(jarvis `docs/TEST_SPEC.md`)을 확정한다. | 확정 | 확정 |
| K2 | O8: 소유 스킬 가운데 기록한 뒤 바뀌지 않았고 새 판이 싣는 것만 갱신한다(ORCA-8). 예전 jarvis 는 `skills update --all` 이었다. | ORCA-8 | **기각**: `--all` 로 모두 갱신(관련 스킬·CLI 모두) |
| K3 | O8b: 지금 jarvis 기기에는 스킬 소유 기록이 없으므로, ORCA-8 만으로는 스킬이 옛 판에 머문다. (A) 그대로 둔다, (B) 받아들일 때 번들 이름을 `skills_adopted` 로 기록해 예전처럼 갱신하고 uninstall 은 지우지 않는다. | B | K2 로 없어짐 |
| K4 | O11: S4U 를 쓰는 Windows 는 S4U, 데스크톱 전용 Windows 는 jarvis 의 기존 공식 업데이터 경로로 둔다. | 잠정값대로 | 기각 → Windows 는 S4U 하나로 통일(5라운드). 데스크톱 전용 갱신 코드를 지웠다 |
| K5 | O7: 기존 유닛의 페어링 플래그(`--no-pairing` 등)는 바꾸지 않고 형태 판정에서 뺀다. 새로 쓰는 유닛에는 넣지 않는다. | 잠정값대로 | 잠정값대로 |
| K6 | POSIX 모바일 페어링 링크는 기기에 보관하지 않고 core 가 1회 발급한다(Windows 는 보관한다). ORCA-1 문구를 나눴다. | 잠정값대로 | 잠정값대로 |
| K7 | 이미 있는 같은 형태의 서버는 `adopted: true` 로 기록해 다루고, uninstall 은 지우지 않는다. 패키지는 소유로 기록하지 않는다. | 잠정값대로 | 잠정값대로 |
| K8 | 비대화형 갱신에서는 `sudo -n` 을 쓴다(ai-setup SPEC D3 "TTY 가 아니면 sudo 를 실행하지 않는다"의 예외). 비밀번호가 필요하면 바로 실패한다. | 허용 | 허용 |
| K9 | uninstall 도 dry-run 이 낸 `plan_id` 를 받아야 실행한다. 막힌 단계가 있는 apply 는 deferred 가 아니라 failed 다. | 잠정값대로 | 잠정값대로 |
| K10 | Windows 에서 서버 터미널을 셀 수 없을 때(일반 권한의 EPERM)도 전환을 300초까지 기다린다. 데스크톱 앱이 열려 있으면 강제 옵션 없이 보류한다(강제는 승인 카드가 생기면 넣는다). | 잠정값대로 | 잠정값대로(5라운드) |
| K11 | 매니페스트 검증 코드 두 벌의 통합은 실기기 실측 뒤 별도 단위로 미룬다(D11 의 잠정 예외). | 잠정값대로 | 잠정값대로 |
| K12 | 다음 단계를 허가한다: 커밋(jarvis 브랜치), pi5-s3 와 kgolf 의 실측(실제 Orca 서비스에 닿는다), ai-setup 의 Orca 코드 삭제와 ORCA-12, 릴리스, 모든 기기 배포(thsvkd-win 포함). | 순서대로 허가 | 1~5 모두 순서대로 허가 |
| K13 | jarvis 가 0dcf2de 의 보류(S4U 서버면 `update-ai orca` 로 넘김)를 지우고 S4U Windows 의 Orca 갱신을 직접 맡는다. 이 jarvis 를 배포하기 전에 ai-setup 쪽 ORCA-12(`update-ai orca` 가 Orca 를 바꾸지 않음)를 먼저 배포해야, 두 도구가 같은 기기의 Orca 를 동시에 갱신하지 않는다. | ORCA-12 를 먼저 배포 | ORCA-12 를 먼저 배포 |

아직 하지 않은 일: POSIX 새 설치의 데스크톱 페어링(P1), 옮긴 파일 메시지의 한국어화, core 화면과 승인 카드(D7), Windows 전용 테스트 8건의 kgolf 실행, 실기기 실측.

## 8. 진행 기록

두 세션이 함께 쓴다. 한 줄에 한 항목씩, 시각과 세션 이름을 붙인다.

- 2026-10-30 ai-setup-49: 초안을 작성하고 jarvis-a3 의 검토 의견 R1~R11 을 반영했다. ai-setup 쪽에서는 AI-65 문서를 850a025 로 push 했다(사용자가 자율 진행을 지시하기 전에 승인한 항목이다). R4 참고 구현은 ai-setup 로컬 브랜치 `wip/orca-skill-update` 에 있다.
- 2026-10-30 jarvis-a3: SSoT 골격 초안을 만들었다(`AGENTS.md`, `CLAUDE.md`, `docs/README.md`·`PRD.md`·`SPEC.md`·`TEST_SPEC.md`, 모두 draft). 4절 계약은 `SPEC.md` 로 옮겼고, 승인 뒤 4절은 SPEC 을 가리키게 줄이기를 제안한다. `plan_id` 불일치 케이스를 ORCA-13 으로 제안했다.
- 2026-10-30 jarvis-a3: 이식 단위 1. ai-setup main(850a025)의 `res/tools/{orca,orca_server,orca_windows}.py` 를 `jarvis/modules/orca/` 로 그대로 복사했다(cmp 동일). 테스트 3개는 `tests/test_orca_module_{install,server,windows}.py` 로 옮기고, 본문은 바꾸지 않은 채 `tests/pytest_compat.py`(표준 라이브러리만)로 unittest 에서 돌린다. ai-setup 셋업 스크립트(setup.sh·setup.ps1·훅 uninstall)를 시험하는 11건은 ai-setup 에 남는 기능이라 옮기지 않았다. 결과: 옮긴 127건 OK(Windows 전용 8건 skip, ai-setup pytest 의 같은 케이스와 수가 같다), 전체 304건 OK.
- 2026-10-30 jarvis-a3: 대응표 AI→ORCA: AI-14·15·18·35·37·48 → ORCA-1, AI-17 → ORCA-9, AI-34 → ORCA-11, AI-49~54 → ORCA-4. 테스트의 `covers:` 는 `ORCA-n (AI-n)` 형식으로 바꿨다.
- 2026-10-30 jarvis-a3: 이식 단위 2. `jarvis/module.py`(계약 공통: plan_id, 종료 코드, `python -m jarvis module <이름> <동작> --json` CLI), `jarvis/orca_module.py`(Orca status·plan), `jarvis/__main__.py` 에 `module` 하위 명령, `tests/test_orca_module_contract.py`(ORCA-9, 4건). apply·uninstall 은 단위 6에서 구현한다(그 전에는 CLI 가 오류 JSON 과 종료 코드 1 을 낸다). pi5-server2 에서 읽기 전용 실측: status rc=0(installed, owned=false, 1.4.237 → 1.4.244, health ok), plan rc=0(update: package[sudo]·skills[update]).
- 2026-10-30 ai-setup-49: 단위 1 을 별도 컨텍스트(opus)에서 적대적으로 리뷰했다. 결론은 건전하고 가짜 GREEN 은 없다. 같은 127 케이스를 pytest 로 돌려도 119 passed·8 skipped 로 결과가 같고, 변이 검사 7종이 모두 FAIL 로 잡혔다. 지적: `fail` 이 `AssertionError` 라 `except Exception` 에 삼켜질 수 있음(중간), 지원하지 않는 형태를 만나도 오류를 내지 않음, 케이스 ID 의 모듈 이름, 쓰지 않는 import. 빼낸 것은 11 함수·15 케이스이며 ai-setup 에서 계속 돈다.
- 2026-10-30 ai-setup-49: 단위 2 리뷰. status·plan 이 읽기 전용임을 확인했다. 지적: plan_id 가 기기에 묶이지 않아 다른 기기에서 승인을 재사용할 수 있음(중간), Windows S4U 의 RPC 접근 거부를 down 으로 보고함(중간), plan 의 deb argv 가 실제 경로가 아님, ORCA-9 의 크기·mtime 비교. 기존 유닛을 받아들이는 경우는 `adopted: true` 로 기록하고 uninstall 이 지우지 않기로 했다(잠정, ai-setup 불변식 "이미 있던 것은 기록·제거하지 않는다").
- 2026-10-30 ai-setup-49: 단위 3 설계 답변. P1~P6 에 동의했다. POSIX 의 모바일 링크는 기기에 보관하지 않고 core 가 1회 발급한다(잠정, ORCA-1 문구 변경, 사용자 확인 필요). 빠진 것을 세 가지 지적했다. adopted 기기는 프로필이 이미 서비스 중이라 임시 서버로 페어링할 수 없으므로 실행 중인 서버를 몰래 멈추지 않는다. 새 유닛에 PATH·SHELL 을 넣는다. linger 는 승인 뒤에만 켠다.
- 2026-10-30 jarvis-a3: 단위 2 리뷰 U1~U4 반영. plan 에 `device`(hostname)를 넣어 plan_id 를 기기에 묶었다. `orca_rpc.Elevated`(PermissionError·runtime_access_denied)를 만들어 status 가 S4U 서버를 down 이 아니라 degraded 로 보고한다. deb argv 는 `<검증한 deb>` 자리표시자로 바꿨다. ORCA-9 테스트는 내용 해시로 비교하고 jarvis 상태 폴더도 넣었다.
- 2026-10-30 jarvis-a3: 이식 단위 4(ORCA-8). wip/orca-skill-update 의 orca.py 변경을 그대로 적용하고(git apply), 지적 (a) 번들 목록과 교집합, (b) 링크만 지운 스킬 제외를 넣었다(변이 검사 2종 모두 FAIL 로 잡힘). (c) jarvis 쪽은 `orca_update.execute` 가 모든 Exception 을 스킬 부분 실패로 받는다. (d) `--agent universal` 은 유지했다. jarvis 갱신 경로 `orca_install.skills()` 는 `skills update --all` 대신 소유 스킬만 갱신하고, 실패해도 영수증에 남은 상태를 기록한다. 그 결과 소유 기록이 없는 지금 jarvis 기기에서는 갱신 뒤 스킬을 바꾸지 않는다(O8 확인 필요).
- 2026-10-30 jarvis-a3: 단위 1 리뷰 C1~C4 반영. pytest.fail·skip 은 BaseException(Failed·Skipped)이고 자기 점검에 넓은 except 탐침을 넣었다. 지원하지 않는 형태(Test* 클래스, 리스트 pytestmark, indirect)는 TypeError 를 낸다. 케이스 클래스는 원래 모듈의 `Cases` 라 `python -m unittest tests.<모듈>.Cases.<테스트>` 로 하나만 돌릴 수 있다. 정정: 빼낸 것은 11개 함수, 15개 케이스이고, 빼낸 15건을 제외하면 ai-setup pytest 와 수가 같다. 전체 318건 OK(skip 8).
- 2026-10-30 jarvis-a3: 단위 3 설계 P1~P6·G1~G3 을 SPEC 에 잠정으로 넣었다. pi5-server2 읽기 전용 확인: `Linger=yes`, orca-serve 유닛 Environment 에 PATH 와 `ORCA_USER_DATA_PATH=/home/pi/.config/orca` 가 있다.
- 2026-10-30 ai-setup-49: 단위 4 리뷰. 막을 문제는 없다. 전체 318 OK(skip 8)를 직접 확인했다. 소유 기록이 없는 기기에서 스킬 갱신이 멈추는 퇴행을 O8b 로 올렸다(추천 B).
- 2026-10-30 jarvis-a3: 이식 단위 3. `jarvis/orca_serve.py`(POSIX 서버 서비스: 형태 판정 missing·same·different, 새 유닛·LaunchAgent 작성, uninstall 은 adopted·바뀐 파일을 남김, linger 조회, 로그인 셸 PATH)와 plan 의 server·linger 단계. 받아들인 기기는 데스크톱 페어링을 시도하지 않는다(G1: 임시 서버를 띄우려면 실행 중인 서버를 멈춰야 한다). LaunchAgent 이름은 thsvkd-mac·thsvkd-kgolf-macmini 에서 읽기 전용으로 확인한 `com.stablyai.orca.serve` 다(둘 다 `serve --supervise --no-pairing`, PATH·ORCA_USER_DATA_PATH). pi5-server2 읽기 전용: login_env 는 zsh 의 PATH 51항목(~/.local/bin 포함), form=same, linger=yes. 테스트 `tests/test_orca_serve.py` 9건, contract 2건 추가.
- 2026-10-30 ai-setup-49: 단위 3 리뷰. G1 처리(adopted 기기는 데스크톱 페어링을 시도하지 않고, 명시적인 reconfigure 로 넘김)에 동의했다. 지적: 활성화가 실패하면 기록 없이 파일만 남아 스스로 빠져나올 수 없음(중간, configuring 기록을 먼저 남기는 방식 제안), 형태 판정이 서버 프로필을 비교하지 않음(중간), login_env 가 서비스 프로세스의 $SHELL 과 stdin 에 기댐(중간), systemd 의 % 와 $ 이스케이프, remove 경로 검증, macOS adopted 의 실제 plist 기록(가벼움).
- 2026-10-30 jarvis-a3: 단위 3 리뷰 V1~V6 반영. write 는 띄우기 전에 configuring 기록을 남기고, 재실행은 다이제스트가 같은 우리 파일의 활성화만 이어서 한다(plan 에 server 단계로 보인다). form 은 서비스 프로필(ORCA_USER_DATA_PATH, 없으면 Orca 기본 프로필)도 realpath 로 비교한다. login_env 는 계정의 로그인 셸(pw_shell)을 stdin 없이 실행한다. systemd 값의 `%`·ExecStart 의 `$` 를 이스케이프한다. remove 는 service_path 가 정해진 경로일 때만 지운다. adopted 기록에는 실제로 적재된 plist·label 을 남긴다.
- 2026-10-30 jarvis-a3: 이식 단위 6. `orca_module.apply(plan_id)`: 다시 세운 계획과 plan_id 가 다르면(다른 기기 포함) 아무것도 하지 않고 stale(3). install 은 옮긴 설치기(`--no-server`, Windows 는 서버까지), update 는 `orca_update.start()` 를 끝까지 기다리고, 세션 보류는 `orca_sessions.Deferred`(보류 문구 10곳) → job.deferred → deferred(2). server 단계는 같은 형태면 adopted 기록, 없으면 `orca_serve.write`, linger 단계는 `loginctl enable-linger`. 앞 단계가 실패하면 멈춘다. `uninstall`: adopted 는 기록만 지우고(preserved adopted unit), 우리가 쓴 뒤 바뀐 유닛은 영수증째 남기고 deferred(2), 패키지·스킬은 옮긴 uninstall. 옮긴 설치기: 비대화형 sudo 는 `sudo -n`(AI-35 테스트를 이 규칙으로 바꿈), `--no-server`. 변이 검사 4종(stale 확인, deferred 대응, changed 보존, 실패 시 중단) 모두 FAIL 로 잡힘. 전체 339건 OK(skip 8).
- 2026-10-30 jarvis-a3: 남은 일: POSIX 새 설치의 데스크톱 페어링(P1, issue_pairings 와 복구 기록), Windows 갱신(단위 5, ORCA-6·7), 매니페스트 검증 한 벌(옮긴 orca.py 의 fetch_manifest·download 와 orca_update), 실기기 실측, core 화면의 승인 카드(D7)는 아직이다.
- 2026-10-30 jarvis-a3: 이식 단위 5(ORCA-6·7). update_ai.py 의 orca_server_record·update_orca_windows·wait_for_orca_server_switch·version_key·is_current 를 `jarvis/modules/orca/orca_update_windows.py` 로 본문 그대로 옮겼다. 바꾼 것: run_step 은 출력 흘리기 대신 시간 제한과 프로세스 트리 끊기만 하는 단순판, wait 는 터미널을 셀 수 없으면(None, 일반 권한) 끝내지 않고 시간까지 러너의 전환을 기다린다(잠정, ORCA-6 문구와 함께 확인 필요). 테스트는 AI-55a~c·AI-65(Windows 부분) 10건을 본문 그대로 옮기고, 매개변수 하나(None 을 두 번 셈)와 "3 live terminals" 케이스를 더했다. 옮기지 않은 것: update_orca_deb(jarvis orca_update 가 맡는다), runs_target·method_for(update-ai 의 세션 판정·설치 방식 판별; jarvis 는 설치 폴더의 Orca.exe 가 떠 있으면 Deferred 로 보류한다).
- 2026-10-30 jarvis-a3: `orca_update` 에서 0dcf2de 의 `_ai_setup_managed`(update-ai 로 보류)를 지우고 `_s4u_server()`(Windows, task-scheduler, configured)로 바꿨다. `_supported` 는 S4U 면 가능, `_run` 은 S4U 면 `_run_s4u`(데스크톱 앱 보류 → 설치기 → 설치본 버전 → 소유 스킬). plan 의 Windows S4U package 단계 argv 는 `<검증한 orca-windows-setup.exe> /S --updated`. README 의 갱신 표에 Windows(S4U) 행을 더했다. 전체 354건 OK(skip 8).
- 2026-10-30 jarvis-a3: 매니페스트 검증 한 벌로 합치기는 미뤘다. 지금 두 벌(옮긴 orca.py 의 fetch_manifest·download 는 설치·Windows 갱신용, orca_update 의 parse_manifest·Live.download 는 Linux·macOS 갱신용)은 같은 규칙(크기·SHA-512, GitHub 호스트만 따라가는 리다이렉트)을 따르지만, 합치면 실제 기기에서 도는 갱신 경로(프록시 끄기, 1200초 제한, apt 가 읽을 0644 권한, STATE_DIR 의 .partial)를 바꾸게 된다. 실기기 실측 뒤 별도 단위로 한다.
- 2026-10-30 ai-setup-49: 단위 5 리뷰. 막을 문제는 없다. 지적: 서버 전환 결과(미룬 이유)가 job 결과에 남지 않음(중간, AI-55c), 무엇을 세지 않는지 지키는 테스트가 없음(중간), 권한이 높은 프로세스의 exe 가 None 이 되는 것에 기대고 있음을 드러낼 것, 데스크톱 앱 확인과 설치기 사이의 TOCTOU(범위 밖).
- 2026-10-30 jarvis-a3: 단위 5 리뷰 W1~W3 반영. `_s4u_switch()` 가 설치 뒤 러너 상태와 설치본 식별자를 다시 읽어 job 결과에 `server: switched|pending` 과 `server_reason`(터미널 수·셀 수 없음·시간 안에 옮기지 않음)을 남긴다(옮긴 함수 본문은 그대로). `_desktop_app_open` 테스트에 S4U 복사본·터미널 호스트·exe=None(권한이 높은 서버)을 넣었고, 판정을 "이름이 Orca.exe" 로 넓히는 변이가 FAIL 로 잡힌다. 전체 356건 OK(skip 8). W4(확인과 설치기 사이에 앱을 여는 경우)는 범위 밖.
- 2026-10-30 ai-setup-49: 단위 6 적대적 리뷰(opus). 판정은 변경 요청이다. 파괴적 경로는 없다. 막을 것: Windows 에서 import 실패(pwd), Windows 설치가 늘 failed, 갱신 뒤 실패를 deferred 로 보고, skills 만 승인한 계획이 매니페스트가 바뀌면 sudo apt 까지 실행(승인 계약 위반), 패키지가 막히면 apply 실패와 끝나지 않는 계획. 고칠 것: apply --dry-run 무시, 자식 프로세스 출력이 JSON 표준 출력을 깸, 설치 때 기존 서버 기록 덮어쓰기, 동시 갱신 잠금 없음. X1~X9 로 jarvis-a3 에 전달했다.
- 2026-10-30 jarvis-a3: 단위 6 적대적 리뷰 X1~X9·가벼운 지적 반영. X1 `pwd` 는 login_env 안에서만 import, module.cli 의 import 는 try 안. X2 Windows plan 에는 POSIX server 단계가 없다(설치기가 S4U 서버까지 한다). X3 `Sessions.verify` 는 Deferred 를 RuntimeError 로 바꾼다(바꾼 뒤의 확인 실패는 보류가 아니다). X4 package 단계에 `target` 을 넣어 plan_id 에 묶고, apply 는 `orca_update.start(expect=target)`·옮긴 설치기 `--expect-version`·`update_orca_windows(expect=)` 로 넘긴다. 매니페스트가 다르면 아무것도 바꾸지 않는다. skills 만 있는 계획은 갱신 작업을 돌리지 않고 `orca_install.skills()` 만 부른다. X5 막힌 단계가 있으면 apply 는 failed, 소유 스킬을 마지막으로 맞춘 판(`skills_version`)이 설치본과 같으면 skills 를 계획하지 않아 current 에 이른다. X6 apply --dry-run 거절. X7 module.cli 가 fd 1 을 fd 2 로 dup2 한 뒤 JSON 만 원래 fd 로 쓴다. X8 설치의 server 단계는 다른 서버 기록을 덮어쓰지 않는다. X9 `orca_update.start` 가 O_CREAT|O_EXCL 잠금 파일(PID·생성 시각으로 회수)을 잡고 `_run` 이 끝나면 언제나 푼다. 가벼운 것: 바꾸기 전의 "확인할 수 없음·바뀜" 4곳을 Deferred 로 맞춤, uninstall 은 dry-run 의 plan_id(영수증의 Orca 기록에 묶임)를 받아야 실행, tool_attempts 검사를 무엇보다 먼저, 우리가 켠 linger 는 `orca_linger` 로 기록했다가 uninstall 때 끔, 읽기 전용 경로의 `sudo -n true` 는 한 시간 묶음. 새 테스트 14건, 변이 검사 4종(X3·X4·X7·목표 버전 묶기) 모두 FAIL 로 잡힘. 전체 369건 OK(skip 8).
- 2026-10-30 ai-setup-49: 단위 6 재리뷰(opus, 같은 실험). X1~X8 은 고쳐졌다. X4(skills 만 승인한 계획이 패키지를 바꾸던 문제)는 deb·native·S4U·설치기 경로 모두에서 목표 버전이 다르면 아무것도 하기 전에 거부한다. X9 는 부분이다. 새 지적: 갱신 잠금을 두 프로세스가 함께 잡을 수 있음(N1, 중간, flock 추천), 스레드의 print 가 되돌린 표준 출력으로 나감(N2), recover() 가 잠금을 잡지 않음(N3), 오프라인이면 install plan 이 실패함(N4).
- 2026-10-30 jarvis-a3: X1~X9 재리뷰 N1~N4 반영. N1 갱신 잠금을 fcntl.flock(POSIX)·msvcrt.locking(Windows)으로 바꿨다. 잡은 프로세스가 죽으면 OS 가 풀므로 남은 잠금 회수와 그 경쟁이 없다(파일은 지우지 않는다). N2 `_run_job` 은 RESULT 기록·로그 출력·잠금 해제를 마친 뒤에 state 를 바꾼다(모듈 apply 가 표준 출력을 되돌린 뒤에 로그가 나가지 않게). N3 `recover()` 도 같은 잠금을 잡고, 다른 프로세스가 갱신 중이면 그 MARK 를 건드리지 않는다. N4 오프라인이면 plan 은 "릴리스 매니페스트를 받지 못했습니다: <urlopen error … name resolution>" 오류 JSON 을 낸다(동작 그대로). sudo 확인 묶음은 프로세스 안에서만 유효하다는 사실을 주석에 적었다. 변이 검사(flock 제거, recover 잠금 제거) 모두 FAIL 로 잡힘. 전체 370건 OK(skip 8), 실제 ~/.local/state/jarvis 에 잠금 파일이 생기지 않았다.
- 2026-10-30 ai-setup-49: N1~N4 반영을 확인했다(N1 은 flock·msvcrt.locking 으로 바꿔 회수 경쟁이 없어졌다. Windows 경로는 kgolf 미실행). 아침 확인 목록을 8절에 모았다.
- 2026-10-30 jarvis-a3: 사용자 4라운드 답을 반영했다. K2 에 따라 ORCA-8 을 `skills update --all` 로 고쳤다. `orca_install.skills()` 는 `--all` 을 실행하고, 맞춘 판을 `orca-skills-version` 에 남기며, 깐 스킬의 다이제스트를 다시 기록한다(실패해도 기록한다). plan 은 앱을 올릴 때와 맞춘 판이 설치본과 다를 때만 skills 단계를 낸다. 옮긴 `update_owned_skills` 와 AI-66 테스트 4건은 지웠다. 변이 4종(계획 조건, 소유 기록, 맞춘 판, `--all`) 모두 FAIL 로 잡힘. 전체 366건 OK(skip 8). 처음 돌린 테스트 하나가 실제 `~/.local/state/jarvis/orca-skills-version` 을 남겨 지웠고, 테스트가 상태 폴더를 임시 폴더로 바꾼 뒤에는 생기지 않는다. SSoT 3종은 authoritative(K4·K10 확인 중)로 바꿨다.
- 2026-10-30 jarvis-a3: K12 에 따라 `orca-module` 브랜치에 커밋했다(c0c04a8, push 안 함). pi5-s3(pi5-server3) 실측: 커밋을 `/tmp/jarvis-orca-measure-c0c04a8` 에 풀어 status·plan(rc=0) → 같은 plan_id 로 apply(ssh 가 끊겨도 설치가 끊기지 않게 setsid 로 분리). 결과 done: deb 1.4.237 → 1.4.244, 번들 스킬 `--all`, 기존 `orca-serve` 유닛을 `adopted: true` 로 기록(유닛 파일 mtime 10-26 그대로). 뒤의 status 는 1.4.244·health ok, plan 은 단계가 없다(current). orca-serve active, NRestarts=0. 실측에서 apply 결과에 skills 단계가 빠진 것을 찾아, 앞 단계가 함께 처리한 단계도 결과에 남기게 고쳤다(변이 FAIL 로 잡힘).
- 2026-10-30 jarvis-a3: kgolf 실측. 전체 테스트를 Windows 에서 처음 돌렸다. Windows 전용 테스트는 통과했고, 실패는 테스트 쪽 문제였다: mac 테스트가 플랫폼을 고정하지 않아 kgolf 의 실제 영수증을 읽음, 잠금 테스트가 fcntl 만 씀, X1 테스트가 없는 os.getuid 를 지움. 또 `test_orca_serve` 가 HOME 만 바꿔 Windows 에서는 실제 홈(USERPROFILE)에 `.config\systemd\user\orca-serve.service` 와 `Library\LaunchAgents\com.stablyai.orca.serve.plist` 를 남겼다. 둘 다 그 실행이 만든 폴더(생성 시각 10:49)라 폴더째 지웠고, 그 모듈은 Windows 에서 건너뛰며 USERPROFILE 도 바꾼다. 고친 뒤 kgolf 에서 새로 생긴 홈 파일 없음, 영수증 해시 그대로. 남은 실패 3건(`test_cli_login` 2, `test_parsers` 1)은 main 과 같은 기존 코드의 Windows 문제로 이번 범위 밖이다. status·plan rc=0(1.4.243 → 1.4.244, S4U 설치기·스킬). apply 는 데스크톱 앱이 열려 있어 deferred·rc=2, 영수증·설치본 그대로. 실측에서 고친 것: Windows 콘솔에서도 표준 출력 JSON 을 UTF-8 로 냄, 보류를 진행 로그에 "실패"가 아니라 "보류"로 적음. 전체 367건 OK(skip 8).
- 2026-10-30 jarvis-a3: 사용자 5라운드 답(K4 `s4u_only`, 스킬 범위 해석 `ok`)을 반영했다. `orca_update._supported` 는 S4U 서버 기록이 없는 Windows 를 공식 업데이터 RPC 없이 거절하고, 실행 분기·중단 기록의 native 판정은 Mac 만 남겼다. `orca_install.native_contract` 의 Windows(7-Zip) 검사와 `validate_runtime` 의 Orca.exe 분기를 지웠다. 새 테스트 1건(S4U 없는 Windows 는 갱신하지 않음)은 가드를 지우는 변이에서 FAIL. 전체 368건 OK(skip 8). 스킬 범위는 해석대로다: CLI 는 앱과 함께 올라가고, 스킬은 `skills update --all`, 플러그인은 Orca CLI 에 명령이 없어 범위 밖이다.
