# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Re-live the seed corpus through the real app.

Feeds each entry into the companion as a chat turn, gets a real reply,
closes each session at its summarize point, and generates the seed
summary candidates - producing the artifacts a hand-written corpus
can't have: two-sided braids, session archives, and a seed summary that
was actually generated from them.

Everything is sandboxed to seed_corpus/build/ via the MC_* env vars.
The author's live journal in sessions/ and summaries/ is never touched.

  python build_sessions.py            dry run, no API calls
  python build_sessions.py --live     real companion + seed calls
  python build_sessions.py --live --only 1    just session 1
  python build_sessions.py --upload   reviewed candidate becomes the seed

The loop per session is: --live --only N, review the candidate by hand,
--upload, then the next session chats against the seed you approved.

Chat turns come in two kinds. A turn that just adds another point is
scripted here in FOLLOWUPS. A turn that answers something the companion
actually asked can't be - we have to read the reply first. For those,
run to a stopping point, write the answer into FOLLOWUPS, and resume:

  ... --live --only 1 --until 2026-08-03    stop after that day
  ... --live --only 1 --resume              pick up where it left off

Resume replays nothing: finished turns and the exact message history
are kept in current.json, so no turn is ever paid for twice.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
BUILD = HERE / "build"
ENTRIES = HERE / "journal_entries"

# sandbox every data dir before the project imports read config
for _var, _sub in [
    ("MC_SESSION_DIR", "sessions"), ("MC_SUMMARY_DIR", "summaries"),
    ("MC_CHROMA_DIR", "chroma_data"), ("MC_JOURNAL_DIR", "journal_entries"),
    ("MC_ENTITY_DIR", "entity_graph"), ("MC_CATEGORY_DIR", "categories"),
    ("MC_PATTERN_DIR", "patterns"), ("MC_DREAM_DIR", "dreams"),
]:
    os.environ[_var] = str(BUILD / _sub)
# hard set, not setdefault - the real author's name must never reach a
# braid or a seed that ships
os.environ["MC_AUTHOR_NAME"] = "Jordan"

sys.path.insert(0, str(HERE.parent))

# ---------------------------------------------------------------------------
# THE PLAN
# ---------------------------------------------------------------------------
# Each session is one stretch of chatting, closed at its summarize point.

SESSIONS = [
    {"n": 1, "start": "2026-07-28", "end": "2026-08-09",
     "title": "Groundwork nights and the reorg news"},
    {"n": 2, "start": "2026-08-11", "end": "2026-08-25",
     "title": "Lena leaving and the open mic"},
    {"n": 3, "start": "2026-08-27", "end": "2026-09-14",
     "title": "The holding pen and a door"},
    {"n": 4, "start": "2026-09-15", "end": "2026-09-17",
     "title": "The waiting week"},
]

# when each entry was written. Keyed by filename stem, not date - two
# days carry two entries each, and they were written hours apart.
TIMES = {
    "2026-07-28_Working on Coda at Groundwork": "21:40",
    "2026-07-30_Team restructuring news": "20:15",
    "2026-07-31_Friday run with Mika": "19:05",
    "2026-08-01_Quiet Saturday": "16:30",
    "2026-08-03_Monday crash": "22:10",
    "2026-08-04_Breaking out of it": "21:20",
    "2026-08-07_Mom called about visiting": "20:00",
    "2026-08-08_Walk with Dev": "18:45",
    "2026-08-09_Lease paperwork and errands": "17:30",
    "2026-08-11_Sprint review fallout": "22:25",
    "2026-08-12_Groundwork again": "20:50",
    "2026-08-14_Dream about the empty office": "05:20",
    "2026-08-14_Lena is leaving": "21:15",
    "2026-08-15_Dinner with Dev": "23:10",
    "2026-08-17_Singing practice": "19:40",
    "2026-08-18_AI for Coda": "21:05",
    "2026-08-19_Solo run clear head": "18:20",
    "2026-08-20_Sprint demo went well": "20:35",
    "2026-08-21_Open mic night": "23:45",
    "2026-08-24_The loop again": "22:40",
    "2026-08-25_dream": "03:15",
    "2026-08-25_Groundwork Coda breakthrough": "21:55",
    "2026-08-27_Being honest with Dev": "21:30",
    "2026-08-28_Lenas last day": "20:10",
    "2026-09-01_The reorg lands": "22:05",
    "2026-09-03_Dev tries free play": "21:45",
    "2026-09-06_Moms visit": "20:25",
    "2026-09-09_Lenas referral": "23:20",
    "2026-09-11_Phone screen with Northlight": "19:50",
    "2026-09-14_Signing the lease": "20:40",
    "2026-09-15_Rebuilding free play": "21:15",
    "2026-09-16_Booking the open mic": "22:00",
    "2026-09-17_Building the presentation": "20:30",
}

# Entries Jordan keeps talking after, via send rather than save - the
# back-and-forth that makes the braid a conversation instead of a log.
# These are all the "one more thing" kind, which stand on their own.
# Turns that answer a question the companion asked get written in here
# between runs, once we've actually read the question.
FOLLOWUPS = {
    "2026-07-30_Team restructuring news": [
        "no. Tuesday was the last time. I keep meaning to and then it's 7pm "
        "and I'm still at my desk sorting files I'll never open.\n\n"
        "and the portfolio thing is going to sit there again, isn't it. I "
        "said maybe this weekend. I have been saying maybe this weekend "
        "since March.",
    ],
    "2026-08-03_Monday crash": [
        "no. it's face down on the counter. I heard it buzz twice before I "
        "turned it off and I'm fairly sure that was Mika.\n\n"
        "I keep replaying the part where Lena tried to back me up and he "
        "talked over her too. that's the bit that won't sit still.",
        "I don't want advice about how to handle him. I want to not care, "
        "and I can't figure out how other people do that.",
    ],
    "2026-08-14_Lena is leaving": [
        "the thing I can't say out loud at work is that I'm not sad she's "
        "leaving, I'm scared. those feel like they should be the same and "
        "they aren't.",
        "three weeks. that's the part. three weeks and then the floor is "
        "just Marcus and people I nod at.",
    ],
    "2026-09-01_The reorg lands": [
        "good, actually. they'd already started making it when I walked in, "
        "and on tonight of all nights that nearly took me out.\n\n"
        "twenty minutes. I want to make it mean something and I also know "
        "that's exactly how I set myself up to be disappointed next time.",
        "ok. say it back to me plainly - what actually changed tonight, "
        "without the encouragement part.",
    ],
    "2026-09-11_Phone screen with Northlight": [
        "I don't want to get attached to this. I've been wrong about "
        "interviews before and the crash after is worse than the wait.",
    ],
    "2026-08-08_Walk with Dev": [
        "one more thing and then I'm going to bed - I didn't check my phone "
        "once the whole two hours. I only noticed afterward.",
    ],
    "2026-08-18_AI for Coda": [
        "also I want it on the record that I shelved the recommendation "
        "engine on purpose. next time I'm calling it scope creep I'll want "
        "to know I can actually do it.",
    ],
    "2026-08-21_Open mic night": [
        "adding this in the morning: my hands shook through the whole first "
        "song and nobody could tell. that seems worth knowing.",
    ],
    "2026-08-28_Lenas last day": [
        "she did. it went in the box on top of the mug and she made me hold "
        "it while she got her car door open.\n\n"
        "one more thing - I've been telling myself I'm not ready to move for "
        "about a year now. I'd like to know when I started saying that.",
    ],
    "2026-09-03_Dev tries free play": [
        "I keep coming back to the fact that I used the feature every day "
        "and Dev saw it in one session. I want to understand what that "
        "actually says about how I design, not just feel bad about it.",
    ],
    "2026-09-16_Booking the open mic": [
        "one more thing about the picks - Dev tried one on the low B "
        "string and said it changes the attack completely, warmer. and "
        "then they used it for the rest of practice. Dad would have "
        "liked that.",
    ],
    "2026-09-17_Building the presentation": [
        "I just went back and read the Coda case study I wrote on the 9th "
        "and the version I'm putting in the deck is different. better. the "
        "9th version was honest but defensive - 'I missed this but here's "
        "why it's not that bad.' the deck version is just 'I missed this "
        "and here's what I learned.' that's the whole difference and it "
        "took me eight days to get there.",
    ],
    "2026-09-06_Moms visit": [
        "you're right, I skipped it. what I'd be doing is Coda, or something "
        "like it - building one small thing all the way through instead of "
        "in ninety-minute pieces at a coffee shop. and playing more. the "
        "open mic was two weeks ago, I said yes to a second one, and I have "
        "not booked it.\n\n"
        "she also asked why I didn't tell her sooner and I said I didn't "
        "want her to worry, which is true and also not the reason.",
    ],
}

# Phrases Jordan would call out. When one shows up in a reply she says so
# on the spot, before whatever she was going to write next.
INTERJECTIONS = [
    ("that's not nothing", "please never say that's not nothing"),
]


def parse_entry(path: Path) -> dict:
    """The entry body as Jordan typed it - title and _Date:_ line stripped."""
    raw = path.read_text(encoding="utf-8")
    date = re.search(r"_Date:\s*(\d{4}-\d{2}-\d{2})_", raw)
    title = re.search(r"^#\s+(.+)$", raw, re.M)
    body = re.sub(r"^#\s+.*$", "", raw, count=1, flags=re.M)
    body = re.sub(r"^_Date:.*$", "", body, count=1, flags=re.M)
    return {
        "stem": path.stem,
        "date": date.group(1) if date else "",
        "time": TIMES.get(path.stem, "20:00"),
        "title": title.group(1).strip() if title else path.stem,
        "text": body.strip(),
        "dream": bool(re.search(r"_Realm:\s*dream_", raw))
        or path.stem.endswith("_dream"),
    }


def load_entries() -> list[dict]:
    entries = [parse_entry(p) for p in ENTRIES.glob("*.md")]
    missing = [e["stem"] for e in entries if not e["date"]]
    if missing:
        raise SystemExit(f"entries with no _Date: line: {missing}")
    untimed = [e["stem"] for e in entries if e["stem"] not in TIMES]
    if untimed:
        raise SystemExit(f"entries missing from TIMES: {untimed}")
    return sorted(entries, key=lambda e: (e["date"], e["time"]))


class FrozenClock:
    """close_session and _stamp read datetime.now(); the corpus needs the
    dates it actually happened on, not today's."""

    def __init__(self, when: datetime):
        self.when = when

    def now(self):
        return self.when

    def __getattr__(self, name):
        return getattr(datetime, name)


def asks_question(reply: str) -> bool:
    """Did the companion end by asking something? Only the tail counts -
    a question in the middle of a long reply isn't what it left open."""
    return "?" in reply.strip()[-300:]


def interjection_for(reply: str) -> str:
    low = reply.lower()
    return next((line for phrase, line in INTERJECTIONS if phrase in low), "")


def companion_reply(companion, client, collection, entity_index, api_msgs,
                    text, live):
    if not live:
        api_msgs.append({"role": "user", "content": text})
        api_msgs.append({"role": "assistant", "content": "[dry run]"})
        return "[dry run]"
    return "".join(companion.stream_reply(client, collection, entity_index,
                                          api_msgs, text))


def retitle_parts(sessions, key, days):
    """close_session labels every day with the session title. The corpus
    has one real entry per day - point the parts at those instead, so
    history shows entry titles and their summaries resolve."""
    titles = {e["date"]: e["title"] for e in days if not e["dream"]}
    path = sessions.ARCHIVE_DIR / f"{key}.json"
    archive = json.loads(path.read_text(encoding="utf-8"))
    for part in archive["parts"]:
        if part["date"] in titles:
            part["title"] = titles[part["date"]]
    path.write_text(json.dumps(archive, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def run_session(spec, entries, collection, client, entity_index,
                sessions, seed, companion, live, resume=False, until=""):
    days = [e for e in entries if spec["start"] <= e["date"] <= spec["end"]]
    print(f"\n=== session {spec['n']}: {spec['title']} "
          f"({len(days)} entries, {spec['start']} -> {spec['end']}) ===")

    # The companion must only ever retrieve the past. Entries land in
    # chroma at close, so nothing dated on or after this session's start
    # can legitimately be there yet - if it is, a previous run left it.
    ahead = sorted({m.get("date", "") for m in
                    collection.get(include=["metadatas"])["metadatas"]
                    if m.get("date", "") >= spec["start"]})
    if ahead:
        raise SystemExit(
            f"chroma already holds {len(ahead)} day(s) from {ahead[0]} on - "
            f"the companion would retrieve its own future. "
            f"wipe seed_corpus/build/ and start the session over.")

    started = f"{days[0]['date']} {days[0]['time']}"
    api_msgs = []   # the companion's own message list, whole session
    braid = []      # what lands in current.json, with real timestamps
    done = {}       # stem -> scripted turns sent, so resume never re-pays

    if resume:
        cur = sessions.load_current()
        braid, api_msgs = cur["messages"], cur.get("_api", [])
        done, started = cur.get("_done", {}), cur["started"]
        print(f"  resuming: {len(done)} entries touched, {len(braid)} messages")

    def checkpoint():
        sessions.save_current({"started": started,
                               "entry_schema": sessions.ENTRY_SCHEMA,
                               "base": [], "_api": api_msgs,
                               "_done": done, "messages": braid})

    for e in days:
        stamp = f"{e['date']} {e['time']}"
        flag = "*" if e["dream"] else " "
        # scripted turns only; interjections are sent inline and uncounted,
        # so resume indexes back into a FOLLOWUPS you may have just edited
        turns = [e["text"]] + FOLLOWUPS.get(e["stem"], [])
        sent = done.get(e["stem"], 0)
        if sent >= len(turns):
            continue

        def say(text, label, saved=False):
            # The entry file itself is Jordan's one **save entry** for the
            # day; follow-ups and interjections are sends. The id is the
            # file's stem, so a rebuild names the same entry the same way.
            msg = {"role": "you", "kind": "chat", "text": text, "ts": stamp}
            if saved:
                msg.update(kind="entry",
                           entry_id="demo-" + re.sub(r"[^A-Za-z0-9-]+", "-", e["stem"]))
            if e["dream"]:
                msg["dream"] = True
            braid.append(msg)
            reply = companion_reply(companion, client, collection,
                                    entity_index, api_msgs, text, live)
            braid.append({"role": "companion", "text": reply, "ts": stamp})
            print(f"  {e['date']}{flag} {label}: {len(reply)} chars")
            return reply

        for i in range(sent, len(turns)):
            reply = say(turns[i], f"turn {i + 1}", saved=i == 0)
            # Jordan calls out the phrase before saying anything else
            line = interjection_for(reply)
            if line:
                reply = say(line, "interjection")
            done[e["stem"]] = i + 1
            checkpoint()
            # a scripted "one more thing" must not talk over a question the
            # companion just asked - stop so the next turn can answer it
            if i + 1 < len(turns) and asks_question(reply):
                print(f"\n  the companion asked something. rewrite "
                      f"FOLLOWUPS[{e['stem']!r}][{i}] to answer it, "
                      f"then --resume.\n  ...{reply.strip()[-300:]}\n")
                return

        if until and e["date"] >= until:
            print(f"  stopped after {e['date']} - read the replies, then "
                  f"--resume")
            return

    remaining = [e["stem"] for e in days
                 if done.get(e["stem"], 0) < 1 + len(FOLLOWUPS.get(e["stem"], []))]
    if remaining:
        print(f"  not closing: {len(remaining)} entries still to chat")
        return

    closed_at = datetime.strptime(
        f"{days[-1]['date']} {days[-1]['time']}", "%Y-%m-%d %H:%M")
    real_dt = sessions.datetime
    sessions.datetime = FrozenClock(closed_at)
    try:
        result = sessions.close_session(collection, client,
                                        title_hint=spec["title"])
    finally:
        sessions.datetime = real_dt
    print(f"  closed -> {result['key']}")

    retitle_parts(sessions, result["key"], days)

    if live:
        # the seed's "Updated" line is datetime.now() too - it should read
        # the day the chat closed, not the day we built the corpus
        seed.datetime = FrozenClock(closed_at)
        try:
            print(f"  candidate -> {seed.generate_candidate(result['key'])}")
        finally:
            seed.datetime = real_dt
    else:
        print("  candidate -> skipped (dry run)")


def main():
    live = "--live" in sys.argv
    resume = "--resume" in sys.argv
    only, until = None, ""
    if "--only" in sys.argv:
        only = int(sys.argv[sys.argv.index("--only") + 1])
    if "--until" in sys.argv:
        until = sys.argv[sys.argv.index("--until") + 1]
    if resume and only is None:
        raise SystemExit("--resume needs --only N (which session to continue)")
    if not live:
        os.environ["MC_MOCK"] = "1"

    BUILD.mkdir(parents=True, exist_ok=True)

    import config
    # hard stop if the sandbox didn't take - never write the real journal
    for d in (config.SESSION_DIR, config.SUMMARY_DIR, config.CHROMA_DIR,
              config.JOURNAL_DIR):
        if BUILD.resolve() not in Path(d).resolve().parents:
            raise SystemExit(f"refusing to run: {d} is outside {BUILD}")

    import companion
    import seed
    import sessions
    from rag_journal import get_collection

    if "--upload" in sys.argv:
        if not seed.CANDIDATE_FILE.exists():
            raise SystemExit("no candidate to upload")
        seed.save_seed(seed.CANDIDATE_FILE.read_text(encoding="utf-8"))
        print(f"  seed <- candidate ({seed.SEED_FILE}); prior seed backed up")
        return

    collection = get_collection()
    entries = load_entries()

    # No starting seed. Jordan writes first and summarizes when ready,
    # like anyone would - session 1's close is what creates the seed.
    config.SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    client = config.get_client() if live else None
    entity_index = companion.load_entity_index()

    for spec in SESSIONS:
        if only is not None and spec["n"] != only:
            continue
        run_session(spec, entries, collection, client, entity_index,
                    sessions, seed, companion, live, resume, until)


if __name__ == "__main__":
    main()
