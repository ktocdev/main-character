# SPDX-License-Identifier: AGPL-3.0-or-later
"""The demo journal's script, for the local server.

Whatever a visitor types in the demo journal, the next scripted message is
what gets sent (static/js/demo-script.js swaps it in the box), and its reply
was written for it (mock_fixtures/demo_script.json). This is the server's
half: it knows where the visitor is in the script and which reply a send
gets. The published web demo has the same logic in the page
(static/js/web-demo/backend.js), which keeps the two in step: change one,
change the other.

Only a demo journal on canned replies uses it (`active()`). The position
lives in the demo's own sessions dir, so the rebuild that opens every trip
into the demo (seed_corpus/reset_demo_state.py) starts it over.
"""
import json
import threading
from pathlib import Path

import config

SCRIPT_PATH = Path(__file__).parent / "mock_fixtures" / "demo_script.json"

# Past the end of a script, every send gets this. Locally there is nothing
# to refresh: the demo starts over on the next trip into it.
END = ("That's the end of the demo script. To start it again, go back to "
       "your own journal and open the demo journal from Settings once more.")
END_HINT = "that's the end of the demo script"
NOTICE = ("Type anything and press send. Your text is swapped for the next "
          "entry in Jordan's week, and the reply was written for that entry. "
          "The box shows what's coming next. Nothing here is kept: the demo "
          "journal starts fresh each time you open it.")

_LOCK = threading.Lock()
_SCRIPT: dict | None = None


def active() -> bool:
    """The demo journal on canned replies. Never the author's own journal."""
    import os
    return (os.getenv("MC_SEED_INSTANCE", "").strip() == "1"
            and config.MOCK_MODE)


def _script() -> dict:
    global _SCRIPT
    if _SCRIPT is None:
        _SCRIPT = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    return _SCRIPT


def _state_path() -> Path:
    return Path(config.SESSION_DIR) / "demo_script_state.json"


def _load() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return {"write_at": int(state.get("write_at", 0)),
            "lookup_at": int(state.get("lookup_at", 0)),
            "closes": int(state.get("closes", 0))}


def _save(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _next_entry(i: int) -> int:
    """The first entry step at or after `i` (the script's length when none is left)."""
    write = _script()["write"]
    while i < len(write) and write[i]["send"] != "entry":
        i += 1
    return i


def public(instance: str) -> dict:
    """What the page needs to swap and pre-fill: the texts, never the replies."""
    s, state = _script(), _load()
    return {
        "write": [{"send": w["send"], "text": w["text"]} for w in s["write"]],
        "lookup": [q["text"] for q in s["lookup"]],
        "chips": [c["text"] for c in s["chips"]],
        "write_at": state["write_at"], "lookup_at": state["lookup_at"],
        "end_hint": END_HINT,
        "notice": {"eyebrow": "demo journal", "body": NOTICE, "key": instance},
    }


def take_write(send: str, text: str, no_reply: bool = False) -> dict | None:
    """The write step a send answers to, if it is one, and the script moves
    past it. A no-reply entry also skips that entry's follow-ups: there is no
    reply for them to follow up on. A no-reply save with no entry left ends
    the write script."""
    write = _script()["write"]
    with _LOCK:
        state = _load()
        at = state["write_at"]
        i = _next_entry(at) if send == "entry" and no_reply else at
        step = write[i] if i < len(write) else None
        if step and step["send"] == send and step["text"] == text:
            state["write_at"] = _next_entry(i + 1) if no_reply else i + 1
            _save(state)
            return step
        if no_reply and _next_entry(at) >= len(write) and at < len(write):
            state["write_at"] = len(write)
            _save(state)
        return None


def write_reply(step: dict | None) -> str | None:
    """The reply a write send gets: its step's, or the end message once the
    script is over. None leaves it to the canned replies (a send that went
    around the swap)."""
    if step:
        return step["reply"]
    return END if _load()["write_at"] >= len(_script()["write"]) else None


def lookup_reply(text: str) -> str | None:
    """A suggestion chip is answered as itself, any number of times, without
    moving the script; after a close it gets its after-close answer."""
    s = _script()
    with _LOCK:
        state = _load()
        chip = next((c for c in s["chips"] if c["text"] == text), None)
        if chip:
            return (state["closes"] and chip.get("reply_after")) or chip["reply"]
        at = state["lookup_at"]
        if at < len(s["lookup"]) and s["lookup"][at]["text"] == text:
            state["lookup_at"] = at + 1
            _save(state)
            return s["lookup"][at]["reply"]
        return END if at >= len(s["lookup"]) else None


def after_close() -> None:
    """The new chapter starts blank, so an answer to a question asked in the
    old one would make no sense: the next send is the next scripted entry."""
    with _LOCK:
        state = _load()
        state["write_at"] = _next_entry(state["write_at"])
        state["closes"] += 1
        _save(state)
