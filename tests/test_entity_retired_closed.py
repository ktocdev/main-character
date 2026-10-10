# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Retired people closed to bare mentions (entity-identity plan, Phase 7's
deferred rule): once Marisol is retired, a plain "Marisol" in a later entry
doesn't reopen her profile. It waits in "Marisol · ?" to be sorted: the
retired one after all, someone new (a split, with the retired one as
"Marisol · past"), or un-retire. Earlier entries, a "past" qualifier from
extraction and mentions kept by hand still reach her.
"""
import json
from types import SimpleNamespace

import entities


def _setup(tmp_path, monkeypatch, raws):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "RAW_DIR", raw)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(entities, "HISTORY_FILE", tmp_path / "history.json")
    for stem, data in raws.items():
        _write(tmp_path, stem, data)


def _write(tmp_path, stem, data):
    (tmp_path / "raw" / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def _build(tmp_path, curation=None):
    if curation is not None:
        entities.save_curation(curation)
    records = [
        {"date": p.stem[:10], "title": p.stem, "key": p.stem,
         "entities": json.loads(p.read_text(encoding="utf-8"))}
        for p in sorted((tmp_path / "raw").glob("*.json"))
    ]
    return entities.build_entity_docs(records)


def _person(name, obs, relationship="coworker", qualifier=None):
    rec = {"name": name, "relationship": relationship, "observations": obs}
    if qualifier is not None:
        rec["qualifier"] = qualifier
    return rec


RAWS = {
    "2026-03-01_a": {"people": [_person("Marisol", ["Marisol ran the standup"])]},
    "2026-03-04_b": {"people": [_person("Marisol", ["lunch with Marisol"])]},
}


def _retire(tmp_path):
    cur = entities.load_curation()
    cur["retired"].append("person:marisol")
    return _build(tmp_path, cur)


def test_retiring_records_the_last_mention_and_moves_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _build(tmp_path)
    index = _retire(tmp_path)
    assert entities.load_curation()["retired_through"] == {"person:marisol": "2026-03-04"}
    assert index["Marisol"]["mentions"] == 2 and "Marisol · ?" not in index


def test_a_later_bare_mention_waits_to_be_sorted(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _retire(tmp_path)
    _write(tmp_path, "2026-05-01_c", {"people": [_person("Marisol", ["met Marisol at the gym"], "friend")]})
    # an older entry written late still reaches her
    _write(tmp_path, "2026-02-10_d", {"people": [_person("Marisol", ["Marisol's first day"])]})
    index = _build(tmp_path)
    assert index["Marisol"]["mentions"] == 3 and index["Marisol"]["retired"] == "self"
    bucket = index["Marisol · ?"]
    assert bucket["unsorted"] and bucket["closed"] and bucket["variant_of"] == "Marisol"
    assert bucket["mentions"] == 1 and "retired" not in bucket
    # each has its own doc; a slug alone would give both people/marisol.md
    assert bucket["path"] != index["Marisol"]["path"]
    assert "mentions: 3" in (tmp_path / index["Marisol"]["path"]).read_text(encoding="utf-8")
    # not a known name to nudge extraction toward
    assert entities.known_people_hint() == []


def test_past_qualifier_and_kept_entries_reach_the_retired_one(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _retire(tmp_path)
    _write(tmp_path, "2026-05-01_c", {"people": [_person("Marisol", ["Marisol emailed from the old team"], qualifier="past")]})
    _write(tmp_path, "2026-05-02_d", {"people": [_person("Marisol", ["ran into Marisol"])]})
    index = _build(tmp_path)
    assert index["Marisol"]["mentions"] == 3 and index["Marisol · ?"]["mentions"] == 1

    cur = entities.load_curation()
    picks = entities.list_observations("person", "Marisol · ?")
    assert entities.keep_with_retired(cur, index, "Marisol · ?", picks) == "Marisol"
    index = _build(tmp_path, cur)
    assert index["Marisol"]["mentions"] == 4 and "Marisol · ?" not in index
    assert index["Marisol"]["retired"] == "self"


def test_un_retiring_opens_the_profile_again(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _retire(tmp_path)
    _write(tmp_path, "2026-05-01_c", {"people": [_person("Marisol", ["ran into Marisol"])]})
    assert "Marisol · ?" in _build(tmp_path)
    cur = entities.load_curation()
    cur["retired"] = []
    # the build notices the cutoff is stale and builds again
    index = _build(tmp_path, cur)
    assert index["Marisol"]["mentions"] == 3 and "Marisol · ?" not in index
    assert entities.load_curation()["retired_through"] == {}


def test_a_retired_group_closes_its_members(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    entities.save_groups([{"name": "Old job", "parent": "", "members": ["Marisol"], "retired": True}])
    _build(tmp_path)
    _write(tmp_path, "2026-05-01_c", {"people": [_person("Marisol", ["ran into Marisol"])]})
    index = _build(tmp_path)
    assert index["Marisol"]["retired"] == "Old job" and index["Marisol · ?"]["closed"]


def test_someone_new_splits_off_and_the_retired_one_becomes_past(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _retire(tmp_path)
    _write(tmp_path, "2026-05-01_c", {"people": [_person("Marisol", ["met Marisol at the gym"], "friend")]})
    _write(tmp_path, "2026-05-02_d", {"people": [_person("Marisol", ["ran into Marisol"])]})
    index = _build(tmp_path)
    picks = [o for o in entities.list_observations("person", "Marisol · ?")
             if o["text"] == "met Marisol at the gym"]
    entities.split_entity(index, "Marisol · ?", picks, "gym")
    index = _build(tmp_path)
    assert index["Marisol · past"]["mentions"] == 2 and index["Marisol · past"]["retired"] == "self"
    assert index["Marisol · gym"]["mentions"] == 1 and "retired" not in index["Marisol · gym"]
    # the one not picked still waits, now as a split name's unsorted mention
    assert index["Marisol · ?"]["mentions"] == 1 and not index["Marisol · ?"].get("closed")
    cur = entities.load_curation()
    assert cur["variants"]["person:marisol"] == ["gym", "past"] and cur["retired_through"] == {}

    entities.undo()
    index = _build(tmp_path)
    assert index["Marisol"]["mentions"] == 2 and index["Marisol · ?"]["mentions"] == 2


def test_retired_names_reach_extraction(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    _retire(tmp_path)
    assert entities.known_retired_hint() == "Marisol (coworker)"

    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"]
            reply = {"people": [], "projects": [], "places": [], "things": []}
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                  {"date": "2026-05-01", "title": "t", "text": "x"},
                                  retired=entities.known_retired_hint())
    assert 'Set "qualifier" to "past"' in seen["prompt"] and "Marisol (coworker)" in seen["prompt"]
    entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                  {"date": "2026-05-01", "title": "t", "text": "x"})
    assert "past chapter" not in seen["prompt"]
