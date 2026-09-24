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

- **Passages are measured in tokens, not characters,** with the embedder's own tokenizer (the one Chroma's `ONNXMiniLM_L6_V2` loads), so the count is exactly what the embedder sees. Characters per token vary too much, with names, numbers and punctuation, for a character limit to be safe.
- **The limit is 254 tokens:** the embedder's 256 minus the two marker tokens it adds itself. An entry that fits stays whole as one passage. A longer entry is split into the fewest pieces that fit, **all about the same size**. For example, a 300-token entry becomes two passages of about 150, not one of 254 and a 46-token scrap. Split between paragraphs first, then between sentences within long paragraphs. Each piece overlaps the one before it by about 30 tokens, and the overlap counts toward the 254. Overlap is safe here because nothing rebuilds entries from this index.
- **Metadata on each passage:** `date`, `title`, `source_id` (the id of the journal chunk it came from) and the passage's position in the entry. This is enough to show it, to go back to the entry it came from, and to delete or rebuild it.
- **Fallback:** if the passage index is empty, search the old collection as it does today. Existing installs keep working until they rebuild.

Rejected alternative: changing `chunk_entry` and making `get_conversations` rebuild entries from the original separators. That changes the pipeline's input, risks the demo replay, and touches every reader of the journal collection, for no extra benefit.

## Plan

Each step can be merged on its own. Step 0 comes first, so every later step can be measured.

### Step 0: a local retrieval test (free, no API calls)

`scripts/eval_retrieval.py`. Build a set of about 30 questions, each paired with a fact that sits **after the first 1,000 characters** of its entry. Take them from the demo corpus (`seed_corpus/journal_entries/`) so the set can be committed, and optionally from the real journal kept locally and untracked. For each question, report whether a passage containing the fact is among the top 6 search results (and the top 12). Run it against the current search to get a baseline.

Also time the whole search for each question: the milliseconds from receiving the query to having the passages, summaries and entity docs ready. Today this is a small fraction of a second, and Opus's reply takes seconds. The later steps add searches, so the timing shows whether they slow the reply noticeably.

Build two testing tools in this step too, before anything changes, so every later step can be compared before and after.

- **A context viewer,** `scripts/show_context.py "question or entry text"`. It calls `build_context_block` and prints exactly what the companion would receive: passages, summaries, entity docs, patterns and dream weather, with each passage's date and title. It is free and never calls Claude. It reads whichever journal the `MC_*_DIR` variables point at, with a `--demo` flag for the demo corpus (the same dirs `SEED_ENV` in `server.py` uses) and a `--sandbox` flag for the sandbox copy below.
- **A sandbox copy of the real journal:** a `-CopyJournal` switch on `.claude/skills/sandbox-reset/run-sandbox.ps1` that copies the real journal's data dirs into the sandbox's `_d` folder, **except `chroma_data`**, then rebuilds the index there. Skipping the index means the copy can be taken while the real journal on 8144 is running, because only a running server's Chroma files are locked. The sandbox keeps its own `.env`, so its key is entered separately in the first-run screen.

Why these tools: **the demo cannot show whether replies improved.** Its companion replies are canned (`MC_MOCK=1`), so they are the same whatever the search finds. Steps 1–4 only change what the search finds, and that search runs for real in demo mode, so test it by looking at the search's output, not at the reply.

Include a second set shaped like entry replies: the query is a long, new entry whose connection to an older entry is **after its first 1,000 characters**. This is the case the query splitting in step 1 is for, and short questions won't show it.

### Step 1: the passage index

- Add the passage splitter, next to `chunk_entry` or in a new `passages.py`, with unit tests: an entry under the limit stays whole, long paragraphs split at sentence boundaries, pieces of one entry are close in size, no passage is over 254 tokens by the embedder's tokenizer, and every character of the entry is covered.
- Write passages wherever journal chunks are written today: `sessions._close_locked`, `rebuild_index.py`, `bulk_import.py`, `seed_corpus/import_seed_corpus.py`, and the old CLI save in `companion.py` (the upsert near `ids=[entry_id]`). Remove stale passages wherever `rebuild_index.py` removes stale chunks.
- Switch `rag_journal.query_journal`, which feeds `build_context_block`, to the passage index, with the fallback above.
- **Split long queries.** When the query is longer than one passage (an entry reply, or the reflection query), split it with the same splitter, run one search per piece, then merge, remove duplicates and keep the best matches. Search the **whole** entry, not only its first few pieces: the 90th-percentile entry is about 6,000 characters, and its ending matters as much as its opening. The ceiling is **26 searches per query**, which covers an entry twice as long as the journal's longest on 2026-09-23 (11,777 characters, 2,824 tokens; each piece adds about 224 new tokens after the overlap). Every entry written so far is searched in full. If a longer one comes along, pick 26 pieces spread evenly across it so the ending is still searched. The ceiling applies separately to the passage search and to `get_summary_hits`. Do the same for `get_summary_hits`. This is local and free. There is no summary step and no extra Claude call: the reply prompt still contains the full entry, and only the choice of past passages changes. Without this, step 1 helps chat-screen questions but not entry replies.
- Once passages are small, stop trimming at `EXCERPT_CHARS`, and raise `N_SEMANTIC` from 6 to about 12. The total size of the context stays roughly the same.
- Move the search tab's by-meaning mode (`server.search_journal`, `mode=semantic`) to the new index too. It has the same problem. Smaller passages mean more results per entry, so check how the tab lists them, and group by entry if it doesn't already. The exact mode stays on the journal collection, which already searches full text.
- **Dreams too.** Each dream is indexed as one document (`dreams.build_index`), so a long dream is cut off the same way. Use the same splitter there, with each passage pointing back to its dream. Most dreams are short and stay whole.
- Follow-up in the same shape: split long summary-index documents (domain docs, entity profiles) into passages, and map each hit back to its document.

Done when: the step 0 test shows a clear improvement on the deep-fact questions, the context viewer shows the deep passages on the sandbox copy of the real journal, the search time stays a small fraction of the reply time, the full test suite passes, and `tests/test_demo_close_replay.py` passes without re-recording.

### Step 2: go from a summary to the entry it summarizes

In `companion.get_summary_hits`, when a hit's level is `entry summary`, its metadata already carries `date` and `title`. Query the passage index again, limited to that entry (`where={"date": ..., "title": ...}`), for the one or two passages that best match the question, and add them under the summary. A summary hit then brings the actual text with it.

### Step 3: exact-word search alongside search by meaning

Search by meaning is weak on names, numbers and rare words. Pull candidate terms from the question: quoted phrases, capitalized words that aren't at the start of a sentence, and known entity names and aliases (`entity_index`, which `match_entities` already uses). Look for them as exact substrings, reusing the search tab's exact mode (`_fold`, `_snippet_around` in `server.py`), and add a few matching passages. A full scan of the collection on each turn is fine at this size, but check the time it takes.

### Step 4: date filters

When a question names a month, a date or a range ("in March", "last summer", "2026-04-12"), limit the passage search to entries in that range. Dates are stored as `YYYY-MM-DD` strings. Use a `$in` filter over the dates that exist in the range, or filter in Python. Start with explicit dates and month names, and add relative phrases later.

### Step 5: Smart Search on the chat screen (a setting, costs more)

By default the server searches once, packs the results into the prompt and makes one Claude call. If that search missed, Claude cannot look again. Smart Search gives Claude the search tools instead, so it can search, read and search again before answering. It is much better at questions about firsts, changes over time, counts and comparisons, which one search based on the question's wording often gets wrong.

Give `/api/lookup` a tool-use loop (Claude API tool use) with three tools: `search_journal(query, mode, date_from, date_to)`, `read_entry(date, title)` and `list_entries(date_from, date_to)`. Put a hard cap on the number of rounds, for example 5.

- **It is an option, called Smart Search, and off by default.** Add it to Settings, next to the model choices, and say there that each question makes several Claude calls, so it costs more and takes a few seconds longer. When it is off, the chat screen works as it does after steps 1–4.
- Use it on the chat screen only, not on companion replies.
- The client has to go through `config.get_client()` so every round is metered and checked against the caps.
- The mock client probably doesn't support tool use. The demo and the web demo must keep their current lookup behaviour. Check `MOCK_MODE`, `scripts/build_web_demo.py` and `static/js/web-demo/backend.js`.

## Checking your work

Three layers, cheapest first. Run them after every step.

1. **Automatic, free.**
   - The full suite: `.venv\Scripts\python.exe -m pytest -q`, about 2 minutes.
   - `tests/test_demo_close_replay.py` must pass without re-recording. This is what the demo is still good for: it proves the close pipeline was not disturbed.
   - Step 0's test script on the demo corpus, compared with the baseline, for both the found-or-not results and the timing.
2. **The context viewer, free.** For the owner to read what the companion would see, before and after:
   - On the demo (`--demo`): questions about details deep in long demo entries.
   - On the sandbox copy of the real journal (`--sandbox`): the owner's own kinds of questions, and pasted-in long entries whose connection to an older entry comes near the end. This is the realistic test.
3. **Real replies, costs the normal reply price.** Start the sandbox on the copy of the real journal with a key, and write and ask as usual. This is the only way to judge whether replies actually got better, and **the only way to test Smart Search** (step 5): the mock client cannot do tool use, so it stays off in the demo.

Do not judge reply quality on the demo. Its replies are canned and never change.

Only after the owner has seen these results, move to the real journal: stop 8144, run `python backup.py`, then rebuild the index (`rebuild_index.py`, extended in step 1). Rebuilding is local and costs nothing.

At the end, update `HOW-IT-WORKS.md` ("What the companion sees") with the new layers and counts.

## Open questions for the owner

None. Decided on 2026-09-23:

- Passages are split evenly under the 254-token limit.
- Step 1 covers dreams.
- Query splitting searches the whole entry, up to 26 searches: twice the longest entry at the time.
- Step 5 is an option called Smart Search, off by default.

If step 0's timing shows 26 searches slowing replies noticeably, bring the ceiling back to the owner rather than lowering it quietly.
