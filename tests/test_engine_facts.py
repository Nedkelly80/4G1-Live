"""Regression tests for hard-won facts about the 4G15 12V that the dashboard
must not quietly un-learn.

Every assertion here cost a race weekend or a workshop-manual cross-check.
If one of these fails, someone has reverted a correction — do not "fix" the
test, go and find out why the behaviour changed.
"""

import unittest

import j2534


class NoKnockSensorTests(unittest.TestCase):
    """The 4G15 12V has NO knock sensor: zero detonation protection.

    Confirmed 19/8/26 against the AU 4G15 12V workshop manual. A knock count
    of 0 therefore means "nothing is listening", not "the engine is not
    detonating" — so it must never show a green/healthy light. Pistons 3 and 4
    melted on this engine; a false all-clear on that channel is the single most
    dangerous thing this dashboard could display.
    """

    def test_zero_knock_count_is_never_green(self):
        self.assertIsNone(j2534.sensor_health(0x26, 0))

    def test_low_knock_count_is_never_green(self):
        for value in (0, 1, 2, 5):
            with self.subTest(value=value):
                self.assertNotEqual(j2534.sensor_health(0x26, value), "good")

    def test_moving_knock_register_still_raises_a_flag(self):
        self.assertEqual(j2534.sensor_health(0x26, 10), "warn")
        self.assertEqual(j2534.sensor_health(0x26, 30), "bad")

    def test_profile_records_the_absence(self):
        profile = j2534.get_profile("4g15_12v")
        self.assertIs(profile["knock_sensor"], False)
        self.assertIn("knock", profile["absent_sensors"].lower())

    def test_note_warns_the_operator(self):
        note = j2534.get_profile("4g15_12v")["note_overrides"][0x26]
        self.assertIn("NO KNOCK SENSOR", note)


class NoMafTests(unittest.TestCase):
    """No MAF/AFM either — the engine is speed-density off MAP (0x1A) + rpm.

    An earlier note had 0x1A as "AFM only, ignore", which was exactly backwards
    and sent a diagnosis down the wrong path.
    """

    def test_profile_airflow_is_speed_density(self):
        airflow = j2534.get_profile("4g15_12v")["airflow"].lower()
        self.assertIn("speed-density", airflow)
        self.assertIn("no maf", airflow)

    def test_map_channel_is_documented_as_the_load_source(self):
        note = j2534.get_profile("4g15_12v")["note_overrides"][0x1A]
        self.assertIn("MAP SENSOR", note)

    def test_egr_recorded_as_absent(self):
        self.assertIs(j2534.get_profile("4g15_12v")["egr"], False)


class ColdRunningTests(unittest.TestCase):
    """Darwin logs showed coolant at 30-48 C for a whole race.

    That is a real cooling/thermostat condition holding the ECU in warm-up
    enrichment, not a failed sensor — the dashboard must not call it healthy,
    but the criteria text must tell the operator which of the two it is.
    """

    def test_race_temperatures_do_not_read_as_healthy(self):
        ctx = {0x21: 6000, 0x07: 40, 0x2F: 80}
        for coolant in (30, 40, 48):
            with self.subTest(coolant=coolant):
                self.assertNotEqual(
                    j2534.sensor_health(0x07, coolant, ctx), "good"
                )

    def test_proper_operating_temperature_is_healthy(self):
        ctx = {0x21: 6000, 0x07: 88, 0x2F: 80}
        self.assertEqual(j2534.sensor_health(0x07, 88, ctx), "good")

    def test_criteria_explains_the_known_cold_run(self):
        self.assertIn("run COLD", j2534.HEALTH_CRITERIA[0x07])


if __name__ == "__main__":
    unittest.main()
