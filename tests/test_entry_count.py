# SPDX-License-Identifier: AGPL-3.0-or-later
"""The header's entry count counts saved entries, not index records.

docs/releasing/saved-entry-count-handoff.md, the acceptance list:

  * save entry adds one at once, with or without a reply; send, reflect,
    replies and dreams never do; a restart changes nothing;
  * two saves in one minute are two entries with two backups, and a retry of
    the same save is one;
  * a close moves saved entries from open to indexed without counting them
    again, however many chunks or merged daily records it writes -- and a
    chat-only close adds nothing while still reaching memory;
  * a close interrupted after its archive counts each entry once;
  * a save whose reply fails is still counted; one refused before it was
    stored is not;
  * legacy history counts once per dated entry, however it is chunked, and a
    rebuild of the index keeps the count; the migration of an old open chat
    is conservative and repeatable.

Everything writes to tmp_path or conftest's throwaway tree. Two layers: the
catalog against a fake collection (fast, exact), and the routes against the
real app (the contract the browser sees).
"""

import json

import pytest
from starlette.testclient import TestClient

import entry_catalog
import passages
import sessions

BASE = "http://127.0.0.1:8144"
DAY = "2026-09-10"


class FakeCollection:
    """What close_session and the catalog use of a chroma collection."""

    def __init__(self):
        self.meta = {}

    def upsert(self, ids, documents, metadatas):
        self.meta.update(zip(ids, metadatas))

    def count(self):
        return len(self.meta)

    def get(self, include=None, where=None):
        return {"ids": list(self.meta), "metadatas": list(self.meta.values())}


def chunk(col, cid, date, title, source="bulk_import"):
    col.meta[cid] = {"date": date, "title": title, "source": source}


@pytest.fixture
def col(tmp_path, monkeypatch):
    """An empty journal: fresh session, empty index, empty archive."""
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "CURRENT_FILE", tmp_path / "sessions" / "current.json")
    monkeypatch.setattr(sessions, "ARCHIVE_DIR", tmp_path / "sessions" / "archive")
    monkeypatch.setattr(sessions, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(entry_catalog, "_CACHE", dict.fromkeys(entry_catalog._CACHE))
    # The search passages are test_passages.py's; the fake has no documents.
    monkeypatch.setattr(passages, "index_chunks", lambda *a, **k: 0)
    sessions.save_current(sessions._fresh())
    return FakeCollection()


def totals(col):
    t = entry_catalog.counts(col)
    return t["entries"], t["open_entries"], t["indexed_entries"]


def save(col, text, entry_id, ts=f"{DAY} 09:00", dream=False):
    from config import parse_stamp
    sessions.save_entry(text, entry_id, dream=dream, collection=col,
                        when=parse_stamp(ts))


def send(col, text, ts=f"{DAY} 09:30"):
    from config import parse_stamp
    sessions.append_message("you", text, collection=col, when=parse_stamp(ts),
                            kind="chat")
    sessions.append_message("companion", "mm.", collection=col)


# ---------------------------------------------------------------------------
# THE CATALOG
# ---------------------------------------------------------------------------


def test_an_empty_journal_counts_zero(col):
    assert entry_catalog.counts(col) == {
        "entries": 0, "open_entries": 0, "indexed_entries": 0,
        "unclassified_open": 0}


def test_saves_count_and_sends_do_not(col):
    save(col, "a morning entry", "entry-one-0001")
    save(col, "a morning entry", "entry-two-0002")   # same words, meant twice
    send(col, "and another thing")
    send(col, "one more")
    assert totals(col) == (2, 2, 0)


def test_dreams_stay_out_of_the_waking_total(col):
    save(col, "the flooded house again", "dream-one-0001", dream=True)
    assert totals(col) == (0, 0, 0)


def test_close_moves_entries_to_indexed_without_adding(col):
    """The handoff's example: two saves and five sends on one day. The
    total is two before the close and two after, whatever the chunking."""
    save(col, "first thing", "entry-one-0001")
    save(col, "second thing", "entry-two-0002")
    for n in range(5):
        send(col, f"chat {n}")
    assert totals(col) == (2, 2, 0)
    sessions.close_session(col, title_hint="a week")
    assert totals(col) == (2, 0, 2)
    assert entry_catalog.counts(col)["entries"] == 2


def test_a_long_entry_is_one_entry_in_several_chunks(col):
    long_text = "\n\n".join(f"paragraph {n}. " + "word " * 300 for n in range(8))
    save(col, long_text, "entry-long-0001")
    save(col, "a short one the same day", "entry-short-002")
    sessions.close_session(col, title_hint="long day")
    assert col.count() > 2                    # the day split into chunks
    assert totals(col) == (2, 0, 2)           # still two saved entries


def test_saves_on_different_days_merge_nothing_in_the_count(col):
    save(col, "monday", "entry-mon-00001", ts="2026-09-07 09:00")
    save(col, "tuesday", "entry-tue-00001", ts="2026-09-08 09:00")
    save(col, "tuesday again", "entry-tue-00002", ts="2026-09-08 21:00")
    sessions.close_session(col, title_hint="week")
    assert col.count() == 2                   # one record per day
    assert totals(col) == (3, 0, 3)


def test_a_chat_only_close_adds_nothing_but_still_reaches_memory(col):
    send(col, "just talking today")
    r = sessions.close_session(col, title_hint="talk")
    assert col.count() == 1                   # retrieval material exists
    assert sessions.load_archive(r["key"])["messages"]
    assert totals(col) == (0, 0, 0)


def test_a_close_interrupted_after_its_archive_counts_once(col):
    """Crash between writing the archive and opening the fresh session: the
    same ids sit in both files. They are indexed, and counted once."""
    save(col, "kept safe", "entry-one-0001")
    before = sessions.CURRENT_FILE.read_text(encoding="utf-8")
    sessions.close_session(col, title_hint="x")
    sessions.CURRENT_FILE.write_text(before, encoding="utf-8")
    sessions.WRITES["current"] += 1           # an outside write, as on restart
    assert totals(col) == (1, 0, 1)
    assert entry_catalog.is_saved("entry-one-0001", col)


def test_retry_of_an_archived_save_is_recognised(col):
    save(col, "kept", "entry-one-0001")
    sessions.close_session(col, title_hint="x")
    assert entry_catalog.is_saved("entry-one-0001", col)
    assert not entry_catalog.is_saved("entry-new-0001", col)


def test_a_torn_archive_is_skipped_not_fatal(col):
    """An archive truncated by a crash (closes weren't always atomic) must
    not stop the recount that startup and /api/status both run."""
    save(col, "kept", "entry-one-0001")
    sessions.close_session(col, title_hint="x")
    (sessions.ARCHIVE_DIR / "0000-torn.json").write_text('{"parts": [', encoding="utf-8")
    assert entry_catalog.refresh(col)["entries"] == 1
    assert entry_catalog.is_saved("entry-one-0001", col)


# ---- legacy history ----


def test_imported_history_counts_once_per_dated_entry(col):
    for n in range(3):                        # one long imported day, 3 chunks
        chunk(col, f"a{n}", "2026-01-02", "Old chat")
    chunk(col, "b0", "2026-01-03", "Old chat")   # the same chat, next day
    chunk(col, "c0", "2026-01-03", "Journal entry 2026-01-03 10:00", "write_mode")
    assert totals(col) == (3, 0, 3)
    origins = sorted(r["origin"] for r in
                     json.loads((sessions.SESSION_DIR / "entry_catalog.json")
                                .read_text(encoding="utf-8"))["entries"])
    assert origins == ["import", "import", "legacy"]


def test_an_old_close_is_counted_from_the_index_not_its_messages(col):
    """A pre-schema archive's day parts are counted once from the index; its
    user messages and carried-forward base are never counted again."""
    chunk(col, "imp", "2026-01-02", "Old chat")
    chunk(col, "cl", "2026-01-05", "Old chat — continued", "session_close")
    sessions.ARCHIVE_DIR.mkdir(parents=True)
    (sessions.ARCHIVE_DIR / "old.json").write_text(json.dumps({
        "id": "old", "title": "Old chat", "parts": [
            {"date": "2026-01-02", "title": "Old chat", "text": "base"},
            {"date": "2026-01-05", "title": "Old chat — continued"}],
        "messages": [{"role": "you", "text": f"m{n}", "ts": "2026-01-05 10:00"}
                     for n in range(4)]}), encoding="utf-8")
    assert totals(col) == (2, 0, 2)


def test_recount_from_scratch_agrees_and_is_repeatable(col):
    chunk(col, "imp", "2026-01-02", "Old chat")
    save(col, "new", "entry-one-0001")
    first = entry_catalog.counts(col)
    (sessions.SESSION_DIR / "entry_catalog.json").unlink()
    entry_catalog._CACHE.update(dict.fromkeys(entry_catalog._CACHE))
    assert entry_catalog.refresh(col) == first
    assert entry_catalog.refresh(col) == first
    catalog = json.loads((sessions.SESSION_DIR / "entry_catalog.json")
                         .read_text(encoding="utf-8"))
    assert catalog["version"] == entry_catalog.VERSION


# ---- migrating an old open chat ----


def _legacy_session(col, msgs, backups):
    """current.json as the pre-schema app wrote it, with its backups."""
    sessions.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    arts = []
    for name, text in backups.items():
        path = sessions.JOURNAL_DIR / name
        path.write_text(f"# Journal entry — x\n_Date: {name[:10]}_\n\n{text}",
                        encoding="utf-8")
        arts.append({"kind": "entry_file", "path": str(path)})
    sessions.CURRENT_FILE.write_text(json.dumps({
        "started": f"{DAY} 08:00", "base": [], "artifacts": arts,
        "messages": msgs}), encoding="utf-8")
    sessions.WRITES["current"] += 1


def you(text, ts=f"{DAY} 09:00", **extra):
    return {"role": "you", "text": text, "ts": ts, **extra}


def test_migration_classifies_only_what_a_backup_proves(col):
    _legacy_session(col, [
        you("saved with a backup"),
        {"role": "companion", "text": "ok", "ts": f"{DAY} 09:00"},
        you("sent, no backup", ts=f"{DAY} 09:05"),
        you("a dream", ts=f"{DAY} 09:06", dream=True),
    ], {f"{DAY}_0900_entry.md": "saved with a backup"})
    t = entry_catalog.counts(col)
    assert (t["entries"], t["open_entries"], t["unclassified_open"]) == (1, 1, 1)

    cur = json.loads(sessions.CURRENT_FILE.read_text(encoding="utf-8"))
    kinds = [m.get("kind") for m in cur["messages"] if m["role"] == "you"]
    assert kinds == ["entry", None, None]
    assert cur["entry_schema"] == sessions.ENTRY_SCHEMA
    # the original is kept, and a second pass changes nothing
    assert (sessions.SESSION_DIR / "current.pre-entry-schema.json").exists()
    again = sessions.load_current(col)
    assert again == cur


def test_migration_leaves_identical_same_minute_messages_unclassified(col):
    _legacy_session(col, [you("same words"), you("same words")],
                    {f"{DAY}_0900_entry.md": "same words"})
    t = entry_catalog.counts(col)
    assert (t["entries"], t["unclassified_open"]) == (0, 2)


def test_closing_a_legacy_chat_counts_its_unknown_day_once(col):
    """The documented exception: unclassified writing counts as the legacy
    dated entry its old close would have been, alongside identified saves."""
    _legacy_session(col, [
        you("proven save"), you("unknown one", ts=f"{DAY} 10:00"),
        you("unknown two", ts=f"{DAY} 11:00"),
        you("only unknown", ts="2026-09-11 09:00"),
    ], {f"{DAY}_0900_entry.md": "proven save"})
    assert totals(col) == (1, 1, 0)
    sessions.close_session(col, title_hint="old chat")
    # DAY: the proven save + one legacy unit; the 11th: one legacy unit
    assert totals(col) == (3, 0, 3)


# ---------------------------------------------------------------------------
# THE ROUTES
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture
def fresh(client):
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    yield
    sessions.CURRENT_FILE.unlink(missing_ok=True)


def status(client):
    return client.get("/api/status").json()


def you_messages():
    return [m for m in json.loads(sessions.CURRENT_FILE.read_text(
        encoding="utf-8"))["messages"] if m["role"] == "you"]


def test_saves_count_immediately_and_nothing_else_does(client, fresh):
    start = status(client)["entries"]
    assert client.post("/api/entry", json={"text": "with a reply"}).status_code == 200
    assert status(client)["entries"] == start + 1
    r = client.post("/api/entry", json={"text": "no reply", "no_reply": True})
    assert r.json()["ok"] and not r.json()["duplicate"]
    assert status(client)["entries"] == start + 2
    assert client.post("/api/chat", json={"message": "a send"}).status_code == 200
    assert client.post("/api/reflect", json={}).status_code == 200
    s = status(client)
    assert (s["entries"], s["open_entries"]) == (start + 2, 2)
    assert s["entries"] == s["open_entries"] + s["indexed_entries"]
    assert [m["kind"] for m in you_messages()] == ["entry", "entry", "chat"]


def test_the_count_survives_a_restart(client, fresh):
    import server
    client.post("/api/entry", json={"text": "still here", "no_reply": True})
    before = status(client)
    entry_catalog._CACHE.update(dict.fromkeys(entry_catalog._CACHE))
    server.startup()
    assert status(client)["entries"] == before["entries"]


def test_two_saves_in_one_minute_are_two_entries_with_two_backups(client, fresh):
    from rag_journal import JOURNAL_DIR
    start = status(client)["entries"]
    for _ in range(2):
        client.post("/api/entry", json={"text": "identical", "no_reply": True,
                                        "ts": "2024-04-01 07:30"})
    assert status(client)["entries"] == start + 2
    assert len(list(JOURNAL_DIR.glob("2024-04-01_0730_*_entry.md"))) == 2


def test_a_retried_save_is_stored_once(client, fresh):
    start = status(client)["entries"]
    body = {"text": "sent twice by a flaky network", "save_id": "retry-me-0001"}
    first = client.post("/api/entry", json={**body, "no_reply": True})
    again = client.post("/api/entry", json={**body, "no_reply": True})
    assert again.json()["duplicate"] and not first.json()["duplicate"]
    # the reply path recognises it too, and pays for no second reply
    replied = client.post("/api/entry", json=body)
    assert replied.json()["duplicate"]
    assert replied.headers["X-Entry-Saved"] == "1"
    assert status(client)["entries"] == start + 1
    assert len(you_messages()) == 1


def test_a_retry_after_its_own_reply_hit_the_cap_is_a_duplicate(client, fresh, monkeypatch):
    """The first attempt landed and its reply spent up to the cap, but the
    response was lost. The retry must say "already saved", not 429 -- a
    duplicate makes no call, so the cap has nothing to refuse."""
    import caps
    body = {"text": "landed, then the network dropped", "save_id": "capped-00001"}
    assert client.post("/api/entry", json=body).status_code == 200

    def capped():
        raise caps.CapExceeded("monthly cap reached")

    monkeypatch.setattr(caps, "check", capped)
    again = client.post("/api/entry", json=body)
    assert again.status_code == 200 and again.json()["duplicate"]
    assert again.headers["X-Entry-Saved"] == "1"
    # a genuinely new save is still refused
    assert client.post("/api/entry", json={"text": "new"}).status_code == 429


def test_a_save_refused_before_storing_does_not_count(client, fresh):
    start = status(client)["entries"]
    assert client.post("/api/entry", json={"text": "   "}).status_code == 400
    assert client.post("/api/entry", json={
        "text": "fine", "save_id": "../../etc"}).status_code == 400
    # `$` would accept a trailing newline; it must be refused like any other
    for sid in ("abcdefgh\n", "abcdefgh\n "):
        assert client.post("/api/entry", json={
            "text": "fine", "save_id": sid, "dream": True}).status_code == 400
    assert status(client)["entries"] == start


def test_a_failed_reply_leaves_the_entry_counted_once(client, fresh, monkeypatch):
    import companion

    def broken(*args, **kwargs):
        yield "half a thou"
        raise RuntimeError("the connection dropped")

    monkeypatch.setattr(companion, "stream_reply", broken)
    start = status(client)["entries"]
    with pytest.raises(RuntimeError):
        client.post("/api/entry", json={"text": "kept anyway", "save_id": "kept-0000001"})
    assert status(client)["entries"] == start + 1
    assert [m["entry_id"] for m in you_messages()] == ["kept-0000001"]


def test_a_dream_save_does_not_count(client, fresh):
    start = status(client)["entries"]
    client.post("/api/entry", json={"text": "I was flying over a lake",
                                    "dream": True, "no_reply": True})
    assert status(client)["entries"] == start


def test_close_keeps_the_total_and_empties_open(client, fresh, monkeypatch):
    import server
    # the count moves at close itself; the memory pipeline queued after it
    # is slow in mock mode and covered by the demo replay, so it stays out
    monkeypatch.setattr(server, "_tracked", lambda tasks, fn, *args: (lambda: None))
    col = server.STATE["collection"]
    had = (set(col.get()["ids"]), set(sessions.ARCHIVE_DIR.glob("*.json")),
           set(sessions.JOURNAL_DIR.glob("*.md")))
    try:
        client.post("/api/entry", json={"text": "a saved entry", "no_reply": True})
        client.post("/api/chat", json={"message": "and a chat"})
        before = status(client)
        r = client.post("/api/sessions/close", json={"title": "count test"})
        assert r.status_code == 200, r.text
        after = status(client)
        assert after["entries"] == before["entries"]
        assert after["open_entries"] == 0
        assert after["indexed_entries"] == before["indexed_entries"] + 1
        assert after["journal_chunks"] > before["journal_chunks"]
    finally:
        # the suite shares one journal, and test_smoke expects it empty
        server._close_finish()
        added = sorted(set(col.get()["ids"]) - had[0])
        if added:
            col.delete(ids=added)
        for path in (set(sessions.ARCHIVE_DIR.glob("*.json")) - had[1]) \
                | (set(sessions.JOURNAL_DIR.glob("*.md")) - had[2]):
            path.unlink()


# ---------------------------------------------------------------------------
# EXPORT AND REBUILD
# ---------------------------------------------------------------------------


def test_the_new_backup_name_is_still_a_backup():
    import export
    for name in ("2026-09-19_1015_3f2a9c0de1b24c55_entry.md",
                 "2026-09-19_1015_entry.md"):
        assert export.DRAFT.match(name), name
    assert not export.DRAFT.match("2026-09-19_A day_entry.md")


def test_a_rebuilt_index_keeps_the_count(tmp_path, monkeypatch):
    """Rebuild from the markdown onto an empty index: every logical entry --
    an imported day split in parts, a schema-2 close and its backup -- comes
    back to the same total."""
    import config
    import rag_journal
    import rebuild_index
    journal = tmp_path / "journal_entries"
    for mod in (config, rag_journal, sessions):
        monkeypatch.setattr(mod, "JOURNAL_DIR", journal)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma_data")
    monkeypatch.setattr(rag_journal, "CHROMA_DIR", tmp_path / "chroma_data")
    monkeypatch.setattr(config, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "CURRENT_FILE", tmp_path / "sessions" / "current.json")
    monkeypatch.setattr(sessions, "ARCHIVE_DIR", tmp_path / "sessions" / "archive")
    monkeypatch.setattr(entry_catalog, "_CACHE", dict.fromkeys(entry_catalog._CACHE))
    journal.mkdir()
    long = "\n\n".join("word " * 400 for _ in range(4))
    (journal / "2026-01-02_Old chat.md").write_text(
        f"# Old chat\n_Date: 2026-01-02_\n\n{long[:6000]}", encoding="utf-8")
    (journal / "2026-01-02_Old chat_part2.md").write_text(
        f"# Old chat\n_Date: 2026-01-02_\n\n{long[6000:]}", encoding="utf-8")

    col = rag_journal.get_collection()
    sessions.save_current(sessions._fresh())
    save(col, "a new saved entry", "entry-new-00001")
    sessions.backup_entry_text("a new saved entry", entry_id="entry-new-00001",
                               when=config.parse_stamp(f"{DAY} 09:00"))
    sessions.close_session(col, title_hint="new chat")
    rebuild_index.rebuild()
    before = entry_catalog.refresh(col)
    assert before["entries"] == 2

    for cid in col.get()["ids"]:
        col.delete(ids=[cid])
    rebuild_index.rebuild()
    assert entry_catalog.refresh(col) == before
