"""Change only the current user's Orca scheduled task through Windows consent."""
import base64
import ctypes
import json
from functools import lru_cache
from pathlib import Path
import subprocess


# Job 이 없던 예전 러너는 작업을 멈춰도 자기만 끝나고 서버 트리를 남긴다. 멈추기 전에 이 러너와
# 그 자손(부모보다 늦게 생긴 프로세스만: PID 재사용 방지)을 기록해 두고 그 트리만 끝낸다.
# 승격 없이도 돌 수 있게 따로 둔다. 테스트가 같은 함수를 실제 프로세스 트리에 대고 실행한다.
TREE_FUNCTIONS = r'''
function Get-OrcaRunnerTree([string]$runner) {
    $all = @(Get-CimInstance Win32_Process)
    $tree = [Collections.Generic.List[object]]::new()
    $all | Where-Object {
        $_.Name -like 'python*.exe' -and $_.CommandLine -and $_.CommandLine.IndexOf($runner, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $_.ProcessId -ne $PID
    } | ForEach-Object { $tree.Add($_) }
    for ($i = 0; $i -lt $tree.Count; $i++) {
        $parent = $tree[$i]
        $all | Where-Object {
            $_.ParentProcessId -eq $parent.ProcessId -and $_.CreationDate -ge $parent.CreationDate
        } | ForEach-Object { $tree.Add($_) }
    }
    return ,$tree
}

function Stop-OrcaRunnerTree($tree) {
    foreach ($item in $tree) {
        $now = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $item.ProcessId) -ErrorAction SilentlyContinue
        if ($now -and $now.CreationDate -eq $item.CreationDate) {
            Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}
'''

WORKER = TREE_FUNCTIONS + r'''
$ErrorActionPreference = 'Stop'
$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($identity.User.Value -ne $data.callerSid) {
        throw 'The administrator account must match the original Windows user.'
    }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $reference = $scheduler.NewTask(0)
    $reference.XmlText = [regex]::Replace([IO.File]::ReadAllText($data.service), '<\?xml[^?]*\?>', '')
    if ($reference.Actions.Count -ne 1 -or $reference.Actions.Item(1).Type -ne 0) {
        throw 'The Orca service action changed; preserving existing tasks.'
    }
    $expected = $reference.Actions.Item(1)
    if (-not $expected.Arguments.Contains($data.runner)) { throw 'Invalid Orca runner action.' }
    if ($data.operation -eq 'create') {
        $definition = $scheduler.NewTask(0)
        $xml = [IO.File]::ReadAllText($data.service)
        $definition.XmlText = [regex]::Replace($xml, '<\?xml[^?]*\?>', '')
        if ($definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Type -ne 0 -or
            $definition.Actions.Item(1).Path -ne $expected.Path -or
            $definition.Actions.Item(1).Arguments -cne $expected.Arguments -or
            $definition.Actions.Item(1).WorkingDirectory -ne $expected.WorkingDirectory) {
            throw 'The Orca service action changed; preserving existing tasks.'
        }
        $definition.Principal.UserId = $data.callerSid
        $definition.Principal.LogonType = 2
        $definition.Principal.RunLevel = 0
        $null = $folder.RegisterTaskDefinition($data.task, $definition, 2, $data.callerSid, $null, 2)
    } elseif ($data.operation -in @('delete', 'end')) {
        try { $registered = $folder.GetTask($data.task); $definition = $registered.Definition } catch {
            if ($_.Exception.HResult -eq -2147024894) { exit 0 }
            throw
        }
        if ($definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Type -ne 0 -or
            $definition.Actions.Item(1).Path -ne $expected.Path -or
            $definition.Actions.Item(1).Arguments -cne $expected.Arguments -or
            $definition.Actions.Item(1).WorkingDirectory -ne $expected.WorkingDirectory -or
            $definition.Principal.RunLevel -ne 0 -or $definition.Principal.LogonType -ne 2) {
            throw 'The registered Orca service action changed; preserving it.'
        }
        $account = [Security.Principal.NTAccount]::new($definition.Principal.UserId)
        $sid = if ($definition.Principal.UserId.StartsWith('S-1-')) {
            $definition.Principal.UserId
        } else { $account.Translate([Security.Principal.SecurityIdentifier]).Value }
        if ($sid -ne $data.callerSid) { throw 'The registered Orca task belongs to another user.' }
        if ($data.operation -eq 'end') {
            $tree = Get-OrcaRunnerTree $data.runner
            try { $registered.Stop(0) } catch { }
            Stop-OrcaRunnerTree $tree
        } else { $folder.DeleteTask($data.task, 0) }
    } else {
        throw 'Unsupported Orca task operation.'
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit $_.Exception.HResult
}
'''


def powershell_path():
    system = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(system, len(system)):
        raise OSError('Could not locate Windows PowerShell')
    return Path(system.value) / 'WindowsPowerShell/v1.0/powershell.exe'


@lru_cache(maxsize=1)
def current_user_sid():
    result = subprocess.run(
        [str(powershell_path()), '-NoProfile', '-NonInteractive', '-Command',
         '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
        check=True, capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    value = result.stdout.strip()
    if not value.startswith('S-1-') or any(char not in 'S-0123456789' for char in value):
        raise RuntimeError('Could not identify the Windows user')
    return value


def protect_file(path):
    path.chmod(0o600)
    system = powershell_path().parents[2]
    subprocess.run(
        [str(system / 'icacls.exe'), str(path), '/grant:r', '*' + current_user_sid() + ':(F)'],
        check=True, capture_output=True, timeout=30,
    )


def contain_process_tree():
    """러너를 kill-on-close Job 에 넣는다. 러너가 어떻게 끝나든 남은 자손도 함께 끝난다.

    schtasks /End 는 작업이 띄운 러너만 TerminateProcess 하고 자손은 그대로 둔다.
    Orca 는 터미널 호스트를 breakaway 없이 detached 로 띄우므로 이 Job 에 함께 묶인다.
    """
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', ctypes.c_uint64 * 6),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if (not job
            or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
        raise OSError(ctypes.get_last_error(), 'Could not tie the Orca server to its runner')
    # 핸들은 상속되지 않고 닫지도 않는다. 러너가 끝날 때 커널이 닫으며 Job 을 끝낸다.
    return job


def change_task(plan, operation):
    if operation not in ('create', 'delete', 'end'):
        raise ValueError('Unsupported Orca task operation')
    powershell = powershell_path()
    template = base64.b64encode(WORKER.encode('utf-8')).decode('ascii')
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$data | Add-Member -NotePropertyName callerSid -NotePropertyValue $identity.User.Value -Force
$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($data | ConvertTo-Json -Compress)))
$worker = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__TEMPLATE__')).Replace('__PAYLOAD__', $payload)
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    & ([scriptblock]::Create($worker))
} else {
    try {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
        $process = Start-Process -FilePath $data.powershell -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded)
        exit $process.ExitCode
    } catch {
        if ($_.Exception.NativeErrorCode -eq 1223) { exit 1223 }
        [Console]::Error.WriteLine($_.Exception.Message)
        exit 1
    }
}
'''.replace('__TEMPLATE__', template)
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
        input=json.dumps({'task': plan['task'], 'service': str(plan['service']),
                          'runner': str(plan['runner']), 'operation': operation,
                          'powershell': str(powershell)}),
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
    )
    if result.returncode == 1223:
        result.stderr = 'Administrator consent was cancelled; the setup checkpoint was preserved'
    elif result.returncode and not result.stderr.strip():
        result.stderr = 'Windows task {} failed (0x{:08x})'.format(operation, result.returncode & 0xffffffff)
    return result
