# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Does the search find passages deep inside entries? A free, local test.

    python scripts/eval_retrieval.py --demo
    python scripts/eval_retrieval.py --demo --save docs/retrieval-eval/baseline-demo.json
    python scripts/eval_retrieval.py --demo --compare docs/retrieval-eval/baseline-demo.json
    python scripts/eval_retrieval.py --sandbox --questions docs/retrieval-eval/real.json

Each test pairs a query with a fact: an exact quote from one entry, placed
after that entry's first 1,000 characters, which is past where the embedder
stops reading (LOOKUP-UPGRADE-HANDOFF.md). A test passes at k when a passage
containing the fact is among the first k the search returns, as the companion
would see it: passages whole, and the fallback's whole chunks trimmed to
EXCERPT_CHARS, as build_context_block shows them.

Two sets, both in the questions file:

  questions      chat-screen questions, short.
  entry_replies  a long new entry as the query, the way an entry reply
                 searches. Its link to the older entry (`link`, a quote from
                 the query) sits after its own first 1,000 characters.

It also times each query: the search alone, and the whole context block
(passages, summaries, entity docs and the rest) that build_context_block
assembles before a reply. No Claude calls, nothing written.

The demo's set is scripts/eval_retrieval_demo.json. A set for the real
journal belongs somewhere untracked, such as docs/ (it quotes the journal).
"""

import _journal_dirs

WHICH = _journal_dirs.apply()

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import companion  # noqa: E402
from config import EXCERPT_CHARS, JOURNAL_DIR  # noqa: E402
from rag_journal import get_collection, query_journal  # noqa: E402

DEMO_SET = Path(__file__).with_name("eval_retrieval_demo.json")
DEEP = 1000   # "past the first 1,000 characters"
KS = (6, 12)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _entry_body(path: Path) -> str:
    """An entry file minus its `# title` / `_Date:_` / `_Realm:_` header,
    which is what the journal collection stores."""
    lines = path.read_text(encoding="utf-8").split("\n")
    i = 0
    while i < len(lines) and (lines[i].startswith("# ") or lines[i].startswith("_")
                              or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:])


def validate(tests: list[dict]) -> list[str]:
    """Each fact must be in an entry of its date. A question's fact must sit
    past DEEP characters of every entry that holds it; for an entry reply it is
    the link that must sit past DEEP characters of the query, and the fact
    can be anywhere."""
    problems = []
    for t in tests:
        files = sorted(JOURNAL_DIR.glob(f"{t['date']}_*.md"))
        where = [(f, _norm(_entry_body(f)).find(_norm(t["fact"]))) for f in files]
        where = [(f, at) for f, at in where if at >= 0]
        # More than one file is fine: a written-here day keeps both the saved
        # entry (_HHMM_entry.md) and the day as the chat closed it.
        if not where:
            problems.append(f"{t['id']}: fact not found in any entry dated {t['date']}")
        elif "query" not in t and min(at for _, at in where) < DEEP:
            problems.append(f"{t['id']}: fact starts at character "
                            f"{min(at for _, at in where)}, inside the first {DEEP}")
        if "query" in t:
            at = _norm(t["query"]).find(_norm(t["link"]))
            if at < 0:
                problems.append(f"{t['id']}: link is not in the query")
            elif at < DEEP:
                problems.append(f"{t['id']}: link starts at character {at}, "
                                f"inside the first {DEEP}")
    return problems


def run_one(t: dict, collection, entity_index: dict) -> dict:
    query = t.get("query") or t["question"]
    fact = _norm(t["fact"])

    started = time.perf_counter()
    matches = query_journal(query, n_results=max(KS))
    search_ms = (time.perf_counter() - started) * 1000

    rank = None
    shown = [m["text"] if "source_id" in m["metadata"] else m["text"][:EXCERPT_CHARS]
             for m in matches]
    for i, text in enumerate(shown, 1):
        if fact in _norm(text):
            rank = i
            break

    started = time.perf_counter()
    companion.build_context_block(query, collection, entity_index)
    context_ms = (time.perf_counter() - started) * 1000

    return {"id": t["id"], "rank": rank, "search_ms": round(search_ms, 1),
            "context_ms": round(context_ms, 1),
            # How much the companion reads for this search, so a setting that
            # finds more by showing more can be told apart from one that finds
            # more by ranking better.
            "chars": {k: sum(len(s) for s in shown[:k]) for k in KS}}


def _ms(values: list[float]) -> str:
    values = sorted(values)
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return (f"median {statistics.median(values):6.0f}  p90 {p90:6.0f}  "
            f"max {values[-1]:6.0f} ms")


def report(name: str, results: list[dict], before: dict) -> dict:
    n = len(results)
    print(f"\n{name} ({n})")
    print(f"  {'id':<5} {'rank':>5} {'was':>5}  {'search':>8} {'context':>8}")
    for r in results:
        rank = r["rank"] or "-"
        was = ""
        if r["id"] in before:
            was = before[r["id"]]["rank"] or "-"
        print(f"  {r['id']:<5} {rank!s:>5} {was!s:>5}  "
              f"{r['search_ms']:7.0f}ms {r['context_ms']:7.0f}ms")
    summary = {}
    for k in KS:
        hits = sum(1 for r in results if r["rank"] and r["rank"] <= k)
        summary[f"hit@{k}"] = hits
        line = f"  found in top {k:<2}: {hits:>2}/{n}"
        if before:
            was = sum(1 for r in results
                      if r["id"] in before and before[r["id"]]["rank"]
                      and before[r["id"]]["rank"] <= k)
            line += f"   (was {was}/{n})"
        print(line)
    for k in KS:
        chars = statistics.median(r["chars"][k] for r in results)
        print(f"  text shown, top {k:<2}: median {chars:,.0f} chars")
    mrr = sum(1 / r["rank"] for r in results if r["rank"]) / n
    summary["mrr"] = round(mrr, 3)
    print(f"  mean reciprocal rank: {mrr:.3f}")
    print(f"  search alone : {_ms([r['search_ms'] for r in results])}")
    print(f"  context block: {_ms([r['context_ms'] for r in results])}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", type=Path,
                    help=f"test set (default for --demo: {DEMO_SET.name})")
    ap.add_argument("--save", type=Path, help="write the results here as JSON")
    ap.add_argument("--compare", type=Path, help="an earlier --save to compare with")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--sandbox", action="store_true")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    path = args.questions or (DEMO_SET if WHICH == "demo" else None)
    if path is None:
        ap.error("--questions is required outside the demo")
    data = json.loads(path.read_text(encoding="utf-8"))
    sets = {"questions": data.get("questions", []),
            "entry_replies": data.get("entry_replies", [])}

    problems = validate(sets["questions"] + sets["entry_replies"])
    if problems:
        print("the test set does not match this journal:", *problems, sep="\n  ")
        return 1

    before = {}
    if args.compare:
        old = json.loads(args.compare.read_text(encoding="utf-8"))
        before = {r["id"]: r for rs in old["results"].values() for r in rs}

    collection = get_collection()
    entity_index = companion.load_entity_index()
    # The first search loads the embedders; keep that out of the timings.
    companion.build_context_block("warm up", collection, entity_index)

    print(f"{WHICH} journal, {collection.count()} chunks in the journal "
          f"collection, EXCERPT_CHARS={EXCERPT_CHARS}")
    results, summaries = {}, {}
    for name, tests in sets.items():
        if not tests:
            continue
        results[name] = [run_one(t, collection, entity_index) for t in tests]
        summaries[name] = report(name, results[name], before)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(
            {"journal": WHICH, "questions": str(path),
             "summary": summaries, "results": results}, indent=2) + "\n",
            encoding="utf-8")
        print(f"\nsaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
