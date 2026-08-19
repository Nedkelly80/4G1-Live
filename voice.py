"""Voice I/O for 4G1 Live AI.

Text-to-speech for reading alerts aloud (pyttsx3/SAPI, offline),
and push-to-talk speech-to-text (Google Web Speech via speech_recognition).

Latency design (trackside — every second counts after you release PTT):
  - Mic device is created once and reused (open is expensive).
  - Recording stops ASAP when stop_listening() is called.
  - Speech-to-text runs on its OWN thread so TTS/jobs aren't blocked.
  - Leading/trailing silence is trimmed before upload (smaller = faster STT).
  - Max hold time caps runaway recordings.
  - Stage timings are logged: record_ms / stt_ms / speak_ms.

IMPORTANT: microphone / COM init is LAZY and never blocks App startup.
Native PyAudio crashes on some machines if mic is opened during App.__init__.
"""
from __future__ import annotations

import audioop
import logging
import queue
import struct
import threading
import time

log = logging.getLogger(__name__)

# Hard cap so a stuck key or long monologue can't send a 30s blob to Google.
MAX_RECORD_S = 10.0
# Drop quiet lead-in/tail so STT upload is smaller and faster.
_SILENCE_THRESH = 450  # RMS over int16 samples; mic-dependent, intentionally low
_TRIM_FRAME = 1024     # samples per RMS window at source rate
# Spoken answers: short = heard sooner (full text still shows in the UI).
MAX_SPEAK_CHARS = 280
DEFAULT_SPEAK_RATE = 190  # slightly brisker than stock ~150–175


def clip_for_speech(text, max_chars=MAX_SPEAK_CHARS):
    """Keep TTS snappy — first 1–2 sentences, hard-capped."""
    text = (text or "").strip()
    if not text:
        return ""
    # Prefer sentence boundaries
    cut = text
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        parts = []
        rest = text
        while rest and len(parts) < 2:
            if sep in rest:
                i = rest.find(sep)
                parts.append(rest[: i + 1].strip())
                rest = rest[i + len(sep) :].lstrip()
            else:
                break
        if parts:
            cut = " ".join(parts)
            break
    if len(cut) > max_chars:
        cut = cut[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return cut


def trim_silence(raw: bytes, sample_width: int = 2, frame_samples: int = _TRIM_FRAME,
                 thresh: int = _SILENCE_THRESH) -> bytes:
    """Strip quiet head/tail from PCM so STT has less audio to upload."""
    if not raw or sample_width <= 0:
        return raw
    frame = frame_samples * sample_width
    if frame <= 0 or len(raw) < frame * 3:
        return raw
    n = len(raw) // frame
    # Find first/last loud frames
    first = 0
    last = n - 1
    for i in range(n):
        chunk = raw[i * frame : (i + 1) * frame]
        if audioop.rms(chunk, sample_width) >= thresh:
            first = max(0, i - 1)  # keep a little context
            break
    for i in range(n - 1, -1, -1):
        chunk = raw[i * frame : (i + 1) * frame]
        if audioop.rms(chunk, sample_width) >= thresh:
            last = min(n - 1, i + 1)
            break
    if last < first:
        return raw
    return raw[first * frame : (last + 1) * frame]


class VoiceEngine:
    def __init__(self, rate=DEFAULT_SPEAK_RATE):
        self._rate = rate
        self._jobs = queue.Queue()
        self._result_callback = None
        self._error_callback = None
        self._status_callback = None  # optional: stage strings for the UI
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._init_error = None
        self._recording = False
        self._thread = threading.Thread(target=self._run, name="VoiceEngine", daemon=True)
        self._thread.start()
        # Do NOT wait long — UI must open even if speech stack is broken.
        self._ready.wait(timeout=1.5)

    @property
    def available(self):
        """False if TTS/microphone failed or is still initializing."""
        return self._ready.is_set() and self._init_error is None

    def set_status_callback(self, cb):
        """Optional cb(str) for stage updates: 'Listening…', 'Transcribing…', etc."""
        self._status_callback = cb

    def _status(self, text):
        cb = self._status_callback
        if cb:
            try:
                cb(text)
            except Exception:
                log.exception("Voice status callback failed")

    def speak(self, text, clip=True):
        if not text or not self.available:
            return
        payload = clip_for_speech(text) if clip else text.strip()
        if payload:
            self._jobs.put(("speak", payload))

    def start_listening(self, on_result, on_error=None):
        if not self.available:
            if on_error:
                on_error(self._init_error or RuntimeError("Voice unavailable"))
            return
        if self._recording:
            return
        self._result_callback = on_result
        self._error_callback = on_error
        self._stop_event.clear()
        self._jobs.put(("listen_start", None))

    def stop_listening(self):
        self._stop_event.set()

    def shutdown(self):
        self._stop_event.set()
        try:
            self._jobs.put(("quit", None))
        except Exception:
            pass

    def _run(self):
        tts = None
        recognizer = None
        mic = None
        try:
            import pythoncom
            import pyttsx3
            import speech_recognition as sr

            pythoncom.CoInitialize()
            # TTS only at startup — microphone stream opened on demand, device reused
            tts = pyttsx3.init()
            tts.setProperty("rate", self._rate)
            recognizer = sr.Recognizer()
            # Faster fail if Google is wedged; default can hang a long time.
            recognizer.operation_timeout = 8
            self._sr = sr
            try:
                mic = sr.Microphone()
            except Exception:
                log.exception("Microphone device open failed at init — will retry on listen")
                mic = None
        except Exception as e:
            log.exception("Voice engine init failed")
            self._init_error = e
            self._ready.set()
            return

        self._ready.set()
        running = True
        while running:
            try:
                kind, payload = self._jobs.get(timeout=0.1)
            except queue.Empty:
                continue

            if kind == "speak":
                try:
                    t0 = time.monotonic()
                    tts.setProperty("rate", self._rate)
                    tts.say(payload)
                    tts.runAndWait()
                    log.info("TTS speak_ms=%.0f chars=%d", (time.monotonic() - t0) * 1000, len(payload))
                except Exception:
                    log.exception("TTS speak failed")
            elif kind == "listen_start":
                self._record_and_recognize(recognizer, mic)
            elif kind == "quit":
                running = False

        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def _record_and_recognize(self, recognizer, mic):
        sr = getattr(self, "_sr", None)
        if sr is None:
            if self._error_callback:
                self._error_callback(RuntimeError("SpeechRecognition not loaded"))
            return

        frames = []
        sample_rate, sample_width = 16000, 2
        chunk = 1024
        self._recording = True
        t_rec0 = time.monotonic()
        try:
            # Reuse pre-built Microphone; recreate once if init failed
            if mic is None:
                mic = sr.Microphone()
            with mic as source:
                sample_rate = getattr(source, "SAMPLE_RATE", 16000) or 16000
                sample_width = getattr(source, "SAMPLE_WIDTH", 2) or 2
                chunk = getattr(source, "CHUNK", 1024) or 1024
                # Lower latency on stop: smaller read blocks (~30–60ms)
                try:
                    chunk = min(chunk, 1024)
                except Exception:
                    pass
                max_frames = int((MAX_RECORD_S * sample_rate) / max(chunk, 1)) + 1
                while not self._stop_event.is_set() and len(frames) < max_frames:
                    # speech_recognition's MicrophoneStream.read() does not
                    # accept exception_on_overflow (that's raw PyAudio only).
                    frames.append(source.stream.read(chunk))
        except Exception as e:
            log.exception("Microphone record failed")
            self._recording = False
            if self._error_callback:
                self._error_callback(e)
            return
        finally:
            self._recording = False

        record_ms = (time.monotonic() - t_rec0) * 1000
        if not frames:
            log.info("Voice record empty record_ms=%.0f", record_ms)
            if self._result_callback:
                self._result_callback("")
            return

        raw = b"".join(frames)
        raw = trim_silence(raw, sample_width=sample_width)
        duration_s = len(raw) / float(sample_rate * sample_width) if sample_rate and sample_width else 0
        log.info(
            "Voice recorded record_ms=%.0f audio_s=%.1f bytes=%d",
            record_ms, duration_s, len(raw),
        )

        # STT on a side thread so this engine can speak/listen again immediately
        # (and so a slow Google call doesn't jam the job queue).
        self._status("Transcribing speech…")
        audio = sr.AudioData(raw, sample_rate, sample_width)
        threading.Thread(
            target=self._stt_worker,
            args=(recognizer, audio, sr),
            name="VoiceSTT",
            daemon=True,
        ).start()

    def _stt_worker(self, recognizer, audio, sr_mod):
        t0 = time.monotonic()
        try:
            # language helps Google a bit for AU English workshop talk
            text = recognizer.recognize_google(audio, language="en-AU")
            stt_ms = (time.monotonic() - t0) * 1000
            log.info("Voice STT stt_ms=%.0f text=%r", stt_ms, (text or "")[:80])
            if self._result_callback:
                self._result_callback(text or "")
        except sr_mod.UnknownValueError:
            stt_ms = (time.monotonic() - t0) * 1000
            log.info("Voice STT stt_ms=%.0f (no speech recognized)", stt_ms)
            if self._result_callback:
                self._result_callback("")
        except Exception as e:
            stt_ms = (time.monotonic() - t0) * 1000
            log.exception("Speech recognition failed stt_ms=%.0f", stt_ms)
            if self._error_callback:
                self._error_callback(e)
