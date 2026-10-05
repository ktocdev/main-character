# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smart replies on the chat tab (lookup_tools.py, companion.stream_smart_lookup,
LOOKUP-UPGRADE-HANDOFF.md step 5).

  * the tools: list_entries lists a range oldest first; exact search counts
    every entry a phrase is in, newest first, within the dates asked; a
    search by meaning keeps to its dates; read_entry puts an entry's chunks
    back in order, and names the day's entries when the title is wrong; a
    bad date is an error the model can read, not an exception;
  * the loop: tool calls are run and their results sent back, round after
    round; the last round has tools off, so there are never more calls than
    SMART_REPLY_ROUNDS; the history keeps every round as it came back; a
    round that fails leaves the history as it was before the question;
  * the route uses it only when the chat tab's toggle asks and the client
    is real: the demo keeps its one-search lookup.

The tools run the real embedding model, like test_passages. The loop runs
against a scripted client: the mock client can't use tools.
"""

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import caps
import companion
import config
import lookup_tools
import passages
import rag_journal

BASE = "http://127.0.0.1:8144"


# ---------------------------------------------------------------------------
# THE TOOLS
# ---------------------------------------------------------------------------

@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Four entries across three months, with their passage index."""
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma_data")
    monkeypatch.setattr(rag_journal, "CHROMA_DIR", tmp_path / "chroma_data")
    col = rag_journal.get_collection()
    rows = [
        ("2026-01-05_aaaa_c0", "2026-01-05", "New year",
         "Robin and I planted the first tomatoes in the Quillon garden."),
        ("2026-02-10_bbbb_c0", "2026-02-10", "Rain",
         "Rained all day. Robin called about the Quillon garden twice. "
         "Quillon garden again."),
        ("2026-03-15_cccc_c1", "2026-03-15", "Long day",
         "Second part: the evening, when the lanterns went up."),
        ("2026-03-15_cccc_c0", "2026-03-15", "Long day",
         "First part: the morning at the harbour."),
    ]
    col.upsert(ids=[r[0] for r in rows], documents=[r[3] for r in rows],
               metadatas=[{"date": r[1], "title": r[2]} for r in rows])
    passages.sync(col)
    return col


def run(col, name, **args):
    text, is_error = lookup_tools.run(col, name, args)
    assert not is_error, text
    return text


def test_list_entries_lists_a_range_oldest_first(journal):
    text = run(journal, "list_entries", date_from="2026-02-01")
    lines = text.splitlines()
    assert lines[0] == "2 entries from 2026-02-01 on:"
    assert lines[1].startswith("2026-02-10 | Rain | ")
    assert lines[2].startswith("2026-03-15 | Long day | ")


def test_exact_search_counts_every_entry_newest_first(journal):
    text = run(journal, "search_journal", query="quillon garden", mode="exact")
    assert text.startswith('"quillon garden" appears 3 times in 2 entries')
    assert text.index("[2026-02-10] Rain (2x)") < text.index("[2026-01-05] New year (1x)")
    text = run(journal, "search_journal", query="quillon garden", mode="exact",
               date_to="2026-01-31")
    assert "Rain" not in text and "New year (1x)" in text


def test_a_search_by_meaning_keeps_to_its_dates(journal):
    text = run(journal, "search_journal", query="tomatoes in the garden",
               date_from="2026-02-01", date_to="2026-02-28")
    assert "[2026-02-10] Rain" in text
    assert "2026-01-05" not in text and "2026-03-15" not in text
    assert run(journal, "search_journal", query="garden",
               date_from="2025-01-01", date_to="2025-12-31") \
        == "No entries between 2025-01-01 and 2025-12-31."


def test_read_entry_puts_the_chunks_back_in_order(journal):
    text = run(journal, "read_entry", date="2026-03-15", title="long day")
    assert text.startswith("[2026-03-15] Long day\n")
    assert text.index("First part") < text.index("Second part")
    # a wrong title on a day with one entry reads that entry; on a day with
    # more, it names them
    assert run(journal, "read_entry", date="2026-03-15",
               title="Short day").startswith("[2026-03-15] Long day")
    journal.upsert(ids=["2026-03-15_dddd_c0"], documents=["Night."],
                   metadatas=[{"date": "2026-03-15", "title": "Later"}])
    assert run(journal, "read_entry", date="2026-03-15", title="Short day") \
        == 'No entry titled "Short day" on 2026-03-15. That day has: "Later"; "Long day".'


def test_a_bad_date_is_an_error_the_model_can_read(journal):
    text, is_error = lookup_tools.run(journal, "list_entries", {"date_from": "March"})
    assert is_error and "YYYY-MM-DD" in text
    text, is_error = lookup_tools.run(journal, "no_such_tool", {})
    assert is_error


# ---------------------------------------------------------------------------
# THE LOOP
# ---------------------------------------------------------------------------

def block(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


class Round:
    """One scripted streamed response."""

    def __init__(self, texts, content, stop_reason):
        self._texts, self._final = texts, SimpleNamespace(
            content=content, stop_reason=stop_reason)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield from self._texts

    def get_final_message(self):
        return self._final


class Scripted:
    """A client whose messages.stream() plays `rounds` in order and keeps
    what each call was sent."""

    def __init__(self, rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tool_round(name="list_entries", call_id="t1", **args):
    return Round([], [block("thinking", thinking=""),
                      block("tool_use", id=call_id, name=name, input=args)],
                 "tool_use")


def answer(text):
    return Round([text], [block("text", text=text)], "end_turn")


@pytest.fixture
def quiet(monkeypatch):
    """No journal needed: a fixed context block and tools that echo."""
    monkeypatch.setattr(companion, "build_context_block", lambda *a, **k: "ctx")
    monkeypatch.setattr(companion, "journal_span", lambda col: "span")
    monkeypatch.setattr(companion, "load_seed", lambda: "")
    ran = []

    def fake_run(col, name, args):
        ran.append((name, args))
        return f"{name} said hello", False
    monkeypatch.setattr(lookup_tools, "run", fake_run)
    return ran


def smart(client, messages, question="when did I first?"):
    return "".join(companion.stream_smart_lookup(client, None, {}, messages, question))


def test_tool_results_go_back_until_it_answers(quiet):
    client = Scripted([tool_round(date_from="2026-01-01"), answer("January 5.")])
    messages = []
    assert smart(client, messages) == "January 5."
    assert quiet == [("list_entries", {"date_from": "2026-01-01"})]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    result = messages[2]["content"][0]
    assert result == {"type": "tool_result", "tool_use_id": "t1",
                      "content": "list_entries said hello", "is_error": False}
    # the thinking block goes back as it came
    assert client.calls[1]["messages"][1]["content"][0].type == "thinking"
    assert [c["tool_choice"] for c in client.calls] == [{"type": "auto"}] * 2
    assert client.calls[0]["system"][-1]["text"] == companion.SMART_REPLY_PROMPT
    # every round caches up to its last block, so the next reads it back
    assert all(c["cache_control"] == {"type": "ephemeral"} for c in client.calls)


def test_the_last_round_has_tools_off(quiet, monkeypatch):
    monkeypatch.setattr(companion, "SMART_REPLY_ROUNDS", 3)
    client = Scripted([tool_round(call_id="a"), tool_round(call_id="b"),
                       answer("Found it.")])
    assert smart(client, []) == "Found it."
    assert [c["tool_choice"]["type"] for c in client.calls] == ["auto", "auto", "none"]
    assert not client.rounds


def test_text_from_two_rounds_is_kept_apart(quiet):
    client = Scripted([Round(["Looking."], [block("text", text="Looking."),
                                            block("tool_use", id="t", name="list_entries",
                                                  input={})], "tool_use"),
                       answer("Here.")])
    assert smart(client, []) == "Looking.\n\nHere."


def test_a_failed_round_leaves_the_history_as_it_was(quiet):
    before = [{"role": "user", "content": "earlier"},
              {"role": "assistant", "content": "reply"}]
    messages = list(before)
    client = Scripted([tool_round(), RuntimeError("connection dropped")])
    with pytest.raises(RuntimeError):
        smart(client, messages)
    assert messages == before


def test_a_round_past_the_cap_says_which_cap(quiet):
    before = [{"role": "user", "content": "earlier"},
              {"role": "assistant", "content": "reply"}]
    messages = list(before)
    client = Scripted([Round(["Looking."], [block("text", text="Looking."),
                                            block("tool_use", id="t", name="list_entries",
                                                  input={})], "tool_use"),
                       caps.CapExceeded("this month's estimated spend is $5.00")])
    assert smart(client, messages) == (
        "Looking.\n\n[Stopped: this month's estimated spend is $5.00]")
    assert messages == before


# ---------------------------------------------------------------------------
# THE ROUTE
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    import server
    with TestClient(server.app, base_url=BASE) as c:
        yield c


@pytest.fixture
def turns(monkeypatch):
    """Which lookup turn the route picked, instead of running it."""
    def fake(name):
        def turn(*args):
            yield name
        return turn
    monkeypatch.setattr(companion, "stream_lookup", fake("plain"))
    monkeypatch.setattr(companion, "stream_smart_lookup", fake("smart"))


def test_the_toggle_picks_the_turn_and_the_demo_never_searches(app, turns, monkeypatch):
    ask = lambda **extra: app.post("/api/lookup", json={"message": "hi", **extra}).text
    assert ask() == "plain"
    assert ask(smart=True) == "plain"          # the suite runs in mock mode
    monkeypatch.setattr(config, "MOCK_MODE", False)
    assert ask(smart=True) == "smart"
    assert ask(smart=False) == "plain"
