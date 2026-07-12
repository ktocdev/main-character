"""
Resplit — Phase 3 one-shot migration: per-day entry granularity.

One imported Claude conversation used to be one "entry" spanning up to a
week under a single date. This script re-splits every imported
conversation into per-day entries (each local calendar day the user
wrote = its own dated entry), then re-runs the full memory pipeline
fresh at day granularity.

What it does, in order:
    1. preflight   — refuse to run while the dev server is up
    2. backup      — full copy of every data dir to backups/pre_resplit_*
    3. match       — pair chroma bulk_import conversations with export records
    4. split       — group each conversation's user messages by local day
    5. migrate     — delete old week-blob chunks, import per-day chunks
    6. quarantine  — move (never delete) all per-conversation caches aside
    7. braids      — write additive per-day braid files for history display
    8. manifest    — sessions/legacy_map.json maps old keys → new day keys
    9. pipeline    — entities → categories → dreams → summarizer → patterns

Stored braids and archives are never modified; the open session's base
references are updated to the new day keys so history doesn't
double-list. Old caches keep the user's observation edits on disk in the
backup for reference.

Usage:
    .venv\\Scripts\\python.exe resplit.py --dry-run       preview, no changes
    .venv\\Scripts\\python.exe resplit.py                 migrate + pipeline
    .venv\\Scripts\\python.exe resplit.py --migrate-only  no API calls yet
    .venv\\Scripts\\python.exe resplit.py --pipeline-only resume after a crash
    --keep-tiny        keep sub-100-char days (meta-requests) as entries
    --skip-patterns    don't rebuild the pattern library at the end
"""

import argparse
import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rag_journal import get_collection, local_day, JOURNAL_DIR
from bulk_import import (
    load_conversations,
    extract_user_entries,
    chunk_entry,
    import_entry,
    entry_chunk_id,
)

ROOT = Path(__file__).parent
EXPORT_PATH = ROOT / "conversations.json"
BACKUP_ROOT = ROOT / "backups"
LEGACY_MAP = ROOT / "sessions" / "legacy_map.json"
BRAID_DIR = ROOT / "sessions" / "braids"
CURRENT_FILE = ROOT / "sessions" / "current.json"
HISTORY_FILE = ROOT / "entity_graph" / "history.json"

SERVER_PORT = 8144
MIN_LENGTH = 100  # matches bulk_import's --min-length default

DATA_DIRS = [
    "chroma_data", "entity_graph", "categories", "summaries",
    "dreams", "sessions", "journal_entries", "patterns",
]

# (relative dir, filename prefix to leave in place)
QUARANTINE_DIRS = [
    ("entity_graph/raw", None),
    ("categories/raw", None),
    ("summaries/entries", None),
    ("summaries/arcs", None),
    ("summaries/domains", None),
    ("dreams/raw", "dreamentry_"),  # write-mode dream raws stay live
]


# ---------------------------------------------------------------------------
# PREFLIGHT & BACKUP
# ---------------------------------------------------------------------------

def preflight():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", SERVER_PORT)) == 0:
            sys.exit(f"  ABORT: dev server is running on port {SERVER_PORT} — stop it first.")
    if not EXPORT_PATH.exists():
        sys.exit(f"  ABORT: {EXPORT_PATH} not found.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("  ABORT: ANTHROPIC_API_KEY is not set (pipeline stages need it).")


def backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_ROOT / f"pre_resplit_{stamp}"
    dest.mkdir(parents=True)
    for name in DATA_DIRS:
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, dest / name)
            print(f"  backed up {name}/")
    return dest


# ---------------------------------------------------------------------------
# MATCH & SPLIT
# ---------------------------------------------------------------------------

def collect_old(collection) -> dict:
    """Every bulk_import conversation in chroma: (date,title) → its
    chunk ids and whether it was tagged a summary."""
    data = collection.get(include=["metadatas"])
    old = {}
    for chunk_id, meta in zip(data["ids"], data["metadatas"]):
        if meta.get("source") != "bulk_import":
            continue
        key = (meta.get("date", ""), meta.get("title", ""))
        rec = old.setdefault(key, {"ids": [], "is_summary": False})
        rec["ids"].append(chunk_id)
        if meta.get("is_summary") == "True":
            rec["is_summary"] = True
    return old


def match_export(old: dict) -> dict:
    """Pair each chroma conversation with its export record, keyed the
    same way the original import derived (date, title)."""
    by_key = {}
    for conv in load_conversations(str(EXPORT_PATH)):
        key = ((conv.get("created_at") or "")[:10], conv.get("name", "Untitled"))
        by_key[key] = conv
    matches, unmatched = {}, []
    for key in old:
        if key in by_key:
            matches[key] = by_key[key]
        else:
            unmatched.append(key)
    if unmatched:
        for key in unmatched:
            print(f"  UNMATCHED in export: {key}")
        sys.exit("  ABORT: some chroma conversations have no export record.")
    return matches


def plan_split(old: dict, matches: dict, keep_tiny: bool) -> tuple[list, dict]:
    """Per-day entries for every conversation, plus the manifest data.
    Aborts on chunk-id collisions (the reason ids are title-salted)."""
    day_entries = []
    manifest = {}
    seen_ids = {}
    seen_keys = {}

    for key, conv in sorted(matches.items()):
        entries = extract_user_entries(conv)
        kept, dropped = [], []
        for entry in entries:
            if len(entry["text"]) < MIN_LENGTH and not keep_tiny:
                dropped.append({"date": entry["date"], "chars": len(entry["text"])})
                continue
            entry["is_summary"] = old[key]["is_summary"]
            kept.append(entry)
        day_entries.extend(kept)

        old_key = cache_key(*key)
        manifest[old_key] = {
            "date": key[0],
            "title": key[1],
            "new_keys": [cache_key(e["date"], e["title"]) for e in kept],
            "dropped_days": dropped,
        }

        for entry in kept:
            new_key = (entry["date"], entry["title"])
            if new_key in seen_keys:
                print(f"  WARNING: two conversations produce the same (date,title): {new_key}")
            seen_keys[new_key] = old_key
            for chunk in chunk_entry(entry):
                cid = entry_chunk_id(
                    chunk["date"], chunk["title"], chunk["text"],
                    chunk.get("chunk_index", 0),
                )
                if cid in seen_ids:
                    sys.exit(f"  ABORT: chunk id collision {cid} "
                             f"({seen_ids[cid]} vs {old_key})")
                seen_ids[cid] = old_key

    return day_entries, manifest


def cache_key(date: str, title: str) -> str:
    from entities import conversation_cache_key
    return conversation_cache_key({"date": date, "title": title})


# ---------------------------------------------------------------------------
# MIGRATE
# ---------------------------------------------------------------------------

def move_old_markdown(old: dict, dest: Path):
    """Old week-blob markdown backups move into the backup dir so the
    per-day files written on import are the only live copies."""
    moved = 0
    md_dir = dest / "old_markdown"
    for date, title in old:
        safe = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:50]
        for path in JOURNAL_DIR.glob(f"{date}_{safe}*.md"):
            md_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(md_dir / path.name))
            moved += 1
    print(f"  moved {moved} old markdown backups")


def delete_old_chunks(collection, old: dict):
    ids = [cid for rec in old.values() for cid in rec["ids"]]
    for i in range(0, len(ids), 500):
        collection.delete(ids=ids[i:i + 500])
    print(f"  deleted {len(ids)} old chunks from {len(old)} conversations")


def import_day_entries(collection, day_entries: list):
    total = len(day_entries)
    for i, entry in enumerate(day_entries, 1):
        for chunk in chunk_entry(entry):
            import_entry(chunk, collection)
        print(f"  [{i}/{total}] {entry['date']} | {entry['title'][:50]}")


def quarantine_caches(dest: Path):
    qroot = dest / "quarantine"
    for rel, keep_prefix in QUARANTINE_DIRS:
        src = ROOT / Path(rel)
        if not src.exists():
            continue
        moved = 0
        for path in sorted(src.iterdir()):
            if not path.is_file():
                continue
            if keep_prefix and path.name.startswith(keep_prefix):
                continue
            target = qroot / rel
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target / path.name))
            moved += 1
        print(f"  quarantined {moved} files from {rel}/")


def reset_history():
    """Raw-kind undo entries restore cache files by their old filenames —
    replaying one after the re-key would resurrect an orphan raw file.
    The full history is preserved in the backup."""
    if HISTORY_FILE.exists():
        HISTORY_FILE.write_text(json.dumps({"undo": [], "redo": []}), encoding="utf-8")
        print("  cleared entity undo/redo history (full copy in backup)")


def quarantine_old_braids(manifest: dict, dest: Path):
    """Whole-conversation braid files for re-split conversations move to
    the backup: they're superseded by the per-day braids (same messages,
    regenerable from conversations.json), and a day-one braid can share
    the old file's name, so the old file must not shadow it."""
    target = dest / "quarantine" / "sessions_braids"
    moved = 0
    for old_key in manifest:
        path = BRAID_DIR / f"{old_key}.json"
        if path.exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target / path.name))
            moved += 1
    print(f"  quarantined {moved} whole-conversation braids")


def _local_stamp(ts: str) -> str:
    raw = (ts or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:16].replace("T", " ")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def write_day_braids(matches: dict, manifest: dict) -> int:
    """Additive per-day braid files (both sides, local timestamps) so the
    history entry modal can show the full conversation for a day entry.
    Existing braid files are never touched."""
    made = 0
    BRAID_DIR.mkdir(parents=True, exist_ok=True)
    for (date, title), conv in matches.items():
        wanted = set(manifest[cache_key(date, title)]["new_keys"])
        by_day = {}
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
            day = local_day(m.get("created_at", ""))
            by_day.setdefault(day, []).append({
                "role": "you" if sender in ("human", "user") else "companion",
                "text": text,
                "ts": _local_stamp(m.get("created_at", "")),
            })
        for day, msgs in by_day.items():
            key = cache_key(day, title)
            path = BRAID_DIR / f"{key}.json"
            if key not in wanted or path.exists():
                continue
            path.write_text(json.dumps(
                {"date": day, "title": title, "messages": msgs},
                indent=2, ensure_ascii=False), encoding="utf-8")
            made += 1
    print(f"  wrote {made} per-day braid files")
    return made


def update_session_base(manifest: dict):
    """The open session's base may reference a re-split conversation.
    Point it at the new day keys (same content, day-granular) so the
    history sidebar doesn't list them twice."""
    if not CURRENT_FILE.exists():
        return
    cur = json.loads(CURRENT_FILE.read_text(encoding="utf-8"))
    new_base, changed = [], False
    for part in cur.get("base") or []:
        old_key = cache_key(part["date"], part["title"])
        if old_key in manifest:
            for nk in manifest[old_key]["new_keys"]:
                new_base.append({"date": nk[:10], "title": part["title"]})
            changed = True
        else:
            new_base.append(part)
    if changed:
        cur["base"] = new_base
        CURRENT_FILE.write_text(
            json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  open session base now references {len(new_base)} day parts")


def write_manifest(manifest: dict):
    LEGACY_MAP.parent.mkdir(parents=True, exist_ok=True)
    LEGACY_MAP.write_text(json.dumps({
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "timezone": str(datetime.now().astimezone().tzinfo),
        "note": "Phase 3 re-split: old conversation cache keys → per-day entry keys",
        "conversations": manifest,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  manifest written to {LEGACY_MAP}")


# ---------------------------------------------------------------------------
# PIPELINE
# ---------------------------------------------------------------------------

def run_pipeline(skip_patterns: bool):
    """Fresh full run. Quarantined caches make every day-entry look
    unprocessed, so plain build() does the work and stays resumable —
    rerun with --pipeline-only after any crash and finished calls skip.

    Order matters: entities first (categories' custom tags read the
    entity records), dreams before summarizer (the snapshot's dream
    weather and the embedding sync read the fresh dream index)."""
    import entities, categories, dreams, summarizer

    stages = [
        ("entity extraction", lambda: entities.build()),
        ("category tagging", lambda: categories.build()),
        ("dream re-scan", lambda: dreams.extract()),
        ("summaries (arcs, domains, entries, snapshot, embeddings)",
         lambda: summarizer.build()),
    ]
    if not skip_patterns:
        import patterns
        stages.append(("pattern library", lambda: patterns.build(force=True)))

    t0 = time.time()
    for name, fn in stages:
        print(f"\n{'=' * 60}\n  STAGE: {name}\n{'=' * 60}", flush=True)
        t = time.time()
        fn()
        print(f"  stage done in {(time.time() - t) / 60:.1f} min", flush=True)
    print(f"\n  PIPELINE COMPLETE in {(time.time() - t0) / 60:.1f} min")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Phase 3: per-day entry re-split")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--migrate-only", action="store_true")
    parser.add_argument("--pipeline-only", action="store_true")
    parser.add_argument("--keep-tiny", action="store_true")
    parser.add_argument("--skip-patterns", action="store_true")
    args = parser.parse_args()

    preflight()

    if args.pipeline_only:
        run_pipeline(args.skip_patterns)
        return

    if LEGACY_MAP.exists() and not args.dry_run:
        sys.exit(f"  ABORT: {LEGACY_MAP} exists — the re-split already ran. "
                 "Use --pipeline-only to resume the pipeline.")

    collection = get_collection()
    old = collect_old(collection)
    if not old:
        sys.exit("  Nothing to migrate — no bulk_import conversations found.")
    matches = match_export(old)
    day_entries, manifest = plan_split(old, matches, args.keep_tiny)

    n_dropped = sum(len(m["dropped_days"]) for m in manifest.values())
    multi = sum(1 for m in manifest.values() if len(m["new_keys"]) > 1)
    print(f"\n  {len(old)} conversations → {len(day_entries)} day-entries "
          f"({multi} multi-day, {n_dropped} tiny days dropped)")

    if args.dry_run:
        print()
        for old_key, m in manifest.items():
            days = [k[:10] for k in m["new_keys"]]
            span = f"{days[0]} → {days[-1]}" if days else "(all dropped)"
            print(f"  {old_key}")
            print(f"      {len(days)} days: {span}")
            for d in m["dropped_days"]:
                print(f"      dropped {d['date']} ({d['chars']} chars)")
        print("\n  DRY RUN — nothing changed.")
        return

    print("\n  backing up…")
    dest = backup()
    print(f"\n  migrating (backup at {dest})…")
    move_old_markdown(old, dest)
    delete_old_chunks(collection, old)
    import_day_entries(collection, day_entries)
    quarantine_caches(dest)
    reset_history()
    quarantine_old_braids(manifest, dest)
    write_day_braids(matches, manifest)
    update_session_base(manifest)
    write_manifest(manifest)
    print(f"\n  MIGRATION COMPLETE — {collection.count()} chunks in store")

    if not args.migrate_only:
        run_pipeline(args.skip_patterns)


if __name__ == "__main__":
    main()
