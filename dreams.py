"""
Dream Layer — Phase 3A of the RAG Journal.

Dreams share entities with waking life but are a different data type: a
dream about your boss isn't a meeting with your boss. This module keeps
the realms separate while linking them.

  Extraction     — Claude finds every discrete dream described in the
                   journal (only entries that mention dreams are scanned;
                   results cached per conversation). Each dream gets a
                   narrative retelling, its cast (people/places, spelled
                   to match the waking entity graph), emotional tones,
                   and the author's own interpretation when one was given
                   in the text — those reads are data too.
  Flagged entries— new entries marked "this was a dream" in write mode
                   are stored whole in the dream realm. User-flagged only:
                   no language-cue guessing — half the bar stories would
                   qualify.
  Realm isolation— dreams live in their own vector collection
                   (journal_dreams), so waking queries can never surface
                   a dream by accident. Asking about dreams crosses the
                   boundary explicitly.
  Dream weather  — a one-line tone signal computed from recent dreams,
                   appended to the status snapshot.

v1 deliberately skips scene-level chunking and emergent symbol detection
(they need more dream volume to mean anything) — the raw material for
both is preserved.

Usage:
    python dreams.py extract        # scan journal for dreams (cached)
    python dreams.py list           # show the dream index
    python dreams.py weather        # print the current dream-weather line

Layout (gitignored — personal data):
    dreams/raw/        per-source extraction JSON (API-call cache)
    dreams/index.json  flat dream list + per-entity appearances
"""

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date as date_type, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


from config import AUTHOR, CHROMA_DIR, DREAM_DIR, JOURNAL_DIR, MC_PROCESSING_MODEL as MODEL, get_client, processing_thinking_kwargs
from entities import get_conversations, conversation_cache_key, known_people_hint

RAW_DIR = DREAM_DIR / "raw"
INDEX_FILE = DREAM_DIR / "index.json"
DREAM_HINT = re.compile(r"\b(dream|dreams|dreamt|dreamed|nightmare|nightmares)\b", re.I)
INPUT_CHARS = 45_000
WEATHER_DAYS = 14

TONES = ["nightmare", "anxiety", "processing", "peaceful",
         "surreal", "lucid", "nostalgic", "joyful"]

EXTRACTION_PROMPT = """\
Find every actual sleep-dream {author} describes in this journal entry. \
Entries mix waking life with dream retellings — extract only dreams that \
were dreamt while asleep. Not daydreams, not aspirations, not the idiom \
("dream job"), not song lyrics.
{whole_entry_note}
For each dream:
- narrative: a faithful 2-5 sentence retelling in {author}'s own terms and \
vocabulary. Keep the strange specifics — the sequence of scenes matters.
- people / places: who and where appears, including dream-only figures. \
{known_block}
- tones: one or two emotional tones from the allowed list.
- interpretation: {author}'s OWN reading of the dream if the entry gives \
one, in their words. Empty string if they didn't interpret it. Never \
invent an interpretation — no dream dictionary, no symbolism of yours.

Return an empty list if there are no actual dreams here.

<entry date="{date}" title="{title}">
{text}
</entry>"""

KNOWN_BLOCK = "Use these exact spellings when a dream figure matches a known person: {names}."

EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dreams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "narrative": {"type": "string"},
                    "people": {"type": "array", "items": {"type": "string"}},
                    "places": {"type": "array", "items": {"type": "string"}},
                    "tones": {"type": "array", "items": {"type": "string", "enum": TONES}},
                    "interpretation": {"type": "string"},
                },
                "required": ["narrative", "people", "places", "tones", "interpretation"],
            },
        },
    },
    "required": ["dreams"],
}


def _extract(client, text: str, date: str, title: str,
             whole_entry_is_dream: bool = False) -> list[dict]:
    known = known_people_hint()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        **processing_thinking_kwargs(),
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT.format(
                author=AUTHOR, date=date, title=title, text=text[:INPUT_CHARS],
                whole_entry_note=(
                    "\nThis entire entry IS a dream the author flagged "
                    "themselves — extract it as one dream (or more if it "
                    "clearly contains several).\n" if whole_entry_is_dream else ""
                ),
                known_block=KNOWN_BLOCK.format(names=", ".join(known)) if known else "",
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    raw = next(b.text for b in response.content if b.type == "text")
    return json.loads(raw)["dreams"]


def run_extraction(force: bool = False, quiet: bool = False) -> int:
    """Scan every conversation that mentions dreams; cache per conversation.
    Returns how many sources were newly extracted."""
    client = get_client()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    new = 0
    candidates = [c for c in get_conversations() if DREAM_HINT.search(c["text"])]
    if not quiet:
        print(f"  {len(candidates)} conversations mention dreams")
    for conv in candidates:
        key = conversation_cache_key(conv)
        cache_file = RAW_DIR / f"{key}.json"
        if cache_file.exists() and not force:
            if not quiet:
                print(f"  {key} (cached)")
            continue
        try:
            found = _extract(client, conv["text"], conv["date"], conv["title"])
        except Exception as e:
            print(f"  {key} FAILED: {e}")
            continue
        cache_file.write_text(json.dumps({
            "date": conv["date"], "title": conv["title"], "dreams": found,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        new += 1
        if not quiet:
            print(f"  {key} -> {len(found)} dream{'s' if len(found) != 1 else ''}")
    return new


# ---------------------------------------------------------------------------
# FLAGGED DREAM ENTRIES (write mode, "this was a dream")
# ---------------------------------------------------------------------------

def get_dream_collection():
    """Dreams live in their own collection so waking queries can never
    surface one by accident. Crossing realms is always explicit."""
    import chromadb

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name="journal_dreams",
        metadata={"hnsw:space": "cosine"},
    )


def store_dream_entry(text: str, when=None) -> tuple[str, Path]:
    """Store a user-flagged dream entry: dream collection + markdown
    backup. Never touches the waking collection or its pipelines.
    Returns (entry_id, backup_path)."""
    from config import now_local
    now = when or now_local()
    date = now.strftime("%Y-%m-%d")
    time_of_day = now.strftime("%H:%M")
    entry_id = f"dreamentry_{date}_{hashlib.md5(text[:200].encode()).hexdigest()[:8]}"

    get_dream_collection().upsert(
        ids=[entry_id],
        documents=[text],
        metadatas=[{
            "date": date, "time": time_of_day, "realm": "dream",
            "title": f"Dream {date} {time_of_day}", "source": "write_mode",
        }],
    )
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = JOURNAL_DIR / f"{date}_{now.strftime('%H%M')}_dream.md"
    filepath.write_text(
        f"# Dream — {date} {time_of_day}\n_Date: {date}_\n_Realm: dream_\n\n{text}",
        encoding="utf-8",
    )
    return entry_id, filepath


def ingest_dream_entry(text: str, entry_id: str, when=None):
    """Background follow-up to store_dream_entry: extract the dream(s),
    rebuild the index, refresh weather on the snapshot. `when` must match
    what store_dream_entry used, or the extraction files under a different
    day than the entry it came from."""
    from config import now_local
    client = get_client()
    now = when or now_local()
    date = now.strftime("%Y-%m-%d")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    try:
        found = _extract(client, text, date, f"Dream {date}",
                         whole_entry_is_dream=True)
    except Exception as e:
        print(f"  dream ingest failed: {e}")
        return
    (RAW_DIR / f"{entry_id}.json").write_text(json.dumps({
        "date": date, "title": f"Dream {date}", "dreams": found,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    build_index()
    _stamp_weather_on_snapshot()


# ---------------------------------------------------------------------------
# INDEX + REALM COLLECTION SYNC
# ---------------------------------------------------------------------------

def build_index() -> dict:
    """Flatten the raw caches into dreams/index.json and mirror each dream
    into the journal_dreams collection (local embeddings, free)."""
    dreams = []
    for path in sorted(RAW_DIR.glob("*.json")) if RAW_DIR.exists() else []:
        data = json.loads(path.read_text(encoding="utf-8"))
        for i, d in enumerate(data["dreams"]):
            dreams.append({
                "id": f"{path.stem}:{i}",
                "date": data["date"], "source": data["title"],
                **d,
            })
    dreams.sort(key=lambda d: d["date"], reverse=True)

    by_entity = defaultdict(list)
    for d in dreams:
        for name in d["people"] + d["places"]:
            by_entity[name].append(d["id"])

    index = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "dreams": dreams,
        "by_entity": dict(sorted(by_entity.items())),
    }
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    collection = get_dream_collection()
    ids, docs, metas = [], [], []
    for d in dreams:
        ids.append(f"dream:{d['id']}")
        cast = ", ".join(d["people"] + d["places"])
        doc = f"[{d['date']}] dream ({'/'.join(d['tones'])}): {d['narrative']}"
        if cast:
            doc += f"\nCast: {cast}"
        if d["interpretation"]:
            doc += f"\n{AUTHOR}'s read: {d['interpretation']}"
        docs.append(doc)
        metas.append({
            "date": d["date"], "realm": "dream",
            "tones": "/".join(d["tones"]),
            "cast": cast[:250], "source": d["source"][:100],
        })
    existing = collection.get()["ids"]
    stale = [i for i in existing if i.startswith("dream:") and i not in set(ids)]
    if stale:
        collection.delete(ids=stale)
    if ids:
        collection.upsert(ids=ids, documents=docs, metadatas=metas)
    return index


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"generated": None, "dreams": [], "by_entity": {}}


# ---------------------------------------------------------------------------
# DREAM WEATHER
# ---------------------------------------------------------------------------

def dream_weather(days: int = WEATHER_DAYS) -> str:
    """One-line tone signal from recent dreams; empty when there are none."""
    index = load_index()
    cutoff = (date_type.today() - timedelta(days=days)).isoformat()
    recent = [d for d in index["dreams"] if d["date"] >= cutoff]
    if not recent:
        return ""
    tones = Counter(t for d in recent for t in d["tones"])
    cast = Counter(n for d in recent for n in d["people"])
    top_tones = "/".join(t for t, _ in tones.most_common(2))
    line = f"Dream weather (last {days} days): {top_tones} ({len(recent)} dream{'s' if len(recent) != 1 else ''}"
    if cast:
        name, n = cast.most_common(1)[0]
        if n > 1:
            line += f", {n} involving {name}"
    return line + ")"


def _stamp_weather_on_snapshot():
    """Re-append the current weather line to the status snapshot file."""
    from summarizer import SNAPSHOT_FILE, append_dream_weather
    if SNAPSHOT_FILE.exists():
        append_dream_weather()


def extract(force: bool = False, quiet: bool = False) -> dict:
    n = run_extraction(force=force, quiet=quiet)
    index = build_index()
    print(f"\n  Dreams: {len(index['dreams'])} in the index ({n} sources newly extracted)")
    return index


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "extract":
        extract(force="--force" in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        index = load_index()
        for d in index["dreams"]:
            print(f"\n[{d['date']}] {'/'.join(d['tones'])}")
            print(f"  {d['narrative']}")
            if d["people"] or d["places"]:
                print(f"  cast: {', '.join(d['people'] + d['places'])}")
            if d["interpretation"]:
                print(f"  {AUTHOR}'s read: {d['interpretation']}")
    elif len(sys.argv) > 1 and sys.argv[1] == "weather":
        print(dream_weather() or "  no recent dreams")
    else:
        print(__doc__)
