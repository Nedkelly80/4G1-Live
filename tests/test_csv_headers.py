import csv
import os
import tempfile
import unittest
from unittest.mock import patch

import app


class _AutosaveHarness:
    gauges = [0x21, 0x07]
    heroes = [0x21]

    def __init__(self):
        self.messages = []

    def log_line(self, text):
        self.messages.append(text)

    _autosave_columns = app.App._autosave_columns
    _autosave_start = app.App._autosave_start
    _autosave_row = app.App._autosave_row
    _autosave_stop = app.App._autosave_stop


class CsvHeaderTests(unittest.TestCase):
    def test_autosave_writes_header_before_first_sample(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "SESSION_DIR", folder):
            recorder = _AutosaveHarness()
            recorder._autosave_start("test")
            path = recorder._autosave_path
            self.assertTrue(path and os.path.isfile(path))

            # A newly opened session must be readable and self-describing even
            # before any ECU sample has arrived.
            with open(path, newline="", encoding="utf-8") as fh:
                self.assertEqual(next(csv.reader(fh)), ["t", "mode", "RPM", "Coolant"])

            recorder._autosave_row({"t": 1.25, "mode": "LIVE", "RPM": 850.0, "Coolant": 90.0})
            recorder._autosave_stop()
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["RPM"], "850.0")
            self.assertEqual(rows[0]["Coolant"], "90.0")


if __name__ == "__main__":
    unittest.main()
