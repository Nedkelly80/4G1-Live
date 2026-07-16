# PyInstaller one-folder build. Run via build-release.ps1 with 32-bit Python.
from pathlib import Path

root = Path(SPEC).resolve().parent

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "4g1_logo.png"), "."),
        (str(root / "4g1_mark.png"), "."),
        (str(root / "4g1.icon.png"), "."),
        (str(root / "4g1.ico"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["idlelib", "test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="4G1 Live",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / "4g1.ico"),
    version=str(root / "version-info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="4G1 Live",
)
