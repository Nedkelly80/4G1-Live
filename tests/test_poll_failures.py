"""Failure-mode tests for the live poll loop.

These cover what happens trackside when things go wrong: the adapter is
unplugged mid-session, the driver throws, or the ECU stops answering. The
poll loop is exercised directly against a fake device, with the tkinter
surface stubbed out, so no display or hardware is needed.
"""

import sys
import types
import unittest


class FakeDev:
    """Adapter stand-in. Answers a few PIDs, then optionally fails."""

    def __init__(self, fail_after=None, exc=OSError("device not responding"),
                 silent_after=None):
        self.fail_after = fail_after
        self.silent_after = silent_after
        self.exc = exc
        self.calls = 0
        self.disconnected = False
        self.pin1_released = False
        self.request_timeout_ms = 400
        self.baud = 15625

    def ground_pin1(self):
        pass

    def release_pin1(self):
        self.pin1_released = True

    def mut2_connect(self, baud=None):
        pass

    def mut2_init(self, addr=0, attempts=5):
        return b"\xc0\x55\xef\x85"

    def mut2_request(self, pid, timeout=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.exc
        if self.silent_after is not None and self.calls > self.silent_after:
            return None
        return 64

    def disconnect(self):
        self.disconnected = True


class _BoundedSamples(list):
    """Stops a healthy loop after N samples so tests terminate."""

    def __init__(self, harness, limit):
        super().__init__()
        self._h = harness
        self._limit = limit

    def append(self, item):
        super().append(item)
        if len(self) >= self._limit:
            self._h.live = False


class PollLoopHarness:
    """Minimal stand-in for the app object the poll loop runs against."""

    def __init__(self, dev, gauges):
        self.dev = dev
        self.settings = {
            "pin1_ground": True, "mut_baud": 15625, "init_addr": "0x00",
            "init_attempts": 5, "request_timeout_ms": 400,
            "live_timeout_ms": 60, "poll_hz": 200,
        }
        self.gauges = dict(gauges)
        self.heroes = {}
        self.latest = {}
        self.samples = []
        self.live = True
        self.demo = False
        self.logged = []
        self.faults = []
        self.autosaved = 0
        self.autosave_closed = False
        self.status = None
        self._hz_times = []
        self._poll_hz_actual = 0.0
        self._session_t0 = 0.0
        self.max_cycles = 400
        self.samples = _BoundedSamples(self, self.max_cycles)

    # --- surface the poll loop touches -------------------------------------
    def log_line(self, text, telemetry=False):
        self.logged.append(text)

    def _set_status(self, text, mode="off"):
        self.status = (text, mode)

    def _autosave_row(self, snap):
        self.autosaved += 1

    def _autosave_stop(self):
        self.autosave_closed = True

    def _update_idle_banner(self):
        pass

    def after(self, delay, fn=None, *args):
        if fn is not None:
            fn(*args)

    def _live_stopped_by_fault(self, reason):
        self.live = False
        self.faults.append(reason)

    def _parse_init_addr(self, v):
        return 0x00


class _Stub:
    """Permissive stand-in: subclassable, callable, any attribute."""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Stub()

    def __call__(self, *a, **k):
        return _Stub()


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        return _Stub


def _load_app_module():
    """Import app.py with tkinter stubbed so it loads headless."""
    if "app" in sys.modules:
        return sys.modules["app"]
    for name in ("tkinter", "tkinter.font", "tkinter.ttk"):
        sys.modules.setdefault(name, _StubModule(name))
    tk = sys.modules["tkinter"]
    tk.messagebox = _StubModule("tkinter.messagebox")
    tk.filedialog = _StubModule("tkinter.filedialog")
    tk.ttk = sys.modules["tkinter.ttk"]
    sys.modules.setdefault("tkinter.messagebox", tk.messagebox)
    sys.modules.setdefault("tkinter.filedialog", tk.filedialog)
    import app
    return app


class PollLoopFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _load_app_module()

    def _run(self, dev):
        h = PollLoopHarness(dev, {0x21: object(), 0x06: object()})
        self.app.App._poll_loop(h)
        return h

    def test_adapter_failure_stops_the_session(self):
        h = self._run(FakeDev(fail_after=10))
        self.assertFalse(h.live, "session must not stay live after adapter failure")

    def test_adapter_failure_is_reported_as_a_fault(self):
        h = self._run(FakeDev(fail_after=10))
        self.assertTrue(h.faults, "user must be told comms were lost")
        self.assertIn("communication lost", h.faults[0])

    def test_adapter_failure_closes_the_autosave_file(self):
        h = self._run(FakeDev(fail_after=10))
        self.assertTrue(h.autosave_closed, "session CSV must be closed on failure")

    def test_adapter_is_disconnected_and_pin1_released_on_failure(self):
        dev = FakeDev(fail_after=10)
        self._run(dev)
        self.assertTrue(dev.disconnected)
        self.assertTrue(dev.pin1_released)

    def test_transient_errors_do_not_kill_the_session(self):
        """A couple of glitches should be ridden out, not treated as fatal."""
        class Flaky(FakeDev):
            def mut2_request(self, pid, timeout=None):
                self.calls += 1
                if self.calls in (3, 4):
                    raise OSError("transient")
                return 64

        h = self._run(Flaky())
        self.assertFalse(h.faults, "a brief glitch should not end the session")
        self.assertGreater(len(h.samples), 10)

    def test_unanswered_pid_is_dropped_not_fatal(self):
        h = self._run(FakeDev(silent_after=2))
        self.assertFalse(h.faults, "timeouts are not adapter failures")
        self.assertTrue(any("dropped from polling" in m for m in h.logged))

    def test_healthy_run_records_samples_and_stops_cleanly(self):
        h = self._run(FakeDev())
        self.assertGreaterEqual(len(h.samples), h.max_cycles)
        self.assertEqual(h.autosaved, len(h.samples))
        self.assertFalse(h.faults)
        self.assertTrue(h.autosave_closed)


if __name__ == "__main__":
    unittest.main()
