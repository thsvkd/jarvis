# 가드 정책

> 훅 모듈(`jarvis/modules/hooks/`)이 설치하는 안전장치의 설계 배경이다. ai-setup README 의 "가드 정책" 절(ai-setup 30a955b 이전)을 옮겼다. 설치·갱신·제거 계약은 `docs/SPEC.md` 의 "훅 모듈" 절이 소유한다. 아래의 `res/hooks/...`·`setup_hooks` 언급은 지금 `jarvis/modules/hooks/...`·`jarvis/hooks_module.py` 를 가리킨다.

## 설계 원칙 — 차단이 아니라 복구

이 훅의 가치는 위험한 명령을 막는 데 있지 않다. 실사용 로그 `~/.claude/hooks/guard.log` 10,329건을 전수 분석한 결과가 근거다.

| 판정 | 발동 | 진짜 위험한 명령 | 오탐·무의미 |
| --- | ---: | ---: | --- |
| `deny-fork-bomb` | 8 | **0** | 8 — 평범한 셸 함수 정의 `f(){ … \| grep … && … }` |
| `deny-pipe-to-shell` | 14 | **0** | 8 테스트 픽스처, 5 히어독 본문 오탐, 1 의도한 설치 스크립트 차단 |
| `deny-root-delete` | 13 | **0** | 13 전부 테스트 픽스처 |
| `deny-unexpandable-target` | 4 | **0** | 4 전부 글로브 미일치(`rm -f *.png`) |
| `ask-chained-delete` | 9 | **0** | 9 전부 임시 파일 정리 |
| `ask-find-delete` | 5 | **0** | 5 전부 `__pycache__` 정리 |
| **휴지통 치환** | **311** | — | 실제로 작동한 복구 기능 |
| **git 백업** | **22** | — | 실제로 작동한 복구 기능 |

deny/ask 53건 중 진짜 위험한 명령은 **0건**이었다. 반대로 복구 기능은 333건 작동했다. 게다가 오탐의 상당수는 훅이 **명령 문자열을 재작성하다** 생겼다 — 훅은 셸이 아니라서 `$VAR`·글로브·따옴표 확장을 흉내 낼 수 없는데도 인자를 다시 따옴표로 감싸 넘겼고(`rm -rf "$FOO/bar"` → `trash-put '$FOO/bar'`), 확장이 막혀 명령이 실패했다.

그래서 현재 설계는 셋뿐이다.

1. **명령 문자열을 재작성하지 않는다.** 삭제를 휴지통으로 돌리는 일은 shim이 한다. Bash 는 세션 시작 때 `PATH` 에 shim을 한 번 끼워 두고 명령은 건드리지 않는다. PowerShell 만 모듈을 불러오는 **선행 한 줄**을 붙인다. 인자 해석은 전적으로 셸/PowerShell 몫이다.
2. **git 백업도 명령을 건드리지 않는다.** 훅 프로세스가 직접 스냅샷 커밋을 만들고 원본 명령은 그대로 흘려보낸다.
3. **deny 는 되돌릴 수단이 아예 없는 두 부류뿐이다.** 루트/홈 전체 삭제와 디스크 파괴. 파싱하지 않고 명령 문자열의 꼴만 본다.

## 삭제 → 휴지통: PATH·모듈 shim

| 툴 | 방식 |
| --- | --- |
| Bash (POSIX 셸·Git Bash) | `SessionStart` 훅이 `CLAUDE_ENV_FILE` 에 `PATH` 맨 앞으로 `~/.claude/hooks/shims` 를 넣는 줄을 적는다. Claude Code 가 매 Bash 명령 전에 이 파일을 source 하므로 명령 문자열도 권한 판정도 건드리지 않는다. `shims/rm` 이 셸의 확장이 **끝난 뒤의 argv** 를 받는다 |
| PowerShell | `CLAUDE_ENV_FILE` 은 Bash 전용이라, `shims/RmShim.psm1` 을 `Import-Module` 하는 한 줄을 `updatedInput` 으로 붙인다(권한 판정 없이 — `allow` 를 붙이면 권한 프롬프트와 auto mode 분류기를 건너뛴다). 명령 우선순위가 별칭 > 함수 > cmdlet 이라 같은 이름의 함수가 `Remove-Item` cmdlet 을 가리고, `rm`/`del`/`erase`/`ri`/`rd`/`rmdir` 별칭이 전부 그 함수로 해석된다 |

셸이 이미 확장을 끝낸 뒤에 받으므로 **원본 명령과 해석이 어긋날 수 없다.** 종전에 확인을 요구하거나 차단하던 것들이 그냥 동작한다.

| 종전 | 지금 |
| --- | --- |
| `rm -rf "$FOO/bar"` → 확장이 깨져 실패 | ✅ 그대로 동작, 휴지통으로 |
| `rm -f *.nomatch` → 매칭 0개라 차단 | ✅ `rm -f` 의미 그대로 성공 |
| `find … -exec rm -rf {} +` → 확인 | ✅ shim을 타므로 휴지통으로 |
| `find … \| xargs rm -f` → 확인 | ✅ 같음 |
| 여러 줄·체이닝에 `rm` 이 섞임 → 확인 | ✅ 파싱하지 않으므로 무관 |
| 스크립트 내부의 `rm` → 훅이 못 봄 | ✅ `PATH` 로 해석되므로 커버 |
| 히어독 본문의 `rm -rf /` → 오탐 | ✅ 데이터로 남음 |

shim이 **원본 `rm` 에게 그대로 넘기는** 경우 — shim이 흉내 내면 의미가 달라지는 것들이다.

- `-i`/`-I` — 치환하면 프롬프트가 사라진다
- `.`/`..` 로 끝나는 대상 — `rm` 이 거부하는 형태다. 치환이 원본보다 더 지우면 안 된다
- 대상이 **전부** `/tmp` 등 tmpfs 아래 — 휴지통 자리도 같은 램이라 옮겨봐야 회수되지 않는다 (실측: 1.4GB 가 이렇게 묶여 스왑을 소진시킨 적이 있다). 하나라도 tmpfs 밖이면 전부 휴지통으로 간다
- 휴지통 도구 부재 — 경고를 남기고 실제 삭제한다. `rm` 자체를 못 쓰게 만드는 쪽이 더 해롭다는 판단이다
- `CLAUDE_NO_TRASH` 가 설정됨 — 명시적 탈출구

루트/홈 전체 삭제는 shim도 argv 수준에서 한 번 더 거부한다. `bash -c "rm -rf /"` 처럼 훅의 텍스트 규칙을 비껴가는 형태를 여기서 잡는다. 이때 홈은 환경 변수 `$HOME` 이 아니라 사용자 DB 의 홈이다 — 테스트가 `HOME` 을 임시 디렉터리로 바꿔 두고 `rm -rf "$HOME"` 으로 정리하는 것까지 막으면 안 된다.

**Bash 에 선행 줄을 붙이지 않는 이유**: 명령 앞에 줄을 붙이면 Claude Code 는 **바뀐 명령**에 대해 권한 규칙을 평가한다. 그래서 `Bash(rm *)` 같은 사용자 허용 규칙이 더 이상 맞지 않고, 그렇다고 `allow` 를 붙이면 `rm` 이라는 낱말만 섞여도 어떤 명령이든 자동 승인된다(둘 다 Claude Code 2.1.283 에서 실측).

**한계**: 세션 도중에 설치하면 다음 세션부터 shim이 적용된다(`SessionStart` 가 다시 불려야 한다). `sudo rm` 은 sudo 의 `secure_path` 가 `PATH` 를 버려 shim을 타지 않는다(실사용 10,329건 중 2건). `find … -delete` 처럼 `rm` 을 거치지 않는 삭제도 커버 밖이다.

## git — 미커밋 변경만 스냅샷

Claude Code 의 체크포인팅(`/rewind`)은 [공식 문서상](https://code.claude.com/docs/en/checkpointing) **"bash 명령이 바꾼 파일을 추적하지 않는다."** `git reset --hard` 로 날아간 작업은 `/rewind` 로 못 되돌린다. 그래서 이 부분은 훅이 계속 맡는다.

훅 프로세스가 임시 `GIT_INDEX_FILE` 에 `git add -A` → `git write-tree` → `git commit-tree` 로 스냅샷 커밋을 만들고 `refs/claude-backups/<timestamp>` 에 고정한 뒤, **원본 명령은 한 글자도 바꾸지 않고** 통과시킨다. `git stash create` 는 추적 안 된 파일을 담지 못해서(실측 확인) 스크래치 인덱스를 쓴다.

| 대상 | 근거 |
| --- | --- |
| `reset --hard`, `checkout`/`restore` (워킹트리), `clean -f*`, `worktree remove`, `checkout`/`switch --force` | 미커밋 변경은 git 어디에도 남지 않는다 |
| ~~`rebase`, `branch -D`, `commit --amend`, `filter-branch`, `gc --prune`, `tag -d`~~ | **제외** — reflog·`ORIG_HEAD` 가 이미 되돌려 준다 |
| ~~`stash drop`/`clear`~~ | **제외** — gc 유예기간 안에서 `git fsck --unreachable` 로 살릴 수 있다 |

- `clean -x`/`-X` 는 `.gitignore` 대상(`.env`, 로컬 설정)까지 지우므로 이 경우에만 `git add -Af` 로 무시된 파일까지 담는다. 단 **10,000개 또는 100MB 를 넘으면 무시된 파일은 포기**하고 추적/미추적만 담는다 — 실측(라즈베리파이 5)에서 한 저장소의 `.gitignore` 대상이 52,722개 1,244MB 였고, 이 기기의 해싱 처리량(100MB/2,000파일당 4.4초)으로는 훅이 1~2분을 잡아먹고 SD 카드에 1.2GB 를 쓴다. 규모 측정 자체는 169ms 로 싸다.
- 워킹트리가 `HEAD` 와 같거나 직전 스냅샷과 트리가 같으면 ref 를 만들지 않는다. `refs/claude-backups/` 는 14일이 지나면 정리된다.
- `cd <경로> && git reset --hard` 는 그 경로에서 백업한다. `cd "$VAR"` 처럼 대상을 특정할 수 없으면 엉뚱한 저장소를 백업하느니 아무것도 하지 않는다.
- `git worktree remove <경로>` 의 대상은 cwd 가 아니라 인자다 — 사라질 워크트리 쪽에서 스냅샷을 뜬다.

오탐의 대가가 **쓸데없는 백업 ref 한 개**뿐이라 패턴을 느슨하게 잡아도 안전하다. 이것이 문자열 재작성 방식과의 결정적인 차이다.

- **조회**: `git for-each-ref refs/claude-backups/`
- **복구**: `git checkout <ref> -- .`

## 차단·확인 목록

| 명령 | 조치 |
| --- | --- |
| `rm -rf /`, `/*`, `~`, `$HOME` (따옴표 유무 무관, `sudo`/`timeout` 접두어 포함) | ⛔ **차단** |
| `Remove-Item`/`del`/`rd` + 드라이브 루트(`C:\`)·`$env:USERPROFILE`·`$HOME` | ⛔ **차단** |
| `mkfs*`/`newfs_*`/`fdisk`/`gdisk`/`sgdisk`/`parted`/`wipefs`, `dd of=/dev/*`, `diskutil erase*`, `/dev/sd*` 리다이렉트 | ⛔ **차단** |
| `format <드라이브>:`, `diskpart`, `Clear-Disk`/`Initialize-Disk`/`Remove-Partition`/`Clear-Volume` | ⛔ **차단** |
| `git push` — force push·원격 브랜치 삭제·`--mirror` 포함 | ✅ **판정하지 않음** — auto mode 분류기의 `Git Destructive` 규칙이 공유 브랜치(`main`, 기본 브랜치, `release/*` 등)의 맨 force push 와 원격 브랜치·태그 삭제를 막는다. 자기 커밋만 있는 개인 브랜치의 force push 는 허용하고, `--force-with-lease` 는 파괴로 간주하지 않되 남의 작업을 지우는 것이 분명하면 막는다. 사용자가 대상을 명시하면 풀리는 soft-deny 다. 원격 히스토리를 확실히 지키려면 원격의 브랜치 보호(GitHub rulesets 의 force push·삭제 차단)를 쓴다 |
| 그 외 전부 | ✅ 통과 (판단 안 함) |

차단 규칙은 **명령 자리**에서 시작할 때만 잡는다. `grep -rn "rm -rf /" docs/` 처럼 위험한 문자열을 데이터로 다루는 명령은 막지 않는다 — 종전 오탐이 대부분 이 부류였다.

**종전에 있었으나 실측 근거로 제거한 것**: 포크밤 패턴, `curl … | bash` 패턴, `find`/`xargs` 대량 삭제 확인, 체이닝 삭제 확인, 글로브 미일치 차단, `.`/`..` 차단, 대상 100개 초과 확인, `chmod 777` 확인, `shred` 확인, `shutdown`/`reboot` 확인, 훅 내부 오류 확인, 그리고 시크릿 파일 접근 확인(`secrets_guard`) — 파괴가 아니라 유출 위험이고, 그쪽은 auto mode 분류기가 맡는다. 기존 설치본에 남아 있으면 설치 중에 훅 파일과 `settings.json` 등록을 함께 걷어낸다.

**이 목록은 완전하지 않고, 완전할 수도 없다.** 셸은 임의의 프로그램을 실행할 수 있어서 `python3 -c "shutil.rmtree('.')"` 같은 경로를 명령 문자열만 보고 판정하는 건 불가능하다. 그래서 이 훅은 차단을 목표로 하지 않는다 — 되돌릴 수 있게 만드는 것이 목표다. 배경은 [docs/2026-07-31-delete-guard-architecture.md](docs/2026-07-31-delete-guard-architecture.md) 참고.

## push 를 훅이 판정하지 않는 이유

예전 버전은 명령 문자열을 읽어 "이 push 가 로컬에 없는 원격 커밋을 지우는가"를 가리고, 되돌릴 수 있는 force push 는 원격 커밋을 `refs/claude-backups/` 에 고정한 뒤 통과시켰다. 그 판정을 두 차례 다시 만들었지만 두 번 다 적대적 리뷰에서 무너졌다(2026-10-11). 셸은 임의의 코드를 실행할 수 있어서, 같은 명령 줄의 앞선 `git checkout`·`git config`, 셸 확장, `bash -c`·heredoc, git·셸 별칭 같은 형태가 판정을 비껴가 원격 커밋을 조용히 지웠다. 실행 전에 문자열만 보는 판정기는 이 계층에서 완성될 수 없다.

그래서 push 는 auto mode 분류기에 맡긴다. 기본 규칙 `Git Destructive` 가 의미 수준에서 공유 브랜치 force push 와 원격 브랜치 삭제를 막는다(`claude auto-mode defaults` 로 규칙 원문을 확인할 수 있다). 다른 권한 모드에서는 push 를 확인하는 것이 없으므로, 중요한 저장소는 원격의 브랜치 보호로 지킨다. 훅이 남기는 것은 되돌리는 수단(휴지통, git 스냅샷)과 되돌릴 수 없는 두 부류의 차단뿐이다.

**업그레이드 시 자동 마이그레이션**: 예전 버전은 `Bash(git push *)`·`Bash(git push --force*)`·`Bash(git push -f*)`·`Bash(git reset --hard *)`(및 PowerShell 짝)을 `deny`로, `Bash(git push *)`(및 PowerShell 짝)을 `ask`로 배포했다. 병합 엔진은 원래 '추가만' 하므로 그 규칙들이 기존 설치본에 그대로 남고, deny/ask가 훅보다 우선하니 **새로 설치한 사람에게는 동작하고 업그레이드한 사람에게는 조용히 죽는** 상태가 된다. 그래서 설치 중에 설치 기록이 *이 설치가 넣었다*고 적은 규칙만 제거하고, 무엇을 지웠는지 설치 리포트에 출력한다. 같은 문자열이라도 설치 기록에 없으면 사용자가 직접 넣은 규칙일 수 있으므로 지우지 않는다(설치 기록이 없던 2026-07-04 이전 설치본이 여기에 해당한다). 그중 스냅샷 백업을 막는 `reset --hard` deny 규칙만 경고를 출력한다 — push 규칙은 이제 훅과 충돌하지 않는다. 예전 버전이 `autoMode.allow` 에 넣은 'danger_guard 가 확인한 push 는 허용' 예외도 같은 방식으로 걷어낸다 — 훅이 push 를 판정하지 않게 된 지금 그 예외는 거짓 전제로 `Git Destructive` 규칙을 약하게 만든다. 예전 설치기가 설치 기록 근거 없이 지운 규칙은 설치 기록의 `removed_legacy` 에 남아 있어 `--uninstall` 시 복원된다.

**훅이 죽어도 잃는 것은 복구 수단이지 실행 권한이 아니다.** 훅이 예외로 죽으면 Claude Code는 판정 없이 명령을 실행한다(fail-open). 종전에는 이때 확인으로 승격했지만 실측상 그 확인은 전부 마찰이기만 했으므로, 이제 내부 오류는 `guard.log`에 `guard-error`로 남기고 조용히 통과시킨다. 설치 후 셀프테스트가 통과했는지 확인하는 것이 그만큼 더 중요하다.

</details>

<details>
<summary><b>settings.json에 병합되는 것</b></summary>

## settings.json 병합

`permissions.defaultMode: "auto"`, 핵심 툴 `allow`(`Bash`, `PowerShell`, `Read(*)`, `Edit(*)`, `Write(*)`, `Agent(*)`, `WebFetch(*)` 등), `ask`(위험 스킬만 — git 관련 규칙은 없음), `hooks.SessionStart`·`hooks.PreToolUse` 등록, 그리고 없을 때만 채우는 `model: "opus"` / `effortLevel: "xhigh"` / `language: "korean"` 과 `env.CLAUDE_CODE_SCROLL_SPEED: "3"`(마우스 휠 스크롤 속도, `/scroll-speed` 대응)이 병합된다. Windows에서는 `env.GRAFT_TEST_SYNC_RUN`도 없을 때만 채운다 — [graft](https://github.com/NanoNets/context-graph-engine)의 Stop 훅이 턴 종료 때 돌리는 `graft build`가 빈 콘솔 창을 띄우지 않도록, 원래 동기화 스크립트를 숨김 콘솔로 다시 실행하는 `~/.claude/hooks/graft_sync_hidden.cjs`를 가리킨다(graft가 없으면 쓰이지 않는다). git 관련 deny/ask를 `permissions`에 두지 않는 이유는 [push 를 훅이 판정하지 않는 이유](#push-를-훅이-판정하지-않는-이유)에 있다.

`permissions.defaultMode`는 설치할 때마다 고른 모드(기본 `auto`)로 바꾸고, 설치 전 값은 설치 기록에 남겨 제거할 때 되돌린다. `allow`·`ask`는 합집합으로 병합하므로, 사용자가 지운 규칙도 다시 설치하면 다시 들어간다. 그 밖에 사용자가 이미 고른 값(`model` 등)과 최상위 키(`statusLine` 등), 기존 `env` 키·값은 건드리지 않는다(이 설치기가 관리하는 `env` 키만 없을 때 추가한다). 단일 소스는 [`res/settings.json`](res/settings.json)이며, 설치 시 python 실행 명령과 훅 경로만 치환된다.

</details>

<details>
<summary><b>AGENTS.md와 하네스 선택</b></summary>

