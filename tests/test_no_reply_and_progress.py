# SPDX-License-Identifier: AGPL-3.0-or-later
"""Phase 2 carry-over items 1 and 3 — the two that changed the backend.

Item 3 (no-reply): a write can be saved without calling the companion at all.
The entry still lands in the session exactly like a replied-to one; there is
just no companion turn after it, and no model call to spend against.

Item 1 (close progress): the post-close pipeline exposes which stage it is on,
so the client can show staged progress instead of one static "closed" line.
The stage record is a small state machine; these drive it directly rather than
through a full close, which is slow and already covered elsewhere.
"""

import json

import pytest
from starlette.testclient import TestClient

# TrustedHostMiddleware only allows 127.0.0.1/localhost — see test_smoke.
BASE = "http://127.0.0.1:8144"
BACKDATED = "2024-03-05 08:15"


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture(autouse=True)
def empty_session():
    import sessions
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    yield


def messages():
    import sessions
    return json.loads(sessions.CURRENT_FILE.read_text(encoding="utf-8"))["messages"]


def roles():
    return [m["role"] for m in messages()]


# ---- item 3: no-reply ----

def test_no_reply_saves_the_entry_without_a_companion_turn(client):
    r = client.post("/api/entry", json={"text": "just getting it down", "no_reply": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["no_reply"] is True
    # the entry is in the session; the companion never spoke
    assert roles() == ["you"]


def test_a_normal_entry_still_gets_a_reply(client):
    """The contrast that makes the item meaningful: default is a reply."""
    r = client.post("/api/entry", json={"text": "say something back"})
    assert r.status_code == 200
    assert roles() == ["you", "companion"]


def test_no_reply_still_honors_the_client_stamp(client):
    """Backdating a missed day has to work whether or not you want a reply."""
    client.post("/api/entry", json={"text": "a day I skipped",
                                     "ts": BACKDATED, "no_reply": True})
    you = [m for m in messages() if m["role"] == "you"][0]
    assert you["ts"] == BACKDATED


# ---- item 1: close-pipeline progress ----

def test_progress_lists_every_stage_in_order():
    import server
    server._close_begin()
    try:
        p = server.close_progress()
        assert p["active"] and not p["done"]
        assert [s["key"] for s in p["steps"]] == \
            ["seed", "categories", "entities", "summaries", "dreams"]
        assert all(s["status"] == "pending" for s in p["steps"])
    finally:
        server._close_finish()


def test_a_step_reads_running_then_done():
    import server
    server._close_begin()
    try:
        with server._close_step("seed"):
            assert server.close_progress()["steps"][0]["status"] == "running"
        assert server.close_progress()["steps"][0]["status"] == "done"
    finally:
        server._close_finish()


def test_a_failed_step_is_reported_and_re_raises():
    """A stage that throws must not stall the readout — it is marked failed,
    the exception still propagates so the caller logs it, and the record is
    left in a state the remaining steps can keep advancing from."""
    import server
    server._close_begin()
    try:
        with pytest.raises(RuntimeError):
            with server._close_step("categories"):
                raise RuntimeError("boom")
        status = {s["key"]: s["status"] for s in server.close_progress()["steps"]}
        assert status["categories"] == "failed"
    finally:
        server._close_finish()


def test_finish_flips_done_for_the_client_to_stop_polling():
    import server
    server._close_begin()
    server._close_finish()
    p = server.close_progress()
    assert p["done"] and not p["active"]


# ---- discard: the mock-only reset ----

def test_discard_empties_the_open_chat_without_closing_it(client):
    """A scratch chat can be thrown away with no journal entry and no
    pipeline -- the session file is left empty rather than archived."""
    client.post("/api/entry", json={"text": "just poking at it", "no_reply": True})
    assert roles() == ["you"]
    r = client.post("/api/sessions/discard", json={})
    assert r.status_code == 200 and r.json()["ok"] is True
    # a fresh session with no turns -- not an archived one
    assert messages() == []


def test_discard_is_refused_off_mock(client, monkeypatch):
    """On a real journal an unsaved chat vanishing is data loss, so the route
    refuses and leaves the session untouched."""
    import config
    monkeypatch.setattr(config, "MOCK_MODE", False)
    client.post("/api/entry", json={"text": "keep me", "no_reply": True})
    r = client.post("/api/sessions/discard", json={})
    assert r.status_code == 403
    assert roles() == ["you"]  # nothing was thrown away
