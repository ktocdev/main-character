# SPDX-License-Identifier: AGPL-3.0-or-later
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


from config import AUTHOR, ENTITY_DIR, MC_PROCESSING_MODEL as MODEL, get_client, processing_thinking_kwargs
from rag_journal import get_collection

RAW_DIR = ENTITY_DIR / "raw"
CURATION_FILE = ENTITY_DIR / "curation.json"
GROUPS_FILE = ENTITY_DIR / "groups.json"
SEGMENT_CHARS = 45_000  # long conversations are split, not truncated

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
You are building an entity graph from one person's journal. The journal \
author is {author}. Below is one journal entry (originally a conversation \
with an AI companion; only the author's side is included), written on {date}.

Extract the PEOPLE, PROJECTS, and PLACES that actually appear.

Rules:
- Never include the author ({author}) themselves — first-person statements \
are about the author, not an entity. Never include the AI companion.
- Never include celebrities or public figures, even if discussed at length. \
Only people the author actually knows or encounters.
- Use the shortest natural name the author uses ("Pip", "Mom", "Orbit \
Web"). No descriptive parentheticals, no slashes, no combined names — if \
two things are mentioned, they are two entities.
{known_block}- Capture EVERY concrete mention as its own observation — one per distinct \
fact or event, however minor or recurring (a pet making a mess counts, every \
time it happens). Short, factual, no editorializing.
- Projects are ongoing named efforts and pursuits (a work project, an app, \
a class, a creative pursuit) — including named games, shows, or hobbies the \
author returns to repeatedly (e.g. a video game they keep playing). One-off \
tasks don't count.
- Places are physical locations that matter to the story (venues, bars, \
cities, homes) — not incidental geography.
- If something happens in a dream, prefix the observation with "in a dream:".

<journal_entry date="{date}" title="{title}">
{text}
</journal_entry>"""

KNOWN_BLOCK = """\
- These people are already known from earlier entries — when a mention \
matches one of them, use exactly this spelling: {names}.
"""


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

def _segments(text: str, size: int = SEGMENT_CHARS) -> list[str]:
    """Split long text into segments at paragraph boundaries (no truncation)."""
    if len(text) <= size:
        return [text]
    segments, current, length = [], [], 0
    for para in text.split("\n\n"):
        if length + len(para) > size and current:
            segments.append("\n\n".join(current))
            current, length = [], 0
        current.append(para)
        length += len(para) + 2
    if current:
        segments.append("\n\n".join(current))
    return segments


def known_people_hint() -> list[str]:
    """Canonical people names from the current index, for name consistency."""
    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        return []
    index = json.loads(index_file.read_text(encoding="utf-8"))
    people = sorted(
        ((i["mentions"], n) for n, i in index.items() if i["type"] == "person"),
        reverse=True,
    )
    return [n for _, n in people[:80]]


def extract_conversation(client, conv: dict, known_people: list[str] | None = None) -> dict:
    """Extract entities from one conversation via the Claude API."""
    known_block = (
        KNOWN_BLOCK.format(names=", ".join(known_people)) if known_people else ""
    )
    combined = {"people": [], "projects": [], "places": []}
    for segment in _segments(conv["text"]):
        prompt = EXTRACTION_PROMPT.format(
            author=AUTHOR, date=conv["date"], title=conv["title"],
            text=segment, known_block=known_block,
        )
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            **processing_thinking_kwargs(),
            output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined this entry")
        raw = next(b.text for b in response.content if b.type == "text")
        data = json.loads(raw)
        for group in combined:
            combined[group].extend(data.get(group, []))
    return combined


def run_extraction(force: bool = False, quiet: bool = False) -> list[dict]:
    """
    Extract entities from every conversation, caching per-conversation
    results in entity_graph/raw/ so re-runs don't re-call the API.
    Returns a list of {date, title, entities} records.
    """
    client = get_client()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    conversations = get_conversations()
    if not quiet:
        print(f"  {len(conversations)} conversations to process")
    known = known_people_hint()

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
                entities = extract_conversation(client, conv, known_people=known)
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
# curation.json fields (keys are "kind:lowername"):
#   "merge":        {key: canonical-or-"kind:Name"}  combine; old name kept as alias
#   "correct":      {key: canonical-or-"kind:Name"}  typo fix; NO alias recorded
#   "retype":       {key: {"type": kind, "name": optional new name}}
#   "alias_add":    {key: [names]}   extra aliases for matching
#   "alias_remove": {key: [names]}   suppress unwanted aliases
#   "delete":       [keys]
#
# merge/correct targets may be kind-qualified ("person:Dr. Reyes") to
# move an entity across kinds while combining.

KINDS = ("person", "project", "place")
_CURATION_DEFAULTS = {
    "merge": {}, "correct": {}, "retype": {}, "rename": {},
    "alias_add": {}, "alias_remove": {}, "delete": [],
    "reviewed": [],        # entity keys the user has marked as checked
    "not_duplicates": [],  # dismissed duplicate-pair keys ("kind:a|b")
}


def load_curation() -> dict:
    curation = dict(_CURATION_DEFAULTS)
    if CURATION_FILE.exists():
        stored = json.loads(CURATION_FILE.read_text(encoding="utf-8"))
        for field, default in _CURATION_DEFAULTS.items():
            curation[field] = stored.get(field, default if isinstance(default, list) else dict(default))
    else:
        curation = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in _CURATION_DEFAULTS.items()}
    return curation


def save_curation(curation: dict):
    ENTITY_DIR.mkdir(parents=True, exist_ok=True)
    CURATION_FILE.write_text(
        json.dumps(curation, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def curation_key(kind: str, name: str) -> str:
    return f"{kind}:{name.lower()}"


# ---------------------------------------------------------------------------
# GROUPS (user-made collections of entities, for viewing and associating)
# ---------------------------------------------------------------------------
# groups.json: [{"name": str, "parent": str ("" = root), "members": [names]}]
# Members are entity display names, resolved through aliases at read time —
# so merges and renames (which keep the old name as an alias) never break a
# membership, and a deleted entity's membership sits dormant until an undo
# brings the entity back.

def load_groups() -> list[dict]:
    if GROUPS_FILE.exists():
        return json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    return []


def save_groups(groups: list[dict]):
    ENTITY_DIR.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(
        json.dumps(groups, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def find_group(groups: list[dict], name: str) -> dict | None:
    lname = name.strip().lower()
    return next((g for g in groups if g["name"].lower() == lname), None)


def group_path(groups: list[dict], name: str) -> str:
    """Breadcrumb display name: 'Landmarks › Gardens'."""
    chain, seen = [], set()
    current = find_group(groups, name)
    while current and current["name"].lower() not in seen:
        seen.add(current["name"].lower())
        chain.append(current["name"])
        current = find_group(groups, current["parent"]) if current["parent"] else None
    return " › ".join(reversed(chain)) or name


def group_would_cycle(groups: list[dict], name: str, parent: str) -> bool:
    """Would setting `name`'s parent to `parent` create a loop?"""
    lname = name.strip().lower()
    seen = set()
    current = find_group(groups, parent)
    while current:
        if current["name"].lower() == lname:
            return True
        if current["name"].lower() in seen:
            return True  # pre-existing corruption; refuse to extend it
        seen.add(current["name"].lower())
        current = find_group(groups, current["parent"]) if current["parent"] else None
    return False


def group_descendants(groups: list[dict], name: str) -> set[str]:
    """Lowercase names of the group plus all transitive children."""
    result = {name.strip().lower()}
    changed = True
    while changed:
        changed = False
        for g in groups:
            if g["parent"].lower() in result and g["name"].lower() not in result:
                result.add(g["name"].lower())
                changed = True
    return result


def _parse_target(value: str, default_kind: str) -> tuple[str, str]:
    """A merge/correct target may be 'Name' or 'kind:Name'."""
    if ":" in value:
        prefix, rest = value.split(":", 1)
        if prefix in KINDS:
            return prefix, rest.strip()
    return default_kind, value.strip()


def apply_curation(curation: dict, kind: str, name: str):
    """
    Resolve one extracted (kind, name) through the curation rules.
    Returns (kind, canonical_name, alias_of_canonical: bool) or None if deleted.
    """
    if curation_key(kind, name) in {d.lower() for d in curation["delete"]}:
        return None

    rt = curation["retype"].get(curation_key(kind, name))
    if rt:
        kind = rt.get("type", kind)
        name = rt.get("name") or name

    key = curation_key(kind, name)
    is_alias = False
    if key in curation["correct"]:
        kind, name = _parse_target(curation["correct"][key], kind)
    elif key in curation["merge"]:
        new_kind, new_name = _parse_target(curation["merge"][key], kind)
        is_alias = new_name.lower() != name.lower()
        kind, name = new_kind, new_name

    # rename controls display spelling (incl. case-only changes: book club ->
    # Book Club); a rename that changes more than case keeps the old name as
    # an alias so matching still works
    rn = curation["rename"].get(curation_key(kind, name))
    if rn:
        if rn.lower() != name.lower():
            is_alias = True
        name = rn

    if curation_key(kind, name) in {d.lower() for d in curation["delete"]}:
        return None
    return kind, name, is_alias


# ---------------------------------------------------------------------------
# AGGREGATION INTO ENTITY DOCS
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    # Invariant: any place a model-produced string (entity name, category
    # name, ...) becomes part of a filesystem path goes through this. It's
    # the only thing standing between a crafted journal entry and path
    # traversal, since entity names originate in model output over
    # imported/pasted text, not from a fixed set the app controls.
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unnamed"


def build_entity_docs(records: list[dict]) -> dict:
    """
    Merge per-conversation extractions into one markdown doc per entity.
    Returns the entity index {name: {type, path, mentions}}.
    """
    curation = load_curation()

    # merged[(kind, name_lower)] = {name, kind, attr, aliases, timeline}
    merged = {}
    kind_fields = {
        "people": ("person", "relationship"),
        "projects": ("project", "status"),
        "places": ("place", "kind"),
    }
    attr_for_kind = {kind: attr for _, (kind, attr) in kind_fields.items()}

    for record in records:
        for group, (kind, attr_field) in kind_fields.items():
            for ent in record["entities"].get(group, []):
                name = ent.get("name", "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, kind, name)
                if resolved is None:
                    continue
                final_kind, display, is_alias = resolved
                key = (final_kind, display.lower())
                if key not in merged:
                    merged[key] = {
                        "name": display, "kind": final_kind, "attr": "",
                        "aliases": set(), "timeline": [],
                    }
                if is_alias:
                    merged[key]["aliases"].add(name)
                # attr only carries over within the same kind (a person's
                # relationship isn't a place's type)
                if final_kind == kind:
                    attr = (ent.get(attr_field) or "").strip()
                    if attr and attr != "unknown":
                        merged[key]["attr"] = attr  # latest wins
                merged[key]["timeline"].append(
                    (record["date"], record["title"], ent.get("observations", []))
                )

    # manual alias adjustments
    for key_str, names in curation["alias_add"].items():
        kind, _, lname = key_str.partition(":")
        if (kind, lname) in merged:
            merged[(kind, lname)]["aliases"].update(names)
    for key_str, names in curation["alias_remove"].items():
        kind, _, lname = key_str.partition(":")
        if (kind, lname) in merged:
            drop = {n.lower() for n in names}
            merged[(kind, lname)]["aliases"] = {
                a for a in merged[(kind, lname)]["aliases"] if a.lower() not in drop
            }
    for ent in merged.values():
        ent["aliases"] = {a for a in ent["aliases"] if a.lower() != ent["name"].lower()}

    # group memberships, resolved through names + aliases
    groups = load_groups()
    name_lookup = {}  # lowercase name/alias -> canonical display name
    for ent in merged.values():
        name_lookup[ent["name"].lower()] = ent["name"]
        for a in ent["aliases"]:
            name_lookup.setdefault(a.lower(), ent["name"])
    entity_groups: dict[str, list[str]] = {}  # canonical name -> [group names]
    for g in groups:
        for member in g["members"]:
            canon = name_lookup.get(member.lower())
            if canon and g["name"] not in entity_groups.get(canon, []):
                entity_groups.setdefault(canon, []).append(g["name"])

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
        gnames = sorted(entity_groups.get(ent["name"], []), key=str.lower)
        if gnames:
            lines.append(
                f"groups: {', '.join(group_path(groups, g) for g in gnames)}"
            )
        lines += ["---", ""]
        for date, title, observations in ent["timeline"]:
            lines.append(f"### {date} — {title}")
            for obs in observations:
                lines.append(f"- {obs}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

        index[ent["name"]] = {
            "type": kind,
            "path": path.relative_to(ENTITY_DIR).as_posix(),
            "mentions": len(ent["timeline"]),
            "aliases": sorted(ent["aliases"]),
            "groups": gnames,
            "reviewed": curation_key(kind, ent["name"]) in {
                r.lower() for r in curation["reviewed"]
            },
        }

    (ENTITY_DIR / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return index


# ---------------------------------------------------------------------------
# UNDO / REDO HISTORY
# ---------------------------------------------------------------------------
# Every curation or observation mutation records a before/after snapshot.
# Curation snapshots are the whole curation dict (small); observation
# snapshots are the affected raw file's content.

HISTORY_FILE = ENTITY_DIR / "history.json"
HISTORY_LIMIT = 50


def _load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {"undo": [], "redo": []}


def _save_history(history: dict):
    history["undo"] = history["undo"][-HISTORY_LIMIT:]
    history["redo"] = history["redo"][-HISTORY_LIMIT:]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False), encoding="utf-8"
    )


def record_change(description: str, kind: str, before, after, filename: str = ""):
    """kind: 'curation' (before/after are curation dicts), 'groups'
    (before/after are the groups list), or 'raw' (file text)."""
    history = _load_history()
    history["undo"].append({
        "description": description, "kind": kind,
        "before": before, "after": after, "file": filename,
    })
    history["redo"] = []  # a new change invalidates the redo stack
    _save_history(history)


def _apply_snapshot(entry: dict, direction: str):
    payload = entry[direction]
    if entry["kind"] == "curation":
        save_curation(payload)
    elif entry["kind"] == "groups":
        # rollup is a view preference, not journal data — carry the live flags
        # across so undo/redo of membership/nesting never toggles a group's
        # collapsed state (copy first so the stored snapshot stays untouched)
        live = {g["name"].lower(): g.get("rollup", False) for g in load_groups()}
        payload = json.loads(json.dumps(payload))
        for g in payload:
            if live.get(g["name"].lower()):
                g["rollup"] = True
            else:
                g.pop("rollup", None)
        save_groups(payload)
    else:  # raw file content
        (RAW_DIR / Path(entry["file"]).name).write_text(payload, encoding="utf-8")


def undo() -> str | None:
    history = _load_history()
    if not history["undo"]:
        return None
    entry = history["undo"].pop()
    _apply_snapshot(entry, "before")
    history["redo"].append(entry)
    _save_history(history)
    return entry["description"]


def redo() -> str | None:
    history = _load_history()
    if not history["redo"]:
        return None
    entry = history["redo"].pop()
    _apply_snapshot(entry, "after")
    history["undo"].append(entry)
    _save_history(history)
    return entry["description"]


def history_peek() -> dict:
    history = _load_history()
    return {
        "undo": history["undo"][-1]["description"] if history["undo"] else None,
        "redo": history["redo"][-1]["description"] if history["redo"] else None,
    }


# ---------------------------------------------------------------------------
# OBSERVATION-LEVEL EDITING (operates on the raw extraction cache, so edits
# survive rebuilds; a --force re-extraction DOES discard them)
# ---------------------------------------------------------------------------

GROUP_FOR_KIND = {"person": "people", "project": "projects", "place": "places"}
_NEW_RECORD = {
    "people": lambda name: {"name": name, "relationship": "", "observations": []},
    "projects": lambda name: {"name": name, "domain": "personal", "status": "unknown", "observations": []},
    "places": lambda name: {"name": name, "kind": "", "observations": []},
}


def _conversation_lookup() -> dict:
    """Map raw-cache filename stems to (date, title)."""
    return {
        conversation_cache_key(c): (c["date"], c["title"])
        for c in get_conversations()
    }


def _raw_path(filename: str) -> Path:
    path = RAW_DIR / Path(filename).name  # basename only — no traversal
    if not path.exists():
        raise FileNotFoundError(filename)
    return path


def list_observations(kind: str, canonical_name: str) -> list[dict]:
    """
    Every observation that rolls up into the given curated entity, with
    enough provenance (file/group/indices) to edit it in place.
    """
    curation = load_curation()
    lookup = _conversation_lookup()
    target = canonical_name.lower()
    out = []
    for path in sorted(RAW_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        date, title = lookup.get(path.stem, (path.stem[:10], path.stem))
        for group, raw_kind in (("people", "person"), ("projects", "project"), ("places", "place")):
            for ent_index, ent in enumerate(data.get(group, [])):
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, raw_kind, name)
                if not resolved or resolved[0] != kind or resolved[1].lower() != target:
                    continue
                for obs_index, text in enumerate(ent.get("observations", [])):
                    out.append({
                        "file": path.name, "group": group,
                        "ent_index": ent_index, "obs_index": obs_index,
                        "text": text, "date": date, "title": title,
                        "extracted_name": name,
                    })
    out.sort(key=lambda o: o["date"])
    return out


def _mutate_raw(filename: str, group: str, ent_index: int, obs_index: int):
    """Load a raw file and validate indices; returns (path, data, entity)."""
    path = _raw_path(filename)
    data = json.loads(path.read_text(encoding="utf-8"))
    ents = data.get(group, [])
    if not (0 <= ent_index < len(ents)):
        raise IndexError("entity index out of range")
    if not (0 <= obs_index < len(ents[ent_index].get("observations", []))):
        raise IndexError("observation index out of range")
    return path, data, ents[ent_index]


def _save_raw(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def edit_observation(filename: str, group: str, ent_index: int, obs_index: int, new_text: str):
    path, data, ent = _mutate_raw(filename, group, ent_index, obs_index)
    ent["observations"][obs_index] = new_text.strip()
    _save_raw(path, data)


def delete_observation(filename: str, group: str, ent_index: int, obs_index: int):
    path, data, ent = _mutate_raw(filename, group, ent_index, obs_index)
    ent["observations"].pop(obs_index)
    if not ent["observations"]:
        data[group].pop(ent_index)  # entity had nothing else to say here
    _save_raw(path, data)


def reassign_observation(
    filename: str, group: str, ent_index: int, obs_index: int,
    target_kind: str, target_name: str,
):
    """Move one observation to a different (possibly new) entity."""
    if target_kind not in GROUP_FOR_KIND:
        raise ValueError(f"unknown kind: {target_kind}")
    path, data, ent = _mutate_raw(filename, group, ent_index, obs_index)
    text = ent["observations"].pop(obs_index)

    target_group = GROUP_FOR_KIND[target_kind]
    data.setdefault(target_group, [])
    target = next(
        (e for e in data[target_group]
         if (e.get("name") or "").lower() == target_name.lower()),
        None,
    )
    if target is None:
        target = _NEW_RECORD[target_group](target_name)
        data[target_group].append(target)
    target.setdefault("observations", []).append(text)

    if not ent["observations"] and target is not ent:
        data[group].remove(ent)
    _save_raw(path, data)


# ---------------------------------------------------------------------------
# LOCAL DUPLICATE DETECTION (free — no API calls)
# ---------------------------------------------------------------------------

NAME_SIM_THRESHOLD = 0.72
EMB_SIM_THRESHOLD = 0.86


def pair_key(kind: str, a: str, b: str) -> str:
    lo, hi = sorted((a.lower(), b.lower()))
    return f"{kind}:{lo}|{hi}"


def _entity_texts() -> dict:
    """(kind, canonical_lower) -> concatenated observation text (capped)."""
    curation = load_curation()
    texts = defaultdict(list)
    for path in RAW_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for group, raw_kind in (("people", "person"), ("projects", "project"), ("places", "place")):
            for ent in data.get(group, []):
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, raw_kind, name)
                if resolved:
                    texts[(resolved[0], resolved[1].lower())].extend(
                        ent.get("observations", [])
                    )
    return {k: " ".join(v)[:1200] for k, v in texts.items()}


def find_duplicate_candidates(max_pairs: int = 60, use_embeddings: bool = True) -> list[dict]:
    """
    Rank likely duplicate pairs within each kind. Two signals:
    - name similarity (catches typos/variants: Myra/Mira)
    - embedding similarity of observation text (catches same-thing,
      different-name: "metal bar" / "Blue Room")
    Dismissed pairs (curation.not_duplicates) never come back.
    """
    import difflib

    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        return []
    index = json.loads(index_file.read_text(encoding="utf-8"))
    curation = load_curation()
    dismissed = {d.lower() for d in curation["not_duplicates"]}

    by_kind = defaultdict(list)
    for name, info in index.items():
        by_kind[info["type"]].append((name, info))

    candidates = {}

    # signal 1: name similarity (canonical names + aliases)
    for kind, items in by_kind.items():
        for i, (a, ia) in enumerate(items):
            a_names = [a] + ia.get("aliases", [])
            for b, ib in items[i + 1:]:
                pk = pair_key(kind, a, b)
                if pk in dismissed:
                    continue
                b_names = [b] + ib.get("aliases", [])
                best = max(
                    difflib.SequenceMatcher(None, x.lower(), y.lower()).ratio()
                    for x in a_names for y in b_names
                )
                if best >= NAME_SIM_THRESHOLD:
                    candidates[pk] = {
                        "kind": kind, "a": a, "b": b,
                        "a_mentions": ia["mentions"], "b_mentions": ib["mentions"],
                        "score": round(best, 3), "basis": "name",
                    }

    # signal 2: what-happened-there similarity
    if use_embeddings:
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            texts = _entity_texts()
            for kind, items in by_kind.items():
                keyed = [
                    (name, info, texts.get((kind, name.lower()), ""))
                    for name, info in items
                ]
                keyed = [(n, i, t) for n, i, t in keyed if len(t) > 60]
                if len(keyed) < 2:
                    continue
                ef = DefaultEmbeddingFunction()
                vecs = ef([t for _, _, t in keyed])
                import numpy as np
                mat = np.array(vecs)
                mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
                sims = mat @ mat.T
                for i in range(len(keyed)):
                    for j in range(i + 1, len(keyed)):
                        a, ia, _ = keyed[i]
                        b, ib, _ = keyed[j]
                        pk = pair_key(kind, a, b)
                        if pk in dismissed or pk in candidates:
                            continue
                        if sims[i, j] >= EMB_SIM_THRESHOLD:
                            candidates[pk] = {
                                "kind": kind, "a": a, "b": b,
                                "a_mentions": ia["mentions"], "b_mentions": ib["mentions"],
                                "score": round(float(sims[i, j]), 3), "basis": "context",
                            }
        except Exception:
            pass  # embeddings unavailable -> name signal only

    ranked = sorted(candidates.values(), key=lambda c: -c["score"])
    return ranked[:max_pairs]


# ---------------------------------------------------------------------------
# MERGE SUGGESTIONS (Claude proposes; the user approves each group)
# ---------------------------------------------------------------------------

SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["canonical", "members", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["groups"],
    "additionalProperties": False,
}

SUGGEST_PROMPT = """\
Below is a list of {kind} entities extracted from one person's journal, as \
JSON with name, attribute, and mention count. Some are duplicate or variant \
names for the same real-world {kind} (long descriptive names, slash-combined \
names, spelling variants).

Propose merge groups — but ONLY where you are confident the names refer to \
the same real-world thing. Similar names can be genuinely different things \
(two people with the same first name, an old and new version of a project \
tracked separately); when unsure, leave them alone. The user reviews every \
suggestion, and precision matters more than coverage.

For each group: "canonical" is the best short name (prefer the existing \
member with the most mentions), "members" are the OTHER names to fold into \
it (do not repeat the canonical), and "reason" is one short sentence.

<entities>
{listing}
</entities>"""


def suggest_merges(kind: str) -> list[dict]:
    """Ask Claude to propose merge groups for one entity kind."""
    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        return []
    index = json.loads(index_file.read_text(encoding="utf-8"))
    listing = [
        {"name": n, "attribute": "", "mentions": i["mentions"]}
        for n, i in sorted(index.items(), key=lambda kv: -kv[1]["mentions"])
        if i["type"] == kind
    ]
    if len(listing) < 2:
        return []

    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        **processing_thinking_kwargs(),
        output_config={"format": {"type": "json_schema", "schema": SUGGEST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": SUGGEST_PROMPT.format(
                kind=kind, listing=json.dumps(listing, ensure_ascii=False),
            ),
        }],
    )
    if response.stop_reason == "refusal":
        return []
    raw = next(b.text for b in response.content if b.type == "text")
    groups = json.loads(raw).get("groups", [])
    known = {n.lower() for n in index}
    cleaned = []
    for g in groups:
        members = [m for m in g["members"]
                   if m.lower() in known and m.lower() != g["canonical"].lower()]
        if members:
            cleaned.append({**g, "members": members})
    return cleaned


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
