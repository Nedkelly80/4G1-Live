"""Domain-vocabulary correction for voice-recognized questions.

Windows' generic dictation grammar isn't tuned for automotive vocabulary.
Two distinct failure modes need two distinct fixes:

1. Phonetic mishearings (the word it picked is a common English word that
   sounds similar but isn't spelling-similar — e.g. "coolant" heard as
   "cooler"). Spelling-distance can't catch these; they need an explicit
   known-confusions dictionary, extended as real usage turns up more.
2. Simple near-miss typos on jargon SAPI doesn't have strong priors for
   (e.g. "injector" heard as "injecter"). Fuzzy spelling match catches
   these fine — but ONLY when guarded carefully: short words (like "is")
   can accidentally be close, by edit distance, to short vocabulary
   acronyms (like "isc"), so correction is restricted to longer words
   matched against longer vocabulary terms only.
"""
import difflib
import re

import j2534

# Confirmed/likely phonetic mishearings: generic dictation favours the more
# common everyday word over the automotive term it sounds like. Add entries
# here as real usage turns up more — this is the primary correction path.
_KNOWN_CONFUSIONS = {
    "cooler": "coolant",
    "cooling": "coolant",
}

_EXTRA_TERMS = [
    "coolant", "cooling", "radiator", "overheating", "overheat",
    "misfire", "misfiring", "stalling", "vacuum",
    "knocking", "detonation", "backfire", "idling",
    "revving", "redline", "sensor", "voltage", "alternator",
    "battery", "ignition", "timing", "advance",
    "retard", "octane", "injector", "throttle",
    "manifold", "pressure", "intake", "exhaust", "temperature",
    "barometric", "warming",
]

_MIN_WORD_LEN = 6     # words shorter than this are never fuzzy-corrected
_MIN_VOCAB_LEN = 4    # vocabulary entries shorter than this are never a
                       # fuzzy-match target (acronyms like "isc", "egr", "map")
_FUZZY_CUTOFF = 0.82


def _build_vocabulary():
    words = set()
    for meta in j2534.ALL_PARAMS.values():
        name = meta[0]
        for w in re.findall(r"[a-zA-Z]+", name):
            if len(w) >= _MIN_VOCAB_LEN:
                words.add(w.lower())
    for w in _EXTRA_TERMS:
        if len(w) >= _MIN_VOCAB_LEN:
            words.add(w.lower())
    return words


VOCABULARY = _build_vocabulary()


def correct(text, cutoff=_FUZZY_CUTOFF):
    """Return `text` with misheard words corrected: known phonetic
    confusions first (exact match, any length), then a conservative fuzzy
    spelling match for longer words only. Ordinary short English words
    (the "is the ... right now" scaffolding around the technical terms)
    are never touched.
    """
    if not text:
        return text
    out_words = []
    for token in text.split(" "):
        core = re.sub(r"[^a-zA-Z']", "", token)
        low = core.lower()
        if not core:
            out_words.append(token)
            continue
        if low in _KNOWN_CONFUSIONS:
            out_words.append(token.replace(core, _KNOWN_CONFUSIONS[low]))
            continue
        if low in VOCABULARY or len(core) < _MIN_WORD_LEN:
            out_words.append(token)
            continue
        matches = difflib.get_close_matches(low, VOCABULARY, n=1, cutoff=cutoff)
        if matches:
            out_words.append(token.replace(core, matches[0]))
        else:
            out_words.append(token)
    return " ".join(out_words)
