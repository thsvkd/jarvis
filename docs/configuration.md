# 설정

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

## 설정

우선순위: 환경 변수 `JARVIS_*` > 설정 파일 > `jarvis/config.py` 의 기본값.

- 설정 파일: `$JARVIS_CONFIG` 또는 `~/.config/jarvis/config.toml`. 키는 환경 변수 이름에서 접두어를 뗀 소문자다
  (`JARVIS_INGRESS_DIR` → `ingress_dir`). 목록은 TOML 배열 또는 쉼표 구분 문자열.
- 예시: `deploy/pit5.toml`(pit5 운영값), `deploy/macstudio.toml`.
- 코드의 기본값은 중립이다(Caddy 컨테이너 `caddy`, 터널 `cloudflared`, 인그레스 경로·도메인·추가 Host 없음). pit5 는 설정 파일이 있어야
  지금과 같은 결과가 나온다. 설정 파일을 못 읽으면 `meta.errors.config` 에 이유가 뜬다.
- Caddy·터널이 없는 기기는 `caddy = ""`·`tunnel = ""` 로 두면 그 수집을 건너뛰고 인프라 목록에서 뺀다.
- `[infra_units]`: 인프라 항목의 서비스 관리자 이름(Linux 는 systemd 유닛, macOS 는 launchd 라벨). 빈 값이면 뺀다.

주요 키: `port`(9300) · `bind` · `allow` · `hosts` · `state` · `ingress_dir` · `caddy` · `tunnel` · `tunnel_name` · `domain` ·
`docker_sock`(자동: /var/run/docker.sock → ~/.orbstack/run/docker.sock → ~/.docker/run/docker.sock. Docker 가 없는 기기는 `""`) ·
`hermes_re`(Hermes 카테고리로 묶을 컨테이너·프로세스 이름, 기본 `hermes`) · `hermes_url`(Hermes 게이트웨이 `/api/status`, 빈 값이면 안 봄) · `ss` · `pm2` · `tailscale` ·
`core`(이 기기의 사용량·계정·모듈 경로를 부를 core 기기 tailnet IP 목록. 루프백은 받지 않는다) ·
`devices`(core 역할: 모을 게이트웨이 `[{id, url}, …]`, SSH 로만 닿는 기기는 `{id, ssh}`(아래 "SSH 기기"), 설정 파일 전용·재시작 필요) · `viewers`(core 홈의 조작 버튼(jarvis 갱신·로그인·계정 전환·모듈 적용)을 누를 수 있는 tailnet IP) · `proxies`(viewers 판정에서 X-Forwarded-For 마지막 항목을 믿을 리버스 프록시 tailnet IP. 도메인을 받는 pit5 Caddy) · `cswap_install`(hswap 자동 설치와 Claude 자동 갱신 예약, macOS 로그인 세션 밖의 파일 저장 방식에서 게이트웨이가 직접 부르는 keepalive·자동 전환, 기본 true. 저장 방식 확인(`hswap claude doctor`)은 읽기만 해서 이 값과 상관없이 한다) · `claude_stale_s`(Claude 사용량 저장소 값이 오래됐다고 볼 기준, 기본 900초) · `claude_keepalive_stale_s`(hswap keepalive 확인이 멈췄다고 볼 기준, 기본 1200초). `core`·`viewers` 는 호스트명이 아니라 tailnet IP 로 적는다(루프백·LAN IP 는 적어도 받지 않는다. core 가 자기 자신도 모으면 그 `url` 도 tailnet IP 로 적는다).
core 홈(`/`) 위쪽에 기기 목록이 생기고, `/?device=<id>`·`/map/?device=<id>` 로 그 기기 화면을 연다.

설정 파일은 10초마다 변경 여부를 확인한다. 인그레스 대상·도메인·추가 Host·Hermes·인프라 유닛·기대 서비스는 자동 반영한다.
잘못된 TOML은 마지막 정상 설정을 유지하고 수집 오류로 표시하며, 파일을 고치면 재시작 없이 회복한다.
`port`, `bind`, `allow`, `state`, `static`, `docker_sock`, `user_uid`, 명령 경로(`ss`·`pm2`·`tailscale`) 변경은
기존 리스너·접근 통제와 함께 재시작해야 하며 화면에 경보가 뜬다. 환경 변수 우선순위는 동일하다.

- `caddyfile_container_path`: adapter가 읽는 컨테이너 안 파일(기본 `/etc/caddy/Caddyfile`). 호스트 파일의 주석·설명은 `ingress_dir/Caddyfile`에서 읽는다.
- `expected_services`: 없어지면 경보할 서비스 ID 목록(예: `["route:card.example.com", "ctr:chat-ai-db"]`). 의도적으로 철거하면 목록에서도 뺀다.
- `expected_units`: 중지·언로드되어도 감시할 시스템 유닛 목록(예: `["my-app.service"]`). 일반 loaded 유닛도 inactive 상태를 목록에 유지한다.

홈의 런타임 섹션과 `/api/changes`에서 구성 변경 이력을 본다. `~/.local/state/jarvis/inventory.json`에
30초마다, 종료 시 저장한다. 최초 관측은 기준선으로 잡으며 수집 실패는 대량 삭제로 기록하지 않는다.
명령줄·환경 변수·임의 Docker label은 저장하지 않는다. 기기 제거는 관측 목록에서 빠졌다는 의미다.

## SSH 기기

core 의 tailnet 으로는 닿지 않고 SSH 로만 닿는 기기(예: 다른 tailnet 의 회사 기기에 ProxyJump 로 닿는 경우)는 core 설정에 `ssh` 로 적는다. 계약은 `docs/SPEC.md` 의 "SSH 기기" 절이다.

```toml
[[devices]]
id = "thsvkd-kgolf-macmini"
ssh = "kgolf-macmini-m1"   # core 사용자 ~/.ssh/config 의 Host 별칭
```

- 항목은 `{id, url}` 또는 `{id, ssh}` 중 하나다. 별칭은 영숫자로 시작하는 `[A-Za-z0-9._-]` 64자 이하만 받는다. `/api/devices` 의 `url` 은 `ssh://<별칭>` 이고, 카드에 'SSH 경유' 가 붙는다.
- 조회(스냅샷·history·changes)는 `ssh -W 127.0.0.1:9300` 으로 그 기기 게이트웨이의 루프백에 GET 한다. 조작은 AI 셋업 모듈(status·plan·apply)과 jarvis 업데이트를 원격 `python -m jarvis …` 로 하고, 사용량·로그인·계정 전환·삭제는 원격 bridge 로 그 기기 게이트웨이에 전달한다. Windows 기기는 모듈·업데이트도 bridge 로 전달한다(그 기기 jarvis 1.13.0 이상).
- jarvis 업데이트는 원격 `update` 로 받은 뒤 새 판이면 스냅샷의 게이트웨이 pid 에 `kill -TERM` 을 보낸다(그 pid 의 명령줄이 `-m jarvis` 로 끝날 때만). 다시 띄우는 것은 서비스 관리자다.

전제(사용자가 준비한다. jarvis 는 ssh 설정을 읽거나 쓰지 않는다):

- core 기기
  - 별칭과 ProxyJump 를 core 서비스 사용자의 `~/.ssh/config` 에 둔다. macOS 는 `/usr/bin/ssh` 를 쓴다(Homebrew openssh 는 `UseKeychain` 을 거절한다).
  - **점프 호스트 Host 블록에도** `BatchMode yes`·`ConnectTimeout 5`·`ServerAliveInterval 5` 를 둔다. 명령줄 `-o` 는 목적지 홉에만 걸린다.
  - known_hosts 에 점프 호스트와 대상의 호스트 키를 미리 등록한다(대화형 `ssh <별칭> true` 한 번).
  - core 서비스(LaunchAgent·systemd) 세션에서 passphrase 입력 없이 쓸 수 있는 키(에이전트·키체인)가 있어야 한다. 대화형 셸에서 되더라도 서비스에서는 `Permission denied` 일 수 있다. [LaunchDaemon 모드](operations.md#launchdaemon-모드-gui-로그인-없는-mac)에는 에이전트·키체인이 없어 passphrase 없는 키 파일만 쓸 수 있다.
- 대상 기기
  - sshd 가 명령 실행과 TCP 포워딩을 허용한다. 키를 `permitopen` 전용으로 좁히면 조작이 안 된다.
  - install.sh 배치다: 저장소 `~/.local/share/jarvis/app`(없으면 옛 배치·개발 체크아웃 `~/Workspace/jarvis`), python `~/.local/share/jarvis/venv/bin/python`. 손으로 설치한 기기는 조작이 502(배치 아님)로 끝난다. 대상은 macOS·Linux 이고 jarvis 1.7.8 이상이다(낮으면 501, 그 기기에서 직접 갱신).
  - 사용량·로그인·계정 조작은 ssh 로그인 사용자가 게이트웨이를 돌리는 사용자와 같아야 한다(bridge 는 그 사용자만 열 수 있는 IPC 에 닿는다). Windows(OpenSSH 서버) 기기는 모든 조작이 bridge 를 거치므로 게이트웨이가 한 번 기동해 `%LOCALAPPDATA%\jarvis\bridge.cmd` 를 써 두어야 한다. jarvis 업데이트 뒤 재시작은 그 게이트웨이가 스스로 한다(HTTP 기기와 같다).
  - **jarvis 업데이트 버튼은**(POSIX 기기) 서비스 정의가 `KeepAlive=true`(launchd) 또는 `Restart=always`(systemd)이고, 스냅샷의 `monitor.pid` 가 서비스 관리자가 띄운 pid 일 때만 쓴다. 아니면 kill 뒤 게이트웨이가 꺼진 채 남는다. install.sh 는 이미 있는 plist 를 덮어쓰지 않으므로 그 기기에서 확인한다.

        plutil -extract KeepAlive raw -o - ~/Library/LaunchAgents/dev.jarvis.plist     # true
        launchctl print gui/$(id -u)/dev.jarvis | grep -m1 'pid ='                      # 스냅샷 monitor.pid 와 같은지
        # LaunchDaemon 모드는 /Library/LaunchDaemons/dev.jarvis.plist 와 system/dev.jarvis 로 확인한다

절차:

1. core 기기 대화형 셸에서 `/usr/bin/ssh -T -o BatchMode=yes -- <별칭> true` 가 되는지 본다.
2. core 설정에 `{id, ssh}` 를 넣고 core 서비스를 재시작한다(`devices` 는 재시작 키다).
3. `/api/devices` 의 그 행이 `reachable` 이고 `error` 가 비어 있으며 `fails` 가 0 인지 본다. `Permission denied` 면 서비스 세션의 키 문제다.
4. AI 셋업 화면에서 그 기기의 모듈 상태가 보이는지, 사용량 섹션에 그 기기의 사용량이 보이는지 본다(사용량·로그인·계정 조작은 원격 bridge 로 간다. 그 기기의 jarvis 가 1.13.0 이상이어야 한다).

위험: 원격과 core 사이에 프로세스 잠금이 없다(그 기기 화면에서 시작한 작업과 겹칠 수 있다). apply 중 연결이 끊기면 core 는 `interrupted` 로 두지만 원격은 계속 돌 수 있다. core 가 재시작하면 SSH 작업 기록은 사라진다. 그 기기 스냅샷(피어 목록·명령줄)과 ssh 오류 마지막 줄이 core 의 tailnet 에 보인다.
