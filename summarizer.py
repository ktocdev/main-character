"""
Summarizer — Phase 3 of the RAG Journal.

Generates the summary layers from persona-spec.md §6 that feed the
companion's Layer 1 context:

  Weekly arc summaries  — ~450-word narrative per ISO week, cached in
                          summaries/arcs/{YYYY-Www}.md; only weeks whose
                          entries changed are regenerated
  Status snapshot       — summaries/status_snapshot.md, a compact
                          "what's going on in this person's life right now"
                          built from the recent arcs + the latest raw entries

Usage:
    python summarizer.py build            # incremental (only stale weeks)
    python summarizer.py build --force    # regenerate everything
"""

import hashlib
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from entities import get_conversations

MODEL = "claude-opus-4-8"
SUMMARY_DIR = Path(__file__).parent / "summaries"
ARC_DIR = SUMMARY_DIR / "arcs"
SNAPSHOT_FILE = SUMMARY_DIR / "status_snapshot.md"
AUTHOR = os.getenv("RAG_AUTHOR_NAME", "").strip() or "the journal author"
ARC_INPUT_CHARS = 60_000
SNAPSHOT_RECENT_ARCS = 4

ARC_PROMPT = """\
You are summarizing one week of {author}'s journal. Below are the journal \
entries from that week (originally conversations with an AI companion; only \
{author}'s side is included).

Write the narrative arc of this week in about 400-500 words of plain prose \
(no headers, no bullet points): what happened, what changed, what patterns \
appeared, and what's unresolved going into next week. Use people's names as \
{author} does. Anchor events to their dates where it matters. Don't \
moralize or append advice — this is a record, not a pep talk.

<week entries_from="{start}" entries_to="{end}">
{text}
</week>"""

SNAPSHOT_PROMPT = """\
You maintain a status snapshot for {author}'s journal companion: a compact, \
always-current answer to "if you could know only one page about this \
person's life right now, what would it say?"

Below are the last few weekly arc summaries (oldest first) and the most \
recent raw entries. Today is {today}.

Write the snapshot in about 350-450 words of plain prose grouped into short \
paragraphs covering: active relationships and where they stand, work \
situation, health, upcoming events, active emotional threads, and recent \
wins/struggles. Present tense, current state — this is "now," not a recap. \
Weight the most recent information most heavily.

<recent_weekly_arcs>
{arcs}
</recent_weekly_arcs>

<latest_entries>
{recent}
</latest_entries>"""


def week_key(date_str: str) -> str:
    """'2026-07-06' -> '2026-W28' (ISO week)."""
    d = date.fromisoformat(date_str)
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def group_by_week(conversations: list[dict]) -> dict:
    weeks = {}
    for conv in conversations:
        try:
            key = week_key(conv["date"])
        except ValueError:
            continue
        weeks.setdefault(key, []).append(conv)
    return weeks


def _week_hash(convs: list[dict]) -> str:
    joined = "\n".join(f"{c['date']}|{c['title']}|{len(c['text'])}" for c in convs)
    return hashlib.md5(joined.encode()).hexdigest()[:12]


def _arc_text(client, key: str, convs: list[dict]) -> str:
    text = "\n\n---\n\n".join(
        f"[{c['date']}] {c['title']}\n{c['text']}" for c in convs
    )[:ARC_INPUT_CHARS]
    dates = [c["date"] for c in convs]
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": ARC_PROMPT.format(
                author=AUTHOR, start=min(dates), end=max(dates), text=text,
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return next(b.text for b in response.content if b.type == "text").strip()


def build_arcs(force: bool = False, quiet: bool = False) -> int:
    """Generate/refresh weekly arcs. Returns how many were (re)generated."""
    client = anthropic.Anthropic()
    ARC_DIR.mkdir(parents=True, exist_ok=True)
    weeks = group_by_week(get_conversations())
    regenerated = 0

    for key in sorted(weeks):
        convs = sorted(weeks[key], key=lambda c: c["date"])
        current_hash = _week_hash(convs)
        path = ARC_DIR / f"{key}.md"

        if path.exists() and not force:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            if current_hash in first_line:
                if not quiet:
                    print(f"  {key} (current)")
                continue
        try:
            arc = _arc_text(client, key, convs)
        except Exception as e:
            print(f"  {key} FAILED: {e}")
            continue
        dates = [c["date"] for c in convs]
        path.write_text(
            f"<!-- hash: {current_hash} -->\n"
            f"# Week {key} ({min(dates)} to {max(dates)})\n\n{arc}\n",
            encoding="utf-8",
        )
        regenerated += 1
        if not quiet:
            print(f"  {key} -> written")
    return regenerated


def build_snapshot(quiet: bool = False):
    client = anthropic.Anthropic()
    arc_files = sorted(ARC_DIR.glob("*.md"))[-SNAPSHOT_RECENT_ARCS:]
    if not arc_files:
        print("  no arcs yet — run build first")
        return
    arcs = "\n\n".join(f.read_text(encoding="utf-8") for f in arc_files)

    conversations = sorted(get_conversations(), key=lambda c: c["date"])
    recent = "\n\n---\n\n".join(
        f"[{c['date']}] {c['title']}\n{c['text']}" for c in conversations[-3:]
    )[:25_000]

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": SNAPSHOT_PROMPT.format(
                author=AUTHOR, today=datetime.now().strftime("%Y-%m-%d"),
                arcs=arcs, recent=recent,
            ),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined snapshot")
    snapshot = next(b.text for b in response.content if b.type == "text").strip()
    SNAPSHOT_FILE.write_text(
        f"<!-- generated: {datetime.now().isoformat(timespec='minutes')} -->\n"
        f"# Status snapshot\n\n{snapshot}\n",
        encoding="utf-8",
    )
    if not quiet:
        print("  status snapshot -> written")


def build(force: bool = False, quiet: bool = False) -> dict:
    n = build_arcs(force=force, quiet=quiet)
    build_snapshot(quiet=quiet)
    arcs_total = len(list(ARC_DIR.glob("*.md")))
    print(f"\n  Summaries: {arcs_total} weekly arcs ({n} regenerated) + status snapshot")
    return {"arcs": arcs_total, "regenerated": n}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(force="--force" in sys.argv)
    else:
        print(__doc__)
