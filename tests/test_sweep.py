"""Tests for the MUT-II request sweep, using a mock ECU (no hardware)."""
import unittest

import sweep


class MockECU:
    """Answers a known set of ids; others return None (silent).

    'moving' ids return a different byte on each pass so the analyser can
    tell live channels from constants.
    """

    def __init__(self, served, moving=(), fail_ids=()):
        self.served = dict(served)
        self.moving = set(moving)
        self.fail_ids = set(fail_ids)
        self.asked = []
        self.pass_n = 0

    def mut2_request(self, rid, timeout=None):
        self.asked.append(rid)
        if rid in self.fail_ids:
            raise RuntimeError("adapter glitch")
        if rid not in self.served:
            return None
        base = self.served[rid]
        if rid in self.moving:
            return (base + self.pass_n * 7) & 0xFF
        return base


class SweepIdsTests(unittest.TestCase):
    def test_excludes_dtc_and_ecu_id_by_default(self):
        ids = sweep.sweep_ids()
        for banned in (0x38, 0x39, 0xFE, 0xFF):
            self.assertNotIn(banned, ids)

    def test_never_sweeps_beyond_requested_span(self):
        ids = sweep.sweep_ids(0x10, 0x20)
        self.assertEqual(min(ids), 0x10)
        self.assertEqual(max(ids), 0x20)

    def test_sweep_only_asks_allowed_ids(self):
        dev = MockECU({0x21: 64})
        ids = sweep.sweep_ids(0x00, 0x9F)
        sweep.sweep_once(dev, ids)
        self.assertNotIn(0x38, dev.asked)
        self.assertNotIn(0xFF, dev.asked)


class SweepBehaviourTests(unittest.TestCase):
    def test_silent_ids_report_none(self):
        dev = MockECU({0x21: 64})
        got = sweep.sweep_once(dev, [0x21, 0x22])
        self.assertEqual(got[0x21], 64)
        self.assertIsNone(got[0x22])

    def test_adapter_exception_is_contained(self):
        dev = MockECU({0x21: 64}, fail_ids={0x30})
        got = sweep.sweep_once(dev, [0x21, 0x30])
        self.assertEqual(got[0x21], 64)
        self.assertIsNone(got[0x30])


class AnalyseTests(unittest.TestCase):
    def _two_passes(self):
        dev = MockECU({0x21: 64, 0x07: 180, 0x15: 100}, moving={0x21})
        ids = [0x07, 0x15, 0x21, 0x22]
        passes = []
        for n in range(2):
            dev.pass_n = n
            passes.append(sweep.sweep_once(dev, ids))
        return passes

    def test_identifies_moving_channel(self):
        rows = {r["id"]: r for r in sweep.analyse(self._two_passes())}
        self.assertTrue(rows[0x21]["changed"])
        self.assertFalse(rows[0x07]["changed"])

    def test_identifies_silent_channel(self):
        rows = {r["id"]: r for r in sweep.analyse(self._two_passes())}
        self.assertFalse(rows[0x22]["answers"])
        self.assertTrue(rows[0x07]["answers"])

    def test_spread_reflects_movement(self):
        rows = {r["id"]: r for r in sweep.analyse(self._two_passes())}
        self.assertEqual(rows[0x21]["spread"], 7)
        self.assertEqual(rows[0x15]["spread"], 0)

    def test_report_mentions_moving_channel_first(self):
        rows = sweep.analyse(self._two_passes())
        text = sweep.format_report(rows, {0x21: "RPM"})
        self.assertIn("0x21", text)
        self.assertIn("RPM", text)
        self.assertIn("CHANGED", text)

    def test_analyse_handles_no_passes(self):
        self.assertEqual(sweep.analyse([]), [])


if __name__ == "__main__":
    unittest.main()
