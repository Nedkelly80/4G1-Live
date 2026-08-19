# 4G1 Live AI reliable launcher.
# Called by "4G1 Live.bat". Focuses an existing window, or starts the app and
# verifies it actually came up (pythonw is silent on crash - this catches that).

$ErrorActionPreference = "Continue"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppPy = Join-Path $AppDir "app.py"
$LogDir = Join-Path $env:LOCALAPPDATA "4G1 Live AI\Logs"
$BootLog = Join-Path $LogDir "boot.log"
$CrashLog = Join-Path $LogDir "launch-crash.log"
$PyW = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313-32\pythonw.exe"
$Py = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313-32\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Boot([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $BootLog -Value $line -Encoding UTF8
}

function Show-Msg([string]$title, [string]$text) {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        [System.Windows.Forms.MessageBox]::Show($text, $title) | Out-Null
    } catch {
        Write-Host $title
        Write-Host $text
        Start-Sleep 4
    }
}

function Get-AppProcesses {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like '*4G1-Live-AI*'
            ) -and ($_.CommandLine -like '*app.py*')
        }
}

function Focus-AppWindow {
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.MainWindowTitle -like "4G1 Live AI*"
    }
    if (-not $procs) { return $false }
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class GrokFocus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
}
"@ -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.MainWindowHandle -eq [IntPtr]::Zero) { continue }
        try {
            [void][GrokFocus]::ShowWindow($p.MainWindowHandle, 9)
            [void][GrokFocus]::BringWindowToTop($p.MainWindowHandle)
            [void][GrokFocus]::SetForegroundWindow($p.MainWindowHandle)
            Write-Boot "focused existing PID $($p.Id)"
            return $true
        } catch {}
    }
    return $false
}

# --- start ---
Set-Content -Path $BootLog -Value "" -Encoding UTF8
Write-Boot "launch.ps1 start cwd=$AppDir"

if (-not (Test-Path $AppPy)) {
    Write-Boot "MISSING app.py"
    Show-Msg "4G1 Live AI" "Missing app.py in:`n$AppDir"
    exit 1
}

# Already open? Just bring it forward.
if (Focus-AppWindow) {
    Write-Boot "handed off to existing window"
    exit 0
}

# Kill zombie 4G1 AI processes that have no window (stuck launches)
Get-AppProcesses | ForEach-Object {
    $gp = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    if ($gp -and [string]::IsNullOrEmpty($gp.MainWindowTitle)) {
        Write-Boot "killing zombie PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $PyW) -and -not (Test-Path $Py)) {
    Write-Boot "No 32-bit Python 3.13"
    Show-Msg "4G1 Live AI" "32-bit Python 3.13 not found.`nInstall Python 3.13 x86 from python.org`n`nExpected:`n$PyW"
    exit 1
}

$python = if (Test-Path $PyW) { $PyW } else { $Py }
Write-Boot "starting $python $AppPy"
try {
    $proc = Start-Process -FilePath $python -ArgumentList "`"$AppPy`"" `
        -WorkingDirectory $AppDir -PassThru -WindowStyle Normal
} catch {
    $err = $_.Exception.Message
    Write-Boot "Start-Process failed: $err"
    Add-Content -Path $CrashLog -Value "$(Get-Date) Start-Process failed: $err"
    Show-Msg "4G1 Live AI" "Failed to start:`n$err"
    exit 1
}

# Wait for window (up to ~8s) - pythonw is silent if import crashes
$ok = $false
for ($i = 0; $i -lt 16; $i++) {
    Start-Sleep -Milliseconds 500
    if (Focus-AppWindow) { $ok = $true; break }
    if ($proc.HasExited) {
        Write-Boot "process exited early code=$($proc.ExitCode)"
        break
    }
}

if ($ok) {
    Write-Boot "UI up PID=$($proc.Id)"
    exit 0
}

# Failed - retry with visible console so the error is not invisible
Write-Boot "no window after wait - retry with console python"
if (-not (Test-Path $Py)) {
    $detail = "App did not open a window.`nSee log:`n$LogDir\4g1-live.log"
    Show-Msg "4G1 Live AI failed to open" $detail
    exit 1
}

Show-Msg "4G1 Live AI" "First start did not show a window.`nOpening with a visible console so you can see any error..."
$con = Start-Process -FilePath $Py -ArgumentList "`"$AppPy`"" `
    -WorkingDirectory $AppDir -PassThru -Wait
Write-Boot "console exit=$($con.ExitCode)"
if ($con.ExitCode -ne 0) {
    $code = $con.ExitCode
    Show-Msg "4G1 Live AI failed" "Exit code $code`nLogs:`n$LogDir"
    exit $code
}
exit 0
