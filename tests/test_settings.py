"""The settings surface: the .env writer and the two routes over it.

Every test here points env_file.ENV_PATH at a throwaway file. Nothing in
this module may touch the real .env — it holds the API key, and a test that
rewrote it would be a lockout, not a failed assertion.

The route tests exist mainly for the constraints that are easy to
reintroduce by accident: the key must never come back out, and a save must
never write a key that isn't on the whitelist. Both are the kind of thing a
later refactor "simplifies" away.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import env_file
import server

# TrustedHostMiddleware refuses TestClient's default "testserver" host
BASE = "http://127.0.0.1:8144"

# Deliberately not key-shaped. scripts/check_leaks.sh greps every tracked
# file for `sk-ant-` followed by a key body, and it cannot tell a fixture
# from the real thing -- a realistic-looking placeholder here fails the
# pre-push gate. Nothing in these tests depends on the shape, only on the
# value being distinctive enough to find in a response body.
STARTING_ENV = (
    "# my journal\n"
    "ANTHROPIC_API_KEY=fake-key-original\n"
    "\n"
    "# who the entries are by\n"
    "RAG_AUTHOR_NAME=Jordan\n"
    "ANTHROPIC_BASE_URL=https://api.anthropic.com\n"
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A .env of our own, in place of the real one, for the whole test."""
    path = tmp_path / ".env"
    path.write_text(STARTING_ENV, encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", path)
    return path


@pytest.fixture
def client():
    with TestClient(server.app, base_url=BASE) as c:
        yield c


# ---- the writer ----

def test_comments_and_unknown_keys_survive_a_save(env):
    env_file.update_env({"MC_TIMEZONE": "America/Chicago"})
    text = env.read_text(encoding="utf-8")
    assert "# my journal" in text
    assert "# who the entries are by" in text
    assert "ANTHROPIC_BASE_URL=https://api.anthropic.com" in text
    assert env_file.read_env()["MC_TIMEZONE"] == "America/Chicago"


def test_an_existing_key_is_rewritten_in_place(env):
    env_file.update_env({"RAG_AUTHOR_NAME": "Katina"})
    lines = env.read_text(encoding="utf-8").splitlines()
    # still directly under its own comment, not appended to the end
    assert lines[lines.index("# who the entries are by") + 1] == "RAG_AUTHOR_NAME=Katina"


def test_clearing_a_value_removes_the_assignment(env):
    env_file.update_env({"MC_TIMEZONE": "UTC"})
    assert "MC_TIMEZONE" in env_file.read_env()
    env_file.update_env({"MC_TIMEZONE": ""})
    # gone entirely, so config.py's default applies again rather than ""
    assert "MC_TIMEZONE" not in env_file.read_env()


def test_a_value_cannot_smuggle_in_a_second_assignment(env):
    with pytest.raises(ValueError):
        env_file.update_env(
            {"MC_TIMEZONE": "UTC\nANTHROPIC_BASE_URL=https://evil.example"})
    # and the refusal left the file alone
    assert env.read_text(encoding="utf-8") == STARTING_ENV


def test_a_value_with_spaces_round_trips(env):
    env_file.update_env({"MC_DATE_FORMAT": "%B %d, %Y"})
    assert env_file.read_env()["MC_DATE_FORMAT"] == "%B %d, %Y"


def test_a_duplicated_key_is_written_where_it_is_read(env):
    """dotenv takes the last assignment. Rewriting an earlier one would leave
    the duplicate below it still winning -- a save that reports success and
    changes nothing."""
    env.write_text(STARTING_ENV + textwrap.dedent("""\
        MC_TIMEZONE=UTC
        # a stale duplicate someone left behind
        MC_TIMEZONE=UTC
        """), encoding="utf-8")
    env_file.update_env({"MC_TIMEZONE": "Europe/Paris"})
    assert env_file.read_env()["MC_TIMEZONE"] == "Europe/Paris"


def test_clearing_removes_every_duplicate_assignment(env):
    """Dropping only the last one would promote a shadowed line into effect."""
    env.write_text(STARTING_ENV + textwrap.dedent("""\
        MC_TIMEZONE=UTC
        MC_TIMEZONE=Asia/Tokyo
        """), encoding="utf-8")
    env_file.update_env({"MC_TIMEZONE": ""})
    assert "MC_TIMEZONE" not in env_file.read_env()


def test_an_inline_comment_is_not_part_of_the_value(env):
    """dotenv stops an unquoted value at a whitespace-preceded '#'. A reader
    that didn't would report a value the app never loaded -- and would fold
    the comment into the value for real on the next save."""
    env.write_text(STARTING_ENV + "MC_TIMEZONE=UTC   # my zone" + "\n",
                   encoding="utf-8")
    assert env_file.read_env()["MC_TIMEZONE"] == "UTC"


def test_a_quoted_value_ends_at_its_closing_quote(env):
    env.write_text(STARTING_ENV + 'MC_DATE_FORMAT="%B %d, %Y"  # long' + "\n",
                   encoding="utf-8")
    assert env_file.read_env()["MC_DATE_FORMAT"] == "%B %d, %Y"


def test_a_hash_inside_a_quoted_value_survives_a_round_trip(env):
    env_file.update_env({"RAG_AUTHOR_NAME": "Jordan # not a comment"})
    assert env_file.read_env()["RAG_AUTHOR_NAME"] == "Jordan # not a comment"


def test_an_unlisted_date_format_keeps_the_style_it_renders_as():
    """A format hand-set in .env is one the picker never offered, and
    date_style is what the browser renders by. Reading every unknown format
    as long would show %m/%d/%Y as "August 25, 2026" in the page while the
    server's own stamps beside it stayed numeric."""
    import config
    assert config.date_style("%m/%d/%Y") == "short"
    assert config.date_style("%d.%m.%Y") == "short"
    assert config.date_style("%B %-d, %Y") == "long"
    # the listed formats still answer from the table
    for fmt, style in config.DATE_FORMATS.items():
        assert config.date_style(fmt) == style


# ---- the routes ----

def test_get_never_returns_the_api_key(env, client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["api_key_set"] is True
    # not the value, and not a suffix of it either
    blob = json.dumps(body)
    assert "fake-key-original" not in blob
    assert "original" not in blob


def test_get_offers_only_resolvable_timezones(env, client):
    body = client.get("/api/settings").json()
    if body["tz_database"]:
        from zoneinfo import available_timezones
        assert set(body["options"]["timezones"]) <= available_timezones()
        assert "UTC" in body["options"]["timezones"]


def test_save_writes_a_whitelisted_setting(env, client):
    r = client.post("/api/settings",
                    json={"values": {"MC_DATE_FORMAT": "%m/%d/%y"}})
    assert r.status_code == 200
    assert r.json()["restart_required"] is True
    assert env_file.read_env()["MC_DATE_FORMAT"] == "%m/%d/%y"


def test_save_refuses_a_key_outside_the_whitelist(env, client):
    r = client.post("/api/settings", json={
        "values": {"ANTHROPIC_BASE_URL": "https://evil.example"}})
    assert r.status_code == 400
    # the whole point: the file is untouched, so the real base URL still stands
    assert env_file.read_env()["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_save_refuses_an_unresolvable_timezone(env, client):
    r = client.post("/api/settings",
                    json={"values": {"MC_TIMEZONE": "Mars/Olympus_Mons"}})
    assert r.status_code == 400
    assert "MC_TIMEZONE" not in env_file.read_env()


def test_save_refuses_an_unknown_date_format(env, client):
    r = client.post("/api/settings", json={"values": {"MC_DATE_FORMAT": "%Q"}})
    assert r.status_code == 400
    assert "MC_DATE_FORMAT" not in env_file.read_env()


def test_one_bad_field_rejects_the_whole_save(env, client):
    r = client.post("/api/settings", json={"values": {
        "MC_DATE_FORMAT": "%m/%d/%y",           # fine on its own
        "MC_LANGUAGE": "kl",                    # not supported
    }})
    assert r.status_code == 400
    # neither was written: a partial save would leave a state nobody chose
    stored = env_file.read_env()
    assert "MC_DATE_FORMAT" not in stored and "MC_LANGUAGE" not in stored


def test_the_api_key_can_be_replaced_but_not_cleared(env, client):
    ok = client.post("/api/settings",
                     json={"values": {"ANTHROPIC_API_KEY": "fake-key-replacement"}})
    assert ok.status_code == 200
    assert env_file.read_env()["ANTHROPIC_API_KEY"] == "fake-key-replacement"
    # and the response doesn't echo what it was just given
    assert "fake-key-replacement" not in json.dumps(ok.json())

    cleared = client.post("/api/settings",
                          json={"values": {"ANTHROPIC_API_KEY": ""}})
    assert cleared.status_code == 400
    assert env_file.read_env()["ANTHROPIC_API_KEY"] == "fake-key-replacement"


def test_effort_is_validated_against_the_selected_model(env, client):
    # Haiku 4.5 takes no effort at all (config.MODEL_EFFORT_LEVELS)
    r = client.post("/api/settings", json={"values": {
        "MC_COMPANION_MODEL": "claude-haiku-4-5",
        "MC_COMPANION_EFFORT": "high",
    }})
    assert r.status_code == 400
    assert "MC_COMPANION_MODEL" not in env_file.read_env()


def test_effort_is_validated_against_the_stored_model(env, client):
    """A model saved but not yet restarted into lives only in .env; config's
    constant still names the old one. Judging the effort against that would
    refuse a valid pair (or accept an impossible one) on the strength of a
    model nothing is going to use."""
    env_file.update_env({"MC_COMPANION_MODEL": "claude-haiku-4-5"})
    # haiku takes no effort at all, whatever config was imported with
    r = client.post("/api/settings",
                    json={"values": {"MC_COMPANION_EFFORT": "high"}})
    assert r.status_code == 400
    assert "MC_COMPANION_EFFORT" not in env_file.read_env()


def test_a_saved_value_comes_back_from_get_before_any_restart(env, client):
    """The regression this exists for: GET used to report config.DATE_FORMAT,
    frozen at import, so a save round-tripped to the old value and looked
    like it had done nothing at all."""
    before = client.get("/api/settings").json()["values"]["MC_DATE_FORMAT"]
    other = next(f for f in ("%B %d, %Y", "%m/%d/%y") if f != before)

    assert client.post("/api/settings",
                       json={"values": {"MC_DATE_FORMAT": other}}).status_code == 200

    body = client.get("/api/settings").json()
    # the file, so the picker shows what was saved ...
    assert body["values"]["MC_DATE_FORMAT"] == other
    # ... and the process, so the UI can say it isn't in effect yet
    assert body["active"]["MC_DATE_FORMAT"] == before


# ---- restart ----

def test_restart_refuses_when_it_cannot_actually_restart(env, client):
    """Under TestClient there is no uvicorn Server to stop, so the route must
    say so. Reporting a restart that never happens would be worse than
    refusing one: the author would wait for a change that never arrives."""
    r = client.post("/api/restart")
    assert r.status_code == 501
    assert not server.RESTART["requested"]


def test_restart_waits_for_the_memory_pipeline(env, client, monkeypatch):
    """Tagging, summaries and seed candidates run after the response and cost
    real API calls. A restart mid-pipeline throws that away silently."""
    monkeypatch.setitem(server.SERVER, "instance", object())   # restartable
    monkeypatch.setitem(server._BUSY, "count", 1)              # ...but busy

    r = client.post("/api/restart")
    assert r.status_code == 409
    assert "still running" in r.json()["error"]
    assert not server.RESTART["requested"]


def test_status_identifies_which_process_answered(env, client):
    """uvicorn keeps serving while it drains, so a 200 from /api/status is
    not proof the restart happened. The client watches this id change."""
    r = client.get("/api/status")
    assert r.json()["instance"] == server.INSTANCE_ID


def test_background_work_is_released_when_the_stream_dies(env, client, monkeypatch):
    """A StreamingResponse that raises never reaches its background tasks, so
    nothing would give the busy count back and /api/restart would answer 409
    for the life of the process."""
    import dreams

    def boom(*a, **k):
        raise RuntimeError("the model call failed mid-stream")

    monkeypatch.setattr(server.companion, "stream_reply", boom)
    monkeypatch.setattr(dreams, "store_dream_entry", lambda *a, **k: "d1")
    monkeypatch.setattr(dreams, "ingest_dream_entry", lambda *a, **k: None)
    monkeypatch.setattr(server.sessions, "append_message", lambda *a, **k: None)

    before = server._BUSY["count"]
    with pytest.raises(RuntimeError):
        client.post("/api/entry", json={"text": "I dreamt of a door", "dream": True})
    assert server._BUSY["count"] == before


def test_a_save_records_its_keys_for_the_restart(env, client):
    """os.execve keeps the environment and load_dotenv() won't override it,
    so the new process must be told to forget exactly what was just saved."""
    server.RESTART["keys"].clear()
    client.post("/api/settings", json={"values": {"MC_DATE_FORMAT": "%m/%d/%y"}})
    assert "MC_DATE_FORMAT" in server.RESTART["keys"]
