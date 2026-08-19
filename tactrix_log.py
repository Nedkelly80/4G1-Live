"""Analyse Tactrix OpenPort / MegaLogViewer / 4G1 Live session CSVs.

Does not invent MUT request IDs. Column mapping uses the same catalogue
as j2534.ALL_PARAMS plus the known Tactrix logcfg / MLV aliases.
"""
from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import j2534

_NUMBERED_LOG_RE = re.compile(r"^log(\d+)\.csv$", re.IGNORECASE)

# header (lower, stripped) -> MUT pid
_HEADER_PID = {
    "rpm": 0x21,
    "enginespeed": 0x21,
    "timing": 0x06,
    "timingadv": 0x06,
    "ecu_timingadv": 0x06,
    "coolant": 0x07,
    "clt": 0x07,
    "coolanttemp": 0x07,
    "coolant_c": 0x10,
    "intake temp": 0x3A,
    "intaketemp": 0x3A,
    "iat": 0x3A,
    "airtemp": 0x3A,
    "iat_c": 0x11,
    "throttle": 0x17,
    "tps": 0x17,
    "throttleposition": 0x17,
    "battery": 0x14,
    "battery_v": 0x14,
    "batteryvoltage": 0x14,
    "baro": 0x15,
    "engine load": 0x1C,
    "engineload": 0x1C,
    "load": 0x1C,
    "ecu_load": 0x1C,
    "ecuload": 0x1C,
    "speed": 0x2F,
    "vss": 0x2F,
    "vss_ecu_kph": 0x2F,
    "inj pulse": 0x29,
    "injpulsedwidth_ms": 0x29,
    "injpulsewidth_ms": 0x29,
    "injpulsewidth": 0x29,
    "pw": 0x29,
    "o2 sensor": 0x13,
    "o2": 0x13,
    "narrowbando2_v": 0x13,
    "o2sensor": 0x13,
    "airflow": 0x1A,
    "isc steps": 0x16,
    "iscsteps": 0x16,
    "target idle": 0x24,
    "targetidle": 0x24,
    "fuel trim lo": 0x0C,
    "fueltrim_low": 0x0C,
    "fuel trim mid": 0x0D,
    "fueltrim_mid": 0x0D,
    "fuel trim hi": 0x0E,
    "fueltrim_high": 0x0E,
    "knock sum": 0x26,
    "knocksum": 0x26,
    "octane": 0x27,
    "octnumber": 0x27,
    "manifold": 0x38,
}

_TIME_HEADERS = ("t", "time", "Time", "TIME")


class TactrixLogError(ValueError):
    """Raised when a log cannot be parsed into usable numbers."""


@dataclass
class ChannelStats:
    count: int
    minimum: float
    maximum: float
    average: float


@dataclass
class LogVerdict:
    """Instant pit-lane call from the numbers — does not wait on Grok."""

    call: str  # NORMAL / WATCH / FIX NOW
    headline: str
    findings: list[str]


@dataclass
class LogAnalysis:
    source_path: Path
    kind: str
    headers: tuple[str, ...]
    row_count: int
    duration_seconds: float
    sampling_hz: float
    pid_stats: dict[int, ChannelStats]
    high_load_rows: int
    idle_rows: int
    health_counts: dict[int, dict[str, int]]
    notes: list[str] = field(default_factory=list)
    o2_switches: int | None = None
    wot_pulls: int = 0
    idle_stats: dict[int, ChannelStats] = field(default_factory=dict)
    wot_stats: dict[int, ChannelStats] = field(default_factory=dict)
    extra_stats: dict[str, ChannelStats] = field(default_factory=dict)
    verdict: LogVerdict | None = None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _finite(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stats(values: list[float]) -> ChannelStats:
    return ChannelStats(len(values), min(values), max(values), sum(values) / len(values))


def detect_kind(headers: tuple[str, ...]) -> str:
    lower = {_norm(h) for h in headers}
    if "ecu_load" in lower and "injpulsewidth_ms" in lower:
        return "tactrix-card"
    if "pw" in lower and "clt" in lower:
        return "mlv"
    if "inj pulse" in lower or ("t" in lower and "coolant" in lower):
        return "4g1-session"
    return "generic-csv"


def numbered_log_index(path: str | os.PathLike) -> int | None:
    match = _NUMBERED_LOG_RE.fullmatch(Path(path).name)
    return int(match.group(1)) if match else None


def find_latest_numbered_log(root: str | os.PathLike) -> Path:
    root_path = Path(root)
    candidates = []
    for path in root_path.iterdir():
        if path.is_file():
            index = numbered_log_index(path)
            if index is not None:
                candidates.append((index, path))
    if not candidates:
        raise TactrixLogError("No numbered Tactrix logNNNN.csv file found")
    return max(candidates, key=lambda item: item[0])[1]


def default_search_roots() -> list[Path]:
    home = Path.home()
    desktop = home / "OneDrive" / "Desktop"
    g_desktop = Path(r"G:\Users\trmra\OneDrive\Desktop")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "4G1 Live AI"
    roots = [
        desktop / "Tactrix Logs MLV",
        desktop / "Tactrix Logs",
        desktop / "Darwin round 6 tactrix data",
        g_desktop / "Tactrix Logs MLV",
        g_desktop / "Tactrix Logs",
        g_desktop / "Darwin round 6 tactrix data",
        local / "Sessions",
        local / "Tactrix Sessions",
    ]
    if os.name == "nt":
        for letter in "DEFG":
            roots.append(Path(f"{letter}:/"))
    return roots


def discover_latest_log(roots=None) -> Path:
    """Newest numbered card log, else newest CSV in known log folders."""
    search = list(roots) if roots is not None else default_search_roots()
    numbered = []
    loose = []
    for root in search:
        path = Path(root)
        if not path.is_dir():
            continue
        try:
            entries = list(path.iterdir())
        except OSError:
            continue
        has_cfg = (path / "logcfg.txt").is_file()
        for entry in entries:
            if not entry.is_file() or entry.suffix.lower() != ".csv":
                continue
            index = numbered_log_index(entry)
            if index is not None:
                numbered.append((1 if has_cfg else 0, index, entry.stat().st_mtime, entry))
            else:
                try:
                    loose.append((entry.stat().st_mtime, entry))
                except OSError:
                    continue
    if numbered:
        numbered.sort()
        return numbered[-1][3]
    if loose:
        loose.sort()
        return loose[-1][1]
    raise TactrixLogError("No Tactrix / 4G1 CSV log found")


def _time_column(headers: tuple[str, ...]) -> str | None:
    lookup = {h.lower(): h for h in headers}
    for name in _TIME_HEADERS:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def _map_headers(headers: tuple[str, ...]) -> dict[int, str]:
    mapping = {}
    for header in headers:
        pid = _HEADER_PID.get(_norm(header))
        if pid is not None and pid not in mapping:
            mapping[pid] = header
    return mapping


def _o2_switch_count(values: list[float]) -> int:
    if len(values) < 2:
        return 0
    switches = 0
    prev = values[0] >= 0.45
    for value in values[1:]:
        now = value >= 0.45
        if now != prev:
            switches += 1
            prev = now
    return switches


def analyse_log(path: str | os.PathLike, profile_id: str | None = None) -> LogAnalysis:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise TactrixLogError("CSV has no header row")
            headers = tuple(h.strip() for h in reader.fieldnames if h)
            rows = [{(k or "").strip(): v for k, v in row.items() if k} for row in reader]
    except (UnicodeError, csv.Error) as exc:
        raise TactrixLogError(f"Cannot parse CSV: {source}") from exc
    except OSError as exc:
        raise TactrixLogError(f"Cannot read log: {source}") from exc
    if not rows:
        raise TactrixLogError("CSV has a header but no data rows")

    kind = detect_kind(headers)
    time_header = _time_column(headers)
    times: list[float] = []
    if time_header:
        for row in rows:
            number = _finite(row.get(time_header))
            if number is not None:
                times.append(number)
    if len(times) >= 2 and times[-1] > times[0]:
        duration = times[-1] - times[0]
    else:
        duration = max(0.001, (len(rows) - 1) * 0.075)
        notes_time = "Time column missing or not increasing — duration estimated at 13 Hz"
    sampling = (len(rows) - 1) / duration if duration > 0 else 0.0

    mapping = _map_headers(headers)
    series: dict[int, list[float]] = {pid: [] for pid in mapping}
    notes: list[str] = []
    if "notes_time" in locals():
        notes.append(notes_time)

    for row in rows:
        for pid, header in mapping.items():
            number = _finite(row.get(header))
            if number is not None:
                series[pid].append(number)

    pid_stats = {pid: _stats(values) for pid, values in series.items() if values}

    rpm_h = mapping.get(0x21)
    tps_h = mapping.get(0x17)
    spd_h = mapping.get(0x2F)
    high_load = 0
    idle = 0
    wot_pulls = 0
    in_wot = False
    idle_series: dict[int, list[float]] = {}
    wot_series: dict[int, list[float]] = {}
    extra_series: dict[str, list[float]] = {}
    idc_header = next((h for h in headers if _norm(h) in ("idc", "idc_pct_approx")), None)
    for row in rows:
        rpm = _finite(row.get(rpm_h)) if rpm_h else None
        tps = _finite(row.get(tps_h)) if tps_h else None
        speed = _finite(row.get(spd_h)) if spd_h else None
        is_wot = rpm is not None and tps is not None and rpm >= 3000 and tps >= 80
        is_idle = rpm is not None and rpm < 1100 and (speed is None or speed < 3)
        if is_wot:
            high_load += 1
            if not in_wot:
                wot_pulls += 1
                in_wot = True
        else:
            in_wot = False
        if is_idle:
            idle += 1
        bucket = wot_series if is_wot else idle_series if is_idle else None
        if bucket is not None:
            for pid, header in mapping.items():
                number = _finite(row.get(header))
                if number is not None:
                    bucket.setdefault(pid, []).append(number)
        if idc_header:
            idc = _finite(row.get(idc_header))
            if idc is not None:
                extra_series.setdefault("IDC", []).append(idc)

    step = max(1, len(rows) // 400)
    health_counts: dict[int, dict[str, int]] = {}
    for i, row in enumerate(rows):
        if i % step != 0:
            continue
        ctx = {}
        for pid, header in mapping.items():
            number = _finite(row.get(header))
            if number is not None:
                ctx[pid] = number
        if not ctx:
            continue
        for pid in list(ctx):
            if pid not in j2534.HEALTH_PIDS:
                continue
            verdict = j2534.sensor_health(pid, ctx[pid], ctx=ctx)
            if verdict is None:
                continue
            bucket = health_counts.setdefault(pid, {"good": 0, "warn": 0, "bad": 0})
            bucket[verdict] = bucket.get(verdict, 0) + 1

    o2_values = series.get(0x13) or []
    o2_switches = _o2_switch_count(o2_values) if o2_values else None
    if 0x1A in pid_stats and profile_id == "4g15_12v":
        notes.append("Airflow/MAF channel present — stock AU 4G15 is MAP, treat 0x1A as N/A")
    if 0x38 in pid_stats and pid_stats[0x38].maximum == 0 and pid_stats[0x38].minimum == 0:
        notes.append("Manifold/MAP channel is all-zero — this ECU has not served real MAP on 0x38")

    idle_stats = {pid: _stats(vals) for pid, vals in idle_series.items() if vals}
    wot_stats = {pid: _stats(vals) for pid, vals in wot_series.items() if vals}
    extra_stats = {name: _stats(vals) for name, vals in extra_series.items() if vals}

    analysis = LogAnalysis(
        source_path=source,
        kind=kind,
        headers=headers,
        row_count=len(rows),
        duration_seconds=duration,
        sampling_hz=sampling,
        pid_stats=pid_stats,
        high_load_rows=high_load,
        idle_rows=idle,
        health_counts=health_counts,
        notes=notes,
        o2_switches=o2_switches,
        wot_pulls=wot_pulls,
        idle_stats=idle_stats,
        wot_stats=wot_stats,
        extra_stats=extra_stats,
    )
    analysis.verdict = local_verdict(analysis, series)
    return analysis


def _range(stats: ChannelStats | None, unit: str = "") -> str:
    if stats is None:
        return "n/a"
    tail = f" {unit}" if unit else ""
    return f"{stats.minimum:.1f}–{stats.maximum:.1f}{tail} (avg {stats.average:.1f})"


def local_verdict(analysis: LogAnalysis, series: dict[int, list[float]] | None = None) -> LogVerdict:
    """Factory-band call from the capture. Prefer this over waiting on Grok."""
    findings: list[str] = []
    worst = "NORMAL"
    pid_stats = analysis.pid_stats
    idle = analysis.idle_stats
    wot = analysis.wot_stats

    def raise_to(level: str) -> None:
        nonlocal worst
        rank = {"NORMAL": 0, "WATCH": 1, "FIX NOW": 2}
        if rank[level] > rank[worst]:
            worst = level

    cool = pid_stats.get(0x07) or pid_stats.get(0x10)
    if cool and cool.maximum >= 110:
        findings.append(f"Coolant peaked {cool.maximum:.0f}°C — overheat. Check fan, cap, thermostat (opens 82°C).")
        raise_to("FIX NOW")
    elif cool and cool.maximum >= 100:
        findings.append(f"Coolant peaked {cool.maximum:.0f}°C — hot. Watch fan and airflow after a few laps.")
        raise_to("WATCH")

    iat = pid_stats.get(0x3A) or pid_stats.get(0x11)
    if series and cool:
        cool_s = series.get(0x07) or series.get(0x10) or []
        iat_s = series.get(0x3A) or series.get(0x11) or []
        if len(cool_s) >= 20:
            n = max(5, len(cool_s) // 8)
            climb = sum(cool_s[-n:]) / n - sum(cool_s[:n]) / n
            if climb >= 12:
                findings.append(f"Coolant climbed {climb:.0f}°C through the log — heat building, typical heat-soak pattern.")
                raise_to("WATCH")
        if iat_s and len(iat_s) >= 20:
            n = max(5, len(iat_s) // 8)
            iat_end = sum(iat_s[-n:]) / n
            if iat_end >= 60:
                findings.append(f"Intake air finished ~{iat_end:.0f}°C — heat soak under the bonnet.")
                raise_to("WATCH")

    batt_wot = wot.get(0x14) or pid_stats.get(0x14)
    if batt_wot and batt_wot.minimum < 12.3:
        findings.append(f"Battery sagged to {batt_wot.minimum:.1f} V under load — charging/earth problem.")
        raise_to("FIX NOW")
    elif batt_wot and batt_wot.minimum < 13.2:
        findings.append(f"Battery min {batt_wot.minimum:.1f} V — weak charge under load (healthy running is 13.2–14.8 V).")
        raise_to("WATCH")

    idle_rpm = idle.get(0x21)
    if idle_rpm and idle_rpm.average > 980:
        findings.append(f"Idle averaged {idle_rpm.average:.0f} rpm (spec 700±50 / 750±50). Check ISC, vacuum, PCV.")
        raise_to("WATCH")
    elif idle_rpm and idle_rpm.average < 600:
        findings.append(f"Idle averaged {idle_rpm.average:.0f} rpm — low. Check ISC steps and air leaks.")
        raise_to("WATCH")

    idc = analysis.extra_stats.get("IDC")
    if idc and idc.maximum >= 85:
        findings.append(f"Injector duty peaked {idc.maximum:.0f}% — near the wall. Fuel pressure / injector size.")
        raise_to("FIX NOW")
    elif idc and idc.maximum >= 70:
        findings.append(f"Injector duty peaked {idc.maximum:.0f}% on WOT — watch fuel delivery.")
        raise_to("WATCH")
    else:
        pw_wot = wot.get(0x29)
        if pw_wot and pw_wot.average >= 10:
            findings.append(f"WOT injector pulse avg {pw_wot.average:.1f} ms — fat pulse, confirm fuel pressure.")
            raise_to("WATCH")

    if analysis.idle_rows >= 20 and analysis.o2_switches == 0:
        o2 = pid_stats.get(0x13)
        if o2 and (o2.maximum - o2.minimum) < 0.15:
            findings.append(
                f"O2 barely moved at idle ({o2.minimum:.2f}–{o2.maximum:.2f} V) — should switch 0–0.4 ↔ 0.6–1.0."
            )
            raise_to("WATCH")
    elif analysis.high_load_rows >= 8:
        o2w = wot.get(0x13)
        if o2w and o2w.average >= 0.7:
            findings.append(
                f"O2 pinned ~{o2w.average:.2f} V on WOT — expected open-loop rich, not a wideband AFR."
            )

    if analysis.high_load_rows == 0 and analysis.row_count > 30:
        findings.append("No WOT/high-load rows (RPM≥3000 and TPS≥80%). This looks like idle/cruise only.")

    if 0x2F in pid_stats and pid_stats[0x2F].maximum == 0:
        findings.append("Speed stayed 0 — VSS not logged or not working. Don't use it for distance.")

    rpm = pid_stats.get(0x21)
    if rpm:
        findings.insert(
            0,
            f"{analysis.wot_pulls} WOT pull(s), peak {rpm.maximum:.0f} rpm, "
            f"{analysis.duration_seconds:.0f}s @ {analysis.sampling_hz:.0f} Hz.",
        )

    if not findings:
        findings.append("Nothing outside AU CE 4G15 factory bands on the sampled rows.")

    if worst == "FIX NOW":
        headline = "Something needs a hands-on check before the next heat."
    elif worst == "WATCH":
        headline = "Usable log — a couple of things to keep an eye on."
    else:
        headline = "Looks like a clean session against the factory bands."
    return LogVerdict(call=worst, headline=headline, findings=findings)


def format_analysis(analysis: LogAnalysis) -> str:
    """Human-readable pit-lane report. Verdict first, then the numbers."""
    v = analysis.verdict
    lines = []
    if v:
        lines.extend([v.call, v.headline, ""])
        lines.extend(f"• {item}" for item in v.findings)
        lines.append("")
    lines.extend([
        f"File: {analysis.source_path.name}   ({analysis.kind})",
        f"{analysis.row_count} rows  ·  {analysis.duration_seconds:.1f}s  ·  "
        f"{analysis.sampling_hz:.1f} Hz  ·  {analysis.wot_pulls} WOT pull(s)",
        f"Idle rows: {analysis.idle_rows}   High-load: {analysis.high_load_rows}",
        "",
        "Idle:    "
        + f"RPM {_range(analysis.idle_stats.get(0x21), 'rpm')}   "
        + f"CLT {_range(analysis.idle_stats.get(0x07) or analysis.idle_stats.get(0x10), 'C')}",
        "WOT:     "
        + f"RPM {_range(analysis.wot_stats.get(0x21), 'rpm')}   "
        + f"batt {_range(analysis.wot_stats.get(0x14), 'V')}   "
        + f"PW {_range(analysis.wot_stats.get(0x29), 'ms')}",
        "",
        "All-session ranges:",
    ])
    for pid in sorted(analysis.pid_stats):
        name, _fn, units, _mx, _mn = j2534.ALL_PARAMS[pid]
        stats = analysis.pid_stats[pid]
        unit = "" if units == "state" else f" {units}"
        lines.append(
            f"  {name:12} {stats.minimum:7.2f} … {stats.maximum:7.2f}{unit}"
            f"   avg {stats.average:.2f}"
        )
    if analysis.extra_stats:
        for name, stats in analysis.extra_stats.items():
            lines.append(
                f"  {name:12} {stats.minimum:7.2f} … {stats.maximum:7.2f}   avg {stats.average:.2f}"
            )
    if analysis.o2_switches is not None:
        lines.append(f"  O2 crossings of 0.45 V: {analysis.o2_switches}")
    if analysis.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in analysis.notes)
    lines.append("")
    lines.append("Narrowband O2 is switching evidence, not wideband AFR.")
    return "\n".join(lines)
