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


@app.post("/api/entities/merge")
def merge_entities(body: MergeIn):
    index = STATE["entity_index"]
    src = companion.resolve_entity(index, body.source)
    if not src:
        return JSONResponse({"error": f"'{body.source}' not found"}, status_code=404)
    dst = companion.resolve_entity(index, body.target) or body.target.strip()
    if src == dst:
        return JSONResponse({"error": "already the same entity"}, status_code=400)

    curation = entities.load_curation()
    kind = index[src]["type"]
    curation["merge"][entities.curation_key(kind, src)] = dst
    for k, v in list(curation["merge"].items()):
        if v.lower() == src.lower():
            curation["merge"][k] = dst
    entities.save_curation(curation)
    _rebuild()
    return {"ok": True, "merged": src, "into": dst}


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
