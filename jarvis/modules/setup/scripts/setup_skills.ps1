<#
.SYNOPSIS
    Bundled Claude Code skills worker (Windows).
#>
param([switch]$DryRun, [switch]$Uninstall)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot "lib\SetupCommon.ps1")
$py = Resolve-PythonCommand
if (-not $py) { Write-Host "Python 3 is required." -ForegroundColor Red; Write-PythonInstallHint; exit 1 }
$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
$worker = Join-Path (Split-Path -Parent $PSScriptRoot) "res\skills\apply.py"
$pyArgs = @("-X", "utf8", $worker, "--home", $homeDir)
if ($DryRun) { $pyArgs += "--dry-run" }
if ($Uninstall) { $pyArgs += "--uninstall"; Write-Host "🧹 Removing bundled skills" } else { Write-Host "🔧 Bundled skills" }
& $py.Exe @($py.Args) @pyArgs
exit $LASTEXITCODE
