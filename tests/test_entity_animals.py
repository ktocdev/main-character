# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The fifth kind, "animal" (animals plan): pets and other people's animals,
filed apart from people. Covers the build, retyping a person in, the
generic detector, and that animals are never parts or parents.
"""
import json

import entities
from test_entity_things import _build, _setup


def _person(name, relationship, obs):
    return {"name": name, "relationship": relationship, "qualifier": "", "observations": [obs]}


def test_animals_are_a_kind_with_a_relationship():
    assert entities.GROUP_FOR_KIND["animal"] == "animals"
    assert ("animals", "animal", "relationship") in entities.KIND_FIELDS
    assert "animal" in entities.NO_PARTS


def test_extracted_animals_build_their_own_docs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"animals": [{"name": "Pip", "relationship": "pet cat",
                                      "observations": ["knocked a glass off the table"]}]},
        # a raw file from before the kind existed reads as before
        "2026-03-02_b": {"people": [_person("Dev", "friend", "had lunch")]},
    })
    index = _build(tmp_path)
    assert index["Pip"]["type"] == "animal"
    assert index["Pip"]["path"] == "animals/pip.md"
    assert index["Pip"]["attrs"] == {"pet cat": 1}
    doc = (tmp_path / "animals" / "pip.md").read_text(encoding="utf-8")
    assert "relationship: pet cat" in doc
    obs = entities.list_observations("animal", "Pip")
    assert obs[0]["group"] == "animals" and obs[0]["attr"] == "pet cat"


def test_a_pet_filed_as_a_person_retypes_to_an_animal(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"people": [_person("Pip", "pet", "asleep on the pillow")]},
        "2026-03-02_b": {"people": [_person("Pip", "pet cat", "brought in a mouse"),
                                    _person("Pipster", "pet", "zoomies at 3am")]},
    })
    curation = entities.load_curation()
    curation["merge"]["person:pipster"] = "Pip"
    curation["reviewed"].append("person:pip")
    curation["part_of"]["person:pip"] = "place:home"  # nonsense, and dropped
    entities.retype_rule(curation, "person:pip", "animal", "Pip")
    index = _build(tmp_path, curation)
    assert index["Pip"]["type"] == "animal"
    assert index["Pip"]["mentions"] == 3                 # Pipster came along
    assert index["Pip"]["aliases"] == ["Pipster"]
    assert index["Pip"]["reviewed"]
    # the labels it had as a person read right as an animal's
    assert index["Pip"]["attrs"] == {"pet": 2, "pet cat": 1}
    assert "mixup" not in index["Pip"]
    assert "animal:pip" not in curation["part_of"]
    assert not list((tmp_path / "people").glob("*.md"))

    entities.retype_rule(curation, "animal:pip", "person", "Pip")
    assert curation["retype"] == {}


def test_reassign_into_a_new_animal(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"people": [_person("Dev", "coworker", "his dog Biscuit chewed a shoe")]},
    })
    entities.reassign_observation("2026-03-01_a.json", "people", 0, 0, "animal", "Biscuit")
    data = json.loads((tmp_path / "raw" / "2026-03-01_a.json").read_text(encoding="utf-8"))
    assert data["animals"] == [{"name": "Biscuit", "relationship": "",
                                "observations": ["his dog Biscuit chewed a shoe"]}]
    assert data["people"] == []


def test_generic_animals():
    for name in ("the cat", "my dog", "a puppy", "the guinea pig", "pets"):
        assert entities.is_generic("animal", name), name
    for name in ("Pip", "mom's dog", "the neighbor's dog"):
        assert not entities.is_generic("animal", name), name


def test_animals_are_never_parts_or_parents(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {
            "animals": [{"name": "Pip", "relationship": "pet", "observations": ["x"]}],
            "places": [{"name": "Harbor Town", "kind": "city", "observations": ["y"]},
                       {"name": "Pip's bed", "kind": "home", "observations": ["z"]}],
        },
    })
    curation = entities.load_curation()
    curation["part_of"]["animal:pip"] = "place:Harbor Town"
    curation["part_of"]["place:pip's bed"] = "animal:Pip"
    index = _build(tmp_path, curation)
    assert "Pip" in index and "part_of" not in index["Pip"]
    assert "Pip's bed" in index and "part_of" not in index["Pip's bed"]


def test_the_review_suggests_people_whose_labels_are_mostly_animal():
    for label in ("pet", "pet cat", "mom's dog", "guinea pig", "Pet Guinea Pig", "cat"):
        assert entities.is_animal_label(label), label
    for label in ("dog walker", "friend", "catering manager", "petite friend"):
        assert not entities.is_animal_label(label), label


def test_animals_review_lists_strays_unsuggested(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"people": [_person("Pip", "pet", "asleep on the pillow"),
                                    _person("Dev", "coworker", "standup ran long"),
                                    _person("Mika", "friend", "brunch")]},
        "2026-03-02_b": {"people": [_person("Pip", "pet cat", "brought in a mouse"),
                                    _person("Dev", "dog", "his dog chewed a shoe"),
                                    _person("Dev", "coworker", "code review")]},
    })
    index = _build(tmp_path)
    review = entities.animals_review(index)
    assert [(a["name"], a["suggest"]) for a in review] == [("Pip", True), ("Dev", False)]
    assert review[0]["first"] == "asleep on the pillow"
    assert review[1]["labels"] == {"coworker": 2, "dog": 1}


def test_applying_the_review_is_one_undo(tmp_path, monkeypatch):
    import server
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"people": [_person("Pip", "pet", "asleep on the pillow"),
                                    _person("Biscuit", "pet dog", "fetch")]},
    })
    index = _build(tmp_path)
    monkeypatch.setitem(server.STATE, "entity_index", index)
    monkeypatch.setattr(server, "_rebuild",
                        lambda: server.STATE.__setitem__("entity_index", _build(tmp_path)))
    out = server.retype_animals(server.NamesIn(names=["Pip", "Biscuit", "nobody"]))
    assert sorted(out["retyped"]) == ["Biscuit", "Pip"]
    assert {server.STATE["entity_index"][n]["type"] for n in ("Pip", "Biscuit")} == {"animal"}
    assert entities.history_peek()["undo"].endswith("2 retyped to animals")
    entities.undo()
    assert entities.load_curation()["retype"] == {}


def test_extraction_asks_for_animals_and_names_the_known_ones(tmp_path, monkeypatch):
    from types import SimpleNamespace
    assert "animals" in entities.EXTRACTION_SCHEMA["required"]
    item = entities.EXTRACTION_SCHEMA["properties"]["animals"]["items"]
    assert item["required"] == ["name", "relationship", "observations"]

    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"animals": [{"name": "Pip", "relationship": "pet cat", "observations": ["x"]}],
                         "people": [_person("Dev", "friend", "y")]},
    })
    _build(tmp_path)
    assert entities.known_animals_hint() == ["Pip"]
    reply = {"people": [], "projects": [], "places": [], "things": [],
             "animals": [{"name": "Pip", "relationship": "pet cat", "observations": ["zoomies"]}]}
    seen = []

    class Messages:
        def create(self, **kwargs):
            seen.append(kwargs["messages"][0]["content"])
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    out = entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                        {"date": "2026-03-02", "title": "t", "text": "zoomies"},
                                        known_people=["Dev"], known_animals=["Pip"])
    assert out["animals"][0]["name"] == "Pip"
    assert "ANIMALS" in seen[0] and "never people" in seen[0]
    assert "file it under animals with exactly this spelling: Pip." in seen[0]
