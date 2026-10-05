# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Import the seed corpus into the local data stores.

Reads the markdown entries from journal_entries/ and the dream entry from
its _Realm: dream_ marker, then populates:
  - chroma_data/  (journal_entries, its search passages and journal_dreams
    collections) -- only entries dated before OPEN_SESSION_FROM; the rest
    belong to the open session and are never embedded here
  - journal_entries/  (markdown backups of every entry, open ones included)
  - sessions/archive/  (the three closed sessions, both-sided braids)
  - sessions/current.json  (the open session: the days written since the
    last close, not yet closed)
  - summaries/  (the reviewed live seed and its backups)

The session archives and seed summaries are not written here — they are
produced by build_sessions.py, which chats the corpus through the real
companion. This script only installs them.

Usage:
    python seed_corpus/import_seed_corpus.py
    python seed_corpus/import_seed_corpus.py --dry-run
    python seed_corpus/import_seed_corpus.py --wipe

--wipe clears the journal_entries, journal_passages, journal_dreams and
journal_summaries collections, the journal_entries/ dir, and the
entity_graph/, categories/, patterns/ and dreams/ dirs before importing (the
clean-slate path for capture_fixtures.py). Because it deletes outright, it
refuses to run unless the data dirs point somewhere under seed_corpus/ —
use `bash seed_corpus/run_capture.sh --wipe`, which sets them for you.

A plain install is allowed against a real, empty journal, but refuses
the moment it finds session archives or a seed it didn't ship.
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --demo has to be handled before config is imported, not in main(): config
# reads the environment once at import time, and the import below is that
# moment. Parsed off sys.argv by hand for the same reason -- argparse runs
# far too late to matter.
#
# It exists so installing the demo is one command on every platform. The
# alternative is eight MC_*/RAG_* exports the reader has to get right, which
# is a bash script on Windows, i.e. not an instruction a README can give.
if "--demo" in sys.argv:
    _I = Path(__file__).resolve().parent / "install"
    os.environ.update({
        "MC_JOURNAL_DIR": str(_I / "journal_entries"),
        "MC_CHROMA_DIR": str(_I / "chroma_data"),
        "MC_ENTITY_DIR": str(_I / "entity_graph"),
        "MC_SUMMARY_DIR": str(_I / "summaries"),
        "MC_CATEGORY_DIR": str(_I / "categories"),
        "MC_PATTERN_DIR": str(_I / "patterns"),
        "MC_DREAM_DIR": str(_I / "dreams"),
        "MC_SESSION_DIR": str(_I / "sessions"),
        # The corpus is Jordan's. Left alone, entity extraction would skip
        # entities matching the real author's name -- see run_capture.sh.
        "MC_AUTHOR_NAME": "Jordan",
    })

from bulk_import import import_entry, entry_chunk_id, chunk_entry
from config import ENTITY_DIR, CATEGORY_DIR, PATTERN_DIR, DREAM_DIR
from config import SESSION_DIR, SUMMARY_DIR, CHROMA_DIR
from rag_journal import get_collection, JOURNAL_DIR

# Entries dated from here on belong to the demo's OPEN session, not to journal
# memory. They ship as markdown backups -- which is exactly what a real journal
# has on disk after "save entry" -- and enter chroma only if the visitor closes
# the chat, the same way they would for a real author.
OPEN_SESSION_FROM = "2026-09-15"


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
    for name in ["journal_entries", "journal_passages", "journal_dreams",
                 "journal_summaries"]:
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
    from dreams import put_entry
    import hashlib
    date = entry["date"]
    text = entry["text"]
    entry_id = f"dreamentry_{date}_{hashlib.md5(text[:200].encode()).hexdigest()[:8]}"

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    filepath = JOURNAL_DIR / f"{date}_dream.md"
    filepath.write_text(
        f"# {entry['title']}\n_Date: {date}_\n_Realm: dream_\n\n{text}",
        encoding="utf-8",
    )
    put_entry(entry_id, text, {
        "date": date, "time": "03:15", "realm": "dream",
        "title": entry["title"], "source": "seed_corpus",
    })
    return entry_id


def session_files() -> list[tuple[Path, Path]]:
    """The seed loop's own history, produced by build_sessions.py: three
    closed sessions with both-sided braids, the live seed those closes
    generated and Jordan reviewed, and the seeds it replaced.
    Copied as-is — none of it is regenerated at import.

    current.json ships too, and it is not empty. The demo opens three days
    *after* the 9/14 close: Jordan reviewed and uploaded that summary before
    writing the 9/15-9/17 entries, so they have the latest seed's context.
    No candidate awaits review. The open session holds those entries as
    messages with an empty base. Messages rather than base because
    only messages count as new material: a base-only session cannot be
    closed, and closing is what the demo invites.

    Shipping it also keeps load_current's first-run path out of the way,
    which would otherwise build a base from the newest imported
    conversation and make the demo's state whatever the first person to
    launch it happened to generate."""
    here = Path(__file__).parent
    pairs = [(here / "sessions" / "archive", SESSION_DIR / "archive"),
             (here / "summaries" / "seed_backups",
              SUMMARY_DIR / "seed_backups")]
    files = [(here / "summaries" / n, SUMMARY_DIR / n)
             for n in ("seed_summary.md",)]
    files.append((here / "sessions" / "current.json",
                  SESSION_DIR / "current.json"))

    for src_dir, dst_dir in pairs:
        for src in sorted(src_dir.glob("*")):
            files.append((src, dst_dir / src.name))
    return files


def derived_files() -> list[tuple[Path, Path]]:
    """Everything Claude worked out about the corpus: the entity graph,
    category assignments, summary layers, the pattern library, the dream index.
    Captured through September 14 by capture_demo_close.py under derived/, then copied
    in here the same way the sessions are.

    They ship because the demo has to install without an API key. The
    entries and the chroma index are free to build locally -- embeddings
    are local -- but every one of these files is Claude output, so a
    cloner who ran the capture themselves would need a key and would
    spend real money to reproduce what is already fixed content. Without
    them the demo boots with 29 indexed entries and an empty Entities tab, which
    reads as a broken app rather than a sparse one.

    chroma_data/ deliberately stays out, the same way export.py leaves the
    index out: it is derived from the entries, it is the one bulky part,
    and import rebuilds it on the spot for nothing."""
    here = Path(__file__).parent / "derived"
    dests = {"entity_graph": ENTITY_DIR, "categories": CATEGORY_DIR,
             "patterns": PATTERN_DIR, "dreams": DREAM_DIR,
             "summaries": SUMMARY_DIR}
    files = []
    for name, dst_root in dests.items():
        src_root = here / name
        for src in sorted(src_root.rglob("*")):
            if src.is_file():
                files.append((src, Path(dst_root) / src.relative_to(src_root)))
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
    # any first run invents, and replacing that is the point. The corpus's
    # own open session has turns too, so an untouched copy of it is ours.
    open_session = SESSION_DIR / "current.json"
    shipped = here / "sessions" / "current.json"
    if open_session.exists() and not (
            shipped.exists()
            and open_session.read_bytes() == shipped.read_bytes()):
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


def install_files(files: list[tuple[Path, Path]], dry_run: bool):
    print()
    for src, dst in files:
        if not src.exists():
            print(f"  MISSING {src.name} — run build_sessions.py "
                  f"(sessions) or capture_fixtures.py (derived) first")
            raise FileNotFoundError(src)
        if dry_run:
            print(f"  [dry] {src.name} -> {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  {src.name} -> {dst}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--demo", action="store_true",
                        help="install into seed_corpus/install/ -- the demo "
                             "journal the app restarts into. Handled at import "
                             "time; see the note at the top of this file.")
    parser.add_argument("--wipe", action="store_true",
                        help="clear local data before importing")
    args = parser.parse_args()

    entries_dir = Path(__file__).parent / "journal_entries"
    files = sorted(entries_dir.glob("*.md"))
    if not files:
        sys.exit(f"no .md files in {entries_dir}")

    # Both guards run before the first destructive or writing call. They
    # used to sit inside install_files(), i.e. after --wipe had already
    # deleted the entries they were meant to protect.
    to_install = session_files()
    refuse_if_real_journal(to_install)
    if args.wipe:
        refuse_if_unsandboxed()

    marker = CHROMA_DIR / ".install-complete"
    if not args.dry_run:
        marker.unlink(missing_ok=True)

    if args.wipe and not args.dry_run:
        wipe()

    parsed = [(f, parse_entry(f)) for f in files]
    open_files = [f for f, e in parsed if e["date"] >= OPEN_SESSION_FROM]
    entries = [e for _, e in parsed if e["date"] < OPEN_SESSION_FROM]
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

    # The open session's entries: backup only, dreams included. See
    # OPEN_SESSION_FROM -- embedding them would let search find unclosed
    # material and a visitor's close add them a second time.
    print(f"\n{len(open_files)} open-session entr"
          f"{'y' if len(open_files) == 1 else 'ies'} (backup only, not embedded)")
    install_files([(f, JOURNAL_DIR / f.name) for f in open_files],
                  args.dry_run)

    install_files(to_install, args.dry_run)
    install_files(derived_files(), args.dry_run)

    if not args.dry_run:
        # The demo ships every summary layer, not just the rolling seed.
        # Populate retrieval locally from these captured docs (no API calls).
        import passages
        import summarizer
        import dreams as dream_store
        passages.sync(collection)
        summarizer.sync_summary_embeddings(quiet=True)
        dream_store.build_index()
        marker.touch()

    print(f"\ndone. {'(dry run, nothing written)' if args.dry_run else ''}")
    if not args.dry_run and args.wipe:
        # Only the capture path needs this. A plain install is already
        # complete — derived/ supplied everything Claude would have.
        print("next: python capture_fixtures.py --stages tag,entities,summaries,dreams,patterns,organic --i-am-running-the-seed-corpus")


if __name__ == "__main__":
    main()
