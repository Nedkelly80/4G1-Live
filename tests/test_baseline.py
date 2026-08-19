import csv
import os
import tempfile
import unittest

import baseline
import j2534


def _write_csv(path, rows):
    fieldnames = sorted({k for row in rows for k in row})
    if "t" in fieldnames:
        fieldnames.remove("t")
        fieldnames = ["t"] + fieldnames
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.session_dir = self.tmpdir.name

    def _seed_idle_session(self, coolant_values):
        rows = [
            {"t": i, "RPM": 800.0, "Speed": 0.0, "Coolant": c}
            for i, c in enumerate(coolant_values)
        ]
        _write_csv(os.path.join(self.session_dir, "4g1_live_20260101_000000.csv"), rows)

    def test_build_baseline_computes_mean_and_stdev(self):
        self._seed_idle_session([80.0, 82.0, 78.0, 81.0, 79.0, 80.0])
        result = baseline.build_baseline(self.session_dir)
        self.assertIn("Coolant", result)
        self.assertIn("idle", result["Coolant"])
        stats = result["Coolant"]["idle"]
        self.assertAlmostEqual(stats["mean"], 80.0, places=1)
        self.assertEqual(stats["n"], 6)
        self.assertGreater(stats["stdev"], 0)

    def test_bucket_omitted_below_min_samples(self):
        self._seed_idle_session([80.0, 82.0])  # below baseline._MIN_SAMPLES
        result = baseline.build_baseline(self.session_dir)
        self.assertNotIn("Coolant", result)

    def test_ignores_non_live_and_sweep_csvs(self):
        rows = [{"t": 0, "RPM": 800.0, "Speed": 0.0, "Coolant": 999.0}] * 10
        _write_csv(os.path.join(self.session_dir, "4g1_import_20260101_000000.csv"), rows)
        _write_csv(os.path.join(self.session_dir, "4g1_sweep_20260101_000000.csv"), rows)
        result = baseline.build_baseline(self.session_dir)
        self.assertEqual(result, {})

    def test_save_and_load_roundtrip(self):
        self._seed_idle_session([80.0, 82.0, 78.0, 81.0, 79.0, 80.0])
        built = baseline.build_baseline(self.session_dir)
        path = os.path.join(self.session_dir, "baseline.json")
        baseline.save_baseline(built, path)
        loaded = baseline.load_baseline(path)
        self.assertEqual(loaded, built)

    def test_load_missing_file_returns_empty_dict(self):
        missing = os.path.join(self.session_dir, "nope.json")
        self.assertEqual(baseline.load_baseline(missing), {})

    def test_deviation_flags_unusual_reading(self):
        self._seed_idle_session([80.0, 82.0, 78.0, 81.0, 79.0, 80.0])
        built = baseline.build_baseline(self.session_dir)
        state = j2534.engine_state({0x21: 800.0, 0x2F: 0.0})
        z_normal = baseline.deviation(built, "Coolant", 80.5, state)
        z_hot = baseline.deviation(built, "Coolant", 110.0, state)
        self.assertIsNotNone(z_normal)
        self.assertLess(abs(z_normal), 1.0)
        self.assertGreater(z_hot, 3.0)

    def test_deviation_none_without_history(self):
        state = j2534.engine_state({0x21: 800.0, 0x2F: 0.0})
        self.assertIsNone(baseline.deviation({}, "Coolant", 90.0, state))


if __name__ == "__main__":
    unittest.main()
