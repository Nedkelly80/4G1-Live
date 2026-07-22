$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python32 = "C:\Users\trmra\AppData\Local\Programs\Python\Python313-32\python.exe"
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (-not (Test-Path -LiteralPath $Python32)) {
    throw "32-bit Python 3.13 was not found at $Python32"
}
if (-not (Test-Path -LiteralPath $Iscc)) {
    throw "Inno Setup 6 was not found at $Iscc"
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
