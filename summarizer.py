"""
Summarizer — Phase 3 of the RAG Journal.

Generates the summary layers from persona-spec.md §6 that feed the
companion's Layer 1 context:

  Weekly arc summaries  — ~450-word narrative per ISO week, cached in
                          summaries/arcs/{YYYY-Www}.md; only weeks whose
                          entries changed are regenerated
  Domain summaries      — one living document per category (work, health,
                          relationships, …) in summaries/domains/{name}.md, built
                          from the entries tagged with that category; only
                          domains whose entry set changed are regenerated
  Entry summaries       — 2-3 sentences per entry (key events, emotional
                          state, decisions), cached in summaries/entries/
  Status snapshot       — summaries/status_snapshot.md; retired from the
                          pipeline (the co-edited seed summary is Layer 1
                          now — see seed.py); regenerate manually with
                          `python summarizer.py snapshot` if ever needed
  Summary embeddings    — every layer above plus the entity docs mirrored
                          into the journal_summaries collection (local
                          embeddings, free) so retrieval can match at any
                          zoom level, not just chunks

Usage:
    python summarizer.py build            # incremental (only stale weeks/domains)
    python summarizer.py build --force    # regenerate everything
"""

import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


from config import AUTHOR, ENTITY_DIR, MC_PROCESSING_MODEL as MODEL, SUMMARY_DIR, get_client, processing_thinking_kwargs
from entities import get_conversations, conversation_cache_key

ARC_DIR = SUMMARY_DIR / "arcs"
DOMAIN_DIR = SUMMARY_DIR / "domains"
ENTRY_DIR = SUMMARY_DIR / "entries"
SNAPSHOT_FILE = SUMMARY_DIR / "status_snapshot.md"
ARC_INPUT_CHARS = 60_000
SNAPSHOT_RECENT_ARCS = 4
DOMAIN_INPUT_CHARS = 100_000   # newest entries kept in full, oldest dropped first
DOMAIN_ENTRY_CHARS = 6_000     # per-entry cap inside a domain's input
MIN_DOMAIN_ENTRIES = 3         # a "domain" of one entry isn't a story yet
ENTRY_INPUT_CHARS = 45_000
EMBED_DOC_CHARS = 4_000        # per-document cap in the summary collection

ENTRY_PROMPT = """\
Summarize this journal entry of {author}'s in 2-3 sentences: the key \
events, the emotional state, and any decisions made. Plain prose, no \
headers, no advice. Use people's names as {author} does.

<entry date="{date}" title="{title}">
{text}
</entry>"""

ARC_PROMPT = """\
You are summarizing one week of {author}'s journal. Below are the journal \
entries from that week (originally conversations with an AI companion; only \
{author}'s side is included).

Write the narrative arc of this week in about 400-500 words of plain prose \
(no headers, no bullet points): what happened, what changed, what patterns \
appeared, and what's unresolved going into next week. Use people's names as \
{author} does. Anchor events to their dates where it matters. Don't \
moralize or append advice — this is a record, not a pep talk.

<week entries_from="{start}" entries_to="{end}">
{text}
</week>"""

DOMAIN_PROMPT = """\
You maintain the living "{category}" document of {author}'s journal — the \
one page to read to understand this part of {author}'s life. In this \
journal, "{category}" covers: {definition}.

Below are {author}'s journal entries tagged with this category, oldest \
first (each originally a conversation with an AI companion; only \
{author}'s side is included). Some entries touch many parts of life — \
draw out the {category} thread and leave the rest.

Write the document in about 400-600 words of plain prose. Open with where \
things stand now, then how it got here — the key developments anchored to \
their dates — and end with the open threads. Use people's names as \
{author} does. This is a record, not a pep talk: no advice, no moralizing.

Today is {today}.

<entries category="{category}">
{text}
</entries>"""

SNAPSHOT_PROMPT = """\
You maintain a status snapshot for {author}'s journal companion: a compact, \
always-current answer to "if you could know only one page about this \
person's life right now, what would it say?"

Below are the last few weekly arc summaries (oldest first) and the most \
recent raw entries. Today is {today}.

Write the snapshot in about 350-450 words of plain prose grouped into short \
paragraphs covering: active relationships and where they stand, work \
situation, health, upcoming events, active emotional threads, and recent \
wins/struggles. Present tense, current state — this is "now," not a recap. \
Weight the most recent information most heavily.

<recent_weekly_arcs>
{arcs}
</recent_weekly_arcs>

<latest_entries>
{recent}
</latest_entries>"""


def week_key(date_str: str) -> str:
    """'2026-07-06' -> '2026-W28' (ISO week)."""
    d = date.fromisoformat(date_str)
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def group_by_week(conversations: list[dict]) -> dict:
    weeks = {}
    for conv in conversations:
        try:
            key = week_key(conv["date"])
        except ValueError:
            continue
        weeks.setdefault(key, []).append(conv)
    return weeks


def _week_hash(convs: list[dict]) -> str:
    joined = "\n".join(f"{c['date']}|{c['title']}|{len(c['text'])}" for c in convs)
    return hashlib.md5(joined.encode()).hexdigest()[:12]


def _arc_text(client, key: str, convs: list[dict]) -> str:
    text = "\n\n---\n\n".join(
        f"[{c['date']}] {c['title']}\n{c['text']}" for c in convs
    )[:ARC_INPUT_CHARS]
    dates = [c["date"] for c in convs]
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        **processing_thinking_kwargs(),
        messages=[{
            "role": "user",
            "content": ARC_PROMPT.format(
                author=AUTHOR, start=min(dates), end=max(dates), text=text,
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return next(b.text for b in response.content if b.type == "text").strip()


def build_arcs(force: bool = False, quiet: bool = False) -> int:
    """Generate/refresh weekly arcs. Returns how many were (re)generated."""
    client = get_client()
    ARC_DIR.mkdir(parents=True, exist_ok=True)
    weeks = group_by_week(get_conversations())
    regenerated = 0

    for key in sorted(weeks):
        convs = sorted(weeks[key], key=lambda c: c["date"])
        current_hash = _week_hash(convs)
        path = ARC_DIR / f"{key}.md"

        if path.exists() and not force:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            if current_hash in first_line:
                if not quiet:
                    print(f"  {key} (current)")
                continue
        try:
            arc = _arc_text(client, key, convs)
        except Exception as e:
            print(f"  {key} FAILED: {e}")
            continue
        dates = [c["date"] for c in convs]
        path.write_text(
            f"<!-- hash: {current_hash} -->\n"
            f"# Week {key} ({min(dates)} to {max(dates)})\n\n{arc}\n",
            encoding="utf-8",
        )
        regenerated += 1
        if not quiet:
            print(f"  {key} -> written")
    return regenerated


def build_domains(force: bool = False, quiet: bool = False) -> int:
    """Generate/refresh the per-category domain documents. Returns how
    many were (re)generated. Uses the category index (categories.py), so
    entries must be tagged first."""
    import categories as cats

    index = cats.load_index()
    client = get_client()
    DOMAIN_DIR.mkdir(parents=True, exist_ok=True)

    by_key = {conversation_cache_key(c): c for c in get_conversations()}
    regenerated = 0

    for name, definition in cats.CATEGORIES.items():
        tagged = sorted(
            ((key, rec) for key, rec in index["conversations"].items()
             if name in rec["categories"] and key in by_key),
            key=lambda p: p[1]["date"],
        )
        path = DOMAIN_DIR / f"{name}.md"
        if len(tagged) < MIN_DOMAIN_ENTRIES:
            if not quiet:
                print(f"  {name} (only {len(tagged)} entries — skipped)")
            continue

        current_hash = hashlib.md5("\n".join(
            f"{rec['date']}|{rec['title']}|{len(by_key[key]['text'])}"
            for key, rec in tagged
        ).encode()).hexdigest()[:12]

        if path.exists() and not force:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            if current_hash in first_line:
                if not quiet:
                    print(f"  {name} (current)")
                continue

        # newest entries win the char budget; render oldest-first
        picked, used = [], 0
        for key, rec in reversed(tagged):
            entry_text = by_key[key]["text"][:DOMAIN_ENTRY_CHARS]
            block = (
                f"[{rec['date']}] {rec['title']}"
                f" (tagged: {rec['categories'][name]})\n{entry_text}"
            )
            if used + len(block) > DOMAIN_INPUT_CHARS and picked:
                break
            picked.append(block)
            used += len(block)
        text = "\n\n---\n\n".join(reversed(picked))

        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                **processing_thinking_kwargs(),
                messages=[{
                    "role": "user",
                    "content": DOMAIN_PROMPT.format(
                        author=AUTHOR, category=name, definition=definition,
                        today=datetime.now().strftime("%Y-%m-%d"), text=text,
                    ),
                }],
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("model declined")
            doc = next(b.text for b in response.content if b.type == "text").strip()
        except Exception as e:
            print(f"  {name} FAILED: {e}")
            continue

        path.write_text(
            f"<!-- hash: {current_hash} -->\n"
            f"# {name} ({len(tagged)} entries, through {tagged[-1][1]['date']})\n\n{doc}\n",
            encoding="utf-8",
        )
        regenerated += 1
        if not quiet:
            print(f"  {name} -> written ({len(tagged)} entries)")
    return regenerated


def build_entry_summaries(force: bool = False, quiet: bool = False) -> int:
    """Generate/refresh the 2-3 sentence summary of each entry, cached in
    summaries/entries/{key}.json. Returns how many were (re)generated."""
    client = get_client()
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    regenerated = 0

    for conv in get_conversations():
        key = conversation_cache_key(conv)
        current_hash = hashlib.md5(
            f"{conv['date']}|{conv['title']}|{len(conv['text'])}".encode()
        ).hexdigest()[:12]
        path = ENTRY_DIR / f"{key}.json"

        if path.exists() and not force:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("hash") == current_hash:
                if not quiet:
                    print(f"  {key} (current)")
                continue
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=400,
                **processing_thinking_kwargs(),
                messages=[{
                    "role": "user",
                    "content": ENTRY_PROMPT.format(
                        author=AUTHOR, date=conv["date"], title=conv["title"],
                        text=conv["text"][:ENTRY_INPUT_CHARS],
                    ),
                }],
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("model declined")
            summary = next(b.text for b in response.content if b.type == "text").strip()
        except Exception as e:
            print(f"  {key} FAILED: {e}")
            continue
        path.write_text(json.dumps({
            "hash": current_hash, "key": key, "date": conv["date"],
            "title": conv["title"], "summary": summary,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        regenerated += 1
        if not quiet:
            print(f"  {key} -> summarized")
    return regenerated


def load_entry_summaries() -> list[dict]:
    if not ENTRY_DIR.exists():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(ENTRY_DIR.glob("*.json"))
    ]


def load_entry_summary(key: str) -> str | None:
    path = ENTRY_DIR / f"{Path(key).name}.json"  # basename only — no traversal
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("summary")
    return None


def _strip_hash_comment(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("<!--")
    ).strip()


def sync_summary_embeddings(quiet: bool = False) -> int:
    """Multi-granularity embeddings: mirror every summary layer into the
    journal_summaries collection (local embeddings, no API cost) so the
    companion can retrieve at entry / week / domain / entity zoom levels,
    not just chunk level. Returns the number of documents in the mirror."""
    from rag_journal import get_summary_collection

    collection = get_summary_collection()
    ids, docs, metas = [], [], []

    for e in load_entry_summaries():
        ids.append(f"entry:{e['key']}")
        docs.append(f"[{e['date']}] {e['title']}\n{e['summary']}")
        metas.append({"level": "entry summary", "date": e["date"], "title": e["title"]})

    for f in sorted(ARC_DIR.glob("*.md")) if ARC_DIR.exists() else []:
        ids.append(f"arc:{f.stem}")
        docs.append(_strip_hash_comment(f.read_text(encoding="utf-8"))[:EMBED_DOC_CHARS])
        metas.append({"level": "week arc", "week": f.stem})

    for f in sorted(DOMAIN_DIR.glob("*.md")) if DOMAIN_DIR.exists() else []:
        ids.append(f"domain:{f.stem}")
        docs.append(_strip_hash_comment(f.read_text(encoding="utf-8"))[:EMBED_DOC_CHARS])
        metas.append({"level": "domain summary", "domain": f.stem})

    index_file = ENTITY_DIR / "index.json"
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
        for name, info in index.items():
            doc_path = ENTITY_DIR / info["path"]
            if not doc_path.exists():
                continue
            ids.append(f"entity:{info['type']}:{name}")
            docs.append(doc_path.read_text(encoding="utf-8")[:EMBED_DOC_CHARS])
            metas.append({"level": "entity doc", "name": name, "type": info["type"]})

    stale = set(collection.get()["ids"]) - set(ids)
    if stale:
        collection.delete(ids=list(stale))
    if ids:
        collection.upsert(ids=ids, documents=docs, metadatas=metas)
    if not quiet:
        print(f"  summary embeddings: {len(ids)} documents ({len(stale)} stale removed)")
    return len(ids)


def load_domain_doc(name: str) -> str | None:
    path = DOMAIN_DIR / f"{Path(name).name}.md"  # basename only — no traversal
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def build_snapshot(quiet: bool = False):
    client = get_client()
    arc_files = sorted(ARC_DIR.glob("*.md"))[-SNAPSHOT_RECENT_ARCS:]
    if not arc_files:
        print("  no arcs yet — run build first")
        return
    arcs = "\n\n".join(f.read_text(encoding="utf-8") for f in arc_files)

    conversations = sorted(get_conversations(), key=lambda c: c["date"])
    recent = "\n\n---\n\n".join(
        f"[{c['date']}] {c['title']}\n{c['text']}" for c in conversations[-3:]
    )[:25_000]

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        **processing_thinking_kwargs(),
        messages=[{
            "role": "user",
            "content": SNAPSHOT_PROMPT.format(
                author=AUTHOR, today=datetime.now().strftime("%Y-%m-%d"),
                arcs=arcs, recent=recent,
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined snapshot")
    snapshot = next(b.text for b in response.content if b.type == "text").strip()
    SNAPSHOT_FILE.write_text(
        f"<!-- generated: {datetime.now().isoformat(timespec='minutes')} -->\n"
        f"# Status snapshot\n\n{snapshot}\n",
        encoding="utf-8",
    )
    append_dream_weather()
    if not quiet:
        print("  status snapshot -> written")


def append_dream_weather():
    """Add (or refresh) the one-line dream-weather signal at the bottom of
    the snapshot. The companion glances at dream tone without loading any
    dream content into waking context."""
    try:
        from dreams import dream_weather
        weather = dream_weather()
    except Exception:
        return
    if not weather or not SNAPSHOT_FILE.exists():
        return
    lines = [
        l for l in SNAPSHOT_FILE.read_text(encoding="utf-8").splitlines()
        if not l.startswith("Dream weather")
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", weather, ""]
    SNAPSHOT_FILE.write_text("\n".join(lines), encoding="utf-8")


def build(force: bool = False, quiet: bool = False) -> dict:
    # the status snapshot is retired from the pipeline — the co-edited seed
    # summary is Layer 1 now (build_snapshot stays for manual fallback:
    # python summarizer.py snapshot)
    n = build_arcs(force=force, quiet=quiet)
    d = build_domains(force=force, quiet=quiet)
    e = build_entry_summaries(force=force, quiet=quiet)
    embedded = sync_summary_embeddings(quiet=quiet)
    arcs_total = len(list(ARC_DIR.glob("*.md")))
    domains_total = len(list(DOMAIN_DIR.glob("*.md"))) if DOMAIN_DIR.exists() else 0
    print(f"\n  Summaries: {arcs_total} weekly arcs ({n} regenerated) + "
          f"{domains_total} domains ({d} regenerated) + "
          f"{e} entry summaries regenerated; "
          f"{embedded} docs in the summary index")
    return {"arcs": arcs_total, "regenerated": n,
            "domains": domains_total, "domains_regenerated": d,
            "entry_summaries_regenerated": e, "summary_index_docs": embedded}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(force="--force" in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        build_snapshot()
    else:
        print(__doc__)
