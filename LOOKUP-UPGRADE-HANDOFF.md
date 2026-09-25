# Handoff: make lookup find exact text, not just summaries

Written 2026-09-23. Step 0 done 2026-09-25 (results under step 0); steps 1–5 not started.

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
- **The hard limit is 254 tokens:** the embedder's 256 minus the two marker tokens it adds itself. No passage may exceed it.
- **The target size is a setting, and the step 0 test picks it** (revised 2026-09-25). The model averages every token into one vector, so a passage that mixes several topics matches each of them weakly. Smaller passages hold fewer topics and match more sharply, but a passage that is too small carries too little meaning. Two ways of splitting to compare on the real journal:
  - **Fewest pieces** (the original plan). An entry that fits in 254 tokens stays whole. A longer entry is split into the fewest pieces that fit, **all about the same size**: a 300-token entry becomes two of about 150, not one of 254 and a 46-token scrap. That is about 1,000 characters, or 3–4 of the owner's paragraphs, per piece.
  - **Paragraph-sized.** The owner's paragraphs average about 470 characters (about 110 tokens). Aim for about 120 tokens per piece: a paragraph near that size is its own passage, a very short one is merged with its neighbour, and a long one is split at sentences.

  Either way: split between paragraphs first, then between sentences within long paragraphs. Each piece overlaps the one before it by about 30 tokens, and the overlap counts toward the limit. Overlap is safe here because nothing rebuilds entries from this index.
- **Search small, read bigger.** Matching works best on small passages, but the companion needs context to understand a match. The alternative to test: match on the passage, then show the companion that passage together with its neighbours in the same entry (the passage before and after, found by `source_id` and position). The step 0 test decides this too. Measure hits on what the companion is shown.
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

#### Step 0 results (2026-09-25)

Built:

- `scripts/eval_retrieval.py` with the demo set `scripts/eval_retrieval_demo.json`: 33 questions and 8 entry replies. The script checks that each fact sits past character 1,000 of its entry (for entry replies, that the link sits past character 1,000 of the query) and refuses to run if the set doesn't match the journal. A hit means the fact appears in a returned passage as the companion sees it, trimmed to `EXCERPT_CHARS`. `--save` and `--compare` show the before and after for each question. A set for the real journal goes somewhere untracked (`docs/`), because it quotes the journal.
- `scripts/show_context.py`: see the step description above. It also has `--file` for a pasted entry and `--reflection` for the query the companion uses when it speaks first.
- `scripts/_journal_dirs.py`: the `--demo` / `--sandbox` switch both scripts share.
- `run-sandbox.ps1 -CopyJournal`: tested against a scratch copy. The rebuild takes 42 s for 397 chunks.

Baseline on the demo, saved in `docs/retrieval-eval/baseline-demo.json` (local):

| set | top 6 | top 12 | MRR | search alone (median) | context block (median) |
|---|---|---|---|---|---|
| questions | 16/33 | 22/33 | 0.355 | 165 ms | 349 ms |
| entry replies | 4/8 | 6/8 | 0.168 | 170 ms | 369 ms |

Keep in mind when reading the demo numbers: the demo has only 29 indexed entries, all single chunks of 670–2,300 characters. So "top 12" means 41% of the collection, and a deep fact is often found anyway because its entry's opening matched. Compare the top-6 count and MRR.

**The real journal is the test that counts.** Its set is `docs/retrieval-eval/real.json`: 38 questions from 36 long entries spread from December to September, plus 8 entry replies. It quotes the journal, so it stays local, as does its baseline, `docs/retrieval-eval/baseline-real.json`. To rerun it: `run-sandbox.ps1 -CopyJournal`, then `eval_retrieval.py --sandbox --questions docs/retrieval-eval/real.json`. (The baseline was taken on a scratch copy built the same way, 397 chunks.)

| set | top 6 | top 12 | MRR | search alone (median) | context block (median) |
|---|---|---|---|---|---|
| questions | 2/38 | 2/38 | 0.012 | 161 ms | 347 ms |
| entry replies | 0/8 | 0/8 | 0.000 | 159 ms | 358 ms |

A check that the test itself is sound: all 38 facts are in the index, but only 2 of them sit within the first 1,000 characters of their stored chunk, and only 6 of the 38 questions bring back any entry of the right date in the top 12. So today, search by meaning almost never reaches a detail past an entry's opening.

Files dated from July on are stored twice: the saved entry (`_HHMM_entry.md`) and the day as written up at chat close. The test accepts a fact found in either.

**Timing finding, which matters for step 1's 26-search ceiling.** Most of the 170 ms is not the search itself. Chroma's default embedding function reloads the ONNX model on every `collection.query(query_texts=...)`:

| what | ms |
|---|---|
| `col.query(query_texts=[q])` | 214 |
| embed with a new `ONNXMiniLM_L6_V2()` | 193 |
| embed with one reused `ONNXMiniLM_L6_V2` | 14 |
| `col.query(query_embeddings=...)` with that embedding | 15 |
| embed 26 ~1,000-char pieces in one batch, reused | 391 |

So step 1 should embed queries itself with one module-level embedder and pass `query_embeddings`, batching all the pieces of a long query in one call. With that change, 26 searches cost about 0.4 s. Doing them the current way would cost about 5 s. Written queries still go through the collection's own embedding, so the two must stay the same model. Use `ONNXMiniLM_L6_V2`, which is Chroma's default.

### Step 1: the passage index

- Add the passage splitter, next to `chunk_entry` or in a new `passages.py`, with the target size as a parameter. Unit tests: under the fewest-pieces setting an entry under the limit stays whole, long paragraphs split at sentence boundaries, very short paragraphs are merged rather than left as passages of their own, pieces of one entry are close in size, no passage is over 254 tokens by the embedder's tokenizer at any setting, and every character of the entry is covered.
- **Choose the size before wiring it in everywhere.** Build the passage index on the sandbox copy of the real journal with each candidate (fewest pieces up to 254, and paragraph-sized at about 120), each with and without neighbours added when shown. Run `eval_retrieval.py --compare` against the real baseline for each, and keep the winner as the default. Each run is a local rebuild of about a minute. Show the owner the table before deciding.
- Write passages wherever journal chunks are written today: `sessions._close_locked`, `rebuild_index.py`, `bulk_import.py`, `seed_corpus/import_seed_corpus.py`, and the old CLI save in `companion.py` (the upsert near `ids=[entry_id]`). Remove stale passages wherever `rebuild_index.py` removes stale chunks.
- Switch `rag_journal.query_journal`, which feeds `build_context_block`, to the passage index, with the fallback above.
- **Split long queries.** When the query is longer than one passage (an entry reply, or the reflection query), split it with the same splitter, run one search per piece, then merge, remove duplicates and keep the best matches. Search the **whole** entry, not only its first few pieces: the 90th-percentile entry is about 6,000 characters, and its ending matters as much as its opening. The ceiling is **26 searches per query**, which covers an entry twice as long as the journal's longest on 2026-09-23 (11,777 characters, 2,824 tokens; each piece adds about 224 new tokens after the overlap). Every entry written so far is searched in full. If a longer one comes along, pick 26 pieces spread evenly across it so the ending is still searched. The ceiling applies separately to the passage search and to `get_summary_hits`. Do the same for `get_summary_hits`. This is local and free. There is no summary step and no extra Claude call: the reply prompt still contains the full entry, and only the choice of past passages changes. Without this, step 1 helps chat-screen questions but not entry replies.
- Once passages are small, stop trimming at `EXCERPT_CHARS`, and raise `N_SEMANTIC` from 6 to about 12. The total size of the context stays roughly the same. If neighbours are added when shown, each result is about three passages, so check the context size with `show_context.py` and lower `N_SEMANTIC` if needed. Merge overlapping neighbour windows from the same entry so no text is shown twice.
- Move the search tab's by-meaning mode (`server.search_journal`, `mode=semantic`) to the new index too. It has the same problem. Smaller passages mean more results per entry, so check how the tab lists them, and group by entry if it doesn't already. The exact mode stays on the journal collection, which already searches full text.
- **Dreams too.** Each dream is indexed as one document (`dreams.build_index`), so a long dream is cut off the same way. Use the same splitter there, with each passage pointing back to its dream. Most dreams are short and stay whole.
- Follow-up in the same shape: split long summary-index documents (domain docs, entity profiles) into passages, and map each hit back to its document.

Done when: the step 0 test shows a clear improvement on the deep-fact questions, the context viewer shows the deep passages on the sandbox copy of the real journal, the search time stays a small fraction of the reply time, the full test suite passes, and `tests/test_demo_close_replay.py` passes without re-recording.

### Step 1b: tell the companion how it works

The companion's prompt (`SYSTEM_PROMPT` in `companion.py`) only half explains where its knowledge comes from. The passage index changes what it receives, so fix the prompt at the same time. Found on 2026-09-23:

- **The opening contradicts the rest.** It calls the companion a friend "who has read every previous entry and remembers what matters", then says it only has retrieved excerpts. Keep the second idea: it doesn't remember everything, it knows where to look.
- **"The user's journal spans December 2025 to the present" is hard-coded.** That is the owner's start date, wrong for the demo (Jordan's journal) and for anyone else. Fill it in from the journal's first entry date. The system prompt is cached, so the date must stay stable between turns; the first entry's date is.
- **The context sections are not explained.** Nothing says `<related_summaries>` are generated text rather than the person's words, while the prompt also says "Quote their own language back". It could quote a summary as if the person wrote it. Say which sections are their own words (`<recent_entries>`, `<related_history>`) and may be quoted, and that summaries are an outline with a date, not something to quote.
- **It does not know the app exists.** When it has only a summary, it cannot say "that's in History under March 12", because it does not know History or the search tab exist.
- **The chat screen uses the companion persona.** `/api/lookup` calls `stream_reply` with the same `SYSTEM_PROMPT`, so the screen for finding things gets the witness-friend voice and its rules ("Ask more than one question" is banned, "Default to prose"). Give it its own short prompt: its job is to find things in the journal, give exact dates, quote when it has the passage, and say plainly when it has only a summary and where to read the entry.
- **A stale reference.** `REFLECTION_REQUEST` tells it to look at "the snapshot", which no longer exists.

Add a short "How you work" section to the prompt:

- It is part of a journal app the person runs on their own computer and writes in. The conversation is a **chapter**: it stays open for days and becomes memory when the person closes the chapter (see the side task below).
- Each turn comes with a context block. Name each section and what it is.
- The open chapter is not in the search yet. It knows it from the conversation itself.
- When the context doesn't hold something, it says so and does not invent it. When it has only a summary or a date, it says where to read the full entry: the History tab by date, or the search tab.

**Use the knowledge, don't narrate it.** A line like "I only have a summary of that one, it's in History under March 12" is right. Explaining the retrieval system in a journal reply is wrong. Say this in the prompt.

Steps 2 to 5 each add a layer. Each of them updates the "How you work" section in the same change, so the prompt always describes what the companion actually receives.

Testing: this changes the companion's voice, so only real replies show whether it worked (layer 3 in [Checking your work](#checking-your-work)). The demo replay is not affected: none of its recorded calls use these prompts. Things to look for:

- It no longer quotes summaries as the person's words.
- When it has only a summary, it gives the date and says where to read the entry.
- Journal replies do not mention the app unless that helps.
- The chat screen answers like a finder, not a companion.

### Step 2: go from a summary to the entry it summarizes

In `companion.get_summary_hits`, when a hit's level is `entry summary`, its metadata already carries `date` and `title`. Query the passage index again, limited to that entry (`where={"date": ..., "title": ...}`), for the one or two passages that best match the question, and add them under the summary. A summary hit then brings the actual text with it.

### Step 3: exact-word search alongside search by meaning

Search by meaning is weak on names, numbers and rare words. Pull candidate terms from the question: quoted phrases, capitalized words that aren't at the start of a sentence, and known entity names and aliases (`entity_index`, which `match_entities` already uses). Look for them as exact substrings, reusing the search tab's exact mode (`_fold`, `_snippet_around` in `server.py`), and add a few matching passages. A full scan of the collection on each turn is fine at this size, but check the time it takes.

### Step 4: date filters

When a question names a month, a date or a range ("in March", "last summer", "2026-04-12"), limit the passage search to entries in that range. Dates are stored as `YYYY-MM-DD` strings. Use a `$in` filter over the dates that exist in the range, or filter in Python. Start with explicit dates and month names, and add relative phrases later.

### Step 5: Smart Search on the chat screen (a setting, costs more)

By default the server searches once, packs the results into the prompt and makes one Claude call. If that search missed, Claude cannot look again. Smart Search gives Claude the search tools instead, so it can search, read and search again before answering. It is much better at questions about firsts, changes over time, counts and comparisons, which one search based on the question's wording often gets wrong.

Give `/api/lookup` a tool-use loop (Claude API tool use) with three tools: `search_journal(query, mode, date_from, date_to)`, `read_entry(date, title)` and `list_entries(date_from, date_to)`. Put a hard cap on the number of rounds, for example 5. Smart Search uses the chat screen's own prompt from step 1b, extended to explain the tools.

- **It is an option, called Smart Search, and off by default.** Add it to Settings, next to the model choices, and say there that each question makes several Claude calls, so it costs more and takes a few seconds longer. When it is off, the chat screen works as it does after steps 1–4.
- Use it on the chat screen only, not on companion replies.
- The client has to go through `config.get_client()` so every round is metered and checked against the caps.
- The mock client probably doesn't support tool use. The demo and the web demo must keep their current lookup behaviour. Check `MOCK_MODE`, `scripts/build_web_demo.py` and `static/js/web-demo/backend.js`.

### Side task: call the conversation a chapter

Added 2026-09-25. This is not part of the search work and can be merged on its own at any point. It is a wording change to fit Main Character's story theme: the conversation on the write tab, which stays open for days and becomes memory when it is closed, is called a **chapter**. Closing it is **closing the chapter**.

"Chat" means two things in the interface today: that conversation, and the **chat tab**, which is the lookup screen (`/api/lookup`, "look something up in your journal…"). Only the first one is renamed. The chat tab keeps its name. The rename also clears up that ambiguity. In this document, "the chat screen" means the chat tab.

What changes (user-facing text only):

- The write tab's ⋯ menu button `summarize & close chat` (`#reset` in `static/index.html`) becomes **close chapter**, and its tooltip becomes "this is the summarize point. Your side of this chapter becomes a journal entry, memory updates in the background, and a new chapter opens".
- The history tab's `summarize & start new chat` (`#session-close-btn`) becomes **close chapter & start the next** (or the same label as above, if that reads better), with its tooltip changed to match.
- The other places that mean the conversation: "the life summary every chat opens with" (seed tooltips and the seed review note), "Closing a chat already does this" (`#refresh-summaries`), the help text (`static/index.html`, around the "summarize & close chat", "closed chat" and "Closed chats" lines), and `static/js/write.js` (`SAVED_NOTE`, "chat closed and saved as…", and the note at line ~201). History's list of closed conversations becomes a list of chapters.
- `README.md` and `HOW-IT-WORKS.md`, where they describe the button and closed chats.
- Case: lowercase, like the rest of the interface (`close chapter`). Decided by the owner on 2026-09-25.

What does not change:

- Code identifiers, API routes (`/api/session/...`), file and folder names (`sessions/`), and code comments. The code calls it a session, and that stays.
- **The prompts the close pipeline sends to Claude.** Their exact wording is hashed into the recorded demo replies (`mock_fixtures/demo_close/`), so changing a word there breaks `tests/test_demo_close_replay.py` and costs a re-recording. Leave them alone.
- Titles already stored in the journal, such as "… — continued".

Where the chapter word does belong outside the interface: step 1b's "How you work" section in the companion's prompt. That prompt is not recorded, so describe the conversation there as a chapter that becomes memory when it is closed, so the companion uses the same word as the app.

Checking: grep the tests for the old strings and update any that assert on them. Run the full suite, and `tests/test_web_demo_build.py` in particular. Then rebuild the web demo (`scripts/build_web_demo.py`, whose `static/js/web-demo/backend.js` may carry its own copies of these strings) and refresh the portfolio copy (the `refresh-demo` skill). Look at the write, history and help tabs in the sandbox to make sure no user-facing "close chat" is left.

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

- Passages are split evenly under the 254-token limit. *Revised 2026-09-25:* the target size is a setting chosen by the step 0 test on the real journal, between fewest pieces (up to 254) and paragraph-sized (about 120), with and without neighbouring passages added when shown.
- Step 1 covers dreams.
- Query splitting searches the whole entry, up to 26 searches: twice the longest entry at the time.
- Step 5 is an option called Smart Search, off by default.

If step 0's timing shows 26 searches slowing replies noticeably, bring the ceiling back to the owner rather than lowering it quietly.
