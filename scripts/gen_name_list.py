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

1. **People (and animals) only.** Places and projects are what
   contributed the app's own name ("RAG Journal"), products ("Figma"), and
   generic venues ("Thai restaurant"). A pet's name is as identifying as
   a person's, so animals are listed too.
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

Names the demo's fiction also uses as an entity are dropped and listed,
not checked: every hit on them is the fiction, so the check would only
cry wolf, and an exemption list in this file would publish the very
names it exists to protect. If a listed name is a real person who
matters, rename the fiction's entity instead.

Usage:  python scripts/gen_name_list.py [--dry-run]

The output is gitignored — the list is itself sensitive.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "entity_graph" / "index.json"
OUT = ROOT / "third-party-names.txt"
# the demo's entities: the fiction everyone's repo ships
FICTION = ROOT / "seed_corpus" / "derived" / "entity_graph" / "index.json"

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
# People and animals only, capitalized only, kinship terms and names the demo's
# fiction also uses removed. See the script's
# docstring for why each of those rules exists.
#
# Gitignored: the list is itself sensitive.
"""


def main():
    dry_run = "--dry-run" in sys.argv

    if not GRAPH.exists():
        sys.exit(f"no entity graph at {GRAPH} — nothing to generate from")
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    fiction = json.loads(FICTION.read_text(encoding="utf-8")) if FICTION.exists() else {}

    def names_of(kinds, graph=graph):
        out = set()
        for name, entity in graph.items():
            if entity.get("type") not in kinds:
                continue
            out.add(name)
            out.update(entity.get("aliases") or [])
        return {n.strip() for n in out if n and n.strip()}

    # a pet's name is as identifying as a person's
    people = {n for n in names_of({"person", "animal"}) if n[:1].isupper()}
    # lowercase: a name filed as a person once and later retyped is often
    # stored in different case ("Guitar" carried, `guitar` the project)
    not_people = {n.lower() for n in names_of({"place", "project", "thing"})}

    carried = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line[:1].isupper() and line.lower() not in not_people:
                carried.add(line)

    in_fiction = {n.lower() for n in names_of({"person", "place", "project", "thing", "animal"}, fiction)}
    collisions = sorted(n for n in (people | carried) - GENERIC if n.lower() in in_fiction)
    names = sorted((people | carried) - GENERIC - set(collisions))

    print(f"  {len(people)} people and animals in the graph")
    print(f"  {len(carried - people)} carried forward (no longer in the graph)")
    print(f"  {len((people | carried) & GENERIC)} kinship terms dropped")
    if collisions:
        print(f"  {len(collisions)} also in the demo's fiction, so not checked: {', '.join(collisions)}")
    print(f"  -> {len(names)} names")

    if dry_run:
        print("\n  (dry run, nothing written)")
        return

    OUT.write_text(HEADER + "\n".join(names) + "\n", encoding="utf-8")
    print(f"\n  wrote {OUT.name}")


if __name__ == "__main__":
    main()
