# Install / repair 4G1 Live AI shortcuts (Desktop + Start Menu).
# Removes confusing lookalikes that point at the original non-AI app.
# Run:  powershell -ExecutionPolicy Bypass -File .\install-start-menu.ps1

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppName = "4G1 Live AI"
$BatPath = Join-Path $AppDir "4G1 Live.bat"
$DebugBat = Join-Path $AppDir "OPEN-WITH-CONSOLE.bat"
$LaunchPs1 = Join-Path $AppDir "launch.ps1"
$IcoSrc = Join-Path $AppDir "4g1.ico"
$LocalDir = Join-Path $env:LOCALAPPDATA $AppName
$IcoPath = Join-Path $LocalDir "4g1.ico"

if (-not (Test-Path $BatPath)) { throw "Missing launcher: $BatPath" }
if (-not (Test-Path $LaunchPs1)) { throw "Missing launch.ps1: $LaunchPs1" }
if (-not (Test-Path $IcoSrc)) { throw "Missing icon: $IcoSrc" }
if (-not (Test-Path $DebugBat)) { throw "Missing debug bat: $DebugBat" }

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
Copy-Item $IcoSrc $IcoPath -Force

$Programs = [Environment]::GetFolderPath("Programs")
$StartFolder = Join-Path $Programs $AppName
New-Item -ItemType Directory -Force -Path $StartFolder | Out-Null

$Desktop = [Environment]::GetFolderPath("Desktop")
$OneDesktop = Join-Path $env:USERPROFILE "OneDrive\Desktop"
$DesktopTargets = @()
foreach ($d in @($Desktop, $OneDesktop)) {
    if ((Test-Path $d) -and ($DesktopTargets -notcontains $d)) {
        $DesktopTargets += $d
    }
}

$Wsh = New-Object -ComObject WScript.Shell

function New-AppShortcut([string]$Path, [string]$Target, [string]$WorkDir, [string]$Desc) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    if (Test-Path $Path) { Remove-Item $Path -Force }
    $sc = $Wsh.CreateShortcut($Path)
    $sc.TargetPath = $Target
    $sc.Arguments = ""
    $sc.WorkingDirectory = $WorkDir
    $sc.IconLocation = "$IcoPath,0"
    $sc.Description = $Desc
    $sc.WindowStyle = 1
    $sc.Save()
    Write-Host "  + $Path"
}

function Remove-IfExists([string]$Path) {
    if (Test-Path $Path) {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
        Write-Host "  - removed $Path"
    }
}

Write-Host ""
Write-Host "Cleaning confusing 4G1 shortcuts..."

# Old crash shortcut name - replaced with clearer debug shortcut below
foreach ($d in $DesktopTargets) {
    Remove-IfExists (Join-Path $d "4G1 Live AI (if crash use this).lnk")
}

# Desktop / Start "4G1 Live.lnk" that points at the SOURCE non-AI fork
# (pythonw + 4G1-Live\app.py) is easy to click by mistake.
$confusing = @()
$confusing += (Join-Path $Programs "4G1 Live.lnk")
foreach ($d in $DesktopTargets) {
    $confusing += (Join-Path $d "4G1 Live.lnk")
}
foreach ($p in $confusing) {
    if (-not (Test-Path $p)) { continue }
    try {
        $sc = $Wsh.CreateShortcut($p)
        $t = "$($sc.TargetPath) $($sc.Arguments) $($sc.WorkingDirectory)"
        # Only rename if it points at Desktop 4G1-Live source, NOT Program Files installer
        if ($t -like '*4G1-Live*app.py*' -or ($t -like '*4G1-Live*' -and $t -notlike '*Program Files*')) {
            $leaf = "4G1 Live (original non-AI).lnk"
            $renamed = Join-Path (Split-Path $p -Parent) $leaf
            if (Test-Path $renamed) { Remove-Item $renamed -Force -ErrorAction SilentlyContinue }
            Rename-Item -Path $p -NewName $leaf -Force
            Write-Host "  ~ renamed to $leaf"
        }
    } catch {
        Write-Host "  ! could not inspect $p : $_"
    }
}

Write-Host ""
Write-Host "Installing 4G1 Live AI shortcuts..."
New-AppShortcut (Join-Path $StartFolder "$AppName.lnk") $BatPath $AppDir "Mitsubishi MUT-II live ECU data + AI assistant"
New-AppShortcut (Join-Path $Programs "$AppName.lnk") $BatPath $AppDir "Mitsubishi MUT-II live ECU data + AI assistant"
New-AppShortcut (Join-Path $StartFolder "$AppName - Debug Console.lnk") $DebugBat $AppDir "Open 4G1 Live AI with a visible console for errors"

foreach ($d in $DesktopTargets) {
    New-AppShortcut (Join-Path $d "$AppName.lnk") $BatPath $AppDir "Mitsubishi MUT-II live ECU data + AI assistant"
    New-AppShortcut (Join-Path $d "$AppName - Debug Console.lnk") $DebugBat $AppDir "Open with visible console if launch fails"
}

Write-Host ""
Write-Host "Done."
Write-Host "  Desktop / Start:  $AppName"
Write-Host "  If it fails:      $AppName - Debug Console"
Write-Host ""
Write-Host "Use '4G1 Live AI' for this build."
Write-Host "Plain '4G1 Live' is the original/commercial app, not the AI fork."
Write-Host ""
