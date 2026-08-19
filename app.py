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
import baseline
import ai_assistant
import memory as ai_memory
import knowledge as ai_knowledge
import tactrix_log
import voice
import speech_vocab
from product import APP_NAME, APP_ID, VERSION, COPYRIGHT, PRODUCT_DESCRIPTION

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = getattr(sys, "_MEIPASS", SOURCE_DIR)
USER_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", SOURCE_DIR), APP_NAME
)
LOG_DIR = os.path.join(USER_DATA_DIR, "Logs")
# Every live session is streamed to its own CSV here as it is captured,
# so telemetry survives a crash, a stall or an accidental restart.
SESSION_DIR = os.path.join(USER_DATA_DIR, "Sessions")
TACTRIX_SESSION_DIR = os.path.join(USER_DATA_DIR, "Tactrix Sessions")
SETTINGS_PATH = os.path.join(USER_DATA_DIR, "settings.json")
LEGACY_SETTINGS_PATH = os.path.join(SOURCE_DIR, "settings.json")
# Learned per-car sensor baseline, rebuilt from Sessions CSVs — see baseline.py
BASELINE_PATH = os.path.join(USER_DATA_DIR, "baseline.json")
# Durable AI memory (name, car notes, lessons) — survives restarts; see memory.py
MEMORY_PATH = os.path.join(USER_DATA_DIR, "memory.json")
# xAI Collections manuals index — see knowledge.py
KNOWLEDGE_PATH = os.path.join(USER_DATA_DIR, "knowledge.json")
ICON_PATH = os.path.join(APP_DIR, "4g1.ico")          # window / taskbar (.ico)
LOGO_PATH = os.path.join(APP_DIR, "4g1_logo.png")     # settings brand preview
MARK_PATH = os.path.join(APP_DIR, "4g1_mark.png")     # header badge
SOURCE_LOGO_PATH = os.path.join(APP_DIR, "4g1.icon.png")  # master artwork

# Legacy Claude model ids from early personal builds — map to Grok on load.
_LEGACY_AI_MODELS = {
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4",
    "claude-3-5-sonnet-latest",
    "claude-3-opus-latest",
}


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
    "Windows XP": dict(
        # Exact colouring from the Edge 4G1 Live HTML mockup
        # (Windows XP Luna — grey edition). Gauges are mini XP windows.
        bg="#3A6EA5",                 # Luna desktop / workspace blue
        panel="#F4F4F4",              # mockup body grey
        panel2="#FFFFFF",             # white fields / selected tab face
        elev="#EFEFEF",               # toolbar / button face
        fg="#000000",
        dim="#0046D5",                # mockup legend / group headers
        muted="#555555",
        accent="#1049B5",             # mockup tile value blue
        accent_hi="#3F8CF3",          # Luna caption highlight
        accent2="#003C74",
        good="#0A7B26",
        warn="#B07000",
        bad="#CC0000",
        border="#0855DD",             # mockup window chrome
        chart="#FFFFFF",
        glow="#245EDC33",
        tab_sel="#FFFFFF",
        tab_bar="#EFEFEF",
        title="#0A246A",              # caption navy
        title2="#3F8CF3",             # caption top stop
        brand2="#FFE45A",             # "LIVE" gold on blue title bar
        value="#1049B5",              # live numbers
        win_border="#0855DD",
        button_face="#F2F2F2",
        button_hi="#FFF8E3",
        button_shadow="#BFBFBF",
        button_dk="#003C74",
        font="Tahoma",
        mono="Lucida Console",
        font_choices=("Tahoma", "MS Sans Serif", "Segoe UI"),
        mono_choices=("Lucida Console", "Consolas", "Courier New"),
        xp=True,
    ),
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


DEFAULTS = dict(
    theme="Windows XP",
    style="Dial",
    gauge_scale=100,
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
    # --- AI assistant (personal edition) ---
    ai_enabled=True,
    ai_model=ai_assistant.DEFAULT_MODEL,
    ai_user_name=ai_memory.DEFAULT_USER_NAME,
    ai_collection_id="",  # optional override / paste from console.x.ai
    voice_enabled=True,
)

# API keys live here — never commit. Loaded into os.environ at startup.
SECRETS_PATH = os.path.join(USER_DATA_DIR, "secrets.env")
SECRETS_EXAMPLE_PATH = os.path.join(SOURCE_DIR, "secrets.env.example")


def load_secrets():
    """Load KEY=VALUE pairs into os.environ (does not overwrite existing env).

    AI can also authenticate via Grok Build login (~/.grok/auth.json) — see
    ai_assistant.resolve_api_key() — so no manual console key is required.
    """
    for path in (SECRETS_PATH, os.path.join(SOURCE_DIR, "secrets.env"),
                 os.path.join(SOURCE_DIR, ".env")):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            logging.info("Loaded secrets from %s", path)
        except Exception:
            logging.exception("Could not read secrets from %s", path)
    # If secrets.env holds a rejected/garbage key, clear it so Grok login wins
    env_key = (os.environ.get("XAI_API_KEY") or "").strip()
    if env_key and not (env_key.startswith("xai-") or env_key.startswith("eyJ")):
        logging.info("Ignoring non-xAI XAI_API_KEY value")
        os.environ.pop("XAI_API_KEY", None)
    try:
        logging.info("AI auth: %s", ai_assistant.auth_status())
    except Exception:
        logging.exception("AI auth status check failed")


def save_secrets(updates):
    """Merge KEY=VALUE into SECRETS_PATH and os.environ. Empty values remove keys."""
    existing = {}
    if os.path.isfile(SECRETS_PATH):
        try:
            with open(SECRETS_PATH, encoding="utf-8-sig") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            logging.exception("Could not read existing secrets")
    for k, v in (updates or {}).items():
        v = (v or "").strip()
        if v:
            existing[k] = v
            os.environ[k] = v
        else:
            existing.pop(k, None)
            os.environ.pop(k, None)
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        lines = [
            "# 4G1 Live AI secrets — do not share or commit",
            f"# updated {time.strftime('%Y-%m-%d %H:%M')}",
        ]
        for k in sorted(existing):
            lines.append(f"{k}={existing[k]}")
        temp = SECRETS_PATH + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(temp, SECRETS_PATH)
    except Exception:
        logging.exception("Could not write secrets to %s", SECRETS_PATH)


def api_key_status():
    """Short UI line for whether Grok credentials are present."""
    try:
        auth = ai_assistant.auth_status()
    except Exception:
        auth = "unknown"
    mgmt = bool(os.environ.get("XAI_MANAGEMENT_API_KEY"))
    bits = [
        f"AI auth: {auth}",
        f"MANAGEMENT={'OK' if mgmt else 'optional until manuals'}",
    ]
    return "  ·  ".join(bits)


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
    # migrate Claude-era model setting to Grok
    if s.get("ai_model") in _LEGACY_AI_MODELS or not s.get("ai_model"):
        s["ai_model"] = ai_assistant.DEFAULT_MODEL
    # Promote old installed defaults to the current flagship, but never
    # override a model that the user explicitly chose in Settings.
    if (
        s.get("ai_model") in ("grok-4.5", "grok-4.20-0309-non-reasoning", "", None)
        and s.get("_ai_model_user_set") is not True
    ):
        s["ai_model"] = ai_assistant.DEFAULT_MODEL
    if not (s.get("ai_user_name") or "").strip():
        s["ai_user_name"] = ai_memory.DEFAULT_USER_NAME
    if s.get("theme") not in THEMES:
        s["theme"] = "Windows XP"
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
    """Flat button with hover + disabled states (classic 3D when theme is XP)."""

    def __init__(self, master, text, command, theme, primary=False, **kw):
        super().__init__(master, bg=theme.get("panel", theme["bg"]), **kw)
        self.theme = theme
        self.primary = primary
        self.command = command
        self._enabled = True
        self._pressed = False
        t = theme
        if t.get("xp"):
            # Classic Luna command buttons. Never inherit title-bar white
            # text — that made Connect / Live Data invisible on the navy header.
            if primary:
                self.bg0 = t.get("accent", "#245EDC")
                self.bg1 = t.get("accent_hi", "#3C8CF0")
                self.fg = "#FFFFFF"
            else:
                self.bg0 = t.get("button_face", "#FFFFFF")
                self.bg1 = t.get("button_hi", "#FFF8E3")
                self.fg = "#000000"
            self._disabled_bg = "#D4D0C8"
            self._disabled_fg = "#404040"
            self.lbl = tk.Label(
                self, text=text, bg=self.bg0, fg=self.fg,
                font=ui_font(t, 10, bold=True), padx=16, pady=5, cursor="hand2",
                relief="raised", bd=2,
            )
            self.lbl.pack()
            self.configure(highlightthickness=0, bd=0)
        else:
            if primary:
                self.bg0, self.bg1, self.fg = t["accent"], t["accent_hi"], "#ffffff"
            else:
                self.bg0, self.bg1, self.fg = t["panel2"], t["elev"], t["fg"]
            self.lbl = tk.Label(
                self, text=text, bg=self.bg0, fg=self.fg,
                font=ui_font(t, 9, bold=True), padx=14, pady=5, cursor="hand2",
            )
            self.lbl.pack()
            if not primary:
                self.configure(highlightbackground=t["border"], highlightthickness=1)
                self.lbl.configure(highlightthickness=0)
        # Bind only the label (the visible button). Fire command once on release
        # so XP press/sunken animation works without double-firing.
        self.lbl.bind("<Enter>", self._enter)
        self.lbl.bind("<Leave>", self._leave)
        self.lbl.bind("<ButtonPress-1>", self._press)
        self.lbl.bind("<ButtonRelease-1>", self._release_click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def _press(self, _=None):
        if not self._enabled:
            return
        self._pressed = True
        if self.theme.get("xp"):
            try:
                self.lbl.config(relief="sunken")
            except Exception:
                pass

    def _release_click(self, _=None):
        if self.theme.get("xp"):
            try:
                self.lbl.config(relief="raised")
            except Exception:
                pass
        if not self._enabled:
            return
        if getattr(self, "_pressed", False) and self.command:
            self._pressed = False
            try:
                self.command()
            except Exception:
                # Never let a button callback kill the whole app silently.
                logging.exception("SoftButton command failed: %s", getattr(self.lbl, "cget", lambda *_: "?")("text"))
                raise

    def _enter(self, _=None):
        if self._enabled:
            self.lbl.config(bg=self.bg1)

    def _leave(self, _=None):
        self._pressed = False
        if self._enabled:
            self.lbl.config(bg=self.bg0)
            if self.theme.get("xp"):
                try:
                    self.lbl.config(relief="raised")
                except Exception:
                    pass

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
                if t.get("xp"):
                    self.lbl.config(
                        bg=getattr(self, "_disabled_bg", "#D4D0C8"),
                        fg=getattr(self, "_disabled_fg", "#404040"),
                        cursor="arrow",
                    )
                else:
                    self.lbl.config(bg=t["elev"], fg=t["muted"], cursor="arrow")
        if kw:
            super().config(**kw)

    configure = config


# ---------------------------------------------------------------- status LED
def _health_colours(theme):
    """Colours for sensor-status LED: good / bad / warn / idle."""
    off = "#7a7a7a" if theme.get("xp") else theme.get("elev", "#172a38")
    return {
        "good": theme.get("good", "#34d399"),
        "bad": theme.get("bad", "#ff6474"),
        "warn": theme.get("warn", "#fbbf24"),
        None: theme.get("muted", "#647584"),
        "off": off,
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


# ---------------------------------------------------------------- xp caption bar
class XPGaugeTitle(tk.Canvas):
    """Luna caption that turns a gauge into an XP window (Edge mockup)."""

    _STOPS = ("#3F8CF3", "#2A6CD4", "#1049B5", "#1E5BC8", "#1653B8")

    def __init__(self, master, title, reqid, theme, health=False, height=20, **kw):
        super().__init__(master, height=height, highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self._title = title
        self._reqid = reqid
        self._health = health
        self._status = None
        self._h = height
        self.bind("<Configure>", lambda _e: self._paint())

    def set_status(self, status):
        if not self._health or status == self._status:
            return
        self._status = status
        self._paint()

    def _paint(self):
        self.delete("all")
        w = max(self.winfo_width(), 8)
        h = max(self.winfo_height(), self._h)
        stops = self._STOPS
        n = len(stops) - 1
        for y in range(h):
            f = y / max(1, h - 1)
            seg = min(n - 1, int(f * n))
            col = _blend_hex(stops[seg], stops[seg + 1], f * n - seg)
            self.create_line(0, y, w, y, fill=col)
        fnt = ui_font(self.theme, 8, bold=True)
        self.create_text(8, h // 2 + 1, text=self._title, anchor="w",
                         fill="#0A2464", font=fnt)
        self.create_text(7, h // 2, text=self._title, anchor="w",
                         fill="#FFFFFF", font=fnt)
        rx = w - 5
        if self._health:
            cols = _health_colours(self.theme)
            fill = cols.get(self._status, cols["off"])
            r, cy = 4, h // 2
            self.create_oval(rx - 2 * r, cy - r, rx, cy + r,
                             fill=fill, outline="#0A2464")
            rx -= (2 * r + 7)
        if self._reqid:
            self.create_text(rx, h // 2, text=self._reqid, anchor="e",
                             fill="#DCEAFF", font=ui_font(self.theme, 7))


class LcdReadout(tk.Frame):
    """Green LCD RPM tile from the Edge 4G1 Live mockup."""

    def __init__(self, master, theme, unit="rpm", **kw):
        super().__init__(
            master, bg="#0B0F0B",
            highlightbackground="#686868", highlightthickness=1, **kw,
        )
        inner = tk.Frame(self, bg="#0B0F0B")
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        row = tk.Frame(inner, bg="#0B0F0B")
        row.pack(expand=True)
        self.val = tk.Label(
            row, text="----", bg="#0B0F0B", fg="#39FF5C",
            font=ui_font(theme, 34, bold=True, mono=True),
        )
        self.val.pack(side="left")
        self.unit = tk.Label(
            row, text=unit, bg="#0B0F0B", fg="#3f7a4a",
            font=ui_font(theme, 9, mono=True),
        )
        self.unit.pack(side="left", padx=(8, 0), pady=(12, 0))
        self.shift = tk.Canvas(
            row, width=15, height=15, bg="#0B0F0B", highlightthickness=0,
        )
        self.shift.pack(side="left", padx=(8, 0), pady=(8, 0))
        self._paint_shift(False)

    def set_text(self, text, health=None, rpm=None):
        if health == "bad":
            fg = "#FF3B3B"
        elif health == "warn":
            fg = "#FFC63A"
        else:
            fg = "#39FF5C"
        self.val.config(text=text, fg=fg)
        if rpm is not None:
            self._paint_shift(float(rpm) >= 6800)

    def _paint_shift(self, on):
        self.shift.delete("all")
        fill = "#E81123" if on else "#3a2a2a"
        outline = "#8a0000" if on else "#555555"
        self.shift.create_oval(1, 1, 13, 13, fill=fill, outline=outline, width=2)


# ---------------------------------------------------------------- hero vital tile
class HeroTile(tk.Frame):
    """Large KPI tile for the top vitals strip."""

    def __init__(self, master, pid, theme, caption=True, **kw):
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
        self.lcd = None
        self.bar = None
        health = pid in j2534.HEALTH_PIDS
        xp = bool(theme.get("xp"))
        use_caption = bool(caption) and xp
        if use_caption:
            self.configure(
                highlightbackground=theme.get("win_border", "#0855DD"),
                highlightthickness=2, highlightcolor=theme.get("win_border", "#0855DD"),
                bd=0,
            )
            self.strip = XPGaugeTitle(
                self, name.upper(), f"0x{pid:02X}", theme, health=health)
            self.strip.pack(fill="x")
            self.led = self.strip if health else None
            body = tk.Frame(self, bg=theme["panel"])
            body.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        elif xp:
            self.configure(highlightthickness=0, bd=0, bg=theme["panel"])
            self.strip = None
            self.led = None
            body = tk.Frame(self, bg=theme["panel"])
            body.pack(fill="both", expand=True)
        else:
            self.configure(highlightbackground=theme["border"], highlightthickness=1)
            self.strip = tk.Frame(self, bg=theme["border"], height=2)
            self.strip.pack(fill="x")
            body = tk.Frame(self, bg=theme["panel"])
            body.pack(fill="both", expand=True, padx=12, pady=(6, 8))
            title_row = tk.Frame(body, bg=theme["panel"])
            title_row.pack(fill="x")
            tk.Label(title_row, text=name.upper(), bg=theme["panel"], fg=theme["dim"],
                     font=ui_font(theme, 8, bold=True), anchor="w").pack(side="left")
            if health:
                self.led = StatusLED(title_row, theme, size=9)
                self.led.pack(side="right", padx=(4, 0))
            else:
                self.led = None

        if xp and (pid == 0x21 or units == "rpm"):
            self.lcd = LcdReadout(body, theme, unit="rpm")
            self.lcd.pack(fill="both", expand=True)
            self.val = self.lcd.val
            self.unit = self.lcd.unit
        else:
            row = tk.Frame(body, bg=theme["panel"])
            row.pack(fill="x", pady=(0, 0))
            idle = theme.get("value", theme["fg"]) if xp else theme["muted"]
            self.val = tk.Label(row, text="----", bg=theme["panel"], fg=idle,
                                font=ui_font(theme, 26, bold=True, mono=True), anchor="w")
            self.val.pack(side="left", pady=(4, 0))
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
        col = t.get("value", t["fg"]) if t.get("xp") else t["fg"]
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
        if self.lcd is not None:
            self.lcd.set_text(txt, health=self._health, rpm=value)
        else:
            self.val.config(text=txt, fg=col)

        if self.led is not None:
            self.led.set_status(self._health)

        c = self.bar
        if c is None:
            return
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
        if not t.get("xp"):
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
        if theme.get("xp"):
            self.configure(
                highlightbackground=theme.get("win_border", "#0855DD"),
                highlightthickness=2, highlightcolor=theme.get("win_border", "#0855DD"),
                bd=0,
            )
        else:
            self.configure(highlightbackground=theme["border"], highlightthickness=1)
        self._build()

    def _build(self):
        t = self.theme
        health = self.pid in j2534.HEALTH_PIDS
        if t.get("xp"):
            self.strip = XPGaugeTitle(
                self, self.name.upper(), f"0x{self.pid:02X}", t, health=health)
            self.strip.pack(fill="x")
            body = tk.Frame(self, bg=t["panel"])
            body.pack(side="top", fill="both", expand=True)
            self.body = body
            self.led = self.strip if health else None
        else:
            self.strip = tk.Frame(self, bg=t["border"], height=2)
            self.strip.pack(fill="x")
            body = tk.Frame(self, bg=t["panel"])
            body.pack(side="left", fill="both", expand=True)
            self.body = body
            head = tk.Frame(body, bg=t["panel"])
            head.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(head, text=self.name.upper(), bg=t["panel"], fg=t["dim"],
                     font=ui_font(t, 8, bold=True), anchor="w").pack(side="left")
            if health:
                self.led = StatusLED(head, t, size=9)
                self.led.pack(side="right", padx=(6, 0))
            else:
                self.led = None
            unit_text = "" if self.units == "state" else self.units
            tk.Label(head, text=unit_text, bg=t["panel"], fg=t["muted"],
                     font=ui_font(t, 7), anchor="e").pack(side="right")

        idle = t.get("value", t["muted"]) if t.get("xp") else t["muted"]
        if self.style == "Digital":
            self.val = tk.Label(body, text="—", bg=t["panel"], fg=idle,
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
            return t.get("value", t["fg"]) if t.get("xp") else t["fg"]
        if self.gmin < 0:
            if abs(value) > self.gmax * 0.6:
                return t["warn"]
            return t.get("value", t["fg"]) if t.get("xp") else t["fg"]
        if self.name == "Coolant" and value >= 105:
            return t["bad"]
        if self.name == "Battery" and (value < 11.5 or value > 15.5):
            return t["warn"]
        if frac > 0.88:
            return t["warn"]
        if t.get("xp"):
            return t.get("value", "#1049B5")
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
        if not t.get("xp"):
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
        if theme.get("xp"):
            self.configure(relief="sunken", bd=2, highlightthickness=0)
        else:
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
def card(parent, theme, accent=None, title=None):
    """A panel with a subtle drop-shadow and coloured top accent strip.

    Returns (outer, panel): pack/grid `outer`, build content inside `panel`.
    On Windows XP theme: nested Luna window matching the Edge mockup.
    """
    t = theme
    outer = tk.Frame(parent, bg=t["bg"])
    if t.get("xp"):
        frame = tk.Frame(
            outer, bg=t["panel"],
            highlightbackground=t.get("win_border", "#0855DD"),
            highlightthickness=2,
            highlightcolor=t.get("win_border", "#0855DD"),
        )
        frame.pack(fill="both", expand=True)
        if title:
            XPGaugeTitle(frame, title, "", t, health=False, height=19).pack(fill="x")
        panel = tk.Frame(frame, bg=t["panel"])
        panel.pack(fill="both", expand=True)
    else:
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
        load_secrets()
        self.settings = load_settings()
        # Personal edition default: always land on Windows XP unless user picks another
        if self.settings.get("theme") not in THEMES:
            self.settings["theme"] = "Windows XP"
        self.theme = dict(THEMES.get(self.settings["theme"], THEMES["Windows XP"]))
        self._resolve_theme_fonts()
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        self.title(f"{APP_NAME}  —  {prof['short']}  ·  MUT-II")
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
        # Maximize after first paint — state('zoomed') during __init__ freezes
        # some Windows/OneDrive desktop sessions and looks like a crash.
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

        # --- AI assistant state (personal edition) ---
        self._baseline = baseline.load_baseline(BASELINE_PATH)
        if not self._baseline:
            self._rebuild_baseline()
        self._last_verdict = {}
        self._last_dev = {}
        self._explain_cooldown = {}
        self._explain_busy = False
        self._ai_alerts = []
        self._trend_flagged = {}

        # Durable long-term memory (name, car notes, lessons) + this-run transcript
        self._memory = ai_memory.load_memory(MEMORY_PATH)
        name = (self.settings.get("ai_user_name") or "").strip()
        if name:
            ai_memory.set_user_name(self._memory, name)
        self._session_turns = []  # [{role, content}] this run, for consolidate-on-exit
        self._memory_dirty = False
        self._consolidate_busy = False

        # Manuals knowledge base (xAI Collections)
        self._knowledge = ai_knowledge.load_state(KNOWLEDGE_PATH)
        override = (self.settings.get("ai_collection_id") or "").strip()
        if override and override != (self._knowledge.get("collection_id") or ""):
            ai_knowledge.set_collection_id(self._knowledge, override)
        seeded = ai_knowledge.seed_local_extracts(self._knowledge)
        if seeded or override:
            ai_knowledge.save_state(self._knowledge, KNOWLEDGE_PATH)

        # --- voice / chat I/O (personal edition) — see voice.py ---
        # Voice is started AFTER the window shows. Opening the mic/COM stack
        # during __init__ native-crashes or freezes some PCs (looks like a crash).
        self._ptt_active = False
        self._ask_busy = False
        self._voice_busy = False  # alias kept for older call sites
        self._chat_history = []  # multi-turn chat for this app session (typed + voice)
        self.voice = None

        logging.info("Building UI…")
        self._build()
        self.bind_all("<KeyPress-F9>", self._on_ptt_press)
        self.bind_all("<KeyRelease-F9>", self._on_ptt_release)
        self.after(50, self._safe_maximize)
        self.after(80, self._bring_to_front)
        self.after(300, self._set_titlebar_colour)
        self.after(100, self._refresh_ui)
        self.after(280, self._status_tick)
        self.after(1200, self._init_voice_deferred)
        self.after(60000, self._check_trend_reminders)
        # honour auto-connect / auto-live from settings
        if self.settings.get("auto_connect"):
            self.after(2000, self.on_connect)
        if self.settings.get("auto_live"):
            self.after(2800, self._auto_live_if_ready)
        logging.info("UI ready")

    def _safe_maximize(self):
        try:
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                logging.info("Could not maximize window (non-fatal)")

    def _bring_to_front(self):
        """Make sure the window is visible after launch (pythonw starts behind
        other apps often enough that it looks like a failed start)."""
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(450, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            logging.info("Could not force window to front (non-fatal)")

    def _init_voice_deferred(self):
        """Start voice after the main window is already visible."""
        if self._closing:
            return
        try:
            eng = voice.VoiceEngine()
            if eng.available:
                # Stage labels: Listening → Transcribing → Thinking (app) → Speaking
                eng.set_status_callback(
                    lambda msg: self.after(0, self._set_voice_status, msg)
                )
                self.voice = eng
                logging.info("Voice engine ready")
                # Prefer the live "Hold F9…" hint once mic is up
                self._set_voice_status("Hold F9 to ask a question")
            else:
                self.voice = None
                logging.warning("Voice engine unavailable: %s", eng._init_error)
        except Exception:
            logging.exception("Voice engine failed to start — voice features disabled")
            self.voice = None

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
        self.tab_ai = tk.Frame(self.body, bg=t["bg"])
        self.tab_settings = tk.Frame(self.body, bg=t["bg"])

        self._build_dash()
        self._build_codes()
        self._build_ai()
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
        """Window + taskbar icon from 4G1 badge artwork.

        Prefer a small PNG via iconphoto — large masters / PNG-in-ICO can
        misbehave on some Tk builds. iconbitmap is best-effort only.
        """
        # iconphoto first with a small safe image
        for path in (MARK_PATH, LOGO_PATH):
            if not os.path.isfile(path):
                continue
            try:
                img = tk.PhotoImage(file=path)
                # Tk PhotoImage can get unhappy with very large images on 32-bit
                while img.height() > 64 and img.width() > 64:
                    img = img.subsample(2, 2)
                self._app_icon = img
                self.iconphoto(True, self._app_icon)
                break
            except Exception:
                logging.exception("iconphoto failed for %s", path)
        try:
            if os.path.isfile(ICON_PATH):
                self.iconbitmap(default=ICON_PATH)
        except Exception:
            try:
                if os.path.isfile(ICON_PATH):
                    self.iconbitmap(ICON_PATH)
            except Exception:
                logging.info("iconbitmap unavailable for %s", ICON_PATH)

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
            # XP caption is navy blue — force white title text for readability
            text_hex = "#FFFFFF" if t.get("xp") else t.get("fg", "#ffffff")
            text = ctypes.c_int(colorref(text_hex))
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
        tk.Label(row, text=" AI", bg=bar, fg=("#7EC8FF" if xp else t.get("brand2", t["accent"])),
                 font=ui_font(t, 17, bold=True)).pack(side="left")
        self.vehicle_lbl = tk.Label(
            title, text="MITSUBISHI LIVE ECU DATA  ·  AI EDITION", bg=bar, fg=dim,
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

        if not xp:
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
            SoftButton(actions, "Analyse Log", self.analyse_tactrix_log, ht,
                       primary=False).pack(side="left", padx=(10, 0))

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
        """Grey Luna toolbar — Connect / Live Data stay readable (black on beige)."""
        t = self.theme
        if not t.get("xp"):
            return
        bar = tk.Frame(self, bg=t.get("elev", "#EFEFEF"), height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=t.get("elev", "#EFEFEF"))
        inner.pack(side="left", padx=8, pady=5)
        self.connect_btn = SoftButton(inner, "Connect", self.on_connect, t, primary=False)
        self.connect_btn.pack(side="left")
        live_theme = dict(t)
        live_theme["button_face"] = "#FFE45A"
        live_theme["button_hi"] = "#FFF3A0"
        self.live_btn = SoftButton(
            inner, "Start Live Data", self.toggle_live, live_theme, primary=False,
        )
        self.live_btn.config(state="disabled")
        self.live_btn.pack(side="left", padx=(8, 0))
        SoftButton(inner, "Analyse Log", self.analyse_tactrix_log, t, primary=False).pack(
            side="left", padx=(8, 0),
        )
        about = tk.Label(
            inner, text=f"About  ·  v{VERSION}", bg=t.get("elev", "#EFEFEF"),
            fg=t["dim"], font=ui_font(t, 8), cursor="hand2",
        )
        about.pack(side="left", padx=(14, 0))
        about.bind("<Button-1>", lambda _e: self._show_about())
        hint = tk.Label(
            bar, text="Connect adapter  →  Start Live Data",
            bg=t.get("elev", "#EFEFEF"), fg=t["dim"], font=ui_font(t, 8),
        )
        hint.pack(side="right", padx=12)

    def _tabbar(self):
        t = self.theme
        xp = bool(t.get("xp"))
        bar_bg = t.get("tab_bar", t["panel"]) if xp else t["panel"]
        bar = tk.Frame(self, bg=bar_bg, height=36 if xp else 42)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self._tab_btns = {}
        for key, label in (("dash", "Dashboard"), ("codes", "Codes"),
                           ("ai", "AI Assistant"), ("settings", "Settings")):
            if xp:
                # Classic Luna tabs: raised bevel, selected sits on white face
                b = tk.Label(
                    bar, text=f"  {label}  ", bg=t["elev"], fg=t["fg"],
                    font=ui_font(t, 9, bold=True), pady=6, padx=10, cursor="hand2",
                    relief="raised", bd=2,
                )
                b.pack(side="left", padx=(6 if key == "dash" else 1, 0), pady=(6, 0))
            else:
                b = tk.Label(bar, text=f"    {label}    ", bg=t["panel"], fg=t["dim"],
                             font=ui_font(t, 9, bold=True), pady=11, cursor="hand2")
                b.pack(side="left", padx=(8 if key == "dash" else 0, 2))
            b.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tab_btns[key] = b
        if xp:
            # no modern underline — selected tab face joins the content area
            self._tab_underline = tk.Frame(self, bg=t["panel"], height=0)
            self._tab_line = tk.Frame(self, bg=t["panel"], height=0, width=0)
            tk.Frame(self, bg=t.get("button_shadow", "#808080"), height=1).pack(fill="x")
        else:
            self._tab_underline = tk.Frame(self, bg=t["panel"], height=2)
            self._tab_underline.pack(fill="x")
            self._tab_line = tk.Frame(self._tab_underline, bg=t["accent"], height=2, width=100)
            tk.Frame(self, bg=t["border"], height=1).pack(fill="x")

    def _show_tab(self, key):
        self._active_tab = key
        t = self.theme
        xp = bool(t.get("xp"))
        for k, fr in (("dash", self.tab_dash), ("codes", self.tab_codes),
                      ("ai", self.tab_ai), ("settings", self.tab_settings)):
            fr.pack_forget()
        {"dash": self.tab_dash, "codes": self.tab_codes, "ai": self.tab_ai,
         "settings": self.tab_settings}[key].pack(fill="both", expand=True)

        for k, b in self._tab_btns.items():
            if xp:
                if k == key:
                    b.config(
                        fg=t["fg"], bg=t.get("tab_sel", t["panel2"]),
                        relief="raised", bd=2,
                    )
                else:
                    b.config(
                        fg=t.get("muted", t["dim"]), bg=t.get("elev", t["panel"]),
                        relief="raised", bd=1,
                    )
            else:
                if k == key:
                    b.config(fg=t["accent"], bg=t.get("tab_sel", t["panel2"]))
                else:
                    b.config(fg=t["dim"], bg=t["panel"])
        # accent underline under active tab (modern themes only)
        if not xp and getattr(self, "_tab_line", None) is not None:
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
        foot_bg = t.get("elev", t["panel"]) if t.get("xp") else t["panel"]
        foot = tk.Frame(self, bg=foot_bg)
        foot.pack(fill="x", side="bottom")
        if t.get("xp"):
            tk.Frame(foot, bg=t.get("button_hi", "#FFFFFF"), height=1).pack(fill="x")
            tk.Frame(foot, bg=t.get("button_shadow", "#808080"), height=1).pack(fill="x")
        else:
            tk.Frame(foot, bg=t["border"], height=1).pack(fill="x")
        inner = tk.Frame(foot, bg=foot_bg)
        inner.pack(fill="x", padx=10, pady=4)
        self.foot_left = tk.Label(inner, text=self._conn_summary(),
                                  bg=foot_bg, fg=t["muted"], font=ui_font(t, 7))
        self.foot_left.pack(side="left")
        self.foot_right = tk.Label(inner, text="Ready", bg=foot_bg, fg=t["muted"],
                                   font=ui_font(t, 7, mono=True))
        self.foot_right.pack(side="right")

    def _conn_summary(self):
        s = self.settings
        dll = os.path.basename(s.get("dll_path") or "op20pt32.dll")
        pin = "Pin1 GND" if s.get("pin1_ground", True) else "Pin1 off"
        prof = j2534.get_profile(s.get("vehicle_profile"))["short"]
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
        tk.Label(ib, text="connect an adapter with the ignition in RUN to begin live diagnostics.",
                 bg=t["elev"], fg=t["dim"], font=ui_font(t, 9)).pack(side="left")

        # Persistent, glanceable AI alert strip — meant to be readable from
        # across the truck on a big screen mid-race, not tucked in a tab.
        self.ai_banner = tk.Frame(self.tab_dash, bg=t["elev"],
                                  highlightbackground=t.get("accent2", t["accent"]),
                                  highlightthickness=1)
        self.ai_banner.pack(fill="x", pady=(0, 6))
        self._ai_banner_rows = []
        for _ in range(3):
            row = tk.Label(self.ai_banner, text="", bg=t["elev"], fg=t["fg"],
                           font=ui_font(t, 11, bold=True), anchor="w",
                           justify="left", wraplength=1000)
            row.pack(fill="x", padx=14, pady=(4, 4))
            self._ai_banner_rows.append(row)
        self._render_ai_banner()

        # Push-to-talk hint / live status — hold F9 anywhere in the app to ask
        # a question by voice; see _on_ptt_press/_on_ptt_release in app.py.
        voice_hint = "Hold F9 to ask a question" if self.voice is not None else \
            "Voice unavailable on this machine"
        self.voice_status_lbl = tk.Label(
            self.tab_dash, text=voice_hint, bg=t["bg"], fg=t["muted"],
            font=ui_font(t, 9), anchor="w",
        )
        self.voice_status_lbl.pack(fill="x", pady=(0, 4))

        # Bottom-anchored sections pack FIRST so they always keep their
        # space — shortfall pinches the middle, which _autofit_dash repairs.
        logwrap_outer, logwrap = card(self.tab_dash, t, title="Telemetry Log")
        logwrap_outer.pack(side="bottom", fill="both", expand=True, pady=(0, 4))
        bar = tk.Frame(logwrap, bg=t["panel"])
        bar.pack(fill="x")
        if not t.get("xp"):
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
        health_outer, health = card(lower, t, accent=t.get("good"), title="Session Health")
        health_outer.pack(side="left", fill="both", expand=True)
        if not t.get("xp"):
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

        rpm_outer, rpm_panel = card(primary, t, title="Engine RPM")
        rpm_outer.pack(side="left", fill="both", expand=True, padx=(0, 6))
        if not t.get("xp"):
            rpm_head = tk.Frame(rpm_panel, bg=t["panel"])
            rpm_head.pack(fill="x", padx=12, pady=(6, 3))
            tk.Label(rpm_head, text="RPM", bg=t["panel"], fg=t["fg"],
                     font=ui_font(t, 9, bold=True)).pack(side="left")
            tk.Label(rpm_head, text="ENGINE SPEED  ·  rpm", bg=t["panel"], fg=t["muted"],
                     font=ui_font(t, 7)).pack(side="right")
        rpm_body = tk.Frame(rpm_panel, bg=t["panel"])
        rpm_body.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        rpm_tile = HeroTile(rpm_body, 0x21, t, caption=False)
        rpm_tile.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.heroes[0x21] = rpm_tile
        self.chart_rpm = StripChart(rpm_body, t, "RPM trend", "rpm",
                                    _param(0x21)[3], accent=t["accent"])
        self.chart_rpm.pack(side="left", fill="both", expand=True)

        critical = tk.Frame(primary, bg=t["bg"])
        critical.pack(side="left", fill="both", expand=True)
        for i, pid in enumerate([0x06, 0x07, 0x3A, 0x17, 0x14, 0x2F]):
            if pid not in j2534.ALL_PARAMS:
                continue
            tile = HeroTile(critical, pid, t, width=self._scaled_px(160), height=self._scaled_px(110))
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
            # Above the AI banner too — connecting an adapter is the next step
            # thing that matters before any data exists at all.
            before_widget = getattr(self, "ai_banner", None) or getattr(self, "_dash_primary", None)
            if before_widget is not None and before_widget.winfo_exists():
                banner.pack(fill="x", pady=(8, 4), before=before_widget)
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
            "Codes are read from the ECU's fault registers: 0x36 active\n"
            "fault count, with 0x47/0x48 as the active fault bitmask.\n"
            "(Corrected 18/8/26 — this previously read 0x38/0x39, which are\n"
            "pressure channels, not fault registers.)\n\n"
            "The COUNT is reliable. The bit-to-code mapping is not yet\n"
            "verified on this ECU, so individual code names are provisional\n"
            "until confirmed against a deliberately induced fault.\n\n")
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

    # ---------- AI assistant (personal edition) ----------
    def _build_ai(self):
        t = self.theme
        wrap = tk.Frame(self.tab_ai, bg=t["bg"])
        wrap.pack(fill="both", expand=True, pady=10)

        outer, panel = card(wrap, t, accent=t.get("accent2"))
        outer.pack(fill="both", expand=True)

        head = tk.Frame(panel, bg=t["panel"])
        head.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(head, text="AI Assistant", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 15, bold=True)).pack(anchor="w")
        tk.Label(
            head,
            text="Grok-powered pit mate. Remembers you and this car across restarts, "
                 "learns sensor normals from session CSVs, searches uploaded manuals "
                 "(xAI Collections), and explains warn/bad or baseline drift. "
                 "Type below or hold F9. Grok login or XAI_API_KEY.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8), wraplength=760, justify="left",
        ).pack(anchor="w", pady=(2, 4))
        tk.Label(
            head,
            text=ai_memory.memory_stats(self._memory),
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8),
        ).pack(anchor="w", pady=(0, 2))
        self.ai_knowledge_lbl = tk.Label(
            head,
            text=ai_knowledge.stats(self._knowledge),
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8),
        )
        self.ai_knowledge_lbl.pack(anchor="w", pady=(0, 6))

        # Show the learned baseline right away — it's built from real logged
        # sessions at startup, so there's something real to see here even
        # before this run has captured a single live sample.
        baseline_frame = tk.Frame(panel, bg=t["panel"])
        baseline_frame.pack(fill="x", padx=18, pady=(0, 10))
        tk.Label(
            baseline_frame,
            text=f"LEARNED BASELINE  ·  {len(self._baseline)} parameters from your logged sessions",
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8, bold=True),
        ).pack(anchor="w")
        if self._baseline:
            name_to_units = {meta[0]: meta[2] for meta in j2534.ALL_PARAMS.values()}
            grid = tk.Frame(baseline_frame, bg=t["panel"])
            grid.pack(fill="x", pady=(4, 0))
            for i, (name, buckets) in enumerate(sorted(self._baseline.items())):
                units = name_to_units.get(name, "")
                parts = [
                    f"{bucket} {stats['mean']:.1f}±{stats['stdev']:.1f}"
                    for bucket, stats in buckets.items()
                ]
                cell = tk.Frame(grid, bg=t["panel"])
                cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 20), pady=1)
                tk.Label(cell, text=f"{name} ({units})", bg=t["panel"], fg=t["fg"],
                         font=ui_font(t, 8, bold=True)).pack(side="left")
                tk.Label(cell, text=f"  {'  ·  '.join(parts) or '—'}", bg=t["panel"], fg=t["muted"],
                         font=ui_font(t, 8)).pack(side="left")
        else:
            tk.Label(
                baseline_frame,
                text="No history yet — log a few sessions, then Rebuild Baseline in Settings.",
                bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
            ).pack(anchor="w", pady=(2, 0))

        log_relief = "sunken" if t.get("xp") else "flat"
        log_bd = 2 if t.get("xp") else 0
        self.ai_log = tk.Text(
            panel, bg=t["chart"], fg=t["fg"], relief=log_relief, bd=log_bd,
            font=ui_font(t, 10, mono=True), height=12, insertbackground=t["fg"],
            selectbackground=t.get("accent", t["panel2"]) if t.get("xp") else t["panel2"],
            selectforeground="#FFFFFF" if t.get("xp") else t["fg"],
            padx=16, pady=14, wrap="word",
        )
        self.ai_log.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        who = (self._memory.get("user_name") or ai_memory.DEFAULT_USER_NAME)
        n_docs = len(self._knowledge.get("documents") or [])
        manuals_bit = (
            f"{n_docs} manual(s) linked — ask for factory specs anytime."
            if n_docs else
            "No manuals yet — use Upload Manual below or Settings."
        )
        self.ai_log.insert(
            "end",
            f"Hey {who} — 4G1 AI is awake. Baseline + memory loaded. {manuals_bit}\n"
            f"Type a question below, or hold F9 to speak.\n",
        )

        # Typed chat input
        chat_row = tk.Frame(panel, bg=t["panel"])
        chat_row.pack(fill="x", padx=18, pady=(0, 8))
        self.ai_chat_var = tk.StringVar()
        entry_kw = dict(
            textvariable=self.ai_chat_var,
            bg=t["panel2"] if t.get("xp") else t["elev"],
            fg=t["fg"], insertbackground=t["fg"],
            font=ui_font(t, 10),
        )
        if t.get("xp"):
            entry_kw.update(relief="sunken", bd=2)
        else:
            entry_kw.update(relief="flat")
        self.ai_chat_entry = tk.Entry(chat_row, **entry_kw)
        self.ai_chat_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.ai_chat_entry.bind("<Return>", lambda _e: self._on_chat_send())
        SoftButton(chat_row, "Send", self._on_chat_send, t, primary=True).pack(side="left")

        tools_row = tk.Frame(panel, bg=t["panel"])
        tools_row.pack(fill="x", padx=18, pady=(0, 14))
        SoftButton(tools_row, "Upload Manual…", self._upload_manual, t, primary=False).pack(
            side="left", padx=(0, 8),
        )
        SoftButton(tools_row, "Analyse Tactrix Log…", self.analyse_tactrix_log, t,
                   primary=False).pack(side="left", padx=(0, 8))
        SoftButton(tools_row, "Save Memory Now", self._force_consolidate_memory, t,
                   primary=False).pack(side="left")
        SoftButton(tools_row, "Get PDF Management Key", self._open_mgmt_console, t,
                   primary=False).pack(side="left", padx=(8, 0))
        mgmt_ok = bool((os.environ.get("XAI_MANAGEMENT_API_KEY") or "").strip())
        tk.Label(
            tools_row,
            text=("  PDF key OK — cloud + local ingest")
            if mgmt_ok else
            "  PDF management key REQUIRED for cloud search — Get key, paste in Settings",
            bg=t["panel"], fg=(t["good"] if mgmt_ok else t["bad"]),
            font=ui_font(t, 8, bold=True),
        ).pack(side="left")

    def _render_ai_banner(self):
        rows = getattr(self, "_ai_banner_rows", None)
        if not rows:
            return
        t = self.theme
        alerts = list(reversed(self._ai_alerts[-len(rows):]))
        if not alerts:
            rows[0].config(text="AI Assistant — watching sensor health, nothing to report yet.",
                           fg=t["muted"])
            rows[0].pack(fill="x", padx=14, pady=(4, 4))
            for row in rows[1:]:
                row.pack_forget()
            return
        for row, alert in zip(rows, alerts):
            if alert["verdict"] == "bad":
                colour = t["bad"]
            elif alert["verdict"] == "warn":
                colour = t.get("warn", t["accent"])
            else:
                colour = t.get("accent2", t["accent"])
            row.config(text=f"⚠ {alert['name']}: {alert['text']}", fg=colour)
            row.pack(fill="x", padx=14, pady=(4, 4))
        for row in rows[len(alerts):]:
            row.pack_forget()

    def _add_ai_alert(self, reading, text):
        self._ai_alerts.append({"name": reading.name, "verdict": reading.verdict, "text": text})
        self._ai_alerts = self._ai_alerts[-20:]
        self._render_ai_banner()
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            log.insert("end", f"[{stamp}] {reading.name} ({reading.verdict}): {text}\n\n")
            log.see("end")
        try:
            ai_memory.record_alert(self._memory, reading.name, reading.verdict, text)
            self._session_turns.append({
                "role": "assistant",
                "content": f"[alert {reading.name}/{reading.verdict}] {text}",
            })
            self._memory_dirty = True
            ai_memory.save_memory(self._memory, MEMORY_PATH)
        except Exception:
            logging.exception("Failed to record AI alert into memory")
        if self.voice is not None and self.settings.get("voice_enabled", True):
            self.voice.speak(text)

    def _memory_brief(self):
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        base = ai_memory.format_for_prompt(
            self._memory,
            vehicle_profile_id=self.settings.get("vehicle_profile"),
            vehicle_label=prof.get("name"),
        )
        manuals = ai_knowledge.format_for_prompt(self._knowledge)
        brief = base + "\n\n" + manuals
        last = getattr(self, "_last_log_report", None)
        if last:
            brief += "\n\nMost recently analysed Tactrix/4G1 log (use if they ask about the log):\n"
            brief += last[:3500]
        return brief

    def _collection_ids(self):
        return ai_knowledge.collection_ids(self._knowledge)

    _EXPLAIN_COOLDOWN_S = 60.0

    def _maybe_explain_transitions(self, ctx):
        """State-change trigger: only fires on a NEW warn/bad verdict or a
        freshly-crossed baseline deviation — never on every poll tick.
        """
        if not self.settings.get("ai_enabled", True):
            return
        state = j2534.engine_state(ctx)
        for pid in j2534.HEALTH_PIDS:
            if pid not in ctx:
                continue
            value = ctx[pid]
            verdict = j2534.sensor_health(pid, value, ctx=ctx)
            name = _param(pid)[0]
            z = baseline.deviation(self._baseline, name, value, state)
            is_dev = z is not None and abs(z) >= 3.0
            prev_verdict = self._last_verdict.get(pid)
            prev_dev = self._last_dev.get(pid, False)
            trigger = (verdict in ("warn", "bad") and verdict != prev_verdict) or (
                is_dev and not prev_dev)
            self._last_verdict[pid] = verdict
            self._last_dev[pid] = is_dev
            if trigger:
                self._request_explanation(pid, name, value, verdict or "deviation", z, ctx)

    def _request_explanation(self, pid, name, value, verdict, z, ctx):
        now = time.monotonic()
        if self._explain_busy or now - self._explain_cooldown.get(pid, 0.0) < self._EXPLAIN_COOLDOWN_S:
            return
        self._explain_cooldown[pid] = now
        self._explain_busy = True
        ctx_readings = {}
        for other_pid, other_val in ctx.items():
            if other_pid == pid or other_pid not in j2534.HEALTH_PIDS:
                continue
            ctx_readings[_param(other_pid)[0]] = other_val
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        reading = ai_assistant.Reading(
            name=name, value=value, units=_param(pid)[2], verdict=verdict,
            note=j2534.PARAM_NOTES.get(pid, ""),
            criteria=j2534.HEALTH_CRITERIA.get(pid, ""),
            baseline_z=z, ctx_readings=ctx_readings, vehicle_profile=prof["name"],
            engine_state=j2534.engine_state(ctx),
        )
        model = self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL)
        brief = self._memory_brief()
        threading.Thread(
            target=self._explain_worker, args=(reading, model, brief), daemon=True,
        ).start()

    def _explain_worker(self, reading, model, memory_brief):
        text = None
        try:
            text = ai_assistant.explain(reading, model=model, memory_brief=memory_brief)
        except Exception:
            logging.exception("AI explanation request failed")
        self.after(0, self._explain_done, reading, text)

    def _explain_done(self, reading, text):
        self._explain_busy = False
        if not text:
            text = (
                f"{reading.note or reading.criteria or 'Outside its normal band.'} "
                f"(AI explanation unavailable — check connectivity / XAI_API_KEY.)"
            )
        self._add_ai_alert(reading, text)

    _TREND_INTERVAL_MS = 180000  # 3 minutes — a trend, not an instant spike
    _TREND_MIN_SAMPLES = 30
    _TREND_Z_THRESHOLD = 2.5
    _TREND_RECHECK_S = 600.0  # don't re-flag the same parameter inside 10 min

    def _check_trend_reminders(self):
        try:
            if (self.live and self.settings.get("ai_enabled", True)
                    and len(self.samples) >= self._TREND_MIN_SAMPLES):
                recent = self.samples[-self._TREND_MIN_SAMPLES:]
                ctx = dict(self.latest)
                state = j2534.engine_state(ctx)
                for pid in j2534.HEALTH_PIDS:
                    name = _param(pid)[0]
                    vals = [s[name] for s in recent if s.get(name) is not None]
                    if len(vals) < self._TREND_MIN_SAMPLES // 2:
                        continue
                    avg = sum(vals) / len(vals)
                    z = baseline.deviation(self._baseline, name, avg, state)
                    if z is None or abs(z) < self._TREND_Z_THRESHOLD:
                        continue
                    now = time.monotonic()
                    if now - self._trend_flagged.get(name, 0.0) < self._TREND_RECHECK_S:
                        continue
                    self._trend_flagged[name] = now
                    self._request_explanation(pid, name, avg, "trend", z, ctx)
        finally:
            self.after(self._TREND_INTERVAL_MS, self._check_trend_reminders)

    def _rebuild_baseline(self):
        try:
            self._baseline = baseline.build_baseline(SESSION_DIR)
            baseline.save_baseline(self._baseline, BASELINE_PATH)
            log = getattr(self, "log_line", None)
            if log is not None:
                log(f"Baseline rebuilt from {SESSION_DIR}")
        except Exception:
            logging.exception("Baseline rebuild failed")

    def _rebuild_baseline_and_refresh(self):
        """Settings-tab 'Rebuild Baseline' button: rebuild, then reflect the
        new parameter count in the label right away."""
        self._rebuild_baseline()
        lbl = getattr(self, "ai_baseline_lbl", None)
        if lbl is not None:
            lbl.config(text=f"Learned baseline: {len(self._baseline)} parameters from logged sessions")

    def _test_ai_api(self):
        """Settings 'Test API' — verify credentials + model with a live ping."""
        model = (
            self.ai_model_var.get()
            if hasattr(self, "ai_model_var")
            else self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL)
        )
        status = getattr(self, "ai_keys_status_lbl", None)
        if status is not None:
            status.config(text=f"Testing {model}…", fg=self.theme.get("warn", self.theme["fg"]))

        def worker():
            ok, detail, _dt = ai_assistant.probe_api(model=model)
            self.after(0, self._test_ai_api_done, ok, detail)

        threading.Thread(target=worker, daemon=True).start()

    def _test_ai_api_done(self, ok, detail):
        status = getattr(self, "ai_keys_status_lbl", None)
        color = self.theme["good"] if ok else self.theme["bad"]
        if status is not None:
            status.config(text=detail, fg=color)
        try:
            if ok:
                messagebox.showinfo(f"{APP_NAME} — API OK", detail)
            else:
                messagebox.showerror(f"{APP_NAME} — API failed", detail)
        except Exception:
            pass

    # ---------- voice I/O — hold F9 to ask a question (personal edition) ----------
    def _set_voice_status(self, text):
        lbl = getattr(self, "voice_status_lbl", None)
        if lbl is not None:
            lbl.config(text=text)

    def _on_ptt_press(self, _event=None):
        if self._ptt_active or self.voice is None or not self.settings.get("voice_enabled", True):
            return
        if self._ask_busy or self._voice_busy:
            self._set_voice_status("Still working on the last answer — wait a sec")
            return
        self._ptt_active = True
        self._ptt_t0 = time.monotonic()
        self._set_voice_status("Listening… (release F9 when done)")
        self.voice.start_listening(self._on_voice_result, self._on_voice_error)

    def _on_ptt_release(self, _event=None):
        if not self._ptt_active or self.voice is None:
            return
        self._ptt_active = False
        held = time.monotonic() - getattr(self, "_ptt_t0", time.monotonic())
        logging.info("PTT released after %.1fs — stopping mic, starting STT", held)
        self._set_voice_status("Transcribing speech…")
        self.voice.stop_listening()

    def _on_voice_result(self, text):
        # Fires on the VoiceSTT background thread — dispatch to Tk.
        self.after(0, self._handle_voice_question, text)

    def _on_voice_error(self, exc):
        self.after(0, self._set_voice_status, f"Voice error: {exc}")

    def _voice_context_summary(self):
        """Compact, diagnostic-ready live context for both typed chat and voice.

        Values without engine state, units, or the app's health verdict are easy
        for a model to misread.  Keep this small enough for fast voice replies,
        while preserving the facts that change the diagnosis.
        """
        ctx = dict(self.latest)
        if not ctx:
            return ""
        parts = [f"Engine state={j2534.engine_state(ctx)}"]
        for pid, val in ctx.items():
            if pid not in j2534.HEALTH_PIDS:
                continue
            name, _desc, units, _gmax, _gmin = _param(pid)
            verdict = j2534.sensor_health(pid, val, ctx=ctx)
            unit = f" {units}" if units and units != "state" else ""
            status = f" ({verdict})" if verdict else ""
            parts.append(f"{name}={val}{unit}{status}")
        return ", ".join(parts)

    def _on_chat_send(self):
        text = (self.ai_chat_var.get() if hasattr(self, "ai_chat_var") else "") or ""
        text = text.strip()
        if not text:
            return
        self.ai_chat_var.set("")
        self._ask_ai(text, speak=False, source="chat")

    def _handle_voice_question(self, text):
        if not text:
            self._set_voice_status("Didn't catch that — hold F9 to try again")
            self.after(2500, self._set_voice_status, "Hold F9 to ask a question")
            return
        text = speech_vocab.correct(text)
        logging.info("Voice transcript ready: %r", text[:120])
        self._ask_ai(text, speak=True, source="voice")

    def _ask_ai(self, text, speak=False, source="chat"):
        """Shared typed-chat + voice path into Grok (background thread)."""
        if not text:
            return
        if self._ask_busy or self._voice_busy:
            if source == "voice":
                self._set_voice_status("Still working on the last question — try again shortly")
                self.after(2500, self._set_voice_status, "Hold F9 to ask a question")
            else:
                log = getattr(self, "ai_log", None)
                if log is not None:
                    log.insert("end", "Still thinking about the last question — try again shortly.\n")
                    log.see("end")
            return
        self._ask_busy = True
        self._voice_busy = True
        for_voice = source == "voice"
        if for_voice:
            self._set_voice_status(f"Thinking: \"{text}\"")
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            via = "voice" if for_voice else "chat"
            log.insert("end", f"[{stamp}] You ({via}): {text}\n[{stamp}] …thinking…\n")
            log.see("end")
        context_summary = self._voice_context_summary()
        prof = j2534.get_profile(self.settings.get("vehicle_profile"))
        model = self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL)
        brief = self._memory_brief()
        coll_ids = self._collection_ids()
        history = list(self._chat_history)
        threading.Thread(
            target=self._ask_worker,
            args=(text, context_summary, prof["name"], model, history, brief,
                  coll_ids, speak, for_voice),
            daemon=True,
            name="AskAI",
        ).start()

    def _ask_worker(self, question, context_summary, vehicle_profile, model,
                    history, memory_brief, collection_ids, speak, for_voice=False):
        answer, new_history = None, None
        t0 = time.monotonic()
        try:
            answer, new_history = ai_assistant.ask(
                question, context_summary, vehicle_profile,
                history=history, model=model, memory_brief=memory_brief,
                collection_ids=collection_ids, for_voice=for_voice,
            )
        except Exception:
            logging.exception("AI question request failed")
        logging.info(
            "Ask done in %.1fs voice=%s ok=%s",
            time.monotonic() - t0, for_voice, bool(answer),
        )
        self.after(0, self._ask_done, question, answer, new_history, speak, for_voice)

    def _ask_done(self, question, answer, new_history, speak, for_voice=False):
        self._ask_busy = False
        self._voice_busy = False
        if new_history is not None:
            self._chat_history = new_history  # only advance on success
            self._session_turns.append({"role": "user", "content": question})
            if answer:
                self._session_turns.append({"role": "assistant", "content": answer})
            self._memory_dirty = True
            if len(self._session_turns) >= 4 and len(self._session_turns) % 6 == 0:
                threading.Thread(target=self._consolidate_memory_worker, daemon=True).start()
        if not answer:
            answer = "Sorry, couldn't reach the AI just now — check connectivity / XAI_API_KEY."
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            log.insert("end", f"[{stamp}] Answer: {answer}\n\n")
            log.see("end")
        # Show text immediately; speak a clipped version so you hear it sooner.
        if speak and self.voice is not None and self.settings.get("voice_enabled", True):
            self._set_voice_status("Speaking answer…")
            self.voice.speak(answer, clip=True)
            # Reset hint after a short delay (don't wait for TTS to finish)
            self.after(2500, self._set_voice_status, "Hold F9 to ask a question")
        else:
            self._set_voice_status("Hold F9 to ask a question")

    def _upload_manual(self):
        paths = filedialog.askopenfilenames(
            title="Upload workshop manual / notes for 4G1 AI",
            filetypes=[
                ("Documents", "*.pdf;*.txt;*.md;*.docx;*.doc;*.html;*.csv"),
                ("PDF", "*.pdf"),
                ("Text", "*.txt;*.md"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        log = getattr(self, "ai_log", None)
        if log is not None:
            log.insert("end", f"Uploading {len(paths)} file(s) to xAI Collections…\n")
            log.see("end")
        threading.Thread(target=self._upload_manual_worker, args=(list(paths),), daemon=True).start()

    def _upload_manual_worker(self, paths):
        results = []
        errors = []
        local_ok = []
        for path in paths:
            try:
                lname, nchars = ai_knowledge.ingest_local(self._knowledge, path)
                local_ok.append((lname, nchars))
            except Exception as e:
                logging.exception("Local extract failed for %s", path)
                errors.append(f"{os.path.basename(path)} (local): {e}")
            if not (os.environ.get("XAI_MANAGEMENT_API_KEY") or "").strip():
                errors.append(
                    f"{os.path.basename(path)}: PDF management key missing — "
                    "saved locally only. Click Get PDF Management Key, paste it, Save Keys, "
                    "then upload again for cloud search."
                )
                continue
            try:
                file_id, cid, name = ai_knowledge.upload_document(self._knowledge, path)
                results.append((name, file_id, cid))
            except Exception as e:
                logging.exception("Manual upload failed for %s", path)
                errors.append(f"{os.path.basename(path)}: {e}")
        try:
            ai_knowledge.save_state(self._knowledge, KNOWLEDGE_PATH)
        except Exception:
            logging.exception("Could not save knowledge state after upload")
        # keep settings in sync with collection id
        cid = self._knowledge.get("collection_id") or ""
        if cid:
            self.settings["ai_collection_id"] = cid
            try:
                save_settings(self.settings)
            except Exception:
                pass
        self.after(0, self._upload_manual_done, results, errors, local_ok)

    def _upload_manual_done(self, results, errors, local_ok=None):
        log = getattr(self, "ai_log", None)
        stamp = time.strftime("%H:%M:%S")
        local_ok = local_ok or []
        if log is not None:
            for name, nchars in local_ok:
                log.insert(
                    "end",
                    f"[{stamp}] Local extract for AI: {name}  ({nchars} chars)\n",
                )
            for name, file_id, cid in results:
                log.insert(
                    "end",
                    f"[{stamp}] Uploaded manual: {name}  (file {file_id}, collection {cid})\n",
                )
            for err in errors:
                log.insert("end", f"[{stamp}] Upload note — {err}\n")
            if results:
                log.insert(
                    "end",
                    f"[{stamp}] Manuals may take a minute to index before search works fully.\n\n",
                )
            log.see("end")
        lbl = getattr(self, "ai_knowledge_lbl", None)
        if lbl is not None:
            lbl.config(text=ai_knowledge.stats(self._knowledge))
        lbl2 = getattr(self, "ai_knowledge_settings_lbl", None)
        if lbl2 is not None:
            lbl2.config(text=ai_knowledge.stats(self._knowledge))
        if hasattr(self, "ai_collection_var"):
            self.ai_collection_var.set(self._knowledge.get("collection_id") or "")
        if results and not errors:
            messagebox.showinfo(
                "4G1 Live AI",
                f"Uploaded {len(results)} manual(s) to xAI Collections.\n"
                "Grok can search them once indexing finishes (usually under a minute).",
            )
        elif local_ok:
            extra = ("\n\n" + "\n".join(errors[:4])) if errors else ""
            messagebox.showinfo(
                "4G1 Live AI",
                f"Saved {len(local_ok)} manual(s) into local AI memory.\n"
                "Cloud collection search still needs the PDF management key "
                "(Get PDF Management Key → paste → Save Keys)."
                + extra,
            )
        elif errors:
            messagebox.showwarning(
                "4G1 Live AI",
                "Manual ingest failed:\n\n" + "\n".join(errors[:6]),
            )

    def _consolidate_memory_worker(self, force=False):
        """Background: distill this session into durable memory (Grok or local).

        force=True waits briefly if another consolidate is running, then proceeds
        (used on app exit so we don't drop the last night's lessons).
        """
        if self._consolidate_busy:
            if not force:
                return
            deadline = time.time() + 3.0
            while self._consolidate_busy and time.time() < deadline:
                time.sleep(0.05)
            if self._consolidate_busy:
                # Still busy — at least flush what we have locally.
                try:
                    ai_memory.save_memory(self._memory, MEMORY_PATH)
                except Exception:
                    logging.exception("Forced memory save while consolidate busy")
                return
        if not self._session_turns:
            if self._memory_dirty:
                ai_memory.save_memory(self._memory, MEMORY_PATH)
                self._memory_dirty = False
            return
        self._consolidate_busy = True
        try:
            prof = j2534.get_profile(self.settings.get("vehicle_profile"))
            profile_id = self.settings.get("vehicle_profile") or ""
            brief = self._memory_brief()
            model = self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL)
            turns = list(self._session_turns)
            update = {}
            try:
                update = ai_assistant.consolidate(
                    turns,
                    memory_brief=brief,
                    vehicle_profile_id=profile_id,
                    vehicle_label=prof.get("name", ""),
                    model=model,
                )
            except Exception:
                logging.exception("Grok memory consolidate failed — using local fallback")
                update = {}
            if update:
                ai_memory.apply_consolidation(self._memory, update)
            else:
                ai_memory.consolidate_local(
                    self._memory, turns,
                    vehicle_profile_id=profile_id,
                    vehicle_label=prof.get("name", ""),
                )
            ai_memory.save_memory(self._memory, MEMORY_PATH)
            self._memory_dirty = False
            # Drop turns already folded into memory so we don't re-hash forever
            if len(self._session_turns) == len(turns):
                self._session_turns = []
            else:
                self._session_turns = self._session_turns[len(turns):]
            logging.info("AI memory consolidated (%s)", ai_memory.memory_stats(self._memory))
        except Exception:
            logging.exception("Memory consolidate worker failed")
        finally:
            self._consolidate_busy = False

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
        theme_cb = ttk.Combobox(sw, textvariable=self.theme_var, values=list(THEMES),
                                state="readonly", width=24)
        theme_cb.pack(side="left")
        theme_cb.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.after_idle(lambda: self._pick_theme(self.theme_var.get())),
        )

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
        tk.Label(head, text="J2534 adapter  ·  MUT-II session", bg=t["panel"],
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

        # DLL path
        dll_row = tk.Frame(advanced, bg=t["panel"])
        dll_row.pack(fill="x", padx=16, pady=4)
        tk.Label(dll_row, text="J2534 DLL", bg=t["panel"], fg=t["fg"],
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

        # ---- AI assistant (personal edition) ----
        aicard = tk.Frame(inner, bg=t["panel"],
                          highlightbackground=t["border"], highlightthickness=1)
        aicard.pack(fill="x", pady=(12, 0), padx=2)
        tk.Frame(aicard, bg=t.get("accent2", t["accent"]), height=3).pack(fill="x")
        tk.Label(aicard, text="AI ASSISTANT", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w", padx=16, pady=(12, 4))

        ai_opt = tk.Frame(aicard, bg=t["panel"])
        ai_opt.pack(fill="x", padx=16, pady=(0, 4))
        self.ai_enabled_var = tk.BooleanVar(value=bool(self.settings.get("ai_enabled", True)))
        tk.Checkbutton(
            ai_opt, text="Enabled — explain sensor warnings and trends automatically",
            variable=self.ai_enabled_var, bg=t["panel"], fg=t["fg"], selectcolor=t["elev"],
            activebackground=t["panel"], activeforeground=t["fg"],
            font=ui_font(t, 9), anchor="w",
        ).pack(anchor="w", pady=1)

        self.voice_enabled_var = tk.BooleanVar(value=bool(self.settings.get("voice_enabled", True)))
        voice_text = "Voice — speak alerts aloud, hold F9 to ask a question"
        if self.voice is None:
            voice_text += "  (unavailable on this machine)"
        tk.Checkbutton(
            ai_opt, text=voice_text,
            variable=self.voice_enabled_var, bg=t["panel"], fg=t["fg"], selectcolor=t["elev"],
            activebackground=t["panel"], activeforeground=t["fg"],
            font=ui_font(t, 9), anchor="w", state=("normal" if self.voice is not None else "disabled"),
        ).pack(anchor="w", pady=1)

        # Grok API keys (stored in secrets.env, not settings.json)
        key_frame = tk.Frame(aicard, bg=t["panel"])
        key_frame.pack(fill="x", padx=16, pady=(6, 2))
        # Green when Grok login or console key is present (not only env console key)
        _auth_ok = "MISSING" not in api_key_status()
        self.ai_keys_status_lbl = tk.Label(
            key_frame, text=api_key_status(),
            bg=t["panel"], fg=t["good"] if _auth_ok else t["bad"],
            font=ui_font(t, 8, bold=True),
        )
        self.ai_keys_status_lbl.pack(anchor="w")
        tk.Label(
            key_frame,
            text="Usually auto via Grok login on this PC. Optional: paste console key below.",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        ).pack(anchor="w", pady=(0, 4))

        xai_row = tk.Frame(aicard, bg=t["panel"])
        xai_row.pack(fill="x", padx=16, pady=2)
        tk.Label(xai_row, text="XAI API key", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.xai_key_var = tk.StringVar(value=os.environ.get("XAI_API_KEY", ""))
        xai_entry = tk.Entry(
            xai_row, textvariable=self.xai_key_var, show="•",
            bg=t["panel2"], fg=t["fg"], insertbackground=t["fg"],
            relief=("sunken" if t.get("xp") else "flat"),
            bd=(2 if t.get("xp") else 0), font=ui_font(t, 9), width=40,
        )
        xai_entry.pack(side="left", fill="x", expand=True)
        tk.Label(xai_row, text="  console.x.ai", bg=t["panel"], fg=t["muted"],
                 font=ui_font(t, 8)).pack(side="left")

        mgmt_row = tk.Frame(aicard, bg=t["panel"])
        mgmt_row.pack(fill="x", padx=16, pady=2)
        tk.Label(mgmt_row, text="PDF mgmt key", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.xai_mgmt_var = tk.StringVar(value=os.environ.get("XAI_MANAGEMENT_API_KEY", ""))
        mgmt_entry = tk.Entry(
            mgmt_row, textvariable=self.xai_mgmt_var, show="•",
            bg=t["panel2"], fg=t["fg"], insertbackground=t["fg"],
            relief=("sunken" if t.get("xp") else "flat"),
            bd=(2 if t.get("xp") else 0), font=ui_font(t, 9), width=40,
        )
        mgmt_entry.pack(side="left", fill="x", expand=True)
        SoftButton(mgmt_row, "Get Key", self._open_mgmt_console, t, primary=False).pack(
            side="left", padx=(8, 0),
        )
        SoftButton(mgmt_row, "Save Keys", self._save_keys_now, t, primary=True).pack(
            side="left", padx=(6, 0),
        )
        tk.Label(
            aicard,
            text="PDF management key is REQUIRED to upload manuals to xAI Collections. "
            "Create one at console.x.ai → Settings → Management Keys "
            "(tick AddFileToCollection + Collections). Then paste it above and Save Keys. "
            "PDFs still get a local extract for the AI even without the cloud key.",
            bg=t["panel"], fg=t["bad"] if not (os.environ.get("XAI_MANAGEMENT_API_KEY") or "").strip()
            else t["good"],
            font=ui_font(t, 8), wraplength=720, justify="left",
        ).pack(anchor="w", padx=16, pady=(2, 6))

        ai_name_row = tk.Frame(aicard, bg=t["panel"])
        ai_name_row.pack(fill="x", padx=16, pady=4)
        tk.Label(ai_name_row, text="Your name", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.ai_user_name_var = tk.StringVar(
            value=self.settings.get("ai_user_name")
            or self._memory.get("user_name")
            or ai_memory.DEFAULT_USER_NAME
        )
        tk.Entry(
            ai_name_row, textvariable=self.ai_user_name_var,
            bg=t["elev"], fg=t["fg"], insertbackground=t["fg"],
            relief="flat", font=ui_font(t, 9), width=24,
        ).pack(side="left")
        tk.Label(ai_name_row, text="  (AI greets you by name)", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        ai_model_row = tk.Frame(aicard, bg=t["panel"])
        ai_model_row.pack(fill="x", padx=16, pady=4)
        tk.Label(ai_model_row, text="Model", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.ai_model_var = tk.StringVar(value=self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL))
        ttk.Combobox(
            ai_model_row, textvariable=self.ai_model_var,
            values=list(ai_assistant.MODEL_CHOICES),
            state="readonly", width=32,
        ).pack(side="left")
        SoftButton(ai_model_row, "Test API", self._test_ai_api, t, primary=False).pack(
            side="left", padx=10
        )
        tk.Label(ai_model_row, text="  4.5 default · 4.6 newest · non-reasoning = faster",
                 bg=t["panel"], fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        ai_baseline_row = tk.Frame(aicard, bg=t["panel"])
        ai_baseline_row.pack(fill="x", padx=16, pady=(4, 4))
        n_params = len(self._baseline)
        self.ai_baseline_lbl = tk.Label(
            ai_baseline_row, text=f"Learned baseline: {n_params} parameters from logged sessions",
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        )
        self.ai_baseline_lbl.pack(side="left")
        SoftButton(ai_baseline_row, "Rebuild Baseline", self._rebuild_baseline_and_refresh, t,
                   primary=False).pack(side="left", padx=12)

        ai_mem_row = tk.Frame(aicard, bg=t["panel"])
        ai_mem_row.pack(fill="x", padx=16, pady=(0, 4))
        self.ai_memory_lbl = tk.Label(
            ai_mem_row, text=ai_memory.memory_stats(self._memory),
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        )
        self.ai_memory_lbl.pack(side="left")
        SoftButton(ai_mem_row, "Save Memory Now", self._force_consolidate_memory, t,
                   primary=False).pack(side="left", padx=12)

        ai_coll_row = tk.Frame(aicard, bg=t["panel"])
        ai_coll_row.pack(fill="x", padx=16, pady=4)
        tk.Label(ai_coll_row, text="Collection ID", bg=t["panel"], fg=t["fg"],
                 font=ui_font(t, 9), width=16, anchor="w").pack(side="left")
        self.ai_collection_var = tk.StringVar(
            value=self.settings.get("ai_collection_id")
            or self._knowledge.get("collection_id")
            or ""
        )
        tk.Entry(
            ai_coll_row, textvariable=self.ai_collection_var,
            bg=t["elev"], fg=t["fg"], insertbackground=t["fg"],
            relief="flat", font=ui_font(t, 9), width=36,
        ).pack(side="left")
        tk.Label(ai_coll_row, text="  (optional — auto-created on upload)", bg=t["panel"],
                 fg=t["muted"], font=ui_font(t, 8)).pack(side="left")

        ai_know_row = tk.Frame(aicard, bg=t["panel"])
        ai_know_row.pack(fill="x", padx=16, pady=(0, 12))
        self.ai_knowledge_settings_lbl = tk.Label(
            ai_know_row, text=ai_knowledge.stats(self._knowledge),
            bg=t["panel"], fg=t["muted"], font=ui_font(t, 8),
        )
        self.ai_knowledge_settings_lbl.pack(side="left")
        SoftButton(ai_know_row, "Upload Manual…", self._upload_manual, t,
                   primary=False).pack(side="left", padx=12)

        act = tk.Frame(inner, bg=t["bg"])
        act.pack(fill="x", pady=12, padx=2)
        SoftButton(act, "Apply Settings", self.apply_settings, t, primary=True).pack(side="left")
        SoftButton(act, "Reset Connection Defaults", self._reset_conn_defaults, t,
                   primary=False).pack(side="left", padx=8)

    def _open_mgmt_console(self):
        """Open the xAI page where a Collections management key is created."""
        url = "https://console.x.ai/team/default/settings/management-keys"
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            logging.exception("Could not open management-key console")
        messagebox.showinfo(
            "PDF management key",
            "Browser opened at xAI Management Keys.\n\n"
            "1. Create Management Key\n"
            "2. Tick AddFileToCollection and Collections (create/update)\n"
            "3. Copy the key once (it is not shown again)\n"
            "4. Paste it in Settings → PDF mgmt key\n"
            "5. Click Save Keys, then Upload Manual…\n\n"
            f"{url}",
        )

    def _save_keys_now(self):
        """Write API + management keys without rebuilding the whole UI."""
        api = self.xai_key_var.get() if hasattr(self, "xai_key_var") else ""
        mgmt = self.xai_mgmt_var.get() if hasattr(self, "xai_mgmt_var") else ""
        save_secrets({
            "XAI_API_KEY": api,
            "XAI_MANAGEMENT_API_KEY": mgmt,
        })
        status = api_key_status()
        lbl = getattr(self, "ai_keys_status_lbl", None)
        if lbl is not None:
            ok = "MISSING" not in status
            lbl.config(text=status, fg=self.theme["good"] if ok else self.theme["bad"])
        if (mgmt or "").strip():
            messagebox.showinfo(
                "4G1 Live AI",
                "Keys saved.\nPDF management key is set — Upload Manual… can now "
                "push workshop PDFs into xAI Collections for search.",
            )
        else:
            messagebox.showwarning(
                "4G1 Live AI",
                "API key saved, but the PDF management key is still empty.\n"
                "Click Get Key, create one in the console, paste it, then Save Keys.",
            )

    def analyse_tactrix_log(self):
        """Open the newest known Tactrix/4G1 CSV, or let the user pick one."""
        try:
            path = tactrix_log.discover_latest_log()
        except tactrix_log.TactrixLogError:
            path = None
        if path is None:
            self._choose_tactrix_csv()
            return
        self._run_tactrix_analysis(path)

    def _choose_tactrix_csv(self):
        starters = [
            # shed/race logs land here via "TRM Shed Run.bat" - and this path
            # is on C:, so it still works with G: unplugged at the car
            r"C:\Users\trmra\TRM RaceLogs",
            r"G:\Users\trmra\OneDrive\Desktop\Tactrix Logs MLV",
            os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop", "Tactrix Logs MLV"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "4G1 Live AI", "Sessions"),
        ]
        initial = next((p for p in starters if os.path.isdir(p)), os.path.expanduser("~"))
        path = filedialog.askopenfilename(
            title="Choose Tactrix / 4G1 log CSV",
            filetypes=[("CSV logs", "*.csv"), ("All files", "*.*")],
            initialdir=initial,
        )
        if path:
            self._run_tactrix_analysis(path)

    def _run_tactrix_analysis(self, path):
        try:
            analysis = tactrix_log.analyse_log(
                path, profile_id=self.settings.get("vehicle_profile")
            )
            report = tactrix_log.format_analysis(analysis)
        except (tactrix_log.TactrixLogError, OSError) as exc:
            messagebox.showerror("4G1 Live AI — Log", str(exc))
            return
        self._last_log_report = report
        self._show_tactrix_analysis(analysis, report)
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            log.insert("end", f"[{stamp}] Analysing Tactrix log: {os.path.basename(path)}\n")
            log.see("end")
        threading.Thread(
            target=self._tactrix_ai_worker, args=(report,), daemon=True, name="LogAI"
        ).start()

    def _tactrix_ai_worker(self, report):
        answer = None
        try:
            prof = j2534.get_profile(self.settings.get("vehicle_profile"))
            answer = ai_assistant.interpret_log(
                report,
                vehicle_profile=prof.get("name", "4G15 12V"),
                memory_brief=self._memory_brief(),
                model=self.settings.get("ai_model", ai_assistant.DEFAULT_MODEL),
                collection_ids=self._collection_ids(),
            )
        except Exception:
            logging.exception("AI log interpretation failed")
        self.after(0, self._tactrix_ai_done, answer)

    def _tactrix_ai_done(self, answer):
        if not answer:
            answer = (
                "Log numbers are above — use those. "
                "Grok write-up timed out or failed. Ask in the AI tab if you want a reread."
            )
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            log.insert("end", f"[{stamp}] Log AI: {answer}\n\n")
            log.see("end")
        win = getattr(self, "_tactrix_review_window", None)
        box = getattr(self, "_tactrix_ai_box", None)
        if box is not None and win is not None and win.winfo_exists():
            try:
                box.configure(state="normal")
                box.delete("1.0", "end")
                box.insert("1.0", answer)
                box.configure(state="disabled")
            except Exception:
                pass

    def _show_tactrix_analysis(self, analysis, report):
        t = self.theme
        existing = getattr(self, "_tactrix_review_window", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        win = tk.Toplevel(self, bg=t["bg"])
        self._tactrix_review_window = win
        win.title("4G1 Live AI — Tactrix Log")
        win.transient(self)
        win.geometry("900x720")
        win.minsize(760, 560)
        try:
            win.iconbitmap(ICON_PATH)
        except Exception:
            pass
        outer, panel = card(win, t, title="Tactrix / 4G1 Log Analysis")
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        verdict = analysis.verdict
        call = verdict.call if verdict else "WATCH"
        banner_bg = {"NORMAL": "#0A7B26", "WATCH": "#B07000", "FIX NOW": "#C00000"}.get(call, t["accent"])
        banner = tk.Frame(panel, bg=banner_bg)
        banner.pack(fill="x", padx=8, pady=(8, 6))
        tk.Label(
            banner, text=call, bg=banner_bg, fg="#FFFFFF",
            font=ui_font(t, 16, bold=True),
        ).pack(anchor="w", padx=12, pady=(8, 0))
        tk.Label(
            banner, text=(verdict.headline if verdict else analysis.source_path.name),
            bg=banner_bg, fg="#FFFFFF", font=ui_font(t, 10),
        ).pack(anchor="w", padx=12, pady=(0, 8))

        heroes = tk.Frame(panel, bg=t["panel"])
        heroes.pack(fill="x", padx=8, pady=(0, 6))
        rpm = analysis.pid_stats.get(0x21)
        cool = analysis.pid_stats.get(0x07) or analysis.pid_stats.get(0x10)
        batt = analysis.wot_stats.get(0x14) or analysis.pid_stats.get(0x14)
        tiles = [
            ("PEAK RPM", f"{rpm.maximum:.0f}" if rpm else "—"),
            ("COOLANT", f"{cool.maximum:.0f}°C" if cool else "—"),
            ("WOT BATT", f"{batt.minimum:.1f} V" if batt else "—"),
            ("WOT PULLS", str(analysis.wot_pulls)),
            ("TIME", f"{analysis.duration_seconds:.0f}s"),
        ]
        for label, value in tiles:
            cell = tk.Frame(heroes, bg=t["panel2"], highlightbackground=t.get("win_border", t["border"]),
                            highlightthickness=1)
            cell.pack(side="left", fill="both", expand=True, padx=3)
            tk.Label(cell, text=label, bg=t["panel2"], fg=t["dim"],
                     font=ui_font(t, 7, bold=True)).pack(anchor="w", padx=8, pady=(6, 0))
            tk.Label(cell, text=value, bg=t["panel2"], fg=t.get("value", t["fg"]),
                     font=ui_font(t, 16, bold=True, mono=True)).pack(anchor="w", padx=8, pady=(0, 6))

        findings = (verdict.findings if verdict else [])[:8]
        find_box = tk.Frame(panel, bg=t["panel"])
        find_box.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(find_box, text="WHAT IT MEANS", bg=t["panel"], fg=t["dim"],
                 font=ui_font(t, 8, bold=True)).pack(anchor="w")
        for item in findings:
            tk.Label(
                find_box, text="•  " + item, bg=t["panel"], fg=t["fg"],
                font=ui_font(t, 9), wraplength=820, justify="left", anchor="w",
            ).pack(fill="x", pady=1)

        text = tk.Text(
            panel, bg=t["chart"], fg=t["fg"], relief="sunken" if t.get("xp") else "flat",
            font=ui_font(t, 9, mono=True), wrap="word", padx=10, pady=8,
            insertbackground=t["fg"], selectbackground=t["panel2"], height=10,
        )
        text.insert("1.0", report)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, padx=10, pady=(4, 6))
        self._tactrix_report_box = text

        tk.Label(
            panel, text="GROK (extra — numbers above already stand)",
            bg=t["panel"], fg=t["dim"], font=ui_font(t, 8, bold=True),
        ).pack(anchor="w", padx=10)
        ai_box = tk.Text(
            panel, bg=t["panel2"], fg=t["fg"], relief="sunken" if t.get("xp") else "flat",
            font=ui_font(t, 9), wrap="word", padx=10, pady=8, height=5,
            insertbackground=t["fg"],
        )
        ai_box.insert("1.0", "Optional Grok note loading…")
        ai_box.configure(state="disabled")
        ai_box.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self._tactrix_ai_box = ai_box

        actions = tk.Frame(panel, bg=t["panel"])
        actions.pack(fill="x", padx=10, pady=(0, 10))
        SoftButton(actions, "Open Another CSV", lambda: (win.destroy(), self._choose_tactrix_csv()), t).pack(
            side="left"
        )

        def _copy_report():
            try:
                win.clipboard_clear()
                win.clipboard_append(report)
            except Exception:
                pass

        SoftButton(actions, "Copy Report", _copy_report, t).pack(side="left", padx=8)
        SoftButton(actions, "Close", win.destroy, t).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())

    def _browse_dll(self):
        path = filedialog.askopenfilename(
            title="Select J2534 DLL",
            filetypes=[("DLL files", "*.dll"), ("All files", "*.*")],
            initialdir=r"C:\Windows\SysWOW64",
        )
        if path:
            self.dll_var.set(path)

    def _detect_dll(self):
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
        save_settings(self.settings)
        self.theme = dict(THEMES.get(name, THEMES["Windows XP"]))
        self._resolve_theme_fonts()
        tab = getattr(self, "_active_tab", "settings")
        self.rebuild_ui()
        self._show_tab(tab)

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
        if hasattr(self, "ai_enabled_var"):
            self.settings["ai_enabled"] = bool(self.ai_enabled_var.get())
        if hasattr(self, "ai_model_var"):
            self.settings["ai_model"] = self.ai_model_var.get()
            # Remember deliberate model picks so we don't re-migrate off grok-4.5
            self.settings["_ai_model_user_set"] = True
        if hasattr(self, "ai_user_name_var"):
            name = (self.ai_user_name_var.get() or "").strip() or ai_memory.DEFAULT_USER_NAME
            self.settings["ai_user_name"] = name
            ai_memory.set_user_name(self._memory, name)
            self._memory_dirty = True
            ai_memory.save_memory(self._memory, MEMORY_PATH)
        if hasattr(self, "ai_collection_var"):
            cid = (self.ai_collection_var.get() or "").strip()
            self.settings["ai_collection_id"] = cid
            if cid:
                ai_knowledge.set_collection_id(self._knowledge, cid)
            ai_knowledge.save_state(self._knowledge, KNOWLEDGE_PATH)
        if hasattr(self, "voice_enabled_var"):
            self.settings["voice_enabled"] = bool(self.voice_enabled_var.get())
        # Persist Grok keys into secrets.env (not settings.json)
        if hasattr(self, "xai_key_var") or hasattr(self, "xai_mgmt_var"):
            save_secrets({
                "XAI_API_KEY": self.xai_key_var.get() if hasattr(self, "xai_key_var") else "",
                "XAI_MANAGEMENT_API_KEY": (
                    self.xai_mgmt_var.get() if hasattr(self, "xai_mgmt_var") else ""
                ),
            })
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
        self.title(f"{APP_NAME}  —  {prof['short']}  ·  MUT-II")
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
        if not self.live:
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
        if self.dev:
            if self.live:
                messagebox.showinfo(APP_NAME, "Stop Live Data before disconnecting the adapter.")
                return
            self._disconnect_adapter()
            return
        try:
            kw = self._conn_kwargs()
            if not os.path.isfile(kw["dll_path"]):
                raise j2534.J2534Error(f"DLL not found:\n{kw['dll_path']}\n\nSet path in Settings → Connection.")
            self.dev = j2534.Device(**kw)
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
            self._maybe_explain_transitions(ctx)

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

    def _autosave_columns(self):
        """Stable session schema, known before the first live sample arrives.

        Writing the header at session open makes every CSV self-describing even
        when an adapter drops before the first sample.  A stable gauge/hero
        schema also prevents later rows from gaining values with no matching
        column name.
        """
        columns = ["t", "mode"]
        for pid in list(getattr(self, "gauges", []) or []) + list(getattr(self, "heroes", []) or []):
            if pid not in j2534.ALL_PARAMS:
                continue
            name = _param(pid)[0]
            if name not in columns:
                columns.append(name)
        return columns

    def _autosave_start(self, tag="live"):
        """Open a crash-safe, self-describing session CSV.

        The header is committed before polling starts, so every newly created
        file has a header row—even if the ECU disconnects immediately.
        """
        self._autosave_path = None
        self._autosave_cols = None
        self._autosave_fh = None
        self._autosave_writer = None
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            name = time.strftime(f"4g1_{tag}_%Y%m%d_%H%M%S.csv")
            path = os.path.join(SESSION_DIR, name)
            fh = open(path, "w", newline="", encoding="utf-8")
            columns = self._autosave_columns()
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            fh.flush()  # Persist the header before the first poll / adapter failure.
            self._autosave_fh = fh
            self._autosave_cols = columns
            self._autosave_writer = writer
            self._autosave_path = path
            self.log_line(f"Auto-saving this session to {path}")
        except Exception:
            logging.exception("Could not open autosave file")
            fh = getattr(self, "_autosave_fh", None)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
            self._autosave_fh = None
            self._autosave_writer = None

    def _autosave_row(self, snap):
        fh = getattr(self, "_autosave_fh", None)
        writer = getattr(self, "_autosave_writer", None)
        if fh is None or writer is None:
            return
        try:
            # The schema is intentionally fixed at session start.  All normal
            # live samples use the active gauges and heroes that seeded it.
            writer.writerow(snap or {})
            fh.flush()   # flush every row — the whole point is crash safety
        except Exception:
            logging.exception("Autosave row failed")
            try:
                fh.close()
            except Exception:
                pass
            self._autosave_fh = None
            self._autosave_writer = None

    def _autosave_stop(self):
        fh = getattr(self, "_autosave_fh", None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        self._autosave_fh = None
        self._autosave_writer = None
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
            # Also dump every candidate fault register raw. Which request
            # actually carries DTCs on this ECU is genuinely unknown (three
            # sources disagree, and live ROM tracing contradicts two of them),
            # so show the bytes and let the car settle it.
            try:
                cand = self.dev.read_dtc_candidates()
            except Exception:
                cand = None
            if reconnect:
                self._mut_session_end(pin1)
        except Exception as e:
            self.codes_box.insert("end", f"Read failed: {e}\n(ignition on?)\n")
            return
        if cand:
            self.codes_box.insert("end", "Candidate fault registers (RAW):\n")
            for req in sorted(cand):
                v = cand[req]
                shown = "no reply" if v is None else f"0x{int(v):02X}  ({int(v)})"
                self.codes_box.insert("end", f"   request 0x{req:02X} = {shown}\n")
            self.codes_box.insert(
                "end",
                "\nTO IDENTIFY THE REAL FAULT REGISTER: note these values with\n"
                "the engine healthy, then unplug one sensor at idle and read\n"
                "again. Whichever register CHANGES is the fault register on\n"
                "this ECU. Nothing below is trustworthy until that is done.\n\n")
        if codes is None:
            self.codes_box.insert("end", "No response from ECU.\n")
            return
        if not codes:
            # An empty result is NOT proof of a healthy engine. The registers
            # are now correct (0x36 count, 0x47/0x48 mask), but the bit-to-code
            # mapping is still unverified on this ECU family, so keep the
            # wording honest and never imply a clean bill of health.
            self.codes_box.insert("end", "No trouble codes returned.\n\n")
            self.codes_box.insert(
                "end",
                "This is NOT confirmation that the engine is fault-free.\n\n"
                "Codes now come from the correct registers — 0x36 active fault\n"
                "count, 0x47/0x48 active fault mask. An empty result means the\n"
                "ECU reported a zero active-fault count, which is meaningful\n"
                "but not conclusive:\n"
                "   - stored (historic) faults live at 0x37 / 0x40-0x45 and\n"
                "     are not read here yet, and\n"
                "   - the bit-to-code mapping has not been confirmed on this\n"
                "     ECU, so a set bit could go unnamed.\n\n"
                "To validate this screen: unplug one sensor at idle and check\n"
                "that the active-fault count goes non-zero.\n")
            return
        self.codes_box.insert("end", f"{len(codes)} code(s) present:\n\n")
        for code, desc in codes:
            # code is normally an int MUT code, but read_dtcs may also return a
            # plain-text summary row (ECU reported N active faults with no
            # matching bit), so format defensively rather than assuming int.
            label = f"#{code:02d}" if isinstance(code, int) else str(code)
            self.codes_box.insert("end", f"  {label}   {desc}\n")

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

    def _force_consolidate_memory(self):
        """Settings button: fold current session into durable memory now."""
        if self._consolidate_busy:
            messagebox.showinfo("4G1 Live AI", "Memory save already in progress.")
            return
        if not self._session_turns and not self._memory_dirty:
            # Still refresh name from settings into memory
            name = (self.settings.get("ai_user_name") or "").strip()
            if name:
                ai_memory.set_user_name(self._memory, name)
            ai_memory.save_memory(self._memory, MEMORY_PATH)
            lbl = getattr(self, "ai_memory_lbl", None)
            if lbl is not None:
                lbl.config(text=ai_memory.memory_stats(self._memory))
            messagebox.showinfo("4G1 Live AI", "Memory saved.\n" + ai_memory.memory_stats(self._memory))
            return

        def _run():
            self._consolidate_memory_worker()
            self.after(0, self._after_force_consolidate)

        threading.Thread(target=_run, daemon=True).start()
        messagebox.showinfo("4G1 Live AI", "Learning from this session… (saves in background)")

    def _after_force_consolidate(self):
        lbl = getattr(self, "ai_memory_lbl", None)
        if lbl is not None:
            lbl.config(text=ai_memory.memory_stats(self._memory))
        log = getattr(self, "ai_log", None)
        if log is not None:
            stamp = time.strftime("%H:%M:%S")
            log.insert("end", f"[{stamp}] Memory updated — {ai_memory.memory_stats(self._memory)}\n\n")
            log.see("end")

    def destroy(self):
        self._closing = True
        self.live = False
        # On exit: NEVER block on network. Local memory write only.
        # (Grok consolidate mid-session / Save Memory Now still use the API.)
        try:
            if self._session_turns:
                prof = j2534.get_profile(self.settings.get("vehicle_profile"))
                ai_memory.consolidate_local(
                    self._memory,
                    list(self._session_turns),
                    vehicle_profile_id=self.settings.get("vehicle_profile") or "",
                    vehicle_label=prof.get("name", ""),
                )
                self._session_turns = []
            ai_memory.save_memory(self._memory, MEMORY_PATH)
        except Exception:
            logging.exception("Failed to persist AI memory on exit")
        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass
        try:
            if self.voice is not None:
                self.voice.shutdown()
        except Exception:
            pass
        try:
            super().destroy()
        except Exception:
            logging.exception("Tk destroy failed")


def _install_crash_hooks():
    """Log startup/main-thread crashes when launched via pythonw (no console)."""
    crash_path = os.path.join(LOG_DIR, "crash.log")

    def _write(msg):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(msg)
                if not msg.endswith("\n"):
                    f.write("\n")
        except Exception:
            pass

    def _hook(exc_type, exc, tb):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        _write(f"\n---- {time.strftime('%Y-%m-%d %H:%M:%S')} ----\n{detail}")
        logging.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        try:
            # Last-ditch visible error when pythonw has no console
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{APP_NAME} crashed.\n\nSee:\n{crash_path}\n\n{exc_type.__name__}: {exc}",
                f"{APP_NAME} crash",
                0x10,
            )
        except Exception:
            pass

    sys.excepthook = _hook


def _focus_existing_instance():
    """If another 4G1 Live AI window is already open, bring it forward."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            if title.startswith(APP_NAME):
                found.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return False
        hwnd = found[0]
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        logging.info("Another instance is already open — focused existing window")
        return True
    except Exception:
        logging.exception("Could not focus existing instance")
        return False


def _pid_is_alive(pid):
    if not pid or pid <= 0:
        return False
    try:
        # Windows: OpenProcess + exit code still-active check
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _acquire_single_instance():
    """One live UI at a time. Uses a PID lock file (survives messy pythonw exits).

    Returns True if this process should continue, False if we handed off to
    an already-running window and should exit.
    """
    if sys.platform != "win32":
        return True
    if "--allow-multi" in sys.argv:
        return True
    lock_path = os.path.join(USER_DATA_DIR, "instance.pid")
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        old_pid = None
        if os.path.isfile(lock_path):
            try:
                with open(lock_path, encoding="utf-8") as f:
                    old_pid = int((f.read() or "").strip() or "0")
            except Exception:
                old_pid = None
        if old_pid and old_pid != os.getpid() and _pid_is_alive(old_pid):
            # Another live process — focus its window if possible
            for _ in range(4):
                if _focus_existing_instance():
                    logging.info("Handed off to existing instance pid=%s", old_pid)
                    return False
                time.sleep(0.4)
            # Alive but no window → almost certainly a zombie hang
            logging.warning("Stale instance pid=%s has no window — taking over", old_pid)
            try:
                # PROCESS_TERMINATE = 0x0001
                h = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(old_pid))
                if h:
                    ctypes.windll.kernel32.TerminateProcess(h, 1)
                    ctypes.windll.kernel32.CloseHandle(h)
            except Exception:
                logging.exception("Could not terminate stale pid=%s", old_pid)
        # Claim lock
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        logging.exception("Single-instance check failed — continuing")
        return True


if __name__ == "__main__":
    _install_crash_hooks()
    if not _acquire_single_instance():
        sys.exit(0)
    try:
        app = App()
    except Exception:
        logging.exception("App failed during startup")
        detail = traceback.format_exc()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(os.path.join(LOG_DIR, "crash.log"), "a", encoding="utf-8") as f:
                f.write(f"\n---- startup {time.strftime('%Y-%m-%d %H:%M:%S')} ----\n{detail}")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{APP_NAME} failed to start.\n\nSee:\n"
                f"{os.path.join(LOG_DIR, 'crash.log')}\n\n{detail[-800:]}",
                f"{APP_NAME} startup error",
                0x10,
            )
        except Exception:
            pass
        raise
    if "--autoconnect" in sys.argv or "--autolive" in sys.argv:
        app.after(700, app.on_connect)
    if "--autolive" in sys.argv:
        app.after(1600, app.toggle_live)
    app.mainloop()
