# 운영

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

## 실행본·릴리스·갱신

개발 저장소: `~/Workspace/jarvis`(git 체크아웃). 설치기(install.sh·install.ps1)가 설치한 실행본은 `~/.local/share/jarvis/app`
(Windows `%LOCALAPPDATA%\jarvis\app`)이다. 아래의 '저장소'는 서비스가 띄우는 이 폴더(실행본 app 또는 개발 체크아웃)를 말한다.
1.8.0 이전 설치기가 `~/Workspace/jarvis` 에 푼 실행본은 [실행본 이전](#실행본-이전-180)으로 app 으로 옮긴다.
원본은 s2 의 이 저장소이고, 운영 기기는 GitHub 릴리스를 받는다(`claude update` 와 같은 방식, `jarvis/self_update.py`·`jarvis/launch.py`).
- 릴리스: s2 에서 `jarvis/__init__.py` 의 `__version__` 을 올려 커밋한 뒤 `python3 tools/release.py`. 태그 `v<버전>` 과
  소스 묶음·`manifest.json`(버전·주소·SHA256)을 GitHub 릴리스에 올린다.
- 갱신: core 홈 기기 목록의 **jarvis 업데이트**(또는 **jarvis 모두 업데이트**, core 자신은 마지막) 또는 그 기기에서 `python3 -m jarvis update`
  (`--check` 는 확인만). 새 버전이면 묶음을 받아 SHA256 을 확인하고 `~/.local/share/jarvis/versions/<버전>`
  (Windows `%LOCALAPPDATA%\jarvis\versions`)에 풀고 `current.json` 을 바꾼 뒤 스스로 끝난다. 자동 갱신은 없다.
  기기 카드는 갱신 중에 스피너 → 받는 비율의 막대 → 스피너(풀기·재시작) → 바뀐 버전 순으로 진행을 보인다. 단계는 게이트웨이 `GET /api/jarvis/update`(설정 core 만)를 core `GET /api/devices/<id>/jarvis/update` 로 읽는다(UI-4).
- 기동: 서비스는 그대로 저장소에서 `python3 -m jarvis` 를 띄우고, 런처가 받아 둔 릴리스가 저장소보다 새로우면 그 폴더의 코드로 넘어간다
  (s2 처럼 저장소가 같거나 새로우면 저장소가 돈다). 새 릴리스가 60초를 못 버티고 세 번째로 뜨면 이전 버전(없으면 저장소)으로 되돌리고,
  그 버전은 다음 릴리스가 나올 때까지 다시 받지 않는다. `python3 -m jarvis --version` 이 지금 쓰는 버전을 보인다.
- 의존 패키지(psutil 등)나 서비스 정의(plist·pyw·유닛)가 바뀌는 릴리스는 버튼만으로 반영되지 않는다. 그 기기에서 직접 손본다.

## 접속 (pit5)

| 경로 | 주소 |
|---|---|
| tailnet (HTTPS) | https://jarvis.example.com/ — Caddy `(private)` 스니펫. 터널 경로(:80)에서는 404 |
| tailnet (직접) | http://pit5:9300/ · http://100.64.0.2:9300/ |
| LAN | http://192.168.0.10:9300/ (유선) · http://192.168.0.11:9300/ (Wi-Fi) |

| 페이지 | 경로 |
|---|---|
| 홈(개요) — Bento Focus | `/` |
| 서비스 네트워크 맵 — Atlas | `/map/` — 개념 모드(서비스가 어떤 경로로 노출되는가) · 물리 모드(어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가). `#focus=<서비스 id>` 로 특정 노드를 연다 |

인증이 없으므로 접근 통제가 전부다. 공개 라우트(Caddy `(public)`)나 터널에 절대 올리지 않는다.
- 접속 IP: 루프백·사설망·tailnet 만. 도커 대역은 Caddy 컨테이너에서 온 요청만, X-Forwarded-For 원 클라이언트가 허용 대역일 때만.
- Host 헤더: 이 기기의 IP·호스트명·`<호스트명>.local`·tailnet 이름·설정의 `hosts`(pit5: `jarvis.example.com`)·모니터로 가는 Caddy 라우트만(DNS 리바인딩 차단, 그 밖은 421).
- Cloudflare 헤더가 붙은 요청은 403(터널로 잘못 노출돼도 열리지 않게).
- Caddy `@mon` 은 `remote_ip` 로 tailnet 대역만 받는다(:443 이 모든 인터페이스에 열려 있어서).
- 동시 연결 48개·소켓 타임아웃 15초. 정적 경로의 제어 문자·역슬래시·인코딩된 슬래시는 400, 점 파일은 404.
- 홈·맵은 엄격한 CSP(`script-src 'self'`, 인라인 스크립트·이벤트 핸들러 금지). 공용 `JARVIS.html` 템플릿은 자동 이스케이프하고
  href/src 에 안전한 URL 만 넣으며, 이벤트 핸들러·따옴표 없는 속성·script/style 본문에는 값을 넣지 않는다.

## 운영 — Linux (systemd 사용자 유닛)

모니터는 도커가 아니라 **호스트 프로세스**다. 도커가 멈춰도 모니터는 살아서 "Docker 다운"을 보여 줘야 하고,
호스트 전체(프로세스·cgroup·소켓·PMIC)를 봐야 하므로 컨테이너 격리가 이득이 없다.
Linux 기기(s2·pit5·pi5-s3·cm5-1)는 모두 systemd 사용자 유닛 `jarvis`(deploy/jarvis.service)로 돈다.
새 Linux 기기는 macOS 와 같은 `install.sh` 한 줄로 설치한다(아래 macOS 절). 유닛만 venv 의 python 을 부르는 사용자 유닛으로 만들고 linger 를 켠다.

```sh
systemctl --user status jarvis           # 상태
systemctl --user restart jarvis          # 파이썬을 고쳤을 때 (정적 파일은 재시작 불필요)
journalctl --user -u jarvis -n 50        # 수집 실패만 찍힌다(접근 로그는 남기지 않음)
curl -s localhost:9300/api/health
```

개발 기기(s2 처럼 저장소에서 바로 도는 기기)의 처음 설치. 운영 기기는 `install.sh` 를 쓴다(실행본은 app 에 둔다):

```sh
git clone https://github.com/thsvkd/jarvis.git ~/Workspace/jarvis && cd ~/Workspace/jarvis   # public 저장소라 인증이 필요 없다
install -Dm644 deploy/gateway.toml ~/.config/jarvis/config.toml     # core 는 deploy/s2.toml 형식(지금 core 는 thsvkd-macmini), pit5 는 deploy/pit5.toml
mkdir -p ~/.config/systemd/user   # 유닛의 작업 폴더를 app 대신 이 저장소로
sed 's#^WorkingDirectory=.*#WorkingDirectory=%h/Workspace/jarvis#' deploy/jarvis.service > ~/.config/systemd/user/jarvis.service
loginctl enable-linger && systemctl --user daemon-reload && systemctl --user enable --now jarvis
# ss -p 로 root 소유 리스너의 프로세스를 보려면: pi 그룹만 실행할 수 있는 능력 부여 사본(없으면 10초마다 sudo 를 시도한다)
sudo install -d /usr/local/libexec/jarvis
sudo install -o root -g pi -m 0750 /usr/bin/ss /usr/local/libexec/jarvis/ss
sudo setcap cap_sys_ptrace,cap_dac_read_search+ep /usr/local/libexec/jarvis/ss
```

- 히스토리: `~/.local/state/jarvis/history.json` (5분마다, 종료 시 저장).
- 컨테이너·root 프로세스 메모리는 PSS 를 읽을 권한이 없어 RSS 합(상한)으로 센다. pi 소유 프로세스는 PSS.
- pm2 로 띄우고 싶은 기기는 `deploy/ecosystem.config.cjs`(앱 이름 `jarvis`)를 쓴다.
- pit5 재부팅 가드는 사용자 유닛도 기록·복구하고, 모니터는 `127.0.0.1:9300/api/snapshot` 으로 읽는다.
- pm2 앱에서 옮길 때는 먼저 `pm2 delete jarvis && pm2 save`(부팅 때 resurrect 가 :9300 을 두고 부딪히지 않게) 뒤 유닛을 켠다.

Caddy 에 부담을 주지 않으려고:
- 설정은 admin API 대신 컨테이너 안 `/config/caddy/autosave.json` 을 읽는다(admin API 를 두드리면 Caddy 로그에 요청 줄이 쌓인다).
- 접근 로그는 docker API follow 스트림 하나로 받는다(`docker logs --since` 반복은 dockerd 가 로그 파일을 매번 훑는다).
- 프로브(User-Agent `jarvis/…`)는 Caddyfile `log_skip @jarvis_probe` 로 접근 로그에서 빠지고, 집계에서도 뺀다.
- 라우트 프로브는 60초마다, Cloudflare 경유 공개 프로브는 2분마다(앱 레이트 리밋 예산 보호).
- 컨테이너 구성이나 Caddyfile이 바뀌면 10초 수집에서 라우트 재수집을 깨운다. 새·변경 라우트의 이전 프로브·DNS·실패 횟수는 버린다.
- Caddy 버전은 라우트와 함께 다시 읽고, cloudflared 프로세스가 재시작되면 이전 ingress 캐시를 비운다.

화면 검사:

```sh
node tools/shot.mjs http://127.0.0.1:9300/ /tmp/shots/home --sizes 1440x900,390x844 --full --wait 6000 --dark
```

## 운영 — macOS (launchd 또는 pm2)

**실동작 미검증.** macOS 경로(`platform/darwin.py`·`svc/launchd.py`·`hw/macmon.py`)는 명령 출력 fixture 로 파서만 검증했고,
s2(Linux)에서 `sys.platform` 을 darwin 으로 바꿔 크래시 없이 스냅샷이 나오는 것까지만 봤다. Mac Studio 에서 한 번 확인해야 할 것:

- `launchctl print system` 의 services 블록 형식(열 순서: PID·마지막 종료 코드·라벨로 가정)과 `launchctl list` 출력
- 비 root 에서 `psutil.net_connections` 가 AccessDenied 를 내고 `lsof -F pcPn` 폴백으로 가는지, 남의 프로세스 리스너가 얼마나 보이는지
- Docker Desktop·OrbStack 게시 포트가 컨테이너로 묶이는지(게시 포트 번호로 매칭), 컨테이너 CPU·메모리(Docker stats API)
- `macmon pipe -s 1` 의 JSON 키(temp.cpu_temp_avg·gpu_usage·sys_power)
- APFS 볼륨 목록(`/`·`/System/Volumes/Data`·`/Volumes/*` 만 남기는지), 인터페이스 이름(en0·utun·bridge)

설치(새 Mac·Linux 포함. git·Homebrew 가 없어도 된다):

```sh
curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --core 100.107.29.19
```

릴리스에도 설치기가 자산으로 올라가 있어 `https://github.com/thsvkd/jarvis/releases/latest/download/install.sh` 로 받아도 같다(릴리스 본문에 한 줄 안내가 있다. `tools/release.py`).

`install.sh` 는 uv 로 Python venv(`~/.local/share/jarvis/venv`)를 만들고, 최신 릴리스 묶음을 sha256 으로 확인해 `~/.local/share/jarvis/app` 에 푼다.
그다음 설정 파일과 LaunchAgent(`dev.jarvis`, Linux 는 systemd 사용자 유닛 `jarvis`)를 만들고, 모듈(훅·셋업)을 plan 으로 보인 뒤 그 plan_id 로 apply 한다.
이미 있는 설정·LaunchAgent·유닛·코드는 덮어쓰지 않으므로 다시 실행해도 된다. 옛 배치가 있으면 [실행본 이전](#실행본-이전-180)을 한다. Tailscale·하네스 로그인과 core 의 `devices` 등록은 직접 한다.

**Claude 계정 저장 방식(macOS).** Claude Code 는 macOS 에서 로그인을 키체인에 두고, 키체인은 로그인 세션 안에서만 열린다. 그래서 hswap 이 키체인 저장 방식(`credentials.store=auto`)이면 로그인 세션이 없을 때(재부팅 뒤 로그인 전, LaunchDaemon, SSH) Claude 사용량 조회·로그인 유지·계정 전환이 모두 실패한다.
- `install.sh` 는 LaunchAgent 설치 끝에 `python -m jarvis.macos_daemon store --report` 로 저장 방식과 `hswap claude doctor` 의 문제를 출력한다. 자동 로그인이 꺼져 있고 저장 방식이 파일이 아니면 "재부팅 뒤 로그인 전에는 Claude 사용량·전환이 되지 않습니다"라고 경고한다. 설치는 계속한다.
- 세션 없이도 쓰려면 키체인이 열린 곳(그 기기의 화면 세션, 또는 `security unlock-keychain` 을 먼저 실행한 셸)에서 파일 저장 방식으로 옮긴다: `hswap claude migrate-store --to file --keep keychain --dry-run` 으로 계획을 본 뒤 같은 명령을 `--dry-run` 없이 실행한다. 자격은 `~/.claude/.credentials.json`(0600 평문)과 슬롯 `.enc` 에 남는다. 키체인의 저장 시 암호화를 잃으므로 FileVault 를 켜 두는 것을 권한다.
- 파일 저장 방식에서도 로그인 세션의 Claude Code 가 로그인을 키체인에 다시 쓸 수 있다. hswap 0.3.0 의 reconcile 예약(`hswap claude reconcile --install-service`, gui LaunchAgent `dev.hswap.reconcile`)이 로그인 세션에서 그것을 파일로 정리한다. 정리 전에는 화면에 "키체인에 Claude 로그인이 있거나 확인하지 못해 자동 갱신을 멈췄습니다"가 보이고, 뒤에 doctor 가 답한 예약 상태에 따라 "로그인 세션에서 reconcile 이 정리합니다"(예약이 설치·적재됨) 또는 "hswap claude reconcile --install-service 로 정리 예약을 설치하세요"가 붙는다. 지난 reconcile 이 어느 쪽이 새 로그인인지 몰랐으면(`reconcile_ambiguous`) 어느 쪽이 최근에 쓰였는지 확인하고 사람이 `migrate-store --to file --keep file|keychain` 으로 골라야 한다고 보이고, hswap 이 남긴 힌트(`lastReconcileHint`, 영문 300자까지)를 함께 보인다. 처음 파일 방식으로 옮길 때만 `--keep keychain` 이 정해진 답이다. doctor 가 `keepalive_ahead_too_short`(예약의 ahead 600초 이하)를 내면 "keepalive 예약의 갱신 여유가 너무 짧습니다. 다시 설치하세요(hswap claude keepalive --install-service)"를 보인다. jarvis 가 예약을 다시 설치할 때는 600초 이하인 옛 ahead 를 이어받지 않고 7200초를 쓴다(hswap 0.3.0 이 거절하기 때문이다).
- 게이트웨이는 macOS 에서 `hswap claude doctor --json`(hswap 0.3.0 이상, 키체인 없이 돈다. 그 전 hswap 은 `credentials.store` 설정만 읽는다)을 시작할 때와 그 뒤 1시간마다, 로그인 세션 밖이면 5분마다 읽어 사용량 화면의 기기 행에 저장 방식, 문제, 키체인 백업 개수를 보인다. 세션 안의 키체인 저장 방식은 흔한 정상이라 `not_file_store` 는 세션 밖에서만 문제로 보인다. 로그인 세션 밖(`launchctl managername` 이 Aqua 가 아님)인데 저장 방식이 파일이 아니면 슬롯마다의 "자격 증명이 없습니다" 대신 "로그인 세션 밖에서 실행 중 — 키체인을 읽을 수 없습니다. 파일 저장 방식으로 바꾸세요(hswap claude migrate-store --to file --keep keychain)"를 보인다.
- 로그인 세션 밖이고 파일 저장 방식이며 적재된 hswap keepalive 예약(gui·system)이 없으면, 게이트웨이가 Linux 처럼 5분마다 `hswap claude keepalive` 와(자동 전환을 켰으면) `auto --once` 를 부른다. 로그인 세션 안이면 이 tick 을 띄우지 않고, 예약이 적재돼 있으면 두 번 돌지 않게 부르지 않는다. 자동 전환을 켜고 끄는 화면 버튼은 hswap 이 만든 gui 예약의 `--then-auto` 를 함께 고친다. 데몬 keepalive(`--install-service --system`)는 관리자 권한이 필요해 jarvis 가 바꾸지 않고, 적재돼 있으면 실행할 명령을 알리며(아무것도 쓰지 않는다), 적재되지 않아 tick 이 도는 동안은 전략만 바꾼다(gui 예약을 설치하지 않는다).

제거는 같은 설치기에 `--uninstall` 을 준다(다른 인자와 함께 쓰지 않는다):

```sh
curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --uninstall
```

서비스 등록(LaunchAgent·systemd 사용자 유닛)과 실행본(`~/.local/share/jarvis` 의 `app`·`venv`·`versions`·`daemon-src`·`current.json`)만 지운다. 이 사용자의 LaunchDaemon 이면 `deploy/macos-daemon.sh uninstall`(sudo)로 먼저 에이전트로 되돌리고, 그것이 실패하면 아무것도 지우지 않고 멈춘다. 다른 사용자의 daemon 은 건드리지 않는다.
**남기는 것:** `~/.config/jarvis/config.toml`(다시 설치하면 이어진다), 훅·셋업 모듈이 쓴 `~/.claude` 설정(필요하면 `python -m jarvis module <hooks|setup> uninstall` 을 설치를 지우기 전에 먼저 실행한다), 하네스, Tailscale, Linux 의 linger, 개발 저장소 `~/Workspace/jarvis`.
Windows 는 관리자 PowerShell 에서 `install.ps1 -Uninstall`(`-DryRun` 으로 지울 것만 본다)이 예약 작업 `jarvis`·방화벽 규칙 `jarvis-9300`·`%LOCALAPPDATA%\jarvis` 의 `app`·`versions`·`current.json` 을 지우고 설정과 Python 은 남긴다.

수동 설치:

```sh
brew install python && python3 -m pip install psutil     # Python 3.11+ (시스템 python3 는 3.9 라 설정 파일을 못 읽는다)
python3 -m pip install pystray pillow                      # 선택: 메뉴 막대 아이콘(plist 의 JARVIS_TRAY=1)
brew install macmon                                        # 선택: 온도·GPU·전력
mkdir -p ~/.config/jarvis && cp deploy/macstudio.toml ~/.config/jarvis/config.toml
# launchd(LaunchAgent, 로그인 세션에서 돈다)
sed -e "s#/Users/USER/.local/share/jarvis/app#$PWD#" -e "s#/Users/USER#$HOME#g" deploy/jarvis.plist > ~/Library/LaunchAgents/dev.jarvis.plist   # 작업 폴더는 이 저장소
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.jarvis.plist
launchctl kickstart -k gui/$(id -u)/dev.jarvis            # 코드를 고친 뒤 재시작(메뉴 막대 아이콘의 '재시작'도 같다)
launchctl bootout gui/$(id -u)/dev.jarvis                 # 잠시 끄기(다음 로그인 또는 bootstrap 까지). 아이콘 메뉴에는 종료가 없다
# 또는 pm2: ecosystem.config.cjs 의 script 를 /opt/homebrew/bin/python3 로 바꿔 pm2 start, pm2 startup launchd
```

### LaunchDaemon 모드 (GUI 로그인 없는 Mac)

기본 LaunchAgent(`gui/<uid>/dev.jarvis`)는 GUI 로그인 세션이 있어야 뜬다. CLI·SSH 로만 쓰고 자동 로그인을 끈 Mac 은 시스템 도메인의 LaunchDaemon(`system/dev.jarvis`, 사용자 권한)으로 돌린다. 선택 사항이고, 플래그를 주지 않으면 지금까지와 같다.

```sh
# 새 기기는 --core 도 준다. 이미 설치한 기기도 같은 한 줄이다(app 에 스크립트가 없으면 ~/.local/share/jarvis/daemon-src 에 받는다)
curl -fsSL https://raw.githubusercontent.com/thsvkd/jarvis/main/install.sh | sh -s -- --daemon
# Claude Code 는 있는데 hswap 이 아직 파일 저장 방식이 아니면(새 기기는 hswap 도 아직 없다) 아래 저장 방식 검사가 거절한다.
# 키체인 저장 방식 그대로 옮기려면 --allow-keychain-store 를 덧붙인다(그대로 macos-daemon.sh 로 넘긴다)

# 스크립트 위치: ~/.local/share/jarvis/app/deploy, 없으면 ~/.local/share/jarvis/daemon-src/deploy(설치가 끝날 때 경로를 출력한다)
# 사용자로 실행한다. sudo 는 처음에 한 번만 묻는다
bash deploy/macos-daemon.sh install --dry-run   # 렌더한 plist 와 실행할 명령만 본다(sudo 도 묻지 않는다)
bash deploy/macos-daemon.sh install             # LaunchAgent → LaunchDaemon(Claude Code 가 있으면 hswap 파일 저장 방식일 때만)
bash deploy/macos-daemon.sh install --allow-keychain-store   # 키체인 저장 방식 그대로 옮긴다(Claude 는 로그인 세션이 있을 때만 된다)
bash deploy/macos-daemon.sh install --no-reconcile           # hswap reconcile 예약을 설치하지 않는다(기본은 --with-reconcile)
bash deploy/macos-daemon.sh status              # 어느 쪽이 로드됐는지, health
bash deploy/macos-daemon.sh uninstall           # LaunchDaemon → LaunchAgent
```

- **Claude 저장 방식 검사:** Claude Code 가 있는 기기(`~/.local/bin`·`/opt/homebrew/bin`·`/usr/local/bin` 의 `claude`)에서 `install` 은 sudo 를 묻기 전에(`--dry-run` 도) `hswap claude doctor --json` 으로 저장 방식을 읽는다(doctor 가 없는 옛 hswap 은 `hswap claude config --json` 의 `credentials.store`). 파일이 아니거나 확인하지 못하면 이유와 `hswap claude migrate-store --to file --keep keychain` 명령을 보이고 아무것도 바꾸지 않은 채 끝난다. LaunchDaemon 은 로그인 세션 밖이라 키체인을 읽지 못해서다. `--allow-keychain-store` 로만 넘어간다. hswap 이 아직 없는 새 기기는 거절하지 않는다(hswap 은 게이트웨이가 처음 돌 때 설치한다): daemon 으로 옮긴 뒤 "남은 일"로 hswap 설치 뒤 `migrate-store` 를 실행하라고 안내하고 종료 코드 3 으로 끝나며, `install.sh --daemon` 은 모듈 설치까지 마친 뒤 같은 3 으로 끝난다. 옮긴 뒤 `macos-daemon.sh install` 을 다시 실행하면 reconcile 예약을 설치한다. 전환이 끝난 뒤의 Ctrl-C 는 전환을 되돌리지 않는다. 파일 저장 방식이면 전환이 끝난 뒤 `hswap claude reconcile --install-service --json` 으로 reconcile 예약(gui)을 설치한다(이미 있으면 건너뛰고, GUI 세션이 없으면 다음 로그인 때 적재되며, 실패해도 전환은 되돌리지 않고 실행할 명령을 알린다). 저장 방식을 옮기는 방법은 위 [Claude 계정 저장 방식](#운영--macos-launchd-또는-pm2)을 본다.
- `/Library/LaunchDaemons/dev.jarvis.plist` 는 에이전트 plist 를 기준으로 렌더한다(`python -m jarvis.macos_daemon render`). 허용한 키(`Label`·`ProgramArguments`·`Program`·`WorkingDirectory`·`EnvironmentVariables`·`RunAtLoad`·`KeepAlive`·`ThrottleInterval`·`ExitTimeOut`·`ProcessType`·`SoftResourceLimits`·`HardResourceLimits`·`Nice`)만 옮기고, 나머지(`Sockets`·`MachServices`·`UserName` 등)는 버리고 이름을 알린다. 고친 ProgramArguments·WorkingDirectory·환경 변수는 남고, `UserName`·`GroupName`·`InitGroups`·`HOME`·`USER`·`LOGNAME` 이 더해지며 `KeepAlive`·`RunAtLoad` 는 true 가 된다. 이 파일은 누구나 읽을 수 있으므로 비밀로 보이는 환경 변수 이름이나 인자(TOKEN·SECRET·PASSWORD·`_KEY` 등)가 있으면 렌더를 거절한다. 이름으로만 판별하므로 그 밖의 비밀은 직접 뺀다.
- 에이전트 plist 는 `~/.local/share/jarvis/dev.jarvis.plist.agent` 로 옮긴다(남겨 두면 다음 GUI 로그인 때 두 번째 jarvis 가 같은 포트를 잡으려 한다). `uninstall` 이 되돌린다.
- `install` 은 어느 단계에서든 실패하거나 Ctrl-C 로 중단하면 daemon 을 내리고 LaunchAgent 로 되돌린 뒤 0 이 아닌 코드로 끝난다. GUI 세션이 없으면 에이전트는 다음 GUI 로그인 때까지 뜨지 않고, 스크립트가 그렇게 알린다(`uninstall` 도 같다). 렌더 결과가 같으면 다시 실행해도 아무것도 하지 않는다.
- SSH 연결이 끊겨도 전환은 멈추지 않고 끝까지 돈다(끊긴 뒤에는 sudo 권한이 사라질 수 있어서다). 뒤 단계가 실패했는데 sudo 가 없거나 bootout 이 실패해 daemon 을 내리지 못하면 에이전트를 띄우지 않고 daemon 과 plist 를 그대로 둔다. 다시 접속해 `status` 로 확인하고 필요하면 `uninstall` 한다.
- 스크립트는 PATH 를 시스템 폴더로 고정하고 sudo 아래 명령을 절대경로(`/usr/bin/install`·`/bin/launchctl`·`/bin/rm`)로만 부른다. 설치한 뒤 bootstrap 전에 root 소유 사본의 소유·권한(root:wheel 0644), `UserName`, `GroupName`, `InitGroups`, `StandardOutPath`·`StandardErrorPath`, `Label`, 허용 목록 밖의 최상위 키, 렌더 직후의 sha256 을 검사하고, 다르면 지운 뒤 되돌린다. 그래도 렌더 코드와 스크립트는 사용자 쓰기 가능한 파일이므로, 같은 uid 프로세스를 믿지 않는 기기에서는 실행 전에 두 파일의 해시를 확인한다.
- 시스템 도메인의 `dev.jarvis` 는 기기에 하나다. 기존 daemon plist 의 `UserName` 이 지금 사용자와 다르면 스크립트는 아무것도 바꾸지 않고 거절하고, `install.sh` 는 안내만 하고 LaunchAgent 로 설치한다(`--daemon` 을 줘도 같다). `system/dev.jarvis` 가 로드돼 있는데 plist 가 없거나 `launchctl print` 의 `path` 가 그 plist 가 아니면 소유를 알 수 없으므로 스크립트가 거절한다. `install.sh` 는 plist 만 보므로, 플래그 없이는 LaunchAgent 로 설치하고 `--daemon` 이면 스크립트의 거절로 실패한다.
- 이 사용자의 daemon 으로 등록된 기기에서 `install.sh` 를 플래그 없이 다시 실행해도 LaunchAgent 를 만들지 않는다. [실행본 이전](#실행본-이전-180)은 root 소유 등록을 고칠 수 없어 `noop` 과 안내로 끝난다.
- **메뉴 막대 아이콘이 없다.** GUI 세션이 없으면 AppKit 이 프로세스를 죽일 수 있어 `JARVIS_TRAY` 를 뺀다.
- **로그:** `~/Library/Logs/jarvis.log`(LaunchAgent 와 같은 파일). launchd 는 `StandardOutPath` 를 root 로 열 수 있어 둘 다 `/dev/null` 로 두고, 사용자 권한으로 바뀐 뒤 셸이 이 파일에 덧붙인 다음 `<python> -m jarvis` 로 exec 한다.
- **SSH(core 로 쓸 때):** ssh-agent·키체인이 없다. passphrase 없는 키 파일만 쓸 수 있다.
- **키체인에 의존하는 도구는 안 될 수 있다.** 파일 저장소를 쓰는 도구는 그대로 돈다. Claude 는 hswap 파일 저장 방식이면 게이트웨이가 keepalive·자동 전환을 직접 부른다(위 macOS 절).
- **Claude keepalive 는 jarvis 가 돌린다.** daemon 모드의 게이트웨이는 로그인 세션 밖이라, 파일 저장 방식이고 적재된 hswap keepalive 예약이 없으면 5분마다 `hswap claude keepalive` 를 직접 부른다. 그래서 hswap 의 system keepalive 데몬(`keepalive --install-service --system`, sudo)은 따로 설치하지 않아도 된다. hswap doctor 는 jarvis tick 을 보지 못해 `keepalive_service_missing` 을 내지만, 화면은 tick 이 대신 도는 동안 그것을 문제로 세지 않고 "jarvis 가 5분마다 keepalive 를 돌립니다"를 보인다. `migrate-store --to file` 은 keepalive 예약이 없으면 gui 에이전트(`dev.hswap.keepalive`)를 설치하고, 세션이 없으면 다음 로그인 때 적재된다며 종료 코드 3(성공)으로 끝난다. 그 에이전트는 GUI 로그인 때 적재돼 돈다. 게이트웨이 tick 은 돌 때마다 doctor 를 다시 읽어 예약이 적재돼 있으면 쉬므로, 겹치는 것은 로그인 직후 다음 tick(최대 5분)까지다. 겹쳐도 keepalive 는 hswap 잠금(슬롯의 consume 잠금, 엔진 잠금, Claude Code 자격 잠금, 기본 10초 대기)을 차례로 쥐어 한 번에 하나만 갱신한다. 뒤에 온 실행은 보통 새 토큰을 보고 그냥 끝나고, 앞 실행이 10초를 넘기면 `locked` 실패를 남겨 다음 실행까지 "자동 갱신 실패(다른 프로세스가 잠금을 쥐고 있음)"가 보일 수 있다. 로그아웃해 에이전트가 내려가면 tick 이 다시 맡는다. 데몬 프로세스에서 doctor 가 gui 예약의 적재 상태를 맞게 답하는지는 실기기에서 확인하지 않았다.
- **사용자 작업 목록:** 시스템 도메인에서는 `launchctl list` 대신 `launchctl print gui/<uid>` 로 읽고, GUI 세션이 없으면 사용자 작업이 없는 것으로 보인다.
- **업데이트 버튼:** `KeepAlive=true` 와 사용자 권한이면 그대로 돈다(서비스 정의 조건은 위 SSH 기기 절의 확인 명령을 daemon 경로로 본다). 게이트웨이 코드 경로는 `~/.local/share/jarvis` 의 런처·`versions` 그대로다.

## 운영 — Windows (게이트웨이)

Windows 는 게이트웨이로만 돈다: 호스트 자원(CPU·메모리·디스크·네트워크·프로세스).
서비스 관리자·Docker·Caddy 수집은 없다. SSH 세션에서 띄운 프로세스는 세션이 끝나면 죽으므로 예약 작업(로그온 시)으로 돌린다.

새 기기는 관리자 PowerShell 에서 한 줄로 설치한다. Python 이 없으면 winget 으로 깔고, 최신 릴리스를 sha256 으로 확인해 `%LOCALAPPDATA%\jarvis\app` 에 푼 뒤 아래 `deploy\install-windows.ps1` 과 모듈(훅·셋업) 적용까지 한다. `-DryRun` 은 할 일만 보인다.

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/thsvkd/jarvis/main/install.ps1))) -Core 100.107.29.19
```

인자가 없으면 `irm https://github.com/thsvkd/jarvis/releases/latest/download/install.ps1 | iex` 로도 된다.

```powershell
# 저장소: git clone https://github.com/thsvkd/jarvis.git (갱신은 core 의 jarvis 업데이트 버튼이 GitHub 릴리스를 받는다)
winget install -e --id Python.Python.3.13 --scope user          # python 3.11+ (WindowsApps 의 python.exe 는 안내용 껍데기)
cd $env:USERPROFILE\Workspace\jarvis                           # 손으로 설치하는 저장소(install.ps1 설치본은 $env:LOCALAPPDATA\jarvis\app)
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Python "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
Stop-ScheduledTask jarvis; Start-ScheduledTask jarvis           # 코드 갱신 뒤 재시작(알림 영역 아이콘의 '재시작'도 같다)
Disable-ScheduledTask jarvis; Stop-ScheduledTask jarvis         # 잠시 끄기(Stop 만 하면 5분 반복 트리거가 다시 켠다). 되돌리기: Enable-ScheduledTask jarvis; Start-ScheduledTask jarvis
Get-Content -Encoding UTF8 $env:LOCALAPPDATA\jarvis\jarvis.log -Tail 20
```

## 실행본 이전 (1.8.0)

1.8.0 부터 실행본은 `~/.local/share/jarvis/app`(Windows `%LOCALAPPDATA%\jarvis\app`)에 둔다. 그 전 설치기가 `~/Workspace/jarvis` 에 푼 실행본(.git 없음)은
macOS·Linux 는 게이트웨이가 새 릴리스로 60초를 버틴 뒤 스스로 옮긴다(업데이트 버튼만 누르면 된다).
**Windows 는 업데이트 버튼만으로는 옮겨지지 않는다.** 게이트웨이가 관리자 권한 없이(RunLevel Limited) 돌아 예약 작업을 고칠 수 없으므로,
관리자 PowerShell 에서 `install.ps1` 을 다시 실행하거나 app·versions 의 python 으로 `python -m jarvis relocate` 를 실행한다. 계약은 `docs/SPEC.md` 의 "실행본 배치와 이전" 절이다.

- `~/Workspace/jarvis` 에 `.git` 이 있으면(개발 체크아웃) 아무것도 하지 않는다. 서비스 등록이 그 폴더를 가리키지 않아도(pm2·수동) 그대로다.
- 옛 폴더를 app 으로 복사하고 LaunchAgent plist·systemd 유닛·예약 작업 `jarvis` 가 app 을 가리키게 고친다. 원본 파일은 `~/.local/share/jarvis/<파일 이름>.bak-relocate` 에 남는다.
- 옛 폴더는 지우지 않고 `~/.local/share/jarvis/workspace-old` 로 옮긴다. 서비스 관리자가 실제로 app 을 쓰고, 등록에 옛 경로가 남지 않았고, 옛 폴더에서 도는 프로세스가 없을 때만 옮긴다.
- **macOS 는 지연 완료다.** launchd 는 plist 를 다시 읽기 전까지(로그인·재부팅·bootout/bootstrap) 옛 폴더에서 띄우므로, 그때까지 옛 폴더를 남긴다(`pending_reload`).
  jarvis 는 원격 기기가 꺼질 수 있어 스스로 다시 읽히지 않는다. 바로 끝내려면 그 기기에서:

      launchctl bootout gui/$(id -u)/dev.jarvis; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.jarvis.plist

  다음 기동이 60초 뒤 옛 폴더를 옮긴다. `install.sh` 를 다시 실행해도 같은 일을 한다.
- Linux·Windows 는 재시작을 강제하지 않는다. 다음 기동부터 app 에서 뜬다. 단 Linux 에서 `install.sh` 가 이번에 유닛을 옮겼다면 게이트웨이를 한 번 `try-restart` 하고 health 를 기다린 뒤 옛 폴더를 정리한다.
- 설치기를 다시 실행해도 옛 base 를 그대로 app 으로 옮긴다(새 릴리스를 app 에 풀지 않는다. 롤백 기준이 바뀌지 않게). 이전 코드는 임시 폴더에 받은 최신 릴리스의 것을 쓴다.
- 한 번에 하나만 돈다(`~/.local/share/jarvis/.relocate.lock`). 다른 이전이 도는 중이면 `busy` 로 끝나고 아무것도 바꾸지 않는다.

```sh
$PY -m jarvis relocate --dry-run      # 할 일만 본다
$PY -m jarvis relocate --json         # 지금 한 번 돈다: {"state": noop|busy|registered|pending_reload|cleaned|failed, "detail", "steps"}
```

되돌리기: `workspace-old` 를 `~/Workspace/jarvis` 로 옮기고, `~/.local/share/jarvis/<파일 이름>.bak-relocate` 를 원래 자리(`~/Library/LaunchAgents/`·`~/.config/systemd/user/`)로 되돌린다(Linux 는 `systemctl --user daemon-reload`,
Windows 는 백업 파일 대신 옛 폴더에서 `deploy\install-windows.ps1` 을 다시 실행한다).
게이트웨이 로그에는 기동마다 `[jarvis] 실행본 이전: <state> <detail>` 한 줄이 남는다.
