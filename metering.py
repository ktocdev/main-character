# SPDX-License-Identifier: AGPL-3.0-or-later
"""
What this session has spent, counted as it happens.

Every Anthropic response carries a `usage` block. This module wraps the
client returned by `config.get_client()` so that block is read on the way
back out, added to a running per-session total, and priced against
`config.MODEL_PRICES`. Nothing here changes a request.

It does now *refuse* some: the proxy asks `caps.check()` before each call, so
a spend ceiling cannot be bypassed by a call site for the same reason a call
cannot go uncounted. The check lives here rather than in a second wrapper
because another layer would add another frame to every call, and the frame
stack is what decides which bucket a call belongs to (see `_bucket`).

The accumulator is process-global and resets on restart, which is the right
lifetime for the question it answers -- "what has this session cost me?" --
and the wrong one for a monthly budget. `caps.py` keeps that one in a file,
and is handed each call's dollars from `record()` below.

Three things are worth knowing before trusting the number:

  - **It is a list-price estimate, not a bill.** `MODEL_PRICES` is a
    hand-maintained table that goes stale, and it knows nothing about batch
    discounts or whatever the account actually pays. It is here to make one
    model choice comparable to another, which it does honestly.
  - **Cached input is priced differently from fresh input.** The companion
    marks its system prompt `cache_control: ephemeral`, so a total that
    ignored the cache fields would overcharge every turn after the first by
    roughly ten times on the cached portion.
  - **A call is counted after it returns**, and a call that raises is never
    counted though the tokens were still spent. So the total is a floor under
    real spend, never a ceiling -- which is also why a cap can only ever be
    compared against what is *already* spent, and why the call that crosses
    a ceiling completes while the next one is refused.
"""

import inspect
from pathlib import Path as _Path

# Ratios to a model's base input price. Anthropic bills a cache write above
# fresh input and a cache read far below it; storing ratios rather than a
# second price table means adding a model still means adding one row.
CACHE_WRITE_RATIO = 1.25
CACHE_READ_RATIO = 0.10

# Which bucket a call belongs to is decided by *where it was made from*, not
# by which client object made it: server.py builds one client at startup and
# passes it both to the companion and to `sessions.close_session`, whose title
# call is processing work on the processing model. Bucketing by client
# instance would file that under companion. `mock_client._call_key` reads the
# stack for the same reason -- there is no marker kwarg the real SDK accepts.
COMPANION_MODULES = frozenset({"companion"})

_EMPTY = {"calls": 0, "input": 0, "output": 0, "cache_write": 0,
          "cache_read": 0, "dollars": 0.0}

SESSION = {"companion": dict(_EMPTY), "processing": dict(_EMPTY)}


def reset():
    """Start a fresh count. Called when a session closes, so the next one
    starts at zero rather than inheriting the last one's total."""
    for bucket in SESSION.values():
        bucket.update(_EMPTY)


def _bucket(skip: frozenset) -> str:
    """companion or processing, from the first frame outside the plumbing."""
    for frame in inspect.stack()[1:]:
        module = _Path(frame.filename).stem
        if module not in skip:
            return "companion" if module in COMPANION_MODULES else "processing"
    # An unattributable call is still spend. Filing it under processing keeps
    # it in the total; dropping it would make the number quietly wrong.
    return "processing"


def price(model: str, usage: dict) -> float:
    """USD for one call's usage. An unpriced model costs 0.0 rather than
    raising -- a model missing from the table is a stale table, and taking
    the app down over a display figure is the wrong trade."""
    from config import MODEL_PRICES
    rates = MODEL_PRICES.get(model)
    if not rates:
        return 0.0
    per_in = rates["in"] / 1_000_000
    per_out = rates["out"] / 1_000_000
    return (usage["input"] * per_in
            + usage["cache_write"] * per_in * CACHE_WRITE_RATIO
            + usage["cache_read"] * per_in * CACHE_READ_RATIO
            + usage["output"] * per_out)


def read_usage(raw) -> dict:
    """The SDK's usage object as plain counts. `getattr` throughout because
    the mock client carries only input/output, and a real response omits the
    cache fields entirely when nothing was cached."""
    return {
        "input": getattr(raw, "input_tokens", 0) or 0,
        "output": getattr(raw, "output_tokens", 0) or 0,
        "cache_write": getattr(raw, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(raw, "cache_read_input_tokens", 0) or 0,
    }


def record(bucket: str, model: str, raw) -> None:
    import caps
    if raw is None:
        return
    usage = read_usage(raw)
    dollars = price(model, usage)
    target = SESSION[bucket]
    target["calls"] += 1
    for field, value in usage.items():
        target[field] += value
    target["dollars"] += dollars
    # The monthly ledger is fed from here rather than from the call sites:
    # every counted call is a spent call, and the two must never disagree
    # about what happened.
    caps.record_spend(dollars)


def totals() -> dict:
    """The shape the cost views read. `tokens` counts every token the call
    was billed for, cached input included, so the headline figure matches
    what the dollars were computed from."""
    out = {}
    for name, bucket in SESSION.items():
        out[name] = {**bucket,
                     "tokens": (bucket["input"] + bucket["output"]
                                + bucket["cache_write"] + bucket["cache_read"])}
    out["total"] = {
        "calls": sum(b["calls"] for b in SESSION.values()),
        "tokens": out["companion"]["tokens"] + out["processing"]["tokens"],
        "dollars": sum(b["dollars"] for b in SESSION.values()),
    }
    return out


# ---------------------------------------------------------------------------
# CLIENT WRAPPER
# ---------------------------------------------------------------------------
#
# A proxy rather than a subclass: the SDK's client is not built to be
# extended, and the app only ever touches `client.messages.create` and
# `client.messages.stream`. Anything else falls through untouched, so a call
# site reaching for a part of the SDK this does not know about still works --
# uncounted, which shows up in `calls`, rather than broken.


class _MeteredStream:
    """`messages.stream(...)` returns a context manager whose usage is only
    final once `get_final_message()` has been called. Both streaming call
    sites do call it, and `__exit__` asks as a fallback for one that does
    not -- otherwise a whole companion turn would go uncounted."""

    def __init__(self, inner, bucket: str, model: str, holds_lock: bool = False):
        self._inner = inner
        self._bucket, self._model = bucket, model
        self._entered = None
        self._recorded = False
        # Only `stream()` below passes True: it is the only caller that took
        # caps.acquire() before constructing this, and only that acquire has
        # a release to give back. Tests build this class directly to exercise
        # the context-manager/recording behavior in isolation, with no lock
        # held to mismanage.
        self._lock_released = not holds_lock

    def __enter__(self):
        self._entered = self._inner.__enter__()
        return self

    def _release_lock(self):
        # Exactly once: caps.acquire() happened once in stream(), and this is
        # its matching release, whichever exit path gets here first.
        if not self._lock_released:
            self._lock_released = True
            import caps
            caps.release()

    def __exit__(self, *exc):
        try:
            # Only chase a final message on a clean exit: mid-exception the
            # stream may be unusable, and a metering call that raised here
            # would replace the real error with a confusing one.
            if exc[0] is None:
                self._finish()
        finally:
            self._release_lock()
        return self._inner.__exit__(*exc)

    def _finish(self):
        if self._recorded:
            return
        try:
            final = self._entered.get_final_message()
        except Exception:
            return
        self._recorded = True
        record(self._bucket, self._model, getattr(final, "usage", None))

    def get_final_message(self):
        final = self._entered.get_final_message()
        if not self._recorded:
            self._recorded = True
            record(self._bucket, self._model, getattr(final, "usage", None))
        return final

    def __getattr__(self, name):
        target = self._entered if self._entered is not None else self._inner
        return getattr(target, name)


class _MeteredMessages:
    def __init__(self, inner, skip: frozenset):
        self._inner = inner
        self._skip = skip

    def create(self, **kwargs):
        import caps
        caps.acquire()
        try:
            caps.check()
            # The bucket is read before the call, while the caller's frame is
            # still on the stack -- after it returns, it still is, but reading
            # it first keeps the two paths (create and stream) identical.
            bucket = _bucket(self._skip)
            response = self._inner.create(**kwargs)
            record(bucket, kwargs.get("model", ""), getattr(response, "usage", None))
            return response
        finally:
            caps.release()

    def stream(self, **kwargs):
        import caps
        caps.acquire()
        try:
            caps.check()
        except BaseException:
            caps.release()
            raise
        # The lock passes to the returned _MeteredStream, which releases it
        # when the `with` block it is used in exits -- not here, since the
        # real cost of a stream is only known once it has been read.
        return _MeteredStream(self._inner.stream(**kwargs),
                              _bucket(self._skip), kwargs.get("model", ""),
                              holds_lock=True)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class MeteredClient:
    def __init__(self, inner):
        self._inner = inner
        # This module's own frames must not be mistaken for the caller's.
        self.messages = _MeteredMessages(inner.messages, frozenset({"metering"}))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def wrap(client):
    return MeteredClient(client)
