# Jarvis API

모든 엔드포인트는 읽기 전용 GET 이고, LAN(사설 대역)·tailnet(100.64/10, fd7a:115c:a1e0::/48)·루프백에서만 열린다.

| 경로 | 내용 | 주기 |
|---|---|---|
| `GET /api/snapshot` | 전체 상태 한 덩어리 (~130KB, gzip ~23KB) | 항목별로 2·10·60·300초마다 갱신. 1초 캐시 |
| `GET /api/history?range=1h` | 5초 간격 720점 | |
| `GET /api/history?range=24h` | 1분 간격 1440점 (재시작해도 `~/.local/state/jarvis` 에 보존) | |
| `GET /api/health` | `{ok, version}` | |
| `GET /api/orca` | 이 기기 Orca 페어링 코드 `{installed, running, endpoint, codes:{desktop, mobile}, issuable, issue, update}`. `codes.*` 는 `{url, device_id, pending, name, paired_at, last_seen_at}` 또는 `null`(그 scope 기기가 없음). 대기 기기(`pending:true`)가 먼저이고, 없으면 가장 최근에 페어링된 기기(`pending:false`, 그 기기와 자격을 나눠 쓴다). `update` 는 진행 중인 Orca 업데이트 또는 `null` | 설정 `core` 의 tailnet IP 만(루프백·LAN 포함 그 밖은 403). 캐시 없음. 스냅샷에는 없다 |
| `POST /api/orca/issue?scope=desktop\|mobile` | 그 scope 의 Orca 기기가 하나도 없을 때(처음 한 번)만 Orca 를 잠시 내리고 serve 를 그 모드로 한 번 띄워 코드를 만든 뒤 다시 띄운다(Linux orca-serve 유닛 · macOS LaunchAgent · Windows 데스크톱 앱). 202 로 시작하고 진행은 `/api/orca` 의 `issue`, 발급 가능한 scope 는 `issuable`. 진행 중 409, 실패 뒤 15분 429 | 설정 `core` 만. 헤더 `X-Jarvis-Action: orca-issue` 필수(CSRF) |
| `POST /api/orca/update` | Linux deb(`orca-ide`)만. 공식 릴리스가 더 새로우면 deb 를 받아 sha512 를 확인한 뒤 `orca-serve` 를 멈추고 `sudo -n apt install -y -- <deb>` 로 설치하고 다시 띄운다. 이어서 `orca-ide skills update --all`. 이미 최신이면 패키지는 건너뛰고 스킬만 갱신한다. 202 로 시작하고 진행은 `/api/orca` 의 `update`(`phase`: manifest·download·install·skills, `app`: current·updated, `from_version`, `to_version`). deb 가 아니거나 유닛이 없으면 409 | 설정 `core` 만. 헤더 `X-Jarvis-Action: orca-update` 필수 |
| `GET /api/orca/update` | 읽기 전용 `{supported, installed, latest, current, error}`. 설치본과 공식 릴리스를 비교한다. 패키지를 받거나 서비스를 멈추지 않는다. `current` 는 설치본이 릴리스와 같거나 더 새로울 때. 지원하지 않으면 `supported:false` 와 이유 | 설정 `core` 의 tailnet IP 만. 캐시 없음 |
| `GET /api/devices` | core 전용(설정 `devices`). 기기별 요약 `{generated_at, devices:[{id, url, reachable, ok_at, error, fails, host, version, status, alerts, services, model, cpu_pct, mem_pct, disk_pct}]}` | 10초마다 각 게이트웨이 `/api/snapshot` 을 pull. 게이트웨이(core 가 아닌 jarvis)는 404 |
| `GET /api/devices/<id>/snapshot` · `/history?range=` · `/changes` | core 전용. 그 기기 응답 그대로. snapshot 은 마지막 성공본(없으면 503), 나머지는 요청 시 전달(실패 502) | |
| `GET /api/devices/<id>/orca` | core 전용. 페어링 버튼: 그 기기 `/api/orca` 에 QR 행렬 `codes.*.qr`(행마다 `"0101…"`)과 `supported` 를 붙인다. 옛 게이트웨이(404)·core 미지정(403)은 `{supported:false, error}` | 설정 `viewers` 의 tailnet IP 만(루프백·LAN 포함 그 밖은 403). 설정 `proxies` 의 프록시를 거친 요청은 그 프록시가 붙인 X-Forwarded-For 마지막 항목으로 판정. 캐시 없음 |
| `POST /api/devices/<id>/orca/issue?scope=` | core 전용. 화면의 발급 버튼: 그 게이트웨이 `POST /api/orca/issue` 로 전달 | `viewers`(proxies 판정 포함) 만. 헤더 `X-Jarvis-Action: orca-issue` 필수 |
| `GET /api/devices/<id>/orca/update` | core 전용. 업데이트 확인: 그 게이트웨이 `GET /api/orca/update` 로 전달. 옛 게이트웨이(404)는 501 `{error}`(갱신 필요) | `viewers`. 캐시 없음 |
| `POST /api/devices/<id>/orca/update` | core 전용. 그 기기 Orca 업데이트. 게이트웨이 `POST /api/orca/update` 로 전달 | `viewers`. 헤더 `X-Jarvis-Action: orca-update` 필수 |
| `POST /api/devices/orca/update` | core 전용. 목록의 기기마다 업데이트를 시작한다. `{devices:[{id, status, ...}]}`. 한 대 실패가 나머지를 멈추지 않는다 | `viewers`. 헤더 `X-Jarvis-Action: orca-update` 필수 |
| `GET /api/changes` | 최근 구성 변경 최대 200건 `{changes:[...]}` | 10초. 30초마다 저장 및 종료 시 저장 |

클라이언트는 `/shared/jarvis.js` 의 `JARVIS.subscribe()` / `JARVIS.history()` 를 쓴다.
시간은 전부 **epoch 초(float)**, 크기는 **바이트**, 속도는 **바이트/초**, 비율은 **0~100 퍼센트**다.
어떤 값이든 `null` 일 수 있다(첫 수집 전, 수집 실패, 해당 없음). 화면은 `–` 로 표시하고 절대 깨지면 안 된다.

접근 통제: Host 헤더는 이 기기의 IP·호스트명(`<호스트명>`, `<호스트명>.local`, tailnet 이름)·설정의 `hosts`(예: `jarvis.example.com`)·이 서비스로 가는 Caddy 라우트만 받는다(그 밖은 421).
Cloudflare 헤더(`Cf-Ray`, `Cf-Connecting-Ip`)가 붙은 요청은 403. 도커 대역에서는 Caddy 컨테이너만 받는다.

## 상태 어휘

- 서비스 `status`: `up` 정상 · `degraded` 저하 · `down` 다운 · `unknown` 확인 불가
- 전체 `summary.status`: `ok` · `warn` · `crit` (경보 중 가장 높은 등급)
- 경보 `level`: `crit` 심각 · `warn` 경고 · `info` 정보
- 리스너 `scope`(바인딩 주소 기준 도달 범위): `all`(0.0.0.0/::, LAN+tailnet+docker+로컬) · `lan` · `tailnet` · `docker`(172.17.0.1 등, 컨테이너만) · `loopback`
- 트래픽 `via`: `tunnel`(평문 :80 + cloudflared 경유 + CF 헤더가 모두 맞는 공개 요청) · `tailnet` · `lan` · `docker` · `local`.
  모니터 자신의 프로브(User-Agent `jarvis/…`)는 집계에서 빠진다.

## 최상위 키

```
meta        호스트명·버전·생성시각·수집 시각·수집 오류·접속 URL
summary     전체 상태와 개수 요약 (헤더/히어로용)
alerts[]    경보 목록 (심각도 내림차순)
categories[] 서비스 카테고리 7종과 상태별 개수
services[]  "무엇이 떠 있는가" — 통합 서비스 목록 (카테고리·상태순 정렬)
system      하드웨어: CPU·GPU·메모리·온도·팬·전력·클럭·디스크·네트워크·프로세스
containers[] 도커 컨테이너 전부
projects[]  compose 프로젝트
units[]     서비스 관리자 유닛: Linux 는 systemd(시스템+사용자), macOS 는 launchd 작업(실행 중·실패·감시 대상)
listeners[] 열린 포트 전부(소유 주체·도달 범위 포함)
ingress     caddy(라우트·버전) · tunnel(Cloudflare) · traffic(Caddy 접근 로그 집계)
tailscale   tailnet 상태·피어
topology    네트워크 맵 — 개념 관점(서비스가 어떤 경로로 노출되는가)
topology_physical  네트워크 맵 — 물리 관점(어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가)
monitor     이 모니터 자신의 상태(버전·커밋·수집기별 최신성과 오류·자원)
pm2         pm2 데몬과 앱 목록 {available, daemon_pid, apps:[{name, pm_id, pid, status, restarts, unstable_restarts, uptime_s, cpu_pct, mem_bytes, exec, cwd, autorestart}]}
spark       최근 5분(5초 간격 60점) 시계열 — 스파크라인용
changes[]   새 컨테이너·배포·장비·유닛·PM2·LAN 이웃·Caddy 경로의 변경 이력
```

## meta

`changes[]`는 최신순 `{t, source, id, kind, before, after}`다. `kind`는 `added`·`removed`·`changed`,
`source`는 `containers`·`devices`·`units`·`pm2`·`lan`·`routes`다. 유닛 ID에는 system/user 범위가 붙는다.
첫 관측은 기준선이고 실패한 수집 결과는 삭제로 기록하지 않는다. LAN 이웃은 수동적인 ARP 관측이며
`online: null`은 캐시된 주소만 안다는 뜻이다. 원격 라우트는 유일한 Tailnet 장비에 매핑하고
최근 HTTP 검증으로 상태를 판정한다. 원격 호스트의 컨테이너나 배포 버전을 수집한다는 뜻은 아니다.

```js
{ host: "<호스트명>", version: "1.0.0", generated_at, started_at,
  intervals_s: {fast:2, medium:10, slow:60, dns:300},
  collected_at: { system_fast, system, docker, systemd, listeners, tailscale, tunnel, caddy_log, caddy, probes, hermes, dns, ... },
  errors: { "<수집기>": "메시지" },            // 비어 있으면 정상
  access: [{url:"https://jarvis.example.com/", audience:"tailnet"}, {url:"http://192.168.0.10:9300/", audience:"lan"}, ...],
  domain: "example.com" }
```

## summary

```js
{ status: "ok"|"warn"|"crit",
  alerts: {crit, warn, info},
  services: {total, up, degraded, down},
  public, tailnet, host, hermes,              // 카테고리별 서비스 수
  containers: {total, running, healthy, unhealthy},
  listeners: {total, lan, tailnet_only, loopback},   // TCP 기준
  requests_24h, requests_1h }
```

## alerts[]

```js
{ level: "crit"|"warn"|"info", source: "system"|"container"|"systemd"|"route"|"exposure"|"dns"|"cert"|"tunnel"|"caddy"|"traffic"|"tailscale"|"hermes"|"monitor",
  message: "한국어 문장", target: "대상 이름(호스트·컨테이너·유닛·서비스 id) 또는 null",
  key: "스냅샷 안에서 고유하고 시간이 지나도 같은 키", since: "이 경보가 처음 뜬 시각(epoch 초)" }
```
라우트·Hermes 경보는 **두 번 연속** 실패해야 뜬다(한 번 튄 실패는 서비스 상태에만 "재확인 중"으로 보인다).
온도 경보는 3°C 내려가야 풀린다. 스왑 경보는 스왑 인이 있거나 가용 메모리가 15% 미만일 때만 경고, 아니면 정보.

```
```

## categories[] — 순서가 곧 표시 순서

| id | label | 무엇 |
|---|---|---|
| `public` | 공개 서비스 | 인터넷 → Cloudflare Tunnel → Caddy :80 → 앱 |
| `tailnet` | Tailnet 전용 | tailnet → `*.example.com`(→100.64.0.2) → Caddy :443 → 앱. 터널 경로에선 404 |
| `hermes` | Hermes | Hermes 에이전트 게이트웨이(상태 API, `hermes:gateway`)와 이름이 `hermes_re` 에 맞는 이 기기의 컨테이너·프로세스(`hermes:ctr:<이름>`·`hermes:<유닛>`) |
| `host` | 호스트 서버 | Caddy 를 거치지 않고 이 기기에서 직접 포트를 연 서버(orca-ide, usbipd …) |
| `internal` | 내부 백엔드 | DB·캐시 등 밖에 안 열린 컨테이너 |
| `infra` | 인프라 | Caddy·cloudflared·Tailscale·Docker·SSH·이 모니터 |
| `system` | 시스템 데몬 | cups·avahi 같은 OS 데몬 |

각 항목: `{id, label, desc, count, up, degraded, down, unknown}`

## services[]

```js
{
  id: "route:plz.example.com" | "ctr:jari-redis-1" | "host:usbipd.service" | "infra:caddy" | "hermes:gateway" ...,
  name: "plz",                 // 짧은 이름(서브도메인·컨테이너명·프로세스명)
  title: "plz-findme",         // 보조 이름(compose 프로젝트 등)
  category, kind,              // kind: web|static|database|cache|app|proxy|tunnel|vpn|runtime|remote-access|process-manager|server|agent-gateway|monitor
                               // static = Caddy 가 직접 응답하는 라우트(예: 루트 도메인 example.com 의 ads.txt·플레이스홀더). runtime 은 null 이고 상태는 프로브로만 정한다
  status, status_reason,       // 사유는 한국어 문장 또는 null
  status_source: "runtime"|"probe",   // 라우트 전용: 상태를 정한 근거
  description,                 // Caddyfile 주석 등 (null 가능)
  hostnames: ["plz.example.com"],
  urls: [{url, audience: "public"|"tailnet"|"lan", code?, ms?, error?, label?}],   // 링크로 걸어도 되는 주소
  upstream: "plz-findme-app:8080",
  runtime: {                   // 실행 주체. type 별로 필드가 조금 다르다
    type: "container"|"systemd"|"pm2"|"process"|"remote",   // pm2: pm2 가 관리하는 앱(state = online|stopped|errored|launching…)
    name, project?, image?, container_id?, image_id?, revision?, state, health?, uptime_s, started_at, restart_count, cpu_pct, mem_bytes, pid?, ip?, peer_id?, port?, online?
  } | null,
  listeners: [{proto, port, addrs, scope, reach, process}],
  exposure: { public: bool, tailnet: bool, lan: bool, local_only: bool,
              via: ["cloudflare-tunnel","caddy-https","direct-lan","direct-tailnet","tailnet-node"] },
  probes: {                    // 라우트 서비스(public/tailnet) 전용. 각 {ok, code, ms, error, caddy_fallback, t, tls_error, fail_streak, cf_ray}
    internal: Caddy :443 경유(=tailnet 사용자 경로),
    tunnel:   Caddy :80 경유(=터널 경로; 비공개 서비스는 404 가 정상),
    public:   공개 DNS→Cloudflare 엣지 경유(공개 서비스만)
  },
  dns: {answers: [IPv4...], cnames: [...], points_to: "cloudflare"|"tailnet"|"none"|"other"|"error"},   // 공개 리졸버가 보는 A 레코드 기준
  cert: {issuer, subject, not_after, days_left},       // 오리진(Caddy) 인증서
  edge_cert: {issuer, days_left},                      // Cloudflare 엣지 인증서 (공개만)
  in_tunnel: bool, snippet: "public"|"private",
  traffic: {requests_1h, requests_24h, errors_5xx_24h, errors_4xx_24h, p50_ms_24h, p95_ms_24h, last_ts},
  depends_on: ["ctr:jari-redis-1", ...],               // 같은 compose 프로젝트의 백엔드
  extra: {...}                                          // 인프라 전용: version, connections, edge_locations, routes, peers_online ...
}
```

## system

```js
{
  host: {hostname, model: "Raspberry Pi 5 Model B Rev 1.0", os, kernel, arch, cores: 4, cpu_max_mhz: 2400, gpu_name, gpu_mem_mb},
  uptime_s, boot_time,
  cpu: {usage_pct, per_core_pct: [..4], iowait_pct, freq_mhz, per_core_freq_mhz, governor, load: [1m,5m,15m],
        procs_running, threads_total, temp_c, cores, max_mhz},
  gpu: {usage_pct, queues: {bin, render, tfu, csd, cache_clean, cpu}, jobs_per_s, available, freq_mhz, name, mem_mb},
  memory: {total, available, used, free, cached, buffers, shared, usage_pct, swap_total, swap_used, swap_pct},
  thermal: {cpu_temp_c, pmic_temp_c, fan: {rpm, pwm, pct}, throttle: {raw:"0x0", ok, now: ["저전압",...], since_boot: [...]}},
  power: {total_w, input_v, core_volts, rails: [{name, volts, amps, watts}]},     // PMIC 추정 보드 전력
  clocks_mhz: {arm, core, v3d, isp, hevc},   // macOS(macmon)는 {gpu}. 없는 키는 빠진다
  // Raspberry Pi 가 아닌 기기: gpu.queues={}·pmic_temp_c·fan·throttle·input_v·core_volts 는 null, rails=[].
  // macOS 는 macmon 이 있으면 cpu_temp_c·gpu.usage_pct·power.total_w(시스템 전력)를 채운다. 화면은 null 인 계기를 숨긴다.
  disks: [{mount, device, fs, total, used, free, usage_pct}],
  disk_io: {read_bps, write_bps, devices: {mmcblk0: {read_bps, write_bps}}},
  network: {interfaces: [{name, kind: "wired"|"wifi"|"tailnet"|"docker"|"other", rx_bps, tx_bps, rx_total, tx_total,
                          ipv4, ipv6, up, speed_mbps, mac, wifi: {quality, signal_dbm}|null}],
            rx_bps, tx_bps,                 // 물리 인터페이스(유선+무선) 합
            tailnet_rx_bps, tailnet_tx_bps, docker_rx_bps, docker_tx_bps, lan_ips, tailnet_ip,
            neighbors: [{id, ip, mac, interface, state, online}]},
  processes: {count, threads, top_cpu: [{pid, name, cpu_pct, rss, threads, user, state /* psutil 상태: running|sleeping|disk-sleep|zombie|idle… */, cmd, owner_kind, owner}], top_mem: [...]}
             // Windows: threads(합계·프로세스별)·state·user 는 null(읽는 비용이 커서 읽지 않는다)
}
```
`cpu_pct` 는 코어 하나 기준(%, 4코어면 최대 400). 컨테이너·유닛 `mem_bytes` 는 소속 프로세스 메모리 합 — 읽을 수 있으면 PSS,
아니면(root·다른 사용자 프로세스) RSS 라 공유 페이지만큼 크게 나올 수 있다.
`memory.swap_in_ps` 는 초당 스왑 인 페이지 수(메모리 압박 지표).
이 모니터 자신은 Caddy 라우트(`jarvis.example.com`)가 있으면 `tailnet` 카테고리의 `route:jarvis.example.com`(runtime `pm2`, kind `monitor`)로,
없으면 `infra:monitor` 로 나온다. pm2 데몬은 인프라 `infra:pm2`(kind `process-manager`, extra.apps)다. pm2 앱이 연 포트는 `host:pm2:<앱 이름>` 서비스가 된다.
호스트 서버 id 는 유닛이면 `host:<유닛>`, 일반 프로세스면 `host:<프로세스명>:<가장 낮은 포트>` 다.

## containers[] / projects[] / units[] / listeners[]

```js
containers: {id, full_id, name, image, image_id, revision, state: "running"|"exited"|"restarting"|..., status_text, health: "healthy"|"unhealthy"|"starting"|null,
             health_last, started_at, uptime_s, restart_count, restart_policy, exit_code, oom_killed,
             project, service, project_dir, network_mode, networks, aliases, ports: [{ip, public, private, proto}], cpu_pct, mem_bytes, pids}
projects:   {name, dir, containers: [names], running, total, status: "running"|"partial"|"stopped"}
units:      {name, manager: "systemd"|"launchd", scope: "system"|"user", description, active: "active"|"failed"|..., sub, since, uptime_s, main_pid, restarts,
             enabled, cpu_pct, mem_bytes, category: "app"|"server"|"infra"|"system"|"desktop"}      // 실패 유닛이 맨 앞
listeners:  {proto: "tcp"|"udp", port, addrs, process, pid, owner_kind: "container"|"unit"|"user-unit"|"scope"|"unknown",
             owner, scope, reach: ["lan","tailnet","docker","local"], cwd}
```

## ingress

```js
{
  domain,
  caddy:  {container, version, config_ok, error, caddyfile_mtime, public_hosts, private_hosts,
           routes: [{host, upstreams, public, listen: [":443", ":80"], header_inject, snippet, comment}]},
  tunnel: {name: "<터널 이름>", container, metrics_ok, ready, connections, ready_connections, edge_locations: ["icn05","icn06"],
           total_requests, request_errors, responses_by_code: {"200": n, ...}, req_per_min, concurrent_requests,
           rtt_ms: [..], version, outdated: {current, latest}|null, hostnames: [...], ingress_version, ingress_updated,
           script_hosts, recent_errors: [{ts, level, msg}], metrics_port, process_start},
  traffic: {                   // Caddy 접근 로그 (24시간 창)
    last_1h:  {requests, by_status: {"2xx","3xx","4xx","5xx"}, by_via: {tunnel, tailnet, ...}, p50_ms, p95_ms,
               hosts: [{host, requests, errors_5xx, errors_4xx, p50_ms, p95_ms, known, last_ts}]},
    last_24h: {...같은 모양},
    per_minute: [60],  per_minute_5xx: [60],   // 최근 60분, 마지막이 현재 분
    per_hour: [24],                            // 최근 24시간, 마지막이 현재 시
    recent: [{ts, host, method, path, status, ms, bytes, proto, client, country, via}],   // 최신순 40건
    errors: [{ts, level, logger, msg, error}],                                            // Caddy 경고·오류
    stream: {connected, since, error}                                                     // 로그 스트림 상태
  }
}
```
`hosts[].known == false` 는 Caddy 에 없는 호스트(예: 제거된 서비스를 계속 두드리는 클라이언트)다.
미등록 호스트는 상위 8개만 이름이 남고 나머지는 `"(기타 미등록)"` 한 줄(`folded` = 묶인 수)로 합쳐진다.
**`recent[].path`, `host`, `client` 는 인터넷에서 온 값이다. 반드시 이스케이프해서 출력한다.**

## tailscale

```js
{ backend_state: "Running", version, tailnet, magic_dns_suffix, health: [], serve, funnel,
  self: {hostname, dns_name, ips, os, online, relay, key_expiry},
  peers: [{hostname, dns_name, ip, os, online, active, last_seen, last_seen_ago_s, connection: "direct"|"relay"|null,
           cur_addr, relay, rx_bytes, tx_bytes, tags, exit_node}],       // 온라인 먼저
  peers_online, peers_total }
```

## topology

```js
nodes: [{id, label, type: "zone"|"edge"|"infra"|"service"|"internal"|"host", layer: 0..5, status,
         category?, service_id?, detail?}]
edges: [{from, to, kind: "public"|"tailnet"|"lan"|"depends", label}]
```
`layer` 는 왼쪽→오른쪽 배치 힌트다: 0 영역(Internet·Tailnet·LAN) · 1 Cloudflare 엣지 · 2 cloudflared · 3 Caddy·SSH · 4 서비스·호스트 서버 · 5 내부 백엔드.

## topology_physical

```js
nodes: [{id, label, type, parent, status, detail?, service_ids?: [...], ...type별 필드}]
edges: [{from, to, kind, label}]
```
`parent` 는 **포함 관계**(중첩 상자로 그린다): 기기 ⊃ 인터페이스·Docker 엔진·호스트 프로세스, 엔진 ⊃ 도커 네트워크 ⊃ 컨테이너,
원격 기기 ⊃ 같은 기기 안의 다른 tailnet 노드. `edges` 는 **실제 연결**이다.

| type | id 예 | 필드 |
|---|---|---|
| `external` | `ext:internet`, `ext:cloudflare` | detail(엣지 위치) |
| `segment` | `seg:lan`, `seg:tailnet` | cidr, gateway / detail(온라인 수) |
| `device` | `dev:<호스트명>` (이 기기, `self:true`) | detail(모델), os, cores, mem_total |
| `iface` | `iface:eth0` | kind wired/wifi/tailnet, ips, speed_mbps, signal_dbm, rx_bps, tx_bps |
| `engine` | `engine:docker` | service_ids |
| `network` | `net:edge` | subnet, driver |
| `container` | `ctr:plz-findme-app` | service_ids, image(detail), networks, host_network, ports, project |
| `process` | `proc:ssh.service`, `proc:pm2:<앱>`, `proc:<프로세스명>:<포트>` | service_ids, unit, manager(systemd/pm2/null), detail(포트) — id 는 재시작해도 그대로 |
| `peer` | `peer:pi5-server2` | online, os, hostname, ip, lan_ip, on_lan, via_gateway, connection direct/relay/null, relay, last_seen, tags, same_device, service_ids |

엣지 `kind`: `wired`·`wifi`(인터페이스→LAN), `overlay`(tailscale0→Tailnet), `uplink`(LAN→인터넷, 게이트웨이 NAT),
`edge`(Cloudflare→인터넷), `tunnel`(cloudflared 컨테이너→Cloudflare, QUIC 연결 수), `bridge`(다중 네트워크 컨테이너→추가 네트워크),
`bind`(특정 인터페이스에만 바인딩한 프로세스→그 인터페이스, 포트), `tailnet-direct`·`tailnet-relay`(DERP 지역)·`tailnet-idle`(온라인이지만
지금 오가는 트래픽이 없어 경로 미정)·`offline`(피어→Tailnet), `lan`(같은 LAN 에서 직결되는 피어→LAN, 그 LAN IP).
피어에는 `lan_ip_last`(최근 30분 안에 직결했던 LAN 주소 — 유휴가 되어도 같은 기기 판정이 흔들리지 않게)가 있다. 피어의 LAN 주소가 공유기(게이트웨이) 주소면 NAT 너머라 `on_lan:false, via_gateway:true` 다. `last_seen` 은 Tailscale 이 온라인 피어에 대해
갱신하지 않으므로 오프라인 피어에만 의미가 있다.
`service_ids` 로 `services[]` 와 이어진다(인스펙터·홈 딥링크용). 같은 CurAddr IP 를 쓰는 tailnet 노드는 먼저 생긴 노드 안에 들어간다
(예: 서버 안의 Tailscale 사이드카 컨테이너).

## 화면 URL 계약

- 홈: `/` — `#svc=<서비스 id>` 로 그 서비스 상세 시트를 연다. 섹션 앵커(`#hardware` 등)는 홈이 정의한다.
- 네트워크 맵: `/map/?view=concept|physical` (`conceptual`·`phys` 도 받아서 정규화) — `#focus=<서비스 id | zone:* | 물리 노드 id>` 로 그 노드를 고르고 인스펙터를 연다.
  보기 선택은 localStorage `jarvis.map.view`(옛 `pit5.map.view` 도 읽는다)에 기억되고, URL 의 `view` 가 있으면 그게 이긴다.

## monitor (이 웹 서비스 자신)

```js
{ version, commit /* git describe --dirty: 커밋 안 된 수정이 있으면 '-dirty' */, pid, started_at, uptime_s, rss_bytes, threads, bind,
  config_path /* 읽은 설정 파일, 없으면 null */,
  snapshot_ms, snapshot_bytes, snapshot_gz_bytes,
  loops: {fast|medium|slow|dns|persist: {last_ms, last_at, interval_s}},
  collectors: [{name, age_s, limit_s, stale, error}], problems: [수집기 이름], ok }
```
같은 내용이 모니터 서비스(`kind:"monitor"`, 보통 `route:jarvis.example.com`)의 `extra.monitor` 에도 붙는다.
수집기가 오래되면(`stale`) 경보 `monitor` 가 뜨고 서비스 상태가 `degraded` 가 된다.

## spark / history

```js
{ interval_s, t: [...], cpu, gpu, mem, swap, temp, load, rx, tx, trx, ttx, dr, dw, pw, fan, mhz }
```
`rx/tx` 물리 NIC 바이트/초, `trx/ttx` tailnet, `dr/dw` 디스크, `pw` 와트, `fan` rpm, `mhz` 평균 CPU 클럭. 배열 길이는 `t` 와 같다.
점 시각 `t` 는 벽시계 5초(24h 는 60초) 경계에 맞춰져 있어 간격이 일정하다. 재시작 직후에는 점이 적을 수 있다.
