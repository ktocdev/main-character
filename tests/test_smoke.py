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
