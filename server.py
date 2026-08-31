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
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import categories
import companion
import entities
import sessions
from config import HOST, PORT, MOCK_MODE, get_client
from config import DATE_FORMAT, parse_stamp, now_local, stamp as _now_stamp, zone_name
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


@app.get("/api/status")
def status():
    return {
        "entries": STATE["collection"].count(),
        "entities": len(STATE["entity_index"]),
        "conversation_turns": len(STATE["messages"]) // 2,
        # drives the UI banner — a canned reply must never be mistaken for
        # a real one
        "mock": MOCK_MODE,
        # the server owns the clock; the client stamps against this
        "now": _now_stamp(),
        "tz": zone_name(),
        # a strftime format can't be handed to JS — send the one bit the
        # client actually branches on
        "date_style": "short" if "%B" not in DATE_FORMAT else "long",
    }


@app.post("/api/chat")
def chat(body: ChatIn):
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
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/lookup")
def lookup(body: ChatIn):
    """The chat screen: pull information out of the journal. Its own
    conversation, separate from the journal companion — lookups never
    join the open session and never become journal memory."""
    def gen():
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["lookup"], body.message,
        )
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/lookup/reset")
def reset_lookup():
    STATE["lookup"] = []
    return {"ok": True}


def _after_close_refresh():
    """Full memory pipeline after a chat closes: the closed chat is now a
    journal entry. Tag it, extract its entities, refresh arcs + domain
    docs + entry summaries, scan it for dreams, re-sync embeddings.
    Everything is incremental — cached work is skipped."""
    import dreams
    import summarizer
    try:
        categories.build(quiet=True)
        STATE["entity_index"] = entities.build(quiet=True)
        summarizer.build(quiet=True)
        dreams.extract(quiet=True)
    except Exception as e:
        print(f"  post-close refresh failed: {e}")


def _after_close_seed(archive_key: str):
    """Integrate-at-close: fold the just-archived braid into the live seed
    and write the candidate for download/review. Never touches the seed."""
    import seed
    try:
        path = seed.generate_candidate(archive_key)
        print(f"  seed candidate -> {path.name}")
    except Exception as e:
        print(f"  seed candidate failed: {e}")


@app.post("/api/entry")
def write_entry(body: EntryIn, background_tasks: BackgroundTasks):
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "empty entry"}, status_code=400)

    when = parse_stamp(body.ts) if body.ts else None

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
        background_tasks.add_task(dreams.ingest_dream_entry, text, entry_id, when)
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
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], entry_message, include_dreams=body.dream,
        )
        sessions.append_message("companion", STATE["messages"][-1]["content"],
                                when=when)
    return StreamingResponse(
        gen(),
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
    dreams.extract(force=body.force, quiet=True)
    index = dreams.load_index()
    return {**index, "weather": dreams.dream_weather()}


@app.post("/api/reflect")
def reflect():
    """The companion opens the conversation: connects dots across time."""
    def gen():
        yield from companion.stream_reflection(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"],
        )
        sessions.append_message("companion", STATE["messages"][-1]["content"],
                                collection=STATE["collection"])
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


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
    background_tasks.add_task(_after_close_seed, result["key"])
    background_tasks.add_task(_after_close_refresh)
    return {"ok": True, **result}


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
    return {"groups": entities.suggest_merges(body.kind)}


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
    cat = categories.build(quiet=True)
    result = summarizer.build(quiet=True)
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
    return {"ok": True, **categories.build(force=body.force, quiet=True)}


@app.post("/api/categories/tag")
def set_category_tag(body: CategoryTagIn):
    """Manually add/remove a tag on one entry — recorded as an override."""
    try:
        return categories.set_tag(body.key, body.name, body.present)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


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
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
