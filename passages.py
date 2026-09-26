# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The search-only passage index: every part of every entry, findable by meaning.

The journal collection stores entries in chunks of up to ~6,000 characters,
but an embedder reads only so many tokens of what it is given -- Chroma's
all-MiniLM-L6-v2, which embeds that collection, stops at 256, about 1,000
characters -- and drops the rest without a word. A detail on page two of an
entry is stored, but search by meaning can never reach it. See
LOOKUP-UPGRADE-HANDOFF.md.

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

The embedder is config.EMBED_MODEL, run locally by fastembed: a model trained
to match a question to the passage that answers it, which found half again
as many of the test set's quotes as MiniLM (the handoff's step 1a). Sizes are
counted with the model's own tokenizer, so a passage's count is exactly what
the model sees. One long-lived instance does every embedding: a long query
split into 26 searches cannot afford a model load per search.

The collection records which model embedded it. A collection embedded by
another model is never searched or added to -- its vectors mean nothing to
this one -- and sync() replaces it whole.
"""

import math
import re
import sys
import time
import warnings
from functools import lru_cache

from config import EMBED_MODEL, MODEL_CACHE, PASSAGE_NEIGHBORS, PASSAGE_TOKENS

COLLECTION_NAME = "journal_passages"
OVERLAP = 30           # tokens repeated from the end of the previous passage
MAX_QUERY_PIECES = 26  # searches per query; see the handoff for the sizing
_BATCH = 64            # passages per embedding call
_UPSERT = 1000         # passages per write; small writes degrade the index
# The nearest-neighbour index's settings, wider than chroma's defaults.
# At the defaults, searches of the real journal's 4,117 passages missed 46
# of the true top-12 lists' members across the test set's 46 queries -- one
# question's best passage among them. These miss 1, and a query still takes
# ~10 ms (the handoff's step 1a notes).
_HNSW = {"space": "cosine", "ef_construction": 400, "ef_search": 400,
         "max_neighbors": 48}
_DOWNLOAD_TRIES = 5

# The models this index can use, and how each marks a search query. Both were
# trained with this instruction on queries and nothing on passages; leaving
# it off, or putting it on passages, quietly costs accuracy.
_RETRIEVAL_PREFIX = "Represent this sentence for searching relevant passages: "
MODELS = {
    "snowflake/snowflake-arctic-embed-s": {"query_prefix": _RETRIEVAL_PREFIX},
    "BAAI/bge-small-en-v1.5": {"query_prefix": _RETRIEVAL_PREFIX},
}


class ModelUnavailable(RuntimeError):
    """The embedding model is not on this machine and could not be fetched."""


# ---------------------------------------------------------------------------
# THE EMBEDDER AND ITS TOKENIZER
# ---------------------------------------------------------------------------

def _model_files() -> tuple[str, list[str]]:
    """(Hugging Face repo, files to fetch) for EMBED_MODEL, from fastembed's
    own description of it."""
    from fastembed import TextEmbedding
    if EMBED_MODEL not in MODELS:
        raise ModelUnavailable(
            f"MC_EMBED_MODEL={EMBED_MODEL!r} is not one of: {', '.join(MODELS)}")
    desc = next(d for d in TextEmbedding._list_supported_models()
                if d.model == EMBED_MODEL)
    return desc.sources.hf, ["config.json", "tokenizer.json", "tokenizer_config.json",
                             "special_tokens_map.json", desc.model_file,
                             *desc.additional_files]


def _local_copy() -> str | None:
    from huggingface_hub import snapshot_download
    repo, files = _model_files()
    try:
        return snapshot_download(repo, allow_patterns=files,
                                 cache_dir=str(MODEL_CACHE), local_files_only=True)
    except Exception:
        return None


def model_cached() -> bool | None:
    """Whether the model is already downloaded, so using it fetches nothing.
    None when that can't be told."""
    try:
        return _local_copy() is not None
    except Exception:
        return None


def _download() -> str:
    """The model's folder, fetched first if need be.

    One file at a time, with retries: on some connections Hugging Face resets
    the parallel downloader every time, and a retried file resumes where it
    stopped. About 130 MB, once per machine.
    """
    from huggingface_hub import snapshot_download
    found = _local_copy()
    if found:
        return found
    repo, files = _model_files()
    print(f"Downloading the search model {EMBED_MODEL} (about 130 MB, once "
          f"per machine) to {MODEL_CACHE}", file=sys.stderr, flush=True)
    for attempt in range(1, _DOWNLOAD_TRIES + 1):
        try:
            with warnings.catch_warnings():
                # Windows without Developer Mode can't make the cache's
                # symlinks, so it keeps plain copies: fine for five files.
                warnings.filterwarnings("ignore", message=".*symlinks.*")
                return snapshot_download(repo, allow_patterns=files,
                                         cache_dir=str(MODEL_CACHE), max_workers=1)
        except Exception as exc:
            if attempt == _DOWNLOAD_TRIES:
                raise ModelUnavailable(
                    f"Could not download the search model {EMBED_MODEL} "
                    f"({exc.__class__.__name__}: {exc}). Search uses the older "
                    f"index until it can; check the connection and run "
                    f"rebuild_index.py.") from exc
            print(f"  download interrupted ({exc.__class__.__name__}), "
                  f"retrying ({attempt}/{_DOWNLOAD_TRIES - 1})",
                  file=sys.stderr, flush=True)
            time.sleep(2 * attempt)


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(EMBED_MODEL, cache_dir=str(MODEL_CACHE),
                         specific_model_path=_download())


@lru_cache(maxsize=1)
def _tokenizer():
    """The model's tokenizer without its truncation and padding, so a
    count is the true length rather than a capped one."""
    from tokenizers import Tokenizer
    tok = Tokenizer.from_str(_embedder().model.tokenizer.to_str())
    tok.no_truncation()
    tok.no_padding()
    return tok


@lru_cache(maxsize=1)
def limit() -> int:
    """The most tokens a passage or query piece can have and still be read
    whole: the model's window, less the two markers it adds and the prefix a
    query carries."""
    window = _embedder().model.tokenizer.truncation["max_length"]
    return window - 2 - count_tokens(MODELS[EMBED_MODEL]["query_prefix"])


def embed(texts: list[str], query: bool = False) -> list:
    """Vectors for passages, or with `query`, for search queries."""
    if not texts:
        return []
    if query:
        texts = [MODELS[EMBED_MODEL]["query_prefix"] + t for t in texts]
    return list(_embedder().embed(texts, batch_size=_BATCH))


def count_tokens(text: str) -> int:
    """Tokens the model reads from `text`, not counting its two markers."""
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
    """Split `text` into passages of about `target` tokens, none over limit().

    Returns [{"start", "end", "text"}], character spans into `text`. After
    the first, each passage also carries the last ~OVERLAP tokens of the one
    before it, counted within the limit. The new material of the passages
    covers the text end to end, so nothing falls between two of them.
    """
    cap = limit()
    target = min(cap, max(OVERLAP * 2, target or PASSAGE_TOKENS))
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
        budget = min(cap - OVERLAP, math.ceil(n / k) + OVERLAP)
        cuts = _place_cuts(n, k, budget, scores)
        if cuts is not None:
            pieces = _materialize(text, offsets, [0] + cuts + [n])
            if all(count_tokens(p["text"]) <= cap for p in pieces):
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

def get_passage_collection(replace_stale: bool = False):
    """The passage collection, or None if another model embedded it (or,
    before this was recorded, nothing says which did). With
    `replace_stale`, such a collection is deleted and an empty one made in
    its place -- sync() fills it again."""
    import chromadb
    from config import CHROMA_DIR
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    # No embedding function: every vector is supplied from embed(), and
    # chroma's default would be MiniLM, which must never touch this index.
    kwargs = {"metadata": {"embed_model": EMBED_MODEL},
              "configuration": {"hnsw": _HNSW}, "embedding_function": None}
    col = client.get_or_create_collection(name=COLLECTION_NAME, **kwargs)
    if (col.metadata or {}).get("embed_model") == EMBED_MODEL:
        return col
    if not replace_stale:
        return None
    client.delete_collection(COLLECTION_NAME)
    return client.create_collection(name=COLLECTION_NAME, **kwargs)


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
    for i in range(0, len(rows), _UPSERT):
        batch = rows[i:i + _UPSERT]
        col.upsert(ids=[r[0] for r in batch],
                   documents=[r[1] for r in batch],
                   metadatas=[r[2] for r in batch],
                   embeddings=embed([r[1] for r in batch]))


def index_chunks(ids: list[str], docs: list[str], metas: list[dict],
                 collection=None) -> int:
    """Passages for journal chunks that were just written. Replaces any a
    chunk had before. Returns the number of passages written."""
    col = collection or get_passage_collection()
    if not ids or col is None:
        return 0      # embedded by another model: sync() rebuilds it whole
    old = col.get(where={"source_id": {"$in": list(ids)}}, include=[])["ids"]
    if old:
        col.delete(ids=old)
    rows = [r for cid, doc, meta in zip(ids, docs, metas)
            for r in passages_for_chunk(cid, doc, meta)]
    _upsert(col, rows)
    return len(rows)


def remove_chunks(ids: list[str], collection=None) -> None:
    """Drop the passages of journal chunks that were deleted."""
    col = collection or get_passage_collection()
    if not ids or col is None:
        return
    old = col.get(where={"source_id": {"$in": list(ids)}}, include=[])["ids"]
    if old:
        col.delete(ids=old)


def sync(journal=None, dry_run: bool = False) -> dict:
    """Make the passage index match the journal collection. Only passages
    that are new or changed are embedded; the rest are left alone. An index
    embedded by another model is replaced, so everything is embedded."""
    from rag_journal import get_collection
    journal = journal or get_collection()
    col = get_passage_collection(replace_stale=not dry_run)

    got = journal.get(include=["documents", "metadatas"])
    want = {pid: (doc, meta)
            for cid, text, m in zip(got["ids"], got["documents"], got["metadatas"])
            for pid, doc, meta in passages_for_chunk(cid, text, m)}

    have = (col.get(include=["documents", "metadatas"]) if col is not None
            else {"ids": [], "documents": [], "metadatas": []})
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
    total = col.count() if col is not None else 0
    if total == 0:
        return []
    neighbors = PASSAGE_NEIGHBORS if neighbors is None else neighbors
    pieces = query_pieces(query)
    kwargs = {"where": where} if where else {}
    res = col.query(query_embeddings=embed(pieces, query=True), n_results=min(n, total),
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
    return _with_neighbors(ranked, neighbors, col)


def _with_neighbors(ranked, neighbors: int, col) -> list[dict]:
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
