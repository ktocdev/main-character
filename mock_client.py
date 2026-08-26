"""
Mock client — a drop-in stand-in for `anthropic.Anthropic()`.

Enabled by `MC_MOCK=1`. `config.get_client()` returns this instead of the
real SDK client, so no call site knows the difference: the same
`messages.create()` / `messages.stream()` surface, the same response shape
(`.content` blocks, `.stop_reason`, `.usage`), and no network access.

Two things it deliberately does NOT do:

- **Return instantly.** Every call type gets a delay roughly matching what
  the real call feels like. Loading states, spinners, and the staged
  close-chat progress UI (Phase 2) only exist to cover latency — a mock
  that resolves in zero time can't exercise any of them.
- **Reset anything.** Mock mode swaps the model, not the storage. Writes
  still land in real `journal_entries/`, `chroma_data/`, `entity_graph/`.
  Getting back to a clean slate means wiping data the same way you would
  in real mode.

Responses come from `mock_fixtures/<call_key>.json`, captured by running
the curated seed corpus through the real pipeline once (see
`capture_fixtures.py`). Where no fixture exists yet, a shape-valid
response is synthesized from the call's own `json_schema` so the app still
works end to end — visibly mock, never mistaken for captured output.
"""

import hashlib
import inspect
import json
import random
import time
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "mock_fixtures"

# Per-call-type delay in seconds, keyed by `module.function` of the caller.
# Roughly what the real call feels like: tagging is a short structured
# call, entity extraction and pattern detection chew through far more
# context. These are what make Phase 2's progress UI testable at all.
DELAYS = {
    "companion._stream_turn": 1.2,
    "seed._call": 2.0,
    "entities.extract_conversation": 2.5,
    "entities.suggest_merges": 3.0,
    "categories.tag_conversation": 0.8,
    "patterns.build": 3.0,
    "dreams._extract": 2.0,
    "organic.scan": 1.5,
    "summarizer._arc_text": 1.5,
    "summarizer.build_domains": 2.0,
    "summarizer.build_entry_summaries": 1.0,
    "summarizer.build_snapshot": 1.5,
    "sessions._generate_title": 0.6,
}
DEFAULT_DELAY = 1.0

# Streaming cadence: seconds between emitted chunks. Slow enough that
# scroll anchoring and mid-stream interaction (Phase 2 items 1 and 5) are
# actually observable.
STREAM_CHUNK_DELAY = 0.035


def _call_key(skip: frozenset = frozenset({"mock_client"})) -> str:
    """`module.function` of the first frame outside the plumbing — the
    natural identity of a call type, and stable without threading a marker
    kwarg through 14 call sites (which the real SDK would reject).

    `skip` exists so `capture_fixtures.py` can wrap the real client and
    still derive the same keys, rather than recording everything under
    its own frame."""
    for frame in inspect.stack()[1:]:
        module = Path(frame.filename).stem
        if module not in skip:
            return f"{module}.{frame.function}"
    return "unknown.unknown"


def _prompt_text(kwargs: dict) -> str:
    """Flatten the request's user content, for deterministic fixture
    selection — the same input picks the same captured response every run,
    so mock mode is reproducible rather than randomized."""
    parts = []
    for message in kwargs.get("messages", []):
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.extend(b.get("text", "") for b in content if isinstance(b, dict))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

_fixture_cache: dict[str, list] = {}


def _fixtures(key: str) -> list:
    if key not in _fixture_cache:
        path = FIXTURE_DIR / f"{key}.json"
        try:
            _fixture_cache[key] = json.loads(path.read_text(encoding="utf-8"))["responses"]
        except (OSError, ValueError, KeyError):
            _fixture_cache[key] = []
    return _fixture_cache[key]


def _pick(key: str, prompt: str) -> str | None:
    """One captured response, chosen by prompt hash so it's stable across
    runs. Returns None when nothing has been captured for this call type."""
    responses = _fixtures(key)
    if not responses:
        return None
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    chosen = responses[int.from_bytes(digest[:4], "big") % len(responses)]
    return chosen if isinstance(chosen, str) else json.dumps(chosen)


# ---------------------------------------------------------------------------
# SYNTHESIS — the no-fixture fallback
# ---------------------------------------------------------------------------

MOCK_PROSE = (
    "[mock] This is placeholder text from mock mode, not a real model "
    "response. It exists so the UI has something of realistic length to "
    "render, stream, and lay out. Set MC_MOCK=0 and provide an API key for "
    "real output, or capture fixtures with capture_fixtures.py."
)


def _synth(schema: dict, name: str = "") -> object:
    """Build a shape-valid instance of a json_schema. Values are obviously
    placeholder — the point is exercising the UI's rendering and storage
    paths, not faking plausible journal content."""
    kind = schema.get("type", "string")
    if kind == "object":
        return {
            prop: _synth(sub, prop)
            for prop, sub in schema.get("properties", {}).items()
        }
    if kind == "array":
        return [_synth(schema.get("items", {"type": "string"}))]
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.5
    if kind == "boolean":
        return True
    if "enum" in schema:
        return schema["enum"][0]
    # Dates have to parse — several consumers sort and window on them.
    if "date" in name.lower():
        return time.strftime("%Y-%m-%d")
    return f"[mock {name}]" if name else "[mock]"


def _fallback(kwargs: dict) -> str:
    schema = (
        kwargs.get("output_config", {})
        .get("format", {})
        .get("schema")
    )
    if schema:
        return json.dumps(_synth(schema))
    return MOCK_PROSE


# ---------------------------------------------------------------------------
# RESPONSE OBJECTS — the shape call sites already destructure
# ---------------------------------------------------------------------------


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Usage:
    """Present because Phase 2's cost meter reads `usage.*` off every
    response. Mock tokens are a rough char/4 estimate so the meter has
    something non-zero to accumulate and render."""

    def __init__(self, prompt: str, output: str):
        self.input_tokens = max(1, len(prompt) // 4)
        self.output_tokens = max(1, len(output) // 4)


class _Response:
    def __init__(self, text: str, prompt: str, model: str):
        self.content = [_TextBlock(text)]
        self.stop_reason = "end_turn"
        self.role = "assistant"
        self.model = model
        self.usage = _Usage(prompt, text)


class _Stream:
    """Context manager matching `client.messages.stream(...)`: a
    `.text_stream` generator and `.get_final_message()`."""

    def __init__(self, text: str, prompt: str, model: str):
        self._text = text
        self._final = _Response(text, prompt, model)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        # Chunk on whitespace with jittered sizes — a real stream doesn't
        # arrive a character or a word at a time, and UI that only looks
        # right under one cadence should look wrong here.
        words = self._text.split(" ")
        i = 0
        while i < len(words):
            n = random.randint(1, 4)
            chunk = " ".join(words[i:i + n])
            if i + n < len(words):
                chunk += " "
            i += n
            time.sleep(STREAM_CHUNK_DELAY)
            yield chunk

    def get_final_message(self):
        return self._final


class _Messages:
    def create(self, **kwargs):
        key = _call_key()
        time.sleep(DELAYS.get(key, DEFAULT_DELAY))
        prompt = _prompt_text(kwargs)
        text = _pick(key, prompt) or _fallback(kwargs)
        return _Response(text, prompt, kwargs.get("model", "mock"))

    def stream(self, **kwargs):
        key = _call_key()
        time.sleep(DELAYS.get(key, DEFAULT_DELAY))
        prompt = _prompt_text(kwargs)
        text = _pick(key, prompt) or _fallback(kwargs)
        return _Stream(text, prompt, kwargs.get("model", "mock"))


class MockClient:
    def __init__(self, *args, **kwargs):
        self.messages = _Messages()
