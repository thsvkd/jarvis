"task RunLevel: " + (Get-ScheduledTask jarvis).Principal.RunLevel
$rt = Get-Content "$env:APPDATA\orca\orca-runtime.json" | ConvertFrom-Json
"orca pid: " + $rt.pid
$p = Get-Process -Id $rt.pid -ErrorAction SilentlyContinue
"orca start: " + $p.StartTime + "  path: " + $p.Path
$sig = @'
using System; using System.Runtime.InteropServices;
public class E { [DllImport("advapi32.dll", SetLastError=true)] public static extern bool OpenProcessToken(IntPtr h, uint a, out IntPtr t);
[DllImport("advapi32.dll", SetLastError=true)] public static extern bool GetTokenInformation(IntPtr t, int c, out int v, int l, out int r);
[DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint a, bool i, int pid);
public static string El(int pid){ IntPtr h=OpenProcess(0x1000,false,pid); if(h==IntPtr.Zero) return "open-failed"; IntPtr t; if(!OpenProcessToken(h,8,out t)) return "token-failed"; int v,r; GetTokenInformation(t,20,out v,4,out r); return v!=0?"elevated":"not-elevated"; } }
'@
Add-Type $sig
"orca elevation: " + [E]::El([int]$rt.pid)
Get-Process pythonw -ErrorAction SilentlyContinue | ForEach-Object { "jarvis pythonw " + $_.Id + ": " + [E]::El($_.Id) }
"this ssh shell: " + [E]::El($PID)
