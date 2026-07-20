import math
import unittest

import j2534


class J2534CatalogueTests(unittest.TestCase):
    def test_primary_profile_is_valid(self):
        profile = j2534.get_profile()
        self.assertEqual(profile["id"], j2534.DEFAULT_PROFILE_ID)
        self.assertTrue(profile["short"])

    def test_core_scalings(self):
        self.assertEqual(j2534.ALL_PARAMS[0x21][1](24), 750.0)
        self.assertTrue(math.isclose(j2534.ALL_PARAMS[0x14][1](192), 14.0736))
        self.assertEqual(j2534.ALL_PARAMS[0x2F][1](50), 100.0)
        self.assertEqual(j2534.ALL_PARAMS[0x1B][1](0), 0.0)
        self.assertEqual(j2534.ALL_PARAMS[0x1B][1](1), 1.0)
        self.assertEqual(j2534.ALL_PARAMS[0x1B][1](255), 255.0)

    def test_temperature_curve_is_monotonic(self):
        convert = j2534.ALL_PARAMS[0x07][1]
        values = [convert(raw) for raw in range(256)]
        self.assertTrue(all(a >= b for a, b in zip(values, values[1:])))
        self.assertGreater(convert(0), 100)
        self.assertLess(convert(255), 0)

    def test_health_bands_cover_common_warm_idle(self):
        ctx = {0x21: 750.0, 0x2F: 0.0, 0x07: 90.0}
        self.assertEqual(j2534.sensor_health(0x21, 750.0, ctx), "good")
        self.assertEqual(j2534.sensor_health(0x07, 90.0, ctx), "good")
        self.assertEqual(j2534.sensor_health(0x14, 14.1, ctx), "good")
        self.assertEqual(j2534.sensor_health(0x29, 2.7, ctx), "good")

    def test_out_of_range_values_are_flagged(self):
        ctx = {0x21: 750.0, 0x2F: 0.0, 0x07: 90.0}
        self.assertEqual(j2534.sensor_health(0x14, 16.0, ctx), "bad")
        self.assertEqual(j2534.sensor_health(0x07, 115.0, ctx), "bad")
        self.assertEqual(j2534.sensor_health(0x29, 0.0, ctx), "bad")


if __name__ == "__main__":
    unittest.main()
