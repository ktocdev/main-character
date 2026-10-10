# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Triage shows every observation with the attribute its own entry gave it
(entity-identity plan, Phase 1). Two people under one name tend to show up
as "coworker" in one entry and "friend" in another, so list_observations
has to carry each record's attribute rather than the entity's latest one.
"""
import json

import pytest

import entities


def _raw(tmp_path, stem, data):
    (tmp_path / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def test_each_observation_carries_its_own_records_attribute(tmp_path, monkeypatch):
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "_conversation_lookup", lambda: {
        "a": ("2026-03-04", "standup"), "b": ("2026-06-10", "dinner"),
    })
    _raw(tmp_path, "a", {"people": [
        {"name": "Tobias", "relationship": "coworker", "observations": ["ran the standup"]}]})
    _raw(tmp_path, "b", {"people": [
        {"name": "tobias", "relationship": "friend", "observations": ["cooked pasta", "brought wine"]}],
        "places": [{"name": "Edelweiss", "kind": "bar", "observations": ["after dinner"]}]})

    obs = entities.list_observations("person", "Tobias")
    assert [(o["date"], o["attr"], o["text"]) for o in obs] == [
        ("2026-03-04", "coworker", "ran the standup"),
        ("2026-06-10", "friend", "cooked pasta"),
        ("2026-06-10", "friend", "brought wine"),
    ]
    # places carry their own field (kind), and a missing one is an empty string
    assert entities.list_observations("place", "Edelweiss")[0]["attr"] == "bar"
    _raw(tmp_path, "a", {"projects": [{"name": "Tabs", "observations": ["shipped"]}]})
    assert entities.list_observations("project", "Tabs")[0]["attr"] == ""


def test_first_observations_matches_list_observations_in_one_pass(tmp_path, monkeypatch):
    # the generic-names list and the things review show one line per
    # entity; one pass gives each the line list_observations would lead with
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "_conversation_lookup", lambda: {})
    _raw(tmp_path, "2026-06-10_dinner", {"people": [
        {"name": "tobias", "observations": ["cooked pasta"]}],
        "places": [{"name": "dive bar", "observations": ["after dinner"]}]})
    _raw(tmp_path, "2026-03-04_standup", {"people": [
        {"name": "Tobias", "observations": []},
        {"name": "Tobias", "observations": ["ran the standup"]}],
        "projects": [{"name": "Tabs", "observations": ["shipped"]}]})
    (tmp_path / "curation.json").write_text(json.dumps({
        "rename": {"person:tobias": "Tobias"}, "merge": {"project:tabs": "Coda"}}), encoding="utf-8")

    firsts = entities.first_observations()
    for kind, name in (("person", "Tobias"), ("place", "dive bar"), ("project", "Coda")):
        assert firsts[(kind, name.lower())] == entities.list_observations(kind, name)[0]["text"]
    assert firsts[("person", "tobias")] == "ran the standup"


def test_load_curation_never_hands_out_its_defaults(tmp_path, monkeypatch):
    # an older curation.json without a list field used to get the module's
    # own default list, so an append leaked into every later load
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    (tmp_path / "curation.json").write_text(json.dumps({"delete": ["Place:Dive Bar"]}), encoding="utf-8")
    entities.load_curation()["not_mixed"].append("person:dev")
    assert entities.load_curation()["not_mixed"] == []
    assert entities._CURATION_DEFAULTS["not_mixed"] == []
    # stored keys are lowercase; an old hand-edited one still resolves
    assert entities.load_curation()["delete"] == ["place:dive bar"]
    assert entities.apply_curation(entities.load_curation(), "place", "dive bar") is None


def _build(tmp_path, monkeypatch, records, curation=None):
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    if curation:
        (tmp_path / "curation.json").write_text(json.dumps(curation), encoding="utf-8")
    return entities.build_entity_docs(records)


def _rec(date, **groups):
    return {"date": date, "title": date, "entities": groups}


def test_two_kinds_sharing_a_name_both_stay_in_the_index(tmp_path, monkeypatch):
    # Juniper the person (3 mentions) and a stray place "Juniper" (1): the place
    # used to overwrite the person in the name-keyed index.
    records = [
        _rec(f"2026-09-0{i}", people=[{"name": "Juniper", "observations": [f"o{i}"]}])
        for i in (1, 2, 3)
    ] + [_rec("2026-09-08", places=[{"name": "Juniper", "observations": []}])]
    index = _build(tmp_path, monkeypatch, records, {"reviewed": ["person:juniper"]})
    assert index["Juniper"]["type"] == "person" and index["Juniper"]["mentions"] == 3
    assert index["Juniper"]["reviewed"] and "base" not in index["Juniper"]
    assert index["Juniper · place"] == {**index["Juniper · place"], "type": "place", "base": "Juniper", "mentions": 1}
    assert entities.index_key(index, "Juniper · place") == "place:juniper"
    assert "name: Juniper · place" in (tmp_path / index["Juniper · place"]["path"]).read_text(encoding="utf-8")


def test_rules_on_the_labelled_one_use_its_plain_name(tmp_path, monkeypatch):
    records = [
        _rec("2026-01-01", projects=[{"name": "karaoke", "observations": ["a"]}]),
        _rec("2026-01-02", projects=[{"name": "karaoke", "observations": ["b"]}]),
        _rec("2026-01-03", places=[{"name": "karaoke", "observations": ["c"]}]),
    ]
    index = _build(tmp_path, monkeypatch, records)
    assert set(index) == {"karaoke", "karaoke · place"}
    # merging the place into the project (what the server writes) leaves one
    key = entities.index_key(index, "karaoke · place")
    index = _build(tmp_path, monkeypatch, records, {"merge": {key: "project:karaoke"}})
    assert set(index) == {"karaoke"} and index["karaoke"]["mentions"] == 3


def test_setting_one_records_relationship_leaves_the_others(tmp_path, monkeypatch):
    # the entity page relabels one entry's "as pet" without touching the
    # other entries under the same name; it's a hand edit, so it's flagged
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "_conversation_lookup", lambda: {
        "a": ("2026-02-25", "trip"), "b": ("2026-03-01", "vet")})
    _raw(tmp_path, "a", {"animals": [
        {"name": "Biscuit", "relationship": "pet", "observations": ["hated the singing"]}]})
    _raw(tmp_path, "b", {"animals": [
        {"name": "Biscuit", "relationship": "pet", "observations": ["got a checkup"]}]})

    entities.set_record_relationship("a.json", "animals", 0, 0, "  pet cat ")
    assert [o["attr"] for o in entities.list_observations("animal", "Biscuit")] == ["pet cat", "pet"]
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["edited"] is True
    assert "edited" not in json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))

    entities.set_record_relationship("a.json", "animals", 0, 0, "")
    assert entities.list_observations("animal", "Biscuit")[0]["attr"] == ""


def test_only_people_and_animals_take_a_relationship(tmp_path, monkeypatch):
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path)
    _raw(tmp_path, "a", {"places": [{"name": "Edelweiss", "kind": "bar", "observations": ["x"]}]})
    with pytest.raises(ValueError):
        entities.set_record_relationship("a.json", "places", 0, 0, "friend")


def test_an_edit_only_reaches_the_entity_lists(tmp_path, monkeypatch):
    # the group comes from the request: anything but the five lists is
    # refused before the file is read, never written as a hand edit
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path)
    _raw(tmp_path, "a", {"edited": True, "people": [
        {"name": "Dev", "relationship": "coworker", "observations": ["ran the standup"]}]})
    before = (tmp_path / "a.json").read_text(encoding="utf-8")
    for group in ("edited", "summary", ""):
        with pytest.raises(ValueError):
            entities.edit_observation("a.json", group, 0, 0, "changed")
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == before
    entities.edit_observation("a.json", "people", 0, 0, "changed")
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))["people"][0]["observations"] == ["changed"]
