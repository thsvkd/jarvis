# setup.ps1 오케스트레이션 계층 테스트 (Pester v5). ai-setup 의 tests/Setup.Tests.ps1 을 옮겼다.
# 옮기지 않은 것: install.ps1 부트스트랩·Update-Ai.ps1·온보딩(res/onboard.py).
#   실행: Invoke-Pester tests/Setup.Tests.ps1 -Output Detailed
#
# 스크립트가 `exit` 를 호출하므로 Pester 세션을 죽이지 않도록 자식 pwsh 프로세스로 실행한다.

BeforeAll {
    $script:RepoRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "jarvis/modules/setup"
    $script:Setup = Join-Path $script:RepoRoot "scripts\setup.ps1"
    # 훅은 jarvis 로 옮겨 갔다. setup.ps1·install.ps1 의 -Hooks 는 이 안내만 내고 exit 1 로 끝난다(res/stages.py 의 hooks_moved).
    $script:HooksPointer = [regex]::Escape("Hooks are now managed by jarvis; ai-setup no longer installs or removes them. From a jarvis checkout run: py -3 -m jarvis module hooks plan --json (uninstall: module hooks uninstall --json --dry-run).")

    function Invoke-Script {
        param([string]$Path, [string[]]$ScriptArgs)
        $out = pwsh -NoProfile -File $Path @ScriptArgs 2>&1 | Out-String
        return [pscustomobject]@{ Output = $out; ExitCode = $LASTEXITCODE }
    }

    # 지침 워커는 "현재 디렉터리 = 대상 프로젝트" 규약이라 반드시 그 안에서 돌려야 한다.
    # Pester 5 는 Describe 본문을 discovery 단계에 실행하므로 헬퍼는 여기 둔다.
    function Invoke-AgentsMdIn {
        param([string]$ProjectDir, [string[]]$ScriptArgs = @())
        Push-Location $ProjectDir
        try {
            return Invoke-Script (Join-Path $script:RepoRoot "scripts\setup_agentsmd.ps1") $ScriptArgs
        } finally { Pop-Location }
    }

    # `-File` 로 넘기면 PowerShell 이 `--help` 를 -Help 로 붙여주지만, 사용자가 셸에서 직접
    # 치는 `.\setup.ps1 --help` 는 파라미터가 아닌 위치 인자로 들어온다.
    # 위치 바인딩 회귀는 후자로만 재현되므로 그 경로를 그대로 흉내 낸다.
    function Invoke-ScriptInline {
        param([string]$Path, [string]$ArgString)
        $out = pwsh -NoProfile -Command "& '$Path' $ArgString; exit `$LASTEXITCODE" 2>&1 | Out-String
        return [pscustomobject]@{ Output = $out; ExitCode = $LASTEXITCODE }
    }
}

Describe "setup.ps1 dispatcher" {
    BeforeEach {
        $script:sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:sandbox | Out-Null
        $script:oldUP = $env:USERPROFILE
        $env:USERPROFILE = $script:sandbox     # 자식 pwsh 가 상속 → 실제 프로필 격리
    }
    AfterEach {
        $env:USERPROFILE = $script:oldUP
        Remove-Item -Recurse -Force $script:sandbox -ErrorAction SilentlyContinue
    }

    It "-Help exits 0 and prints usage" {
        $r = Invoke-Script $script:Setup @("-Help")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Match "Usage"
    }

    # 훅은 jarvis 가 맡는다. -Hooks·--hooks 는 안내만 내고 프로필(설치된 훅·영수증의 훅 기록 포함)을 그대로 둔 채 exit 1 로 끝난다.
    It "-Hooks and --hooks point to jarvis and write nothing (covers: AI-69)" {
        $claude = Join-Path $env:USERPROFILE ".claude"
        New-Item -ItemType Directory -Force -Path (Join-Path $claude "hooks") | Out-Null
        Set-Content -Path (Join-Path $claude "hooks\danger_guard.py") -Encoding ascii -Value "# installed hook"
        Set-Content -Path (Join-Path $claude ".claude-code-setup.json") -Encoding ascii -Value '{"hook_files": ["danger_guard.py"], "added_allow": ["Bash"]}'
        # 자식 pwsh 자신이 프로필 아래에 시작 캐시(AppData\Local\Microsoft\PowerShell)를 쓴다.
        # setup.ps1 이 아니라 테스트가 띄운 인터프리터의 흔적이므로 그 경로만 비교에서 뺀다.
        $listHome = {
            ((Get-ChildItem -Recurse -Force $env:USERPROFILE | ForEach-Object FullName |
                Where-Object { $_ -notmatch '[\\/]AppData([\\/]Local([\\/]Microsoft([\\/]PowerShell([\\/].*)?)?)?)?$' } |
                Sort-Object) -join "`n") + (Get-Content (Join-Path $claude ".claude-code-setup.json") -Raw)
        }
        $before = & $listHome
        foreach ($a in @(@("-Hooks"), @("-Hooks", "-Uninstall"), @("-Hooks", "-DryRun"), @("-Keybinds", "-Hooks"))) {
            $r = Invoke-Script $script:Setup $a
            $r.ExitCode | Should -Be 1 -Because ($a -join " ")
            $r.Output | Should -Match $script:HooksPointer -Because ($a -join " ")
        }
        foreach ($a in @("--hooks", "--hooks --uninstall", "--help --hooks", "--stages hooks", "--stages newline,hooks")) {
            $r = Invoke-ScriptInline $script:Setup $a
            $r.ExitCode | Should -Be 1 -Because $a
            # --stages 는 stages.py 가 같은 안내를 낸다(Linux pwsh 에서는 python3 표기).
            $r.Output | Should -Match "Hooks are now managed by jarvis; ai-setup no longer installs or removes them\." -Because $a
        }
        (& $listHome) | Should -Be $before
    }

    It "-Uninstall -DryRun on clean profile exits 0" {
        # 전체 제거는 키바인드 워커도 부른다. POSIX pwsh 의 워커가 실제 홈을 읽지 않도록 HOME 도 격리한다.
        $oldHome = $env:HOME
        $env:HOME = $script:sandbox
        try {
            $r = Invoke-Script $script:Setup @("-Uninstall", "-DryRun")
        } finally { $env:HOME = $oldHome }
        $r.ExitCode | Should -Be 0 -Because $r.Output
        Test-Path (Join-Path $env:USERPROFILE ".claude\settings.json") | Should -Be $false
    }

    # 회귀: 셸에서 직접 실행하면 `--help` 가 위치 인자로 들어와 -Newline 에 바인딩됐고,
    # 도움말 대신 "값은 shift-enter 또는 ctrl-enter 여야 합니다 (받은 값: --help)" 가 나왔다.
    It "--help run directly from the shell also prints the help" {
        $r = Invoke-ScriptInline $script:Setup "--help"
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Match "Usage"
        $r.Output | Should -Match "--no-onboard"
    }

    It "also accepts setup.sh-style --agentsmd --dry-run and runs only that scope" {
        $r = Invoke-ScriptInline $script:Setup "--agentsmd --dry-run"
        $r.ExitCode | Should -Be 0 -Because $r.Output
        $r.Output | Should -Not -Match "keybindings\.json"      # --agentsmd 범위가 지켜졌다
        Test-Path (Join-Path $env:USERPROFILE ".claude\settings.json") | Should -Be $false
    }

    It "an invalid --newline value exits 1 regardless of notation" {
        $r = Invoke-ScriptInline $script:Setup "--keybinds --newline foo"
        $r.ExitCode | Should -Be 1
        $r.Output | Should -Match "shift-enter"
    }

    # doctor 는 jarvis 로 옮겨 갔다. -Doctor·--doctor 는 안내만 내고 프로필을 그대로 둔 채 exit 1 로 끝난다.
    It "-Doctor and --doctor point to jarvis and write nothing (covers: AI-68)" {
        $pointer = [regex]::Escape("Doctor is now run by jarvis; ai-setup no longer runs it. From a jarvis checkout run: py -3 -m jarvis module doctor status --json.")
        $skills = Join-Path $env:USERPROFILE ".agents\skills\stray"
        New-Item -ItemType Directory -Force -Path $skills | Out-Null
        Set-Content -Path (Join-Path $env:USERPROFILE ".agents\.skill-lock.json") -Encoding ascii -Value '{"version": 3, "skills": {}}'
        # 자식 pwsh 자신이 프로필 아래에 시작 캐시(AppData\Local\Microsoft\PowerShell)를 쓴다.
        # setup.ps1 이 아니라 테스트가 띄운 인터프리터의 흔적이므로 그 경로만 비교에서 뺀다.
        $listHome = {
            (Get-ChildItem -Recurse -Force $env:USERPROFILE | ForEach-Object FullName |
                Where-Object { $_ -notmatch '[\\/]AppData([\\/]Local([\\/]Microsoft([\\/]PowerShell([\\/].*)?)?)?)?$' } |
                Sort-Object) -join "`n"
        }
        $before = & $listHome
        $r = Invoke-Script $script:Setup @("-Doctor")
        $r.ExitCode | Should -Be 1 -Because $r.Output
        $r.Output | Should -Match $pointer
        $r.Output | Should -Not -Match "stray"
        $r = Invoke-Script $script:Setup @("-Doctor", "-Uninstall")
        $r.ExitCode | Should -Be 1 -Because $r.Output
        $r.Output | Should -Match $pointer
        foreach ($a in @("--doctor", "--doctor --hooks", "--help --doctor")) {
            $r = Invoke-ScriptInline $script:Setup $a
            $r.ExitCode | Should -Be 1 -Because $a
            $r.Output | Should -Match $pointer -Because $a
            $r.Output | Should -Not -Match "stray" -Because $a
        }
        (& $listHome) | Should -Be $before
    }

    # 조용히 무시하면 오타(--keybind)가 '전체 설치' 로 둔갑한다.
    It "an unknown option exits 1 and shows the usage too" {
        $r = Invoke-ScriptInline $script:Setup "--keybind"
        $r.ExitCode | Should -Be 1
        $r.Output | Should -Match "Unknown option"
        $r.Output | Should -Match "Usage"
        Test-Path (Join-Path $env:USERPROFILE ".claude\settings.json") | Should -Be $false
    }
}

# S2: 기존 Pester 스위트는 -Help / -DryRun 만 밟고 실제 설치 경로를 한 번도 실행하지 않았다.
# macOS 의 치명적 버그(패턴 A)가 정확히 이 빈칸에 숨어 있었던 전례가 있어 추가한다.
Describe "real install path (S2)" {
    BeforeEach {
        $script:sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-real-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:sandbox | Out-Null
        $script:oldUP = $env:USERPROFILE
        $script:oldHome = $env:HOME
        $script:oldApp = $env:APPDATA
        $script:oldLocal = $env:LOCALAPPDATA
        $script:oldVs = $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS
        $script:oldCur = $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS
        $script:oldWt = $env:CLAUDE_SETUP_WT_SETTINGS
        $env:USERPROFILE = $script:sandbox
        $env:HOME = $script:sandbox
        $env:APPDATA = Join-Path $script:sandbox "appdata"
        $env:LOCALAPPDATA = Join-Path $script:sandbox "local"
        New-Item -ItemType Directory -Force -Path $env:APPDATA | Out-Null
        New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA | Out-Null
        $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS = Join-Path $script:sandbox "vscode-keybindings.json"
        $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS = Join-Path $script:sandbox "cursor-keybindings.json"
        $env:CLAUDE_SETUP_WT_SETTINGS = Join-Path $script:sandbox "wt-settings.json"
        Set-Content -Path $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Value '[]' -Encoding utf8
        Set-Content -Path $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS -Value '[]' -Encoding utf8
        Set-Content -Path $env:CLAUDE_SETUP_WT_SETTINGS -Value '{"actions":[]}' -Encoding utf8
    }
    AfterEach {
        $env:USERPROFILE = $script:oldUP
        if ($null -eq $script:oldHome) { Remove-Item Env:HOME -ErrorAction SilentlyContinue } else { $env:HOME = $script:oldHome }
        if ($null -eq $script:oldApp) { Remove-Item Env:APPDATA -ErrorAction SilentlyContinue } else { $env:APPDATA = $script:oldApp }
        if ($null -eq $script:oldLocal) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $script:oldLocal }
        if ($null -eq $script:oldVs) { Remove-Item Env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS = $script:oldVs }
        if ($null -eq $script:oldCur) { Remove-Item Env:CLAUDE_SETUP_CURSOR_KEYBINDINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS = $script:oldCur }
        if ($null -eq $script:oldWt) { Remove-Item Env:CLAUDE_SETUP_WT_SETTINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_WT_SETTINGS = $script:oldWt }
        Remove-Item -Recurse -Force $script:sandbox -ErrorAction SilentlyContinue
    }

    # 전체 -Uninstall 은 훅을 지우지 않는다. jarvis 훅 모듈이 훅 파일과 영수증의 훅 기록을 이어 읽는다.
    # 옛 Orca 기록은 모르는 키다. 영수증을 다시 쓰는 워커(여기서는 키바인드)가 그 키들을 지우면 안 된다.
    It "-Uninstall keeps the hooks, their receipt records and old Orca records (covers: SETUP-14, AI-69)" {
        (Invoke-Script $script:Setup @("-Keybinds")).ExitCode | Should -Be 0
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        $hooksDir = Join-Path $claudeDir "hooks"
        New-Item -ItemType Directory -Force -Path (Join-Path $hooksDir "shims") | Out-Null
        Set-Content -Path (Join-Path $hooksDir "danger_guard.py") -Encoding ascii -Value "# installed hook"
        Set-Content -Path (Join-Path $hooksDir "shims\RmShim.psm1") -Encoding ascii -Value "# shim"
        $settingsPath = Join-Path $claudeDir "settings.json"
        $settingsText = '{"permissions": {"allow": ["PowerShell"], "defaultMode": "auto"}, "hooks": {"PreToolUse": [{"matcher": "PowerShell", "hooks": [{"type": "command", "command": "py -3 danger_guard.py"}]}]}}'
        [IO.File]::WriteAllText($settingsPath, $settingsText)
        $manifestPath = Join-Path $claudeDir ".claude-code-setup.json"
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $manifest.PSObject.Properties.Name | Should -Contain "keybinds"
        $kept = '{"hook_files": ["danger_guard.py", "shims/RmShim.psm1"], "added_allow": ["PowerShell"], "default_mode_changed": true, "hooks": true, "tool_installs": {"orca": {"method": "deb", "version": "1.4.242", "skills_added": ["orca-cli"]}, "orca_server": {"service_manager": "systemd-user", "state": "configured"}}, "tool_attempts": {"orca": {"state": "done"}}}' | ConvertFrom-Json
        foreach ($key in $kept.PSObject.Properties.Name) {
            $manifest | Add-Member -NotePropertyName $key -NotePropertyValue $kept.$key -Force
        }
        [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 20))
        $r = Invoke-Script $script:Setup @("-Uninstall")
        $r.ExitCode | Should -Be 0 -Because $r.Output
        (Get-Content (Join-Path $hooksDir "danger_guard.py") -Raw).Trim() | Should -Be "# installed hook"
        Test-Path (Join-Path $hooksDir "shims\RmShim.psm1") | Should -Be $true
        [IO.File]::ReadAllText($settingsPath) | Should -Be $settingsText
        $after = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $after.PSObject.Properties.Name | Should -Not -Contain "keybinds"  # 워커가 영수증을 실제로 다시 썼다
        foreach ($key in $kept.PSObject.Properties.Name) {
            ($after.$key | ConvertTo-Json -Depth 20 -Compress) | Should -Be ($kept.$key | ConvertTo-Json -Depth 20 -Compress) -Because $key
        }
    }

    # OMC 설치는 실제 claude CLI 를 부르므로, 설치 결과(settings + 영수증)를 심어 두고 제거 경로만 검증한다.
    It "-Uninstall -Hooks points to jarvis and keeps the omc_hud manifest, then -Uninstall -Omc reverts the cache TTL setting (covers: AI-69)" {
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
        $settingsPath = Join-Path $claudeDir "settings.json"
        $manifestPath = Join-Path $claudeDir ".claude-code-setup.json"
        $omcCmd = 'sh "$HOME/.claude/hud/omc-hud-cache.sh" "$HOME/.claude/hud/omc-hud.mjs"'
        $settings = [pscustomobject]@{ statusLine = [pscustomobject]@{
            type = "command"; command = 'node "$HOME/.claude/hud/omc-hud-ttl.mjs" ' + $omcCmd; refreshInterval = 10 } }
        Set-Content -Path $settingsPath -Value ($settings | ConvertTo-Json -Depth 20) -Encoding utf8
        $manifest = [pscustomobject]@{ omc_hud = [pscustomobject]@{ refresh_interval_added = $true } }
        Set-Content -Path $manifestPath -Value ($manifest | ConvertTo-Json -Depth 20) -Encoding utf8
        $before = (Get-Content $settingsPath -Raw) + (Get-Content $manifestPath -Raw)

        $r = Invoke-Script $script:Setup @("-Uninstall", "-Hooks")
        $r.ExitCode | Should -Be 1 -Because $r.Output
        $r.Output | Should -Match $script:HooksPointer
        ((Get-Content $settingsPath -Raw) + (Get-Content $manifestPath -Raw)) | Should -Be $before

        $r = Invoke-Script $script:Setup @("-Uninstall", "-Omc")
        $r.ExitCode | Should -Be 0
        $after = (Get-Content $settingsPath -Raw | ConvertFrom-Json).statusLine
        $after.command | Should -Be $omcCmd
        $after.PSObject.Properties.Name | Should -Not -Contain "refreshInterval"
        Test-Path $manifestPath | Should -Be $false
    }

    It "-Omc -Uninstall -DryRun changes nothing" {
        New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE ".claude") | Out-Null
        $settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
        $seed = '{"statusLine": {"type": "command", "command": "node \"$HOME/.claude/hud/omc-hud-ttl.mjs\" node x/omc-hud.mjs", "refreshInterval": 10}}'
        Set-Content -Path $settingsPath -Value $seed -Encoding utf8
        $r = Invoke-Script $script:Setup @("-Omc", "-Uninstall", "-DryRun")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Match "DRY RUN"
        (Get-Content $settingsPath -Raw).Trim() | Should -Be $seed
    }

    It "-Uninstall -Hooks points to jarvis and keeps the output_style manifest, then -Uninstall -OutputStyle reverts outputStyle (covers: AI-69)" {
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
        $settingsPath = Join-Path $claudeDir "settings.json"
        $manifestPath = Join-Path $claudeDir ".claude-code-setup.json"
        $settings = [pscustomobject]@{ model = "sonnet"; outputStyle = "fluent-korean:fluent-korean" }
        Set-Content -Path $settingsPath -Value ($settings | ConvertTo-Json -Depth 20) -Encoding utf8
        $manifest = [pscustomobject]@{ output_style = [pscustomobject]@{ previous = "Concise"; settings_created = $false } }
        Set-Content -Path $manifestPath -Value ($manifest | ConvertTo-Json -Depth 20) -Encoding utf8
        $before = (Get-Content $settingsPath -Raw) + (Get-Content $manifestPath -Raw)

        $r = Invoke-Script $script:Setup @("-Uninstall", "-Hooks")
        $r.ExitCode | Should -Be 1 -Because $r.Output
        $r.Output | Should -Match $script:HooksPointer
        ((Get-Content $settingsPath -Raw) + (Get-Content $manifestPath -Raw)) | Should -Be $before

        $r = Invoke-Script $script:Setup @("-Uninstall", "-OutputStyle")
        $r.ExitCode | Should -Be 0
        (Get-Content $settingsPath -Raw | ConvertFrom-Json).outputStyle | Should -Be "Concise"
        Test-Path $manifestPath | Should -Be $false
    }

    It "the default full install with -NoOnboard does not install mods" {
        $r = Invoke-Script $script:Setup @("-NoOnboard")
        $r.ExitCode | Should -Be 0
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        Test-Path (Join-Path $claudeDir "mods") | Should -Be $false
        (Get-Content (Join-Path $claudeDir "settings.json") -Raw) | Should -Not -Match "CLAUDE_CODE_PLUGIN_DIRS"
    }

    It "installs with the long --mods notation and reverts only the mods with -Uninstall -Mods" {
        $claudeDir = Join-Path $env:USERPROFILE ".claude"
        New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
        Set-Content -Path (Join-Path $claudeDir "settings.json") -Value '{"model": "opus"}' -Encoding utf8
        (Invoke-ScriptInline $script:Setup "--mods").ExitCode | Should -Be 0
        Test-Path (Join-Path $claudeDir "mods\cache-ttl-status\hooks\register.ts") | Should -Be $true
        (Invoke-Script $script:Setup @("-Uninstall", "-Mods")).ExitCode | Should -Be 0
        Test-Path (Join-Path $claudeDir "mods") | Should -Be $false
        $after = Get-Content (Join-Path $claudeDir "settings.json") -Raw | ConvertFrom-Json
        $after.model | Should -Be "opus"
        $after.PSObject.Properties.Name | Should -Not -Contain "env"
    }

    Context "claude CLI stub" {
        BeforeAll {
            # 실제 claude 를 부르지 않도록 PATH 앞에 기록만 하는 스텁을 둔다. Windows 는 claude.cmd,
            # Linux/macOS 의 pwsh 는 배치 파일을 실행하지 못하므로 같은 일을 하는 셸 스크립트를 함께 둔다.
            function Set-ClaudeStub([string]$Log, [string]$ListLine) {
                $cmd = @()
                if ($Log) { $cmd += "@echo %* >> `"$Log`"" }
                $cmd += "@if `"%1 %2`"==`"plugin list`" echo   $ListLine"
                Set-Content -Path (Join-Path $script:stubBin "claude.cmd") -Encoding ascii -Value $cmd
                if ($IsLinux -or $IsMacOS) {
                    $sh = @("#!/bin/sh")
                    if ($Log) { $sh += "echo `"`$*`" >> '$Log'" }
                    $sh += "[ `"`$1 `$2`" = 'plugin list' ] && echo '  $ListLine'"
                    $sh += "exit 0"
                    $p = Join-Path $script:stubBin "claude"
                    Set-Content -Path $p -Encoding ascii -Value $sh
                    chmod +x $p
                }
            }
        }
        BeforeEach {
            $script:stubBin = Join-Path $script:sandbox "stubbin"
            New-Item -ItemType Directory -Force -Path $script:stubBin | Out-Null
            $script:claudeLog = Join-Path $script:sandbox "claude.log"
            Set-ClaudeStub -Log $script:claudeLog -ListLine "other@x"
            $script:oldPath = $env:PATH
            $env:PATH = $script:stubBin + [System.IO.Path]::PathSeparator + $env:PATH
        }
        AfterEach { $env:PATH = $script:oldPath }

        It "-OutputStyle installs the fluent-korean plugin and sets only outputStyle" {
            $r = Invoke-Script $script:Setup @("-OutputStyle")
            $r.ExitCode | Should -Be 0
            $calls = Get-Content $script:claudeLog -Raw
            $calls | Should -Match "plugin marketplace add snflkd/fluent-korean"
            $calls | Should -Match "plugin install fluent-korean@fluent-korean"
            $after = Get-Content (Join-Path $env:USERPROFILE ".claude\settings.json") -Raw | ConvertFrom-Json
            $after.outputStyle | Should -Be "fluent-korean:fluent-korean"
            # -OutputStyle 만 줬으므로 다른 설정(훅 등록 포함)은 쓰지 않는다
            $after.PSObject.Properties.Name | Should -Not -Contain "hooks"
        }

        It "after -Omc -Mods on an empty home, -Uninstall leaves no settings.json and no mods" {
            Set-ClaudeStub -ListLine "oh-my-claudecode@omc"
            # HUD 래퍼 템플릿을 복사할 플러그인 캐시를 흉내 낸다
            $lib = Join-Path $env:USERPROFILE ".claude\plugins\cache\omc\oh-my-claudecode\5.4.0\scripts\lib"
            New-Item -ItemType Directory -Force -Path $lib | Out-Null
            Set-Content -Path (Join-Path $lib "hud-wrapper-template.txt") -Value "// template" -Encoding ascii
            Set-Content -Path (Join-Path $lib "config-dir.mjs") -Value "// config" -Encoding ascii
            $claudeDir = Join-Path $env:USERPROFILE ".claude"

            (Invoke-Script $script:Setup @("-Omc", "-Mods")).ExitCode | Should -Be 0
            $after = Get-Content (Join-Path $claudeDir "settings.json") -Raw | ConvertFrom-Json
            $after.statusLine.command | Should -Match "omc-hud-ttl.mjs"
            # 목록 구분자는 OS 의 경로 구분자다(apply.py 의 os.pathsep — Windows ';', 그 외 ':').
            $after.env.CLAUDE_CODE_PLUGIN_DIRS | Should -Be ((Join-Path $claudeDir "mods\bash-diff-filter") + [System.IO.Path]::PathSeparator + (Join-Path $claudeDir "mods\cache-ttl-status") + [System.IO.Path]::PathSeparator + (Join-Path $claudeDir "mods\korean-reply-rewrite"))
            Test-Path (Join-Path $claudeDir "mods\bash-diff-filter\hooks\register.ts") | Should -Be $true
            Test-Path (Join-Path $claudeDir "mods\cache-ttl-status\hooks\register.ts") | Should -Be $true
            Test-Path (Join-Path $claudeDir "mods\korean-reply-rewrite\hooks\register.ts") | Should -Be $true

            (Invoke-Script $script:Setup @("-Uninstall")).ExitCode | Should -Be 0
            Test-Path (Join-Path $claudeDir "settings.json") | Should -Be $false
            Test-Path (Join-Path $claudeDir "mods") | Should -Be $false
            Test-Path (Join-Path $claudeDir ".claude-code-setup.json") | Should -Be $false
        }

        It "--output-style --dry-run neither installs claude nor writes settings" {
            $r = Invoke-ScriptInline $script:Setup "--output-style --dry-run"
            $r.ExitCode | Should -Be 0
            $r.Output | Should -Match "\(dry-run\) claude plugin install fluent-korean@fluent-korean"
            (Get-Content $script:claudeLog -Raw) | Should -Not -Match "install"
            Test-Path (Join-Path $env:USERPROFILE ".claude\settings.json") | Should -Be $false
        }
    }
}

# Windows PowerShell 5.1 은 BOM 없는 UTF-8 .ps1 을 OS ANSI 코드페이지([System.Text.Encoding]::Default)
# 로 읽는다. DBCS 코드페이지(한국어 949, 일본어 932, 중국어 936 등) 에서는 UTF-8 멀티바이트
# 시퀀스의 특정 바이트가 개행/따옴표까지 삼켜버려 스크립트 전체가 파싱조차 안 된다 — 실측 확인됨.
Describe "Windows .ps1 encoding regression (BOM)" {
    It "every shipped .ps1 file must start with a UTF-8 BOM" {
        $files = @(
            "scripts\setup.ps1", "scripts\setup_agentsmd.ps1",
            "scripts\lib\SetupCommon.ps1", "scripts\setup_output_style.ps1", "scripts\setup_mods.ps1", "..\..\..\tests\Setup.Tests.ps1"
        )
        foreach ($f in $files) {
            $path = Join-Path $script:RepoRoot $f
            $bytes = [System.IO.File]::ReadAllBytes($path)
            $hasBom = ($bytes.Length -ge 3) -and ($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF)
            $hasBom | Should -Be $true -Because "$f without a BOM can fail to parse on Windows PowerShell 5.1 with a DBCS locale"
        }
    }

    # powershell.exe 는 Windows 에만 있다. 없는 곳에서 '실패'로 잡으면 진짜 실패가 묻힌다 —
    # 건너뛴 것은 통과가 아니라 '검증 안 됨'이며, Pester 요약에 Skipped 로 정직하게 남는다.
    It "setup.ps1 -Help parses and runs correctly under powershell.exe (Windows PowerShell 5.1)" -Skip:(-not (Get-Command powershell.exe -ErrorAction SilentlyContinue)) {
        $out = powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Setup -Help 2>&1 | Out-String
        $ec = $LASTEXITCODE
        $ec | Should -Be 0
        # 비ASCII 줄로 확인해야 BOM 없이 ANSI 로 읽혀 깨진 경우를 잡는다.
        $out | Should -Match "ai-setup — Claude Code user-level setup"
    }
}

# S1: PS 7.4+ 에서 $PSNativeCommandUseErrorActionPreference 가 $true 인 환경(사용자 프로필/그룹
# 정책/향후 PS 버전 기본값 변경 등) 에서는 `& python -c "import send2trash" *> $null` 같은 네이티브
# 명령의 0이 아닌 종료 코드가 예외로 승격되어 첫 설치가 통째로 실패한다 (실측 재현됨). 이 저장소는
# 공유 send2trash 패키지를 설치/제거하며 동적으로 재현하는 대신, 방어 코드가 소스에 남아있는지
# 정적으로 확인한다 (동적 재현은 Phase 2 수기 검증에서 이미 수행함 — docs/HANDOFF.md 참고).
Describe "jarvis install.ps1 (irm | iex path)" {
    # irm | iex 는 BOM 을 U+FEFF 문자로 남겨 5.1 파서가 첫 줄부터 무너진다(ai-setup 의 같은 가드를 옮김)
    It "install.ps1 must start without a BOM" {
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $script:RepoRoot "..\..\..\install.ps1"))
        $hasBom = ($bytes.Length -ge 3) -and ($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF)
        $hasBom | Should -Be $false
    }

    It "install.ps1 parses" {
        $errors = $null; $tokens = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $script:RepoRoot "..\..\..\install.ps1"), [ref]$tokens, [ref]$errors)
        $errors.Count | Should -Be 0
    }
}

Describe "native command error escalation guard regression (S1)" {
    It "scripts that judge native commands by LASTEXITCODE explicitly turn off PSNativeCommandUseErrorActionPreference" {
        $files = @(
            "scripts\setup.ps1", "scripts\lib\SetupCommon.ps1"
        )
        foreach ($f in $files) {
            $path = Join-Path $script:RepoRoot $f
            $content = Get-Content -Path $path -Raw
            $content | Should -Match '\$PSNativeCommandUseErrorActionPreference\s*=\s*\$false' `
                -Because "$f judges native command failures by `$LASTEXITCODE directly, so PS 7.4+ must not escalate them to exceptions"
        }
    }
}

# 온보딩은 jarvis 로 옮기지 않았다. 인자가 없으면 jarvis setup 모듈을 안내하고 아무것도 설치하지 않으며,
# 기존 플래그 호출은 그대로 돈다.
Describe "onboarding wiring" {
    BeforeEach {
        $script:sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-onboard-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:sandbox | Out-Null
        $script:oldUP = $env:USERPROFILE
        $env:USERPROFILE = $script:sandbox
    }
    AfterEach {
        $env:USERPROFILE = $script:oldUP
        Remove-Item -Recurse -Force $script:sandbox -ErrorAction SilentlyContinue
    }

    It "no arguments points to the jarvis setup module and installs nothing" {
        Push-Location $script:sandbox
        try { $r = Invoke-Script $script:Setup @() } finally { Pop-Location }
        $r.ExitCode | Should -Be 1
        $r.Output | Should -Match "jarvis module setup plan --json"
        $r.Output | Should -Match "Nothing was installed"
        Join-Path $script:sandbox "AGENTS.md" | Should -Not -Exist
    }

    It "passing a flag does not launch onboarding" {
        # 자동화/CI 가 쓰던 기존 호출이 프롬프트를 기다리며 멈추면 안 된다.
        $r = Invoke-Script $script:Setup @("-AgentsMd", "-DryRun")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Not -Match "What would you like to install"
    }

    It "-NoOnboard proceeds with the default behavior without onboarding" {
        $r = Invoke-Script $script:Setup @("-NoOnboard", "-DryRun")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Not -Match "What would you like to install"
        $r.Output | Should -Match "DRY RUN"
    }

}

Describe "AGENTS.md overwrite (-Force)" {
    BeforeEach {
        $script:proj = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-agents-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:proj | Out-Null
        $script:AgentsMd = Join-Path $script:RepoRoot "scripts\setup_agentsmd.ps1"
    }
    AfterEach {
        Remove-Item -Recurse -Force $script:proj -ErrorAction SilentlyContinue
    }

    It "without -Force the existing AGENTS.md is left alone" {
        $target = Join-Path $script:proj "AGENTS.md"
        Set-Content -Path $target -Value "사용자가 손댄 내용" -Encoding UTF8
        Push-Location $script:proj
        try { pwsh -NoProfile -File $script:AgentsMd *> $null } finally { Pop-Location }
        (Get-Content $target -Raw) | Should -Match "사용자가 손댄 내용"
    }

    It "-Force backs up the original before overwriting" {
        $target = Join-Path $script:proj "AGENTS.md"
        Set-Content -Path $target -Value "사용자가 손댄 내용" -Encoding UTF8
        Push-Location $script:proj
        try { pwsh -NoProfile -File $script:AgentsMd -Force *> $null } finally { Pop-Location }

        $backups = @(Get-ChildItem -Path $script:proj -Filter "AGENTS.md.bak.*")
        $backups.Count | Should -Be 1 -Because "overwriting user content must leave a way to undo it"
        (Get-Content $backups[0].FullName -Raw) | Should -Match "사용자가 손댄 내용"
        (Get-Content $target -Raw) | Should -Not -Match "사용자가 손댄 내용"
    }
}

Describe "project instructions harness selection" {
    BeforeEach {
        $script:proj = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-harness-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:proj | Out-Null
    }
    AfterEach {
        Remove-Item -Recurse -Force $script:proj -ErrorAction SilentlyContinue
    }

    It "with no arguments every harness is installed - AGENTS.md and CLAUDE.md" {
        (Invoke-AgentsMdIn $script:proj).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $true
    }

    It "-Harness codex does not create CLAUDE.md" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "codex")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $false -Because "Codex reads AGENTS.md directly"
    }

    It "-Harness agy,gemini,copilot writes AGENTS.md and not CLAUDE.md" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "agy,gemini,copilot")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $false
    }

    It "-Harness grok,opencode does not create CLAUDE.md" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "grok,opencode")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $false
    }

    It "-Harness claude,codex is a multi-select that installs both" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "claude,codex")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $true
    }

    It "-Harness all is the same as -Harness claude,codex,grok,opencode" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "all")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $true
    }

    It "CLAUDE.md imports AGENTS.md" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "claude")).ExitCode | Should -Be 0
        (Get-Content (Join-Path $script:proj "CLAUDE.md") -Raw) | Should -Match "@AGENTS\.md"
    }

    It "an unknown harness installs nothing and fails" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "cursor")).ExitCode | Should -Not -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $false -Because "input that cannot be judged must stop the run"
    }

    It "-DryRun creates no files whichever harness is chosen" {
        (Invoke-AgentsMdIn $script:proj @("-Harness", "claude,codex", "-DryRun")).ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $false
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $false
    }

    It "an existing CLAUDE.md is left alone without -Force" {
        $target = Join-Path $script:proj "CLAUDE.md"
        Set-Content -Path $target -Value "사용자가 손댄 내용" -Encoding UTF8
        (Invoke-AgentsMdIn $script:proj @("-Harness", "claude")).ExitCode | Should -Be 0
        (Get-Content $target -Raw) | Should -Match "사용자가 손댄 내용"
    }

    It "warns about an existing CLAUDE.md that does not import @AGENTS.md" {
        # 건너뛰기 자체는 옳다 — 사용자 콘텐츠다. 다만 그 파일이 AGENTS.md 를 불러오지
        # 않으면 Claude Code 는 방금 설치한 지침을 영영 읽지 않는데, 조용히 성공으로
        # 보고하면 사용자가 알 방법이 없다.
        $target = Join-Path $script:proj "CLAUDE.md"
        Set-Content -Path $target -Value "프로젝트 고유 규칙만 있음" -Encoding UTF8

        $r = Invoke-AgentsMdIn $script:proj @("-Harness", "claude")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Match "@AGENTS\.md"
        $r.Output | Should -Match "will not read"
        (Get-Content $target -Raw) | Should -Match "프로젝트 고유 규칙만 있음" -Because "warn, but leave the user file untouched"
    }

    It "does not warn about a CLAUDE.md that already imports @AGENTS.md" {
        $target = Join-Path $script:proj "CLAUDE.md"
        Set-Content -Path $target -Value "@AGENTS.md`n`n## 프로젝트 고유" -Encoding UTF8

        $r = Invoke-AgentsMdIn $script:proj @("-Harness", "claude")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Not -Match "will not read"
    }

    It "-Force also backs up CLAUDE.md before overwriting it" {
        $target = Join-Path $script:proj "CLAUDE.md"
        Set-Content -Path $target -Value "사용자가 손댄 내용" -Encoding UTF8
        (Invoke-AgentsMdIn $script:proj @("-Harness", "claude", "-Force")).ExitCode | Should -Be 0

        $backups = @(Get-ChildItem -Path $script:proj -Filter "CLAUDE.md.bak.*")
        $backups.Count | Should -Be 1 -Because "it must get the same recovery guarantee as AGENTS.md"
        (Get-Content $backups[0].FullName -Raw) | Should -Match "사용자가 손댄 내용"
        (Get-Content $target -Raw) | Should -Match "@AGENTS\.md"
    }

    # setup.sh 처럼 긴 표기 --harness 도 디스패처를 거쳐 워커까지 가야 한다.
    It "setup.ps1 --agentsmd --harness codex does not create CLAUDE.md" {
        $oldUP = $env:USERPROFILE
        $env:USERPROFILE = $script:proj
        Push-Location $script:proj
        try {
            $r = Invoke-ScriptInline $script:Setup "--agentsmd --harness codex"
        } finally { Pop-Location; $env:USERPROFILE = $oldUP }
        $r.ExitCode | Should -Be 0
        Test-Path (Join-Path $script:proj "AGENTS.md") | Should -Be $true
        Test-Path (Join-Path $script:proj "CLAUDE.md") | Should -Be $false
    }
}

# 줄바꿈 키바인드 디스패처. 실제 프로필을 건드리지 않도록 HOME/APPDATA 와
# CLAUDE_SETUP_* 경로를 샌드박스로 돌린다.
Describe "keybinding dispatcher" {
    BeforeEach {
        $script:sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-kb-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $script:sandbox | Out-Null
        $script:oldUP = $env:USERPROFILE
        $script:oldHome = $env:HOME
        $script:oldApp = $env:APPDATA
        $script:oldLocal = $env:LOCALAPPDATA
        $script:oldVs = $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS
        $script:oldCur = $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS
        $script:oldWt = $env:CLAUDE_SETUP_WT_SETTINGS
        $env:USERPROFILE = $script:sandbox
        $env:HOME = $script:sandbox
        $env:APPDATA = Join-Path $script:sandbox "appdata"
        $env:LOCALAPPDATA = Join-Path $script:sandbox "local"
        New-Item -ItemType Directory -Force -Path $env:APPDATA | Out-Null
        New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA | Out-Null
        $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS = Join-Path $script:sandbox "vscode-keybindings.json"
        $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS = Join-Path $script:sandbox "cursor-keybindings.json"
        $env:CLAUDE_SETUP_WT_SETTINGS = Join-Path $script:sandbox "wt-settings.json"
        Set-Content -Path $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Value '[]' -Encoding utf8
        Set-Content -Path $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS -Value '[]' -Encoding utf8
        Set-Content -Path $env:CLAUDE_SETUP_WT_SETTINGS -Value '{"actions":[]}' -Encoding utf8
    }
    AfterEach {
        $env:USERPROFILE = $script:oldUP
        if ($null -eq $script:oldHome) { Remove-Item Env:HOME -ErrorAction SilentlyContinue } else { $env:HOME = $script:oldHome }
        if ($null -eq $script:oldApp) { Remove-Item Env:APPDATA -ErrorAction SilentlyContinue } else { $env:APPDATA = $script:oldApp }
        if ($null -eq $script:oldLocal) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $script:oldLocal }
        if ($null -eq $script:oldVs) { Remove-Item Env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS = $script:oldVs }
        if ($null -eq $script:oldCur) { Remove-Item Env:CLAUDE_SETUP_CURSOR_KEYBINDINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_CURSOR_KEYBINDINGS = $script:oldCur }
        if ($null -eq $script:oldWt) { Remove-Item Env:CLAUDE_SETUP_WT_SETTINGS -ErrorAction SilentlyContinue } else { $env:CLAUDE_SETUP_WT_SETTINGS = $script:oldWt }
        Remove-Item -Recurse -Force $script:sandbox -ErrorAction SilentlyContinue
    }

    It "-Help shows -Keybinds and -Newline (covers: Test-15)" {
        $r = Invoke-Script $script:Setup @("-Help")
        $r.ExitCode | Should -Be 0
        $r.Output | Should -Match "Keybinds"
        $r.Output | Should -Match "Newline"
    }

    It "-Keybinds -DryRun writes no target files and shows the paths in the plan (covers: Test-10)" {
        $beforeVs = Get-Content $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Raw
        $beforeWt = Get-Content $env:CLAUDE_SETUP_WT_SETTINGS -Raw
        $r = Invoke-Script $script:Setup @("-Keybinds", "-DryRun")
        $r.ExitCode | Should -Be 0
        (Get-Content $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Raw) | Should -Be $beforeVs
        (Get-Content $env:CLAUDE_SETUP_WT_SETTINGS -Raw) | Should -Be $beforeWt
        Test-Path (Join-Path $env:USERPROFILE ".claude\settings.json") | Should -Be $false
        $r.Output | Should -Match "keybindings.json"
    }

    It "a disallowed -Newline exits 1 and leaves files untouched (covers: Test-8)" {
        $beforeVs = Get-Content $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Raw
        $r = Invoke-Script $script:Setup @("-Keybinds", "-Newline", "foo")
        $r.ExitCode | Should -Be 1
        (Get-Content $env:CLAUDE_SETUP_VSCODE_KEYBINDINGS -Raw) | Should -Be $beforeVs
        $r.Output | Should -Not -Match "parameter cannot be found"
        $r.Output | Should -Match "Newline|newline|shift-enter"
    }

    It "-Keybinds does not install hooks or AGENTS.md (covers: Test-6)" {
        $r = Invoke-Script $script:Setup @("-Keybinds")
        $r.ExitCode | Should -Be 0
        Test-Path (Join-Path $env:USERPROFILE ".claude\hooks\danger_guard.py") | Should -Be $false
        Test-Path (Join-Path $script:sandbox "AGENTS.md") | Should -Be $false
        Test-Path (Join-Path $env:USERPROFILE ".claude\.claude-code-setup.json") | Should -Be $true
    }
}

Describe "Python-not-installed guidance" {
    It "without Python, setup_skills.ps1 fails and shows the winget install command" -Skip:(-not $IsWindows) {
        $oldPath = $env:PATH; $oldUP = $env:USERPROFILE
        $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("cc-pester-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
        try {
            $env:USERPROFILE = $sandbox
            # python/python3/py 가 하나도 보이지 않는 PATH (Store 별칭이 있는 WindowsApps 도 뺀다).
            $env:PATH = "$PSHOME;$env:SystemRoot\System32"
            $pwshExe = (Get-Process -Id $PID).Path
            $out = & $pwshExe -NoProfile -File (Join-Path $script:RepoRoot "scripts\setup_skills.ps1") -DryRun 2>&1 | Out-String
            $LASTEXITCODE | Should -Be 1
            $out | Should -Match "Python 3 is required"
            $out | Should -Match "winget install -e --id Python\.Python\.3"
        } finally {
            $env:PATH = $oldPath; $env:USERPROFILE = $oldUP
            Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
        }
    }
}
