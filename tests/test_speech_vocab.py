import unittest

import speech_vocab


class SpeechVocabTests(unittest.TestCase):
    def test_known_phonetic_confusion_corrected(self):
        self.assertEqual(
            speech_vocab.correct("is the cooler temperature normal right now"),
            "is the coolant temperature normal right now",
        )

    def test_short_common_words_are_never_touched(self):
        # Regression: "is" scored 0.8 against the acronym "isc" (ISC Steps)
        # under the original difflib-only approach — a real false positive
        # that corrupted ordinary sentence scaffolding.
        for phrase in [
            "is the rpm okay",
            "is it running fine at idle",
            "is the battery voltage ok",
        ]:
            self.assertIn("is ", speech_vocab.correct(phrase))
            self.assertNotIn("isc", speech_vocab.correct(phrase))

    def test_fuzzy_match_catches_near_miss_typos(self):
        self.assertEqual(speech_vocab.correct("check the injecter pulse"),
                         "check the injector pulse")
        self.assertEqual(speech_vocab.correct("is the batter voltage ok"),
                         "is the battery voltage ok")
        self.assertEqual(speech_vocab.correct("why is it nocking"),
                         "why is it knocking")

    def test_short_acronyms_excluded_from_fuzzy_targets(self):
        # "isc", "map", "egr" etc. must never be fuzzy-match targets —
        # too short and acronym-like to reliably match spoken words.
        for short in ("isc", "map", "egr", "wg"):
            self.assertNotIn(short, speech_vocab.VOCABULARY)

    def test_empty_and_none_safe(self):
        self.assertEqual(speech_vocab.correct(""), "")
        self.assertIsNone(speech_vocab.correct(None))

    def test_already_correct_text_unchanged(self):
        text = "is the coolant temperature normal right now"
        self.assertEqual(speech_vocab.correct(text), text)


if __name__ == "__main__":
    unittest.main()
