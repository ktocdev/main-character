"""
Import the seed corpus into the local data stores.

Reads the markdown entries from journal_entries/ and the dream entry from
its _Realm: dream_ marker, then populates:
  - chroma_data/  (journal_entries + journal_dreams collections)
  - journal_entries/  (markdown backups)
  - sessions/archive/  (the three closed sessions, both-sided braids)
  - sessions/current.json  (the empty session the last close opened)
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
slate path for capture_fixtures.py). Because it deletes outright, it
refuses to run unless the data dirs point somewhere under seed_corpus/ —
use `bash seed_corpus/run_capture.sh --wipe`, which sets them for you.

A plain install is allowed against a real, empty journal, but refuses
the moment it finds session archives or a seed it didn't ship.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bulk_import import import_entry, entry_chunk_id, chunk_entry
from config import ENTITY_DIR, CATEGORY_DIR, PATTERN_DIR, DREAM_DIR
from config import SESSION_DIR, SUMMARY_DIR, CHROMA_DIR
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


def session_files() -> list[tuple[Path, Path]]:
    """The seed loop's own history, produced by build_sessions.py: three
    closed sessions with both-sided braids, the live seed those closes
    generated, the seed it replaced, and the candidate still awaiting
    review. Copied as-is — none of it is regenerated at import.

    current.json ships too, and has to. Without it the first run falls into
    load_current's first-run path, which builds a base from the newest
    imported conversation — a session that looks like it continues 9/14
    rather than one the 9/14 close just opened. That contradicts the pending
    candidate sitting next to it: a real close ends with save_current(_fresh())
    and an empty base. Shipping the file makes the demo state the corpus's
    own, instead of whatever the first person to launch it happened to
    generate."""
    here = Path(__file__).parent
    pairs = [(here / "sessions" / "archive", SESSION_DIR / "archive"),
             (here / "summaries" / "seed_backups",
              SUMMARY_DIR / "seed_backups")]
    files = [(here / "summaries" / n, SUMMARY_DIR / n)
             for n in ("seed_summary.md", "seed_summary.candidate.md")]
    files.append((here / "sessions" / "current.json",
                  SESSION_DIR / "current.json"))

    for src_dir, dst_dir in pairs:
        for src in sorted(src_dir.glob("*")):
            files.append((src, dst_dir / src.name))
    return files


def refuse_if_real_journal(files: list[tuple[Path, Path]]):
    """Never clobber a real journal. Anything already in these stores that
    we didn't ship means they belong to an actual author, not a demo.

    Runs before anything is wiped or written, not after — this is the only
    thing standing between --wipe and someone's entries."""
    here = Path(__file__).parent
    ours = {src.name for src, _ in files}
    intruders = [p.name for d in (SESSION_DIR / "archive",
                                  SUMMARY_DIR / "seed_backups")
                 if d.exists() for p in d.glob("*") if p.name not in ours]
    live = SUMMARY_DIR / "seed_summary.md"
    if live.exists() and live.read_text(encoding="utf-8") != \
            (here / "summaries" / "seed_summary.md").read_text(encoding="utf-8"):
        intruders.append(live.name)
    # the open session is installed now, so it can also be overwritten. Only
    # unsaved turns make it precious — a base-only or empty session is what
    # any first run invents, and replacing that is the point.
    open_session = SESSION_DIR / "current.json"
    if open_session.exists():
        try:
            live_msgs = json.loads(
                open_session.read_text(encoding="utf-8")).get("messages") or []
        except (ValueError, OSError):
            live_msgs = ["unreadable"]   # can't vouch for it: treat as theirs
        if live_msgs:
            intruders.append(open_session.name)
    if intruders:
        sys.exit(
            f"\nrefusing to install: {SUMMARY_DIR.parent} already holds a "
            f"journal that isn't the seed corpus\n  ({', '.join(intruders[:4])}"
            f"{'…' if len(intruders) > 4 else ''})\n"
            "  the seed corpus is for a fresh install — move or back up "
            "that data first.")


def refuse_if_unsandboxed():
    """--wipe is the capture path, and capture always runs against a
    throwaway dir under seed_corpus/ (see run_capture.sh). Installing into
    a real fresh journal is fine; *wiping* one never is, so the destructive
    flag is the one that demands a sandbox.

    The plain-install path is guarded by refuse_if_real_journal instead —
    that one has to stay usable against a genuine empty install."""
    corpus = Path(__file__).resolve().parent
    for d in (JOURNAL_DIR, CHROMA_DIR, ENTITY_DIR,
              CATEGORY_DIR, PATTERN_DIR, DREAM_DIR):
        if corpus not in Path(d).resolve().parents:
            sys.exit(
                f"\nrefusing to --wipe: {d} is outside {corpus}\n"
                "  --wipe deletes entries, collections and the entity graph "
                "outright.\n  point the data dirs at a scratch dir first — "
                "bash seed_corpus/run_capture.sh does this for you.")


def install_sessions(files: list[tuple[Path, Path]], dry_run: bool):
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

    # Both guards run before the first destructive or writing call. They
    # used to sit inside install_sessions(), i.e. after --wipe had already
    # deleted the entries they were meant to protect.
    to_install = session_files()
    refuse_if_real_journal(to_install)
    if args.wipe:
        refuse_if_unsandboxed()

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

    install_sessions(to_install, args.dry_run)

    print(f"\ndone. {'(dry run, nothing written)' if args.dry_run else ''}")
    if not args.dry_run:
        print("next: python capture_fixtures.py --stages tag,entities,summaries,dreams,patterns,organic --i-am-running-the-seed-corpus")


if __name__ == "__main__":
    main()
