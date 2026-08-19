import os
import tempfile
import unittest

import memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "memory.json")

    def test_default_has_simon(self):
        mem = memory.default_memory()
        self.assertEqual(mem["user_name"], "Simon")

    def test_load_missing_returns_defaults(self):
        mem = memory.load_memory(self.path)
        self.assertEqual(mem["user_name"], "Simon")
        self.assertEqual(mem["lessons"], [])

    def test_save_and_load_roundtrip(self):
        mem = memory.default_memory()
        mem["user_name"] = "Simon"
        memory.apply_consolidation(mem, {
            "lessons": ["PCV hose vacuum leak raised idle"],
            "vehicle_profile_id": "4g15_12v",
            "vehicle_label": "CE 4G15",
            "vehicle_notes": ["hot idle ~950 after long run"],
            "summary": "Night session — idle high after heatsoak, PCV suspect.",
        })
        memory.save_memory(mem, self.path)
        loaded = memory.load_memory(self.path)
        self.assertEqual(loaded["user_name"], "Simon")
        self.assertEqual(len(loaded["lessons"]), 1)
        self.assertIn("PCV", loaded["lessons"][0]["text"])
        self.assertIn("4g15_12v", loaded["vehicles"])
        self.assertIn("hot idle", loaded["vehicles"]["4g15_12v"]["notes"][0])
        self.assertEqual(len(loaded["session_summaries"]), 1)

    def test_format_for_prompt_includes_name_and_lessons(self):
        mem = memory.default_memory()
        memory.apply_consolidation(mem, {
            "user_name": "Simon",
            "lessons": ["Check thermostat if coolant climbs past 100"],
            "vehicle_profile_id": "4g15_12v",
            "vehicle_label": "Mirage 1.5",
            "vehicle_notes": ["MAP car"],
        })
        brief = memory.format_for_prompt(
            mem, vehicle_profile_id="4g15_12v", vehicle_label="Mirage 1.5",
        )
        self.assertIn("Simon", brief)
        self.assertIn("4G1 AI", brief)
        self.assertIn("thermostat", brief)
        self.assertIn("MAP car", brief)
        # real-world pit-lane guidance stays in the system brief
        self.assertIn("MUT-II", brief)
        self.assertIn("ground truth", brief.lower())
        self.assertIn("factory sensor memory", brief.lower())
        self.assertIn("700±50", brief)
        self.assertIn("2.1–2.7 kΩ", brief)
        self.assertIn("pin 1", brief.lower())

    def test_load_seeds_ce_4g15_factory_notes(self):
        mem = memory.load_memory(self.path)
        self.assertTrue(mem.get("factory_seeded"))
        notes = mem["vehicles"]["4g15_12v"]["notes"]
        self.assertTrue(any("MAP" in n for n in notes))
        self.assertTrue(any("thermostat" in n.lower() or "82" in n for n in notes))

    def test_record_alert_caps(self):
        mem = memory.default_memory()
        for i in range(30):
            memory.record_alert(mem, "Coolant", "warn", f"note {i}")
        self.assertEqual(len(mem["recent_alerts"]), memory.MAX_RECENT_ALERTS)
        self.assertIn("note 29", mem["recent_alerts"][-1]["text"])

    def test_consolidate_local_writes_summary(self):
        mem = memory.default_memory()
        turns = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hey Simon"},
        ]
        memory.consolidate_local(mem, turns, vehicle_profile_id="4g15_12v", vehicle_label="4G15")
        self.assertEqual(len(mem["session_summaries"]), 1)
        self.assertIn("hello", mem["session_summaries"][0]["text"])

    def test_lesson_dedupe(self):
        mem = memory.default_memory()
        memory.apply_consolidation(mem, {"lessons": ["same lesson"]})
        memory.apply_consolidation(mem, {"lessons": ["same lesson"]})
        self.assertEqual(len(mem["lessons"]), 1)

    def test_memory_stats_readable(self):
        mem = memory.default_memory()
        s = memory.memory_stats(mem)
        self.assertIn("Simon", s)
        self.assertIn("lessons", s)


if __name__ == "__main__":
    unittest.main()
