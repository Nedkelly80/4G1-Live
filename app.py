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
import sys
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

import j2534
import sweep
from product import APP_NAME, VERSION, COPYRIGHT, PRODUCT_DESCRIPTION

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)

if sys.platform == "darwin":
    _app_support = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    USER_DATA_DIR = os.path.join(_app_support, "4G1 Live")
elif sys.platform == "win32":
    USER_DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA", SOURCE_DIR), "4G1 Live"
    )
else:
    USER_DATA_DIR = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")),
        "4g1-live",
    )

LOG_DIR = os.path.join(USER_DATA_DIR, "Logs")
SESSION_DIR = os.path.join(USER_DATA_DIR, "Sessions")
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
    "Windows XP": dict(
        # Classic Luna Blue — beige chrome, blue accents, Tahoma UI
        bg="#9DB9EB", panel="#ECE9D8", panel2="#FFFFFF", elev="#C2D5F2",
        fg="#000000", dim="#0A246A", muted="#5A574B",
        accent="#245EDC", accent_hi="#3C8CF0", accent2="#003C74",
        good="#008000", warn="#E56717", bad="#C00000",
        border="#0A246A", chart="#F8FBFF",
        glow="#245EDC33", tab_sel="#FFFFFF",
        title="#0A246A", title2="#3A8CF0",
        font="Tahoma", mono="Lucida Console",
        xp=True,
    ),
    "Precision Dark": dict(
        bg="#07111a", panel="#0b1721", panel2="#101f2b", elev="#172a38",
        fg="#f4f7fa", dim="#a9b5c1", muted="#647584",
        accent="#52d9e8", accent_hi="#7ee7f1", accent2="#c7d4de",
        good="#4bd7a5", warn="#ff8a2a", bad="#ff6474",
        border="#294050", chart="#040b11",
        glow="#52d9e833", tab_sel="#101f2b",
    ),
    "Carbon Red": dict(
        bg="#090a0c", panel="#12141a", panel2="#1a1d26", elev="#232833",
        fg="#f2f4f7", dim="#8e96a3", muted="#5a6270",
        accent="#e11d2a", accent_hi="#ff3b4a", accent2="#c8ced8",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#2a303c", chart="#060708",
        glow="#e11d2a33", tab_sel="#1a1d26",
    ),
    "Midnight Blue": dict(
        bg="#070b12", panel="#0e1522", panel2="#162033", elev="#1c2a42",
        fg="#e8eef8", dim="#8490a8", muted="#526078",
        accent="#3b82f6", accent_hi="#60a5fa", accent2="#a8bdd8",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#243148", chart="#05080f",
        glow="#3b82f633", tab_sel="#162033",
    ),
    "Slate Pro": dict(
        bg="#0e1014", panel="#161a22", panel2="#1f2430", elev="#2a3140",
        fg="#eef1f6", dim="#9aa3b2", muted="#646e7e",
        accent="#f97316", accent_hi="#fb923c", accent2="#c9d0db",
        good="#4ade80", warn="#facc15", bad="#f87171",
        border="#2e3644", chart="#090b0f",
        glow="#f9731633", tab_sel="#1f2430",
    ),
    "Titanium": dict(
        bg="#0c0e11", panel="#14171c", panel2="#1e232b", elev="#282e38",
        fg="#eef0f3", dim="#8f97a3", muted="#5c6572",
        accent="#94a3b8", accent_hi="#cbd5e1", accent2="#e11d2a",
        good="#4ade80", warn="#fbbf24", bad="#f87171",
        border="#2c333e", chart="#07080a",
        glow="#94a3b833", tab_sel="#1e232b",
    ),
    "Ralliart": dict(
        # Mitsubishi Ralliart works livery: competition red on near-black
        # graphite, with chrome-silver secondary (the wordmark reads silver,
        # not blue, so the header sits closer to the Ralliart badge).
        bg="#08080a", panel="#111215", panel2="#191b1f", elev="#23262b",
        fg="#f5f6f8", dim="#a3a8af", muted="#6a7078",
        accent="#e60012", accent_hi="#ff2b3a", accent2="#c9ced5",
        good="#35c98d", warn="#ffb020", bad="#ff4d6d",
        border="#2b2f36", chart="#050506",
        glow="#e6001233", tab_sel="#191b1f",
        brand2="#c9ced5",
        font_choices=("Eurostile", "Bahnschrift Condensed", "Bahnschrift",
                      "Segoe UI Semibold", "Segoe UI"),
        mono_choices=("JetBrains Mono", "Cascadia Mono", "Consolas"),
    ),
    "Racing Green": dict(
        bg="#070c0a", panel="#0e1612", panel2="#15221b", elev="#1c2e24",
        fg="#eaf6ef", dim="#86a092", muted="#547064",
        accent="#10b981", accent_hi="#34d399", accent2="#b8d4c6",
        good="#34d399", warn="#fbbf24", bad="#f87171",
        border="#1e3328", chart="#050806",
        glow="#10b98133", tab_sel="#15221b",
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
    """Theme-aware UI font (Tahoma on Windows XP, Segoe UI otherwise)."""
    if mono:
        family = (theme or {}).get("mono", "Consolas")
    else:
        family = (theme or {}).get("font", "Segoe UI")
    return (family, size, "bold") if bold else (family, size)


def _default_adapter_path():
    if sys.platform == "darwin":
        import tactrix_mac
        return tactrix_mac.default_port() or ""
    return j2534.default_dll()


DEFAULTS = dict(
    theme="4G1 Red",
    style="Dial",
    gauge_scale=100,
    gauges=[hex(g) for g in j2534.DEFAULT_GAUGES],
    poll_hz=12,
    vehicle_profile=j2534.DEFAULT_PROFILE_ID,
    # --- connection ---
    dll_path=_default_adapter_path(),
    mut_baud=15625,
    pin1_ground=True,
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
            # utf-8-sig: tolerate a BOM from external editors/tools —
            # a BOM'd file silently reset everything to defaults (21/7/26)
            with open(path, encoding="utf-8-sig") as f:
                s.update(json.load(f))
            break
        except FileNotFoundError:
            continue
        except Exception:
            logging.exception("Could not read settings from %s", path)
    # migrate / clamp unknown profile ids to primary 4G15 12V
    if s.get("vehicle_profile") not in j2534.VEHICLE_PROFILES:
        s["vehicle_profile"] = j2534.DEFAULT_PROFILE_ID
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


# ---------------------------------------------------------------- soft button
class SoftButton(tk.Frame):
    """Flat button with hover + disabled states."""

    def __init__(self, master, text, command, theme, primary=False, **kw):
        super().__init__(master, bg=theme.get("panel", theme["bg"]), **kw)
        self.theme = theme
        self.primary = primary
        self.command = command
        self._enabled = True
        t = theme
        if primary:
            self.bg0, self.bg1, self.fg = t["accent"], t["accent_hi"], "#ffffff"
        else:
            self.bg0, self.bg1, self.fg = t["panel2"], t["elev"], t["fg"]
        self.lbl = tk.Label(
            self, text=text, bg=self.bg0, fg=self.fg,
            font=ui_font(t, 9, bold=True), padx=14, pady=5, cursor="hand2",
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
        for w in (self, self.lbl):
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
            w.bind("<Button-1>", self._click)

    def _enter(self, _=None):
        if self._enabled:
            self.lbl.config(bg=self.bg1)

    def _leave(self, _=None):
        if self._enabled:
            self.lbl.config(bg=self.bg0)

    def _click(self, _=None):
        if self._enabled and self.command:
            self.command()

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

    def __init__(self, master, theme, size=10, **kw):
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
        pad = 1
        self.create_oval(
            pad, pad, self.size - pad, self.size - pad,
            fill=fill, outline=fill,
        )


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
        self.strip = tk.Frame(self, bg=theme["border"], height=2)
        self.strip.pack(fill="x")

        body = tk.Frame(self, bg=theme["panel"])
        body.pack(fill="both", expand=True, padx=12, pady=(6, 8))

        title_row = tk.Frame(body, bg=theme["panel"])
        title_row.pack(fill="x")
        tk.Label(title_row, text=name.upper(), bg=theme["panel"], fg=theme["dim"],
                 font=ui_font(theme, 8, bold=True), anchor="w").pack(side="left")
        if pid in j2534.HEALTH_PIDS:
            self.led = StatusLED(title_row, theme, size=9)
            self.led.pack(side="right", padx=(4, 0))
        else:
            self.led = None

        row = tk.Frame(body, bg=theme["panel"])
        row.pack(fill="x", pady=(0, 0))
        self.val = tk.Label(row, text="—", bg=theme["panel"], fg=theme["muted"],
                            font=ui_font(theme, 24, bold=True, mono=True), anchor="w")
        self.val.pack(side="left")
        unit_text = "" if units == "state" else units
        self.unit = tk.Label(row, text=unit_text, bg=theme["panel"], fg=theme["muted"],
                             font=ui_font(theme, 9), anchor="sw")
        self.unit.pack(side="left", padx=(5, 0), pady=(0, 2))

        self.bar = tk.Canvas(body, height=2, bg=theme["elev"], highlightthickness=0)
        self.bar.pack(fill="x", pady=(6, 0))

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

        if self.units in ("rpm", "°C", "°F", "Hz", "count", "km/h", "steps", "kPa", "°", "raw"):
            txt = f"{value:.0f}"
        else:
            txt = f"{value:.1f}"
        self.val.config(text=txt, fg=col)

        if self.led is not None:
            self.led.set_status(self._health)

        c = self.bar
        c.delete("all")
        w = max(c.winfo_width(), 40)
        bar_col = t["accent"]
        if self._health == "good":
            bar_col = t["good"]
        elif self._health == "bad":
            bar_col = t["bad"]
        elif self._health == "warn":
            bar_col = t["warn"]
        c.create_rectangle(0, 0, w, 3, fill=t["elev"], outline="")
        c.create_rectangle(0, 0, max(2, int(w * frac)), 3, fill=bar_col, outline="")
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
        self.strip = tk.Frame(self, bg=t["border"], height=2)
        self.strip.pack(fill="x")
        body = tk.Frame(self, bg=t["panel"])
        body.pack(side="left", fill="both", expand=True)
        self.body = body

        head = tk.Frame(body, bg=t["panel"])
        head.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(head, text=self.name.upper(), bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True), anchor="w").pack(side="left")
        if self.pid in j2534.HEALTH_PIDS:
            self.led = StatusLED(head, t, size=9)
            self.led.pack(side="right", padx=(6, 0))
        else:
            self.led = None
        unit_text = "" if self.units == "state" else self.units
        tk.Label(head, text=unit_text, bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7), anchor="e").pack(side="right")

        if self.style == "Digital":
            self.val = tk.Label(body, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 22, bold=True, mono=True), anchor="w")
            self.val.pack(fill="x", padx=12, pady=(0, 0))
            self.spark = tk.Canvas(body, height=10, bg=t["panel"], highlightthickness=0)
            self.spark.pack(fill="x", padx=10, pady=(0, 1))
            self.range_lbl = tk.Label(body, text=self._range_text(), bg=t["panel"],
                                      fg=t["muted"], font=ui_font(t, 7), anchor="w")
            self.range_lbl.pack(fill="x", padx=12, pady=(0, 5))
        elif self.style == "Bar":
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="x", padx=10, pady=(2, 0))
            self.val = tk.Label(row, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 18, bold=True, mono=True), anchor="w")
            self.val.pack(side="left")
            self.canvas = tk.Canvas(body, height=12, bg=t["elev"], highlightthickness=0)
            self.canvas.pack(fill="x", padx=10, pady=(5, 2))
            self.range_lbl = tk.Label(body, text=self._range_text(), bg=t["panel"],
                                      fg=t["muted"], font=ui_font(t, 7), anchor="w")
            self.range_lbl.pack(fill="x", padx=10, pady=(0, 6))
        elif self.style == "Compact":
            row = tk.Frame(body, bg=t["panel"])
            row.pack(fill="both", expand=True, padx=10, pady=6)
            self.val = tk.Label(row, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 16, bold=True, mono=True), anchor="w")
            self.val.pack(side="left")
            if self.pid in j2534.HEALTH_PIDS and self.led is None:
                pass  # LED already in head for non-compact; compact uses head too
            self.pct = tk.Label(row, text="", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 8, mono=True), anchor="e")
            self.pct.pack(side="right")
        else:  # Dial
            self.canvas = tk.Canvas(body, height=90, bg=t["panel"], highlightthickness=0)
            self.canvas.pack(fill="x", padx=4, pady=1)
            self.val = tk.Label(body, text="—", bg=t["panel"], fg=t["muted"],
                                font=ui_font(t, 12, bold=True, mono=True))
            self.val.pack(pady=(0, 4))

    def _range_text(self):
        if self.units == "state":
            return "OFF  ···  ON"
        if self.gmin < 0:
            return f"{self.gmin:g}  ···  {self.gmax:g}"
        return f"0  ···  {self.gmax:g}"

    def _fmt(self, v):
        if self.units == "state":
            return "ON" if v >= 0.5 else "OFF"
        if self.units in ("rpm", "°C", "°F", "Hz", "count", "km/h", "steps", "kPa", "°", "raw"):
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
            h = 12
            # segmented track
            segs = 20
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
                c.create_rectangle(x0, 0, x0 + sw, h, fill=fill, outline="")
            if self.gmin < 0:
                z = (0 - self.gmin) / (self.gmax - self.gmin)
                zx = int(w * z)
                c.create_line(zx, 0, zx, h, fill=t["fg"], width=1)
        elif self.style == "Compact":
            self.val.config(text=txt, fg=col)
            status_txt = {"good": "OK", "bad": "BAD", "warn": "!"}.get(self._health, "")
            self.pct.config(text=status_txt or f"{frac * 100:.0f}%")
        else:
            self._draw_dial(frac, col, txt)

    def _draw_spark(self):
        c = self.spark
        c.delete("all")
        if len(self.history) < 2:
            return
        w = max(c.winfo_width(), 40)
        h = 18
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
            y = h - 2 - ((v - lo) / (hi - lo)) * (h - 5)
            pts += [x, y]
        c.create_line(*pts, fill=t["accent"], width=1.5, smooth=True)

    def _draw_dial(self, frac, col, txt):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 100)
        h = max(c.winfo_height(), 90)
        cx, cy, r = w / 2, h * 0.78, min(w, h) * 0.42
        t = self.theme
        # outer ring
        c.create_arc(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3,
                     start=200, extent=-220, style="arc", outline=t["border"], width=2)
        # background arc
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=200, extent=-220,
                     style="arc", outline=t["elev"], width=10)
        # value arc (warn colour near redline)
        arc_col = t["warn"] if frac > 0.88 else t["accent"]
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=200, extent=-220 * frac,
                     style="arc", outline=arc_col, width=10)
        # ticks + labels
        for i in range(11):
            a = math.radians(200 - 220 * (i / 10))
            major = i % 5 == 0
            r0, r1 = r - 2, r - (12 if major else 8)
            c.create_line(cx + r0 * math.cos(a), cy - r0 * math.sin(a),
                          cx + r1 * math.cos(a), cy - r1 * math.sin(a),
                          fill=t["muted"] if not major else t["dim"], width=1)
            if major:
                tv = self.gmin + (self.gmax - self.gmin) * (i / 10)
                tx = cx + (r - 20) * math.cos(a)
                ty = cy - (r - 20) * math.sin(a)
                c.create_text(tx, ty, text=f"{tv:.0f}", fill=t["muted"],
                              font=ui_font(t, 6, mono=True))
        # needle
        ang = math.radians(200 - 220 * frac)
        nx = cx + r * 0.72 * math.cos(ang)
        ny = cy - r * 0.72 * math.sin(ang)
        c.create_line(cx, cy, nx, ny, fill=col, width=2, capstyle="round")
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=t["accent"], outline=t["fg"])
        unit_text = "" if self.units == "state" else self.units
        suffix = f"  {unit_text}" if unit_text else ""
        self.val.config(text=f"{txt}{suffix}", fg=col)


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

        tk.Frame(self, bg=self.accent, height=2).pack(fill="x")
        head = tk.Frame(self, bg=theme["panel"])
        head.pack(fill="x")
        tk.Label(head, text=label.upper(), bg=theme["panel"], fg=theme["dim"],
                 font=ui_font(theme, 7, bold=True)).pack(side="left", padx=8, pady=3)
        self.stats = tk.Label(head, text="min —  ·  max —", bg=theme["panel"],
                              fg=theme["muted"], font=ui_font(theme, 7, mono=True))
        self.stats.pack(side="left", padx=6)
        self.cur = tk.Label(head, text="—", bg=theme["panel"], fg=theme["muted"],
                            font=ui_font(theme, 11, bold=True, mono=True))
        self.cur.pack(side="right", padx=8)
        self.canvas = tk.Canvas(self, bg=theme["chart"], highlightthickness=0, height=64)
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
        vmax = self.vmax
        # grid
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            c.create_line(36, y, w, y, fill=t["border"], dash=(2, 4))
            c.create_text(4, y, anchor="w", fill=t["muted"],
                          font=ui_font(t, 7, mono=True), text=f"{vmax * (1 - frac):.0f}")
        if len(self.data) < 2:
            c.create_text(w / 2, h / 2, text="waiting for data…", fill=t["muted"],
                          font=ui_font(t, 9))
            return
        n = len(self.data)
        step = (w - 40) / max(1, self.data.maxlen - 1)
        pts = []
        for i, v in enumerate(self.data):
            x = 40 + (self.data.maxlen - n + i) * step
            y = h - max(0.0, min(1.0, v / vmax)) * (h - 4) - 2
            pts += [x, y]
        if len(pts) >= 4:
            fill_pts = list(pts) + [pts[-2], h, pts[0], h]
            try:
                c.create_polygon(*fill_pts, fill=t["elev"], outline="")
            except Exception:
                pass
        c.create_line(*pts, fill=self.accent, width=2, smooth=True)
        # latest dot
        c.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3,
                      fill=self.accent, outline="")


# ---------------------------------------------------------------- elevated card
def card(parent, theme, accent=None):
    """A panel with a subtle drop-shadow and coloured top accent strip.

    Returns (outer, panel): pack/grid `outer`, build content inside `panel`.
    """
    t = theme
    outer = tk.Frame(parent, bg=t["bg"])
    panel = tk.Frame(outer, bg=t["panel"],
                      highlightbackground=t["border"], highlightthickness=1)
    panel.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 3))
    tk.Frame(panel, bg=accent or t["accent"], height=2).pack(fill="x")
    return outer, panel


# ---------------------------------------------------------------- section header
def section_label(parent, text, theme):
    f = tk.Frame(parent, bg=theme["bg"])
    tk.Label(f, text=text, bg=theme["bg"], fg=theme["dim"],
             font=ui_font(theme, 8, bold=True)).pack(side="left")
    tk.Frame(f, bg=theme["border"], height=1).pack(side="left", fill="x", expand=True,
                                                    padx=(10, 0), pady=1)
    return f


# ---------------------------------------------------------------- main app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.theme = dict(THEMES.get(self.settings["theme"], THEMES["4G1 Red"]))
        self._resolve_theme_fonts()
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        self.title(f"4G1 Live  —  {prof['short']}  ·  MUT-II")
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
        try:
            self.state("zoomed")  # full-screen dash by default; autofit trims to suit
        except Exception:
            pass
        # Responsive fit: extra factor applied on top of the user's gauge
        # scale so the full dashboard stack (heroes, gauge grid, health
        # strip, telemetry log) always fits the screen. 1080-class laptops
        # clipped the footer at 100% — seen at the 20/7 on-car session.
        self._fit_factor = 1.0
        self._fit_tries = 0
        self._fit_job = None
        self._last_root_h = 0
        self.bind("<Configure>", self._on_root_resize)
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
        self._sweep_wait = threading.Event()
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

    def _gauge_scale_pct(self):
        try:
            pct = int(self.settings.get("gauge_scale", 100))
        except Exception:
            pct = 100
        return max(70, min(170, pct))

    def _scaled_px(self, base):
        fit = getattr(self, "_fit_factor", 1.0)
        return max(1, int(round(base * (self._gauge_scale_pct() / 100.0) * fit)))

    def _on_root_resize(self, evt):
        if evt.widget is not self:
            return
        if abs(evt.height - self._last_root_h) < 30:
            return
        self._last_root_h = evt.height
        if self._fit_job:
            try:
                self.after_cancel(self._fit_job)
            except Exception:
                pass
        self._fit_tries = 0
        self._fit_job = self.after(250, self._autofit_dash)

    def _autofit_dash(self):
        """Shrink (or restore) the dashboard so the whole stack fits on
        screen — nothing important gets clipped off the bottom."""
        self._fit_job = None
        tab = getattr(self, "tab_dash", None)
        if tab is None or not tab.winfo_exists():
            return
        if getattr(self, "_active_tab", "dash") != "dash":
            return  # re-checked when the dash tab is shown
        try:
            self.update_idletasks()  # settle geometry before measuring
            avail = tab.winfo_height()
            req = tab.winfo_reqheight()
        except Exception:
            return
        if avail <= 80 or req <= 0:
            # not laid out yet — retry briefly
            if self._fit_tries < 15:
                self._fit_tries += 1
                self._fit_job = self.after(200, self._autofit_dash)
            return
        if req > avail + 4:
            new = max(0.60, self._fit_factor * (avail / float(req)))
            if new < self._fit_factor - 0.005:
                self._fit_factor = new
                self._rebuild_dash()
        elif self._fit_factor < 0.995 and req < avail - 140:
            # window grew — claw back toward full size, with hysteresis
            new = min(1.0, self._fit_factor * (avail / float(req)) * 0.96)
            if new > self._fit_factor + 0.04:
                self._fit_factor = new
                self._rebuild_dash()

    def _rebuild_dash(self):
        keep_log = ""
        try:
            keep_log = self.logbox.get("1.0", "end-1c")
        except Exception:
            pass
        for w in self.tab_dash.winfo_children():
            w.destroy()
        self._build_dash()
        if keep_log.strip():
            try:
                self.logbox.insert("end", keep_log + "\n")
                self.logbox.see("end")
            except Exception:
                pass

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
        self.body.pack(fill="both", expand=True, padx=10, pady=(0, 2))

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
        if sys.platform == "win32":
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

    def _load_logo_images(self):
        """Load logo PhotoImages (must keep refs on self)."""
        self._mark_img = None
        self._logo_img = None
        for path, attr in (
            (MARK_PATH, "_mark_img"),
            (LOGO_PATH, "_logo_img"),
            (SOURCE_LOGO_PATH, "_logo_img"),  # full master if resized logo missing
            (SOURCE_LOGO_PATH, "_mark_img"),
        ):
            if getattr(self, attr) is not None:
                continue
            if not os.path.isfile(path):
                continue
            try:
                img = tk.PhotoImage(file=path)
                # scale mark into ~40–48px range for the header (integer subsample only)
                if attr == "_mark_img" and img.height() > 48:
                    factor = max(1, img.height() // 48)
                    img = img.subsample(factor, factor)
                # keep logo mid-size; settings panel loads its own preview
                if attr == "_logo_img" and img.height() > 200:
                    factor = max(1, img.height() // 160)
                    img = img.subsample(factor, factor)
                setattr(self, attr, img)
            except Exception:
                pass

    def _draw_fallback_logo(self, canvas, t):
        """Vector logo if PNG missing."""
        # Works for ~40×40 or larger canvases
        canvas.create_oval(2, 2, 38, 38, fill="#0e1014", outline=t["border"], width=2)
        canvas.create_arc(5, 5, 35, 35, start=205, extent=-230, style="arc",
                          outline=t["elev"], width=4)
        canvas.create_arc(5, 5, 35, 35, start=205, extent=-95, style="arc",
                          outline=t["accent"], width=4)
        canvas.create_line(20, 22, 31, 10, fill=t["fg"], width=2, capstyle="round")
        canvas.create_oval(17, 19, 23, 25, fill=t["accent"], outline=t["fg"])

    def _header(self):
        t = self.theme
        xp = bool(t.get("xp"))
        # Luna title-bar chrome on Windows XP theme
        if xp:
            bar = t.get("title", "#0A246A")
            fg, dim, muted = "#FFFFFF", "#D6E8FF", "#A8C4E8"
            live_fg = "#FFE45A"
            div = t.get("title2", "#3A8CF0")
            ht = dict(
                t, bg=bar, panel=bar, panel2="#FFFFFF", elev="#D6E8FF",
                fg=fg, dim=dim, muted=muted, border=div,
            )
        else:
            bar = t["panel"]
            fg, dim, muted = t["fg"], t["dim"], t["muted"]
            live_fg = t.get("brand2", t["warn"])
            div = t["border"]
            ht = t

        wrap = tk.Frame(self, bg=bar, height=72)
        wrap.pack(fill="x")
        wrap.pack_propagate(False)
        if xp:
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
        inner.pack(fill="both", expand=True, padx=18, pady=10)

        self._load_logo_images()
        logo_wrap = tk.Frame(inner, bg=bar)
        logo_wrap.pack(side="left", pady=0)
        if self._mark_img is not None:
            tk.Label(logo_wrap, image=self._mark_img, bg=bar).pack()
        else:
            logo = tk.Canvas(logo_wrap, width=40, height=40, bg=bar, highlightthickness=0)
            logo.pack()
            self._draw_fallback_logo(logo, t)

        title = tk.Frame(inner, bg=bar)
        title.pack(side="left", padx=(10, 0))
        row = tk.Frame(title, bg=bar)
        row.pack(anchor="w")
        tk.Label(row, text="4G1", bg=bar, fg=fg,
                 font=ui_font(t, 17, bold=True)).pack(side="left")
        tk.Label(row, text="LIVE", bg=bar, fg=live_fg,
                 font=ui_font(t, 17, bold=True)).pack(side="left", padx=(4, 0))
        self.vehicle_lbl = tk.Label(
            title, text="MITSUBISHI LIVE ECU DATA", bg=bar, fg=dim,
            font=ui_font(t, 7),
        )
        self.vehicle_lbl.pack(anchor="w")

        tk.Frame(inner, bg=div, width=1).pack(side="left", fill="y", padx=18)
        profile = tk.Frame(inner, bg=bar)
        profile.pack(side="left")
        tk.Label(profile, text="VEHICLE / PROFILE", bg=bar, fg=muted,
                 font=ui_font(t, 7, bold=True)).pack(anchor="w")
        tk.Label(profile, text=self._active_profile()["short"], bg=bar, fg=fg,
                 font=ui_font(t, 10, bold=True)).pack(anchor="w", pady=(3, 0))

        tk.Frame(inner, bg=div, width=1).pack(side="left", fill="y", padx=18)
        conn = tk.Frame(inner, bg=bar)
        conn.pack(side="left")
        tk.Label(conn, text="CONNECTION", bg=bar, fg=muted,
                 font=ui_font(t, 7, bold=True)).pack(anchor="w")
        self.status_pill = tk.Label(
            conn, text="●  OFFLINE",
            bg=bar if xp else t["panel2"], fg=dim,
            font=ui_font(t, 9, bold=True),
            padx=10 if not xp else 0,
            pady=2 if not xp else 0,
        )
        if not xp:
            self.status_pill.configure(highlightbackground=t["border"], highlightthickness=1)
        self.status_pill.pack(anchor="w", pady=(3, 0))

        adapter = tk.Frame(inner, bg=bar)
        adapter.pack(side="left", padx=(28, 0))
        tk.Label(adapter, text="ADAPTER / ECU", bg=bar, fg=muted,
                 font=ui_font(t, 7, bold=True)).pack(anchor="w")
        self.dev_info = tk.Label(adapter, text="—", bg=bar, fg=dim,
                                 font=ui_font(t, 8, mono=True))
        self.dev_info.pack(anchor="w", pady=(3, 0))

        actions = tk.Frame(inner, bg=bar)
        actions.pack(side="right")
        self.connect_btn = SoftButton(actions, "Connect", self.on_connect, ht, primary=False)
        self.connect_btn.pack(side="left")
        action_theme = dict(ht)
        action_theme["accent"] = t["warn"]
        action_theme["accent_hi"] = "#FF9A3C"
        self.live_btn = SoftButton(actions, "▶  Start Live Data", self.toggle_live,
                                   action_theme, primary=True)
        self.live_btn.config(state="disabled")
        self.live_btn.pack(side="left", padx=(10, 0))

        self.demo_btn = SoftButton(actions, "Explore Demo", self.toggle_demo, ht,
                                   primary=False)
        self.demo_btn.pack(side="left", padx=(10, 0))

        about = tk.Label(actions, text=f"About  ·  v{VERSION}", bg=bar, fg=muted,
                         font=ui_font(t, 8), cursor="hand2")
        about.pack(side="left", padx=(14, 0))
        about.bind("<Button-1>", lambda _e: self._show_about())

        self.clock_lbl = tk.Label(inner, text="", bg=bar, fg=muted,
                                  font=ui_font(t, 8, mono=True))
        self.clock_lbl.pack(side="right", padx=14)
        if not xp:
            tk.Frame(self, bg=t["border"], height=1).pack(fill="x")

    def _toolbar(self):
        # Connection actions now live in the compact product header.
        pass

    def _tabbar(self):
        t = self.theme
        bar = tk.Frame(self, bg=t["panel"], height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._tab_btns = {}
        for key, label in (("dash", "Dashboard"), ("codes", "Codes"), ("settings", "Settings")):
            b = tk.Label(bar, text=f"    {label}    ", bg=t["panel"], fg=t["dim"],
                         font=ui_font(t, 9, bold=True), pady=11, cursor="hand2")
            b.pack(side="left", padx=(8 if key == "dash" else 0, 2))
            b.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tab_btns[key] = b
        self._tab_underline = tk.Frame(self, bg=t["panel"], height=2)
        self._tab_underline.pack(fill="x")
        self._tab_line = tk.Frame(self._tab_underline, bg=t["accent"], height=2, width=100)
        # placeholder; position updated on show
        tk.Frame(self, bg=t["border"], height=1).pack(fill="x")

    def _show_tab(self, key):
        self._active_tab = key
        t = self.theme
        for k, fr in (("dash", self.tab_dash), ("codes", self.tab_codes),
                      ("settings", self.tab_settings)):
            fr.pack_forget()
        {"dash": self.tab_dash, "codes": self.tab_codes,
         "settings": self.tab_settings}[key].pack(fill="both", expand=True)

        for k, b in self._tab_btns.items():
            if k == key:
                b.config(fg=t["accent"], bg=t.get("tab_sel", t["panel2"]))
            else:
                b.config(fg=t["dim"], bg=t["panel"])
        # accent underline under active tab
        self._tab_line.place_forget()
        try:
            b = self._tab_btns[key]
            self.update_idletasks()
            x = b.winfo_x()
            w = b.winfo_width()
            self._tab_line.configure(width=max(w, 40), bg=t["accent"])
            self._tab_line.place(x=x, y=0, height=2)
        except Exception:
            pass
        if key == "dash":
            self._fit_tries = 0
            self.after(120, self._autofit_dash)

    def _footer(self):
        t = self.theme
        foot = tk.Frame(self, bg=t["panel"])
        foot.pack(fill="x", side="bottom")
        tk.Frame(foot, bg=t["border"], height=1).pack(fill="x")
        inner = tk.Frame(foot, bg=t["panel"])
        inner.pack(fill="x", padx=10, pady=4)
        self.foot_left = tk.Label(inner, text=self._conn_summary(),
                                  bg=t["panel"], fg=t["muted"], font=ui_font(t, 7))
        self.foot_left.pack(side="left")
        self.foot_right = tk.Label(inner, text="Ready", bg=t["panel"], fg=t["muted"],
                                   font=ui_font(t, 7, mono=True))
        self.foot_right.pack(side="right")

    def _conn_summary(self):
        s = self.settings
        pin = "Pin1 GND" if s.get("pin1_ground", True) else "Pin1 off"
        prof = j2534.get_profile(s.get("vehicle_profile"))["short"]
        if sys.platform == "darwin":
            port = os.path.basename(s.get("dll_path") or "not detected")
            return (
                f"{prof}  ·  Serial  ·  {port}  ·  MUT-II  ·  "
                f"{s.get('mut_baud', 15625)} baud  ·  {pin}"
            )
        dll = os.path.basename(s.get("dll_path") or "op20pt32.dll")
        return (
            f"{prof}  ·  J2534  ·  {dll}  ·  MUT-II  ·  "
            f"{s.get('mut_baud', 15625)} baud  ·  {pin}"
        )

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
            tk.Label(head, image=self._mark_img, bg=t["panel"]).pack(side="left")
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
            PRODUCT_DESCRIPTION, "",
            COPYRIGHT, "",
            "Runs locally. Vehicle data is not uploaded or shared.",
            "Demo mode does not connect to a vehicle.", "",
            "For diagnostic assistance only. Confirm readings and repairs",
            "against the applicable factory service information.",
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

        self.idle_banner = tk.Frame(self.tab_dash, bg=t["elev"],
                                    highlightbackground=t["accent"], highlightthickness=1)
        ib = tk.Frame(self.idle_banner, bg=t["elev"])
        ib.pack(fill="x", padx=14, pady=8)
        tk.Label(ib, text="●", bg=t["elev"], fg=t["accent"],
                 font=ui_font(t, 11)).pack(side="left")
        tk.Label(ib, text="No live data yet  —  ", bg=t["elev"], fg=t["fg"],
                 font=ui_font(t, 9, bold=True)).pack(side="left")
        tk.Label(ib, text="press Connect to read your adapter, or try Explore Demo "
                 "to see 4G1 Live in action.", bg=t["elev"], fg=t["dim"],
                 font=ui_font(t, 9)).pack(side="left")

        # Bottom-anchored sections pack FIRST so they always keep their
        # space — shortfall pinches the middle, which _autofit_dash repairs.
        logwrap_outer, logwrap = card(self.tab_dash, t)
        logwrap_outer.pack(side="bottom", fill="both", expand=True, pady=(0, 4))
        bar = tk.Frame(logwrap, bg=t["panel"])
        bar.pack(fill="x")
        tk.Label(bar, text="TELEMETRY LOG", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(side="left", padx=12, pady=7)
        save = tk.Label(bar, text="  Save CSV  ", bg=t["panel2"], fg=t["fg"],
                        font=ui_font(t, 8, bold=True), cursor="hand2", pady=4)
        save.pack(side="right", padx=8, pady=4)
        save.bind("<Button-1>", lambda e: self.save_csv())
        clr = tk.Label(bar, text="  Clear  ", bg=t["elev"], fg=t["dim"],
                       font=ui_font(t, 8, bold=True), cursor="hand2", pady=4)
        clr.pack(side="right", pady=4)
        clr.bind("<Button-1>", lambda e: self.clear_log())
        self.logbox = tk.Text(
            logwrap, bg=t["chart"], fg=t["accent2"], relief="flat",
            font=ui_font(t, 8, mono=True), height=3, wrap="none",
            insertbackground=t["fg"], selectbackground=t["panel2"], padx=8, pady=5,
        )
        self.logbox.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        lower = tk.Frame(self.tab_dash, bg=t["bg"], height=self._scaled_px(105))
        lower.pack(side="bottom", fill="x", pady=(0, 6))
        lower.pack_propagate(False)
        self.chart_load = StripChart(lower, t, "Engine Load", "%",
                                     _param(0x1C)[3], accent=t["good"])
        self.chart_load.pack(side="left", fill="both", expand=True, padx=(0, 6))
        health_outer, health = card(lower, t, accent=t.get("good"))
        health_outer.pack(side="left", fill="both", expand=True)
        tk.Label(health, text="SESSION HEALTH", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=14, pady=(7, 5))
        metrics = tk.Frame(health, bg=t["panel"])
        metrics.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.hz_lbl = tk.Label(metrics, text="— Hz", bg=t["panel"], fg=t["muted"],
                               font=ui_font(t, 12, bold=True, mono=True))
        self.hz_lbl.pack(side="left", expand=True)
        self.n_lbl = tk.Label(metrics, text="0 samples", bg=t["panel"], fg=t["muted"],
                              font=ui_font(t, 10, mono=True))
        self.n_lbl.pack(side="left", expand=True)
        self.sess_lbl = tk.Label(metrics, text="Session —", bg=t["panel"], fg=t["muted"],
                                 font=ui_font(t, 10, mono=True))
        self.sess_lbl.pack(side="left", expand=True)

        primary = tk.Frame(self.tab_dash, bg=t["bg"], height=self._scaled_px(248))
        primary.pack(fill="x", pady=(8, 6))
        primary.pack_propagate(False)
        self._dash_primary = primary

        rpm_outer, rpm_panel = card(primary, t)
        rpm_outer.pack(side="left", fill="both", expand=True, padx=(0, 6))
        rpm_head = tk.Frame(rpm_panel, bg=t["panel"])
        rpm_head.pack(fill="x", padx=12, pady=(6, 3))
        tk.Label(rpm_head, text="RPM", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9, bold=True)).pack(side="left")
        tk.Label(rpm_head, text="ENGINE SPEED  ·  rpm", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 7)).pack(side="right")
        rpm_body = tk.Frame(rpm_panel, bg=t["panel"])
        rpm_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        rpm_tile = HeroTile(rpm_body, 0x21, t, width=self._scaled_px(150))
        rpm_tile.pack(side="left", fill="y", padx=(0, 6))
        rpm_tile.pack_propagate(False)
        rpm_tile.configure(width=self._scaled_px(150))
        self.heroes[0x21] = rpm_tile
        self.chart_rpm = StripChart(rpm_body, t, "RPM trend", "rpm",
                                    _param(0x21)[3], accent=t["accent"])
        self.chart_rpm.pack(side="left", fill="both", expand=True)

        critical = tk.Frame(primary, bg=t["bg"])
        critical.pack(side="left", fill="both", expand=True)
        for i, pid in enumerate([0x06, 0x07, 0x3A, 0x17, 0x14, 0x2F]):
            if pid not in j2534.ALL_PARAMS:
                continue
            tile = HeroTile(critical, pid, t, width=self._scaled_px(160), height=self._scaled_px(96))
            tile.grid(row=i // 3, column=i % 3, padx=3, pady=3, sticky="nsew")
            tile.grid_propagate(False)
            self.heroes[pid] = tile
        for c in range(3):
            critical.grid_columnconfigure(c, weight=1, uniform="critical")
        for r in range(2):
            critical.grid_rowconfigure(r, weight=1, uniform="critical")

        self.grid_frame = tk.Frame(self.tab_dash, bg=t["bg"], height=self._scaled_px(108))
        self.grid_frame.pack(fill="x", pady=(0, 6))
        self.grid_frame.pack_propagate(False)
        self._build_gauges()

        self._update_idle_banner()
        self._fit_tries = 0
        self.after(150, self._autofit_dash)

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
        h = self._scaled_px(102)
        w = self._scaled_px(220)
        for i, pid in enumerate(ids):
            g = Gauge(self.grid_frame, pid, self.theme, style, width=w, height=h)
            g.grid(row=i // cols, column=i % cols, padx=3, pady=0, sticky="nsew")
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
        wrap.pack(fill="both", expand=True, pady=10)

        codes_outer, codes_card = card(wrap, t)
        codes_outer.pack(side="left", fill="both", expand=True, padx=(0, 6))

        head = tk.Frame(codes_card, bg=t["panel"])
        head.pack(fill="x", padx=18, pady=(14, 4))
        left = tk.Frame(head, bg=t["panel"])
        left.pack(side="left")
        tk.Label(left, text="Diagnostic Trouble Codes", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 15, bold=True)).pack(anchor="w")
        tk.Label(left, text="Classic Mitsubishi 2-digit MUT-II fault mask",
                 bg=t["panel"], fg=t["muted"], font=ui_font(t, 8)).pack(anchor="w")

        bar = tk.Frame(codes_card, bg=t["panel"])
        bar.pack(anchor="w", padx=18, pady=12)
        SoftButton(bar, "Read Codes", self.read_codes, t, primary=False).pack(side="left")
        SoftButton(bar, "Clear Codes", self.clear_codes, t, primary=True).pack(side="left", padx=8)
        SoftButton(bar, "Sweep Requests", self.run_sweep, t, primary=False).pack(side="left")

        self.codes_box = tk.Text(
            codes_card, bg=t["chart"], fg=t["fg"], relief="flat",
            font=ui_font(t, 11, mono=True), height=16, insertbackground=t["fg"],
            selectbackground=t["panel2"], padx=16, pady=14,
        )
        self.codes_box.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.codes_box.insert("end", "Connect to the ECU, then press Read Codes.\n\n")
        self.codes_box.insert(
            "end",
            "Note: fault-code support varies by ECU. Codes are read from\n"
            "requests 0x38/0x39, which on some MUT-II ECUs carry live data\n"
            "instead. Treat an empty result as inconclusive, not as proof\n"
            "the vehicle is fault-free.\n\n")
        self.codes_box.insert("end", "Examples:\n")
        self.codes_box.insert("end", "  #11  Oxygen sensor\n")
        self.codes_box.insert("end", "  #21  Engine coolant temperature sensor\n")
        self.codes_box.insert("end", "  #22  Crankshaft position sensor\n")

        ref_outer, ref_card = card(wrap, t, accent=t.get("accent2"))
        ref_outer.pack(side="left", fill="y")
        ref_outer.configure(width=250)
        ref_outer.pack_propagate(False)
        tk.Label(ref_card, text="MUT-II CODE REFERENCE", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(ref_card, text="Full classic 2-digit fault list", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 7)).pack(anchor="w", padx=14, pady=(0, 8))
        ref_list = tk.Frame(ref_card, bg=t["panel"])
        ref_list.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        for code, _bit, desc in j2534.DTC_TABLE:
            row = tk.Frame(ref_list, bg=t["panel"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"#{code:02d}", bg=t["panel"], fg=t["accent"],
                     font=ui_font(t, 8, bold=True, mono=True), width=4,
                     anchor="w").pack(side="left")
            tk.Label(row, text=desc, bg=t["panel"], fg=t["dim"],
                     font=ui_font(t, 8), anchor="w").pack(side="left")

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
            if sys.platform == "darwin":
                canvas.yview_scroll(int(-1 * e.delta), "units")
            else:
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
        tk.Label(brand, text="BRAND", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=12, pady=(10, 4))
        preview = None
        for path in (LOGO_PATH, SOURCE_LOGO_PATH, MARK_PATH):
            if not os.path.isfile(path):
                continue
            try:
                preview = tk.PhotoImage(file=path)
                if preview.height() > 120:
                    factor = max(1, preview.height() // 100)
                    preview = preview.subsample(factor, factor)
                break
            except Exception:
                preview = None
        if preview is not None:
            self._settings_logo = preview
            tk.Label(brand, image=self._settings_logo, bg=t["panel"]).pack(pady=8)
        elif self._mark_img is not None:
            self._settings_logo = self._mark_img
            tk.Label(brand, image=self._settings_logo, bg=t["panel"]).pack(pady=8)
        tk.Label(brand, text="4G1 LIVE", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 12, bold=True)).pack()
        tk.Label(brand, text="Mitsubishi MUT-II", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(pady=(0, 12))

        # appearance
        card = tk.Frame(top, bg=t["panel"], highlightbackground=t["border"],
                        highlightthickness=1)
        card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Frame(card, bg=t["accent"], height=3).pack(fill="x")
        tk.Label(card, text="APPEARANCE", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=16, pady=(12, 8))

        sw = tk.Frame(card, bg=t["panel"])
        sw.pack(fill="x", padx=16, pady=(0, 8))
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        tk.Label(sw, text="Theme", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=16, anchor="w").pack(side="left")
        ttk.Combobox(sw, textvariable=self.theme_var, values=list(THEMES),
                     state="readonly", width=24).pack(side="left")

        style_row = tk.Frame(card, bg=t["panel"])
        style_row.pack(fill="x", padx=16, pady=4)
        tk.Label(style_row, text="Gauge style", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=16, anchor="w").pack(side="left")
        self.style_var = tk.StringVar(value=self.settings.get("style", "Digital"))
        ttk.Combobox(style_row, textvariable=self.style_var, values=GAUGE_STYLES,
                     state="readonly", width=24).pack(side="left")

        size_row = tk.Frame(card, bg=t["panel"])
        size_row.pack(fill="x", padx=16, pady=4)
        tk.Label(size_row, text="Gauge box size", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=16, anchor="w").pack(side="left")
        self.gauge_scale_var = tk.StringVar(value=str(self._gauge_scale_pct()))
        ttk.Combobox(
            size_row,
            textvariable=self.gauge_scale_var,
            values=["70", "80", "90", "100", "110", "120", "130", "140", "150", "160", "170"],
            state="readonly",
            width=8,
        ).pack(side="left")
        tk.Label(size_row, text="  %", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(side="left")

        row3 = tk.Frame(card, bg=t["panel"])
        row3.pack(fill="x", padx=16, pady=(6, 16))
        tk.Label(row3, text="Poll rate", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 10), width=12, anchor="w").pack(side="left")
        self.poll_var = tk.StringVar(value=str(self.settings.get("poll_hz", 12)))
        ttk.Combobox(row3, textvariable=self.poll_var,
                     values=["6", "8", "10", "12", "15", "20"],
                     state="readonly", width=18).pack(side="left")
        tk.Label(row3, text="  Hz target (live data)", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(side="left")

        # vehicle card — selectable profiles (4G15 12V primary, 4G93 MAF secondary)
        vcard = tk.Frame(top, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1, width=260)
        vcard.pack(side="left", fill="both")
        vcard.pack_propagate(False)
        tk.Frame(vcard, bg=t["accent"], height=3).pack(fill="x")
        tk.Label(vcard, text="VEHICLE PROFILE", bg=t["panel"], fg=t["dim"],
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
                f"Engine   {p['engine']}",
                f"Protocol {p['protocol']}",
                f"Region   {p['region']}",
                f"Airflow  {p['airflow']}",
                f"Boost    {p['boost']}",
                f"ECU      {p['ecu_hint']}",
            ):
                tk.Label(
                    self._profile_detail, text=line, bg=t["panel"], fg=t["fg"],
                    font=ui_font(t, 8, mono=True), anchor="w", wraplength=220, justify="left",
                ).pack(fill="x", pady=1)

        def _on_profile_pick(_e=None):
            pid = self._profile_id_by_name.get(self.profile_var.get(), j2534.DEFAULT_PROFILE_ID)
            _fill_profile_detail(pid)

        self.profile_var.trace_add("write", lambda *_: _on_profile_pick())
        _fill_profile_detail(cur["id"])

        tk.Label(
            vcard, text="Primary target: 4G15 12V\nPin 1 ground ON for CE",
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
        tk.Label(head, text="CONNECTION", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(side="left")
        conn_hint = ("Tactrix serial  ·  MUT-II session" if sys.platform == "darwin"
                     else "J2534 adapter  ·  MUT-II session")
        tk.Label(head, text=conn_hint, bg=t["panel"],
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
                advanced_button.config(text="▾  Advanced adapter settings")
            else:
                advanced.pack_forget()
                advanced_button.config(text="▸  Advanced adapter settings")

        advanced_button = tk.Label(
            advanced_toggle, text="▸  Advanced adapter settings",
            bg=t["panel2"], fg=t["dim"], font=ui_font(t, 9, bold=True),
            cursor="hand2", padx=10, pady=6,
        )
        advanced_button.pack(side="left")
        advanced_button.bind("<Button-1>", lambda _e: _toggle_advanced())
        tk.Label(advanced_toggle, text="Most users can leave these defaults unchanged.",
                 bg=t["panel"], fg=t["muted"], font=ui_font(t, 8)).pack(
                     side="left", padx=10)

        # Adapter path — DLL on Windows, serial port on macOS
        dll_row = tk.Frame(advanced, bg=t["panel"])
        dll_row.pack(fill="x", padx=16, pady=4)
        if sys.platform == "darwin":
            adapter_label = "Serial port"
            adapter_default = self.settings.get("dll_path") or _default_adapter_path()
        else:
            adapter_label = "J2534 DLL"
            adapter_default = self.settings.get("dll_path") or j2534.default_dll()
        tk.Label(dll_row, text=adapter_label, bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.dll_var = tk.StringVar(value=adapter_default)
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
        tk.Label(baud_row, text="MUT baud", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.baud_var = tk.StringVar(value=str(self.settings.get("mut_baud", 15625)))
        ttk.Combobox(baud_row, textvariable=self.baud_var, values=BAUD_PRESETS,
                     width=12).pack(side="left")
        tk.Label(baud_row, text="  CE / most MUT-II = 15625", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        # timeout + init attempts
        to_row = tk.Frame(advanced, bg=t["panel"])
        to_row.pack(fill="x", padx=16, pady=4)
        tk.Label(to_row, text="Request timeout", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.timeout_var = tk.StringVar(value=str(self.settings.get("request_timeout_ms", 400)))
        ttk.Combobox(to_row, textvariable=self.timeout_var,
                     values=["200", "300", "400", "500", "750", "1000"],
                     width=12).pack(side="left")
        tk.Label(to_row, text="  ms per MUT request", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        init_row = tk.Frame(advanced, bg=t["panel"])
        init_row.pack(fill="x", padx=16, pady=4)
        tk.Label(init_row, text="Init address", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.init_addr_var = tk.StringVar(value=str(self.settings.get("init_addr", "0x00")))
        ttk.Combobox(init_row, textvariable=self.init_addr_var,
                     values=["0x00", "0x33", "0x11"], width=12).pack(side="left")
        tk.Label(init_row, text="  Attempts", bg=t["panel"], fg=t["fg"],
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
            (self.pin1_var, "Ground OBD pin 1 (required for CE Lancer / Mirage)"),
            (self.auto_conn_var, "Auto-connect adapter on startup"),
            (self.auto_live_var, "Auto-start live data after connect"),
        ):
            tk.Checkbutton(
                opt, text=text, variable=var,
                bg=t["panel"], fg=t["fg"], selectcolor=t["elev"],
                activebackground=t["panel"], activeforeground=t["fg"],
                font=ui_font(t, 9), anchor="w",
            ).pack(anchor="w", pady=1)

        tip = tk.Label(
            ccard,
            text="Tip: leave Pin 1 grounded for CE. Evo 8/9 often work without it. "
                 "Changes apply on next Connect / Start Live Data.",
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
            gcard, text=f"PARAMETERS  ·  {prof['short']}  ·  CE MUT-II SCALINGS",
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8, bold=True),
        ).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(
            gcard,
            text="MUT-II formulas  ·  green/red LED = sensor health (FSM bands)  ·  tick to show",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        ).pack(anchor="w", padx=16, pady=(0, 4))
        legend = tk.Frame(gcard, bg=t["panel"])
        legend.pack(anchor="w", padx=16, pady=(0, 8))
        for col, lab in (
            (t["good"], "● green = optimal"),
            (t["warn"], "● amber = marginal"),
            (t["bad"], "● red = not optimal / fault-like"),
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
        if sys.platform == "darwin":
            path = filedialog.askopenfilename(
                title="Select serial port",
                initialdir="/dev",
            )
        else:
            path = filedialog.askopenfilename(
                title="Select J2534 DLL",
                filetypes=[("DLL files", "*.dll"), ("All files", "*.*")],
                initialdir=r"C:\Windows\SysWOW64",
            )
        if path:
            self.dll_var.set(path)

    def _detect_dll(self):
        if sys.platform == "darwin":
            import tactrix_mac
            devices = tactrix_mac.find_openport_usb()
            if not devices:
                messagebox.showwarning(
                    "4G1 Live",
                    "No Tactrix OpenPort 2.0 detected.\n\n"
                    "Connect the cable and check System Information → USB.",
                )
                return
            d = devices[0]
            port = d.get("port_path") or ""
            self.dll_var.set(port)
            info = (
                f"Tactrix {d['product']}\n"
                f"Serial: {d.get('serial', '—')}\n"
                f"Port: {port}\n"
                f"USB: {d['vendor_id']:04X}:{d['product_id']:04X}"
            )
            if len(devices) > 1:
                info += f"\n\n({len(devices)} devices found, using first)"
            messagebox.showinfo("4G1 Live — Tactrix Detected", info)
            return
        found = j2534.find_j2534_dlls()
        if not found:
            messagebox.showwarning("4G1 Live", "No known J2534 DLLs found.\nBrowse manually.")
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
        self.theme_var.set(name)
        self.settings["theme"] = name

    def _parse_init_addr(self, text):
        text = (text or "0x00").strip().lower()
        try:
            return int(text, 0)
        except Exception:
            return 0

    # ---------- settings apply ----------
    def apply_settings(self):
        self.settings["theme"] = self.theme_var.get()
        self.settings["style"] = self.style_var.get()
        try:
            self.settings["gauge_scale"] = max(70, min(170, int(self.gauge_scale_var.get())))
        except Exception:
            self.settings["gauge_scale"] = 100
        try:
            self.settings["poll_hz"] = int(self.poll_var.get())
        except Exception:
            self.settings["poll_hz"] = 12
        # vehicle profile (4G15 12V primary / 4G93 MAF secondary)
        if hasattr(self, "profile_var") and hasattr(self, "_profile_id_by_name"):
            pid = self._profile_id_by_name.get(
                self.profile_var.get(), j2534.DEFAULT_PROFILE_ID
            )
            self.settings["vehicle_profile"] = pid
        # connection
        self.settings["dll_path"] = self.dll_var.get().strip()
        try:
            self.settings["mut_baud"] = int(self.baud_var.get())
        except Exception:
            self.settings["mut_baud"] = 15625
        try:
            self.settings["request_timeout_ms"] = int(self.timeout_var.get())
        except Exception:
            self.settings["request_timeout_ms"] = 400
        self.settings["init_addr"] = self.init_addr_var.get().strip() or "0x00"
        try:
            self.settings["init_attempts"] = int(self.init_att_var.get())
        except Exception:
            self.settings["init_attempts"] = 5
        self.settings["pin1_ground"] = bool(self.pin1_var.get())
        self.settings["auto_connect"] = bool(self.auto_conn_var.get())
        self.settings["auto_live"] = bool(self.auto_live_var.get())
        # if already connected, update runtime timeout/baud on device object
        if self.dev:
            self.dev.request_timeout_ms = self.settings["request_timeout_ms"]
            self.dev.baud = self.settings["mut_baud"]

        chosen = [hex(pid) for pid, v in self.gauge_vars.items() if v.get()]
        if chosen:
            self.settings["gauges"] = chosen
        save_settings(self.settings)
        self.theme = dict(THEMES.get(self.settings["theme"], self.theme))
        self._resolve_theme_fonts()
        tab = self._active_tab
        self.rebuild_ui()
        self._show_tab(tab)

    def rebuild_ui(self):
        for w in self.winfo_children():
            w.destroy()
        self.configure(bg=self.theme["bg"])
        prof = self._active_profile()
        self.title(f"4G1 Live  —  {prof['short']}  ·  MUT-II")
        self.gauges = {}
        self.heroes = {}
        self._build()
        self._set_titlebar_colour()
        if self.dev:
            self.connect_btn.config(text="Disconnect", state="normal")
            self.live_btn.config(state="normal")
            if self.live:
                self._set_status("LIVE", "live")
                self.live_btn.config(text="Stop Live Data")
            else:
                self._set_status("CONNECTED", "ok")

    def _set_status(self, text, mode="off"):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self._set_status(text, mode))
            return
        self._status_mode = mode
        t = self.theme
        # Match header chrome (Luna blue on Windows XP)
        bar = t.get("title", t["panel"]) if t.get("xp") else t["panel"]
        dim = "#D6E8FF" if t.get("xp") else t["dim"]
        good = "#7CFF7C" if t.get("xp") else t["good"]
        live = "#FFE45A" if t.get("xp") else t["accent"]
        warn = "#FFB347" if t.get("xp") else t["warn"]
        styles = {
            "off":  (bar, dim, f"●  {text}"),
            "ok":   (bar, good, f"●  {text}"),
            "live": (bar, live, f"●  {text}"),
            "warn": (bar, warn, f"●  {text}"),
        }
        if t.get("xp"):
            bg, fg, label = styles.get(mode, styles["off"])
            self.status_pill.config(text=label, bg=bg, fg=fg)
            return

        non_xp = {
            "off":  (t["panel2"], t["dim"], t["border"], f"●  {text}"),
            "ok":   (_blend_hex(t["panel2"], t["good"], 0.10), t["good"],
                     _blend_hex(t["border"], t["good"], 0.35), f"●  {text}"),
            "live": (_blend_hex(t["panel2"], t["accent"], 0.12), t["accent_hi"],
                     _blend_hex(t["border"], t["accent"], 0.45), f"●  {text}"),
            "warn": (_blend_hex(t["panel2"], t["warn"], 0.12), t["warn"],
                     _blend_hex(t["border"], t["warn"], 0.35), f"●  {text}"),
        }
        bg, fg, bd, label = non_xp.get(mode, non_xp["off"])
        self.status_pill.config(text=label, bg=bg, fg=fg, highlightbackground=bd)

    def _status_tick(self):
        if self._closing:
            return
        try:
            if getattr(self, "_status_mode", "off") == "live" and hasattr(self, "status_pill"):
                t = self.theme
                phase = (getattr(self, "_status_phase", 0) + 1) % 10
                self._status_phase = phase
                pulse = 0.25 if phase < 5 else 0.05
                self.status_pill.config(
                    fg=_blend_hex(t["accent_hi"], t.get("brand2", t["accent"]), pulse)
                )
        except Exception:
            pass
        self._stale_data_watchdog()
        self.after(280, self._status_tick)

    def _stale_data_watchdog(self):
        """Catch a live session that has silently stopped producing samples.

        Belt and braces for the poll loop's own error handling: if the polling
        thread dies, hangs, or blocks on a wedged driver call, the dashboard
        would otherwise keep showing its last readings under a LIVE header.
        Anything older than a few seconds is not live data, so say so.
        """
        if self.demo or not self.live:
            self._stale_flagged = False
            return
        last = self._hz_times[-1] if self._hz_times else self._session_t0
        if last is None:
            return
        stale_for = time.monotonic() - last
        if stale_for < 5.0:
            self._stale_flagged = False
            return
        if getattr(self, "_stale_flagged", False):
            return
        self._stale_flagged = True
        self._live_stopped_by_fault(
            f"no data received for {stale_for:.0f}s — check the adapter and cable"
        )

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
                messagebox.showinfo(APP_NAME, "Stop Live Data before disconnecting the adapter.")
                return
            self._disconnect_adapter()
            return
        try:
            kw = self._conn_kwargs()
            if sys.platform == "win32" and not os.path.isfile(kw.get("dll_path", "")):
                raise j2534.J2534Error(f"DLL not found:\n{kw.get('dll_path')}\n\nSet path in Settings → Connection.")
            self.dev = j2534.create_device(**kw)
            self.dev.open()
            fw, dl, ap = self.dev.version()
            mv = self.dev.battery_mv() or 0
            self._set_status("CONNECTED", "ok")
            self.dev_info.config(text=f"fw {fw}   ·   {mv / 1000:.1f} V")
            self.connect_btn.config(text="Disconnect", state="normal")
            self.live_btn.config(state="normal")
            self.foot_right.config(text=f"Adapter ready  ·  {mv / 1000:.2f} V")
            try:
                self.foot_left.config(text=self._conn_summary())
            except Exception:
                pass
            self.log_line(f"Connected  ·  fw {fw}  ·  adapter {mv / 1000:.2f} V")
            self.log_line(f"DLL  ·  {kw['dll_path']}")
            self.log_line(f"Target  ·  {vehicle_line(self.settings)}")
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
        self.live_btn.config(text="Start Live Data", state="disabled")
        self.dev_info.config(text="—")
        self._set_status("OFFLINE", "off")
        self.foot_right.config(text="Adapter disconnected  ·  Ready")
        self.log_line("Adapter disconnected")

    def toggle_demo(self):
        """Run a realistic, clearly labelled dashboard demonstration."""
        if self.demo:
            self.live = False
            self.demo = False
            self.demo_btn.config(text="Explore Demo")
            self.connect_btn.config(state="normal")
            self.live_btn.config(text="Start Live Data", state="disabled")
            self._set_status("OFFLINE", "off")
            self.dev_info.config(text="—")
            self.foot_right.config(text="Demo ended  ·  Ready")
            self.log_line("Demo session ended")
            self._session_t0 = None
            self._update_idle_banner()
            return
        if self.live or self.dev:
            messagebox.showinfo(APP_NAME, "Disconnect the live session before starting Demo mode.")
            return
        self.demo = True
        self.live = True
        self.latest.clear()
        self.samples = []
        self._log_rows = []
        self._hz_times.clear()
        self._session_t0 = time.monotonic()
        self._autosave_start("demo")
        self.demo_btn.config(text="Stop Demo")
        self.connect_btn.config(state="disabled")
        self.live_btn.config(text="Demo Mode", state="disabled")
        self._set_status("DEMO", "warn")
        self.dev_info.config(text="SIMULATED DATA  ·  NO VEHICLE")
        self.log_line("Demo mode started — all values are simulated")
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
        # Switch byte 0x1B: one bit flicks periodically (bitfield decode pending)
        values[0x1B] = 0x08 if (int(elapsed) % 9) < 2 else 0x00
        # ECU-scaled temps mirror the raw NTC channels in demo
        values[0x10] = values[0x07]
        values[0x11] = values[0x3A]
        self.latest.update(values)
        snap = {"t": round(time.monotonic(), 3), "mode": "DEMO"}
        for pid in set(self.gauges) | set(self.heroes) | {0x1C, 0x21}:
            if pid in values:
                snap[_param(pid)[0]] = round(values[pid], 2)
        self.samples.append(snap)
        self._autosave_row(snap)
        self._hz_times.append(time.monotonic())
        if len(self._hz_times) >= 2:
            dt = self._hz_times[-1] - self._hz_times[0]
            self._poll_hz_actual = ((len(self._hz_times) - 1) / dt) if dt else 0
        self.after(max(50, int(1000 / max(1, self.settings.get("poll_hz", 10)))),
                   self._demo_tick)

    def toggle_live(self):
        if self.live:
            self.live = False
            self.live_btn.config(text="Start Live Data")
            self._update_idle_banner()
            return
        if not self.dev:
            messagebox.showwarning("4G1 Live", "Connect the adapter first.")
            return
        self.live = True
        self.live_btn.config(text="Stop Live Data")
        self.samples = []
        self._hz_times.clear()
        self._session_t0 = time.monotonic()
        self._autosave_start("live")
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
                f"MUT-II session  ·  {baud} baud  ·  {pin_txt}  ·  "
                f"init 0x{init_addr:02X} OK  ·  key {key_hex}"
            )
        except Exception as e:
            self.log_line(f"No ECU response — ignition on / run mode?  ({e})")
            self.live = False
            try:
                self.live_btn.config(text="Start Live Data")
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
        # Live polling uses a short per-request window: at 15625 baud a
        # single-byte answer lands in a few ms. The long timeout above is for
        # init/DTC work, not the hot loop.
        live_timeout = max(20, min(timeout, int(s.get("live_timeout_ms", 60))))
        last_sw_raw = None  # raw 0x1B byte — logged on change to bench-decode switch bits
        # Channels that physically cannot change fast (temps, baro, battery,
        # octane/knock learn) are polled every Nth cycle instead of every cycle.
        SLOW_PIDS = {0x07, 0x3A, 0x10, 0x11, 0x15, 0x14, 0x27, 0x12}
        SLOW_EVERY = 6
        misses = {}          # pid -> consecutive no-answer count
        dead = set()         # pids this ECU does not serve — stop asking
        cycle = 0
        comm_errors = 0      # consecutive hard adapter failures (not timeouts)
        stop_reason = None
        while self.live:
            t0 = time.monotonic()
            ids = set(self.gauges.keys()) | set(self.heroes.keys())
            ids.add(0x1C)
            ids.add(0x21)
            snap = {"t": round(t0, 3)}
            for pid in ids:
                if pid not in j2534.ALL_PARAMS or pid in dead:
                    continue
                if pid in SLOW_PIDS and (cycle % SLOW_EVERY) and pid in self.latest:
                    continue
                try:
                    raw = self.dev.mut2_request(pid, timeout=live_timeout)
                except Exception as e:
                    # A raised error means the adapter or driver itself failed —
                    # cable pulled, USB reset, driver fault. A no-answer timeout
                    # is a None return and is handled separately below.
                    comm_errors += 1
                    logging.exception("Adapter request failed (0x%02X)", pid)
                    if comm_errors >= 8:
                        stop_reason = f"adapter communication lost ({e})"
                        break
                    continue
                comm_errors = 0
                if raw is None:
                    misses[pid] = misses.get(pid, 0) + 1
                    if misses[pid] >= 12:
                        dead.add(pid)
                        name = _param(pid)[0]
                        self.log_line(
                            f"{name} (0x{pid:02X}) not answered by this ECU "
                            f"— dropped from polling to keep the rate up"
                        )
                    continue
                misses[pid] = 0
                if pid == 0x1B and raw != last_sw_raw:
                    last_sw_raw = raw
                    self.log_line(
                        f"Switch byte 0x1B  raw=0x{raw:02X}  bits={raw:08b}"
                    )
                name, scale, *_ = _param(pid)
                val = scale(raw)
                self.latest[pid] = val
                snap[name] = round(val, 2)
            if stop_reason:
                break
            self.samples.append(snap)
            self._autosave_row(snap)
            cycle += 1
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
        self._autosave_stop()
        self._session_t0 = None
        if stop_reason:
            # Never leave the dashboard sitting on frozen numbers while the
            # header still says LIVE — that reads as good data.
            self.live = False
            self.after(0, self._live_stopped_by_fault, stop_reason)
        else:
            self.log_line("Live data stopped")

    def _live_stopped_by_fault(self, reason):
        """End a live session that died on us, loudly and visibly."""
        self.live = False
        try:
            self.live_btn.config(text="Start Live Data")
        except Exception:
            pass
        self._set_status("COMMS LOST", "warn")
        self.log_line(f"LIVE DATA STOPPED — {reason}")
        self.log_line("Readings on screen are the last values received, not current.")
        self._update_idle_banner()

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
            self.n_lbl.config(text=f"{n} samples")
            if self.live and self._poll_hz_actual > 0:
                self.hz_lbl.config(text=f"{self._poll_hz_actual:.1f} Hz", fg=t["good"])
            else:
                self.hz_lbl.config(text="— Hz", fg=t["muted"])
            if self.live and self._session_t0:
                sec = int(time.monotonic() - self._session_t0)
                m, s = divmod(sec, 60)
                self.sess_lbl.config(text=f"Session {m:02d}:{s:02d}")
                self.foot_right.config(text=f"LIVE  ·  {n} samples  ·  {self._poll_hz_actual:.1f} Hz")
            elif not self.live:
                self.sess_lbl.config(text="Session —")
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

    def _autosave_start(self, tag="live"):
        """Open a crash-safe session CSV. Every sample is appended as it is
        captured, so a crash, a flat battery or an accidental restart can
        never lose a session again."""
        self._autosave_path = None
        self._autosave_cols = None
        self._autosave_fh = None
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            name = time.strftime(f"4g1_{tag}_%Y%m%d_%H%M%S.csv")
            path = os.path.join(SESSION_DIR, name)
            self._autosave_fh = open(path, "w", newline="", encoding="utf-8")
            self._autosave_path = path
            self.log_line(f"Auto-saving this session to {path}")
        except Exception:
            logging.exception("Could not open autosave file")
            self._autosave_fh = None

    def _autosave_row(self, snap):
        fh = getattr(self, "_autosave_fh", None)
        if fh is None:
            return
        try:
            if self._autosave_cols is None:
                self._autosave_cols = list(snap.keys())
                fh.write(",".join(self._autosave_cols) + "\n")
            for k in snap:
                if k not in self._autosave_cols:
                    self._autosave_cols.append(k)
            fh.write(",".join(str(snap.get(c, "")) for c in self._autosave_cols) + "\n")
            fh.flush()   # flush every row — the whole point is crash safety
        except Exception:
            logging.exception("Autosave row failed")
            try:
                fh.close()
            except Exception:
                pass
            self._autosave_fh = None

    def _autosave_stop(self):
        fh = getattr(self, "_autosave_fh", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        self._autosave_fh = None
        path = getattr(self, "_autosave_path", None)
        if path:
            self.log_line(f"Session saved: {path}")

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

    def run_sweep(self):
        """Sweep single-byte read requests to find channels this ECU serves.

        Two passes: one now, one after the user changes engine state (a rev,
        a load change). Channels whose raw byte moves between passes are live
        sensors — that is how the real MAP request gets identified.
        """
        if self.demo:
            messagebox.showinfo(APP_NAME, "Sweep needs a real ECU — demo data is simulated.")
            return
        if not self.dev:
            messagebox.showwarning(APP_NAME, "Connect the adapter first.")
            return
        if self.live:
            messagebox.showinfo(
                APP_NAME,
                "Stop Live Data first — the sweep needs exclusive use of the K-line.")
            return
        if not messagebox.askokcancel(
            f"{APP_NAME} — Request Sweep",
            "This asks the ECU for every single-byte read request in 0x00–0x9F "
            "and records what answers.\n\n"
            "Only read requests are sent. DTC/actuator command frames are never "
            "used, and 0x38/0x39/0xFE/0xFF are skipped.\n\n"
            "Even so, run this with the car STATIONARY, in neutral, handbrake on "
            "— idling or key-on. Not on track, not while driving.\n\n"
            "Two passes are taken. Between them you will be asked to change the "
            "engine state (blip the throttle) so live channels reveal themselves.\n\n"
            "Proceed?",
        ):
            return
        threading.Thread(target=self._sweep_worker, daemon=True).start()

    def _sweep_worker(self):
        ids = sweep.sweep_ids()
        names = {pid: j2534.ALL_PARAMS[pid][0] for pid in j2534.ALL_PARAMS}
        pin1 = False
        try:
            self._sweep_out("Opening MUT-II session for sweep…\n")
            pin1 = self._mut_session_start()
        except Exception as e:
            self._sweep_out(f"Could not open a MUT-II session: {e}\n")
            return
        passes = []
        try:
            self._sweep_out(f"Pass 1 of 2 — asking {len(ids)} request ids…\n")
            passes.append(sweep.sweep_once(self.dev, ids))
            answered = sum(1 for v in passes[0].values() if v is not None)
            self._sweep_out(f"Pass 1 done — {answered} ids answered.\n")
            self._sweep_wait.clear()
            self.after(0, self._sweep_prompt_pass2)
            if not self._sweep_wait.wait(timeout=180):
                self._sweep_out("Timed out waiting for pass 2 — sweep abandoned.\n")
                return
            self._sweep_out("Pass 2 of 2…\n")
            passes.append(sweep.sweep_once(self.dev, ids))
            self._sweep_out("Pass 2 done.\n\n")
        finally:
            try:
                self._mut_session_end(pin1)
            except Exception:
                pass
        rows = sweep.analyse(passes)
        self._sweep_out(sweep.format_report(rows, names) + "\n")
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            path = os.path.join(SESSION_DIR, time.strftime("4g1_sweep_%Y%m%d_%H%M%S.csv"))
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["request_id_hex", "known_name", "answers",
                            "changed", "spread", "pass1", "pass2"])
                for r in rows:
                    vals = list(r["values"]) + [None, None]
                    w.writerow([f"0x{r['id']:02X}", names.get(r["id"], ""),
                                r["answers"], r["changed"], r["spread"],
                                vals[0], vals[1]])
            self._sweep_out(f"\nSweep saved: {path}\n")
        except Exception:
            logging.exception("Could not save sweep results")

    def _sweep_prompt_pass2(self):
        messagebox.showinfo(
            f"{APP_NAME} — Sweep",
            "Pass 1 captured.\n\nNow change the engine state so live channels "
            "move: blip the throttle and hold a slightly higher idle, or turn "
            "the steering to load the pump.\n\nPress OK to take pass 2.")
        self._sweep_wait.set()

    def _sweep_out(self, text):
        self.after(0, lambda: (self.codes_box.insert("end", text),
                               self.codes_box.see("end")))

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
            # An empty fault mask is NOT proof of a healthy engine. DTC reads
            # use requests 0x38/0x39, and on some MUT-II ECUs those addresses
            # serve live data instead of a fault bitmask — on the CE 4G15 this
            # was developed against, 0x38 returned a constant 0.0 for an entire
            # running session. Never let this screen imply a clean bill of health.
            self.codes_box.insert("end", "No trouble codes returned.\n\n")
            self.codes_box.insert(
                "end",
                "This is NOT confirmation that the engine is fault-free.\n\n"
                "Fault codes are read from requests 0x38/0x39. On some MUT-II\n"
                "ECUs those addresses carry live data rather than a fault mask,\n"
                "so an empty result can mean either:\n"
                "   - no faults are stored, or\n"
                "   - this ECU does not report faults at those addresses.\n\n"
                "Confirm with a factory-level scan tool before declaring a\n"
                "vehicle fault-free. Use Sweep Requests to see what this ECU\n"
                "actually serves.\n")
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
