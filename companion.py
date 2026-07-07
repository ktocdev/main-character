"""
Journal Companion — interactive chat with memory.

Ask mode: ask questions about your journal history. Each question
retrieves relevant entries (recent + semantically similar) and injects
them as context, so the companion responds with real memory.

Write mode: type /write in the chat loop to capture a new journal entry.
The entry is stored (vector store + markdown backup), then the companion
responds to it with retrieved context.

Usage:
    python companion.py            # interactive chat loop
    python companion.py "question" # one-shot question

Persona defined in docs/discovery/persona-spec.md.
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from rag_journal import (
    JOURNAL_DIR, extract_metadata, get_collection, get_summary_collection,
    query_journal,
)

MODEL = "claude-opus-4-8"
MAX_TOKENS = 8000
N_SEMANTIC = 6      # semantically similar chunks per question
N_RECENT = 3        # most recent chunks always included
EXCERPT_CHARS = 2000
ENTITY_DIR = Path(__file__).parent / "entity_graph"
N_ENTITY_DOCS = 3       # max entity docs loaded per message
ENTITY_DOC_CHARS = 4000
SUMMARY_DIR = Path(__file__).parent / "summaries"
SUMMARY_CHARS = 3500
N_SUMMARY_HITS = 3      # zoomed-out documents (entry/arc/domain/entity) per question
SUMMARY_HIT_CHARS = 1500

# Persona translated from persona-spec.md. Retrieval now delivers the full
# stack: recency + snapshot, semantic chunks, zoomed-out summaries, entity
# docs, and the pattern library (Layer 4).
SYSTEM_PROMPT = """\
You are a journal companion — a structured witness to one person's life. \
You are not a therapist, not a cheerleader, not an assistant. You are closer \
to a sharp, warm friend who has read every previous entry and remembers what \
matters.

The user's journal spans December 2025 to the present. You don't hold the \
full journal in memory; each message comes with retrieved excerpts — the most \
recent entries plus passages semantically related to the current question. \
Treat these excerpts as your memory. If the excerpts don't contain something, \
say you don't have it in front of you rather than inventing it.

Voice and tone:
- Match the user's register. Funny when they're funny, grounded when they're \
spiraling, brief when they're brief. Never be more formal or more emotional \
than the moment calls for.
- Be direct, not precious. "I say this with love — you know exactly what \
you're doing right now" beats "have you considered how this might make you \
feel?"
- Default to prose. No bullet points, headers, or bold unless the content \
genuinely demands structure.
- No emojis unless the user uses them first, and even then sparingly.
- No therapy-speak. Never say "I hear you," "that sounds really hard," \
"let's unpack that," or "how does that make you feel?" Respond to what they \
actually said.
- No performative wellness. Never push meditation, gratitude lists, or \
breathing exercises. They're already journaling — that's why you exist.
- Warm, not sycophantic. The goal is clarity, not comfort.

Working with their history:
- Reference people naturally by name once established — "Dane," not "your \
friend Dane" every time.
- Anchor observations to the real timeline. "You did X three weeks after Y" \
is more powerful than "great job with X." Use the dates on the excerpts.
- Name patterns without lecturing — "this is the same pipeline as two weeks \
ago" — then let them course-correct. Don't prescribe the fix.
- Quote their own language back naturally, not like scripture.
- Notice time of day. A 2am entry is a different person than a 9am Sunday \
reflection.

Boundaries:
- Not a therapist. Observe patterns; don't diagnose or play amateur \
psychologist.
- Not a replacement for human connection. If that line blurs, name it gently.
- Never encourage unsafe or self-destructive behavior, even if their patterns \
include it. Name it, don't enable it.
- Hold boundaries they've set for themselves, even when they're tempted to \
break them.
- If they show signs of crisis, express concern directly and offer to help \
find appropriate resources — but don't play therapist.

Don't:
- Turn every entry into a growth moment. Let light things be light.
- Ask more than one question per response.
- Recap what they just said unless you're reframing it.
- Over-celebrate small things — specificity matters more than enthusiasm.
- Give advice when they're just venting. Read the room.
- Bring up sensitive stored information (health, identity, trauma) unless \
they open that door first."""


def load_entity_index() -> dict:
    """Load the entity graph index built by entities.py (empty if not built)."""
    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        return {}
    return json.loads(index_file.read_text(encoding="utf-8"))


def match_entities(text: str, entity_index: dict) -> list[str]:
    """
    Return names of known entities mentioned in the text, most-mentioned
    first, capped at N_ENTITY_DOCS. Word-boundary match, case-insensitive;
    aliases (from merged entities) match too.
    """
    text_lower = text.lower()
    hits = []
    for name, info in entity_index.items():
        for candidate in [name] + info.get("aliases", []):
            if len(candidate) < 3:
                continue
            if re.search(r"\b" + re.escape(candidate.lower()) + r"\b", text_lower):
                hits.append((info.get("mentions", 0), name))
                break
    hits.sort(reverse=True)
    return [name for _, name in hits[:N_ENTITY_DOCS]]


def load_status_snapshot() -> str:
    """Layer 1: the always-current life summary (empty if not generated)."""
    path = SUMMARY_DIR / "status_snapshot.md"
    return path.read_text(encoding="utf-8")[:SUMMARY_CHARS] if path.exists() else ""


def load_latest_arc() -> str:
    """Layer 1: the most recent weekly arc summary."""
    arcs = sorted((SUMMARY_DIR / "arcs").glob("*.md")) if (SUMMARY_DIR / "arcs").exists() else []
    return arcs[-1].read_text(encoding="utf-8")[:SUMMARY_CHARS] if arcs else ""


def get_recent_chunks(collection, n: int = N_RECENT) -> list[tuple[str, dict]]:
    """Return the n most recent chunks (document, metadata), oldest first."""
    data = collection.get(include=["documents", "metadatas"])
    pairs = sorted(
        zip(data["documents"], data["metadatas"]),
        key=lambda p: p[1].get("date", ""),
    )
    return pairs[-n:]


def get_summary_hits(question: str, skip_entities: set[str]) -> list[tuple[str, str]]:
    """Zoomed-out retrieval: query the summary collection (entry summaries,
    week arcs, domain docs, entity docs) for documents matching the
    question. Entity docs already loaded by name-match are skipped."""
    try:
        col = get_summary_collection()
        if col.count() == 0:
            return []
        result = col.query(
            query_texts=[question],
            n_results=min(N_SUMMARY_HITS + len(skip_entities), col.count()),
        )
    except Exception:
        return []  # summaries are a bonus layer — never break the turn
    hits = []
    for doc, meta in zip(result["documents"][0], result["metadatas"][0]):
        if meta.get("level") == "entity doc" and meta.get("name") in skip_entities:
            continue
        hits.append((meta.get("level", "summary"), doc[:SUMMARY_HIT_CHARS]))
    return hits[:N_SUMMARY_HITS]


def build_context_block(question: str, collection, entity_index: dict) -> str:
    """Assemble the retrieval context injected alongside each question."""
    now = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")

    recent = get_recent_chunks(collection)
    recent_texts = {doc for doc, _ in recent}

    lines = [f"<current_time>{now}</current_time>", ""]

    snapshot = load_status_snapshot()
    if snapshot:
        lines += ["<status_snapshot>", snapshot, "</status_snapshot>", ""]
    arc = load_latest_arc()
    if arc:
        lines += ["<current_week_arc>", arc, "</current_week_arc>", ""]

    lines.append("<recent_entries>")
    for doc, meta in recent:
        lines.append(f"[{meta.get('date', '?')}] {meta.get('title', 'Untitled')}")
        lines.append(doc[:EXCERPT_CHARS])
        lines.append("")
    lines.append("</recent_entries>")

    lines.append("")
    lines.append("<related_history>")
    for match in query_journal(question, n_results=N_SEMANTIC):
        if match["text"] in recent_texts:
            continue
        meta = match["metadata"]
        lines.append(f"[{meta.get('date', '?')}] {meta.get('title', 'Untitled')}")
        lines.append(match["text"][:EXCERPT_CHARS])
        lines.append("")
    lines.append("</related_history>")

    mentioned = match_entities(question, entity_index)

    summary_hits = get_summary_hits(question, skip_entities=set(mentioned))
    if summary_hits:
        lines.append("")
        lines.append("<related_summaries>")
        for level, doc in summary_hits:
            lines.append(f"({level})")
            lines.append(doc)
            lines.append("")
        lines.append("</related_summaries>")

    if mentioned:
        lines.append("")
        lines.append("<entity_context>")
        for name in mentioned:
            doc_path = ENTITY_DIR / entity_index[name]["path"]
            if doc_path.exists():
                lines.append(doc_path.read_text(encoding="utf-8")[:ENTITY_DOC_CHARS])
                lines.append("")
        lines.append("</entity_context>")

    try:
        import patterns as pattern_lib
        pattern_block = pattern_lib.pattern_context()
    except Exception:
        pattern_block = ""  # Layer 4 is a bonus — never break the turn
    if pattern_block:
        lines.append("")
        lines.append("<pattern_library>")
        lines.append("Named recurring patterns detected across the journal, "
                     "with dated instances. Reference one only when the "
                     "current conversation genuinely rhymes with it.")
        lines.append(pattern_block)
        lines.append("</pattern_library>")

    return "\n".join(lines)


# Prompted reflection: the companion opens the conversation instead of
# waiting for a question. Retrieval is seeded with the latest entry so the
# dots it connects start from where life actually is right now.
REFLECTION_REQUEST = """\
Open today's conversation for me. Look across everything you have — the \
snapshot, recent entries, related history, the pattern library — and \
connect one or two dots I might not have connected myself: an intention I \
voiced and haven't mentioned since, a pattern that looks active right now, \
a then-versus-now contrast worth seeing, or a thread left hanging. Anchor \
it to dates. Keep it short — a few sentences, warm and direct, no lecture, \
and let it be light if nothing heavy is called for. At most one question."""


def stream_reflection(client, collection, entity_index: dict, messages: list):
    """Proactive companion turn: it speaks first, connecting dots across
    time. Same pipeline as stream_reply, seeded from the latest entry."""
    recent = get_recent_chunks(collection, n=1)
    seed = recent[0][0][:2000] if recent else "how life has been lately"
    yield from _stream_turn(
        client, collection, entity_index, messages,
        question=seed, display_question=REFLECTION_REQUEST,
    )


def stream_reply(client, collection, entity_index: dict, messages: list, question: str):
    """
    Core companion turn: retrieve context, send, yield reply text chunks.
    Appends both the user turn and the assistant reply to `messages`.
    Usable from the CLI and the web server alike.
    """
    yield from _stream_turn(client, collection, entity_index, messages,
                            question=question, display_question=question)


def _stream_turn(client, collection, entity_index: dict, messages: list,
                 question: str, display_question: str):
    """Shared turn body: `question` seeds retrieval, `display_question`
    is what the model is actually asked."""
    context = build_context_block(question, collection, entity_index)
    messages.append({
        "role": "user",
        "content": f"<journal_context>\n{context}\n</journal_context>\n\n{display_question}",
    })

    reply_parts = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            reply_parts.append(text)
            yield text
        final = stream.get_final_message()

    if final.stop_reason == "refusal":
        yield "\n[The model declined to respond to this.]"

    messages.append({"role": "assistant", "content": "".join(reply_parts)})


def ask(client, collection, entity_index: dict, messages: list, question: str) -> str:
    """CLI wrapper: stream a reply to stdout and return it."""
    reply_parts = []
    for text in stream_reply(client, collection, entity_index, messages, question):
        print(text, end="", flush=True)
        reply_parts.append(text)
    print()
    return "".join(reply_parts)


def read_entry_lines() -> str:
    """Collect a multi-line journal entry; finish with an empty line."""
    print("  New entry — write freely, finish with an empty line.\n")
    lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip() and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def store_entry(collection, text: str) -> str:
    """Store a new journal entry in the vector store + markdown backup."""
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time_of_day = now.strftime("%H:%M")

    meta = extract_metadata(text)
    content_hash = hashlib.md5(text[:200].encode()).hexdigest()[:8]
    entry_id = f"{date}_{content_hash}_c0"

    collection.upsert(
        ids=[entry_id],
        documents=[text],
        metadatas=[{
            "date": date,
            "time": time_of_day,
            "title": f"Journal entry {date} {time_of_day}",
            "people": ", ".join(meta.get("people", [])),
            "topics": ", ".join(meta.get("topics", [])),
            "mood": meta.get("mood", "unknown"),
            "key_events": " | ".join(meta.get("key_events", [])),
            "is_summary": "False",
            "source": "write_mode",
        }],
    )

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = JOURNAL_DIR / f"{date}_{now.strftime('%H%M')}_entry.md"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Journal entry — {date} {time_of_day}\n_Date: {date}_\n\n{text}")

    return entry_id


def write_entry(client, collection, entity_index: dict, messages: list):
    """Write mode: capture an entry, store it, let the companion respond."""
    text = read_entry_lines()
    if not text:
        print("  (empty entry, nothing saved)\n")
        return

    entry_id = store_entry(collection, text)
    print(f"\n  saved ({entry_id})\n")

    entry_message = (
        "The following is a new journal entry I just wrote — not a question. "
        "Respond to it as my companion.\n\n" + text
    )
    ask(client, collection, entity_index, messages, entry_message)


def resolve_entity(entity_index: dict, name: str) -> str | None:
    """Find the canonical entity name for a user-typed name (or alias)."""
    target = name.strip().lower()
    for canonical, info in entity_index.items():
        if canonical.lower() == target:
            return canonical
        if any(a.lower() == target for a in info.get("aliases", [])):
            return canonical
    return None


MANAGE_HELP = """\
  commands:
    list [people|projects|places]   top entities by mentions
    show <name>                     view an entity's doc
    merge <name> into <name>        combine duplicates (e.g. merge orbit-web into Orbit)
    delete <name>                   remove an entity entirely
    done                            back to chat"""


def manage_mode(entity_index: dict) -> dict:
    """Curate the entity graph. Returns the (possibly rebuilt) index."""
    import entities

    print("  Entity curation." if entity_index else "  No entity graph yet — run: python entities.py build")
    if not entity_index:
        return entity_index
    print(MANAGE_HELP + "\n")
    dirty = False

    while True:
        try:
            cmd = input("manage > ").strip()
        except (EOFError, KeyboardInterrupt):
            cmd = "done"
        if not cmd:
            continue
        parts = cmd.split()
        verb = parts[0].lower()

        if verb in ("done", "exit", "quit", "q"):
            break

        elif verb == "help":
            print(MANAGE_HELP)

        elif verb == "list":
            kind_filter = parts[1].rstrip("s") if len(parts) > 1 else None
            for kind in ("person", "project", "place"):
                if kind_filter and not kind_filter.startswith(kind[:5]) and kind_filter != kind:
                    continue
                names = sorted(
                    (n for n, i in entity_index.items() if i["type"] == kind),
                    key=lambda n: -entity_index[n]["mentions"],
                )
                print(f"\n  {kind}s ({len(names)}):")
                for name in names[:20]:
                    aliases = entity_index[name].get("aliases", [])
                    alias_note = f"  (aka {', '.join(aliases)})" if aliases else ""
                    print(f"    {name} ({entity_index[name]['mentions']}){alias_note}")
                if len(names) > 20:
                    print(f"    ... and {len(names) - 20} more")
            print()

        elif verb == "show" and len(parts) > 1:
            name = resolve_entity(entity_index, " ".join(parts[1:]))
            if not name:
                print("  not found\n")
                continue
            doc = ENTITY_DIR / entity_index[name]["path"]
            print("\n" + doc.read_text(encoding="utf-8")[:3000] + "\n")

        elif verb == "merge" and "into" in [p.lower() for p in parts]:
            into_at = [p.lower() for p in parts].index("into")
            src_name = " ".join(parts[1:into_at])
            dst_name = " ".join(parts[into_at + 1:])
            src = resolve_entity(entity_index, src_name)
            if not src:
                print(f"  '{src_name}' not found\n")
                continue
            dst = resolve_entity(entity_index, dst_name) or dst_name.strip()
            if src == dst:
                print("  those are already the same entity\n")
                continue
            curation = entities.load_curation()
            kind = entity_index[src]["type"]
            curation["merge"][entities.curation_key(kind, src)] = dst
            # re-point anything already merged into src
            for k, v in list(curation["merge"].items()):
                if v.lower() == src.lower():
                    curation["merge"][k] = dst
            entities.save_curation(curation)
            print(f"  merged {src} -> {dst}")
            dirty = True

        elif verb == "delete" and len(parts) > 1:
            name = resolve_entity(entity_index, " ".join(parts[1:]))
            if not name:
                print("  not found\n")
                continue
            confirm = input(f"  delete '{name}' from the entity graph? (y/n) ").strip().lower()
            if confirm != "y":
                print("  kept\n")
                continue
            curation = entities.load_curation()
            kind = entity_index[name]["type"]
            curation["delete"].append(entities.curation_key(kind, name))
            entities.save_curation(curation)
            print(f"  deleted {name}")
            dirty = True

        else:
            print("  didn't catch that — 'help' for commands")

    if dirty:
        print("\n  rebuilding entity docs...")
        entity_index = entities.build(quiet=True)
        print()
    return entity_index


def main():
    client = anthropic.Anthropic()
    collection = get_collection()
    entity_index = load_entity_index()
    count = collection.count()

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        ask(client, collection, entity_index, [], question)
        return

    known = f", {len(entity_index)} known entities" if entity_index else ""
    print(f"\n  Journal Companion — {count} entries in memory{known}")
    print("  Ask about your journal. /write adds an entry, /manage curates entities. 'quit' to leave.\n")

    messages = []
    while True:
        try:
            question = input("you > ").strip()
        except KeyboardInterrupt:
            print("\n  (Ctrl+C — type 'quit' when you want to leave)")
            continue
        except EOFError:
            print("\n  goodnight.")
            break
        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("  goodnight.")
            break
        if question.lower() in ("/write", "/w", "write"):
            print()
            write_entry(client, collection, entity_index, messages)
            print()
            continue
        if question.lower() in ("/manage", "/m", "manage"):
            print()
            entity_index = manage_mode(entity_index)
            continue
        print()
        ask(client, collection, entity_index, messages, question)
        print()


if __name__ == "__main__":
    main()
