# SPDX-License-Identifier: AGPL-3.0-or-later
"""The demo opens on a week in progress, not an empty write screen.

What `docs/releasing/demo-open-session-implementation.md` changed, and what
has to keep holding:

  * the shipped open session is closable on arrival -- messages, not base,
    because only messages count as new material;
  * its entries ship as markdown backups but stay out of the demo's index,
    exactly as a real journal's unclosed entries do (OPEN_SESSION_FROM);
  * every restart into the demo restores the opening state, and does
    nothing when no demo is installed.

Everything that writes points at tmp_path. Nothing here touches
seed_corpus/install/ or the real journal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import env_file
import server
import sessions
import seed
from seed_corpus import reset_demo_state

BASE = "http://127.0.0.1:8144"
SHIPPED = ROOT / "seed_corpus" / "sessions" / "current.json"
CORPUS = ROOT / "seed_corpus" / "journal_entries"
OPEN_FROM = "2026-09-15"   # mirrors import_seed_corpus.OPEN_SESSION_FROM


def shipped() -> dict:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


# ---- the shipped open session ----


def test_the_shipped_session_is_the_open_week_and_carries_no_build_bookkeeping():
    cur = shipped()
    assert cur["base"] == []
    assert "_api" not in cur and "_done" not in cur
    # opened by the 9/14 close, not by the first entry written into it
    assert cur["started"] == "2026-09-14 20:40"
    for m in cur["messages"]:
        assert "2026-09-15" <= m["ts"][:10] <= "2026-09-17", m["ts"]
    roles = [m["role"] for m in cur["messages"]]
    assert roles == ["you", "companion"] * (len(roles) // 2)


def test_the_demo_opens_closable():
    """The client's rule (write.js hasNewMaterial): at least one non-dream
    `you` message. A base-only session would leave summarize & close dead."""
    assert any(m["role"] == "you" and not m.get("dream")
               for m in shipped()["messages"])


class _Collection:
    """Enough of a chroma collection for close_session with an empty base:
    it only ever upserts."""
    def __init__(self):
        self.ids = []

    def upsert(self, ids, documents, metadatas):
        self.ids += ids


def test_close_session_accepts_the_shipped_session(monkeypatch, tmp_path):
    """The server-side twin: close_session enforces the same rule and would
    raise "nothing new in this chat yet" over a session it can't close."""
    current = tmp_path / "current.json"
    current.write_text(SHIPPED.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sessions, "CURRENT_FILE", current)
    monkeypatch.setattr(sessions, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(sessions, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path)

    col = _Collection()
    r = sessions.close_session(col, client=None, title_hint="demo week")
    assert r["title"] == "demo week"
    assert col.ids                       # the days were embedded at close
    assert r["date"] == "2026-09-17"     # dated by the last thing written


# ---- the import boundary ----


def run_installer(root: Path) -> subprocess.CompletedProcess:
    """A real import into a throwaway install. A subprocess because the
    installer freezes its data dirs at import time -- the same reason
    /api/setup/install-demo spawns it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MC_")}
    env.update({
        "MC_MOCK": "1",
        "MC_AUTHOR_NAME": "Jordan",
        **{f"MC_{name.upper()}_DIR": str(root / d) for name, d in [
            ("journal", "journal_entries"), ("chroma", "chroma_data"),
            ("entity", "entity_graph"), ("summary", "summaries"),
            ("category", "categories"), ("pattern", "patterns"),
            ("dream", "dreams"), ("session", "sessions")]},
    })
    return subprocess.run(
        [sys.executable, str(ROOT / "seed_corpus" / "import_seed_corpus.py")],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-install")
    done = run_installer(root)
    assert done.returncode == 0, done.stderr or done.stdout
    assert (root / "chroma_data" / ".install-complete").is_file()

    import chromadb
    client = chromadb.PersistentClient(path=str(root / "chroma_data"))
    return SimpleNamespace(root=root, client=client)


def open_entry_files() -> list[Path]:
    return sorted(p for p in CORPUS.glob("*.md") if p.name[:10] >= OPEN_FROM)


def test_the_corpus_has_open_entries_to_test_against():
    assert len(open_entry_files()) == 3


def test_open_entries_are_backed_up_but_not_embedded(installed):
    for f in open_entry_files():
        backup = installed.root / "journal_entries" / f.name
        assert backup.read_text(encoding="utf-8") == \
            f.read_text(encoding="utf-8")

    for name in ("journal_entries", "journal_dreams"):
        dates = [m["date"] for m in
                 installed.client.get_collection(name).get()["metadatas"]]
        assert dates, f"{name} is empty -- the closed corpus went missing"
        assert all(d < OPEN_FROM for d in dates), (name, sorted(dates)[-3:])


def test_search_cannot_find_unclosed_material(installed):
    """The test that the boundary actually holds for a reader: a phrase from
    an open entry retrieves nothing from its day."""
    text = open_entry_files()[0].read_text(encoding="utf-8")
    hits = installed.client.get_collection("journal_entries").query(
        query_texts=[text[-400:]], n_results=5)
    assert all(m["date"] < OPEN_FROM for m in hits["metadatas"][0])


def test_the_advertised_entry_count_is_unchanged(installed):
    """/api/status reports the chroma count. The open week must not move it:
    29 before these entries existed, 29 now."""
    assert installed.client.get_collection("journal_entries").count() == 29


def test_open_week_has_the_latest_seed_loaded_and_no_pending_candidate(installed, monkeypatch):
    summaries = installed.root / "summaries"
    monkeypatch.setattr(seed, "SEED_FILE", summaries / "seed_summary.md")
    monkeypatch.setattr(seed, "CANDIDATE_FILE", summaries / "seed_summary.candidate.md")
    assert "Updated September 14, 2026" in seed.load_seed()
    assert not seed.status()["candidate_exists"]
    assert any("Updated August 25, 2026" in p.read_text(encoding="utf-8")
               for p in (summaries / "seed_backups").glob("*.md"))


def test_reinstalling_over_an_untouched_demo_is_not_refused(installed):
    """The shipped current.json has turns now, and turns are what the
    installer's real-journal guard protects. Its own untouched copy must not
    trip that guard -- while a copy a visitor has written into still does."""
    done = run_installer(installed.root)
    assert done.returncode == 0, done.stderr or done.stdout

    session = installed.root / "sessions" / "current.json"
    touched = shipped()
    touched["messages"].append(
        {"role": "you", "text": "a visitor's line", "ts": "2026-09-18 10:00"})
    session.write_text(json.dumps(touched), encoding="utf-8")
    try:
        done = run_installer(installed.root)
        assert done.returncode != 0
        assert "refusing to install" in (done.stderr + done.stdout)
    finally:
        session.write_bytes(SHIPPED.read_bytes())


# ---- reset on entering the demo ----


def _fake_install(root: Path):
    (root / "chroma_data").mkdir(parents=True)
    (root / "chroma_data" / "chroma.sqlite3").touch()
    (root / "chroma_data" / ".install-complete").touch()
    (root / "sessions").mkdir()
    (root / "sessions" / "current.json").write_text(
        json.dumps({"started": "2026-09-18 09:00", "base": [], "messages": []}),
        encoding="utf-8")


def test_restore_removes_all_visitor_data(tmp_path, monkeypatch):
    install = tmp_path / "install"
    built = run_installer(install)
    assert built.returncode == 0, built.stderr
    monkeypatch.setattr(reset_demo_state, "INSTALL", install)
    # Open and close Chroma in a child so Windows releases its file handles.
    add = subprocess.run([sys.executable, "-c",
        "import chromadb,sys; c=chromadb.PersistentClient(path=sys.argv[1]); "
        "c.get_collection('journal_entries').upsert(ids=['private-review'], "
        "documents=['private visitor writing'], embeddings=[[0.0]*384])",
        str(install / "chroma_data")], capture_output=True, text=True)
    assert add.returncode == 0, add.stderr
    for directory in reset_demo_state.DATA_DIRS.values():
        folder = install / directory
        folder.mkdir(exist_ok=True)
        (folder / "private.txt").write_text("private visitor writing")
    # A real child rebuilds the index; no parent Chroma handle holds it open.
    assert reset_demo_state.restore(install) == [install]
    assert not list(install.rglob("private.txt"))
    assert (install / "sessions" / "current.json").read_bytes() == SHIPPED.read_bytes()
    assert (install / "chroma_data" / ".install-complete").is_file()
    checked = subprocess.run([sys.executable, "-c",
        "import chromadb,sys; c=chromadb.PersistentClient(path=sys.argv[1]); "
        "assert not c.get_collection('journal_entries').get(ids=['private-review'])['ids']; "
        "assert c.get_collection('journal_entries').count() == 29",
        str(install / "chroma_data")], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_restore_refuses_other_paths_and_running_demo(tmp_path, monkeypatch):
    install = tmp_path / "install"
    _fake_install(install)
    with pytest.raises(OSError, match="outside"):
        reset_demo_state.restore(install)
    monkeypatch.setattr(reset_demo_state, "INSTALL", install)
    monkeypatch.setattr(server.config, "CHROMA_DIR", install / "chroma_data")
    with pytest.raises(OSError, match="restart back"):
        reset_demo_state.restore(install)
    assert (install / "chroma_data" / "chroma.sqlite3").exists()


def test_restore_is_a_no_op_with_nothing_installed(tmp_path, monkeypatch):
    install = tmp_path / "install"
    monkeypatch.setattr(reset_demo_state, "INSTALL", install)
    assert reset_demo_state.restore(install) == []
    assert list(tmp_path.iterdir()) == []


def test_restarting_into_the_demo_restores_its_opening_state(
        tmp_path, monkeypatch):
    monkeypatch.setattr(env_file, "ENV_PATH", tmp_path / ".env")
    install = tmp_path / "install"
    _fake_install(install)
    monkeypatch.setattr(server, "SEED_ROOT", install)
    calls = []
    monkeypatch.setattr(reset_demo_state, "restore", lambda path: calls.append(path))
    monkeypatch.setitem(server.SERVER, "instance", SimpleNamespace())
    monkeypatch.setitem(server.RESTART, "requested", False)
    monkeypatch.setitem(server.RESTART, "into", "journal")

    with TestClient(server.app, base_url=BASE) as client:
        r = client.post("/api/restart", json={"into": "seed"})
    assert r.status_code == 200
    assert calls == [install]


def test_a_plain_restart_leaves_the_demo_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(env_file, "ENV_PATH", tmp_path / ".env")
    install = tmp_path / "install"
    _fake_install(install)
    before = (install / "sessions" / "current.json").read_bytes()
    monkeypatch.setattr(server, "SEED_ROOT", install)
    monkeypatch.setitem(server.SERVER, "instance", SimpleNamespace())
    monkeypatch.setitem(server.RESTART, "requested", False)
    monkeypatch.setitem(server.RESTART, "into", "journal")

    with TestClient(server.app, base_url=BASE) as client:
        r = client.post("/api/restart", json={})
    assert r.status_code == 200
    assert (install / "sessions" / "current.json").read_bytes() == before
