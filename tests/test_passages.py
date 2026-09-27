# SPDX-License-Identifier: AGPL-3.0-or-later
"""The search-only passage index (passages.py, LOOKUP-UPGRADE-HANDOFF.md).

  * the splitter: an entry within the target stays whole; long paragraphs
    split at sentence ends; one entry's pieces come out about the same size;
    no piece is over the model's window at any setting; every character is
    covered;
  * search: the pieces of a long query take turns at the results, and a
    hit's neighbours are cut from the chunk itself, so nothing shows twice;
  * keyword search is merged in: a rare word finds the passage search by
    meaning ranks last, a passage found only by keyword still has a
    distance, and the keyword index follows the passage index's writes and
    a search's filter;
  * a question that names a time brings that time's entries forward, a
    time with no entries changes nothing, and a whole entry isn't searched
    by the dates it mentions;
  * the write paths keep the index complete or absent: search falls back to
    the journal chunks only when it is absent, so an index missing an entry
    would hide that entry instead;
  * the context block shows passages whole, and skips one it already shows
    as a recent entry;
  * summaries and dreams are on the same model: a collection from before it
    is still searched the old way until its owner rewrites it, and a dream
    written into a collection that isn't built brings every dream back;
  * the search tab lists each entry once, with the passage that matched.

These run the real model (downloaded once per machine, like chroma's own).
Everything writes to tmp_path.
"""

import json
import re
from datetime import datetime

import pytest

import companion
import config
import passages
import rag_journal
import sessions


def para(topic: str, n: int) -> str:
    return " ".join(f"The {topic} was on my mind again, sentence number {i}."
                    for i in range(n))


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    """An empty chroma directory for both indexes."""
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma_data")
    monkeypatch.setattr(rag_journal, "CHROMA_DIR", tmp_path / "chroma_data")
    return tmp_path / "chroma_data"


def write_chunks(chunks: dict[str, str], date="2026-03-02", title="A day"):
    """Journal chunks, written the way every write path writes them."""
    col = rag_journal.get_collection()
    ids = list(chunks)
    metas = [{"date": date, "title": title} for _ in ids]
    col.upsert(ids=ids, documents=list(chunks.values()), metadatas=metas)
    return col, ids, list(chunks.values()), metas


def sources(col) -> set[str]:
    return {m["source_id"] for m in col.get(include=["metadatas"])["metadatas"]}


# ---------------------------------------------------------------------------
# THE SPLITTER
# ---------------------------------------------------------------------------


def test_an_entry_within_the_target_stays_whole():
    text = "\n\n" + para("garden", 3) + "\n"
    pieces = passages.split(text, target=passages.limit())
    assert len(pieces) == 1
    assert pieces[0]["text"] == text.strip()
    assert text[pieces[0]["start"]:pieces[0]["end"]] == text.strip()


@pytest.mark.parametrize("target", [60, 120, 254, None])
def test_no_piece_is_over_the_window_and_every_character_is_covered(target):
    text = "\n\n".join(para(t, n) for t, n in
                       [("garden", 4), ("move", 30), ("sister", 2),
                        ("job", 1), ("trip", 18)])
    pieces = passages.split(text, target=target)
    assert len(pieces) > 1
    assert all(passages.count_tokens(p["text"]) <= passages.limit() for p in pieces)
    for p in pieces:
        assert text[p["start"]:p["end"]].strip() == p["text"]
    # Consecutive pieces touch or overlap, from the first word to the last.
    assert pieces[0]["start"] == 0 and pieces[-1]["end"] == len(text)
    for a, b in zip(pieces, pieces[1:]):
        assert b["start"] <= a["end"]


def test_long_paragraphs_split_at_sentence_ends_and_pieces_are_even():
    text = para("move", 40)
    pieces = passages.split(text, target=120)
    for p in pieces[:-1]:
        assert p["text"].endswith("."), p["text"][-40:]
    sizes = [passages.count_tokens(p["text"]) for p in pieces]
    assert max(sizes) - min(sizes) <= 0.35 * max(sizes), sizes


def test_short_paragraphs_are_merged_not_left_alone():
    text = "\n\n".join(["Tired.", "Rain.", para("job", 3), "Ok.", para("trip", 3)])
    pieces = passages.split(text, target=254)
    assert len(pieces) == 1


def test_a_passage_records_its_place_in_the_chunk():
    rows = passages.passages_for_chunk(
        "c1", para("move", 40), {"date": "2026-03-02", "title": "Moving"})
    assert [r[0] for r in rows] == [f"c1_p{i}" for i in range(len(rows))]
    assert {r[2]["count"] for r in rows} == {len(rows)}
    assert all(r[2]["source_id"] == "c1" and r[2]["date"] == "2026-03-02"
               for r in rows)


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------


class _Ranked:
    """A collection whose search results are given, one list per query
    piece, so the merge can be checked without depending on the model's
    exact distances."""

    def __init__(self, per_piece):
        self.per_piece = per_piece

    def count(self):
        return 100

    def query(self, query_embeddings, n_results, include, **_):
        assert len(query_embeddings) == len(self.per_piece)
        lists = [p[:n_results] for p in self.per_piece]
        return {k: [[h[i] for h in hits] for hits in lists]
                for i, k in enumerate(["ids", "documents", "metadatas", "distances"])}


def hit(pid, dist):
    return (pid, pid, {"source_id": pid, "position": 0, "count": 1}, dist)


def test_the_pieces_of_a_long_query_take_turns(monkeypatch):
    """A long opening about work must not take every slot from the one
    thing the entry's ending is about."""
    monkeypatch.setattr(passages, "query_pieces", lambda q: ["opening", "ending"])
    col = _Ranked([[hit(f"work{i}", 0.10 + i / 100) for i in range(6)],
                   [hit("ending", 0.40), hit("ending2", 0.45)]])
    got = [h["text"] for h in passages.search("long", 3, neighbors=0, collection=col)]
    assert got == ["work0", "ending", "work1"]


def test_a_long_query_is_searched_end_to_end():
    query = "\n\n".join(para(t, 12) for t in ["work", "garden", "move", "trip"])
    pieces = passages.query_pieces(query)
    assert len(pieces) > 1
    assert "trip" in pieces[-1] and "work" in pieces[0]


STRUGGLING = "When did Robin admit he was still struggling with melatonin?"


def struggles(chroma, rare="melatonin"):
    """Twelve entries that mean what the question means, and one that names
    `rare` in passing -- the one it asks about, which search by meaning
    ranks last. The twelve share one of its words, "Robin", as a journal's
    entries about a person do."""
    for i in range(12):
        col, *_ = write_chunks(
            {f"c{i}": f"Robin confessed he is still having a hard time quitting, "
                      f"day {i}. He told me on the phone and he keeps trying."},
            date=f"2026-03-{i + 1:02d}")
    write_chunks({"c12": "The garden was muddy. Later there was "
                         f"{rare} on the counter and I didn't say anything."},
                 date="2026-03-20")
    passages.sync(col)


def test_a_rare_word_finds_its_passage_among_many_alike(chroma, monkeypatch):
    struggles(chroma)
    monkeypatch.setattr(passages, "KEYWORDS", False)
    assert all("melatonin" not in h["text"]
               for h in passages.search(STRUGGLING, 2, neighbors=0))
    monkeypatch.setattr(passages, "KEYWORDS", True)
    assert any("melatonin" in h["text"]
               for h in passages.search(STRUGGLING, 2, neighbors=0))


def test_a_passage_found_only_by_keyword_has_a_distance(chroma, monkeypatch):
    struggles(chroma)
    monkeypatch.setattr(passages, "FUSION_DEPTH", 1)    # meaning offers 2
    got = passages.search(STRUGGLING, 2, neighbors=0)
    assert any("melatonin" in h["text"] for h in got)
    assert all(isinstance(h["distance"], float) and 0 <= h["distance"] <= 2
               for h in got)


def test_the_keyword_index_follows_the_writes(chroma):
    struggles(chroma, rare="coffee")
    assert all("melatonin" not in h["text"]                 # built, without it
               for h in passages.search(STRUGGLING, 2, neighbors=0))
    _, ids, docs, metas = write_chunks(
        {"c13": "Melatonin again, left by the sink."}, date="2026-03-21")
    passages.index_chunks(ids, docs, metas)
    assert any("Melatonin" in h["text"]
               for h in passages.search(STRUGGLING, 2, neighbors=0))
    passages.remove_chunks(["c13"])
    assert all("Melatonin" not in h["text"]
               for h in passages.search(STRUGGLING, 13, neighbors=0))


def test_a_filtered_search_keeps_its_filter_for_keywords(chroma):
    struggles(chroma)
    got = passages.search(STRUGGLING, 5, neighbors=0,
                          where={"date": {"$eq": "2026-03-01"}})
    assert [h["metadata"]["source_id"] for h in got] == ["c0"]


GARDEN = "What did I do in the garden in March?"


def gardens(chroma):
    """Six garden entries in other months, and a March one that is mostly
    about something else -- by meaning, the last garden entry."""
    for m in (1, 2, 4, 5, 6, 7):
        write_chunks({f"c{m}": "Out in the garden all afternoon, weeding the garden "
                               f"beds and planning the garden, day {m}."},
                     date=f"2026-{m:02d}-10")
    col, *_ = write_chunks({"c3": "Robin came by. Later the garden, briefly."},
                           date="2026-03-10")
    passages.sync(col)


def test_a_month_brings_its_entries_forward(chroma, monkeypatch):
    gardens(chroma)
    monkeypatch.setattr(passages, "DATES", False)
    assert passages.search(GARDEN, 3, neighbors=0)[0]["metadata"]["source_id"] != "c3"
    monkeypatch.setattr(passages, "DATES", True)
    assert passages.search(GARDEN, 3, neighbors=0)[0]["metadata"]["source_id"] == "c3"


def test_a_month_with_no_entries_changes_nothing(chroma, monkeypatch):
    gardens(chroma)
    question = "What did I do in the garden in August?"
    with_dates = passages.search(question, 3, neighbors=0)
    monkeypatch.setattr(passages, "DATES", False)
    assert passages.search(question, 3, neighbors=0) == with_dates


def test_a_whole_entry_is_not_searched_by_its_dates(chroma, monkeypatch):
    """An entry names dates in passing; they aren't what it asks about."""
    import timeframe
    gardens(chroma)
    monkeypatch.setattr(timeframe, "dates_within",
                        lambda *a, **k: pytest.fail("a long query read for dates"))
    passages.search("\n\n".join([para("garden", 12), "Back in March, too."]), 3)


def test_neighbours_are_cut_from_the_chunk_and_never_shown_twice(chroma):
    text = "\n\n".join(para(t, 8) for t in
                       ["garden", "move", "sister", "job", "trip", "piano"])
    col, ids, docs, metas = write_chunks({"c1": text})
    passages.sync(col)
    got = passages.search("my sister", 3, neighbors=1)
    assert got
    for h in got:
        assert h["text"] in text                  # one span of the entry
    # Windows that touch were merged, so no sentence appears in two.
    sentences = [s for h in got for s in re.findall(r"[^.]+\.", h["text"])]
    assert len(sentences) == len(set(sentences))


def test_a_stale_index_is_not_searched_and_the_journal_is(chroma, monkeypatch):
    col, *_ = write_chunks({"c1": para("sister", 4), "c2": para("garden", 4)})
    passages.sync(col)
    monkeypatch.setattr(passages, "EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    assert passages.get_passage_collection() is None
    assert passages.search("sister", 2) == []
    hits = rag_journal.query_journal("sister", 2)
    assert "source_id" not in hits[0]["metadata"]     # whole journal chunks
    assert hits[0]["text"] == para("sister", 4)


# ---------------------------------------------------------------------------
# THE WRITE PATHS
# ---------------------------------------------------------------------------


def test_a_new_journals_first_write_builds_the_index(chroma):
    col, ids, docs, metas = write_chunks({"c1": para("sister", 20)})
    assert passages.index_chunks(ids, docs, metas, journal=col) > 1
    assert sources(passages.get_passage_collection()) == {"c1"}


def test_new_chunks_join_a_built_index(chroma):
    col, *_ = write_chunks({"c1": para("sister", 6)})
    passages.sync(col)
    _, ids, docs, metas = write_chunks({"c2": para("garden", 6)})
    assert passages.index_chunks(ids, docs, metas, journal=col) >= 1
    assert sources(passages.get_passage_collection()) == {"c1", "c2"}
    assert "garden" in passages.search("the garden", 1)[0]["text"]


def test_a_rewritten_chunk_replaces_its_passages(chroma):
    col, ids, docs, metas = write_chunks({"c1": para("sister", 30)})
    passages.index_chunks(ids, docs, metas, journal=col)
    _, ids, docs, metas = write_chunks({"c1": para("garden", 3)})
    passages.index_chunks(ids, docs, metas, journal=col)
    got = passages.get_passage_collection().get(include=["documents"])
    assert got["ids"] == ["c1_p0"]
    assert "sister" not in got["documents"][0]


def test_an_unbuilt_index_is_not_started_by_one_entry(chroma):
    """An install that hasn't run rebuild_index.py since the passage index
    arrived: an index of just the newest entry would be all search sees."""
    col, *_ = write_chunks({"old": para("sister", 6)})
    _, ids, docs, metas = write_chunks({"new": para("garden", 6)})
    assert passages.index_chunks(ids, docs, metas, journal=col) == 0
    assert passages._open()[2] == "missing"
    assert rag_journal.query_journal("sister", 1)[0]["text"] == para("sister", 6)


def test_an_index_from_another_model_is_left_for_the_rebuild(chroma, monkeypatch):
    col, *_ = write_chunks({"c1": para("sister", 6)})
    passages.sync(col)
    with monkeypatch.context() as m:
        m.setattr(passages, "EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        _, ids, docs, metas = write_chunks({"c2": para("garden", 6)})
        assert passages.index_chunks(ids, docs, metas, journal=col) == 0
    assert sources(passages.get_passage_collection()) == {"c1"}


def test_a_failed_write_drops_the_index_rather_than_hide_the_entry(
        chroma, monkeypatch, capsys):
    col, *_ = write_chunks({"c1": para("sister", 6)})
    passages.sync(col)

    def unavailable(*a, **k):
        raise passages.ModelUnavailable("offline")
    with monkeypatch.context() as m:
        m.setattr(passages, "embed", unavailable)
        _, ids, docs, metas = write_chunks({"c2": para("garden", 6)})
        assert passages.index_chunks(ids, docs, metas, journal=col) == 0
    assert passages._open()[2] == "missing"
    assert "rebuild_index.py" in capsys.readouterr().err
    # The journal chunks hold the new entry, and search reads them now.
    assert rag_journal.query_journal("garden", 1)[0]["text"] == para("garden", 6)


def test_removed_chunks_take_their_passages(chroma):
    col, *_ = write_chunks({"c1": para("sister", 6), "c2": para("garden", 6)})
    passages.sync(col)
    passages.remove_chunks(["c1"])
    assert sources(passages.get_passage_collection()) == {"c2"}


def test_a_close_writes_the_passages(chroma, tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "CURRENT_FILE", tmp_path / "sessions" / "current.json")
    monkeypatch.setattr(sessions, "ARCHIVE_DIR", tmp_path / "sessions" / "archive")
    monkeypatch.setattr(sessions, "JOURNAL_DIR", tmp_path / "journal_entries")
    sessions.save_current(sessions._fresh())
    col = rag_journal.get_collection()
    ending = "At the very end: the karaoke night with the neighbours."
    sessions.append_message("you", para("job", 40) + "\n\n" + ending,
                            collection=col, kind="chat")
    sessions.append_message("companion", "mm.", collection=col)
    sessions.close_session(col, title_hint="a week")

    passage_col = passages.get_passage_collection()
    assert sources(passage_col) == set(col.get(include=[])["ids"])
    assert ending in passages.search("karaoke with the neighbours", 1,
                                     neighbors=0)[0]["text"]


# ---------------------------------------------------------------------------
# THE CONTEXT BLOCK
# ---------------------------------------------------------------------------


def related(block: str) -> str:
    return block.split("<related_history>")[1].split("</related_history>")[0]


def test_passages_are_shown_whole_and_not_repeated_from_recent(chroma, monkeypatch):
    long_old = "\n\n".join([para("job", 60), "The hidden detail: Pickle the cat."])
    recent = "\n\n".join([para("garden", 10), "My sister called about the piano.",
                          para("trip", 60), "Late in the day: the piano again."])
    col = rag_journal.get_collection()
    col.upsert(ids=["old", "new"], documents=[long_old, recent],
               metadatas=[{"date": "2026-01-01", "title": "Old"},
                          {"date": "2026-03-01", "title": "New"}])
    passages.sync(col)
    monkeypatch.setattr(companion, "get_recent_chunks",
                        lambda c, n=1: [(recent, {"date": "2026-03-01",
                                                  "title": "New", "_id": "new"})])

    block = related(companion.build_context_block(
        "Pickle the cat, and the piano", col, {}))
    # Past EXCERPT_CHARS of its chunk, and shown anyway, in full.
    assert long_old.index("Pickle") > config.EXCERPT_CHARS
    assert "The hidden detail: Pickle the cat." in block
    # The recent entry shows its first EXCERPT_CHARS: a passage from there
    # isn't repeated, one from past it is.
    assert "My sister called about the piano." not in block
    assert "Late in the day: the piano again." in block
    # Nor does any of it come back as a neighbour of another hit.
    for sentence in re.findall(r"[^.]+\.", recent[:config.EXCERPT_CHARS]):
        assert sentence.strip() not in block, sentence


def summaries(block: str) -> str:
    return block.split("<related_summaries>")[1].split("</related_summaries>")[0]


def entry_with_a_summary(col):
    """One long entry whose detail sits deep, and its entry summary."""
    text = "\n\n".join([para("job", 60), "The hidden detail: Pickle the cat."])
    col.upsert(ids=["old"], documents=[text],
               metadatas=[{"date": "2026-01-01", "title": "Old"}])
    passages.sync(col)
    passages.mirror(companion.SUMMARY_COLLECTION, [(
        "s-old", "A long week at work, and a new cat called Pickle.",
        {"level": "entry summary", "date": "2026-01-01", "title": "Old"})])


def test_an_entry_summary_brings_the_entrys_words(chroma, monkeypatch):
    """Step 2: a summary hit comes with that entry's best passage, even when
    related history didn't find the entry."""
    col = rag_journal.get_collection()
    entry_with_a_summary(col)
    monkeypatch.setattr(companion, "get_recent_chunks", lambda c, n=1: [])
    monkeypatch.setattr(companion, "query_journal", lambda q, n_results: [])

    block = summaries(companion.build_context_block("Pickle the cat", col, {}))
    assert "A long week at work, and a new cat called Pickle." in block
    assert "From the entry itself, in their words: [2026-01-01] Old" in block
    assert "The hidden detail: Pickle the cat." in block


def test_an_entry_passage_already_shown_is_not_repeated(chroma, monkeypatch):
    col = rag_journal.get_collection()
    entry_with_a_summary(col)
    monkeypatch.setattr(companion, "get_recent_chunks", lambda c, n=1: [])

    block = companion.build_context_block("Pickle the cat", col, {})
    assert "The hidden detail: Pickle the cat." in related(block)
    assert "From the entry itself" not in summaries(block)


# ---------------------------------------------------------------------------
# SUMMARIES AND DREAMS, ON THE SAME MODEL
# ---------------------------------------------------------------------------


def test_mirror_replaces_a_minilm_collection_and_embeds_only_changes(chroma):
    import chromadb
    client = chromadb.PersistentClient(path=str(chroma))
    old = client.get_or_create_collection("docs", metadata={"hnsw:space": "cosine"})
    old.upsert(ids=["a", "gone"], documents=[para("sister", 2), para("job", 2)])

    # Before its owner rewrites it: still searched, the old way.
    found = passages.search_documents("docs", "my sister", 1)
    assert found[0][0] == para("sister", 2)

    rows = [("a", para("sister", 2), {"level": "x"}), ("b", para("garden", 2), {"level": "x"})]
    assert passages.mirror("docs", rows) == {"documents": 2, "removed": 0, "embedded": 2}
    assert passages.model_collection("docs").get()["ids"] == ["a", "b"]
    rows[1] = ("b", para("garden", 3), {"level": "x"})
    assert passages.mirror("docs", rows)["embedded"] == 1
    assert passages.search_documents("docs", "the garden", 1)[0][0] == para("garden", 3)


def test_a_collection_from_another_model_is_not_searched(chroma, monkeypatch):
    passages.mirror("docs", [("a", para("sister", 2), {"level": "x"})])
    monkeypatch.setattr(passages, "EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    assert passages.search_documents("docs", "my sister", 1) == []


@pytest.fixture
def dream_dirs(chroma, tmp_path, monkeypatch):
    import dreams
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(dreams, "JOURNAL_DIR", tmp_path / "journal_entries")
    monkeypatch.setattr(dreams, "RAW_DIR", tmp_path / "dreams" / "raw")
    monkeypatch.setattr(dreams, "INDEX_FILE", tmp_path / "dreams" / "index.json")
    monkeypatch.setattr(dreams, "DREAM_DIR", tmp_path / "dreams")
    return dreams


def extracted(dreams, date, narrative):
    dreams.RAW_DIR.mkdir(parents=True, exist_ok=True)
    (dreams.RAW_DIR / f"{date}_x.json").write_text(json.dumps({
        "date": date, "title": "A day", "dreams": [{
            "narrative": narrative, "tones": ["uneasy"], "people": [],
            "places": [], "interpretation": ""}]}), encoding="utf-8")


def test_a_flagged_dream_joins_and_a_rebuild_keeps_its_time(dream_dirs):
    dreams = dream_dirs
    extracted(dreams, "2026-02-01", "The office was flooded and nobody minded.")
    dreams.build_index()
    entry_id, _ = dreams.store_dream_entry(
        "A lighthouse, and my grandmother at the top of it.",
        when=datetime(2026, 2, 3, 4, 10))
    col = passages.model_collection(dreams.DREAM_COLLECTION)
    metas = col.get(include=["metadatas"])["metadatas"]
    assert {m["source_id"] for m in metas} == {entry_id, "dream:2026-02-01_x:0"}

    passages.drop(dreams.DREAM_COLLECTION)     # a rebuild from nothing...
    dreams.store_dream_entry("Late: the lighthouse again.",
                             when=datetime(2026, 2, 4, 5, 0))
    metas = passages.model_collection(dreams.DREAM_COLLECTION).get(
        include=["metadatas"])["metadatas"]
    # ...brings back every dream from its files, not just the newest one
    assert len({m["source_id"] for m in metas}) == 3


def test_a_long_dream_is_read_to_its_end_and_shown_once(dream_dirs, monkeypatch):
    dreams = dream_dirs
    long = "\n\n".join([para("staircase", 60), "At the end: a green door that hummed."])
    dreams.store_dream_entry(long, when=datetime(2026, 2, 3, 4, 10))
    col = passages.model_collection(dreams.DREAM_COLLECTION)
    assert col.count() > 1
    monkeypatch.setattr(companion, "N_DREAM_HITS", 4)
    hits = companion.get_dream_hits("a green door that hummed")
    assert len(hits) == 1
    assert "green door" in hits[0] and hits[0].startswith("[2026-02-03] dream, continued")


# ---------------------------------------------------------------------------
# THE SEARCH TAB
# ---------------------------------------------------------------------------


def test_the_search_tab_finds_a_deep_match_and_shows_it(chroma, monkeypatch):
    import server
    col, *_ = write_chunks({
        "deep": "\n\n".join([para("job", 60), "Pickle the cat sat on the piano."]),
        "deep_2": para("trip", 5)})                  # the same entry, two chunks
    write_chunks({"other": para("garden", 8)}, date="2026-03-05", title="Other")
    passages.sync(col)
    monkeypatch.setitem(server.STATE, "collection", col)
    got = server.search_journal("where did the cat sit", mode="semantic")["results"]
    # One row per entry, however many chunks and passages matched.
    titles = [r["title"] for r in got]
    assert titles[0] == "A day" and len(titles) == len(set(titles))
    assert "Pickle the cat sat on the piano." in got[0]["snippet"]
