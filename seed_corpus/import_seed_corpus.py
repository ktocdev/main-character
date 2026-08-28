"""
Import the seed corpus into the local data stores.

Reads the markdown entries from journal_entries/ and the dream entry from
its _Realm: dream_ marker, then populates:
  - chroma_data/  (journal_entries + journal_dreams collections)
  - journal_entries/  (markdown backups)
  - sessions/archive/  (the three closed sessions, both-sided braids)
  - summaries/  (the live seed, its backup, the pending candidate)

The session archives and seed summaries are not written here — they are
produced by build_sessions.py, which chats the corpus through the real
companion. This script only installs them.

Usage:
    python seed_corpus/import_seed_corpus.py
    python seed_corpus/import_seed_corpus.py --dry-run
    python seed_corpus/import_seed_corpus.py --wipe

--wipe clears journal_entries collection, journal_dreams collection,
journal_entries/ dir, and entity_graph/ before importing (the clean-
slate path for capture_fixtures.py).
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bulk_import import import_entry, entry_chunk_id, chunk_entry
from config import ENTITY_DIR, CATEGORY_DIR, PATTERN_DIR, DREAM_DIR
from config import SESSION_DIR, SUMMARY_DIR
from rag_journal import get_collection, JOURNAL_DIR


def parse_entry(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    date_match = re.search(r"_Date:\s*(\d{4}-\d{2}-\d{2})_", text)
    if not date_match:
        raise ValueError(f"no _Date:_ line in {path.name}")
    date = date_match.group(1)

    title_match = re.match(r"^#\s+(.+)", text)
    title = title_match.group(1).strip() if title_match else path.stem

    is_dream = bool(re.search(r"_Realm:\s*dream_", text))

    body_start = text.find("\n\n")
    body = text[body_start:].strip() if body_start != -1 else text
    return {"date": date, "title": title, "text": body, "is_dream": is_dream}


def wipe():
    print("wiping local data stores...")
    import chromadb
    from config import CHROMA_DIR
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for name in ["journal_entries", "journal_dreams", "journal_summaries"]:
        try:
            client.delete_collection(name)
            print(f"  deleted collection: {name}")
        except Exception:
            pass

    if JOURNAL_DIR.exists():
        for f in JOURNAL_DIR.glob("*.md"):
            f.unlink()
        print(f"  cleared {JOURNAL_DIR}")

    for d in [ENTITY_DIR, CATEGORY_DIR, PATTERN_DIR, DREAM_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"  removed {d.name}/")
    print()


def import_dream(entry: dict):
    from dreams import get_dream_collection, DREAM_DIR
    import hashlib, json
    date = entry["date"]
    text = entry["text"]
    entry_id = f"dreamentry_{date}_{hashlib.md5(text[:200].encode()).hexdigest()[:8]}"

    get_dream_collection().upsert(
        ids=[entry_id],
        documents=[text],
        metadatas=[{
            "date": date, "time": "03:15", "realm": "dream",
            "title": entry["title"], "source": "seed_corpus",
        }],
    )
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = JOURNAL_DIR / f"{date}_dream.md"
    filepath.write_text(
        f"# {entry['title']}\n_Date: {date}_\n_Realm: dream_\n\n{text}",
        encoding="utf-8",
    )
    return entry_id


def install_sessions(dry_run: bool):
    """The seed loop's own history, produced by build_sessions.py: three
    closed sessions with both-sided braids, the live seed those closes
    generated, the seed it replaced, and the candidate still awaiting
    review. Copied as-is — none of it is regenerated at import."""
    here = Path(__file__).parent
    pairs = [(here / "sessions" / "archive", SESSION_DIR / "archive"),
             (here / "summaries" / "seed_backups",
              SUMMARY_DIR / "seed_backups")]
    files = [(here / "summaries" / n, SUMMARY_DIR / n)
             for n in ("seed_summary.md", "seed_summary.candidate.md")]

    for src_dir, dst_dir in pairs:
        for src in sorted(src_dir.glob("*")):
            files.append((src, dst_dir / src.name))

    # Never clobber a real journal. Anything already here that we didn't
    # ship means these stores belong to an actual author, not a demo.
    ours = {src.name for src, _ in files}
    intruders = [p.name for d in (SESSION_DIR / "archive",
                                  SUMMARY_DIR / "seed_backups")
                 if d.exists() for p in d.glob("*") if p.name not in ours]
    live = SUMMARY_DIR / "seed_summary.md"
    if live.exists() and live.read_text(encoding="utf-8") != \
            (here / "summaries" / "seed_summary.md").read_text(encoding="utf-8"):
        intruders.append(live.name)
    if intruders:
        sys.exit(
            f"\nrefusing to install: {SUMMARY_DIR.parent} already holds a "
            f"journal that isn't the seed corpus\n  ({', '.join(intruders[:4])}"
            f"{'…' if len(intruders) > 4 else ''})\n"
            "  the seed corpus is for a fresh install — move or back up "
            "that data first.")

    print()
    for src, dst in files:
        if not src.exists():
            print(f"  MISSING {src.name} — run build_sessions.py first")
            continue
        if dry_run:
            print(f"  [dry] {src.name} -> {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  {src.name} -> {dst}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wipe", action="store_true",
                        help="clear local data before importing")
    args = parser.parse_args()

    entries_dir = Path(__file__).parent / "journal_entries"
    files = sorted(entries_dir.glob("*.md"))
    if not files:
        sys.exit(f"no .md files in {entries_dir}")

    if args.wipe and not args.dry_run:
        wipe()

    entries = [parse_entry(f) for f in files]
    journal = [e for e in entries if not e["is_dream"]]
    dreams = [e for e in entries if e["is_dream"]]

    collection = get_collection()

    print(f"importing {len(journal)} journal entries + {len(dreams)} dream(s)\n")
    for e in journal:
        chunks = chunk_entry(e)
        for chunk in chunks:
            if args.dry_run:
                print(f"  [dry] {chunk['date']}  {e['title'][:50]}")
            else:
                result = import_entry(chunk, collection)
                print(f"  {result['date']}  {result['id']}  {e['title'][:50]}")

    for e in dreams:
        if args.dry_run:
            print(f"  [dry] {e['date']}  DREAM  {e['title'][:50]}")
        else:
            eid = import_dream(e)
            print(f"  {e['date']}  {eid}  DREAM  {e['title'][:50]}")

    install_sessions(args.dry_run)

    print(f"\ndone. {'(dry run, nothing written)' if args.dry_run else ''}")
    if not args.dry_run:
        print("next: python capture_fixtures.py --stages tag,entities,summaries,dreams,patterns,organic --i-am-running-the-seed-corpus")


if __name__ == "__main__":
    main()
