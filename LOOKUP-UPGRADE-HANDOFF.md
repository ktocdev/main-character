# Handoff: make lookup find exact text, not just summaries

Written 2026-09-23. Nothing in this plan is implemented yet.

## The problem

When you ask the companion or the chat screen about something specific, it often has only a summary of the entry, not the passage itself. It can say *when* you wrote something, but not quote *what* you wrote, so you end up opening History to read it yourself.

## Why it happens

Most of every long entry is never indexed for search by meaning.

1. **Entries are stored in large passages.** `bulk_import.chunk_entry` splits at about 6,000 characters (`max_tokens=1500`, about 4 characters per token), and only between paragraphs. Every write path uses it: chat close (`sessions._close_locked`), `rebuild_index.py`, `bulk_import.py` and `seed_corpus/import_seed_corpus.py`.
2. **The embedder reads only the first 256 tokens,** about 1,000 characters. Chroma's default all-MiniLM-L6-v2 cuts off the rest without any warning (`tokenizer.enable_truncation(max_length=256)` in `.venv/Lib/site-packages/chromadb/utils/embedding_functions/onnx_mini_lm_l6_v2.py`). Text after that point has no effect on the passage's embedding.
3. **The prompt trims each passage to 2,000 characters** (`EXCERPT_CHARS` in `config.py`). Even when a passage matches, the companion sees only its first part.

Measured on the real journal on 2026-09-23:

- 393 entry files. The median is 3,887 characters, and the 90th percentile is 6,031. 313 files are over 2,000 characters.
- 3,280 paragraphs. 302 of them (9%) are over 1,000 characters, and together they hold **32% of all text**.

For a typical entry, search by meaning is working from about the first quarter.

**The query is cut off the same way.** Before an entry reply, the search query is the whole new entry, and the embedder reads only its first ~1,000 characters. So the search finds past passages related to the entry's opening and nothing after it. When the companion speaks first (`stream_reflection`), it has the same problem, because its query is the opening of the latest entry. Chat messages and chat-screen questions are short, so they are mostly unaffected.

The same limit applies to the summary index (`summarizer.sync_summary_embeddings`). Domain docs, about 500 words each, and longer entity profiles are indexed only by their opening.

## Constraints found while planning

These rule out the obvious fix of making `chunk_entry` smaller.

- **The pipeline rebuilds entries from the stored passages.** `entities.get_conversations` joins each entry's passages with `"\n\n"`, in `_c<n>` id order. Categories, dreams, entities and organic all read entries this way. If passages overlapped, or split a paragraph into sentences, the rebuilt text would no longer match the original. Everything downstream would see altered entries.
- **Recorded demo replies are matched by an exact hash of the request.** `mock_client.request_fingerprint` hashes the full `system`, `messages` and `output_config`. There are 68 recorded close-pipeline calls in `mock_fixtures/demo_close/`: summaries, categories, entities, seed, title, patterns and organic. Any change to the text these steps receive breaks the demo close replay. `tests/test_demo_close_replay.py` catches this, and re-recording costs real API calls (`seed_corpus/capture_demo_close.py`). No companion replies are recorded, so changing what the companion's search returns does **not** affect the replay.
- **Splitting only between paragraphs isn't enough.** A third of the text is in paragraphs longer than the embedder's limit.

## Recommended design: a separate search-only passage index

Leave the existing journal collection exactly as it is. It remains the stored copy of each entry that the pipeline, entry counts, category tags and exact search rely on. Add a second Chroma collection, for example `journal_passages`, that is used **only for search by meaning**.

- **Passages:** about 800 characters, split between paragraphs first, then between sentences within long paragraphs, with about 150 characters of overlap. Overlap is safe here because nothing rebuilds entries from this index.
- **Metadata on each passage:** `date`, `title`, `source_id` (the id of the journal chunk it came from) and the passage's position in the entry. This is enough to show it, to go back to the entry it came from, and to delete or rebuild it.
- **Fallback:** if the passage index is empty, search the old collection as it does today. Existing installs keep working until they rebuild.

Rejected alternative: changing `chunk_entry` and making `get_conversations` rebuild entries from the original separators. That changes the pipeline's input, risks the demo replay, and touches every reader of the journal collection, for no extra benefit.

## Plan

Each step can be merged on its own. Step 0 comes first, so every later step can be measured.

### Step 0: a local retrieval test (free, no API calls)

`scripts/eval_retrieval.py`. Build a set of about 30 questions, each paired with a fact that sits **after the first 1,000 characters** of its entry. Take them from the demo corpus (`seed_corpus/journal_entries/`) so the set can be committed, and optionally from the real journal kept locally and untracked. For each question, report whether a passage containing the fact is among the top 6 search results (and the top 12). Run it against the current search to get a baseline.

Include a second set shaped like entry replies: the query is a long, new entry whose connection to an older entry is **after its first 1,000 characters**. This is the case the query splitting in step 1 is for, and short questions won't show it.

### Step 1: the passage index

- Add the passage splitter, next to `chunk_entry` or in a new `passages.py`, with unit tests: long paragraphs split at sentence boundaries, no passage over the limit, every character of the entry covered.
- Write passages wherever journal chunks are written today: `sessions._close_locked`, `rebuild_index.py`, `bulk_import.py`, `seed_corpus/import_seed_corpus.py`, and the old CLI save in `companion.py` (the upsert near `ids=[entry_id]`). Remove stale passages wherever `rebuild_index.py` removes stale chunks.
- Switch `rag_journal.query_journal`, which feeds `build_context_block`, to the passage index, with the fallback above.
- **Split long queries.** When the query is longer than one passage (an entry reply, or the reflection query), split it with the same splitter, run one search per piece (capped at about 5), then merge, remove duplicates and keep the best matches. Do the same for `get_summary_hits`. This is local and free. There is no summary step and no extra Claude call: the reply prompt still contains the full entry, and only the choice of past passages changes. Without this, step 1 helps chat-screen questions but not entry replies.
- Once passages are small, stop trimming at `EXCERPT_CHARS`, and raise `N_SEMANTIC` from 6 to about 12. The total size of the context stays roughly the same.
- Move the search tab's by-meaning mode (`server.search_journal`, `mode=semantic`) to the new index too. It has the same problem. Smaller passages mean more results per entry, so check how the tab lists them, and group by entry if it doesn't already. The exact mode stays on the journal collection, which already searches full text.
- Follow-up in the same shape: split long summary-index documents (domain docs, entity profiles) into passages, and map each hit back to its document.

Done when: the step 0 test shows a clear improvement on the deep-fact questions, the full test suite passes, and `tests/test_demo_close_replay.py` passes without re-recording.

### Step 2: go from a summary to the entry it summarizes

In `companion.get_summary_hits`, when a hit's level is `entry summary`, its metadata already carries `date` and `title`. Query the passage index again, limited to that entry (`where={"date": ..., "title": ...}`), for the one or two passages that best match the question, and add them under the summary. A summary hit then brings the actual text with it.

### Step 3: exact-word search alongside search by meaning

Search by meaning is weak on names, numbers and rare words. Pull candidate terms from the question: quoted phrases, capitalized words that aren't at the start of a sentence, and known entity names and aliases (`entity_index`, which `match_entities` already uses). Look for them as exact substrings, reusing the search tab's exact mode (`_fold`, `_snippet_around` in `server.py`), and add a few matching passages. A full scan of the collection on each turn is fine at this size, but check the time it takes.

### Step 4: date filters

When a question names a month, a date or a range ("in March", "last summer", "2026-04-12"), limit the passage search to entries in that range. Dates are stored as `YYYY-MM-DD` strings. Use a `$in` filter over the dates that exist in the range, or filter in Python. Start with explicit dates and month names, and add relative phrases later.

### Step 5: the chat screen searches for itself (optional, costs more)

Give `/api/lookup` a tool-use loop (Claude API tool use) with three tools: `search_journal(query, mode, date_from, date_to)`, `read_entry(date, title)` and `list_entries(date_from, date_to)`. Claude searches, reads and searches again before answering. Put a hard cap on the number of rounds, for example 5.

- Use it on the chat screen only, not on companion replies. Each question becomes several calls.
- The client has to go through `config.get_client()` so every round is metered and checked against the caps.
- The mock client probably doesn't support tool use. The demo and the web demo must keep their current lookup behaviour. Check `MOCK_MODE`, `scripts/build_web_demo.py` and `static/js/web-demo/backend.js`.

## Checking your work

- Full suite: `.venv\Scripts\python.exe -m pytest -q`, about 2 minutes.
- Demo replay: `tests/test_demo_close_replay.py` must pass without re-recording.
- Try it on the sandbox, never the real journal first: `.\journal sandbox-reset` starts port 8145 from a clean first run. Load the demo journal there, rebuild, and ask about details deep in long demo entries.
- The real journal: only after the owner has seen the sandbox results. Stop 8144, run `python backup.py`, then rebuild the index (`rebuild_index.py`, extended in step 1). Rebuilding is local and costs nothing.
- Update `HOW-IT-WORKS.md` ("What the companion sees") with the new layers and counts.

## Open questions for the owner

- Passage size: 800 characters is a starting point. Step 0 should decide it.
- Should step 1 also cover the dreams collection? Each dream is indexed as one document (`dreams.build_index`), so a long dream narrative has the same 1,000-character limit, but dreams are usually short.
- Step 5 raises the cost of each chat-screen question. Should it be a setting, or always on?
