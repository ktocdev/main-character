"""
Keep a retired built-in category working in an existing journal.

When a built-in category is renamed or turned off, the entries already
tagged with the old name don't lose the tag — `categories/raw/*.json`
still holds it and `build_index()` still copies it into each
conversation's record. What they lose is everything that keys off
`CATEGORIES` membership:

  1. counts        build_index() does `if name in counts` (categories.py),
                   so the retired name drops out of index.json's counts
                   and the Categories tab stops showing the bucket — even
                   though every tagged entry still carries it.
  2. search        update_chroma() sweeps any `cat_*` metadata key not in
                   `set(CATEGORIES) | custom`, so `cat_<old>` is cleared
                   off every chunk and category-filtered search loses them.
  3. triage        set_tag() validates against the same union and refuses
                   the name outright.

Registering the old name as a *custom* category puts it back in that
union, which restores all three at once. It adds no tags: with no
keywords and no members, organic.custom_tags() contributes nothing, and
the raw tag files are read first regardless.

The one thing registration can't restore is the domain document —
build_domains() iterates CATEGORIES only, so custom categories never get
one. `summaries/domains/<old>.md` would otherwise freeze at its last
state and keep being globbed into the pattern detector (patterns.py)
forever, next to the growing document for the new name. So it's deleted.

Usage:
    python scripts/retire_category.py dating
    python scripts/retire_category.py dating --dry-run
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

import categories as cats
import organic
from config import SUMMARY_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the retired category name, e.g. dating")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    name = args.name.strip()

    # this name is unlink()ed as summaries/domains/<name>.md below, so it
    # has to be a bare filename component — the same guard summarizer's
    # load_domain_doc() applies on the read side.
    if not name or name != Path(name).name or name in (".", ".."):
        sys.exit(f"'{name}' is not a plain category name.")

    if name in cats.CATEGORIES:
        sys.exit(f"'{name}' is still a built-in category — nothing to retire.")

    index = cats.load_index()
    affected = sorted(
        (rec["date"], rec["title"])
        for rec in index["conversations"].values()
        if name in rec["categories"]
    )
    print(f"{len(affected)} conversation(s) tagged '{name}'")
    for date, title in affected[:5]:
        print(f"    {date}  {title[:60]}")
    if len(affected) > 5:
        print(f"    ... and {len(affected) - 5} more")
    if not affected:
        print("  nothing to preserve — not registering an empty category.")
        return

    domain = SUMMARY_DIR / "domains" / f"{name}.md"
    already = name in organic.custom_names()

    print()
    print(f"  register '{name}' as a custom category   "
          f"{'(already registered)' if already else ''}")
    print(f"  delete {domain}   {'' if domain.exists() else '(absent)'}")
    print(f"  rebuild categories/index.json + chunk metadata")

    if args.dry_run:
        print("\n(dry run, nothing written)")
        return

    if not already:
        organic.add_custom(name)
    if domain.exists():
        domain.unlink()

    fresh = cats.build_index()
    cats.update_chroma(fresh)
    print(f"\ndone. '{name}': {fresh['counts'].get(name)} entries, "
          f"custom={name in fresh['custom']}")


if __name__ == "__main__":
    main()
