# res/hooks/shims/RmShim.psm1 이 원본 Remove-Item 과 '관측 가능한 결과'가 같은지 대조한다.
#
# 이 셰임의 존재 이유는 훅이 명령 문자열을 재작성하지 않게 하는 것이다. 그래서 검증도
# "치환된 문자열이 어떻게 생겼나"가 아니라 "같은 명령을 원본 cmdlet 으로 돌렸을 때와 결과가
# 같은가"로 한다. 각 케이스를 동일한 픽스처 두 벌에서 실행하고 살아남은 파일 목록과 오류
# 발생 여부를 비교한다.

BeforeAll {
    $script:RepoRoot = Split-Path -Parent $PSScriptRoot
    $script:ModulePath = Join-Path $RepoRoot 'jarvis/modules/hooks/shims/RmShim.psm1'
    # 픽스처는 저장소 안에 만든다 — 실제 사용자 홈을 테스트 대상으로 쓰지 않는다.
    $script:Base = Join-Path $RepoRoot '.tmp-rmshim-ps'
    if (Test-Path $Base) { Remove-Item -LiteralPath $Base -Recurse -Force }
    New-Item -ItemType Directory -Path $Base -Force | Out-Null

    function New-Fixture([string]$dir) {
        if (Test-Path -LiteralPath $dir) {
            Microsoft.PowerShell.Management\Remove-Item -LiteralPath $dir -Recurse -Force
        }
        New-Item -ItemType Directory -Path (Join-Path $dir 'sub/deep') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $dir 'emptydir') -Force | Out-Null
        foreach ($n in 'a.txt', 'b.txt', 'c.log', 'name with spaces.txt') {
            'x' | Set-Content -LiteralPath (Join-Path $dir $n)
        }
        'x' | Set-Content -LiteralPath (Join-Path $dir 'sub/deep/x.log')
    }

    # 실제 trash-put 을 쓰면 픽스처가 사용자 휴지통에 쌓인다. 대조 테스트는 남은 파일만 비교하므로
    # 대상을 지우기만 하는 가짜를 PATH 앞에 둔다(POSIX). Windows 는 send2trash 경로라 해당 없다.
    $script:FakeTrashBin = Join-Path $Base 'fake-trash-bin'
    if (-not $IsWindows) {
        New-Item -ItemType Directory -Path $FakeTrashBin -Force | Out-Null
        $fakeTrash = Join-Path $FakeTrashBin 'trash-put'
        Set-Content -LiteralPath $fakeTrash -Value "#!/bin/sh`n[ `"`$1`" = -- ] && shift`nexec /bin/rm -rf -- `"`$@`"`n" -NoNewline
        & chmod 755 $fakeTrash
    }

    function Get-Observation([string]$script, [bool]$UseShim) {
        $dir = Join-Path $Base $(if ($UseShim) { 'shim' } else { 'real' })
        New-Fixture $dir
        $import = if ($UseShim) { "Import-Module '$ModulePath' -Force -DisableNameChecking -Global" } else { '' }
        if ($UseShim -and -not $IsWindows) {
            $import = "`$env:PATH = '$FakeTrashBin' + [IO.Path]::PathSeparator + `$env:PATH`n" + $import
        }
        # 대조할 원본 쪽은 실행 중인 세션 PATH 의 rm 셔임(~/.claude/hooks/shims)을 물려받지 않게 한다(실제 휴지통으로 가지 않게).
        if (-not $UseShim) {
            $import = "`$env:PATH = ((`$env:PATH -split [IO.Path]::PathSeparator) | Where-Object { `$_ -notmatch '[\\/]hooks[\\/]shims$' }) -join [IO.Path]::PathSeparator`n" + $import
        }
        $body = @"
`$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath '$dir'
`$ErrorActionPreference = 'Continue'
$import
`$global:Error.Clear()
$script
if (`$global:Error.Count -gt 0) { Write-Output '<<ERR>>' }
Get-ChildItem -Recurse -Force -Name | Sort-Object | ForEach-Object { Write-Output "F:`$_" }
"@
        $out = & (Join-Path $PSHOME 'pwsh') -NoProfile -Command $body 2>&1
        return (($out | Where-Object { $_ -match '^(F:|<<ERR>>)' }) -join "`n")
    }

    # 셰임이 실제로 잡혔는지, 홈이 샌드박스인지 같은 세션에서 확인하고, 아니면 아무것도 지우지 않고 97 로 끝낸다.
    function Invoke-Guarded([string]$module, [bool]$posixHome, [string]$sandbox, [string]$script) {
        $out = & (Join-Path $PSHOME 'pwsh') -NoProfile -Command @"
`$ErrorActionPreference='Stop'
Import-Module '$module' -Force -DisableNameChecking -Global
if ((Get-Command Remove-Item).Source -ne 'RmShim') { exit 97 }
if ('$posixHome' -eq 'True' -and `$HOME -ne '$sandbox') { exit 97 }
`$ErrorActionPreference='Continue'
$script
"@ 2>&1 | Out-String
        return [pscustomobject]@{ Out = $out; Rc = $LASTEXITCODE }
    }
}

AfterAll {
    if (Test-Path $Base) {
        Microsoft.PowerShell.Management\Remove-Item -LiteralPath $Base -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
}

Describe 'RmShim gives the same result as the real Remove-Item' {
    It '<name>' -ForEach @(
        @{ name = 'plain file';                     script = 'Remove-Item a.txt' }
        @{ name = 'rm alias';                       script = 'rm a.txt' }
        @{ name = 'del alias';                      script = 'del a.txt' }
        @{ name = 'ri alias';                       script = 'ri a.txt' }
        @{ name = 'directory -Recurse';             script = 'Remove-Item sub -Recurse' }
        @{ name = 'directory -Recurse -Force';      script = 'Remove-Item sub -Recurse -Force' }
        @{ name = 'empty directory';                script = 'Remove-Item emptydir' }
        @{ name = 'wildcard';                       script = 'Remove-Item *.txt' }
        @{ name = 'unmatched wildcard';             script = 'Remove-Item *.nomatch' }
        @{ name = 'nonexistent file';               script = 'Remove-Item nope.txt' }
        @{ name = 'nonexistent file -EA Silent';    script = 'Remove-Item nope.txt -ErrorAction SilentlyContinue' }
        @{ name = 'multiple targets';               script = 'Remove-Item a.txt,b.txt,c.log' }
        @{ name = 'only some exist';                script = 'Remove-Item a.txt,nope.txt,b.txt' }
        @{ name = 'name with spaces';               script = 'Remove-Item "name with spaces.txt"' }
        @{ name = 'variable expansion';             script = '$f = "a.txt"; Remove-Item $f' }
        @{ name = 'variable + path join';           script = '$d = "sub"; Remove-Item "$d/deep" -Recurse' }
        @{ name = '-LiteralPath';                   script = 'Remove-Item -LiteralPath a.txt' }
        @{ name = 'explicit -Path';                 script = 'Remove-Item -Path a.txt' }
        @{ name = 'pipeline';                       script = 'Get-ChildItem *.log -Recurse | Remove-Item' }
        @{ name = 'pipeline directory';             script = 'Get-ChildItem -Directory | Remove-Item -Recurse' }
        # 원본 cmdlet 으로 넘기는 경로(위임·휴지통 없음)도 파이프라인 항목을 전부 지워야 한다.
        @{ name = 'pipeline -Confirm:$false';       script = 'Get-ChildItem *.txt | Remove-Item -Confirm:$false' }
        @{ name = 'pipeline strings -Confirm:$false'; script = '"a.txt","b.txt" | Remove-Item -Confirm:$false' }
        # 휴지통 도구가 없으면 셰임이 잡은 예외가 $Error 에 남으므로, 이 케이스는 남은 파일만 비교한다.
        @{ name = 'pipeline without a trash tool';  script = '$env:PATH = ""; Get-ChildItem *.txt | Remove-Item; $global:Error.Clear()' }
        @{ name = '-WhatIf does not delete';        script = 'Remove-Item a.txt -WhatIf' }
        @{ name = '-Include filter';                script = 'Remove-Item * -Include *.log -Recurse' }
        @{ name = 'nested path';                    script = 'Remove-Item sub/deep/x.log' }
        @{ name = 'non-filesystem provider';        script = '$env:CLAUDETMP="x"; Remove-Item Env:CLAUDETMP' }
    ) {
        $real = Get-Observation $script $false
        $shim = Get-Observation $script $true
        $shim | Should -Be $real
    }
}

Describe 'refuses to delete the entire root/home' {
    # 실제 홈을 대상으로 삼지 않는다. 셰임 import 가 실패하거나 퇴행하면 원본 Remove-Item 이 그대로
    # 지우기 때문이다. 자식 프로세스의 홈을 샌드박스로 두고, 휴지통 도구도 아무것도 옮기지 않는 가짜로 둔다.
    # 셰임은 $env:USERPROFILE 을 먼저 보고, 없으면(POSIX pwsh) $HOME 을 본다. 두 경로를 각각 검증한다.
    It '<script>' -ForEach @(
        @{ script = 'Remove-Item $env:USERPROFILE -Recurse -Force'; posixHome = $false }
        @{ script = 'Remove-Item -LiteralPath $env:USERPROFILE'; posixHome = $false }
        @{ script = 'Remove-Item $HOME -Recurse -Force'; posixHome = $true }
        # 원본 cmdlet 으로 위임되는 인자도 거부가 먼저 적용돼야 한다.
        @{ script = 'Remove-Item $env:USERPROFILE -Recurse -Force -Confirm:$false'; posixHome = $false }
        @{ script = 'Remove-Item $HOME -Recurse -Force -Confirm:$false'; posixHome = $true }
        @{ script = 'Remove-Item $HOME -Recurse -Force -Include *'; posixHome = $true }
        @{ script = 'Remove-Item $HOME -Recurse -Force -Exclude nomatch'; posixHome = $true }
        @{ script = 'Remove-Item $HOME -Recurse -Force -Filter *'; posixHome = $true }
        @{ script = 'Get-Item -LiteralPath $HOME | Remove-Item -Recurse -Force -Confirm:$false'; posixHome = $true }
        # 홈 경로에 링크가 끼어 있어도(Windows 의 C:\Documents and Settings 정션 등) 실제 경로로 비교한다.
        # 홈은 <Base>/lnk/home 이고 lnk 가 realdir 을 가리킨다. 지우는 쪽은 실제 경로 realdir/home 이다.
        @{ script = 'Remove-Item $env:CLAUDE_REAL_HOME -Recurse -Force'; posixHome = $false; viaLink = $true }
        @{ script = 'Remove-Item $env:CLAUDE_REAL_HOME -Recurse -Force'; posixHome = $true; viaLink = $true }
        @{ script = 'Remove-Item $env:CLAUDE_REAL_HOME -Recurse -Force -Exclude nomatch'; posixHome = $true; viaLink = $true }
    ) {
        if ($posixHome -and $IsWindows) {
            Set-ItResult -Skipped -Because 'On Windows, $HOME does not follow the HOME environment variable - verified via the USERPROFILE path'
            return
        }
        $sandbox = Join-Path $Base 'guarded-home'
        $homeDir = $sandbox
        if ($viaLink) {
            $sandbox = Join-Path $Base 'realdir/home'
            $lnk = Join-Path $Base 'lnk'
            if (-not (Test-Path -LiteralPath $lnk)) {
                New-Item -ItemType $(if ($IsWindows) { 'Junction' } else { 'SymbolicLink' }) -Path $lnk `
                    -Target (Split-Path -Parent $sandbox) -Force | Out-Null
            }
            $homeDir = Join-Path $lnk 'home'
        }
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        'x' | Set-Content -LiteralPath (Join-Path $sandbox 'keep')
        $fakeBin = Join-Path $Base 'fakebin'
        New-Item -ItemType Directory -Path $fakeBin -Force | Out-Null
        if (-not $IsWindows) {
            $fake = Join-Path $fakeBin 'trash-put'
            "#!/bin/sh`necho TRASHED `"`$*`"" | Set-Content -LiteralPath $fake
            chmod +x $fake
        }
        $old = @($env:USERPROFILE, $env:HOME, $env:PATH, $env:CLAUDE_REAL_HOME)
        $env:USERPROFILE = if ($posixHome) { $null } else { $homeDir }
        $env:HOME = $homeDir
        $env:PATH = $fakeBin + [System.IO.Path]::PathSeparator + $env:PATH
        $env:CLAUDE_REAL_HOME = $sandbox
        try {
            $r = Invoke-Guarded $ModulePath $posixHome $homeDir $script
        } finally {
            $env:USERPROFILE = $old[0]; $env:HOME = $old[1]; $env:PATH = $old[2]; $env:CLAUDE_REAL_HOME = $old[3]
        }
        $r.Rc | Should -Not -Be 97 -Because "the shim and the sandbox home must be in effect: $($r.Out)"
        $r.Out | Should -Match 'refusing'
        (Test-Path -LiteralPath (Join-Path $sandbox 'keep')) | Should -BeTrue
    }

    # 링크 자체를 지우는 것은 홈 삭제가 아니다. 홈을 가리키는 링크라도 링크만 휴지통으로 가야 한다.
    It 'removes a link that points to the home without refusing' {
        $sandbox = Join-Path $Base 'linked-home'
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        'x' | Set-Content -LiteralPath (Join-Path $sandbox 'keep')
        $link = Join-Path $Base 'home-link'
        if (-not (Test-Path -LiteralPath $link)) {
            New-Item -ItemType $(if ($IsWindows) { 'Junction' } else { 'SymbolicLink' }) -Path $link `
                -Target $sandbox -Force | Out-Null
        }
        $fakeBin = Join-Path $Base 'fakebin'
        New-Item -ItemType Directory -Path $fakeBin -Force | Out-Null
        if (-not $IsWindows) {
            $fake = Join-Path $fakeBin 'trash-put'
            "#!/bin/sh`necho TRASHED `"`$*`"" | Set-Content -LiteralPath $fake
            chmod +x $fake
        }
        $old = @($env:USERPROFILE, $env:HOME, $env:PATH)
        $env:USERPROFILE = if ($IsWindows) { $sandbox } else { $null }
        $env:HOME = $sandbox
        $env:PATH = $fakeBin + [System.IO.Path]::PathSeparator + $env:PATH
        try {
            $r = Invoke-Guarded $ModulePath (-not $IsWindows) $sandbox "Remove-Item -LiteralPath '$link'"
        } finally { $env:USERPROFILE = $old[0]; $env:HOME = $old[1]; $env:PATH = $old[2] }
        $r.Rc | Should -Not -Be 97 -Because "the shim and the sandbox home must be in effect: $($r.Out)"
        $r.Out | Should -Not -Match 'refusing'
        if (-not $IsWindows) { $r.Out | Should -Match ([regex]::Escape("TRASHED -- $link")) }
        (Test-Path -LiteralPath (Join-Path $sandbox 'keep')) | Should -BeTrue
    }

    # 가드 자체의 검증. 셰임이 잡히지 않으면 원본 Remove-Item 이 지우기 전에 97 로 끝나야 한다.
    It 'the guard stops before deleting when the shim is not loaded' {
        $sandbox = Join-Path $Base 'guard-check'
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        'x' | Set-Content -LiteralPath (Join-Path $sandbox 'keep')
        $empty = Join-Path $Base 'NotTheShim.psm1'
        '# Remove-Item 을 가리지 않는 빈 모듈' | Set-Content -LiteralPath $empty
        $r = Invoke-Guarded $empty $false $sandbox "Remove-Item -LiteralPath '$sandbox' -Recurse -Force"
        $r.Rc | Should -Be 97
        (Test-Path -LiteralPath (Join-Path $sandbox 'keep')) | Should -BeTrue
    }
}

Describe 'deleted content can be recovered' {
    It 'shows up in the trash' {
        if (-not (Get-Command trash-list -ErrorAction SilentlyContinue)) {
            Set-ItResult -Skipped -Because 'trash-cli not installed (on Windows the send2trash path is used)'
            return
        }
        $dir = Join-Path $Base 'rec'
        New-Fixture $dir
        # 휴지통도 실제 홈의 것을 쓰지 않는다. `pip --user` 로 깔린 trash-cli 가 모듈을 찾도록
        # PYTHONUSERBASE 만 실제 위치로 둔다.
        $trashHome = Join-Path $Base 'trash-home'
        $userBase = (& python3 -c 'import site; print(site.getuserbase())' | Out-String).Trim()
        $old = @($env:HOME, $env:XDG_DATA_HOME, $env:PYTHONUSERBASE)
        $env:HOME = $trashHome
        $env:XDG_DATA_HOME = Join-Path $trashHome '.local/share'
        $env:PYTHONUSERBASE = $userBase
        try {
            & (Join-Path $PSHOME 'pwsh') -NoProfile -Command @"
Import-Module '$ModulePath' -Force -DisableNameChecking -Global
Set-Location -LiteralPath '$dir'
Remove-Item sub -Recurse -Force
"@ | Out-Null
            $listing = & trash-list | Out-String
        } finally { $env:HOME = $old[0]; $env:XDG_DATA_HOME = $old[1]; $env:PYTHONUSERBASE = $old[2] }
        (Test-Path -LiteralPath (Join-Path $dir 'sub')) | Should -BeFalse
        $listing | Should -BeLike ("*" + (Join-Path $dir 'sub') + "*")
        (Test-Path -LiteralPath (Join-Path $trashHome '.local/share/Trash/files/sub')) | Should -BeTrue
    }
}

Describe 'link resolution cost' {
    # 링크 해석은 항목마다 Get-Item 을 여러 번 부른다. 홈과 이름이 다른 항목까지 해석하면
    # 파일 수백 개짜리 파이프라인이 수십 초가 걸린다. 홈만 한 번 해석해야 한다.
    It 'resolves the home once and skips items whose name differs from it' {
        $dir = Join-Path $Base 'many'
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        1..20 | ForEach-Object { 'x' | Set-Content -LiteralPath (Join-Path $dir "f$_.txt") }
        Import-Module $ModulePath -Force -DisableNameChecking
        try {
            (Get-Command Remove-Item).Source | Should -Be 'RmShim'
            Mock Resolve-ClaudeRealPath -ModuleName RmShim { $Path }
            Get-ChildItem -LiteralPath $dir | Remove-Item -WhatIf
            Should -Invoke Resolve-ClaudeRealPath -ModuleName RmShim -Times 1 -Exactly
        } finally { Remove-Module RmShim }
        @(Get-ChildItem -LiteralPath $dir).Count | Should -Be 20
    }
}
