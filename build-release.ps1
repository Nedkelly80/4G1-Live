$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Toolchain discovery. Nothing here may be tied to one machine or one user
# profile — a release build must work on any build box. Override either path
# with the FOURG1_PYTHON32 / FOURG1_ISCC environment variables if needed.
function Find-Python32 {
    if ($env:FOURG1_PYTHON32 -and (Test-Path -LiteralPath $env:FOURG1_PYTHON32)) {
        return $env:FOURG1_PYTHON32
    }
    $viaLauncher = & py -3-32 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $viaLauncher -and (Test-Path -LiteralPath $viaLauncher)) {
        return $viaLauncher
    }
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313-32\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312-32\python.exe",
        "${env:ProgramFiles(x86)}\Python313-32\python.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Find-Iscc {
    if ($env:FOURG1_ISCC -and (Test-Path -LiteralPath $env:FOURG1_ISCC)) {
        return $env:FOURG1_ISCC
    }
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $onPath = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if ($onPath) { return $onPath }
    return $null
}

$Python32 = Find-Python32
$Iscc = Find-Iscc

if (-not $Python32) {
    throw "32-bit Python 3 was not found. Install the x86 build, or set FOURG1_PYTHON32."
}
if (-not $Iscc) {
    throw "Inno Setup 6 was not found. Install it, or set FOURG1_ISCC to ISCC.exe."
}

Push-Location $Root
try {
    & $Python32 -c "import sys; assert sys.maxsize < 2**32, '32-bit Python is required'"
    & $Python32 -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

    $BuildDir = Join-Path $Root "build"
    $DistDir = Join-Path $Root "dist"
    foreach ($Path in @($BuildDir, $DistDir)) {
        $ResolvedRoot = [System.IO.Path]::GetFullPath($Root)
        $ResolvedPath = [System.IO.Path]::GetFullPath($Path)
        if (-not $ResolvedPath.StartsWith($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean outside project: $ResolvedPath"
        }
        if (Test-Path -LiteralPath $ResolvedPath) {
            Remove-Item -LiteralPath $ResolvedPath -Recurse -Force
        }
    }

    # --- Single source of truth: read release metadata from product.py -------
    $Version = & $Python32 -c "import product; print(product.VERSION)"
    $Publisher = & $Python32 -c "import product; print(product.PUBLISHER)"
    $VersionNumeric = & $Python32 -c "import make_version_info as m, product; print('.'.join(str(n) for n in m.numeric_version(product.VERSION)))"
    if (-not $Version) { throw "Could not read VERSION from product.py" }
    Write-Host "Building $Publisher 4G1 Live $Version ($VersionNumeric)" -ForegroundColor Cyan

    # Regenerate the Windows VERSIONINFO resource so the .exe properties, the
    # installer and the app can never disagree about the version.
    & $Python32 "make_version_info.py"
    if ($LASTEXITCODE -ne 0) { throw "version-info generation failed" }

    & $Python32 -m PyInstaller --noconfirm "4G1Live.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    & $Iscc "/DMyAppVersion=$Version" "/DMyAppPublisher=$Publisher" `
            "/DMyAppVersionNumeric=$VersionNumeric" "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

    $Portable = Join-Path $Root "releases\4G1-Live-$Version-portable-win32.zip"
    if (Test-Path -LiteralPath $Portable) { Remove-Item -LiteralPath $Portable -Force }
    Compress-Archive -Path "dist\4G1 Live\*" -DestinationPath $Portable -CompressionLevel Optimal

    $Setup = Join-Path $Root "releases\4G1-Live-Setup-$Version-win32.exe"
    $Hashes = Get-FileHash -Algorithm SHA256 $Setup, $Portable
    $Hashes | Format-Table -AutoSize

    # Publish the checksums next to the artifacts so downloads can be verified.
    $ManifestPath = Join-Path $Root "releases\SHA256SUMS-$Version.txt"
    $Hashes | ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } |
        Set-Content -LiteralPath $ManifestPath -Encoding ascii
    Write-Host "Checksums written to $ManifestPath" -ForegroundColor Cyan
}
finally {
    Pop-Location
}
