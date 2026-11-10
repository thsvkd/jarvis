# 구조와 화면

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

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
  module.py      모듈 공통 계약의 CLI 와 모듈 목록(MODULES)
  modules/       doctor·hooks·setup·harness. 폴더마다 contract.py 가 계약 층(jarvis 안에서만 쓰고 설치본에 복사하지 않는다)이고, 나머지는 ai-setup 에서 옮긴 독립 실행 파일이다
static/
  index.html     홈(개요, Bento)
  map/           서비스 네트워크 맵(Atlas)
  setup/         AI 셋업: 기기별 모듈(훅·셋업·하네스·doctor) 상태 → 계획 → 실행 → 결과(API 는 docs/SPEC.md 의 "AI 셋업 화면")
  app/           제품 자산: tokens.css(디자인 토큰·라이트/다크 값), shell.*(공통 헤더·공용 유틸리티·테마), theme-init.js, bento.*, focus-layout.js·focus.js·focus.css(운영 Focus), atlas.*
  shared/        jarvis.js(공용 클라이언트, 전역 JARVIS), API.md(스냅샷 스키마), favicon
                 화면 설정은 localStorage `jarvis.*` 에 두고, 이름을 바꾸기 전 `pit5.*` 값도 이어 읽는다
tests/           파서·플랫폼 층·스냅샷 조립 단위 테스트, tests/data/ 는 명령 출력 fixture
tools/shot.mjs   헤드리스 chromium 스크린샷·콘솔 오류·가로 넘침 검사 (CDP 직접, 의존성 없음)
tools/snapcheck.py  시험 포트로 기동해 /api/snapshot 스키마를 기준선과 비교
deploy/          systemd 사용자 유닛(Linux)·pm2 ecosystem, launchd plist·LaunchDaemon 전환 스크립트(macOS), 기기별 설정(s2.toml·pit5.toml·gateway.toml·macstudio.toml)
```

의존성: Python 3.11+(설정 파일을 읽는 tomllib), psutil. 선택: pystray·Pillow(Windows 알림 영역·macOS 메뉴 막대 아이콘, `JARVIS_TRAY=1` 일 때). Linux 에서 쓰면 좋은 것: iproute2(`ip`·`ss`), docker, tailscale, pm2.

## 운영 홈 — Focus

`/`는 선택한 Focus 개선판을 사용한다. 기존 Bento 카드·계기·상세 창을 재사용한다.
서비스를 개수 제한 없이 고정하고, 전체 보기·검색·카테고리로 모든 서비스에 접근한다.
고정 목록 밖의 저하·다운·확인 불가 항목은 별도 상세 바로가기로 표시한다.
헤더의 탭 한 줄(개요·기기·AI 사용량·서비스·하드웨어·네트워크·런타임 | 네트워크 맵·AI 셋업)이 화면을 고른다.
`/` 안의 탭은 주소 해시(`#usage` 등)로 섹션 하나만 보이며 다시 불러오지 않는다. 해시가 없으면 개요다.
기기·AI 사용량 탭은 core 에서만 보인다. 맵·셋업 페이지는 개요에서 마지막으로 본 값(localStorage `jarvis.shellCore`)으로 판단한다.
페이지를 옮길 때 헤더는 마지막으로 받은 호스트(localStorage `jarvis.shellHost`)를 먼저 그리고 응답이 오면 바꾼다.
개요의 네트워크 맵은 접고 제목에 경로 수·문제 수를 표시한다. 수치는 갱신될 때 중간 값 없이 바로 최종 값으로 바뀐다.
모바일에서는 분류별 막대·운영 통계와 경보 상세를 선택해 펼친다.

서비스 고정·전체 보기·지도 접기·모바일 통계·경보 펼치기는 localStorage `jarvis.home.focus.*`에 저장한다.
기존 테마·지도 관점·검색 필터의 저장 경로를 유지한다.

```sh
node tools/check-home.mjs http://100.64.0.2:9300 /tmp/jarvis-home-check
```

같은 브라우저 프로필에서 데스크톱→모바일 재접속 시 설정 복원, 헤더 탭 이동(해시가 없으면 개요, 탭마다 섹션 하나),
검색·필터·상세·경보 이동, 폴링 후 고정 목록 유지, 지도 접기 저장, 실제 수집 오류 키·연결 지연을 검사한다.
