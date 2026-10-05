# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dates a question names (timeframe.py, the handoff's step 4).

  * a day, a month, a year, a holiday and a season each read as the range
    they name, in every year the journal has unless a year is given;
  * "last" and "this" pick one of them;
  * "may", "march", "fall" and the like count only with a clue they are
    dates, so ordinary sentences name no time;
  * only the journal's own dates come back.

Searching the time a question names is in test_passages.py.
"""

from datetime import date

import pytest

import timeframe

TODAY = date(2026, 9, 26)
YEARS = {2025, 2026}


def spans(text):
    return [(a.isoformat(), b.isoformat()) for a, b in timeframe.ranges(text, YEARS, TODAY)]


@pytest.mark.parametrize("text, want", [
    ("What did I write on 2026-04-12?", [("2026-04-12", "2026-04-12")]),
    ("the 3rd of June 2026", [("2026-06-03", "2026-06-03")]),
    ("April 12th", [("2025-04-12", "2025-04-12"), ("2026-04-12", "2026-04-12")]),
    ("Sept 3", [("2025-09-03", "2025-09-03"), ("2026-09-03", "2026-09-03")]),
    ("the 2026-03 entries", [("2026-03-01", "2026-03-31")]),
    ("in March", [("2025-03-01", "2025-03-31"), ("2026-03-01", "2026-03-31")]),
    ("February 2026", [("2026-02-01", "2026-02-28")]),
    ("in 2025", [("2025-01-01", "2025-12-31")]),
    ("last summer", [("2026-06-01", "2026-08-31")]),
    ("this March", [("2026-03-01", "2026-03-31")]),
    ("last Christmas", [("2025-12-18", "2025-12-31")]),
])
def test_a_time_reads_as_the_range_it_names(text, want):
    assert spans(text) == want


def test_holidays_move_with_the_calendar():
    easter = [a for a, _ in spans("Easter")]
    assert "2026-04-02" in easter                     # Easter Sunday 2026: April 5
    thanksgiving = [a for a, _ in spans("Thanksgiving")]
    assert "2025-11-24" in thanksgiving               # 4th Thursday: November 27


@pytest.mark.parametrize("text", [
    "I may go to the march", "going to march in the parade",
    "I had a fall on the stairs", "spring cleaning", "august company",
    "what about dec", "we talked for 20 minutes",
])
def test_everyday_words_name_no_time(text):
    assert spans(text) == []


@pytest.mark.parametrize("text", ["in May", "early May", "in the fall", "May 5"])
def test_a_clue_makes_them_a_time(text):
    assert spans(text)


def test_only_the_journals_dates_come_back():
    dates = {"2026-03-02", "2026-03-30", "2026-04-01", "2025-12-24"}
    assert timeframe.dates_within("in March", dates, TODAY) == ["2026-03-02", "2026-03-30"]
    assert timeframe.dates_within("around Christmas", dates, TODAY) == ["2025-12-24"]
    assert timeframe.dates_within("in August", dates, TODAY) == []
    assert timeframe.dates_within("the garden", dates, TODAY) == []
