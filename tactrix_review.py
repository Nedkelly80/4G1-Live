"""Offline analysis of CSV logs written by the Tactrix standalone logger."""

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import re
import shutil


class TactrixLogError(ValueError):
    """Raised when a card log cannot provide trustworthy review data."""


@dataclass(frozen=True)
class LogCfgParam:
    """One Tactrix standalone-logger request parsed from ``logcfg.txt``."""

    name: str
    kind: str
    param_id: str | None
    scaling_rpn: str | None
    priority: int | None


_WEEKEND_LOGCFG_EXPECTED = {
    "RPM": ("mut2", "0x21", "x,31.25,*", 1),
    "TPS": ("mut2", "0x17", "x,0.39215686,*", 1),
    "ECU_Load": ("mut2", "0x1c", "x,0.625,*", 1),
    "ECU_TimingAdv": ("mut2", "0x06", "x,20,-", 1),
    "InjPulseWidth_ms": ("mut2", "0x29", "x,0.256,*", 1),
    "NarrowbandO2_V": ("mut2", "0x13", "x,0.01952,*", 1),
    "Battery_V": ("mut2", "0x14", "x,0.07333,*", 1),
    "Coolant_C": ("mut2", "0x10", "x,40,-", 1),
    "IAT_C": ("mut2", "0x11", "x,40,-", 1),
    "VSS_ECU_kph": ("mut2", "0x2f", "x,2,*", 1),
    "Coolant_raw07": ("mut2", "0x07", None, 2),
    "IAT_raw3A": ("mut2", "0x3a", None, 2),
    "IDC_pct_approx": ("calc", None, "RPM,InjPulseWidth_ms,*,0.0008333333,*", 1),
}
_WEEKEND_LOGCFG_ASSIGNMENT_SIGNATURE = (
    ("type", "mut2"),
    ("setpinvoltage", "1,-2"),
    ("paramname", "RPM"),
    ("paramid", "0x21"),
    ("scalingrpn", "x,31.25,*"),
    ("priority", "1"),
    ("paramname", "TPS"),
    ("paramid", "0x17"),
    ("scalingrpn", "x,0.39215686,*"),
    ("priority", "1"),
    ("paramname", "ECU_Load"),
    ("paramid", "0x1C"),
    ("scalingrpn", "x,0.625,*"),
    ("priority", "1"),
    ("paramname", "ECU_TimingAdv"),
    ("paramid", "0x06"),
    ("scalingrpn", "x,20,-"),
    ("priority", "1"),
    ("paramname", "InjPulseWidth_ms"),
    ("paramid", "0x29"),
    ("scalingrpn", "x,0.256,*"),
    ("priority", "1"),
    ("paramname", "NarrowbandO2_V"),
    ("paramid", "0x13"),
    ("scalingrpn", "x,0.01952,*"),
    ("priority", "1"),
    ("paramname", "Battery_V"),
    ("paramid", "0x14"),
    ("scalingrpn", "x,0.07333,*"),
    ("priority", "1"),
    ("paramname", "Coolant_C"),
    ("paramid", "0x10"),
    ("scalingrpn", "x,40,-"),
    ("priority", "1"),
    ("paramname", "IAT_C"),
    ("paramid", "0x11"),
    ("scalingrpn", "x,40,-"),
    ("priority", "1"),
    ("paramname", "VSS_ECU_kph"),
    ("paramid", "0x2F"),
    ("scalingrpn", "x,2,*"),
    ("priority", "1"),
    ("paramname", "Coolant_raw07"),
    ("paramid", "0x07"),
    ("priority", "2"),
    ("paramname", "IAT_raw3A"),
    ("paramid", "0x3A"),
    ("priority", "2"),
    ("type", "calc"),
    ("paramname", "IDC_pct_approx"),
    ("scalingrpn", "RPM,InjPulseWidth_ms,*,0.0008333333,*"),
)
_REJECTED_LOGCFG_IDS = {
    "0x0c": "fuel trim",
    "0x0d": "fuel trim",
    "0x0e": "fuel trim",
    "0x15": "barometer",
    "0x16": "idle control",
    "0x1a": "Airflow/MAF",
    "0x24": "idle control",
    "0x38": "MDP",
}
_REJECTED_LOGCFG_NAME_TOKENS = (
    "airflow",
    "maf",
    "mdp",
    "knock",
    "octane",
    "idle",
    "baro",
    "fueltrim",
    "fuel_trim",
)


def _active_logcfg_assignments(text: str) -> tuple[tuple[str, str], ...]:
    """Return uncommented ``key=value`` assignments, preserving their order."""
    assignments = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        assignments.append((key.strip().lower(), value.strip()))
    return tuple(assignments)


def parse_logcfg(text: str) -> tuple[LogCfgParam, ...]:
    """Parse MUT-II/calc logger requests and deliberately ignore comments/directives."""
    params: list[LogCfgParam] = []
    kind: str | None = None
    current: dict[str, str] | None = None

    def flush_current() -> None:
        nonlocal current
        if current is None or "paramname" not in current:
            current = None
            return
        try:
            priority = int(current.get("priority", "1"))
        except ValueError:
            priority = None
        params.append(
            LogCfgParam(
                name=current["paramname"],
                kind=kind or "",
                param_id=current.get("paramid"),
                scaling_rpn=current.get("scalingrpn"),
                priority=priority,
            )
        )
        current = None

    for key, value in _active_logcfg_assignments(text):
        if key == "type":
            flush_current()
            kind = value.lower()
        elif key == "paramname":
            flush_current()
            current = {"paramname": value}
        elif current is not None and key in {"paramid", "scalingrpn", "priority"}:
            current[key] = value
    flush_current()
    return tuple(params)


def validate_weekend_logcfg(text: str) -> list[str]:
    """Return safety/evidence errors for the one approved NT43 logger profile."""
    errors: list[str] = []
    assignments = _active_logcfg_assignments(text)
    params = parse_logcfg(text)
    names = [param.name for param in params]
    lower_names = [name.casefold() for name in names]
    mut_params = [param for param in params if param.kind == "mut2"]
    mut_ids = [param.param_id.lower() for param in mut_params if param.param_id]

    type_values = [value.lower() for key, value in assignments if key == "type"]
    for required_type in ("mut2", "calc"):
        occurrences = type_values.count(required_type)
        if occurrences == 0:
            errors.append(f"Missing required type={required_type} directive")
        elif occurrences > 1:
            errors.append(f"Duplicate singleton directive: type={required_type}")
    for type_value in set(type_values) - {"mut2", "calc"}:
        errors.append(f"Unapproved type directive: type={type_value}")

    pin_values = [value for key, value in assignments if key == "setpinvoltage"]
    if not pin_values:
        errors.append("Missing required setpinvoltage=1,-2 directive")
    elif len(pin_values) > 1:
        errors.append("Duplicate singleton directive: setpinvoltage")
    elif pin_values[0] != "1,-2":
        errors.append("Incorrect singleton directive: setpinvoltage")
    if any(key in {"conditionrpn", "action"} for key, _ in assignments):
        errors.append("Active trigger directives are not permitted")

    assignment_counts = Counter(assignments)
    approved_counts = Counter(_WEEKEND_LOGCFG_ASSIGNMENT_SIGNATURE)
    for assignment, count in (assignment_counts - approved_counts).items():
        key, value = assignment
        errors.extend(
            f"Unapproved active directive: {key}={value}" for _ in range(count)
        )
    if assignments != _WEEKEND_LOGCFG_ASSIGNMENT_SIGNATURE:
        errors.append("Active directives do not exactly match the approved weekend profile")

    for index, name in enumerate(names):
        if lower_names.index(name.casefold()) != index:
            errors.append(f"Duplicate parameter name: {name}")
        if any(token in name.casefold() for token in _REJECTED_LOGCFG_NAME_TOKENS):
            errors.append(f"Rejected legacy parameter: {name}")
    for index, param_id in enumerate(mut_ids):
        if mut_ids.index(param_id) != index:
            errors.append(f"Duplicate MUT-II ID: {param_id}")
        if param_id in _REJECTED_LOGCFG_IDS:
            errors.append(
                f"Rejected legacy MUT-II ID {param_id}: {_REJECTED_LOGCFG_IDS[param_id]}"
            )

    request_map = {param.name: param for param in params}
    for name, expected in _WEEKEND_LOGCFG_EXPECTED.items():
        param = request_map.get(name)
        if param is None:
            errors.append(f"Missing required weekend request: {name}")
            continue
        actual = (
            param.kind,
            param.param_id.lower() if param.param_id else None,
            param.scaling_rpn,
            param.priority,
        )
        if actual != expected:
            errors.append(f"Incorrect weekend request: {name}")
    for name in request_map:
        if name not in _WEEKEND_LOGCFG_EXPECTED:
            errors.append(f"Unapproved weekend request: {name}")

    for raw_name in ("Coolant_raw07", "IAT_raw3A"):
        param = request_map.get(raw_name)
        if param is not None and param.scaling_rpn is not None:
            errors.append(f"{raw_name} must remain an unscaled raw count")

    for raw_line in text.splitlines():
        comment = raw_line.strip().lstrip(";").strip().casefold()
        has_provenance = re.search(r"\brom\b|\bcalibration\b", comment) is not None
        status_words = "established|confirmed|verified|proven|known|identified"
        negated_status = re.compile(
            rf"\bnot(?:\s+[a-z]+){{0,2}}\s+(?:{status_words})\b"
        )
        safely_unproven = negated_status.search(comment) is not None
        without_negated_status = negated_status.sub("", comment)
        has_positive_status = re.search(
            rf"\b(?:{status_words})\b", without_negated_status
        ) is not None
        if has_provenance and (not safely_unproven or has_positive_status):
            errors.append("ROM/calibration identity claim is not permitted")
    return errors


_NUMBERED_LOG_RE = re.compile(r"^log(\d+)\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class ChannelStats:
    """Finite numeric summary for one captured channel."""

    count: int
    minimum: float
    maximum: float
    average: float


@dataclass(frozen=True)
class LogReview:
    """Evidence-limited review of a standalone logger CSV capture."""

    source_path: Path
    profile_kind: str
    capture_status: str
    headers: tuple[str, ...]
    row_count: int
    duration_seconds: float
    sampling_hz: float
    channel_stats: dict[str, ChannelStats]
    high_load_row_count: int
    high_load_stats: dict[str, ChannelStats]
    evidence_warnings: tuple[str, ...]
    engine_health_verdict: None = None


_CHANNEL_ALIASES = {
    "time": ("time", "Time"),
    "rpm": ("RPM", "EngineSpeed"),
    "tps": ("TPS", "ThrottlePosition"),
    "ecu_load": ("ECU_Load", "ECULoad", "Load"),
    "ecu_timing_adv": ("ECU_TimingAdv", "TimingAdv", "Timing"),
    "inj_pulse_width_ms": ("InjPulseWidth_ms", "InjPulseWidth", "InjectorPulseWidth"),
    "narrowband_o2_v": ("NarrowbandO2_V", "O2Sensor", "O2"),
    "battery_v": ("Battery_V", "Battery", "BatteryVoltage"),
    "coolant_c": ("Coolant_C",),
    "iat_c": ("IAT_C", "IAT"),
    "vss_ecu_kph": ("VSS_ECU_kph", "Speed", "VSS"),
    "coolant_raw07": ("Coolant_raw07", "CoolantRaw07", "Raw07", "CoolantTemp"),
    "iat_raw3a": ("IAT_raw3A", "IATRaw3A", "Raw3A", "AirTemp"),
    "idc_pct_approx": ("IDC_pct_approx", "IDC"),
}
_CURRENT_PROFILE_REQUIRED_CHANNELS = (
    "rpm",
    "tps",
    "ecu_load",
    "ecu_timing_adv",
    "inj_pulse_width_ms",
    "narrowband_o2_v",
    "battery_v",
    "coolant_c",
    "iat_c",
    "vss_ecu_kph",
    "coolant_raw07",
    "iat_raw3a",
    "idc_pct_approx",
)
_REJECTED_LEGACY_FIELDS = ("Airflow", "MDP", "Knock", "KnockSum", "OCTNumber")


def _finite_number(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stats(values: list[float]) -> ChannelStats:
    return ChannelStats(len(values), min(values), max(values), sum(values) / len(values))


def _header_for(canonical_name: str, headers: tuple[str, ...]) -> str | None:
    aliases = _CHANNEL_ALIASES[canonical_name]
    return next((header for header in headers if header in aliases), None)


def _profile_kind(headers: tuple[str, ...]) -> str:
    if "ECU_Load" in headers and "InjPulseWidth_ms" in headers:
        return "current"
    if "ECULoad" in headers or "Airflow" in headers or "Knock" in headers:
        return "legacy"
    return "unknown"


def numbered_log_index(path: str | Path) -> int | None:
    """Return a card log's numeric sequence, or ``None`` for another filename."""
    match = _NUMBERED_LOG_RE.fullmatch(Path(path).name)
    return int(match.group(1)) if match else None


def find_latest_numbered_log(root: str | Path) -> Path:
    """Find the greatest numbered Tactrix CSV without relying on timestamps."""
    candidates = (
        (index, path)
        for path in Path(root).iterdir()
        if path.is_file()
        if (index := numbered_log_index(path)) is not None
    )
    try:
        return max(candidates, key=lambda candidate: candidate[0])[1]
    except ValueError as exc:
        raise TactrixLogError("No numbered Tactrix logNNNN.csv file found") from exc


def discover_tactrix_root(roots=None) -> Path:
    """Locate a Tactrix logger card by its config and numbered-log markers."""
    if roots is None:
        roots = (Path(f"{letter}:/") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ") if os.name == "nt" else ()
    matches = []
    for root in (Path(candidate) for candidate in roots):
        if not root.is_dir() or not (root / "logcfg.txt").is_file():
            continue
        if any(path.is_file() and numbered_log_index(path) is not None for path in root.iterdir()):
            matches.append(root)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        paths = ", ".join(str(path.resolve()) for path in matches)
        raise TactrixLogError(f"Multiple Tactrix cards found: {paths}")
    raise TactrixLogError("No Tactrix card with logcfg.txt and numbered logs found")


def archive_session_copy(source, destination_root, session_label, now=None) -> Path:
    """Copy one numbered card log into a dated, collision-safe PC archive."""
    source_path = Path(source)
    log_index = numbered_log_index(source_path)
    if log_index is None:
        raise TactrixLogError("Source must be a numbered Tactrix logNNNN.csv file")
    safe_label = re.sub(r"[^A-Za-z0-9]+", "-", str(session_label).strip()).strip("-")
    if not safe_label:
        raise TactrixLogError("Session label cannot be empty")

    captured_at = datetime.now() if now is None else now
    date_folder = Path(destination_root) / captured_at.strftime("%Y-%m-%d")
    stem = f"{captured_at:%Y-%m-%d_%H%M}_{safe_label}_log{log_index:04d}"
    destination = date_folder / f"{stem}.csv"
    collision_number = 2
    while destination.exists():
        destination = date_folder / f"{stem}_{collision_number}.csv"
        collision_number += 1
    date_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    return destination


def _format_stat_range(stats: ChannelStats | None, unit: str) -> str:
    """Return a compact, explicit unavailable-or-range presentation value."""
    if stats is None:
        return "n/a"
    return f"{stats.minimum:.1f} to {stats.maximum:.1f} {unit}"


def format_review_text(review: LogReview) -> str:
    """Render a concise, evidence-limited review suitable for a read-only UI."""
    if review.profile_kind == "legacy":
        heading = "LEGACY PROFILE"
    elif review.capture_status == "ready":
        heading = "CAPTURE READY"
    else:
        heading = "NEEDS CHECK"

    max_rpm = review.channel_stats.get("rpm")
    high = review.high_load_stats
    raw_temperature_evidence = (
        "coolant_raw07" in review.channel_stats or "iat_raw3a" in review.channel_stats
    )
    lines = [
        heading,
        f"File: {review.source_path.resolve()}",
        f"Profile: {review.profile_kind}",
        f"Rows: {review.row_count}",
        f"Duration: {review.duration_seconds:.3f} s",
        f"Rate: {review.sampling_hz:.1f} Hz",
        f"Max RPM: {max_rpm.maximum:.0f}" if max_rpm else "Max RPM: n/a",
        "",
        "High-load (RPM >= 3000, TPS >= 80%): "
        f"{review.high_load_row_count} rows",
        f"Timing: {_format_stat_range(high.get('ecu_timing_adv'), 'deg')}",
        f"Battery: {_format_stat_range(high.get('battery_v'), 'V')}",
        f"IDC: {_format_stat_range(high.get('idc_pct_approx'), '% approx')}",
        "",
        "ECU-scaled coolant (unvalidated): "
        f"{_format_stat_range(review.channel_stats.get('coolant_c'), 'C')}",
        "ECU-scaled IAT (unvalidated): "
        f"{_format_stat_range(review.channel_stats.get('iat_c'), 'C')}",
        "ECU VSS (unvalidated): "
        f"{_format_stat_range(review.channel_stats.get('vss_ecu_kph'), 'km/h')}",
        "",
        "Evidence limits:",
        "- ECU-scaled coolant/IAT and ECU VSS require car-side validation.",
        "- ECU VSS is not confirmed ground speed.",
        "- Narrowband O2 is switching evidence, not a wideband AFR measurement.",
        "- No knock channel is available in this capture; do not infer knock status.",
    ]
    if raw_temperature_evidence:
        lines.append("- Raw MUT 0x07/0x3A temperatures are counts, not Celsius.")
    if review.evidence_warnings:
        lines.append("")
        lines.append("Capture notes:")
        lines.extend(f"- {warning}" for warning in review.evidence_warnings)
    return "\n".join(lines)


def analyse_tactrix_log(path: str | Path) -> LogReview:
    """Review a card CSV without making an engine-health conclusion."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise TactrixLogError("CSV has no header row")
            headers = tuple(header.strip() for header in reader.fieldnames if header)
            rows = [
                {header.strip(): value for header, value in row.items() if header}
                for row in reader
            ]
    except (UnicodeError, csv.Error) as exc:
        raise TactrixLogError(f"Cannot parse Tactrix CSV: {source}") from exc
    except OSError as exc:
        raise TactrixLogError(f"Cannot read Tactrix log: {source}") from exc
    if not rows:
        raise TactrixLogError("CSV has a header but no data rows")

    values: dict[str, list[float]] = {}
    incomplete_channels: set[str] = set()
    for canonical_name in _CHANNEL_ALIASES:
        header = _header_for(canonical_name, headers)
        if header:
            parsed = [number for row in rows if (number := _finite_number(row.get(header))) is not None]
            if parsed:
                values[canonical_name] = parsed
            if len(parsed) != len(rows):
                incomplete_channels.add(canonical_name)

    time_values = values.get("time", [])
    if len(time_values) != len(rows):
        raise TactrixLogError("CSV has missing or non-numeric time values")
    duration = time_values[-1] - time_values[0]
    if duration <= 0:
        raise TactrixLogError("CSV time does not increase")
    if any(later <= earlier for earlier, later in zip(time_values, time_values[1:])):
        raise TactrixLogError("CSV time is not strictly increasing")

    profile_kind = _profile_kind(headers)
    warnings: list[str] = []
    quality_warning = False
    if profile_kind == "current":
        required_channels = _CURRENT_PROFILE_REQUIRED_CHANNELS
    elif profile_kind == "legacy":
        required_channels = ("rpm", "tps", "battery_v")
    else:
        required_channels = ("rpm", "tps", "battery_v", "coolant_c", "iat_c")
        warnings.append("Unknown profile")
        quality_warning = True
    for canonical_name in required_channels:
        header = _header_for(canonical_name, headers)
        channel_values = values.get(canonical_name, [])
        if header is None:
            warnings.append(f"Missing {canonical_name} channel")
            quality_warning = True
        elif not channel_values:
            warnings.append(f"Non-numeric {canonical_name} channel")
            quality_warning = True
        elif canonical_name in incomplete_channels:
            warnings.append(f"Incomplete/non-numeric samples for {canonical_name} channel")
            quality_warning = True
        elif all(value == 0 for value in channel_values):
            warnings.append(f"All-zero {canonical_name} channel")
            quality_warning = True
        elif len(set(channel_values)) == 1:
            warnings.append(f"Constant {canonical_name} channel")
            quality_warning = True

    for canonical_name, channel_values in values.items():
        if canonical_name == "time" or canonical_name in required_channels:
            continue
        if all(value == 0 for value in channel_values):
            warnings.append(f"All-zero {canonical_name} channel")
            quality_warning = True
        elif len(set(channel_values)) == 1:
            warnings.append(f"Constant {canonical_name} channel")
            quality_warning = True
        if canonical_name in incomplete_channels:
            warnings.append(f"Incomplete/non-numeric samples for {canonical_name} channel")
            quality_warning = True

    if profile_kind == "legacy":
        warnings.append("Legacy profile: historical channel scaling and availability require review")
        for field in _REJECTED_LEGACY_FIELDS:
            if field in headers:
                warnings.append(f"Legacy rejected field present: {field}")
        if "CoolantTemp" in headers or "AirTemp" in headers:
            warnings.append("Legacy temperature channels may be raw values, not Celsius")

    if "coolant_raw07" in values or "iat_raw3a" in values:
        warnings.append("Raw MUT 0x07/0x3A temperatures are counts, not Celsius")

    if "idc_pct_approx" not in values:
        rpm_values = values.get("rpm", [])
        pulse_values = values.get("inj_pulse_width_ms", [])
        if len(rpm_values) == len(rows) and len(pulse_values) == len(rows):
            values["idc_pct_approx"] = [
                rpm * pulse_width * 0.0008333333
                for rpm, pulse_width in zip(rpm_values, pulse_values)
            ]

    channel_stats = {name: _stats(channel_values) for name, channel_values in values.items()}
    rpm_header = _header_for("rpm", headers)
    tps_header = _header_for("tps", headers)
    high_rows = []
    if rpm_header and tps_header:
        for row in rows:
            rpm = _finite_number(row.get(rpm_header))
            tps = _finite_number(row.get(tps_header))
            if rpm is not None and tps is not None and rpm >= 3000 and tps >= 80:
                high_rows.append(row)
    high_load_stats: dict[str, ChannelStats] = {}
    for canonical_name in _CHANNEL_ALIASES:
        header = _header_for(canonical_name, headers)
        if header:
            high_values = [number for row in high_rows if (number := _finite_number(row.get(header))) is not None]
            if high_values:
                high_load_stats[canonical_name] = _stats(high_values)
    if "idc_pct_approx" in values and _header_for("idc_pct_approx", headers) is None:
        pulse_header = _header_for("inj_pulse_width_ms", headers)
        if rpm_header and pulse_header:
            derived_high_idc = []
            for row in high_rows:
                rpm = _finite_number(row.get(rpm_header))
                pulse_width = _finite_number(row.get(pulse_header))
                if rpm is not None and pulse_width is not None:
                    derived_high_idc.append(rpm * pulse_width * 0.0008333333)
            if derived_high_idc:
                high_load_stats["idc_pct_approx"] = _stats(derived_high_idc)

    return LogReview(
        source_path=source,
        profile_kind=profile_kind,
        capture_status="warning" if quality_warning else "ready",
        headers=headers,
        row_count=len(rows),
        duration_seconds=duration,
        sampling_hz=(len(rows) - 1) / duration,
        channel_stats=channel_stats,
        high_load_row_count=len(high_rows),
        high_load_stats=high_load_stats,
        evidence_warnings=tuple(warnings),
    )
