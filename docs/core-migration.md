# core 전환

> README 에서 옮긴 상세 문서다(2026-11-10). 처음 설치는 [README 의 빠른 시작](../README.md#빠른-시작)을 본다.

## core 전환

core 를 다른 기기로 옮길 때 각 기기 설정의 `core`(그 기기를 조작할 수 있는 core 의 tailnet IP)를 바꾸는 절차다. 계약은 `docs/SPEC.md` 의 "core 전환" 절이다.
`core` 는 HTTP 로는 바꿀 수 없다. 각 기기의 셸 사용자가 `python -m jarvis core-trust` 로 바꾼다.

실행 위치와 python: 그 기기의 서비스가 쓰는 python 을 저장소 폴더에서 부른다.

| 기기 | 폴더 | python(아래 `$PY`) |
| --- | --- | --- |
| macOS | `cd ~/.local/share/jarvis/app`(개발 체크아웃 기기는 `~/Workspace/jarvis`) | `~/.local/share/jarvis/venv/bin/python`(install.sh 설치. 수동 설치면 LaunchAgent plist 의 python) |
| Linux | `cd ~/.local/share/jarvis/app`(개발 체크아웃 기기는 `~/Workspace/jarvis`) | `systemctl --user cat jarvis` 의 `ExecStart` 에 적힌 python |
| Windows | `cd $env:LOCALAPPDATA\jarvis\app`(개발 체크아웃 기기는 `$env:USERPROFILE\Workspace\jarvis`) | 예약 작업의 python: `(Get-ScheduledTask jarvis).Actions.Execute` 의 `pythonw.exe` 와 같은 폴더의 `python.exe` |

각 단계 전에 `$PY -m jarvis --version` 이 **1.7.11 이상**인지 본다. 그보다 옛 릴리스는 `core-trust` 를 모르고 게이트웨이를 하나 더 띄우려 한다.

```sh
$PY -m jarvis core-trust plan --add <새 core IP> --json                          # before·after·target.host·blocked·plan_id
$PY -m jarvis core-trust apply --add <새 core IP> --plan-id <plan_id> --json     # 같은 계획일 때만 core 한 줄을 바꾼다
```

- `target.host` 가 옮길 core 의 tailnet 호스트명(MagicDNS 이름, 예: `thsvkd-macmini.<tailnet>.ts.net`)인지 보고 승인한다. `blocked` 가 있으면 apply 는 `failed` 다.
- 설정 파일 소유자로 실행한다. `sudo` 로 실행하면 막힌다(바꾼 파일을 게이트웨이가 읽지 못하게 된다).
- `core` 줄의 꼬리 주석이나 여러 줄 배열처럼 막힌 사유는 손으로 고친 뒤 다시 plan 한다.
- 출력에는 `proxy_key` 의 값이 아니라 `set`/`unset` 만 나온다. 백업 파일은 만들지 않는다.

순서는 넓히기 → 확인 → 인그레스·proxy_key 회전 → 좁히기 → 옛 core 정리다.

0. **사전**: 옛 core 홈의 **jarvis 모두 업데이트**로 1.7.11 이상을 깐다. 꺼져 있거나 갱신하지 못한 기기는 미반영 목록에 적는다.
1. **새 core 준비(수동)**
   - `devices`·`viewers`·`hosts`·`proxies`·`hermes_url` 을 첫 테이블 앞 최상위에 적는다.
   - proxy_key 는 전환할 때마다 새로 만든다. 새 키는 화면·명령 인자·셸 히스토리에 남기지 않고 새 core 설정의 `proxy_key`(파일 권한 0600)에만 넣는다.
   - 서비스를 재시작한다(`devices` 는 재시작 키다). 이 시점에 게이트웨이 조작이 403 인 것은 정상이다.
2. **넓히기**: 새 core 의 `devices` 에 든 모든 기기(옛 core 와 새 core 자신 포함)에서 `plan --add <새 IP>` → `apply` 를 한다. 10초 안에 반영되며 재시작은 필요 없다.
3. **확인**: 새 core 의 `GET /api/devices/modules` 에서 기기별 `status` 를 본다.

   | 코드 | 뜻 | 할 일 |
   | --- | --- | --- |
   | 200 | 그 기기가 새 core 를 믿는다 | 5단계 대상 |
   | 403 | 믿지 않는다 | 2단계가 빠졌거나 서비스 환경이 다르다. 그 기기 `/api/snapshot` 의 `config_path` 를 CLI 출력의 `path` 와 대조한다 |
   | 501 | 판단할 수 없다(게이트웨이가 옛 버전) | 갱신한 뒤 다시 확인한다. 그때까지 미반영 |
   | 502 | 닿지 않는다 | 미반영 |

4. **인그레스·proxy_key 회전(pit5, 수동)**
   - ingress `.env` 의 `JARVIS_PROXY_KEY` 를 1단계의 새 키로 바꾼다. 이때도 화면에 내지 않는다.
   - Caddy upstream 을 새 core 로 바꾸고 Caddy 를 재생성한다.
   - 도메인 화면에서 viewer 전용 읽기(모듈 계획 보기)가 되면 `proxies`·`proxy_key` 가 맞은 것이다.
   - 실패하면 Caddy 만 되돌린다. 아직 두 IP 를 모두 믿으므로 다른 롤백은 필요 없다.
5. **좁히기**: 3단계에서 200 을 받은 기기에서만 `plan --remove <옛 IP>` → `apply` 를 한다. 결과에 유효한 tailnet IP 가 하나도 남지 않으면 막힌다.
6. **옛 core 정리(수동)**: 옛 core 설정에서 `devices`·`viewers`·`proxies`·`proxy_key` 줄을 지우고 재시작한다. 그 설정의 백업·사본에서도 `proxy_key` 줄을 지운다.
7. **미반영 기기**: 켜지거나 갱신되면 그 기기에만 2·3·5단계를 한다.
   - 그때까지 그 기기는 옛 core IP 를 믿는다.
   - 옛 core 기기를 재활용하거나 처분하기 전에 이 목록을 비운다. 그 tailnet IP 가 다른 기기에 가면 그 기기가 core 권한을 얻는다.

롤백:

- 5단계 전이면 두 IP 를 모두 믿으므로 Caddy 와 새 core 설정만 되돌린다.
- 5단계 뒤면 그 기기에서 `plan --add <옛 IP>` → `apply` 로 되돌린다.
- 실행 중에는 마지막 정상 설정을 유지하지만 재시작하면 `CONFIG_ERROR` 로 빈 신뢰가 된다. 그래서 CLI 는 다시 파싱해 검증된 파일만 교체한다. 설정을 손으로 고쳤다면 재시작 전에 `plan` 에 TOML 오류가 없는지 본다.

사례: 2026-11-04 의 s2(pi5-server2) → thsvkd-macmini 전환은 이 CLI 가 생기기 전이라 각 기기 설정을 손으로 고쳤다.
