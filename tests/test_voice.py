import struct
import unittest

import voice


class VoiceHelpersTests(unittest.TestCase):
    def test_clip_for_speech_keeps_short(self):
        short = "Coolant is fine."
        self.assertEqual(voice.clip_for_speech(short), short)

    def test_clip_for_speech_caps_long(self):
        long = (
            "NORMAL. Coolant is fine at idle. "
            "You should also check the radiator fan, the thermostat, "
            "the coolant level, the water pump, and every hose under the car "
            "before you drive to the shops and back again mate."
        )
        clipped = voice.clip_for_speech(long, max_chars=80)
        self.assertLessEqual(len(clipped), 81)
        self.assertTrue(clipped)

    def test_trim_silence_keeps_loud_middle(self):
        # quiet + loud tone-ish + quiet
        quiet = b"\x00\x00" * 2048
        loud = struct.pack("<" + "h" * 2048, *([12000] * 2048))
        raw = quiet + loud + quiet
        trimmed = voice.trim_silence(raw, sample_width=2, frame_samples=512, thresh=500)
        self.assertLess(len(trimmed), len(raw))
        self.assertGreater(len(trimmed), len(loud) // 2)


class VoiceEngineSmokeTests(unittest.TestCase):
    """Real SAPI/COM setup on the background thread — no audio is played
    unless speak()/start_listening() are actually called, so this just
    confirms the engine starts and shuts down cleanly on this machine.
    """

    def test_engine_starts_and_reports_available(self):
        engine = voice.VoiceEngine()
        try:
            self.assertTrue(engine.available, "voice engine failed to initialize")
        finally:
            engine.shutdown()

    def test_shutdown_is_idempotent_safe(self):
        engine = voice.VoiceEngine()
        engine.shutdown()
        engine.shutdown()  # must not raise


if __name__ == "__main__":
    unittest.main()
