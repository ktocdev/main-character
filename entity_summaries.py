# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Entity summaries — a short profile heading the page of each entity the
journal knows well.

An entity qualifies once it has MIN_OBSERVATIONS observations across
MIN_ENTRIES entries: below that, the list of observations already reads
at a glance, and one long entry isn't a profile. Each summary is written
from the entity's observations and kept in entity_graph/summaries.json,
keyed by curation key, with a hash of the observations it was written
from:

    {"person:mika": {"text": ..., "hash": ..., "count": 42,
                     "written": "2026-10-17", "edited": false}}

The close writes only the ones that are due (status): a new one, or
one whose entity has gained REFRESH_NEW observations, or grown by
REFRESH_GROWTH, since it was written; PER_CLOSE at most, so the first
close in a journal without any isn't held up (the clean up menu's
"entity summaries" writes the rest). A summary the author edited is never
rewritten on its own; "rewrite" on the entity page does that, on request.

The prompt is discreet on purpose: the page is on screen, and the profile
is the first thing read about someone. Hard parts are named once, kindly,
or left out; the observations below it keep the full record.

Usage:
    python entity_summaries.py estimate     # how many are due, roughly what it costs
    python entity_summaries.py build        # write the due ones
"""

import hashlib
import sys
from collections import defaultdict
from datetime import date

import entities
from config import AUTHOR, MC_PROCESSING_MODEL as MODEL, get_client, processing_thinking_kwargs

MIN_OBSERVATIONS = 10
MIN_ENTRIES = 3
REFRESH_NEW = 3          # observations gained since the summary...
REFRESH_GROWTH = 0.25    # ...or this share of its count, whichever is less
PER_CLOSE = 10           # a close writes at most this many; the clean up menu does the rest
INPUT_CHARS = 30_000     # newest observations kept first, oldest dropped
MAX_CHARS = 2_000        # an edited summary longer than this is cut
_EST_OUTPUT_TOKENS = 300

SUMMARY_PROMPT = """\
You write the short profile at the top of {name}'s page in {author}'s \
journal app: who {name} is in {author}'s life, how that has gone, and where \
it stands now. Below the profile, the page lists every observation; the \
profile is the part read first, often with someone else able to see the \
screen.

{intro}{extra}

Below are the observations the app gathered about {name} from {author}'s \
journal entries, oldest first, each with the date of its entry. The \
latest is from {last}.

Write 60-150 words of plain prose: third person, no headers, no bullet \
points, no title. Refer to {author} by name, and to people as {author} \
does. Fewer words are fine when there's less to say.

Discretion:
- Leave out health and medical details, mental health, sexuality and \
romantic life, money trouble, legal matters, drinking or drug use, and \
anything traumatic: {name}'s and {author}'s alike. Leave out the specifics \
of arguments, grievances and anything said in anger, and quote no \
criticism.
- When something hard is part of the story (a falling-out, a loss, \
distance that grew, a project that stalled), say so once, plainly and \
kindly, in the most neutral words that are still true, without blame and \
without the reasons. Don't dwell on it, and don't let it set the tone of \
the whole profile.
- Don't guess at motives or feelings the observations don't state. Don't \
judge, diagnose or advise.
- Accurate, not flattering: don't add warmth the record doesn't show, and \
don't hide what {name} plainly is to {author}.
{care}
<observations>
{text}
</observations>"""

CARE = {
    "person": "This matters most here: {name} is a real person, who may "
              "never have chosen to appear in a journal. Be as fair to them "
              "as to {author}.\n",
    "animal": "{name} is a real animal someone loves; an illness or a loss "
              "is mentioned gently, at most once.\n",
}
CARE_OTHER = ("This is a {kind}, so most of this rarely comes up, but the "
              "people who appear in it get the same care.\n")


def _hash(observations: list[tuple[str, str]]) -> str:
    """Order-free: the raw cache and list_observations walk the same files,
    but a summary shouldn't go stale over the order of two lines."""
    lines = sorted(f"{d}|{t}" for d, t in observations)
    return hashlib.md5("\n".join(lines).encode()).hexdigest()[:12]


def observations_by_entity() -> dict:
    """(kind, base_lower) -> {"obs": [(date, text)], "entries": set}, in one
    pass over the raw cache."""
    curation = entities.load_curation()
    dates = {key: d for key, (d, _) in entities._conversation_lookup().items()}
    out = defaultdict(lambda: {"obs": [], "entries": set()})
    for entry, kind, name, ent in entities._raw_mentions():
        obs = ent.get("observations") or []
        if not obs:
            continue
        resolved = entities.apply_curation(curation, kind, name, entry, ent.get("qualifier", ""))
        if not resolved:
            continue
        slot = out[(resolved[0], resolved[1].lower())]
        day = dates.get(entry, entry[:10])
        slot["obs"].extend((day, t) for t in obs)
        slot["entries"].add(entry)
    for slot in out.values():
        slot["obs"].sort(key=lambda o: o[0])
    return out


def qualifies(info: dict, slot: dict | None) -> bool:
    if not slot or info.get("generic") or info.get("unsorted"):
        return False
    return len(slot["obs"]) >= MIN_OBSERVATIONS and len(slot["entries"]) >= MIN_ENTRIES


def status(record: dict | None, count: int, obs_hash: str) -> str:
    """missing | due (the close rewrites it) | changed (observations moved
    on, not enough to rewrite unasked, or it's the author's own text) |
    current."""
    if not record:
        return "missing"
    if record["hash"] == obs_hash:
        return "current"
    gained = count - record.get("count", 0)
    if not record.get("edited") and gained >= min(REFRESH_NEW, max(1, record.get("count", 0) * REFRESH_GROWTH)):
        return "due"
    return "changed"


def _entity_key(index: dict, name: str) -> str:
    return entities.curation_key(index[name]["type"], entities.base_name(index, name))


def candidates(index: dict, by_entity: dict | None = None) -> list[dict]:
    """Every qualifying entity, with its observations and summary status."""
    by_entity = by_entity if by_entity is not None else observations_by_entity()
    stored = entities.load_summaries()
    out = []
    for name, info in index.items():
        base = entities.base_name(index, name)
        slot = by_entity.get((info["type"], base.lower()))
        if not qualifies(info, slot):
            continue
        key = _entity_key(index, name)
        obs_hash = _hash(slot["obs"])
        out.append({
            "name": name, "base": base, "key": key, "info": info, "obs": slot["obs"],
            "hash": obs_hash, "status": status(stored.get(key), len(slot["obs"]), obs_hash),
        })
    return out


def _observation_text(obs: list[tuple[str, str]]) -> str:
    picked, used = [], 0
    for day, text in reversed(obs):
        line = f"[{day}] {text}"
        if used + len(line) > INPUT_CHARS and picked:
            break
        picked.append(line)
        used += len(line) + 1
    left = len(obs) - len(picked)
    head = [f"({left} earlier observations, from {obs[0][0]} on, left out for length)"] if left else []
    return "\n".join(head + picked[::-1])


def _author_s() -> str:
    return f"{AUTHOR}'" if AUTHOR.endswith("s") else f"{AUTHOR}'s"


def _prompt(cand: dict) -> str:
    info, name = cand["info"], cand["base"]
    kind = info["type"]
    attr = info.get("category") if kind == "thing" else next(iter(info.get("attrs", {})), "")
    a_kind = f"{'an' if kind == 'animal' else 'a'} {kind}"
    if attr and kind in ("person", "animal"):
        intro = f"{name}, {a_kind}, is {_author_s()} {attr}."
    else:
        intro = f"{name} is {a_kind}" + (f" ({attr})." if attr else ".")
    extra = []
    if info.get("aliases"):
        extra.append(f" Also called: {', '.join(info['aliases'])}.")
    if info.get("part_of"):
        extra.append(f" Part of {info['part_of']}.")
    if info.get("groups"):
        extra.append(f" In {_author_s()} groups: {', '.join(info['groups'])}.")
    if info.get("retired"):
        extra.append(f" {AUTHOR} has marked {name} a past chapter: write about it in the past tense.")
    care = CARE.get(kind, CARE_OTHER).format(name=name, author=AUTHOR, kind=kind)
    return SUMMARY_PROMPT.format(
        name=name, author=AUTHOR, intro=intro, extra="".join(extra),
        last=cand["obs"][-1][0], care=care, text=_observation_text(cand["obs"]),
    )


def write_one(client, cand: dict) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        **processing_thinking_kwargs(),
        messages=[{"role": "user", "content": _prompt(cand)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return next(b.text for b in response.content if b.type == "text").strip()


def store(cand: dict, text: str, edited: bool = False) -> dict:
    """Keep one summary. Returns {key, before, after} for the undo history."""
    with entities.WRITE_LOCK:
        stored = entities.load_summaries()
        before = stored.get(cand["key"])
        stored[cand["key"]] = {
            "text": text, "hash": cand["hash"], "count": len(cand["obs"]),
            "written": date.today().isoformat(), "edited": edited,
        }
        entities.save_summaries(stored)
        return {"key": cand["key"], "before": before, "after": stored[cand["key"]]}


def estimate(index: dict) -> dict:
    """How many summaries are missing or due, and roughly what writing
    them costs, before spending."""
    from config import MODEL_PRICES
    prices = MODEL_PRICES.get(MODEL, {"in": 0, "out": 0})
    todo = [c for c in candidates(index) if c["status"] in ("missing", "due")]
    cost = sum((len(_prompt(c)) / 4 * prices["in"] + _EST_OUTPUT_TOKENS * prices["out"]) / 1_000_000
               for c in todo)
    return {"count": len(todo), "estimate": round(cost, 2), "model": MODEL,
            "missing": sum(c["status"] == "missing" for c in todo)}


def refresh(index: dict, progress=None, quiet: bool = True, limit: int | None = None) -> dict:
    """Write every missing or due summary, or the first `limit` of them:
    due ones first (the entity is active, and its page already shows a
    summary going stale), then the best-known. A spend cap stops the run;
    what's written is kept. The caller rebuilds the entity docs."""
    import caps
    todo = sorted((c for c in candidates(index) if c["status"] in ("missing", "due")),
                  key=lambda c: (c["status"] != "due", -len(c["obs"])))[:limit]
    if not todo:
        return {"done": [], "failed": []}
    client = get_client()
    done, failed = [], []
    for i, cand in enumerate(todo):
        if progress:
            progress(i, len(todo), cand["name"])
        try:
            store(cand, write_one(client, cand))
        except caps.CapExceeded:
            raise
        except Exception as e:
            failed.append({"name": cand["name"], "error": str(e)})
            continue
        done.append(cand["name"])
        if not quiet:
            print(f"  {cand['name']} -> summarized")
    return {"done": done, "failed": failed}


def for_entity(index: dict, name: str, observations: list[dict]) -> dict:
    """What the entity page shows: the summary (if any), whether the
    entity qualifies, and whether its observations moved on since.
    `observations` is list_observations' answer, already in hand."""
    slot = {"obs": [(o["date"], o["text"]) for o in observations],
            "entries": {o["file"] for o in observations}}
    record = entities.load_summaries().get(_entity_key(index, name))
    out = {"qualifies": qualifies(index[name], slot), "min": MIN_OBSERVATIONS}
    if record:
        out.update(text=record["text"], written=record["written"], edited=record.get("edited", False),
                   count=record.get("count", 0),
                   status=status(record, len(slot["obs"]), _hash(slot["obs"])))
    return out


def candidate(index: dict, name: str) -> dict | None:
    """One entity as a candidate, whether or not it qualifies -- a rewrite
    on request works for any entity that has observations."""
    info = index[name]
    base = entities.base_name(index, name)
    slot = observations_by_entity().get((info["type"], base.lower()))
    if not slot:
        return None
    return {"name": name, "base": base, "key": _entity_key(index, name), "info": info,
            "obs": slot["obs"], "hash": _hash(slot["obs"])}


def edit(index: dict, name: str, text: str) -> dict:
    """The author's own wording: kept until they rewrite it."""
    cand = candidate(index, name) or {"key": _entity_key(index, name), "obs": [], "hash": ""}
    return store(cand, text.strip()[:MAX_CHARS], edited=True)


def move(old_key: str, new_key: str):
    """A summary follows its entity to a new name."""
    with entities.WRITE_LOCK:
        stored = entities.load_summaries()
        if old_key in stored and new_key not in stored:
            stored[new_key] = stored.pop(old_key)
            entities.save_summaries(stored)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "estimate":
        print(estimate(entities.load_index()))
    elif command == "build":
        result = refresh(entities.load_index(), quiet=False)
        entities.build(quiet=True)
        print(f"  {len(result['done'])} written, {len(result['failed'])} failed")
    else:
        print(__doc__)
