"""4G1 Live — J2534 device layer (OpenPort 2.0, Mitsubishi MUT-II).

Talks to the vehicle interface over the standard J2534 pass-thru API.
Must run under 32-bit Python (the J2534 driver is 32-bit).

MUT-II request IDs and scalings are the OBD-era (mid-90s+) set used by
EvoScan / OpenPort logcfg for CE Lancer / Mirage (1996–2003).

Vehicle profiles (4G15 12V primary, 4G93 MAF secondary) share the same MUT
channel; notes and branding differ. Coolant (0x07) and intake air (0x3A) are
raw NTC ADC values and must be converted with a thermistor table — not plain °C.
"""

import ctypes as ct

REAL_DLL = r"C:\Windows\SysWOW64\op20pt32.dll"

# Protocol IDs
ISO9141        = 3
ISO9141_K      = 0x00009240   # J2534-2 K-line channel
ISO14230       = 4
ISO15765       = 6

# Connect flags
ISO9141_NO_CHECKSUM = 0x00000200
ISO9141_K_LINE_ONLY = 0x00001000

# Ioctl IDs
GET_CONFIG, SET_CONFIG, READ_VBATT = 0x01, 0x02, 0x03
FIVE_BAUD_INIT, FAST_INIT = 0x04, 0x05
CLEAR_TX_BUFFER, CLEAR_RX_BUFFER = 0x07, 0x08

# Filters / RxStatus
PASS_FILTER = 0x00000001
TX_MSG_TYPE = 0x00000001   # echo of our own transmitted request

# SET_CONFIG parameter IDs
DATA_RATE, LOOPBACK, P1_MAX, PARITY, DATA_BITS, FIVE_BAUD_MOD = 0x01, 0x03, 0x07, 0x16, 0x20, 0x21

# Pins & special voltages
J1962_PIN_1 = 1
SHORT_TO_GROUND = 0xFFFFFFFE
VOLTAGE_OFF     = 0xFFFFFFFF

MUT_BAUD = 15625
INIT_KEY_BYTES = bytes([0xC0, 0x55, 0xEF, 0x85])

# Common J2534 DLL locations (first existing path wins for auto-detect)
COMMON_DLLS = [
    r"C:\Windows\SysWOW64\op20pt32.dll",          # Tactrix OpenPort 2.0
    r"C:\Windows\System32\op20pt32.dll",
    r"C:\Program Files (x86)\OpenECU\Tactrix\op20pt32.dll",
    r"C:\Program Files\OpenECU\Tactrix\op20pt32.dll",
    r"C:\Windows\SysWOW64\MHDPTS32.dll",          # some third-party
    r"C:\Windows\SysWOW64\j2534.dll",
]


def find_j2534_dlls():
    """Return list of existing candidate DLL paths."""
    import os
    found = []
    for p in COMMON_DLLS:
        if os.path.isfile(p) and p not in found:
            found.append(p)
    return found


def default_dll():
    found = find_j2534_dlls()
    return found[0] if found else REAL_DLL


class PASSTHRU_MSG(ct.Structure):
    _fields_ = [("ProtocolID", ct.c_ulong), ("RxStatus", ct.c_ulong),
                ("TxFlags", ct.c_ulong), ("Timestamp", ct.c_ulong),
                ("DataSize", ct.c_ulong), ("ExtraDataIndex", ct.c_ulong),
                ("Data", ct.c_ubyte * 4128)]


class SCONFIG(ct.Structure):
    _fields_ = [("Parameter", ct.c_ulong), ("Value", ct.c_ulong)]


class SCONFIG_LIST(ct.Structure):
    _fields_ = [("NumOfParams", ct.c_ulong), ("ConfigPtr", ct.POINTER(SCONFIG))]


class SBYTE_ARRAY(ct.Structure):
    _fields_ = [("NumOfBytes", ct.c_ulong), ("BytePtr", ct.POINTER(ct.c_ubyte))]


class J2534Error(Exception):
    pass


class Device:
    """Thin, safe wrapper over the OpenPort J2534 DLL."""

    def __init__(self, dll_path=None, baud=None, request_timeout_ms=400):
        self.dll_path = dll_path or default_dll()
        self.baud = int(baud or MUT_BAUD)
        self.request_timeout_ms = int(request_timeout_ms)
        self.dll = ct.WinDLL(self.dll_path)
        self.dev = ct.c_ulong(0)
        self.chan = None

    def _err(self):
        buf = ct.create_string_buffer(160)
        try:
            self.dll.PassThruGetLastError(buf)
        except Exception:
            pass
        return buf.value.decode(errors="replace")

    def open(self):
        rc = self.dll.PassThruOpen(None, ct.byref(self.dev))
        if rc:
            raise J2534Error(f"PassThruOpen failed rc=0x{rc:02X}: {self._err()}")

    def version(self):
        fw = ct.create_string_buffer(96); dl = ct.create_string_buffer(96); ap = ct.create_string_buffer(96)
        self.dll.PassThruReadVersion(self.dev, fw, dl, ap)
        return fw.value.decode(errors="replace"), dl.value.decode(errors="replace"), ap.value.decode(errors="replace")

    def battery_mv(self):
        v = ct.c_ulong(0)
        rc = self.dll.PassThruIoctl(self.dev, READ_VBATT, None, ct.byref(v))
        return v.value if rc == 0 else None

    def ground_pin1(self):
        """Pull OBD pin 1 to ground — required to enable MUT-II on non-Evo ECUs."""
        return self.dll.PassThruSetProgrammingVoltage(self.dev, J1962_PIN_1, SHORT_TO_GROUND)

    def release_pin1(self):
        return self.dll.PassThruSetProgrammingVoltage(self.dev, J1962_PIN_1, VOLTAGE_OFF)

    # --- MUT-II live data (K-line 5-baud init -> single-byte request/response) ---
    def mut2_connect(self, baud=None):
        baud = int(baud or self.baud)
        self.baud = baud
        chan = ct.c_ulong(0)
        rc = self.dll.PassThruConnect(self.dev, ISO9141, ISO9141_NO_CHECKSUM, baud, ct.byref(chan))
        if rc:
            raise J2534Error(f"Connect failed rc=0x{rc:02X}: {self._err()}")
        self.chan = chan
        params = (SCONFIG * 2)(SCONFIG(DATA_RATE, baud), SCONFIG(PARITY, 0))
        lst = SCONFIG_LIST(2, ct.cast(params, ct.POINTER(SCONFIG)))
        self.dll.PassThruIoctl(chan, SET_CONFIG, ct.byref(lst), None)
        # PASS-all filter — REQUIRED, or ReadMsgs returns nothing (buffer empty).
        mask = PASSTHRU_MSG(ProtocolID=ISO9141, DataSize=1); mask.Data[0] = 0
        patt = PASSTHRU_MSG(ProtocolID=ISO9141, DataSize=1); patt.Data[0] = 0
        fid = ct.c_ulong(0)
        self.dll.PassThruStartMsgFilter(chan, PASS_FILTER, ct.byref(mask), ct.byref(patt), None, ct.byref(fid))

    def mut2_init(self, addr=0x00, attempts=5):
        for _ in range(attempts):
            inp = (ct.c_ubyte * 1)(addr); out = (ct.c_ubyte * 8)()
            ia = SBYTE_ARRAY(1, ct.cast(inp, ct.POINTER(ct.c_ubyte)))
            oa = SBYTE_ARRAY(8, ct.cast(out, ct.POINTER(ct.c_ubyte)))
            rc = self.dll.PassThruIoctl(self.chan, FIVE_BAUD_INIT, ct.byref(ia), ct.byref(oa))
            if rc == 0:
                self.dll.PassThruIoctl(self.chan, CLEAR_RX_BUFFER, None, None)
                return bytes(out[:oa.NumOfBytes])
        raise J2534Error("MUT-II init failed (pin1 grounded? ECU in run mode, not flash/boot mode?)")

    def mut2_request(self, req, timeout=None):
        import time
        timeout = int(timeout if timeout is not None else self.request_timeout_ms)
        m = PASSTHRU_MSG(ProtocolID=ISO9141, DataSize=1); m.Data[0] = req
        n = ct.c_ulong(1)
        self.dll.PassThruWriteMsgs(self.chan, ct.byref(m), ct.byref(n), timeout)
        deadline = time.monotonic() + timeout / 1000.0
        while time.monotonic() < deadline:
            rx = PASSTHRU_MSG(ProtocolID=ISO9141); n = ct.c_ulong(1)
            rc = self.dll.PassThruReadMsgs(self.chan, ct.byref(rx), ct.byref(n), 100)
            if rc == 0 and n.value and rx.DataSize:
                if rx.RxStatus & TX_MSG_TYPE:   # skip echo of our own request
                    continue
                return rx.Data[rx.DataSize - 1]
        return None

    # --- Diagnostic trouble codes ---
    def read_dtcs(self, low_req=0x38, high_req=0x39):
        """Return list of (code, description) for currently-set faults.

        On many pre-/early-MUT ECUs, active DTCs are bitmasks at 0x38/0x39.
        On later turbo ECUs those addresses are MDP/boost — if live data for
        0x38 looks like pressure rather than a sparse bitmask, DTC decode may
        not apply to that ECU family.
        """
        lo = self.mut2_request(low_req)
        hi = self.mut2_request(high_req)
        if lo is None and hi is None:
            return None  # no response
        lo = lo or 0; hi = hi or 0
        bits = lo | (hi << 8)
        found = []
        for code, bit, desc in DTC_TABLE:
            if bits & (1 << bit):
                found.append((code, desc))
        return found

    def clear_dtcs(self, req=0xCA):
        """Clear fault codes; ECU returns 0x00 on success."""
        return self.mut2_request(req)

    def disconnect(self):
        if self.chan is not None:
            self.dll.PassThruDisconnect(self.chan); self.chan = None

    def close(self):
        self.disconnect()
        try:
            self.dll.PassThruClose(self.dev)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Vehicle profiles
#
# Same MUT-II channel / request IDs for CE-class ECUs. Profiles change branding,
# expected sensors, and Settings notes — not the core scale formulas.
# ---------------------------------------------------------------------------

DEFAULT_PROFILE_ID = "4g15_12v"

VEHICLE_PROFILES = {
    "4g15_12v": {
        "id": "4g15_12v",
        "name": "4G15 12V",
        "short": "4G15 12V",
        "line": "CE Lancer / Mirage  ·  4G15 12V  ·  MUT-II  ·  1996–2003",
        "chassis": "CE Lancer / Mirage 1.5",
        "engine": "4G15 SOHC 12V 1.5L",
        "protocol": "MUT-II K-line",
        "region": "AU metric",
        "airflow": "Often MAP-based (check 0x1A if AFM fitted)",
        "boost": "N/A (NA)",
        "ecu_hint": "Metal or plastic CE ECU",
        "note_overrides": {
            0x1A: "raw × 6.29 Hz  ·  only if AFM/MAF fitted (many 4G15 use MAP)",
            0x15: "raw × 0.5 kPa  ·  sea level ~100–102",
            0x3A: "NTC ADC → °C  ·  manifold IAT on many 4G15",
            0x38: "MAP scale  ·  may be useful on MAP-based 4G15",
        },
    },
    "4g93_maf": {
        "id": "4g93_maf",
        "name": "4G93 MAF",
        "short": "4G93 MAF",
        "line": "CE Lancer  ·  4G93 MAF  ·  MUT-II  ·  MD360479 class",
        "chassis": "CE Lancer 1.8",
        "engine": "4G93 SOHC 1.8L",
        "protocol": "MUT-II K-line",
        "region": "AU metric",
        "airflow": "Karman vortex MAF (Hz) — stock 4G93",
        "boost": "N/A (NA)",
        "ecu_hint": "e.g. MD360479 plastic H8",
        "note_overrides": {
            0x1A: "raw × 6.29 Hz  ·  Karman vortex MAF (stock 4G93)",
            0x15: "raw × 0.5 kPa  ·  baro often in MAF housing",
            0x3A: "NTC ADC → °C  ·  IAT often built into MAF",
            0x38: "MAP/boost scale — usually N/A on MAF NA 4G93",
            0x29: "raw × 0.256 ms  ·  4G93 ~220 cc injectors",
        },
    },
}


def get_profile(profile_id=None):
    """Return a vehicle profile dict; falls back to primary 4G15 12V."""
    if profile_id in VEHICLE_PROFILES:
        return VEHICLE_PROFILES[profile_id]
    return VEHICLE_PROFILES[DEFAULT_PROFILE_ID]


def profile_ids():
    return list(VEHICLE_PROFILES.keys())


def profile_choices():
    """Ordered (id, display name) for UI combos."""
    return [(pid, VEHICLE_PROFILES[pid]["name"]) for pid in VEHICLE_PROFILES]


# ---------------------------------------------------------------------------
# MUT-II parameter catalogue
#
# id: (name, scale_fn, units, gauge_max, gauge_min)
#
# Formulas match EvoScan / OpenPort type=mut2 logcfg (OBD-era MUT-II).
# Shared across CE 4G15 12V and 4G93 MAF profiles (NA, metric speed).
# ---------------------------------------------------------------------------

def _pct255(x):
    return x * 100.0 / 255.0

def _fuel_trim(x):
    # EvoScan: x * 0.19607843 - 25  →  centred at ~0% when raw ≈ 128
    return x * (50.0 / 255.0) - 25.0

def _egr_f(x):
    return -2.7 * x + 597.7


# MUT 0x07 / 0x3A are raw NTC ADC (high = cold, low = hot) — NOT plain °C.
# EvoScan applies an internal lookup for these IDs; without it coolant reads
# too low and intake air too high. Table matches typical Mitsi ECT/IAT NTC
# curves used on OBD-era MUT-II (Evo / CE-class ECUs).
_TEMP_ADC = (
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75,
    80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130, 140, 150, 160,
    170, 180, 190, 200, 210, 220, 230, 240, 250, 255,
)
_TEMP_C = (
    148, 136, 125, 116, 108, 101, 95, 89, 84, 80, 76, 72, 68, 65, 62, 59,
    56, 53, 50, 48, 45, 43, 40, 38, 35, 33, 31, 27, 23, 19,
    15, 11, 7, 3, -1, -6, -12, -18, -28, -40,
)


def _mut_temp_c(x):
    """Convert raw MUT temperature ADC (0x07 / 0x3A) to °C."""
    x = max(0.0, min(255.0, float(x)))
    # exact hit
    for i, a in enumerate(_TEMP_ADC):
        if x <= a:
            if i == 0:
                return float(_TEMP_C[0])
            a0, a1 = _TEMP_ADC[i - 1], a
            t0, t1 = _TEMP_C[i - 1], _TEMP_C[i]
            if a1 == a0:
                return float(t1)
            return t0 + (t1 - t0) * (x - a0) / (a1 - a0)
    return float(_TEMP_C[-1])


def _scaled_temp_c(x):
    """ECU-scaled temp MUT 0x10 / 0x11 → °C (same encoding as OBD-II)."""
    return float(x) - 40.0


ALL_PARAMS = {
    # --- always useful on 4G15 ---
    0x21: ("RPM",          lambda x: x * 31.25,     "rpm",  8000, 0),
    0x06: ("Timing",       lambda x: x - 20,        "°",      50, -10),
    # 0x07 / 0x3A = raw NTC ADC → °C via thermistor table (NOT raw-as-°C)
    0x07: ("Coolant",      _mut_temp_c,             "°C",    130, -40),
    0x3A: ("Intake Temp",  _mut_temp_c,             "°C",    100, -40),
    # ECU-scaled temps (when supported): °C = raw − 40
    0x10: ("Coolant Scl",  _scaled_temp_c,          "°C",    130, -40),
    0x11: ("Intake Scl",   _scaled_temp_c,          "°C",    100, -40),
    0x17: ("Throttle",     _pct255,                 "%",     100, 0),
    0x14: ("Battery",      lambda x: x * 0.0733,    "V",      16, 0),
    0x15: ("Baro",         lambda x: x * 0.5,       "kPa",   110, 0),
    0x1A: ("Airflow",      lambda x: x * 6.29,      "Hz",   1600, 0),
    0x1B: ("P/S Switch",   lambda x: 1.0 if x else 0.0, "state", 1, 0),
    0x1C: ("Engine Load",  lambda x: x * 0.625,     "%",     160, 0),
    0x2F: ("Speed",        lambda x: x * 2.0,       "km/h",  220, 0),
    0x29: ("Inj Pulse",    lambda x: x * 0.256,     "ms",     66, 0),
    0x13: ("O2 Sensor",    lambda x: x * 0.0195,    "V",     1.0, 0),
    0x0C: ("Fuel Trim Lo", _fuel_trim,              "%",      25, -25),
    0x0D: ("Fuel Trim Mid",_fuel_trim,              "%",      25, -25),
    0x0E: ("Fuel Trim Hi", _fuel_trim,              "%",      25, -25),
    0x26: ("Knock Sum",    lambda x: float(x),      "count",  50, 0),
    0x27: ("Octane",       lambda x: float(x),      "count", 255, 0),
    0x24: ("Target Idle",  lambda x: x * 7.8,       "rpm",  2000, 0),
    0x16: ("ISC Steps",    lambda x: float(x),      "steps", 140, 0),
    0x12: ("EGR Temp",     _egr_f,                  "°F",    600, 0),
    # --- turbo-oriented (often unused / N/A on NA 4G15) ---
    0x86: ("WG Duty",      _pct255,                 "%",     100, 0),
    0x38: ("Manifold",     lambda x: x * 0.1935,    "psi",    30, 0),
}

# Default gauges for CE NA live sessions (both profiles)
DEFAULT_GAUGES = [0x21, 0x06, 0x07, 0x3A, 0x17, 0x14, 0x15, 0x1A, 0x1C, 0x2F, 0x29, 0x13]

# Short notes shown in Settings (scaling provenance / expected range)
# Service bands: AU CE / Mirage MFI FSM + Autodata 4G15 CE (96–05)
PARAM_NOTES = {
    0x21: "raw × 31.25  ·  FSM idle 700±50 (AU 96: 750±50)",
    0x06: "raw − 20  ·  base 5±2° BTDC @~700 (timing link earthed)",
    0x07: "NTC ADC → °C  ·  warm 80–100; ECT 2.1–2.7kΩ@20 / 0.26–0.36@80",
    0x3A: "NTC ADC → °C  ·  IAT 2.3–3.0kΩ@20 / 0.30–0.42@80; cold≈ambient",
    0x10: "ECU scaled  ·  °C = raw − 40  ·  if supported",
    0x11: "ECU scaled  ·  °C = raw − 40  ·  if supported",
    0x17: "raw × 100/255  ·  closed TPS adjust 400–1000 mV",
    0x14: "raw × 0.0733  ·  running 13.2–14.8 V  ·  low alt ~12.3 V",
    0x15: "raw × 0.5 kPa  ·  sea level ~100–102",
    0x1A: "raw × 6.29 Hz (Karman vortex / AFM)",
    0x1B: "digital input  ·  decode unverified — Snap-on read P/S Off while app showed ON; may be multi-switch bitfield",
    0x1C: "raw × 0.625 %",
    0x2F: "raw × 2  →  km/h (AU metric)",
    0x29: "raw × 0.256 ms  ·  idle often ~2–4 ms",
    0x13: "raw × 0.0195 V  ·  FSM warm idle switches 0–0.4 ↔ 0.6–1.0 V",
    0x0C: "(raw×50/255)−25  ·  centred ~0%",
    0x0D: "(raw×50/255)−25",
    0x0E: "(raw×50/255)−25",
    0x26: "knock event count",
    0x27: "octane learn (raw count)",
    0x24: "raw × 7.8 rpm  ·  target near curb idle",
    0x16: "idle air stepper position",
    0x12: "−2.7×raw+597.7 °F  ·  if fitted (many AU 4G15 no EGR)",
    0x86: "turbo only — usually N/A on NA CE",
    0x38: "MAP abs  ·  idle sensor 0.9–1.5 V  ·  useful on MAP 4G15",
}


def param_notes_for(profile_id=None):
    """Base MUT notes merged with the active vehicle profile overrides."""
    notes = dict(PARAM_NOTES)
    prof = get_profile(profile_id)
    notes.update(prof.get("note_overrides") or {})
    return notes


# ---------------------------------------------------------------------------
# Sensor health bands (green / red status lights)
#
# Derived from AU CE / Mirage MFI FSM service specs + Autodata 4G15 CE:
#   - Basic idle 700±50 (Autodata / USDM 1.5L); AU 96 FSM 750±50
#   - Base timing 5±2° BTDC @ ~700 rpm (timing connector grounded)
#   - Thermostat opens 82°C; warm operating ~80–97°C (FSM monitors)
#   - ECT 2.1–2.7 kΩ @20°C / 0.26–0.36 kΩ @80°C
#   - IAT 2.3–3.0 kΩ @20°C / 0.30–0.42 kΩ @80°C
#   - TPS closed adjust 400–1000 mV; resistance 3.5–6.5 kΩ
#   - O2 front: switches 0–400 mV ↔ 600–1000 mV at warm idle (closed loop)
#   - O2 rich check 0.6–1.0 V when racing
#   - MAP idle 0.9–1.5 V (≈ low absolute pressure)
#   - Fault open/short temps: IAT ≤−45°C / ≥125°C; ECT ≤−45°C / ≥140°C
#   - Battery / charging: KOEO ~12 V; running alternator healthy ~13.5–14.8 V
#   - Low alt output symptom listed at ~12.3 V
#
# Status values returned by sensor_health():
#   "good"  → green LED (sensor / parameter in optimal band)
#   "bad"   → red LED  (out of optimal / fault-like range)
#   "warn"  → amber (marginal / context-dependent)
#   None    → no health light (display-only / insufficient context)
# ---------------------------------------------------------------------------

# PIDs that get a status light on gauges / hero tiles
HEALTH_PIDS = frozenset({
    0x21, 0x06, 0x07, 0x3A, 0x10, 0x11, 0x17, 0x14, 0x15, 0x1A,
    0x1C, 0x2F, 0x29, 0x13, 0x0C, 0x0D, 0x0E, 0x26, 0x27, 0x24,
    0x16, 0x38,
})

# Human-readable criteria (shown in Settings notes / tooltips)
HEALTH_CRITERIA = {
    0x21: "Idle green 650–850 · red if 0 while other data live or >7500",
    0x06: "Idle green 3–18° · base FSM 5±2° with timing link earthed",
    0x07: "Warm green 80–100°C · red ≤−20 or ≥110 (FSM fault ~−45/140)",
    0x3A: "Green −10–60°C typical · red ≤−40 or ≥100 (FSM fault −45/125)",
    0x10: "Same bands as Coolant (ECU-scaled)",
    0x11: "Same bands as Intake Temp (ECU-scaled)",
    0x17: "Closed green 0–12% · WOT green ≥75% · red stuck mid with 0 rpm",
    0x14: "Running green 13.2–14.8 V · red <11.5 or >15.5 · KOEO ~12 OK",
    0x15: "Sea-level green 95–105 kPa · red <80 or >110",
    0x1A: "MAF idle green ~15–80 Hz (4G93) · red 0 with rpm or ≥1400",
    0x1C: "Idle green 10–45% · red 0 with rpm or >140%",
    0x2F: "Green 0–200 · red >210 (sensor glitch)",
    0x29: "Idle green 1.5–5.0 ms · red 0 with rpm>400 or >20 ms idle",
    0x13: "Warm closed-loop: switching 0.1–0.9 V green · stuck red",
    0x0C: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x0D: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x0E: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x26: "Green 0–5 · warn 6–15 · red >15 knock events",
    0x27: "Learn value · green mid-range · red at extremes 0/255",
    0x24: "Target idle green 600–1100 · red outside 400–1500",
    0x16: "ISC green 0–80 steps warm · red >120",
    0x38: "MAP absolute · idle green ~3–8 psi · red 0 or near max",
}


def _ctx_get(ctx, pid, default=None):
    if not ctx:
        return default
    return ctx.get(pid, default)


def sensor_health(pid, value, ctx=None, history=None):
    """Return 'good' | 'bad' | 'warn' | None for a live MUT value.

    ctx: dict of other live pid→value (e.g. rpm, coolant) for context checks.
    history: optional recent values for this pid (list/deque) — used for O2 switch.
    """
    if pid not in HEALTH_PIDS or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    rpm = _ctx_get(ctx, 0x21)
    cool = _ctx_get(ctx, 0x07)
    if cool is None:
        cool = _ctx_get(ctx, 0x10)
    speed = _ctx_get(ctx, 0x2F)
    # treat anything above cranking pulse as "running"
    engine_running = rpm is not None and rpm > 50
    warm = cool is not None and cool >= 75
    stopped = speed is None or speed < 3
    idle_like = engine_running and rpm is not None and rpm < 1100 and stopped

    # --- RPM ---
    if pid == 0x21:
        if v <= 0 and ctx and any(
            k != 0x21 and ctx.get(k) is not None for k in ctx
        ):
            # other data present but no rpm pulse
            return "bad"
        if v > 7500:
            return "bad"
        # vehicle stopped: evaluate against idle service band
        if stopped and v > 50:
            # Autodata 700±50; AU 96 basic 750±50 — allow both
            if 650 <= v <= 900:
                return "good"
            if 500 <= v < 650 or 900 < v <= 1100:
                return "warn"
            # too low / hunting high while not moving
            if v < 500 or v > 1200:
                return "bad"
            return "warn"
        if 50 < v <= 7000:
            return "good"
        if 0 < v <= 50:
            return "warn"  # cranking
        return None

    # --- Timing advance ---
    if pid == 0x06:
        if v < -5 or v > 45:
            return "bad"
        if idle_like:
            # base 5±2 with timing link; live MUT often 5–15° at idle
            if 3 <= v <= 18:
                return "good"
            if -2 <= v < 3 or 18 < v <= 25:
                return "warn"
            return "bad"
        if -5 <= v <= 40:
            return "good"
        return "warn"

    # --- Coolant (raw NTC or scaled) ---
    if pid in (0x07, 0x10):
        if v <= -20 or v >= 110:
            return "bad"
        if v >= 105:
            return "bad"
        if warm or (engine_running and cool is not None):
            if 80 <= v <= 100:
                return "good"
            if 70 <= v < 80 or 100 < v < 105:
                return "warn"
            if engine_running and v < 40 and rpm and rpm > 600:
                # still cold after start — not red immediately
                return "warn" if v > -10 else "bad"
        if -10 <= v < 110:
            return "good" if v < 105 else "warn"
        return None

    # --- Intake air temp ---
    if pid in (0x3A, 0x11):
        if v <= -40 or v >= 100:
            return "bad"
        if -10 <= v <= 60:
            return "good"
        if 60 < v < 90:
            return "warn"
        if v > 90:
            return "bad"
        if -40 < v < -10:
            return "warn"
        return None

    # --- Throttle % ---
    if pid == 0x17:
        if v < 0 or v > 100:
            return "bad"
        if idle_like or (not engine_running and v <= 15):
            if 0 <= v <= 12:
                return "good"
            if 12 < v <= 20:
                return "warn"
            return "bad"  # high throttle at idle / closed expected
        if v >= 75:
            return "good"  # WOT region
        if 0 <= v <= 100:
            return "good"
        return None

    # --- Battery ---
    if pid == 0x14:
        if v < 11.5 or v > 15.5:
            return "bad"
        if engine_running:
            if 13.2 <= v <= 14.8:
                return "good"
            if 12.5 <= v < 13.2 or 14.8 < v <= 15.2:
                return "warn"  # FSM lists ~12.3 V as low alternator
            if v < 12.5:
                return "bad"
            return "warn"
        # KOEO / not running
        if 11.8 <= v <= 13.0:
            return "good"
        if 11.5 <= v < 11.8 or 13.0 < v <= 13.5:
            return "warn"
        return "bad"

    # --- Barometric ---
    if pid == 0x15:
        if v < 80 or v > 110:
            return "bad"
        if 95 <= v <= 105:
            return "good"
        if 90 <= v < 95 or 105 < v <= 108:
            return "warn"  # altitude / weather
        return "warn"

    # --- Airflow Hz (Karman MAF) ---
    if pid == 0x1A:
        if engine_running and v <= 0:
            return "bad"
        if v >= 1400:
            return "bad"
        if idle_like:
            if 15 <= v <= 80:
                return "good"
            if 5 <= v < 15 or 80 < v <= 120:
                return "warn"
            return "bad" if v > 0 else "bad"
        if 5 < v < 1200:
            return "good"
        return "warn" if v > 0 else None

    # --- Engine load ---
    if pid == 0x1C:
        if engine_running and v <= 0:
            return "bad"
        if v > 140:
            return "bad"
        if idle_like:
            if 10 <= v <= 45:
                return "good"
            if 5 <= v < 10 or 45 < v <= 60:
                return "warn"
            return "bad"
        if 0 < v <= 130:
            return "good"
        return None

    # --- Vehicle speed ---
    if pid == 0x2F:
        if v < 0 or v > 210:
            return "bad"
        return "good"

    # --- Injector pulse width ---
    if pid == 0x29:
        if engine_running and v <= 0:
            return "bad"
        if idle_like:
            if 1.5 <= v <= 5.0:
                return "good"
            if 1.0 <= v < 1.5 or 5.0 < v <= 8.0:
                return "warn"
            if v > 8.0:
                return "bad"
            return "warn"
        if 0 < v <= 25:
            return "good"
        if v > 25:
            return "warn"
        return None

    # --- O2 sensor voltage ---
    if pid == 0x13:
        if v < 0 or v > 1.15:
            return "bad"
        # Stuck rich / lean when warm
        if warm and engine_running:
            hist = list(history) if history is not None else []
            if len(hist) >= 8:
                recent = hist[-12:]
                lo, hi = min(recent), max(recent)
                # FSM: should alternate 0–400 mV and 600–1000 mV at idle
                if hi - lo < 0.08:
                    if v >= 0.55:
                        return "bad"  # stuck high (rich / failed)
                    if v <= 0.15:
                        return "bad"  # stuck low (lean / failed)
                    return "warn"  # not switching enough
                if lo <= 0.45 <= hi or (lo < 0.4 and hi > 0.55):
                    return "good"
                return "warn"
            # no history yet — band check only
            if 0.05 <= v <= 0.95:
                return "good"
            return "warn"
        if 0.0 <= v <= 1.0:
            return "good" if not warm else "warn"
        return None

    # --- Fuel trims ---
    if pid in (0x0C, 0x0D, 0x0E):
        av = abs(v)
        if av > 18:
            return "bad"
        if av > 10:
            return "warn"
        return "good"

    # --- Knock sum ---
    if pid == 0x26:
        if v > 15:
            return "bad"
        if v > 5:
            return "warn"
        return "good"

    # --- Octane learn ---
    if pid == 0x27:
        if v <= 0 or v >= 250:
            return "bad"
        if 20 <= v <= 220:
            return "good"
        return "warn"

    # --- Target idle ---
    if pid == 0x24:
        if v < 400 or v > 1500:
            return "bad"
        if 600 <= v <= 1100:
            return "good"
        return "warn"

    # --- ISC steps ---
    if pid == 0x16:
        if v > 120:
            return "bad"
        if v > 80:
            return "warn"
        if 0 <= v <= 80:
            return "good"
        return None

    # --- Manifold absolute (MAP) — more relevant on 4G15 ---
    if pid == 0x38:
        if v <= 0 or v >= 28:
            return "bad"
        if idle_like:
            # ~25–45 kPa abs ≈ 3.6–6.5 psi absolute at idle
            if 3.0 <= v <= 8.5:
                return "good"
            if 2.0 <= v < 3.0 or 8.5 < v <= 12:
                return "warn"
            return "bad"
        if 2.0 <= v <= 22:
            return "good"
        return "warn"

    return None


# Back-compat alias
MUT2_PARAMS = ALL_PARAMS

# Diagnostic trouble code table: (display code, bit position, description)
DTC_TABLE = [
    (11, 0, "Oxygen sensor"),
    (12, 1, "Intake air flow sensor"),
    (13, 2, "Intake air temperature sensor"),
    (14, 3, "Throttle position sensor"),
    (15, 4, "ISC motor position sensor"),
    (21, 5, "Engine coolant temperature sensor"),
    (22, 6, "Crankshaft position sensor"),
    (23, 7, "Camshaft position sensor"),
    (24, 8, "Vehicle speed sensor"),
    (25, 9, "Barometric pressure sensor"),
    (31, 10, "Knock sensor"),
    (41, 11, "Injector circuit"),
    (42, 12, "Fuel pump relay"),
    (43, 13, "EGR sensor"),
    (44, 14, "Ignition coil"),
    (36, 15, "Ignition circuit"),
]
