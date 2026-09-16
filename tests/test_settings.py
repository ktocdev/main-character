# SPDX-License-Identifier: AGPL-3.0-or-later
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
from types import SimpleNamespace
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
    "MC_AUTHOR_NAME=Jordan\n"
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
    env_file.update_env({"MC_AUTHOR_NAME": "Katina"})
    lines = env.read_text(encoding="utf-8").splitlines()
    # still directly under its own comment, not appended to the end
    assert lines[lines.index("# who the entries are by") + 1] == "MC_AUTHOR_NAME=Katina"


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
    env_file.update_env({"MC_AUTHOR_NAME": "Jordan # not a comment"})
    assert env_file.read_env()["MC_AUTHOR_NAME"] == "Jordan # not a comment"


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


# ---- models & cost ----

def test_the_three_model_maps_cover_the_same_models(env):
    """Effort levels, thinking support, labels and prices are four separate
    dicts keyed by model id. A model added to one and missed in another is a
    picker with no price, or a call that sends a parameter the model rejects."""
    import config
    models = set(config.MODEL_EFFORT_LEVELS)
    assert set(config.MODEL_THINKING_SUPPORT) == models
    assert set(config.MODEL_LABELS) == models
    assert set(config.MODEL_PRICES) == models
    for m, price in config.MODEL_PRICES.items():
        assert price["in"] > 0 and price["out"] > 0, m


def test_get_offers_the_full_lineup_to_both_pickers(env, client):
    """Neither bucket restricts the options (Phase 0 item 6) -- the companion
    and processing pickers read the same list and differ only in their copy."""
    import config
    body = client.get("/api/settings").json()
    models = body["options"]["models"]
    assert [m["value"] for m in models] == list(config.MODEL_EFFORT_LEVELS)
    for m in models:
        assert m["label"] and m["price"]["in"] > 0
    # the effort list travels with each model, so the picker can repopulate
    # without a second request -- and empty is meaningful, not missing
    haiku = next(m for m in models if m["value"] == "claude-haiku-4-5")
    assert haiku["efforts"] == [] and haiku["thinking"] is False


def test_get_reports_the_model_settings_as_file_and_process(env, client):
    body = client.get("/api/settings").json()
    for key in ("MC_COMPANION_MODEL", "MC_COMPANION_EFFORT", "MC_PROCESSING_MODEL"):
        assert key in body["values"] and key in body["active"]


def test_a_model_and_its_effort_save_together(env, client):
    """The picker sends both when a model change invalidates the effort.
    Validation has to judge the effort against the model *in the same save*,
    not the one already on disk, or a legal pair is rejected."""
    ok = client.post("/api/settings", json={"values": {
        "MC_COMPANION_MODEL": "claude-opus-4-7",
        "MC_COMPANION_EFFORT": "xhigh",          # 4.7 has it, 4.6 does not
    }})
    assert ok.status_code == 200
    stored = env_file.read_env()
    assert stored["MC_COMPANION_MODEL"] == "claude-opus-4-7"
    assert stored["MC_COMPANION_EFFORT"] == "xhigh"


def test_the_stored_model_can_also_widen_what_effort_is_allowed(env, client):
    """The other direction of test_effort_is_validated_against_the_stored_model
    above, and the reason both exist: that one proves a stored model can
    *refuse* an effort, which a validator that rejected everything would also
    pass. This proves it can permit one the running model would not."""
    client.post("/api/settings",
                json={"values": {"MC_COMPANION_MODEL": "claude-opus-4-7"}})
    # xhigh is invalid for the default 4.6 the process is still running on,
    # and valid for the 4.7 now in .env. The file is what counts.
    r = client.post("/api/settings", json={"values": {"MC_COMPANION_EFFORT": "xhigh"}})
    assert r.status_code == 200
    assert env_file.read_env()["MC_COMPANION_EFFORT"] == "xhigh"


def test_spend_caps_round_trip_through_the_file(env, client):
    """These were stored-but-unenforced for the whole of item 7; `caps.py`
    reads them now, and `tests/test_caps.py` covers the enforcing. What is
    still this module's business is the file: a cap that does not survive the
    save is a ceiling the author believes they set."""
    body = client.get("/api/settings").json()
    assert body["spend_caps_enforced"] is True
    assert body["values"]["MC_MAX_SESSION_SPEND"] == ""
    # ...and they are not in `active`: that dict is what *this process*
    # loaded at import, and the caps are read live from config on each check
    assert "MC_MAX_SESSION_SPEND" not in body["active"]

    assert client.post("/api/settings", json={
        "values": {"MC_MAX_MONTHLY_SPEND": "20"}}).status_code == 200
    assert env_file.read_env()["MC_MAX_MONTHLY_SPEND"] == "20"

    # ...and GET hands it back, which is what the Settings pane fills its
    # inputs from. A saved cap that came back blank would read as unset, and
    # the next save would clear it.
    after = client.get("/api/settings").json()
    assert after["values"]["MC_MAX_MONTHLY_SPEND"] == "20"


def test_a_negative_spend_cap_is_refused(env, client):
    r = client.post("/api/settings", json={"values": {"MC_MAX_SESSION_SPEND": "-1"}})
    assert r.status_code == 400
    assert "MC_MAX_SESSION_SPEND" not in env_file.read_env()


def test_an_unknown_model_is_refused(env, client):
    r = client.post("/api/settings",
                    json={"values": {"MC_PROCESSING_MODEL": "claude-imaginary-9"}})
    assert r.status_code == 400
    assert "MC_PROCESSING_MODEL" not in env_file.read_env()


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
    assert "companion reply" in r.json()["error"]   # not the pipeline alone
    assert not server.RESTART["requested"]


def test_a_streaming_reply_holds_the_restart_off(env, client, monkeypatch):
    """The tokens are spent while the text is still arriving, so a stream has
    to count as busy. Only the dream path did before, which left the common
    case -- a chat turn, a plain entry -- looking idle to /api/restart while
    a paid-for reply was mid-flight."""
    before = server._BUSY["count"]
    seen = []

    def fake(*a, **k):
        seen.append(server._BUSY["count"])
        yield "a reply, arriving"

    monkeypatch.setattr(server.companion, "stream_reply", fake)

    r = client.post("/api/lookup", json={"message": "when did I last write?"})
    assert r.status_code == 200
    assert seen == [before + 1]          # counted while the tokens were spending
    assert server._BUSY["count"] == before   # and given back at the end


def test_the_count_is_given_back_when_a_stream_dies(env, client, monkeypatch):
    """A count taken for a stream and not returned would make /api/restart
    answer 409 for the life of the process -- the same failure _tracked
    already had to be taught, arriving by a different door."""
    def fake(*a, **k):
        yield "half a "
        raise RuntimeError("the model call failed mid-stream")

    monkeypatch.setattr(server.companion, "stream_reply", fake)

    before = server._BUSY["count"]
    with pytest.raises(RuntimeError):
        client.post("/api/lookup", json={"message": "when did I last write?"})
    assert server._BUSY["count"] == before


# ---- the seed instance ----
#
# `server.RESTART` is module-global and these set it, so every test here
# restores it through monkeypatch rather than by hand: leaving `requested`
# True leaks a pending restart into whatever runs next, which is how the
# first draft of these tests failed only when the suite ran in order.

def test_the_seed_destination_lives_only_in_the_child_environment(
        env, client, monkeypatch, tmp_path):
    """Nothing about the seed instance is written to .env. That is the whole
    mechanism for getting back out: the destination dies with the process, so
    any later restart lands on real data."""
    (tmp_path / "chroma_data").mkdir()
    (tmp_path / "chroma_data" / "chroma.sqlite3").touch()
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    # not object(): this one gets all the way to `srv.should_exit = True`,
    # which a bare object cannot carry
    monkeypatch.setitem(server.SERVER, "instance", SimpleNamespace())
    monkeypatch.setitem(server.RESTART, "requested", False)
    monkeypatch.setitem(server.RESTART, "into", "journal")

    r = client.post("/api/restart", json={"into": "seed"})
    assert r.status_code == 200 and r.json()["into"] == "seed"
    assert "MC_SEED_INSTANCE" not in env_file.read_env()

    child = server.restart_env()
    assert child["MC_SEED_INSTANCE"] == "1"
    assert child["MC_JOURNAL_DIR"].endswith("journal_entries")


def test_a_plain_restart_always_leaves_the_seed_instance(env, monkeypatch):
    """The way home is any restart at all. The keys are dropped
    unconditionally and only put back when the seed is asked for by name, so a
    demo is one restart deep and cannot be wandered into permanently."""
    monkeypatch.setitem(server.os.environ, "MC_SEED_INSTANCE", "1")
    monkeypatch.setitem(server.os.environ, "MC_JOURNAL_DIR",
                        "seed_corpus/install/journal_entries")
    monkeypatch.setitem(server.RESTART, "into", "journal")

    child = server.restart_env()
    assert "MC_SEED_INSTANCE" not in child
    assert "MC_JOURNAL_DIR" not in child


def test_an_uninstalled_seed_corpus_is_refused_not_booted_empty(
        env, client, monkeypatch, tmp_path):
    """An author who asked for the corpus and got a blank journal has no way
    to tell that from a broken one."""
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)   # nothing installed
    monkeypatch.setitem(server.SERVER, "instance", object())
    monkeypatch.setitem(server.RESTART, "requested", False)

    r = client.post("/api/restart", json={"into": "seed"})
    assert r.status_code == 409
    assert "not installed" in r.json()["error"]
    assert not server.RESTART["requested"]


def test_a_partially_built_seed_corpus_is_refused_too(
        env, client, monkeypatch, tmp_path):
    """An interrupted `run_capture.sh --wipe` can leave chroma_data/ created
    but empty. The directory existing is not the same as the corpus being
    installed, and booting into it would be exactly the blank-journal
    failure the 409 above exists to prevent."""
    (tmp_path / "chroma_data").mkdir()   # no chroma.sqlite3 inside
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    monkeypatch.setitem(server.SERVER, "instance", object())
    monkeypatch.setitem(server.RESTART, "requested", False)

    r = client.post("/api/restart", json={"into": "seed"})
    assert r.status_code == 409
    assert "not installed" in r.json()["error"]
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
    monkeypatch.setattr(dreams, "store_dream_entry",
                        lambda *a, **k: ("d1", Path("d1.md")))
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


# ---- categories ----

def test_get_lists_the_built_in_categories(env, client):
    """The toggles render from this, so every built-in has to arrive with its
    definition, and the disabled line is reported like any other setting -- in
    both `values` (the file) and `active` (the process)."""
    import categories as cats
    body = client.get("/api/settings").json()
    offered = body["options"]["categories"]
    assert [c["name"] for c in offered] == list(cats.CATEGORIES)
    assert all(c["description"] for c in offered)
    assert "MC_DISABLED_CATEGORIES" in body["values"]
    assert "MC_DISABLED_CATEGORIES" in body["active"]


def test_disabling_categories_round_trips_and_is_normalised(env, client):
    """Stored in built-in order regardless of the order the toggles were sent,
    so the line is stable and the pending-marker comparison stays honest."""
    r = client.post("/api/settings",
                    json={"values": {"MC_DISABLED_CATEGORIES": "pets,relationships"}})
    assert r.status_code == 200
    # relationships precedes pets in the built-in list
    assert env_file.read_env()["MC_DISABLED_CATEGORIES"] == "relationships,pets"
    after = client.get("/api/settings").json()
    assert after["values"]["MC_DISABLED_CATEGORIES"] == "relationships,pets"


def test_disabling_an_unknown_category_is_refused(env, client):
    r = client.post("/api/settings", json={
        "values": {"MC_DISABLED_CATEGORIES": "work,not_a_category"}})
    assert r.status_code == 400
    assert "MC_DISABLED_CATEGORIES" not in env_file.read_env()


def test_disabling_every_category_is_refused(env, client):
    """An empty enum is a schema the API rejects, and a tagger that can offer
    nothing is a worse state than any one category being on: one has to stay."""
    import categories as cats
    r = client.post("/api/settings", json={
        "values": {"MC_DISABLED_CATEGORIES": ",".join(cats.CATEGORIES)}})
    assert r.status_code == 400
    assert "MC_DISABLED_CATEGORIES" not in env_file.read_env()


def test_clearing_the_disabled_line_turns_everything_back_on(env, client):
    client.post("/api/settings",
                json={"values": {"MC_DISABLED_CATEGORIES": "pets"}})
    assert env_file.read_env()["MC_DISABLED_CATEGORIES"] == "pets"
    client.post("/api/settings",
                json={"values": {"MC_DISABLED_CATEGORIES": ""}})
    # gone from the file entirely, so config's default (all on) applies again
    assert "MC_DISABLED_CATEGORIES" not in env_file.read_env()


def test_enabled_categories_drops_the_disabled_ones(monkeypatch):
    import config
    import categories as cats
    monkeypatch.setattr(config, "DISABLED_CATEGORIES", ["pets", "relationships"])
    enabled = cats.enabled_categories()
    assert "pets" not in enabled and "relationships" not in enabled
    assert "work" in enabled
    # the definition rides along, so the tagging prompt still describes it
    assert enabled["work"] == cats.CATEGORIES["work"]


def test_tagging_never_offers_or_applies_a_disabled_category(monkeypatch):
    """Two guarantees at once: the schema handed to the model excludes the
    disabled name, and a reply that names it anyway (an old mock fixture, a
    rename) is dropped rather than applied."""
    import config
    import categories as cats
    monkeypatch.setattr(config, "DISABLED_CATEGORIES", ["pets"])
    seen = {}

    def create(**kw):
        seen["enum"] = kw["output_config"]["format"]["schema"]["properties"][
            "categories"]["items"]["properties"]["name"]["enum"]
        text = json.dumps({"categories": [
            {"name": "work", "evidence": "job stuff"},
            {"name": "pets", "evidence": "the dog wandered through"},
        ]})
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    conv = {"text": "a short entry about my job", "date": "2026-08-01", "title": "Work"}
    tags = cats.tag_conversation(client, conv)

    assert "pets" not in seen["enum"] and "work" in seen["enum"]
    assert "work" in tags and "pets" not in tags
