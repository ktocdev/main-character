# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keyword search over the passage index (keywords.py, the handoff's step 3).

  * words are normalized the same way on both sides: a possessive, a plural
    and a verb ending don't stop two forms of a word meeting, and accents
    fold;
  * a rare word counts for more than a common one;
  * a question word the journal never uses matches the journal's spelling
    of it, if it is long enough, and a short one doesn't;
  * a `where` filter is applied as Chroma would apply it.

Searching the passage index with both, merged, is in test_passages.py.
"""

import pytest

import keywords


def test_forms_of_a_word_meet():
    assert keywords.terms("Robin's lanterns") == keywords.terms("robin lantern")
    assert keywords.terms("moved") == keywords.terms("moving") == keywords.terms("move")
    assert keywords.terms("Robin’s") == keywords.terms("Robin")   # curly apostrophe
    assert keywords.terms("fiancé") == keywords.terms("fiance")


def test_stopwords_are_left_out():
    assert keywords.terms("When did I watch it with them?") == keywords.terms("watch")


def index(*docs):
    return keywords.Index([f"p{i}" for i in range(len(docs))], list(docs),
                          [{"n": i} for i in range(len(docs))])


def test_a_rare_word_counts_for_more_than_a_common_one():
    idx = index("Dinner with Robin, then the film.", "Dinner with Robin again.",
                "Dinner alone.", "Robin fell asleep during Quillon.",
                "Robin called.", "Robin and I walked.")
    assert idx.top("dinner with Robin and Quillon", 1)[0][0] == "p3"


def test_a_misspelling_meets_the_journals_spelling(monkeypatch):
    idx = index("We saw a rhinocerous at the zoo.", "A quiet day.")
    assert idx.top("rhinoceros", 1)[0][0] == "p0"
    monkeypatch.setattr(keywords, "FUZZY", False)
    assert idx.top("rhinoceros", 1) == []


def test_a_short_word_is_not_matched_loosely():
    idx = index("The ruin by the river.")
    assert idx.top("rain", 1) == []


def test_a_filter_is_applied_as_chroma_would():
    idx = index("Quillon on Monday.", "Quillon on Tuesday.")
    where = {"$and": [{"n": {"$eq": 1}}, {"n": {"$in": [0, 1]}}]}
    assert [pid for pid, _ in idx.top("quillon", 5, where)] == ["p1"]
    with pytest.raises(ValueError):
        idx.top("quillon", 5, {"n": {"$gt": 0}})
