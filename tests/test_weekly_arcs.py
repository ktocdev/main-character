# SPDX-License-Identifier: AGPL-3.0-or-later
"""History's weeks view (GET /api/summaries/arcs, GET /api/summaries/arc).

  * the list has every week with an arc file, newest first, without text;
  * `days` counts the dates written that week, from the entries, not the
    arc heading's first and last;
  * `chapters` are the closed chapters with a day in the week, oldest
    first: a chapter whose only day there is its last still counts (one
    closed on a Monday touches that whole week), one with none doesn't;
  * one arc comes back with its hash comment and heading stripped and the
    heading's dates as start/end; a missing week is a 404, and anything not
    shaped like a week key never reaches the filesystem;
  * the week key turns over with the ISO year (2025-12-29 is 2026-W01).
"""

import json

import pytest
from starlette.testclient import TestClient

import sessions
import summarizer

BASE = "http://127.0.0.1:8144"


class FakeCollection:
    """What the arcs list and session_list read of the chroma collection."""

    def __init__(self, metas=()):
        self.metas = list(metas)

    def get(self, include=None, where=None, limit=None):
        return {"ids": [str(i) for i in range(len(self.metas))], "metadatas": self.metas}


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture(autouse=True)
def journal(client, tmp_path, monkeypatch):
    """No arcs, no archives, an empty open chapter and an empty index."""
    import server
    summarizer.ARC_DIR.mkdir(parents=True, exist_ok=True)
    for f in summarizer.ARC_DIR.glob("*.md"):
        f.unlink()
    monkeypatch.setattr(sessions, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "CURRENT_FILE", tmp_path / "sessions" / "current.json")
    monkeypatch.setattr(sessions, "ARCHIVE_DIR", tmp_path / "sessions" / "archive")
    sessions.save_current(sessions._fresh())
    col = FakeCollection()
    monkeypatch.setitem(server.STATE, "collection", col)
    yield col
    for f in summarizer.ARC_DIR.glob("*.md"):
        f.unlink()


def write_arc(key, start, end, text="An arc."):
    (summarizer.ARC_DIR / f"{key}.md").write_text(
        f"<!-- hash: abc123 -->\n# Week {key} ({start} to {end})\n\n{text}\n",
        encoding="utf-8")


def write_archive(aid, title, dates, message_days=()):
    sessions.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (sessions.ARCHIVE_DIR / f"{aid}.json").write_text(json.dumps({
        "id": aid, "title": title, "started": f"{dates[0]} 09:00",
        "closed": f"{dates[-1]} 18:00",
        "parts": [{"date": d, "title": title} for d in dates],
        "messages": [{"role": "you", "text": "x", "ts": f"{d} 10:00"} for d in message_days],
    }), encoding="utf-8")


def weeks(client):
    r = client.get("/api/summaries/arcs")
    assert r.status_code == 200
    return r.json()["weeks"]


def test_newest_first_without_text(client):
    write_arc("2026-W28", "2026-07-07", "2026-07-11")
    write_arc("2026-W30", "2026-07-20", "2026-07-21")
    got = weeks(client)
    assert [w["week"] for w in got] == ["2026-W30", "2026-W28"]
    assert got[1] == {"week": "2026-W28", "start": "2026-07-07", "end": "2026-07-11",
                      "days": 0, "chapters": []}


def test_no_arcs_is_an_empty_list(client):
    assert weeks(client) == []


def test_days_counts_dates_written(client, journal):
    write_arc("2026-W28", "2026-07-07", "2026-07-11")
    journal.metas += [{"date": d, "title": "t"} for d in
                      ("2026-07-07", "2026-07-07", "2026-07-09", "2026-07-11", "2026-07-13")]
    [w] = weeks(client)
    assert w["days"] == 3


def test_chapters_touching_a_week(client):
    """The Sun → Mon chapter: Aug 9 (W32), Aug 10-15 (W33), closed Aug 17 (W34)."""
    for key, a, b in (("2026-W32", "2026-08-03", "2026-08-09"),
                      ("2026-W33", "2026-08-10", "2026-08-15"),
                      ("2026-W34", "2026-08-17", "2026-08-20")):
        write_arc(key, a, b)
    write_archive("before", "Before", ["2026-08-03", "2026-08-06", "2026-08-08"])
    write_archive("middle", "Middle", ["2026-08-09", "2026-08-12"], message_days=["2026-08-17"])
    write_archive("after", "After", ["2026-08-18", "2026-08-20"])
    by_week = {w["week"]: [c["title"] for c in w["chapters"]] for w in weeks(client)}
    assert by_week == {"2026-W32": ["Before", "Middle"],
                       "2026-W33": ["Middle"],
                       "2026-W34": ["Middle", "After"]}


def test_chapters_carry_what_history_keys_them_by(client, journal):
    write_arc("2026-W33", "2026-08-10", "2026-08-15")
    write_archive("a1", "Archived", ["2026-08-10"])
    journal.metas.append({"date": "2026-08-12", "title": "Imported"})
    [w] = weeks(client)
    assert w["chapters"] == [
        {"kind": "archive", "id": "a1", "title": "Archived",
         "start": "2026-08-10", "end": "2026-08-10"},
        {"kind": "import", "title": "Imported", "start": "2026-08-12", "end": "2026-08-12"},
    ]


def test_one_arc_stripped(client):
    write_arc("2026-W28", "2026-07-07", "2026-07-11", "One.\n\nTwo.")
    r = client.get("/api/summaries/arc", params={"week": "2026-W28"})
    assert r.json() == {"week": "2026-W28", "start": "2026-07-07",
                        "end": "2026-07-11", "text": "One.\n\nTwo."}


@pytest.mark.parametrize("week", ["2026-W29", "../2026-W28", "2026-W1", "2026-W28.md", ""])
def test_missing_or_malformed_week_is_404(client, week):
    write_arc("2026-W28", "2026-07-07", "2026-07-11")
    r = client.get("/api/summaries/arc", params={"week": week})
    assert r.status_code == 404


def test_iso_year_turn(client, journal):
    assert summarizer.week_key("2025-12-29") == "2026-W01"
    write_arc("2026-W01", "2025-12-29", "2026-01-02")
    write_arc("2025-W52", "2025-12-23", "2025-12-28")
    journal.metas += [{"date": "2025-12-29", "title": "t"}, {"date": "2025-12-28", "title": "t"}]
    got = weeks(client)
    assert [(w["week"], w["days"]) for w in got] == [("2026-W01", 1), ("2025-W52", 1)]
