import json
import os
import tempfile
import unittest

import j2534
import runtime_validation


DEFAULTS = dict(
    theme="4G1 Red",
    style="Dial",
    gauges=[hex(g) for g in j2534.DEFAULT_GAUGES],
    poll_hz=12,
    vehicle_profile=j2534.DEFAULT_PROFILE_ID,
    dll_path=j2534.default_dll(),
    mut_baud=15625,
    pin1_ground=True,
    init_addr="0x00",
    init_attempts=5,
    request_timeout_ms=400,
    auto_connect=False,
    auto_live=False,
)
THEMES = {"4G1 Red": {}, "Windows XP": {}}
GAUGE_STYLES = ["Digital", "Bar", "Dial", "Compact"]


class AppSettingsTests(unittest.TestCase):
    def test_sanitize_settings_clamps_and_resets_invalid_values(self):
        settings, issues = runtime_validation.sanitize_settings({
            "theme": "BadTheme",
            "style": "BadStyle",
            "poll_hz": "999",
            "vehicle_profile": "bad_profile",
            "gauges": ["0x21", "bad", "0xFFFF"],
            "mut_baud": "-5",
            "request_timeout_ms": "999999",
            "init_addr": "bad",
            "init_attempts": "999",
        }, DEFAULTS, THEMES, GAUGE_STYLES)
        self.assertEqual(settings["theme"], DEFAULTS["theme"])
        self.assertEqual(settings["style"], DEFAULTS["style"])
        self.assertEqual(settings["poll_hz"], 20)
        self.assertEqual(settings["vehicle_profile"], j2534.DEFAULT_PROFILE_ID)
        self.assertEqual(settings["gauges"], ["0x21"])
        self.assertEqual(settings["mut_baud"], 1200)
        self.assertEqual(settings["request_timeout_ms"], 5000)
        self.assertEqual(settings["init_addr"], "0x00")
        self.assertEqual(settings["init_attempts"], 10)
        self.assertTrue(issues)

    def test_load_settings_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, "settings.json")
            backup_path = settings_path + ".bak"
            legacy_path = os.path.join(tmp, "legacy-settings.json")

            with open(settings_path, "w", encoding="utf-8") as f:
                f.write("{bad json")
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump({"poll_hz": "999", "vehicle_profile": "bad_profile"}, f)

            settings, meta = runtime_validation.load_settings(
                paths={
                    "settings": settings_path,
                    "backup": backup_path,
                    "legacy": legacy_path,
                },
                defaults=DEFAULTS,
                themes=THEMES,
                gauge_styles=GAUGE_STYLES,
            )
            self.assertTrue(meta["recovered_from_backup"])
            self.assertEqual(settings["poll_hz"], 20)
            self.assertEqual(settings["vehicle_profile"], j2534.DEFAULT_PROFILE_ID)

    def test_save_settings_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = os.path.join(tmp, "settings.json")
            backup_path = settings_path + ".bak"
            paths = {
                "settings": settings_path,
                "backup": backup_path,
                "user_data_dir": tmp,
            }

            first = dict(DEFAULTS)
            first["poll_hz"] = 12
            runtime_validation.save_settings(first, paths, DEFAULTS, THEMES, GAUGE_STYLES)

            second = dict(DEFAULTS)
            second["poll_hz"] = 8
            runtime_validation.save_settings(second, paths, DEFAULTS, THEMES, GAUGE_STYLES)

            with open(backup_path, encoding="utf-8") as f:
                backup = json.load(f)
            self.assertEqual(backup["poll_hz"], 12)

    def test_startup_health_reports_missing_dll(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = dict(DEFAULTS)
            settings["dll_path"] = os.path.join(tmp, "missing.dll")
            report = runtime_validation.run_startup_health_check(
                settings=settings,
                user_data_dir=tmp,
                log_dir=os.path.join(tmp, "Logs"),
            )
            checks = {c["key"]: c for c in report["checks"]}
            self.assertFalse(checks["dll_detected"]["ok"])
            self.assertTrue(checks["settings_writable"]["ok"])
            self.assertTrue(checks["logs_writable"]["ok"])


if __name__ == "__main__":
    unittest.main()
