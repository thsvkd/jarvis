# 모듈 다루기

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

## 모듈 CLI

모듈을 기기에서 직접 다룰 때는 `python -m jarvis module <이름> plan --json` 으로 계획을 보고, 그 `plan_id` 로 `apply --json --plan-id <id>` 를 실행한다.
python 은 게이트웨이 서비스가 쓰는 것을 쓴다(Mac 의 PATH 첫 `python3` 는 psutil 이 없는 시스템 3.9 일 수 있다: LaunchAgent 의 `/opt/homebrew/bin/python3` 나 venv).
하네스·도구 점검은 `python -m jarvis module doctor status --json` 이다. 읽기 전용이며 설치 위치·버전, 중복 설치, 은퇴한 훅, 평문 시크릿의 위치와 키 이름(값은 내지 않는다), lock 밖 스킬을 보고한다.

ai-setup 이 하던 설치·갱신도 같은 모듈 계약으로 한다(core 홈의 AI 셋업 화면 또는 같은 CLI).

| 모듈 | 하는 일 |
| --- | --- |
| `setup` | 하네스(claude·codex 와 영수증의 하네스)·Claude 플러그인·번들 스킬·CodeGraph·MCP 등록·줄바꿈 키·출력 스타일·mod·OMC HUD·Tailscale 을 없는 것만 설치하고, 이미 설치한 설정은 배포본이 바뀌면 다시 적용한다. 엔진은 ai-setup 에서 옮긴 `jarvis/modules/setup/` 이다 |
| `harness` | 설치된 하네스·도구를 최신으로 올린다(update-ai). codex 관리형 데몬이 CLI 와 어긋나면 데몬 재시작을 승인 단계로 보인다 |
