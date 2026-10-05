# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking to close a long chapter (sessions.close_prompt, MC_CHAPTER_CLOSE_CHARS).

  * it asks once the author's own writing reaches the length, and counts
    nothing else: the companion's replies and dreams don't make a chapter
    long, since a close doesn't turn them into entries;
  * "not yet" holds it off until the chapter grows by ASK_AGAIN_CHARS, and
    is kept in the open chapter's file, so a new chapter starts over;
  * 0 never asks;
  * Settings takes the length as a whole number, commas allowed, and
    refuses anything else.
"""

import pytest
from starlette.testclient import TestClient

import config
import env_file
import sessions

BASE = "http://127.0.0.1:8144"


@pytest.fixture(scope="module")
def client():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture(autouse=True)
def short_chapters(monkeypatch):
    """An empty chapter, asked about at 100 characters and again 50 later."""
    sessions.CURRENT_FILE.unlink(missing_ok=True)
    monkeypatch.setattr(config, "CHAPTER_CLOSE_CHARS", 100)
    monkeypatch.setattr(sessions, "ASK_AGAIN_CHARS", 50)
    yield
    sessions.CURRENT_FILE.unlink(missing_ok=True)


def write(n, role="you", dream=False):
    sessions.append_message(role, "x" * n, dream=dream)


def ask(client):
    return client.get("/api/sessions/current").json()["close_prompt"]


def test_it_asks_once_your_writing_is_long_enough(client):
    write(60)
    assert ask(client) == {"chars": 60, "limit": 100, "ask": False}
    write(40)
    assert ask(client)["ask"] is True


def test_replies_and_dreams_dont_count(client):
    write(90)
    write(500, role="companion")
    write(500, dream=True)
    assert ask(client) == {"chars": 90, "limit": 100, "ask": False}


def test_not_yet_waits_for_the_chapter_to_grow(client):
    write(120)
    r = client.post("/api/sessions/close/not-yet")
    assert r.status_code == 200 and r.json()["ask"] is False
    write(49)
    assert ask(client)["ask"] is False
    write(1)
    assert ask(client)["ask"] is True


def test_a_new_chapter_starts_over(client):
    write(120)
    client.post("/api/sessions/close/not-yet")
    sessions.save_current(sessions._fresh())        # what a close leaves
    write(100)
    assert ask(client)["ask"] is True


def test_zero_never_asks(client, monkeypatch):
    monkeypatch.setattr(config, "CHAPTER_CLOSE_CHARS", 0)
    write(10_000)
    assert ask(client)["ask"] is False


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", path)
    return path


def test_settings_take_a_whole_number(env, client):
    r = client.post("/api/settings", json={"values": {"MC_CHAPTER_CLOSE_CHARS": "40,000"}})
    assert r.status_code == 200
    assert env_file.read_env()["MC_CHAPTER_CLOSE_CHARS"] == "40000"
    for bad in ("lots", "-5", "1.5"):
        r = client.post("/api/settings", json={"values": {"MC_CHAPTER_CLOSE_CHARS": bad}})
        assert r.status_code == 400, bad
    got = client.get("/api/settings").json()
    assert got["values"]["MC_CHAPTER_CLOSE_CHARS"] == "40000"
    assert got["chapter_close"] == {"limit": 100,
                                    "default": config.DEFAULT_CHAPTER_CLOSE_CHARS}
