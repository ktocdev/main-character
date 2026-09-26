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
    remove_chunks(ids)      -> ...and for chunks just deleted
    sync()                  -> reconcile the whole index with the journal
    search(query, n)        -> the best passages, shaped like query_journal's

The summaries and dreams are searched with the same model, through
mirror() (their owners rewrite them whole) and search_documents().

The embedder is config.EMBED_MODEL, run locally by fastembed: a model trained
to match a question to the passage that answers it, which found half again
as many of the test set's quotes as MiniLM (the handoff's step 1a). Sizes are
counted with the model's own tokenizer, so a passage's count is exactly what
the model sees. One long-lived instance does every embedding: a long query
split into dozens of searches cannot afford a model load per search.

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
# Searches per query: at 120-token passages each piece adds ~90 new tokens,
# so 64 cover ~5,800 tokens, twice the journal's longest entry (2,824 tokens,
# 31 pieces) in September 2026. Longer entries are sampled evenly.
MAX_QUERY_PIECES = 64
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
# COLLECTIONS ON THIS MODEL
# ---------------------------------------------------------------------------
# The passage index, the summaries and the dreams are all searched with this
# model, so each records it (`embed_model` in the collection's metadata) and
# is opened without an embedding function: every vector comes from embed().
# Chroma's default would be MiniLM, whose vectors have the same 384
# dimensions -- a document it embedded would be searchable, and wrong. With
# none, a write that brings no vectors fails instead.

def _open(name: str = COLLECTION_NAME):
    """(client, collection, state): state is "current", "stale" (another
    model embedded it, or nothing says which) or "missing"."""
    import chromadb
    from chromadb.errors import NotFoundError
    from config import CHROMA_DIR
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        col = client.get_collection(name, embedding_function=None)
    except NotFoundError:
        return client, None, "missing"
    state = "current" if (col.metadata or {}).get("embed_model") == EMBED_MODEL else "stale"
    return client, col, state


def model_collection(name: str, create: bool = False):
    """Collection `name` if this model embedded it, else None. With
    `create`, a missing one is made and one from another model replaced,
    empty -- for a caller about to fill it (mirror())."""
    client, col, state = _open(name)
    if state == "current":
        return col
    if not create:
        return None
    if col is not None:
        client.delete_collection(name)
    return client.create_collection(
        name=name, metadata={"embed_model": EMBED_MODEL},
        configuration={"hnsw": _HNSW}, embedding_function=None)


def get_passage_collection(create: bool = False):
    """The passage collection if it can be searched and added to, else None:
    it can't if it was never built or another model embedded it."""
    return model_collection(COLLECTION_NAME, create)


def drop(name: str = COLLECTION_NAME) -> None:
    """Delete a collection. For the passage index, search then falls back
    to the journal chunks until rebuild_index.py builds it again."""
    client, col, _ = _open(name)
    if col is not None:
        client.delete_collection(name)


def mirror(name: str, rows: list[tuple[str, str, dict]],
           dry_run: bool = False) -> dict:
    """Make collection `name` hold exactly `rows`, [(id, text, metadata)].
    Only rows that are new or changed are embedded; ids not among them are
    deleted. A missing collection is made, and one from another model
    replaced, so then everything is embedded."""
    col = model_collection(name, create=not dry_run)
    want = {rid: (doc, meta) for rid, doc, meta in rows}
    have = (col.get(include=["documents", "metadatas"]) if col is not None
            else {"ids": [], "documents": [], "metadatas": []})
    have = {rid: (doc, meta) for rid, doc, meta in
            zip(have["ids"], have["documents"], have["metadatas"])}

    stale = [rid for rid in have if rid not in want]
    fresh = [(rid, *want[rid]) for rid in want if have.get(rid) != want[rid]]
    if not dry_run:
        if stale:
            col.delete(ids=stale)
        _upsert(col, fresh)
    return {"documents": len(want), "removed": len(stale), "embedded": len(fresh)}


def split_documents(rows: list[tuple[str, str, dict]],
                    target: int | None = None) -> list[tuple[str, str, dict]]:
    """Each document as its passages, [(id, text, metadata)]: ids
    `<document id>_p<n>`, and metadata the document's plus where the passage
    sits in it (`source_id`, `position`, `count`, `start`, `end`)."""
    out = []
    for doc_id, text, meta in rows:
        pieces = split(text, target)
        for i, p in enumerate(pieces):
            out.append((f"{doc_id}_p{i}", p["text"], {
                **meta, "source_id": doc_id, "position": i, "count": len(pieces),
                "start": p["start"], "end": p["end"]}))
    return out


def search_documents(name: str, query: str, n: int,
                     where: dict | None = None) -> list[tuple[str, dict, float]]:
    """The n best documents of collection `name` for `query`, as
    [(text, metadata, distance)], with the query split as search() splits
    it. A collection from before the model switch -- chroma's MiniLM
    embedded it, and nothing is recorded -- is searched with MiniLM until
    its owner rewrites it; one from another model, not at all."""
    client, col, state = _open(name)
    if col is None or col.count() == 0:
        return []
    if state == "current":
        return [(doc, meta, dist) for _, doc, meta, dist in ranked(col, query, n, where)]
    if "embed_model" in (col.metadata or {}):
        return []
    legacy = client.get_collection(name)      # chroma's default: MiniLM
    kwargs = {"where": where} if where else {}
    res = legacy.query(query_texts=[query], n_results=min(n, legacy.count()), **kwargs)
    return list(zip(res["documents"][0], res["metadatas"][0], res["distances"][0]))


# ---------------------------------------------------------------------------
# THE PASSAGE INDEX
# ---------------------------------------------------------------------------

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


def _upsert(col, rows: list[tuple[str, str, dict]], vectors=None) -> None:
    for i in range(0, len(rows), _UPSERT):
        batch = rows[i:i + _UPSERT]
        col.upsert(ids=[r[0] for r in batch],
                   documents=[r[1] for r in batch],
                   metadatas=[r[2] for r in batch],
                   embeddings=(vectors[i:i + _UPSERT] if vectors is not None
                               else embed([r[1] for r in batch])))


def index_chunks(ids: list[str], docs: list[str], metas: list[dict],
                 journal=None) -> int:
    """Passages for journal chunks that were just written, replacing any the
    chunks had before. Returns the number of passages written.

    For the write paths, which call it right after writing the chunks. The
    index is kept complete or absent: search falls back to the journal chunks
    only when it is absent, so an index missing an entry would hide that
    entry instead. So the passages are added only to an index built for this
    model. A missing index is built when the journal holds nothing but these
    chunks (a new journal's first write), since that is the whole journal;
    otherwise it waits for rebuild_index.py. And if adding fails -- the model
    deleted from its cache and no connection, say -- the index is dropped,
    and search falls back to the chunks, which do hold the new entry.
    """
    if not ids:
        return 0
    try:
        _, col, state = _open()
        if state == "missing":
            from rag_journal import get_collection
            journal = journal or get_collection()
            if set(journal.get(include=[])["ids"]) <= set(ids):
                return sync(journal)["embedded"]
            return 0
        if state == "stale":
            return 0          # rebuild_index.py replaces it whole
        rows = [r for cid, doc, meta in zip(ids, docs, metas)
                for r in passages_for_chunk(cid, doc, meta)]
        vectors = embed([r[1] for r in rows])   # before anything is deleted
        _delete_sources(col, ids)
        _upsert(col, rows, vectors)
        return len(rows)
    except Exception as exc:
        _give_up(exc)
        return 0


def remove_chunks(ids: list[str]) -> None:
    """Drop the passages of journal chunks that were deleted."""
    if not ids:
        return
    try:
        col = get_passage_collection()
        if col is not None:
            _delete_sources(col, ids)
    except Exception as exc:
        _give_up(exc)


def _delete_sources(col, ids: list[str]) -> None:
    old = col.get(where={"source_id": {"$in": list(ids)}}, include=[])["ids"]
    if old:
        col.delete(ids=old)


def _give_up(exc: Exception) -> None:
    print(f"  [passages] could not update the passage index "
          f"({exc.__class__.__name__}: {exc}). Search uses the journal "
          f"chunks until `python rebuild_index.py` builds it again.",
          file=sys.stderr, flush=True)
    try:
        drop()
    except Exception:
        pass


def sync(journal=None, dry_run: bool = False) -> dict:
    """Make the passage index match the journal collection, building it if
    it is missing. Only passages that are new or changed are embedded; the
    rest are left alone. An index embedded by another model is replaced, so
    everything is embedded."""
    from rag_journal import get_collection
    journal = journal or get_collection()
    got = journal.get(include=["documents", "metadatas"])
    rows = [r for cid, text, m in zip(got["ids"], got["documents"], got["metadatas"])
            for r in passages_for_chunk(cid, text, m)]
    return mirror(COLLECTION_NAME, rows, dry_run)


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


def ranked(col, query: str, n: int,
           where: dict | None = None) -> list[tuple[str, str, dict, float]]:
    """The n best records of `col` for `query`, as [(id, text, metadata,
    distance)], best first.

    Each piece of the query is searched, and the pieces take turns filling
    the n slots: every piece's best match comes before any piece's second
    best. Merging on distance alone lets whichever part of a long entry the
    journal has the most to say about crowd out the rest -- a long opening
    about work fills every slot, and the connection in the last paragraph
    never gets one. Within a turn, closer comes first. A short query is one
    piece, so this is plain nearest-first.
    """
    total = col.count()
    if total == 0:
        return []
    pieces = query_pieces(query)
    kwargs = {"where": where} if where else {}
    res = col.query(query_embeddings=embed(pieces, query=True), n_results=min(n, total),
                    include=["documents", "metadatas", "distances"], **kwargs)
    per_piece = [list(zip(ids, docs, metas, dists)) for ids, docs, metas, dists
                 in zip(res["ids"], res["documents"], res["metadatas"], res["distances"])]
    out, seen = [], set()
    for turn in range(max((len(p) for p in per_piece), default=0)):
        row = sorted((p[turn] for p in per_piece if turn < len(p)), key=lambda h: h[3])
        for hit in row:
            if hit[0] not in seen:
                seen.add(hit[0])
                out.append(hit)
        if len(out) >= n:
            break
    return out[:n]


def search(query: str, n: int, where: dict | None = None,
           neighbors: int | None = None, collection=None) -> list[dict]:
    """The n best passages for `query`, as [{"text", "metadata", "distance"}],
    best first -- the shape `rag_journal.query_journal` returns. The
    metadata's `source_id`, `start` and `end` say which span of which
    journal chunk the text is.

    The query is split and its pieces take turns (ranked()). With
    `neighbors`, each hit is shown with that many passages on either side
    from the same chunk, and hits whose windows touch are merged, so no text
    is shown twice.
    """
    col = collection or get_passage_collection()
    hits = ranked(col, query, n, where) if col is not None else []
    if not hits:
        return []
    neighbors = PASSAGE_NEIGHBORS if neighbors is None else neighbors
    if neighbors <= 0:
        return [{"text": doc, "metadata": meta, "distance": dist}
                for _, doc, meta, dist in hits]
    return _with_neighbors([(dist, doc, meta) for _, doc, meta, dist in hits],
                           neighbors, col)


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
        # start/end become the window's, so they describe the text shown.
        out.append({"text": text[first["start"]:last["end"]].strip(),
                    "metadata": {**w["metadata"], "start": first["start"],
                                 "end": last["end"]},
                    "distance": w["distance"]})
    return out
