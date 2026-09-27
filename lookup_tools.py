# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The tools Claude gets on the chat tab when smart replies are on.

By default the chat tab searches once, packs what it found into the prompt,
and makes one call. If that search missed, Claude can't look again. With
smart replies on (a toggle on the chat tab, off by default, sent with each
question), `companion.stream_smart_lookup` gives Claude these three tools as
well, so it can search, read and search again before answering:

  - **search_journal** -- passages by meaning (the same search as the
    context block, keywords and all) or entries by exact words, optionally
    within a range of dates.
  - **read_entry** -- one entry whole, by date and title.
  - **list_entries** -- every entry's date and title in a range, oldest
    first. What "when did I first..." and "how many times..." need.

All three are local and free: they read the journal's own store. Each
returns plain text for a tool_result, and a mistake the model can fix (a
bad date, a title that isn't there) comes back as text that says so,
never as an exception.
"""

import re

from config import N_SEMANTIC

ENTRY_CHARS = 40_000     # read_entry: past this, the rest is cut and said so
LIST_MAX = 200           # list_entries: rows before it asks for a narrower range
EXACT_MAX = 25           # search_journal exact: entries listed
SNIPPET = 300            # exact mode: characters shown around the first match

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_RANGE = {
    "date_from": {"type": "string",
                  "description": "Earliest date to include, YYYY-MM-DD. Optional."},
    "date_to": {"type": "string",
                "description": "Latest date to include, YYYY-MM-DD. Optional."},
}

TOOLS = [
    {
        "name": "search_journal",
        "description": (
            "Search the journal's closed entries. mode 'meaning' (the default) "
            "finds passages about the query, whatever words they use, and also "
            "weighs matching words; each result is a passage with a little of "
            "the text around it, under the entry's date and title. mode "
            "'exact' finds every entry containing the query's exact text "
            "(case and accents ignored), newest first, with how many times it "
            "appears and a snippet around the first match: use it for names, "
            "phrases and counting. Give date_from and/or date_to to search "
            "only entries in that range. Dreams and the open chapter are not "
            "searched."),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for."},
                "mode": {"type": "string", "enum": ["meaning", "exact"],
                         "description": "'meaning' (default) or 'exact'."},
                **_RANGE,
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_entry",
        "description": (
            "Read one closed entry in full, in the author's own words, by its "
            "date and title exactly as a search result or list_entries shows "
            "them. When the title doesn't match, the entries on that date are "
            "listed instead."),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD."},
                "title": {"type": "string",
                          "description": "The entry's title."},
            },
            "required": ["date", "title"],
        },
    },
    {
        "name": "list_entries",
        "description": (
            "List closed entries by date and title, oldest first, with each "
            "one's length in characters. Use it to see what exists in a "
            "stretch of time, to find the first or last of something together "
            "with exact search, or to count. Without dates it lists the whole "
            f"journal, which is long; past {LIST_MAX} entries it asks for a "
            "narrower range."),
        "input_schema": {
            "type": "object",
            "properties": dict(_RANGE),
        },
    },
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _entries(collection) -> dict[tuple[str, str], int]:
    """(date, title) -> characters, over every closed entry."""
    data = collection.get(include=["metadatas", "documents"])
    out: dict[tuple[str, str], int] = {}
    for doc, meta in zip(data["documents"], data["metadatas"]):
        key = (meta.get("date", ""), meta.get("title", ""))
        out[key] = out.get(key, 0) + len(doc)
    return out


def _keys(collection) -> set[tuple[str, str]]:
    """(date, title) of every closed entry, without their text."""
    metas = collection.get(include=["metadatas"])["metadatas"]
    return {(m.get("date", ""), m.get("title", "")) for m in metas}


def _range(args: dict) -> tuple[str, str]:
    """The (date_from, date_to) the call asked for, blank for open. Raises
    ValueError, with a message for the model, on a malformed date."""
    lo = str(args.get("date_from") or "").strip()
    hi = str(args.get("date_to") or "").strip()
    for d in (lo, hi):
        if d and not _DATE.fullmatch(d):
            raise ValueError(f"'{d}' is not a date in YYYY-MM-DD form.")
    if lo and hi and lo > hi:
        raise ValueError(f"date_from {lo} is after date_to {hi}.")
    return lo, hi


def _in_range(date: str, lo: str, hi: str) -> bool:
    return (not lo or date >= lo) and (not hi or date <= hi)


def _where(collection, lo: str, hi: str) -> dict | None:
    """A Chroma filter for the range. Dates are stored as strings, which
    Chroma can't compare, so the range becomes the list of the journal's
    own dates inside it."""
    if not (lo or hi):
        return None
    dates = sorted({d for d, _ in _keys(collection) if _in_range(d, lo, hi)})
    return {"date": {"$in": dates}} if dates else {"date": {"$in": [""]}}


def _span(lo: str, hi: str) -> str:
    if lo and hi:
        return f" between {lo} and {hi}"
    if lo:
        return f" from {lo} on"
    if hi:
        return f" up to {hi}"
    return ""


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------

def search_journal(collection, args: dict) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "The query is empty."
    lo, hi = _range(args)
    if args.get("mode") == "exact":
        return _search_exact(collection, query, lo, hi)
    return _search_meaning(collection, query, lo, hi)


def _search_meaning(collection, query: str, lo: str, hi: str) -> str:
    import passages
    from rag_journal import query_journal
    where = _where(collection, lo, hi)
    if where and where["date"]["$in"] == [""]:
        return f"No entries{_span(lo, hi)}."
    try:
        hits = passages.search(query, N_SEMANTIC, where=where) if where else []
    except passages.ModelUnavailable:
        hits = []
    if not where:
        hits = query_journal(query, n_results=N_SEMANTIC)
    elif not hits:
        # no passage index (or none built for this model): the chunks, by
        # meaning, then kept to the range
        hits = [h for h in query_journal(query, n_results=N_SEMANTIC * 4)
                if _in_range(h["metadata"].get("date", ""), lo, hi)][:N_SEMANTIC]
    if not hits:
        return f"Nothing found{_span(lo, hi)}."
    lines = []
    for hit in hits:
        meta = hit["metadata"]
        lines.append(f"[{meta.get('date', '?')}] {meta.get('title', 'Untitled')}")
        lines.append(hit["text"])
        lines.append("")
    return "\n".join(lines).strip()


def _search_exact(collection, query: str, lo: str, hi: str) -> str:
    from keywords import fold
    needle = fold(query)
    data = collection.get(include=["documents", "metadatas"])
    found: dict[tuple[str, str], dict] = {}
    for doc, meta in zip(data["documents"], data["metadatas"]):
        date = meta.get("date", "")
        if not _in_range(date, lo, hi):
            continue
        hay = fold(doc)
        count = hay.count(needle)
        if not count:
            continue
        key = (date, meta.get("title", ""))
        hit = found.setdefault(key, {"hits": 0, "snippet": ""})
        hit["hits"] += count
        if not hit["snippet"]:
            # fold() keeps one character per character, so offsets line up
            at = hay.find(needle)
            start = max(0, at - SNIPPET // 3)
            end = min(len(doc), at + len(needle) + SNIPPET)
            hit["snippet"] = (("…" if start else "") + doc[start:end].strip()
                              + ("…" if end < len(doc) else ""))
    if not found:
        return f"No entry contains \"{query}\"{_span(lo, hi)}."
    keys = sorted(found, reverse=True)
    total = sum(h["hits"] for h in found.values())
    lines = [f"\"{query}\" appears {total} time{'s' * (total != 1)} in "
             f"{len(keys)} entr{'ies' if len(keys) != 1 else 'y'}{_span(lo, hi)}, "
             f"newest first."]
    if len(keys) > EXACT_MAX:
        oldest = keys[-1]
        lines.append(f"Showing the newest {EXACT_MAX}. The oldest is "
                     f"[{oldest[0]}] {oldest[1]}; narrow the dates to see the rest.")
    lines.append("")
    for key in keys[:EXACT_MAX]:
        hit = found[key]
        lines.append(f"[{key[0]}] {key[1]} ({hit['hits']}x)")
        lines.append(hit["snippet"])
        lines.append("")
    return "\n".join(lines).strip()


def read_entry(collection, args: dict) -> str:
    from sessions import conversation_text
    date = str(args.get("date") or "").strip()
    title = str(args.get("title") or "").strip()
    if not _DATE.fullmatch(date):
        return f"'{date}' is not a date in YYYY-MM-DD form."
    on_day = sorted(t for d, t in _keys(collection) if d == date)
    if not on_day:
        return f"No entries on {date}."
    match = [t for t in on_day if t == title] \
        or [t for t in on_day if t.casefold() == title.casefold()]
    if not match and len(on_day) == 1:
        match = on_day
    if not match:
        return (f"No entry titled \"{title}\" on {date}. That day has: "
                + "; ".join(f"\"{t}\"" for t in on_day) + ".")
    text = conversation_text(collection, date, match[0])
    head = f"[{date}] {match[0]}\n"
    if len(text) > ENTRY_CHARS:
        return (head + text[:ENTRY_CHARS]
                + f"\n\n[Cut here: the entry is {len(text):,} characters and "
                  f"only the first {ENTRY_CHARS:,} are shown. Search within "
                  "this date to find a later part.]")
    return head + text


def list_entries(collection, args: dict) -> str:
    lo, hi = _range(args)
    rows = sorted((d, t, n) for (d, t), n in _entries(collection).items()
                  if _in_range(d, lo, hi))
    if not rows:
        return f"No entries{_span(lo, hi)}."
    head = f"{len(rows)} entr{'ies' if len(rows) != 1 else 'y'}{_span(lo, hi)}:"
    if len(rows) > LIST_MAX:
        return (f"{head} too many to list. They run from {rows[0][0]} to "
                f"{rows[-1][0]}; ask for a narrower range.")
    return "\n".join([head] + [f"{d} | {t} | {n:,} chars" for d, t, n in rows])


_HANDLERS = {
    "search_journal": search_journal,
    "read_entry": read_entry,
    "list_entries": list_entries,
}


def run(collection, name: str, args: dict) -> tuple[str, bool]:
    """(text, is_error) for one tool call."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"There is no tool named {name}.", True
    if not isinstance(args, dict):
        return "The tool input was not an object.", True
    try:
        return handler(collection, args), False
    except ValueError as exc:
        return str(exc), True
