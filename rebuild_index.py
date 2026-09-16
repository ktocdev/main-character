# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Put the search index back from the files it was built out of.

    python rebuild_index.py
    python rebuild_index.py --dry-run

`chroma_data/` is the one directory `export.py` deliberately leaves behind:
it is derived, it is binary, and it is large. This is what makes leaving it
behind safe -- everything in it can be recomputed from `journal_entries/`
and the derived stores, on the local MiniLM embeddings ChromaDB ships with.
**No API key, no network, no cost.** Run it after restoring an export, after
a corrupted index, or to find out whether the round trip really works.

Three collections come back, from three file-backed sources:

  - **`journal_entries`** -- the waking chunks, re-split and re-tagged from
    the markdown by the same `bulk_import` functions that wrote them.
  - **`journal_dreams`** -- dream *entries* from their markdown, plus every
    dream the pipeline extracted, via `dreams.build_index()`.
  - **`journal_summaries`** -- entry summaries, weekly arcs, domain docs and
    entity docs, via `summarizer.sync_summary_embeddings()`.

**It reconciles rather than wipes.** Everything the files describe is upserted
and anything else in the collection is deleted, which lands in the same place
as a wipe-and-refill while staying safe to run against a live index -- and
against the running server, which holds the same store open.

Two things it cannot perfectly restore, both harmless to search and worth
knowing before you compare two indexes id by id:

  - **Leading whitespace on a chunk is lost, and the id is a hash of the
    text.** `bulk_import` writes `f"# {title}\\n_Date: {date}_\\n\\n{text}"`,
    so a chunk that began with a blank line has that line absorbed into the
    separator and cannot be read back. Those chunks come back with the same
    words under a different id. In this journal that is 2 of 329, and one
    rebuild makes the files and the index agree for good.
  - **`source` is preserved where a document already exists, and is
    `rebuild_index` where one does not.** It is not decoration:
    `sessions._initial_base()` reads it to decide which conversation an open
    chat continues. Rebuilding onto an empty index therefore loses the
    distinction between imported and written-here entries, and the run says
    so rather than leaving you to notice later.
"""

import argparse
import hashlib
from pathlib import Path

import config
import export


def _existing(collection) -> dict:
    """id -> metadata for everything already in a collection, so a rebuild
    can keep what it has no way to recompute."""
    got = collection.get(include=["metadatas"])
    return dict(zip(got["ids"], got["metadatas"]))


def _reconcile(collection, ids, docs, metas, dry_run: bool) -> dict:
    """Upsert what the files describe, delete what nothing explains."""
    stale = sorted(set(_existing(collection)) - set(ids))
    if not dry_run:
        if stale:
            collection.delete(ids=stale)
        if ids:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)
    return {"documents": len(ids), "removed": len(stale)}


# ---------------------------------------------------------------------------
# THE WAKING COLLECTION
# ---------------------------------------------------------------------------


def _open_session_text() -> str:
    """Everything the still-open chat holds.

    An entry written into a session that has not been closed is deliberately
    absent from the index -- the companion reads it from `current.json`, and
    closing the chat is what files it. A rebuild has to respect that, or the
    open session's writing shows up in search under a placeholder title and
    then again, properly titled, the moment the chat closes.
    """
    import json

    path = Path(config.SESSION_DIR) / "current.json"
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return "\n\n".join(m.get("text", "") for m in cur.get("messages", []))


def _write_mode(entry: dict) -> tuple[str, dict]:
    """The id and metadata `companion.store_entry` would have written.

    Its scheme is its own: the hash covers the text *without* the title, and
    the stored title is `Journal entry <date> <time>` -- no em dash, unlike
    the heading in the file it writes alongside. Reproducing both exactly is
    what lets a rebuilt index be compared to the original id by id instead of
    read by eye.
    """
    from rag_journal import extract_metadata

    text = entry["text"]
    digest = hashlib.md5(text[:200].encode()).hexdigest()[:8]
    # `2026-07-06_2108_entry.md` -> "21:08"
    stamp = entry["file"].split("_")[1]
    clock = f"{stamp[:2]}:{stamp[2:]}" if len(stamp) == 4 else ""
    meta = extract_metadata(text)
    return f"{entry['date']}_{digest}_c0", {
        "date": entry["date"],
        "time": clock,
        "title": f"Journal entry {entry['date']} {clock}".strip(),
        "people": ", ".join(meta.get("people", [])),
        "topics": ", ".join(meta.get("topics", [])),
        "mood": meta.get("mood", "unknown"),
        "key_events": " | ".join(meta.get("key_events", [])),
        "is_summary": "False",
        "source": "write_mode",
    }


def build_entries(dry_run: bool) -> dict:
    """Re-chunk and re-tag every waking entry.

    Uses `bulk_import`'s own `chunk_entry`/`entry_chunk_id` and
    `rag_journal.extract_metadata` rather than reimplementing them -- a
    rebuild that chunked differently from the writer would produce an index
    that answers differently, which is the one thing this must not do.
    """
    from bulk_import import chunk_entry, entry_chunk_id
    from rag_journal import extract_metadata, get_collection

    collection = get_collection()
    known = _existing(collection)
    ids, docs, metas, fresh, skipped = [], [], [], 0, 0

    all_entries = export.read_entries()
    # What each day's finished entries say, so a pre-close backup can be
    # checked against them rather than assumed redundant.
    closed: dict[str, str] = {}
    for e in all_entries:
        if e["kind"] == "entry" and e["realm"] == "waking":
            closed[e["date"]] = closed.get(e["date"], "") + "\n\n" + e["text"]
    open_chat = _open_session_text()

    for entry in all_entries:
        if entry["realm"] != "waking":
            continue                        # dreams live in their own realm
        if entry["kind"] == "draft backup":
            # `<date>_<time>_entry.md` is the copy written when an entry is
            # submitted; closing the chat writes the same words again under
            # the session's title, and *that* is the file the index is built
            # from. Indexing both would put every day's writing in twice --
            # once under a real title and once under "Journal entry — <time>".
            #
            # But only once the chat has actually closed. A backup whose words
            # are in no finished entry is either the open session or one of
            # the old CLI write-mode entries, and both are in the index today
            # -- dropping them would quietly delete writing from search, which
            # is the one outcome a rebuild must never produce.
            # Closed entries and the open session both join their messages
            # with "\n\n" (see close_session / _open_session_text), so a
            # covered draft's text is one whole joined segment -- not just
            # any substring, which a short or common draft could also match
            # inside unrelated text and get wrongly skipped.
            covered = entry["text"] and (
                entry["text"] in closed.get(entry["date"], "").split("\n\n")
                or entry["text"] in open_chat.split("\n\n"))
            if covered:
                skipped += 1
                continue
            cid, meta = _write_mode(entry)
            if cid not in known:
                fresh += 1
            ids.append(cid)
            docs.append(entry["text"])
            metas.append(meta)
            continue
        for chunk in chunk_entry({"text": entry["text"], "date": entry["date"],
                                  "title": entry["title"]}):
            text = chunk["text"]
            # `entry["part"]` is the chunk index the filename remembers -- a
            # `_part2.md` file *is* chunk 1 of its entry, so re-splitting it
            # numbers from there rather than from zero. Without this the same
            # text comes back under a different id and the round trip can
            # only be checked by reading, not by comparing.
            idx = entry["part"] + chunk.get("chunk_index", 0)
            cid = entry_chunk_id(entry["date"], entry["title"], text, idx)
            meta = extract_metadata(text)
            was = known.get(cid, {})
            if not was:
                fresh += 1
            ids.append(cid)
            docs.append(text)
            metas.append({
                "date": entry["date"],
                "title": entry["title"],
                "people": ", ".join(meta.get("people", [])),
                "topics": ", ".join(meta.get("topics", [])),
                "mood": meta.get("mood", "unknown"),
                "key_events": " | ".join(meta.get("key_events", [])),
                "is_summary": was.get("is_summary", "False"),
                # see the module docstring: provenance cannot be recomputed
                "source": was.get("source", "rebuild_index"),
            })

    out = _reconcile(collection, ids, docs, metas, dry_run)
    out["unprovenanced"] = fresh
    out["skipped"] = skipped
    return out


# ---------------------------------------------------------------------------
# DREAMS
# ---------------------------------------------------------------------------


def build_dreams(dry_run: bool) -> dict:
    """Dream entries from markdown, then the extracted dreams from raw.

    Two populations in one collection, and only the first is rebuilt here:
    `dreams.build_index()` owns the `dream:` ids and does its own stale
    sweep, so this reconciles only what it wrote.
    """
    from dreams import get_dream_collection

    collection = get_dream_collection()
    known = _existing(collection)
    ids, docs, metas = [], [], []

    for entry in export.read_entries():
        if entry["realm"] != "dream":
            continue
        text = entry["text"]
        # the id `dreams.store_dream_entry` would have written
        digest = hashlib.md5(text[:200].encode()).hexdigest()[:8]
        cid = f"dreamentry_{entry['date']}_{digest}"
        was = known.get(cid, {})
        ids.append(cid)
        docs.append(text)
        metas.append({
            "date": entry["date"],
            "time": was.get("time", ""),
            "realm": "dream",
            "title": entry["title"],
            "source": was.get("source", "write_mode"),
        })

    # Only entry documents are this function's to reconcile. Anything with a
    # `dream:` id belongs to build_index() below and must survive.
    keep = {i for i in known if i.startswith("dream:")}
    stale = sorted(set(known) - set(ids) - keep)
    if not dry_run:
        if stale:
            collection.delete(ids=stale)
        if ids:
            collection.upsert(ids=ids, documents=docs, metadatas=metas)

    extracted = 0
    if not dry_run:
        import dreams
        extracted = len(dreams.build_index()["dreams"])
    else:
        raw = Path(config.DREAM_DIR) / "raw"
        extracted = len(list(raw.glob("*.json"))) if raw.exists() else 0

    return {"documents": len(ids), "removed": len(stale),
            "extracted": extracted}


# ---------------------------------------------------------------------------
# SUMMARIES
# ---------------------------------------------------------------------------


def build_summaries(dry_run: bool) -> dict:
    """Hand off to the summarizer, which already rebuilds this collection
    from files as the last step of every processing run."""
    if dry_run:
        entries = Path(config.SUMMARY_DIR) / "entries"
        return {"documents": len(list(entries.glob("*.json")))
                if entries.exists() else 0, "removed": 0}
    import summarizer
    return {"documents": summarizer.sync_summary_embeddings(quiet=True),
            "removed": 0}


def rebuild(dry_run: bool = False) -> dict:
    return {
        "journal_entries": build_entries(dry_run),
        "journal_dreams": build_dreams(dry_run),
        "journal_summaries": build_summaries(dry_run),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild the search index from the journal files.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, change nothing")
    args = ap.parse_args()

    journal = Path(config.JOURNAL_DIR)
    if not journal.exists() or not any(journal.glob("*.md")):
        print(f"no entries found under {journal}")
        return 1

    print(f"rebuilding from {journal}")
    print("  (local embeddings, no API key needed, nothing to spend)\n")
    result = rebuild(dry_run=args.dry_run)
    for name, r in result.items():
        line = f"  {name:<18}: {r['documents']} documents"
        if r["removed"]:
            line += f", {r['removed']} removed"
        if r.get("skipped"):
            line += f", {r['skipped']} pre-close backups skipped"
        if r.get("extracted"):
            line += f", {r['extracted']} extracted dreams"
        print(line)

    orphans = result["journal_entries"]["unprovenanced"]
    if orphans:
        print(f"\n  {orphans} chunks had no existing document to inherit "
              "`source` from, and are marked `rebuild_index`. Search is "
              "unaffected; which conversation a new chat continues from may "
              "not be (see the module docstring).")

    print("\ndry run -- nothing was written." if args.dry_run
          else "\ndone. Restart the journal so it reopens the store.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
