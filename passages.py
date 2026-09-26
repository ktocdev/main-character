# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The search-only passage index: every part of every entry, findable by meaning.

The journal collection stores entries in chunks of up to ~6,000 characters,
but the embedder (all-MiniLM-L6-v2) reads only the first 256 tokens of what
it is given -- about 1,000 characters -- and drops the rest without a word.
A detail on page two of an entry is stored, but search by meaning can never
reach it. See LOOKUP-UPGRADE-HANDOFF.md.

This module keeps a second collection, `journal_passages`, of pieces small
enough that the embedder reads every token of each one. It is derived from
the journal collection and used **only for search**: nothing rebuilds entries
from it, which is what makes overlap between passages safe. The journal
collection stays exactly as it was -- the pipeline, entry counts and exact
search all read that.

    split(text)             -> spans of one text, each within the window
    index_chunks(...)       -> passages for journal chunks just written
    sync()                  -> reconcile the whole index with the journal
    search(query, n)        -> the best passages, shaped like query_journal's

Sizes are counted with the embedder's own tokenizer, so a passage's count is
exactly what the embedder sees. Embeddings are computed here with one
long-lived embedder rather than by Chroma: Chroma's default reloads the model
on every query (~190 ms), which a long query split into 26 searches cannot
afford. Both are the same model, all-MiniLM-L6-v2, so the vectors agree.
"""

import math
import re
from functools import lru_cache

from config import PASSAGE_NEIGHBORS, PASSAGE_TOKENS

COLLECTION_NAME = "journal_passages"
LIMIT = 254            # the embedder's 256, less the [CLS] and [SEP] it adds
OVERLAP = 30           # tokens repeated from the end of the previous passage
MAX_QUERY_PIECES = 26  # searches per query; see the handoff for the sizing
_BATCH = 64            # passages embedded per upsert


# ---------------------------------------------------------------------------
# THE EMBEDDER AND ITS TOKENIZER
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _embedder():
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    ef = ONNXMiniLM_L6_V2()
    ef._download_model_if_not_exists()
    return ef


@lru_cache(maxsize=1)
def _tokenizer():
    """The embedder's tokenizer without its truncation and padding, so a
    count is the true length rather than a capped one."""
    import os
    from tokenizers import Tokenizer
    ef = _embedder()
    tok = Tokenizer.from_file(os.path.join(
        ef.DOWNLOAD_PATH, ef.EXTRACTED_FOLDER_NAME, "tokenizer.json"))
    tok.no_truncation()
    tok.no_padding()
    return tok


def embed(texts: list[str]) -> list:
    return list(_embedder()(texts)) if texts else []


def count_tokens(text: str) -> int:
    """Tokens the embedder reads from `text`, not counting its two markers."""
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


# ---------------------------------------------------------------------------
# SPLITTING
# ---------------------------------------------------------------------------
# Every cut falls between tokens, and it prefers, in order: a paragraph
# break, a line break, the end of a sentence, a space. The number of pieces
# is fixed first (the fewest that fit the target) and the cuts are placed
# near even intervals, so one entry's passages come out about the same size
# rather than full pieces and a scrap at the end.

_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")


def _cut_scores(text: str, offsets: list[tuple[int, int]]) -> list[int]:
    """score[i] = how good a cut is just before token i; -1 = not allowed
    (inside a word)."""
    scores = [-1] * len(offsets)
    for i in range(1, len(offsets)):
        gap = text[offsets[i - 1][1]:offsets[i][0]]
        if not gap:
            continue                      # same word, or punctuation stuck on
        if "\n\n" in gap or "\n\r\n" in gap:
            scores[i] = 4
        elif "\n" in gap:
            scores[i] = 3
        elif _SENTENCE_END.search(text[max(0, offsets[i - 1][1] - 4):offsets[i - 1][1]]):
            scores[i] = 2
        else:
            scores[i] = 1
    return scores


def _place_cuts(n: int, k: int, budget: int, scores: list[int]) -> list[int] | None:
    """k-1 cut positions splitting n tokens into k pieces of at most
    `budget` each, near even intervals, at the best-scoring breaks."""
    cuts, start = [], 0
    for j in range(1, k):
        size = (n - start) / (k - j + 1)          # even share of what is left
        ideal = start + size
        lo = max(start + 1, n - budget * (k - j))  # leave room for the rest
        hi = min(start + budget, n - 1)
        if lo > hi:
            return None
        slack = max(1, int(size * 0.25))
        best = None
        for c in range(lo, hi + 1):
            if scores[c] < 0:
                continue
            key = (abs(c - ideal) <= slack, scores[c], -abs(c - ideal))
            if best is None or key > best[0]:
                best = (key, c)
        if best is None:                            # one enormous word
            c = int(max(lo, min(hi, round(ideal))))
        else:
            c = best[1]
        cuts.append(c)
        start = c
    if n - start > budget:
        return None
    return cuts


def split(text: str, target: int | None = None) -> list[dict]:
    """Split `text` into passages of about `target` tokens, none over LIMIT.

    Returns [{"start", "end", "text"}], character spans into `text`. After
    the first, each passage also carries the last ~OVERLAP tokens of the one
    before it, counted within the limit. The new material of the passages
    covers the text end to end, so nothing falls between two of them.
    """
    target = min(LIMIT, max(OVERLAP * 2, target or PASSAGE_TOKENS))
    enc = _tokenizer().encode(text, add_special_tokens=False)
    offsets = enc.offsets
    n = len(offsets)
    if n <= target:
        if not text.strip():
            return []
        lead = len(text) - len(text.lstrip())
        return [{"start": lead, "end": len(text.rstrip()), "text": text.strip()}]

    scores = _cut_scores(text, offsets)
    k = math.ceil(n / (target - OVERLAP))
    while True:
        budget = min(LIMIT - OVERLAP, math.ceil(n / k) + OVERLAP)
        cuts = _place_cuts(n, k, budget, scores)
        if cuts is not None:
            pieces = _materialize(text, offsets, [0] + cuts + [n])
            if all(count_tokens(p["text"]) <= LIMIT for p in pieces):
                return pieces
        k += 1


def _materialize(text: str, offsets, bounds: list[int]) -> list[dict]:
    pieces = []
    for j in range(len(bounds) - 1):
        a, b = bounds[j], bounds[j + 1]
        # Content runs from the end of the previous piece's last token, so
        # the whitespace between pieces belongs somewhere.
        content_start = 0 if j == 0 else offsets[a - 1][1]
        end = len(text) if b == len(offsets) else offsets[b - 1][1]
        start = content_start
        if j > 0:
            o = max(bounds[j - 1], a - OVERLAP)
            while o < a and not text[offsets[o - 1][1]:offsets[o][0]]:
                o += 1                      # don't start mid-word
            if o < a:
                start = offsets[o][0]
        span = text[start:end]
        lead = len(span) - len(span.lstrip())
        pieces.append({"start": start + lead, "end": end,
                       "text": span.strip()})
    return pieces


# ---------------------------------------------------------------------------
# THE INDEX
# ---------------------------------------------------------------------------

def get_passage_collection():
    import chromadb
    from config import CHROMA_DIR
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def passages_for_chunk(chunk_id: str, text: str, meta: dict) -> list[tuple[str, str, dict]]:
    """(id, text, metadata) for each passage of one journal chunk."""
    pieces = split(text)
    out = []
    for i, p in enumerate(pieces):
        out.append((f"{chunk_id}_p{i}", p["text"], {
            "date": meta.get("date", ""),
            "title": meta.get("title", ""),
            "source_id": chunk_id,
            "position": i,
            "count": len(pieces),
            "start": p["start"],
            "end": p["end"],
        }))
    return out


def _upsert(col, rows: list[tuple[str, str, dict]]) -> None:
    for i in range(0, len(rows), _BATCH):
        batch = rows[i:i + _BATCH]
        col.upsert(ids=[r[0] for r in batch],
                   documents=[r[1] for r in batch],
                   metadatas=[r[2] for r in batch],
                   embeddings=embed([r[1] for r in batch]))


def index_chunks(ids: list[str], docs: list[str], metas: list[dict],
                 collection=None) -> int:
    """Passages for journal chunks that were just written. Replaces any a
    chunk had before. Returns the number of passages written."""
    col = collection or get_passage_collection()
    if not ids:
        return 0
    old = col.get(where={"source_id": {"$in": list(ids)}}, include=[])["ids"]
    if old:
        col.delete(ids=old)
    rows = [r for cid, doc, meta in zip(ids, docs, metas)
            for r in passages_for_chunk(cid, doc, meta)]
    _upsert(col, rows)
    return len(rows)


def remove_chunks(ids: list[str], collection=None) -> None:
    """Drop the passages of journal chunks that were deleted."""
    if not ids:
        return
    col = collection or get_passage_collection()
    old = col.get(where={"source_id": {"$in": list(ids)}}, include=[])["ids"]
    if old:
        col.delete(ids=old)


def sync(journal=None, dry_run: bool = False) -> dict:
    """Make the passage index match the journal collection. Only passages
    that are new or changed are embedded; the rest are left alone."""
    from rag_journal import get_collection
    journal = journal or get_collection()
    col = get_passage_collection()

    got = journal.get(include=["documents", "metadatas"])
    want = {pid: (doc, meta)
            for cid, text, m in zip(got["ids"], got["documents"], got["metadatas"])
            for pid, doc, meta in passages_for_chunk(cid, text, m)}

    have = col.get(include=["documents", "metadatas"])
    have = {pid: (doc, meta) for pid, doc, meta in
            zip(have["ids"], have["documents"], have["metadatas"])}

    stale = [pid for pid in have if pid not in want]
    fresh = [(pid, *want[pid]) for pid in want if have.get(pid) != want[pid]]
    if not dry_run:
        if stale:
            col.delete(ids=stale)
        _upsert(col, fresh)
    return {"documents": len(want), "removed": len(stale), "embedded": len(fresh)}


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

def query_pieces(query: str) -> list[str]:
    """A long query is searched piece by piece, the whole of it: an entry's
    ending matters as much as its opening. Past MAX_QUERY_PIECES, pieces are
    taken evenly across it so the ending is still searched."""
    pieces = [p["text"] for p in split(query)] or [query]
    if len(pieces) > MAX_QUERY_PIECES:
        step = (len(pieces) - 1) / (MAX_QUERY_PIECES - 1)
        pieces = [pieces[round(i * step)] for i in range(MAX_QUERY_PIECES)]
    return pieces


def search(query: str, n: int, where: dict | None = None,
           neighbors: int | None = None, collection=None) -> list[dict]:
    """The n best passages for `query`, as [{"text", "metadata", "distance"}],
    best first -- the shape `rag_journal.query_journal` returns.

    Each piece of the query is searched, and the pieces take turns filling
    the n slots (see below). A short query is one piece, so this is plain
    nearest-first. With `neighbors`, each hit is shown with that
    many passages on either side from the same chunk, and hits whose
    windows touch are merged, so no text is shown twice.
    """
    col = collection or get_passage_collection()
    total = col.count()
    if total == 0:
        return []
    neighbors = PASSAGE_NEIGHBORS if neighbors is None else neighbors
    pieces = query_pieces(query)
    kwargs = {"where": where} if where else {}
    res = col.query(query_embeddings=embed(pieces), n_results=min(n, total),
                    include=["documents", "metadatas", "distances"], **kwargs)

    # Pieces take turns: every piece's best match comes before any piece's
    # second best. Merging on distance alone lets whichever part of a long
    # entry the journal has the most to say about crowd out the rest -- a
    # long opening about work fills every slot, and the connection in the
    # last paragraph never gets one. Within a turn, closer comes first.
    per_piece = [list(zip(ids, docs, metas, dists)) for ids, docs, metas, dists
                 in zip(res["ids"], res["documents"], res["metadatas"], res["distances"])]
    ranked, seen = [], set()
    for turn in range(max(len(p) for p in per_piece)):
        row = sorted((p[turn] for p in per_piece if turn < len(p)), key=lambda h: h[3])
        for pid, doc, meta, dist in row:
            if pid not in seen:
                seen.add(pid)
                ranked.append((dist, doc, meta))
        if len(ranked) >= n:
            break
    ranked = ranked[:n]

    if neighbors <= 0:
        return [{"text": doc, "metadata": meta, "distance": dist}
                for dist, doc, meta in ranked]
    return _with_neighbors(ranked, neighbors)


def _with_neighbors(ranked, neighbors: int) -> list[dict]:
    """Widen each hit to its neighbours' span of the source chunk, merging
    hits from the same chunk whose windows touch. The window text is cut
    from the journal chunk itself, so overlap is never shown twice."""
    from rag_journal import get_collection

    windows: list[dict] = []
    for dist, _doc, meta in ranked:
        src, pos = meta["source_id"], meta["position"]
        lo, hi = max(0, pos - neighbors), min(meta["count"] - 1, pos + neighbors)
        for w in windows:
            if w["source_id"] == src and lo <= w["hi"] + 1 and hi >= w["lo"] - 1:
                w["lo"], w["hi"] = min(lo, w["lo"]), max(hi, w["hi"])
                break
        else:
            windows.append({"source_id": src, "lo": lo, "hi": hi,
                            "distance": dist, "metadata": meta})

    col = get_passage_collection()
    want = [f"{w['source_id']}_p{i}" for w in windows for i in (w["lo"], w["hi"])]
    spans = dict(zip(*[col.get(ids=list(set(want)), include=["metadatas"])[k]
                       for k in ("ids", "metadatas")]))
    sources = get_collection().get(ids=list({w["source_id"] for w in windows}),
                                   include=["documents"])
    source_text = dict(zip(sources["ids"], sources["documents"]))

    out = []
    for w in windows:
        first = spans.get(f"{w['source_id']}_p{w['lo']}")
        last = spans.get(f"{w['source_id']}_p{w['hi']}")
        text = source_text.get(w["source_id"])
        if not (first and last and text):
            continue
        out.append({"text": text[first["start"]:last["end"]].strip(),
                    "metadata": w["metadata"], "distance": w["distance"]})
    return out
