# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate third-party-names.txt from the entity graph.

The leak grep's name pass (scripts/check_leaks.sh) is only as good as
this list, and the obvious way to build it — dump every name in
entity_graph/index.json — produces a list that cannot be used. The graph
holds places and projects alongside people, and the extractor files
plenty of ordinary nouns as entities, so a raw dump carries `name`,
`type`, `place`, `target`, `merge` and `delete`. Grepping the codebase
for those matches thousands of Python and JS identifiers, and a check
that cries wolf is one people learn to skim.

Three rules, each one earned from a specific false positive:

1. **People only.** Places and projects are what contributed the app's
   own name ("RAG Journal"), products ("Figma"), and generic venues
   ("Thai restaurant").
2. **Capitalized only.** A real name used as an illustrative example is
   capitalized; the lowercase entries are the extractor's noise.
3. **Carry forward what we cannot reclassify.** Entries in the existing
   list that are no longer in the graph are kept unless the graph now
   says they are a place or project. Someone merged or deleted out of
   the graph is still a real person whose name may sit in old prose, and
   regenerating from scratch would silently drop them.

GENERIC is the one hand-maintained part. Kinship terms identify nobody,
and the fiction uses them constantly — `Mom` alone matched 96 times.
Names that merely collide with common words (Max, Pat, Cat, Major, Mark)
are NOT listed here: they are real people, and case-sensitive matching
already keeps them quiet, since the collisions are lowercase in code.

Usage:  python scripts/gen_name_list.py [--dry-run]

The output is gitignored — the list is itself sensitive.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "entity_graph" / "index.json"
OUT = ROOT / "third-party-names.txt"

# Kinship terms only. See the module docstring for why name/word
# collisions are deliberately absent.
GENERIC = {
    "Mom", "Mum", "Mother", "Dad", "Father", "Papa", "Pop",
    "Grandma", "Grandpa", "Granny", "Nana", "Grandmother", "Grandfather",
    "Aunt", "Auntie", "Uncle", "Sister", "Brother", "Cousin",
}

HEADER = """\
# Third-party names, for the leak grep's name pass.
#
# GENERATED — run `python scripts/gen_name_list.py` to rebuild. Editing
# by hand is fine but will be overwritten; add durable exclusions to
# GENERIC in that script instead.
#
# People only, capitalized only, kinship terms removed. See the script's
# docstring for why each of those rules exists.
#
# Gitignored: the list is itself sensitive.
"""


def main():
    dry_run = "--dry-run" in sys.argv

    if not GRAPH.exists():
        sys.exit(f"no entity graph at {GRAPH} — nothing to generate from")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))

    def names_of(kinds):
        out = set()
        for name, entity in graph.items():
            if entity.get("type") not in kinds:
                continue
            out.add(name)
            out.update(entity.get("aliases") or [])
        return {n.strip() for n in out if n and n.strip()}

    people = {n for n in names_of({"person"}) if n[:1].isupper()}
    not_people = names_of({"place", "project"})

    carried = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line[:1].isupper() and line not in not_people:
                carried.add(line)

    names = sorted((people | carried) - GENERIC)

    print(f"  {len(people)} people in the graph")
    print(f"  {len(carried - people)} carried forward (no longer in the graph)")
    print(f"  {len((people | carried) & GENERIC)} kinship terms dropped")
    print(f"  -> {len(names)} names")

    if dry_run:
        print("\n  (dry run, nothing written)")
        return

    OUT.write_text(HEADER + "\n".join(names) + "\n", encoding="utf-8")
    print(f"\n  wrote {OUT.name}")


if __name__ == "__main__":
    main()
