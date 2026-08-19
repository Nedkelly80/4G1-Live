"""Durable long-term memory for 4G1 AI.

Survives app restarts. Injected into every Grok system prompt so the assistant
knows Simon, the car, and lessons from prior race nights. Sensor *numbers*
still learn via baseline.py; this file holds human facts and session wisdom.

Stored under the per-user data dir as memory.json (see app.MEMORY_PATH).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

MEMORY_VERSION = 1
MAX_LESSONS = 40
MAX_SUMMARIES = 12
MAX_VEHICLE_NOTES = 20
MAX_RECENT_ALERTS = 24
MAX_SESSION_TURNS_IN_PROMPT = 16

DEFAULT_USER_NAME = "Simon"

# Built-in AU CE 4G15 factory sensor memory. Sourced from AU CE / Mirage MFI
# FSM + Autodata 4G15 CE (96–05) — same numbers as j2534.HEALTH_CRITERIA /
# PARAM_NOTES. Do not invent request IDs or ohm values here.
FACTORY_SENSOR_MEMORY = """
AU CE Mirage / Lancer 4G15 12V MPI (1996–2003) — factory sensor memory
(source: AU MMAL CE FSM Groups 11/13 + Autodata 4G15 CE 96–05):

Air / fuel
- Stock AU 4G15 is MAP / vacuum-sensor MFI (FSM Group 13). No AFM on the stock car.
- MUT 0x1A (Karman Hz) is N/A on MAP 4G15. 4G93 stock uses Karman MAF instead.
- MUT 0x38 (trialled as MAP) returned flat 0.0 for a whole running session on this ECU — real MAP request ID still unknown. Do not treat 0x38 as MAP.
- Injector pulse MUT 0x29: idle often ~2–4 ms (green 1.5–5.0 ms).

Idle / ignition
- Idle: Autodata 700±50 rpm; AU 96 FSM 750±50. Live green 650–850.
- Base ignition: 5±2° BTDC @ ~700 rpm with the timing connector earthed.
- Target idle MUT 0x24: green 600–1100 rpm.

Temperatures (FSM ohm checks are bench / multimeter — not live MUT)
- Thermostat opens 82°C. Warm running ~80–97/100°C. Live coolant green 80–100°C.
- ECT: 2.1–2.7 kΩ @ 20°C, 0.26–0.36 kΩ @ 80°C. Fault open/short ~≤−45°C / ≥140°C.
- IAT: 2.3–3.0 kΩ @ 20°C, 0.30–0.42 kΩ @ 80°C. Fault ~≤−45°C / ≥125°C.
- MUT 0x07 coolant and 0x3A IAT are raw NTC ADC (high=cold, low=hot), converted by table — not raw-as-°C. Cross-check 0x10/0x11 (°C = raw−40) if the ECU serves them.

Throttle / O2 / charging
- TPS closed adjust 400–1000 mV; total resistance 3.5–6.5 kΩ. Live closed throttle green 0–12%.
- Front O2 warm idle (closed loop) switches 0–0.4 V ↔ 0.6–1.0 V. Rich check 0.6–1.0 V.
- O2 heater ~11–18 Ω (Autodata).
- Battery: KOEO ~12 V; running 13.2–14.8 V; red <11.5 or >15.5. Low-alternator symptom ~12.3 V.

CE K-line
- Pin 1 must be grounded for CE Lancer/Mirage K-line. MUT baud 15625, 5-baud address 0x00.

1999 AU low-emission cam (carry-over after 1999)
- Intake BTDC 13° / ABDC 47°. Exhaust BBDC 56° / ATDC 8°.
""".strip()


def factory_sensor_brief():
    """Always-on factory CE 4G15 sensor brief for the Grok system prompt."""
    extra = []
    try:
        import j2534
        extra.append("Live health bands (same as the app LEDs):")
        for pid in (0x21, 0x06, 0x07, 0x3A, 0x17, 0x14, 0x13, 0x29, 0x16, 0x24):
            crit = (j2534.HEALTH_CRITERIA or {}).get(pid)
            spec = (j2534.ALL_PARAMS or {}).get(pid)
            if crit and spec:
                extra.append(f"- {spec[0]} 0x{pid:02X}: {crit}")
    except Exception:
        pass
    if extra:
        return FACTORY_SENSOR_MEMORY + "\n\n" + "\n".join(extra)
    return FACTORY_SENSOR_MEMORY


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_memory():
    """Fresh memory for first launch."""
    return {
        "version": MEMORY_VERSION,
        "user_name": DEFAULT_USER_NAME,
        "prefs": {
            "tone": "short pit-lane answers, friendly mate energy, concrete next checks",
        },
        # profile_id -> {"label": str, "notes": [str, ...]}
        "vehicles": {},
        # [{ "date": iso, "text": str }]
        "lessons": [],
        # [{ "date": iso, "text": str }]
        "session_summaries": [],
        # [{ "date": iso, "name": str, "verdict": str, "text": str }]
        "recent_alerts": [],
        "updated_at": None,
        "factory_seeded": False,
    }


def seed_factory_vehicle(mem):
    """Attach AU CE 4G15 factory notes to in-built vehicle memory once."""
    mem = mem or default_memory()
    vehicles = dict(mem.get("vehicles") or {})
    slot = dict(vehicles.get("4g15_12v") or {"label": "", "notes": []})
    if not slot.get("label"):
        slot["label"] = "AU CE Mirage / Lancer 4G15 12V"
    notes = list(slot.get("notes") or [])
    factory_notes = [
        "AU CE 4G15 is MAP/vacuum MFI — no AFM. Don't expect MUT 0x1A airflow.",
        "Idle spec 700±50 (Autodata) / 750±50 (AU 96 FSM). Base timing 5±2° BTDC, timing link earthed.",
        "Thermostat opens 82°C. ECT 2.1–2.7 kΩ@20 / 0.26–0.36 kΩ@80. IAT 2.3–3.0 kΩ@20 / 0.30–0.42 kΩ@80.",
        "O2 warm idle switches 0–0.4 V ↔ 0.6–1.0 V. TPS closed adjust 400–1000 mV.",
        "CE K-line needs pin 1 ground. MUT 15625 baud, 5-baud addr 0x00.",
        "1999 AU low-emission cam: Int 13°/47°, Exh 56°/8°.",
    ]
    for n in factory_notes:
        if n not in notes:
            notes.append(n)
    slot["notes"] = notes[-MAX_VEHICLE_NOTES:]
    vehicles["4g15_12v"] = slot
    mem["vehicles"] = vehicles
    mem["factory_seeded"] = True
    return mem


def load_memory(path):
    """Load memory.json; return defaults if missing or corrupt."""
    mem = default_memory()
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            mem.update({k: raw[k] for k in mem if k in raw})
            # nested prefs merge
            if isinstance(raw.get("prefs"), dict):
                prefs = dict(mem.get("prefs") or {})
                prefs.update(raw["prefs"])
                mem["prefs"] = prefs
            if not mem.get("user_name"):
                mem["user_name"] = DEFAULT_USER_NAME
            mem["version"] = MEMORY_VERSION
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Could not read AI memory from %s", path)
    already = bool(mem.get("factory_seeded"))
    mem = seed_factory_vehicle(mem)
    if path and not already:
        save_memory(mem, path)
    return mem


def save_memory(mem, path):
    """Atomic write of memory.json."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        out = dict(mem)
        out["version"] = MEMORY_VERSION
        out["updated_at"] = _now_iso()
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        mem["updated_at"] = out["updated_at"]
    except Exception:
        logging.exception("Could not save AI memory to %s", path)


def set_user_name(mem, name):
    name = (name or "").strip()
    if name:
        mem["user_name"] = name


def record_alert(mem, name, verdict, text):
    """Keep a short trail of AI sensor alerts across sessions."""
    entry = {
        "date": _now_iso(),
        "name": name,
        "verdict": verdict,
        "text": (text or "").strip()[:400],
    }
    alerts = list(mem.get("recent_alerts") or [])
    alerts.append(entry)
    mem["recent_alerts"] = alerts[-MAX_RECENT_ALERTS:]


def apply_consolidation(mem, update):
    """Merge a structured consolidation dict (from Grok or local) into memory.

    Expected keys (all optional):
      user_name, prefs (dict), vehicle_label, vehicle_profile_id, vehicle_notes (list[str]),
      lessons (list[str]), summary (str)
    """
    if not update or not isinstance(update, dict):
        return mem

    name = (update.get("user_name") or "").strip()
    if name:
        mem["user_name"] = name

    prefs = update.get("prefs")
    if isinstance(prefs, dict):
        merged = dict(mem.get("prefs") or {})
        for k, v in prefs.items():
            if v is not None and str(v).strip():
                merged[str(k)] = str(v).strip()
        mem["prefs"] = merged

    profile_id = (update.get("vehicle_profile_id") or "").strip() or "default"
    vehicles = dict(mem.get("vehicles") or {})
    slot = dict(vehicles.get(profile_id) or {"label": "", "notes": []})
    label = (update.get("vehicle_label") or "").strip()
    if label:
        slot["label"] = label
    notes = update.get("vehicle_notes") or []
    if isinstance(notes, str):
        notes = [notes]
    existing = list(slot.get("notes") or [])
    for n in notes:
        n = (n or "").strip()
        if n and n not in existing:
            existing.append(n)
    slot["notes"] = existing[-MAX_VEHICLE_NOTES:]
    vehicles[profile_id] = slot
    mem["vehicles"] = vehicles

    lessons = update.get("lessons") or []
    if isinstance(lessons, str):
        lessons = [lessons]
    stored = list(mem.get("lessons") or [])
    for text in lessons:
        text = (text or "").strip()
        if not text:
            continue
        # de-dupe exact matches
        if any(e.get("text") == text for e in stored):
            continue
        stored.append({"date": _now_iso(), "text": text[:500]})
    mem["lessons"] = stored[-MAX_LESSONS:]

    summary = (update.get("summary") or "").strip()
    if summary:
        summaries = list(mem.get("session_summaries") or [])
        summaries.append({"date": _now_iso(), "text": summary[:800]})
        mem["session_summaries"] = summaries[-MAX_SUMMARIES:]

    return mem


def consolidate_local(mem, session_turns, vehicle_profile_id="", vehicle_label=""):
    """Offline fallback: store a crude session summary without calling Grok."""
    if not session_turns:
        return mem
    lines = []
    for turn in session_turns[-MAX_SESSION_TURNS_IN_PROMPT:]:
        role = turn.get("role", "?")
        text = (turn.get("content") or "").strip().replace("\n", " ")
        if text:
            lines.append(f"{role}: {text[:200]}")
    if not lines:
        return mem
    summary = "Session log (no API consolidate): " + " | ".join(lines[-8:])
    return apply_consolidation(mem, {
        "vehicle_profile_id": vehicle_profile_id,
        "vehicle_label": vehicle_label,
        "summary": summary[:800],
    })


def format_for_prompt(mem, vehicle_profile_id=None, vehicle_label=None):
    """Compact brief for the Grok system prompt."""
    mem = mem or default_memory()
    lines = [
        "You are 4G1 AI — a real-world Mitsubishi MUT-II pit-lane diagnostician.",
        "Cars: CE Lancer / Mirage 4G15 12V (MAP) primary; also 4G93 MAF and related 4G1.",
        "Protocol: MUT-II over J2534 K-Line — live ADC-derived sensors, not OBD-II PIDs.",
        "",
        "How to think (trackside, dirty hands):",
        "- Live MUT-II readings are ground truth. Never argue with the numbers on screen.",
        "- Always note engine state: KOEO (key on, engine off), cold idle, warm idle, load.",
        "- Call it: NORMAL / WATCH / FIX NOW — then one concrete next check.",
        "- Prefer real failure modes: vacuum leak, ISC sticky, coolant fan, thermostat, "
        "O2 lazy, MAP hose, grounding, connector corrosion, heatsoak, bad earth.",
        "- Prefer the AU CE factory sensor memory below over generic 4G15 internet numbers.",
        "- Don't invent pinouts, torques, request IDs, or DTCs. If you don't know a factory "
        "number, say so or use manuals/web when those tools are offered.",
        "- Short answers (2–4 sentences) unless they ask for a procedure. Mate energy, not a textbook.",
        "- If they uploaded FSM manuals, trust those over generic web for this car.",
        "- When a manual or web search was used, say which source type supports any factory number; "
        "do not present an unverified number as certain.",
        "- Live data helps diagnose; it is not permission to keep driving. For overheating, low oil "
        "pressure, fuel leaks, or other safety-critical signs, clearly tell the user to stop safely "
        "and verify the fault before continuing.",
    ]
    name = (mem.get("user_name") or DEFAULT_USER_NAME).strip()
    lines.append(f"User's name: {name}. Address them by name when it fits.")
    lines.append("")
    lines.append(factory_sensor_brief())
    lines.append("")

    prefs = mem.get("prefs") or {}
    tone = (prefs.get("tone") or "").strip()
    if tone:
        lines.append(f"Preferred tone: {tone}.")
    other_prefs = [f"{k}={v}" for k, v in prefs.items() if k != "tone" and v]
    if other_prefs:
        lines.append("Other preferences: " + "; ".join(other_prefs) + ".")

    vehicles = mem.get("vehicles") or {}
    if vehicle_profile_id and vehicle_profile_id in vehicles:
        v = vehicles[vehicle_profile_id]
        label = v.get("label") or vehicle_label or vehicle_profile_id
        notes = v.get("notes") or []
        if notes:
            lines.append(f"Known about this car ({label}): " + "; ".join(notes[-6:]) + ".")
        elif label:
            lines.append(f"Active vehicle: {label}.")
    elif vehicle_label:
        lines.append(f"Active vehicle: {vehicle_label}.")
    # include notes for other cars if few
    extra = []
    for pid, v in vehicles.items():
        if pid == vehicle_profile_id:
            continue
        notes = v.get("notes") or []
        if notes:
            extra.append(f"{v.get('label') or pid}: {notes[-1]}")
    if extra:
        lines.append("Other cars: " + " | ".join(extra[-3:]) + ".")

    lessons = mem.get("lessons") or []
    if lessons:
        recent = [e.get("text", "") for e in lessons[-8:] if e.get("text")]
        if recent:
            lines.append("Lessons from prior sessions (use these — real history on this car):")
            for i, text in enumerate(recent, 1):
                lines.append(f"  {i}. {text}")

    summaries = mem.get("session_summaries") or []
    if summaries:
        last = summaries[-1]
        text = (last.get("text") or "").strip()
        if text:
            lines.append(f"Last session summary ({last.get('date', '?')}): {text}")

    alerts = mem.get("recent_alerts") or []
    if alerts:
        bits = []
        for a in alerts[-4:]:
            bits.append(f"{a.get('name')} ({a.get('verdict')}): {(a.get('text') or '')[:120]}")
        lines.append("Recent AI alerts: " + " || ".join(bits))

    lines.append(
        "When you learn a durable fact (name correction, car quirk, fix that worked), "
        "use it in later answers. Do not invent history you were not given."
    )
    return "\n".join(lines)


def memory_stats(mem):
    """Short status string for Settings / AI tab."""
    mem = mem or default_memory()
    n_lessons = len(mem.get("lessons") or [])
    n_sums = len(mem.get("session_summaries") or [])
    n_alerts = len(mem.get("recent_alerts") or [])
    name = mem.get("user_name") or DEFAULT_USER_NAME
    updated = mem.get("updated_at") or "never"
    return (
        f"Memory: {name}  ·  {n_lessons} lessons  ·  "
        f"{n_sums} session summaries  ·  {n_alerts} alerts  ·  updated {updated}"
    )


def format_session_transcript(session_turns, max_turns=MAX_SESSION_TURNS_IN_PROMPT):
    """Plain transcript for consolidation prompts."""
    lines = []
    for turn in (session_turns or [])[-max_turns:]:
        role = (turn.get("role") or "unknown").upper()
        text = (turn.get("content") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)
