# Jarvis (옛 PIT5 Monitor)

저장소: `~/Workspace/jarvis` (systemd 사용자 유닛 `jarvis` 의 WorkingDirectory).
원본은 s2 의 이 저장소이고, 운영 기기(pit5 등)는 `git pull` 과 재시작만 한다.

기기 한 대를 통째로 보는 읽기 전용 대시보드. Python 수집기(표준 라이브러리 + psutil) + 정적 HTML(홈·네트워크 맵).
Linux(지금은 pit5 = Raspberry Pi 5 에서 운영)와 macOS(Mac Studio 로 옮길 예정)에서 같은 코드로 돈다.
기기마다 다른 값(인그레스 경로·컨테이너 이름·도메인 등)은 설정 파일로 준다([설정](#설정)).

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
| 보관: 시안 갤러리·초안 5종 | `/gallery/`, `/d/<slug>/` (제품 아님, 유지보수하지 않음) |

인증이 없으므로 접근 통제가 전부다. 공개 라우트(Caddy `(public)`)나 터널에 절대 올리지 않는다.
- 접속 IP: 루프백·사설망·tailnet 만. 도커 대역은 Caddy 컨테이너에서 온 요청만, X-Forwarded-For 원 클라이언트가 허용 대역일 때만.
- Host 헤더: 이 기기의 IP·호스트명·`<호스트명>.local`·tailnet 이름·설정의 `hosts`(pit5: `jarvis.example.com`)·모니터로 가는 Caddy 라우트만(DNS 리바인딩 차단, 그 밖은 421).
- Cloudflare 헤더가 붙은 요청은 403(터널로 잘못 노출돼도 열리지 않게).
- Caddy `@mon` 은 `remote_ip` 로 tailnet 대역만 받는다(:443 이 모든 인터페이스에 열려 있어서).
- 동시 연결 48개·소켓 타임아웃 15초. 정적 경로의 제어 문자·역슬래시·인코딩된 슬래시는 400, 점 파일은 404.
- 홈·맵은 엄격한 CSP(`script-src 'self'`, 인라인 스크립트·이벤트 핸들러 금지). 공용 `JARVIS.html` 템플릿은 자동 이스케이프하고
  href/src 에 안전한 URL 만 넣으며, 이벤트 핸들러·따옴표 없는 속성·script/style 본문에는 값을 넣지 않는다.

## 무엇을 보나

- 하드웨어: CPU(전체·코어별·클럭·부하·온도), 메모리·스왑, 디스크 용량·I/O, 네트워크(LAN·tailnet·도커).
  Raspberry Pi 면 GPU(V3D 큐별 사용률·클럭)·PMIC 보드 전력·5V 입력·팬·스로틀링, Apple Silicon 이면 macmon 이 있을 때 온도·GPU·시스템 전력.
  기기에 없는 계기는 화면에서 숨긴다
- 서비스 7개 카테고리: 공개(Cloudflare Tunnel) · Tailnet 전용(Caddy :443) · Hermes · 호스트 서버(직접 포트) · 내부 백엔드 · 인프라 · 시스템 데몬
- 라우트별 3경로 프로브: Caddy :443(tailnet 경로) · Caddy :80(터널 경로 — 비공개는 404 여야 함) · 공개 DNS→Cloudflare(공개만), 인증서 잔여일, 공개 DNS 가 가리키는 곳
- Caddy 활성 설정 vs Caddyfile vs 터널 ingress vs create-public-tunnel.sh 드리프트
- Caddy adapter로 해석한 파일과 활성 라우트의 호스트·업스트림·수신 포트 비교. 같은 도메인의 배포 대상 변경도 감지
- 원격 Caddy 업스트림을 Tailscale 피어에 연결하고 HTTP 응답과 실행 위치를 표시. 원격 호스트 내부의 컨테이너·자원은 수집하지 않는다
- LAN 이웃 테이블(ip neigh / arp)에서 관측한 기기. STALE 캐시는 온라인 증거로 사용하지 않는다. 통신하지 않은 기기는 발견되지 않을 수 있다
- 구성 변경 최근 200건(컨테이너·라우트·Tailscale·LAN 기기·유닛·PM2). 이미지 ID·OCI revision으로 같은 태그의 재배포도 구분
- Caddy 접근 로그 24시간(호스트별·상태별·경로별), cloudflared 연결·엣지·RTT·버전
- Tailscale 피어, Hermes 게이트웨이 상태 API(어댑터별 연결)와 이 기기의 Hermes 컨테이너·프로세스(2026-10-13 OpenClaw 에서 Hermes 로 옮김)
- 열린 포트 전부(바인딩 범위·소유 프로세스), 컨테이너·compose·서비스 관리자 유닛(systemd·launchd), 경보
- 물리 구성: 기기 ⊃ 인터페이스·Docker 엔진 ⊃ 도커 네트워크 ⊃ 컨테이너, LAN(게이트웨이)·tailnet 과 다른 기기(같은 기기 안 노드 묶음)
- 이 모니터 자신: 버전·커밋·수집기별 최신성과 오류·루프 시간·스냅샷 크기(멈추면 경보)

## 구조

```
jarvis/          수집기 + HTTP 서버 (python3 -m jarvis)
  config.py      설정: 환경 변수 > 설정 파일(TOML) > 기본값
  system.py      호스트 자원(psutil) — CPU·메모리·디스크·네트워크·프로세스
  platform/      운영체제별 구현. common.py(psutil·Docker API 기본값) ← linux.py(cgroup·ss·ip·sysfs) / darwin.py(route·lsof·sysctl)
  hw/            기기 전용 선택 수집기: rpi.py(vcgencmd·V3D·팬), macmon.py(Apple Silicon 온도·GPU·전력)
  svc/           서비스 관리자: systemd.py / launchd.py (같은 모양의 유닛 목록)
  runtime.py     docker API(unix socket)·pm2·리스닝 소켓 → 소유 주체
  ingress.py     Caddy autosave.json·접근 로그, cloudflared metrics/로그, 프로브, DoH
  tailnet.py     tailscale status, Hermes 게이트웨이
  model.py       스냅샷 조립: 서비스 분류·경보·토폴로지
  collector.py   2/10/60/300초 주기 스레드, 히스토리(1h@5s, 24h@1m → ~/.local/state/jarvis), 경보 since·연속 실패 카운트
  server.py      /api/*, 정적 파일, 접근 대역 제한
static/
  index.html     홈(개요, Bento)
  map/           서비스 네트워크 맵(Atlas)
  app/           제품 자산: shell.*(공통 헤더·디자인 토큰·테마), theme-init.js, bento.*, focus-layout.js·focus.js·focus.css(운영 Focus), atlas.*
  shared/        jarvis.js(공용 클라이언트, 전역 JARVIS), API.md(스냅샷 스키마), favicon
                 화면 설정은 localStorage `jarvis.*` 에 두고, 이름을 바꾸기 전 `pit5.*` 값도 이어 읽는다
  gallery/, d/   시안 비교 단계의 보관본
  lab/           Bento 콘텐츠 구성 랩 03: 요약·업무 탭·서비스 우선·경보 우선·분류 접기 5종 + Focus 개선판 실시간 데모
tests/           파서·플랫폼 층·스냅샷 조립 단위 테스트, tests/data/ 는 명령 출력 fixture
tools/shot.mjs   헤드리스 chromium 스크린샷·콘솔 오류·가로 넘침 검사 (CDP 직접, 의존성 없음)
tools/check-lab.mjs  디자인 랩 갤러리·6개 시안의 모바일·데스크톱 탐색과 동일 스냅샷 비교 검증
tools/snapcheck.py  시험 포트로 기동해 /api/snapshot 스키마를 기준선과 비교
deploy/          systemd 사용자 유닛(Linux)·pm2 ecosystem, launchd plist(macOS), 기기별 설정(s2.toml·pit5.toml·gateway.toml·macstudio.toml)
```

의존성: Python 3.11+(설정 파일을 읽는 tomllib), psutil. 선택: pystray·Pillow(Windows 알림 영역·macOS 메뉴 막대 아이콘, `JARVIS_TRAY=1` 일 때). Linux 에서 쓰면 좋은 것: iproute2(`ip`·`ss`), docker, tailscale, pm2.

## 운영 홈 — Focus

`/`는 선택한 Focus 개선판을 사용한다. 기존 Bento 카드·계기·상세 창을 재사용한다.
서비스를 개수 제한 없이 고정하고, 전체 보기·검색·카테고리로 모든 서비스에 접근한다.
고정 목록 밖의 저하·다운·확인 불가 항목은 별도 상세 바로가기로 표시한다.
맵·하드웨어·네트워크·런타임 상세는 접고 제목에 실제 수치·수집 오류/지연·최근 1시간 변경 건수를 표시한다.
모바일에서는 분류별 막대·운영 통계와 경보 상세를 선택해 펼친다.

서비스 고정·전체 보기·분야별 접기·모바일 통계·경보 펼치기는 localStorage `jarvis.home.focus.*`에 저장한다.
기존 테마·지도 관점·검색 필터의 저장 경로를 유지한다. 랩은 별도 sessionStorage를 사용한다.

```sh
node tools/check-home.mjs http://100.64.0.2:9300 /tmp/jarvis-home-check
```

같은 브라우저 프로필에서 데스크톱→모바일 재접속 시 설정 복원, 검색·필터·상세·경보 이동,
폴링 후 고정 목록 유지, 실제 수집 오류 키·누락 값·연결 지연을 검사한다.

## 디자인 랩 — 가독성 시안 비교

`/lab/`에서 기존 Bento 기반 5종과 Focus 개선판을 실시간 데이터로 비교한다. 개별 데모·후보 저장·나란히 비교를 제공하며
기존 Bento의 디자인과 렌더러를 재사용하며 콘텐츠 순서·탭·접기만 달리한다. 운영 홈은 기존 주소 `/`에 있다.
같은 Jarvis 서버에서 제공하므로 LAN·Tailnet 접근 제한도 동일하다. [시안별 설계·검증 방법](static/lab/README.md).
`/lab/usage/` 는 랩 04(AI 구독 사용량 배치 A·B·C, 샘플 데이터·시뮬레이션 새로고침)다. 운영 화면은 C 를 골라 자세히 모드를 더했다.

```sh
node tools/check-lab.mjs http://100.64.0.2:9300 /tmp/jarvis-lab-check
```

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
`core`(이 기기 Orca 페어링 코드 `/api/orca` 를 받아 갈 core 기기 tailnet IP 목록. 루프백은 받지 않는다) ·
`devices`(core 역할: 모을 게이트웨이 `[{id, url}, …]`, 설정 파일 전용·재시작 필요) · `viewers`(core 홈의 Orca 페어링 버튼을 누를 수 있는 tailnet IP) · `proxies`(viewers 판정에서 X-Forwarded-For 마지막 항목을 믿을 리버스 프록시 tailnet IP. 도메인을 받는 pit5 Caddy). `core`·`viewers` 는 호스트명이 아니라 tailnet IP 로 적는다(루프백·LAN IP 는 적어도 받지 않는다. core 가 자기 자신도 모으면 그 `url` 도 tailnet IP 로 적는다).
core 홈(`/`) 위쪽에 기기 목록과 페어링 버튼이 생기고, `/?device=<id>`·`/map/?device=<id>` 로 그 기기 화면을 연다.

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

## 운영 — Linux (systemd 사용자 유닛)

모니터는 도커가 아니라 **호스트 프로세스**다. 도커가 멈춰도 모니터는 살아서 "Docker 다운"을 보여 줘야 하고,
호스트 전체(프로세스·cgroup·소켓·PMIC)를 봐야 하므로 컨테이너 격리가 이득이 없다.
Linux 기기(s2·pit5·pi5-s3·cm5-1)는 모두 systemd 사용자 유닛 `jarvis`(deploy/jarvis.service)로 돈다.

```sh
systemctl --user status jarvis           # 상태
systemctl --user restart jarvis          # 파이썬을 고쳤을 때 (정적 파일은 재시작 불필요)
journalctl --user -u jarvis -n 50        # 수집 실패만 찍힌다(접근 로그는 남기지 않음)
curl -s localhost:9300/api/health
```

처음 설치:

```sh
git clone https://github.com/thsvkd/jarvis.git ~/Workspace/jarvis && cd ~/Workspace/jarvis   # GitHub 인증이 없으면 s2 에서 git push
install -Dm644 deploy/gateway.toml ~/.config/jarvis/config.toml     # s2 는 deploy/s2.toml(core), pit5 는 deploy/pit5.toml
install -Dm644 deploy/jarvis.service ~/.config/systemd/user/jarvis.service
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

설치:

```sh
brew install python && python3 -m pip install psutil     # Python 3.11+ (시스템 python3 는 3.9 라 설정 파일을 못 읽는다)
python3 -m pip install pystray pillow                      # 선택: 메뉴 막대 아이콘(plist 의 JARVIS_TRAY=1)
brew install macmon                                        # 선택: 온도·GPU·전력
mkdir -p ~/.config/jarvis && cp deploy/macstudio.toml ~/.config/jarvis/config.toml
# launchd(LaunchAgent, 로그인 세션에서 돈다)
sed -e "s#/Users/USER#$HOME#g" deploy/jarvis.plist > ~/Library/LaunchAgents/dev.jarvis.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.jarvis.plist
launchctl kickstart -k gui/$(id -u)/dev.jarvis            # 코드를 고친 뒤 재시작(메뉴 막대 아이콘의 '재시작'도 같다)
launchctl bootout gui/$(id -u)/dev.jarvis                 # 잠시 끄기(다음 로그인 또는 bootstrap 까지). 아이콘 메뉴에는 종료가 없다
# 또는 pm2: ecosystem.config.cjs 의 script 를 /opt/homebrew/bin/python3 로 바꿔 pm2 start, pm2 startup launchd
```

## 운영 — Windows (게이트웨이)

Windows 는 게이트웨이로만 돈다: Orca 데스크톱 앱(`%APPDATA%\orca`)의 페어링 코드와 호스트 자원(CPU·메모리·디스크·네트워크·프로세스).
서비스 관리자·Docker·Caddy 수집은 없다. SSH 세션에서 띄운 프로세스는 세션이 끝나면 죽으므로 예약 작업(로그온 시)으로 돌린다.

```powershell
# 저장소: GitHub 인증이 없으면 s2 에서 git push <host>:Workspace/jarvis main (receive.denyCurrentBranch=updateInstead)
winget install -e --id Python.Python.3.13 --scope user          # python 3.11+ (WindowsApps 의 python.exe 는 안내용 껍데기)
cd $env:USERPROFILE\Workspace\jarvis
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Python "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
Stop-ScheduledTask jarvis; Start-ScheduledTask jarvis           # 코드 갱신 뒤 재시작(알림 영역 아이콘의 '재시작'도 같다)
Disable-ScheduledTask jarvis; Stop-ScheduledTask jarvis         # 잠시 끄기(Stop 만 하면 5분 반복 트리거가 다시 켠다). 되돌리기: Enable-ScheduledTask jarvis; Start-ScheduledTask jarvis
Get-Content -Encoding UTF8 $env:LOCALAPPDATA\jarvis\jarvis.log -Tail 20
```

## 검증

Orca 업데이트는 core 홈의 기기 목록에서 **업데이트 확인 → 업데이트** 순서로 실행한다.
확인은 설치된 버전과 공식 stable 매니페스트를 비교하며 설치 권한·런타임 지원 여부와 독립적이다.
최신 기기의 업데이트 버튼은 비활성화된다. 진행 상태는 기기 타일에 표시하며 페어링 QR 은 페어링 버튼으로 연다.

| OS | 설치본 하나 | 업데이트 주체 |
| --- | --- | --- |
| Debian/Ubuntu | 공식 `orca-ide` deb, `/opt/Orca`, `orca-serve` 사용자 유닛 | Jarvis: GitHub 매니페스트 크기·SHA-512 검증 → sudo apt → 서비스·실행 버전 확인 |
| sudo 없는 기존 Linux | 같은 공식 deb, `~/.local/opt/orca/opt/Orca` | Jarvis: 검증한 deb 를 별도 폴더에 풀고 경로 교체 → 서비스·실행 버전 확인. 시스템 deb 로 통일하려면 관리자 설치가 필요 |
| macOS | `/Applications/Orca.app`, 그 앱의 CLI 를 쓰는 LaunchAgent | Orca 공식 supervised 업데이터의 check/download/install RPC → 새 버전 재연결 확인 |
| Windows | `%LOCALAPPDATA%\Programs\orca` 사용자 설치본 | Orca 공식 업데이터의 check/download/install RPC → 새 버전 재연결 확인 |

앱 갱신 후 설치본의 CLI 로 전역 Orca 스킬을 갱신한다. 앱과 스킬의 결과는 따로 표시한다.
열린 터미널은 별도 데몬이 소유하고 있으면 유지한 채 갱신한다. 다운로드 전과 설치 직전에 공개 RPC 의 전체 작업·터미널·데몬 조사를 대조한다.
데몬 밖에서 실행되는 터미널, 실행 중인 네이티브 채팅, 조사 누락은 보류한다. Linux 는 데몬과 그 작업이 서비스 종료 cgroup 밖에 있는지도 확인한다.
새 패키지의 호환성 계약에 모든 기존 데몬 프로토콜이 포함되어야 하며, 새 버전 재연결 후 같은 데몬 PID·시작 시각과 PTY 실행 식별자가 유지되어야 성공한다.
지원되는 런타임에서는 최종 조사 때 새 터미널 생성을 잠시 막고 끝나면 해제한다. 구형 1.4.224/232 는 이 fence 를 지원하지 않으므로 관찰한 기존 PTY 의 연속성만 검증하며, 최종 조사 이후 시작한 새 작업 전체를 원자적으로 보호한다고 보장하지 않는다.
Mac 은 공식 업데이터가 받은 ZIP 의 해시·버전·아키텍처와 계약을 확인한다. Windows 의 열린 세션과 함께 갱신할 때는 설치 EXE 안의 계약을 읽는 7-Zip 이 필요하다. 도구나 계약이 없으면 보류한다.
macOS 1.4.224 서버 모드는 Squirrel 설치 준비와 Orca 준비 확인이 서로 기다리는 문제가 있어 자동 갱신을 보류한다.
이 버전은 공식 ZIP 의 SHA-512·코드 서명·공증·데몬 호환성을 확인한 뒤 백업을 남겨 한 번 이전해야 한다. 1.4.237 은 서버 모드의 설치 준비 순서를 수정했다.
구형 런타임에서 `checking` 이 멈춘 경우 열린 창의 상태도 확인한다. 세션 조사 없이 데몬을 종료하거나 재시작하지 않는다.
Ubuntu 사용자 deb 의 이전 폴더는 새 버전 실행 검증에 성공한 뒤에만 정리한다. 검증 실패 시 `.previous` 를 복구용으로 남긴다.
Mac 의 Homebrew cask 와 앱 업데이터를 함께 설치 주체로 사용하지 않는다. Windows 도 사용자 설치 경로를 유지한다.
서비스를 관리하는 경로와 CLI 는 모두 같은 공식 설치본을 가리켜야 한다.

공식 계약: [업데이트 RPC](https://github.com/stablyai/orca/blob/main/src/main/runtime/rpc/methods/updater.ts),
[로컬 RPC 프레임](https://github.com/stablyai/orca/blob/main/src/cli/runtime/transport.ts),
[macOS 재시작 감독](https://github.com/stablyai/orca/blob/main/src/cli/runtime/serve-update-supervisor.ts).

core 홈의 **AI 구독 사용량** 섹션은 기기마다 Orca 가 로그인한 구독 계정(Claude·Codex·Grok·Antigravity 등)의 한도를 보여 준다.
게이트웨이 `GET /api/orca/usage`(설정 core 만)는 Orca 로컬 RPC `accounts.list` 의 rateLimits 를 프로바이더 → 계정 → 한도 창(5시간·주간·Fable 주간·월간)으로 정리한다.
같은 프로바이더의 다른 관리 계정은 `inactiveClaudeAccounts`·`inactiveCodexAccounts` 에서 읽는다. 계정은 이메일만 내보내고 홈 경로·조직 UUID·프로바이더 계정 ID 는 넣지 않는다.
Claude 는 그 기기에 [cswap](https://github.com/realiti4/claude-swap)(claude-swap)이 있으면 `cswap list --json`(schemaVersion 1)의 계정 목록을 쓴다.
cswap 으로 로그인을 바꿔 끼우면 Orca 는 그 로그인을 '시스템 기본 계정' 하나로만 보기 때문이다. 토큰 갱신·조회 간격은 cswap 이 맡고 jarvis 는 자격 증명 파일을 열지 않는다.
cswap 은 PATH 다음에 `~/.local/bin/cswap(.exe)` 에서 찾는다. 결과는 30초 묶으며, cswap 이 없거나 실패하면 Orca 값을 쓴다.
Orca 가 없거나 실패한 기기(관리자 권한 Orca 를 쓰는 Windows 등)도 cswap 의 Claude 계정은 보인다. Codex 는 Orca 관리 계정(Orca 설정의 계정)으로 계정별 사용량이 나온다.
프로바이더 API 를 다시 부르는 `refreshUsage` 는 5분에 한 번(새로고침 버튼은 30초 간격)만 쓰고, 그 사이에는 Orca 가 가진 값을 읽는다.
core `GET /api/devices/usage`(전체, `?refresh=1` 강제)·`/api/devices/<id>/usage` 는 모아 두지 않고 요청마다 게이트웨이에 묻는다.
화면은 디자인 랩 04(`/lab/usage/`)의 C 안이다. 간략히(칸마다 최대 사용률, 누르면 계정 상세)와 자세히(칸 안에 계정별 한도 창 전부) 보기를 전환하며, 선택은 localStorage `jarvis.usage.mode` 에 둔다.
공식 계약: [계정 RPC](https://github.com/stablyai/orca/blob/main/src/main/runtime/rpc/methods/accounts.ts), [사용량 형식](https://github.com/stablyai/orca/blob/main/src/shared/rate-limit-types.ts).

```sh
JARVIS_CONFIG=/dev/null python3 -m unittest discover -s tests   # 파서·플랫폼 층·스냅샷 조립(이 기기 설정 파일과 무관하게)
node tests/test_jarvis_js.mjs                             # 화면 공용 HTML 이스케이프(jarvis.js)
# 시험 포트로 기동해 스키마 비교(9300 은 쓰지 않는다). 기준선 fixtures/ 는 내부 정보라 git 에 없다.
JARVIS_CONFIG=deploy/pit5.toml python3 tools/snapcheck.py --port 9391 \
    --baseline fixtures/pit5-baseline-20260928 --known fixtures/s2-pre-refactor/known.txt
# 기기 환경이 바뀌어(컨테이너·서비스가 사라지는 등) 알려진 차이 목록이 낡으면 실패가 나온다.
# 목록은 반드시 리팩토링 전 코드로 같은 기기에서 다시 만든다(지금 코드로 만들면 회귀도 기기 차이로 묻힌다).
#   git archive 9432919 | tar -x -C "$TMP" && (cd "$TMP" && python3 tools/snapcheck.py --port 9393 --module pit5mon \
#       --baseline <이 저장소>/fixtures/pit5-baseline-20260928 --write-known <이 저장소>/fixtures/s2-pre-refactor/known.txt)
```
