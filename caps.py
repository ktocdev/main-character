"""
What this journal refuses to spend.

`metering.py` answers "what has this cost?", and can only answer it after the
fact -- a usage block arrives with the response. This answers "is the next
call allowed?", which has to be decided before the call. That is the whole
reason it is a separate module rather than another field on the accumulator.

Two ceilings:

  - **`MC_MAX_SESSION_SPEND`** -- dollars since this process started. Reads
    `metering.SESSION`, so it costs nothing to maintain and resets when the
    accumulator does: on a restart, and when a chat is closed.
  - **`MC_MAX_MONTHLY_SPEND`** -- dollars this calendar month, which needs a
    store the session accumulator cannot provide: it is process-global and
    a restart zeroes it. `SPEND_FILE` is a small JSON ledger keyed by
    `YYYY-MM` in the configured timezone.

**Neither is a budget.** They are runaway detectors. A loop that calls the
API a thousand times, a paste of a novel into the composer, a background
pipeline that fans out further than expected -- those are what these numbers
are sized to catch, which is why the defaults sit above ordinary use rather
than near it. A cap tuned to normal use would fire on a good week of writing,
and a journal that refuses to answer is a worse failure than a surprising
bill.

Three things to know before trusting them:

  - **The overshoot is one call, not zero.** Nothing here can predict what a
    call will cost, so the comparison is against what is already spent. The
    call that crosses the line completes; the next one is refused.
  - **The dollar figure is `MODEL_PRICES`, not an invoice.** Same estimate
    the cost meter shows, with the same staleness. The backstop that does not
    depend on this code being right is a spend limit on the Anthropic Console
    account, and the README says so.
  - **Mock mode never writes the ledger.** Its usage numbers are fictional,
    and a fictional dollar in a file that claims to record real money is
    worse than no file. The session cap still applies there -- it reads an
    in-process counter that makes no claim about a real account.
"""

import json
import os
import threading

import config

_LOCK = threading.Lock()

# The single call-gate. `check()` alone only reads a snapshot -- two calls
# racing between their own check and their own metering.record() can both
# read `used < limit` and both proceed, so together they cross a line only
# one of them was actually clear to cross. Holding this from `check()`
# through the matching record (or release() on failure) makes the calls this
# module guards line up one at a time, which is what "the call that crosses
# the line completes; the next one is refused" already assumed.
_CALL_LOCK = threading.Lock()


def acquire() -> None:
    _CALL_LOCK.acquire()


def release() -> None:
    _CALL_LOCK.release()


class CapExceeded(RuntimeError):
    """A call was refused because a ceiling was already reached.

    Carries `detail` for the routes to hand back verbatim: the author needs
    to know *which* cap stopped them and where to change it, or the only
    available reading is that the journal is broken.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ---------------------------------------------------------------------------
# THE MONTHLY LEDGER
# ---------------------------------------------------------------------------


def month_key(when=None) -> str:
    """`YYYY-MM` in the configured zone -- the same wall clock entries are
    stamped in, so "this month" means the month the author is living in
    rather than UTC's."""
    return (when or config.now_local()).strftime("%Y-%m")


def read_ledger() -> dict:
    """Every month on record. A corrupt or missing file reads as empty: this
    is a spend estimate, and refusing to start the journal over an unparsable
    JSON file would be a far larger failure than a forgotten month."""
    try:
        with open(config.SPEND_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def spent_this_month() -> float:
    return float(read_ledger().get(month_key(), 0.0))


def record_spend(dollars: float) -> None:
    """Add one call's estimated cost to this month's total.

    Read-modify-write under a lock, and written through a temporary file:
    background tasks run in threads, and a half-written ledger reads as zero
    on the next start -- which would silently lift the cap it exists to
    enforce. The month key is computed per call, so a session running past
    midnight on the 1st starts filling the new month by itself; nothing has
    to notice the rollover.
    """
    if not dollars or config.MOCK_MODE:
        return
    with _LOCK:
        ledger = read_ledger()
        key = month_key()
        ledger[key] = round(float(ledger.get(key, 0.0)) + dollars, 6)
        path = config.SPEND_FILE
        tmp = str(path) + ".tmp"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except OSError:
            # An unwritable ledger must not take down the call it was
            # counting. The cap it feeds goes stale; that is the smaller
            # failure, and `status()` is what surfaces it.
            pass


# ---------------------------------------------------------------------------
# THE CHECK
# ---------------------------------------------------------------------------


def session_spend() -> float:
    import metering
    return metering.totals()["total"]["dollars"]


def check() -> None:
    """Raise CapExceeded if a ceiling has already been reached.

    Called from the client proxy in `metering.py`, so every call site is
    covered by construction, and again at the top of the streaming routes so
    the author gets a clean refusal instead of an empty reply: a
    StreamingResponse has already sent its status line by the time its
    generator runs, so an exception in there cannot become a 429.
    """
    limit = config.MAX_SESSION_SPEND
    if limit:
        used = session_spend()
        if used >= limit:
            raise CapExceeded(
                f"this session's estimated spend is ${used:.2f}, which is at "
                f"or over the ${limit:.2f} cap in Settings. Closing the chat "
                f"starts the count over, or raise MC_MAX_SESSION_SPEND.")

    limit = config.MAX_MONTHLY_SPEND
    if limit:
        used = spent_this_month()
        if used >= limit:
            raise CapExceeded(
                f"this month's estimated spend is ${used:.2f}, which is at or "
                f"over the ${limit:.2f} cap in Settings. Raise "
                f"MC_MAX_MONTHLY_SPEND, or wait for the month to roll over.")


def status() -> dict:
    """What the settings pane reports: both ceilings, what has been used
    against them, and whether anything is enforcing them at all."""
    return {
        "enforced": True,
        # `limit` is what is in force; `default` is what blank would mean.
        # The pane needs both: without the second it can only say "uses the
        # default" and leave the reader to guess what that is.
        "session": {"limit": config.MAX_SESSION_SPEND,
                    "default": config.DEFAULT_SESSION_SPEND,
                    "used": session_spend()},
        "monthly": {"limit": config.MAX_MONTHLY_SPEND,
                    "default": config.DEFAULT_MONTHLY_SPEND,
                    "used": spent_this_month(),
                    "month": month_key(),
                    # Mock mode's dollars are fictional and never reach the
                    # ledger, so the monthly figure is honestly stuck at
                    # whatever real use last wrote. Say so rather than let it
                    # read as a month of no spending.
                    "recording": not config.MOCK_MODE},
    }
