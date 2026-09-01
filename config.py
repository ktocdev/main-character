"""
Config — centralized settings for RAG Journal.

Single source of truth for what used to be scattered across modules: the
two-model split (companion vs. processing), the companion's effort level,
server host/port, data directory paths, retrieval tuning, mock mode, and
the author name. All values are env-overridable (via .env) with working
defaults, so a fresh clone runs with zero configuration.

Also owns the model -> valid-effort-levels map and the model ->
thinking-support map, which both the API call sites and (eventually) the
Settings UI read from — one place to update when a new model ships.
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------
# Two workloads, two settings: the companion is the one user-visible voice,
# so quality matters most there. Processing (entity extraction, summaries,
# arcs, categories, patterns, dreams, organic naming, seed integration) is
# structured/mechanical, runs in bulk, and dominates token spend — Sonnet 5
# is the default there because that work doesn't need Opus-tier reasoning.

MC_COMPANION_MODEL = os.getenv("MC_COMPANION_MODEL", "claude-opus-4-6").strip()
MC_PROCESSING_MODEL = os.getenv("MC_PROCESSING_MODEL", "claude-sonnet-5").strip()

MC_COMPANION_EFFORT = os.getenv("MC_COMPANION_EFFORT", "high").strip()

# Model -> valid `effort` levels. Haiku 4.5 doesn't take the parameter at
# all; Opus 4.6 predates `xhigh`. A Settings picker must derive its options
# from this map rather than offering a static list, or a request 400s.
MODEL_EFFORT_LEVELS = {
    "claude-opus-4-6": ["low", "medium", "high", "max"],
    "claude-opus-4-7": ["low", "medium", "high", "max", "xhigh"],
    "claude-opus-4-8": ["low", "medium", "high", "max", "xhigh"],
    "claude-opus-5": ["low", "medium", "high", "max", "xhigh"],
    "claude-sonnet-5": ["low", "medium", "high", "max", "xhigh"],
    "claude-haiku-4-5": [],
}

# Model -> the name a person recognizes. The API ids are what get written
# to .env; these are what the pickers show.
MODEL_LABELS = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-7": "Opus 4.7",
    "claude-opus-4-8": "Opus 4.8",
    "claude-opus-5": "Opus 5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5": "Haiku 4.5",
}

# Model -> USD list price per million tokens, input and output.
#
# Display only. Nothing here bills, meters or enforces anything -- these
# exist so the Settings pickers can show what a choice costs relative to the
# others, which is the whole point of offering the choice. Anthropic's
# pricing page is the authority; check these against it when adding a model,
# and treat a figure here as stale rather than as a quote.
MODEL_PRICES = {
    "claude-opus-4-6": {"in": 5.0, "out": 25.0},
    "claude-opus-4-7": {"in": 5.0, "out": 25.0},
    "claude-opus-4-8": {"in": 5.0, "out": 25.0},
    "claude-opus-5": {"in": 5.0, "out": 25.0},
    "claude-sonnet-5": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}

# Model -> whether the `thinking` parameter is supported at all. Where
# it's supported, Opus 4.6/4.7/4.8 default to *off* when the parameter is
# omitted; Sonnet 5 and Opus 5 default to *adaptive* when omitted. Haiku
# 4.5 rejects the parameter outright.
MODEL_THINKING_SUPPORT = {
    "claude-opus-4-6": True,
    "claude-opus-4-7": True,
    "claude-opus-4-8": True,
    "claude-opus-5": True,
    "claude-sonnet-5": True,
    "claude-haiku-4-5": False,
}


def companion_effort_kwargs(model: str = None, effort: str = None) -> dict:
    """The `output_config` kwarg carrying the companion's effort level, for
    the given model (default: the configured companion model/effort).
    Omitted entirely for models that don't take an `effort` param at all
    (Haiku 4.5) or when the configured level isn't valid for the model."""
    model = model or MC_COMPANION_MODEL
    effort = effort or MC_COMPANION_EFFORT
    if effort in MODEL_EFFORT_LEVELS.get(model, []):
        return {"output_config": {"effort": effort}}
    return {}


def processing_thinking_kwargs(model: str = None) -> dict:
    """The `thinking` kwarg for a processing call on the given model
    (default: the configured processing model). Processing calls want
    thinking explicitly disabled on models that support the parameter —
    several call sites are sized tight enough that adaptive thinking would
    eat the budget and truncate the response — and omitted entirely on
    models that reject the parameter (Haiku 4.5)."""
    model = model or MC_PROCESSING_MODEL
    if MODEL_THINKING_SUPPORT.get(model, True):
        return {"thinking": {"type": "disabled"}}
    return {}


# ---------------------------------------------------------------------------
# SERVER
# ---------------------------------------------------------------------------

HOST = os.getenv("MC_HOST", "127.0.0.1")
PORT = int(os.getenv("MC_PORT", "8144"))

# ---------------------------------------------------------------------------
# DATA DIRECTORIES
# ---------------------------------------------------------------------------
# RAG_JOURNAL_DIR / RAG_CHROMA_DIR keep their existing env var names (predate
# this module); the rest are new and use the MC_ prefix.

JOURNAL_DIR = Path(os.getenv("RAG_JOURNAL_DIR", _PROJECT_ROOT / "journal_entries"))
CHROMA_DIR = Path(os.getenv("RAG_CHROMA_DIR", _PROJECT_ROOT / "chroma_data"))
ENTITY_DIR = Path(os.getenv("MC_ENTITY_DIR", _PROJECT_ROOT / "entity_graph"))
SUMMARY_DIR = Path(os.getenv("MC_SUMMARY_DIR", _PROJECT_ROOT / "summaries"))
CATEGORY_DIR = Path(os.getenv("MC_CATEGORY_DIR", _PROJECT_ROOT / "categories"))
PATTERN_DIR = Path(os.getenv("MC_PATTERN_DIR", _PROJECT_ROOT / "patterns"))
DREAM_DIR = Path(os.getenv("MC_DREAM_DIR", _PROJECT_ROOT / "dreams"))
SESSION_DIR = Path(os.getenv("MC_SESSION_DIR", _PROJECT_ROOT / "sessions"))

# ---------------------------------------------------------------------------
# RETRIEVAL TUNING
# ---------------------------------------------------------------------------

N_SEMANTIC = int(os.getenv("MC_N_SEMANTIC", "6"))     # semantically similar chunks per question
N_RECENT = int(os.getenv("MC_N_RECENT", "3"))         # most recent chunks always included
EXCERPT_CHARS = int(os.getenv("MC_EXCERPT_CHARS", "2000"))
MAX_TOKENS = int(os.getenv("MC_MAX_TOKENS", "8000"))  # companion reply budget

# ---------------------------------------------------------------------------
# MISC
# ---------------------------------------------------------------------------

MOCK_MODE = os.getenv("MC_MOCK", "0").strip() == "1"
AUTHOR = os.getenv("RAG_AUTHOR_NAME", "").strip() or "the journal author"


# ---------------------------------------------------------------------------
# TIME
# ---------------------------------------------------------------------------
# The server knows the real time; the model never should. Everything that
# needs "now" comes through now_local() so a single clock feeds writes,
# session closes and the seed's Updated line.
#
# Stored wall-clock stays LOCAL, not UTC: a journal entry belongs to the
# day it was lived, and `date` / `ts[:10]` are what close_session groups
# days by and what every window and sort reads. The zone rides alongside
# so a local stamp stays interpretable.

TIMEZONE = os.getenv("MC_TIMEZONE", "").strip()
DATE_FORMAT = os.getenv("MC_DATE_FORMAT", "%B %d, %Y").strip()

# The date styles the Settings picker offers. Storage is ISO regardless —
# these only decide how a stamp is *displayed*, so the set is deliberately
# small: a free-text strftime box would let someone save a format that
# renders every date as an empty string with no way back but a file edit.
DATE_FORMATS = {
    "%B %d, %Y": "long",     # August 25, 2026
    "%m/%d/%y": "short",     # 08/25/26
}

# Reserved for Phase 10. The slot exists now so that shipping a second
# language is a config change rather than a Settings rebuild; until then
# this is the only accepted value.
LANGUAGE = os.getenv("MC_LANGUAGE", "en").strip() or "en"
LANGUAGES = {"en": "English"}


def date_style(fmt: str = None) -> str:
    """'long' or 'short' for the given format — what the browser needs to
    render a stamp the same way the server would.

    A format outside DATE_FORMATS can still be hand-set in .env, and the
    Settings picker will not have offered it. Those fall back to the rule
    /api/status used before the styles were named — a month name means long —
    rather than reading as long unconditionally, which would render a custom
    numeric format like %m/%d/%Y as "August 25, 2026" in the browser while
    every server-side stamp stayed numeric.
    """
    fmt = fmt if fmt is not None else DATE_FORMAT
    if fmt in DATE_FORMATS:
        return DATE_FORMATS[fmt]
    return "long" if "%B" in fmt else "short"


def _zone():
    if TIMEZONE:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(TIMEZONE)
        except Exception:
            pass  # bad zone name shouldn't stop the app booting
    return datetime.now().astimezone().tzinfo


def now_local() -> datetime:
    """Current time in the configured zone, timezone-aware."""
    return datetime.now(_zone())


def zone_name() -> str:
    """The zone the stamps are actually in — the configured IANA name only
    when it resolved, else whatever the server's own zone calls itself.

    Not simply `TIMEZONE`: this rides alongside every stored stamp and is
    what /api/status reports, so it must never name a zone the clock isn't
    using. zoneinfo ships no database of its own on Windows, so _zone()
    falls back silently there unless `tzdata` is installed."""
    zone = _zone()
    if TIMEZONE and getattr(zone, "key", None) == TIMEZONE:
        return TIMEZONE
    return now_local().tzname() or ""


def stamp(when: datetime | None = None) -> str:
    """The `YYYY-MM-DD HH:MM` form used by session messages."""
    return (when or now_local()).strftime("%Y-%m-%d %H:%M")


def parse_stamp(text: str) -> datetime | None:
    """A client-supplied stamp, or None if it isn't one. Naive on purpose —
    it's wall-clock in the configured zone, same as what stamp() emits."""
    try:
        return datetime.strptime(text.strip()[:16], "%Y-%m-%d %H:%M").replace(
            tzinfo=_zone())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# CLIENT
# ---------------------------------------------------------------------------


def get_client():
    """The single construction point for the Anthropic client.

    Every call site goes through this rather than building its own
    `anthropic.Anthropic()`, so mock mode is one swap instead of fourteen.
    In mock mode the real SDK is never constructed — that's what lets the
    app boot with no `ANTHROPIC_API_KEY` at all, since the SDK raises on
    construction when the key is missing.

    The returned client is wrapped by `metering.py`, which counts what each
    call cost on the way back. Wrapping here rather than at the call sites is
    the same argument as mock mode: one swap instead of fourteen, and a new
    call site is metered by default rather than by remembering to.

    Mock mode is metered too. The counts are fictional, but the plumbing that
    carries them is the same plumbing -- a cost view that only works against
    the real API is one nobody can develop against.

    Phase 2 item 10 puts the spend-cap check here, in front of the returned
    client, so the ceiling can't be bypassed by a call site. It needs a check
    *before* the call, which is why it is a separate piece of work from the
    counting: metering only ever learns what a call cost after it is spent.
    """
    import metering
    if MOCK_MODE:
        from mock_client import MockClient
        return metering.wrap(MockClient())
    import anthropic
    return metering.wrap(anthropic.Anthropic())
