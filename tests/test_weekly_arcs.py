# SPDX-License-Identifier: AGPL-3.0-or-later
"""Weekly arcs for a date range (GET /api/summaries/arcs, History's arc blocks).

  * every ISO week the range touches is read, oldest first, and weeks with
    no arc file are left out, not returned empty;
  * the hash comment and the heading are stripped, and the heading's dates
    (first and last days with entries) come back as start/end;
  * bad dates, a backwards range and one over a year are refused;
  * the week key turns over with the ISO year (2025-12-29 is 2026-W01),
    and history.js's weekKey agrees with summarizer.week_key;
  * History's arcs query (arcRange) matches the one the web demo build
    records (build_web_demo.arc_range), since the demo replays it exactly.
"""

import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import summarizer

BASE = "http://127.0.0.1:8144"
HISTORY_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "history.js"


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture(autouse=True)
def arc_dir():
    summarizer.ARC_DIR.mkdir(parents=True, exist_ok=True)
    for f in summarizer.ARC_DIR.glob("*.md"):
        f.unlink()
    yield summarizer.ARC_DIR
    for f in summarizer.ARC_DIR.glob("*.md"):
        f.unlink()


def write_arc(key, start, end, text):
    (summarizer.ARC_DIR / f"{key}.md").write_text(
        f"<!-- hash: abc123 -->\n# Week {key} ({start} to {end})\n\n{text}\n",
        encoding="utf-8")


def arcs(client, start, end):
    r = client.get("/api/summaries/arcs", params={"start": start, "end": end})
    assert r.status_code == 200
    return r.json()["arcs"]


def test_a_range_across_two_weeks_returns_both(client):
    write_arc("2026-W28", "2026-07-07", "2026-07-11", "First week.")
    write_arc("2026-W29", "2026-07-13", "2026-07-13", "Second week.")
    got = arcs(client, "2026-07-09", "2026-07-14")
    assert [a["week"] for a in got] == ["2026-W28", "2026-W29"]


def test_heading_and_hash_comment_are_stripped(client):
    write_arc("2026-W28", "2026-07-07", "2026-07-11", "One.\n\nTwo.")
    [arc] = arcs(client, "2026-07-06", "2026-07-06")
    assert arc == {"week": "2026-W28", "start": "2026-07-07",
                   "end": "2026-07-11", "text": "One.\n\nTwo."}


def test_a_week_with_no_arc_is_left_out(client):
    write_arc("2026-W27", "2026-06-29", "2026-07-05", "Before.")
    write_arc("2026-W29", "2026-07-13", "2026-07-19", "After.")
    got = arcs(client, "2026-06-29", "2026-07-19")
    assert [a["week"] for a in got] == ["2026-W27", "2026-W29"]


def test_no_arcs_at_all_is_an_empty_list(client):
    assert arcs(client, "2026-07-06", "2026-07-12") == []


def test_iso_year_turn(client):
    assert summarizer.week_key("2025-12-29") == "2026-W01"
    write_arc("2026-W01", "2025-12-29", "2026-01-02", "New year.")
    write_arc("2025-W52", "2025-12-23", "2025-12-28", "Old year.")
    got = arcs(client, "2025-12-27", "2026-01-01")
    assert [a["week"] for a in got] == ["2025-W52", "2026-W01"]


@pytest.mark.parametrize("start,end", [
    ("2026-07-06", "nope"),
    ("../secrets", "2026-07-06"),
    ("2026-07-12", "2026-07-06"),       # backwards
    ("2024-01-01", "2026-07-06"),       # more than a year
])
def test_bad_ranges_are_refused(client, start, end):
    r = client.get("/api/summaries/arcs", params={"start": start, "end": end})
    assert r.status_code == 400


def test_load_arc_only_takes_week_keys():
    assert summarizer.load_arc("../2026-W28") is None


def _js_week_key(iso):
    """Run history.js's weekKey under node, if it's there."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = HISTORY_JS.read_text(encoding="utf-8")
    m = re.search(r"^function weekKey\(iso\) \{.*?^\}", src, re.S | re.M)
    assert m, "history.js has no top-level weekKey(iso)"
    out = subprocess.run([node, "-e", f"{m.group(0)}\nprocess.stdout.write(weekKey({iso!r}))"],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.parametrize("iso", [
    "2025-12-29", "2026-01-01", "2026-01-04", "2026-01-05", "2026-07-06",
    "2026-07-12", "2020-12-31", "2021-01-03", "2027-01-01", "2026-12-31",
])
def test_js_week_key_matches_python(iso):
    assert _js_week_key(iso) == summarizer.week_key(iso)


def _js_arc_range(chapter):
    """history.js's arcRange (with weekEdge and isoDay) under node."""
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = HISTORY_JS.read_text(encoding="utf-8")
    parts = [re.search(pat, src, re.S | re.M) for pat in (
        r"^function weekEdge\(iso, offset\) \{.*?^\}",
        r"^const isoDay = .*?;$",
        r"^function arcRange\(r\) \{.*?^\}",
    )]
    assert all(parts), "history.js is missing weekEdge, isoDay or arcRange"
    code = "\n".join(m.group(0) for m in parts)
    out = subprocess.run(
        [node, "-e", f"{code}\nprocess.stdout.write(JSON.stringify(arcRange({json.dumps(chapter)})))"],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("chapter", [
    {"parts": [{"date": "2026-07-09"}], "messages": [{"ts": "2026-07-14 09:00"}]},
    {"parts": [{"date": "2025-12-31", "messages": [{"ts": "2026-01-05 22:10"}]}]},
    {"parts": [{"date": "2026-07-06"}, {"date": "2026-07-12"}]},
    {"parts": [], "messages": []},
])
def test_js_arc_range_matches_the_demo_build(chapter):
    """The web demo replays the arcs read by its exact query, which
    scripts/build_web_demo.py builds the way History does."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_web_demo", HISTORY_JS.parent.parent.parent / "scripts" / "build_web_demo.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    want = build.arc_range(chapter)
    assert _js_arc_range(chapter) == (None if want is None else [want["start"], want["end"]])
