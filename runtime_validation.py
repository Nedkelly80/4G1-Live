import ctypes
import json
import logging
import os

import j2534


def _parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        txt = value.strip().lower()
        if txt in ("1", "true", "yes", "on"):
            return True
        if txt in ("0", "false", "no", "off"):
            return False
    return bool(value)


def _clamp_int(value, default, minimum, maximum):
    try:
        ivalue = int(value)
    except Exception:
        ivalue = int(default)
    return max(minimum, min(maximum, ivalue))


def _normalise_init_addr(value):
    try:
        ivalue = int(str(value).strip(), 0)
    except Exception:
        ivalue = 0
    ivalue = max(0, min(0xFF, ivalue))
    return f"0x{ivalue:02X}"


def sanitize_settings(raw, defaults, themes, gauge_styles):
    base = dict(defaults)
    issues = []
    if not isinstance(raw, dict):
        issues.append("Settings were not a JSON object; defaults restored.")
        raw = {}
    merged = dict(base)
    merged.update(raw)

    theme = merged.get("theme")
    if theme not in themes:
        issues.append("Theme was invalid and has been reset.")
        theme = base["theme"]

    style = merged.get("style")
    if style not in gauge_styles:
        issues.append("Gauge style was invalid and has been reset.")
        style = base["style"]

    profile = merged.get("vehicle_profile")
    if profile not in j2534.VEHICLE_PROFILES:
        issues.append("Vehicle profile was invalid and has been reset.")
        profile = j2534.DEFAULT_PROFILE_ID

    dll_path = str(merged.get("dll_path") or "").strip() or j2534.default_dll()
    if not dll_path:
        dll_path = base["dll_path"]
        issues.append("J2534 DLL path was empty and has been reset.")

    gauges = []
    raw_gauges = merged.get("gauges")
    if not isinstance(raw_gauges, (list, tuple)):
        raw_gauges = base["gauges"]
        issues.append("Gauge list was invalid and has been reset.")
    seen = set()
    for item in raw_gauges:
        try:
            pid = int(item, 16) if isinstance(item, str) else int(item)
        except Exception:
            continue
        if pid in j2534.ALL_PARAMS and pid not in seen:
            gauges.append(hex(pid))
            seen.add(pid)
    if not gauges:
        gauges = list(base["gauges"])
        issues.append("Gauge list was empty/invalid and has been reset.")

    return dict(
        theme=theme,
        style=style,
        gauges=gauges,
        poll_hz=_clamp_int(merged.get("poll_hz"), base["poll_hz"], 1, 20),
        vehicle_profile=profile,
        dll_path=dll_path,
        mut_baud=_clamp_int(merged.get("mut_baud"), base["mut_baud"], 1200, 50000),
        pin1_ground=_parse_bool(merged.get("pin1_ground"), True),
        init_addr=_normalise_init_addr(merged.get("init_addr")),
        init_attempts=_clamp_int(merged.get("init_attempts"), base["init_attempts"], 1, 10),
        request_timeout_ms=_clamp_int(
            merged.get("request_timeout_ms"),
            base["request_timeout_ms"],
            100,
            5000,
        ),
        auto_connect=_parse_bool(merged.get("auto_connect"), False),
        auto_live=_parse_bool(merged.get("auto_live"), False),
    ), issues


def load_settings(paths, defaults, themes, gauge_styles):
    settings_path = paths["settings"]
    backup_path = paths["backup"]
    legacy_path = paths["legacy"]
    for path in (settings_path, backup_path, legacy_path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("Settings file must contain a JSON object")
            s, issues = sanitize_settings(raw, defaults, themes, gauge_styles)
            if issues:
                logging.warning("Settings sanitised from %s: %s", path, "; ".join(issues))
            return s, dict(
                source=path,
                first_run=False,
                issues=issues,
                recovered_from_backup=(path == backup_path),
            )
        except FileNotFoundError:
            continue
        except Exception:
            logging.exception("Could not read settings from %s", path)
    first_run = not any(os.path.exists(p) for p in (settings_path, backup_path, legacy_path))
    s, issues = sanitize_settings(dict(defaults), defaults, themes, gauge_styles)
    return s, dict(
        source="defaults",
        first_run=first_run,
        issues=issues,
        recovered_from_backup=False,
    )


def save_settings(s, paths, defaults, themes, gauge_styles):
    settings_path = paths["settings"]
    backup_path = paths["backup"]
    user_data_dir = paths["user_data_dir"]
    try:
        os.makedirs(user_data_dir, exist_ok=True)
        clean, issues = sanitize_settings(s, defaults, themes, gauge_styles)
        if issues:
            logging.warning("Saving sanitised settings: %s", "; ".join(issues))
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "rb") as src, open(backup_path, "wb") as dst:
                    dst.write(src.read())
            except Exception:
                logging.exception("Could not write settings backup")
        temp_path = settings_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
        os.replace(temp_path, settings_path)
        return clean, issues
    except Exception:
        logging.exception("Could not save settings")
        return dict(defaults), ["Could not save settings"]


def run_startup_health_check(settings, user_data_dir, log_dir):
    checks = []
    dll_path = (settings or {}).get("dll_path") or j2534.default_dll()
    dll_found = os.path.isfile(dll_path)
    checks.append(dict(
        key="dll_detected",
        label="J2534 interface DLL detected",
        ok=bool(dll_found),
        detail=dll_path,
    ))
    dll_load_ok = False
    dll_detail = "Not attempted."
    if dll_found:
        try:
            ctypes.WinDLL(dll_path)
            dll_load_ok = True
            dll_detail = "Load test succeeded."
        except Exception as exc:
            dll_detail = f"Load test failed: {exc}"
    checks.append(dict(
        key="dll_loadable",
        label="J2534 interface DLL can be loaded",
        ok=bool(dll_load_ok),
        detail=dll_detail,
    ))

    settings_ok = False
    settings_detail = user_data_dir
    try:
        os.makedirs(user_data_dir, exist_ok=True)
        probe = os.path.join(user_data_dir, ".write-test.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        settings_ok = True
    except Exception as exc:
        settings_detail = f"{user_data_dir} ({exc})"
    checks.append(dict(
        key="settings_writable",
        label="Settings folder is writable",
        ok=bool(settings_ok),
        detail=settings_detail,
    ))

    logs_ok = False
    logs_detail = log_dir
    try:
        os.makedirs(log_dir, exist_ok=True)
        probe = os.path.join(log_dir, ".write-test.tmp")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        logs_ok = True
    except Exception as exc:
        logs_detail = f"{log_dir} ({exc})"
    checks.append(dict(
        key="logs_writable",
        label="Diagnostic log folder is writable",
        ok=bool(logs_ok),
        detail=logs_detail,
    ))

    profile = (settings or {}).get("vehicle_profile")
    profile_ok = profile in j2534.VEHICLE_PROFILES
    checks.append(dict(
        key="profile_valid",
        label="Default vehicle profile is valid",
        ok=bool(profile_ok),
        detail=profile or j2534.DEFAULT_PROFILE_ID,
    ))

    blocking = {"dll_detected", "dll_loadable", "settings_writable", "logs_writable", "profile_valid"}
    ok = all(c["ok"] for c in checks if c["key"] in blocking)
    return dict(ok=ok, checks=checks)
