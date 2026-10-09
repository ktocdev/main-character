# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mix-up flags (entity-identity plan, Phase 2): one name that probably covers
two people, found from what each entry says they are to the author. Covers
the relationship groups, the tiered rule (family and pets clash at two
mentions; friend, work, romantic and service need a real share), keeping
every attribute at build, dismissal, and the startup refresh.
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


def _build(tmp_path, curation=None):
    if curation is not None:
        entities.save_curation(curation)
    records = [
        {"date": p.stem[:10], "title": p.stem, "key": p.stem,
         "entities": json.loads(p.read_text(encoding="utf-8"))}
        for p in sorted((tmp_path / "raw").glob("*.json"))
    ]
    return entities.build_entity_docs(records)


def _person(name, relationship):
    return {"people": [{"name": name, "relationship": relationship,
                        "observations": [f"saw {name}"]}]}


def _raws(name, labels):
    """One entry per label, a day apart."""
    return {f"2026-03-{i + 1:02d}_e": _person(name, label) for i, label in enumerate(labels)}


def test_relationship_groups():
    g = entities.relationship_group
    assert g("Coworker") == g("colleague") == g("former coworker") == "work"
    assert g("ex") == g("on-and-off boyfriend") == "romantic"
    assert g("high school best friend") == "friend"
    assert g("pet cat") == "pet"
    # hedged or someone else's: no group, so no vote
    assert g("ex/partner") == g("classmate or teacher") == g("Mika's mother") == ""
    assert g("unspecified") == ""


def test_overlapping_groups_need_a_real_share():
    flag = entities.mixup_flag
    assert flag("person", {"coworker": 9, "friend": 4}) == [["coworker", 9], ["friend", 4]]
    # a friend who became an ex, mentioned as a date twice: one person
    assert flag("person", {"friend": 20, "date": 2}) == []
    # three mentions, but not a quarter of them
    assert flag("person", {"friend": 30, "coworker": 3}) == []
    # labels in one group add up; the most-used one names it
    assert flag("person", {"coworker": 5, "colleague": 4, "friend": 2, "old friend": 2}) == [
        ["coworker", 9], ["friend", 4]]


def test_family_and_pets_clash_at_two():
    flag = entities.mixup_flag
    assert flag("person", {"mother": 40, "date": 2}) == [["mother", 40], ["date", 2]]
    assert flag("person", {"dog": 25, "neighbor": 2}) == [["dog", 25], ["neighbor", 2]]
    assert flag("person", {"mother": 40, "friend": 1}) == []  # one stray label


def test_only_people_are_flagged():
    assert entities.mixup_flag("place", {"home": 5, "bar": 5}) == []
    assert entities.mixup_flag("project", {"active": 5, "completed": 5}) == []


def test_text():
    assert entities.mixup_text([["coworker", 9], ["friend", 4]]) == "seen as coworker (9) and friend (4)"
    assert entities.mixup_text([["a", 3], ["b", 2], ["c", 2]]) == "seen as a (3), b (2) and c (2)"


def test_build_keeps_every_attribute_and_flags(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        **_raws("Dev", ["coworker"] * 3 + ["friend"] * 3 + ["coworker"]),
        "2026-04-01_e": _person("Ana", "friend"),
    })
    index = _build(tmp_path)
    assert index["Dev"]["attrs"] == {"coworker": 4, "friend": 3}
    assert index["Dev"]["mixup"] == [["coworker", 4], ["friend", 3]]
    # the doc still shows the latest
    doc = (tmp_path / "people" / "dev.md").read_text(encoding="utf-8")
    assert "relationship: coworker" in doc
    assert "mixup" not in index["Ana"]
    assert [m["name"] for m in entities.find_mixups(index)] == ["Dev"]


def test_merged_names_vote_together(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        **_raws("Devin", ["coworker"] * 3),
        "2026-04-01_e": _person("Dev", "friend"),
        "2026-04-02_e": _person("Dev", "friend"),
        "2026-04-03_e": _person("Dev", "friend"),
    })
    curation = entities.load_curation()
    curation["merge"]["person:devin"] = "Dev"
    index = _build(tmp_path, curation)
    assert index["Dev"]["mixup"] == [["coworker", 3], ["friend", 3]]


def test_dismissed_stays_dismissed(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _raws("Dev", ["coworker"] * 3 + ["friend"] * 3))
    curation = entities.load_curation()
    curation["not_mixed"].append("person:dev")
    index = _build(tmp_path, curation)
    assert "mixup" not in index["Dev"]
    assert index["Dev"]["attrs"] == {"coworker": 3, "friend": 3}
    assert entities.find_mixups(index) == []


def test_startup_refresh_matches_the_build(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _raws("Dev", ["coworker"] * 3 + ["friend"] * 3))
    built = _build(tmp_path)
    # an index from before mix-ups: no attrs, no flags
    old = {n: {k: v for k, v in i.items() if k not in ("attrs", "mixup")} for n, i in built.items()}
    assert entities.mark_mixups(old) == built
    curation = entities.load_curation()
    curation["not_mixed"].append("person:dev")
    entities.save_curation(curation)
    assert "mixup" not in entities.mark_mixups(old)["Dev"]
