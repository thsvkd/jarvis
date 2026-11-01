<#
.SYNOPSIS
    Claude Code mod 설치 워커 (Windows).
.DESCRIPTION
    scripts/setup.ps1 이 -Mods 로 호출한다. 번들 mod(res/mods/*)를 ~/.claude/mods 에 복사하고
    ~/.claude/settings.json 의 env.CLAUDE_CODE_PLUGIN_DIRS 에 등록하는 일은 res/mods/apply.py 가 한다.
.USAGE
    powershell -ExecutionPolicy Bypass -File setup_mods.ps1
    powershell -ExecutionPolicy Bypass -File setup_mods.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File setup_mods.ps1 -Uninstall
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

$RepoRoot = Split-Path -Parent $PSScriptRoot
$apply = Join-Path $RepoRoot "res\mods\apply.py"

$py = Resolve-PythonCommand
if (-not $py) {
    Write-Host "❌ Python not found." -ForegroundColor Red
    Write-PythonInstallHint
    exit 1
}

$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
$pyArgs = @("-X", "utf8", $apply, "--home", $homeDir)
if ($DryRun) { $pyArgs += "--dry-run" }
if ($Uninstall) { $pyArgs += "--uninstall" }

Write-Host "🔧 Claude Code mod"
& $py.Exe @($py.Args) @pyArgs
exit $LASTEXITCODE
