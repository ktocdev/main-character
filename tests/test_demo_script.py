# SPDX-License-Identifier: AGPL-3.0-or-later
"""The demo journal follows its script (demo_script.py).

The page swaps whatever is typed for the next scripted message
(static/js/demo-script.js); these lock down the server's half: a scripted
send gets its own reply and its own time, the position moves the way the
web demo's backend.js moves it, and none of it reaches a journal that isn't
the demo.
"""

import json

import pytest
from starlette.testclient import TestClient

BASE = "http://127.0.0.1:8144"
SCRIPT = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parent.parent
     / "mock_fixtures" / "demo_script.json").read_text(encoding="utf-8"))
W = SCRIPT["write"]


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture(autouse=True)
def demo(monkeypatch):
    """A demo journal on canned replies, with no waiting, starting at the top."""
    import demo_script
    import mock_client
    import sessions
    monkeypatch.setenv("MC_SEED_INSTANCE", "1")
    monkeypatch.setattr(mock_client, "DELAYS", {})
    monkeypatch.setattr(mock_client, "DEFAULT_DELAY", 0)
    monkeypatch.setattr(mock_client, "STREAM_CHUNK_DELAY", 0)
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    demo_script._state_path().unlink(missing_ok=True)
    yield
    demo_script._state_path().unlink(missing_ok=True)


def position():
    import demo_script
    return demo_script._load()


def messages():
    import sessions
    return json.loads(sessions.CURRENT_FILE.read_text(encoding="utf-8"))["messages"]


def test_script_state_gives_texts_not_replies(client):
    r = client.get("/api/demo/script")
    assert r.status_code == 200
    body = r.json()
    assert [w["text"] for w in body["write"]] == [w["text"] for w in W]
    assert body["write_at"] == 0 and body["lookup_at"] == 0
    sent = json.dumps(body)
    replies = [w["reply"] for w in W] + [q["reply"] for q in SCRIPT["lookup"]]
    assert not any(json.dumps(r)[1:-1] in sent for r in replies)


def test_scripted_entry_gets_its_reply_and_its_date(client):
    r = client.post("/api/entry", json={"text": W[0]["text"]})
    assert r.text == W[0]["reply"]
    you, companion = messages()[-2:]
    assert you["ts"] == W[0]["ts"]
    assert companion["text"] == W[0]["reply"]
    assert position()["write_at"] == 1


def test_scripted_follow_up_is_stamped_both_sides(client):
    client.post("/api/entry", json={"text": W[0]["text"]})
    r = client.post("/api/chat", json={"message": W[1]["text"]})
    assert r.text == W[1]["reply"]
    you, companion = messages()[-2:]
    assert you["ts"] == companion["ts"] == W[1]["ts"]
    assert position()["write_at"] == 2


def test_no_reply_entry_skips_its_follow_ups(client):
    r = client.post("/api/entry", json={"text": W[0]["text"], "no_reply": True})
    assert r.json()["no_reply"] is True
    assert position()["write_at"] == 2


def test_past_the_end_every_send_gets_the_end_message(client):
    import demo_script
    demo_script._save({"write_at": len(W), "lookup_at": 0, "closes": 0})
    r = client.post("/api/entry", json={"text": "whatever I like"})
    assert r.text == demo_script.END


def test_a_no_reply_save_with_no_entry_left_ends_the_script(client):
    import demo_script
    last = max(i for i, w in enumerate(W) if w["send"] == "entry")
    demo_script._save({"write_at": last + 1, "lookup_at": 0, "closes": 0})
    if last + 1 < len(W):   # a follow-up is pending after the last entry
        client.post("/api/entry", json={"text": "not scripted", "no_reply": True})
        assert position()["write_at"] == len(W)


def test_lookup_follows_its_own_script_and_chips_stay_put(client):
    q = SCRIPT["lookup"]
    chip = SCRIPT["chips"][0]
    assert client.post("/api/lookup", json={"message": q[0]["text"]}).text == q[0]["reply"]
    assert client.post("/api/lookup", json={"message": chip["text"]}).text == chip["reply"]
    assert position()["lookup_at"] == 1


def test_after_a_close_a_chip_gives_its_after_close_answer(client):
    import demo_script
    chip = next(c for c in SCRIPT["chips"] if c.get("reply_after"))
    demo_script.after_close()
    assert client.post("/api/lookup", json={"message": chip["text"]}).text == chip["reply_after"]


def test_a_close_moves_to_the_next_entry(client):
    import demo_script
    client.post("/api/entry", json={"text": W[0]["text"]})   # a follow-up is next
    demo_script.after_close()
    assert W[position()["write_at"]]["send"] == "entry"


def test_the_scripted_reply_is_one_shot():
    import mock_client
    mock_client.say_next("once")
    assert mock_client._take_next() == "once"
    assert mock_client._take_next() is None


def test_nothing_of_it_reaches_a_journal_that_is_not_the_demo(client, monkeypatch):
    monkeypatch.delenv("MC_SEED_INSTANCE")
    assert client.get("/api/demo/script").status_code == 404
    r = client.post("/api/entry", json={"text": W[0]["text"]})
    assert r.text != W[0]["reply"]
    assert messages()[-2]["ts"] != W[0]["ts"]
    assert position()["write_at"] == 0


def test_only_the_demo_page_keeps_its_own_browser_storage(client, monkeypatch):
    """The demo journal runs on the author's origin; its page is marked so
    its draft and lookups never land in the author's keys (core.storeKey)."""
    import server
    mark = '<meta name="mc-journal" content="demo">'
    monkeypatch.setattr(server, "SEED_INSTANCE", False)
    assert mark not in client.get("/").text
    monkeypatch.setattr(server, "SEED_INSTANCE", True)
    assert mark in client.get("/").text
