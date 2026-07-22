"""4G1 Live — Mitsubishi Live ECU Data.

Professional live-data dashboard for the Mitsubishi MUT-II ECU over a J2534
vehicle interface. Primary target: CE Lancer / Mirage 4G15 12V (1996–2003).
Also supports CE 4G93 MAF (e.g. MD360479) via vehicle profiles.

Run with 32-bit Python:  py -3.13-32 app.py
"""

import collections
import csv
import ctypes
import json
import logging
import math
import os
import struct
import sys
import threading
import time
import traceback
import zlib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

import j2534
from product import APP_NAME, VERSION, COPYRIGHT, PRODUCT_DESCRIPTION

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
USER_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", SOURCE_DIR), "4G1 Live"
)
LOG_DIR = os.path.join(USER_DATA_DIR, "Logs")
SETTINGS_PATH = os.path.join(USER_DATA_DIR, "settings.json")
LEGACY_SETTINGS_PATH = os.path.join(SOURCE_DIR, "settings.json")
ICON_PATH = os.path.join(APP_DIR, "4g1.ico")          # window / taskbar (.ico)
LOGO_PATH = os.path.join(APP_DIR, "4g1_logo.png")     # settings brand preview
MARK_PATH = os.path.join(APP_DIR, "4g1_mark.png")     # header badge
SOURCE_LOGO_PATH = os.path.join(APP_DIR, "4g1.icon.png")  # master artwork


def _configure_logging():
    """Create a per-user diagnostic log without requiring admin rights."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        logging.basicConfig(
            filename=os.path.join(LOG_DIR, "4g1-live.log"),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        )
        logging.info("%s %s started (frozen=%s)", APP_NAME, VERSION,
                     bool(getattr(sys, "frozen", False)))
    except Exception:
        logging.basicConfig(level=logging.INFO)


_configure_logging()

# Hero vitals always shown at top of dashboard (if present in latest)
HERO_PIDS = [0x21, 0x07, 0x14, 0x2F, 0x06, 0x17]


def vehicle_line(settings_or_id=None):
    """Header / log line for the active vehicle profile."""
    if isinstance(settings_or_id, dict):
        pid = settings_or_id.get("vehicle_profile")
    else:
        pid = settings_or_id
    return j2534.get_profile(pid)["line"]

# Baud presets shown in connection settings
BAUD_PRESETS = ["15625", "10400", "9600", "4800"]

# ---------------------------------------------------------------- themes
# Optional keys: font, mono, xp, title, title2 (Windows XP chrome), brand2
# (secondary brand accent used for the "LIVE" wordmark in the header)
THEMES = {
    "4G1 Red": dict(
        # Premium dark chrome inspired by the box art, tuned for better depth.
        bg="#08090d", panel="#10131a", panel2="#171b24", elev="#212734",
        fg="#f7f8fb", dim="#a4acba", muted="#6c7688",
        accent="#f12b3d", accent_hi="#ff5a67", accent2="#d4dae4",
        good="#3ed598", warn="#ffb020", bad="#ff4d6d",
        border="#2d3442", chart="#06080d",
        glow="#f12b3d33", tab_sel="#171b24",
        brand2="#4cb7ff",
        font_choices=("Eurostile", "Bahnschrift", "Segoe UI Semibold", "Segoe UI"),
        mono_choices=("JetBrains Mono", "Cascadia Mono", "Consolas"),
    ),
    "Windows Luna": dict(
        # Authentic XP Luna Blue — classic beige 3D chrome, blue caption bar,
        # selection blue, Tahoma. Matches the default Windows XP visual style.
        bg="#3A6EA5",          # classic Luna desktop / workspace blue
        panel="#ECE9D8",       # 3D Face / button face beige
        panel2="#FFFFFF",      # window / field white
        elev="#D6D2C2",        # recessed beige
        fg="#000000",
        dim="#0A246A",         # caption text navy
        muted="#5A574B",       # gray text on beige
        accent="#316AC5",      # Luna selection / highlight blue
        accent_hi="#4B8CF0",
        accent2="#003C74",     # deep caption navy
        good="#008000",
        warn="#E56717",
        bad="#C00000",
        border="#0054E3",      # active border blue
        chart="#FFFFFF",
        glow="#316AC533",
        tab_sel="#FFFFFF",
        title="#0A246A",       # active caption (dark end of Luna gradient)
        title2="#A6CAF0",      # active caption light / gradient end
        brand2="#FFE45A",      # yellow LIVE wordmark like XP start glow
        font="Tahoma",
        mono="Lucida Console",
        xp=True,
        luna=True,
    ),
    "Windows XP": dict(
        # Lighter Luna-inspired workspace (kept for existing users)
        bg="#9DB9EB", panel="#ECE9D8", panel2="#FFFFFF", elev="#C2D5F2",
        fg="#000000", dim="#0A246A", muted="#5A574B",
        accent="#245EDC", accent_hi="#3C8CF0", accent2="#003C74",
        good="#008000", warn="#E56717", bad="#C00000",
        border="#0A246A", chart="#F8FBFF",
        glow="#245EDC33", tab_sel="#FFFFFF",
        title="#0A246A", title2="#3A8CF0",
        brand2="#FFE45A",
        font="Tahoma", mono="Lucida Console",
        xp=True,
    ),
    "Windows Olive": dict(
        # Authentic XP Luna Olive Green — default olive desktop, olive caption,
        # beige 3D face, Tahoma. Matches the real "Olive Green" colour scheme.
        bg="#9BA18C",          # classic Olive desktop
        panel="#ECE9D8",       # 3D Face beige
        panel2="#FFFFFF",
        elev="#D6D2C2",
        fg="#000000",
        dim="#3F4632",
        muted="#5A574B",
        accent="#93A070",      # Olive selection / menu highlight
        accent_hi="#A8B88A",
        accent2="#5A6140",
        good="#2E7D32",
        warn="#E56717",
        bad="#C00000",
        border="#7A8258",
        chart="#FFFFFF",
        glow="#93A07033",
        tab_sel="#FFFFFF",
        title="#6B7245",       # active caption dark olive
        title2="#C2C9A4",      # active caption light
        brand2="#FFE45A",
        font="Tahoma",
        mono="Lucida Console",
        xp=True,
        luna=True,
    ),
    "Luna Silver": dict(
        # XP Luna Silver colour scheme
        bg="#A0A0A4", panel="#E0DFE3", panel2="#FFFFFF", elev="#C8C7CC",
        fg="#000000", dim="#2B3A55", muted="#5A5A5E",
        accent="#4B6EAF", accent_hi="#6B8ECF", accent2="#2B3A55",
        good="#008000", warn="#E56717", bad="#C00000",
        border="#4B6EAF", chart="#FFFFFF",
        glow="#4B6EAF33", tab_sel="#FFFFFF",
        title="#4B6EAF", title2="#C0C7D0",
        brand2="#FFE45A",
        font="Tahoma", mono="Lucida Console",
        xp=True,
        luna=True,
    ),
    "Precision Dark": dict(
        bg="#050d14", panel="#0a1520", panel2="#0f1d2a", elev="#162636",
        fg="#f2f7fb", dim="#9eb0c0", muted="#5f7284",
        accent="#3dd5e8", accent_hi="#6ee7f4", accent2="#b8c9d6",
        good="#3dd68c", warn="#ff9a2e", bad="#ff5c6e",
        border="#243848", chart="#03090f",
        glow="#3dd5e840", tab_sel="#0f1d2a",
    ),
    "Carbon Red": dict(
        bg="#07080a", panel="#101218", panel2="#181b24", elev="#222733",
        fg="#f2f4f7", dim="#8e96a3", muted="#5a6270",
        accent="#e11d2a", accent_hi="#ff3b4a", accent2="#c8ced8",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#2a303c", chart="#040506",
        glow="#e11d2a40", tab_sel="#181b24",
    ),
    "Midnight Blue": dict(
        bg="#05080f", panel="#0c1320", panel2="#141e30", elev="#1a2740",
        fg="#eaf0fa", dim="#8a98b0", muted="#516078",
        accent="#3b82f6", accent_hi="#60a5fa", accent2="#a8bdd8",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#243148", chart="#03060c",
        glow="#3b82f640", tab_sel="#141e30",
    ),
    "Slate Pro": dict(
        bg="#0b0d11", panel="#141820", panel2="#1c2230", elev="#272f40",
        fg="#eef1f6", dim="#9aa3b2", muted="#646e7e",
        accent="#f97316", accent_hi="#fb923c", accent2="#c9d0db",
        good="#4ade80", warn="#facc15", bad="#f87171",
        border="#2e3644", chart="#07090d",
        glow="#f9731640", tab_sel="#1c2230",
    ),
    "Titanium": dict(
        bg="#0a0c0f", panel="#12151a", panel2="#1b2028", elev="#262c36",
        fg="#eef0f3", dim="#8f97a3", muted="#5c6572",
        accent="#94a3b8", accent_hi="#cbd5e1", accent2="#e11d2a",
        good="#4ade80", warn="#fbbf24", bad="#f87171",
        border="#2c333e", chart="#060709",
        glow="#94a3b840", tab_sel="#1b2028",
    ),
    "Racing Green": dict(
        bg="#050906", panel="#0c1410", panel2="#132018", elev="#1a2c22",
        fg="#eaf6ef", dim="#86a092", muted="#547064",
        accent="#10b981", accent_hi="#34d399", accent2="#b8d4c6",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#1e3328", chart="#030705",
        glow="#10b98140", tab_sel="#132018",
    ),
}
GAUGE_STYLES = ["Digital", "Bar", "Dial", "Compact"]
_FONT_CACHE = {}


def _best_available_font(root, candidates, fallback):
    key = (tuple(candidates or ()), fallback)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        families = {f.lower(): f for f in tkfont.families(root)}
    except Exception:
        return fallback
    for name in candidates or ():
        if name.lower() in families:
            _FONT_CACHE[key] = families[name.lower()]
            return _FONT_CACHE[key]
    _FONT_CACHE[key] = fallback
    return fallback


def ui_font(theme, size, bold=False, mono=False):
    """Theme-aware UI font (Tahoma on Luna / XP, Segoe UI otherwise)."""
    if mono:
        family = (theme or {}).get("mono", "Consolas")
    else:
        family = (theme or {}).get("font", "Segoe UI")
    return (family, size, "bold") if bold else (family, size)


DEFAULTS = dict(
    theme="4G1 Red",
    style="Dial",
    gauges=[hex(g) for g in j2534.DEFAULT_GAUGES],
    poll_hz=12,
    # primary product target; switch to 4g93_maf when live on 1.8 MAF
    vehicle_profile=j2534.DEFAULT_PROFILE_ID,
    # --- connection ---
    dll_path=j2534.default_dll(),
    mut_baud=15625,
    pin1_ground=True,          # required for CE Lancer / Mirage
    init_addr="0x00",
    init_attempts=5,
    request_timeout_ms=400,
    auto_connect=False,
    auto_live=False,
)


def load_settings():
    s = dict(DEFAULTS)
    for path in (SETTINGS_PATH, LEGACY_SETTINGS_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                s.update(json.load(f))
            break
        except FileNotFoundError:
            continue
        except Exception:
            logging.exception("Could not read settings from %s", path)
    # migrate / clamp unknown profile ids to primary 4G15 12V
    if s.get("vehicle_profile") not in j2534.VEHICLE_PROFILES:
        s["vehicle_profile"] = j2534.DEFAULT_PROFILE_ID
    # theme renames
    theme_aliases = {
        "Luna Olive": "Windows Olive",
    }
    if s.get("theme") in theme_aliases:
        s["theme"] = theme_aliases[s["theme"]]
    if s.get("theme") not in THEMES:
        s["theme"] = DEFAULTS["theme"]
    return s


def save_settings(s):
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        temp_path = SETTINGS_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        os.replace(temp_path, SETTINGS_PATH)
    except Exception:
        logging.exception("Could not save settings")


def _param(pid):
    p = j2534.ALL_PARAMS[pid]
    if len(p) >= 5:
        return p[0], p[1], p[2], p[3], p[4]
    return p[0], p[1], p[2], p[3], 0


def _lerp(a, b, t):
    return a + (b - a) * t


def _blend_hex(c1, c2, t):
    """Blend two #rrggbb colours. t=0 → c1, t=1 → c2."""
    def _rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    r = int(_lerp(r1, r2, t))
    g = int(_lerp(g1, g2, t))
    b = int(_lerp(b1, b2, t))
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_rgb(hexcolor):
    h = (hexcolor or "#000000").lstrip("#")
    if len(h) != 6:
        return 0, 0, 0
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _read_png_rgba(path):
    """Minimal PNG decoder for 8-bit RGB/RGBA (no interlacing). Returns (w, h, pixels)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    i = 8
    w = h = None
    color = bit = None
    idat = b""
    while i + 8 <= len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        chunk = data[i + 8:i + 8 + ln]
        i += 12 + ln
        if typ == b"IHDR":
            w, h, bit, color = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if not w or color not in (2, 6) or bit != 8:
        raise ValueError("unsupported PNG")
    raw = zlib.decompress(idat)
    bpp = 4 if color == 6 else 3
    stride = w * bpp
    rows = []
    pos = 0
    prev = bytearray(stride)
    for _y in range(h):
        ft = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        if ft == 1:
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + left) & 255
        elif ft == 2:
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 255
        elif ft == 4:
            def paeth(a, b, c):
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    return a
                if pb <= pc:
                    return b
                return c
            for x in range(stride):
                a = row[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + paeth(a, b, c)) & 255
        rows.append(row)
        prev = row
    pixels = []
    for row in rows:
        for x in range(w):
            if color == 6:
                o = x * 4
                pixels.append((row[o], row[o + 1], row[o + 2], row[o + 3]))
            else:
                o = x * 3
                pixels.append((row[o], row[o + 1], row[o + 2], 255))
    return w, h, pixels


def _scale_nearest_rgba(pixels, w, h, size):
    """Nearest-neighbour scale to size×size."""
    if w == size and h == size:
        return pixels
    out = []
    for y in range(size):
        sy = min(h - 1, int(y * h / size))
        for x in range(size):
            sx = min(w - 1, int(x * w / size))
            out.append(pixels[sy * w + sx])
    return out


def _badge_photo(path, bg_hex, size=42, master=None):
    """Circular badge composited onto bg — kills the black square corners.

    The shipped mark/logo PNGs are circular art on an opaque black square.
    We mask to a soft circle and blend onto the header/panel colour so the
    badge reads as a round chip, not a black tile.
    """
    w0, h0, src = _read_png_rgba(path)
    size = max(16, int(size))
    src = _scale_nearest_rgba(src, w0, h0, size)
    br, bg_, bb = _hex_rgb(bg_hex)
    cx = cy = (size - 1) * 0.5
    radius = size * 0.5 - 0.35
    feather = 1.35
    rows = []
    for y in range(size):
        cols = []
        for x in range(size):
            pr, pg, pb, pa = src[y * size + x]
            dist = math.hypot(x - cx, y - cy)
            # Soft circular mask
            if dist >= radius + feather:
                mask = 0.0
            elif dist <= radius - feather:
                mask = 1.0
            else:
                mask = (radius + feather - dist) / (2.0 * feather)
            # Drop residual black plate outside the disc (safety for soft edge)
            if pr + pg + pb < 28 and dist > radius * 0.90:
                mask = 0.0
            a = mask * (pa / 255.0)
            r = int(br * (1 - a) + pr * a)
            g = int(bg_ * (1 - a) + pg * a)
            b = int(bb * (1 - a) + pb * a)
            cols.append(f"#{r:02x}{g:02x}{b:02x}")
        rows.append("{" + " ".join(cols) + "}")
    img = tk.PhotoImage(width=size, height=size, master=master)
    img.put(" ".join(rows))
    return img


# ---------------------------------------------------------------- soft button
class SoftButton(tk.Frame):
    """Flat product button with hover, press, and disabled states."""

    def __init__(self, master, text, command, theme, primary=False, compact=False, **kw):
        super().__init__(master, bg=theme.get("panel", theme["bg"]), **kw)
        self.theme = theme
        self.primary = primary
        self.command = command
        self._enabled = True
        t = theme
        if primary:
            self.bg0, self.bg1, self.bg_press = t["accent"], t["accent_hi"], _blend_hex(t["accent"], "#000000", 0.18)
            self.fg = "#ffffff"
        else:
            self.bg0, self.bg1, self.bg_press = t["panel2"], t["elev"], t["border"]
            self.fg = t["fg"]
        pad_x, pad_y = (12, 5) if compact else (16, 7)
        self.lbl = tk.Label(
            self, text=text, bg=self.bg0, fg=self.fg,
            font=ui_font(t, 9, bold=True), padx=pad_x, pady=pad_y, cursor="hand2",
        )
        self.lbl.pack()
        # Classic XP-style raised edge on secondary buttons
        if t.get("xp") and not primary:
            self.configure(highlightbackground="#FFFFFF", highlightthickness=1)
            self.lbl.configure(
                highlightbackground=t["border"], highlightthickness=1,
                relief="raised", bd=1,
            )
        elif not primary:
            self.configure(highlightbackground=t["border"], highlightthickness=1)
            self.lbl.configure(highlightthickness=0)
        else:
            # Primary: subtle outer edge so the accent block reads as a control
            edge = _blend_hex(t["accent"], "#000000", 0.35)
            self.configure(highlightbackground=edge, highlightthickness=1)
        for w in (self, self.lbl):
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<Button-1>", self._click)

    def _enter(self, _=None):
        if self._enabled:
            try:
                self.lbl.config(bg=self.bg1)
            except tk.TclError:
                pass

    def _leave(self, _=None):
        if self._enabled:
            try:
                self.lbl.config(bg=self.bg0)
            except tk.TclError:
                pass

    def _press(self, _=None):
        if self._enabled:
            try:
                self.lbl.config(bg=self.bg_press)
            except tk.TclError:
                pass

    def _click(self, _=None):
        if not self._enabled or not self.command:
            return
        # Guard against nested bind on Frame+Label firing twice
        if getattr(self, "_firing", False):
            return
        self._firing = True
        try:
            self.command()
        finally:
            self._firing = False

    def config(self, **kw):
        if "text" in kw:
            self.lbl.config(text=kw.pop("text"))
        if "state" in kw:
            st = kw.pop("state")
            self._enabled = st != "disabled"
            t = self.theme
            if self._enabled:
                self.lbl.config(bg=self.bg0, fg=self.fg, cursor="hand2")
            else:
                self.lbl.config(bg=t["elev"], fg=t["muted"], cursor="arrow")
        if kw:
            super().config(**kw)

    configure = config


# ---------------------------------------------------------------- status LED
def _health_colours(theme):
    """Colours for sensor-status LED: good / bad / warn / idle."""
    return {
        "good": theme.get("good", "#34d399"),
        "bad": theme.get("bad", "#ff6474"),
        "warn": theme.get("warn", "#fbbf24"),
        None: theme.get("muted", "#647584"),
        "off": theme.get("elev", "#172a38"),
    }


class StatusLED(tk.Canvas):
    """Small circular green/red/amber indicator for sensor health."""

    def __init__(self, master, theme, size=11, **kw):
        super().__init__(
            master, width=size, height=size, bg=theme["panel"],
            highlightthickness=0, **kw
        )
        self.theme = theme
        self.size = size
        self._status = None
        self._draw("off")

    def set_status(self, status):
        if status == self._status:
            return
        self._status = status
        self._draw(status if status in ("good", "bad", "warn") else "off")

    def _draw(self, status):
        self.delete("all")
        cols = _health_colours(self.theme)
        fill = cols.get(status, cols["off"])
        s = self.size
        # Soft outer ring + solid core for a lit-instrument look
        if status in ("good", "bad", "warn"):
            ring = _blend_hex(fill, self.theme["panel"], 0.45)
            self.create_oval(0, 0, s - 1, s - 1, fill=ring, outline="")
            self.create_oval(2, 2, s - 3, s - 3, fill=fill, outline="")
        else:
            self.create_oval(1, 1, s - 2, s - 2, fill=fill, outline=self.theme["border"])


class StatusPill(tk.Frame):
    """Compact status badge used in the product header."""

    def __init__(self, master, theme, text="●  OFFLINE", **kw):
        super().__init__(master, bg=theme["panel"], **kw)
        self.theme = theme
        self.lbl = tk.Label(
            self, text=text, bg=theme["elev"], fg=theme["dim"],
            font=ui_font(theme, 8, bold=True), padx=10, pady=3,
        )
        self.lbl.pack()
        self.configure(highlightbackground=theme["border"], highlightthickness=1)

    def set(self, text, mode="off"):
        t = self.theme
        if t.get("xp"):
            styles = {
                "off":  (t["elev"], t["dim"]),
                "ok":   ("#d4edda", "#0a6b2d"),
                "live": ("#fff3cd", "#7a5a00"),
                "warn": ("#ffe0b2", "#8a4b00"),
                "demo": ("#e2d6ff", "#4a2f8a"),
            }
        else:
            styles = {
                "off":  (t["elev"], t["dim"]),
                "ok":   (_blend_hex(t["good"], t["panel"], 0.78), t["good"]),
                "live": (_blend_hex(t["accent"], t["panel"], 0.78), t["accent_hi"]),
                "warn": (_blend_hex(t["warn"], t["panel"], 0.78), t["warn"]),
                "demo": (_blend_hex(t["warn"], t["panel"], 0.72), t["warn"]),
            }
        bg, fg = styles.get(mode, styles["off"])
        self.lbl.config(text=text, bg=bg, fg=fg)
        self.configure(bg=bg, highlightbackground=_blend_hex(fg, t["border"], 0.55))
        self.lbl.configure(bg=bg)


# ---------------------------------------------------------------- hero vital tile
class HeroTile(tk.Frame):
    """Large KPI tile for the top vitals strip."""

    def __init__(self, master, pid, theme, **kw):
        super().__init__(master, bg=theme["panel"], **kw)
        self.pid = pid
        self.theme = theme
        name, _, units, gmax, gmin = _param(pid)
        self.name, self.units = name, units
        self.gmax, self.gmin = gmax, gmin
        self._display = None
        self._target = None
        self._health = None
        self.history = collections.deque(maxlen=24)
        self.configure(highlightbackground=theme["border"], highlightthickness=1)
        self.strip = tk.Frame(self, bg=theme["border"], height=3)
        self.strip.pack(fill="x")

        body = tk.Frame(self, bg=theme["panel"])
        body.pack(fill="both", expand=True, padx=12, pady=(8, 10))

        title_row = tk.Frame(body, bg=theme["panel"])
        title_row.pack(fill="x")
        tk.Label(title_row, text=name.upper(), bg=theme["panel"], fg=theme["dim"],
                 font=ui_font(theme, 8, bold=True), anchor="w").pack(side="left")
        if pid in j2534.HEALTH_PIDS:
            self.led = StatusLED(title_row, theme, size=10)
            self.led.pack(side="right", padx=(4, 0))
        else:
            self.led = None

        row = tk.Frame(body, bg=theme["panel"])
        row.pack(fill="x", pady=(4, 0))
        self.val = tk.Label(row, text="—", bg=theme["panel"], fg=theme["muted"],
                            font=ui_font(theme, 26, bold=True, mono=True), anchor="w")
        self.val.pack(side="left")
        self.unit = tk.Label(row, text=units, bg=theme["panel"], fg=theme["muted"],
                             font=ui_font(theme, 9), anchor="sw")
        self.unit.pack(side="left", padx=(6, 0), pady=(0, 4))

        # Track + fill for at-a-glance range
        self.bar = tk.Canvas(body, height=4, bg=theme["elev"], highlightthickness=0)
        self.bar.pack(fill="x", pady=(8, 0))

    def set(self, value, ctx=None):
        self._target = value
        self.history.append(value)
        if self._display is None:
            self._display = value
        # smooth toward target
        self._display = _lerp(self._display, value, 0.45)
        self._health = j2534.sensor_health(
            self.pid, value, ctx=ctx, history=self.history
        )
        self._paint(self._display)

    def _paint(self, value):
        t = self.theme
        span = self.gmax - self.gmin
        frac = 0.0 if span <= 0 else max(0.0, min(1.0, (value - self.gmin) / span))
        col = t["fg"]
        if self._health == "bad":
            col = t["bad"]
        elif self._health == "warn":
            col = t["warn"]
        elif self.name == "Coolant" and value >= 105:
            col = t["bad"]
        elif self.name == "Battery" and (value < 11.5 or value > 15.5):
            col = t["warn"]
        elif frac > 0.88:
            col = t["warn"]

        if self.units in ("rpm", "°C", "°F", "Hz", "count", "km/h", "steps", "kPa", "°"):
            txt = f"{value:.0f}"
        else:
            txt = f"{value:.1f}"
        self.val.config(text=txt, fg=col)
        self.unit.config(fg=t["muted"] if col == t["fg"] else col)

        if self.led is not None:
            self.led.set_status(self._health)

        c = self.bar
        c.delete("all")
        w = max(c.winfo_width(), 40)
        h = 4
        bar_col = t["accent"]
        if self._health == "good":
            bar_col = t["good"]
        elif self._health == "bad":
            bar_col = t["bad"]
        elif self._health == "warn":
            bar_col = t["warn"]
        c.create_rectangle(0, 0, w, h, fill=t["elev"], outline="")
        fill_w = max(3, int(w * frac))
        c.create_rectangle(0, 0, fill_w, h, fill=bar_col, outline="")
        # Bright tip so the level reads under motion
        if fill_w > 6:
            tip = _blend_hex(bar_col, "#ffffff", 0.35)
            c.create_rectangle(fill_w - 3, 0, fill_w, h, fill=tip, outline="")
        self.strip.config(bg=bar_col)


# ---------------------------------------------------------------- gauge widget
class Gauge(tk.Frame):
    def __init__(self, master, pid, theme, style, **kw):
        super().__init__(master, bg=theme["panel"], **kw)
        self.pid = pid
        self.theme = theme
        self.style = style
        name, _, units, gmax, gmin = _param(pid)
        self.name, self.units = name, units
        self.gmax = gmax if gmax != gmin else gmin + 1
        self.gmin = gmin
        self.value = None
        self._display = None
        self._health = None
        self.history = collections.deque(maxlen=48)
        self.configure(highlightbackground=theme["border"], highlightthickness=1)
        self._build()

    def _build(self):
        t = self.theme
        self.strip = tk.Frame(self, bg=t["border"], height=3)
        self.strip.pack(fill="x")
        body = tk.Frame(self, bg=t["panel"])
        body.pack(side="left", fill="both", expand=True)
        self.body = body

        head = tk.Frame(body, bg=t["panel"])
        head.pack(fill="x", padx=12, pady=(9, 0))
        tk.Label(head, text=self.name.upper(), bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True), anchor="w").pack(side="left")
        if self.pid in j2534.HEALTH_PIDS:
            self.led = StatusLED(head, t, size=10)
            self.led.pack(side="right", padx=(6, 0))
        else:
            self.led = None
        tk.Label(head, text=self.units, bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7), anchor="e").pack(side="right")

        if self.style == "Digital":
            self.val = tk.Label(body, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 24, bold=True, mono=True), anchor="w")
            self.val.pack(fill="x", padx=12, pady=(2, 0))
            self.spark = tk.Canvas(body, height=14, bg=t["panel"], highlightthickness=0)
            self.spark.pack(fill="x", padx=10, pady=(2, 0))
            self.range_lbl = tk.Label(body, text=self._range_text(), bg=t["panel"],
                                      fg=t["muted"], font=ui_font(t, 7), anchor="w")
            self.range_lbl.pack(fill="x", padx=12, pady=(2, 8))
        elif self.style == "Bar":
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="x", padx=12, pady=(4, 0))
            self.val = tk.Label(row, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 20, bold=True, mono=True), anchor="w")
            self.val.pack(side="left")
            self.canvas = tk.Canvas(body, height=14, bg=t["elev"], highlightthickness=0)
            self.canvas.pack(fill="x", padx=12, pady=(8, 2))
            self.range_lbl = tk.Label(body, text=self._range_text(), bg=t["panel"],
                                      fg=t["muted"], font=ui_font(t, 7), anchor="w")
            self.range_lbl.pack(fill="x", padx=12, pady=(0, 8))
        elif self.style == "Compact":
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="both", expand=True, padx=12, pady=8)
            self.val = tk.Label(row, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 17, bold=True, mono=True), anchor="w")
            self.val.pack(side="left")
            self.pct = tk.Label(row, text="", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 8, bold=True, mono=True), anchor="e")
            self.pct.pack(side="right")
        else:  # Dial
            self.canvas = tk.Canvas(body, height=96, bg=t["panel"], highlightthickness=0)
            self.canvas.pack(fill="x", padx=4, pady=2)
            self.val = tk.Label(body, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 12, bold=True, mono=True))
            self.val.pack(pady=(0, 6))

    def _range_text(self):
        if self.gmin < 0:
            return f"{self.gmin:g}  ···  {self.gmax:g}"
        return f"0  ···  {self.gmax:g}"

    def _fmt(self, v):
        if self.units in ("rpm", "°C", "°F", "Hz", "count", "km/h", "steps", "kPa", "°"):
            return f"{v:.0f}"
        if self.units == "V" and self.name == "O2 Sensor":
            return f"{v:.2f}"
        return f"{v:.1f}"

    def _frac(self, value):
        span = self.gmax - self.gmin
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (value - self.gmin) / span))

    def _colour(self, value, frac):
        t = self.theme
        if self._health == "bad":
            return t["bad"]
        if self._health == "warn":
            return t["warn"]
        if self._health == "good":
            return t["fg"]
        if self.gmin < 0:
            if abs(value) > self.gmax * 0.6:
                return t["warn"]
            return t["fg"]
        if self.name == "Coolant" and value >= 105:
            return t["bad"]
        if self.name == "Battery" and (value < 11.5 or value > 15.5):
            return t["warn"]
        if frac > 0.88:
            return t["warn"]
        return t["fg"]

    def set(self, value, ctx=None):
        self.value = value
        self.history.append(value)
        if self._display is None:
            self._display = value
        else:
            self._display = _lerp(self._display, value, 0.5)
        self._health = j2534.sensor_health(
            self.pid, value, ctx=ctx, history=self.history
        )
        self._render(self._display)

    def _render(self, value):
        t = self.theme
        frac = self._frac(value)
        col = self._colour(value, frac)
        txt = self._fmt(value)
        self.strip.config(bg=col if col != t["fg"] else t["accent"])

        if self.led is not None:
            self.led.set_status(self._health)

        if self.style == "Digital":
            self.val.config(text=txt, fg=col)
            self._draw_spark()
        elif self.style == "Bar":
            self.val.config(text=txt, fg=col)
            c = self.canvas
            c.delete("all")
            w = max(c.winfo_width(), 40)
            h = 14
            # segmented track
            segs = 22
            gap = 2
            sw = (w - gap * (segs - 1)) / segs
            filled = int(round(segs * frac))
            fill_on = t["accent"]
            if self._health == "good":
                fill_on = t["good"]
            elif self._health == "bad":
                fill_on = t["bad"]
            elif self._health == "warn":
                fill_on = t["warn"]
            for i in range(segs):
                x0 = i * (sw + gap)
                fill = fill_on if i < filled else t["elev"]
                if i < filled and self._health != "good" and frac > 0.88 and i >= segs - 3:
                    fill = t["warn"]
                # slight vertical inset for a recessed track look
                c.create_rectangle(x0, 1, x0 + sw, h - 1, fill=fill, outline="")
            if self.gmin < 0:
                z = (0 - self.gmin) / (self.gmax - self.gmin)
                zx = int(w * z)
                c.create_line(zx, 0, zx, h, fill=t["fg"], width=1)
        elif self.style == "Compact":
            self.val.config(text=txt, fg=col)
            status_txt = {"good": "OK", "bad": "BAD", "warn": "!"}.get(self._health, "")
            self.pct.config(
                text=status_txt or f"{frac * 100:.0f}%",
                fg={"good": t["good"], "bad": t["bad"], "warn": t["warn"]}.get(
                    self._health, t["muted"]
                ),
            )
        else:
            self._draw_dial(frac, col, txt)

    def _draw_spark(self):
        c = self.spark
        c.delete("all")
        if len(self.history) < 2:
            return
        w = max(c.winfo_width(), 40)
        h = 14
        t = self.theme
        vals = list(self.history)
        lo = min(vals)
        hi = max(vals)
        if hi - lo < 1e-6:
            hi = lo + 1
        step = w / max(1, len(vals) - 1)
        pts = []
        for i, v in enumerate(vals):
            x = i * step
            y = h - 1 - ((v - lo) / (hi - lo)) * (h - 3)
            pts += [x, y]
        # Soft fill under sparkline
        if len(pts) >= 4:
            fill_pts = list(pts) + [pts[-2], h, pts[0], h]
            try:
                c.create_polygon(*fill_pts, fill=t["elev"], outline="")
            except Exception:
                pass
        c.create_line(*pts, fill=t["accent"], width=1.6, smooth=True)
        c.create_oval(pts[-2] - 2, pts[-1] - 2, pts[-2] + 2, pts[-1] + 2,
                      fill=t["accent_hi"], outline="")

    def _draw_dial(self, frac, col, txt):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 100)
        h = max(c.winfo_height(), 90)
        cx, cy, r = w / 2, h * 0.80, min(w, h) * 0.44
        t = self.theme
        # Outer rim
        c.create_arc(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4,
                     start=200, extent=-220, style="arc", outline=t["border"], width=2)
        # Recessed track
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=200, extent=-220,
                     style="arc", outline=t["elev"], width=12)
        # Value arc — redline warns near the top of range
        if self._health == "bad":
            arc_col = t["bad"]
        elif self._health == "warn" or frac > 0.88:
            arc_col = t["warn"]
        elif self._health == "good":
            arc_col = t["good"]
        else:
            arc_col = t["accent"]
        if frac > 0.004:
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=200, extent=-220 * frac,
                         style="arc", outline=arc_col, width=12)
        # Major / minor ticks
        for i in range(11):
            a = math.radians(200 - 220 * (i / 10))
            major = i % 5 == 0
            r0, r1 = r - 1, r - (13 if major else 8)
            c.create_line(cx + r0 * math.cos(a), cy - r0 * math.sin(a),
                          cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                          fill=t["dim"] if major else t["muted"],
                          width=2 if major else 1)
            if major:
                tv = self.gmin + (self.gmax - self.gmin) * (i / 10)
                tx = cx + (r - 22) * math.cos(a)
                ty = cy - (r - 22) * math.sin(a)
                c.create_text(tx, ty, text=f"{tv:.0f}", fill=t["muted"],
                              font=ui_font(t, 6, mono=True))
        # Needle + hub
        ang = math.radians(200 - 220 * frac)
        nx = cx + r * 0.70 * math.cos(ang)
        ny = cy - r * 0.70 * math.sin(ang)
        # Needle shadow for depth
        c.create_line(cx + 1, cy + 1, nx + 1, ny + 1, fill=t["chart"], width=3,
                      capstyle="round")
        c.create_line(cx, cy, nx, ny, fill=col, width=2.5, capstyle="round")
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=t["elev"], outline=t["border"])
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=t["accent"], outline="")
        self.val.config(text=f"{txt}  {self.units}", fg=col)


# ---------------------------------------------------------------- strip chart
class StripChart(tk.Frame):
    def __init__(self, master, theme, label="RPM", units="rpm", vmax=8000,
                 npoints=260, accent=None, **kw):
        super().__init__(master, bg=theme["panel"], **kw)
        self.theme = theme
        self.label = label
        self.units = units
        self.vmax = vmax
        self.vmin = 0
        self.accent = accent or theme["accent"]
        self.data = collections.deque(maxlen=npoints)
        self.configure(highlightbackground=theme["border"], highlightthickness=1)

        tk.Frame(self, bg=self.accent, height=3).pack(fill="x")
        head = tk.Frame(self, bg=theme["panel"])
        head.pack(fill="x")
        tk.Label(head, text=label.upper(), bg=theme["panel"], fg=theme["dim"],
                 font=ui_font(theme, 7, bold=True)).pack(side="left", padx=10, pady=5)
        self.stats = tk.Label(head, text="min —  ·  max —", bg=theme["panel"],
                              fg=theme["muted"], font=ui_font(theme, 7, mono=True))
        self.stats.pack(side="left", padx=4)
        self.cur = tk.Label(head, text="—", bg=theme["panel"], fg=theme["muted"],
                            font=ui_font(theme, 12, bold=True, mono=True))
        self.cur.pack(side="right", padx=10)
        self.canvas = tk.Canvas(self, bg=theme["chart"], highlightthickness=0, height=72)
        self.canvas.pack(fill="both", expand=True, padx=1, pady=(0, 1))

    def push(self, value):
        self.data.append(value)
        self.cur.config(text=f"{value:.0f} {self.units}", fg=self.accent)
        if self.data:
            self.stats.config(text=f"min {min(self.data):.0f}  ·  max {max(self.data):.0f}")
        self._draw()

    def clear(self):
        self.data.clear()
        self.cur.config(text="—", fg=self.theme["muted"])
        self.stats.config(text="min —  ·  max —")
        self.canvas.delete("all")

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 400
        h = c.winfo_height() or 100
        t = self.theme
        vmax = self.vmax if self.vmax else 1
        # Horizontal grid + axis labels
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            c.create_line(40, y, w - 4, y, fill=t["border"], dash=(1, 4))
            c.create_text(6, y, anchor="w", fill=t["muted"],
                          font=ui_font(t, 7, mono=True), text=f"{vmax * (1 - frac):.0f}")
        # Baseline
        c.create_line(40, h - 1, w - 4, h - 1, fill=t["border"])
        if len(self.data) < 2:
            c.create_text(w / 2, h / 2, text="waiting…", fill=t["muted"],
                          font=ui_font(t, 9))
            return
        n = len(self.data)
        step = (w - 48) / max(1, self.data.maxlen - 1)
        pts = []
        for i, v in enumerate(self.data):
            x = 40 + (self.data.maxlen - n + i) * step
            y = h - max(0.0, min(1.0, v / vmax)) * (h - 8) - 4
            pts += [x, y]
        if len(pts) >= 4:
            fill_pts = list(pts) + [pts[-2], h - 1, pts[0], h - 1]
            try:
                c.create_polygon(*fill_pts, fill=t["elev"], outline="")
            except Exception:
                pass
        c.create_line(*pts, fill=self.accent, width=2.2, smooth=True)
        # Latest sample marker
        c.create_oval(pts[-2] - 4, pts[-1] - 4, pts[-2] + 4, pts[-1] + 4,
                      fill=self.accent, outline=t["chart"], width=1)
        c.create_oval(pts[-2] - 1.5, pts[-1] - 1.5, pts[-2] + 1.5, pts[-1] + 1.5,
                      fill="#ffffff", outline="")


# ---------------------------------------------------------------- elevated card
def card(parent, theme, accent=None):
    """A panel with a subtle drop-shadow and coloured top accent strip.

    Returns (outer, panel): pack/grid `outer`, build content inside `panel`.
    """
    t = theme
    outer = tk.Frame(parent, bg=t["bg"])
    # Soft shadow step (Tk has no real shadows)
    shadow = tk.Frame(outer, bg=t["chart"] if not t.get("xp") else t["border"])
    shadow.pack(fill="both", expand=True, padx=(1, 0), pady=(1, 0))
    panel = tk.Frame(shadow, bg=t["panel"],
                      highlightbackground=t["border"], highlightthickness=1)
    panel.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))
    tk.Frame(panel, bg=accent or t["accent"], height=3).pack(fill="x")
    return outer, panel


# ---------------------------------------------------------------- section header
def section_label(parent, text, theme):
    f = tk.Frame(parent, bg=theme["bg"])
    tk.Label(f, text=text, bg=theme["bg"], fg=theme["dim"],
             font=ui_font(theme, 8, bold=True)).pack(side="left")
    tk.Frame(f, bg=theme["border"], height=1).pack(side="left", fill="x", expand=True,
                                                    padx=(10, 0), pady=1)
    return f


def mini_chip(parent, text, theme, tone="neutral"):
    """Small label chip for headers / meta rows."""
    t = theme
    if tone == "accent":
        bg, fg = _blend_hex(t["accent"], t["panel"], 0.82), t["accent_hi"]
    elif tone == "good":
        bg, fg = _blend_hex(t["good"], t["panel"], 0.82), t["good"]
    else:
        bg, fg = t["elev"], t["dim"]
    fr = tk.Frame(parent, bg=bg, highlightbackground=t["border"], highlightthickness=1)
    tk.Label(fr, text=text, bg=bg, fg=fg, font=ui_font(t, 7, bold=True),
             padx=7, pady=2).pack()
    return fr


# ---------------------------------------------------------------- main app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.theme = dict(THEMES.get(self.settings["theme"], THEMES["4G1 Red"]))
        self._resolve_theme_fonts()
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        self.title(f"4G1 Live [OLD]  —  {prof['short']}")
        self.configure(bg=self.theme["bg"])
        # Fit common laptop screens (was 1180×940 — too tall for many displays)
        self.update_idletasks()
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except Exception:
            sw, sh = 1280, 800
        win_w = min(1366, max(1024, sw - 32))
        win_h = min(768, max(680, sh - 72))
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(960, 640)
        self._logo_img = None  # keep PhotoImage refs alive
        self._mark_img = None
        self._app_icon = None
        self._set_app_icon()

        self.dev = None
        self.live = False
        self.demo = False
        self._closing = False
        self.gauges = {}
        self.heroes = {}
        self.latest = {}
        self.samples = []
        self._log_rows = []
        self._hz_times = collections.deque(maxlen=40)
        self._poll_hz_actual = 0.0
        self._active_tab = "dash"
        self._session_t0 = None

        self._build()
        self.after(300, self._set_titlebar_colour)
        self.after(100, self._refresh_ui)
        self.after(280, self._status_tick)
        # honour auto-connect / auto-live from settings
        if self.settings.get("auto_connect"):
            self.after(500, self.on_connect)
        if self.settings.get("auto_live"):
            self.after(1400, self._auto_live_if_ready)

    def _resolve_theme_fonts(self):
        self.theme["font"] = _best_available_font(
            self,
            self.theme.get("font_choices"),
            self.theme.get("font", "Segoe UI"),
        )
        self.theme["mono"] = _best_available_font(
            self,
            self.theme.get("mono_choices"),
            self.theme.get("mono", "Consolas"),
        )

    def report_callback_exception(self, exc, val, tb):
        """Turn unexpected UI faults into a supportable error instead of a crash."""
        logging.error("Unhandled UI error", exc_info=(exc, val, tb))
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            messagebox.showerror(
                f"{APP_NAME} encountered a problem",
                "The operation could not be completed. Your settings and logs "
                f"are safe.\n\nDiagnostic log:\n{os.path.join(LOG_DIR, '4g1-live.log')}",
            )
        except Exception:
            logging.error(detail)

    def _auto_live_if_ready(self):
        if self.dev and not self.live:
            self.toggle_live()

    # ---------- layout ----------
    def _build(self):
        t = self.theme
        self._style_ttk()
        self._header()
        self._toolbar()
        self._tabbar()

        self.body = tk.Frame(self, bg=t["bg"])
        self.body.pack(fill="both", expand=True, padx=12, pady=(4, 4))

        self.tab_dash = tk.Frame(self.body, bg=t["bg"])
        self.tab_codes = tk.Frame(self.body, bg=t["bg"])
        self.tab_settings = tk.Frame(self.body, bg=t["bg"])

        self._build_dash()
        self._build_codes()
        self._build_settings()
        self._show_tab(getattr(self, "_active_tab", "dash") or "dash")
        self._footer()

    def _style_ttk(self):
        t = self.theme
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        # XP uses white fields + navy text like classic forms
        field_bg = t["panel2"]
        field_fg = t["fg"]
        sel_bg = t["accent"] if t.get("xp") else t["elev"]
        sel_fg = "#ffffff" if t.get("xp") else t["fg"]
        st.configure("TCombobox",
                     fieldbackground=field_bg, background=field_bg,
                     foreground=field_fg, arrowcolor=field_fg,
                     bordercolor=t["border"], lightcolor=field_bg,
                     darkcolor=field_bg,
                     font=ui_font(t, 9))
        st.map("TCombobox",
               fieldbackground=[("readonly", field_bg)],
               selectbackground=[("readonly", sel_bg)],
               selectforeground=[("readonly", sel_fg)])

    def _set_app_icon(self):
        """Window + taskbar icon from 4G1 badge artwork."""
        try:
            if os.path.isfile(ICON_PATH):
                self.iconbitmap(ICON_PATH)
        except Exception:
            pass
        # iconphoto gives a sharper taskbar/title icon on modern Tk (PNG)
        for path in (MARK_PATH, LOGO_PATH, SOURCE_LOGO_PATH):
            if not os.path.isfile(path):
                continue
            try:
                img = tk.PhotoImage(file=path)
                if img.height() > 64:
                    factor = max(1, img.height() // 64)
                    img = img.subsample(factor, factor)
                self._app_icon = img
                self.iconphoto(True, self._app_icon)
                break
            except Exception:
                continue

    def _set_titlebar_colour(self):
        """Tint the native Windows title bar to match the active theme (Win11 20H1+).

        winfo_id() returns Tk's inner drawing-surface HWND, not the overlapped
        frame that owns the caption; GA_ROOT resolves the real top-level window.
        """
        if sys.platform != "win32":
            return
        try:
            self.update()
            t = self.theme
            def colorref(hexcolor):
                h = hexcolor.lstrip("#")
                r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
                return r | (g << 8) | (b << 16)
            GA_ROOT = 2
            raw = self.winfo_id()
            hwnd = ctypes.windll.user32.GetAncestor(raw, GA_ROOT) or raw
            caption = ctypes.c_int(colorref(t.get("title", t["panel"])))
            text = ctypes.c_int(colorref(t.get("fg", "#ffffff")))
            DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR = 35, 36
            for attr, val in ((DWMWA_CAPTION_COLOR, caption), (DWMWA_TEXT_COLOR, text)):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
                )
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x20
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
        except Exception:
            logging.info("Custom title bar colour unavailable (requires Windows 11)")

    def _load_logo_images(self, header_bg=None, panel_bg=None):
        """Load circular badges composited onto chrome colours (no black square)."""
        t = self.theme
        if header_bg is None:
            header_bg = t.get("title", t["panel"]) if t.get("xp") else t["panel"]
        if panel_bg is None:
            panel_bg = t["panel"]
        self._mark_img = None
        self._logo_img = None
        # Prefer small mark for header; fall back to full logo artwork
        for path in (MARK_PATH, LOGO_PATH, SOURCE_LOGO_PATH):
            if not os.path.isfile(path):
                continue
            try:
                self._mark_img = _badge_photo(path, header_bg, size=44, master=self)
                break
            except Exception:
                logging.exception("Could not blend header badge from %s", path)
        for path in (LOGO_PATH, SOURCE_LOGO_PATH, MARK_PATH):
            if not os.path.isfile(path):
                continue
            try:
                self._logo_img = _badge_photo(path, panel_bg, size=108, master=self)
                break
            except Exception:
                logging.exception("Could not blend settings logo from %s", path)

    def _draw_fallback_logo(self, canvas, t):
        """Vector circular badge if PNG missing."""
        canvas.create_oval(1, 1, 39, 39, fill="#0e1014", outline=t.get("border", "#333"),
                           width=2)
        canvas.create_arc(5, 5, 35, 35, start=205, extent=-230, style="arc",
                          outline=t["elev"], width=4)
        canvas.create_arc(5, 5, 35, 35, start=205, extent=-95, style="arc",
                          outline=t["accent"], width=4)
        canvas.create_line(20, 22, 31, 10, fill=t["fg"], width=2, capstyle="round")
        canvas.create_oval(17, 19, 23, 25, fill=t["accent"], outline=t["fg"])

    def _header(self):
        t = self.theme
        xp = bool(t.get("xp"))
        luna = bool(t.get("luna") or xp)
        # Luna / XP caption-bar chrome
        if xp:
            bar = t.get("title", "#0A246A")
            light = t.get("title2", "#A6CAF0")
            fg, dim, muted = "#FFFFFF", _blend_hex("#FFFFFF", light, 0.35), light
            live_fg = t.get("brand2", "#FFE45A")
            div = light
            ht = dict(
                t, bg=bar, panel=bar, panel2="#FFFFFF", elev=light,
                fg=fg, dim=dim, muted=muted, border=div,
            )
        else:
            bar = t["panel"]
            light = t["border"]
            fg, dim, muted = t["fg"], t["dim"], t["muted"]
            live_fg = t.get("brand2", t["warn"])
            div = t["border"]
            ht = dict(t, panel=bar)

        wrap = tk.Frame(self, bg=bar, height=80)
        wrap.pack(fill="x")
        wrap.pack_propagate(False)
        if luna:
            # Luna caption gradient: dark → mid → light highlight strip.
            mid = _blend_hex(bar, light, 0.45)
            tk.Frame(wrap, bg=mid, height=3).pack(side="bottom", fill="x")
            tk.Frame(wrap, bg=light, height=2).pack(side="bottom", fill="x")
        elif xp:
            # subtle XP-style bottom highlight under title bar
            tk.Frame(wrap, bg=t.get("title2", "#3A8CF0"), height=2).pack(side="bottom", fill="x")
        else:
            accent_band = tk.Canvas(wrap, bg=bar, highlightthickness=0, height=6)
            accent_band.pack(side="bottom", fill="x")

            def _paint_accent_band(_e=None):
                accent_band.delete("all")
                w = max(accent_band.winfo_width(), 2)
                h = max(accent_band.winfo_height(), 2)
                c0 = t.get("accent_hi", t["accent"])
                c1 = t.get("brand2", t["accent"])
                for x in range(w):
                    frac = x / max(1, w - 1)
                    accent_band.create_line(x, 0, x, h, fill=_blend_hex(c0, c1, frac))
                accent_band.create_line(0, 0, w, 0, fill=t["border"])

            accent_band.bind("<Configure>", _paint_accent_band)
        inner = tk.Frame(wrap, bg=bar)
        inner.pack(fill="both", expand=True, padx=16, pady=10)

        self._load_logo_images(header_bg=bar, panel_bg=t["panel"])
        logo_wrap = tk.Frame(inner, bg=bar)
        logo_wrap.pack(side="left", pady=0)
        if self._mark_img is not None:
            tk.Label(logo_wrap, image=self._mark_img, bg=bar, bd=0,
                     highlightthickness=0).pack()
        else:
            logo = tk.Canvas(logo_wrap, width=40, height=40, bg=bar, highlightthickness=0)
            logo.pack()
            self._draw_fallback_logo(logo, t)

        title = tk.Frame(inner, bg=bar)
        title.pack(side="left", padx=(12, 0))
        row = tk.Frame(title, bg=bar)
        row.pack(anchor="w")
        tk.Label(row, text="4G1", bg=bar, fg=fg,
                 font=ui_font(t, 18, bold=True)).pack(side="left")
        tk.Label(row, text="LIVE", bg=bar, fg=live_fg,
                 font=ui_font(t, 18, bold=True)).pack(side="left", padx=(5, 0))
        self.vehicle_lbl = tk.Label(
            title, text="Live engine data", bg=bar, fg=dim,
            font=ui_font(t, 8),
        )
        self.vehicle_lbl.pack(anchor="w", pady=(1, 0))

        def _meta_block(parent, caption, value_widget_factory):
            block = tk.Frame(parent, bg=bar)
            block.pack(side="left")
            tk.Label(block, text=caption, bg=bar, fg=muted,
                     font=ui_font(t, 7, bold=True)).pack(anchor="w")
            value_widget_factory(block)
            return block

        tk.Frame(inner, bg=div, width=1).pack(side="left", fill="y", padx=16, pady=2)

        def _profile_val(block):
            tk.Label(block, text=self._active_profile()["short"], bg=bar, fg=fg,
                     font=ui_font(t, 10, bold=True)).pack(anchor="w", pady=(4, 0))

        _meta_block(inner, "VEHICLE", _profile_val)

        tk.Frame(inner, bg=div, width=1).pack(side="left", fill="y", padx=16, pady=2)

        def _status_val(block):
            self.status_pill = StatusPill(block, ht, text="●  OFFLINE")
            self.status_pill.pack(anchor="w", pady=(4, 0))

        _meta_block(inner, "STATUS", _status_val)

        tk.Frame(inner, bg=div, width=1).pack(side="left", fill="y", padx=16, pady=2)

        def _adapter_val(block):
            self.dev_info = tk.Label(block, text="—", bg=bar, fg=dim,
                                     font=ui_font(t, 8, mono=True))
            self.dev_info.pack(anchor="w", pady=(4, 0))

        _meta_block(inner, "ADAPTER", _adapter_val)

        actions = tk.Frame(inner, bg=bar)
        actions.pack(side="right")

        self.clock_lbl = tk.Label(actions, text="", bg=bar, fg=muted,
                                  font=ui_font(t, 9, mono=True))
        self.clock_lbl.pack(side="left", padx=(0, 12))

        self.connect_btn = SoftButton(actions, "Connect", self.on_connect, ht, primary=False)
        self.connect_btn.pack(side="left")
        action_theme = dict(ht)
        action_theme["accent"] = t["warn"] if not xp else t["accent"]
        action_theme["accent_hi"] = "#FF9A3C" if not xp else t["accent_hi"]
        self.live_btn = SoftButton(actions, "▶  Start Live", self.toggle_live,
                                   action_theme, primary=True)
        self.live_btn.config(state="disabled")
        self.live_btn.pack(side="left", padx=(8, 0))

        self.demo_btn = SoftButton(actions, "Demo", self.toggle_demo, ht,
                                   primary=False)
        self.demo_btn.pack(side="left", padx=(8, 0))

        about = tk.Label(actions, text=f"v{VERSION}", bg=bar, fg=muted,
                         font=ui_font(t, 8), cursor="hand2")
        about.pack(side="left", padx=(12, 0))
        about.bind("<Button-1>", lambda _e: self._show_about())

    def _toolbar(self):
        # Connection actions now live in the compact product header.
        pass

    def _tabbar(self):
        t = self.theme
        bar = tk.Frame(self, bg=t["panel"], height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._tab_bar = bar
        self._tab_btns = {}
        self._tab_frames = {}
        pad = tk.Frame(bar, bg=t["panel"], width=8)
        pad.pack(side="left")
        for key, label in (("dash", "Dashboard"), ("codes", "Codes"), ("settings", "Settings")):
            cell = tk.Frame(bar, bg=t["panel"])
            cell.pack(side="left", fill="y", padx=(0, 2))
            b = tk.Label(cell, text=f"  {label}  ", bg=t["panel"], fg=t["dim"],
                         font=ui_font(t, 9, bold=True), pady=12, cursor="hand2")
            b.pack(side="top", fill="x")
            underline = tk.Frame(cell, bg=t["panel"], height=3)
            underline.pack(side="bottom", fill="x")
            b.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            b.bind("<Enter>", lambda e, k=key: self._tab_hover(k, True))
            b.bind("<Leave>", lambda e, k=key: self._tab_hover(k, False))
            self._tab_btns[key] = b
            self._tab_frames[key] = (cell, underline)
        tk.Frame(self, bg=t["border"], height=1).pack(fill="x")

    def _tab_hover(self, key, entering):
        if key == self._active_tab:
            return
        t = self.theme
        b = self._tab_btns.get(key)
        if b is None:
            return
        b.config(fg=t["fg"] if entering else t["dim"])

    def _show_tab(self, key):
        self._active_tab = key
        t = self.theme
        for k, fr in (("dash", self.tab_dash), ("codes", self.tab_codes),
                      ("settings", self.tab_settings)):
            fr.pack_forget()
        {"dash": self.tab_dash, "codes": self.tab_codes,
         "settings": self.tab_settings}[key].pack(fill="both", expand=True)

        for k, b in self._tab_btns.items():
            cell, underline = self._tab_frames[k]
            if k == key:
                b.config(fg=t["accent"], bg=t.get("tab_sel", t["panel2"]))
                underline.config(bg=t["accent"])
            else:
                b.config(fg=t["dim"], bg=t["panel"])
                underline.config(bg=t["panel"])

    def _footer(self):
        t = self.theme
        foot = tk.Frame(self, bg=t["panel"])
        foot.pack(fill="x", side="bottom")
        tk.Frame(foot, bg=t["border"], height=1).pack(fill="x")
        inner = tk.Frame(foot, bg=t["panel"])
        inner.pack(fill="x", padx=14, pady=6)
        self.foot_left = tk.Label(inner, text=self._conn_summary(),
                                  bg=t["panel"], fg=t["muted"], font=ui_font(t, 8))
        self.foot_left.pack(side="left")
        self.foot_right = tk.Label(inner, text="Ready", bg=t["panel"], fg=t["dim"],
                                   font=ui_font(t, 8, mono=True))
        self.foot_right.pack(side="right")

    def _conn_summary(self):
        s = self.settings
        dll = os.path.basename(s.get("dll_path") or "op20pt32.dll")
        prof = j2534.get_profile(s.get("vehicle_profile"))["short"]
        return f"{prof}  ·  {dll}"

    def _show_about(self):
        t = self.theme
        win = tk.Toplevel(self, bg=t["bg"])
        win.title(f"About {APP_NAME}")
        win.resizable(False, False)
        win.transient(self)
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass

        card_outer, panel = card(win, t)
        card_outer.pack(fill="both", expand=True, padx=14, pady=14)

        head = tk.Frame(panel, bg=t["panel"])
        head.pack(fill="x", padx=18, pady=(16, 6))
        if self._mark_img is not None:
            tk.Label(head, image=self._mark_img, bg=t["panel"], bd=0,
                     highlightthickness=0).pack(side="left")
        title_col = tk.Frame(head, bg=t["panel"])
        title_col.pack(side="left", padx=(10, 0))
        row = tk.Frame(title_col, bg=t["panel"])
        row.pack(anchor="w")
        tk.Label(row, text="4G1", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 15, bold=True)).pack(side="left")
        tk.Label(row, text="LIVE", bg=t["panel"], fg=t.get("brand2", t["warn"]),
                 font=ui_font(t, 15, bold=True)).pack(side="left", padx=(4, 0))
        tk.Label(title_col, text=f"v{VERSION}", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8, mono=True)).pack(anchor="w")

        body = tk.Frame(panel, bg=t["panel"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        for line in (
            "Live engine data for Mitsubishi CE Lancer / Mirage.",
            "",
            COPYRIGHT, "",
            "Runs on this PC only — nothing is uploaded.",
            "Demo mode does not talk to a car.", "",
            "For diagnostic help only. Confirm repairs with factory info.",
        ):
            tk.Label(body, text=line or " ", bg=t["panel"], fg=t["dim"],
                     font=ui_font(t, 9), anchor="w", justify="left").pack(fill="x")

        paths = tk.Frame(panel, bg=t["panel"])
        paths.pack(fill="x", padx=18, pady=(2, 4))
        tk.Label(paths, text=f"Settings: {SETTINGS_PATH}", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7, mono=True), anchor="w").pack(fill="x")
        tk.Label(paths, text=f"Logs: {LOG_DIR}", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7, mono=True), anchor="w").pack(fill="x")

        btn_row = tk.Frame(panel, bg=t["panel"])
        btn_row.pack(fill="x", padx=18, pady=(6, 16))
        SoftButton(btn_row, "Close", win.destroy, t, primary=True).pack(side="right")

        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.grab_set()
        win.focus_set()

    def _active_profile(self):
        return j2534.get_profile(self.settings.get("vehicle_profile"))

    # ---------- dashboard ----------
    def _build_dash(self):
        t = self.theme
        self.heroes = {}

        # Friendly empty-state banner
        self.idle_banner = tk.Frame(
            self.tab_dash, bg=t["panel2"],
            highlightbackground=t["accent"], highlightthickness=1,
        )
        ib = tk.Frame(self.idle_banner, bg=t["panel2"])
        ib.pack(fill="x", padx=16, pady=10)
        accent_dot = tk.Canvas(ib, width=10, height=10, bg=t["panel2"], highlightthickness=0)
        accent_dot.pack(side="left", padx=(0, 10))
        accent_dot.create_oval(1, 1, 9, 9, fill=t["accent"], outline="")
        text_col = tk.Frame(ib, bg=t["panel2"])
        text_col.pack(side="left", fill="x", expand=True)
        tk.Label(text_col, text="No live data yet", bg=t["panel2"], fg=t["fg"],
                 font=ui_font(t, 10, bold=True), anchor="w").pack(anchor="w")
        tk.Label(
            text_col,
            text="Connect the adapter, or try Demo to preview the gauges.",
            bg=t["panel2"], fg=t["dim"], font=ui_font(t, 9), anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        primary = tk.Frame(self.tab_dash, bg=t["bg"], height=252)
        primary.pack(fill="x", pady=(8, 8))
        primary.pack_propagate(False)
        self._dash_primary = primary

        rpm_outer, rpm_panel = card(primary, t)
        rpm_outer.pack(side="left", fill="both", expand=True, padx=(0, 8))
        rpm_head = tk.Frame(rpm_panel, bg=t["panel"])
        rpm_head.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(rpm_head, text="Engine speed", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9, bold=True)).pack(side="left")
        tk.Label(rpm_head, text="RPM", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7, bold=True)).pack(side="right")
        rpm_body = tk.Frame(rpm_panel, bg=t["panel"])
        rpm_body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        rpm_tile = HeroTile(rpm_body, 0x21, t, width=156)
        rpm_tile.pack(side="left", fill="y", padx=(0, 8))
        rpm_tile.pack_propagate(False)
        self.heroes[0x21] = rpm_tile
        self.chart_rpm = StripChart(rpm_body, t, "RPM trend", "rpm",
                                    _param(0x21)[3], accent=t["accent"])
        self.chart_rpm.pack(side="left", fill="both", expand=True)

        critical = tk.Frame(primary, bg=t["bg"])
        critical.pack(side="left", fill="both", expand=True)
        for i, pid in enumerate([0x06, 0x07, 0x3A, 0x17, 0x14, 0x2F]):
            if pid not in j2534.ALL_PARAMS:
                continue
            tile = HeroTile(critical, pid, t)
            tile.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            self.heroes[pid] = tile
        for c in range(3):
            critical.grid_columnconfigure(c, weight=1, uniform="critical")
        for r in range(2):
            critical.grid_rowconfigure(r, weight=1, uniform="critical")

        self.grid_frame = tk.Frame(self.tab_dash, bg=t["bg"], height=112)
        self.grid_frame.pack(fill="x", pady=(0, 8))
        self.grid_frame.pack_propagate(False)
        self._build_gauges()

        lower = tk.Frame(self.tab_dash, bg=t["bg"], height=112)
        lower.pack(fill="x", pady=(0, 8))
        lower.pack_propagate(False)
        self.chart_load = StripChart(lower, t, "Engine Load", "%",
                                     _param(0x1C)[3], accent=t["good"])
        self.chart_load.pack(side="left", fill="both", expand=True, padx=(0, 8))
        health_outer, health = card(lower, t, accent=t.get("good"))
        health_outer.pack(side="left", fill="both", expand=True)
        tk.Label(health, text="Session", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=14, pady=(10, 6))
        metrics = tk.Frame(health, bg=t["panel"])
        metrics.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def _metric_cell(parent, caption, attr_name, default):
            cell = tk.Frame(parent, bg=t["elev"],
                            highlightbackground=t["border"], highlightthickness=1)
            cell.pack(side="left", fill="both", expand=True, padx=4)
            tk.Label(cell, text=caption, bg=t["elev"], fg=t["muted"],
                     font=ui_font(t, 7, bold=True)).pack(anchor="w", padx=10, pady=(8, 0))
            lbl = tk.Label(cell, text=default, bg=t["elev"], fg=t["fg"],
                           font=ui_font(t, 13, bold=True, mono=True))
            lbl.pack(anchor="w", padx=10, pady=(2, 8))
            setattr(self, attr_name, lbl)

        _metric_cell(metrics, "UPDATE RATE", "hz_lbl", "— Hz")
        _metric_cell(metrics, "SAMPLES", "n_lbl", "0")
        _metric_cell(metrics, "TIME", "sess_lbl", "—")

        logwrap_outer, logwrap = card(self.tab_dash, t)
        logwrap_outer.pack(fill="both", expand=True, pady=(0, 2))
        bar = tk.Frame(logwrap, bg=t["panel"])
        bar.pack(fill="x")
        tk.Label(bar, text="Log", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(side="left", padx=14, pady=8)
        save = SoftButton(bar, "Save CSV", self.save_csv, t, primary=False, compact=True)
        save.pack(side="right", padx=(0, 10), pady=6)
        clr = SoftButton(bar, "Clear", self.clear_log, t, primary=False, compact=True)
        clr.pack(side="right", padx=(0, 6), pady=6)
        self.logbox = tk.Text(
            logwrap, bg=t["chart"], fg=t["accent2"], relief="flat",
            font=ui_font(t, 8, mono=True), height=3, wrap="none",
            insertbackground=t["fg"], selectbackground=t["panel2"], padx=10, pady=6,
            borderwidth=0, highlightthickness=0,
        )
        self.logbox.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._update_idle_banner()

    def _update_idle_banner(self):
        """Show a friendly call-to-action instead of blank dashes before data flows."""
        banner = getattr(self, "idle_banner", None)
        if banner is None:
            return
        if self.live:
            banner.pack_forget()
        else:
            primary = getattr(self, "_dash_primary", None)
            if primary is not None and primary.winfo_exists():
                banner.pack(fill="x", pady=(8, 4), before=primary)
            else:
                banner.pack(fill="x", pady=(8, 4))

    def _build_gauges(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.gauges = {}
        ids = [int(g, 16) if isinstance(g, str) else g for g in self.settings["gauges"]]
        ids = [i for i in ids if i in j2534.ALL_PARAMS and i not in self.heroes]
        cols = max(1, min(6, len(ids)))
        style = self.settings.get("style", "Digital")
        h = 108
        for i, pid in enumerate(ids):
            g = Gauge(self.grid_frame, pid, self.theme, style, width=220, height=h)
            g.grid(row=i // cols, column=i % cols, padx=4, pady=0, sticky="nsew")
            g.grid_propagate(False)
            self.gauges[pid] = g
        for c in range(cols):
            self.grid_frame.grid_columnconfigure(c, weight=1)
        rows = max(1, (len(ids) + cols - 1) // cols)
        for r in range(rows):
            self.grid_frame.grid_rowconfigure(r, weight=1)

    # ---------- codes ----------
    def _build_codes(self):
        t = self.theme
        wrap = tk.Frame(self.tab_codes, bg=t["bg"])
        wrap.pack(fill="both", expand=True, pady=6)

        codes_outer, codes_card = card(wrap, t)
        codes_outer.pack(side="left", fill="both", expand=True, padx=(0, 8))

        head = tk.Frame(codes_card, bg=t["panel"])
        head.pack(fill="x", padx=20, pady=(16, 6))
        left = tk.Frame(head, bg=t["panel"])
        left.pack(side="left")
        tk.Label(left, text="Trouble codes", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 16, bold=True)).pack(anchor="w")
        tk.Label(left, text="Read or clear stored fault codes from the engine computer.",
                 bg=t["panel"], fg=t["muted"], font=ui_font(t, 9)).pack(anchor="w", pady=(2, 0))

        bar = tk.Frame(codes_card, bg=t["panel"])
        bar.pack(anchor="w", padx=20, pady=(10, 12))
        SoftButton(bar, "Read Codes", self.read_codes, t, primary=True).pack(side="left")
        SoftButton(bar, "Clear Codes", self.clear_codes, t, primary=False).pack(side="left", padx=8)

        self.codes_box = tk.Text(
            codes_card, bg=t["chart"], fg=t["fg"], relief="flat",
            font=ui_font(t, 11, mono=True), height=16, insertbackground=t["fg"],
            selectbackground=t["panel2"], padx=18, pady=16,
            borderwidth=0, highlightthickness=0,
        )
        self.codes_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.codes_box.insert("end", "Connect first, then press Read Codes.\n\n")
        self.codes_box.insert("end", "Common examples:\n")
        self.codes_box.insert("end", "  #11  Oxygen sensor\n")
        self.codes_box.insert("end", "  #21  Coolant temperature sensor\n")
        self.codes_box.insert("end", "  #22  Crankshaft position sensor\n")

        ref_outer, ref_card = card(wrap, t, accent=t.get("accent2"))
        ref_outer.pack(side="left", fill="y")
        ref_outer.configure(width=270)
        ref_outer.pack_propagate(False)
        tk.Label(ref_card, text="CODE LIST", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(ref_card, text="What each code number means", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(anchor="w", padx=16, pady=(0, 10))
        ref_list = tk.Frame(ref_card, bg=t["panel"])
        ref_list.pack(fill="both", expand=True, padx=12, pady=(0, 14))
        for i, (code, _bit, desc) in enumerate(j2534.DTC_TABLE):
            row_bg = t["elev"] if i % 2 == 0 else t["panel"]
            row = tk.Frame(ref_list, bg=row_bg)
            row.pack(fill="x", pady=0)
            tk.Label(row, text=f"#{code:02d}", bg=row_bg, fg=t["accent"],
                     font=ui_font(t, 8, bold=True, mono=True), width=4,
                     anchor="w", padx=4, pady=3).pack(side="left")
            tk.Label(row, text=desc, bg=row_bg, fg=t["dim"],
                     font=ui_font(t, 8), anchor="w", pady=3).pack(side="left", fill="x")

    # ---------- settings ----------
    def _settings_row(self, parent, label, widget_factory, hint=None):
        t = self.theme
        row = tk.Frame(parent, bg=t["panel"])
        row.pack(fill="x", padx=16, pady=4)
        tk.Label(row, text=label, bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        w = widget_factory(row)
        if hint:
            tk.Label(row, text=f"  {hint}", bg=t["panel"], fg=t["muted"],
                     font=ui_font(t, 8)).pack(side="left")
        return w

    def _build_settings(self):
        t = self.theme
        # scrollable canvas so connection + gauges fit
        canvas = tk.Canvas(self.tab_settings, bg=t["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(self.tab_settings, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=t["bg"])
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        inner_window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(inner_window, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        def _bind_wheel(_e=None):
            canvas.bind_all("<MouseWheel>", _wheel)

        def _unbind_wheel(_e=None):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        inner.bind("<Enter>", _bind_wheel)
        self._settings_canvas = canvas

        # ---- logo preview + vehicle ----
        top = tk.Frame(inner, bg=t["bg"])
        top.pack(fill="x", pady=(10, 0), padx=2)

        brand = tk.Frame(top, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1, width=200)
        brand.pack(side="left", fill="y", padx=(0, 6))
        brand.pack_propagate(False)
        tk.Frame(brand, bg=t["accent"], height=3).pack(fill="x")
        tk.Label(brand, text="4G1 LIVE", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=12, pady=(10, 4))
        self._settings_logo = None
        if self._logo_img is not None:
            self._settings_logo = self._logo_img
        elif self._mark_img is not None:
            self._settings_logo = self._mark_img
        else:
            for path in (LOGO_PATH, MARK_PATH, SOURCE_LOGO_PATH):
                if not os.path.isfile(path):
                    continue
                try:
                    self._settings_logo = _badge_photo(path, t["panel"], size=108, master=self)
                    break
                except Exception:
                    continue
        if self._settings_logo is not None:
            tk.Label(brand, image=self._settings_logo, bg=t["panel"], bd=0,
                     highlightthickness=0).pack(pady=8)
        tk.Label(brand, text="4G1 Live", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 12, bold=True)).pack()
        tk.Label(brand, text="Mitsubishi live data", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(pady=(0, 12))

        # appearance
        acard = tk.Frame(top, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1)
        acard.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Frame(acard, bg=t["accent"], height=3).pack(fill="x")
        tk.Label(acard, text="APPEARANCE", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=16, pady=(12, 8))

        self.theme_var = tk.StringVar(value=self.settings["theme"])
        # Visual theme swatches — click applies immediately
        swatches = tk.Frame(acard, bg=t["panel"])
        swatches.pack(fill="x", padx=16, pady=(0, 4))
        self._theme_swatch_frames = {}
        for name, palette in THEMES.items():
            cell = tk.Frame(swatches, bg=t["panel"])
            cell.pack(side="left", padx=(0, 6))
            chip = tk.Frame(
                cell, bg=palette["bg"], width=34, height=22,
                highlightthickness=2,
                highlightbackground=(
                    palette["accent"] if name == self.theme_var.get() else t["border"]
                ),
                cursor="hand2",
            )
            chip.pack()
            chip.pack_propagate(False)
            # Mini accent bar inside the swatch
            tk.Frame(chip, bg=palette["accent"], height=4).pack(fill="x", side="bottom")
            chip.bind("<Button-1>", lambda e, n=name: self._pick_theme_swatch(n))
            for child in chip.winfo_children():
                child.bind("<Button-1>", lambda e, n=name: self._pick_theme_swatch(n))
            self._theme_swatch_frames[name] = chip
        tk.Label(
            acard, text="Click a colour to switch themes.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        ).pack(anchor="w", padx=16, pady=(0, 8))

        sw = tk.Frame(acard, bg=t["panel"])
        sw.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(sw, text="Theme", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=16, anchor="w").pack(side="left")
        theme_cb = ttk.Combobox(sw, textvariable=self.theme_var, values=list(THEMES),
                                state="readonly", width=24)
        theme_cb.pack(side="left")
        # Defer so Combobox finishes writing StringVar before we rebuild UI
        theme_cb.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.after_idle(self._on_theme_combobox),
        )

        style_row = tk.Frame(acard, bg=t["panel"])
        style_row.pack(fill="x", padx=16, pady=4)
        tk.Label(style_row, text="Gauge style", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=16, anchor="w").pack(side="left")
        self.style_var = tk.StringVar(value=self.settings.get("style", "Digital"))
        style_cb = ttk.Combobox(style_row, textvariable=self.style_var, values=GAUGE_STYLES,
                                state="readonly", width=24)
        style_cb.pack(side="left")
        style_cb.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.after_idle(self._on_style_combobox),
        )

        row3 = tk.Frame(acard, bg=t["panel"])
        row3.pack(fill="x", padx=16, pady=(6, 16))
        tk.Label(row3, text="Poll rate", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=12, anchor="w").pack(side="left")
        self.poll_var = tk.StringVar(value=str(self.settings.get("poll_hz", 12)))
        ttk.Combobox(row3, textvariable=self.poll_var,
                     values=["6", "8", "10", "12", "15", "20"],
                     state="readonly", width=18).pack(side="left")
        tk.Label(row3, text="  updates / second", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(side="left")

        # vehicle card — selectable profiles (4G15 12V primary, 4G93 MAF secondary)
        vcard = tk.Frame(top, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1, width=260)
        vcard.pack(side="left", fill="both")
        vcard.pack_propagate(False)
        tk.Frame(vcard, bg=t["accent"], height=3).pack(fill="x")
        tk.Label(vcard, text="VEHICLE", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=14, pady=(12, 6))

        choices = j2534.profile_choices()
        name_by_id = {pid: name for pid, name in choices}
        id_by_name = {name: pid for pid, name in choices}
        cur = j2534.get_profile(self.settings.get("vehicle_profile"))
        self.profile_var = tk.StringVar(value=cur["name"])
        ttk.Combobox(
            vcard, textvariable=self.profile_var,
            values=[name for _, name in choices],
            state="readonly", width=22,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self._profile_detail = tk.Frame(vcard, bg=t["panel"])
        self._profile_detail.pack(fill="both", expand=True, padx=14, pady=(0, 4))
        self._profile_id_by_name = id_by_name
        self._profile_name_by_id = name_by_id

        def _fill_profile_detail(profile_id=None):
            for w in self._profile_detail.winfo_children():
                w.destroy()
            p = j2534.get_profile(profile_id)
            for line in (
                p["chassis"],
                f"Engine  {p['engine']}",
                f"Airflow {p['airflow']}",
                f"Boost   {p['boost']}",
            ):
                tk.Label(
                    self._profile_detail, text=line, bg=t["panel"], fg=t["fg"],
                    font=ui_font(t, 8), anchor="w", wraplength=220, justify="left",
                ).pack(fill="x", pady=1)

        def _on_profile_pick(_e=None):
            pid = self._profile_id_by_name.get(self.profile_var.get(), j2534.DEFAULT_PROFILE_ID)
            _fill_profile_detail(pid)

        self.profile_var.trace_add("write", lambda *_: _on_profile_pick())
        _fill_profile_detail(cur["id"])

        tk.Label(
            vcard, text="Pick the engine that matches your car.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
            anchor="w", justify="left",
        ).pack(fill="x", padx=14, pady=(4, 12))

        # ---- CONNECTION ----
        ccard = tk.Frame(inner, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1)
        ccard.pack(fill="x", pady=(12, 0), padx=2)
        tk.Frame(ccard, bg=t["accent"], height=3).pack(fill="x")
        head = tk.Frame(ccard, bg=t["panel"])
        head.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(head, text="ADAPTER", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(side="left")
        tk.Label(head, text="Cable & connection options", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="right")

        advanced_toggle = tk.Frame(ccard, bg=t["panel"])
        advanced_toggle.pack(fill="x", padx=16, pady=(4, 8))
        advanced = tk.Frame(ccard, bg=t["panel"])
        advanced.pack(fill="x")
        self._advanced_connection_open = False

        def _toggle_advanced():
            self._advanced_connection_open = not self._advanced_connection_open
            if self._advanced_connection_open:
                advanced.pack(fill="x", after=advanced_toggle)
                advanced_button.config(text="▾  Advanced settings")
            else:
                advanced.pack_forget()
                advanced_button.config(text="▸  Advanced settings")

        advanced_button = tk.Label(
            advanced_toggle, text="▸  Advanced settings",
            bg=t["panel2"], fg=t["dim"], font=ui_font(t, 9, bold=True),
            cursor="hand2", padx=10, pady=6,
        )
        advanced_button.pack(side="left")
        advanced_button.bind("<Button-1>", lambda _e: _toggle_advanced())
        tk.Label(advanced_toggle, text="Defaults work for most CE cars.",
                 bg=t["panel"], fg=t["muted"], font=ui_font(t, 8)).pack(
                     side="left", padx=10)

        # DLL path
        dll_row = tk.Frame(advanced, bg=t["panel"])
        dll_row.pack(fill="x", padx=16, pady=4)
        tk.Label(dll_row, text="Driver file", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.dll_var = tk.StringVar(value=self.settings.get("dll_path") or j2534.default_dll())
        dll_entry = tk.Entry(dll_row, textvariable=self.dll_var, bg=t["elev"], fg=t["fg"],
                             insertbackground=t["fg"], relief="flat", font=ui_font(t, 9, mono=True),
                             width=48)
        dll_entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        tk.Label(dll_row, text=" Browse ", bg=t["panel2"], fg=t["fg"],
                 font=ui_font(t, 8, bold=True), cursor="hand2", pady=4).pack(side="left", padx=2)
        dll_row.winfo_children()[-1].bind("<Button-1>", lambda e: self._browse_dll())
        tk.Label(dll_row, text=" Detect ", bg=t["panel2"], fg=t["fg"],
                 font=ui_font(t, 8, bold=True), cursor="hand2", pady=4).pack(side="left")
        dll_row.winfo_children()[-1].bind("<Button-1>", lambda e: self._detect_dll())

        # baud
        baud_row = tk.Frame(advanced, bg=t["panel"])
        baud_row.pack(fill="x", padx=16, pady=4)
        tk.Label(baud_row, text="Speed (baud)", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.baud_var = tk.StringVar(value=str(self.settings.get("mut_baud", 15625)))
        ttk.Combobox(baud_row, textvariable=self.baud_var, values=BAUD_PRESETS,
                     width=12).pack(side="left")
        tk.Label(baud_row, text="  usually 15625", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        # timeout + init attempts
        to_row = tk.Frame(advanced, bg=t["panel"])
        to_row.pack(fill="x", padx=16, pady=4)
        tk.Label(to_row, text="Timeout", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.timeout_var = tk.StringVar(value=str(self.settings.get("request_timeout_ms", 400)))
        ttk.Combobox(to_row, textvariable=self.timeout_var,
                     values=["200", "300", "400", "500", "750", "1000"],
                     width=12).pack(side="left")
        tk.Label(to_row, text="  ms per request", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        init_row = tk.Frame(advanced, bg=t["panel"])
        init_row.pack(fill="x", padx=16, pady=4)
        tk.Label(init_row, text="Init address", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.init_addr_var = tk.StringVar(value=str(self.settings.get("init_addr", "0x00")))
        ttk.Combobox(init_row, textvariable=self.init_addr_var,
                     values=["0x00", "0x33", "0x11"], width=12).pack(side="left")
        tk.Label(init_row, text="  Retries", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9)).pack(side="left", padx=(16, 4))
        self.init_att_var = tk.StringVar(value=str(self.settings.get("init_attempts", 5)))
        ttk.Combobox(init_row, textvariable=self.init_att_var,
                     values=["3", "5", "8", "10"], width=6).pack(side="left")

        # checkboxes
        opt = tk.Frame(ccard, bg=t["panel"])
        opt.pack(fill="x", padx=16, pady=(8, 4))
        self.pin1_var = tk.BooleanVar(value=bool(self.settings.get("pin1_ground", True)))
        self.auto_conn_var = tk.BooleanVar(value=bool(self.settings.get("auto_connect", False)))
        self.auto_live_var = tk.BooleanVar(value=bool(self.settings.get("auto_live", False)))
        for var, text in (
            (self.pin1_var, "Ground OBD pin 1 (needed for most CE Lancer / Mirage)"),
            (self.auto_conn_var, "Connect automatically when the app starts"),
            (self.auto_live_var, "Start live data after connect"),
        ):
            tk.Checkbutton(
                opt, text=text, variable=var,
                bg=t["panel"], fg=t["fg"], selectcolor=t["elev"],
                activebackground=t["panel"], activeforeground=t["fg"],
                font=ui_font(t, 9), anchor="w",
            ).pack(anchor="w", pady=1)

        tip = tk.Label(
            ccard,
            text="Leave pin 1 grounded for CE cars. Changes apply on the next Connect.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8), wraplength=900, justify="left",
        )
        tip.pack(anchor="w", padx=16, pady=(4, 14))
        advanced.pack_forget()

        # ---- gauges ----
        gcard = tk.Frame(inner, bg=t["panel"],
                         highlightbackground=t["border"], highlightthickness=1)
        gcard.pack(fill="both", expand=True, pady=(12, 0), padx=2)
        tk.Frame(gcard, bg=t["accent"], height=3).pack(fill="x")
        prof = self._active_profile()
        tk.Label(
            gcard, text=f"Gauges  ·  {prof['short']}",
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8, bold=True),
        ).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(
            gcard,
            text="Tick which readings to show. Green / amber / red lights mean sensor health.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        ).pack(anchor="w", padx=16, pady=(0, 4))
        legend = tk.Frame(gcard, bg=t["panel"])
        legend.pack(anchor="w", padx=16, pady=(0, 8))
        for col, lab in (
            (t["good"], "● green = good"),
            (t["warn"], "● amber = check"),
            (t["bad"], "● red = problem"),
        ):
            tk.Label(legend, text=lab, bg=t["panel"], fg=col,
                     font=ui_font(t, 8)).pack(side="left", padx=(0, 14))

        grid = tk.Frame(gcard, bg=t["panel"])
        grid.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.gauge_vars = {}
        active = set(int(g, 16) if isinstance(g, str) else g for g in self.settings["gauges"])
        items = list(j2534.ALL_PARAMS.items())
        for i, (pid, meta) in enumerate(items):
            name, units = meta[0], meta[2]
            v = tk.BooleanVar(value=(pid in active))
            self.gauge_vars[pid] = v
            cell = tk.Frame(grid, bg=t["panel"])
            cell.grid(row=i // 4, column=i % 4, sticky="ew", padx=6, pady=3)
            led_mark = " ●" if pid in j2534.HEALTH_PIDS else ""
            cb = tk.Checkbutton(
                cell, text=f"{name}  ({units}){led_mark}", variable=v,
                bg=t["panel"], fg=t["fg"], selectcolor=t["elev"],
                activebackground=t["panel"], activeforeground=t["fg"],
                font=ui_font(t, 9), anchor="w",
            )
            cb.pack(anchor="w")
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1, uniform="parameter")

        act = tk.Frame(inner, bg=t["bg"])
        act.pack(fill="x", pady=12, padx=2)
        SoftButton(act, "Apply Settings", self.apply_settings, t, primary=True).pack(side="left")
        SoftButton(act, "Reset Connection Defaults", self._reset_conn_defaults, t,
                   primary=False).pack(side="left", padx=8)

    def _browse_dll(self):
        path = filedialog.askopenfilename(
            title="Select adapter driver",
            filetypes=[("DLL files", "*.dll"), ("All files", "*.*")],
            initialdir=r"C:\Windows\SysWOW64",
        )
        if path:
            self.dll_var.set(path)

    def _detect_dll(self):
        found = j2534.find_j2534_dlls()
        if not found:
            messagebox.showwarning("4G1 Live", "No adapter driver found.\nBrowse for it manually.")
            return
        self.dll_var.set(found[0])
        if len(found) > 1:
            messagebox.showinfo(
                "4G1 Live",
                "Found:\n" + "\n".join(found) + f"\n\nUsing:\n{found[0]}",
            )

    def _reset_conn_defaults(self):
        self.dll_var.set(j2534.default_dll())
        self.baud_var.set("15625")
        self.timeout_var.set("400")
        self.init_addr_var.set("0x00")
        self.init_att_var.set("5")
        self.pin1_var.set(True)
        self.auto_conn_var.set(False)
        self.auto_live_var.set(False)

    def _pick_theme(self, name):
        self._apply_theme_now(name)

    def _pick_theme_swatch(self, name):
        """Click a colour swatch in Settings → Appearance — applies immediately."""
        self._apply_theme_now(name)

    def _on_theme_combobox(self):
        """Dropdown theme pick — applies immediately."""
        try:
            name = self.theme_var.get()
        except Exception:
            return
        self._apply_theme_now(name)

    def _on_style_combobox(self):
        """Gauge style pick — apply UI rebuild so the change is visible right away."""
        try:
            style = self.style_var.get()
        except Exception:
            return
        if style not in GAUGE_STYLES:
            return
        self._read_settings_from_ui()
        self.settings["style"] = style
        save_settings(self.settings)
        self._rebuild_after_settings()

    def _highlight_theme_swatch(self, name):
        frames = getattr(self, "_theme_swatch_frames", {})
        for n, chip in frames.items():
            try:
                palette = THEMES.get(n, self.theme)
                chip.configure(
                    highlightbackground=palette["accent"] if n == name else self.theme["border"],
                    highlightthickness=2,
                )
            except Exception:
                pass

    def _apply_theme_now(self, name):
        """Switch theme, save, and rebuild the whole chrome so the change is obvious."""
        name = (name or "").strip()
        if name not in THEMES:
            logging.warning("Unknown theme requested: %r", name)
            return
        # Skip no-op rebuilds (same theme already active)
        if self.settings.get("theme") == name and self.theme is THEMES[name]:
            try:
                self.theme_var.set(name)
            except Exception:
                pass
            self._highlight_theme_swatch(name)
            return
        # Capture current form values before widgets are destroyed
        self._read_settings_from_ui()
        self.settings["theme"] = name
        self.theme = THEMES[name]
        save_settings(self.settings)
        logging.info("Theme applied: %s", name)
        self._rebuild_after_settings()

    def _parse_init_addr(self, text):
        text = (text or "0x00").strip().lower()
        try:
            return int(text, 0)
        except Exception:
            return 0

    def _read_settings_from_ui(self):
        """Copy live Settings-form vars into self.settings (no rebuild)."""
        if hasattr(self, "theme_var"):
            try:
                name = self.theme_var.get()
                if name in THEMES:
                    self.settings["theme"] = name
            except Exception:
                pass
        if hasattr(self, "style_var"):
            try:
                self.settings["style"] = self.style_var.get()
            except Exception:
                pass
        if hasattr(self, "poll_var"):
            try:
                self.settings["poll_hz"] = int(self.poll_var.get())
            except Exception:
                self.settings["poll_hz"] = 12
        if hasattr(self, "profile_var") and hasattr(self, "_profile_id_by_name"):
            try:
                pid = self._profile_id_by_name.get(
                    self.profile_var.get(), j2534.DEFAULT_PROFILE_ID
                )
                self.settings["vehicle_profile"] = pid
            except Exception:
                pass
        if hasattr(self, "dll_var"):
            try:
                self.settings["dll_path"] = self.dll_var.get().strip()
            except Exception:
                pass
        if hasattr(self, "baud_var"):
            try:
                self.settings["mut_baud"] = int(self.baud_var.get())
            except Exception:
                self.settings["mut_baud"] = 15625
        if hasattr(self, "timeout_var"):
            try:
                self.settings["request_timeout_ms"] = int(self.timeout_var.get())
            except Exception:
                self.settings["request_timeout_ms"] = 400
        if hasattr(self, "init_addr_var"):
            try:
                self.settings["init_addr"] = self.init_addr_var.get().strip() or "0x00"
            except Exception:
                pass
        if hasattr(self, "init_att_var"):
            try:
                self.settings["init_attempts"] = int(self.init_att_var.get())
            except Exception:
                self.settings["init_attempts"] = 5
        if hasattr(self, "pin1_var"):
            try:
                self.settings["pin1_ground"] = bool(self.pin1_var.get())
            except Exception:
                pass
        if hasattr(self, "auto_conn_var"):
            try:
                self.settings["auto_connect"] = bool(self.auto_conn_var.get())
            except Exception:
                pass
        if hasattr(self, "auto_live_var"):
            try:
                self.settings["auto_live"] = bool(self.auto_live_var.get())
            except Exception:
                pass
        if hasattr(self, "gauge_vars") and self.gauge_vars:
            try:
                chosen = [hex(pid) for pid, v in self.gauge_vars.items() if v.get()]
                if chosen:
                    self.settings["gauges"] = chosen
            except Exception:
                pass
        if self.dev:
            try:
                self.dev.request_timeout_ms = self.settings["request_timeout_ms"]
                self.dev.baud = self.settings["mut_baud"]
            except Exception:
                pass

    def _rebuild_after_settings(self):
        """Rebuild chrome after a settings change; keep tab + link state."""
        tab = getattr(self, "_active_tab", "settings") or "settings"
        # Prefer staying on Settings when theme is being previewed from there
        if tab not in ("dash", "codes", "settings"):
            tab = "settings"
        self.theme = dict(THEMES.get(self.settings.get("theme"), THEMES["4G1 Red"]))
        self._resolve_theme_fonts()
        self.rebuild_ui()
        self._show_tab(tab)
        # Restore demo / live button labels after rebuild
        if self.demo:
            try:
                self.demo_btn.config(text="Stop Demo")
                self.connect_btn.config(state="disabled")
                self.live_btn.config(text="Demo", state="disabled")
                self._set_status("DEMO", "warn")
                self.dev_info.config(text="Demo — no car connected")
            except Exception:
                pass
        elif self.live and self.dev:
            try:
                self.live_btn.config(text="Stop Live", state="normal")
                self._set_status("LIVE", "live")
            except Exception:
                pass

    # ---------- settings apply ----------
    def apply_settings(self):
        self._read_settings_from_ui()
        # Resolve theme dict from the (possibly new) name
        theme_name = self.settings.get("theme", "4G1 Red")
        if theme_name not in THEMES:
            theme_name = "4G1 Red"
            self.settings["theme"] = theme_name
        self.theme = THEMES[theme_name]
        save_settings(self.settings)
        logging.info("Settings applied (theme=%s style=%s)",
                     theme_name, self.settings.get("style"))
        self._rebuild_after_settings()

    def rebuild_ui(self):
        for w in self.winfo_children():
            w.destroy()
        self.theme = THEMES.get(self.settings.get("theme"), THEMES["4G1 Red"])
        self.configure(bg=self.theme["bg"])
        prof = self._active_profile()
        self.title(f"4G1 Live [OLD]  —  {prof['short']}")
        self.gauges = {}
        self.heroes = {}
        self._build()
        self._set_titlebar_colour()
        if self.dev and not self.demo:
            self.connect_btn.config(text="Disconnect", state="normal")
            self.live_btn.config(state="normal")
            if self.live:
                self._set_status("LIVE", "live")
                self.live_btn.config(text="Stop Live")
            else:
                self._set_status("CONNECTED", "ok")

    def _set_status(self, text, mode="off"):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self._set_status(text, mode))
            return
        self._status_mode = mode
        # StatusPill owns the badge chrome; map mode aliases cleanly
        pill_mode = mode
        if mode == "ok":
            pill_mode = "ok"
        elif mode == "live":
            pill_mode = "live"
        elif mode == "warn":
            pill_mode = "demo" if "DEMO" in text.upper() else "warn"
        label = text if text.startswith("●") else f"●  {text}"
        try:
            self.status_pill.set(label, pill_mode)
        except Exception:
            # Fallback if UI was mid-rebuild
            pass

    def _status_tick(self):
        if self._closing:
            return
        try:
            if getattr(self, "_status_mode", "off") == "live" and hasattr(self, "status_pill"):
                t = self.theme
                phase = (getattr(self, "_status_phase", 0) + 1) % 10
                self._status_phase = phase
                pulse = 0.25 if phase < 5 else 0.05
                self.status_pill.lbl.config(
                    fg=_blend_hex(t["accent_hi"], t.get("brand2", t["accent"]), pulse)
                )
        except Exception:
            pass
        self.after(280, self._status_tick)

    # ---------- connection ----------
    def _conn_kwargs(self):
        s = self.settings
        return dict(
            dll_path=s.get("dll_path") or j2534.default_dll(),
            baud=int(s.get("mut_baud", 15625)),
            request_timeout_ms=int(s.get("request_timeout_ms", 400)),
        )

    def on_connect(self):
        if self.demo:
            return
        if self.dev:
            if self.live:
                messagebox.showinfo(APP_NAME, "Stop Live before disconnecting.")
                return
            self._disconnect_adapter()
            return
        try:
            kw = self._conn_kwargs()
            if not os.path.isfile(kw["dll_path"]):
                raise j2534.J2534Error(
                    f"Driver file not found:\n{kw['dll_path']}\n\n"
                    f"Set the path in Settings → Adapter."
                )
            self.dev = j2534.Device(**kw)
            self.dev.open()
            fw, dl, ap = self.dev.version()
            mv = self.dev.battery_mv() or 0
            self._set_status("CONNECTED", "ok")
            self.dev_info.config(text=f"fw {fw}   ·   {mv / 1000:.1f} V")
            self.connect_btn.config(text="Disconnect", state="normal")
            self.live_btn.config(state="normal")
            self.foot_right.config(text=f"Ready  ·  {mv / 1000:.2f} V")
            try:
                self.foot_left.config(text=self._conn_summary())
            except Exception:
                pass
            self.log_line(f"Connected  ·  {mv / 1000:.2f} V")
            self.log_line(f"Vehicle  ·  {vehicle_line(self.settings)}")
            if self.settings.get("auto_live") and not self.live:
                self.after(400, self.toggle_live)
        except Exception as e:
            self.dev = None
            logging.exception("Adapter connection failed")
            messagebox.showerror("Connect failed", str(e))

    def _disconnect_adapter(self):
        try:
            self.dev.close()
        except Exception:
            logging.exception("Adapter disconnect failed")
        finally:
            self.dev = None
        self.connect_btn.config(text="Connect", state="normal")
        self.live_btn.config(text="Start Live", state="disabled")
        self.dev_info.config(text="—")
        self._set_status("OFFLINE", "off")
        self.foot_right.config(text="Ready")
        self.log_line("Disconnected")

    def toggle_demo(self):
        """Run a realistic, clearly labelled dashboard demonstration."""
        if self.demo:
            self.live = False
            self.demo = False
            self.demo_btn.config(text="Demo")
            self.connect_btn.config(state="normal")
            self.live_btn.config(text="Start Live", state="disabled")
            self._set_status("OFFLINE", "off")
            self.dev_info.config(text="—")
            self.foot_right.config(text="Ready")
            self.log_line("Demo ended")
            self._session_t0 = None
            self._update_idle_banner()
            return
        if self.live or self.dev:
            messagebox.showinfo(APP_NAME, "Stop live data / disconnect before starting Demo.")
            return
        self.demo = True
        self.live = True
        self.latest.clear()
        self.samples = []
        self._log_rows = []
        self._hz_times.clear()
        self._session_t0 = time.monotonic()
        self.demo_btn.config(text="Stop Demo")
        self.connect_btn.config(state="disabled")
        self.live_btn.config(text="Demo", state="disabled")
        self._set_status("DEMO", "warn")
        self.dev_info.config(text="Demo — no car connected")
        self.log_line("Demo started — values are simulated")
        for chart_name in ("chart_rpm", "chart_load"):
            chart = getattr(self, chart_name, None)
            if chart:
                chart.clear()
        self._update_idle_banner()
        self._demo_tick()

    def _demo_tick(self):
        if not self.demo or self._closing:
            return
        elapsed = time.monotonic() - self._session_t0
        pulse = (math.sin(elapsed * 0.55) + 1.0) / 2.0
        rpm = 760 + 55 * math.sin(elapsed * 1.4)
        values = {
            0x21: rpm, 0x06: 9 + 2 * math.sin(elapsed),
            0x07: min(92, 76 + elapsed * 0.18),
            0x3A: 28 + math.sin(elapsed * 0.2),
            0x17: 2.2 + pulse * 1.2, 0x14: 14.1 + 0.08 * math.sin(elapsed),
            0x15: 100.5, 0x1A: 38 + 4 * math.sin(elapsed * 1.5),
            0x1C: 24 + 3 * math.sin(elapsed * 0.7), 0x2F: 0.0,
            0x29: 2.6 + 0.25 * math.sin(elapsed * 0.9),
            0x13: 0.45 + 0.34 * math.sin(elapsed * 4.8),
            0x0C: 1.5, 0x0D: -0.8, 0x0E: 0.4, 0x26: 0.0,
            0x27: 128.0, 0x24: 780.0, 0x16: 31.0, 0x38: 5.4,
        }
        self.latest.update(values)
        snap = {"t": round(time.monotonic(), 3), "mode": "DEMO"}
        for pid in set(self.gauges) | set(self.heroes) | {0x1C, 0x21}:
            if pid in values:
                snap[_param(pid)[0]] = round(values[pid], 2)
        self.samples.append(snap)
        self._hz_times.append(time.monotonic())
        if len(self._hz_times) >= 2:
            dt = self._hz_times[-1] - self._hz_times[0]
            self._poll_hz_actual = ((len(self._hz_times) - 1) / dt) if dt else 0
        self.after(max(50, int(1000 / max(1, self.settings.get("poll_hz", 10)))),
                   self._demo_tick)

    def toggle_live(self):
        if self.live:
            self.live = False
            self.live_btn.config(text="Start Live")
            self._update_idle_banner()
            return
        if not self.dev:
            messagebox.showwarning("4G1 Live", "Connect the adapter first.")
            return
        self.live = True
        self.live_btn.config(text="Stop Live")
        self.samples = []
        self._hz_times.clear()
        self._session_t0 = time.monotonic()
        if hasattr(self, "chart_rpm"):
            self.chart_rpm.clear()
        if hasattr(self, "chart_load"):
            self.chart_load.clear()
        self._update_idle_banner()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        s = self.settings
        pin1 = bool(s.get("pin1_ground", True))
        baud = int(s.get("mut_baud", 15625))
        init_addr = self._parse_init_addr(s.get("init_addr", "0x00"))
        attempts = int(s.get("init_attempts", 5))
        timeout = int(s.get("request_timeout_ms", 400))
        if self.dev:
            self.dev.request_timeout_ms = timeout
            self.dev.baud = baud
        try:
            if pin1:
                self.dev.ground_pin1()
            self.dev.mut2_connect(baud=baud)
            key = self.dev.mut2_init(addr=init_addr, attempts=attempts)
            self._set_status("LIVE", "live")
            pin_txt = "pin 1 grounded" if pin1 else "pin 1 not forced"
            key_hex = key.hex() if key else "—"
            self.log_line(
                f"Live session started  ·  {baud} baud  ·  {pin_txt}"
            )
        except Exception as e:
            self.log_line(f"No response from car — ignition on?  ({e})")
            self.live = False
            try:
                self.live_btn.config(text="Start Live")
            except Exception:
                pass
            self._set_status("CONNECTED", "ok")
            try:
                self.dev.disconnect()
                if pin1:
                    self.dev.release_pin1()
            except Exception:
                pass
            return

        # poll hero + selected gauges (union)
        period = 1.0 / max(1, s.get("poll_hz", 12))
        while self.live:
            t0 = time.monotonic()
            ids = set(self.gauges.keys()) | set(self.heroes.keys())
            ids.add(0x1C)
            ids.add(0x21)
            snap = {"t": round(t0, 3)}
            for pid in ids:
                if pid not in j2534.ALL_PARAMS:
                    continue
                raw = self.dev.mut2_request(pid, timeout=timeout)
                if raw is None:
                    continue
                name, scale, *_ = _param(pid)
                val = scale(raw)
                self.latest[pid] = val
                snap[name] = round(val, 2)
            self.samples.append(snap)
            self._hz_times.append(time.monotonic())
            if len(self._hz_times) >= 2:
                dt = self._hz_times[-1] - self._hz_times[0]
                if dt > 0:
                    self._poll_hz_actual = (len(self._hz_times) - 1) / dt
            elapsed = time.monotonic() - t0
            time.sleep(max(0.0, period - elapsed))

        try:
            self.dev.disconnect()
            if pin1:
                self.dev.release_pin1()
        except Exception:
            pass
        self.log_line("Live stopped")
        self._session_t0 = None

    # ---------- UI refresh ----------
    def _refresh_ui(self):
        t = self.theme
        # clock
        try:
            self.clock_lbl.config(text=time.strftime("%H:%M:%S"))
        except Exception:
            pass

        # full live snapshot as context for sensor health lights
        ctx = dict(self.latest)
        for pid, g in self.gauges.items():
            if pid in self.latest:
                g.set(self.latest[pid], ctx=ctx)
        for pid, h in self.heroes.items():
            if pid in self.latest:
                h.set(self.latest[pid], ctx=ctx)

        if self.live:
            if 0x21 in self.latest and hasattr(self, "chart_rpm"):
                self.chart_rpm.push(self.latest[0x21])
            if 0x1C in self.latest and hasattr(self, "chart_load"):
                self.chart_load.push(self.latest[0x1C])

        if self.live and self.samples and (not self._log_rows or self.samples[-1] is not self._log_rows[-1]):
            s = self.samples[-1]
            self._log_rows.append(s)
            parts = [time.strftime("%H:%M:%S")]
            # log hero vitals first for readability
            for pid in self.heroes:
                if pid in self.gauges or pid in self.heroes:
                    name = _param(pid)[0]
                    if name in s:
                        parts.append(f"{name}={s[name]}")
            for pid in self.gauges:
                if pid in self.heroes:
                    continue
                name = _param(pid)[0]
                if name in s:
                    parts.append(f"{name}={s[name]}")
            self.log_line("  ".join(parts), telemetry=True)

        # metrics
        try:
            n = len(self.samples)
            self.n_lbl.config(text=f"{n:,}")
            if self.live and self._poll_hz_actual > 0:
                self.hz_lbl.config(text=f"{self._poll_hz_actual:.1f} Hz", fg=t["good"])
            else:
                self.hz_lbl.config(text="— Hz", fg=t["muted"])
            if self.live and self._session_t0:
                sec = int(time.monotonic() - self._session_t0)
                m, s = divmod(sec, 60)
                self.sess_lbl.config(text=f"{m:02d}:{s:02d}")
                mode = "DEMO" if self.demo else "LIVE"
                self.foot_right.config(
                    text=f"{mode}  ·  {n:,} samples  ·  {self._poll_hz_actual:.1f} Hz"
                )
            elif not self.live:
                self.sess_lbl.config(text="—")
        except Exception:
            pass

        self.after(200, self._refresh_ui)

    def log_line(self, text, telemetry=False):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self.log_line(text, telemetry))
            return
        try:
            prefix = "" if telemetry else "· "
            self.logbox.insert("end", prefix + text + "\n")
            self.logbox.see("end")
            if int(self.logbox.index("end-1c").split(".")[0]) > 500:
                self.logbox.delete("1.0", "120.0")
        except Exception:
            pass

    def clear_log(self):
        self.logbox.delete("1.0", "end")
        self.samples = []
        self._log_rows = []

    def save_csv(self):
        if not self.samples:
            messagebox.showinfo("4G1 Live", "No telemetry captured yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=time.strftime("4g1_log_%Y%m%d_%H%M%S.csv"),
        )
        if not path:
            return
        cols = ["t"]
        for s in self.samples:
            for k in s:
                if k not in cols:
                    cols.append(k)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for s in self.samples:
                w.writerow(s)
        messagebox.showinfo("4G1 Live", f"Saved {len(self.samples)} rows to\n{path}")

    # ---------- codes ----------
    def _mut_session_start(self):
        """Open a MUT-II channel using current connection settings."""
        s = self.settings
        pin1 = bool(s.get("pin1_ground", True))
        baud = int(s.get("mut_baud", 15625))
        addr = self._parse_init_addr(s.get("init_addr", "0x00"))
        attempts = int(s.get("init_attempts", 5))
        self.dev.request_timeout_ms = int(s.get("request_timeout_ms", 400))
        if pin1:
            self.dev.ground_pin1()
        self.dev.mut2_connect(baud=baud)
        self.dev.mut2_init(addr=addr, attempts=attempts)
        return pin1

    def _mut_session_end(self, pin1):
        try:
            self.dev.disconnect()
            if pin1:
                self.dev.release_pin1()
        except Exception:
            pass

    def read_codes(self):
        if not self.dev:
            messagebox.showwarning("4G1 Live", "Connect first.")
            return
        self.codes_box.delete("1.0", "end")
        pin1 = bool(self.settings.get("pin1_ground", True))
        try:
            reconnect = not self.live
            if reconnect:
                pin1 = self._mut_session_start()
            codes = self.dev.read_dtcs()
            if reconnect:
                self._mut_session_end(pin1)
        except Exception as e:
            self.codes_box.insert("end", f"Read failed: {e}\n(ignition on?)\n")
            return
        if codes is None:
            self.codes_box.insert("end", "No response from ECU.\n")
            return
        if not codes:
            self.codes_box.insert("end", "✓  No trouble codes set.\n")
            self.codes_box.insert("end", "   No faults stored.\n")
            return
        self.codes_box.insert("end", f"{len(codes)} code(s) present:\n\n")
        for code, desc in codes:
            self.codes_box.insert("end", f"  #{code:02d}   {desc}\n")

    def clear_codes(self):
        if not self.dev:
            messagebox.showwarning("4G1 Live", "Connect first.")
            return
        if not messagebox.askyesno("4G1 Live", "Clear all stored trouble codes?"):
            return
        pin1 = bool(self.settings.get("pin1_ground", True))
        try:
            reconnect = not self.live
            if reconnect:
                pin1 = self._mut_session_start()
            self.dev.clear_dtcs()
            if reconnect:
                self._mut_session_end(pin1)
            self.codes_box.insert("end", "\nClear command sent (0xCA). Re-read to confirm.\n")
        except Exception as e:
            self.codes_box.insert("end", f"Clear failed: {e}\n")

    def destroy(self):
        self._closing = True
        self.live = False
        self.demo = False
        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass
        super().destroy()


if __name__ == "__main__":
    app = App()
    if "--autoconnect" in sys.argv or "--autolive" in sys.argv:
        app.after(700, app.on_connect)
    if "--autolive" in sys.argv:
        app.after(1600, app.toggle_live)
    app.mainloop()
