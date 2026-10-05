# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate private-terms.txt, the leak grep's list of journal words.

third-party-names.txt covers people. A test set that quotes the journal
also names film titles, substances, nicknames and misspellings, and those
are the words most likely to be copied into a comment, a test or a
handoff as an example -- which is how they reached the repo before
(LOOKUP-UPGRADE-HANDOFF.md, step 3). This lists them for the terms pass of
scripts/check_leaks.sh.

A word is listed when a test set's question, fact or link uses it and it
is in at most MAX_ENTRIES journal entries (or none: a question can spell a
word the journal misspells). The cut is wide on purpose. How often the
journal uses a word doesn't tell a detail from an ordinary word -- a
substance can be in ten entries and "admit" in four -- so this lists both,
and the owner decides: the first check run shows which listed words the
repo already uses, and an ordinary one is commented out by hand (`# word`).
Commented words stay commented, and every entry is kept across rebuilds,
so words added by hand survive too. Words that are never in the repo cost
nothing.

Nothing is skipped for already being in a tracked file: a word that is
there may be exactly the leak.

Only counts are printed, never the words: the output may be read by
someone, or something, that shouldn't see the holdout.

Usage:  python scripts/gen_private_terms.py [--sandbox] [--dry-run] [SET.json ...]
        (default sets: docs/retrieval-eval/real.json and holdout.json)

The output is gitignored -- the list is itself sensitive.
"""

import _journal_dirs

_journal_dirs.apply()

import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import unicodedata  # noqa: E402
from pathlib import Path  # noqa: E402

from config import JOURNAL_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "private-terms.txt"
SETS = [ROOT / "docs" / "retrieval-eval" / n for n in ("real.json", "holdout.json")]
MAX_ENTRIES = 12
MIN_LENGTH = 4
FIELDS = ("question", "fact", "link")

HEADER = """\
# Journal words, for the leak grep's terms pass (scripts/check_leaks.sh).
#
# GENERATED -- run `python scripts/gen_private_terms.py` to rebuild.
# Entries are kept across rebuilds, so adding one by hand is fine.
# Matched case-insensitively as whole words. An ordinary word the repo
# uses on its own account: comment it out as `# word`, and it stays out.
#
# Gitignored: the list is itself sensitive.
"""

_WORD = re.compile(r"[a-z][a-z0-9'-]*[a-z0-9]")
_COMMENTED = re.compile(r"^#\s*([^\s#]+)$")


def fold(text: str) -> str:
    """Lowercase, accents off (fiancé -> fiance)."""
    return "".join(unicodedata.normalize("NFKD", c)[:1] or c for c in text.lower())


def words(text: str) -> set[str]:
    """Its words, lowercased, possessives off. No stopword list: a common
    word is never rare in the journal, so the entry count drops it."""
    out = set()
    for w in _WORD.findall(fold(text.replace("’", "'"))):
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) >= MIN_LENGTH:
            out.add(w)
    return out


def main():
    dry_run = "--dry-run" in sys.argv
    paths = [Path(a) for a in sys.argv[1:] if not a.startswith("--")] or \
        [p for p in SETS if p.exists()]
    if not paths:
        sys.exit("no test set found -- pass one, e.g. docs/retrieval-eval/real.json")

    candidates = set()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for t in data.get("questions", []) + data.get("entry_replies", []):
            for field in FIELDS:
                candidates |= words(t.get(field, ""))

    entries = [words(f.read_text(encoding="utf-8")) for f in JOURNAL_DIR.glob("*.md")]
    if not entries:
        sys.exit(f"no journal entries in {JOURNAL_DIR}")
    rare = {w for w in candidates if sum(w in e for e in entries) <= MAX_ENTRIES}

    kept, commented = set(), set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                m = _COMMENTED.match(line)
                if m:
                    commented.add(m.group(1).lower())
            else:
                kept.add(line.lower())
    active = (kept | rare) - commented

    print(f"  {len(paths)} test set(s), {len(entries)} journal entries")
    print(f"  {len(candidates)} candidate words, {len(rare)} in at most "
          f"{MAX_ENTRIES} entries")
    print(f"  {len(kept)} kept and {len(commented)} commented out from the existing list")
    print(f"  -> {len(active)} terms checked")

    if dry_run:
        print("\n  (dry run, nothing written)")
        return
    lines = sorted(active) + [f"# {w}" for w in sorted(commented)]
    OUT.write_text(HEADER + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  wrote {OUT.name}")


if __name__ == "__main__":
    main()
