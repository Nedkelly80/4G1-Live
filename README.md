# 4G1 Live — Mitsubishi Live ECU Data

Professional live-data dashboard for the **Mitsubishi MUT-II** ECU over a J2534
vehicle interface (OpenPort 2.0).

## Vehicle profiles

| Profile | Use when | Notes |
|---------|----------|--------|
| **4G15 12V** (primary) | CE Lancer / Mirage **1.5 12V** | Product target; many 4G15s are MAP-based |
| **4G93 MAF** | CE Lancer **1.8** (e.g. **MD360479**) | Stock Karman vortex MAF (Hz) |

Switch under **Settings → Vehicle profile**, then **Apply Settings**.  
MUT channel (pin 1, 15625 baud, request IDs) is shared; notes and branding follow the profile.

## Run

Requires **32-bit Python** (the J2534 driver is 32-bit):

```
py -3.13-32 app.py
```

End users do not need Python. Install the versioned Windows package from the
`releases` folder, or use the portable ZIP. The release is deliberately 32-bit
so it can load the supported 32-bit J2534 driver.

## Release build

On the release workstation, run:

```powershell
.\build-release.ps1
```

The script runs the catalogue/scaling tests, creates the PyInstaller application,
compiles the Inno Setup installer, creates a portable ZIP, and prints SHA-256
hashes. Public releases must be code-signed and must complete the validation gate
below. Replace the publisher/support placeholders in `EULA.txt` and `SUPPORT.txt`
before sale.

| File | Purpose |
|------|---------|
| `app.py` | Dashboard / Codes / Settings UI |
| `j2534.py` | Device layer + MUT-II catalogue + vehicle profiles |
| `settings.json` | Theme, gauges, connection, active profile (auto-saved) |

## Features

- **Dashboard** — live gauges (Digital / Bar / Dial), RPM trend chart, telemetry log + CSV export
- **Explore Demo** — realistic simulated data for training and sales demonstrations, clearly isolated from vehicle communication
- **Sensor health LEDs** — green / amber / red on each supported gauge from AU CE / Mirage MFI FSM bands
- **Codes** — read / clear classic Mitsubishi 2-digit DTCs
- **Settings** — themes, gauge style, poll rate, vehicle profile, per-parameter show/hide with scaling + health notes
- **Gauge box sizing** — scale dashboard gauge tiles from 70–170% in Settings
- **Power steering switch gauge** — ECU-driven ON/OFF state via MUT data
- **Commercial runtime** — per-user settings, local diagnostic logs, atomic preference saves, version metadata, and friendly crash reporting

## Using it

1. Plug the interface into the car and PC. Ignition on (run mode, not flash/boot).
2. Pick the matching **vehicle profile** in Settings if needed.
3. **Connect** — reads adapter firmware and battery voltage.
4. **Start Live Data** — grounds OBD pin 1, MUT-II 5-baud init, then polls gauges.
5. **Save CSV** to export a session; **Codes** tab for faults.

### Extra MUT-II gauges you can enable

In **Settings → Parameters**, these are available beyond the default dashboard set:

- `0x10` Coolant Scl
- `0x11` Intake Scl
- `0x1B` P/S Switch (ECU ON/OFF)
- `0x0C` Fuel Trim Lo
- `0x0D` Fuel Trim Mid
- `0x0E` Fuel Trim Hi
- `0x26` Knock Sum
- `0x27` Octane
- `0x24` Target Idle
- `0x16` ISC Steps
- `0x12` EGR Temp
- `0x86` WG Duty (turbo-oriented)
- `0x38` Manifold (MAP-oriented)

**Note:** Only one app can own the OpenPort at a time. If another tool (or an older 4G1 Live window) is connected, a second process will report “no devices available.”

## MUT-II scalings (CE / OBD-era)

The common linear formulas below match published MUT-II community references and
long-standing EvoScan/OpenPort conventions. Mitsubishi does not publish one
universal raw-byte conversion table for every ECU family, so this catalogue must
be validated against the exact ECU/profile before commercial release.

| ID | Parameter | Formula | Units | Sanity check |
|----|-----------|---------|-------|--------------|
| 0x21 | RPM | raw × 31.25 | rpm | idle ~700–900 |
| 0x06 | Timing | raw − 20 | ° | idle often 5–15° |
| 0x07 | Coolant | candidate NTC ADC → °C table | °C | **vehicle validation required**; hot ~85–100 |
| 0x3A | Intake temp | candidate NTC ADC → °C table | °C | **vehicle validation required**; cold should be near ambient |
| 0x10 | Coolant scaled | raw − 40 | °C | ECU-converted, if supported |
| 0x11 | Intake scaled | raw − 40 | °C | ECU-converted, if supported |
| 0x17 | Throttle | raw × 100/255 | % | closed ~0–5, WOT ~80–100 |
| 0x14 | Battery | raw × 0.0733 | V | running ~13.5–14.5 |
| 0x15 | Baro | raw × 0.5 | kPa | sea level ~100–102 |
| 0x1A | Airflow | raw × 6.29 | Hz | Karman MAF (4G93); optional on MAP 4G15 |
| 0x1C | Engine load | raw × 0.625 | % | |
| 0x2F | Speed | raw × 2 | km/h | AU metric |
| 0x29 | Inj pulse | raw × 0.256 | ms | idle often ~2–4 ms |
| 0x13 | O2 sensor | raw × 0.0195 | V | candidate conversion; compare with a meter / known logger |
| 0x0C–0E | Fuel trim | (raw×50/255)−25 | % | centred near 0 |
| 0x24 | Target idle | raw × 7.8 | rpm | |
| 0x16 | ISC steps | raw | steps | |
| 0x26 | Knock sum | raw | count | |
| 0x27 | Octane | raw | count | learn value |
| 0x12 | EGR temp | −2.7×raw+597.7 | °F | if sensor fitted |
| 0x86 | WG duty | raw × 100/255 | % | **N/A on NA CE** |
| 0x38 | Manifold | raw × 0.1935 | psi | more relevant on MAP 4G15 |

### Temperature note (important)

MUT **0x07** (coolant) and **0x3A** (intake air) are treated here as raw NTC ADC.
High ADC = cold, low ADC = hot. 4G1 Live currently converts both through a
candidate thermistor lookup table; do not market these two readings as verified
until the table has been checked on both supported vehicle profiles.

If your ECU also supports scaled temps, enable **0x10** / **0x11** (°C = raw − 40)
in Settings for a cross-check.

### Sensor health lights (green / red)

Each gauge that maps to a factory-testable sensor shows a status LED:

| Colour | Meaning |
|--------|---------|
| **Green** | Value in optimal / service-spec band |
| **Amber** | Marginal (warming up, altitude, slight trim, etc.) |
| **Red** | Outside optimal band or fault-like (stuck O2, overheat, low charge…) |

Bands are taken from **AU CE / Mirage 1.5L MFI FSM** service specs and
**Autodata 4G15 CE (96–05)**:

| Gauge | Optimal (green) highlights | Source |
|-------|----------------------------|--------|
| RPM | Idle **650–850** (spec 700±50 / 750±50) | Autodata + FSM basic idle |
| Timing | Idle **3–18°** (base **5±2°** with timing link earthed) | Autodata / FSM |
| Coolant | Warm **80–100°C** (thermostat opens **82°C**) | Autodata + FSM monitors |
| Intake temp | **−10–60°C** typical; fault ≤−45 / ≥125°C | FSM DTC thresholds |
| Throttle | Closed **0–12%** (TPS adjust **400–1000 mV**) | FSM TPS adjustment |
| Battery | Running **13.2–14.8 V**; red &lt;11.5 or &gt;15.5 | Charging / FSM low-alt ~12.3 V |
| Baro | Sea level **95–105 kPa** | MUT convention |
| Airflow | Idle **~15–80 Hz** (Karman MAF / 4G93) | Community MAF + profile |
| O2 | Warm closed-loop **switching** 0–400 ↔ 600–1000 mV | FSM data-list check |
| Inj pulse | Idle **1.5–5.0 ms** | EvoScan-class idle band |
| Fuel trim | **±10%** good; red beyond **±18%** | Practical closed-loop |
| MAP (0x38) | Idle absolute **~3–8 psi** (0.9–1.5 V sensor) | FSM vacuum-sensor check |

**Resistance checks (bench / multimeter — not live MUT):**

- ECT: **2.1–2.7 kΩ @ 20°C**, **0.26–0.36 kΩ @ 80°C**
- IAT: **2.3–3.0 kΩ @ 20°C**, **0.30–0.42 kΩ @ 80°C**
- TPS total: **3.5–6.5 kΩ**; O2 heater ~**11–18 Ω** (Autodata)

### Gauge scaling vs manuals — confidence

| Parameter | Scaling | Matches factory intent? |
|-----------|---------|-------------------------|
| RPM | raw × 31.25 | Yes — idle lands near 700 with typical raw |
| Timing | raw − 20 | Yes — base ~5° reads correctly |
| Coolant / IAT (0x07 / 0x3A) | NTC ADC table | **Provisional** — FSM publishes Ω vs °C, not MUT byte |
| Coolant / IAT (0x10 / 0x11) | raw − 40 | Standard ECU-scaled if ECU supports |
| Throttle | raw × 100/255 | Yes — closed adjust 0.4–1.0 V ≈ low % |
| Battery | raw × 0.0733 | Yes — running 13.5–14.5 band |
| Baro | raw × 0.5 kPa | Yes — ~100 kPa sea level |
| Airflow | raw × 6.29 Hz | Karman convention; **N/A on many MAP 4G15** |
| O2 | raw × 0.0195 V | ADC-style 0–5 V scale; FSM checks 0.6–1.0 V rich |
| Speed | raw × 2 km/h | AU metric |
| Inj pulse | raw × 0.256 ms | OpenPort convention |

AU **4G15** is **MAP / vacuum-sensor** based (FSM Group 13); **4G93** stock uses
**Karman MAF (Hz)**. Profile notes call this out — do not expect 0x1A on all 4G15s.

### Commercial validation gate

Before calling any value verified, capture the raw response byte and the displayed
value together, then compare it with an independent reference on at least two cars
per profile. Minimum checks:

1. Key-on engine-off: battery against a multimeter; coolant and intake against ambient.
2. Warm idle: RPM against the tachometer, coolant against a trusted scan tool,
   injector pulse and timing against EvoScan (or an equivalent known-good logger).
3. Road test with two people: speed against GPS at several steady speeds; throttle
   at closed, part, and wide-open positions; barometric pressure against local pressure.
4. Save the raw-byte CSV evidence and record ECU part number, engine, firmware,
   ambient temperature, and the reference instrument used.

Until that evidence exists, RPM, timing, throttle, battery, airflow, load, speed,
and injector pulse are **reference-backed but not vehicle-certified**; temperature
curves and O2 scaling are **provisional**. Health LEDs use the same factory bands
as a *live quality indicator*, not a substitute for DTC diagnosis.

## Notes

- Live data needs the ECU in normal run mode (ignition on / engine running).
- Pin 1 is grounded to enable MUT-II on non-Evo ECUs (including CE Lancer / Mirage).
- K-line is slow: more gauges ⇒ lower samples/sec. 10–12 Hz target is a good default.
- DTC mask at 0x38/0x39 is classic MUT; if 0x38 ever looks like continuous pressure data, DTC decode may not apply to that ECU family.
