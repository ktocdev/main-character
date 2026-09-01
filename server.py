"""
RAG Journal — local web UI.

A lightweight FastAPI server wrapping the companion (chat + write) and the
entity graph (browse + curate). Single user, local only. This is the
"initial functional UI" from roadmap Phase 4 — the Vue rebuild replaces
the frontend later; the API layer carries over.

Usage:
    .venv\\Scripts\\python.exe server.py
    -> open http://127.0.0.1:8144
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import caps
import categories
import companion
import config
import entities
import metering
import sessions
from config import HOST, PORT, MOCK_MODE, get_client
from config import DATE_FORMAT, date_style, parse_stamp, now_local, stamp as _now_stamp, zone_name
from rag_journal import get_collection

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="RAG Journal")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Local-only, no-auth is a deliberate design choice — it only holds if the
# server refuses requests that aren't actually local. Without this, DNS
# rebinding (an attacker hostname resolved to 127.0.0.1) makes every request
# same-origin, defeating the browser's own CORS protection. The Origin check
# also doubles as the CSRF mitigation for the destructive POST routes.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def same_origin_only(request, call_next):
    origin = request.headers.get("origin")
    if origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
        return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def revalidate_static(request, call_next):
    # StaticFiles sends ETag/Last-Modified but no Cache-Control, so browsers
    # heuristically reuse cached JS/CSS on a soft reload — which serves stale
    # UI after an edit. "no-cache" forces a revalidation every load; unchanged
    # files still come back as a fast 304, changed files always come through.
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

STATE = {
    "client": None,
    "collection": None,
    "entity_index": {},
    "messages": [],  # the journal companion conversation (the open session)
    "lookup": [],    # the chat screen: an information tool, never journal data
}


@app.on_event("startup")
def startup():
    STATE["client"] = get_client()
    STATE["collection"] = get_collection()
    STATE["entity_index"] = companion.load_entity_index()
    # the open session survives restarts — rebuild the conversation from it
    STATE["messages"] = sessions.conversation_messages()


class ChatIn(BaseModel):
    message: str


class EntryIn(BaseModel):
    text: str
    dream: bool = False  # user-flagged; dreams go to the dream realm
    # when the entry was written, "YYYY-MM-DD HH:MM". The client stamps it
    # on focus and the user may edit it before saving; absent or malformed
    # falls back to now, so an older client keeps working.
    ts: str | None = None
    # save without a companion reply (item 3): store the entry, make no Claude
    # call at all. Defaults False so an older client's entries still get a reply.
    no_reply: bool = False


class MergeIn(BaseModel):
    source: str
    target: str


class NameIn(BaseModel):
    name: str


class RetypeIn(BaseModel):
    name: str
    new_type: str
    new_name: str = ""


class AliasIn(BaseModel):
    name: str
    add: str = ""
    remove: str = ""


class KindIn(BaseModel):
    kind: str


class ObservationIn(BaseModel):
    action: str  # edit | delete | reassign
    file: str
    group: str
    ent_index: int
    obs_index: int
    text: str = ""
    target_kind: str = ""
    target_name: str = ""


class ReviewedIn(BaseModel):
    name: str
    reviewed: bool


class DismissDupIn(BaseModel):
    kind: str
    a: str
    b: str


class GroupIn(BaseModel):
    name: str
    parent: str = ""


class GroupMemberIn(BaseModel):
    group: str
    entity: str
    remove: bool = False


class GroupMembersIn(BaseModel):
    group: str
    entities: list[str]
    parent: str = ""


class GroupEditIn(BaseModel):
    name: str
    rename: str = ""
    parent: str | None = None  # None = unchanged, "" = make root
    delete: bool = False
    rollup: bool | None = None  # None = unchanged; collapse members out of the flat list


class CategoryTagIn(BaseModel):
    key: str      # conversation cache key from the category index
    name: str     # category name
    present: bool


class CategoryBuildIn(BaseModel):
    force: bool = False


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


# Which process is answering. The restart poll cannot use "the server
# responded" as proof the new one is up: uvicorn keeps serving while it
# drains, so the first poll is routinely answered by the process on its way
# out, and the page then reloads into a closing socket. The client waits for
# this value to change instead. PID alone would do on a single machine, but
# it can be reused -- the start time is what makes it unambiguous.
INSTANCE_ID = f"{os.getpid()}-{time.time_ns()}"


@app.get("/api/status")
def status():
    return {
        # see INSTANCE_ID: the restart poll watches this, not the status code
        "instance": INSTANCE_ID,
        "entries": STATE["collection"].count(),
        "entities": len(STATE["entity_index"]),
        "conversation_turns": len(STATE["messages"]) // 2,
        # drives the UI banner — a canned reply must never be mistaken for
        # a real one
        "mock": MOCK_MODE,
        # ...and a demo journal must never be mistaken for the author's own.
        # The seed instance is also mock, so the UI shows one bar for both:
        # its wording covers the canned replies, and the fact that has to
        # land is that the entries belong to someone else.
        "seed_instance": SEED_INSTANCE,
        # the server owns the clock; the client stamps against this
        "now": _now_stamp(),
        "tz": zone_name(),
        # a strftime format can't be handed to JS — send the one bit the
        # client actually branches on. config owns the mapping so the
        # Settings picker and this route can't disagree about a format.
        "date_style": date_style(),
    }


# ---------------------------------------------------------------------------
# SPEND GUARDS
# ---------------------------------------------------------------------------
# `caps.check()` runs inside the client proxy, so no call site can slip past
# it. These add a second check at the top of the routes that stream, for one
# reason: a StreamingResponse has already sent its status line by the time its
# generator runs, so a refusal raised in there arrives as a 200 that stops
# mid-sentence. Checking first is what turns it into a 429 the page can read.


def _refused(exc) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=429)


def _too_long(text: str):
    """A 413 for input past config.MAX_INPUT_CHARS, or None.

    Refusing beats truncating: an entry silently cut in half is writing the
    author believes is saved and will not read again until it matters.
    """
    # 0 turns the limit off, same convention as the spend caps: a blank means
    # "use the default above", but an explicit 0 has to be reachable as "no
    # ceiling at all", or MC_MAX_INPUT_CHARS=0 -- read that way everywhere
    # else in this file -- would instead reject every non-empty submission.
    if not config.MAX_INPUT_CHARS or len(text) <= config.MAX_INPUT_CHARS:
        return None
    return JSONResponse(
        {"error": f"that is {len(text):,} characters, past the "
                  f"{config.MAX_INPUT_CHARS:,} this journal accepts in one "
                  f"go. Nothing was saved -- split it and send it in pieces."},
        status_code=413)


@app.post("/api/chat")
def chat(body: ChatIn):
    oversize = _too_long(body.message)
    if oversize:
        return oversize
    try:
        caps.check()
    except caps.CapExceeded as exc:
        return _refused(exc)

    def gen():
        sessions.append_message("you", body.message, collection=STATE["collection"])
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], body.message,
        )
        # no `when` here on purpose: a chat turn carries no user-supplied
        # stamp — only a write-mode entry can be backdated — so the reply
        # is stamped at the moment it was actually generated.
        sessions.append_message("companion", STATE["messages"][-1]["content"])
    return StreamingResponse(_metered_stream(gen()),
                             media_type="text/plain; charset=utf-8")


@app.post("/api/lookup")
def lookup(body: ChatIn):
    """The chat screen: pull information out of the journal. Its own
    conversation, separate from the journal companion — lookups never
    join the open session and never become journal memory."""
    oversize = _too_long(body.message)
    if oversize:
        return oversize
    try:
        caps.check()
    except caps.CapExceeded as exc:
        return _refused(exc)

    def gen():
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["lookup"], body.message,
        )
    return StreamingResponse(_metered_stream(gen()),
                             media_type="text/plain; charset=utf-8")


@app.post("/api/lookup/reset")
def reset_lookup():
    STATE["lookup"] = []
    return {"ok": True}


# ---- close-pipeline progress (Phase 2 item 1) ----
# The post-close pipeline runs as background tasks, so /api/sessions/close
# returns before any of it has started. The client polls the record below to
# show which stage is running instead of one static "closed" line. The two
# tasks run in the order they are queued -- seed first, then the refresh -- so
# the steps are listed in that order. Progress is what the mock-mode per-call
# delays exist to make visible; against a real key each stage is genuinely long.
CLOSE_STEPS = [
    ("seed", "writing your seed summary candidate"),
    ("categories", "tagging the entry"),
    ("entities", "extracting people, places and projects"),
    ("summaries", "refreshing weekly arcs and summaries"),
    ("dreams", "scanning for dreams"),
]
_CLOSE = {"active": False, "running": None, "done": set(), "failed": None}
_CLOSE_LOCK = threading.Lock()


def _close_begin():
    """Reset the record before the tasks are queued, so a poll never shows the
    previous close's finished state as if it were this one's."""
    with _CLOSE_LOCK:
        _CLOSE.update(active=True, running=None, done=set(), failed=None)


def _close_finish():
    with _CLOSE_LOCK:
        _CLOSE["active"] = False
        _CLOSE["running"] = None


@contextmanager
def _close_step(key: str):
    with _CLOSE_LOCK:
        _CLOSE["running"] = key
    try:
        yield
    except Exception:
        with _CLOSE_LOCK:
            _CLOSE["failed"] = key
        raise
    else:
        with _CLOSE_LOCK:
            _CLOSE["done"].add(key)
    finally:
        with _CLOSE_LOCK:
            if _CLOSE["running"] == key:
                _CLOSE["running"] = None


def _after_close_refresh():
    """Full memory pipeline after a chat closes: the closed chat is now a
    journal entry. Tag it, extract its entities, refresh arcs + domain
    docs + entry summaries, scan it for dreams, re-sync embeddings.
    Everything is incremental — cached work is skipped.

    This is the last of the two close tasks, so it clears the progress
    `active` flag when it finishes however it exits — the client stops polling
    on that flag, so a missed clear would poll forever."""
    import dreams
    import summarizer
    try:
        with _close_step("categories"):
            categories.build(quiet=True)
        with _close_step("entities"):
            STATE["entity_index"] = entities.build(quiet=True)
        with _close_step("summaries"):
            summarizer.build(quiet=True)
        with _close_step("dreams"):
            dreams.extract(quiet=True)
    except Exception as e:
        print(f"  post-close refresh failed: {e}")
    finally:
        _close_finish()


def _after_close_seed(archive_key: str):
    """Integrate-at-close: fold the just-archived braid into the live seed
    and write the candidate for download/review. Never touches the seed."""
    import seed
    try:
        with _close_step("seed"):
            path = seed.generate_candidate(archive_key)
        print(f"  seed candidate -> {path.name}")
    except Exception as e:
        print(f"  seed candidate failed: {e}")


@app.post("/api/entry")
def write_entry(body: EntryIn, background_tasks: BackgroundTasks):
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "empty entry"}, status_code=400)
    oversize = _too_long(text)
    if oversize:
        return oversize

    when = parse_stamp(body.ts) if body.ts else None

    # No-reply save (item 3): the entry is stored and the companion is never
    # called, so there is no spend to check against and nothing to refuse --
    # writing still works when a cap that only gates model calls is hit. It
    # still becomes journal memory at the next close, exactly like a replied-to
    # entry. Dreams keep their realm ingest (memory, not the companion voice).
    if body.no_reply:
        if body.dream:
            import dreams
            entry_id = dreams.store_dream_entry(text, when=when)
            sessions.append_message("you", text, dream=True,
                                    collection=STATE["collection"], when=when)
            _tracked(background_tasks, dreams.ingest_dream_entry, text, entry_id, when)
        else:
            entry_id = "current-chat"
            sessions.append_message("you", text, collection=STATE["collection"],
                                    when=when)
            sessions.backup_entry_text(text, when=when)
        return {"ok": True, "entry_id": entry_id, "no_reply": True}

    # Before anything is written. Saving the entry and then refusing the reply
    # would leave the journal holding an entry the author was told failed --
    # and the composer restores the draft on failure, so refusing here loses
    # nothing.
    try:
        caps.check()
    except caps.CapExceeded as exc:
        return _refused(exc)

    # what the reply stream owes back if it dies before its background tasks
    releases = []

    if body.dream:
        import dreams
        entry_id = dreams.store_dream_entry(text, when=when)
        sessions.append_message("you", text, dream=True,
                                collection=STATE["collection"], when=when)
        entry_message = (
            "The following is a dream I just had — I'm flagging it as a "
            "dream, not a waking event. Respond to it as my companion: "
            "receive it, don't decode it with generic symbolism.\n\n" + text
        )
        releases.append(
            _tracked(background_tasks, dreams.ingest_dream_entry, text, entry_id, when))
    else:
        # the entry joins the open session; it becomes journal memory
        # when the chat is closed (the summarize point)
        entry_id = "current-chat"
        sessions.append_message("you", text, collection=STATE["collection"],
                                when=when)
        sessions.backup_entry_text(text, when=when)
        entry_message = (
            "The following is a new journal entry I just wrote — not a question. "
            "Respond to it as my companion.\n\n" + text
        )

    def gen():
        try:
            yield from companion.stream_reply(
                STATE["client"], STATE["collection"], STATE["entity_index"],
                STATE["messages"], entry_message, include_dreams=body.dream,
            )
            sessions.append_message("companion", STATE["messages"][-1]["content"],
                                    when=when)
        except BaseException:
            # the response ends here, so its background tasks never run --
            # hand back what they were counted for (see _tracked)
            for release in releases:
                release()
            raise
    return StreamingResponse(
        _metered_stream(gen()),
        media_type="text/plain; charset=utf-8",
        headers={"X-Entry-Id": entry_id},
    )


@app.get("/api/dreams")
def dream_index():
    import dreams
    index = dreams.load_index()
    return {**index, "weather": dreams.dream_weather()}


@app.post("/api/dreams/extract")
def extract_dreams(body: CategoryBuildIn):
    """Scan the journal for dreams (cached per conversation; incremental)."""
    import dreams
    try:
        dreams.extract(force=body.force, quiet=True)
    except caps.CapExceeded as exc:
        return _refused(exc)
    index = dreams.load_index()
    return {**index, "weather": dreams.dream_weather()}


@app.post("/api/reflect")
def reflect():
    """The companion opens the conversation: connects dots across time."""
    try:
        caps.check()
    except caps.CapExceeded as exc:
        return _refused(exc)

    def gen():
        yield from companion.stream_reflection(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"],
        )
        sessions.append_message("companion", STATE["messages"][-1]["content"],
                                collection=STATE["collection"])
    return StreamingResponse(_metered_stream(gen()),
                             media_type="text/plain; charset=utf-8")


class SeedIn(BaseModel):
    messages: list[dict] = []


class CloseIn(BaseModel):
    title: str = ""


@app.get("/api/sessions")
def list_sessions():
    """The history sidebar: open session + every closed chat, newest first."""
    return sessions.session_list(STATE["collection"])


@app.get("/api/sessions/current")
def current_session():
    """The open session: base conversation text + the live braid."""
    return sessions.current_view(STATE["collection"])


@app.get("/api/sessions/archive")
def archived_session(id: str):
    """One closed session: stitched parts + the full braid, each part
    carrying its entry summary when the pipeline has written one."""
    archive = sessions.load_archive(id)
    if archive is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    import summarizer
    for part in archive.get("parts", []):
        key = entities.conversation_cache_key(part)
        part["summary"] = summarizer.load_entry_summary(key)
    return archive


@app.get("/api/sessions/conversation")
def conversation_view(title: str, start: str = "", end: str = ""):
    """One imported conversation as its stitched per-day parts (braid or
    text, each with its entry summary), bounded to [start, end] so days
    already covered by the open session or an archive stay out."""
    col = STATE["collection"]
    data = col.get(include=["metadatas"])
    dates = sorted({
        m.get("date", "") for m in data["metadatas"]
        if m.get("title", "") == title
        and (not start or m.get("date", "") >= start)
        and (not end or m.get("date", "") <= end)
    })
    if not dates:
        return JSONResponse({"error": "not found"}, status_code=404)
    import summarizer
    parts = []
    for d in dates:
        part = sessions._part_content(col, {"date": d, "title": title})
        key = entities.conversation_cache_key(part)
        part["summary"] = summarizer.load_entry_summary(key)
        parts.append(part)
    return {"title": title, "start": dates[0], "end": dates[-1], "parts": parts}


@app.post("/api/sessions/seed")
def seed_session(body: SeedIn):
    """One-time migration of the browser's old localStorage chat log
    into the open session. Only fills an empty session."""
    n = sessions.seed_messages(body.messages, collection=STATE["collection"])
    return {"ok": True, "seeded": n}


@app.post("/api/sessions/close")
def close_session(body: CloseIn, background_tasks: BackgroundTasks):
    """The summarize point: the user's side of the chat becomes a journal
    entry, the braid is archived, a fresh chat opens, and the full memory
    pipeline runs in the background."""
    try:
        result = sessions.close_session(
            STATE["collection"], STATE["client"], title_hint=body.title,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    STATE["messages"] = []
    # First, before the pipeline is queued. The pipeline is arguably the
    # closing session's cost -- it happened because of those entries -- but
    # billing it there means resetting after it finishes, and the accumulator
    # is one process-global counter: everything the *new* session spends while
    # the pipeline runs would be zeroed along with it, and until then
    # `/api/cost` would show the closed session's figure labelled "this
    # session". Attribution is a display nicety; losing spend the user just
    # incurred is a wrong number. So the new session starts at zero now and
    # wears the pipeline's processing cost.
    metering.reset()
    _close_begin()   # arm the progress record before the tasks are queued (item 1)
    _tracked(background_tasks, _after_close_seed, result["key"])
    _tracked(background_tasks, _after_close_refresh)
    return {"ok": True, **result}


@app.get("/api/sessions/close/progress")
def close_progress():
    """Which stage of the post-close memory pipeline is running (item 1).

    The client polls this after a close and stops when `done` goes true. A
    step is pending until it starts, running while it does, then done or
    failed; a failed step doesn't stall the readout — the remaining steps
    still run and the client still finishes."""
    with _CLOSE_LOCK:
        running, done, failed = _CLOSE["running"], set(_CLOSE["done"]), _CLOSE["failed"]
        active = _CLOSE["active"]
    steps = []
    for key, label in CLOSE_STEPS:
        if key in done:
            status = "done"
        elif key == failed:
            status = "failed"
        elif key == running:
            status = "running"
        else:
            status = "pending"
        steps.append({"key": key, "label": label, "status": status})
    return {"active": active, "steps": steps, "done": not active}



# ---------------------------------------------------------------------------
# COST
# ---------------------------------------------------------------------------


@app.get("/api/cost")
def cost():
    """What the open session has spent so far.

    Read on demand -- both cost views are collapsed by default (Phase 2 item
    8), so this is fetched when one is opened rather than polled. There is
    nothing to subscribe to: the number only moves when a call the user just
    triggered comes back.

    `estimated` is not decoration. These are list prices from a hand-kept
    table, computed from token counts, and the UI has to be able to say so
    rather than presenting a figure that looks like a statement.
    """
    import config
    totals = metering.totals()
    return {
        **totals,
        "models": {
            "companion": config.MC_COMPANION_MODEL,
            "processing": config.MC_PROCESSING_MODEL,
        },
        "labels": {m: config.MODEL_LABELS.get(m, m)
                   for m in (config.MC_COMPANION_MODEL,
                             config.MC_PROCESSING_MODEL)},
        "estimated": True,
        # In mock mode this figure still climbs -- the meter counts canned
        # calls so the cost view stays developable -- but no real tokens were
        # spent. The popover says so rather than showing a dollar figure that
        # looks like it left the account. (The monthly ledger, by contrast, is
        # never written in mock mode; see caps.record_spend.)
        "mock": config.MOCK_MODE,
    }


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
# A UI over .env, not a second store. Everything here edits the same file a
# cloner would otherwise hand-edit, and the app reads it once at import — so
# a save takes effect on the next start, which is why the responses carry
# `restart_required` rather than pretending the change is live.

# The only keys a settings save may touch. This is a whitelist, not a filter
# on obviously-bad names: without it the route is an arbitrary-environment
# write, and the single most valuable thing to write is ANTHROPIC_BASE_URL —
# point that at a host you control and every subsequent call ships the user's
# key and their journal to you. Allowing the key itself to be *written* is
# what makes rotation work; the whitelist is what stops BASE_URL riding along
# beside it.
SETTINGS_KEYS = {
    "MC_DATE_FORMAT", "MC_TIMEZONE", "MC_LANGUAGE",
    "MC_COMPANION_MODEL", "MC_COMPANION_EFFORT", "MC_PROCESSING_MODEL",
    "MC_MAX_SESSION_SPEND", "MC_MAX_MONTHLY_SPEND",
    "ANTHROPIC_API_KEY",
}

# Written, never read back. GET returns whether one is set, never the value
# and never a masked suffix — a suffix is enough to confirm a guess.
WRITE_ONLY_KEYS = {"ANTHROPIC_API_KEY"}


def _available_timezones() -> list[str]:
    """Zones this machine can actually resolve, sorted.

    Read from the installed database rather than a curated list so the picker
    can only offer zones that will work. When it comes back nearly empty the
    tz database is missing (zoneinfo ships none on Windows — that is what
    `tzdata` in requirements.txt is for), and an honest short list beats a
    long one where most entries silently fall back to the server's own zone.
    """
    try:
        from zoneinfo import available_timezones
        return sorted(available_timezones())
    except Exception:
        return []


@app.get("/api/settings")
def get_settings():
    """Current settings, plus the option lists the pickers derive from."""
    import config
    from env_file import read_env
    stored = read_env()
    zones = _available_timezones()
    # Two different truths, and the UI needs both. `values` is what the file
    # says, so a save is visibly persisted; `active` is what this process
    # loaded at import and is still running on. They differ exactly between a
    # save and the next restart, and that gap is what the UI must show --
    # reporting only `active` made a successful save look like a no-op.
    active = {
        "MC_DATE_FORMAT": config.DATE_FORMAT,
        "MC_TIMEZONE": config.TIMEZONE,
        "MC_LANGUAGE": config.LANGUAGE,
        "MC_COMPANION_MODEL": config.MC_COMPANION_MODEL,
        "MC_COMPANION_EFFORT": config.MC_COMPANION_EFFORT,
        "MC_PROCESSING_MODEL": config.MC_PROCESSING_MODEL,
    }
    # Deliberately not in `active`: nothing in the process reads the spend
    # caps yet (Phase 2 item 10 is what will enforce them), so there is no
    # running value for the file to disagree with. Reporting one would let
    # the UI claim a cap is in effect when nothing checks it.
    # not `caps`: that name is the module holding the ceilings themselves,
    # and shadowing it here cost six tests one afternoon
    cap_values = {k: stored.get(k, "")
                  for k in ("MC_MAX_SESSION_SPEND", "MC_MAX_MONTHLY_SPEND",
                            # retired: it was a token count, and nothing reads
                            # it now. Returned so the pane can say so -- a key
                            # sitting in .env doing nothing is exactly how
                            # someone ends up believing they have a ceiling.
                            "MC_MAX_SESSION_TOKENS")}
    return {
        "values": {**{k: stored.get(k, v) for k, v in active.items()},
                   **cap_values},
        "active": active,
        "options": {
            "date_formats": [
                {"value": f, "style": s,
                 "example": now_local().strftime(f)}
                for f, s in config.DATE_FORMATS.items()
            ],
            "timezones": zones,
            "languages": [{"value": v, "label": l}
                          for v, l in config.LANGUAGES.items()],
            # One lineup, both pickers -- nothing is restricted by bucket.
            # `efforts` travels with each model so the effort picker can
            # repopulate from the selection without a second round trip, and
            # an empty list is meaningful: that model takes no effort at all.
            "models": [
                {"value": m,
                 "label": config.MODEL_LABELS.get(m, m),
                 "efforts": efforts,
                 "price": config.MODEL_PRICES.get(m),
                 "thinking": config.MODEL_THINKING_SUPPORT.get(m, True)}
                for m, efforts in config.MODEL_EFFORT_LEVELS.items()
            ],
        },
        # Both ceilings, what has been used against them, and the fact that
        # something is now checking. The UI reads `enforced` rather than
        # assuming: it was False for the whole of item 7, and a pane that
        # claims protection it hasn't got is the failure this key exists to
        # prevent.
        "spend_caps_enforced": True,
        "caps": caps.status(),
        # what the clock is actually doing, which is not always what
        # MC_TIMEZONE says — see config.zone_name
        "resolved_timezone": zone_name(),
        "tz_database": bool(zones),
        "api_key_set": bool(stored.get("ANTHROPIC_API_KEY")
                            or os.getenv("ANTHROPIC_API_KEY")),
    }


class SettingsIn(BaseModel):
    values: dict[str, str] = {}


def _validate_settings(values: dict) -> dict[str, str]:
    """Whitelist, then check each value against what actually works.

    Rejecting the whole save on one bad field is deliberate: a partial write
    would leave the file in a state the user never chose and the UI never
    showed them.
    """
    import config
    from env_file import read_env
    unknown = sorted(set(values) - SETTINGS_KEYS)
    if unknown:
        raise ValueError(f"not a setting: {', '.join(unknown)}")

    clean: dict[str, str] = {}
    for key, raw in values.items():
        value = ("" if raw is None else str(raw)).strip()

        if key == "MC_DATE_FORMAT" and value and value not in config.DATE_FORMATS:
            raise ValueError(f"unknown date format: {value}")
        if key == "MC_TIMEZONE" and value:
            zones = _available_timezones()
            if zones and value not in zones:
                raise ValueError(f"unknown time zone: {value}")
            if not zones:
                raise ValueError(
                    "no time zone database is installed, so a zone cannot be "
                    "set — install `tzdata` (it is in requirements.txt)")
        if key == "MC_LANGUAGE" and value and value not in config.LANGUAGES:
            raise ValueError(f"unsupported language: {value}")
        if key == "MC_COMPANION_MODEL" and value                 and value not in config.MODEL_EFFORT_LEVELS:
            raise ValueError(f"unknown model: {value}")
        if key == "MC_PROCESSING_MODEL" and value                 and value not in config.MODEL_EFFORT_LEVELS:
            raise ValueError(f"unknown model: {value}")
        if key == "MC_COMPANION_EFFORT" and value:
            # the model this effort will actually run under: the one in
            # this save if it carries one, else what .env holds. config's
            # constant is frozen at import, so after a model change that
            # hasn't been restarted into yet it names the *old* model -- and
            # would judge the effort against a model nothing will use. GET
            # already reads `values` from the file for the same reason.
            model = str(values.get("MC_COMPANION_MODEL")
                        or read_env().get("MC_COMPANION_MODEL")
                        or config.MC_COMPANION_MODEL).strip()
            allowed = config.MODEL_EFFORT_LEVELS.get(model, [])
            if value not in allowed:
                raise ValueError(
                    f"{model} does not take effort '{value}'"
                    + (f" (try: {', '.join(allowed)})" if allowed else ""))
        if key in ("MC_MAX_SESSION_SPEND", "MC_MAX_MONTHLY_SPEND") and value:
            try:
                if float(value) < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"{key} must be a positive number or blank")
        if key == "ANTHROPIC_API_KEY" and not value:
            # clearing it would lock the app out of every call, and the UI
            # has no way to show what was lost
            raise ValueError("an API key can be replaced, but not cleared here")

        clean[key] = value
    return clean


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    """Write the whitelisted keys to .env, preserving everything else."""
    from env_file import update_env
    if SEED_INSTANCE:
        # .env is one file shared by both instances -- a save made while
        # looking at the demo corpus would land in the real journal's
        # config, not a sandboxed copy of it. Restarting back is the only
        # place a save can honestly go.
        return JSONResponse(
            {"error": "settings can't be changed from the demo journal -- "
                      "restart back to yours first."}, status_code=409)
    try:
        clean = _validate_settings(body.values or {})
        update_env(clean)
        # .env is authoritative for these on the way back up -- see RESTART
        RESTART["keys"].update(clean)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {
        "ok": True,
        # never echo a write-only value back, not even the one just received
        "saved": sorted(k for k in clean if k not in WRITE_ONLY_KEYS),
        "restart_required": True,
    }


# ---------------------------------------------------------------------------
# RESTART
# ---------------------------------------------------------------------------
# A setting is only half a change until the process starts again, because
# config.py reads its values at import and ~10 modules bind those constants
# by name at import too (capture_fixtures.CLIENT_MODULES documents the same
# hazard for get_client). Re-reading config in place would leave every one
# of those stale, so the honest move is to actually start over -- and to do
# it for the author rather than sending them to a terminal.
#
# Two things make this safe rather than merely convenient:
#
#   - Work in flight is refused, never interrupted. Tagging, entities,
#     summaries, dream ingest and seed candidates run as BackgroundTasks
#     *after* the response and cost real API calls; restarting mid-pipeline
#     would throw that away with nothing to show for it. Streaming replies
#     count too (see _metered_stream) -- the tokens are spent by the time
#     the text is on screen. A restart waits for both.
#   - The re-exec drops the keys that were just saved. os.execve keeps the
#     environment and load_dotenv() does not override what is already set,
#     so the new process would otherwise inherit the *old* values from this
#     process's own dotenv load and the restart would look like a no-op --
#     the same failure the settings UI already had once.

# ---------------------------------------------------------------------------
# THE SEED INSTANCE
# ---------------------------------------------------------------------------
# The demo corpus is a different journal, not a mode of this one. It used to
# be reachable only by `seed_corpus/run_demo.sh`, which meant the only way to
# look at it was a terminal -- and the only way to *have* it was to write it
# into the real journal, where retrieval and entity extraction could not tell
# invented people from lived ones. This is the same environment that script
# sets, reachable from Settings, and pointed at its own data dirs.
#
# It is deliberately one-shot: `restart_env()` drops these keys on every
# restart and only puts them back when the seed is asked for by name, so the
# way out is any restart at all. A demo you can wander into and not out of is
# how someone ends up writing a real entry into a sandbox.

SEED_ROOT = Path(__file__).parent / "seed_corpus" / "install"
SEED_ENV = {
    "MC_SEED_INSTANCE": "1",
    # Canned replies and a stand-in author: the corpus exists to be looked at
    # without a key and without spending anything.
    "MC_MOCK": "1",
    "RAG_AUTHOR_NAME": "Jordan",
    "RAG_JOURNAL_DIR": str(SEED_ROOT / "journal_entries"),
    "RAG_CHROMA_DIR": str(SEED_ROOT / "chroma_data"),
    "MC_ENTITY_DIR": str(SEED_ROOT / "entity_graph"),
    "MC_SUMMARY_DIR": str(SEED_ROOT / "summaries"),
    "MC_CATEGORY_DIR": str(SEED_ROOT / "categories"),
    "MC_PATTERN_DIR": str(SEED_ROOT / "patterns"),
    "MC_DREAM_DIR": str(SEED_ROOT / "dreams"),
    "MC_SESSION_DIR": str(SEED_ROOT / "sessions"),
}

SEED_INSTANCE = os.getenv("MC_SEED_INSTANCE", "").strip() == "1"

RESTART = {"requested": False, "keys": set(), "into": "journal"}
SERVER = {"instance": None}
_BUSY = {"count": 0}
_BUSY_LOCK = threading.Lock()


def _tracked(background_tasks: BackgroundTasks, fn, *args):
    """Schedule background work and count it while it runs, so a restart can
    tell whether it would be interrupting the memory pipeline.

    Returns an idempotent release callable. The task calls it for you when it
    finishes, but a caller whose response might never reach its background
    tasks -- a StreamingResponse whose generator raises, or whose reader
    disconnects -- has to call it itself: Starlette runs the tasks only after
    a clean stream, so an unreleased count would sit there forever and make
    /api/restart answer 409 for the life of the process.
    """
    held = {"yes": True}

    def release():
        with _BUSY_LOCK:
            if held["yes"]:
                held["yes"] = False
                _BUSY["count"] -= 1

    def run():
        try:
            fn(*args)
        finally:
            release()

    with _BUSY_LOCK:
        _BUSY["count"] += 1
    background_tasks.add_task(run)
    return release


def _metered_stream(chunks):
    """Hold the busy count for the life of a streaming reply.

    A stream is spending tokens the whole time it runs, so /api/restart has to
    refuse during one for the same reason it refuses during the memory
    pipeline: what a restart would interrupt has already been paid for and
    cannot be recovered. Only the dream path held a count before -- and only
    for its background ingest -- which left the common case, a plain entry or
    a chat turn, looking idle to the restart route while the reply was still
    arriving.

    The count is taken *inside* the generator rather than around it. A
    generator that is never iterated never runs its `finally`, so a count
    taken at call time would leak if a response were built and dropped, and
    an unreleased count makes /api/restart answer 409 for the life of the
    process (see _tracked, which learned this the same way).
    """
    with _BUSY_LOCK:
        _BUSY["count"] += 1
    try:
        yield from chunks
    finally:
        with _BUSY_LOCK:
            _BUSY["count"] -= 1


class RestartIn(BaseModel):
    # "journal" or "seed". Absent means journal, so every existing caller --
    # and every restart that is just a restart -- lands back on real data.
    into: str = "journal"


@app.post("/api/restart")
def restart_server(body: RestartIn | None = None):
    """Exit and come back, so a saved setting takes effect. The client polls
    /api/status until it answers again, then reloads the page.

    `into: "seed"` comes back on the demo corpus instead. Nothing is written
    to .env for it: the destination lives in the child process's environment
    and only there, which is what makes the next restart a way out.
    """
    into = (body.into if body else "journal").strip().lower()
    if into not in ("journal", "seed"):
        return JSONResponse({"error": f"no such journal: {into}"},
                            status_code=400)
    if into == "seed" and not (SEED_ROOT / "chroma_data" / "chroma.sqlite3").exists():
        # Checked for the database file, not just the directory: an
        # interrupted or half-run capture can leave an empty chroma_data/
        # behind, and Path.exists() on the bare directory would call that
        # "installed". Refusing beats booting an empty demo: an author who
        # asked for the demo journal and got a blank one has no way to tell
        # that from a broken one.
        return JSONResponse(
            {"error": "the demo journal is not installed. Run "
                      "`bash seed_corpus/run_capture.sh --wipe` to build it, "
                      "then try again."},
            status_code=409)

    with _BUSY_LOCK:
        busy = _BUSY["count"]
    if busy:
        return JSONResponse(
            {"error": "something is still running -- a companion reply "
                      "still arriving, or the memory pipeline's tagging, "
                      "summaries and seed work. Restarting now would throw "
                      "away work already paid for. Try again in a moment."},
            status_code=409)

    srv = SERVER["instance"]
    if srv is None:
        # started by something other than this file's __main__ (a test, or an
        # external uvicorn). Nothing here can re-exec, and claiming a restart
        # that never happens is worse than refusing one.
        return JSONResponse(
            {"error": "this server cannot restart itself -- restart it the "
                      "way you started it"},
            status_code=501)

    RESTART["requested"] = True
    RESTART["into"] = into
    srv.should_exit = True      # uvicorn drains, run() returns, __main__ execs
    return {"ok": True, "restarting": True, "into": into}

def restart_env() -> dict:
    """The environment the replacement process starts with.

    Two subtractions, one addition:

      - the keys a save just wrote, because `load_dotenv()` does not override
        what is already set and the child would inherit this process's stale
        values instead of reading the file it just changed;
      - every `SEED_ENV` key, *always*, so a seed instance is one restart deep
        and any restart is the way home. This also means a journal started
        from a shell that exported these by hand (`run_demo.sh`) restarts onto
        real data -- which is the same rule stated from the other side, and
        the reason the button exists rather than a second script;
      - then `SEED_ENV` back, only when the seed was asked for by name.
    """
    dropped = RESTART["keys"] | set(SEED_ENV)
    env = {k: v for k, v in os.environ.items() if k not in dropped}
    if RESTART["into"] == "seed":
        env.update(SEED_ENV)
    return env


@app.post("/api/reset")
def reset_conversation():
    STATE["messages"] = []
    return {"ok": True}


class SeedUploadIn(BaseModel):
    text: str


@app.get("/api/seed")
def seed_status():
    """The seed ritual's state: live seed + pending candidate."""
    import seed
    return seed.status()


@app.get("/api/seed/download")
def seed_download(which: str = "current"):
    """Download the live seed (or the post-close candidate) for editing."""
    import seed
    path = seed.CANDIDATE_FILE if which == "candidate" else seed.SEED_FILE
    if not path.exists():
        return JSONResponse({"error": f"no {which} seed yet"}, status_code=404)
    return FileResponse(
        path, media_type="text/markdown", filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/seed/upload")
def seed_upload(body: SeedUploadIn):
    """The upload point: the edited file becomes the live seed (the prior
    seed is backed up; the pending candidate is cleared)."""
    import seed
    try:
        info = seed.save_seed(body.text)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, **info}


# ---------------------------------------------------------------------------
# DATA — export, backup, rebuild
# ---------------------------------------------------------------------------
# The three commands from `export.py`, `backup.py` and `rebuild_index.py`,
# reachable without a terminal. They are the same functions the CLI calls, not
# reimplementations: "you can take your writing and go" is only true if it is
# true of what the button does too.
#
# None of them returns a file. This app serves one person on their own
# machine, the folder is on that machine, and the path is in the response --
# so a download endpoint would add a file-serving surface (and the path
# validation that has to come with it) to save a trip to a folder the author
# can already open.


@contextmanager
def _busy():
    """Hold the restart-refusal count for a synchronous job.

    Rebuilding the index rewrites the store the running process has open, and
    a restart in the middle of it would leave a half-built index behind. The
    streaming routes hold this the same way; see `_metered_stream`.
    """
    with _BUSY_LOCK:
        _BUSY["count"] += 1
    try:
        yield
    finally:
        with _BUSY_LOCK:
            _BUSY["count"] -= 1


def export_has_entries() -> bool:
    import export
    return bool(export.read_entries())


@app.post("/api/data/export")
def data_export():
    """Write the journal out as files. Returns where it went."""
    import export
    dest = export.default_dest()
    with _busy():
        info = export.export_journal(dest)
    if not info["entries"]:
        return JSONResponse({"error": "there are no entries to export yet."},
                            status_code=409)
    return {"ok": True, "path": str(dest), **info}


@app.post("/api/data/backup")
def data_backup():
    """Write a dated zip of that same export."""
    import backup
    dest = backup.default_dest()
    with _busy():
        if not export_has_entries():
            return JSONResponse({"error": "there are no entries to back up yet."},
                                status_code=409)
        info = backup.write_backup(dest)
    return {"ok": True, "path": str(dest), **info}


@app.post("/api/data/rebuild")
def data_rebuild():
    """Rebuild the search index from the journal files. Local embeddings, so
    this costs nothing and needs no key -- but it is the slowest thing in
    Settings by a wide margin on a large journal."""
    import rebuild_index
    with _busy():
        if not export_has_entries():
            return JSONResponse({"error": "there are no entries to index yet."},
                                status_code=409)
        result = rebuild_index.rebuild()
    return {"ok": True, **result}


@app.get("/api/entities")
def list_entities():
    return STATE["entity_index"]


@app.get("/api/entities/doc")
def entity_doc(name: str):
    canonical = companion.resolve_entity(STATE["entity_index"], name)
    if not canonical:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = companion.ENTITY_DIR / STATE["entity_index"][canonical]["path"]
    return {"name": canonical, "doc": path.read_text(encoding="utf-8")}


def _rebuild():
    STATE["entity_index"] = entities.build(quiet=True)


def _snapshot():
    return json.loads(json.dumps(entities.load_curation()))


def _record_curation(description: str, before: dict):
    entities.record_change(description, "curation", before, _snapshot())


def _groups_snapshot():
    return json.loads(json.dumps(entities.load_groups()))


def _record_groups(description: str, before: list):
    entities.record_change(description, "groups", before, _groups_snapshot())


def _combine(body: MergeIn, field: str, verb: str):
    """Shared logic for merge (keeps alias) and correct (no alias)."""
    index = STATE["entity_index"]
    src = companion.resolve_entity(index, body.source)
    if not src:
        return JSONResponse({"error": f"'{body.source}' not found"}, status_code=404)
    dst = companion.resolve_entity(index, body.target) or body.target.strip()
    if src == dst:
        return JSONResponse({"error": "already the same entity — use rename to change spelling"}, status_code=400)

    before = _snapshot()
    curation = entities.load_curation()
    src_kind = index[src]["type"]
    # kind-qualify the target when it exists under a different kind
    # (e.g. merge place:Reyes into person:Dr. Reyes)
    dst_kind = index[dst]["type"] if dst in index else src_kind
    value = f"{dst_kind}:{dst}" if dst_kind != src_kind else dst
    curation[field][entities.curation_key(src_kind, src)] = value
    for other in ("merge", "correct"):
        for k, v in list(curation[other].items()):
            _, tname = entities._parse_target(v, src_kind)
            if tname.lower() == src.lower():
                curation[other][k] = value
    entities.save_curation(curation)
    _record_curation(f"{verb} {src} into {dst}", before)
    _rebuild()
    return {"ok": True, verb: src, "into": dst}


@app.post("/api/entities/merge")
def merge_entities(body: MergeIn):
    return _combine(body, "merge", "merged")


@app.post("/api/entities/correct")
def correct_entity(body: MergeIn):
    return _combine(body, "correct", "corrected")


@app.post("/api/entities/retype")
def retype_entity(body: RetypeIn):
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)
    if body.new_type not in entities.KINDS:
        return JSONResponse({"error": f"kind must be one of {entities.KINDS}"}, status_code=400)

    before = _snapshot()
    curation = entities.load_curation()
    curation["retype"][entities.curation_key(index[name]["type"], name)] = {
        "type": body.new_type,
        "name": body.new_name.strip() or name,
    }
    entities.save_curation(curation)
    _record_curation(f"retype {name} to {body.new_type}", before)
    _rebuild()
    return {"ok": True, "retyped": name, "to": body.new_type}


@app.post("/api/entities/rename")
def rename_entity(body: MergeIn):
    """Rename an entity's display spelling (case-only changes included)."""
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.source)
    if not name:
        return JSONResponse({"error": f"'{body.source}' not found"}, status_code=404)
    new_name = body.target.strip()
    if not new_name or new_name == name:
        return JSONResponse({"error": "nothing to rename"}, status_code=400)

    before = _snapshot()
    curation = entities.load_curation()
    curation["rename"][entities.curation_key(index[name]["type"], name)] = new_name
    entities.save_curation(curation)
    _record_curation(f"rename {name} to {new_name}", before)
    _rebuild()
    return {"ok": True, "renamed": name, "to": new_name}


@app.post("/api/entities/alias")
def alias_entity(body: AliasIn):
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)

    before = _snapshot()
    curation = entities.load_curation()
    key = entities.curation_key(index[name]["type"], name)
    if body.add.strip():
        curation["alias_add"].setdefault(key, [])
        if body.add.strip() not in curation["alias_add"][key]:
            curation["alias_add"][key].append(body.add.strip())
    if body.remove.strip():
        curation["alias_remove"].setdefault(key, [])
        if body.remove.strip() not in curation["alias_remove"][key]:
            curation["alias_remove"][key].append(body.remove.strip())
        curation["alias_add"][key] = [
            a for a in curation["alias_add"].get(key, [])
            if a.lower() != body.remove.strip().lower()
        ]
    entities.save_curation(curation)
    _record_curation(f"alias change on {name}", before)
    _rebuild()
    return {"ok": True}


@app.get("/api/entities/observations")
def entity_observations(name: str):
    index = STATE["entity_index"]
    canonical = companion.resolve_entity(index, name)
    if not canonical:
        return JSONResponse({"error": "not found"}, status_code=404)
    kind = index[canonical]["type"]
    return {
        "name": canonical, "type": kind,
        "observations": entities.list_observations(kind, canonical),
    }


@app.post("/api/observation")
def mutate_observation(body: ObservationIn):
    try:
        raw_path = entities._raw_path(body.file)
        raw_before = raw_path.read_text(encoding="utf-8")
        if body.action == "edit":
            entities.edit_observation(body.file, body.group, body.ent_index, body.obs_index, body.text)
        elif body.action == "delete":
            entities.delete_observation(body.file, body.group, body.ent_index, body.obs_index)
        elif body.action == "reassign":
            entities.reassign_observation(
                body.file, body.group, body.ent_index, body.obs_index,
                body.target_kind, body.target_name,
            )
        else:
            return JSONResponse({"error": "unknown action"}, status_code=400)
    except (FileNotFoundError, IndexError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    entities.record_change(
        f"observation {body.action} in {body.file}", "raw",
        raw_before, raw_path.read_text(encoding="utf-8"), body.file,
    )
    _rebuild()
    return {"ok": True}


@app.get("/api/history")
def history_state():
    return entities.history_peek()


@app.post("/api/undo")
def undo_change():
    description = entities.undo()
    if description is None:
        return JSONResponse({"error": "nothing to undo"}, status_code=400)
    _rebuild()
    return {"ok": True, "undid": description}


@app.post("/api/redo")
def redo_change():
    description = entities.redo()
    if description is None:
        return JSONResponse({"error": "nothing to redo"}, status_code=400)
    _rebuild()
    return {"ok": True, "redid": description}


@app.post("/api/entities/suggest")
def suggest(body: KindIn):
    if body.kind not in entities.KINDS:
        return JSONResponse({"error": f"kind must be one of {entities.KINDS}"}, status_code=400)
    try:
        return {"groups": entities.suggest_merges(body.kind)}
    except caps.CapExceeded as exc:
        return _refused(exc)


@app.post("/api/entities/reviewed")
def mark_reviewed(body: ReviewedIn):
    """Toggle the reviewed flag. Patches the index in place — no rebuild,
    so rapid triage keystrokes stay instant. Not recorded in undo history."""
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)

    key = entities.curation_key(index[name]["type"], name)
    curation = entities.load_curation()
    reviewed = {r.lower() for r in curation["reviewed"]}
    if body.reviewed and key not in reviewed:
        curation["reviewed"].append(key)
    if not body.reviewed:
        curation["reviewed"] = [r for r in curation["reviewed"] if r.lower() != key]
    entities.save_curation(curation)
    index[name]["reviewed"] = body.reviewed
    return {"ok": True, "name": name, "reviewed": body.reviewed}


@app.get("/api/entities/duplicates")
def duplicate_candidates():
    return {"pairs": entities.find_duplicate_candidates()}


@app.post("/api/entities/duplicates/dismiss")
def dismiss_duplicate(body: DismissDupIn):
    curation = entities.load_curation()
    pk = entities.pair_key(body.kind, body.a, body.b)
    if pk not in curation["not_duplicates"]:
        curation["not_duplicates"].append(pk)
    entities.save_curation(curation)
    return {"ok": True}


@app.get("/api/groups")
def list_groups():
    """Entity groups with members resolved through current names/aliases.
    Members that no longer match an entity come back under `unresolved`
    (kept in the file — an undo can bring their entity back)."""
    index = STATE["entity_index"]
    out = []
    for g in entities.load_groups():
        resolved, unresolved = set(), set()
        for m in g["members"]:
            canon = companion.resolve_entity(index, m)
            (resolved if canon else unresolved).add(canon or m)
        out.append({
            "name": g["name"],
            "parent": g["parent"],
            "rollup": bool(g.get("rollup")),
            "members": sorted(resolved, key=str.lower),
            "unresolved": sorted(unresolved, key=str.lower),
        })
    return {"groups": out}


@app.post("/api/groups")
def create_group(body: GroupIn):
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "empty group name"}, status_code=400)
    groups = entities.load_groups()
    if entities.find_group(groups, name):
        return JSONResponse({"error": f"group '{name}' already exists"}, status_code=400)
    parent = body.parent.strip()
    if parent and not entities.find_group(groups, parent):
        return JSONResponse({"error": f"parent group '{parent}' not found"}, status_code=404)
    before = _groups_snapshot()
    groups.append({"name": name, "parent": parent, "members": []})
    entities.save_groups(groups)
    _record_groups(f"create group {name}", before)
    _rebuild()
    return {"ok": True, "group": name}


@app.post("/api/groups/member")
def group_member(body: GroupMemberIn):
    """Add an entity to a group (creating the group if it's new) or,
    with remove=true, drop a member — including dangling unresolved ones."""
    groups = entities.load_groups()
    group = entities.find_group(groups, body.group)

    if body.remove:
        if not group:
            return JSONResponse({"error": f"group '{body.group}' not found"}, status_code=404)
        raw = body.entity.strip().lower()
        canon = companion.resolve_entity(STATE["entity_index"], body.entity)
        keep = [
            m for m in group["members"]
            if m.lower() != raw
            and companion.resolve_entity(STATE["entity_index"], m) != (canon or object())
        ]
        if len(keep) == len(group["members"]):
            return JSONResponse({"error": f"'{body.entity}' is not in {group['name']}"}, status_code=404)
        before = _groups_snapshot()
        group["members"] = keep
        entities.save_groups(groups)
        _record_groups(f"remove {body.entity} from group {group['name']}", before)
        _rebuild()
        return {"ok": True}

    canon = companion.resolve_entity(STATE["entity_index"], body.entity)
    if not canon:
        return JSONResponse({"error": f"entity '{body.entity}' not found"}, status_code=404)
    before = _groups_snapshot()
    created = False
    if not group:
        group = {"name": body.group.strip(), "parent": "", "members": []}
        if not group["name"]:
            return JSONResponse({"error": "empty group name"}, status_code=400)
        groups.append(group)
        created = True
    if any(m.lower() == canon.lower() for m in group["members"]):
        return {"ok": True, "group": group["name"], "member": canon}  # already there
    group["members"] = sorted(group["members"] + [canon], key=str.lower)
    entities.save_groups(groups)
    desc = f"add {canon} to group {group['name']}"
    if created:
        desc += " (new group)"
    _record_groups(desc, before)
    _rebuild()
    return {"ok": True, "group": group["name"], "member": canon}


@app.post("/api/groups/members")
def group_members(body: GroupMembersIn):
    """Batch-add several entities to a group at once, creating the group if
    it's new. One save, one history record — so a single undo reverts the
    whole batch. Names that don't resolve to an entity are reported back."""
    name = body.group.strip()
    if not name:
        return JSONResponse({"error": "empty group name"}, status_code=400)
    groups = entities.load_groups()
    group = entities.find_group(groups, name)
    before = _groups_snapshot()
    created = False
    if not group:
        parent = body.parent.strip()
        if parent and not entities.find_group(groups, parent):
            return JSONResponse({"error": f"parent group '{parent}' not found"}, status_code=404)
        group = {"name": name, "parent": parent, "members": []}
        groups.append(group)
        created = True

    have = {m.lower() for m in group["members"]}
    added, skipped, unresolved = [], [], []
    for raw in body.entities:
        canon = companion.resolve_entity(STATE["entity_index"], raw)
        if not canon:
            unresolved.append(raw)
        elif canon.lower() in have:
            skipped.append(canon)
        else:
            group["members"].append(canon)
            have.add(canon.lower())
            added.append(canon)

    if not added and not created:
        return {"ok": True, "group": group["name"], "added": [],
                "skipped": skipped, "unresolved": unresolved, "created": False}
    group["members"] = sorted(group["members"], key=str.lower)
    entities.save_groups(groups)
    desc = f"add {len(added)} to group {group['name']}"
    if created:
        desc += " (new group)"
    _record_groups(desc, before)
    _rebuild()
    return {"ok": True, "group": group["name"], "added": added,
            "skipped": skipped, "unresolved": unresolved, "created": created}


@app.post("/api/groups/edit")
def edit_group(body: GroupEditIn):
    """Rename / reparent / delete a group. Deleting promotes children to
    the deleted group's parent; renaming rewrites children's pointers."""
    groups = entities.load_groups()
    group = entities.find_group(groups, body.name)
    if not group:
        return JSONResponse({"error": f"group '{body.name}' not found"}, status_code=404)

    # roll-up is a view preference (collapse this group's members out of the
    # flat sidebar list), not journal data — save it directly, no undo entry.
    if body.rollup is not None and not body.delete \
            and not body.rename.strip() and body.parent is None:
        group["rollup"] = bool(body.rollup)
        entities.save_groups(groups)
        return {"ok": True, "group": group["name"], "rollup": group["rollup"]}

    before = _groups_snapshot()
    actions = []

    if body.delete:
        for child in groups:
            if child["parent"].lower() == group["name"].lower():
                child["parent"] = group["parent"]
        groups.remove(group)
        entities.save_groups(groups)
        _record_groups(f"delete group {group['name']}", before)
        _rebuild()
        return {"ok": True}

    rename = body.rename.strip()
    if rename and rename != group["name"]:
        clash = entities.find_group(groups, rename)
        if clash and clash is not group:
            return JSONResponse({"error": f"group '{rename}' already exists"}, status_code=400)
        old = group["name"]
        for child in groups:
            if child["parent"].lower() == old.lower():
                child["parent"] = rename
        group["name"] = rename
        actions.append(f"rename group {old} to {rename}")

    if body.parent is not None:
        parent = body.parent.strip()
        if parent:
            if not entities.find_group(groups, parent):
                return JSONResponse({"error": f"parent group '{parent}' not found"}, status_code=404)
            if parent.lower() == group["name"].lower() or \
                    entities.group_would_cycle(groups, group["name"], parent):
                return JSONResponse({"error": "that would make a loop"}, status_code=400)
        if parent.lower() != group["parent"].lower():
            group["parent"] = parent
            actions.append(f"move group {group['name']} under {parent or 'root'}")

    if not actions:
        return {"ok": True}  # nothing changed
    entities.save_groups(groups)
    _record_groups("; ".join(actions), before)
    _rebuild()
    return {"ok": True, "group": group["name"]}


@app.post("/api/summaries/refresh")
def refresh_summaries():
    """Tag any new entries with categories, then regenerate stale weekly
    arcs, domain documents, and the status snapshot (all incremental).
    Tagging runs first so fresh entries land in their domain docs."""
    import summarizer
    try:
        cat = categories.build(quiet=True)
        result = summarizer.build(quiet=True)
    except caps.CapExceeded as exc:
        return _refused(exc)
    return {"ok": True, **result, "categories_tagged": cat["new"]}


def _fold(s: str) -> str:
    """Lowercase and strip accents, one char at a time so indexes still
    line up with the original text (fiancé matches fiance)."""
    import unicodedata
    out = []
    for c in s.lower():
        d = unicodedata.normalize("NFKD", c)
        out.append(d[0] if d else c)
    return "".join(out)


def _snippet_around(doc: str, q: str, before: int = 150, after: int = 300) -> str:
    """A window of text centered on the first occurrence of any query word,
    so the snippet shows the moment that matched — not the top of the chunk.
    Falls back to the chunk opening when the match is purely semantic."""
    hay = _fold(doc)
    q = _fold(q)
    terms = [q.lower()] + [w for w in q.lower().split() if len(w) > 2]
    idx = -1
    for term in terms:
        found = hay.find(term)
        if found != -1 and (idx == -1 or found < idx):
            idx = found
        if term == q.lower() and found != -1:
            break  # the whole phrase matched — center on that
    if idx == -1:
        return doc[:450] + ("…" if len(doc) > 450 else "")
    start = max(0, idx - before)
    end = min(len(doc), idx + after)
    return ("…" if start else "") + doc[start:end] + ("…" if end < len(doc) else "")


@app.get("/api/search")
def search_journal(q: str, mode: str = "semantic", limit: int = 100):
    """Exhaustive search over the whole waking journal — local embeddings
    and plain text scanning, no API calls. Dreams never appear here: they
    live in their own collection (realm isolation).

    mode=semantic  passages ranked by meaning-similarity to the query
    mode=exact     literal substring matches, newest first, with counts"""
    q = q.strip()
    if not q:
        return JSONResponse({"error": "empty query"}, status_code=400)
    col = STATE["collection"]
    results = []

    if mode == "exact":
        data = col.get(include=["documents", "metadatas"])
        needle = _fold(q)
        merged = {}  # (date, title) -> result; chunks of one entry combine
        for doc, meta in zip(data["documents"], data["metadatas"]):
            hay = _fold(doc)
            if needle not in hay:
                continue
            key = (meta.get("date", ""), meta.get("title", ""))
            if key in merged:
                merged[key]["hits"] += hay.count(needle)
            else:
                merged[key] = {
                    "date": key[0],
                    "title": key[1],
                    "snippet": _snippet_around(doc, q),
                    "hits": hay.count(needle),
                }
        results = sorted(merged.values(), key=lambda r: r["date"], reverse=True)
    else:
        # Rank every chunk, then keep only what's worth reading: literal
        # matches always stay (however far down the ranking), and
        # non-literal neighbors stay only while they're close to the best
        # hit — and never more than a handful. When nothing matches
        # literally, the whole corpus sits inside the margin (weak best
        # hit), so the hard cap is what keeps a no-match query short.
        RELATED_MARGIN = 0.12
        RELATED_MAX = 12
        n = col.count()
        if n:
            r = col.query(query_texts=[q], n_results=n)
            needle = _fold(q)
            best = r["distances"][0][0]
            related_kept = 0
            for doc, meta, dist in zip(
                r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                literal = needle in _fold(doc)
                if not literal:
                    if dist > best + RELATED_MARGIN or related_kept >= RELATED_MAX:
                        continue
                    related_kept += 1
                results.append({
                    "date": meta.get("date", ""),
                    "title": meta.get("title", ""),
                    "snippet": _snippet_around(doc, q),
                    "distance": round(dist, 3),
                    "match": "exact" if literal else "related",
                })
            # direct matches first (best first), then the related tail
            exact_hits = [x for x in results if x["match"] == "exact"][:limit]
            related_list = [x for x in results if x["match"] == "related"]
            results = exact_hits + related_list[:max(0, limit - len(exact_hits))]

    return {"query": q, "mode": mode, "results": results}


@app.get("/api/summaries/domain")
def domain_summary(name: str):
    import summarizer
    doc = summarizer.load_domain_doc(name)
    if doc is None:
        return JSONResponse({"error": "no summary for this domain yet"}, status_code=404)
    return {"name": name, "doc": doc}


@app.get("/api/summaries/entries")
def entry_list():
    """All entries sorted newest first, for the history tab."""
    col = STATE["collection"]
    result = col.get(limit=10000)
    entries = []
    seen = set()
    for doc, meta in zip(result["documents"], result["metadatas"]):
        # skip chunks that are parts of the same entry (same date + title)
        key = (meta.get("date", ""), meta.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "date": meta.get("date", ""),
            "title": meta.get("title", ""),
            "text": doc[:500] + ("…" if len(doc) > 500 else ""),
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return {"entries": entries}


@app.get("/api/patterns")
def get_patterns():
    import patterns
    library = patterns.load_library()
    return {"generated": library["generated"], "patterns": patterns.load_patterns()}


@app.post("/api/patterns/build")
def build_patterns(body: CategoryBuildIn):
    """Detect patterns from the current arcs + domain docs (one Claude
    call; skipped when the inputs haven't changed unless force)."""
    import patterns
    library = patterns.build(force=body.force, quiet=True)
    return {"generated": library["generated"], "patterns": patterns.load_patterns()}


@app.post("/api/patterns/dismiss")
def dismiss_pattern(body: NameIn):
    import patterns
    patterns.dismiss(body.name)
    return {"ok": True, "dismissed": body.name}


class OrganicRespondIn(BaseModel):
    id: str
    action: str  # confirm | dismiss | not_now


class CustomCategoryIn(BaseModel):
    name: str
    keywords: str = ""   # comma-separated


class CustomEditIn(BaseModel):
    name: str
    remove_keyword: str = ""
    remove_member: str = ""


def _rebuild_category_index():
    index = categories.build_index()
    categories.update_chroma(index)
    return index


@app.get("/api/organic")
def organic_state():
    import organic
    data = organic.load_organic()
    return {
        "generated": data["generated"],
        "proposals": [p for p in data["proposals"] if p["status"] == "proposed"],
        "custom": organic.load_custom(),
    }


@app.post("/api/organic/scan")
def organic_scan():
    """Cluster place/project entities by context and propose categories
    (local embeddings + one Claude naming call)."""
    import organic
    organic.scan(quiet=True)
    return organic_state()


@app.post("/api/organic/respond")
def organic_respond(body: OrganicRespondIn):
    import organic
    try:
        proposal = organic.respond(body.id, body.action)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if body.action == "confirm":
        _rebuild_category_index()
    return {"ok": True, "status": proposal["status"]}


@app.post("/api/organic/custom")
def create_custom_category(body: CustomCategoryIn):
    import organic
    keywords = [k.strip() for k in body.keywords.split(",") if k.strip()]
    try:
        cat = organic.add_custom(body.name, keywords=keywords)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _rebuild_category_index()
    return {"ok": True, "category": cat}


@app.post("/api/organic/custom/delete")
def delete_custom_category(body: NameIn):
    import organic
    if not organic.remove_custom(body.name):
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)
    _rebuild_category_index()
    return {"ok": True, "deleted": body.name}


@app.post("/api/organic/custom/edit")
def edit_custom_category(body: CustomEditIn):
    """Remove a single keyword and/or member from a custom category."""
    import organic
    if not organic.remove_from_custom(
        body.name, keyword=body.remove_keyword, member=body.remove_member
    ):
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)
    _rebuild_category_index()
    return {"ok": True}


@app.get("/api/categories")
def category_index():
    return categories.load_index()


@app.post("/api/categories/build")
def build_categories(body: CategoryBuildIn):
    """Tag untagged conversations with Claude (incremental unless force)."""
    try:
        return {"ok": True, **categories.build(force=body.force, quiet=True)}
    except caps.CapExceeded as exc:
        return _refused(exc)


@app.post("/api/categories/tag")
def set_category_tag(body: CategoryTagIn):
    """Manually add/remove a tag on one entry — recorded as an override."""
    try:
        return categories.set_tag(body.key, body.name, body.present)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except caps.CapExceeded as exc:
        return _refused(exc)


@app.get("/api/entry")
def entry_text(date: str, title: str):
    """Full text of one entry, reassembled from its chunks."""
    data = STATE["collection"].get(
        where={"$and": [{"date": {"$eq": date}}, {"title": {"$eq": title}}]},
        include=["documents"],
    )
    if not data["ids"]:
        return JSONResponse({"error": "not found"}, status_code=404)

    def chunk_idx(doc_id):
        m = re.search(r"_c(\d+)$", doc_id)
        return int(m.group(1)) if m else 0

    chunks = sorted(zip(data["ids"], data["documents"]), key=lambda p: chunk_idx(p[0]))
    import summarizer
    key = entities.conversation_cache_key({"date": date, "title": title})
    return {"date": date, "title": title,
            "summary": summarizer.load_entry_summary(key),
            "messages": sessions.load_braid(date, title),
            "text": "\n\n".join(doc for _, doc in chunks)}


@app.post("/api/entities/delete")
def delete_entity(body: NameIn):
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)

    before = _snapshot()
    curation = entities.load_curation()
    curation["delete"].append(entities.curation_key(index[name]["type"], name))
    entities.save_curation(curation)
    _record_curation(f"delete {name}", before)
    _rebuild()
    return {"ok": True, "deleted": name}


if __name__ == "__main__":
    print(f"\n  RAG Journal -> http://{HOST}:{PORT}\n")
    # uvicorn.run() builds this itself and keeps it private; building it
    # here is what gives /api/restart something to set should_exit on.
    SERVER["instance"] = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    SERVER["instance"].run()

    if RESTART["requested"]:
        env = restart_env()
        print("  restarting into the demo journal..." if RESTART["into"] == "seed"
              else "  restarting to apply settings...", flush=True)

        # run() has returned, but the listening socket is not always released
        # by the time the replacement tries to bind. Wait for it rather than
        # hand the author a dead port and a stack trace.
        for _ in range(50):
            probe = socket.socket()
            # Exactly the options the replacement's own bind will use, or
            # this probe answers a different question than the one asked.
            # uvicorn sets SO_REUSEADDR on POSIX (so a socket in TIME_WAIT is
            # no obstacle) and not on Windows -- where SO_REUSEADDR means
            # "bind even though someone is still listening", which would make
            # this loop succeed on the first try and wait for nothing.
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((HOST, PORT))
                probe.close()
                break
            except OSError:
                probe.close()
                time.sleep(0.1)

        # NOT os.execve: Windows has no real exec, and the emulation segfaults
        # coming out of uvicorn's asyncio shutdown. Spawning a fresh process
        # and exiting is predictable on both platforms. Handles are inherited
        # on purpose, so the new process keeps printing wherever the old one
        # was -- usually a terminal the author is watching.
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
            env=env, cwd=str(Path(__file__).parent))
