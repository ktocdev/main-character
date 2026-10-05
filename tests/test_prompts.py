# SPDX-License-Identifier: AGPL-3.0-or-later
"""The companion's and the chat tab's prompts (lookup upgrade step 1b):
they describe what each turn actually receives, the journal's start date
comes from the journal, and the chat tab gets its own prompt."""

import inspect
import re

import companion


class FakeCollection:
    def __init__(self, dates):
        self.dates = dates

    def get(self, include=None):
        return {"ids": [str(i) for i in range(len(self.dates))],
                "metadatas": [{"date": d, "title": "t"} for d in self.dates]}


class FakeStream:
    def __init__(self, record, kwargs):
        record.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        yield "an answer"

    def get_final_message(self):
        return type("Final", (), {"stop_reason": "end_turn"})()


class FakeClient:
    def __init__(self):
        self.calls = []
        client = self

        class Messages:
            def stream(self, **kwargs):
                return FakeStream(client.calls, kwargs)
        self.messages = Messages()


def test_the_span_is_the_first_entry_date():
    col = FakeCollection(["2026-03-02", "2025-12-23", "", "not a date"])
    assert companion.journal_span(col) == (
        "Their journal begins on December 23, 2025, and runs to the present.")


def test_an_empty_journal_says_so():
    assert "Nothing has been closed" in companion.journal_span(FakeCollection([]))
    assert "Nothing has been closed" in companion.journal_span(None)


def test_no_date_is_hard_coded():
    for template in (companion.SYSTEM_PROMPT, companion.LOOKUP_PROMPT):
        assert "December 2025" not in template
        assert template.count("{journal_span}") == 1
        filled = companion.system_prompt(template, FakeCollection(["2026-09-14"]))
        assert "{journal_span}" not in filled
        assert "September 14, 2026" in filled


def test_every_context_section_is_explained():
    """A layer added to build_context_block needs a line in CONTEXT_SECTIONS,
    or the prompt no longer describes what the model receives."""
    # From the file, not the attribute: other tests stub the function.
    source = inspect.getsource(companion).split(
        "def build_context_block(", 1)[1].split("\ndef ", 1)[0]
    tags = set(re.findall(r"<([a-z_]+)>", source))
    assert tags  # the regex still finds them
    for tag in tags:
        assert f"<{tag}>" in companion.CONTEXT_SECTIONS, tag


def test_the_reflection_no_longer_mentions_the_snapshot():
    assert "snapshot" not in companion.REFLECTION_REQUEST


def _turn(monkeypatch, fn, *args):
    monkeypatch.setattr(companion, "build_context_block", lambda *a, **k: "ctx")
    monkeypatch.setattr(companion, "load_seed", lambda: "")
    client, messages = FakeClient(), []
    text = "".join(fn(client, FakeCollection(["2026-01-05"]), {}, messages, *args))
    assert text == "an answer"
    return client.calls[0]["system"][0]["text"], messages


def test_the_chat_tab_gets_the_finder_prompt(monkeypatch):
    system, messages = _turn(monkeypatch, companion.stream_lookup, "when did I go?")
    assert system.startswith("You find things in one person's journal.")
    assert "January 5, 2026" in system
    assert "witness" not in system
    assert messages[-1] == {"role": "assistant", "content": "an answer"}


def test_the_companion_keeps_its_persona(monkeypatch):
    system, _ = _turn(monkeypatch, companion.stream_reply, "today was fine")
    assert system.startswith("You are a journal companion")
    assert "How you work:" in system
    assert "January 5, 2026" in system
