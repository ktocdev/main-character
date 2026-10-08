# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Entity Graph.

Extracts people, projects, places and things from imported journal conversations
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
    entity_graph/things/    one markdown doc per thing (bands, games, shows...)
    entity_graph/index.json name -> doc path, used by the companion
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


from config import AUTHOR, ENTITY_DIR, MC_PROCESSING_MODEL as MODEL, get_client, processing_thinking_kwargs
from rag_journal import get_collection

RAW_DIR = ENTITY_DIR / "raw"
CURATION_FILE = ENTITY_DIR / "curation.json"
GROUPS_FILE = ENTITY_DIR / "groups.json"
SEGMENT_CHARS = 45_000  # long conversations are split, not truncated

# Four kinds. A project is something the author makes or works on; a thing
# is something they enjoy or follow. One row per kind: the raw file's
# group, the kind, and the field that carries its attribute.
KIND_FIELDS = (
    ("people", "person", "relationship"),
    ("projects", "project", "status"),
    ("places", "place", "kind"),
    ("things", "thing", "category"),
)
KINDS = tuple(kind for _, kind, _ in KIND_FIELDS)
GROUP_FOR_KIND = {kind: group for group, kind, _ in KIND_FIELDS}
# a thing's subgrouping, as a place has a type and a project a status
THING_CATEGORIES = ("music", "game", "show", "book", "event", "other")

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
        "things": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": list(THING_CATEGORIES)},
                    "observations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "category", "observations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["people", "projects", "places", "things"],
    "additionalProperties": False,
}

EXTRACTION_PROMPT = """\
You are building an entity graph from one person's journal. The journal \
author is {author}. Below is one journal entry (originally a conversation \
with an AI companion; only the author's side is included), written on {date}.

Extract the PEOPLE, PROJECTS, PLACES, and THINGS that actually appear.

Rules:
- Never include the author ({author}) themselves — first-person statements \
are about the author, not an entity. Never include the AI companion.
- Never include celebrities or public figures as people, even if discussed \
at length. Only people the author actually knows or encounters. (A band \
the author listens to is a thing; a celebrity is not a person.)
- Use the shortest natural name the author uses ("Pip", "Mom", "Orbit \
Web"). No descriptive parentheticals, no slashes, no combined names — if \
two things are mentioned, they are two entities.
{known_block}- Capture EVERY concrete mention as its own observation — one per distinct \
fact or event, however minor or recurring (a pet making a mess counts, every \
time it happens). Short, factual, no editorializing.
- Projects are ongoing efforts the author makes or works on (a work \
project, an app, a class, a creative piece, a hobby they practice like \
guitar or karaoke). One-off tasks don't count. Skip generic labels like \
"work project" or "the design system" unless the author means one \
particular project.
- Things are what the author enjoys or follows: bands, artists and albums \
(music), games (game), TV shows and films (show), books (book), and \
recurring events like a yearly festival (event); anything else is other. \
Track a thing the author plays, watches, reads, listens to or attends, \
even once. A one-off event is not a thing: seeing a band play belongs to \
the entry, the band is the thing, and a specific venue is a place.
- Places must be specific, identifiable places that matter to the story \
(a named venue, a particular person's home, a city) — not incidental \
geography. Skip generic categories ("a restaurant", "a dive bar", "the \
gym") unless the author clearly treats it as one particular recurring place.
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
        ((i["mentions"], n) for n, i in index.items()
         if i["type"] == "person" and not i.get("retired")),
        reverse=True,
    )
    return [n for _, n in people[:80]]


def extract_conversation(client, conv: dict, known_people: list[str] | None = None) -> dict:
    """Extract entities from one conversation via the Claude API."""
    known_block = (
        KNOWN_BLOCK.format(names=", ".join(known_people)) if known_people else ""
    )
    combined = {group: [] for group, _, _ in KIND_FIELDS}
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
            n = sum(len(entities.get(group, [])) for group, _, _ in KIND_FIELDS)
            print(f"  {label} -> {n} entities")

        records.append({"date": conv["date"], "title": conv["title"],
                        "key": cache_file.stem, "entities": entities})
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
#   "delete":       [keys]   never track: every mention, now and future
#   "retired":      [keys]   a past chapter: out of everyday view, and the
#                   companion doesn't bring them up unless the author does
#   "category":     {key: category}  a thing's category (music, game, ...),
#                   overriding extraction's; set when retyping into a thing
#   "not_mixed":    [keys]   checked and found to be one person: the mix-up
#                   flag stays off for good
#   "drop_mentions": {key: [entry cache keys]}  delete just these mentions;
#                   the name stays free for new entries. Keyed by entry, not
#                   raw-file index, so it survives raw edits and --force.
#
# merge/correct targets may be kind-qualified ("person:Dr. Reyes") to
# move an entity across kinds while combining.

_CURATION_DEFAULTS = {
    "merge": {}, "correct": {}, "retype": {}, "rename": {},
    "alias_add": {}, "alias_remove": {}, "delete": [], "drop_mentions": {},
    "reviewed": [],        # entity keys the user has marked as checked
    "retired": [],         # entity keys retired one by one (groups: groups.json)
    "category": {},        # thing key -> category, overriding extraction's
    "not_duplicates": [],  # dismissed duplicate-pair keys ("kind:a|b")
    "not_mixed": [],       # entity keys confirmed as one, so never flagged
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


def base_name(index: dict, name: str) -> str:
    """The name curation rules know an indexed entity by. Differs from its
    index key only when two kinds share a name ("karaoke · project")."""
    return index[name].get("base", name)


def index_key(index: dict, name: str) -> str:
    """Curation key for an entity already in the index."""
    return curation_key(index[name]["type"], base_name(index, name))


# ---------------------------------------------------------------------------
# GENERIC NAMES ("restaurant", "dive bar", "work plan" aren't entities)
# ---------------------------------------------------------------------------
# A generic term turns into a profile that unrelated mentions pile into.
# Dropping it loses nothing: the entry text and its embeddings are
# untouched, so search and the companion still find "a dive bar after".
# Local and free. Never lowercase-based: plenty of real names are stored
# lowercase ("belmont tavern"), and it's the word before the noun that
# decides -- "late night bar" is generic, "belmont tavern" isn't.

_ARTICLES = {"the", "a", "an", "my", "our", "his", "her", "their", "this", "that", "some"}

GENERIC_NOUNS = {
    "place": {
        "place", "spot", "restaurant", "bar", "dive bar", "pub", "tavern", "lounge",
        "diner", "cafe", "café", "coffee shop", "bakery", "brewery", "club", "venue",
        "gym", "park", "office", "dr office", "doctor's office", "doctors office",
        "dentist", "doctor", "urgent care", "hospital", "clinic", "pharmacy", "er",
        "store", "shop", "grocery store", "supermarket", "mall", "pet store",
        "gift store", "garden center", "casino", "hotel", "motel", "airport",
        "basement", "library", "theater", "theatre", "movie theater", "cinema",
        "gas station", "parking lot", "laundromat", "salon", "barber", "vet",
        "bank", "post office", "food truck", "liquor store", "bookstore",
        "parking garage", "garage sale", "car wash",
    },
    "project": {
        "project", "projects", "work project", "work projects", "work plan",
        "plan", "side project", "side projects", "personal project",
        "personal projects", "code project", "code projects", "app", "website",
        "site", "design system", "design components", "components", "refactor",
        "ticket", "tickets", "tasks", "sprint", "presentation", "homework", "chores",
    },
    "thing": {
        "game", "games", "video game", "video games", "show", "shows", "tv show",
        "series", "movie", "movies", "film", "book", "books", "novel", "band",
        "album", "song", "songs", "music", "playlist", "podcast", "festival",
        "concert", "event", "fest",
    },
}

# words that only describe ("late night bar", "mid pizza place", "personal
# code projects"). A word not in here -- a proper name -- makes it specific.
_DESCRIPTORS = {
    "new", "old", "local", "little", "small", "big", "cheap", "fancy", "nice",
    "random", "other", "mid", "good", "bad", "late", "night", "late-night",
    "cocktail", "wine", "sports", "trivia", "open", "mic", "karaoke", "dive",
    "corner", "neighborhood", "nearby", "fast", "food", "sushi", "pizza",
    "pasta", "taco", "thai", "chinese", "mexican", "italian", "indian",
    "japanese", "korean", "vietnamese", "ramen", "burger", "bbq", "breakfast",
    "brunch", "coffee", "dessert", "ice", "cream", "vegan", "pet", "gift",
    "sandwich", "deli", "bagel", "donut", "noodle", "salad", "steak", "seafood",
    "chicken", "wing", "hot", "dog", "pho", "dumpling", "greek", "french",
    "grocery", "hardware", "thrift", "vintage", "work", "personal", "code",
    "coding", "side", "design", "main", "weekly", "daily",
    "video", "tv", "board", "card", "horror", "reality", "cooking", "audio",
    "comic", "music", "street", "art", "film", "beer", "food", "summer",
}


def is_generic(kind: str, name: str) -> bool:
    """A category word, optionally after an article and describing words."""
    nouns = GENERIC_NOUNS.get(kind)
    if not nouns:
        return False  # people aren't judged by name
    words = re.sub(r"[^\w\s'’-]", " ", name.lower()).split()
    while words and words[0] in _ARTICLES:
        words = words[1:]
    for i in range(len(words)):
        if " ".join(words[i:]) in nouns and all(w in _DESCRIPTORS for w in words[:i]):
            return True
    return False


def generic_flag(curation: dict, kind: str, name: str, groups: list[str]) -> bool:
    """is_generic, except for what you clearly treat as one particular
    thing: a group member, or a name you renamed by hand ("the gym" that's
    *your* gym can be put in a group to keep it)."""
    if groups or not is_generic(kind, name):
        return False
    renamed_to = {v.lower() for v in curation["rename"].values()}
    return name.lower() not in renamed_to


def mark_generic(index: dict) -> dict:
    """(Re)set the `generic` flag across a loaded index -- for one built
    before the detector existed, or before a detector change."""
    curation = load_curation()
    for name, info in index.items():
        if generic_flag(curation, info["type"], info.get("base", name), info.get("groups", [])):
            info["generic"] = True
        else:
            info.pop("generic", None)
    return index


# ---------------------------------------------------------------------------
# MIX-UPS (one name, probably two people: the coworker and the friend who share a first name)
# ---------------------------------------------------------------------------
# Every entry's extraction says what the person is to the author. When those
# disagree in a way one person can't, the name probably covers two. Local
# and free. People only: a project's status changes legitimately, and a
# place's type is too loose (bar / venue) to tell two places apart.
#
# Embedding each observation and splitting it in two was tried and
# dropped: on short observations any two topics look like two clusters,
# and the cleanest "splits" were all small entities.

RELATIONSHIP_GROUPS = {
    "family": {
        "mother", "mom", "mum", "father", "dad", "parent", "sister", "brother",
        "sibling", "niece", "nephew", "aunt", "uncle", "cousin", "grandmother",
        "grandfather", "grandparent", "son", "daughter", "family", "in-law",
        "stepmother", "stepfather", "stepsister", "stepbrother",
    },
    "pet": {"pet", "pet cat", "pet dog", "pet guinea pig", "dog", "cat"},
    "work": {
        "coworker", "co-worker", "former coworker", "old coworker", "colleague",
        "manager", "boss", "boss's boss", "supervisor", "employee", "report",
        "direct report", "client", "recruiter", "interviewer", "work friend",
        "work partner", "former work partner", "teammate", "mentor",
    },
    "romantic": {
        "ex", "date", "partner", "boyfriend", "girlfriend", "spouse", "husband",
        "wife", "romantic interest", "love interest", "romantic partner",
        "date prospect", "crush", "ex-boyfriend", "ex-girlfriend", "fiance",
        "fiancé", "fiancee", "fiancée", "dating app match",
    },
    "friend": {
        "friend", "old friend", "best friend", "close friend", "childhood friend",
        "acquaintance", "new acquaintance", "classmate", "neighbor", "neighbour",
        "roommate", "former friend", "estranged friend",
    },
    "service": {
        "bartender", "doctor", "dentist", "therapist", "teacher", "singing teacher",
        "esthetician", "landlord", "airbnb host", "tattoo artist", "dermatologist",
        "trainer", "hairdresser", "stylist", "barber", "nurse",
    },
}
_RELATIONSHIP_GROUP = {label: g for g, labels in RELATIONSHIP_GROUPS.items() for label in labels}
# One person can't be both of these, so two mentions are enough to flag.
# The rest overlap or change over time (a coworker who's a friend, a friend
# who became an ex) and need the smaller side to be a real share.
_EXCLUSIVE_GROUPS = {"family", "pet"}
MIXUP_MIN = 2               # an exclusive clash
MIXUP_MIN_OVERLAP = 3       # an overlapping clash: this many mentions...
MIXUP_MIN_SHARE = 0.25      # ...and this share of the classified ones


def relationship_group(label: str) -> str:
    """The group a relationship label belongs to, or "" when it's unknown
    or hedged ("ex/partner", "classmate or teacher", "Mika's mother")."""
    label = label.strip().lower()
    if label in _RELATIONSHIP_GROUP:
        return _RELATIONSHIP_GROUP[label]
    if "/" in label or " or " in label or "'s " in label or "’s " in label:
        return ""
    words = label.split()
    # "high school best friend", "on-and-off boyfriend"
    return _RELATIONSHIP_GROUP.get(words[-1], "") if words else ""


def mixup_flag(kind: str, attrs: dict) -> list:
    """[[label, count], ...] for the relationship groups that clash, each
    under its most-used label with the group's count, biggest first.
    Empty when nothing clashes."""
    if kind != "person":
        return []
    groups: dict[str, Counter] = defaultdict(Counter)
    for label, count in attrs.items():
        g = relationship_group(label)
        if g:
            groups[g][label] += count
    totals = {g: sum(c.values()) for g, c in groups.items()}
    classified = sum(totals.values())
    ranked = sorted(totals, key=lambda g: -totals[g])
    clashing = set()
    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            small = totals[b]  # ranked, so b is the smaller side
            if a in _EXCLUSIVE_GROUPS or b in _EXCLUSIVE_GROUPS:
                clash = small >= MIXUP_MIN
            else:
                clash = small >= MIXUP_MIN_OVERLAP and small / classified >= MIXUP_MIN_SHARE
            if clash:
                clashing.update((a, b))
    return [[groups[g].most_common(1)[0][0], totals[g]] for g in ranked if g in clashing]


def mixup_text(flag: list) -> str:
    """"seen as coworker (9) and friend (4)" """
    parts = [f"{label} ({count})" for label, count in flag]
    return "seen as " + (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0])


def mark_mixups(index: dict) -> dict:
    """(Re)set `attrs` and the `mixup` flag across a loaded index, from the
    raw cache, without rebuilding -- for an index built before mix-ups
    existed. Read-only on disk, like mark_generic."""
    curation = load_curation()
    attrs: dict[tuple, Counter] = defaultdict(Counter)
    fields = {kind: field for _, kind, field in KIND_FIELDS}
    for entry, kind, name, ent in _raw_mentions():
        resolved = apply_curation(curation, kind, name, entry)
        if not resolved or resolved[0] != kind:
            continue
        attr = (ent.get(fields[kind]) or "").strip()
        if attr and attr != "unknown":
            attrs[(kind, resolved[1].lower())][attr.lower()] += 1
    not_mixed = {k.lower() for k in curation["not_mixed"]}
    for name, info in index.items():
        base = info.get("base", name)
        found = attrs.get((info["type"], base.lower()))
        info.pop("attrs", None)
        info.pop("mixup", None)
        if found:
            info["attrs"] = dict(found.most_common())
        if curation_key(info["type"], base) not in not_mixed:
            flag = mixup_flag(info["type"], found or {})
            if flag:
                info["mixup"] = flag
    return index


def find_mixups(index: dict) -> list[dict]:
    """Flagged entities, most mentions first."""
    out = [{"name": name, "type": info["type"], "mentions": info["mentions"],
            "mixup": info["mixup"], "text": mixup_text(info["mixup"])}
           for name, info in index.items() if info.get("mixup")]
    return sorted(out, key=lambda m: -m["mentions"])


def _raw_mentions():
    """Every extracted (entry, kind, name) in the raw cache."""
    for path in sorted(RAW_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for group, kind, _ in KIND_FIELDS:
            for ent in data.get(group, []):
                name = (ent.get("name") or "").strip()
                if name:
                    yield path.stem, kind, name, ent


def entries_for(curation: dict, key: str) -> list[str]:
    """Entries with a mention that is, or resolves to, `key`."""
    out = set()
    for entry, kind, name, _ in _raw_mentions():
        resolved = apply_curation(curation, kind, name, entry)
        if curation_key(kind, name) == key or (
                resolved and curation_key(resolved[0], resolved[1]) == key):
            out.add(entry)
    return sorted(out)


def drop_mentions(curation: dict, key: str):
    """Delete the mentions `key` has now; leave the name free."""
    entries = set(curation["drop_mentions"].get(key, [])) | set(entries_for(curation, key))
    if entries:
        curation["drop_mentions"][key] = sorted(entries)


def free_name(curation: dict, key: str):
    """Turn a never-track rule into delete-these-mentions: what it hid stays
    hidden, but a new entry with the name starts fresh."""
    curation["delete"] = [d for d in curation["delete"] if d.lower() != key]
    drop_mentions(curation, key)


def deleted_list(curation: dict) -> dict:
    """Both kinds of delete, for the Entities "deleted" view."""
    counts = defaultdict(int)
    spelled = {}  # keys are lowercase; show the name as the entries spell it
    for _, kind, name, _ in _raw_mentions():
        counts[curation_key(kind, name)] += 1
        spelled.setdefault(curation_key(kind, name), name)

    def split(key):
        kind, _, name = key.partition(":")
        return kind, spelled.get(key, name)

    names = []
    for key in dict.fromkeys(d.lower() for d in curation["delete"]):
        kind, name = split(key)
        names.append({"key": key, "kind": kind, "name": name, "mentions": counts[key],
                      "generic": is_generic(kind, name)})
    mentions = [{"key": k, "kind": split(k)[0], "name": split(k)[1], "entries": len(v)}
                for k, v in curation["drop_mentions"].items() if v]
    return {"names": names, "mentions": mentions}


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


def retired_groups(groups: list[dict]) -> set[str]:
    """Lowercase names of retired groups, counting every group nested
    under a retired one."""
    out = set()
    for g in groups:
        current, seen = g, set()
        while current and current["name"].lower() not in seen:
            if current.get("retired"):
                out.add(g["name"].lower())
                break
            seen.add(current["name"].lower())
            current = find_group(groups, current["parent"]) if current["parent"] else None
    return out


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


def _dropped(curation: dict, key: str, entry: str) -> bool:
    return bool(entry) and entry in curation["drop_mentions"].get(key, ())


def apply_curation(curation: dict, kind: str, name: str, entry: str = ""):
    """
    Resolve one extracted (kind, name) through the curation rules.
    Returns (kind, canonical_name, alias_of_canonical: bool) or None if deleted.
    `entry` is the record's cache key, for mentions deleted entry by entry.
    """
    if curation_key(kind, name) in {d.lower() for d in curation["delete"]}:
        return None
    if _dropped(curation, curation_key(kind, name), entry):
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

    # a retype of the entity the name resolved to, so its merged aliases
    # move with it ("90 day" -> 90 Day Fiance, now a thing)
    key = curation_key(kind, name)
    rt_final = curation["retype"].get(key)
    if rt_final and rt_final is not rt:
        kind = rt_final.get("type", kind)
        name = rt_final.get("name") or name

    if curation_key(kind, name) in {d.lower() for d in curation["delete"]}:
        return None
    if _dropped(curation, curation_key(kind, name), entry):
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
    for record in records:
        for group, kind, attr_field in KIND_FIELDS:
            for ent in record["entities"].get(group, []):
                name = ent.get("name", "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, kind, name, record.get("key", ""))
                if resolved is None:
                    continue
                final_kind, display, is_alias = resolved
                key = (final_kind, display.lower())
                if key not in merged:
                    merged[key] = {
                        "name": display, "kind": final_kind, "attr": "",
                        "attrs": Counter(), "aliases": set(), "timeline": [],
                    }
                if is_alias:
                    merged[key]["aliases"].add(name)
                # attr only carries over within the same kind (a person's
                # relationship isn't a place's type)
                if final_kind == kind:
                    attr = (ent.get(attr_field) or "").strip()
                    if attr and attr != "unknown":
                        # shown: the latest; kept: all of them, since a
                        # coworker and a friend under one name is a mix-up
                        merged[key]["attr"] = attr
                        merged[key]["attrs"][attr.lower()] += 1
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

    # The index is keyed by display name, so two kinds sharing a name
    # ("karaoke" the place and the project) would overwrite each other and
    # one would vanish. The one with the most mentions keeps the plain name;
    # the others show their kind ("karaoke · project") and stay reachable,
    # e.g. to merge one way. Rules still key on the plain name (`base`).
    by_name: dict[str, list[dict]] = {}
    for ent in merged.values():
        ent["display"] = ent["name"]
        by_name.setdefault(ent["name"].lower(), []).append(ent)
    for same in by_name.values():
        same.sort(key=lambda e: (-len(e["timeline"]), KINDS.index(e["kind"])))
        for ent in same[1:]:
            ent["display"] = f"{ent['name']} · {ent['kind']}"

    # group memberships, resolved through names + aliases
    groups = load_groups()
    name_lookup = {}  # lowercase name/alias -> canonical display name
    for ent in merged.values():
        name_lookup[ent["display"].lower()] = ent["display"]
    for ent in merged.values():
        name_lookup.setdefault(ent["name"].lower(), ent["display"])
        for a in ent["aliases"]:
            name_lookup.setdefault(a.lower(), ent["display"])
    entity_groups: dict[str, list[str]] = {}  # canonical name -> [group names]
    for g in groups:
        for member in g["members"]:
            canon = name_lookup.get(member.lower())
            if canon and g["name"] not in entity_groups.get(canon, []):
                entity_groups.setdefault(canon, []).append(g["name"])

    attr_labels = {"person": "relationship", "project": "status", "place": "type",
                   "thing": "category"}
    # a thing's category: the curated one, else what extraction said, else
    # "other" (one retyped in from another kind has none of its own)
    categories = {k.lower(): v for k, v in curation["category"].items()}
    for (kind, lname), ent in merged.items():
        if kind == "thing":
            ent["attr"] = categories.get(f"thing:{lname}") or ent["attr"] or "other"
    index = {}
    retired_keys = {r.lower() for r in curation["retired"]}
    not_mixed = {k.lower() for k in curation["not_mixed"]}
    retired_gs = retired_groups(groups)

    # Clear generated docs so merged/deleted entities don't leave stale files
    for kind_dir in GROUP_FOR_KIND.values():
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
            f"name: {ent['display']}",
            f"type: {kind}",
            f"{attr_labels[kind]}: {ent['attr'] or 'unknown'}",
            f"first_seen: {dates[0]}",
            f"last_seen: {dates[-1]}",
            f"mentions: {len(ent['timeline'])}",
        ]
        if ent["aliases"]:
            lines.append(f"aliases: {', '.join(sorted(ent['aliases']))}")
        gnames = sorted(entity_groups.get(ent["display"], []), key=str.lower)
        if gnames:
            lines.append(
                f"groups: {', '.join(group_path(groups, g) for g in gnames)}"
            )
        # Retired: one by one, or through groups -- but only when every
        # group they're in is retired; membership in an active group keeps
        # them in view unless they were retired themselves.
        retired_by = ""
        if curation_key(kind, ent["name"]) in retired_keys:
            retired_by = "self"
        elif gnames and all(g.lower() in retired_gs for g in gnames):
            retired_by = gnames[0]
        if retired_by:
            # said in the profile itself, so it reaches the companion
            # whenever the profile does, without a system prompt change
            lines.append("status: retired (a past chapter). Don't bring them "
                         "up unless the author does.")
        lines += ["---", ""]
        for date, title, observations in ent["timeline"]:
            lines.append(f"### {date} — {title}")
            for obs in observations:
                lines.append(f"- {obs}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")

        index[ent["display"]] = {
            "type": kind,
            "path": path.relative_to(ENTITY_DIR).as_posix(),
            "mentions": len(ent["timeline"]),
            "aliases": sorted(ent["aliases"]),
            "groups": gnames,
            "reviewed": curation_key(kind, ent["name"]) in {
                r.lower() for r in curation["reviewed"]
            },
        }
        if ent["display"] != ent["name"]:
            index[ent["display"]]["base"] = ent["name"]
        if generic_flag(curation, kind, ent["name"], gnames):
            index[ent["display"]]["generic"] = True
        if retired_by:
            index[ent["display"]]["retired"] = retired_by
        if kind == "thing":
            index[ent["display"]]["category"] = ent["attr"]
        if ent["attrs"]:
            index[ent["display"]]["attrs"] = dict(ent["attrs"].most_common())
        if curation_key(kind, ent["name"]) not in not_mixed:
            flag = mixup_flag(kind, ent["attrs"])
            if flag:
                index[ent["display"]]["mixup"] = flag

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

_NEW_RECORD = {
    "people": lambda name: {"name": name, "relationship": "", "observations": []},
    "projects": lambda name: {"name": name, "domain": "personal", "status": "unknown", "observations": []},
    "places": lambda name: {"name": name, "kind": "", "observations": []},
    "things": lambda name: {"name": name, "category": "other", "observations": []},
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
        for group, raw_kind, attr_field in KIND_FIELDS:
            for ent_index, ent in enumerate(data.get(group, [])):
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, raw_kind, name, path.stem)
                if not resolved or resolved[0] != kind or resolved[1].lower() != target:
                    continue
                # this record's own attribute, not the entity's "latest wins"
                # one: two people under one name tend to show up right here
                attr = (ent.get(attr_field) or "").strip()
                for obs_index, text in enumerate(ent.get("observations", [])):
                    out.append({
                        "file": path.name, "group": group,
                        "ent_index": ent_index, "obs_index": obs_index,
                        "text": text, "date": date, "title": title,
                        "extracted_name": name, "attr": attr,
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
    # every caller is a hand edit; a re-extract would lose it, so say so
    data["edited"] = True
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
        for group, raw_kind, _ in KIND_FIELDS:
            for ent in data.get(group, []):
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, raw_kind, name, path.stem)
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
        if info.get("generic"):
            continue  # delete it, don't merge it (and nothing merges into it)
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
                    (name, info, texts.get((kind, info.get("base", name).lower()), ""))
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
        if i["type"] == kind and not i.get("generic")
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


# ---------------------------------------------------------------------------
# THINGS REVIEW (Claude proposes projects/places that are really things)
# ---------------------------------------------------------------------------
# Before the thing kind existed, games and shows were filed as projects
# and festivals (or a band) as places. One call proposes the re-filing;
# the user accepts or skips each, and applying is one curation change.

THINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "things": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": list(THING_CATEGORIES)},
                    "reason": {"type": "string"},
                },
                "required": ["name", "category", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["things"],
    "additionalProperties": False,
}

THINGS_PROMPT = """\
Below are project and place entities extracted from one person's journal, \
as JSON with name, kind, mention count and one thing the journal said \
about each. They were filed before the journal had a fourth kind, "thing".

A project is something the author makes or works on (an app, a class, a \
creative piece, a hobby they practice, like guitar or karaoke). A thing is \
something the author enjoys or follows: music (bands, artists, albums), \
game, show (TV, film), book, event (recurring only, like a yearly \
festival), or other. A place is a physical location.

List the entries that are really things, with a category and one short \
reason. Leave out anything the author makes, practices or works on, and \
any place that is a location rather than an event. Precision matters more \
than coverage; the author reviews every suggestion.

<entities>
{listing}
</entities>"""


def suggest_things() -> list[dict]:
    """Ask Claude which projects and places are really things."""
    index_file = ENTITY_DIR / "index.json"
    if not index_file.exists():
        return []
    index = json.loads(index_file.read_text(encoding="utf-8"))
    candidates = [(n, i) for n, i in index.items()
                  if i["type"] in ("project", "place") and not i.get("generic")]
    if not candidates:
        return []
    listing = []
    for name, info in sorted(candidates, key=lambda kv: -kv[1]["mentions"]):
        obs = list_observations(info["type"], info.get("base", name))
        listing.append({"name": name, "kind": info["type"], "mentions": info["mentions"],
                        "said": obs[0]["text"][:160] if obs else ""})

    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        **processing_thinking_kwargs(),
        output_config={"format": {"type": "json_schema", "schema": THINGS_SCHEMA}},
        messages=[{"role": "user", "content": THINGS_PROMPT.format(
            listing=json.dumps(listing, ensure_ascii=False))}],
    )
    if response.stop_reason == "refusal":
        return []
    raw = next(b.text for b in response.content if b.type == "text")
    out, seen = [], set()
    for s in json.loads(raw).get("things", []):
        name = next((n for n, _ in candidates if n.lower() == s["name"].strip().lower()), None)
        if name and name not in seen:
            seen.add(name)
            info = index[name]
            out.append({"name": name, "kind": info["type"], "mentions": info["mentions"],
                        "category": s["category"], "reason": s["reason"]})
    return out


def retype_rule(curation: dict, key: str, new_kind: str, new_name: str, category: str = ""):
    """Retype the entity at `key`. Its own rules (reviewed, retired, aliases)
    follow it to the new key, and retyping back to where a rule came from
    removes that rule instead of stacking a second one."""
    new_key = curation_key(new_kind, new_name)
    if new_key == key:
        if category and new_kind == "thing":
            curation["category"][new_key] = category
        return
    back = next((src for src, rt in curation["retype"].items()
                 if src.lower() == new_key
                 and curation_key(rt.get("type", ""), rt.get("name") or "") == key), None)
    if back:
        curation["retype"].pop(back)
    else:
        curation["retype"][key] = {"type": new_kind, "name": new_name}
    for field in ("reviewed", "retired"):
        if any(k.lower() == key for k in curation[field]):
            curation[field] = [k for k in curation[field] if k.lower() != key] + [new_key]
    for field in ("alias_add", "alias_remove", "rename"):
        if key in curation[field] and new_key not in curation[field]:
            curation[field][new_key] = curation[field].pop(key)
    curation["category"].pop(key, None)
    if new_kind == "thing" and category:
        curation["category"][new_key] = category


# ---------------------------------------------------------------------------
# TARGETED RE-EXTRACT ("re-extract entries that mention ___")
# ---------------------------------------------------------------------------
# A prompt change only reaches entries extracted after it. This finds the
# entries whose text mentions a term and replaces just their raw caches.
# Hand edits in those files are lost, so the preview lists them first.

# rough per-entry output, for the estimate; extraction output is a few
# hundred tokens of names and short observations
_EST_OUTPUT_TOKENS = 1500


def _term_pattern(term: str) -> re.Pattern:
    """Case-insensitive, spaces optional: "live journal" finds LiveJournal."""
    words = [re.escape(w) for w in term.split()]
    return re.compile(r"\s?".join(words), re.IGNORECASE)


def entries_mentioning(terms: list[str]) -> list[dict]:
    """Conversations whose text matches any term, each once."""
    patterns = [_term_pattern(t) for t in terms if t.strip()]
    if not patterns:
        return []
    return [c for c in get_conversations()
            if any(p.search(c["text"]) for p in patterns)]


def _edited_files() -> set[str]:
    """Raw caches with hand edits: flagged by the editor, or (for edits made
    before the flag) named in the undo history."""
    out = set()
    for path in RAW_DIR.glob("*.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("edited"):
                out.add(path.stem)
        except ValueError:
            continue
    for side in _load_history().values():
        for entry in side:
            if entry.get("kind") == "raw" and entry.get("file"):
                out.add(Path(entry["file"]).stem)
    return out


def reextract_preview(terms: list[str]) -> dict:
    """What a re-extract would touch and roughly cost, before spending."""
    from config import MODEL_PRICES
    convs = entries_mentioning(terms)
    edited = _edited_files()
    prices = MODEL_PRICES.get(MODEL, {"in": 0, "out": 0})
    overhead = len(EXTRACTION_PROMPT) + len(KNOWN_BLOCK) + 80 * 20
    cost = 0.0
    for c in convs:
        segments = _segments(c["text"])
        tokens_in = (len(c["text"]) + overhead * len(segments)) / 4
        tokens_out = _EST_OUTPUT_TOKENS * len(segments)
        cost += (tokens_in * prices["in"] + tokens_out * prices["out"]) / 1_000_000
    entries = []
    for c in convs:
        key = conversation_cache_key(c)
        entries.append({"key": key, "date": c["date"], "title": c["title"],
                        "edited": key in edited})
    return {"entries": entries, "estimate": round(cost, 2), "model": MODEL}


def reextract(terms: list[str], progress=None) -> dict:
    """Re-extract the matching entries, replacing only their raw caches.
    The caller rebuilds the entity docs afterwards."""
    import caps
    client = get_client()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    known = known_people_hint()
    convs = entries_mentioning(terms)
    done, failed = [], []
    for i, conv in enumerate(convs):
        key = conversation_cache_key(conv)
        if progress:
            progress(i, len(convs), conv)
        try:
            entities = extract_conversation(client, conv, known_people=known)
        except caps.CapExceeded:
            raise  # a spend cap stops the run; what's done is kept
        except Exception as e:
            failed.append({"key": key, "error": str(e)})
            continue
        (RAW_DIR / f"{key}.json").write_text(
            json.dumps(entities, indent=2, ensure_ascii=False), encoding="utf-8")
        done.append(key)
    return {"done": done, "failed": failed}


def build(force: bool = False, quiet: bool = False) -> dict:
    records = run_extraction(force=force, quiet=quiet)
    index = build_entity_docs(records)
    by_kind = defaultdict(int)
    for info in index.values():
        by_kind[info["type"]] += 1
    print(
        f"\n  Entity graph built: {by_kind['person']} people, "
        f"{by_kind['project']} projects, {by_kind['place']} places, "
        f"{by_kind['thing']} things"
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
    for kind in KINDS:
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
