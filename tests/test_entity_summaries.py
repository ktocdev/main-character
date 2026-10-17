# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Entity summaries: a short profile for each entity with enough
observations, written at close, kept when the author edits it, and put at
the top of the entity's doc so the companion reads it first.
"""
import json
from types import SimpleNamespace

import pytest

import caps
import entities
import entity_summaries as es


@pytest.fixture
def graph(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "RAW_DIR", raw)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(entities, "SUMMARY_FILE", tmp_path / "summaries.json")
    monkeypatch.setattr(entities, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(entities, "_conversation_lookup", lambda: {})
    return raw


def _entry(raw, stem, **groups):
    (raw / f"{stem}.json").write_text(json.dumps(groups), encoding="utf-8")


def _mika(raw, entries=3, per=4, start=1):
    for i in range(start, start + entries):
        _entry(raw, f"2026-09-{i:02d}_e", people=[{
            "name": "Mika", "relationship": "friend",
            "observations": [f"day {i} note {j}" for j in range(per)]}])


def _index():
    records = [{"date": p.stem[:10], "title": p.stem, "key": p.stem,
                "entities": json.loads(p.read_text(encoding="utf-8"))}
               for p in sorted(entities.RAW_DIR.glob("*.json"))]
    return entities.build_entity_docs(records)


class FakeClient:
    def __init__(self, reply="Mika is a close friend of Jordan's.", fail_on=None):
        self.prompts, self.reply, self.fail_on = [], reply, fail_on
        self.messages = self

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        if self.fail_on and self.fail_on in prompt:
            raise caps.CapExceeded("monthly cap reached")
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=f"  {self.reply}\n")])


def test_qualifies_on_observations_and_entries_both(graph):
    _mika(graph, entries=3, per=4)                                   # 12 over 3
    _entry(graph, "2026-09-10_long", places=[{"name": "Harbor Town",
           "observations": [f"o{j}" for j in range(20)]}])           # 20 over 1
    _entry(graph, "2026-09-11_x", projects=[{"name": "Coda", "observations": ["a", "b"]}])
    _entry(graph, "2026-09-12_x", projects=[{"name": "Coda", "observations": ["c"]}])
    _entry(graph, "2026-09-13_x", projects=[{"name": "Coda", "observations": ["d"]}])  # 4 over 3
    names = {c["name"] for c in es.candidates(_index())}
    assert names == {"Mika"}


def test_generic_and_unsorted_never_qualify():
    slot = {"obs": [("2026-01-01", "x")] * 12, "entries": {"a", "b", "c"}}
    assert es.qualifies({"type": "person"}, slot)
    assert not es.qualifies({"type": "place", "generic": True}, slot)
    assert not es.qualifies({"type": "person", "unsorted": True}, slot)


def test_status_missing_current_due_changed():
    rec = {"hash": "h1", "count": 20}
    assert es.status(None, 20, "h1") == "missing"
    assert es.status(rec, 20, "h1") == "current"
    assert es.status(rec, 22, "h2") == "changed"   # two new: not yet
    assert es.status(rec, 23, "h2") == "due"       # three new
    assert es.status(rec, 20, "h2") == "changed"   # edited or deleted, same count
    # a small one is due sooner: a quarter of 4 is one observation
    assert es.status({"hash": "h1", "count": 4}, 5, "h2") == "due"
    # the author's wording is never rewritten unasked
    assert es.status({**rec, "edited": True}, 40, "h2") == "changed"


def test_refresh_writes_due_ones_and_the_doc_leads_with_it(graph, monkeypatch):
    _mika(graph)
    client = FakeClient()
    monkeypatch.setattr(es, "get_client", lambda: client)
    result = es.refresh(_index())
    assert result == {"done": ["Mika"], "failed": []}
    stored = entities.load_summaries()["person:mika"]
    assert stored["text"] == "Mika is a close friend of Jordan's."
    assert stored["count"] == 12 and not stored["edited"]

    index = _index()
    doc = (entities.ENTITY_DIR / index["Mika"]["path"]).read_text(encoding="utf-8")
    body = doc.split("---", 2)[2]
    assert body.index("## Summary") < body.index("## Observations") < body.index("### 2026-09-01")
    assert "Mika is a close friend" in body

    # current now: a second refresh calls nothing
    assert es.refresh(index) == {"done": [], "failed": []}
    assert len(client.prompts) == 1


def test_no_summary_leaves_the_doc_as_it_was(graph):
    # the demo journal has none: its docs (and the close replay's hashes)
    # must not change
    _mika(graph)
    doc = (entities.ENTITY_DIR / _index()["Mika"]["path"]).read_text(encoding="utf-8")
    assert "## Summary" not in doc and "## Observations" not in doc


def test_page_status_agrees_with_the_close(graph, monkeypatch):
    # the page hashes list_observations' answer, the close the raw cache;
    # right after a write they have to agree, or every page says "changed"
    _mika(graph)
    monkeypatch.setattr(es, "get_client", lambda: FakeClient())
    es.refresh(_index())
    index = _index()
    page = es.for_entity(index, "Mika", entities.list_observations("person", "Mika"))
    assert page["status"] == "current" and page["qualifies"] and page["count"] == 12
    _mika(graph, entries=1, per=3, start=4)
    index = _index()
    page = es.for_entity(index, "Mika", entities.list_observations("person", "Mika"))
    assert page["status"] == "due"


def test_an_edit_sticks_and_undo_restores(graph, monkeypatch):
    _mika(graph)
    monkeypatch.setattr(es, "get_client", lambda: FakeClient())
    es.refresh(_index())
    index = _index()
    change = es.edit(index, "Mika", "  Mika, in my words.  ")
    entities.record_change("edit", "summary", change["before"], change["after"], change["key"])
    assert entities.load_summaries()["person:mika"]["text"] == "Mika, in my words."
    _mika(graph, entries=5, per=4, start=4)
    assert es.refresh(_index()) == {"done": [], "failed": []}   # edited: left alone
    entities.undo()
    assert entities.load_summaries()["person:mika"]["text"] == "Mika is a close friend of Jordan's."
    entities.redo()
    assert entities.load_summaries()["person:mika"]["edited"]


def test_a_cap_stops_the_run_and_keeps_whats_written(graph, monkeypatch):
    _mika(graph)
    for i in range(1, 4):
        _entry(graph, f"2026-09-{i:02d}_z", places=[{"name": "Harbor Town",
               "observations": [f"harbor {i}.{j}" for j in range(4)]}])
    client = FakeClient(fail_on="Harbor Town")
    monkeypatch.setattr(es, "get_client", lambda: client)
    with pytest.raises(caps.CapExceeded):
        es.refresh(_index())
    # people sort before places in the index: Mika was written before the cap
    assert set(entities.load_summaries()) == {"person:mika"}


def test_prompt_is_discreet_and_kind_aware(graph):
    _mika(graph)
    _entry(graph, "2026-09-05_r", people=[{"name": "Dev", "relationship": "coworker",
           "observations": [f"d{j}" for j in range(4)]}])
    for i in range(1, 4):
        _entry(graph, f"2026-09-{i:02d}_c", projects=[{"name": "Coda",
               "observations": [f"coda {j}" for j in range(4)]}])
    by_name = {c["name"]: c for c in es.candidates(_index())}
    person = es._prompt(by_name["Mika"])
    for rule in ("health and medical", "sexuality", "money trouble", "legal matters",
                 "drinking or drug use", "without blame", "Don't dwell", "Accurate, not flattering"):
        assert rule in person
    assert "real person" in person and "friend" in person
    assert "[2026-09-01] day 1 note 0" in person and "The latest is from 2026-09-03" in person
    project = es._prompt(by_name["Coda"])
    assert "real person" not in project and "This is a project" in project


def test_long_histories_keep_the_newest(graph, monkeypatch):
    monkeypatch.setattr(es, "INPUT_CHARS", 200)
    _mika(graph, entries=6, per=4)
    cand = next(c for c in es.candidates(_index()) if c["name"] == "Mika")
    text = es._observation_text(cand["obs"])
    assert "day 6 note 3" in text and "day 1 note 0" not in text
    assert text.startswith("(") and "earlier observations, from 2026-09-01" in text


def test_rename_carries_the_summary(graph):
    entities.save_summaries({"person:mika": {"text": "t", "hash": "h", "count": 1}})
    es.move("person:mika", "person:mika s.")
    assert set(entities.load_summaries()) == {"person:mika s."}
    # never over one that's already there
    entities.save_summaries({"person:a": {"text": "a"}, "person:b": {"text": "b"}})
    es.move("person:a", "person:b")
    assert entities.load_summaries()["person:b"]["text"] == "b"


def test_estimate_counts_only_whats_needed(graph, monkeypatch):
    _mika(graph)
    est = es.estimate(_index())
    assert est["count"] == 1 and est["missing"] == 1 and est["estimate"] >= 0
    monkeypatch.setattr(es, "get_client", lambda: FakeClient())
    es.refresh(_index())
    assert es.estimate(_index())["count"] == 0


def test_a_close_writes_a_few_due_ones_first(graph, monkeypatch):
    _mika(graph)
    for i in range(1, 4):
        _entry(graph, f"2026-09-{i:02d}_z", places=[{"name": "Harbor Town",
               "observations": [f"harbor {i}.{j}" for j in range(6)]}])
    monkeypatch.setattr(es, "get_client", lambda: FakeClient())
    # without summaries, the best-known goes first: Harbor Town has 18
    assert es.refresh(_index(), limit=1)["done"] == ["Harbor Town"]
    es.refresh(_index())
    _mika(graph, entries=1, per=4, start=4)     # Mika is due now
    _entry(graph, "2026-09-05_n", projects=[{"name": "Coda", "observations": [f"c{j}" for j in range(30)]}])
    for i in (6, 7):
        _entry(graph, f"2026-09-0{i}_n", projects=[{"name": "Coda", "observations": ["more"]}])
    # a due one beats a missing one, however well known
    assert es.refresh(_index(), limit=1)["done"] == ["Mika"]
