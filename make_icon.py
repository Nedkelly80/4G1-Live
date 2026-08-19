"""Build multi-resolution Windows icon + logo assets for 4G1 Live AI."""
from __future__ import annotations

import io
import os
import shutil
import struct
import sys

from PIL import Image, ImageDraw

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def circularize(img: Image.Image, feather: int = 2) -> Image.Image:
    img = img.convert("RGBA")
    s = img.size[0]
    mask = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((feather, feather, s - 1 - feather, s - 1 - feather), fill=255)
    r, g, b, _a = img.split()
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    circ = Image.merge("RGBA", (r, g, b, mask))
    out.paste(circ, (0, 0), mask)
    return out


def square_center(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def write_png_ico(hi: Image.Image, path: str, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    """Write a multi-size ICO with PNG-compressed frames (Windows Vista+)."""
    entries = []
    payloads = []
    offset = 6 + 16 * len(sizes)
    for s in sizes:
        im = hi.resize((s, s), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset))
        payloads.append(data)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(sizes)))
        for e in entries:
            f.write(e)
        for data in payloads:
            f.write(data)


def build(source_path: str) -> None:
    if not os.path.isfile(source_path):
        raise SystemExit(f"Source not found: {source_path}")

    out_png = os.path.join(APP_DIR, "4g1.icon.png")
    out_mark = os.path.join(APP_DIR, "4g1_mark.png")
    out_logo = os.path.join(APP_DIR, "4g1_logo.png")
    out_ico = os.path.join(APP_DIR, "4g1.ico")
    backup_ico = os.path.join(APP_DIR, "4g1.ico.bak")

    im = square_center(Image.open(source_path))
    hi = circularize(im.resize((1024, 1024), Image.Resampling.LANCZOS))

    if os.path.isfile(out_ico) and not os.path.isfile(backup_ico):
        shutil.copy2(out_ico, backup_ico)

    hi.save(out_png, "PNG")
    hi.resize((280, 280), Image.Resampling.LANCZOS).save(out_logo, "PNG")
    hi.resize((48, 48), Image.Resampling.LANCZOS).save(out_mark, "PNG")
    write_png_ico(hi, out_ico)

    print("icon.png", out_png, os.path.getsize(out_png))
    print("logo.png ", out_logo, os.path.getsize(out_logo))
    print("mark.png ", out_mark, os.path.getsize(out_mark))
    print("ico      ", out_ico, os.path.getsize(out_ico))


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP_DIR, "4g1.icon.png")
    build(src)
