# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Undo history for the entity graph. A curation change stores only the
fields it touched: two whole copies of the curation per entry made the
history file megabytes on a real journal, read and rewritten whole on
every change. Older entries, which hold the whole dict, still undo.
"""
import json

import pytest

import entities


@pytest.fixture
def graph(tmp_path, monkeypatch):
    monkeypatch.setattr(entities, "ENTITY_DIR", tmp_path)
    monkeypatch.setattr(entities, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(entities, "CURATION_FILE", tmp_path / "curation.json")
    monkeypatch.setattr(entities, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(entities, "HISTORY_FILE", tmp_path / "history.json")
    cur = entities.load_curation()
    cur["reviewed"] = ["person:kim"]
    entities.save_curation(cur)
    return tmp_path


def _change(field, value, description="change"):
    before = json.loads(json.dumps(entities.load_curation()))
    cur = entities.load_curation()
    cur[field] = value
    entities.save_curation(cur)
    entities.record_change(description, "curation", before, entities.load_curation())


def test_an_entry_holds_only_the_fields_it_changed(graph):
    _change("retired", ["person:dev"])
    entry = entities._load_history()["undo"][-1]
    assert entry["before"] == {"retired": []}
    assert entry["after"] == {"retired": ["person:dev"]}


def test_undo_and_redo_put_the_field_back_and_leave_the_rest(graph):
    _change("retired", ["person:dev"])
    # changed behind the history's back: an undo must not roll it back
    cur = entities.load_curation()
    cur["reviewed"].append("person:lena")
    entities.save_curation(cur)

    entities.undo()
    cur = entities.load_curation()
    assert cur["retired"] == [] and cur["reviewed"] == ["person:kim", "person:lena"]
    entities.redo()
    assert entities.load_curation()["retired"] == ["person:dev"]


def test_an_entry_from_before_still_undoes(graph):
    whole_before = entities.load_curation()
    whole_after = {**whole_before, "retired": ["person:dev"]}
    entities.save_curation(whole_after)
    entities._save_history({"undo": [{"description": "old", "kind": "curation", "file": "",
                                      "before": whole_before, "after": whole_after}],
                            "redo": []})
    entities.undo()
    assert entities.load_curation() == whole_before


def test_a_batch_keeps_only_its_changed_curation_fields(graph):
    before = {"files": {}, "curation": entities.load_curation()}
    after_cur = {**entities.load_curation(), "variants": {"person:dev": ["work", "friend"]}}
    entities.save_curation(after_cur)
    entities.record_change("split", "batch", before, {"files": {}, "curation": after_cur})
    entry = entities._load_history()["undo"][-1]
    assert entry["before"]["curation"] == {"variants": {}}
    assert after_cur["reviewed"] == ["person:kim"]   # the caller's dict is left alone
    entities.undo()
    assert entities.load_curation()["variants"] == {}
