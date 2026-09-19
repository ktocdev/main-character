# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Everything this journal knows, as files you can read without it.

    python export.py                   # -> exports/journal_export_<stamp>/
    python export.py --out somewhere
    python export.py --dry-run         # say what would be written

This is the first of three commands that share this module: `backup.py` zips
what this writes, and `rebuild_index.py` puts the search index back from it.
Together they are the answer to "what happens to my writing if I stop using
this app" -- and that answer only counts if it is one command away, which is
why this is a script and a Settings button rather than a documented procedure.

**What comes out is the whole of it, minus what can be recomputed.**
`journal_entries/` is the source of truth; `entity_graph/`, `summaries/`,
`categories/`, `patterns/`, `dreams/` and `sessions/` are what the pipeline
inferred, and they are here because inferring them again costs real API
calls. `entries.json` is the same entries as one file, for anything that
would rather not walk a directory of markdown.

**Two things are left out on purpose, and both would be bugs if they weren't:**

  - **`chroma_data/`** -- derived, large, and a binary format that says
    nothing to a human reader. `rebuild_index.py` regenerates it from the
    markdown for free, on local embeddings. Copying it would roughly double
    the size of the export to preserve something no one can read.
  - **`.env` and `spend_ledger.json`** -- an API key and a spend history are
    not journal content, and an export is the single most likely thing to get
    copied somewhere shared. `.env.example` in the repo is the settings shape
    without the key, and it is already public.
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import config

# The derived stores, in the order a reader would want them. Each is a whole
# directory copied as-is: they are already JSON and markdown, and rewriting
# them into some export-specific shape would only add a format to explain.
DERIVED = [
    ("entity_graph", config.ENTITY_DIR),
    ("summaries", config.SUMMARY_DIR),
    ("categories", config.CATEGORY_DIR),
    ("patterns", config.PATTERN_DIR),
    ("dreams", config.DREAM_DIR),
    ("sessions", config.SESSION_DIR),
]

# `# Title` then `_Date: 2026-09-01_`, optionally `_Realm: dream_`. Written by
# `bulk_import.import_entry`, `sessions.close_session` and
# `dreams.store_entry`; read here and by `rebuild_index.py`.
TITLE = re.compile(r"^#\s*(.+?)\s*$", re.M)
DATE = re.compile(r"^_Date:\s*(\d{4}-\d{2}-\d{2})\s*_\s*$", re.M)
REALM = re.compile(r"^_Realm:\s*(\w+)\s*_\s*$", re.M)

# `2026-08-20_1013_<entry id>_entry.md` (`2026-08-20_1013_entry.md` before
# saves carried ids, and for CLI write-mode entries) -- the immediate copy
# `sessions.backup_entry_text` writes the moment an entry is submitted,
# before the chat is closed. It is a
# safety copy, not an entry: closing the session writes the day's writing again
# as `<date>_<Session title>.md` and *that* is what gets indexed. Both files
# live here, and telling them apart matters to anyone reading this directory --
# and matters more to `rebuild_index.py`, which would otherwise index the same
# writing twice.
DRAFT = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(?:_[A-Za-z0-9-]+)?_entry\.md$")

# `..._part2.md` -- `bulk_import.import_entry` writes one file per chunk and
# numbers them from 1, so `_part2` was chunk index 1. The split is baked into
# the filename and nowhere else, which makes this the only way to put a
# rebuilt chunk back under the id it originally had.
PART = re.compile(r"_part(\d+)$")


def read_entry(path: Path) -> dict:
    """One markdown file as a record.

    **The title comes from the heading, not the filename.** The filename is
    sanitised -- `Lena's referral` is filed as `Lenas referral` -- while the
    heading is what every derived store and every Chroma document is keyed
    by. Reading the filename here would produce an export whose titles do not
    match its own entity graph, and `scripts/remove_seed_corpus.py` has
    already paid for that lesson once.

    Falls back to the filename only when a file has no heading at all, which
    means something outside this app wrote it.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    heading = TITLE.search(text)
    stamped = DATE.search(text)
    realm = REALM.search(text)
    body = text.split("\n\n", 1)[1].strip() if "\n\n" in text else text
    part = PART.search(path.stem)
    return {
        "file": path.name,
        "date": stamped.group(1) if stamped else path.stem[:10],
        "title": heading.group(1) if heading else path.stem[11:],
        "realm": realm.group(1) if realm else "waking",
        "kind": "draft backup" if DRAFT.match(path.name) else "entry",
        "part": int(part.group(1)) - 1 if part else 0,
        "text": body,
    }


def read_entries() -> list[dict]:
    """Every entry, oldest first. Sorted by filename rather than by parsed
    date so that a day written in several parts keeps the order it was
    written in -- `_part2` sorts after `_part1`, which no date can express."""
    journal = Path(config.JOURNAL_DIR)
    if not journal.exists():
        return []
    return [read_entry(p) for p in sorted(journal.glob("*.md"))]


def manifest(entries: list[dict], copied: dict) -> dict:
    realms: dict[str, int] = {}
    for e in entries:
        realms[e["realm"]] = realms.get(e["realm"], 0) + 1
    dates = sorted(e["date"] for e in entries)
    drafts = sum(1 for e in entries if e["kind"] == "draft backup")
    return {
        "exported": datetime.now().isoformat(timespec="seconds"),
        "app": "Main Character",
        "entries": len(entries),
        "draft_backups": drafts,
        "by_realm": realms,
        "first_entry": dates[0] if dates else None,
        "last_entry": dates[-1] if dates else None,
        "derived_files": copied,
        # Said in the data, not only in the README, because a manifest is what
        # a script reads and a README is what a person reads.
        "excluded": {
            "chroma_data": "derived — rebuild with `python rebuild_index.py`",
            ".env": "holds your API key",
            "spend_ledger.json": "operational, not journal content",
        },
    }


README = """# Journal export

Everything in this folder is plain text. Nothing here needs the app to read.

- `entries/` — one markdown file per entry. This is the journal itself.
- `entries.json` — the same entries as a single file, with the date, title
  and realm parsed out.

Some files are marked `"kind": "draft backup"` in `entries.json`
(`<date>_<time>_<id>_entry.md`, or `<date>_<time>_entry.md` from older
versions). Those are the immediate copy the app writes when
you submit an entry, before the chat is closed. Once a chat closes, the same
writing is saved again under the session's title — so for most days you will
find the words twice, once as the backup and once as the finished entry. They
are kept because a backup you throw away is not a backup.
- `entity_graph/`, `summaries/`, `categories/`, `patterns/`, `dreams/`,
  `sessions/` — what the app inferred from the entries. Regenerating these
  costs API calls, which is why they are copied rather than left behind.
- `manifest.json` — counts, date range, and what was deliberately left out.

## Putting it back

Copy `entries/` into `journal_entries/` and each derived folder to its
matching directory, then:

    python rebuild_index.py

That rebuilds the search index from the markdown, on local embeddings — no
API key, no cost. The index is not in this export because it is derived from
what is.

## What is not here

No `.env` and no `spend_ledger.json`: an API key and a spend history are not
journal content, and this folder is the most likely thing to get copied
somewhere shared.
"""


def copy_tree(src: Path, dest: Path) -> int:
    if not src.exists():
        return 0
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return sum(1 for p in dest.rglob("*") if p.is_file())


def export_journal(dest: Path, dry_run: bool = False) -> dict:
    """Write the export and return its manifest. `backup.py` calls this into
    a temporary directory; the CLI below calls it into a real one."""
    entries = read_entries()
    if dry_run:
        return manifest(entries, {
            name: sum(1 for p in src.rglob("*") if p.is_file())
            for name, src in DERIVED if src.exists()
        })

    dest.mkdir(parents=True, exist_ok=True)
    entry_dir = dest / "entries"
    entry_dir.mkdir(exist_ok=True)
    journal = Path(config.JOURNAL_DIR)
    for e in entries:
        shutil.copy2(journal / e["file"], entry_dir / e["file"])

    (dest / "entries.json").write_text(
        json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    copied = {}
    for name, src in DERIVED:
        n = copy_tree(Path(src), dest / name)
        if n:
            copied[name] = n

    info = manifest(entries, copied)
    (dest / "manifest.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    (dest / "README.md").write_text(README, encoding="utf-8")
    return info


ROOT = Path(__file__).resolve().parent


def default_dest() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "exports" / f"journal_export_{stamp}"


def report(info: dict, dest: Path, dry_run: bool) -> None:
    print(f"  entries        : {info['entries']}", end="")
    if info["first_entry"]:
        print(f"  ({info['first_entry']} to {info['last_entry']})")
    else:
        print()
    if info["draft_backups"]:
        print(f"    of those     : {info['draft_backups']} pre-close backups")
    for realm, n in sorted(info["by_realm"].items()):
        if realm != "waking":
            print(f"    {realm:<13}: {n}")
    for name, n in info["derived_files"].items():
        print(f"  {name:<15}: {n} files")
    if dry_run:
        print(f"\ndry run -- nothing was written. It would go to {dest}.")
    else:
        print(f"\nwritten to {dest}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the journal as files.")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write it (default: exports/journal_export_<stamp>)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, write nothing")
    args = ap.parse_args()

    dest = args.out or default_dest()
    if dest.exists() and any(dest.iterdir()) and not args.dry_run:
        # An export folder is written into, not over: `--out` pointed at a
        # live directory is a plausible typo, and copytree would merge into it
        # silently.
        print(f"{dest} already has something in it. Pick an empty directory.")
        return 1

    info = export_journal(dest, dry_run=args.dry_run)
    if not info["entries"]:
        print(f"no entries found under {config.JOURNAL_DIR}")
        return 1
    report(info, dest, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
