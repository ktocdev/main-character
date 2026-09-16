# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two routes the backdating work changed, neither of which had a test.

test_smoke.py proves the app boots; nothing exercised /api/entry or
/api/chat, which is how `when=when` shipped in the chat route with `when`
undefined — the reply streamed to the browser and the NameError that
followed silently dropped the companion turn from the session record.

What these lock down:
  * a client-supplied `ts` decides the entry's stamp and the day its
    markdown backup files under (this is what backdating a missed day
    means — close_session groups by ts[:10]),
  * a missing or malformed `ts` falls back to now instead of failing,
    so an older client keeps working,
  * a chat turn records both sides.
"""

import json
from datetime import datetime

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
    """Each test reads the whole session, so each test starts with none."""
    import sessions
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    yield


def messages():
    import sessions
    return json.loads(
        sessions.CURRENT_FILE.read_text(encoding="utf-8")
    )["messages"]


def only(role):
    return [m for m in messages() if m["role"] == role]


def test_entry_is_stamped_by_the_client(client):
    response = client.post("/api/entry", json={"text": "a day I missed writing up",
                                               "ts": BACKDATED})
    assert response.status_code == 200
    assert only("you")[0]["ts"] == BACKDATED


def test_backup_files_under_the_stamped_day(client):
    """The markdown backup is named from the stamp, not the wall clock —
    a backdated entry that filed under today would be findable only by
    the date it was typed, which defeats backdating it."""
    from rag_journal import JOURNAL_DIR
    client.post("/api/entry", json={"text": "written up late", "ts": BACKDATED})
    assert (JOURNAL_DIR / "2024-03-05_0815_entry.md").exists()


def test_absent_stamp_falls_back_to_now(client):
    """An older client sends no `ts` at all."""
    response = client.post("/api/entry", json={"text": "no stamp from this client"})
    assert response.status_code == 200
    assert only("you")[0]["ts"][:10] == datetime.now().strftime("%Y-%m-%d")


def test_malformed_stamp_falls_back_rather_than_failing(client):
    """parse_stamp returns None on junk; the entry still has to save."""
    response = client.post("/api/entry", json={"text": "junk stamp",
                                               "ts": "sometime last tuesday"})
    assert response.status_code == 200
    assert only("you")[0]["ts"][:10] == datetime.now().strftime("%Y-%m-%d")


def test_chat_records_both_sides(client):
    """The regression this file exists for: the companion's reply reaches
    the browser either way, so only the stored session shows the break.
    conversation_messages() replays this at startup and the archived braid
    is built from it — a one-sided record degrades both."""
    response = client.post("/api/chat", json={"message": "how did last week go?"})
    assert response.status_code == 200

    assert [m["role"] for m in messages()] == ["you", "companion"]
    assert only("companion")[0]["text"].strip()


def test_chat_reply_is_stamped_now_not_backdated(client):
    """A chat turn has no user-supplied stamp to inherit — backdating is
    a write-mode entry's affordance only."""
    client.post("/api/entry", json={"text": "an old day", "ts": BACKDATED})
    client.post("/api/chat", json={"message": "anything else?"})
    assert only("companion")[-1]["ts"][:10] == datetime.now().strftime("%Y-%m-%d")
