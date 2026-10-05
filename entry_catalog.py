# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Entry catalog — the header's "entries" count, counted as entries.

The count used to be the search index's record count, which is chunks: a
long day was three "entries", a week of chat closed into one day's record
was one, and nothing moved until close. It now means what it says:

  * **Save entry** adds one the moment it is saved. **Send** never does.
  * Closing a chat moves its saved entries from open to indexed -- the
    total does not change.
  * History from before this schema (imports, old closes, CLI entries)
    counts once per logical dated entry, however many chunks it has.
  * Dreams are their own realm and never enter the waking total.

**Where the numbers come from.** Nothing here is a second source of truth.
Every figure is recomputed from files that already exist and already travel
with export, backup and rebuild:

  open saved entries     `kind: "entry"` messages in sessions/current.json
  indexed saved entries  the same messages inside a schema-2 archive
  legacy/imported        distinct (date, title) pairs in the waking index,
                         minus the day parts schema-2 closes wrote

`sessions/entry_catalog.json` is this computation written down: a readable
list of every logical entry and its state, and what `/api/status` answers
from. It is keyed by a fingerprint of its inputs and rebuilt whenever they
change -- and always at startup -- so a crash, a restore or a stale copy
can only ever cost a recount, never a wrong total that persists.

**The legacy approximation.** Old sessions never recorded Save versus Send,
and nothing in the text can tell them apart. So exact save-only counting
starts with schema 2. Before it, each logical dated entry in the index
counts once: an imported conversation-day, an old closed chat's day, a CLI
write-mode entry. (Distinct (date, title) is the identity -- two different
imported conversations sharing a title and a day would count once; the
chunk ids carry no stable source id to do better.) An old open chat's
messages count only where an immediate backup proves they were saves (see
sessions._migrate_provenance); the rest are "unclassified". When such a
chat closes, each day holding unclassified writing counts once more, as its
old close would have -- the one place a close still adds to the total.
This is a historical approximation, not a claim about old clicks.
"""

import json

import sessions

VERSION = 1

_CACHE = {"history_key": None, "history": None,
          "open_key": None, "catalog": None}


def _stat_key(path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _history_key(collection) -> tuple:
    return (VERSION, str(sessions.ARCHIVE_DIR), _stat_key(sessions.ARCHIVE_DIR),
            sessions.WRITES["archive"], id(collection), collection.count())


def _open_key() -> tuple:
    return (str(sessions.CURRENT_FILE), _stat_key(sessions.CURRENT_FILE),
            sessions.WRITES["current"])


def _record(m: dict, state: str, session: str) -> dict:
    return {"id": m["entry_id"], "origin": "save",
            "realm": "dream" if m.get("dream") else "waking",
            "date": (m.get("ts") or "")[:10], "session": session, "state": state}


def _history(collection) -> dict:
    """Everything already in journal memory: archived saves and the logical
    entries of the index that no schema-2 close accounts for."""
    covered = set()          # day parts whose count is their saved entry ids
    archived: dict[str, dict] = {}
    for archive in sessions.load_archives():
        if (archive.get("entry_schema") or 0) < sessions.ENTRY_SCHEMA:
            continue         # an old close's days are counted from the index
        for part in archive.get("parts", []):
            # base parts are context carried forward, already counted where
            # they were first written; only this close's day parts carry ids
            if "entry_ids" in part and not part.get("legacy"):
                covered.add((part["date"], part["title"]))
        for m in archive.get("messages", []):
            if m.get("role") == "you" and m.get("kind") == "entry" \
                    and m.get("entry_id"):
                archived.setdefault(m["entry_id"],
                                    _record(m, "indexed", archive.get("id", "")))

    units: dict[tuple, str] = {}
    for meta in collection.get(include=["metadatas"])["metadatas"]:
        key = (meta.get("date", ""), meta.get("title", ""))
        if key not in covered:
            units.setdefault(key, meta.get("source", ""))
    legacy = [{"id": f"{'import' if src == 'bulk_import' else 'legacy'}:{d}:{t}",
               "origin": "import" if src == "bulk_import" else "legacy",
               "realm": "waking", "date": d, "session": None, "state": "indexed"}
              for (d, t), src in sorted(units.items())]
    return {"archived": archived, "legacy": legacy}


def _build(collection, history: dict) -> dict:
    cur = sessions.load_current(collection)
    records = list(history["legacy"]) + list(history["archived"].values())
    unclassified = 0
    for m in cur.get("messages", []):
        if m.get("role") != "you":
            continue
        if m.get("kind") == "entry" and m.get("entry_id"):
            # a close interrupted after its archive was written leaves the
            # same entry in both files; the archived copy is the true state
            if m["entry_id"] not in history["archived"]:
                records.append(_record(m, "open", "current"))
        elif not m.get("kind") and not m.get("dream"):
            unclassified += 1
    waking = [r for r in records if r["realm"] == "waking"]
    open_n = sum(1 for r in waking if r["state"] == "open")
    return {
        "version": VERSION,
        "totals": {"entries": len(waking), "open_entries": open_n,
                   "indexed_entries": len(waking) - open_n,
                   "unclassified_open": unclassified},
        "entries": records,
    }


def _catalog(collection, force: bool = False) -> dict:
    hkey, okey = _history_key(collection), _open_key()
    if not force and hkey == _CACHE["history_key"] and okey == _CACHE["open_key"]:
        return _CACHE["catalog"]
    # Serialized with every session write: a recount must not read the open
    # session halfway through a save or a close.
    with sessions.LOCK:
        hkey = _history_key(collection)
        if force or hkey != _CACHE["history_key"]:
            _CACHE["history"] = _history(collection)
            _CACHE["history_key"] = hkey
        catalog = _build(collection, _CACHE["history"])
        _CACHE["open_key"] = _open_key()
        _CACHE["catalog"] = catalog
        try:
            sessions._atomic_write(
                sessions.SESSION_DIR / "entry_catalog.json",
                json.dumps(catalog, indent=2, ensure_ascii=False))
        except OSError:
            pass             # derived: the next change writes it again
        return catalog


def counts(collection) -> dict:
    """The status-line totals. Cheap when nothing has changed."""
    return _catalog(collection)["totals"]


def refresh(collection) -> dict:
    """Recount from scratch -- startup, and anything that rewrites history
    behind the fingerprint's back (a rebuild, a restore)."""
    return _catalog(collection, force=True)["totals"]


def is_saved(entry_id: str, collection) -> bool:
    """Whether a save with this id already exists, open or archived -- the
    retry check. A retried request must not append its entry twice."""
    with sessions.LOCK:
        if sessions.has_entry(entry_id, collection):
            return True
        _catalog(collection)
        return entry_id in _CACHE["history"]["archived"]
