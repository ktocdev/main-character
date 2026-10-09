# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Groups carry context (entity-identity plan, Phase 4): a group's note says
what ties its members together. It reaches each member's profile (so the
companion has it) and, for groups that have one, the extraction prompt,
which uses it to tell who an entry means. Groups without a note change
nothing, so a journal that never writes one extracts exactly as before.
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
    for stem, data in raws.items():
        (raw / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def _build(tmp_path):
    records = [
        {"date": p.stem[:10], "title": p.stem, "key": p.stem,
         "entities": json.loads(p.read_text(encoding="utf-8"))}
        for p in sorted((tmp_path / "raw").glob("*.json"))
    ]
    return entities.build_entity_docs(records)


def _person(name, relationship="friend"):
    return {"name": name, "relationship": relationship, "observations": [f"saw {name}"]}


RAWS = {"2026-03-01_a": {"people": [_person("Dev", "coworker"), _person("Lena", "coworker"),
                                    _person("Mika")]}}


def test_a_note_reaches_profiles_and_extraction(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    entities.save_groups([
        {"name": "Team", "parent": "", "members": ["Dev", "Lena"], "note": "people from the Groundwork job"},
        {"name": "Pals", "parent": "", "members": ["Mika"]},
    ])
    _build(tmp_path)
    doc = (tmp_path / "people" / "dev.md").read_text(encoding="utf-8")
    assert "groups: Team (people from the Groundwork job)" in doc
    assert "groups: Pals\n" in (tmp_path / "people" / "mika.md").read_text(encoding="utf-8")
    # only described groups are offered to extraction
    assert entities.known_groups_hint() == "Team (people from the Groundwork job): Dev, Lena"

    seen = {}

    class Messages:
        def create(self, **kwargs):
            seen["prompt"] = kwargs["messages"][0]["content"]
            reply = {"people": [], "projects": [], "places": [], "things": []}
            return SimpleNamespace(stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(reply))])

    entities.extract_conversation(SimpleNamespace(messages=Messages()),
                                  {"date": "2026-03-02", "title": "t", "text": "x"},
                                  known_people=["Dev"], groups=entities.known_groups_hint())
    assert "Team (people from the Groundwork job): Dev, Lena" in seen["prompt"]


def test_no_notes_no_change(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, RAWS)
    entities.save_groups([{"name": "Pals", "parent": "", "members": ["Mika"]},
                          {"name": "Old", "parent": "", "members": ["Dev"], "note": "x", "retired": True}])
    _build(tmp_path)
    assert entities.known_groups_hint() == ""
