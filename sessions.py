"""
Sessions — the chat-as-session layer of the RAG Journal.

A session is one long-running chat: journal entries, companion replies,
and follow-up conversation braided together over days (about a week in
practice). When it feels finished, the user closes it — that's the
"summarize point". Closing turns the user's side of the chat into a
journal entry in the exact shape of an imported conversation (companion
replies are never journal memory, matching how Claude exports were
imported: user messages only), and kicks off the full memory pipeline.

  Current session  — sessions/current.json. The very first session
                     continues the most recent imported conversation
                     (its `base`), so ongoing chatting extends the chat
                     the user was already having before the web UI.
  Close            — user text (minus dream-flagged entries, which live
                     in the dream realm) becomes a conversation-entry:
                     chunked, embedded, markdown backup. The base
                     conversation is never rewritten — its curated
                     observations and caches stay untouched; the new
                     material is stored as its own dated entry and the
                     archive stitches them into one session for display.
  Archive          — sessions/archive/{key}.json keeps the full braid
                     (both sides) plus the stitched parts, so history
                     can always replay the whole conversation.

Layout (gitignored — personal data):
    sessions/current.json    the open session
    sessions/archive/        closed sessions, full braid + parts
"""

import json
import re
from datetime import datetime
from pathlib import Path

from config import SESSION_DIR, now_local, stamp as _fmt_stamp, zone_name
from rag_journal import JOURNAL_DIR, extract_metadata

ARCHIVE_DIR = SESSION_DIR / "archive"
BRAID_DIR = SESSION_DIR / "braids"
CURRENT_FILE = SESSION_DIR / "current.json"

TITLE_PROMPT = (
    "Give this journal chat a short title — 3 to 6 words, plain text, "
    "no quotes, no punctuation at the end:\n\n"
)


def _stamp(when: datetime | None = None) -> str:
    """Wall-clock in the configured zone. `when` lets a caller supply the
    moment the entry was actually written — the user can backdate a missed
    day, and close_session groups by ts[:10], so this is what decides which
    day an entry lands on."""
    return _fmt_stamp(when)


def _fresh(base: list[dict] | None = None) -> dict:
    return {"started": _stamp(), "base": base or [], "messages": []}


def _initial_base(collection) -> list[dict]:
    """The chat the open session continues: the most recent imported
    conversation plus every loose write-mode entry dated after it, in
    order — they read as one chat, newest at the end. Entries from
    already-closed sessions never re-enter."""
    data = collection.get(include=["metadatas"])
    convs = {}
    for meta in data["metadatas"]:
        key = (meta.get("date", ""), meta.get("title", ""))
        convs.setdefault(key, meta.get("source", ""))
    imports = sorted(k for k, src in convs.items() if src == "bulk_import")
    if not imports:
        latest = sorted(convs)[-1:]
        return [{"date": d, "title": t} for d, t in latest]
    anchor = imports[-1]
    tail = sorted(
        k for k, src in convs.items()
        if k != anchor and k[0] >= anchor[0]
        and src not in ("bulk_import", "session_close")
    )
    return [{"date": d, "title": t} for d, t in [anchor] + tail]


def load_current(collection=None) -> dict:
    if CURRENT_FILE.exists():
        cur = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
        if isinstance(cur.get("base"), dict):  # migrate single-base format
            cur["base"] = _initial_base(collection) if collection is not None \
                else [cur["base"]]
            save_current(cur)
        return cur
    # first run: the open session continues the most recent chat
    base = _initial_base(collection) if collection is not None else []
    cur = _fresh(base=base)
    save_current(cur)
    return cur


def save_current(cur: dict):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(
        json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def append_message(role: str, text: str, dream: bool = False, collection=None,
                   when: datetime | None = None):
    """Record one turn of the open session ('you' or 'companion')."""
    cur = load_current(collection)
    msg = {"role": role, "text": text, "ts": _stamp(when), "tz": zone_name()}
    if dream:
        msg["dream"] = True
    cur["messages"].append(msg)
    save_current(cur)


def backup_entry_text(text: str, when: datetime | None = None):
    """Immediate markdown backup of a write-mode entry. The live copy is
    sessions/current.json; this file is the belt-and-suspenders copy so a
    new entry never has a single point of failure before the chat closes."""
    now = when or now_local()
    date = now.strftime("%Y-%m-%d")
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = JOURNAL_DIR / f"{date}_{now.strftime('%H%M')}_entry.md"
    path.write_text(
        f"# Journal entry — {date} {now.strftime('%H:%M')}\n_Date: {date}_\n\n{text}",
        encoding="utf-8",
    )


def seed_messages(msgs: list[dict], collection=None) -> int:
    """One-time import of the pre-session chat log the browser kept in
    localStorage. Only fills an empty session — never duplicates."""
    cur = load_current(collection)
    if cur["messages"]:
        return 0
    ts = _stamp()
    for m in msgs:
        role = "you" if m.get("role") == "you" else "companion"
        text = (m.get("text") or "").strip()
        if text and text != "…":
            cur["messages"].append({"role": role, "text": text, "ts": ts})
    save_current(cur)
    return len(cur["messages"])


def conversation_messages(limit: int = 30) -> list[dict]:
    """Rebuild the companion's in-memory conversation from the open
    session at server startup. Raw text only (retrieval context is
    reassembled fresh each turn); consecutive same-role turns merge."""
    if not CURRENT_FILE.exists():
        return []
    cur = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
    msgs = []
    for m in cur["messages"][-limit:]:
        role = "user" if m["role"] == "you" else "assistant"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + m["text"]
        else:
            msgs.append({"role": role, "content": m["text"]})
    if msgs and msgs[0]["role"] == "assistant":
        msgs.insert(0, {"role": "user", "content": "(picking our chat back up)"})
    return msgs


def conversation_text(collection, date: str, title: str) -> str:
    """Full text of one conversation, reassembled from its chunks."""
    data = collection.get(
        where={"$and": [{"date": {"$eq": date}}, {"title": {"$eq": title}}]},
        include=["documents"],
    )

    def chunk_idx(doc_id):
        m = re.search(r"_c(\d+)$", doc_id)
        return int(m.group(1)) if m else 0

    chunks = sorted(zip(data["ids"], data["documents"]), key=lambda p: chunk_idx(p[0]))
    return "\n\n".join(doc for _, doc in chunks)


def load_braid(date: str, title: str) -> list[dict] | None:
    """The full two-sided message braid of a conversation, when the
    original Claude export provided one (display-only — journal memory
    stays user-side-only)."""
    from entities import conversation_cache_key
    key = conversation_cache_key({"date": date, "title": title})
    path = BRAID_DIR / f"{Path(key).name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["messages"]


def _part_content(collection, part: dict) -> dict:
    """A session part with its content: the interleaved braid when we
    have both sides, otherwise the flat user-side text."""
    braid = load_braid(part["date"], part["title"])
    if braid:
        return {**part, "messages": braid}
    return {**part, "text": conversation_text(collection, part["date"], part["title"])}


def backfill_braids(collection, export_path: str = "conversations.json") -> int:
    """One-time: rebuild the full two-sided braid for every imported
    conversation from the original Claude export, so history can show
    Claude's replies between entries. Returns how many were written."""
    from bulk_import import load_conversations
    from entities import conversation_cache_key

    data = collection.get(include=["metadatas"])
    known = {(m.get("date", ""), m.get("title", "")) for m in data["metadatas"]}

    BRAID_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    for conv in load_conversations(export_path):
        title = conv.get("name", "Untitled")
        date = (conv.get("created_at") or "")[:10]
        if (date, title) not in known:
            continue
        msgs = []
        for m in conv.get("chat_messages", []):
            sender = m.get("sender", m.get("role", ""))
            text = m.get("text", m.get("content", ""))
            if isinstance(text, list):
                text = " ".join(
                    b.get("text", "") for b in text
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            text = (text or "").strip()
            if not text:
                continue
            msgs.append({
                "role": "you" if sender in ("human", "user") else "companion",
                "text": text,
                "ts": (m.get("created_at") or "")[:16].replace("T", " "),
            })
        if not msgs:
            continue
        key = conversation_cache_key({"date": date, "title": title})
        (BRAID_DIR / f"{key}.json").write_text(json.dumps(
            {"date": date, "title": title, "messages": msgs},
            indent=2, ensure_ascii=False), encoding="utf-8")
        made += 1
    return made


def current_view(collection) -> dict:
    """The open session: every part it continues (braid or text, in
    order) followed by the live braid."""
    cur = load_current(collection)
    parts = [_part_content(collection, p) for p in cur.get("base") or []]
    return {"started": cur["started"], "parts": parts, "messages": cur["messages"]}


def load_archives() -> list[dict]:
    if not ARCHIVE_DIR.exists():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(ARCHIVE_DIR.glob("*.json"))
    ]


def load_archive(archive_id: str) -> dict | None:
    path = ARCHIVE_DIR / f"{Path(archive_id).name}.json"  # basename only — no traversal
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def session_list(collection) -> dict:
    """Everything for the history sidebar: the open session plus all
    closed ones, newest first. Conversations stitched into an archive
    (or being continued by the open session) don't double-list."""
    cur = load_current(collection)
    archives = load_archives()

    covered = set()
    for a in archives:
        for part in a["parts"]:
            covered.add((part["date"], part["title"]))
    for part in cur.get("base") or []:
        covered.add((part["date"], part["title"]))

    data = collection.get(include=["metadatas"])
    conv_keys = set()
    for meta in data["metadatas"]:
        key = (meta.get("date", ""), meta.get("title", ""))
        if key not in covered:
            conv_keys.add(key)

    items = [
        {
            "kind": "archive",
            "id": a["id"],
            "title": a["title"],
            "start": min(p["date"] for p in a["parts"]),
            "end": max(p["date"] for p in a["parts"]),
        }
        for a in archives
    ]
    # per-day entries of one conversation list as a single item spanning
    # its date range, like an archived session does
    by_title: dict[str, list[str]] = {}
    for d, t in conv_keys:
        by_title.setdefault(t, []).append(d)
    items += [
        {"kind": "import", "title": t, "start": min(ds), "end": max(ds)}
        for t, ds in by_title.items()
    ]
    items.sort(key=lambda i: i["end"], reverse=True)

    base = cur.get("base") or []
    return {
        "current": {
            "started": cur["started"],
            "title": base[0]["title"] if base else None,
            "message_count": len(cur["messages"]),
        },
        "sessions": items,
    }


def _generate_title(client, text: str) -> str:
    if client is None:
        return ""
    try:
        from config import MC_PROCESSING_MODEL, processing_thinking_kwargs
        response = client.messages.create(
            model=MC_PROCESSING_MODEL,
            max_tokens=30,
            **processing_thinking_kwargs(),
            messages=[{"role": "user", "content": TITLE_PROMPT + text[:6000]}],
        )
        title = next((b.text for b in response.content if b.type == "text"), "")
        return re.sub(r"\s+", " ", title.strip().strip('"'))[:80]
    except Exception:
        return ""


def _last_written(msgs: list[dict]) -> datetime | None:
    """The newest message stamp, so a close inherits the session's own
    dates instead of the moment the button was pressed."""
    from config import parse_stamp
    stamps = sorted(m.get("ts", "") for m in msgs if m.get("ts"))
    return parse_stamp(stamps[-1]) if stamps else None


def close_session(collection, client=None, title_hint: str = "",
                  when: datetime | None = None) -> dict:
    """The summarize point. The user's side of the open session becomes
    a journal entry in the same shape as an imported conversation; the
    full braid is archived; a fresh empty session opens.

    The base conversation (when the session continues an import) is
    never modified — the new material is its own dated entry, and the
    archive stitches the two for display."""
    cur = load_current(collection)
    user_msgs = [
        m for m in cur["messages"]
        if m["role"] == "you" and not m.get("dream")
    ]
    if not user_msgs:
        raise ValueError("nothing new in this chat yet — write or chat first")

    # The close is dated by the last thing written, not by the wall clock:
    # a chat closed the morning after a late entry belongs with that entry,
    # and a backdated session must not archive under today.
    now = when or _last_written(user_msgs) or now_local()
    date = now.strftime("%Y-%m-%d")
    new_text = "\n\n".join(m["text"] for m in user_msgs)
    base = cur.get("base") or []

    if base:
        session_title = base[0]["title"]
        entry_title = f"{session_title} — continued"
    else:
        session_title = title_hint.strip() or _generate_title(client, new_text) \
            or f"Journal chat {date}"
        entry_title = session_title

    # store the new material exactly like an imported conversation:
    # one dated entry per local calendar day the user wrote (session
    # timestamps are already local time)
    from bulk_import import chunk_entry, entry_chunk_id
    by_day: dict[str, list[str]] = {}
    for m in user_msgs:
        day = (m.get("ts") or "")[:10] or date
        by_day.setdefault(day, []).append(m["text"])

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in entry_title)[:50]
    day_parts = []
    for day in sorted(by_day):
        day_text = "\n\n".join(by_day[day])
        for chunk in chunk_entry({"text": day_text, "date": day, "title": entry_title}):
            text = chunk["text"]
            idx = chunk.get("chunk_index", 0)
            meta = extract_metadata(text)
            collection.upsert(
                ids=[entry_chunk_id(day, entry_title, text, idx)],
                documents=[text],
                metadatas=[{
                    "date": day,
                    "title": entry_title,
                    "people": ", ".join(meta.get("people", [])),
                    "topics": ", ".join(meta.get("topics", [])),
                    "mood": meta.get("mood", "unknown"),
                    "key_events": " | ".join(meta.get("key_events", [])),
                    "is_summary": "False",
                    "source": "session_close",
                }],
            )
        (JOURNAL_DIR / f"{day}_{safe_title}.md").write_text(
            f"# {entry_title}\n_Date: {day}_\n\n{day_text}", encoding="utf-8"
        )
        day_parts.append({"date": day, "title": entry_title})

    # archive the whole session: stitched parts + the full braid.
    # Base parts carry their text; the closing parts' content lives in
    # the braid already, so they stay references (no duplication).
    from entities import conversation_cache_key
    key = conversation_cache_key({"date": date, "title": entry_title})
    parts = [_part_content(collection, p) for p in base]
    parts.extend(day_parts)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (ARCHIVE_DIR / f"{key}.json").write_text(json.dumps({
        "id": key,
        "title": session_title,
        "started": cur["started"],
        "closed": now.strftime("%Y-%m-%d %H:%M"),
        "parts": parts,
        "messages": cur["messages"],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    save_current(_fresh())
    return {"key": key, "title": session_title, "entry_title": entry_title, "date": date}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        from rag_journal import get_collection
        export = sys.argv[2] if len(sys.argv) > 2 else "conversations.json"
        n = backfill_braids(get_collection(), export)
        print(f"  {n} conversation braids backfilled from {export}")
    else:
        print(__doc__)
