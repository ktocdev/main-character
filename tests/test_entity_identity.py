# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Same-name identity (entity-identity plan, Phase 3): two people who share a
name become "Dev · work" and "Dev · friend". A mention goes to one by its
record's qualifier; one without a qualifier goes to "Dev · ?" (unsorted)
instead of being given to either. Covers resolution, the split and sort
operation with its one-step undo, renaming a qualifier, groups, the
extraction hints, and the companion loading every half of a bare name.
"""
import json
from types import SimpleNamespace

import companion
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
        (raw / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def _build(tmp_path, curation=None):
    if curation is not None:
        entities.save_curation(curation)
    records = [
        {"date": p.stem[:10], "title": p.stem, "key": p.stem,
         "entities": json.loads(p.read_text(encoding="utf-8"))}
        for p in sorted((tmp_path / "raw").glob("*.json"))
    ]
    return entities.build_entity_docs(records)


def _person(name, relationship, obs, qualifier=None):
    rec = {"name": name, "relationship": relationship, "observations": obs}
    if qualifier is not None:
        rec["qualifier"] = qualifier
    return rec


RAWS = {
    "2026-03-01_a": {"people": [_person("Dev", "coworker", ["standup with Dev", "Dev reviewed my PR"])]},
    "2026-03-02_b": {"people": [_person("Dev", "friend", ["dinner with Dev"])]},
    "2026-03-03_c": {"people": [_person("Dev", "coworker", ["Dev fixed the build"])]},
}


def _raw(tmp_path, stem):
    return json.loads((tmp_path / "raw" / f"{stem}.json").read_text(encoding="utf-8"))


def _picks(tmp_path, name, texts):
    return [o for o in entities.list_observations("person", name) if o["text"] in texts]


def test_resolution_by_qualifier():
    curation = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in entities._CURATION_DEFAULTS.items()}
    curation["variants"]["person:dev"] = ["work", "friend"]
    curation["merge"]["person:devin"] = "Dev"
    apply = entities.apply_curation
    assert apply(curation, "person", "Dev", "", "work") == ("person", "Dev · work", True)
    assert apply(curation, "person", "Dev", "", "Friend") == ("person", "Dev · friend", True)
    assert apply(curation, "person", "Dev") == ("person", "Dev · ?", True)
    assert apply(curation, "person", "Dev", "", "gym") == ("person", "Dev · ?", True)
    # a merged spelling is routed the same way
    assert apply(curation, "person", "Devin", "", "work") == ("person", "Dev · work", True)
    # a qualifier on a name that isn't split means nothing
    assert apply(curation, "person", "Mika", "", "work") == ("person", "Mika", False)
    # rules on one half act on that half only
    curation["delete"].append("person:dev · friend")
    assert apply(curation, "person", "Dev", "", "friend") is None
    assert apply(curation, "person", "Dev", "", "work")[1] == "Dev · work"


def test_first_split_names_both_halves(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    entities.save_groups([{"name": "Team", "parent": "", "members": ["Dev"]},
                          {"name": "Pals", "parent": "", "members": ["Dev"]}])
    index = _build(tmp_path)
    curation = entities.load_curation()
    curation["reviewed"].append("person:dev")
    entities.save_curation(curation)
    picks = _picks(tmp_path, "Dev", {"dinner with Dev", "Dev reviewed my PR"})
    entities.split_entity(index, "Dev", picks, "friend", rest="work", groups_to={"Pals": "friend"})
    index = _build(tmp_path)

    assert "Dev" not in index
    work, friend = index["Dev · work"], index["Dev · friend"]
    assert (work["mentions"], friend["mentions"]) == (2, 2)  # entry a is in both now
    assert work["variant_of"] == "Dev" and work["qualifier"] == "work"
    assert work["aliases"] == ["Dev"] and friend["aliases"] == ["Dev"]
    assert not work["reviewed"] and not friend["reviewed"]
    assert work["groups"] == ["Team"] and friend["groups"] == ["Pals"]
    # entry a's record was split in two, each with its own qualifier
    a = _raw(tmp_path, "2026-03-01_a")["people"]
    assert [(r["qualifier"], r["observations"]) for r in a] == [
        ("work", ["standup with Dev"]), ("friend", ["Dev reviewed my PR"])]
    assert entities.load_curation()["variants"] == {"person:dev": ["friend", "work"]}

    # one undo puts back the files, the rules and the groups
    entities.undo()
    assert _raw(tmp_path, "2026-03-01_a") == RAWS["2026-03-01_a"]
    assert entities.load_curation()["variants"] == {}
    assert "person:dev" in entities.load_curation()["reviewed"]
    assert entities.load_groups()[1]["members"] == ["Dev"]


def test_new_bare_mentions_wait_in_unsorted(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    index = _build(tmp_path)
    entities.split_entity(index, "Dev", _picks(tmp_path, "Dev", {"dinner with Dev"}), "friend", rest="work")
    (tmp_path / "raw" / "2026-03-04_d.json").write_text(json.dumps(
        {"people": [_person("Dev", "friend", ["Dev came to karaoke", "sang with Dev"], "")]}), encoding="utf-8")
    index = _build(tmp_path)
    assert index["Dev · ?"]["unsorted"] and index["Dev · ?"]["mentions"] == 1
    assert "mixup" not in index["Dev · ?"]
    # sorting is the same split, no rest needed
    entities.split_entity(index, "Dev · ?", _picks(tmp_path, "Dev · ?", {"Dev came to karaoke", "sang with Dev"}),
                          "friend")
    index = _build(tmp_path)
    assert "Dev · ?" not in index and index["Dev · friend"]["mentions"] == 2


def test_a_split_needs_two_names(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    index = _build(tmp_path)
    picks = _picks(tmp_path, "Dev", {"dinner with Dev"})
    for q, rest in (("friend", ""), ("friend", "friend"), ("?", "work"), ("", "work")):
        try:
            entities.split_entity(index, "Dev", picks, q, rest=rest)
        except ValueError:
            continue
        raise AssertionError((q, rest))


def test_renaming_a_qualifier_rewrites_its_records(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    index = _build(tmp_path)
    entities.split_entity(index, "Dev", _picks(tmp_path, "Dev", {"dinner with Dev"}), "friend", rest="work")
    index = _build(tmp_path)
    entities.rename_qualifier(index, "Dev · work", "Orbit")
    index = _build(tmp_path)
    assert index["Dev · Orbit"]["mentions"] == 2 and "Dev · work" not in index
    assert _raw(tmp_path, "2026-03-03_c")["people"][0]["qualifier"] == "Orbit"
    entities.undo()
    assert _raw(tmp_path, "2026-03-03_c")["people"][0]["qualifier"] == "work"


def test_hints_for_extraction(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    index = _build(tmp_path)
    entities.split_entity(index, "Dev", _picks(tmp_path, "Dev", {"dinner with Dev"}), "friend", rest="work")
    _build(tmp_path)
    assert entities.known_people_hint() == ["Dev"]
    assert entities.known_variants_hint() == "Dev: work (coworker), friend (friend)"

    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"]
            reply = {"people": [], "projects": [], "places": [], "things": []}
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                  {"date": "2026-03-05", "title": "t", "text": "x"},
                                  known_people=["Dev"], variants=entities.known_variants_hint())
    assert 'set "qualifier"' in seen["prompt"] and "Dev: work (coworker)" in seen["prompt"]
    person = entities.EXTRACTION_SCHEMA["properties"]["people"]["items"]
    assert "qualifier" in person["required"]


def test_duplicates_leave_split_halves_alone():
    a = {"variant_of": "Dev", "qualifier": "work"}
    assert entities._split_apart(a, {"variant_of": "Dev", "qualifier": "friend"})
    assert not entities._split_apart(a, {})


def test_companion_loads_every_half():
    index = {
        "Dev · work": {"type": "person", "variant_of": "Dev", "qualifier": "work", "mentions": 9, "aliases": ["Dev"]},
        "Dev · friend": {"type": "person", "variant_of": "Dev", "qualifier": "friend", "mentions": 3, "aliases": ["Dev"]},
        "Dev · ?": {"type": "person", "variant_of": "Dev", "qualifier": "?", "unsorted": True, "mentions": 1, "aliases": ["Dev"]},
    }
    assert companion.match_entities("saw Dev today", index) == ["Dev · work", "Dev · friend"]
    assert companion.match_entities("Dev, my friend, called", index) == ["Dev · friend", "Dev · work"]
    # a bare name means neither on its own
    assert companion.resolve_entity(index, "Dev") is None
    assert companion.resolve_entity(index, "dev · friend") == "Dev · friend"
