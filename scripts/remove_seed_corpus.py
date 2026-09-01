"""
Take the seed corpus back out of a real journal.

The corpus in `seed_corpus/journal_entries/` is written content, not lived
content -- and for a while the only way to have it was to write it *into* the
journal, where it was chunked, tagged, summarized and folded into entity
profiles like anything else. It reads as memory to retrieval and to the
companion, which is the problem: fiction the journal cannot tell from a life.

This removes those 30 entries and everything derived from them. It is not a
file delete -- an entry lives in six places by the time the pipeline is done
with it:

  - `journal_entries/<date>_<title>.md`
  - chunks in every Chroma collection, matched on (date, title) metadata
  - `entity_graph/raw/<date>_<Title-dashed>.json`   (the extraction)
  - observation blocks inside entity profiles, headed `### <date> - <title>`
  - `categories/raw/...` and the `conversations` map in `categories/index.json`
  - `summaries/entries/<date>_<Title-dashed>.json`

Entity profiles are edited rather than deleted, because a profile can hold
both real and corpus observations under the same name. A profile left with no
observations at all is removed, and `index.json` and `groups.json` follow.

Everything it touches is copied into `backups/seed_removal_<stamp>/` first.
Dry run is the default; `--apply` is the only thing that writes.

    python scripts/remove_seed_corpus.py            # report, change nothing
    python scripts/remove_seed_corpus.py --apply
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

SEED_DIR = ROOT / "seed_corpus" / "journal_entries"


def seed_entries() -> list[tuple[str, str]]:
    """(date, title) for every entry the corpus ships -- both spellings.

    A title reaches the stores twice, and not the same way each time. The
    file on disk carries a sanitised name (`2026-09-09_Lenas referral.md`),
    because an apostrophe is not something to put in a path; the metadata
    Chroma and the derived stores are keyed by is the `# heading` inside the
    file (`Lena's referral`). Reading only the filename finds the markdown
    and silently misses every chunk, observation block and summary behind it
    -- which is what happened the first time this ran, and left four entries
    visible in History with no file under them.

    So both spellings come back for every entry and callers match the set. A
    spelling that matches nothing costs one lookup that finds no rows.
    """
    out = []
    for path in sorted(SEED_DIR.glob("*.md")):
        stem = path.stem
        date, from_name = stem[:10], stem[11:]
        out.append((date, from_name))
        first = path.read_text(encoding="utf-8", errors="ignore").split(
            "\n", 1)[0]
        if first.startswith("#"):
            heading = first.lstrip("#").strip()
            if heading and heading != from_name:
                out.append((date, heading))
    return out


def key_of(date: str, title: str) -> str:
    """The `<date>_<Title-dashed>` key the derived stores are filed under.

    Deliberately a copy of `entities.conversation_cache_key()` rather than an
    import: this script has to run against a journal written by an older
    build, and a key rule that changes underneath it would quietly stop
    matching instead of failing. If that function ever changes, this one is
    meant to keep describing what is *on disk*.

    The parts that are easy to get wrong -- and did get this wrong once --
    are that every non-alphanumeric becomes a dash (so an apostrophe does
    too: `Lena's referral` files as `Lena-s-referral`), and that the result
    is cut at 40 characters.
    """
    safe = re.sub(r"[^a-zA-Z0-9-]+", "-", title)[:40].strip("-")
    return f"{date}_{safe}"


class Plan:
    """What would be removed, gathered before anything is touched so the dry
    run and the real run cannot describe different operations."""

    def __init__(self):
        self.files: list[Path] = []          # deleted outright
        self.chunks: dict[str, list] = {}    # collection name -> ids
        self.blocks: dict[Path, list] = {}   # profile -> headings removed
        self.profiles: list[Path] = []       # profiles emptied entirely
        self.cat_keys: list[str] = []        # categories/index.json entries

    def touched_files(self) -> list[Path]:
        return self.files + list(self.blocks) + [
            Path(config.CATEGORY_DIR) / "index.json",
            Path(config.ENTITY_DIR) / "index.json",
            Path(config.ENTITY_DIR) / "groups.json",
        ]


def build_plan(keys: list[tuple[str, str]]) -> Plan:
    plan = Plan()
    key_set = set(keys)
    dashed = {key_of(d, t) for d, t in keys}

    for date, title in keys:
        for path in [
            Path(config.JOURNAL_DIR) / f"{date}_{title}.md",
            Path(config.ENTITY_DIR) / "raw" / f"{key_of(date, title)}.json",
            Path(config.CATEGORY_DIR) / "raw" / f"{key_of(date, title)}.json",
            Path(config.SUMMARY_DIR) / "entries" / f"{key_of(date, title)}.json",
        ]:
            if path.exists():
                plan.files.append(path)

    # Chroma: matched on metadata, not on id, because ids are content hashes
    # and a re-import would have produced different ones.
    import chromadb
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    for meta in client.list_collections():
        col = client.get_collection(meta.name)
        data = col.get(include=["metadatas"])
        ids = [i for i, m in zip(data["ids"], data["metadatas"])
               if (m.get("date", ""), m.get("title", "")) in key_set]
        if ids:
            plan.chunks[meta.name] = ids

    # Entity profiles: one heading per observed entry.
    for profile in Path(config.ENTITY_DIR).rglob("*.md"):
        text = profile.read_text(encoding="utf-8", errors="ignore")
        hits = [h for h in observation_headings(text)
                if (h[0], h[1]) in key_set]
        if not hits:
            continue
        plan.blocks[profile] = hits
        if len(hits) == len(observation_headings(text)):
            plan.profiles.append(profile)

    cat_index = Path(config.CATEGORY_DIR) / "index.json"
    if cat_index.exists():
        data = json.loads(cat_index.read_text(encoding="utf-8"))
        plan.cat_keys = [k for k in data.get("conversations", {}) if k in dashed]

    return plan


HEADING = re.compile(r"^### (\d{4}-\d{2}-\d{2}) [—-] (.+?)\s*$", re.M)


def observation_headings(text: str) -> list[tuple[str, str]]:
    """(date, title) for every `### 2026-08-08 - Walk with Dev` in a profile.

    Both dash characters: profiles are model-written, and an em dash is what
    the prompt asks for but not reliably what comes back.
    """
    return [(m.group(1), m.group(2)) for m in HEADING.finditer(text)]


def strip_blocks(text: str, drop: set[tuple[str, str]]) -> str:
    """The profile without the named observation blocks. A block runs from its
    heading to the next one, so removing it takes its bullets with it."""
    out, keep = [], True
    for line in text.splitlines(keepends=True):
        m = HEADING.match(line.rstrip("\n"))
        if m:
            keep = (m.group(1), m.group(2)) not in drop
        if keep:
            out.append(line)
    return "".join(out)


def refresh_frontmatter(text: str) -> str:
    """mentions/first_seen/last_seen recomputed from what is left.

    `entities.py` writes `mentions: len(timeline)`, so a profile that lost
    observations and kept its count would claim memories it no longer holds --
    and it is the count the duplicate finder and the group builder rank by.
    """
    seen = sorted(d for d, _ in observation_headings(text))
    if not seen:
        return text

    def swap(field, value):
        nonlocal text
        text = re.sub(rf"^{field}: .*$", f"{field}: {value}", text,
                      count=1, flags=re.M)

    swap("mentions", len(observation_headings(text)))
    swap("first_seen", seen[0])
    swap("last_seen", seen[-1])
    return text


def back_up(plan: Plan, stamp: str) -> Path:
    """Copy everything about to change. Irreversible is the one property this
    operation must not have -- these are the only copies of what the pipeline
    inferred, and it cost real API calls to infer."""
    dest = ROOT / "backups" / f"seed_removal_{stamp}"
    for path in plan.touched_files():
        if not path.exists():
            continue
        rel = path.resolve().relative_to(ROOT.resolve())
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    if plan.chunks:
        import chromadb
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        dump = {}
        for name, ids in plan.chunks.items():
            col = client.get_collection(name)
            got = col.get(ids=ids, include=["metadatas", "documents"])
            dump[name] = {"ids": got["ids"], "metadatas": got["metadatas"],
                          "documents": got["documents"]}
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "chroma_chunks.json").write_text(
            json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def apply(plan: Plan) -> None:
    for path in plan.files:
        path.unlink()

    if plan.chunks:
        import chromadb
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
        for name, ids in plan.chunks.items():
            client.get_collection(name).delete(ids=ids)

    removed_names = set()
    for profile, hits in plan.blocks.items():
        if profile in plan.profiles:
            removed_names.add(profile.stem)
            profile.unlink()
            continue
        text = profile.read_text(encoding="utf-8")
        text = refresh_frontmatter(strip_blocks(text, set(hits)))
        profile.write_text(text, encoding="utf-8")

    # index.json is keyed by display name, and the files are slugs of it.
    entity_index = Path(config.ENTITY_DIR) / "index.json"
    if entity_index.exists():
        index = json.loads(entity_index.read_text(encoding="utf-8"))
        for name in list(index):
            path = Path(str(index[name].get("path", "")))
            if path.stem in removed_names:
                del index[name]
                continue
            full = Path(config.ENTITY_DIR) / path
            if full.exists():
                index[name]["mentions"] = len(
                    observation_headings(full.read_text(encoding="utf-8")))
        entity_index.write_text(json.dumps(index, indent=2, ensure_ascii=False),
                                encoding="utf-8")

    groups_path = Path(config.ENTITY_DIR) / "groups.json"
    if groups_path.exists() and removed_names:
        groups = json.loads(groups_path.read_text(encoding="utf-8"))
        gone = {n for n in removed_names}
        for group in groups.values() if isinstance(groups, dict) else []:
            members = group.get("members")
            if isinstance(members, list):
                group["members"] = [m for m in members
                                    if str(m).lower().replace(" ", "-") not in gone]
        groups_path.write_text(json.dumps(groups, indent=2, ensure_ascii=False),
                               encoding="utf-8")

    cat_index = Path(config.CATEGORY_DIR) / "index.json"
    if cat_index.exists() and plan.cat_keys:
        data = json.loads(cat_index.read_text(encoding="utf-8"))
        for key in plan.cat_keys:
            data["conversations"].pop(key, None)
        cat_index.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")


def report(plan: Plan) -> None:
    print(f"  markdown + derived files : {len(plan.files)}")
    for name, ids in plan.chunks.items():
        print(f"  chunks in {name:<20}: {len(ids)}")
    edited = len(plan.blocks) - len(plan.profiles)
    blocks = sum(len(v) for v in plan.blocks.values())
    print(f"  entity profiles edited   : {edited} ({blocks} observation blocks)")
    print(f"  entity profiles removed  : {len(plan.profiles)}")
    for p in plan.profiles:
        print(f"      {p.relative_to(Path(config.ENTITY_DIR)).as_posix()}")
    print(f"  categories/index entries : {len(plan.cat_keys)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually remove; without it this only reports")
    args = ap.parse_args()

    keys = seed_entries()
    if not keys:
        print(f"no seed entries found under {SEED_DIR}")
        return 1
    files = len(list(SEED_DIR.glob("*.md")))
    print(f"seed corpus: {files} entries, {len(keys)} title spellings\n")

    plan = build_plan(keys)
    report(plan)

    if not args.apply:
        print("\ndry run -- nothing was changed. Re-run with --apply.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = back_up(plan, stamp)
    print(f"\nbacked up to {dest.relative_to(ROOT).as_posix()}")
    apply(plan)
    print("removed. Restart the journal so it reloads the collection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
