# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fresh-clone smoke test: does mock mode actually work from nothing?

This is the check that keeps "clone it and it works" true. It is not a
functional test of the pipeline — it asserts the three things a stranger
following the README depends on: the app boots with no API key, it says
so in a way the UI banner can read, and no network call is possible.
"""

import json
import sys

import pytest
from starlette.testclient import TestClient

# TrustedHostMiddleware only allows 127.0.0.1/localhost, so TestClient's
# default base_url of http://testserver would be refused on every request.
BASE = "http://127.0.0.1:8144"


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


def test_boots_in_mock_mode_with_empty_data(client):
    # Saved entries count while their chat is still open, so an open chat an
    # earlier test file left in the shared journal would not be empty data.
    import sessions
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["mock"] is True, "the UI banner reads this — a canned reply must never look real"
    assert body["entries"] == 0
    assert body["entities"] == 0


def test_anthropic_sdk_is_never_constructed(client):
    """The README's "no network call is possible" claim, as an assertion.

    Checked with the app up and a request already served, not just after
    import — mock mode is only honest if nothing pulls the SDK in later.
    """
    client.get("/api/status")
    assert "anthropic" not in sys.modules


def test_every_delayed_call_type_has_a_fixture():
    """Every key in DELAYS is a call the app makes. A missing fixture is
    not a crash — mock_client synthesizes a shape-valid placeholder — so
    without this the demo silently degrades to `[mock ...]` text, which
    is what Phase 3 item 8 named as the thing a stranger must not see."""
    import mock_client
    missing = [key for key in mock_client.DELAYS
               if not (mock_client.FIXTURE_DIR / f"{key}.json").exists()]
    assert not missing, f"no captured fixture for: {', '.join(missing)}"


def test_fixtures_are_non_empty_json():
    import mock_client
    for path in mock_client.FIXTURE_DIR.glob("*.json"):
        responses = json.loads(path.read_text(encoding="utf-8"))["responses"]
        assert responses, f"{path.name} has no responses"


def test_foreign_host_is_refused(client):
    """Phase 0.5 item 2. Shipped public with no coverage until now."""
    response = client.get("/api/status", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_cross_origin_post_is_refused(client):
    """The CSRF control on the destructive POST routes — documented as the
    deliberate mechanism, so it needs a test that fails if it is removed."""
    response = client.post("/api/reset", json={},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403

    same_origin = client.post("/api/reset", json={}, headers={"Origin": BASE})
    assert same_origin.status_code == 200


XSS_NAME = '<img src=x onerror="alert(1)">'


def test_hostile_entity_name_round_trips_as_text(client):
    """Phase 0.5 item 1. The other half of the pair above: the server hands
    entity names back as JSON text and never as markup.

    An entity name is attacker-influenced in the one way that matters here:
    it is extracted by a model out of whatever the journal happens to hold,
    including an imported conversation someone else wrote. So a name can be
    anything, and the defence is layered -- the server stores and serves it
    verbatim, and `esc()` escapes it at render time (see the test below).

    Verbatim is the assertion, not "escaped". A route that HTML-escapes on
    the way out would double-escape in the browser and, worse, would make
    the client-side escaping look redundant to the next person reading it.
    """
    import server

    before = server.STATE["entity_index"]
    server.STATE["entity_index"] = {
        XSS_NAME: {"type": "person", "path": "people/xss.md", "mentions": 1},
    }
    try:
        response = client.get("/api/entities")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert XSS_NAME in response.json(), "name came back altered, not verbatim"
        # Byte-level too. The angle brackets have to survive as angle
        # brackets: a filter that rewrites some metacharacters and leaves
        # others is the classic way an XSS defence becomes a false sense
        # of one. (The inner quotes come back as \\" because that is JSON
        # string encoding, which is not the same thing as HTML escaping.)
        assert "<img src=x onerror=" in response.text
        for escaped in ("&lt;", "&gt;", "&amp;", "&quot;"):
            assert escaped not in response.text, (
                f"route HTML-escaped the name ({escaped}) -- the browser "
                "would double-escape it and esc() would look redundant")
    finally:
        server.STATE["entity_index"] = before


def test_esc_escapes_every_html_metacharacter():
    """`esc()` is what makes the verbatim round trip above safe, so the two
    tests belong together -- weakening either one alone reopens the hole.

    A source-level check, not an execution one: the suite is Python and has
    no JS runtime, and pulling node into CI to run five assertions is a
    worse trade than reading the table it would have run. What it catches is
    the realistic regression -- someone trimming a character out of the map.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parent.parent
              / "static" / "js" / "core.js").read_text(encoding="utf-8")
    esc = source[source.index("export const esc"):]
    esc = esc[:esc.index("\n\n")] if "\n\n" in esc else esc[:400]
    for char, entity in [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                         ('"', "&quot;"), ("'", "&#39;")]:
        assert entity in esc, f"esc() no longer escapes {char!r}"
