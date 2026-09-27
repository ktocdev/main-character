# SPDX-License-Identifier: AGPL-3.0-or-later
"""
When a question is about: the dates it names, as ranges.

"What did I write in March?" is a question whose best clue is the month,
and neither search by meaning nor keyword search can use it: a passage
doesn't say the month it was written in, its metadata does. So the
question's dates are read here, and passages.fused() searches the entries
in those ranges as well as everywhere (LOOKUP-UPGRADE-HANDOFF.md, step 4).

Understood:

    2026-04-12, April 12, 12 April, April 12th 2026   one day
    2026-04, March, March 2026, in May                  a month
    in 2025                                             a year
    Christmas, New Year's, Thanksgiving, Halloween,     a window around the
      Valentine's, Easter, Labor Day, Memorial Day,     day, since "around
      the Fourth of July                                Christmas" rarely means
                                                        the 25th alone
    summer, winter, in the spring, in the fall          a season

A month, holiday or season without a year means every one of them the
journal has. "last" and "this" pick one: "last summer" is the most recent
summer that has ended, "this March" the current year's.

"may", "march" and "august" are also ordinary words, and "fall" and
"spring" too, so those count only with a clue they are dates: a day or
year after them, or a word like "in", "last" or "early" before.

Relative phrases ("last week", "three days ago") are not read yet.

    ranges(text, years, today) -> [(first day, last day)]
    dates_within(text, dates)  -> the journal dates the question names
"""

import re
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
SHORT = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
         "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
# Months that are also everyday words, and so need a clue to count.
AMBIGUOUS = {"may", "march", "august"}
# Words before a month, season or year that mark it as a time, allowing a
# "the" between ("in the fall"). Not "to": "going to march".
CUES = ("in", "during", "early", "late", "mid", "since", "until", "till",
        "through", "around", "by", "of", "last", "this", "next", "over",
        "before", "after", "from", "between")

_MONTH = r"(?P<month>" + "|".join(list(MONTHS) + list(SHORT)) + r")\.?"
_DAY = r"(?P<day>[0-3]?\d)(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>(?:19|20)\d\d)"


def _month_end(year: int, month: int) -> date:
    return (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth `weekday` (Monday 0) of a month; n = -1 for the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = _month_end(year, month)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Western Easter (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _around(day: date, before: int, after: int) -> tuple[date, date]:
    return day - timedelta(days=before), day + timedelta(days=after)


# Each holiday, as the window it names in a given year.
HOLIDAYS = [
    (r"christmas|xmas", lambda y: (date(y, 12, 18), date(y, 12, 31))),
    (r"new year'?s?(?: eve| day)?", lambda y: (date(y - 1, 12, 29), date(y, 1, 3))),
    (r"thanksgiving", lambda y: _around(_nth_weekday(y, 11, 3, 4), 3, 4)),
    (r"halloween", lambda y: (date(y, 10, 25), date(y, 11, 1))),
    (r"valentine'?s?(?: day)?", lambda y: _around(date(y, 2, 14), 3, 2)),
    (r"easter", lambda y: _around(_easter(y), 3, 2)),
    (r"labou?r day", lambda y: _around(_nth_weekday(y, 9, 0, 1), 3, 1)),
    (r"memorial day", lambda y: _around(_nth_weekday(y, 5, 0, -1), 3, 1)),
    (r"(?:the )?fourth of july|independence day", lambda y: _around(date(y, 7, 4), 3, 3)),
]
SEASONS = {
    "spring": lambda y: (date(y, 3, 1), date(y, 5, 31)),
    "summer": lambda y: (date(y, 6, 1), date(y, 8, 31)),
    "fall": lambda y: (date(y, 9, 1), date(y, 11, 30)),
    "autumn": lambda y: (date(y, 9, 1), date(y, 11, 30)),
    "winter": lambda y: (date(y - 1, 12, 1), _month_end(y, 2)),
}
# Seasons that are also everyday words.
AMBIGUOUS_SEASONS = {"spring", "fall"}


def _cued(text: str, start: int) -> str | None:
    """The cue word just before position `start`, if there is one."""
    m = re.search(r"(\w+)\s+(?:the\s+)?$", text[:start])
    if m and m.group(1) in CUES:
        return m.group(1)
    return None


def _pick(windows: list[tuple[date, date]], which: str | None,
          today: date) -> list[tuple[date, date]]:
    """All the windows, or with "last" the latest that has ended, with
    "this" the one around today or else the latest begun."""
    if which == "last":
        done = [w for w in windows if w[1] < today]
        return done[-1:] if done else []
    if which == "this":
        now = [w for w in windows if w[0] <= today <= w[1]]
        begun = [w for w in windows if w[0] <= today]
        return now or begun[-1:]
    return windows


def ranges(text: str, years: set[int], today: date) -> list[tuple[date, date]]:
    """The date ranges `text` names, [(first day, last day)], each
    inclusive. `years` are the years to read a date without one in: the
    journal's years."""
    low = text.lower().replace("’", "'")
    years = sorted(years | {today.year})
    found: list[tuple[date, date]] = []
    taken: list[tuple[int, int]] = []       # spans of `low` already read

    def free(m) -> bool:
        return not any(m.start() < e and s < m.end() for s, e in taken)

    def add(m, windows):
        taken.append((m.start(), m.end()))
        found.extend(windows)

    def safe(make, year):
        try:
            return make(year)
        except ValueError:
            return None

    # 2026-04-12, then 2026-04
    for m in re.finditer(r"\b((?:19|20)\d\d)-(\d\d)(?:-(\d\d))?\b", low):
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        if not 1 <= mo <= 12:
            continue
        day = safe(lambda _: date(y, mo, int(d)), 0) if d else None
        if d and day:
            add(m, [(day, day)])
        elif not d:
            add(m, [(date(y, mo, 1), _month_end(y, mo))])

    # April 12, April 12th 2026, 12 April, the 12th of April 2026
    for pattern in (rf"\b{_MONTH}\s+{_DAY}\b(?:,?\s+{_YEAR})?",
                    rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}(?:,?\s+{_YEAR})?\b"):
        for m in re.finditer(pattern, low):
            if not free(m):
                continue
            month = MONTHS.get(m.group("month")) or SHORT[m.group("month")]
            day_n = int(m.group("day"))
            chosen = [int(m.group("year"))] if m.group("year") else years
            days = [d for d in (safe(lambda y: date(y, month, day_n), y) for y in chosen) if d]
            if days:
                add(m, [(d, d) for d in days])

    # March, March 2026, in May
    for m in re.finditer(rf"\b(?P<month>{'|'.join(MONTHS)})\b(?:,?\s+{_YEAR})?", low):
        if not free(m):
            continue
        cue = _cued(low, m.start())
        if m.group("month") in AMBIGUOUS and not (cue or m.group("year")):
            continue
        month = MONTHS[m.group("month")]
        if m.group("year"):
            y = int(m.group("year"))
            add(m, [(date(y, month, 1), _month_end(y, month))])
            continue
        windows = [(date(y, month, 1), _month_end(y, month)) for y in years]
        add(m, _pick(windows, cue if cue in ("last", "this") else None, today))

    # Holidays and seasons, in every year the journal has (and the next,
    # for a window that begins in the year before).
    spans = years + [years[-1] + 1]
    for pattern, make in HOLIDAYS:
        for m in re.finditer(rf"\b(?:{pattern})\b", low):
            if free(m):
                cue = _cued(low, m.start())
                windows = [w for w in (safe(make, y) for y in spans) if w]
                add(m, _pick(windows, cue if cue in ("last", "this") else None, today))
    for name, make in SEASONS.items():
        for m in re.finditer(rf"\b{name}\b(?:\s+{_YEAR})?", low):
            if not free(m):
                continue
            cue = _cued(low, m.start())
            if name in AMBIGUOUS_SEASONS and not (cue or m.group("year")):
                continue
            if m.group("year"):
                add(m, [make(int(m.group("year")) + (name == "winter"))])
                continue
            windows = [make(y) for y in spans]
            add(m, _pick(windows, cue if cue in ("last", "this") else None, today))

    # in 2025
    for m in re.finditer(rf"\b{_YEAR}\b", low):
        if free(m) and _cued(low, m.start()):
            y = int(m.group("year"))
            add(m, [(date(y, 1, 1), date(y, 12, 31))])

    return found


def dates_within(text: str, dates: set[str], today: date | None = None) -> list[str]:
    """The journal's dates (YYYY-MM-DD) inside any range `text` names,
    sorted; empty when it names none, or none of its ranges holds an entry."""
    if today is None:
        from config import now_local
        today = now_local().date()
    years = {int(d[:4]) for d in dates if d[:4].isdigit()}
    found = ranges(text, years, today)
    if not found:
        return []
    return sorted(d for d in dates
                  if any(a.isoformat() <= d <= b.isoformat() for a, b in found))
