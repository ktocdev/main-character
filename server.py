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

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import companion
import entities
from rag_journal import get_collection

HOST = "127.0.0.1"
PORT = 8144
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="RAG Journal")

STATE = {
    "client": None,
    "collection": None,
    "entity_index": {},
    "messages": [],  # single-user conversation history
}


@app.on_event("startup")
def startup():
    STATE["client"] = anthropic.Anthropic()
    STATE["collection"] = get_collection()
    STATE["entity_index"] = companion.load_entity_index()


class ChatIn(BaseModel):
    message: str


class EntryIn(BaseModel):
    text: str


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
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], body.message,
        )
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/entry")
def write_entry(body: EntryIn):
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "empty entry"}, status_code=400)

    entry_id = companion.store_entry(STATE["collection"], text)
    entry_message = (
        "The following is a new journal entry I just wrote — not a question. "
        "Respond to it as my companion.\n\n" + text
    )

    def gen():
        yield from companion.stream_reply(
            STATE["client"], STATE["collection"], STATE["entity_index"],
            STATE["messages"], entry_message,
        )
    return StreamingResponse(
        gen(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Entry-Id": entry_id},
    )


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


def _combine(body: MergeIn, field: str, verb: str):
    """Shared logic for merge (keeps alias) and correct (no alias)."""
    index = STATE["entity_index"]
    src = companion.resolve_entity(index, body.source)
    if not src:
        return JSONResponse({"error": f"'{body.source}' not found"}, status_code=404)
    dst = companion.resolve_entity(index, body.target) or body.target.strip()
    if src == dst:
        return JSONResponse({"error": "already the same entity"}, status_code=400)

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

    curation = entities.load_curation()
    curation["retype"][entities.curation_key(index[name]["type"], name)] = {
        "type": body.new_type,
        "name": body.new_name.strip() or name,
    }
    entities.save_curation(curation)
    _rebuild()
    return {"ok": True, "retyped": name, "to": body.new_type}


@app.post("/api/entities/alias")
def alias_entity(body: AliasIn):
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)

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
    _rebuild()
    return {"ok": True}


@app.post("/api/entities/suggest")
def suggest(body: KindIn):
    if body.kind not in entities.KINDS:
        return JSONResponse({"error": f"kind must be one of {entities.KINDS}"}, status_code=400)
    return {"groups": entities.suggest_merges(body.kind)}


@app.post("/api/entities/delete")
def delete_entity(body: NameIn):
    index = STATE["entity_index"]
    name = companion.resolve_entity(index, body.name)
    if not name:
        return JSONResponse({"error": f"'{body.name}' not found"}, status_code=404)

    curation = entities.load_curation()
    curation["delete"].append(entities.curation_key(index[name]["type"], name))
    entities.save_curation(curation)
    _rebuild()
    return {"ok": True, "deleted": name}


if __name__ == "__main__":
    print(f"\n  RAG Journal -> http://{HOST}:{PORT}\n")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
