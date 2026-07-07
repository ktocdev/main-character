"""
Pattern Library — Phase 3 of the RAG Journal.

Detects and names the recurring patterns in the journal — emotional
cycles, behavioral pipelines, relationship dynamics, recovery patterns,
creative rhythms — from the weekly arcs and domain summaries. The named
patterns become retrieval Layer 4: a compact library loaded into every
companion turn so it can say "this is the same pipeline as two weeks ago"
with receipts.

Patterns are proposals, not diagnoses: the user can dismiss any pattern in
the web UI and it stays dismissed across re-detections.

Usage:
    python patterns.py build            # detect (skips if inputs unchanged)
    python patterns.py build --force    # re-detect
    python patterns.py list             # show the current library

Layout (gitignored — personal data):
    patterns/library.json     the detected patterns + input hash
    patterns/dismissed.json   pattern names the user has dismissed
"""

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import anthropic

from summarizer import ARC_DIR, DOMAIN_DIR, _strip_hash_comment

MODEL = "claude-opus-4-8"
PATTERN_DIR = Path(__file__).parent / "patterns"
LIBRARY_FILE = PATTERN_DIR / "library.json"
DISMISSED_FILE = PATTERN_DIR / "dismissed.json"
AUTHOR = os.getenv("RAG_AUTHOR_NAME", "").strip() or "the journal author"
INPUT_CHARS = 120_000
CONTEXT_CHARS = 4_500   # cap on the Layer 4 block injected per companion turn

PATTERN_KINDS = [
    "emotional cycle",
    "behavioral pipeline",
    "relationship dynamic",
    "recovery pattern",
    "creative rhythm",
]

PATTERN_PROMPT = """\
You are studying {author}'s journal to find its recurring patterns — the \
shapes that repeat across weeks and domains. Below are the weekly arc \
summaries (oldest first) and the per-domain living documents.

Find patterns of these kinds:
- emotional cycle: a repeating emotional sequence (e.g. isolation -> \
vulnerability -> impulsive decisions)
- behavioral pipeline: one thing reliably leading to another (e.g. work \
stress -> drinking -> physical setback)
- relationship dynamic: the same dynamic recurring across different people \
or with the same person
- recovery pattern: what actually helps {author} recover, as evidenced by \
what happened — not what anyone thinks should help
- creative rhythm: when and how creative/productive stretches arrive

Rules:
- Only report a pattern with at least two dated instances in the text. \
One occurrence is an event, not a pattern.
- Give each pattern a short, concrete name in {author}'s own vocabulary \
where possible — the kind of shorthand a sharp friend would coin.
- The description states the shape of the pattern plainly. The trigger \
states what tends to set it off. Instances cite the dates and what \
happened, briefly.
- This is a record, not a diagnosis. No clinical language, no \
pathologizing, no advice.
- Confidence "high" means the pattern shows clearly in 3+ instances; \
"medium" means it's suggestive but thinner.

<weekly_arcs>
{arcs}
</weekly_arcs>

<domain_documents>
{domains}
</domain_documents>"""

PATTERN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": PATTERN_KINDS},
                    "description": {"type": "string"},
                    "trigger": {
                        "type": "string",
                        "description": "What tends to set this pattern off",
                    },
                    "instances": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "date": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["date", "note"],
                        },
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium"]},
                },
                "required": ["name", "kind", "description", "trigger",
                             "instances", "confidence"],
            },
        },
    },
    "required": ["patterns"],
}


def _inputs() -> tuple[str, str]:
    arcs = "\n\n".join(
        _strip_hash_comment(f.read_text(encoding="utf-8"))
        for f in sorted(ARC_DIR.glob("*.md"))
    ) if ARC_DIR.exists() else ""
    domains = "\n\n".join(
        _strip_hash_comment(f.read_text(encoding="utf-8"))
        for f in sorted(DOMAIN_DIR.glob("*.md"))
    ) if DOMAIN_DIR.exists() else ""
    return arcs[:INPUT_CHARS], domains[:INPUT_CHARS]


def load_dismissed() -> list[str]:
    if DISMISSED_FILE.exists():
        return json.loads(DISMISSED_FILE.read_text(encoding="utf-8"))
    return []


def dismiss(name: str):
    dismissed = load_dismissed()
    if name not in dismissed:
        dismissed.append(name)
    PATTERN_DIR.mkdir(parents=True, exist_ok=True)
    DISMISSED_FILE.write_text(
        json.dumps(dismissed, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_library() -> dict:
    if LIBRARY_FILE.exists():
        return json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    return {"generated": None, "hash": None, "patterns": []}


def load_patterns() -> list[dict]:
    """The library minus anything the user has dismissed."""
    dismissed = {d.lower() for d in load_dismissed()}
    return [
        p for p in load_library()["patterns"]
        if p["name"].lower() not in dismissed
    ]


def build(force: bool = False, quiet: bool = False) -> dict:
    """Detect patterns from the current arcs + domain docs. Skips the API
    call when the inputs haven't changed since the last detection."""
    arcs, domains = _inputs()
    if not arcs:
        print("  no weekly arcs yet — run the summarizer first")
        return load_library()

    current_hash = hashlib.md5(f"{arcs}|{domains}".encode()).hexdigest()[:12]
    library = load_library()
    if library["hash"] == current_hash and not force:
        if not quiet:
            print(f"  pattern library current ({len(library['patterns'])} patterns)")
        return library

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": PATTERN_SCHEMA}},
        messages=[{
            "role": "user",
            "content": PATTERN_PROMPT.format(author=AUTHOR, arcs=arcs, domains=domains),
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined")
    raw = next(b.text for b in response.content if b.type == "text")
    patterns = json.loads(raw)["patterns"]

    library = {
        "generated": datetime.now().isoformat(timespec="minutes"),
        "hash": current_hash,
        "patterns": patterns,
    }
    PATTERN_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_FILE.write_text(
        json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not quiet:
        for p in patterns:
            print(f"  [{p['confidence']}] {p['name']} ({p['kind']}, "
                  f"{len(p['instances'])} instances)")
    print(f"\n  Pattern library: {len(patterns)} patterns detected")
    return library


def pattern_context() -> str:
    """Compact Layer 4 block for the companion: one line per pattern,
    whole lines only — better to drop a tail pattern than serve half of one."""
    lines, used = [], 0
    for p in load_patterns():
        dates = ", ".join(i["date"] for i in p["instances"][:4])
        line = (
            f"- {p['name']} ({p['kind']}, seen {dates}): "
            f"{p['description'][:220]} Trigger: {p['trigger'][:120]}"
        )
        if used + len(line) > CONTEXT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        build(force="--force" in sys.argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        for p in load_patterns():
            print(f"\n[{p['confidence']}] {p['name']} ({p['kind']})")
            print(f"  {p['description']}")
            print(f"  trigger: {p['trigger']}")
            for i in p["instances"]:
                print(f"    {i['date']}: {i['note']}")
    else:
        print(__doc__)
