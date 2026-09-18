# SPDX-License-Identifier: AGPL-3.0-or-later
"""The first-run wizard's backend (Phase 3 item 5).

What a fresh clone depends on: the server boots with no key at all, says so
in a way the wizard can read, and can tell the author whether the key they
pasted actually works without ever handing it back.

Every test here points env_file.ENV_PATH at a throwaway file, for the same
reason tests/test_settings.py does: the real .env holds the API key, and a
test that rewrote it would be a lockout rather than a failed assertion.
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import env_file
import server

# TrustedHostMiddleware refuses TestClient's default "testserver" host
BASE = "http://127.0.0.1:8144"

# Deliberately not key-shaped: scripts/check_leaks.sh greps every tracked file
# for `sk-ant-` followed by a key body and cannot tell a fixture from the real
# thing. Nothing here depends on the shape, only on the value being
# distinctive enough to find in a response body.
FAKE_KEY = "fake-key-from-the-wizard"


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(env_file, "ENV_PATH", path)
    return path


@pytest.fixture
def client():
    with TestClient(server.app, base_url=BASE) as c:
        yield c


# ---- is the journal configured? ----


def test_no_key_and_no_mock_reads_as_unconfigured(monkeypatch):
    """The condition the whole wizard hangs off. Both halves matter: mock mode
    never constructs the SDK so it needs no key, and the demo instance runs
    mock -- without that the wizard would open over a working demo."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "MOCK_MODE", False)
    assert config.is_configured() is False

    monkeypatch.setattr(config, "MOCK_MODE", True)
    assert config.is_configured() is True

    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert config.is_configured() is True


def test_a_whitespace_only_key_is_not_a_key(monkeypatch):
    """`ANTHROPIC_API_KEY=` left in .env is the shape a half-finished hand
    edit leaves behind. Reading it as configured would skip the wizard and
    hand the SDK a blank key instead."""
    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert config.is_configured() is False


def test_status_reports_whether_setup_is_needed(client, monkeypatch):
    """The frontend gates on this field; it has to survive in the payload."""
    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/status").json()["configured"] is False

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    assert client.get("/api/status").json()["configured"] is True


def test_status_reports_whether_the_demo_has_been_built(
        client, monkeypatch, tmp_path):
    """What lets the UI mention the build wait exactly once. Both doors into
    the demo -- the wizard's and Settings' -- warn about the twenty seconds
    only while there is something to warn about; a warning that fired every
    time would train the reader to ignore it.

    Answered by the completion marker and database, the same way the install
    route asks, so an interrupted build still reads as unbuilt.
    """
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    assert client.get("/api/status").json()["demo_built"] is False

    (tmp_path / "chroma_data").mkdir()
    (tmp_path / "chroma_data" / "chroma.sqlite3").touch()
    (tmp_path / "chroma_data" / ".install-complete").touch()
    assert client.get("/api/status").json()["demo_built"] is True


EF_MODULE = "chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2"


def test_status_says_whether_the_embedding_model_is_already_here(
        client, monkeypatch, tmp_path):
    """The difference between "twenty seconds" and "a few minutes", and the
    reason the wait wording is not one fixed string.

    Chroma fetches all-MiniLM-L6-v2 at the first embed on the machine and
    caches it under the user's home rather than the project -- so from the
    wizard, where nothing has ever been embedded, the demo build is what pays
    for it, and from Settings it usually is not.

    The path is read off chroma's own embedding-function class rather than
    written out here, so this asserts against a stand-in for that class: what
    matters is that the answer follows the library's attributes.
    """
    fake = SimpleNamespace(ONNXMiniLM_L6_V2=SimpleNamespace(
        DOWNLOAD_PATH=str(tmp_path), EXTRACTED_FOLDER_NAME="onnx"))
    monkeypatch.setitem(sys.modules, EF_MODULE, fake)
    assert client.get("/api/status").json()["embedder_cached"] is False

    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx" / "model.onnx").touch()
    assert client.get("/api/status").json()["embedder_cached"] is True


def test_a_chroma_that_moved_its_cache_makes_the_answer_unknown(
        client, monkeypatch):
    """This is a display hint and nothing else depends on it, so a chroma
    release that renames or relocates the class must not be able to take the
    status route down with it. Unknown is a supported answer -- the UI has
    hedged wording waiting for exactly this -- and a confident wrong estimate
    would be worse than an honest vague one.
    """
    monkeypatch.setitem(sys.modules, EF_MODULE, SimpleNamespace())  # no class
    assert server._embedder_cached() is None
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["embedder_cached"] is None


def test_the_server_boots_with_no_key_at_all(monkeypatch):
    """The regression this exists for: startup() used to build the Anthropic
    client unconditionally, and the SDK raises at *construction* when no key
    is present -- so a fresh clone's first run was a stack trace in a
    terminal, with no way to reach a screen that could ask for a key."""
    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def explode():
        raise AssertionError("the SDK must not be constructed without a key")

    monkeypatch.setattr(server, "get_client", explode)
    before = server.STATE["client"]
    try:
        with TestClient(server.app, base_url=BASE) as c:
            assert c.get("/api/status").status_code == 200
            assert server.STATE["client"] is None
    finally:
        server.STATE["client"] = before


# ---- the routes that need a client ----


def test_writing_is_refused_while_unconfigured(client, monkeypatch):
    """Nothing in the UI can reach these -- the wizard covers the screen --
    but a stray fetch or a tab left open across the setup should get a
    readable 409 rather than an AttributeError out of a None client."""
    monkeypatch.setitem(server.STATE, "client", None)
    for url, payload in (
        ("/api/chat", {"message": "hello"}),
        ("/api/lookup", {"message": "hello"}),
        ("/api/entry", {"text": "my first entry"}),
        ("/api/reflect", {}),
    ):
        r = client.post(url, json=payload)
        assert r.status_code == 409, url
        assert "no API key yet" in r.json()["error"], url


def test_a_no_reply_entry_still_works_while_unconfigured(client, monkeypatch):
    """A no-reply save makes no Claude call at all, so it needs no client --
    and the guard sits past that branch on purpose. Someone who reaches the
    write screen with the key still missing can at least keep their writing."""
    monkeypatch.setitem(server.STATE, "client", None)
    saved = {}

    def backup(text, when=None):
        saved["text"] = text
        return Path("entry.md")

    monkeypatch.setattr(server.sessions, "append_message", lambda *a, **k: None)
    monkeypatch.setattr(server.sessions, "backup_entry_text", backup)
    monkeypatch.setattr(server.sessions, "record_artifact", lambda *a, **k: None)

    r = client.post("/api/entry", json={"text": "kept anyway", "no_reply": True})
    assert r.status_code == 200
    assert r.json()["no_reply"] is True
    assert saved["text"] == "kept anyway"


# ---- validating a key ----


def _fake_sdk(monkeypatch, on_create):
    """Stand in for the `anthropic` module the route imports lazily."""
    monkeypatch.setattr(server, "MOCK_MODE", False)
    class FakeClient:
        def __init__(self, api_key=None, **kw):
            self.api_key = api_key
            self.messages = SimpleNamespace(create=on_create)

    monkeypatch.setitem(sys.modules, "anthropic",
                        SimpleNamespace(Anthropic=FakeClient))


def test_key_validation_obeys_caps(client, monkeypatch):
    def forbidden(**kw):
        raise AssertionError("SDK must not be called after the cap")
    _fake_sdk(monkeypatch, forbidden)
    def exhausted():
        raise server.caps.CapExceeded("cap exhausted")
    monkeypatch.setattr(server.caps, "check", exhausted)
    assert client.post("/api/setup/validate-key", json={"key": FAKE_KEY}).status_code == 429


def test_key_validation_records_real_spend(client, monkeypatch, tmp_path):
    _fake_sdk(monkeypatch, lambda **kw: SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=1)))
    monkeypatch.setattr(config, "MOCK_MODE", False)
    monkeypatch.setattr(config, "SPEND_FILE", tmp_path / "spend.json")
    before = server.metering.totals()["total"]["calls"]
    assert client.post("/api/setup/validate-key", json={"key": FAKE_KEY}).json() == {"ok": True}
    assert server.metering.totals()["total"]["calls"] == before + 1
    assert server.caps.spent_this_month() > 0


def test_key_validation_refuses_mock_mode(client, monkeypatch):
    monkeypatch.setattr(server, "MOCK_MODE", True)
    assert client.post("/api/setup/validate-key", json={"key": FAKE_KEY}).status_code == 409


def test_a_working_key_validates(client, monkeypatch):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[])

    _fake_sdk(monkeypatch, create)
    r = client.post("/api/setup/validate-key", json={"key": FAKE_KEY})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # the cheapest call the API sells -- one token, smallest model. A future
    # edit that quietly makes this an Opus call would cost every cloner.
    assert seen["max_tokens"] == 1
    assert seen["model"] == "claude-haiku-4-5"


def test_a_bad_key_comes_back_as_a_verdict_not_a_failure(client, monkeypatch):
    """200 with ok:false, deliberately: the route did its job and the verdict
    IS the result. The wizard renders the API's own words under the field,
    because "invalid x-api-key" and "credit balance is too low" send someone
    to two different places."""
    def create(**kw):
        raise RuntimeError("invalid x-api-key")

    _fake_sdk(monkeypatch, create)
    r = client.post("/api/setup/validate-key", json={"key": FAKE_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "invalid x-api-key" in body["error"]


def test_a_structured_api_error_is_reduced_to_its_sentence(client, monkeypatch):
    """The SDK stringifies a status error as the whole response dict. That
    belongs in a log, not under a text field on a stranger's first screen --
    and the body carries one sentence worth reading."""
    class Boom(RuntimeError):
        body = {"type": "error",
                "error": {"type": "authentication_error",
                          "message": "invalid x-api-key"},
                "request_id": "req_abc123"}

    def create(**kw):
        raise Boom("Error code: 401 - {'type': 'error', 'error': {...}}")

    _fake_sdk(monkeypatch, create)
    body = client.post("/api/setup/validate-key", json={"key": FAKE_KEY}).json()
    assert body["error"] == "invalid x-api-key"
    assert "request_id" not in body["error"]


def test_an_unstructured_failure_keeps_its_whole_message(client, monkeypatch):
    """A connection error has no `body` to read, and "could not connect" is
    the actionable part -- so the fallback has to be the full text, not a
    shrug."""
    def create(**kw):
        raise RuntimeError("Connection error: name resolution failed")

    _fake_sdk(monkeypatch, create)
    body = client.post("/api/setup/validate-key", json={"key": FAKE_KEY}).json()
    assert "Connection error" in body["error"]


def test_a_failed_validation_never_echoes_the_key(client, monkeypatch):
    """Item 5's key handling is one-directional. An error message is the one
    plausible way back out, so it is filtered rather than trusted."""
    def create(**kw):
        raise RuntimeError(f"authentication failed for {FAKE_KEY}")

    _fake_sdk(monkeypatch, create)
    r = client.post("/api/setup/validate-key", json={"key": FAKE_KEY})
    assert FAKE_KEY not in json.dumps(r.json())
    assert FAKE_KEY not in r.text


def test_validating_never_writes_the_key_anywhere(client, monkeypatch, env):
    """Validation is a question, not a commitment. A key that turns out to be
    wrong must not have landed in .env on the way to finding that out."""
    _fake_sdk(monkeypatch, lambda **kw: SimpleNamespace(content=[]))
    client.post("/api/setup/validate-key", json={"key": FAKE_KEY})
    assert not env.exists()


def test_an_empty_key_is_refused_without_a_call(client, monkeypatch):
    def create(**kw):
        raise AssertionError("no call should be made for an empty key")

    _fake_sdk(monkeypatch, create)
    r = client.post("/api/setup/validate-key", json={"key": "   "})
    assert r.status_code == 400


# ---- what the wizard commits ----


def test_the_wizard_write_lands_in_a_fresh_env_file(client, env):
    """The whole point of the wizard, end to end: a clone with no .env at all
    ends up with one holding the key, the zone and the trimmed category set.
    It goes through the settings route rather than a writer of its own."""
    assert not env.exists()
    r = client.post("/api/settings", json={"values": {
        "ANTHROPIC_API_KEY": FAKE_KEY,
        "MC_TIMEZONE": "America/Chicago",
        "MC_DISABLED_CATEGORIES": "pets",
    }})
    assert r.status_code == 200, r.text
    stored = env_file.read_env()
    assert stored["ANTHROPIC_API_KEY"] == FAKE_KEY
    assert stored["MC_TIMEZONE"] == "America/Chicago"
    assert stored["MC_DISABLED_CATEGORIES"] == "pets"
    # ...and the response does not hand the key back, not even the one it was
    # just given
    assert FAKE_KEY not in r.text


@pytest.mark.skipif(os.name == "nt",
                    reason="POSIX mode bits; Windows protects by ACL instead")
def test_a_written_env_is_not_readable_by_anyone_else(env):
    """It holds the API key, and on a fresh clone this module is what brings
    the file into existence -- so the permissions are this code's to get
    right, not the author's to remember."""
    env_file.update_env({"ANTHROPIC_API_KEY": FAKE_KEY})
    assert env.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt",
                    reason="POSIX mode bits; Windows protects by ACL instead")
def test_a_loose_env_is_tightened_by_the_next_save(env):
    """Covers the clone that hand-made its .env before ever opening the app."""
    env.write_text("MC_TIMEZONE=UTC\n", encoding="utf-8")
    env.chmod(0o644)
    env_file.update_env({"ANTHROPIC_API_KEY": FAKE_KEY})
    assert env.stat().st_mode & 0o777 == 0o600


# ---- building the demo on demand ----
#
# The demo corpus is gitignored, so a fresh clone has none. Until this route
# the only way to build it was a terminal command, which is the one place a
# first-run flow cannot follow someone.


def test_installing_the_demo_spawns_a_fresh_interpreter(client, monkeypatch, tmp_path):
    """A child process, not an import, and the argv is what proves it.

    `import_seed_corpus` aims the eight data dirs at seed_corpus/install/ at
    module-import time, before it imports config. In this process config was
    imported long ago against the author's real journal -- so importing the
    installer here would write 30 fictional entries into their own entries.
    """
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)   # nothing installed
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["cwd"] = kw.get("cwd")
        return SimpleNamespace(returncode=0, stdout="done.", stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    r = client.post("/api/setup/install-demo")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "built": True}
    assert seen["argv"][0] == sys.executable
    assert seen["argv"][1].endswith("import_seed_corpus.py")
    assert seen["argv"][2] == "--demo"
    # a fixed list: nothing user-supplied can reach the command line
    assert len(seen["argv"]) == 3


def test_an_installed_demo_is_not_rebuilt(client, monkeypatch, tmp_path):
    """Only a completed install is skipped; a database alone is not enough."""
    (tmp_path / "chroma_data").mkdir()
    (tmp_path / "chroma_data" / "chroma.sqlite3").touch()
    (tmp_path / "chroma_data" / ".install-complete").touch()
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)

    def fake_run(argv, **kw):
        raise AssertionError("an installed demo must not be rebuilt")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    r = client.post("/api/setup/install-demo")
    assert r.json() == {"ok": True, "built": False}


def test_an_interrupted_demo_build_can_retry(client, monkeypatch, tmp_path):
    chroma = tmp_path / "chroma_data"
    chroma.mkdir()
    (chroma / "chroma.sqlite3").touch()
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    assert client.get("/api/status").json()["demo_built"] is False
    assert client.post("/api/restart", json={"into": "seed"}).status_code == 409

    calls = []

    def build(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="download failed")
        (chroma / ".install-complete").touch()
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(server.subprocess, "run", build)
    assert client.post("/api/setup/install-demo").status_code == 500
    assert client.post("/api/setup/install-demo").json() == {"ok": True, "built": True}
    assert len(calls) == 2
    assert client.get("/api/status").json()["demo_built"] is True


def test_demo_install_refuses_concurrent_rebuild(client):
    with server._DEMO_LOCK:
        assert client.post("/api/setup/install-demo").status_code == 409


def test_the_demo_cannot_rebuild_itself_from_inside(client, monkeypatch, tmp_path):
    """The running demo holds its own Chroma files open. A rebuild from in
    there deletes the markdown out from under the journal being read while
    the locked index survives -- 29 entries on the status line with nothing
    behind them. This guard exists because that happened."""
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    monkeypatch.setattr(server, "SEED_INSTANCE", True)

    def fake_run(argv, **kw):
        raise AssertionError("must not rebuild the demo it is running on")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    r = client.post("/api/setup/install-demo")
    assert r.status_code == 409
    assert "restart back to your own journal" in r.json()["error"]


def test_a_failed_build_reports_the_installers_own_last_line(
        client, monkeypatch, tmp_path):
    """The installer's refusals are one useful sentence on stderr -- it is the
    same script a person would run by hand. "refusing to install over a
    journal I did not ship" is something someone can act on; "it failed" is
    not."""
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)
    monkeypatch.setattr(server.subprocess, "run", lambda argv, **kw: SimpleNamespace(
        returncode=1, stdout="",
        stderr="checking...\nrefusing to install over a journal I did not ship"))

    r = client.post("/api/setup/install-demo")
    assert r.status_code == 500
    assert "did not ship" in r.json()["error"]


def test_a_hung_build_is_stopped_rather_than_hanging_the_page(
        client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "SEED_ROOT", tmp_path)

    def hang(argv, **kw):
        raise server.subprocess.TimeoutExpired(argv, kw.get("timeout", 900))

    monkeypatch.setattr(server.subprocess, "run", hang)
    r = client.post("/api/setup/install-demo")
    assert r.status_code == 504
    # and it still names the command, so a stuck build can be watched by hand
    assert "import_seed_corpus.py --demo" in r.json()["error"]
