"""Grok-powered plain-language explanations and Q&A for 4G1 Live AI.

Called from a background thread by app.py's AI-assistant hook, on a sensor's
verdict transitioning into warn/bad or a fresh baseline deviation — never on
every poll tick, and never on the Tk main thread. See explain() below and
App._maybe_explain in app.py for the trigger/cooldown logic.

Uses the xAI / SpaceXAI API (OpenAI-compatible):
  env  XAI_API_KEY  (or Grok Build login in ~/.grok/auth.json)
  url  https://api.x.ai/v1
  default model  grok-4.20-0309-non-reasoning  (fast pit-lane answers)

Never hardcode the key here or in settings.json. Durable memory is injected via
the system prompt (see memory.format_for_prompt).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from openai import OpenAI

import memory as ai_memory

# Current xAI flagship. Existing deliberate model selections are preserved
# by app.load_settings(); new installs and unpinned legacy defaults use 4.6.
DEFAULT_MODEL = "grok-4.6"
SMART_MODEL = DEFAULT_MODEL
FAST_MODEL = "grok-4.20-0309-non-reasoning"
MODEL_CHOICES = [
    "grok-4.6",                      # Default / newest flagship
    "grok-4.5",                      # Previous flagship
    "grok-4.3",
    "grok-4.20-0309-reasoning",
    "grok-4.20-0309-non-reasoning",  # Fast trackside
]
# Older defaults we quietly migrate off of
_LEGACY_SLOW_DEFAULTS = {
    "grok-4.20-0309-non-reasoning",
}
# Only migrate when it was the *installed* default, not a deliberate choice?
# User asked for faster — migrate previous DEFAULT only once via app load.
MIGRATE_FROM_MODELS = {
    # empty / missing handled in app; these were historical defaults
}

XAI_BASE_URL = "https://api.x.ai/v1"
_MAX_TOKENS = 700
_EXPLAIN_MAX_TOKENS = 420
_CONSOLIDATE_MAX_TOKENS = 900
_FAST_TIMEOUT = 28.0
_TOOL_TIMEOUT = 55.0
_CONSOLIDATE_TIMEOUT = 25.0

# Built-in xAI server-side tools (Responses API).
WEB_SEARCH_TOOL = {"type": "web_search"}

# If the question clearly needs external facts, attach tools. Live-data
# "is this normal?" questions stay tool-free (~1s vs ~9s with web_search).
_TOOL_TRIGGERS = (
    "spec", "specs", "torque", "torques", "pinout", "pin-out", "wiring",
    "wire colour", "wire color", "ohm", "ohms", "resistance", "fsm",
    "manual", "procedure", "how to", "how do i", "part number", "part #",
    "replace", "removing", "install", "tsb", "bulletin", "look up",
    "lookup", "search", "diagram", "connector", "where is", "location of",
    "bolt size", "vacuum diagram", "factory", "oem", "stock setting",
    "idle target", "what should", "workshop", "service data", "clearance",
    "gap", "timing belt", "cam timing", "firing order", "ecu pin",
    "mut code", "dtc meaning", "fault code", "p0", "p1", "p2",
    "tactrix", "logcfg",
)


def file_search_tool(collection_ids, max_num_results=6):
    """OpenAI-compatible file_search tool bound to xAI Collections ids."""
    ids = [c for c in (collection_ids or []) if c]
    if not ids:
        return None
    return {
        "type": "file_search",
        "vector_store_ids": ids,
        "max_num_results": max_num_results,
    }


def needs_external_tools(question, has_manuals=False):
    """True when web_search / file_search are worth the latency cost."""
    q = (question or "").strip().lower()
    if not q:
        return False
    if has_manuals:
        # Manuals collection is local RAG — cheap enough, and user uploaded
        # them to be used. Still skip for pure live-data status checks.
        pure_live = (
            q.startswith("is ")
            or q.startswith("are ")
            or "normal" in q
            or "okay" in q
            or "ok?" in q
            or "too high" in q
            or "too low" in q
            or "what's this reading" in q
            or "what is this reading" in q
        )
        # short status questions about live sensors → skip tools
        if pure_live and len(q) < 80 and not any(t in q for t in _TOOL_TRIGGERS):
            return False
        return True
    return any(t in q for t in _TOOL_TRIGGERS)


def build_tools(collection_ids=None, question=None, force=False):
    """Tools only when needed — web_search on every call was the main lag.

    force=True keeps old always-on behaviour for callers that need it.
    """
    ids = [c for c in (collection_ids or []) if c]
    if not force and question is not None and not needs_external_tools(
        question, has_manuals=bool(ids)
    ):
        return None
    tools = []
    # Prefer manuals first when present; web fills gaps.
    fs = file_search_tool(ids)
    if fs:
        tools.append(fs)
    # Web only when no manuals, or question smells like external lookup.
    if force or not ids or needs_external_tools(question or "", has_manuals=False):
        tools.append(WEB_SEARCH_TOOL)
    return tools or None


class Reading:
    """Everything needed to explain one sensor reading."""

    def __init__(self, name, value, units, verdict, note, criteria,
                 baseline_z, ctx_readings, vehicle_profile, engine_state=None):
        self.name = name
        self.value = value
        self.units = units
        self.verdict = verdict            # "warn" / "bad" from j2534.sensor_health()
        self.note = note                  # j2534.PARAM_NOTES[pid]
        self.criteria = criteria          # j2534.HEALTH_CRITERIA[pid]
        self.baseline_z = baseline_z      # z-score vs this car's own history, or None
        self.ctx_readings = ctx_readings  # dict of other name->value for context
        self.vehicle_profile = vehicle_profile  # profile "name" string
        self.engine_state = engine_state  # KOEO / cold idle / warm idle / load when known


def read_grok_login_token():
    """Use the same xAI login as Grok Build / Desktop (~/.grok/auth.json).

    This is what lets 4G1 Live AI work without pasting a console API key —
    as long as the user is signed in to Grok on this PC.
    """
    path = os.path.join(os.path.expanduser("~"), ".grok", "auth.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        tok = entry.get("key") or entry.get("access_token") or entry.get("token")
        if isinstance(tok, str) and len(tok) > 20:
            return tok.strip()
    return None


def resolve_api_key(api_key=None):
    """Pick a working Bearer token for api.x.ai.

    Order:
      1. explicit argument
      2. XAI_API_KEY env (console key xai-… or JWT)
      3. Grok Build login token from ~/.grok/auth.json
    """
    if api_key and str(api_key).strip():
        return str(api_key).strip()
    env = (os.environ.get("XAI_API_KEY") or "").strip()
    # Prefer real console keys; also accept JWTs stored in env
    if env.startswith("xai-") and len(env) > 20:
        return env
    if env.startswith("eyJ") and len(env) > 40:
        return env
    login = read_grok_login_token()
    if login:
        return login
    if env:
        return env
    raise RuntimeError(
        "No xAI credentials found. Sign in to Grok Build on this PC "
        "(writes ~/.grok/auth.json), or set XAI_API_KEY from https://console.x.ai"
    )


def auth_status():
    """Short status for Settings UI — never returns the raw secret."""
    env = (os.environ.get("XAI_API_KEY") or "").strip()
    if env.startswith("xai-") and len(env) > 20:
        return "console key OK"
    if env.startswith("eyJ") and len(env) > 40:
        return "token in env OK"
    if read_grok_login_token():
        return "Grok login OK (auto)"
    if env:
        return "key present but may be invalid"
    return "MISSING — sign in to Grok Build or set XAI_API_KEY"


def probe_api(api_key=None, model=DEFAULT_MODEL, timeout=20.0):
    """Live connectivity check. Returns (ok: bool, detail: str, latency_s: float)."""
    t0 = time.monotonic()
    try:
        text = _complete(
            [
                {
                    "role": "system",
                    "content": "Reply with exactly: OK",
                },
                {"role": "user", "content": "ping"},
            ],
            model=model,
            api_key=api_key,
            tools=None,
            max_tokens=8,
            timeout=timeout,
        )
        dt = time.monotonic() - t0
        if text:
            return True, f"{auth_status()} · {model} · {dt:.1f}s · reply={text[:40]!r}", dt
        return False, f"Empty reply from {model} after {dt:.1f}s", dt
    except Exception as e:
        dt = time.monotonic() - t0
        return False, f"{type(e).__name__}: {e} ({dt:.1f}s)", dt


def _client(api_key=None, timeout=90.0, max_retries=2):
    key = resolve_api_key(api_key)
    return OpenAI(
        api_key=key, base_url=XAI_BASE_URL, timeout=timeout, max_retries=max_retries,
    )


def _system_prompt(memory_brief=None):
    if memory_brief and memory_brief.strip():
        return memory_brief.strip()
    return ai_memory.format_for_prompt(ai_memory.default_memory())


def _response_text(response):
    """Extract plain text from a Responses API result."""
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    parts = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type != "message":
            continue
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        for block in content or []:
            btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if btype in ("output_text", "text"):
                t = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                if t:
                    parts.append(t.strip())
    return "\n".join(p for p in parts if p)


def _complete(messages, model=DEFAULT_MODEL, api_key=None, tools=None,
              max_tokens=_MAX_TOKENS, timeout=_FAST_TIMEOUT, max_retries=2,
              reasoning_effort=None):
    """Blocking Responses API call. messages: list of {role, content}.

    Grok 4.5/4.6 accept Responses-API reasoning controls.  We use low effort
    only on automatic/voice pit-lane prompts so the UI remains responsive;
    typed diagnosis keeps xAI's high-quality default unless a caller asks
    otherwise.  Older selectable models receive no unsupported parameter.
    """
    client = _client(api_key=api_key, timeout=timeout, max_retries=max_retries)
    kwargs = {
        "model": model,
        "input": messages,
        "store": False,
        "max_output_tokens": max_tokens,
    }
    if reasoning_effort and model in ("grok-4.5", "grok-4.6"):
        kwargs["reasoning"] = {"effort": reasoning_effort}
    if tools:
        kwargs["tools"] = tools
    t0 = time.monotonic()
    response = client.responses.create(**kwargs)
    text = _response_text(response)
    logging.info(
        "Grok %s tools=%s in %.1fs (%d chars)",
        model,
        "yes" if tools else "no",
        time.monotonic() - t0,
        len(text or ""),
    )
    return text


def _build_prompt(reading):
    lines = [
        f"Sensor: {reading.name} = {reading.value} {reading.units}".rstrip(),
        f"Vehicle: {reading.vehicle_profile}",
        f"Engine state: {reading.engine_state or 'unknown'}",
        f"Static verdict: {reading.verdict}",
        f"Factory / FSM note: {reading.note}",
        f"Health band: {reading.criteria}",
    ]
    if reading.baseline_z is not None:
        lines.append(
            f"vs THIS car's logged history: {reading.baseline_z:+.1f}σ from its normal."
        )
    if reading.ctx_readings:
        ctx_str = ", ".join(f"{k}={v}" for k, v in reading.ctx_readings.items())
        lines.append(f"Other live readings: {ctx_str}")
    lines.append(
        "\nPit-lane brief (≤3 short sentences): what it likely means on a real "
        "4G1 car right now, and ONE concrete next check (hose, connector, fan, "
        "sensor, vacuum, wiring — not vague 'investigate further'). "
        "Use live numbers as ground truth. Don't parrot every raw value."
    )
    return "\n".join(lines)


def explain(reading, api_key=None, model=DEFAULT_MODEL, memory_brief=None):
    """Blocking call — run this on a background thread, never the Tk main thread.

    Returns the explanation text. Raises on any API/network failure; callers
    must catch and fall back to showing just the static/learned numbers
    rather than crashing the session (see app.py's _maybe_explain).
    """
    messages = [
        {"role": "system", "content": _system_prompt(memory_brief)},
        {"role": "user", "content": _build_prompt(reading)},
    ]
    # Sensor explains never need web search — speed + less noise trackside.
    return _complete(
        messages,
        model=model,
        api_key=api_key,
        tools=None,
        max_tokens=_EXPLAIN_MAX_TOKENS,
        timeout=_FAST_TIMEOUT,
        reasoning_effort="low",
    )


def _build_question_prompt(question, context_summary, vehicle_profile, has_manuals=False,
                           use_tools=False, for_voice=False):
    lines = [
        f"Vehicle: {vehicle_profile}",
        f"Current live MUT-II readings: {context_summary or 'no live data right now'}",
        f"\nDriver/tuner (at the car): {question}",
    ]
    if for_voice:
        lines.append(
            "\nVOICE reply: MAX 2 short sentences. Lead with NORMAL/WATCH/FIX NOW. "
            "One concrete next check if needed. No lists, no markdown, no waffle. "
            "Live readings are ground truth. AU English fine."
        )
    else:
        lines.append(
            "\nAnswer like a sharp mate on the tools: 2–4 short sentences, concrete, "
            "real-world. Lead with the call (normal / watch / fix now). Name one next "
            "check when something's off. Live readings are ground truth — don't argue "
            "with them. Don't repeat the question. AU/UK English fine."
        )
    if use_tools and has_manuals:
        lines.append(
            "Use the uploaded workshop manuals / FSM first for specs and procedures; "
            "web only for gaps."
        )
    elif use_tools:
        lines.append(
            "Use web search only if you need a factory number or procedure you are "
            "not sure of — otherwise answer from live data and experience."
        )
    else:
        lines.append(
            "Answer from live data + your Mitsubishi MUT-II knowledge. No web needed "
            "for this one."
        )
    return "\n".join(lines)


def ask(question, context_summary, vehicle_profile, history=None, api_key=None,
        model=DEFAULT_MODEL, memory_brief=None, collection_ids=None, for_voice=False):
    """Blocking call — run this on a background thread, never the Tk main thread.

    Answers a typed or spoken question against the car's current live readings.
    Tools (web / manuals) only attach when the question needs them — plain
    live-data status questions stay fast (~1–2s).

    History is compact user/assistant pairs (raw question + answer only) so
    multi-turn stays cheap and clear.

    for_voice=True: shorter answers + tighter token budget (heard sooner on PTT).

    Returns (answer_text, updated_history).
    Raises on any API/network failure — callers should catch and fall back
    without losing history built so far (see app.py's _ask_worker).
    """
    history = list(history) if history else []
    # Voice: keep only last couple of turns — less prompt, faster reply
    if for_voice and len(history) > 4:
        history = history[-4:]
    ids = [c for c in (collection_ids or []) if c]
    tools = build_tools(ids, question=question)
    use_tools = bool(tools)

    # Compact history: only the bare question text for prior turns.
    # Current turn carries fresh live context.
    user_msg_for_api = {
        "role": "user",
        "content": _build_question_prompt(
            question,
            context_summary,
            vehicle_profile,
            has_manuals=bool(ids),
            use_tools=use_tools,
            for_voice=for_voice,
        ),
    }
    messages = [{"role": "system", "content": _system_prompt(memory_brief)}]
    messages.extend(history)
    messages.append(user_msg_for_api)

    max_tok = 160 if for_voice else _MAX_TOKENS
    answer = _complete(
        messages,
        model=model,
        api_key=api_key,
        tools=tools,
        max_tokens=max_tok,
        timeout=_TOOL_TIMEOUT if use_tools else _FAST_TIMEOUT,
        reasoning_effort="low" if for_voice else None,
    )
    # Store compact turns in history (not the full prompt) so follow-ups stay fast.
    compact_user = {"role": "user", "content": question}
    compact_asst = {"role": "assistant", "content": answer}
    new_history = history + [compact_user, compact_asst]
    # Cap session history so tokens don't grow without bound within one run.
    if len(new_history) > 12:
        new_history = new_history[-12:]
    return answer, new_history


def interpret_log(report_text, vehicle_profile, memory_brief=None, api_key=None,
                  model=DEFAULT_MODEL, collection_ids=None):
    """Turn a numeric log report into a pit-lane write-up.

    No web/file tools and no SDK retries — the numbers are already on screen
    and a hung write-up must not block the review window.
    """
    del collection_ids  # numbers-only path; manuals already sit in memory_brief
    messages = [
        {"role": "system", "content": _system_prompt(memory_brief)},
        {
            "role": "user",
            "content": (
                f"Vehicle: {vehicle_profile}\n"
                "Offline Tactrix/4G1 log (no live adapter). "
                "Call NORMAL / WATCH / FIX NOW. Use AU CE 4G15 factory bands. "
                "One concrete next check per problem. 4–8 short sentences.\n\n"
                + (report_text or "")
            ),
        },
    ]
    return _complete(
        messages,
        model=model,
        api_key=api_key,
        tools=None,
        max_tokens=420,
        timeout=18.0,
        max_retries=0,
    )


def _extract_json_object(text):
    """Best-effort parse of a JSON object from a model reply."""
    if not text:
        return None
    text = text.strip()
    # fenced ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def consolidate(session_turns, memory_brief=None, vehicle_profile_id="",
                vehicle_label="", api_key=None, model=DEFAULT_MODEL):
    """Ask Grok to extract durable facts from this session's chat.

    Returns a dict suitable for memory.apply_consolidation, or {} if nothing
    useful / parse failure. Raises on network/API failure so the caller can
    fall back to memory.consolidate_local.
    """
    transcript = ai_memory.format_session_transcript(session_turns)
    if not transcript.strip():
        return {}

    user = (
        "Extract durable facts worth remembering for the NEXT time this "
        "diagnostics app is opened. Reply with ONLY a JSON object, no markdown, "
        "using these keys (omit empty ones):\n"
        '  "user_name": string or null,\n'
        '  "prefs": object of string preferences or {},\n'
        '  "vehicle_label": string or null,\n'
        '  "vehicle_profile_id": string or null,\n'
        '  "vehicle_notes": [short strings about THIS car],\n'
        '  "lessons": [short strings: fixes that worked, recurring issues],\n'
        '  "summary": one short paragraph of this session\n'
        "Only include facts supported by the transcript. Do not invent.\n\n"
        f"Active vehicle profile id: {vehicle_profile_id or 'unknown'}\n"
        f"Active vehicle label: {vehicle_label or 'unknown'}\n\n"
        f"Transcript:\n{transcript}"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You consolidate pit-lane diagnostic session notes into JSON memory. "
                "Be conservative: only solid facts. Prefer short bullet-ready strings.\n\n"
                + (_system_prompt(memory_brief) if memory_brief else "")
            ),
        },
        {"role": "user", "content": user},
    ]
    raw = _complete(
        messages,
        model=model,
        api_key=api_key,
        max_tokens=_CONSOLIDATE_MAX_TOKENS,
        timeout=_CONSOLIDATE_TIMEOUT,
    )
    data = _extract_json_object(raw) or {}
    # Ensure profile id is filled when caller knows it
    if vehicle_profile_id and not data.get("vehicle_profile_id"):
        data["vehicle_profile_id"] = vehicle_profile_id
    if vehicle_label and not data.get("vehicle_label"):
        data["vehicle_label"] = vehicle_label
    return data
