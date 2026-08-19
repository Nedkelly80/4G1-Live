"""xAI Collections knowledge base for 4G1 Live AI manuals / FSM PDFs.

Creates (or reuses) a persistent Collection, uploads local documents, and
returns the collection id so Grok can file_search during ask().

Auth:
  XAI_API_KEY            — file upload + chat search
  XAI_MANAGEMENT_API_KEY — create collection / attach documents
    (console.x.ai → Management Keys → Collections permissions)

State is stored under the user data dir as knowledge.json (see app.KNOWLEDGE_PATH).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from openai import OpenAI

DEFAULT_COLLECTION_NAME = "4G1 Live Manuals"
XAI_API_BASE = "https://api.x.ai/v1"
XAI_MGMT_BASE = "https://management-api.x.ai/v1"
MAX_DOCS_TRACKED = 80


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_state():
    return {
        "collection_id": None,
        "collection_name": DEFAULT_COLLECTION_NAME,
        "documents": [],  # [{name, file_id, path, added_at}]
        "local_extracts": [],  # [{name, path, text, added_at}] — works without mgmt key
        "updated_at": None,
    }


def load_state(path):
    state = default_state()
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            state.update({k: raw[k] for k in state if k in raw})
            if not isinstance(state.get("documents"), list):
                state["documents"] = []
            if not isinstance(state.get("local_extracts"), list):
                state["local_extracts"] = []
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Could not read knowledge state from %s", path)
    return state


def save_state(state, path):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        out = dict(state)
        out["updated_at"] = _now_iso()
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        os.replace(temp, path)
        state["updated_at"] = out["updated_at"]
    except Exception:
        logging.exception("Could not save knowledge state to %s", path)


LOCAL_EXTRACT_CHARS = 14000
LOCAL_PROMPT_BUDGET = 7000


def _api_key():
    return os.environ.get("XAI_API_KEY") or ""


def _mgmt_key():
    return os.environ.get("XAI_MANAGEMENT_API_KEY") or ""


def _mgmt_request(method, path, body=None, timeout=60):
    key = _mgmt_key()
    if not key:
        raise RuntimeError(
            "XAI_MANAGEMENT_API_KEY is not set. Create a Management Key at "
            "https://console.x.ai (Collections permissions) and set the env var, "
            "or paste an existing Collection ID in Settings."
        )
    url = XAI_MGMT_BASE + path
    data = None
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Collections API {method} {path} failed ({e.code}): {detail}") from e


def _extract_collection_id(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("collection_id", "id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    # nested
    for nest in ("collection", "data"):
        inner = payload.get(nest)
        if isinstance(inner, dict):
            found = _extract_collection_id(inner)
            if found:
                return found
    return None


def ensure_collection(state, name=None):
    """Return collection_id, creating one if needed. Updates state in place."""
    cid = (state.get("collection_id") or "").strip()
    if cid:
        return cid
    cname = (name or state.get("collection_name") or DEFAULT_COLLECTION_NAME).strip()
    payload = _mgmt_request("POST", "/collections", {"collection_name": cname})
    cid = _extract_collection_id(payload)
    if not cid:
        raise RuntimeError(f"Could not parse collection_id from create response: {payload!r}")
    state["collection_id"] = cid
    state["collection_name"] = cname
    return cid


def set_collection_id(state, collection_id, name=None):
    """Use an existing collection created in the xAI console."""
    cid = (collection_id or "").strip()
    state["collection_id"] = cid or None
    if name:
        state["collection_name"] = name.strip()
    return state["collection_id"]


def _upload_file_to_xai(file_path, api_key=None):
    key = api_key or _api_key()
    if not key:
        raise RuntimeError("XAI_API_KEY is not set.")
    client = OpenAI(api_key=key, base_url=XAI_API_BASE, timeout=180.0)
    name = os.path.basename(file_path)
    mime, _ = mimetypes.guess_type(file_path)
    with open(file_path, "rb") as f:
        # OpenAI-compatible files endpoint on api.x.ai
        result = client.files.create(file=(name, f, mime or "application/octet-stream"), purpose="assistants")
    file_id = getattr(result, "id", None) or (result.get("id") if isinstance(result, dict) else None)
    if not file_id:
        raise RuntimeError(f"File upload returned no id: {result!r}")
    return file_id


def add_document_to_collection(collection_id, file_id):
    """Attach an uploaded file to a collection (management API)."""
    path = f"/collections/{collection_id}/documents/{file_id}"
    return _mgmt_request("POST", path, body={})


def extract_document_text(file_path, max_chars=LOCAL_EXTRACT_CHARS):
    """Pull plain text from a local manual. No cloud key required."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".txt", ".md", ".csv", ".html", ".htm", ".log"):
        with open(file_path, encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    if ext == ".pdf":
        text = _extract_pdf(file_path, max_chars)
        if not (text or "").strip():
            raise RuntimeError("PDF had no extractable text (scanned image?).")
        return text
    # last resort: try as text
    with open(file_path, encoding="utf-8", errors="replace") as f:
        return f.read(max_chars)


def _extract_pdf(file_path, max_chars):
    try:
        import fitz  # PyMuPDF
        parts = []
        with fitz.open(file_path) as doc:
            for page in doc:
                parts.append(page.get_text("text") or "")
                if sum(len(p) for p in parts) >= max_chars:
                    break
        return "".join(parts)[:max_chars]
    except Exception:
        logging.info("fitz extract failed, trying PyPDF2", exc_info=True)
    try:
        import PyPDF2
        parts = []
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                if sum(len(p) for p in parts) >= max_chars:
                    break
        return "".join(parts)[:max_chars]
    except Exception as exc:
        raise RuntimeError(f"Could not read PDF text: {exc}") from exc


def ingest_local(state, file_path):
    """Store a local text extract so the AI can use the manual without a mgmt key."""
    name = os.path.basename(file_path)
    text = extract_document_text(file_path)
    extracts = [e for e in (state.get("local_extracts") or []) if e.get("name") != name]
    extracts.append({
        "name": name,
        "path": os.path.abspath(file_path),
        "text": (text or "").strip(),
        "added_at": _now_iso(),
    })
    state["local_extracts"] = extracts[-MAX_DOCS_TRACKED:]
    return name, len(text or "")


def upload_document(state, file_path, api_key=None):
    """Ensure collection, upload file, attach, record in state.

    Returns (file_id, collection_id, display_name).
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    name = os.path.basename(file_path)
    # de-dupe by name
    for doc in state.get("documents") or []:
        if doc.get("name") == name:
            raise RuntimeError(f"Already uploaded a document named '{name}'. Rename or remove first.")

    # Always keep a local extract so the AI has the manual even if
    # the management key / collection step fails.
    try:
        ingest_local(state, file_path)
    except Exception:
        logging.exception("Local extract failed for %s", file_path)
    cid = ensure_collection(state)
    file_id = _upload_file_to_xai(file_path, api_key=api_key)
    add_document_to_collection(cid, file_id)
    docs = list(state.get("documents") or [])
    docs.append({
        "name": name,
        "file_id": file_id,
        "path": os.path.abspath(file_path),
        "added_at": _now_iso(),
    })
    state["documents"] = docs[-MAX_DOCS_TRACKED:]
    return file_id, cid, name


SEED_EXTRACT_CANDIDATES = (
    os.path.join(os.path.expanduser("~"), "OldDesktop", "4G1_Manuals",
                 "YEAR_CHEAT_SHEET_4G15_AU.txt"),
    os.path.join(os.path.expanduser("~"), "OldDesktop", "4G1_Manuals",
                 "YEAR_BY_YEAR_TINY_DIFFERENCES.txt"),
    r"G:\Users\trmra\OldDesktop\4G1_Manuals\YEAR_CHEAT_SHEET_4G15_AU.txt",
    r"G:\Users\trmra\OldDesktop\4G1_Manuals\YEAR_BY_YEAR_TINY_DIFFERENCES.txt",
    r"G:\Users\trmra\OneDrive\Desktop\CE_Lancer_4G15_12V_MPI_Workshop_Book.pdf",
)


def seed_local_extracts(state):
    """Pull known AU CE 4G15 extracts into local memory if none are loaded."""
    if state.get("local_extracts"):
        return 0
    added = 0
    seen = set()
    for path in SEED_EXTRACT_CANDIDATES:
        if not path or not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        try:
            ingest_local(state, path)
            added += 1
        except Exception:
            logging.exception("Could not seed local extract %s", path)
        if added >= 3:
            break
    return added


def collection_ids(state):
    """List of collection ids for file_search tool, or empty."""
    cid = (state.get("collection_id") or "").strip()
    return [cid] if cid else []


def stats(state):
    state = state or default_state()
    cid = state.get("collection_id") or "none"
    n = len(state.get("documents") or [])
    n_local = len(state.get("local_extracts") or [])
    names = ", ".join(d.get("name", "?") for d in (state.get("documents") or [])[-4:])
    if n > 4:
        names += f" (+{n - 4} more)"
    extra = f"  ·  {n_local} local extracts" if n_local else ""
    return (
        f"Manuals: {n} cloud docs  ·  collection {cid}"
        + extra
        + (f"  ·  {names}" if names else "")
    )


def format_for_prompt(state):
    """Short system-prompt note when manuals are available."""
    state = state or default_state()
    docs = state.get("documents") or []
    extracts = state.get("local_extracts") or []
    cid = state.get("collection_id")
    bits = []
    if cid:
        names = [d.get("name") for d in docs if d.get("name")]
        if names:
            listing = ", ".join(names[-12:])
            bits.append(
                f"Workshop manuals are available via collections search "
                f"({len(names)} files: {listing}). Prefer those for factory specs."
            )
        else:
            bits.append(
                f"A manuals collection is linked ({cid}). Search it for factory specs."
            )
    if extracts:
        bits.append(
            f"{len(extracts)} local manual extract(s) are in memory below. "
            "Use them when the question is about AU CE 4G15 specs or procedures."
        )
        budget = LOCAL_PROMPT_BUDGET
        for ext in extracts[-6:]:
            if budget <= 200:
                break
            name = ext.get("name") or "manual"
            body = (ext.get("text") or "").strip()
            if not body:
                continue
            chunk = body[: min(1800, budget)]
            bits.append(f"--- local extract: {name} ---\n{chunk}")
            budget -= len(chunk)
    if not bits:
        return (
            "No workshop manuals are loaded yet. Spec questions may use web search; "
            "live readings remain ground truth for this car right now."
        )
    bits.append("Still treat live MUT-II readings as ground truth for the car right now.")
    return "\n".join(bits)
