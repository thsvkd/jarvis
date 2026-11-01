# 셋업 모듈 안내 (ai-setup README 에서 옮김)

> 2026-11-01 ai-setup 의 기능을 jarvis 의 `setup`·`harness` 모듈로 옮기면서 사용자 설명을 이 문서로 가져왔다. 본문의 명령은 ai-setup 시절 표기다. 지금은 다음으로 바꿔 읽는다.
> - `curl … install.sh | sh`, `setup.ps1`(인자 없음), `--stages …`: core 홈의 AI 셋업 화면에서 `setup` 모듈의 계획을 보고 적용한다(또는 `python3 -m jarvis module setup plan --json` → `apply --json --plan-id <id>`).
> - `--keybinds`·`--skills`·`--mods`·`--omc`·`--output-style` 같은 개별 옵션: `bash jarvis/modules/setup/scripts/setup.sh <옵션>`(Windows 는 `setup.ps1`). 이미 설치한 설정은 배포본이 바뀌면 `setup` 모듈이 다시 적용한다.
> - `update-ai`: `harness` 모듈(`python3 -m jarvis module harness plan --json`).
> - AGENTS.md: 프로젝트 폴더에서 `python3 -m jarvis agentsmd`.
> 계약은 `docs/SPEC.md` 의 setup·harness 절, 완료 기준은 `docs/TEST_SPEC.md` 가 소유한다.

## 설치 전에 알아 둘 것

> [!WARNING]
> **사용량과 비용에 영향을 주는 기본값이 있다.** claude_special 단계가 설치하는 mod `korean-reply-rewrite`는 한국어로 물었는데 영어로 답한 응답을 Haiku로 번역해 대화 기록에 저장하므로, 그만큼 호출이 늘고 기록에 남는 응답이 바뀐다.
>
> **sudo를 쓴다.** Linux에서 git이 없으면 패키지 관리자로 설치한다(sudo). Tailscale은 공식 설치 스크립트로 설치하므로 Linux에서는 Tailscale 패키지 저장소도 추가된다.

인자 없이 실행하면 **단계형 설치**가 시작된다. 아래 10개 단계를 차례로 보여 주며, 모든 단계가 기본으로 켜져 있다. 이미 설치된 프로그램은 다시 설치하지 않는다. 새 장비에서 기본값 그대로 설치하면 다음이 설치된다.

| 단계 | 기본으로 하는 일 | 주의할 점 |
| --- | --- | --- |
| base | git이 없으면 설치하고, 뒤 단계에 필요하면 Node 20을 nvm으로 설치한다 | Linux에서는 sudo를 쓴다 |
| harness | Claude Code와 Codex를 각 공식 설치 스크립트로 설치한다 | Grok·OpenCode·Gemini·Antigravity·Copilot은 직접 골라야 설치한다 |
| plugins | Claude Code 플러그인 8개를 사용자 범위로 설치한다: oh-my-claudecode, fluent-korean, ponytail, codex, commit-commands, context7, typescript-lsp, pyright-lsp | 서드파티 마켓플레이스가 포함된다 |
| skills | 번들 스킬 design-lab, tailnet-report를 설치한다 | |
| codegraph | 코드 그래프 도구 CodeGraph를 `npm install -g`로 설치한다 | |
| mcp | Claude Code와 Codex에 CodeGraph MCP 서버를 등록한다 | |
| newline | 줄바꿈 키를 설정한다(기본 Shift+Enter) | |
| claude_special | 출력 스타일을 fluent-korean으로 지정하고, Claude Code mod 3개를 설치하며, OMC HUD를 쓰고 있으면 캐시 표시 래퍼를 붙인다 | |
| tools | 기기들을 사설 네트워크로 묶는 Tailscale을 설치한다. Orca는 jarvis가 관리한다(Orca(`docs/SPEC.md` Orca 모듈 절)) | Linux에서는 **sudo**가 필요하다 |
| agentsmd | 현재 폴더에 `AGENTS.md`(와 `CLAUDE.md`)를 만든다. 내용은 위 AGENTS.md 설명을 참고한다 | 이미 있으면 온보딩에서 덮어쓸지 묻고, 기본은 그대로 둔다 |

- Tailscale은 설치만 하며, 로그인(`tailscale up`)은 하지 않는다.
- Jarvis(작성자의 [홈 서버 모니터링 대시보드](https://github.com/thsvkd/jarvis))와 herdr은 직접 골랐을 때만 설치한다.
- 단계 하나가 실패하면 **거기서 멈추고** 뒤 단계는 실행하지 않는다. tools 단계는 agentsmd 단계보다 앞에 있으므로, 예를 들어 Tailscale 설치가 실패하면 `AGENTS.md`도 만들지 않는다. 원인을 고치거나 실패한 단계를 끈 뒤, 남은 단계만 `--stages agentsmd`처럼 지정해 이어서 실행한다.
- 가드 훅과 권한 규칙·권한 모드는 이 설치기가 아니라 jarvis가 설치한다(훅(`docs/SPEC.md` 훅 모듈 절)).
- 각 용어의 뜻은 [용어](#용어)에 정리해 두었다.

---

## 무엇이 설치되나

| 위치 | 내용 |
| --- | --- |
| `~/.claude/.claude-code-setup.json` | 설치 기록. 제거할 때 이 기록에 남은 것만 되돌린다 |
| `./AGENTS.md`, `./CLAUDE.md` | 프로젝트 작업 규칙. `CLAUDE.md`는 `@AGENTS.md` 한 줄짜리 파일이며, 하네스에 `claude`가 있을 때만 만든다. 이미 있으면 건너뛴다 |
| `~/.claude/skills/design-lab/`, `~/.claude/skills/tailnet-report/` | 번들 스킬 |
| 줄바꿈 키 | `~/.claude/keybindings.json`, `~/.codex/config.toml`, VS Code·Cursor `keybindings.json`(에디터가 없어도 만든다), `~/.tmux.conf`(POSIX만), Windows Terminal(`ctrl-enter`일 때만) |
| OMC HUD 래퍼 | `~/.claude/hud/omc-hud-ttl.mjs`, `settings.json`의 `statusLine` 래퍼와 `refreshInterval` |
| mod | `~/.claude/mods/bash-diff-filter/`, `~/.claude/mods/cache-ttl-status/`, `~/.claude/mods/korean-reply-rewrite/`, `settings.json`의 `env.CLAUDE_CODE_PLUGIN_DIRS` |
| 출력 스타일 | `settings.json`의 `outputStyle` = `fluent-korean:fluent-korean`. 원래 값은 설치 기록에 남긴다 |

가드 훅(`~/.claude/hooks/`)과 `settings.json`의 훅 등록·권한 규칙은 jarvis가 설치한다(훅(`docs/SPEC.md` 훅 모듈 절)).

**보안 주의**: `CLAUDE_SETUP_REPO`·`CLAUDE_SETUP_REF` 환경 변수는 내려받아 실행할 저장소와 버전을 바꾼다. 신뢰할 수 없는 입력이 이 변수를 바꿀 수 있는 환경(공유 CI 등)에서는 설정하지 않는다.

---

## 번들 스킬

- [design-lab](../jarvis/modules/setup/res/skills/design-lab/SKILL.md): 디자인 시안마다 동작하는 앱을 만들어 비교하고, 선택과 의견을 남기는 갤러리를 만든다. Claude Code에서 `/design-lab 예약 앱 시안 3개를 비교할 갤러리를 만들어줘`처럼 부르거나, 시안 비교를 요청하면 자동으로 불러온다.
- [tailnet-report](../jarvis/modules/setup/res/skills/tailnet-report/SKILL.md): 서버를 실제로 조사한 근거로 보고서 페이지를 만들고, `tailscale serve`로 자신의 Tailscale 네트워크(tailnet)에만 게시한다. 페이지 디자인은 외부 스킬 [visual-explainer](https://github.com/nicobailon/visual-explainer)(MIT)에 맡기므로, 먼저 `npx skills add nicobailon/visual-explainer#v0.12.0 -g`로 설치해 둔다. 이 설치기는 외부 스킬을 설치하지 않는다.

스킬 설치 기록은 `~/.claude/.claude-code-setup-skills.json`에 파일 해시로 남긴다. 같은 이름의 기존 사용자 스킬이나 설치 후 사용자가 고친 스킬은 덮어쓰거나 지우지 않고, 종료 코드 1로 알린다. `--skills --uninstall`(PowerShell `-Skills -Uninstall`)은 이 설치기가 관리하는 스킬만 제거한다. 개인 스킬 경로와 호출 방식은 [Claude Code 공식 문서](https://code.claude.com/docs/en/skills)를 따른다.

---

## 용어

| 용어 | 뜻 |
| --- | --- |
| 하네스 | 모델을 실행하는 에이전트 프로그램. Claude Code, Codex CLI, Grok CLI 등 |
| 단계형 설치, Walk, 허브 | 인자 없이 실행했을 때의 설치 방식. Walk는 단계를 하나씩 묻는 진행 방식이고, 허브는 단계별 선택을 모아 보여 주는 메뉴다 |
| 설치 기록 | 이 설치기가 무엇을 넣었는지 적어 두는 `~/.claude/.claude-code-setup.json`. 제거는 이 기록에 있는 것만 되돌린다 |
| 가드 훅 | Claude Code가 명령을 실행하기 전에 부르는 `danger_guard.py`. 위험한 삭제를 막고 git 스냅샷을 남긴다. jarvis가 관리한다 |
| OMC, HUD | [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) 플러그인과, 그 플러그인이 Claude Code 화면 아래에 띄우는 상태 표시줄(`statusLine`) |
| mod | Claude Code의 화면과 동작을 바꾸는 확장. [공식 문서](https://code.claude.com/docs/en/plugins/mods/overview) |
| CodeGraph, MCP | 코드베이스를 미리 색인해 두는 로컬 지식 그래프 도구와, 그 색인을 에이전트가 쓰게 해 주는 MCP(Model Context Protocol) 서버 |
| Tailscale, tailnet | 기기들을 사설 네트워크로 묶는 VPN과, 그렇게 묶인 내 네트워크 |
| Jarvis, herdr | 작성자의 홈 서버 모니터링 대시보드와, 코딩 에이전트를 실행하는 런타임 도구([herdr.dev](https://herdr.dev)). 직접 골랐을 때만 설치한다 |

---

## update-ai

저장소를 clone해 두면, 설치된 하네스(claude, codex, grok, opencode, gemini, agy, copilot)와 도구(herdr, CodeGraph), 그 플러그인을 한 번에 최신으로 올릴 수 있다. 설치되지 않은 것은 건너뛰고 새로 설치하지 않으며, npm·winget·brew 중 실제로 설치한 방식으로 올린다. npm 외의 공식 설치 방식이 없는 gemini·CodeGraph(와 Windows의 opencode·copilot)는 버전마다 디렉터리를 따로 두는 npm-release 배치(`res/tools/npm_release.py`)로 깔고 올린다. CodeGraph의 `codegraph upgrade`는 호출하지 않는다.

```sh
git clone https://github.com/thsvkd/ai-setup.git && cd ai-setup
bash scripts/update-ai.sh --check            # 현재 버전과 최신 버전만 본다
bash scripts/update-ai.sh                    # 전부 올린다
bash scripts/update-ai.sh --only claude,codex
```
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Update-Ai.ps1 -Check
powershell -ExecutionPolicy Bypass -File .\scripts\Update-Ai.ps1 -Only claude,codex
```

실행 중인 세션이 있어도 올린다. 새 버전은 다른 디렉터리에 깔리고 진입점만 바뀌므로, 이미 떠 있는 세션은 재시작할 때까지 이전 버전으로 계속 돈다. 파일을 제자리에서 바꾸는 예전 설치(npm -g, winget)와 codex 데몬 교체만 세션이 돌면 보류로 남기며, 세션을 끊어도 되면 `--allow-interrupt`(PowerShell `-AllowInterrupt`)를 준다. 하나가 실패해도 나머지를 계속하고, 마지막에 종료 코드 1로 끝난다.

</details>

<details>
<summary><b>AGENTS.md와 하네스 선택</b></summary>

### AGENTS.md와 `--harness`

`--harness`는 **무엇을 쓸지**가 아니라 **무엇을 읽는지**로 갈린다. Codex·Grok·OpenCode는 루트 `AGENTS.md`를 직접 읽으므로 그 파일 하나면 되고, Claude Code는 `AGENTS.md`를 읽지 않고 `CLAUDE.md`를 읽으므로 `@AGENTS.md` 한 줄짜리 `CLAUDE.md`가 추가로 필요하다(Windows에서 심볼릭 링크는 관리자 권한을 요구해서 import를 쓴다). 그래서 `AGENTS.md`는 항상 설치되고, `CLAUDE.md`는 선택에 `claude`가 있을 때만 생긴다. 여러 하네스를 함께 쓰는 프로젝트가 흔하므로 다중 선택이 기본이며 — `--harness claude,codex` — 온보딩에서도 체크박스로 고른다. 모르는 이름을 주면 아무것도 쓰지 않고 종료 코드 1로 끝난다.

기존 `AGENTS.md`가 있으면 건너뛴다. 워커 `setup_agentsmd`의 `--force`는 기존 파일을 `.bak.<타임스탬프>`로 백업한 뒤 덮어쓴다 — 온보딩이 내부적으로 쓰는 옵션이다.

</details>

<details>
<summary><b>줄바꿈 키</b> — 어떻게 맞추고 무엇이 안 되는가</summary>

## 줄바꿈 키

CLI 에이전트의 프롬프트에서 **줄을 넣고 전송하지 않는** 키를 세 OS에서 같은 의미로 만든다. 값은 `shift-enter`(기본) 또는 `ctrl-enter` 하나다. **Enter 단독은 계속 전송**이고, 고르지 않은 쪽 수정 Enter는 각 에이전트 기본값 그대로 둔다.

대상은 Claude Code CLI · Codex CLI · Grok Build CLI다. Gemini CLI와 Cursor/VS Code의 IDE 챗 입력창은 범위 밖이다(그쪽은 이미 Shift+Enter가 줄바꿈이다).

`--newline`은 다른 플래그와 조합할 수 있다 — `--keybinds --newline ctrl-enter`는 키바인딩만 Ctrl+Enter로, `--no-onboard --newline ctrl-enter`는 질문 없는 기본 설치를 Ctrl+Enter로 한다. 재실행 시 `--newline`을 주지 않으면 설치 기록에 남은 이전 선택을 그대로 쓰고, 기록이 없으면 `shift-enter`다. 값이 둘 중 하나가 아니면 아무것도 쓰지 않고 종료 코드 1로 끝난다.

### 두 층으로 맞춘다

1. **터미널 층** — Shift+Enter를 Enter와 구분하지 못하는 터미널(VS Code·Cursor 통합 터미널)에 `고른 키 → 줄바꿈 시퀀스` 바인딩을 넣는다. 시퀀스는 `ESC` + `CR` (바이트 `0x1B 0x0D`) 하나로 고정돼 있고, `when`은 `terminalFocus`만 걸어 에디터·노트북의 Shift+Enter는 건드리지 않는다. POSIX에서는 `~/.tmux.conf`에 `extended-keys`가 없을 때만 추가한다 — 이게 꺼져 있으면 tmux가 수정 Enter를 통째로 삼킨다.
2. **에이전트 층** — Claude `~/.claude/keybindings.json`의 `bindings` 배열 안 `Chat` 컨텍스트에 `chat:newline`을(예전 설치기가 남긴 최상위 `Chat` 키는 이 배열로 옮긴다), Codex `~/.codex/config.toml`의 `insert_newline`에 고른 키를 **합집합**으로 넣는다. Grok은 키맵을 리맵할 수 없으므로 파일을 만들지 않는다 — 터미널이 보내는 시퀀스에만 의존한다.

Windows Terminal·iTerm2·Ghostty·Kitty·WezTerm 같은 터미널은 Shift+Enter를 이미 Enter와 다르게 보낸다. 여기에 Shift+Enter 시퀀스를 **덧붙이면 줄바꿈이 두 번** 생기므로, `shift-enter`를 고른 경우 Windows Terminal에는 줄바꿈 바인딩을 **넣지 않는다**(이전 설치가 넣어둔 관리 항목이 있으면 그것만 정리한다). `ctrl-enter`를 고른 경우에만 `Ctrl+Enter` 번역을 넣는다.

VS Code / Cursor `keybindings.json` 경로는 OS별로 다르다 — Windows `%APPDATA%\Code\User\`, macOS `~/Library/Application Support/Code/User/`, Linux `~/.config/Code/User/`.

### 파일을 어떻게 고치나

바꾸기 전 원본을 `<파일>.bak.<타임스탬프>`로 남기고, 임시 파일에 쓴 뒤 교체한다 — 쓰기 도중 끊겨도 원본이 반쯤 잘리지 않는다. 대상이 심볼릭 링크면(dotfile 관리자를 쓰는 경우) 링크를 갈아치우지 않고 가리키는 실체를 고치며, 기존 파일 권한을 그대로 옮긴다. VS Code·Cursor `keybindings.json`은 우리 항목만 텍스트로 넣고 빼므로 주석과 서식이 유지된다. 배열이 없는(주석만 있는) 파일은 다시 직렬화하므로 그때는 주석이 사라진다. VS Code가 허용하는 트레일링 콤마는 우리도 읽는다. 그래도 읽지 못한 대상이 있으면 그 사실을 목록으로 알리고 **0이 아닌 종료 코드**로 끝낸다 — 적용되지 않았는데 '완료'라고 말하지 않기 위해서다.

### 안 되는 것

- **gnome-terminal 등 Shift+Enter를 구분하지 못하는 터미널** — 로컬 세션에서는 맞출 수 없다. 설치는 실패하지 않고 경고만 남긴다. Linux는 로컬 gnome-terminal 대신 Windows Terminal/Cursor에서 `ssh`로 붙는 경로를 쓰면 그대로 동작한다.
- **Grok의 `/multiline`(`/ml`, `Ctrl+M`) 모드** — 이 토글이 켜지면 Enter와 Shift+Enter의 의미가 **반대**가 된다(Enter=줄바꿈, Shift+Enter=전송). Grok은 키를 리맵할 수 없어 이 상태를 Claude/Codex와 한 키로 맞추는 것이 불가능하다. 설치기는 그 토글을 켜거나 끄지 않고 리포트에 한 줄 경고만 한다. 기준은 **Grok 기본 프롬프트(multiline 꺼짐)** 다.
- **에이전트가 아예 없을 때** — 바이너리와 설정 디렉터리가 **둘 다** 없으면 그 에이전트는 건너뛰고 설치는 성공한다. 하나라도 있으면 키맵을 쓴다.

</details>

<details>
<summary><b>OMC와 캐시 TTL HUD</b> — <code>--omc</code>가 하는 일</summary>

## OMC와 캐시 TTL HUD

`--omc`를 주면 [oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode) 플러그인을 `claude plugin marketplace add` / `claude plugin install`로 설치하고, OMC HUD의 세션 경과 시간(`session:45m`) 자리에 **프롬프트 캐시 TTL 잔여 시간**(`cache:57m`)을 띄운다. 단계형 설치에서는 plugins 단계가 OMC 플러그인을 설치하고, claude_special 단계가 이미 OMC HUD를 쓰는 `statusLine`에만 이 래퍼를 붙인다. 서드파티 플러그인이므로 `--no-onboard`에는 포함하지 않는다.

- 값은 Claude Code가 statusLine stdin으로 넘기는 `prompt_cache.expires_at`에서 매 렌더마다 계산한다. 2분 이하면 노랑, 만료되면 `cache:cold`(빨강)이다. 캐시 정보가 없으면(구버전 Claude Code) `session:` 표시를 그대로 둔다. OMC HUD 프리셋에서 session 요소를 끈 경우에는 바꿀 자리가 없어 표시되지 않는다.
- `~/.claude/hud/omc-hud-ttl.mjs`를 복사하고 기존 `statusLine.command` 앞에 래퍼 실행 명령을 끼운다. node는 OMC와 같은 방식으로 찾는다 — POSIX는 OMC의 `find-node.sh`, Windows는 `~/.claude/.omc-config.json`의 `nodeBinary`, 둘 다 없으면 PATH의 `node`. 입력이 없어도 카운트다운이 흐르도록 `statusLine.refreshInterval`이 없으면 `10`(초)을 넣는다.
- `statusLine`이 없으면 플러그인의 정본 템플릿으로 OMC HUD를 먼저 설치한다. OMC가 아닌 다른 `statusLine`이 있으면 사용자 설정이므로 파일 하나 만들지 않는다. `settings.json`이나 설치 기록을 파싱할 수 없으면 쓰지 않고 멈춘다.
- `--uninstall`은 이 설치가 만든 것만 되돌린다 — 래퍼, 우리가 넣은 값 그대로인 `refreshInterval`, 그리고 설치가 새로 만든 `statusLine`·`settings.json`. 설치가 복사한 HUD 파일은 그 `statusLine`도 함께 치울 때, 복사 당시 내용 그대로인 경우에만 지운다 — `omc-setup`이 다시 쓴 파일은 OMC 것이다. 원래 있던 OMC 플러그인과 OMC HUD는 남긴다.
- OMC HUD는 렌더할 때마다(위 `refreshInterval`이면 10초마다) 프로젝트의 `.omc/state/hud-stdin-cache.json`을 다시 쓴다. 그래서 Claude Code가 Bash 실행 뒤 보여 주는 '바뀐 파일' diff에 이 파일이 거의 매번 끼어든다. 이 항목을 빼려면 `--mods`의 `bash-diff-filter`를 함께 설치한다.
- CLAUDE.md 등 OMC의 나머지 설정은 Claude Code 안에서 `/oh-my-claudecode:omc-setup`으로 진행한다. 그 과정에서 `statusLine`이 다시 써지면 `--omc`를 한 번 더 실행하면 된다.

</details>

<details>
<summary><b>Claude Code mod</b> — <code>--mods</code>가 하는 일</summary>

## Claude Code mod

`--mods`를 주거나 단계형 설치의 claude_special 단계를 켜면 `res/mods/` 의 번들 [mod](https://code.claude.com/docs/en/plugins/mods/overview)를 `~/.claude/mods/<이름>/`에 복사하고, `~/.claude/settings.json`의 `env.CLAUDE_CODE_PLUGIN_DIRS`에 그 경로를 넣어 매 세션 로드되게 한다. mod는 Claude Code v2.1.290 이상에서만 동작하므로 `--no-onboard`에는 포함하지 않는다.

| mod | 하는 일 |
| --- | --- |
| `bash-diff-filter` | Bash 실행 뒤 보여 주는 '바뀐 파일' diff에서 OMC HUD가 렌더마다 다시 쓰는 `.omc/state/hud-stdin-cache.json`(세션별 경로 포함)을 뺀다. 걸러내는 대상은 Bash 결과의 `bashEditDiff` 필드이며, 이 필드는 원래 모델에 전달되지 않으므로 화면 표시만 바뀐다. `OMC_STATE_DIR`로 OMC 상태를 프로젝트 밖에 두면 이 파일은 애초에 표시되지 않으므로 거르지 않는다. |
| `cache-ttl-status` | 프롬프트 캐시 잔여 시간(`cache:57m`, 1분 미만은 초, 만료되면 `cache:cold`)을 **프롬프트 아래 상태 줄**에 띄운다. mod API는 statusLine의 `prompt_cache.expires_at`을 주지 않으므로, 메인 대화의 요청이 캐시를 읽거나 쓴 시각에 TTL을 더해 계산한다(서브에이전트 요청은 제외). TTL은 mod 설정 `ttl`(`1h` 기본, `5m`)로 시작하고, 모델을 바꾸면 엔진이 알려 주는 실제 값으로 갱신한다. 계산값이므로 실제 만료 시각과 몇 초 어긋날 수 있다. |
| `korean-reply-rewrite` | 한국어로 물었는데 메인 루프가 영어로 답하면, 그 응답 블록을 저장 직전에 Haiku로 한국어로 번역해 기록과 다음 요청에 넣는다. 영어 응답이 기록에 쌓이면 이후 응답도 영어로 굳기 때문에, 영어 선례를 남기지 않는 것이 목적이다. 프롬프트에서 한글 단어가 영어 단어보다 적거나 영어·영작·번역·커밋·PR을 언급했으면 바꾸지 않는다. 화면이 없는 `claude -p` 실행, 서브에이전트 응답, thinking은 건드리지 않는다. 짝이 맞는 ```` ``` ```` 펜스 코드와 인라인 코드는 자리표시자로 빼 두었다가 그대로 되돌리며(`~~~` 펜스·들여쓰기 코드·닫히지 않은 펜스는 번역 지시로만 보호한다), 번역이 실패하거나 잘리거나 자리표시자가 어긋나면 원문을 그대로 둔다. 터미널에는 스트리밍과 번역이 끝날 때까지(긴 답은 수십 초) 영어 원문이 보였다가 저장되면 번역본으로 바뀐다. 영어 블록마다 Haiku 호출이 한 번 늘어난다. |

- `--omc`의 HUD 래퍼(`session:` 자리에 `cache:`)와는 별개다. 둘 다 설치하면 TTL이 HUD 안과 프롬프트 아래 두 곳에 보인다.
- 테스트(`*.test.ts`)와 엔진이 로드 때 만드는 타입(`.claude-plugin/types/`)은 복사하지 않는다. 같은 이름의 폴더에 내용이 다른 파일이 있으면 덮어쓰지 않고 멈춘다.
- `--uninstall`은 우리가 넣은 경로와 복사 당시 그대로인 파일, 그리고 엔진이 만든 타입 폴더만 치운다. 사용자가 고친 mod 파일은 남긴다(env에서 빠지므로 더는 로드되지 않는다).
- 셸에서 `CLAUDE_CODE_PLUGIN_DIRS`를 따로 export하고 있다면 그 값이 `settings.json`의 값보다 우선할 수 있으니, 그 경우에는 경로를 직접 합쳐 준다.
- mod 하나를 고치려면 `res/mods/<이름>/`에서 `claude plugin validate .`와 `claude plugin test .`로 확인한다.

</details>

<details>
<summary><b>제거가 되돌리는 것</b></summary>

### 제거가 되돌리는 것

`--uninstall`은 설치 기록(`~/.claude/.claude-code-setup.json`)을 근거로 이 설치기가 넣은 것만 되돌린다. 훅·권한 항목·`defaultMode`는 jarvis가 되돌리므로 건드리지 않고, 그 설치 기록도 남긴다(훅(`docs/SPEC.md` 훅 모듈 절)). 사용자가 원래 갖고 있던 설정과 그 외 키는 그대로 보존되며 변경 전 `settings.json.bak.<타임스탬프>`를 남긴다. `settings.json`을 설치가 새로 만든 경우, 되돌린 뒤 비면 파일도 제거한다. 줄바꿈 키바인딩도 함께 되돌리는데, **무엇을 '우리 것'으로 볼지는 대상마다 다르다.**

- **시그니처로 식별**(VS Code·Cursor): 명령 + `when` + 시퀀스가 우리가 쓰는 것과 정확히 같은 항목만 제거한다. 이 조합은 이 설치가 만들어내는 형태라, 누가 먼저 넣었든 같은 항목이다 — 예컨대 Claude `/terminal-setup`이 넣어둔 `shift+enter` → `sendSequence` 항목도 제거 대상이다. 시퀀스가 다른 사용자 항목(에디터·노트북의 Shift+Enter)은 그대로 둔다.
- **설치 기록으로 식별**(tmux·Claude·Codex·Windows Terminal): `extended-keys` 줄, Claude의 `chat:newline`, Codex의 `shift-enter`는 전부 사용자가 직접 쓰는 값과 글자까지 같다. 값이 같다는 이유로 지우면 남의 설정을 지우는 것이므로, 설치 기록에 '설치가 넣었다'고 적힌 항목만 되돌린다. 기록이 없으면 건드리지 않는다. 설치가 기존 값을 덮어썼다면 그 값도 기록해 두었다가 되돌린다.

Codex의 기존 `ctrl-j` 처럼 우리가 넣지 않은 키는 남는다. 우리 키만 빠져 `insert_newline`이 비면 그 줄과 빈 `[tui.keymap.editor]` 테이블까지 지운다 — 빈 배열을 남기면 설치 전에 없던 '줄바꿈 전면 언바인드'라는 새 설정을 만드는 셈이기 때문이다. `--keybinds --uninstall`이면 키바인딩만 제거한다.

</details>

<details>
<summary><b>온보딩</b> — 파이프 설치에서 프롬프트가 동작하는 이유</summary>
