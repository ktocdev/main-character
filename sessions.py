# SPDX-License-Identifier: AGPL-3.0-or-later
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

Provenance (entry schema 2). Every new `you` message says what it is:
`kind: "entry"` with a stable `entry_id` for **save entry**, `kind: "chat"`
for **send**. `dream: true` stays the realm marker. A message with no `kind`
predates the schema and is unclassified: never assumed to be either. The
saved-entry count (`entry_catalog.py`) is built from these fields, so they
are carried unchanged into the archive at close.
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from config import SESSION_DIR, now_local, stamp as _fmt_stamp, zone_name
from rag_journal import JOURNAL_DIR, extract_metadata

ARCHIVE_DIR = SESSION_DIR / "archive"
BRAID_DIR = SESSION_DIR / "braids"
CURRENT_FILE = SESSION_DIR / "current.json"

ENTRY_SCHEMA = 2

# Every read-modify-write of current.json holds this. FastAPI runs sync
# routes on a thread pool, so a save landing mid-close would otherwise be
# read into neither the archive nor the fresh session and simply vanish.
LOCK = threading.RLock()

# Bumped on every write this process makes to the open session or the
# archive. The entry catalog keys its cache on these as well as on file
# stats: two quick writes can share an mtime tick, a counter cannot.
WRITES = {"current": 0, "archive": 0}

# What an entry_id may contain. It lands in a filename (the immediate
# backup), so no separators, no dots -- and no underscore, which the backup
# name uses as its field delimiter (see export.DRAFT).
ENTRY_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

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
    return {"started": _stamp(), "entry_schema": ENTRY_SCHEMA,
            "base": base or [], "messages": []}


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
    with LOCK:
        if CURRENT_FILE.exists():
            cur = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
            if isinstance(cur.get("base"), dict):  # migrate single-base format
                cur["base"] = _initial_base(collection) if collection is not None \
                    else [cur["base"]]
                save_current(cur)
            if cur.get("entry_schema") != ENTRY_SCHEMA:
                _migrate_provenance(cur)
            return cur
        # first run: the open session continues the most recent chat
        base = _initial_base(collection) if collection is not None else []
        cur = _fresh(base=base)
        save_current(cur)
        return cur


def _atomic_write(path: Path, text: str) -> None:
    """Write beside the target, then swap it in, so a crash mid-write leaves
    the previous file rather than half of a new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    # Windows refuses the swap while any other handle has the target open (a
    # reader elsewhere in the process, a virus scanner); those are brief.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)


def save_current(cur: dict):
    with LOCK:
        _atomic_write(CURRENT_FILE, json.dumps(cur, indent=2, ensure_ascii=False))
        WRITES["current"] += 1


def _legacy_entry_id(msg: dict) -> str:
    digest = hashlib.md5(f"{msg.get('ts', '')}\n{msg['text']}".encode()).hexdigest()
    return f"legacy-{digest[:16]}"


def _backup_body(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text.split("\n\n", 1)[1].strip() if "\n\n" in text else None


def _migrate_provenance(cur: dict) -> None:
    """One-time upgrade of an open session written before entry schema 2.

    Old messages never recorded Save versus Send, and nothing in their prose
    can tell them apart. The one unambiguous evidence is the immediate backup
    a save wrote: an `entry_file` artifact whose file body is exactly one
    message's text, stamped to that message's minute. Those messages become
    `kind: "entry"`. Everything else stays unclassified -- kept, closed and
    remembered as before, but not counted as a saved entry while open.

    The pre-migration file is kept beside it, and the marker makes a rerun a
    no-op, so an interrupted or repeated startup cannot count anything twice.
    """
    msgs = cur.get("messages") or []
    if msgs:
        keep = CURRENT_FILE.with_name("current.pre-entry-schema.json")
        if not keep.exists():
            _atomic_write(keep, json.dumps(cur, indent=2, ensure_ascii=False))

    backups = {}
    for art in cur.get("artifacts", []):
        if art.get("kind") == "entry_file" and art.get("path"):
            body = _backup_body(Path(art["path"]))
            if body is not None:
                backups[Path(art["path"]).name] = body

    matches: dict[str, list[dict]] = {}
    for m in msgs:
        if m.get("role") != "you" or m.get("kind") or m.get("dream"):
            continue
        ts = m.get("ts") or ""
        if len(ts) < 16:
            continue
        name = f"{ts[:10]}_{ts[11:13]}{ts[14:16]}_entry.md"
        if backups.get(name) == m["text"].strip():
            matches.setdefault(name, []).append(m)
    for found in matches.values():
        if len(found) == 1:        # two identical texts in one minute: ambiguous
            found[0]["kind"] = "entry"
            found[0]["entry_id"] = _legacy_entry_id(found[0])

    cur["entry_schema"] = ENTRY_SCHEMA
    save_current(cur)


def discard_current(collection=None) -> None:
    """Throw the open chat away without closing it: no journal entry, no
    archive, no memory pipeline. The next chat opens on the same carried-
    forward context a fresh session always does, so this is a reset, not a
    delete of anything already committed. A testing affordance -- the route
    that calls it refuses outside mock mode, where an unsaved chat vanishing
    with no entry would be data loss rather than a convenience.

    Write-mode entries (waking and dream) are backed up to disk -- and dreams
    upserted into the dream collection -- the moment they're written, before
    the chat ever closes (see `record_artifact`). Discard has to undo those
    too, or the "no entry" promise is false: a rebuild or export would still
    pick up the file the discarded chat left behind."""
    with LOCK:
        cur = load_current(collection)
        for art in cur.get("artifacts", []):
            _discard_artifact(art)
        base = _initial_base(collection) if collection is not None else []
        save_current(_fresh(base=base))


def record_artifact(art: dict, collection=None):
    """Track a write-mode side effect (a backup file, a dream collection id)
    against the open session, so `discard_current` can undo it. `art` is
    {"kind": "entry_file", "path": ...} or
    {"kind": "dream", "path": ..., "entry_id": ...}; either may also carry
    `saved_entry_id`, the message it belongs to."""
    with LOCK:
        cur = load_current(collection)
        cur.setdefault("artifacts", []).append(art)
        save_current(cur)


def _discard_artifact(art: dict) -> None:
    path = art.get("path")
    if path:
        Path(path).unlink(missing_ok=True)
    if art.get("kind") == "dream" and art.get("entry_id"):
        import dreams
        dreams.get_dream_collection().delete(ids=[art["entry_id"]])


def append_message(role: str, text: str, dream: bool = False, collection=None,
                   when: datetime | None = None, kind: str | None = None):
    """Record one turn of the open session ('you' or 'companion'). A 'you'
    turn passes `kind="chat"` for send; saves go through `save_entry`."""
    with LOCK:
        cur = load_current(collection)
        msg = {"role": role, "text": text, "ts": _stamp(when), "tz": zone_name()}
        if kind:
            msg["kind"] = kind
        if dream:
            msg["dream"] = True
        cur["messages"].append(msg)
        save_current(cur)


def has_entry(entry_id: str, collection=None) -> bool:
    """Whether the open session already holds this saved entry."""
    with LOCK:
        cur = load_current(collection)
        return any(m.get("entry_id") == entry_id for m in cur["messages"])


def save_entry(text: str, entry_id: str, dream: bool = False, collection=None,
               when: datetime | None = None, artifact: dict | None = None) -> None:
    """Persist one **save entry**: the message, its provenance and the side
    effect `discard_current` would have to undo, in a single write -- so a
    crash can never leave the entry recorded without its artifact, or the
    other way round. Replied and no-reply saves both come through here, which
    is what keeps their counting from drifting apart."""
    with LOCK:
        cur = load_current(collection)
        msg = {"role": "you", "kind": "entry", "entry_id": entry_id,
               "text": text, "ts": _stamp(when), "tz": zone_name()}
        if dream:
            msg["dream"] = True
        cur["messages"].append(msg)
        if artifact:
            cur.setdefault("artifacts", []).append(
                {**artifact, "saved_entry_id": entry_id})
        save_current(cur)


def backup_entry_text(text: str, when: datetime | None = None,
                      entry_id: str | None = None) -> Path:
    """Immediate markdown backup of a write-mode entry. The live copy is
    sessions/current.json; this file is the belt-and-suspenders copy so a
    new entry never has a single point of failure before the chat closes.

    Named with the entry id: minute resolution alone gave two saves in the
    same minute one path, and the second silently overwrote the first."""
    now = when or now_local()
    date = now.strftime("%Y-%m-%d")
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    ident = f"_{entry_id}" if entry_id else ""
    path = JOURNAL_DIR / f"{date}_{now.strftime('%H%M')}{ident}_entry.md"
    path.write_text(
        f"# Journal entry — {date} {now.strftime('%H:%M')}\n_Date: {date}_\n\n{text}",
        encoding="utf-8",
    )
    return path


def seed_messages(msgs: list[dict], collection=None) -> int:
    """One-time import of the pre-session chat log the browser kept in
    localStorage. Only fills an empty session — never duplicates. The log
    never recorded Save versus Send, so these stay unclassified."""
    with LOCK:
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
    archive stitches the two for display.

    Holds the session lock throughout: a save that landed between reading
    the session and opening the fresh one would be in neither."""
    with LOCK:
        return _close_locked(collection, client, title_hint, when)


def _close_locked(collection, client, title_hint: str, when: datetime | None) -> dict:
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
    # timestamps are already local time). Sent messages join memory exactly
    # like saved entries -- what counts as an entry is accounting, not what
    # the companion remembers. Each day part records which saved entries it
    # holds, and whether it holds pre-schema writing nobody can classify
    # (see entry_catalog).
    from bulk_import import chunk_entry, entry_chunk_id
    by_day: dict[str, list[str]] = {}
    saved_by_day: dict[str, list[str]] = {}
    legacy_days: set[str] = set()
    for m in user_msgs:
        day = (m.get("ts") or "")[:10] or date
        by_day.setdefault(day, []).append(m["text"])
        if m.get("kind") == "entry" and m.get("entry_id"):
            saved_by_day.setdefault(day, []).append(m["entry_id"])
        elif not m.get("kind"):
            legacy_days.add(day)

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
        day_parts.append({"date": day, "title": entry_title,
                          "entry_ids": saved_by_day.get(day, []),
                          "legacy": day in legacy_days})

    # archive the whole session: stitched parts + the full braid.
    # Base parts carry their text; the closing parts' content lives in
    # the braid already, so they stay references (no duplication).
    from entities import conversation_cache_key
    key = conversation_cache_key({"date": date, "title": entry_title})
    parts = [_part_content(collection, p) for p in base]
    parts.extend(day_parts)
    # Written only after every day is in the index: the archive is what moves
    # a saved entry from open to indexed. A crash before this line leaves the
    # entries open and the close repeatable (the upserts are idempotent); a
    # crash after it and before the fresh session leaves the same ids in both
    # files, which the catalog counts once.
    _atomic_write(ARCHIVE_DIR / f"{key}.json", json.dumps({
        "id": key,
        "entry_schema": ENTRY_SCHEMA,
        "title": session_title,
        "started": cur["started"],
        "closed": now.strftime("%Y-%m-%d %H:%M"),
        "parts": parts,
        "messages": cur["messages"],
    }, indent=2, ensure_ascii=False))
    WRITES["archive"] += 1

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
