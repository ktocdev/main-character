# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Kinds of thing: the built-ins (music, podcast, game, show, movie, book,
event, other) and the ones the author adds ("songs", "ai tools"). An added
kind joins extraction's enum and prompt and every picker; removing one
sends its things to other without rewriting anything, so adding it back
(or undo) brings them home.
"""
import json
from types import SimpleNamespace

import pytest

import entities
import server


@pytest.fixture
def graph(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "RAW_DIR", raw)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(entities, "HISTORY_FILE", tmp_path / "history.json")
    (raw / "2026-03-01_a.json").write_text(json.dumps({"things": [
        {"name": "Blackbird", "category": "songs", "observations": ["learned the intro"]},
        {"name": "Lollygagger", "category": "music", "observations": ["saw them live"]},
        {"name": "Harbor Town Nights", "category": "show", "observations": ["watched it"]},
    ]}), encoding="utf-8")

    def build():
        records = [{"date": p.stem[:10], "title": p.stem, "key": p.stem,
                    "entities": json.loads(p.read_text(encoding="utf-8"))}
                   for p in sorted(raw.glob("*.json"))]
        return entities.build_entity_docs(records)

    def rebuild():
        server.STATE["entity_index"] = build()

    monkeypatch.setattr(server, "_rebuild", rebuild)
    monkeypatch.setitem(server.STATE, "entity_index", build())
    return build


def _songs(curation):
    curation["thing_kinds"].append({"name": "songs", "description": "songs I'm learning to play",
                                    "icon": "guitar"})
    return curation


def test_own_kinds_sit_between_the_built_ins_and_other():
    curation = _songs(entities.load_curation())
    kinds = entities.thing_categories(curation)
    assert kinds[:-2] == entities.THING_CATEGORIES[:-1]
    assert kinds[-2:] == ("songs", "other")
    assert entities.thing_categories(entities.load_curation()) == entities.THING_CATEGORIES


def test_extraction_offers_own_kinds_with_what_they_cover(graph):
    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            reply = {"people": [], "projects": [], "places": [], "animals": [], "things": []}
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    conv = {"date": "2026-03-01", "title": "t", "text": "practiced"}
    entities.extract_conversation(SimpleNamespace(messages=Messages()), conv)
    prompt = seen["messages"][0]["content"]
    assert "movie (films)" in prompt and "podcast (podcasts)" in prompt
    assert "added these categories" not in prompt

    entities.save_curation(_songs(entities.load_curation()))
    entities.extract_conversation(SimpleNamespace(messages=Messages()), conv)
    prompt = seen["messages"][0]["content"]
    assert "songs (songs I'm learning to play)" in prompt
    assert "added these categories themselves: songs." in prompt
    enum = seen["output_config"]["format"]["schema"]["properties"]["things"]["items"]["properties"]["category"]["enum"]
    assert enum[-2:] == ["songs", "other"]
    # the module's schema itself is untouched
    assert "songs" not in entities.EXTRACTION_SCHEMA["properties"]["things"]["items"]["properties"]["category"]["enum"]


def test_a_removed_kind_sends_its_things_to_other_and_back(graph):
    # without the kind, extraction's "songs" isn't a kind the journal has
    assert graph()["Blackbird"]["category"] == "other"
    curation = _songs(entities.load_curation())
    curation["category"]["thing:lollygagger"] = "songs"  # a curated one, too
    entities.save_curation(curation)
    index = graph()
    assert index["Blackbird"]["category"] == "songs"
    assert index["Lollygagger"]["category"] == "songs"

    curation["thing_kinds"] = []
    entities.save_curation(curation)
    index = graph()
    assert index["Blackbird"]["category"] == "other"
    assert index["Lollygagger"]["category"] == "music"  # falls back to what extraction said
    assert curation["category"]["thing:lollygagger"] == "songs"  # still on file


@pytest.mark.parametrize("name, description, icon, problem", [
    ("", "", "", "1 to 24"),
    ("Songs", "", "", "1 to 24"),
    ("a" * 25, "", "", "1 to 24"),
    ("songs!", "", "", "1 to 24"),
    ("movie", "", "", "already built in"),
    ("other", "", "", "already built in"),
    ("songs", "x" * 201, "", "under 200"),
    ("songs", "", "spaceship", "unknown icon"),
])
def test_a_kind_has_to_make_sense(name, description, icon, problem):
    assert problem in entities.check_thing_kind(entities.load_curation(), name, description, icon)


def test_names_with_spaces_and_editing_are_fine():
    curation = entities.load_curation()
    assert entities.check_thing_kind(curation, "ai tools", "models and apps", "robot") == ""
    assert entities.check_thing_kind(curation, "rock & roll", "", "") == ""


def test_there_is_a_ceiling_on_own_kinds():
    curation = entities.load_curation()
    curation["thing_kinds"] = [{"name": f"kind {i}", "description": "", "icon": ""}
                               for i in range(entities.MAX_THING_KINDS)]
    assert "at most" in entities.check_thing_kind(curation, "one more", "", "")
    assert entities.check_thing_kind(curation, "kind 0", "edited", "") == ""  # editing still works


def test_endpoints_add_edit_remove_and_undo(graph):
    r = server.save_thing_kind(server.ThingKindIn(name="  Songs ", description="songs I'm  learning",
                                                  icon="guitar"))
    assert r == {"ok": True, "name": "songs"}
    assert entities.load_curation()["thing_kinds"] == [
        {"name": "songs", "description": "songs I'm learning", "icon": "guitar"}]
    assert server.STATE["entity_index"]["Blackbird"]["category"] == "songs"

    listed = server.thing_kinds()
    assert [k["name"] for k in listed["kinds"]][-2:] == ["songs", "other"]
    songs = listed["kinds"][-2]
    assert songs["custom"] and songs["icon"] == "guitar"
    assert listed["kinds"][0] == {"name": "music", "custom": False,
                                  "description": "bands, artists and albums", "icon": ""}
    assert "guitar" in listed["icons"]

    # now a thing can be set to it, and the built-in movie works too
    ok = server.set_category(server.CategoryIn(name="Harbor Town Nights", category="movie"))
    assert ok["category"] == "movie"
    assert server.set_category(server.CategoryIn(name="Lollygagger", category="songs"))["ok"]
    assert server.set_category(server.CategoryIn(name="Lollygagger", category="podcasts")).status_code == 400

    server.save_thing_kind(server.ThingKindIn(name="songs", description="songs I play", icon=""))
    assert entities.load_curation()["thing_kinds"][0]["description"] == "songs I play"

    assert server.delete_thing_kind(server.NameIn(name="songs"))["ok"]
    assert entities.load_curation()["thing_kinds"] == []
    assert server.STATE["entity_index"]["Blackbird"]["category"] == "other"
    assert server.delete_thing_kind(server.NameIn(name="songs")).status_code == 404
    assert server.delete_thing_kind(server.NameIn(name="music")).status_code == 404

    entities.undo()  # the removal
    assert [k["name"] for k in entities.load_curation()["thing_kinds"]] == ["songs"]


def test_endpoint_refuses_a_bad_kind(graph):
    r = server.save_thing_kind(server.ThingKindIn(name="show"))
    assert r.status_code == 400
    assert entities.load_curation()["thing_kinds"] == []


def test_things_review_offers_own_kinds(graph, monkeypatch):
    entities.save_curation(_songs(entities.load_curation()))
    monkeypatch.setitem(server.STATE, "entity_index", graph())
    (entities.ENTITY_DIR / "index.json").write_text(json.dumps({
        "Coda": {"type": "project", "mentions": 2}}), encoding="utf-8")
    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text='{"things": []}')])

    monkeypatch.setattr(entities, "get_client", lambda: SimpleNamespace(messages=Messages()))
    monkeypatch.setattr(entities, "first_observations", lambda: {})
    assert entities.suggest_things() == []
    assert "songs (songs I'm learning to play)" in seen["messages"][0]["content"]
    enum = seen["output_config"]["format"]["schema"]["properties"]["things"]["items"]["properties"]["category"]["enum"]
    assert "songs" in enum and "movie" in enum
