<#
.SYNOPSIS
    fluent-korean 출력 스타일 설치 워커 (Windows).
.DESCRIPTION
    scripts/setup.ps1 이 -OutputStyle 로 호출한다. 플러그인은 claude CLI 로 설치하고,
    ~/.claude/settings.json 의 outputStyle 지정은 res/output_style/apply.py 가 한다.
.USAGE
    powershell -ExecutionPolicy Bypass -File setup_output_style.ps1
    powershell -ExecutionPolicy Bypass -File setup_output_style.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File setup_output_style.ps1 -Uninstall   # outputStyle 만 되돌린다
#>

param(
    [switch]$DryRun,
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

. (Join-Path $PSScriptRoot "lib\SetupCommon.ps1")

$MarketplaceRepo = "snflkd/fluent-korean"
$Plugin = "fluent-korean@fluent-korean"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$apply = Join-Path $RepoRoot "res\output_style\apply.py"

$py = Resolve-PythonCommand
if (-not $py) {
    Write-Host "❌ Python not found." -ForegroundColor Red
    Write-PythonInstallHint
    exit 1
}

$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
$pyArgs = @("-X", "utf8", $apply, "--home", $homeDir)
if ($DryRun) { $pyArgs += "--dry-run" }

if ($Uninstall) {
    Write-Host "🧹 Reverting output style"
    & $py.Exe @($py.Args) @pyArgs --uninstall
    exit $LASTEXITCODE
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "❌ claude CLI not found. Install Claude Code first." -ForegroundColor Red
    exit 1
}

function Invoke-Step([string[]]$CliArgs) {
    if ($DryRun) {
        Write-Host "  • (dry-run) claude $($CliArgs -join ' ')"
        return
    }
    & claude @CliArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Windows PowerShell 5.1 은 Stop 아래에서 네이티브 명령의 stderr 리디렉션을 종료 오류로 승격한다.
# 목록 조회는 실패해도 '미설치' 로 보고 진행하면 되므로 이 구간만 Continue 로 읽는다.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$plugins = & claude plugin list 2>$null | Out-String
$marketplaces = & claude plugin marketplace list 2>$null | Out-String
$ErrorActionPreference = $prevEap

Write-Host "🔧 fluent-korean plugin"
if ($plugins -match [regex]::Escape($Plugin)) {
    Write-Host "  • Already installed: $Plugin"
} else {
    if ($marketplaces -notmatch [regex]::Escape($MarketplaceRepo)) {
        Invoke-Step @("plugin", "marketplace", "add", $MarketplaceRepo)
    }
    Invoke-Step @("plugin", "install", $Plugin)
}

Write-Host "🔧 Output style"
& $py.Exe @($py.Args) @pyArgs
exit $LASTEXITCODE
