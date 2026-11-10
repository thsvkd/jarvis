# Jarvis

기기마다 상주하는 게이트웨이가 호스트 자원과 서비스를 읽고, core 홈이 여러 기기를 한 화면에 모아 보여 주는 읽기 전용 대시보드다.
Python 수집기(표준 라이브러리 + psutil)와 정적 HTML 로 되어 있고, macOS·Linux·Windows 에서 같은 코드로 돈다.

## 주요 기능

- **하드웨어**: CPU·메모리·디스크·네트워크. Raspberry Pi 의 전력·팬·스로틀링, Apple Silicon 의 온도·GPU·전력
- **서비스**: 컨테이너·compose·서비스 관리자 유닛(systemd·launchd)·열린 포트와 소유 프로세스
- **인그레스**: Caddy 라우트와 Cloudflare Tunnel 의 경로별 프로브, 설정 드리프트, 인증서 잔여일
- **네트워크 맵**: 서비스가 노출되는 경로(개념)와 기기·인터페이스·도커 네트워크 구성(물리)
- **여러 기기**: core 홈에 기기 목록을 모으고, 기기별 화면과 jarvis 업데이트를 한곳에서 다룬다
- **AI 구독 사용량**: Claude·Codex·Grok 등의 계정별 한도, 로그인 상태, 다시 로그인과 계정 전환
- **AI 셋업 모듈**: 하네스·훅·스킬·플러그인·MCP 를 계획(plan)으로 먼저 보이고 승인한 대로 적용(apply)

전체 목록은 [구조와 화면](docs/architecture.md)에 있다.

## 빠른 시작

준비물은 [Tailscale](https://tailscale.com/) 뿐이다. git·Homebrew·Python 이 없어도 설치기가 uv 로 Python 을 받는다.

> 인증이 없으므로 접근 통제가 전부다. 루프백·사설망·tailnet 에서만 열리며, 공개 라우트나 터널에 올리지 않는다([접속과 접근 통제](docs/operations.md#접속-pit5)).

**1. 첫 기기에 설치한다** (macOS·Linux)

```sh
curl -fsSL https://github.com/thsvkd/jarvis/releases/latest/download/install.sh | sh
```

설치가 끝나면 서비스(macOS LaunchAgent `dev.jarvis`, Linux systemd 사용자 유닛 `jarvis`)가 뜬다. 브라우저에서 `http://<기기>:9300/` 을 연다.

**2. 다른 기기를 core 에 모은다**

모을 기기에서 core 의 tailnet IP 를 주어 설치한다.

```sh
curl -fsSL https://github.com/thsvkd/jarvis/releases/latest/download/install.sh | sh -s -- --core <core 의 tailnet IP>
```

Windows 는 관리자 PowerShell 에서 실행한다.

```powershell
& ([scriptblock]::Create((irm https://github.com/thsvkd/jarvis/releases/latest/download/install.ps1))) -Core <core 의 tailnet IP>
```

**3. core 의 설정에 기기를 등록한다**

core 기기의 `~/.config/jarvis/config.toml` 에 모을 게이트웨이와 조작 버튼을 누를 수 있는 기기를 적고 core 를 재시작한다.

```toml
devices = [
  { id = "my-mac", url = "http://<그 기기의 tailnet IP>:9300" },
]
viewers = ["<조작을 허용할 기기의 tailnet IP>"]
```

core 홈(`/`) 위쪽에 기기 목록이 생기고, `/?device=<id>` 로 그 기기 화면을 연다. 키 전체와 SSH 로만 닿는 기기는 [설정](docs/configuration.md)을 본다.

## 구조

```
기기 A ─ 게이트웨이(:9300) ┐
기기 B ─ 게이트웨이(:9300) ┼─ core 홈 ─ 브라우저(tailnet)
기기 C ─ SSH 로만 닿는 기기 ┘
```

- **게이트웨이**: `python -m jarvis`. 그 기기의 자원·서비스를 수집해 스냅샷 API 와 화면(`/`, `/map/`)을 낸다.
- **core**: 설정에 `devices` 가 있는 게이트웨이. 다른 게이트웨이의 값을 요청마다 모아 보여 주고 조작을 전달한다.
- **모듈**: 기기 쪽 설치(훅·셋업·하네스)를 `plan` → `apply` 계약으로 다룬다.

코드 배치와 수집 항목은 [구조와 화면](docs/architecture.md)에 있다.

## 갱신과 제거

```sh
python3 -m jarvis update            # 새 릴리스를 받는다(--check 는 확인만). core 홈의 'jarvis 업데이트' 버튼과 같다
python3 -m jarvis --version         # 지금 쓰는 버전
curl -fsSL https://github.com/thsvkd/jarvis/releases/latest/download/install.sh | sh -s -- --uninstall
```

자동 갱신은 없다. 제거는 서비스 등록과 실행본만 지우고 설정(`config.toml`)은 남긴다. 릴리스 방법, 되돌리기, Windows 제거는 [운영](docs/operations.md)에 있다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [구조와 화면](docs/architecture.md) | 무엇을 보나, 코드 구조, 운영 홈 |
| [설정](docs/configuration.md) | 설정 키, SSH 기기 |
| [운영](docs/operations.md) | 릴리스·갱신, 접속과 접근 통제, Linux·macOS·Windows 설치와 운영, 실행본 이전 |
| [core 전환](docs/core-migration.md) | core 를 다른 기기로 옮기는 순서 |
| [AI 구독 사용량](docs/usage.md) | 사용량 수집, hswap·caam, 로그인 상태와 다시 로그인 |
| [모듈 다루기](docs/modules.md) | 모듈 CLI(plan·apply), setup·harness·doctor |
| [검증](docs/verification.md) | 테스트와 스냅샷 스키마 비교 |
| [문서 지도](docs/README.md) | PRD·SPEC·TEST_SPEC 등 계약 문서와 상태 |

## 개발

```sh
JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests   # 단위 테스트(표준 라이브러리 unittest)
node tests/test_jarvis_js.mjs                                    # 화면 공용 JS
```

작업 규칙은 [AGENTS.md](AGENTS.md), 완료 기준은 [docs/TEST_SPEC.md](docs/TEST_SPEC.md)가 소유한다.

## 라이선스

[LICENSE](LICENSE)
