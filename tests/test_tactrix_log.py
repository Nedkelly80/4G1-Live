import os
import tempfile
import unittest
from pathlib import Path

import tactrix_log


class TactrixLogTests(unittest.TestCase):
    def test_maps_4g1_session_csv(self):
        text = (
            "t,RPM,Timing,Coolant,Inj Pulse,Speed,O2 Sensor,Battery,Throttle,Engine Load\n"
            "10.0,900,8,82,2.4,0,0.15,14.2,4,22\n"
            "10.1,3200,16,88,6.1,20,0.72,14.4,85,70\n"
            "10.2,6500,18,91,7.8,40,0.81,14.5,95,90\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "4g1_live_test.csv"
            path.write_text(text, encoding="utf-8")
            analysis = tactrix_log.analyse_log(path, profile_id="4g15_12v")
        self.assertEqual(analysis.kind, "4g1-session")
        self.assertEqual(analysis.row_count, 3)
        self.assertAlmostEqual(analysis.duration_seconds, 0.2, places=3)
        self.assertEqual(analysis.high_load_rows, 2)
        self.assertIn(0x21, analysis.pid_stats)
        self.assertEqual(analysis.pid_stats[0x21].maximum, 6500)
        self.assertGreaterEqual(analysis.o2_switches, 1)
        report = tactrix_log.format_analysis(analysis)
        self.assertIn("RPM", report)
        self.assertIn("6500", report)
        self.assertIn(analysis.verdict.call, ("NORMAL", "WATCH", "FIX NOW"))

    def test_maps_mlv_csv(self):
        text = (
            "Time,RPM,TPS,Load,TimingAdv,PW,O2,CLT,IAT,Battery,IDC\n"
            "0.000,1000,17,40,15,2.8,0.14,62,30,14.5,2.3\n"
            "0.075,3500,90,80,18,6.5,0.80,80,34,14.4,19.0\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Darwin6_H1_MLV.csv"
            path.write_text(text, encoding="utf-8")
            analysis = tactrix_log.analyse_log(path)
        self.assertEqual(analysis.kind, "mlv")
        self.assertEqual(analysis.high_load_rows, 1)
        self.assertIn(0x17, analysis.pid_stats)
        self.assertIn(0x07, analysis.pid_stats)

    def test_maps_tactrix_card_csv(self):
        text = (
            "time,RPM,TPS,ECU_Load,ECU_TimingAdv,InjPulseWidth_ms,"
            "NarrowbandO2_V,Battery_V,Coolant_C,IAT_C,VSS_ECU_kph\n"
            "0.000,900,5,20,8,2.0,0.10,12.7,75,30,0\n"
            "0.075,3000,80,72,15,6.0,0.82,14.2,88,34,20\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log0003.csv"
            path.write_text(text, encoding="utf-8")
            analysis = tactrix_log.analyse_log(path)
        self.assertEqual(analysis.kind, "tactrix-card")
        self.assertEqual(analysis.high_load_rows, 1)
        self.assertAlmostEqual(analysis.sampling_hz, 13.333, places=2)

    def test_find_latest_numbered_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Heat1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (root / "log0012.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (root / "log70003.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            latest = tactrix_log.find_latest_numbered_log(root)
            self.assertEqual(latest.name, "log70003.csv")

    def test_discover_latest_prefers_numbered_on_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp) / "card"
            card.mkdir()
            (card / "logcfg.txt").write_text("type=mut2\n", encoding="utf-8")
            (card / "log0002.csv").write_text(
                "time,RPM,TPS\n0,900,5\n0.1,1000,6\n", encoding="utf-8"
            )
            other = Path(tmp) / "loose"
            other.mkdir()
            (other / "old.csv").write_text("time,RPM\n0,1\n1,2\n", encoding="utf-8")
            found = tactrix_log.discover_latest_log([other, card])
            self.assertEqual(found.name, "log0002.csv")

    def test_local_verdict_flags_overheat(self):
        text = (
            "t,RPM,Timing,Coolant,Inj Pulse,Speed,O2 Sensor,Battery,Throttle,Engine Load\n"
            "0.0,900,8,102,2.4,0,0.15,14.2,4,22\n"
            "0.1,900,8,108,2.4,0,0.72,14.1,4,22\n"
            "0.2,900,8,114,2.4,0,0.20,14.0,4,22\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hot.csv"
            path.write_text(text, encoding="utf-8")
            analysis = tactrix_log.analyse_log(path, profile_id="4g15_12v")
        self.assertEqual(analysis.verdict.call, "FIX NOW")
        self.assertTrue(any("Coolant" in f for f in analysis.verdict.findings))
        report = tactrix_log.format_analysis(analysis)
        self.assertIn("FIX NOW", report)

    def test_local_verdict_flags_high_idle(self):
        rows = ["t,RPM,Timing,Coolant,Inj Pulse,Speed,O2 Sensor,Battery,Throttle,Engine Load"]
        for i in range(25):
            rows.append(f"{i * 0.1},1050,8,88,2.4,0,0.2,14.2,4,22")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "idle.csv"
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            analysis = tactrix_log.analyse_log(path, profile_id="4g15_12v")
        self.assertIn(analysis.verdict.call, ("WATCH", "FIX NOW"))
        self.assertTrue(any("Idle" in f for f in analysis.verdict.findings))

    def test_empty_csv_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            path.write_text("RPM,TPS\n", encoding="utf-8")
            with self.assertRaises(tactrix_log.TactrixLogError):
                tactrix_log.analyse_log(path)


if __name__ == "__main__":
    unittest.main()
