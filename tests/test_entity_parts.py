# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Part-of links (entity-identity plan, Phase 5): a piece of something is its
own entity, shown as "Parent / Name", instead of merged into it by a name
rule. Covers the path display, the index and doc fields, cycles, people,
the generic exemption, the one-time review (slash names and merge rules),
and the companion finding a part by its own name.
"""
import json

import companion
import entities


def _setup(tmp_path, monkeypatch, raws):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "RAW_DIR", raw)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
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


def _project(name, obs="worked on it"):
    return {"name": name, "domain": "work", "status": "active", "observations": [obs]}


def _place(name, obs="went there"):
    return {"name": name, "kind": "place", "observations": [obs]}


RAWS = {
    "2026-03-01_a": {"projects": [_project("Coda"), _project("tabs", "built the tabs")]},
    "2026-03-02_b": {"projects": [_project("Coda"), _project("tab component")]},
    "2026-03-03_c": {"places": [_place("Harbor Town"), _place("the beach", "swam")]},
}


def test_a_part_shows_its_path(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["part_of"]["project:tabs"] = "project:Coda"
    curation["merge"]["project:tab component"] = "tabs"
    index = _build(tmp_path, curation)
    part = index["Coda / tabs"]
    assert part["base"] == "tabs" and part["part_of"] == "Coda"
    assert part["mentions"] == 2 and part["aliases"] == ["tab component"]
    assert index["Coda"]["parts"] == ["Coda / tabs"]
    assert "tabs" not in index
    doc = (tmp_path / "projects" / "tabs.md").read_text(encoding="utf-8")
    assert "name: Coda / tabs" in doc and "part of: Coda" in doc
    assert "parts: Coda / tabs" in (tmp_path / "projects" / "coda.md").read_text(encoding="utf-8")
    # rules still key on the plain name
    assert entities.index_key(index, "Coda / tabs") == "project:tabs"


def test_the_parent_is_followed_through_a_rename(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["part_of"]["project:tabs"] = "project:Coda"
    curation["rename"]["project:coda"] = "Coda App"
    index = _build(tmp_path, curation)
    assert index["Coda App / tabs"]["part_of"] == "Coda App"


def test_hidden_path_and_missing_parent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["part_of"]["project:tabs"] = "project:Coda"
    curation["hide_path"].append("project:tabs")
    curation["part_of"]["project:tab component"] = "project:Gone"
    index = _build(tmp_path, curation)
    assert index["tabs"]["part_of"] == "Coda"
    assert "part_of" not in index["tab component"]


def test_cycles_are_caught(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["part_of"]["project:tabs"] = "project:Coda"
    assert entities.part_would_cycle(curation, "project:coda", "project", "tabs")
    assert entities.part_would_cycle(curation, "project:coda", "project", "Coda")
    assert not entities.part_would_cycle(curation, "project:tab component", "project", "tabs")


def test_people_are_never_parts(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-03-01_a": {
        "people": [{"name": "Mika", "relationship": "friend", "observations": ["ran"]}],
        "projects": [_project("Coda")]}})
    curation = entities.load_curation()
    curation["part_of"]["person:mika"] = "project:Coda"
    index = _build(tmp_path, curation)
    assert "part_of" not in index["Mika"]
    # and retyping a part to a person drops its link
    curation = entities.load_curation()
    curation["part_of"]["project:coda"] = "project:Other"
    entities.retype_rule(curation, "project:coda", "person", "Coda")
    assert "person:coda" not in curation["part_of"] and "project:coda" not in curation["part_of"]


def test_a_part_is_not_generic(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-03-01_a": {"places": [
        _place("Mika's apartment"), _place("office", "worked from the office")]}})
    assert _build(tmp_path)["office"].get("generic")
    curation = entities.load_curation()
    curation["part_of"]["place:office"] = "place:Mika's apartment"
    assert "generic" not in _build(tmp_path, curation)["Mika's apartment / office"]


def test_review_lists_slash_names_and_merges(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["merge"]["project:tabs"] = "Coda"
    curation["merge"]["project:tab component"] = "Coda"
    curation["merge"]["place:the beach"] = "Harbor Town / Beach"
    index = _build(tmp_path, curation)
    review = entities.parts_review(index)
    assert review["slash"] == [{"name": "Harbor Town / Beach", "parent": "Harbor Town",
                                "part": "Beach", "mentions": 1, "joins": ""}]
    coda = next(t for t in review["targets"] if t["target"] == "Coda")
    assert [s["key"] for s in coda["sources"]] == ["project:tab component", "project:tabs"]
    assert coda["sources"][1]["said"] == "built the tabs"


def test_applying_the_review(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    curation = entities.load_curation()
    curation["merge"]["project:tabs"] = "Coda"
    curation["merge"]["place:the beach"] = "Harbor Town / Beach"
    curation["reviewed"].append("place:harbor town / beach")
    index = _build(tmp_path, curation)
    curation = entities.load_curation()
    curation["merge"]["place:beach"] = "Harbor Town / Beach"
    entities.make_part(curation, "project:tabs", "project", "Coda")
    assert entities.split_slash_name(curation, index, "Harbor Town / Beach", "Harbor Town") == "Beach"
    assert "place:beach" not in curation["merge"]  # would point at itself
    assert curation["part_of"]["place:beach"] == "place:Harbor Town"
    index = _build(tmp_path, curation)
    assert index["Coda / tabs"]["mentions"] == 1
    assert index["Coda"]["mentions"] == 2
    beach = index["Harbor Town / Beach"]
    assert beach["base"] == "Beach" and beach["aliases"] == ["the beach"]
    assert beach["reviewed"]  # its own rules followed it


def test_companion_finds_a_part_by_its_own_name():
    index = {"Coda / tabs": {"type": "project", "base": "tabs", "mentions": 2, "aliases": []},
             "Coda": {"type": "project", "mentions": 9, "aliases": []}}
    assert companion.match_entities("fixed the tabs today", index) == ["Coda / tabs"]
    assert companion.resolve_entity(index, "tabs") == "Coda / tabs"
