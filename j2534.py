"""4G1 Live — J2534 device layer (OpenPort 2.0, Mitsubishi MUT-II).

Talks to the vehicle interface over the standard J2534 pass-thru API.
Must run under 32-bit Python (the J2534 driver is 32-bit).

MUT-II request IDs and scalings are the OBD-era (mid-90s+) set for the
CE Lancer / Mirage (1996–2003), cross-checked on-car against a reference
scan tool and the factory service data.

Vehicle profiles (4G15 12V primary, 4G93 MAF secondary) share the same MUT
channel; notes and branding differ. Coolant (0x07) and intake air (0x3A) are
raw NTC ADC values and must be converted with a thermistor table — not plain °C.
"""

import ctypes as ct
import sys

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
    """Return list of existing candidate DLL paths (Windows only)."""
    if sys.platform != "win32":
        return []
    import os
    found = []
    for p in COMMON_DLLS:
        if os.path.isfile(p) and p not in found:
            found.append(p)
    return found


def default_dll():
    if sys.platform != "win32":
        return ""
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


def create_device(**kwargs):
    """Return the right Device for this platform (J2534 on Windows, serial on macOS)."""
    if sys.platform == "darwin":
        import tactrix_mac
        port = kwargs.pop("dll_path", None) or kwargs.pop("port_path", None)
        return tactrix_mac.Device(port_path=port, **kwargs)
    return Device(**kwargs)


class Device:
    """Thin, safe wrapper over the OpenPort J2534 DLL."""

    def __init__(self, dll_path=None, baud=None, request_timeout_ms=400):
        if sys.platform != "win32":
            raise J2534Error(
                "J2534 DLL interface is Windows-only.\n"
                "Use j2534.create_device() for cross-platform support."
            )
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
        # Inner read window is capped well under the deadline: a 15625-baud
        # single-byte exchange answers in a few ms, so a long block here just
        # wastes the whole poll cycle when a PID is unsupported.
        slice_ms = max(5, min(25, timeout))
        while time.monotonic() < deadline:
            rx = PASSTHRU_MSG(ProtocolID=ISO9141); n = ct.c_ulong(1)
            rc = self.dll.PassThruReadMsgs(self.chan, ct.byref(rx), ct.byref(n), slice_ms)
            if rc == 0 and n.value and rx.DataSize:
                if rx.RxStatus & TX_MSG_TYPE:   # skip echo of our own request
                    continue
                return rx.Data[rx.DataSize - 1]
        return None

    # --- Diagnostic trouble codes ---
    DTC_CANDIDATE_REQS = (0x3B, 0x3C, 0x3D, 0x36, 0x37, 0x47, 0x48, 0x71)

    def read_dtc_candidates(self, reqs=None):
        """Read every candidate DTC register and return {req: raw_or_None}.

        WHY THIS IS NOT A DECODER. As of 18/8/26 nobody can say which request
        carries fault codes on THIS ECU, and three sources disagree:

          * original code here   : 0x38/0x39  -- now known to be MAP/Boost and
                                   an ADC channel. Definitely wrong.
          * MUT_00.txt symbol list: 0x36/0x37/0x47/0x48/0x71. But live
                                   disassembly of a real H8/539F ROM marks
                                   ALL of 0x36, 0x37, 0x47 and 0x48 as BLANK
                                   (untraced) -- no evidence they exist.
          * EvoScan, decompiled  : 0x3B/0x3C/0x3D, three status bytes decoded
                                   into the 14 classic MUT-II codes. But the
                                   same tracing calls 0x3C "Oxygen Sensor #2,
                                   CONFIRMED", which contradicts that too, and
                                   EvoScan's own UI limits it to pre-1997 cars.

        Guessing here is how the Codes tab got broken in the first place. So
        this returns RAW BYTES ONLY and names nothing. Read them on a healthy
        engine, then unplug one sensor and read again: whichever register
        changes is the fault register on this ECU, and then it can be decoded
        properly. That is a 30-second test and it ends the argument.
        """
        out = {}
        for req in (reqs or self.DTC_CANDIDATE_REQS):
            try:
                out[req] = self.mut2_request(req)
            except Exception:
                out[req] = None
        return out

    def read_dtcs(self, low_req=0x47, high_req=0x48, count_req=0x36):
        """DEPRECATED / UNVERIFIED - prefer read_dtc_candidates().

        Kept so existing callers do not break. The registers below come from
        the MUT_00.txt symbol list, which live ROM tracing contradicts (all
        four trace as BLANK). Do not present this output as fault codes
        without confirming the register on the car first.
        """
        n_active = self.mut2_request(count_req)
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
        # The active-fault COUNT is more trustworthy than the bit mapping. If
        # the ECU says it holds faults but no known bit matched, say so plainly
        # rather than reporting a clean bill of health on an incomplete table.
        if n_active:
            unnamed = int(n_active) - len(found)
            if unnamed > 0:
                found.append((
                    f"ECU reports {int(n_active)} active fault(s)",
                    f"{unnamed} not matched to a known code - bit mapping unverified on this ECU",
                ))
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
        "airflow": "Speed-density (MAP) — NO MAF/AFM fitted, confirmed against the AU 4G15 12V workshop manual",
        "boost": "N/A (NA)",
        "ecu_hint": "Metal or plastic CE ECU",
        # Sensors this engine does NOT have. Kept explicit because their absence
        # changes how you read the dashboard, not just what is displayed.
        "knock_sensor": False,
        "egr": False,
        "absent_sensors": (
            "No knock sensor — this ECU has ZERO detonation protection, so a lean "
            "or over-advanced condition is never pulled back by timing retard. "
            "No MAF/AFM — load is speed-density off MAP (0x1A) + rpm. "
            "No EGR. Single narrowband O2. Ignition is distributor-based with the "
            "crank-angle/TDC sensors inside the distributor."
        ),
        "note_overrides": {
            0x1A: "THIS IS THE MAP SENSOR on this car — corrected 18/8/26. The old note here said 'AFM only, ignore' and was exactly backwards. There is no MAF/AFM on this engine at all: load is speed-density from this channel plus rpm",
            0x15: "raw × 0.5 kPa  ·  sea level ~100–102  ·  read an impossible 120 kPa running vs 100 key-on, so the SCALING is wrong — the request id is correct per the MUT table",
            0x3A: "NTC ADC → °C  ·  manifold IAT on many 4G15  ·  NOT built into a MAF housing on this engine — there is no MAF",
            0x38: "manifold DIFFERENTIAL pressure — expected to sit at 0.0 on this car (no EGR). Not the MAP sensor; use 0x1A",
            0x26: "NO KNOCK SENSOR ON THIS ENGINE. Whatever this register reports is not detonation feedback. A reading of 0 does NOT mean the engine is not detonating — it means nothing is listening. Do not treat this channel as knock protection",
            0x12: "EGR temperature — no EGR fitted to this engine, so this channel is not meaningful here",
            0x07: "Warm operating 80–100 °C (thermostat opens 82 °C). NOTE: this car is KNOWN to run cold — Darwin logs showed 30–48 °C for a whole race. That is a real cooling/thermostat condition, not a sensor fault, and it keeps the ECU in warm-up enrichment all race",
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
            0x1A: "the MUT table calls 0x1A manifold absolute pressure. If a Karman-vortex AFM is actually fitted, the historic ×6.29 Hz reading applied here — the base table now shows this channel RAW, so apply whichever conversion the car proves out",
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
# Formulas follow documented OBD-era MUT-II conventions, validated on-car.
# Shared across CE 4G15 12V and 4G93 MAF profiles (NA, metric speed).
# ---------------------------------------------------------------------------

def _pct255(x):
    return x * 100.0 / 255.0

def _fuel_trim(x):
    # Linear ±25% span over the byte range: centred at ~0% when raw ≈ 128
    return x * (50.0 / 255.0) - 25.0

def _egr_f(x):
    return -2.7 * x + 597.7


# MUT 0x07 / 0x3A are raw NTC ADC (high = cold, low = hot) — NOT plain °C.
# These IDs require a thermistor lookup; without it coolant reads too low and
# intake air too high. Table matches typical Mitsubishi ECT/IAT NTC curves used
# on OBD-era MUT-II (CE-class ECUs).
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
    # 0x1A -> RAM 0xF12F. *** THE MAP SENSOR INPUT. PROVEN ON THE CAR
    # 18/8/26 *** - the strongest evidence tier there is, and it beats every
    # list and every disassembly argument that came before it.
    # Shed run 1, one log, two states:
    #   sensor CONNECTED   -> 0x1A held a constant 138 while the engine was
    #                         revved 812 -> 5,281 rpm (a stuck sensor)
    #   sensor UNPLUGGED   -> 0x1A went to 0 the instant the plug came off
    # An open circuit reading zero is raw-ADC behaviour, so this is the SENSOR
    # input, not a calculated value. That distinction is the whole point: when
    # the sensor is unplugged the ECU's calculated load (0x1C) starts MOVING on
    # its rpm/throttle fallback model, which looks healthy and is not. Only
    # this channel tells stuck / unplugged / working apart.
    # Still RAW: no kPa or psi constant is established. To derive one, take two
    # known points once the sensor is fixed - key-on engine-off (atmospheric,
    # cross-check against 0x15 baro) and warm idle (strong vacuum) - and solve.
    0x1A: ("MAP",          lambda x: float(x),      "raw",   255, 0),
    # 0x1B is TPS Idle Adder per the MUT table - it was never a switch byte,
    # which is why the P/S bit decode disagreed with the Snap-on. Real switch
    # and pin state live at 0xA0 sensor pins / 0x9B output pins / 0x9A lights.
    0x1B: ("TPS Idle Add", lambda x: float(x),      "raw",   255, 0),
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
    # 0x38 -> RAM 0xF15F. *** DEAD ON THIS ECU. RETRACTED 18/8/26 BY CAR TEST.
    # The disassembly argument for calling this MAP was good, and it was still
    # wrong for River's ROM: in shed run 1, 0x38 read flat zero for all 1,315
    # samples INCLUDING the 87 s when the MAP sensor was connected and
    # reporting a value the ECU was visibly acting on. A channel that stays
    # zero while the sensor is live does not carry manifold pressure. Most
    # likely a boost cell, meaningless on a naturally aspirated engine.
    # Do not chase this again. Use 0x1A for the sensor, 0x1C for ECU load.
    0x38: ("38 (dead)",    lambda x: float(x),      "raw",   255, 0),
    # 0x1D: EvoScan and the traced ROM agree on AccelEnrich / Airflow-per-rev.
    # MUT_00.txt's "MAP mean" is the odd one out. Shown raw.
    0x1D: ("AccelEnrich",  lambda x: float(x),      "raw",   255, 0),
    # 0x4A = the REAL combined switch-flags byte per the traced ROM:
    # Crank / Idle Position / Power Steering / A/C Switch / Inhibitor.
    # This is what the old "Switches" gauge on 0x1B was reaching for and
    # missing - decode THIS byte against the Snap-on, not 0x1B.
    0x4A: ("Switch Flags",  lambda x: float(x),     "raw",   255, 0),
}

# Default gauges for CE NA live sessions.
# Revised 18/8/26 after live-ROM tracing evidence:
#  - 0x1A (MAP) replaces 0x38, which the car proved dead on 18/8/26.
#  - 0x15 Baro sits next to it on purpose: if the MAP is stuck because its
#    vacuum line is off or blocked it is reading atmosphere, so MAP should sit
#    at or near baro. Side by side, that comparison is free.
#  - 0x1C Engine Load is on so the stuck-sensor case is visible: with the
#    sensor unplugged, load MOVES on the ECU's fallback model while MAP reads
#    zero. Load moving is not proof of a healthy sensor.
#  - 0x24 Target Idle and 0x16 ISC Steps show whether the ECU is holding its
#    idle target or has lost control of the air.
DEFAULT_GAUGES = [0x21, 0x06, 0x07, 0x3A, 0x17, 0x14, 0x1A, 0x15, 0x1C, 0x24, 0x16, 0x29, 0x13]

# Short notes shown in Settings (scaling provenance / expected range)
# Service bands: AU CE / Mirage MFI FSM + Autodata 4G15 CE (96–05)
PARAM_NOTES = {
    0x21: "raw × 31.25  ·  FSM idle 700±50 (AU 96: 750±50)",
    0x06: "raw − 20  ·  base 5±2° BTDC @~700 (timing link earthed)",
    0x07: "NTC ADC → °C  ·  warm 80–100; ECT 2.1–2.7kΩ@20 / 0.26–0.36@80",
    0x3A: "NTC ADC → °C  ·  IAT 2.3–3.0kΩ@20 / 0.30–0.42@80  ·  verify vs Snap-on (read 23 vs app 32, 20/7)",
    0x10: "ECU scaled  ·  °C = raw − 40  ·  if supported",
    0x11: "ECU scaled  ·  °C = raw − 40  ·  if supported",
    0x17: "raw × 100/255  ·  closed TPS adjust 400–1000 mV",
    0x14: "raw × 0.0733  ·  running 13.2–14.8 V  ·  low alt ~12.3 V",
    0x15: "raw × 0.5 kPa  ·  sea level ~100–102",
    0x1A: "MAP SENSOR INPUT — PROVEN ON THE CAR 18/8/26: held 138 with the sensor connected while the engine was revved to 5,281 rpm, and went to 0 the instant it was unplugged. Shown RAW - no kPa/psi constant is established yet. THE TEST that settles a stuck sensor: key-on engine-off, apply vacuum to the sensor port by hand. If this moves, the sensor and wiring are fine and the vacuum line is the fault. If it stays put, the sensor or its wiring is; EvoScan/MitsuLogger call it AirFlow (×6.25–6.29 Hz). Both sources are unofficial. Shown RAW so no wrong conversion is applied. THE TEST: key-on engine-off vs warm idle. If it is MAP the two differ a lot (atmospheric vs vacuum). If it barely moves, it is not MAP on this ECU",
    0x1B: "TPS Idle Adder (per MUT table)  ·  NOT a switch byte — that was a mislabel, and it is why the old P/S bit decode disagreed with the Snap-on. Switch/pin state lives at 0xA0 sensor pins, 0x9B output pins, 0x9A lights",
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
    0x38: "DEAD on this ECU — read flat zero for a whole run INCLUDING 87 s with the MAP sensor connected and reporting. Not manifold pressure. Off by default — not the MAP sensor. Flat 0.0 all session 21/7/26 is expected on a car with no EGR. Off by default",
    0x1D: "Manifold absolute pressure, MEAN  ·  smoothed companion to 0x1A  ·  shown raw",
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
    0x07: "Warm green 80–100°C · red ≤−20 or ≥110 (FSM fault ~−45/140) · this car is known to run COLD (30–48°C a whole race at Darwin) — that is a real cooling fault holding warm-up enrichment on, not a bad sensor",
    0x3A: "Green −10–60°C typical · red ≤−40 or ≥100 (FSM fault −45/125)",
    0x10: "Same bands as Coolant (ECU-scaled)",
    0x11: "Same bands as Intake Temp (ECU-scaled)",
    0x17: "Closed green 0–12% · WOT green ≥75% · red stuck mid with 0 rpm",
    0x14: "Running green 13.2–14.8 V · red <11.5 or >15.5 · KOEO ~12 OK",
    0x15: "Sea-level green 95–105 kPa · red <80 or >110",
    0x1A: "MAP raw · must MOVE with throttle and vacuum · stuck on one value while rpm changes = stuck sensor or vacuum line · 0 = unplugged or open circuit",
    0x1C: "Idle green 10–45% · red 0 with rpm or >140%",
    0x2F: "Green 0–200 · red >210 (sensor glitch)",
    0x29: "Idle green 1.5–5.0 ms · red 0 with rpm>400 or >20 ms idle",
    0x13: "Warm closed-loop: switching 0.1–0.9 V green · stuck red",
    0x0C: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x0D: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x0E: "Green −10…+10% · warn ±10–18 · red beyond ±18",
    0x26: "NO KNOCK SENSOR on the 4G15 12V — never green. 0 = nothing listening, not 'no knock'. Warn 6–15 · red >15 if the register ever moves at all",
    0x27: "Learn value · green mid-range · red at extremes 0/255",
    0x24: "Target idle green 600–1100 · red outside 400–1500",
    0x16: "ISC green 0–80 steps warm · red >120",
    0x38: "dead register on this ECU — always 0, proven on the car 18/8/26",
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
        if v <= 0:
            # Zero rpm is only a fault with evidence the engine is running.
            # KOEO cross-check 20/7/26: key-on defaults lit this red falsely.
            inj = _ctx_get(ctx, 0x29)
            afm = _ctx_get(ctx, 0x1A)
            if (inj is not None and 0.8 < inj < 25) or (afm is not None and afm > 5):
                return "bad"
            return None
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
        if not engine_running:
            return None  # KOEO reports a default (~61° observed) — meaningless
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
    #
    # Corrected 19/8/26. The old band had a hole in it: the "still cold" arm
    # only fired below 40 C, so anything from 40 C up fell through to the
    # catch-all and came back GREEN. Darwin round 6 ran 30-48 C for a whole
    # race — the dashboard would have shown a healthy coolant light for most
    # of it while the ECU sat in warm-up enrichment the entire time.
    #
    # Rule now: while the engine is running, below the FSM warm band is never
    # green. Amber means "not at operating temperature" — correct during a
    # normal warm-up, and correct (and visible) when the thermostat or cooling
    # system is holding the engine cold all race.
    if pid in (0x07, 0x10):
        if v <= -20 or v >= 105:
            return "bad"
        if engine_running:
            # FSM: thermostat opens 82 C, warm operating band 80-100 C
            if 80 <= v <= 100:
                return "good"
            if 70 <= v < 80 or 100 < v < 105:
                return "warn"
            # Below 70 C with the engine running: warming up, or running cold.
            # Either way it is not at operating temperature — never green.
            return "warn"
        # Engine off: judge the reading for plausibility only, not for
        # operating temperature. A cold soak genuinely reads ambient.
        if -10 <= v < 105:
            return "good"
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
        if not engine_running:
            return None  # KOEO default (~127% observed) — meaningless
        if v <= 0:
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
        if not engine_running:
            return None  # unheated sensor KOEO reads bias/noise
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
    # NO GREEN LIGHT HERE, EVER. Corrected 19/8/26 after confirming against the
    # AU 4G15 12V workshop manual that this engine has NO KNOCK SENSOR — the ECU
    # has zero detonation protection. A zero count therefore proves nothing: it
    # means nothing is listening, not that the engine is not detonating. Showing
    # it green was a false all-clear on the one failure mode that melted pistons
    # 3 and 4. A non-zero count still raises a flag (if the register ever moves,
    # something is being reported and the operator should know), but silence is
    # reported as "no information" rather than "healthy".
    if pid == 0x26:
        if v > 15:
            return "bad"
        if v > 5:
            return "warn"
        return None

    # --- Octane learn ---
    if pid == 0x27:
        if not engine_running:
            return None  # KOEO read 0 on-car — learn value only meaningful running
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

    # --- Manifold absolute (MAP) — primary airflow signal on MAP 4G15 ---
    if pid == 0x38:
        # NA absolute pressure ceiling ≈ baro (Snap-on 100.9 kPa = 14.6 psi)
        if v <= 0 or v >= 16:
            return "bad"
        if idle_like:
            # ~25–45 kPa abs ≈ 3.6–6.5 psi absolute at idle
            if 3.0 <= v <= 8.5:
                return "good"
            if 2.0 <= v < 3.0 or 8.5 < v <= 12:
                return "warn"
            return "bad"
        if 2.0 <= v <= 15.5:
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
