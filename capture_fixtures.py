# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Capture mock-mode fixtures from real Claude output.

Mock mode is only worth having if what it shows is authentic — real
phrasing, real JSON quirks, real lengths. So the fixtures aren't
hand-faked: this runs the curated seed corpus through the real pipeline
once, against a real API key, and records every response into
`mock_fixtures/<module.function>.json`.

    .venv\\Scripts\\python.exe capture_fixtures.py --stages tag,entities,summaries

**This spends real tokens.** It is a one-off authoring step, not something
a cloner runs.

Two rules, because getting them wrong ships someone's private journal to
everyone who clones:

1. Capture from the **curated seed corpus only**, never from a real
   journal. The natural mistake is running this against whatever data
   dirs are already sitting there — so it refuses unless
   `--i-am-running-the-seed-corpus` is passed.
2. Run the Phase 0 item 12 leak grep before committing. `--check` does
   exactly that and nothing else, over everything captured from a real
   model that ships: `mock_fixtures/` plus the seed corpus's session
   archives and summaries.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from mock_client import FIXTURE_DIR, _call_key

MAX_PER_KEY = 12  # enough variety for deterministic selection to feel varied

# The seed corpus's fictional author. Fixtures are full of this name by
# design; the leak check must not confuse it for the real one.
SEED_AUTHOR = "Jordan"

# Frames to look past when deriving a call key, so a recorded response is
# filed under the pipeline function that made it, not under this harness.
SKIP = frozenset({"mock_client", "capture_fixtures"})

# Every module that does `from config import get_client`. The import binds
# the name at import time, so patching `config.get_client` alone would miss
# all of them — each module's own binding has to be replaced.
CLIENT_MODULES = [
    "categories", "companion", "dreams", "entities",
    "organic", "patterns", "seed", "server", "summarizer",
]


# ---------------------------------------------------------------------------
# RECORDING CLIENT
# ---------------------------------------------------------------------------


class _RecordingMessages:
    def __init__(self, real, sink):
        self._real = real
        self._sink = sink

    def create(self, **kwargs):
        response = self._real.create(**kwargs)
        text = next((b.text for b in response.content if b.type == "text"), "")
        self._sink(_call_key(SKIP), text)
        return response

    def stream(self, **kwargs):
        return _RecordingStream(self._real.stream(**kwargs), _call_key(SKIP), self._sink)


class _RecordingStream:
    """Wraps a real stream, tees the text, records on exit. Has to be lazy —
    the caller consumes `text_stream` inside the `with` block, so there's
    nothing to record until the block ends."""

    def __init__(self, inner, key, sink):
        self._inner = inner
        self._key = key
        self._sink = sink
        self._parts = []
        self._stream = None

    def __enter__(self):
        self._stream = self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self._sink(self._key, "".join(self._parts))
        return self._inner.__exit__(*exc)

    @property
    def text_stream(self):
        for text in self._stream.text_stream:
            self._parts.append(text)
            yield text

    def get_final_message(self):
        return self._stream.get_final_message()


class RecordingClient:
    def __init__(self, real, sink):
        self.messages = _RecordingMessages(real.messages, sink)


# ---------------------------------------------------------------------------
# CAPTURE
# ---------------------------------------------------------------------------


def _install(captured: dict):
    """Point every `get_client()` binding at a recording wrapper. Phase 1's
    single injection point is what makes this a handful of lines instead of
    fourteen monkeypatches."""
    import config

    def sink(key, text):
        if text.strip():
            captured.setdefault(key, []).append(text)

    def recording_factory():
        import anthropic
        return RecordingClient(anthropic.Anthropic(), sink)

    config.get_client = recording_factory
    for name in CLIENT_MODULES:
        module = __import__(name)
        if hasattr(module, "get_client"):
            module.get_client = recording_factory


STAGES = {
    "tag": ("categories", "run_tagging"),
    "entities": ("entities", "build"),
    "summaries": ("summarizer", "build"),
    "dreams": ("dreams", "extract"),
    "patterns": ("patterns", "build"),
    "organic": ("organic", "scan"),
}


def capture(stages: list[str], force: bool):
    captured: dict[str, list] = {}
    _install(captured)

    for stage in stages:
        module_name, func_name = STAGES[stage]
        print(f"\n=== {stage} ({module_name}.{func_name})")
        module = __import__(module_name)
        try:
            getattr(module, func_name)(force=force)
        except TypeError:
            getattr(module, func_name)()

    FIXTURE_DIR.mkdir(exist_ok=True)
    for key, responses in captured.items():
        path = FIXTURE_DIR / f"{key}.json"
        existing = []
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))["responses"]
        # de-duplicate: repeated identical output adds nothing but weight
        merged = list(dict.fromkeys(existing + responses))[:MAX_PER_KEY]
        path.write_text(
            json.dumps({"responses": merged}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  {path.name}: {len(merged)} responses")

    print("\nNow run:  python capture_fixtures.py --check")


# ---------------------------------------------------------------------------
# LEAK CHECK
# ---------------------------------------------------------------------------

# Same shape as the Phase 0 item 12 grep, aimed at the shipped captures —
# they go public with the repo, so they're source, not data.
LEAK_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_\-]{10,}", "Anthropic API key"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "email address"),
    (r"\b\d{3}[.\-\s]\d{3}[.\-\s]\d{4}\b", "phone number"),
    (r"[A-Za-z]:\\Users\\[^\\\"\s]+", "Windows user path"),
    (r"/(?:home|Users)/[^/\"\s]+", "Unix home path"),
]


def _identity_patterns() -> list[tuple[str, str]]:
    """The real author's name, which the generic patterns above can't know.
    This is the likeliest leak of all: capture with the data dirs pointed
    at the corpus but `MC_AUTHOR_NAME` left alone, and every summary comes
    back written about the real person.

    Read from .env rather than the environment, because capture runs with
    MC_AUTHOR_NAME overridden to the corpus author — trusting the live
    value would check for the fictional name and miss the real one.

    Deliberately not third-party-names.txt: it holds generic words like
    "Mom" that the fiction uses legitimately, and hundreds of false hits
    would only teach people to ignore this check."""
    names = set()
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if not line.startswith("MC_AUTHOR_NAME="):
                continue
            value = line.split("=", 1)[1].strip()
            if value[:1] in ("'", '"'):           # quoted: take the quoted run
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end != -1 else value[1:]
            else:                                  # bare: a # starts a comment
                value = value.split("#", 1)[0].strip()
            names.add(value)
    from config import AUTHOR
    names.add(AUTHOR)
    names -= {"", SEED_AUTHOR, "the journal author"}
    return [(rf"\b{re.escape(n)}\b", "real author name") for n in sorted(names)]


ROOT = Path(__file__).parent

# Everything captured from a real model that ships in the repo. The
# fixtures are the obvious half; the seed corpus's session archives and
# summaries are the other — they're generated by build_sessions.py from
# real API calls too, so they need the same grep before they're pushed.
SCAN_GLOBS = [
    "mock_fixtures/*.json",
    "mock_fixtures/demo_close/*.json",
    "seed_corpus/sessions/**/*.json",
    "seed_corpus/summaries/**/*.md",
    "seed_corpus/derived/**/*.json",
    "seed_corpus/derived/**/*.md",
]


def _scan_paths() -> list[Path]:
    seen = {}
    for glob in SCAN_GLOBS:
        for path in ROOT.glob(glob):
            if path.is_file():
                seen[path.resolve()] = path
    return sorted(seen.values())


def check() -> int:
    findings = []
    identity = _identity_patterns()
    patterns = LEAK_PATTERNS + identity
    paths = _scan_paths()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for pattern, label in patterns:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{rel}:{line}  {label}: {match.group()}")

    if findings:
        print("LEAKS FOUND — do not commit:")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print(f"clean — {len(paths)} shipped file(s), no leaks matched")
    if not identity:
        # A "clean" here would be reporting on a check that never ran: the
        # generic patterns can't know a name, so without one the likeliest
        # leak of all is the one thing unexamined.
        print(
            "\nWARNING: no real author name to check for.\n"
            "  MC_AUTHOR_NAME isn't set in .env, so the real-name grep "
            "matched nothing\n  because it had nothing to match. If you set "
            "it in your shell instead,\n  put it in .env too — the check "
            "reads .env on purpose (capture overrides\n  the live value to "
            f"{SEED_AUTHOR!r}, so trusting the environment would check for "
            "the\n  fictional name and miss the real one)."
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stages", default=",".join(STAGES),
                        help=f"comma-separated: {', '.join(STAGES)}")
    parser.add_argument("--force", action="store_true",
                        help="re-run stages that are already cached")
    parser.add_argument("--check", action="store_true",
                        help="leak-grep mock_fixtures/ and exit")
    parser.add_argument("--i-am-running-the-seed-corpus", action="store_true",
                        help="confirm the data dirs hold the curated seed "
                             "corpus, not a real journal")
    args = parser.parse_args()

    if args.check:
        sys.exit(check())

    if not args.i_am_running_the_seed_corpus:
        sys.exit(
            "REFUSED: fixtures ship publicly and must come from the curated "
            "seed corpus only.\nIf the data dirs really do hold the seed "
            "corpus, re-run with --i-am-running-the-seed-corpus."
        )

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        sys.exit(f"unknown stage(s): {', '.join(unknown)}")

    capture(stages, args.force)


if __name__ == "__main__":
    main()
