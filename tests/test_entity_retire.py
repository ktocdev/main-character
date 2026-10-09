# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Retiring people and groups (entity-identity plan, Phase 7): a past chapter
leaves everyday view and the companion's zoomed-out hits, but its profile
still loads when named, and nothing is deleted.
"""
import json

import companion
import entities


def _build(tmp_path, monkeypatch, people, curation=None, groups=None):
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    if curation:
        (tmp_path / "curation.json").write_text(json.dumps(curation), encoding="utf-8")
    if groups:
        (tmp_path / "groups.json").write_text(json.dumps(groups), encoding="utf-8")
    records = [{"date": "2026-01-01", "title": "t", "key": "k",
                "entities": {"people": [{"name": n, "observations": ["x"]} for n in people]}}]
    return entities.build_entity_docs(records)


def _g(name, members, parent="", **extra):
    return {"name": name, "parent": parent, "members": members, **extra}


def test_retired_one_by_one(tmp_path, monkeypatch):
    index = _build(tmp_path, monkeypatch, ["Marisol", "Mom"], {"retired": ["person:marisol"]})
    assert index["Marisol"]["retired"] == "self" and "retired" not in index["Mom"]
    doc = (tmp_path / index["Marisol"]["path"]).read_text(encoding="utf-8")
    assert "status: retired (a past chapter)" in doc
    assert "retired" not in (tmp_path / index["Mom"]["path"]).read_text(encoding="utf-8")


def test_group_retires_nested_members_but_an_active_group_keeps_them(tmp_path, monkeypatch):
    groups = [
        _g("Old job", ["Marisol"], retired=True),
        _g("Coworkers", ["Dmitri", "Sam"], parent="Old job"),
        _g("Friends", ["Sam"]),
    ]
    index = _build(tmp_path, monkeypatch, ["Marisol", "Dmitri", "Sam"], groups=groups)
    assert index["Marisol"]["retired"] == "Old job"
    assert index["Dmitri"]["retired"] == "Coworkers"   # nested under a retired group
    assert "retired" not in index["Sam"]              # also in an active group

    index = _build(tmp_path, monkeypatch, ["Marisol", "Dmitri", "Sam"],
                   {"retired": ["person:sam"]}, groups)
    assert index["Sam"]["retired"] == "self"          # unless retired one by one


def test_retired_people_leave_the_known_names_hint(tmp_path, monkeypatch):
    _build(tmp_path, monkeypatch, ["Marisol", "Mom"], {"retired": ["person:marisol"]})
    assert entities.known_people_hint() == ["Mom"]


def test_companion_skips_retired_profiles_in_summary_hits(tmp_path, monkeypatch):
    # named, the profile still loads; unnamed, it stays out of the
    # zoomed-out hits
    seen = {}
    monkeypatch.setattr(companion, "get_summary_hits",
                        lambda q, skip_entities: seen.setdefault("skip", skip_entities) and [])
    monkeypatch.setattr(companion, "get_recent_chunks", lambda c: [])
    monkeypatch.setattr(companion, "query_journal", lambda q, n_results: [])
    monkeypatch.setattr(companion, "load_latest_arc", lambda: "")
    monkeypatch.setattr(companion, "get_dream_hits", lambda q: [])
    monkeypatch.setattr(companion, "ENTITY_DIR", tmp_path)
    (tmp_path / "marisol.md").write_text("Marisol's profile", encoding="utf-8")
    index = {"Marisol": {"type": "person", "path": "marisol.md", "mentions": 9, "retired": "self"},
             "Mom": {"type": "person", "path": "mom.md", "mentions": 4}}

    companion.build_context_block("how was mom", None, index)
    assert seen["skip"] == {"Mom", "Marisol"}

    seen.clear()
    block = companion.build_context_block("lunch with Marisol", None, index)
    assert "Marisol's profile" in block
