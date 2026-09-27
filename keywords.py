# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Keyword search over the passage index: the words themselves, not their meaning.

Search by meaning is weak on names, numbers and rare words. "When did Robin
and I watch Quillon?" is a question about a film title the embedder has no
sense of, and the passage that names it can rank below a dozen passages about
Robin. Keyword search finds it by the word. passages.search() runs both and
merges them (LOOKUP-UPGRADE-HANDOFF.md, step 3).

Passages are ranked with BM25, the standard scoring behind most search
engines: a passage scores for each of the question's words it holds, a rare
word counts for far more than a common one, and a long passage is not
favoured for holding more words.

Words are normalized the same way on both sides: lowercased, accents folded,
a possessive 's dropped, and plurals and simple verb endings cut, so
"Robin's" matches "robin" and "lanterns" matches "lantern". The cutting is
crude -- "moving" and "moved" both become "mov" -- which is fine, since only
whether two words land on the same form matters, never the form itself.

A question word the journal never uses is matched to words within a small
edit distance, at a lower weight: the journal has typos and variant
spellings ("rhinocerous", "definately") that a question won't repeat.

The index is built in memory from the passage collection on first use, about
a second for the whole journal, and rebuilt when the collection changes.
Nothing is stored.

    index_for(col)                -> the keyword index of a passage collection
    Index.top(text, n, where)     -> [(passage id, score)], best first
    terms(text)                   -> the normalized words BM25 compares
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict

K1 = 1.2          # BM25: how fast repeats of a word stop adding
B = 0.75          # BM25: how much a long passage is marked down
# A word in more than this share of passages says nothing about which one
# is meant, and scoring it costs the most time: its list is the longest.
# A small journal skips none: there it costs nothing, and a share of a few
# passages would drop words that do tell them apart.
MAX_DF_SHARE = 0.2
MIN_DF_CUT = 100
# Spelling: words this long or longer, within this many edits (a letter
# added, dropped, changed, or two swapped), at this share of the weight.
FUZZY = True
FUZZY_MIN = 5
FUZZY_WEIGHT = 0.5
# A passage scoring under this share of the best one matched only the
# question's common words, and is left off the list. Otherwise, when a word
# is in most passages, each of them gets a place, and in the merge
# (passages.fused) a place counts the same whatever the score behind it:
# a dozen passages that mention only "Robin" would outvote the one that
# says "melatonin".
FLOOR = 0.25


def _edits_allowed(word: str) -> int:
    return 1 if len(word) < 8 else 2


STOPWORDS = frozenset("""
a about above after again against all almost also am an and any are around as
at back be because been before being below between both but by can could did
do does doing done down during each even ever every few for from further get
gets getting got had has have having he her here hers herself him himself his
how i if in into is it its itself just like made make many me might more most
much must my myself no nor not now of off on once only or other our ours
ourselves out over own really same she should so some still such than that the
their theirs them themselves then there these they this those through to too
under until up very was we were what when where which while who whom why will
with would you your yours yourself yourselves
im ive id ill dont didnt doesnt isnt wasnt cant couldnt wouldnt shouldnt wont
thats theres hes shes theyre youre weve were lets
""".split())


def fold(s: str) -> str:
    """Lowercase and strip accents, one char at a time so indexes still
    line up with the original text (fiancé matches fiance)."""
    out = []
    for c in s.lower():
        d = unicodedata.normalize("NFKD", c)
        out.append(d[0] if d else c)
    return "".join(out)


_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


def stem(word: str) -> str:
    """Plural and simple verb endings off, so a word's forms meet."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[:-len(suffix)]
            if word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]      # running -> run
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]              # move, moved, moving -> mov
    return word


def terms(text: str) -> list[str]:
    """The normalized words of `text`, stopwords out."""
    out = []
    for word in _WORD.findall(fold(text.replace("’", "'"))):
        if word.endswith("'s"):
            word = word[:-2]
        word = word.replace("'", "")
        if word and word not in STOPWORDS and len(word) > 1:
            out.append(stem(word))
    return out


def _close(a: str, b: str, limit: int) -> bool:
    """Within `limit` edits, counting a swap of neighbours as one."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost)
            if (prev2 is not None and i > 1 and j > 1
                    and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                row[j] = min(row[j], prev2[j - 2] + 1)
        if min(row) > limit:
            return False
        prev2, prev = prev, row
    return prev[-1] <= limit


def _trigrams(word: str) -> set[str]:
    padded = f"^{word}$"
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def matches(meta: dict, where: dict | None) -> bool:
    """Whether `meta` passes a Chroma `where` filter -- the operators the
    app uses: $and, $or, $eq, $ne, $in, $nin, and a bare value for $eq."""
    if not where:
        return True
    for key, cond in where.items():
        if key == "$and":
            if not all(matches(meta, c) for c in cond):
                return False
        elif key == "$or":
            if not any(matches(meta, c) for c in cond):
                return False
        else:
            value = meta.get(key)
            if not isinstance(cond, dict):
                cond = {"$eq": cond}
            for op, want in cond.items():
                ok = {"$eq": lambda: value == want,
                      "$ne": lambda: value != want,
                      "$in": lambda: value in want,
                      "$nin": lambda: value not in want}.get(op)
                if ok is None:
                    raise ValueError(f"keyword search can't filter on {op}")
                if not ok():
                    return False
    return True


class Index:
    """BM25 over a fixed set of passages."""

    def __init__(self, ids: list[str], docs: list[str], metas: list[dict]):
        self.ids, self.docs, self.metas = ids, docs, metas
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths = []
        for i, doc in enumerate(docs):
            words = terms(doc)
            self.lengths.append(len(words))
            for word, tf in Counter(words).items():
                self.postings[word].append((i, tf))
        n = len(docs)
        self.avg_length = (sum(self.lengths) / n) if n else 0.0
        self.idf = {w: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5))
                    for w, p in self.postings.items()}
        self.max_df = max(MIN_DF_CUT, int(n * MAX_DF_SHARE))
        # The days the passages are from, for a question that names a time.
        self.dates = {m.get("date") for m in metas if m.get("date")}
        self._by_trigram: dict[str, set[str]] | None = None
        self._variants: dict[str, list[str]] = {}

    def variants(self, word: str) -> list[str]:
        """The journal's words within a few edits of `word`, which it never
        uses itself -- a question's spelling against the journal's."""
        if word in self._variants:
            return self._variants[word]
        found = []
        if len(word) >= FUZZY_MIN and not word.isdigit():
            if self._by_trigram is None:
                self._by_trigram = defaultdict(set)
                for w in self.postings:
                    if len(w) >= FUZZY_MIN - 1:
                        for g in _trigrams(w):
                            self._by_trigram[g].add(w)
            limit = _edits_allowed(word)
            shared = Counter(w for g in _trigrams(word)
                             for w in self._by_trigram.get(g, ()))
            # Each edit breaks at most three trigrams.
            need = len(word) - 3 * limit
            found = [w for w, k in shared.items()
                     if k >= need and _close(word, w, limit)]
        self._variants[word] = found
        return found

    def weighted(self, text: str) -> dict[str, float]:
        """The words of `text` to score, each with its weight: 1 for a word
        the journal uses, FUZZY_WEIGHT for a variant of one it doesn't."""
        out: dict[str, float] = {}
        for word in set(terms(text)):
            if word in self.postings:
                out[word] = 1.0
            elif FUZZY:
                for v in self.variants(word):
                    out[v] = max(out.get(v, 0.0), FUZZY_WEIGHT)
        return {w: x for w, x in out.items() if len(self.postings[w]) <= self.max_df}

    def top(self, text: str, n: int, where: dict | None = None) -> list[tuple[str, float]]:
        """The n best passages for `text`, as [(id, score)], best first,
        leaving out any under FLOOR of the best."""
        scores: dict[int, float] = defaultdict(float)
        for word, weight in self.weighted(text).items():
            idf = self.idf[word] * weight
            for i, tf in self.postings[word]:
                norm = K1 * (1 - B + B * self.lengths[i] / self.avg_length)
                scores[i] += idf * tf * (K1 + 1) / (tf + norm)
        best = sorted(scores.items(), key=lambda s: -s[1])
        out = []
        for i, score in best:
            if out and score < out[0][1] * FLOOR:
                break
            if where is None or matches(self.metas[i], where):
                out.append((self.ids[i], score))
                if len(out) >= n:
                    break
        return out


_cache: dict = {}


def invalidate() -> None:
    """The passage collection changed: build again on next use."""
    _cache.clear()


def index_for(col) -> Index | None:
    """The keyword index of passage collection `col`, built on first use and
    kept until invalidate() or the collection's size changes (a rebuild in
    another process). None if it can't be built."""
    try:
        key = (col.id, col.count())
        if _cache.get("key") != key:
            got = col.get(include=["documents", "metadatas"])
            _cache.clear()
            _cache["key"] = key
            _cache["index"] = Index(got["ids"], got["documents"], got["metadatas"])
        return _cache["index"]
    except Exception:
        return None
