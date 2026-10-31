# Remove-Item 셰임 — 삭제를 휴지통 이동으로 바꾼다.
#
# 훅이 명령 문자열을 재작성하는 대신 이 모듈을 Import-Module 하는 한 줄만 앞에 붙인다.
# PowerShell 의 명령 우선순위는 별칭 > 함수 > cmdlet 이라, 같은 이름의 함수가 cmdlet 을
# 가린다. rm / del / erase / ri / rd / rmdir 은 전부 Remove-Item 을 가리키는 별칭이므로
# 이 함수 하나로 모두 덮인다. 인자 파싱은 PowerShell 이 하므로 훅이 따옴표를 다시 씌우다
# 확장을 깨뜨리는 사고가 원리적으로 일어나지 않는다.

Set-StrictMode -Off

$script:RealRemoveItem = Microsoft.PowerShell.Core\Get-Command `
    -Name Microsoft.PowerShell.Management\Remove-Item -CommandType Cmdlet

function Resolve-ClaudeRealPath {
    # 경로를 뿌리부터 한 단계씩 따라가며 심볼릭 링크·정션을 풀어 실제 경로를 돌려준다.
    # LinkType/Target 은 Windows PowerShell 5.1 에도 있다. Depth 는 순환 링크 방어.
    param([string]$Path, [int]$Depth = 0)
    $full = [System.IO.Path]::GetFullPath($Path)
    $acc = [System.IO.Path]::GetPathRoot($full)
    foreach ($part in $full.Substring($acc.Length).Split([char[]]'\/', [StringSplitOptions]::RemoveEmptyEntries)) {
        $acc = [System.IO.Path]::Combine($acc, $part)
        $i = Get-Item -LiteralPath $acc -Force -ErrorAction Ignore
        if ($Depth -lt 40 -and $i -and $i.LinkType -in 'SymbolicLink', 'Junction') {
            $target = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($acc), @($i.Target)[0])
            $acc = Resolve-ClaudeRealPath $target ($Depth + 1)
        }
    }
    return $acc.TrimEnd('\', '/')
}

function Test-ClaudeRootPath {
    param([string]$FullPath)
    $p = $FullPath.TrimEnd('\', '/')
    if ([string]::IsNullOrEmpty($p)) { return $true }
    $home_ = $env:USERPROFILE
    if (-not $home_) { $home_ = $HOME }
    if ($home_ -and $p -eq $home_.TrimEnd('\', '/')) { return $true }
    # C:\ , D:\ 같은 드라이브 루트와 POSIX 루트.
    if ($p -match '^[A-Za-z]:$') { return $true }
    if ($p -eq '') { return $true }
    # 링크를 거쳐 같은 홈에 닿는 경로(예: C:\Documents and Settings\me). 대상 자신은 풀지 않는다 —
    # 링크를 지우면 링크만 사라지므로, 조상 경로만 실제 경로로 바꿔 홈의 실제 경로와 비교한다.
    # 해석은 항목마다 Get-Item 을 여러 번 부르므로, 홈의 실제 경로는 Remove-Item 호출(end{})마다 한 번만
    # 풀고, 이름이 그 끝 이름과 다른 항목은 실제 경로가 홈일 수 없으니 조상을 풀지 않는다.
    $parent = [System.IO.Path]::GetDirectoryName($p)
    if ($home_ -and $parent) {
        if (-not $script:RealHome) { $script:RealHome = Resolve-ClaudeRealPath $home_ }
        $leaf = [System.IO.Path]::GetFileName($p)
        if ($leaf -eq [System.IO.Path]::GetFileName($script:RealHome) -and
            [System.IO.Path]::Combine((Resolve-ClaudeRealPath $parent), $leaf) -eq $script:RealHome) { return $true }
    }
    return $false
}

function Send-ClaudeTrash {
    <#
      .SYNOPSIS
      경로들을 휴지통으로 옮긴다. 옮길 수단이 없으면 예외를 던져 호출자가 원본 cmdlet 으로
      넘기게 한다 — 삭제 자체를 막아버리는 쪽이 더 해롭기 때문이다.
    #>
    param([Parameter(Mandatory = $true)][string[]]$Paths)

    if ($env:OS -eq 'Windows_NT') {
        Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
        foreach ($p in $Paths) {
            if (Test-Path -LiteralPath $p -PathType Container) {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
                    $p, 'OnlyErrorDialogs', 'SendToRecycleBin')
            }
            else {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
                    $p, 'OnlyErrorDialogs', 'SendToRecycleBin')
            }
        }
        return
    }

    # SilentlyContinue 는 못 찾은 기록을 $Error 에 남겨, 삭제가 성공해도 원본 cmdlet 과 달라진다(macOS).
    $trash = Microsoft.PowerShell.Core\Get-Command trash-put -ErrorAction Ignore
    if ($trash) {
        & $trash.Source -- @Paths
    }
    elseif ($IsMacOS -and ($trash = Microsoft.PowerShell.Core\Get-Command trash -ErrorAction Ignore)) {
        # macOS 14.4+ 기본 /usr/bin/trash (shims/rm 과 같은 우선순위). `--` 지원이 문서화돼
        # 있지 않지만 $Paths 는 Get-Item 의 FullName(절대경로)이라 대시로 시작하지 않는다.
        & $trash.Source @Paths
    }
    else { throw 'No trash tool found.' }
    if ($LASTEXITCODE -ne 0) { throw "$($trash.Name) failed with exit code $LASTEXITCODE." }
}

function Remove-Item {
    [CmdletBinding(DefaultParameterSetName = 'Path', SupportsShouldProcess = $true,
        ConfirmImpact = 'Medium')]
    param(
        [Parameter(ParameterSetName = 'Path', Mandatory = $true, Position = 0,
            ValueFromPipeline = $true, ValueFromPipelineByPropertyName = $true)]
        [string[]]$Path,

        [Parameter(ParameterSetName = 'LiteralPath', Mandatory = $true,
            ValueFromPipelineByPropertyName = $true)]
        [Alias('PSPath', 'LP')]
        [string[]]$LiteralPath,

        [string]$Filter,
        [string[]]$Include,
        [string[]]$Exclude,
        [switch]$Recurse,
        [switch]$Force,
        [System.Management.Automation.PSCredential]$Credential,
        [switch]$Stream
    )

    begin { $collected = New-Object System.Collections.Generic.List[string] }

    process {
        if ($PSCmdlet.ParameterSetName -eq 'LiteralPath') { $collected.AddRange([string[]]$LiteralPath) }
        else { $collected.AddRange([string[]]$Path) }
    }

    end {
        $literal = $PSCmdlet.ParameterSetName -eq 'LiteralPath'
        $script:RealHome = $null
        # end{} 의 $PSBoundParameters 에는 마지막 파이프라인 항목만 남는다. 원본 cmdlet 으로
        # 넘길 때 모은 경로 전부가 가도록 바꿔 둔다.
        $PSBoundParameters[$(if ($literal) { 'LiteralPath' } else { 'Path' })] = $collected.ToArray()

        $items = @()
        foreach ($p in $collected) {
            $got = if ($literal) {
                Get-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
            }
            else {
                Get-Item -Path $p -Force -ErrorAction SilentlyContinue
            }
            if ($got) { $items += $got }
        }

        # 루트/홈 거부는 위임 여부를 정하기 전에 한다 — 위임 경로도 원본 cmdlet 이 실제로 지운다.
        foreach ($i in $items) {
            if ($i.PSProvider.Name -eq 'FileSystem' -and (Test-ClaudeRootPath $i.FullName)) {
                Write-Error "Remove-Item(shim): refusing to delete the entire root/home directory ($($i.FullName))."
                return
            }
        }

        # 원본 cmdlet 에게 그대로 넘겨야 하는 경우들. 셰임이 흉내 내면 의미가 달라진다.
        #  - -WhatIf/-Confirm: 확인 흐름은 cmdlet 이 내는 그대로여야 한다.
        #  - -Stream/-Credential/-Filter/-Include/-Exclude: 휴지통으로는 표현할 수 없다.
        #  - 아무것도 못 찾았다(오탈자·없는 파일) → 오류 문구까지 cmdlet 것을 그대로 쓴다.
        #  - 파일시스템이 아닌 공급자(Env:, HKLM:, Variable:)도 휴지통 대상이 아니다.
        if ($WhatIfPreference -or $PSBoundParameters.ContainsKey('Confirm') -or
            $Stream -or $Credential -or $Filter -or $Include -or $Exclude -or
            -not $items -or ($items | Where-Object { $_.PSProvider.Name -ne 'FileSystem' })) {
            & $script:RealRemoveItem @PSBoundParameters
            return
        }

        $targets = @()
        foreach ($i in $items) {
            if ($targets -notcontains $i.FullName) { $targets += $i.FullName }
        }

        try {
            Send-ClaudeTrash -Paths $targets
        }
        catch {
            Write-Warning "Remove-Item(shim): could not move to the trash; deleting for real - $($_.Exception.Message)"
            & $script:RealRemoveItem @PSBoundParameters
        }
    }
}

Export-ModuleMember -Function Remove-Item
