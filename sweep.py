"""MUT-II request sweep — find which request IDs this ECU actually serves.

Why this exists
---------------
On the CE 4G15 the request believed to be Manifold/MAP (0x38) returned a flat
0.0 for an entire running session, so the real MAP channel (if any) is unknown.
Rather than guess, sweep the ECU: ask each candidate request ID and record what
comes back, then repeat while the engine state changes and see which channels
move.

Safety
------
Only single-byte *read* requests are swept. Per the MUT-II protocol reference,
the single-byte fast-mode loop is the well-established datalogging path, while
DTC handling and actuator tests use a longer addressed/multi-byte command form
that is NOT uniformly documented across models. This module therefore:

  * never sends multi-byte command frames;
  * skips 0xFE/0xFF (ECU-ID) by default to keep the sweep purely data-plane;
  * skips an explicit exclusion set of IDs known or suspected to be non-data.

Even so, a sweep asks the ECU things a dealer tool would not ask in that order.
Run it at idle with the car stationary and in neutral, not on track.
"""

import time

# Request IDs never touched by a sweep.
#   0xFE / 0xFF  — ECU-ID pair; harmless to read but not data, excluded so the
#                  sweep output stays a pure parameter list.
#   0x38 / 0x39  — the addresses this app's DTC path uses. On some ECUs these
#                  are fault bitmasks rather than live data; leave them to the
#                  dedicated DTC reader instead of sweeping them blind.
DEFAULT_EXCLUDE = frozenset({0x38, 0x39, 0xFE, 0xFF})

# Conservative default span. Documented H8/539F fast-mode data items live well
# inside this range; going higher buys little and asks more unknown questions.
DEFAULT_FIRST = 0x00
DEFAULT_LAST = 0x9F


def sweep_once(dev, ids, timeout_ms=60, settle_s=0.0):
    """Ask each id once. Returns {id: raw_byte_or_None}."""
    out = {}
    for rid in ids:
        try:
            out[rid] = dev.mut2_request(rid, timeout=timeout_ms)
        except Exception:
            out[rid] = None
        if settle_s:
            time.sleep(settle_s)
    return out


def sweep_ids(first=DEFAULT_FIRST, last=DEFAULT_LAST, exclude=None):
    """The id list a sweep will actually ask, with exclusions applied."""
    ex = DEFAULT_EXCLUDE if exclude is None else frozenset(exclude)
    return [i for i in range(first, last + 1) if i not in ex]


def analyse(passes):
    """Turn a list of {id: raw} passes into per-id findings.

    Returns a list of dicts sorted by id:
        id, answers (bool), values (list), changed (bool), spread (int)
    'changed' is the useful column: a channel whose raw byte moves while the
    engine state changes is a live sensor, not a constant or a stub.
    """
    if not passes:
        return []
    ids = sorted({rid for p in passes for rid in p})
    rows = []
    for rid in ids:
        vals = [p.get(rid) for p in passes]
        got = [v for v in vals if v is not None]
        answers = len(got) > 0
        spread = (max(got) - min(got)) if got else 0
        rows.append({
            "id": rid,
            "answers": answers,
            "answer_rate": len(got) / float(len(vals)) if vals else 0.0,
            "values": vals,
            "changed": len(set(got)) > 1,
            "spread": spread,
        })
    return rows


def format_report(rows, known_names=None):
    """Human-readable sweep summary, most interesting first."""
    known_names = known_names or {}
    live = [r for r in rows if r["answers"]]
    moving = [r for r in live if r["changed"]]
    static = [r for r in live if not r["changed"]]
    silent = [r for r in rows if not r["answers"]]

    out = []
    out.append(f"Swept {len(rows)} request ids  ·  {len(live)} answered  ·  "
               f"{len(moving)} changed value")
    out.append("")
    if moving:
        out.append("CHANGED between passes — these are live channels:")
        for r in sorted(moving, key=lambda r: -r["spread"]):
            name = known_names.get(r["id"], "unknown")
            vals = " ".join("--" if v is None else f"{v:3d}" for v in r["values"])
            out.append(f"  0x{r['id']:02X}  spread {r['spread']:3d}  "
                       f"[{vals}]  {name}")
        out.append("")
    if static:
        out.append("Answered but constant:")
        line = "  " + "  ".join(
            f"0x{r['id']:02X}={r['values'][0] if r['values'][0] is not None else '--'}"
            for r in static)
        out.append(line)
        out.append("")
    out.append(f"No answer from {len(silent)} ids "
               f"(not served by this ECU).")
    return "\n".join(out)
