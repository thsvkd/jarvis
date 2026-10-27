# Windows 게이트웨이 설치(관리자 PowerShell, 저장소 안에서): powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
#   - 설정: %USERPROFILE%\.config\jarvis\config.toml (없을 때만 deploy\windows.toml 을 복사)
#   - 방화벽: TCP 9300 을 Tailscale 인터페이스의 tailnet(100.64.0.0/10)에서만 받는다(공용 와이파이의 CGNAT 같은 대역은 막는다)
#   - 예약 작업 'jarvis': 이 사용자 로그온 때와 5분마다 pythonw 로 deploy\jarvis-win.pyw 를 띄운다(이미 돌면 IgnoreNew 로 건너뛴다.
#     작업 스케줄러의 '실패 시 다시 시작'은 프로세스가 죽은 경우를 다시 띄우지 않는다). Orca 데스크톱 런타임도 로그온 세션에서 돈다.
#     SSH 세션에서 띄운 프로세스는 세션이 끝나면 함께 죽으므로 예약 작업으로 돌린다. 로그: %LOCALAPPDATA%\jarvis\jarvis.log
# 다시 실행해도 된다(작업을 갈아 끼우고 재시작). 코드 갱신 뒤 재시작만: Stop-ScheduledTask jarvis; Start-ScheduledTask jarvis
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (-not $Python) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -First 1
    if (-not $cmd) { throw "python 3.11+ 가 필요하다(-Python 으로 경로 지정)" }
    $Python = $cmd.Source
}
$pyw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path $pyw)) { throw "pythonw.exe 가 없다: $pyw" }
& $Python -c "import sys; assert sys.version_info >= (3, 11)"
if ($LASTEXITCODE -ne 0) { throw "python 3.11+ 가 필요하다: $Python" }
# stderr 를 내지 않게 확인한다(PowerShell 5.1 은 Stop 일 때 네이티브 stderr 를 종료 오류로 바꾼다)
$has = { (& $Python -c "import importlib.util; print(importlib.util.find_spec('psutil') is not None)") -eq "True" }
# pip 의 stderr 경고(PATH 안내 등)가 Stop 에서 종료 오류가 되지 않게 이 블록만 Continue
if (-not (& $has)) { & { $ErrorActionPreference = "Continue"; & $Python -m pip install --user -q psutil } }
if (-not (& $has)) { throw "psutil 설치 실패" }
$tray = { (& $Python -c "import importlib.util as u; print(all(u.find_spec(m) for m in ('pystray', 'PIL')))") -eq "True" }
if (-not (& $tray)) { & { $ErrorActionPreference = "Continue"; & $Python -m pip install --user -q pystray pillow } }  # 선택: 알림 영역 아이콘(jarvis/tray.py), 없으면 아이콘 없이 돈다

$cfg = Join-Path $env:USERPROFILE ".config\jarvis\config.toml"
if (-not (Test-Path $cfg)) {
    New-Item -ItemType Directory (Split-Path -Parent $cfg) -Force | Out-Null
    Copy-Item (Join-Path $repo "deploy\windows.toml") $cfg
}

# pythonw 가 처음 포트를 열 때 Defender 팝업이 거부·무시되면 이 프로그램의 인바운드 '차단' 규칙이 생기고, 차단은 허용보다 앞선다.
# 그 차단 규칙을 지우고 프로그램·포트·대역·인터페이스를 모두 지정한 허용 규칙을 둔다(프로그램 규칙이 있으면 팝업도 다시 안 뜬다).
Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $pyw } | Get-NetFirewallRule |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Block" } | Remove-NetFirewallRule
if (-not (Get-NetAdapter -Name Tailscale -ErrorAction SilentlyContinue)) { throw "Tailscale 어댑터가 없다(방화벽 규칙을 tailnet 에만 열 수 없다)" }
Remove-NetFirewallRule -DisplayName "jarvis-9300" -ErrorAction SilentlyContinue  # 다시 실행하면 범위를 새로 맞춘다
New-NetFirewallRule -DisplayName "jarvis-9300" -Direction Inbound -Program $pyw -Protocol TCP -LocalPort 9300 `
    -RemoteAddress 100.64.0.0/10 -InterfaceAlias Tailscale -Action Allow | Out-Null

$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$repo\deploy\jarvis-win.pyw`"" -WorkingDirectory $repo
$trigger = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5))  # 죽으면 5분 안에 다시
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Stop-ScheduledTask -TaskName jarvis -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName jarvis -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName jarvis
"jarvis: $pyw $repo\deploy\jarvis-win.pyw (config $cfg)"
