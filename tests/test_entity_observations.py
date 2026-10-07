# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Triage shows every observation with the attribute its own entry gave it
(entity-identity plan, Phase 1). Two people under one name tend to show up
as "coworker" in one entry and "friend" in another, so list_observations
has to carry each record's attribute rather than the entity's latest one.
"""
import json

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
