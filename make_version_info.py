"""Generate version-info.txt for PyInstaller from product.py.

product.py is the single source of truth for release metadata. This script
regenerates the Windows VERSIONINFO resource from it at build time so the
version can never drift between the app, the .exe properties and the installer.
"""

import io
import re
import sys

import product


def numeric_version(v):
    """'1.0.0-rc3' -> (1, 0, 0, 0) — Windows VERSIONINFO needs 4 integers."""
    nums = [int(n) for n in re.findall(r"\d+", v.split("-")[0])][:4]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums)


TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', {publisher!r}),
        StringStruct('FileDescription', {description!r}),
        StringStruct('FileVersion', {version!r}),
        StringStruct('InternalName', {app_id!r}),
        StringStruct('LegalCopyright', {copyright!r}),
        StringStruct('OriginalFilename', {exe_name!r}),
        StringStruct('ProductName', {app_name!r}),
        StringStruct('ProductVersion', {version!r})
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    text = TEMPLATE.format(
        vers=numeric_version(product.VERSION),
        publisher=product.PUBLISHER,
        description=product.PRODUCT_DESCRIPTION,
        version=product.VERSION,
        app_id=product.APP_ID,
        copyright=product.COPYRIGHT,
        exe_name=product.APP_NAME + ".exe",
        app_name=product.APP_NAME,
    )
    io.open("version-info.txt", "w", encoding="utf-8", newline="\n").write(text)
    sys.stdout.write(f"version-info.txt regenerated for {product.VERSION}\n")


if __name__ == "__main__":
    main()
