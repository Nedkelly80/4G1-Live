# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Quality bar

- Read before write; verify with `pytest tests/` before claiming success.
- Never edit the sibling commercial folder `4G1-Live` from this fork.
- Extra scrutiny on live polling (`_poll_loop`, autosave) — add failure-mode tests if touched.
- No secrets in repo (`XAI_API_KEY` / management key stay in env only).
- Windows + **32-bit Python** for J2534 (`py -3.13-32`).

## What this is

**4G1 Live AI** is a personal-use fork of the commercial `4G1 Live` Mitsubishi
MUT-II live-diagnostics app (the original lives in a sibling folder,
`4G1-Live`, and is untouched by this fork — never edit that folder from here).
Same Python/tkinter dashboard, J2534 device layer, and vehicle profiles, plus:

- **`baseline.py`** — learns per-car sensor baselines (mean/stdev per
  parameter, bucketed by engine state) from historical session CSVs under
  `Sessions/4g1_live_*.csv`. Layers on top of `j2534.sensor_health()`'s static
  FSM bands; never replaces them.
- **`memory.py`** — durable long-term AI memory (`%LOCALAPPDATA%/4G1 Live AI/memory.json`):
  user name, vehicle notes, lessons, session summaries. Injected into every
  Grok system prompt. Grows via mid-session + on-exit consolidation.
- **`knowledge.py`** — xAI Collections manuals store (`knowledge.json`). Uploads
  PDFs/notes, creates or reuses a collection, enables `file_search` during ask.
  Needs `XAI_API_KEY` + `XAI_MANAGEMENT_API_KEY` (Collections permissions).
- **`ai_assistant.py`** — calls the xAI / SpaceXAI Grok API (`XAI_API_KEY` from
  the environment — never hardcode a key here or in `settings.json`; base URL
  `https://api.x.ai/v1`, default model `grok-4.5`) to explain sensor readings
  and answer typed/voice questions (web_search + optional file_search). Sensor
  explains trigger on state change only — never on every poll tick, always off
  the Tk main thread.
- App wiring lives in `app.py`: AI Assistant tab (chat box + upload manuals),
  Dashboard alert banner, Settings (name / model / collection id / Save Memory),
  Rebuild Baseline. On exit the session is consolidated into memory.

This is a single-user tool, not a product for other customers — no per-user
billing, no shared API key concerns.

## Self-improvement is gated by tests, run on-demand

This app runs trackside, often with nobody around to fix a bad change mid
race weekend. When asked to review new session data and improve the app
("look at the new logs and improve things"):

1. **Never consider a change done without running `pytest tests/` first.**
   Add tests for new logic (see `tests/test_baseline.py` /
   `tests/test_ai_assistant.py` / `tests/test_memory.py` for the mocking
   pattern used for the OpenAI-compatible xAI client). If a change makes
   tests fail, fix it or revert it — don't report success anyway.
2. **Extra scrutiny on `_poll_loop`, `_autosave_row`/`_autosave_start`, and
   anything else in the live-polling path.** This code was specifically
   hardened after a prior trackside failure (adapter drop mid-session
   freezing the UI under a LIVE header — see git log `87dfc44`). Don't touch
   it casually; if you do, add a failure-mode test alongside
   `tests/test_poll_failures.py`.
3. This is run **on-demand only**, when the user asks after a race weekend —
   nothing here should be wired to run unattended or on a schedule.
4. Keep the learned-baseline / AI-assistant layers additive. `sensor_health()`
   and its static FSM bands are the ground truth for the commercial product's
   validation gate (see the original repo's README) — don't change their
   verdicts as a side effect of tuning the AI layer.

## Running the app

Requires 32-bit Python (the J2534 driver is 32-bit):

```
py -3.13-32 app.py
```

Set the Grok keys first (PowerShell):

```
$env:XAI_API_KEY = "your_key_here"
$env:XAI_MANAGEMENT_API_KEY = "mgmt_key_for_collections"  # optional until you upload manuals
```

## Tests

```
py -3.13-32 -m pytest tests/ -q
```
