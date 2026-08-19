"""Learned per-car sensor baselines built from historical session CSVs.

Layers a "what's normal for this specific car" signal on top of j2534's
static FSM-band sensor_health() bands — this never replaces those bands,
it flags values that are unusual for this car even while still inside the
factory-spec band (e.g. coolant creeping hotter session over session).
"""
import csv
import glob
import json
import math
import os

import j2534

_NAME_TO_PID = {name: pid for pid, (name, *_rest) in j2534.ALL_PARAMS.items()}

# Coarse engine-state buckets, derived from j2534.engine_state() so the
# baseline is bucketed against the exact same notion of "what the car was
# doing" that sensor_health() itself uses.
STATE_BUCKETS = ("idle", "running", "off")

_MIN_SAMPLES = 5  # below this, there's not enough history to trust a bucket


def _bucket_for(state):
    if state["idle_like"]:
        return "idle"
    if state["engine_running"]:
        return "running"
    return "off"


def _row_ctx(row):
    """Build a pid->float ctx dict from a CSV row keyed by display name."""
    ctx = {}
    for name, raw in row.items():
        if name == "t" or not raw:
            continue
        pid = _NAME_TO_PID.get(name)
        if pid is None:
            continue
        try:
            ctx[pid] = float(raw)
        except ValueError:
            continue
    return ctx


def _iter_session_rows(session_dir):
    pattern = os.path.join(session_dir, "4g1_live_*.csv")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as f:
                yield from csv.DictReader(f)
        except OSError:
            continue


def build_baseline(session_dir):
    """Scan every real live-session CSV under session_dir and compute
    mean/stdev per (parameter display name, engine-state bucket).

    Returns a JSON-serializable dict: {name: {bucket: {mean, stdev, n}}}.
    Buckets with fewer than _MIN_SAMPLES rows are omitted rather than
    reported with a misleadingly narrow stdev.
    """
    sums = {}  # name -> bucket -> [n, sum, sumsq]
    for row in _iter_session_rows(session_dir):
        ctx = _row_ctx(row)
        if not ctx:
            continue
        bucket = _bucket_for(j2534.engine_state(ctx))
        for name, raw in row.items():
            if name == "t" or not raw:
                continue
            try:
                v = float(raw)
            except ValueError:
                continue
            slot = sums.setdefault(name, {}).setdefault(bucket, [0, 0.0, 0.0])
            slot[0] += 1
            slot[1] += v
            slot[2] += v * v

    baseline = {}
    for name, buckets in sums.items():
        for bucket, (n, s, ss) in buckets.items():
            if n < _MIN_SAMPLES:
                continue
            mean = s / n
            variance = max(ss / n - mean * mean, 0.0)
            baseline.setdefault(name, {})[bucket] = {
                "mean": mean,
                "stdev": math.sqrt(variance),
                "n": n,
            }
    return baseline


def save_baseline(baseline, path):
    """Atomic write, matching the pattern app.py's save_settings() uses."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_baseline(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def deviation(baseline, name, value, state):
    """Z-score of `value` against the learned baseline for `name` in the
    current engine-state bucket. None if the value isn't numeric or there
    isn't enough history for this bucket yet.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    bucket = _bucket_for(state)
    stats = baseline.get(name, {}).get(bucket)
    if not stats or stats["stdev"] <= 0:
        return None
    return (v - stats["mean"]) / stats["stdev"]
