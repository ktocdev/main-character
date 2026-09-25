# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Print exactly what the companion would receive for a question or an entry.

    python scripts/show_context.py "when did I first go to Groundwork"
    python scripts/show_context.py --demo "what did Dev notice at mark six"
    python scripts/show_context.py --sandbox --file long_entry.txt
    python scripts/show_context.py --demo --reflection

It calls build_context_block, the same function every companion turn uses,
and prints the block: recent entries, related history, summaries, entity
docs, dream context, patterns and dream weather. Free: it never calls Claude.

This is how to judge the search. The demo's replies are canned, so they read
the same whatever the search finds; what the search found is right here.

  --demo / --sandbox  which journal (see scripts/_journal_dirs.py)
  --file PATH         read the question from a file, for a pasted long entry
  --reflection        the query the companion uses when it speaks first
  --dreams            include dream context, as a dream entry's reply does
"""

import _journal_dirs

WHICH = _journal_dirs.apply()

import argparse  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import companion  # noqa: E402
from rag_journal import get_collection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("text", nargs="?", help="the question or entry text")
    ap.add_argument("--file", type=Path)
    ap.add_argument("--reflection", action="store_true")
    ap.add_argument("--dreams", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--sandbox", action="store_true")
    args = ap.parse_args()

    collection = get_collection()
    if args.reflection:
        # Mirrors stream_reflection's seed.
        recent = companion.get_recent_chunks(collection, n=1)
        text = recent[0][0][:2000] if recent else "how life has been lately"
    elif args.file:
        text = args.file.read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        ap.error("give the text, --file or --reflection")

    entity_index = companion.load_entity_index()
    started = time.perf_counter()
    block = companion.build_context_block(text, collection, entity_index,
                                          include_dreams=args.dreams)
    ms = (time.perf_counter() - started) * 1000

    sys.stdout.reconfigure(encoding="utf-8")
    print(block)
    print()
    label = "real journal" if WHICH == "journal" else f"{WHICH} journal"
    print(f"--- {label} | query {len(text):,} chars | "
          f"context {len(block):,} chars | built in {ms:.0f} ms",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
