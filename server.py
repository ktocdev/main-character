"""
RAG Journal — local web UI.

A lightweight FastAPI server wrapping the companion (chat + write) and the
entity graph (browse + curate). Single user, local only. This is the
"initial functional UI" from roadmap Phase 4 — the Vue/Prism Components
version replaces the frontend later; the API layer carries over.

Usage:
    .venv\\Scripts\\python.exe server.py
    -> open http://127.0.0.1:8144
"""

import json
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import categories
import companion
import entities
import sessions
from rag_journal import get_collection

HOST = "127.0.0.1"
PORT = 8144
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="RAG Journal")

STATE = {
    "client": None,
    "collection": None,
    "entity_index": {},
    "messages": [],  # the journal companion conversation (the open session)
    "lookup": [],    # the chat screen: an information tool, never journal data
}


@app.on_event("startup")
def startup():
    STATE["client"] = anthropic.Anthropic()
    STATE["collection"] = get_collection()
    STATE["entity_index"] = companion.load_entity_index()
    # the open session survives restarts — rebuild the conversation from it
    STATE["messages"] = sessions.conversation_messages()


class ChatIn(BaseModel):
    message: str


class EntryIn(BaseModel):
    text: str
    dream: bool = False  # user-flagged; dreams go to the dream realm


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
    }


@app.post("/api/chat")
def chat(body: ChatIn):
    def gen():
        sessions.append_message("you", body.message, collection=STATE["collection"])
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], body.message,
        )
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
    docs + entry summaries + snapshot, scan it for dreams, re-sync
    embeddings. Everything is incremental — cached work is skipped."""
    import dreams
    import summarizer
    try:
        categories.build(quiet=True)
        STATE["entity_index"] = entities.build(quiet=True)
        summarizer.build(quiet=True)
        dreams.extract(quiet=True)
    except Exception as e:
        print(f"  post-close refresh failed: {e}")


@app.post("/api/entry")
def write_entry(body: EntryIn, background_tasks: BackgroundTasks):
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "empty entry"}, status_code=400)

    if body.dream:
        import dreams
        entry_id = dreams.store_dream_entry(text)
        sessions.append_message("you", text, dream=True, collection=STATE["collection"])
        entry_message = (
            "The following is a dream I just had — I'm flagging it as a "
            "dream, not a waking event. Respond to it as my companion: "
            "receive it, don't decode it with generic symbolism.\n\n" + text
        )
        background_tasks.add_task(dreams.ingest_dream_entry, text, entry_id)
    else:
        # the entry joins the open session; it becomes journal memory
        # when the chat is closed (the summarize point)
        entry_id = "current-chat"
        sessions.append_message("you", text, collection=STATE["collection"])
        sessions.backup_entry_text(text)
        entry_message = (
            "The following is a new journal entry I just wrote — not a question. "
            "Respond to it as my companion.\n\n" + text
        )

    def gen():
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], entry_message, include_dreams=body.dream,
        )
        sessions.append_message("companion", STATE["messages"][-1]["content"])
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
    """One closed session: stitched parts + the full braid."""
    archive = sessions.load_archive(id)
    if archive is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return archive


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
    background_tasks.add_task(_after_close_refresh)
    return {"ok": True, **result}


@app.post("/api/reset")
def reset_conversation():
    STATE["messages"] = []
    return {"ok": True}


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


@app.post("/api/summaries/refresh")
def refresh_summaries():
    """Tag any new entries with categories, then regenerate stale weekly
    arcs, domain documents, and the status snapshot (all incremental).
    Tagging runs first so fresh entries land in their domain docs."""
    import summarizer
    cat = categories.build(quiet=True)
    result = summarizer.build(quiet=True)
    return {"ok": True, **result, "categories_tagged": cat["new"]}


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
