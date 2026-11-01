<#
.SYNOPSIS
    Claude Code 사용자 레벨 설정 CLI 엔트리포인트 (Windows)
.DESCRIPTION
    ai-setup 에서 옮겼다(jarvis 모듈 setup). 인자 없이 실행하면 jarvis 의 setup 모듈을 안내하고 끝낸다
    (대화형 온보딩은 core 홈의 AI 셋업 화면이 대신한다).
    -NoOnboard 는 예전 기본 설치를 유지한다. --stages 는 res/stages.py 액션 목록을 따른다.
.USAGE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -AgentsMd             # AGENTS.md만
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Omc                  # OMC + 캐시 TTL HUD만
    powershell -ExecutionPolicy Bypass -File setup.ps1 -OutputStyle          # fluent-korean 출력 스타일만
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Mods                 # Claude Code mod 만
    powershell -ExecutionPolicy Bypass -File setup.ps1 -DryRun               # 미리보기 (전체)
    powershell -ExecutionPolicy Bypass -File setup.ps1 -NoOnboard            # 온보딩 끄기
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Hooks,
    [switch]$AgentsMd,
    [switch]$Keybinds,
    [switch]$Skills,
    [string]$Tools = "",
    [switch]$Omc,
    [switch]$OutputStyle,
    [switch]$Mods,
    [string[]]$Harness = @(),
    [string]$Newline = "",
    [switch]$Uninstall,
    [switch]$DryRun,
    [switch]$NoOnboard,
    [switch]$Doctor,
    [Alias("h")]
    [switch]$Help,
    # setup.sh 와 같은 `--dry-run` 식 긴 표기를 받아내는 자리.
    #
    # PowerShell 은 이름에 하이픈이 들어간 플래그를 파라미터로 인식하지 못한다. 게다가
    # 셸에서 `setup.ps1 --help` 처럼 직접 실행하면 (`-File` 로 넘길 때와 달리) `--help`
    # 조차 파라미터가 아닌 '위치 인자' 로 들어온다. 위치 바인딩이 켜져 있으면 그게 전부
    # 첫 번째 비-스위치 파라미터인 -Newline 으로 빨려 들어가서, 도움말을 부른 사용자에게
    # "값은 shift-enter 또는 ctrl-enter 여야 합니다 (받은 값: --help)" 라는 엉뚱한 오류가
    # 나갔다. 그래서 위치 바인딩을 끄고, 남은 토큰은 여기 모아 아래에서 직접 읽는다.
    [Parameter(ValueFromRemainingArguments = $true)]
    [object[]]$Rest = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# PS 7.4+ 는 네이티브 명령의 0이 아닌 종료 코드를 종료 오류로 승격시킬 수 있다 ($true 가 기본인
# 빌드/프로필이 있음). 이 스크립트들은 실패를 $LASTEXITCODE 로 직접 판정하므로 명시적으로 끈다.
$PSNativeCommandUseErrorActionPreference = $false
$toolsGiven = $PSBoundParameters.ContainsKey("Tools")

function Show-Usage {
    Write-Host @"
ai-setup — Claude Code user-level setup

Usage: .\setup.ps1 [options]
  (no options)  installs nothing; choose and approve on the jarvis AI setup screen (module setup)
  -AgentsMd     install project instructions into the current folder only
  -Harness L    harnesses to install instructions for (comma-separated: claude,codex,grok,opencode,agy,gemini,copilot,all)
                choosing claude also installs CLAUDE.md. Default is all.
  -Skills       install bundled skills only (~/.claude/skills)
  -Keybinds     install newline keybindings only (VS Code/Cursor/Claude/Codex)
  -Newline VAL  newline key (shift-enter | ctrl-enter). Default shift-enter
  -Omc          install the oh-my-claudecode plugin + show cache TTL in the HUD
                (not included in the default install without a scope)
  -OutputStyle  install the fluent-korean plugin + set outputStyle to fluent-korean
                (not included in the default install without a scope)
  -Mods         install Claude Code mods (~/.claude/mods, v2.1.290 or later)
                · bash-diff-filter: hides OMC HUD cache files from the Bash changed-files display
                · cache-ttl-status: shows the remaining prompt-cache time in a status line below the prompt
                · korean-reply-rewrite: after a Korean prompt, stores English replies in Korean so later replies stay Korean
                (not included in the default install without a scope)
  -Uninstall    remove managed keybindings/skills + cache TTL HUD + outputStyle + mod settings
                (your own entries and the OMC/fluent-korean plugins are kept). Guard hooks are left to jarvis.
                For a staged install (no options or --stages) name the stages, e.g.
                --stages newline,claude_special --uninstall (settings only).
                --stages all --uninstall also removes the programs this setup installed.
  -DryRun       print the change plan only, without changing anything
  --stages L    all or a comma list, in stage order. -DryRun prints the list only
  --jarvis-role gateway|core
                with --stages, clone and register Jarvis for that role
  --herdr       with --stages, include herdr (off unless chosen)
  --ss-caps     with --stages, grant the Jarvis ss capability
  -NoOnboard    run the old default install without interactive onboarding
  -Help         this help

The same long options as setup.sh are accepted too:
  --agentsmd  --harness L  --skills  --keybinds  --omc  --output-style  --mods  --newline VAL  --uninstall  --dry-run
  --stages L  --jarvis-role gateway|core  --herdr  --ss-caps
  --no-onboard  --help
"@
}

# param 블록의 $Rest 로 흘러든 토큰을 setup.sh 와 같은 규칙으로 해석한다. 조용히 버리면
# 오타(--keybind)가 '전체 설치' 로 둔갑하므로, 모르는 것은 사용법과 함께 거절한다.
$newlineGiven = $PSBoundParameters.ContainsKey("Newline")
$harnessGiven = $PSBoundParameters.ContainsKey("Harness")
$unknownArgs = @()
$stageRun = $false
$stagesValue = ""
$jarvisRole = ""
$herdrFlag = $false
$orcaDesktop = $false
$ssCaps = $false
for ($i = 0; $i -lt $Rest.Count; $i++) {
    switch -Regex ($Rest[$i]) {
        '^(--help|-help|/[?h])$' { $Help = $true }
        '^--hooks$'              { $Hooks = $true }
        '^--agents-?md$'         { $AgentsMd = $true }
        '^--skills$'             { $Skills = $true }
        '^--keybinds$'           { $Keybinds = $true }
        '^--omc$'                { $Omc = $true }
        '^--output-style$'       { $OutputStyle = $true }
        '^--mods$'               { $Mods = $true }
        '^--uninstall$'          { $Uninstall = $true }
        '^--dry-run$'            { $DryRun = $true }
        '^--no-onboard$'         { $NoOnboard = $true }
        '^--doctor$'             { $Doctor = $true }
        '^--tools(=.*)?$'        { $toolsGiven = $true }
        '^--newline=(.*)$'       { $Newline = $Matches[1]; $newlineGiven = $true }
        '^--harness=(.*)$'       { $Harness = @($Matches[1]); $harnessGiven = $true }
        '^--harness$' {
            $harnessGiven = $true
            # PowerShell 명령줄의 `a,b` 는 배열로 온다. 쉼표 목록으로 되돌린다.
            $Harness = if ($i + 1 -lt $Rest.Count) { @(@($Rest[++$i]) -join ",") } else { @("") }
        }
        '^--newline$' {
            # 값이 없으면 빈 문자열로 둬서 아래 검증에 걸리게 한다 (--newline "" 도 오류다).
            $newlineGiven = $true
            $Newline = if ($i + 1 -lt $Rest.Count) { $Rest[++$i] } else { "" }
        }
        '^--stages=(.*)$' { $stagesValue = $Matches[1]; $stageRun = $true }
        '^--stages$' {
            $stageRun = $true
            $stagesValue = if ($i + 1 -lt $Rest.Count) { @($Rest[++$i]) -join "," } else { "" }
            if (-not $stagesValue) { Write-Host "--stages requires all or a comma-separated list."; exit 1 }
        }
        '^--jarvis-role=(.*)$' { $jarvisRole = $Matches[1] }
        '^--jarvis-role$' {
            $jarvisRole = if ($i + 1 -lt $Rest.Count) { $Rest[++$i] } else { "" }
            if (-not $jarvisRole) { Write-Host "--jarvis-role requires gateway or core."; exit 1 }
        }
        '^--herdr$' { $herdrFlag = $true }
        '^--orca-desktop$' { $orcaDesktop = $true }
        '^--ss-caps$' { $ssCaps = $true }
        default { $unknownArgs += $Rest[$i] }
    }
}

# Orca 관리는 jarvis 로 옮겨 갔다. 예전 진입점은 아무것도 바꾸지 않고 이 안내만 낸다.
if ($toolsGiven -or $orcaDesktop) {
    Write-Host "Orca is now managed by jarvis; ai-setup no longer installs, updates or removes it. From a jarvis checkout run: py -3 -m jarvis module orca plan --json (uninstall: module orca uninstall --json --dry-run)." -ForegroundColor Red
    exit 1
}
# doctor 도 jarvis 로 옮겨 갔다(res/stages.py 의 doctor_moved 와 같은 문구).
if ($Doctor) {
    Write-Host "Doctor is now run by jarvis; ai-setup no longer runs it. From a jarvis checkout run: py -3 -m jarvis module doctor status --json." -ForegroundColor Red
    exit 1
}
# 훅도 jarvis 로 옮겨 갔다(res/stages.py 의 hooks_moved 와 같은 문구). --stages hooks 는 stages.py 가 같은 안내로 막는다.
$HooksMoved = "Hooks are now managed by jarvis; ai-setup no longer installs or removes them. From a jarvis checkout run: py -3 -m jarvis module hooks plan --json (uninstall: module hooks uninstall --json --dry-run)."
if ($Hooks) {
    Write-Host $HooksMoved -ForegroundColor Red
    exit 1
}

if ($Help) {
    Show-Usage
    exit 0
}

if ($unknownArgs.Count -gt 0) {
    Write-Host "Unknown option: $($unknownArgs -join ' ')" -ForegroundColor Red
    Write-Host ""
    Show-Usage
    exit 1
}

$RepoRoot = Split-Path -Parent $PSScriptRoot

# 온보딩이 채우는 값들. 비어 있으면 워커가 각자의 기본값을 쓴다.
$forceAgentsMd = $false
$runAgentsMd = [bool]$AgentsMd
$runKeybinds = [bool]$Keybinds
$runSkills = [bool]$Skills
$runOmc = [bool]$Omc
$runOutputStyle = [bool]$OutputStyle
$runMods = [bool]$Mods
$dryRun = [bool]$DryRun
$newlineChord = $Newline
$harnesses = $Harness

if ($jarvisRole -and $jarvisRole -notin @("gateway", "core")) {
    Write-Host "--jarvis-role must be gateway or core (got: $jarvisRole)." -ForegroundColor Red
    exit 1
}
if (($jarvisRole -or $herdrFlag -or $ssCaps) -and -not $stageRun) {
    Write-Host "--jarvis-role, --herdr, and --ss-caps require --stages." -ForegroundColor Red
    exit 1
}

if ($newlineGiven -and $newlineChord -notin @("shift-enter", "ctrl-enter")) {
    Write-Host "-Newline (--newline) must be shift-enter or ctrl-enter (got: $newlineChord)." -ForegroundColor Red
    exit 1
}

function Resolve-PythonForOnboard {
    # Get-Command 존재만으로는 부족하다. Windows 에서 `py` 가 런처가 아니라 python.exe
    # 자체이면 `py -3` 이 "Unknown option: -3" 을 stderr 로 내고, PS 5.1 은 그걸
    # NativeCommandError 로 승격시켜 설치가 죽는다. 각 후보를 실제로 실행해 3.8+ 인지
    # 확인하고, 실패하면 다음 후보로 넘어간다. 순서는 SetupCommon.Resolve-PythonCommand
    # 와 같다 (python → python3 → py -3 → py).
    $candidates = @(
        @{ Exe = "python";  Args = @() },
        @{ Exe = "python3"; Args = @() },
        @{ Exe = "py";      Args = @("-3") },
        @{ Exe = "py";      Args = @() }   # 런처가 아닌 py.exe (= CPython) 는 -3 을 모른다
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $ok = $false
        try {
            & $c.Exe @($c.Args) -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" *> $null
            $ok = ($LASTEXITCODE -eq 0)
        } catch {
            $ok = $false
        }
        if ($ok) { return $c }
    }
    return $null
}

# 대화형 온보딩(ai-setup 의 res/onboard.py)은 jarvis 로 옮기지 않았다. 고르고 승인하는 일은 core 홈의
# AI 셋업 화면과 `py -3 -m jarvis module setup plan --json` 이 맡는다. 인자가 없으면 아무것도 설치하지 않는다.
$hadArgs = $AgentsMd -or $Keybinds -or $Skills -or $Omc -or $OutputStyle -or $Mods -or $Uninstall -or $DryRun -or $NoOnboard -or $newlineGiven -or $harnessGiven -or $stageRun -or $herdrFlag -or $ssCaps -or [bool]$jarvisRole
if (-not $hadArgs) {
    Write-Host "Interactive onboarding moved to jarvis: use the AI setup screen on the core home, or run py -3 -m jarvis module setup plan --json from the jarvis checkout. Nothing was installed." -ForegroundColor Red
    exit 1
}

function Invoke-StagePlan {
    $py = Resolve-PythonForOnboard
    if (-not $py) { Write-Host "Python 3.8+ is required." -ForegroundColor Red; exit 2 }
    $planArgs = @("--plan", "--stages", $stagesValue, "--project-dir", (Get-Location).Path)
    if ($harnesses -and @($harnesses).Count -gt 0 -and @($harnesses)[0]) {
        $planArgs += @("--harness", (@($harnesses) -join ","))
    }
    if ($jarvisRole) { $planArgs += @("--jarvis-role", $jarvisRole) }
    if ($herdrFlag) { $planArgs += "--herdr" }
    if ($ssCaps) { $planArgs += "--ss-caps" }
    if ($dryRun) { $planArgs += "--dry-run" }
    if ($Uninstall) { $planArgs += "--uninstall" }
    if ($newlineChord) { $planArgs += @("--newline", $newlineChord) }
    if ($forceAgentsMd) { $planArgs += "--overwrite-agentsmd" }
    & $py.Exe @($py.Args) -B -X utf8 (Join-Path $RepoRoot "res\stages.py") @planArgs
    $status = $LASTEXITCODE
    if ($dryRun -and $status -eq 0) {
        Write-Host "DRY RUN complete — nothing was changed."
    }
    exit $status
}

if ($stageRun) {
    Invoke-StagePlan
}

# ── 출력 서식 ────────────────────────────────────────────────────────
# setup.sh 의 ui_format 과 같은 규칙. 워커는 단독으로도 실행되므로 각자 이모지 마커로 줄을
# 찍고, 여기서 그 출력(Write-Host 는 6>&1 로 받는다)을 한 줄씩 clack 거터 모양으로 바꾼다.
# 문구는 그대로 둔다. 기본 conhost 는 이 기호들을 못 그리는 글꼴이 많아 ASCII 로 대체한다
# (clack 이 쓰는 is-unicode-supported 와 같은 판정).
$onWindows = [System.Environment]::OSVersion.Platform -eq 'Win32NT'
$uiUnicode = (-not $onWindows) -or [bool]$env:WT_SESSION -or ($env:TERM_PROGRAM -eq 'vscode') -or
    ($env:TERMINAL_EMULATOR -eq 'JetBrains-JediTerm') -or ($env:ConEmuTask -eq '{cmd::Cmder}')
if ($uiUnicode) {
    $G = @{ Bar = '│'; Start = '┌'; End = '└'; Section = '◇'; Ok = '✓'; Warn = '▲'; Err = '✗'; Dry = '○'; Dot = '•' }
} else {
    $G = @{ Bar = '|'; Start = 'T'; End = '-'; Section = 'o'; Ok = '+'; Warn = '!'; Err = 'x'; Dry = 'o'; Dot = '-' }
}
$uiMarks = @(
    @{ Prefixes = @('✅', '🎉'); Mark = 'Ok'; Color = 'Green' },
    @{ Prefixes = @('⚠️', '⚠'); Mark = 'Warn'; Color = 'Yellow' },
    @{ Prefixes = @('❌'); Mark = 'Err'; Color = 'Red' },
    @{ Prefixes = @('🧪'); Mark = 'Dry'; Color = 'Cyan' },
    @{ Prefixes = @('ℹ️', '💾', '🗑️', '📦', '🐍', '🧾', '🔁', '•'); Mark = 'Dot'; Color = 'DarkGray' }
)

$ord = [System.StringComparison]::Ordinal

function Write-UiBar([string]$Rest = '') {
    Write-Host $G.Bar -ForegroundColor DarkGray -NoNewline
    Write-Host $Rest
}

filter Format-SetupOutput {
    foreach ($line in ([string]$_ -split "`r?`n")) {
        if (-not $line.Trim()) { Write-UiBar; continue }
        $body = $line.TrimStart(' ')
        $pad = [Math]::Max(0, $line.Length - $body.Length - 2)
        # Windows PowerShell 5.1(.NET Framework)의 StartsWith 는 문화권 비교라 이모지를 무시할 수 있다.
        if ($body.StartsWith('🔧 ', $ord) -or $body.StartsWith('🧹 ', $ord)) {
            $title = $body.Substring($body.IndexOf(' ') + 1).Replace('...', '')
            if ($title.StartsWith('Starting ', $ord)) { $title = $title.Substring(9) }
            Write-UiBar
            Write-Host $G.Section -ForegroundColor Green -NoNewline
            Write-Host "  $title"
            continue
        }
        if ($body.StartsWith('── ', $ord)) {
            $title = $body.Substring(3)
            $cut = $title.IndexOf(' ─', $ord)
            if ($cut -ge 0) { $title = $title.Substring(0, $cut) }
            Write-UiBar "  $title"
            continue
        }
        if ($body -match '^\[\d/\d\] ') { Write-UiBar ("  " + $body.Replace('...', '')); continue }
        # 닫는 가로줄(──────)만 있는 줄은 거터가 대신하므로 버린다.
        if (-not $body.Replace('─', '')) { continue }

        $hit = $null
        foreach ($m in $uiMarks) {
            foreach ($p in $m.Prefixes) {
                if ($body.StartsWith("$p ", $ord)) { $hit = $m; $body = $body.Substring($p.Length).TrimStart(' '); break }
            }
            if ($hit) { break }
        }
        Write-Host $G.Bar -ForegroundColor DarkGray -NoNewline
        Write-Host ("  " + (' ' * $pad)) -NoNewline
        if ($hit) {
            Write-Host $G[$hit.Mark] -ForegroundColor $hit.Color -NoNewline
            Write-Host " $body"
        } else {
            Write-Host $body
        }
    }
}

function Exit-Setup([int]$Code) {
    # 설치 출력은 온보딩과 같은 모양의 블록 하나로 묶는다. 끝줄은 종료 코드를 보고 찍는다.
    $what = if ($Uninstall) { "Uninstall" } else { "Install" }
    Write-UiBar
    Write-Host $G.End -ForegroundColor DarkGray -NoNewline
    if ($Code -ne 0) {
        Write-Host "  $what failed (exit $Code) — check the messages above." -ForegroundColor Red
    } elseif ($dryRun) {
        Write-Host "  DRY RUN complete — nothing was changed."
    } else {
        Write-Host "  $what complete"
    }
    Write-Host ""
    exit $Code
}

# 워커가 찍는 python 출력이 파이프 너머에서 블록 버퍼링되면 stderr 경고와 순서가 뒤바뀐다.
$env:PYTHONUNBUFFERED = '1'

Write-Host ""
Write-Host $G.Start -ForegroundColor DarkGray -NoNewline
Write-Host "  ai-setup"

# 워커가 throw 로 끝나도(셀프테스트 실패 등) 끝줄을 찍고 실패 코드로 나간다.
try {

# 제거도 설치와 같은 범위 규칙을 따른다. 범위를 지정했으면 그것만 지운다.
# 훅은 jarvis 가 맡으므로 지우지 않고, 영수증의 훅 기록(hook_files·권한 기록)도 jarvis 를 위해 남긴다.
if ($Uninstall) {
    $hadScope = $AgentsMd -or $Keybinds -or $Skills -or $Omc -or $OutputStyle -or $Mods
    $unKeybinds = if ($hadScope) { [bool]$Keybinds } else { $true }
    $unSkills = if ($hadScope) { [bool]$Skills } else { $true }
    $unOmc = if ($hadScope) { [bool]$Omc } else { $true }
    $unOutputStyle = if ($hadScope) { [bool]$OutputStyle } else { $true }
    $unMods = if ($hadScope) { [bool]$Mods } else { $true }

    $kbExit = 0
    $skillsExit = 0
    if ($unSkills) {
        & (Join-Path $PSScriptRoot "setup_skills.ps1") -Uninstall -DryRun:$dryRun 6>&1 | Format-SetupOutput
        $skillsExit = $LASTEXITCODE
    }
    # mod·출력 스타일·캐시 TTL HUD 는 설치(OMC → 출력 스타일 → mod)의 역순으로 되돌린다 — settings.json 을 처음 만든
    # 워커가 마지막에 와야, 다른 워커의 키가 다 빠진 뒤 빈 파일을 지울 수 있다.
    $modsExit = 0
    if ($unMods) {
        & (Join-Path $PSScriptRoot "setup_mods.ps1") -Uninstall -DryRun:$dryRun 6>&1 | Format-SetupOutput
        $modsExit = $LASTEXITCODE
    }
    $styleExit = 0
    if ($unOutputStyle) {
        & (Join-Path $PSScriptRoot "setup_output_style.ps1") -Uninstall -DryRun:$dryRun 6>&1 | Format-SetupOutput
        $styleExit = $LASTEXITCODE
    }
    $omcExit = 0
    if ($unOmc) {
        & (Join-Path $PSScriptRoot "setup_omc.ps1") -Uninstall -DryRun:$dryRun 6>&1 | Format-SetupOutput
        $omcExit = $LASTEXITCODE
    }
    if ($unKeybinds) {
        & (Join-Path $PSScriptRoot "setup_keybinds.ps1") -Uninstall -DryRun:$dryRun 6>&1 | Format-SetupOutput
        $kbExit = $LASTEXITCODE
    }
    if ($skillsExit -ne 0) { Exit-Setup $skillsExit }
    if ($omcExit -ne 0) { Exit-Setup $omcExit }
    if ($styleExit -ne 0) { Exit-Setup $styleExit }
    if ($modsExit -ne 0) { Exit-Setup $modsExit }
    Exit-Setup $kbExit
}

# OMC·fluent-korean 은 서드파티 플러그인이라, mod 는 최신 Claude Code 에서만 동작해서 기본 전체
# 설치에 끼우지 않는다 — 명시적으로 고른 경우에만.
# 훅은 jarvis 가 맡으므로 전체 설치에 들지 않는다.
if (-not ($runAgentsMd -or $runKeybinds -or $runSkills -or $runOmc -or $runOutputStyle -or $runMods)) {
    $runAgentsMd = $true
    $runKeybinds = $true
    $runSkills = $true
}

if ($runAgentsMd) {
    # An empty selection means "not specified", so the worker applies its own
    # default rather than being handed an empty array it would have to reject.
    if ($harnesses.Count -gt 0) {
        & (Join-Path $PSScriptRoot "setup_agentsmd.ps1") -DryRun:$dryRun -Force:$forceAgentsMd -Harness $harnesses 6>&1 | Format-SetupOutput
    } else {
        & (Join-Path $PSScriptRoot "setup_agentsmd.ps1") -DryRun:$dryRun -Force:$forceAgentsMd 6>&1 | Format-SetupOutput
    }
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

if ($runKeybinds) {
    $kbParams = @{ DryRun = $dryRun }
    if ($newlineChord) { $kbParams["Newline"] = $newlineChord }
    & (Join-Path $PSScriptRoot "setup_keybinds.ps1") @kbParams 6>&1 | Format-SetupOutput
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

if ($runOmc) {
    & (Join-Path $PSScriptRoot "setup_omc.ps1") -DryRun:$dryRun 6>&1 | Format-SetupOutput
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

if ($runOutputStyle) {
    & (Join-Path $PSScriptRoot "setup_output_style.ps1") -DryRun:$dryRun 6>&1 | Format-SetupOutput
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

if ($runMods) {
    & (Join-Path $PSScriptRoot "setup_mods.ps1") -DryRun:$dryRun 6>&1 | Format-SetupOutput
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

if ($runSkills) {
    & (Join-Path $PSScriptRoot "setup_skills.ps1") -DryRun:$dryRun 6>&1 | Format-SetupOutput
    if ($LASTEXITCODE -ne 0) { Exit-Setup $LASTEXITCODE }
}

Exit-Setup 0
} catch {
    "❌ $($_.Exception.Message)" | Format-SetupOutput
    # 어디서 실패했는지는 거터 밖(stderr)에 남긴다 — throw 를 잡지 않던 때 보이던 정보다.
    [Console]::Error.WriteLine($_.InvocationInfo.PositionMessage)
    Exit-Setup 1
}
