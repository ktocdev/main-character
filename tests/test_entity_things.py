# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The fourth kind, "thing" (entity-identity plan, Phase 8): bands, games,
shows, books and recurring events. A project is something the author makes
or works on; a thing is something they enjoy or follow. Covers extraction
round-trips, retyping in and out (with merged aliases following), the
category, the generic detector, and the targeted re-extract's preview.
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


def _project(name, obs):
    return {"name": name, "domain": "personal", "status": "active", "observations": [obs]}


def test_four_kinds_share_one_table():
    assert entities.KINDS == ("person", "project", "place", "thing")
    assert entities.GROUP_FOR_KIND["thing"] == "things"
    things = entities.EXTRACTION_SCHEMA["properties"]["things"]["items"]
    assert things["properties"]["category"]["enum"] == list(entities.THING_CATEGORIES)
    assert "things" in entities.EXTRACTION_SCHEMA["required"]


def test_extracted_things_build_their_own_docs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"things": [{"name": "Lollygagger", "category": "music",
                                     "observations": ["saw them at the Blue Room"]}]},
        # a raw file from before the kind existed reads as before
        "2026-03-02_b": {"people": [], "projects": [_project("guitar", "practiced")], "places": []},
    })
    index = _build(tmp_path)
    assert index["Lollygagger"]["type"] == "thing"
    assert index["Lollygagger"]["category"] == "music"
    assert index["Lollygagger"]["path"] == "things/lollygagger.md"
    doc = (tmp_path / "things" / "lollygagger.md").read_text(encoding="utf-8")
    assert "category: music" in doc
    assert "category" not in index["guitar"]
    obs = entities.list_observations("thing", "Lollygagger")
    assert obs[0]["group"] == "things" and obs[0]["attr"] == "music"


def test_extract_conversation_collects_things(monkeypatch):
    reply = {"people": [], "projects": [], "places": [],
             "things": [{"name": "Cities Skylines", "category": "game", "observations": ["built a city"]}]}

    class Messages:
        def create(self, **kwargs):
            assert "THINGS" in kwargs["messages"][0]["content"]
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    out = entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                        {"date": "2026-03-01", "title": "t", "text": "played"})
    assert out["things"][0]["name"] == "Cities Skylines"
    assert set(out) == {"people", "projects", "places", "things"}


def test_retype_into_a_thing_takes_its_merged_aliases_along(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"projects": [_project("90 Day Fiance", "watched the finale")]},
        "2026-03-02_b": {"projects": [_project("90 day", "caught up")]},
    })
    curation = entities.load_curation()
    curation["merge"]["project:90 day"] = "90 Day Fiance"
    curation["reviewed"].append("project:90 day fiance")
    index = _build(tmp_path, curation)
    assert index["90 Day Fiance"]["mentions"] == 2

    entities.retype_rule(curation, "project:90 day fiance", "thing", "90 Day Fiance", "show")
    index = _build(tmp_path, curation)
    assert index["90 Day Fiance"]["type"] == "thing"
    assert index["90 Day Fiance"]["category"] == "show"
    assert index["90 Day Fiance"]["mentions"] == 2          # the alias came along
    assert index["90 Day Fiance"]["aliases"] == ["90 day"]
    assert index["90 Day Fiance"]["reviewed"]               # and so did the review
    assert len(entities.list_observations("thing", "90 Day Fiance")) == 2
    assert not list((tmp_path / "projects").glob("*.md"))

    # retyping back removes the rule rather than stacking a second one
    entities.retype_rule(curation, "thing:90 day fiance", "project", "90 Day Fiance")
    assert curation["retype"] == {}
    assert curation["category"] == {}
    assert _build(tmp_path, curation)["90 Day Fiance"]["type"] == "project"


def test_category_override_and_default(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"places": [{"name": "rib fest", "kind": "festival", "observations": ["ribs"]}],
                         "things": [{"name": "Tomb Raider 2", "category": "other", "observations": ["x"]}]},
    })
    curation = entities.load_curation()
    # retyped in without a category: "other", not the place's type
    entities.retype_rule(curation, "place:rib fest", "thing", "rib fest")
    curation["category"]["thing:tomb raider 2"] = "game"
    index = _build(tmp_path, curation)
    assert index["rib fest"]["category"] == "other"
    assert index["Tomb Raider 2"]["category"] == "game"


def test_reassign_into_a_new_thing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        "2026-03-01_a": {"projects": [{"name": "games", "domain": "personal", "status": "active",
                                       "observations": ["played Tomb Raider 2", "other"]}]},
    })
    entities.reassign_observation("2026-03-01_a.json", "projects", 0, 0, "thing", "Tomb Raider 2")
    data = json.loads((tmp_path / "raw" / "2026-03-01_a.json").read_text(encoding="utf-8"))
    assert data["things"] == [{"name": "Tomb Raider 2", "category": "other",
                               "observations": ["played Tomb Raider 2"]}]
    assert data["edited"] is True  # a re-extract would lose this, so it's flagged


def test_generic_things():
    for name in ("a video game", "the show", "that band", "the festival", "a horror movie"):
        assert entities.is_generic("thing", name), name
    for name in ("Cities Skylines", "Lollygagger", "90 Day Fiance", "rib fest"):
        assert not entities.is_generic("thing", name), name


def test_reextract_preview_finds_entries_and_flags_edits(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-02-25_Old-blog": {"edited": True},
                                   "2026-03-20_Memories": {}})
    convs = [
        {"date": "2026-02-25", "title": "Old blog", "text": "my old LiveJournal days " * 50},
        {"date": "2026-03-20", "title": "Memories", "text": "posted on live journal"},
        {"date": "2026-03-21", "title": "Unrelated", "text": "nothing here"},
    ]
    monkeypatch.setattr(entities, "get_conversations", lambda: convs)
    preview = entities.reextract_preview(["live journal", "  "])
    assert [(e["key"], e["edited"]) for e in preview["entries"]] == [
        ("2026-02-25_Old-blog", True), ("2026-03-20_Memories", False)]
    assert preview["estimate"] > 0

    # an edit recorded in the undo history counts too (edits from before the flag)
    entities.record_change("edit", "raw", "a", "b", "2026-03-20_Memories.json")
    preview = entities.reextract_preview(["live journal"])
    assert all(e["edited"] for e in preview["entries"])


def test_reextract_replaces_only_matching_caches(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-03-20_Memories": {"edited": True, "people": []},
                                   "2026-03-21_Unrelated": {"people": [{"name": "Kim", "observations": ["x"]}]}})
    convs = [{"date": "2026-03-20", "title": "Memories", "text": "watched 90 Day"},
             {"date": "2026-03-21", "title": "Unrelated", "text": "nothing"}]
    monkeypatch.setattr(entities, "get_conversations", lambda: convs)
    monkeypatch.setattr(entities, "get_client", lambda: None)
    fresh = {"people": [], "projects": [], "places": [],
             "things": [{"name": "90 Day Fiance", "category": "show", "observations": ["watched"]}]}
    monkeypatch.setattr(entities, "extract_conversation", lambda client, conv, **kw: fresh)
    seen = []
    result = entities.reextract(["90 day"], progress=lambda i, n, c: seen.append((i, n)))
    assert result == {"done": ["2026-03-20_Memories"], "failed": []}
    assert seen == [(0, 1)]
    assert json.loads((tmp_path / "raw" / "2026-03-20_Memories.json").read_text(encoding="utf-8")) == fresh
    untouched = json.loads((tmp_path / "raw" / "2026-03-21_Unrelated.json").read_text(encoding="utf-8"))
    assert untouched["people"][0]["name"] == "Kim"


def test_a_reextract_that_breaks_still_indexes_what_it_wrote(monkeypatch):
    """Entries re-extracted before an unexpected error are on disk; the run
    rebuilds the index however it stops, and still reports the error."""
    import server
    rebuilt = []
    monkeypatch.setattr(server, "_rebuild", lambda: rebuilt.append(1))

    def breaks(terms, progress):
        progress(0, 2, {"date": "2026-03-20", "title": "Memories"})
        raise OSError("disk full")
    monkeypatch.setattr(entities, "reextract", breaks)
    server._REEXTRACT.update(running=True, error="", result=None)
    server._run_reextract(["90 day"])
    assert rebuilt == [1]
    assert server._REEXTRACT["error"] == "disk full" and not server._REEXTRACT["running"]

