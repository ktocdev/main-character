"""
The spend ceilings: what stops a call, and what a stopped call looks like.

The failure that matters here is not a crash. It is a cap that reads as set
and does not fire -- an author who believes they are protected and is not --
or the mirror of it, a ledger that records fictional mock-mode dollars and
refuses real calls on the strength of them.

Every test points `config.SPEND_FILE` at a throwaway path. Nothing here may
touch the real ledger: it is the only record of what the journal has actually
spent this month, and a test that overwrote it would lift the cap it feeds.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import caps
import config
import metering
import server

BASE = "http://127.0.0.1:8144"


@pytest.fixture
def spend_file(tmp_path, monkeypatch):
    """A throwaway ledger path. Nothing here may touch the real one."""
    path = tmp_path / "spend_ledger.json"
    monkeypatch.setattr(config, "SPEND_FILE", path)
    metering.reset()
    return path


@pytest.fixture
def ledger(spend_file, monkeypatch):
    """...with mock mode off, so the writes under test actually happen."""
    monkeypatch.setattr(config, "MOCK_MODE", False)
    return spend_file


@pytest.fixture
def client(spend_file):
    """Deliberately *not* built on `ledger`: booting the app re-reads
    config.MOCK_MODE, and with it off the startup handler constructs the real
    Anthropic SDK against whatever key is in .env. That breaks the smoke
    test's "the SDK is never imported in mock mode" assertion for every test
    that runs after it, and builds a live client for no reason. Route tests
    put the ledger over the line by writing the file instead -- which is what
    a restart would find anyway."""
    with TestClient(server.app, base_url=BASE) as c:
        yield c


def already_spent(path, dollars):
    """Put this month's ledger over a line without needing a live writer."""
    path.write_text(json.dumps({caps.month_key(): dollars}), encoding="utf-8")


# ---- the monthly ledger ---------------------------------------------------

def test_spend_accumulates_under_the_current_month(ledger):
    caps.record_spend(1.25)
    caps.record_spend(0.75)
    assert json.loads(ledger.read_text())[caps.month_key()] == 2.0
    assert caps.spent_this_month() == 2.0


def test_another_month_is_not_this_month(ledger):
    """The key is computed per call, so a session running past midnight on the
    1st starts filling the new month by itself. Nothing watches for the
    rollover, which is the point -- a watcher is a thing that can be wrong."""
    ledger.write_text(json.dumps({"1999-01": 500.0}))
    assert caps.spent_this_month() == 0.0
    caps.record_spend(3.0)
    stored = json.loads(ledger.read_text())
    assert stored["1999-01"] == 500.0        # untouched
    assert stored[caps.month_key()] == 3.0


def test_a_corrupt_ledger_reads_as_empty_rather_than_raising(ledger):
    """A spend estimate must never be the reason the journal won't start."""
    ledger.write_text("{not json at all")
    assert caps.spent_this_month() == 0.0


def test_mock_mode_never_writes_the_ledger(ledger, monkeypatch):
    """Mock usage is fictional. A fictional dollar in the file that decides
    whether real calls are allowed is worse than no file at all."""
    monkeypatch.setattr(config, "MOCK_MODE", True)
    caps.record_spend(50.0)
    assert not ledger.exists()
    assert caps.status()["monthly"]["recording"] is False


# ---- the check ------------------------------------------------------------

def test_no_cap_set_means_nothing_is_refused(ledger, monkeypatch):
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 0)
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 0)
    caps.record_spend(10_000.0)
    caps.check()          # no raise


def test_the_session_ceiling_refuses_the_next_call(ledger, monkeypatch):
    """In dollars, not tokens: the ceiling is a setting someone has to have an
    opinion about, and nobody knows whether 5,000,000 tokens is an afternoon
    or a month. It reads the same accumulator the cost meter shows."""
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 10.0)
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 0)
    caps.check()          # nothing spent yet

    metering.SESSION["companion"]["dollars"] = 10.01
    with pytest.raises(caps.CapExceeded) as exc:
        caps.check()
    # the message has to name the cap and the way out, or the only available
    # reading is that the journal is broken
    assert "MC_MAX_SESSION_SPEND" in exc.value.detail


def test_the_session_ceiling_is_cleared_by_closing_a_chat(ledger, monkeypatch):
    """`metering.reset()` runs at close, so the way out named in the message
    is a real one. If that ever stops being true the message is a lie."""
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 1.0)
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 0)
    metering.SESSION["companion"]["dollars"] = 5.0
    with pytest.raises(caps.CapExceeded):
        caps.check()
    metering.reset()
    caps.check()          # no raise


def test_the_monthly_ceiling_refuses_the_next_call(ledger, monkeypatch):
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 0)
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 5.0)
    caps.record_spend(4.99)
    caps.check()          # under it

    caps.record_spend(0.02)
    with pytest.raises(caps.CapExceeded) as exc:
        caps.check()
    assert "MC_MAX_MONTHLY_SPEND" in exc.value.detail


def test_the_client_proxy_refuses_before_the_call_is_made(ledger, monkeypatch):
    """The check has to sit in front of the SDK, not in front of the routes:
    the background pipeline calls the client directly, and a ceiling only the
    routes enforced would be no ceiling at all during a session close."""
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 1.0)
    caps.record_spend(2.0)

    called = []

    class Inner:
        class messages:
            @staticmethod
            def create(**kwargs):
                called.append(kwargs)
                return None

    with pytest.raises(caps.CapExceeded):
        metering.wrap(Inner()).messages.create(model="claude-sonnet-5")
    assert called == []


# ---- the routes -----------------------------------------------------------

def test_a_capped_journal_refuses_an_entry_with_429(client, spend_file, monkeypatch):
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 1.0)
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 0)
    already_spent(spend_file, 2.0)

    r = client.post("/api/entry", json={"text": "a day worth writing down"})
    assert r.status_code == 429
    assert "MC_MAX_MONTHLY_SPEND" in r.json()["error"]


def test_the_refusal_arrives_as_a_status_not_as_an_empty_stream(
        client, spend_file, monkeypatch):
    """A StreamingResponse has already sent its status line by the time the
    generator runs, so the check has to happen in the route as well as in the
    proxy -- otherwise a refusal is a 200 that stops mid-sentence."""
    monkeypatch.setattr(config, "MAX_MONTHLY_SPEND", 1.0)
    monkeypatch.setattr(config, "MAX_SESSION_SPEND", 0)
    already_spent(spend_file, 2.0)

    for path in ("/api/chat", "/api/lookup"):
        r = client.post(path, json={"message": "what did I write about?"})
        assert r.status_code == 429, path


def test_an_oversized_entry_is_refused_rather_than_truncated(client):
    """Silently cutting an entry in half loses writing the author believes is
    saved, and they will not find out until they go looking for it."""
    r = client.post("/api/entry",
                    json={"text": "x" * (config.MAX_INPUT_CHARS + 1)})
    assert r.status_code == 413
    assert "Nothing was saved" in r.json()["error"]


def test_status_reports_the_default_beside_the_limit_in_force(client):
    """The pane says what an empty box will do, and cannot say it without the
    default as a number. Reporting only the limit left it saying "blank uses
    the default" over a box whose placeholder read "no limit"."""
    body = client.get("/api/settings").json()["caps"]
    assert body["session"]["default"] == config.DEFAULT_SESSION_SPEND
    assert body["monthly"]["default"] == config.DEFAULT_MONTHLY_SPEND


def test_settings_reports_the_caps_as_enforced(client):
    """This was False for the whole of item 7, and the pane keyed on it to
    warn that a cap in .env was stored but protecting nobody."""
    body = client.get("/api/settings").json()
    assert body["spend_caps_enforced"] is True
    assert body["caps"]["session"]["limit"] == config.MAX_SESSION_SPEND
    assert body["caps"]["monthly"]["month"] == caps.month_key()
