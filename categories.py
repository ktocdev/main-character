"""
Category System — Phase 2 of the RAG Journal.

Tags every journal conversation with the built-in life-domain categories
from persona-spec.md §5 using the Claude API. Entries can span multiple
categories. Tags are cached per conversation, written into the vector
store's chunk metadata (so retrieval can filter by domain later), and
indexed for the web UI's categories tab.

the user's manual tag fixes live in categories/overrides.json and re-apply
on every rebuild — same philosophy as entity curation.

Usage:
    python categories.py build            # tag untagged conversations
    python categories.py build --force    # re-tag everything
    python categories.py list             # show category counts

Layout (all gitignored — this is personal data):
    categories/raw/            per-conversation tag JSON (API-call cache)
    categories/overrides.json  manual add/remove fixes, survive rebuilds
    categories/index.json      category -> entries map, used by the UI
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from entities import get_conversations, conversation_cache_key, _segments
from rag_journal import get_collection

MODEL = "claude-opus-4-8"
CATEGORY_DIR = Path(__file__).parent / "categories"
RAW_DIR = CATEGORY_DIR / "raw"
OVERRIDES_FILE = CATEGORY_DIR / "overrides.json"
INDEX_FILE = CATEGORY_DIR / "index.json"
AUTHOR = os.getenv("RAG_AUTHOR_NAME", "").strip() or "the journal author"

# The built-in categories from persona-spec.md §5. Organic/emergent
# categories are a later roadmap item and deliberately not handled here.
CATEGORIES = {
    "work": "job, career, coworkers, job searching, professional projects and stress",
    "dating": "dates, romantic interest, relationships, breakups, exes in a romantic context",
    "health": "physical or mental health, injuries, illness, sleep, medication, exercise, doctors",
    "creative": "art, music, singing, drawing, writing, coding side projects, creative hobbies",
    "social": "friends, outings, parties, bars, shows, events, plans with people",
    "family": "parents, siblings, nieces and nephews, extended family",
    "pets": "the author's own pets and animals that matter in their life",
    "emotional": "processing feelings, mood, anxiety, loneliness, gratitude, self-reflection",
    "ai_reflection": "thoughts about AI, this journal or companion itself, relationships with technology",
    "home": "apartment, housing, moving, chores, neighbors, domestic life",
}

TAG_PROMPT = """\
You are tagging one of {author}'s journal entries with life-domain \
categories. The entry was originally a conversation with an AI companion; \
only {author}'s side is included.

Assign only the categories that are real subjects of this entry — threads \
it spends real time on, events that happened, decisions being processed. \
The test for each category: if {author} browsed that category to re-read \
what's been going on in that part of life, does this entry belong there? \
A passing mention never qualifies — one sentence about work inside an \
entry about a date is not a "work" entry; a pet walking through a scene is \
not a "pets" entry. Most entries earn 2-4 tags; if you are about to assign \
more than 5, you are over-tagging — keep only the main threads. List them \
most-central first, and for each give one short evidence phrase (under 12 \
words) naming what earned it.

Categories:
{definitions}

<entry date="{date}" title="{title}">
{text}
</entry>"""

TAG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": list(CATEGORIES)},
                    "evidence": {
                        "type": "string",
                        "description": "Short phrase: what in the entry earned this tag",
                    },
                },
                "required": ["name", "evidence"],
            },
        },
    },
    "required": ["categories"],
}


# ---------------------------------------------------------------------------
# TAGGING (one API call per conversation, cached to disk)
# ---------------------------------------------------------------------------

def tag_conversation(client, conv: dict) -> dict:
    """Tag one conversation. Returns {category_name: evidence}."""
    definitions = "\n".join(f"- {name}: {desc}" for name, desc in CATEGORIES.items())
    tags = {}
    for segment in _segments(conv["text"]):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"format": {"type": "json_schema", "schema": TAG_SCHEMA}},
            messages=[{
                "role": "user",
                "content": TAG_PROMPT.format(
                    author=AUTHOR, definitions=definitions,
                    date=conv["date"], title=conv["title"], text=segment,
                ),
            }],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined this entry")
        raw = next(b.text for b in response.content if b.type == "text")
        # results are ordered most-central first; cap as a backstop against
        # over-tagging (a tag on everything is a tag on nothing)
        for item in json.loads(raw).get("categories", [])[:6]:
            tags.setdefault(item["name"], item["evidence"])
    return tags


def run_tagging(force: bool = False, quiet: bool = False) -> int:
    """Tag every conversation, using the per-conversation cache. Returns
    how many were newly tagged (API calls made)."""
    client = anthropic.Anthropic()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    conversations = get_conversations()
    new = 0
    for i, conv in enumerate(conversations):
        cache_file = RAW_DIR / f"{conversation_cache_key(conv)}.json"
        label = f"[{i + 1}/{len(conversations)}] {conv['date']} {conv['title'][:40]}"

        if cache_file.exists() and not force:
            if not quiet:
                print(f"  {label} (cached)")
            continue
        try:
            tags = tag_conversation(client, conv)
        except Exception as e:
            print(f"  {label} FAILED: {e}")
            continue
        cache_file.write_text(
            json.dumps(tags, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        new += 1
        if not quiet:
            print(f"  {label} -> {', '.join(tags) or '(none)'}")
    return new


# ---------------------------------------------------------------------------
# OVERRIDES (manual tag fixes that survive rebuilds)
# ---------------------------------------------------------------------------
# overrides.json: {cache_key: {"add": [names], "remove": [names]}}

def load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    return {}


def save_overrides(overrides: dict):
    CATEGORY_DIR.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(
        json.dumps(overrides, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def set_tag(key: str, name: str, present: bool) -> dict:
    """Manually add or remove a tag on one conversation. Records the fix
    in overrides.json so it survives re-tagging, then rebuilds the index
    and chunk metadata. Returns the fresh index."""
    if name not in CATEGORIES:
        raise ValueError(f"unknown category '{name}'")

    raw_tags = set()
    cache_file = RAW_DIR / f"{Path(key).name}.json"  # basename only — no traversal
    if cache_file.exists():
        raw_tags = set(json.loads(cache_file.read_text(encoding="utf-8")))

    overrides = load_overrides()
    ov = overrides.setdefault(key, {"add": [], "remove": []})
    if present:
        ov["remove"] = [n for n in ov["remove"] if n != name]
        if name not in raw_tags and name not in ov["add"]:
            ov["add"].append(name)
    else:
        ov["add"] = [n for n in ov["add"] if n != name]
        if name in raw_tags and name not in ov["remove"]:
            ov["remove"].append(name)
    if not ov["add"] and not ov["remove"]:
        overrides.pop(key)
    save_overrides(overrides)

    index = build_index()
    update_chroma(index)
    return index


# ---------------------------------------------------------------------------
# INDEX + CHUNK METADATA
# ---------------------------------------------------------------------------

def build_index() -> dict:
    """Merge raw tags + overrides into categories/index.json."""
    overrides = load_overrides()
    counts = {name: 0 for name in CATEGORIES}
    convs = {}
    tagged = 0

    conversations = get_conversations()
    for conv in conversations:
        key = conversation_cache_key(conv)
        cache_file = RAW_DIR / f"{key}.json"
        tags = {}
        if cache_file.exists():
            tagged += 1
            tags = json.loads(cache_file.read_text(encoding="utf-8"))
        ov = overrides.get(key, {})
        for name in ov.get("remove", []):
            tags.pop(name, None)
        for name in ov.get("add", []):
            tags.setdefault(name, "(added by you)")
        convs[key] = {"date": conv["date"], "title": conv["title"], "categories": tags}
        for name in tags:
            if name in counts:
                counts[name] += 1

    index = {
        "conversations": convs, "counts": counts,
        "tagged": tagged, "total": len(conversations),
    }
    CATEGORY_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return index


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return build_index()


def update_chroma(index: dict):
    """Write category tags into every chunk's metadata: a display string
    ("categories") plus one boolean per category ("cat_work") so retrieval
    can filter with where={"cat_work": True}. All ten booleans are always
    written — Chroma's update merges metadata rather than replacing it, so
    a stale True can only be cleared by an explicit False."""
    collection = get_collection()
    data = collection.get(include=["metadatas"])

    by_conv = defaultdict(list)
    for doc_id, meta in zip(data["ids"], data["metadatas"]):
        by_conv[(meta.get("date", "?"), meta.get("title", "Untitled"))].append((doc_id, meta))

    ids, metas = [], []
    for rec in index["conversations"].values():
        names = set(rec["categories"])
        for doc_id, meta in by_conv.get((rec["date"], rec["title"]), []):
            new_meta = dict(meta)
            new_meta["categories"] = ", ".join(sorted(names))
            for name in CATEGORIES:
                new_meta[f"cat_{name}"] = name in names
            if new_meta != meta:
                ids.append(doc_id)
                metas.append(new_meta)
    if ids:
        collection.update(ids=ids, metadatas=metas)


def build(force: bool = False, quiet: bool = False) -> dict:
    new = run_tagging(force=force, quiet=quiet)
    index = build_index()
    update_chroma(index)
    print(f"\n  Categories: {index['tagged']}/{index['total']} conversations tagged ({new} new)")
    return {"new": new, "tagged": index["tagged"], "total": index["total"],
            "counts": index["counts"]}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(force="--force" in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        index = load_index()
        print(f"\n  {index['tagged']}/{index['total']} conversations tagged\n")
        for name, count in sorted(index["counts"].items(), key=lambda c: -c[1]):
            print(f"  {count:3}  {name}")
    else:
        print(__doc__)
