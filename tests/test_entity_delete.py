# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Two kinds of delete and the generic-name detector (entity-identity plan,
Phase 6). Deleting one Allen used to block every future Allen; now the
default drops only the mentions there are, keyed by entry, and the
never-track rule is kept for generic terms and junk.
"""
import json

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


def _build(tmp_path, curation):
    entities.save_curation(curation)
    records = [
        {"date": p.stem[:10], "title": p.stem, "key": p.stem,
         "entities": json.loads(p.read_text(encoding="utf-8"))}
        for p in sorted((tmp_path / "raw").glob("*.json"))
    ]
    return entities.build_entity_docs(records)


def _allen(obs):
    return {"people": [{"name": "Allen", "relationship": "", "observations": [obs]}]}


def test_delete_these_mentions_leaves_the_name_free(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-05-02_a": _allen("interview"), "2026-05-03_b": _allen("call")})
    curation = entities.load_curation()
    entities.drop_mentions(curation, "person:allen")
    assert curation["drop_mentions"] == {"person:allen": ["2026-05-02_a", "2026-05-03_b"]}
    assert "Allen" not in _build(tmp_path, curation)

    # a later entry with an Allen starts fresh, from that entry alone
    (tmp_path / "raw" / "2026-06-01_c.json").write_text(json.dumps(_allen("new job")), encoding="utf-8")
    index = _build(tmp_path, curation)
    assert index["Allen"]["mentions"] == 1
    assert [o["text"] for o in entities.list_observations("person", "Allen")] == ["new job"]

    # survives a --force re-extraction: keyed by entry, not raw-file index
    (tmp_path / "raw" / "2026-05-02_a.json").write_text(
        json.dumps({"places": [{"name": "Cafe Luna", "observations": ["x"]}], **_allen("redone")}),
        encoding="utf-8")
    assert _build(tmp_path, curation)["Allen"]["mentions"] == 1


def test_never_track_drops_every_mention_and_free_name_lets_new_ones_in(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-05-02_a": _allen("interview")})
    curation = entities.load_curation()
    curation["delete"].append("person:allen")
    (tmp_path / "raw" / "2026-06-01_c.json").write_text(json.dumps(_allen("new")), encoding="utf-8")
    assert "Allen" not in _build(tmp_path, curation)

    listed = entities.deleted_list(curation)
    assert listed["names"] == [{"key": "person:allen", "kind": "person", "name": "Allen",
                                "mentions": 2, "generic": False}]

    # freeing it now keeps today's mentions hidden; only later entries count
    entities.free_name(curation, "person:allen")
    assert curation["delete"] == []
    assert curation["drop_mentions"]["person:allen"] == ["2026-05-02_a", "2026-06-01_c"]
    (tmp_path / "raw" / "2026-07-01_d.json").write_text(json.dumps(_allen("later")), encoding="utf-8")
    assert _build(tmp_path, curation)["Allen"]["mentions"] == 1
    assert entities.deleted_list(curation)["mentions"][0]["entries"] == 2


def test_is_generic():
    for kind, name in [("place", "restaurant"), ("place", "the bar"), ("place", "late night bar"),
                       ("place", "mid pizza place"), ("place", "The Gym"), ("place", "dr office"),
                       ("project", "work plan"), ("project", "personal code projects")]:
        assert entities.is_generic(kind, name), name
    for kind, name in [("place", "edelweiss"), ("place", "belmont tavern"), ("place", "federales"),
                       ("place", "parents' house"), ("project", "helix design system"),
                       ("person", "the bartender")]:
        assert not entities.is_generic(kind, name), name


def test_generic_flag_spares_grouped_and_renamed_names(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"2026-05-02_a": {"places": [
        {"name": "the gym", "kind": "gym", "observations": ["leg day"]},
        {"name": "dive bar", "kind": "bar", "observations": ["late"]},
        {"name": "the park", "kind": "park", "observations": ["walk"]},
    ]}})
    (tmp_path / "groups.json").write_text(json.dumps(
        [{"name": "Routine", "parent": "", "members": ["the gym"]}]), encoding="utf-8")
    curation = entities.load_curation()
    curation["rename"]["place:the park"] = "The Park"
    index = _build(tmp_path, curation)
    assert index["dive bar"].get("generic") is True
    assert "generic" not in index["the gym"]   # in a group: it's *your* gym
    assert "generic" not in index["The Park"]  # renamed by hand
