# CLAUDE.md — 4G1 Live (commercial / non-AI)

Windows Python app: Mitsubishi MUT-II live diagnostics via **Tactrix OpenPort J2534**.

## Quality bar

- This is the **commercial baseline**. Prefer minimal, careful changes.
- **Do not** mix in AI-fork files from `4G1-Live-AI` unless the user explicitly wants a back-port.
- J2534 driver is **32-bit** — use 32-bit Python for run/test when applicable.
- Evidence before “done”: run tests if present; smoke-launch the app when UI changes.

## What this is

Live MUT-II dashboard over J2534 (`j2534.py`), vehicle profiles, gauges, session logging. Sibling **`4G1-Live-AI`** is a personal fork with Grok/AI layers — keep them separate.

## Stack

| Item | Detail |
|------|--------|
| Language | Python |
| UI | tkinter (`app.py`) |
| Device | `j2534.py` + OpenPort / op20 |
| Packaging | `build-release.ps1`, `4G1Live.spec`, `installer.iss` |

## Commands

```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\4G1-Live"
# Prefer 32-bit Python for J2534:
py -3.13-32 app.py
# tests if present:
py -3.13-32 -m pytest tests/ -q
```

## Do not

- Edit `4G1-Live-AI` from this folder (or vice versa) without being asked
- Break release/signing scripts casually
- Hardcode secrets into the product tree
