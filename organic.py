# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Organic Category Creation — Phase 2 of the RAG Journal.

Categories that emerge from the data instead of being predefined. Two
routes in:

  Detected clusters   — place/project entities are clustered by a weighted
                        composite embedding (70% what-happened-there
                        context, 30% entity name), because name similarity
                        alone produces false clusters: "St. Patrick's" the
                        church and "St. Patrick's Day crawl" the 12-hour
                        bar event are unrelated. Clusters that clear the
                        thresholds (3+ members across 2+ separate weeks)
                        get named by Claude and proposed to the user.
  User-defined        — a category seeded by hand: a name plus optional
                        trigger keywords and/or member entities.

The user answers every proposal with confirm, dismiss, or "not now" (defer
without killing — the proposal re-surfaces when the cluster gains a new
member). Confirmed and user-defined categories live in
categories/custom.json and tag entries automatically: an entry gets the
tag when it mentions a member entity or matches a trigger keyword.
Entry categories are deliberately flat — grouping/hierarchy belongs on
the entities side, where it's for browsing, not on entry tags.

Usage:
    python organic.py scan        # cluster + propose (one Claude call)
    python organic.py list        # show proposals + custom categories

Layout (gitignored — personal data):
    categories/organic.json   proposals with statuses
    categories/custom.json    confirmed + user-defined categories
"""

import hashlib
import json
import re
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()


from config import AUTHOR, CATEGORY_DIR, MC_PROCESSING_MODEL as MODEL, get_client, processing_thinking_kwargs
from entities import RAW_DIR, apply_curation, get_conversations, load_curation
from entities import conversation_cache_key
from summarizer import week_key

ORGANIC_FILE = CATEGORY_DIR / "organic.json"
CUSTOM_FILE = CATEGORY_DIR / "custom.json"

CONTEXT_WEIGHT = 0.7        # what happened at/around the entity
NAME_WEIGHT = 0.3           # what the entity is called
CLUSTER_THRESHOLD = 0.55    # cosine on composite vectors
MIN_MEMBERS = 3             # three restaurant mentions in one week is just eating
MAX_MEMBERS = 10            # bigger than this is a life domain, not a category —
                            # the built-ins already cover those
MIN_WEEKS = 2               # ...three church visits across weeks is a pattern
HIGH_COHERENCE = 0.62
CONTEXT_CHARS = 1200        # per-entity observation text fed to the embedder

NAMING_PROMPT = """\
You are reviewing candidate categories detected in {author}'s journal by \
clustering places and projects on what actually happened around them. For \
each numbered cluster below, decide whether the members genuinely belong \
together as a category of {author}'s life — the kind of grouping worth \
browsing, like "Gardens", "Karaoke Nights", or "Live Shows".

Rules:
- keep=false for junk clusters: members that only superficially co-occur, \
mixed bags with no real theme, or groupings that would feel clinical or \
creepy to see labeled.
- Names are short (1-3 words), concrete, and in {author}'s own register — \
title case, no hashtag energy.
- The reason is one sentence naming what actually links the members.

{clusters}"""

NAMING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "keep", "name", "reason"],
            },
        },
    },
    "required": ["clusters"],
}


# ---------------------------------------------------------------------------
# ENTITY RECORDS (which conversations each place/project appears in)
# ---------------------------------------------------------------------------

def entity_records() -> dict:
    """(kind, canonical) -> {"name", "kind", "text", "convs": {key: date},
    "weeks": set} for places and projects, curation applied."""
    curation = load_curation()
    records = {}
    for path in RAW_DIR.glob("*.json"):
        key = path.stem
        date = key[:10]
        data = json.loads(path.read_text(encoding="utf-8"))
        for group, raw_kind in (("projects", "project"), ("places", "place")):
            for ent in data.get(group, []):
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                resolved = apply_curation(curation, raw_kind, name)
                if not resolved:
                    continue
                kind, canonical = resolved[0], resolved[1]
                rec = records.setdefault((kind, canonical.lower()), {
                    "name": canonical, "kind": kind, "texts": [],
                    "convs": {}, "weeks": set(),
                })
                rec["texts"].extend(ent.get("observations", []))
                rec["convs"][key] = date
                try:
                    rec["weeks"].add(week_key(date))
                except ValueError:
                    pass
    for rec in records.values():
        rec["text"] = " ".join(rec.pop("texts"))[:CONTEXT_CHARS]
    return records


# ---------------------------------------------------------------------------
# CLUSTERING (composite embeddings -> connected components)
# ---------------------------------------------------------------------------

def _composite_vectors(records: list[dict]):
    import numpy as np
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    ef = DefaultEmbeddingFunction()
    ctx = np.array(ef([r["text"] for r in records]))
    names = np.array(ef([r["name"] for r in records]))
    ctx = ctx / np.linalg.norm(ctx, axis=1, keepdims=True)
    names = names / np.linalg.norm(names, axis=1, keepdims=True)
    combo = CONTEXT_WEIGHT * ctx + NAME_WEIGHT * names
    return combo / np.linalg.norm(combo, axis=1, keepdims=True)


def _clusters(records: list[dict]) -> list[dict]:
    """Connected components over pairs above CLUSTER_THRESHOLD."""
    import numpy as np

    vecs = _composite_vectors(records)
    sims = vecs @ vecs.T
    n = len(records)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= CLUSTER_THRESHOLD:
                parent[find(i)] = find(j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    out = []
    for members in groups.values():
        if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
            continue
        weeks = set().union(*(records[i]["weeks"] for i in members))
        if len(weeks) < MIN_WEEKS:
            continue
        pair_sims = [
            float(sims[a, b])
            for x, a in enumerate(members) for b in members[x + 1:]
        ]
        coherence = sum(pair_sims) / len(pair_sims)
        out.append({
            "member_idx": members,
            "coherence": round(coherence, 3),
            "weeks": len(weeks),
        })
    return out


def _proposal_id(member_names: list[str]) -> str:
    joined = "|".join(sorted(n.lower() for n in member_names))
    return hashlib.md5(joined.encode()).hexdigest()[:10]


def _confidence(cluster: dict) -> str:
    if cluster["coherence"] >= HIGH_COHERENCE and cluster["weeks"] >= 3:
        return "high"
    return "medium"


# ---------------------------------------------------------------------------
# PROPOSALS (scan -> Claude names clusters -> the user responds)
# ---------------------------------------------------------------------------

def load_organic() -> dict:
    if ORGANIC_FILE.exists():
        return json.loads(ORGANIC_FILE.read_text(encoding="utf-8"))
    return {"generated": None, "proposals": []}


def _save_organic(data: dict):
    CATEGORY_DIR.mkdir(parents=True, exist_ok=True)
    ORGANIC_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def scan(quiet: bool = False) -> dict:
    """Cluster place/project entities and propose categories. Statuses of
    previously-seen proposals (by member set) carry over; a cluster that
    gained a member is a new proposal and re-surfaces."""
    from datetime import datetime

    records = [r for r in entity_records().values() if len(r["text"]) > 60]
    if len(records) < MIN_MEMBERS:
        print("  not enough entity context to cluster yet")
        return load_organic()

    clusters = _clusters(records)
    previous = {p["id"]: p for p in load_organic()["proposals"]}
    dismissed_sets = [
        set(m.lower() for m in p["members"])
        for p in previous.values() if p["status"] == "dismissed"
    ]

    candidates = []
    for c in clusters:
        members = [records[i]["name"] for i in c["member_idx"]]
        member_set = {m.lower() for m in members}
        # a cluster fully inside a dismissed set brings nothing new — skip
        if any(member_set <= d for d in dismissed_sets):
            continue
        candidates.append((c, members))

    if not candidates:
        data = {"generated": datetime.now().isoformat(timespec="minutes"),
                "proposals": list(previous.values())}
        _save_organic(data)
        if not quiet:
            print("  no new clusters cleared the thresholds")
        return data

    # one Claude call names/filters every candidate
    by_key = {r["name"]: r for r in records}
    blocks = []
    for idx, (c, members) in enumerate(candidates):
        lines = [f"Cluster {idx} (seen across {c['weeks']} weeks):"]
        for m in members:
            sample = by_key[m]["text"][:200]
            lines.append(f"- {m} ({by_key[m]['kind']}): {sample}")
        blocks.append("\n".join(lines))
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        **processing_thinking_kwargs(),
        output_config={"format": {"type": "json_schema", "schema": NAMING_SCHEMA}},
        messages=[{
            "role": "user",
            "content": NAMING_PROMPT.format(author=AUTHOR, clusters="\n\n".join(blocks)),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    named = {
        item["index"]: item
        for item in json.loads(
            next(b.text for b in response.content if b.type == "text")
        )["clusters"]
    }

    proposals = []
    kept_ids = set()
    for idx, (c, members) in enumerate(candidates):
        verdict = named.get(idx)
        if not verdict or not verdict["keep"]:
            continue
        pid = _proposal_id(members)
        kept_ids.add(pid)
        prior = previous.get(pid)
        proposals.append({
            "id": pid,
            "name": verdict["name"],
            "reason": verdict["reason"],
            "members": members,
            "weeks": c["weeks"],
            "coherence": c["coherence"],
            "confidence": _confidence(c),
            "status": prior["status"] if prior else "proposed",
        })
    # keep prior decided proposals whose clusters didn't re-form this scan
    for pid, p in previous.items():
        if pid not in kept_ids and p["status"] in ("dismissed", "confirmed", "not_now"):
            proposals.append(p)

    data = {"generated": datetime.now().isoformat(timespec="minutes"),
            "proposals": proposals}
    _save_organic(data)
    if not quiet:
        for p in proposals:
            if p["status"] == "proposed":
                print(f"  [{p['confidence']}] {p['name']}: {', '.join(p['members'])}")
    open_count = sum(1 for p in proposals if p["status"] == "proposed")
    print(f"\n  Organic scan: {open_count} open proposals")
    return data


def respond(proposal_id: str, action: str) -> dict:
    """The user's answer to a proposal: confirm | dismiss | not_now."""
    if action not in ("confirm", "dismiss", "not_now"):
        raise ValueError(f"unknown action '{action}'")
    data = load_organic()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            p["status"] = "confirmed" if action == "confirm" else action
            if action == "confirm":
                add_custom(p["name"], members=p["members"])
            _save_organic(data)
            return p
    raise ValueError("proposal not found")


# ---------------------------------------------------------------------------
# CUSTOM CATEGORIES (confirmed proposals + user-defined)
# ---------------------------------------------------------------------------

def load_custom() -> list[dict]:
    if CUSTOM_FILE.exists():
        return json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))
    return []


def _save_custom(cats: list[dict]):
    CATEGORY_DIR.mkdir(parents=True, exist_ok=True)
    CUSTOM_FILE.write_text(
        json.dumps(cats, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def add_custom(name: str, keywords: list[str] | None = None,
               members: list[str] | None = None):
    name = name.strip()
    if not name:
        raise ValueError("category needs a name")
    cats = load_custom()
    for c in cats:
        if c["name"].lower() == name.lower():
            # merge into the existing definition
            c["keywords"] = sorted({*c["keywords"], *(keywords or [])})
            c["members"] = sorted({*c["members"], *(members or [])})
            _save_custom(cats)
            return c
    cat = {"name": name, "keywords": sorted(keywords or []),
           "members": sorted(members or [])}
    cats.append(cat)
    _save_custom(cats)
    return cat


def remove_custom(name: str) -> bool:
    cats = load_custom()
    kept = [c for c in cats if c["name"].lower() != name.lower()]
    if len(kept) == len(cats):
        return False
    _save_custom(kept)
    return True


def remove_from_custom(name: str, keyword: str = "", member: str = "") -> bool:
    """Drop a single keyword and/or member from a custom category.
    Returns False if the category doesn't exist."""
    cats = load_custom()
    for c in cats:
        if c["name"].lower() == name.lower():
            if keyword:
                c["keywords"] = [k for k in c["keywords"] if k.lower() != keyword.lower()]
            if member:
                c["members"] = [m for m in c["members"] if m.lower() != member.lower()]
            _save_custom(cats)
            return True
    return False


def custom_tags() -> dict:
    """conv_key -> {category_name: evidence} for every custom category.
    An entry is tagged when it mentions a member entity or matches a
    trigger keyword."""
    cats = load_custom()
    if not cats:
        return {}

    records = entity_records()
    by_name = {r["name"].lower(): r for r in records.values()}
    conversations = {conversation_cache_key(c): c for c in get_conversations()}

    tags = defaultdict(dict)

    def tag(key, cat, evidence):
        tags[key].setdefault(cat["name"], evidence)

    for cat in cats:
        for member in cat["members"]:
            rec = by_name.get(member.lower())
            if not rec:
                continue
            for key in rec["convs"]:
                tag(key, cat, f"mentions {rec['name']}")
        for kw in cat["keywords"]:
            pattern = re.compile(r"\b" + re.escape(kw.lower()) + r"\b")
            for key, conv in conversations.items():
                if pattern.search(conv["text"].lower()):
                    tag(key, cat, f"keyword '{kw}'")
    return dict(tags)


def custom_names() -> set[str]:
    return {c["name"] for c in load_custom()}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        scan()
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        data = load_organic()
        for p in data["proposals"]:
            print(f"\n[{p['status']} / {p['confidence']}] {p['name']}")
            print(f"  {', '.join(p['members'])}")
            print(f"  {p['reason']}")
        print()
        for c in load_custom():
            bits = []
            if c["members"]:
                bits.append(f"members: {', '.join(c['members'])}")
            if c["keywords"]:
                bits.append(f"keywords: {', '.join(c['keywords'])}")
            print(f"custom: {c['name']} ({'; '.join(bits) or 'empty'})")
    else:
        print(__doc__)
