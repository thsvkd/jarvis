<#
jarvis 설치기(Windows): 최신 릴리스를 받아 게이트웨이(deploy\install-windows.ps1)를 설치하고 모듈(훅·셋업)을 적용한다.
관리자 PowerShell 에서 실행한다(install-windows.ps1 이 tailnet 에만 여는 방화벽 규칙을 만든다). 다시 실행해도 된다.

  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/thsvkd/jarvis/main/install.ps1))) -Core <core 의 tailnet IP>

코드는 %LOCALAPPDATA%\jarvis\app 에 둔다(%USERPROFILE%\Workspace\jarvis 는 개발 저장소 자리). 1.8.0 이전 배치는 app 으로 옮긴다.
Python 3.11+ 가 없으면 winget 으로 설치한다. 이미 있는 설정·코드는 덮어쓰지 않는다. -DryRun 은 할 일만 보이고 아무것도 바꾸지 않는다.
#>
param([string]$Core = "", [switch]$DryRun)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false  # 네이티브 명령은 $LASTEXITCODE 로 판정한다

$manifestUrl = if ($env:JARVIS_MANIFEST) { $env:JARVIS_MANIFEST } else { "https://github.com/thsvkd/jarvis/releases/latest/download/manifest.json" }
$repo = Join-Path $env:LOCALAPPDATA "jarvis\app"  # launch.ROOT\app
$old = Join-Path $env:USERPROFILE "Workspace\jarvis"  # 1.8.0 이전 배치
$cfg = Join-Path $env:USERPROFILE ".config\jarvis\config.toml"

function Fail([string]$message) {
    Write-Host $message -ForegroundColor Red
    # irm | iex·scriptblock 으로 실행되면 exit 가 사용자의 셸을 닫으므로 그때는 $LASTEXITCODE 로만 남긴다
    if ($PSCommandPath) { exit 1 }
    $global:LASTEXITCODE = 1
    throw $message
}

function Find-Python {
    foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" })) {
        & $c.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $c.Source }
    }
    # 문자열 순이면 Python39 가 Python313 보다 앞선다. 판 숫자로 정렬하고 3.11+ 인지 확인한다
    $found = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Directory.Name -replace '\D', '') } -Descending
    foreach ($p in $found) {
        & $p.FullName -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $p.FullName }
    }
    return $null
}

if (-not (Test-Path $cfg) -and -not $Core) { Fail "처음 설치에는 -Core <core 의 tailnet IP> 가 필요합니다(이 기기의 사용량·모듈을 모아 볼 기기)" }
$octet = '(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)'
if ($Core -and $Core -notmatch "^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.$octet\.$octet$") { Fail "-Core 에는 tailnet IP(100.64.0.0/10)만 받습니다: $Core" }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $DryRun) { Fail "관리자 PowerShell 에서 실행하세요(방화벽 규칙과 예약 작업을 만든다)" }

$py = Find-Python
$steps = @()
if (-not $py) { $steps += "winget install -e --id Python.Python.3.13 --scope user" }
$task = Get-ScheduledTask -TaskName jarvis -ErrorAction SilentlyContinue
# 개발 체크아웃(.git)으로 이미 등록된 기기는 그대로 둔다(받지도 옮기지도 않는다)
if (-not (Test-Path $repo) -and (Test-Path (Join-Path $old ".git")) -and $task) { $repo = $old }
# 1.8.0 이전 배치(.git 없는 실행본)로 등록된 기기: 옛 base 를 그대로 app 으로 옮긴다. 새 릴리스를 app 에 풀지 않는다
# (base 가 바뀌면 런처의 롤백 기준 previous=None → base 가 바뀐다)
$relocate = $task -and ($repo -ne $old) -and (Test-Path (Join-Path $old "jarvis\__init__.py")) -and -not (Test-Path (Join-Path $old ".git"))
if ($relocate) { $steps += "relocate $old -> $repo (python -m jarvis.relocate from the latest release in a temp folder)" }
elseif (-not (Test-Path $repo)) { $steps += "release bundle -> $repo (sha256 verified)" }
if (-not (Test-Path $cfg)) { $steps += "config $cfg (core = $Core)" }
if (-not $task) { $steps += "deploy\install-windows.ps1 (firewall rule jarvis-9300, scheduled task jarvis)" }
$steps += "modules: hooks, setup (plan -> apply with its plan_id)"
if ($DryRun) { $steps | ForEach-Object { "would: $_" }; return }

# 1. Python 3.11+
if (-not $py) {
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    $py = Find-Python
    if (-not $py) { Fail "Python 3.11+ 를 찾지 못했습니다. python.org 설치본을 깐 뒤 다시 실행하세요" }
}

# 2. 코드: 최신 릴리스 묶음(크기가 아니라 sha256 으로 확인). 이후 갱신은 core 의 jarvis 업데이트 버튼이 한다
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("jarvis-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory $tmp | Out-Null
function Get-Release([string]$dest) {
    $manifest = Invoke-RestMethod $manifestUrl
    $tgz = Join-Path $tmp "jarvis.tar.gz"
    Invoke-WebRequest $manifest.url -OutFile $tgz -UseBasicParsing
    if ((Get-FileHash $tgz -Algorithm SHA256).Hash -ne $manifest.sha256.ToUpper()) { Fail "릴리스 묶음의 sha256 이 매니페스트와 다릅니다" }
    New-Item -ItemType Directory $dest -Force | Out-Null
    tar -xzf $tgz -C $dest --strip-components 1
    if ($LASTEXITCODE -ne 0) { Fail "릴리스 묶음을 풀지 못했습니다" }
}
try {
    if ($relocate) {
        # 옛 base 에는 relocate 가 없다. 새 코드(app 에 있으면 그것, 없으면 임시 폴더에 받은 릴리스)로 부른다.
        # 관리자 셸이라 예약 작업을 고칠 수 있다. 돌고 있는 게이트웨이는 다음 기동부터 app 에서 뜬다
        $src = $repo
        if (-not (Test-Path (Join-Path $repo "jarvis\relocate.py"))) { $src = Join-Path $tmp "src"; Get-Release $src }
        Push-Location $src
        try { & { $ErrorActionPreference = "Continue"; & $py -m jarvis.relocate } } finally { Pop-Location }
        if (-not (Test-Path $repo)) { $repo = $old }  # 이전이 실패했으면 아직 옛 배치가 실행본이다
    } elseif (-not (Test-Path $repo)) {
        # 다 푼 뒤에만 app 이름을 준다(반쯤 푼 app 을 쓰지 않게. 남은 .app-tmp-* 는 relocate 가 치운다)
        $part = Join-Path (Split-Path -Parent $repo) (".app-tmp-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        try { Get-Release $part } catch { Remove-Item -Recurse -Force $part -ErrorAction SilentlyContinue; throw }
        Move-Item $part $repo
    }
} finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }

# 3. 설정: 없을 때만 windows.toml 을 바탕으로 core 를 넣는다
if (-not (Test-Path $cfg)) {
    New-Item -ItemType Directory (Split-Path -Parent $cfg) -Force | Out-Null
    # 5.1 의 Get-Content 는 BOM 없는 UTF-8 을 시스템 코드 페이지로, Set-Content -Encoding utf8 은 BOM 을 붙여 쓴다.
    # jarvis 의 tomllib 은 BOM 을 받지 않으므로 .NET 으로 UTF-8(BOM 없음)을 그대로 읽고 쓴다. 줄 끝은 CRLF 일 수 있다(git 체크아웃)
    $text = [IO.File]::ReadAllText((Join-Path $repo "deploy\windows.toml"), [Text.Encoding]::UTF8) -replace '(?m)^core = \[[^\r\n]*\]', "core = [`"$Core`"]"
    [IO.File]::WriteAllText($cfg, $text, (New-Object Text.UTF8Encoding $false))
}

# 4. 게이트웨이(예약 작업). 이미 있으면 건드리지 않는다(다시 등록하면 돌고 있는 게이트웨이와 적용 작업이 끊긴다).
# install-windows.ps1 은 자기 위치($PSScriptRoot) 기준으로 작업을 만든다: app 의 것을 부른다
if (-not $task) { & (Join-Path $repo "deploy\install-windows.ps1") -Python $py }

# 5. 모듈: 계획을 보이고 그 plan_id 로 적용한다. jarvis 는 JSON 을 UTF-8 로 낸다(5.1 의 기본 콘솔 인코딩은 949 등)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
Push-Location $repo
try {
    foreach ($m in "hooks", "setup") {
        # 5.1 은 Stop 에서 네이티브 stderr(진행 로그)를 종료 오류로 바꾼다. 이 호출만 Continue 로 두고 종료 코드로 판정한다
        $raw = & { $ErrorActionPreference = "Continue"; & $py -m jarvis module $m plan --json 2>$null }
        if ($LASTEXITCODE -ne 0) { Write-Host "${m}: 계획을 세우지 못했습니다" -ForegroundColor Yellow; continue }
        $plan = $raw | ConvertFrom-Json
        foreach ($s in $plan.steps) { "  ${m}: $($s.summary)" }
        foreach ($b in $plan.blocked) { "  ${m}: 막힘 $($b.step) $($b.reason)" }
        if (-not $plan.steps) { "  ${m}: 바꿀 것 없음"; continue }
        # 진행 로그(표준 오류)는 콘솔에 그대로 두고, 결과에서 실패·건너뛴 단계만 보인다
        $result = & { $ErrorActionPreference = "Continue"; & $py -m jarvis module $m apply --json --plan-id $plan.plan_id }
        $code = $LASTEXITCODE
        try { foreach ($s in ($result | ConvertFrom-Json).steps) { if ($s.result -ne "ok") { "  ${m}: $($s.id) $($s.result) $($s.message)" } } } catch {}
        switch ($code) { 0 { "${m}: 적용했습니다" } 2 { "${m}: 보류(열린 터미널·앱이 닫히면 다시 실행하세요)" } default { Write-Host "${m}: 적용하지 못했습니다(종료 코드 $LASTEXITCODE)" -ForegroundColor Yellow } }
    }
} finally { Pop-Location }

@"

jarvis 를 설치했습니다: http://127.0.0.1:9300/api/health
남은 일(계정·네트워크라 직접 합니다):
- core 설정의 devices 에 이 기기를 넣은 뒤 core 를 재시작합니다
- 하네스(claude·codex 등)에 로그인합니다
"@
