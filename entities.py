"""
Entity Graph — Phase 2 of the RAG Journal.

Extracts people, projects, and places from imported journal conversations
using the Claude API, then aggregates them into per-entity markdown docs
that the companion loads as context when an entity is mentioned (Layer 3
of the retrieval architecture in persona-spec.md).

Usage:
    python entities.py build            # extract + build entity docs
    python entities.py build --force    # re-extract even if cached
    python entities.py list             # show the entity index

Layout (all gitignored — this is personal data):
    entity_graph/raw/       per-conversation extraction JSON (API-call cache)
    entity_graph/people/    one markdown doc per person
    entity_graph/projects/  one markdown doc per project
    entity_graph/places/    one markdown doc per place
    entity_graph/index.json name -> doc path, used by the companion
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from rag_journal import get_collection

MODEL = "claude-opus-4-8"
ENTITY_DIR = Path(__file__).parent / "entity_graph"
RAW_DIR = ENTITY_DIR / "raw"
CURATION_FILE = ENTITY_DIR / "curation.json"
MAX_ENTRY_CHARS = 60_000  # cap per-conversation text sent to the API

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "relationship": {
                        "type": "string",
                        "description": "Relationship to the journal author, e.g. friend, ex, coworker, mother, date",
                    },
                    "observations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Concrete facts or events involving this person in this entry",
                    },
                },
                "required": ["name", "relationship", "observations"],
                "additionalProperties": False,
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "domain": {
                        "type": "string",
                        "enum": ["work", "personal", "creative"],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "paused", "completed", "abandoned", "unknown"],
                    },
                    "observations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "domain", "status", "observations"],
                "additionalProperties": False,
            },
        },
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "description": "Type of place, e.g. home, venue, city, workplace",
                    },
                    "observations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "kind", "observations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["people", "projects", "places"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """\
You are building an entity graph from one person's journal. Below is one \
journal entry (originally a conversation with an AI companion; only the \
author's side is included), written on {date}.

Extract the PEOPLE, PROJECTS, and PLACES that actually appear.

Rules:
- Do not include the journal author themselves, or the AI companion.
- Use the name the author uses ("Pip", "Mom", "Wren") — normalize \
"my mom" to "Mom" but don't invent formal names.
- Only include people who are actually part of the author's life or story — \
skip celebrities or public figures mentioned in passing.
- Projects are ongoing efforts with a name or clear identity (a work project, \
an app they're building, a creative pursuit). One-off tasks don't count.
- Places only if they matter to the story (venues, cities visited, home) — \
not incidental mentions.
- Observations are short, concrete, dated-to-this-entry facts: what happened, \
what the author said or felt about the entity. 1-4 per entity, most \
significant first.
- If the entry describes a dream, note it in the observation ("in a dream: ...").

<journal_entry date="{date}" title="{title}">
{text}
</journal_entry>"""


# ---------------------------------------------------------------------------
# GATHERING CONVERSATIONS FROM THE VECTOR STORE
# ---------------------------------------------------------------------------

def get_conversations() -> list[dict]:
    """
    Reassemble full conversations from stored chunks, grouped by
    (date, title), chunks ordered by their index.
    """
    collection = get_collection()
    data = collection.get(include=["documents", "metadatas"])

    groups = defaultdict(list)
    for doc_id, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        key = (meta.get("date", "?"), meta.get("title", "Untitled"))
        match = re.search(r"_c(\d+)$", doc_id)
        chunk_idx = int(match.group(1)) if match else 0
        groups[key].append((chunk_idx, doc))

    conversations = []
    for (date, title), chunks in sorted(groups.items()):
        chunks.sort(key=lambda c: c[0])
        text = "\n\n".join(doc for _, doc in chunks)
        conversations.append({"date": date, "title": title, "text": text})
    return conversations


def conversation_cache_key(conv: dict) -> str:
    safe_title = re.sub(r"[^a-zA-Z0-9-]+", "-", conv["title"])[:40].strip("-")
    return f"{conv['date']}_{safe_title}"


# ---------------------------------------------------------------------------
# EXTRACTION (one API call per conversation, cached to disk)
# ---------------------------------------------------------------------------

def extract_conversation(client, conv: dict) -> dict:
    """Extract entities from one conversation via the Claude API."""
    text = conv["text"][:MAX_ENTRY_CHARS]
    prompt = EXTRACTION_PROMPT.format(date=conv["date"], title=conv["title"], text=text)

    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined this entry")
    raw = next(b.text for b in response.content if b.type == "text")
    return json.loads(raw)


def run_extraction(force: bool = False, quiet: bool = False) -> list[dict]:
    """
    Extract entities from every conversation, caching per-conversation
    results in entity_graph/raw/ so re-runs don't re-call the API.
    Returns a list of {date, title, entities} records.
    """
    client = anthropic.Anthropic()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    conversations = get_conversations()
    if not quiet:
        print(f"  {len(conversations)} conversations to process")

    records = []
    for i, conv in enumerate(conversations):
        cache_file = RAW_DIR / f"{conversation_cache_key(conv)}.json"
        label = f"[{i + 1}/{len(conversations)}] {conv['date']} {conv['title'][:40]}"

        if cache_file.exists() and not force:
            entities = json.loads(cache_file.read_text(encoding="utf-8"))
            if not quiet:
                print(f"  {label} (cached)")
        else:
            try:
                entities = extract_conversation(client, conv)
            except Exception as e:
                print(f"  {label} FAILED: {e}")
                continue
            cache_file.write_text(
                json.dumps(entities, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            n = sum(len(entities.get(k, [])) for k in ("people", "projects", "places"))
            print(f"  {label} -> {n} entities")

        records.append({"date": conv["date"], "title": conv["title"], "entities": entities})
    return records


# ---------------------------------------------------------------------------
# CURATION (user corrections that survive rebuilds)
# ---------------------------------------------------------------------------
# curation.json:
#   "merge":  {"project:orbit-web": "Orbit", ...}   kind:lowername -> canonical
#   "delete": ["place:teddy bear", ...]                kind:lowername

def load_curation() -> dict:
    if CURATION_FILE.exists():
        return json.loads(CURATION_FILE.read_text(encoding="utf-8"))
    return {"merge": {}, "delete": []}


def save_curation(curation: dict):
    ENTITY_DIR.mkdir(parents=True, exist_ok=True)
    CURATION_FILE.write_text(
        json.dumps(curation, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def curation_key(kind: str, name: str) -> str:
    return f"{kind}:{name.lower()}"


# ---------------------------------------------------------------------------
# AGGREGATION INTO ENTITY DOCS
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unnamed"


def build_entity_docs(records: list[dict]) -> dict:
    """
    Merge per-conversation extractions into one markdown doc per entity.
    Returns the entity index {name: {type, path, mentions}}.
    """
    curation = load_curation()
    merge_map = {k.lower(): v for k, v in curation.get("merge", {}).items()}
    deleted = {d.lower() for d in curation.get("delete", [])}

    # merged[(kind, name_lower)] = {name, kind, attr, aliases, timeline}
    merged = {}
    kind_fields = {
        "people": ("person", "relationship"),
        "projects": ("project", "status"),
        "places": ("place", "kind"),
    }

    for record in records:
        for group, (kind, attr_field) in kind_fields.items():
            for ent in record["entities"].get(group, []):
                name = ent.get("name", "").strip()
                if not name:
                    continue
                if curation_key(kind, name) in deleted:
                    continue
                canonical = merge_map.get(curation_key(kind, name))
                display = canonical or name
                if curation_key(kind, display) in deleted:
                    continue
                key = (kind, display.lower())
                if key not in merged:
                    merged[key] = {
                        "name": display, "kind": kind, "attr": "",
                        "aliases": set(), "timeline": [],
                    }
                if canonical and name.lower() != display.lower():
                    merged[key]["aliases"].add(name)
                attr = (ent.get(attr_field) or "").strip()
                if attr and attr != "unknown":
                    merged[key]["attr"] = attr  # latest wins
                merged[key]["timeline"].append(
                    (record["date"], record["title"], ent.get("observations", []))
                )

    attr_labels = {"person": "relationship", "project": "status", "place": "type"}
    index = {}

    # Clear generated docs so merged/deleted entities don't leave stale files
    for kind_dir in ("people", "projects", "places"):
        for old in (ENTITY_DIR / kind_dir).glob("*.md"):
            old.unlink()

    for (kind, _), ent in sorted(merged.items()):
        ent["timeline"].sort(key=lambda t: t[0])
        dates = [t[0] for t in ent["timeline"]]

        subdir = ENTITY_DIR / f"{kind}s" if kind != "person" else ENTITY_DIR / "people"
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{slugify(ent['name'])}.md"

        lines = [
            "---",
            f"name: {ent['name']}",
            f"type: {kind}",
            f"{attr_labels[kind]}: {ent['attr'] or 'unknown'}",
            f"first_seen: {dates[0]}",
            f"last_seen: {dates[-1]}",
            f"mentions: {len(ent['timeline'])}",
        ]
        if ent["aliases"]:
            lines.append(f"aliases: {', '.join(sorted(ent['aliases']))}")
        lines += ["---", ""]
        for date, title, observations in ent["timeline"]:
            lines.append(f"### {date} — {title}")
            for obs in observations:
                lines.append(f"- {obs}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

        index[ent["name"]] = {
            "type": kind,
            "path": str(path.relative_to(ENTITY_DIR)),
            "mentions": len(ent["timeline"]),
            "aliases": sorted(ent["aliases"]),
        }

    (ENTITY_DIR / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return index


def build(force: bool = False, quiet: bool = False) -> dict:
    records = run_extraction(force=force, quiet=quiet)
    index = build_entity_docs(records)
    by_kind = defaultdict(int)
    for info in index.values():
        by_kind[info["type"]] += 1
    print(
        f"\n  Entity graph built: {by_kind['person']} people, "
        f"{by_kind['project']} projects, {by_kind['place']} places"
    )
    if not quiet:
        print(f"  Docs in {ENTITY_DIR}")
    return index


def show_index():
    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        print("  No entity graph yet — run: python entities.py build")
        return
    index = json.loads(index_file.read_text(encoding="utf-8"))
    for kind in ("person", "project", "place"):
        names = sorted(
            (n for n, i in index.items() if i["type"] == kind),
            key=lambda n: -index[n]["mentions"],
        )
        if names:
            print(f"\n  {kind}s ({len(names)}):")
            for name in names:
                print(f"    {name} ({index[name]['mentions']} mentions)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(force="--force" in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        show_index()
    else:
        print(__doc__)
