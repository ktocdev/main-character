"""export.py / backup.py / rebuild_index.py — the "you can leave" trio.

The claim these three make together is that nothing in this app is trapped
in it: the writing is files, the index is derived from those files, and one
command puts it back. A test suite is the only thing that keeps that claim
true, because the failure mode is silent — a rebuild that drops entries or
duplicates them still exits 0 and still answers questions.
"""

import json
import zipfile
from pathlib import Path

import pytest

import backup
import config
import export
import rebuild_index


def write(name: str, body: str, title: str | None = None,
          date: str = "2026-08-01", realm: str | None = None) -> Path:
    """One journal file in the shape the app writes them."""
    head = f"# {title or 'Untitled'}\n_Date: {date}_"
    if realm:
        head += f"\n_Realm: {realm}_"
    path = Path(config.JOURNAL_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{head}\n\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """An empty journal dir and an empty index, per test."""
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma_data")
    import rag_journal
    monkeypatch.setattr(rag_journal, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(rag_journal, "CHROMA_DIR", tmp_path / "chroma_data")
    (tmp_path / "journal_entries").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# READING A FILE
# ---------------------------------------------------------------------------


def test_the_title_comes_from_the_heading_not_the_filename(journal):
    """The filename is sanitised and the heading is not, and every derived
    store is keyed by the heading. Reading the filename produces an export
    whose titles do not match its own entity graph."""
    write("2026-09-09_Lenas referral.md", "she texted.", title="Lena's referral",
          date="2026-09-09")
    entry = export.read_entries()[0]
    assert entry["title"] == "Lena's referral"
    assert entry["date"] == "2026-09-09"


def test_a_pre_close_backup_is_marked_as_one(journal):
    write("2026-08-01_Real entry.md", "the finished thing.", title="Real entry")
    write("2026-08-01_1013_entry.md", "the copy.", title="Journal entry")
    kinds = {e["file"]: e["kind"] for e in export.read_entries()}
    assert kinds["2026-08-01_Real entry.md"] == "entry"
    assert kinds["2026-08-01_1013_entry.md"] == "draft backup"


def test_a_part_file_remembers_which_chunk_it_was(journal):
    """`_part2` was chunk index 1. The split lives in the filename and
    nowhere else, so losing it means rebuilt chunks get new ids."""
    write("2026-08-01_Long one.md", "first half", title="Long one")
    write("2026-08-01_Long one_part2.md", "second half", title="Long one")
    parts = {e["file"]: e["part"] for e in export.read_entries()}
    assert parts["2026-08-01_Long one.md"] == 0
    assert parts["2026-08-01_Long one_part2.md"] == 1


# ---------------------------------------------------------------------------
# EXPORT AND BACKUP
# ---------------------------------------------------------------------------


def test_an_export_is_readable_without_the_app(journal, tmp_path):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    dest = tmp_path / "out"
    info = export.export_journal(dest)

    assert (dest / "entries" / "2026-08-01_A day.md").exists()
    assert (dest / "README.md").exists()
    assert json.loads((dest / "manifest.json").read_text())["entries"] == 1
    entries = json.loads((dest / "entries.json").read_text(encoding="utf-8"))
    assert entries[0]["text"] == "it rained."
    assert info["entries"] == 1


def test_an_export_carries_no_key_and_no_index(journal, tmp_path):
    """An export is the most likely thing to get copied somewhere shared, and
    the index is the thing rebuild_index.py exists to make unnecessary."""
    write("2026-08-01_A day.md", "it rained.", title="A day")
    dest = tmp_path / "out"
    export.export_journal(dest)
    names = {p.name for p in dest.rglob("*")}
    assert ".env" not in names
    assert "spend_ledger.json" not in names
    assert "chroma_data" not in names


def test_a_backup_is_that_export_zipped(journal, tmp_path):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    dest = tmp_path / "b.zip"
    info = backup.write_backup(dest)

    assert dest.exists() and info["bytes"] > 0
    with zipfile.ZipFile(dest) as z:
        inside = z.namelist()
    assert any(n.endswith("entries/2026-08-01_A day.md") for n in inside)
    assert any(n.endswith("manifest.json") for n in inside)
    # nothing half-written left behind
    assert not list(tmp_path.glob("*.part"))


# ---------------------------------------------------------------------------
# THE ROUND TRIP
# ---------------------------------------------------------------------------


def documents(collection) -> dict:
    got = collection.get(include=["documents"])
    return dict(zip(got["ids"], got["documents"]))


def test_a_wiped_index_comes_back_from_the_markdown(journal):
    """The claim the whole trio rests on: `chroma_data/` is safe to leave out
    of an export because it can be recomputed from what the export holds."""
    from rag_journal import get_collection

    write("2026-08-01_A day.md", "it rained all morning.", title="A day")
    write("2026-08-02_Another.md", "the sun came back.", title="Another",
          date="2026-08-02")
    rebuild_index.rebuild()
    before = documents(get_collection())
    assert len(before) == 2

    get_collection().delete(ids=list(before))
    assert documents(get_collection()) == {}

    rebuild_index.rebuild()
    assert documents(get_collection()) == before


def test_rebuilding_twice_changes_nothing_the_second_time(journal):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    rebuild_index.rebuild()
    second = rebuild_index.rebuild()["journal_entries"]
    assert second["removed"] == 0
    assert second["documents"] == 1


def test_a_backup_of_a_closed_entry_is_not_indexed_twice(journal):
    """The same writing lives in two files once a chat closes. Indexing both
    puts every day in the index twice, the second time under a placeholder
    title."""
    from rag_journal import get_collection

    write("2026-08-01_The session.md", "i walked to the lake.",
          title="The session")
    write("2026-08-01_1013_entry.md", "i walked to the lake.",
          title="Journal entry — 2026-08-01 10:13")

    result = rebuild_index.rebuild()["journal_entries"]
    assert result["skipped"] == 1
    assert result["documents"] == 1
    titles = [m["title"] for m in
              get_collection().get(include=["metadatas"])["metadatas"]]
    assert titles == ["The session"]


def test_a_backup_with_no_closed_entry_behind_it_is_kept(journal):
    """The mirror of the test above, and the more important half: a backup
    whose words are in no finished entry is writing that exists nowhere else
    in the index. Dropping it would delete it from search."""
    from rag_journal import get_collection

    write("2026-08-01_1013_entry.md", "nobody ever closed this chat.",
          title="Journal entry — 2026-08-01 10:13")

    result = rebuild_index.rebuild()["journal_entries"]
    assert result["skipped"] == 0
    metas = get_collection().get(include=["metadatas"])["metadatas"]
    assert metas[0]["source"] == "write_mode"
    assert metas[0]["title"] == "Journal entry 2026-08-01 10:13"


def test_provenance_survives_a_rebuild(journal):
    """`source` is not decoration — `sessions._initial_base()` reads it to
    decide which conversation an open chat continues. A rebuild that flattened
    it would quietly change where a new chat picks up."""
    from rag_journal import get_collection

    write("2026-08-01_Imported.md", "from an export.", title="Imported")
    rebuild_index.rebuild()

    col = get_collection()
    cid = col.get()["ids"][0]
    was = col.get(ids=[cid], include=["metadatas"])["metadatas"][0]
    col.update(ids=[cid], metadatas=[{**was, "source": "bulk_import"}])

    rebuild_index.rebuild()
    after = col.get(ids=[cid], include=["metadatas"])["metadatas"][0]
    assert after["source"] == "bulk_import"


def test_dreams_stay_out_of_the_waking_collection(journal, monkeypatch):
    """`store_dream_entry` is explicit that it never touches the waking
    collection. A rebuild reading the same directory has to keep that true."""
    from rag_journal import get_collection

    write("2026-08-01_A day.md", "it rained.", title="A day")
    write("2026-08-01_0315_dream.md", "the office was empty.",
          title="Dream — 2026-08-01 03:15", realm="dream")

    rebuild_index.build_entries(dry_run=False)
    titles = [m["title"] for m in
              get_collection().get(include=["metadatas"])["metadatas"]]
    assert titles == ["A day"]


# ---------------------------------------------------------------------------
# THE BUTTONS
# ---------------------------------------------------------------------------
# Settings calls the same functions the CLI does. These check the wiring --
# that each route reaches its command, reports where the result went, and
# refuses politely on an empty journal rather than writing an empty folder.


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import server
    # TrustedHostMiddleware refuses TestClient's default "testserver" host
    with TestClient(server.app, base_url="http://127.0.0.1:8144") as c:
        yield c


def test_the_export_button_writes_and_says_where(journal, client, monkeypatch,
                                                 tmp_path):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    monkeypatch.setattr(export, "default_dest", lambda: tmp_path / "out")

    body = client.post("/api/data/export").json()
    assert body["ok"] and body["entries"] == 1
    assert (tmp_path / "out" / "entries.json").exists()
    assert body["path"].endswith("out")


def test_the_backup_button_writes_a_zip(journal, client, monkeypatch, tmp_path):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    monkeypatch.setattr(backup, "default_dest", lambda: tmp_path / "b.zip")

    body = client.post("/api/data/backup").json()
    assert body["ok"] and body["bytes"] > 0
    assert zipfile.is_zipfile(tmp_path / "b.zip")


def test_the_rebuild_button_reports_each_collection(journal, client):
    write("2026-08-01_A day.md", "it rained.", title="A day")
    body = client.post("/api/data/rebuild").json()
    assert body["ok"]
    assert body["journal_entries"]["documents"] == 1


def test_an_empty_journal_is_refused_not_written(journal, client, monkeypatch,
                                                 tmp_path):
    """An export of nothing is a folder that looks like a successful backup
    of a journal that has been emptied. Say no instead."""
    monkeypatch.setattr(backup, "default_dest", lambda: tmp_path / "b.zip")
    for url in ("/api/data/export", "/api/data/backup", "/api/data/rebuild"):
        res = client.post(url)
        assert res.status_code == 409, url
        assert "no entries" in res.json()["error"]
    assert not (tmp_path / "b.zip").exists()
