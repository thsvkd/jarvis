<#
.SYNOPSIS
    scripts/setup_*.ps1, Update-Ai.ps1 이 dot-source 하는 공용 함수 모음.
.DESCRIPTION
    Python 실행 명령 찾기와, 없을 때 설치 명령 안내를 담당한다. 훅 설치·병합·셀프테스트·제거
    함수는 훅과 함께 jarvis 훅 모듈로 옮겨 갔다.
#>

function Resolve-PythonCommand {
    # 호출부가 이미 설정했더라도, 이 함수가 단독으로(dot-source 후 직접) 쓰이는 경우까지 방어한다
    # (S1: PS 7.4+ 에서 네이티브 명령의 0이 아닌 종료 코드가 종료 오류로 승격되는 것을 방지 —
    # 아래 프로브는 $LASTEXITCODE 로 성패를 판정하도록 설계돼 있다).
    $PSNativeCommandUseErrorActionPreference = $false
    $candidates = @(
        @{ Exe = "python";  Args = @() },
        @{ Exe = "python3"; Args = @() },
        @{ Exe = "py";      Args = @("-3") },
        @{ Exe = "py";      Args = @() }   # 런처가 아닌 py.exe (= CPython) 는 -3 을 모른다
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Exe -ErrorAction SilentlyContinue
        if ($cmd) {
            $ok = $false
            try {
                & $c.Exe @($c.Args) -c "print(1)" *> $null
                $ok = ($LASTEXITCODE -eq 0)
            } catch {
                $ok = $false
            }
            if ($ok) {
                return @{ Exe = $c.Exe; Args = $c.Args }
            }
        }
    }
    return $null
}

# Resolve-PythonCommand 가 실패했을 때 이 환경에서 Python 3 을 설치하는 명령을 안내한다.
# 설치는 하지 않는다 — 런타임 설치는 되돌리기 어렵고 uninstall 대칭 밖이라 사용자가 직접 한다.
function Write-PythonInstallHint {
    $onMac = (Test-Path variable:IsMacOS) -and $IsMacOS
    $onLinux = (Test-Path variable:IsLinux) -and $IsLinux
    Write-Host "   How to install Python 3:" -ForegroundColor Yellow
    if ($onMac) {
        Write-Host "     brew install python"
    } elseif ($onLinux) {
        Write-Host "     sudo apt-get install -y python3   # Debian/Ubuntu"
        Write-Host "     sudo dnf install -y python3       # Fedora/RHEL"
    } else {
        Write-Host "     winget install -e --id Python.Python.3.14"
        Write-Host "   Without winget, install it from https://www.python.org/downloads/windows/."
        Write-Host "   After installing, run again in a new terminal. If the Microsoft Store still opens,"
        Write-Host "   turn off python.exe / python3.exe under Settings > Apps > Advanced app settings > App execution aliases."
    }
}

