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

    & $Python32 -m PyInstaller --noconfirm "4G1Live.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    & $Iscc "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

    $Portable = Join-Path $Root "releases\4G1-Live-1.0.0-rc3-portable-win32.zip"
    if (Test-Path -LiteralPath $Portable) { Remove-Item -LiteralPath $Portable -Force }
    Compress-Archive -Path "dist\4G1 Live\*" -DestinationPath $Portable -CompressionLevel Optimal

    Get-FileHash -Algorithm SHA256 "releases\4G1-Live-Setup-1.0.0-rc3-win32.exe", $Portable |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}
