# Handoff: make lookup find exact text, not just summaries

Written 2026-09-23. Step 0 done 2026-09-25 (results under step 0). Step 1 done 2026-09-25 on branch `JRNL-47`: results under steps 1 and 1a, and in "Step 1 finished". The passage index is in `cef2a55`, the switch to arctic-embed-s in `55445c8`, and the rest of step 1 in the commit after. **Where to pick up: [Next steps](#next-steps).**

## Next steps

As of 2026-09-25, stopped here:

1. **Decided 2026-09-25: switch to `snowflake/snowflake-arctic-embed-s`,** with 120-token passages and 1 neighbour. On the real set it finds 30/38 in the top 6, against 20 with MiniLM and 2 at baseline (step 1a results).
2. **Switch the model: done 2026-09-25** (see "The switch, as built" under step 1a). Through the real code path it finds 30/38 in the top 6 and 31 in the top 12, with MRR 0.628, which matches the comparison exactly. Entry replies are 3/8 in both the top 6 and top 12; MiniLM got 3/8 and 5/8.
3. **Finish step 1: done 2026-09-25** (see "Step 1 finished" under step 1a).
   - **The search ceiling is 64, decided by the owner 2026-09-25** (`passages.MAX_QUERY_PIECES`, was 26). It was sized for 254-token pieces, which add ~224 new tokens each, so 26 covered twice the longest entry. At 120 tokens each piece adds ~90, so 26 cover ~2,340 tokens. On the real journal, 5 of 395 entries need more (27–32 pieces, up to 2,824 tokens). Those are still searched end to end, by 26 pieces spread evenly, but some text between them is skipped. Measured on the copy, the longest chunk (11,697 characters, 31 pieces) takes 394 ms to search at 26 and 502 ms at 64, and its whole context block 1.3 s and 1.6 s. At 64 every entry is covered twice over, for about 0.1 s of search and 0.3 s of context block on the longest entries only.
4. **Step 1b** (the companion's prompt), then **steps 2, 3, 4 and 4b**, measuring each with `eval_retrieval.py --compare`.
5. **Holdout.** The owner is writing their own questions in `my-questions.txt` at the repo root, which is untracked. Move it to `docs/retrieval-eval/` (gitignored) and convert it to `holdout.json`. Only check that each quote is found in its entry. Don't read it for tuning, and run it only at the final check.
6. **Final check,** then the real journal: stop 8144, `python backup.py`, then `rebuild_index.py`.
7. **Step 5** (Smart Search), and the "close chapter" side task at any point.

Where things live:

- **In `docs/retrieval-eval/`** (local, not committed): `real.json` (the real-journal test set), `baseline-real.json`, `real-t{120,254}-n{0,1}.json` (the size runs), `model_compare.py` and `compare.out` (the model comparison: in memory, never touches a Chroma index).
- **In `docs/retrieval-eval/`**, also `real-arctic-t120-n1.json` (the switched model through the real code path) and `real-step1-done.json` (step 1 finished, with passages shown whole): the baseline to `--compare` against from here.
- **Session scratch,** likely gone in a new session: a copy of the real journal with its passage index (arctic, 120 tokens) and its summaries and dreams on arctic, and the throwaway venv with `fastembed`. Recreate the copy with `run-sandbox.ps1 -CopyJournal`, and run the eval against it with `--sandbox`. The app's own `.venv` now has `fastembed`.
- **The model** is downloaded to `~/.cache/main-character` (128 MB, `MC_MODEL_CACHE`).
- **`%TEMP%\mcfe`:** the five downloaded test models, about 1 GB. Only `model_compare.py` uses it; delete it when that's no longer needed.

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

- **Passages are measured in tokens, not characters,** with the embedder's own tokenizer (the one Chroma's `ONNXMiniLM_L6_V2` loads; *since the model switch on 2026-09-25, arctic-embed-s's own*), so the count is exactly what the embedder sees. Characters per token vary too much, with names, numbers and punctuation, for a character limit to be safe.
- **The hard limit is 254 tokens:** the embedder's 256 minus the two marker tokens it adds itself. No passage may exceed it. *Since the model switch it is `passages.limit()`, the model's window less the markers and the query prefix: 502 for arctic-embed-s.*
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

#### Step 1 so far (2026-09-25)

Built and committed in `cef2a55`: `passages.py` (splitter, one long-lived embedder, the `journal_passages` index with `index_chunks` / `remove_chunks` / `sync`, and `search` with query splitting and neighbours), `PASSAGE_TOKENS` / `PASSAGE_NEIGHBORS` in `config.py`, `rag_journal.query_journal` switched to passages with the fallback, `rebuild_index.py` building the passage index, and a "text shown" column in `eval_retrieval.py`. On all 395 real entry files, the splitter produced no passage over 254 tokens and left no character uncovered.

The size experiment on a copy of the real journal (all-MiniLM-L6-v2):

| variant | questions top 6 | top 12 | MRR | entry replies top 6 | top 12 | text shown, top 6 |
|---|---|---|---|---|---|---|
| baseline | 2/38 | 2 | 0.012 | 0/8 | 0 | ~12,000 chars |
| 254 tokens | 17 | 22 | 0.336 | 3 | 4 | 5,500 |
| 254 + 1 neighbour | 20 | 24 | 0.372 | 3 | 4 | 13,600 |
| 120 tokens | 19 | 24 | 0.366 | 2 | 3 | 2,800 |
| 120 + 1 neighbour | 20 | 24 | **0.424** | 3 | **5** | 7,300 |

Findings:

- **The long-query pieces take turns** (`passages.search`): every piece's best match comes before any piece's second best. The original merge ranked by distance alone. With it, a long opening about work took every slot and the link in an entry's last paragraph never got one: 120 tokens scored 0/8 on entry replies. Taking turns raised that to 2/8.
- **Search is 25–90 ms**, down from ~160 ms, because the embedder is loaded once.
- The context block is currently slower than the baseline (~460 ms against ~350). Summary search still goes through Chroma's reloading embedder. Moving it to the shared embedder in this step fixes that.
- 8 entry-reply tests are too few to separate the variants. A difference of one is noise.
- **Provisional choice: 120 tokens with 1 neighbour, `N_SEMANTIC` 12.** Showing 12 results comes to roughly today's context size, with 24/38 hits instead of 2. It is provisional because the embedding model (step 1a) sets the window, and the size is re-run for the chosen model.

**Why the other 14 questions miss** (120-token index): 10 name a rare word that only 1–4 passages contain ("Begonia", "Margarita", "karaoke", "kratom"). Search by meaning ranks those low, and step 3 targets them. 5 are ranked 13–30, just outside what is shown, which is what step 4b targets. 2 give a time ("around Christmas", "in March") with otherwise generic words, which step 4 targets. 2 have the word that ties question to answer in the passage just before, which step 3 plus neighbours targets. These groups overlap.

**The questions were written from the entries**, so they may be phrased more helpfully than the owner's own would be. Before the final comparison, the owner writes about 10 questions of their own into an untracked holdout file (`docs/retrieval-eval/holdout.json`). No setting is tuned against it; it only confirms the final result.

### Step 1a: choose the embedding model (before the size is final)

all-MiniLM-L6-v2 dates from 2021, is one of the smallest embedding models, and reads 256 tokens. Newer small models are trained specifically to match a question to the passage that answers it, and some read 512 tokens. The owner wants this tested. It is **not fine-tuning**: every candidate is used as published. Only which model is used changes.

- **Candidates:** `BAAI/bge-small-en-v1.5` (384 dimensions, 512 tokens), `snowflake/snowflake-arctic-embed-s`, and `intfloat/e5-small-v2`, against MiniLM as the control. Optionally one "base"-sized model (768 dimensions, e.g. `bge-base-en-v1.5`) to see whether size buys enough to be worth it.
- **Runtime:** `fastembed` runs these as ONNX without PyTorch, which keeps the install from growing by gigabytes. It also provides the cross-encoder for step 4b, so one new dependency covers both. Check that it installs cleanly next to chromadb's pinned `onnxruntime` on Windows before relying on it. Ask the owner before adding it to `requirements.lock`.
- **Query and passage prefixes:** bge and e5 expect the question and the passage to be embedded differently. bge prefixes the query with an instruction; e5 uses `query: ` and `passage: `. Getting this wrong quietly costs accuracy. `passages.embed` needs a query/passage flag.
- **The window follows the model.** `LIMIT` and the tokenizer used for counting come from the chosen model, not from Chroma's MiniLM. For a 512-token model, rerun the size experiment with that window (at least 120 and 254 targets, with and without a neighbour).
- **One model for every collection that is searched this way.** Passages, and in this step summaries and dreams, have to be embedded by the same model their queries are. Record the model name in each collection's metadata. When it doesn't match the configured model, search falls back and `rebuild_index.py` re-embeds, locally and at no cost. An existing install switching models must never mix vectors from two models in one collection.
- **Measure:** hits and MRR on the real set, the time to embed a query and to rebuild, and the download size. Show the owner the table. Keep MiniLM unless a candidate wins clearly: the swap has a real cost in download size and rebuild time.

#### Step 1a results (2026-09-25)

Run in memory by `docs/retrieval-eval/model_compare.py` in a throwaway venv, on the copy of the real journal (397 chunks). It uses the same splitter, turn-taking and neighbour logic as `passages.search`. As a check, its MiniLM at 120 tokens matched the production run exactly (20/24). `e5-small` isn't offered by fastembed, so `bge-base-en-v1.5` and `nomic-embed-text-v1.5` were tested instead. Best setting per model:

| model | setting | questions top 6 | top 12 | MRR | entry replies top 6 / 12 | embed the journal | download |
|---|---|---|---|---|---|---|---|
| baseline | whole chunks | 2 | 2 | 0.012 | 0 / 0 | – | – |
| MiniLM (current) | 120 + 1 neighbour | 20 | 24 | 0.424 | 3 / 5 | ~40 s | 90 MB |
| bge-small-en-v1.5 | 120 + 1 | 28 | 28 | 0.563 | 2 / 3 | ~2 min | 67 MB |
| **snowflake-arctic-embed-s** | **120 + 1** | **30** | **31** | **0.628** | 3 / 3 | ~2 min | 130 MB |
| bge-base-en-v1.5 | 120 + 1 | 25 | 31 | 0.544 | **5 / 6** | ~3.5 min | 210 MB |
| nomic-embed-text-v1.5 | 254, no neighbours | 28 | 31 | 0.435 | 3 / 4 | ~6.5 min | 520 MB |

The full grid (every model at 120, 254 and 480 tokens, with and without neighbours) is in `docs/retrieval-eval/compare.out`.

- **Small passages win for every model.** At 480 tokens, every model that can read that far did worst. The 120-token size stands.
- **Neighbours help every model.**
- **arctic-embed-s is recommended:** most hits, best ranking, and a moderate size. The entry-reply set is still too small to decide on (bge-base's 5/8 is suggestive, not conclusive).
- **Embedding a question takes 5–20 ms** for the small models.
- **fastembed installs cleanly** next to `requirements.lock`: it adds 5 small packages and changes none of the pinned ones (`onnxruntime` stays 1.28.0).

Things the switch must handle, found during the test:

- **fastembed's all-MiniLM-L6-v2 truncates at 128 tokens,** not 256, so its 254-token rows were invalid and are left out of the table. The other models are set to 512 (nomic 8,192). If MiniLM is ever used through fastembed, set its max length explicitly.
- **Downloads:** on this connection, Hugging Face resets the parallel downloader (8 connections) every time. `snapshot_download(..., max_workers=1)` with retries works. The first-run download in the app has to do the same, and report progress and failure plainly.
- **Windows' 260-character path limit:** Hugging Face's cache layout under a long folder went over it (`FileNotFoundError` on a `.incomplete` blob). Keep the model cache at a short path.
- **The query prefix** for arctic-embed-s (as for bge) is `Represent this sentence for searching relevant passages: `. Passages get no prefix.
- Write passages wherever journal chunks are written today: `sessions._close_locked`, `rebuild_index.py`, `bulk_import.py`, `seed_corpus/import_seed_corpus.py`, and the old CLI save in `companion.py` (the upsert near `ids=[entry_id]`). Remove stale passages wherever `rebuild_index.py` removes stale chunks.
- Switch `rag_journal.query_journal`, which feeds `build_context_block`, to the passage index, with the fallback above.
- **Split long queries.** When the query is longer than one passage (an entry reply, or the reflection query), split it with the same splitter, run one search per piece, then merge, remove duplicates and keep the best matches. Search the **whole** entry, not only its first few pieces: the 90th-percentile entry is about 6,000 characters, and its ending matters as much as its opening. The ceiling is **26 searches per query**, which covers an entry twice as long as the journal's longest on 2026-09-23 (11,777 characters, 2,824 tokens; each piece adds about 224 new tokens after the overlap). Every entry written so far is searched in full. If a longer one comes along, pick 26 pieces spread evenly across it so the ending is still searched. The ceiling applies separately to the passage search and to `get_summary_hits`. Do the same for `get_summary_hits`. This is local and free. There is no summary step and no extra Claude call: the reply prompt still contains the full entry, and only the choice of past passages changes. Without this, step 1 helps chat-screen questions but not entry replies.
- Once passages are small, stop trimming at `EXCERPT_CHARS`, and raise `N_SEMANTIC` from 6 to about 12. The total size of the context stays roughly the same. If neighbours are added when shown, each result is about three passages, so check the context size with `show_context.py` and lower `N_SEMANTIC` if needed. Merge overlapping neighbour windows from the same entry so no text is shown twice.
- Move the search tab's by-meaning mode (`server.search_journal`, `mode=semantic`) to the new index too. It has the same problem. Smaller passages mean more results per entry, so check how the tab lists them, and group by entry if it doesn't already. The exact mode stays on the journal collection, which already searches full text.
- **Dreams too.** Each dream is indexed as one document (`dreams.build_index`), so a long dream is cut off the same way. Use the same splitter there, with each passage pointing back to its dream. Most dreams are short and stay whole.
- Follow-up in the same shape: split long summary-index documents (domain docs, entity profiles) into passages, and map each hit back to its document.

#### The switch, as built (2026-09-25)

- **`config.py`:** `EMBED_MODEL` (`MC_EMBED_MODEL`, default arctic-embed-s) and `MODEL_CACHE` (`MC_MODEL_CACHE`, default `~/.cache/main-character`). It is deliberately not named `MC_*_DIR`, so the sandbox's copy loop, which copies every `MC_*_DIR`, leaves it alone.
- **`passages.py`:**
  - `MODELS` lists the allowed models and their query prefix (arctic-embed-s and bge-small).
  - `embed(texts, query=True)` adds the prefix.
  - The window comes from the model's own tokenizer: `limit()` is 512 − 2 markers − 8 prefix tokens = 502.
  - `_download()` fetches only the 5 files needed, one at a time, with 5 tries, and prints what it is doing. A final failure raises `ModelUnavailable`, and `query_journal` falls back to the journal chunks.
- **Collection metadata** records `embed_model`. A passage collection from another model, or from before this (no key), is never searched or written to. `sync()` deletes and rebuilds it, so every existing install falls back to the chunks until `rebuild_index.py` runs. The fallback now queries with `query_texts` (Chroma's MiniLM, the model those chunks were embedded with), not `passages.embed`.
- **Chroma's approximate index lost real matches.** At its defaults (`ef_search` 100, 16 links per point), and after upserts of 64 at a time, searches of the 4,117 passages missed 46 of the true top-12 across the 46 test queries. In one case, a passage at distance 0.287 was missing while one at 0.419 was returned. Changing `ef_search` on an existing collection had no effect. Built fresh with `ef_construction` 400, `ef_search` 400, 48 links, and written 1,000 at a time, it misses 1, and a search still takes ~30 ms. Those are now the collection's settings (`_HNSW`, `_UPSERT`). This was the whole gap between the first real-path run (29/30, entry replies 2/2) and the comparison (30/31, 3/3).
- **Timing on the copy:**
  - rebuilding all 4,117 passages takes ~2 min;
  - one search takes ~30 ms, or ~90 ms for an entry reply's 4–5 pieces;
  - the context block takes ~0.5 s. It is still dominated by `get_summary_hits` reloading MiniLM. With the laptop busy, one run measured 1.3 s median, with a 16 s outlier.
- **Docs updated for the switch:** `README.md`, `HOW-IT-WORKS.md` (the two models, the passage index, and the gap below), `.env.example` (`MC_PASSAGE_TOKENS`, `MC_PASSAGE_NEIGHBORS`, `MC_EMBED_MODEL`, `MC_MODEL_CACHE`), both `sandbox-reset/SKILL.md` copies (the model cache and `-CopyJournal`'s time), the export's README text in `export.py`, and the rebuild route's docstring in `server.py`.
- **Known gap until step 1 is finished:** a chapter close wrote only the journal chunks. *Closed when step 1 was finished: every write path now writes passages (below).*
- **Entry replies:** arctic finds 3/8 in the top 12, against MiniLM's 5/8. The set is too small to decide on. Step 4b's re-ranker is the next lever there, and the owner's holdout will add evidence.

Done when: the step 0 test shows a clear improvement on the deep-fact questions, the context viewer shows the deep passages on the sandbox copy of the real journal, the search time stays a small fraction of the reply time, the full test suite passes, and `tests/test_demo_close_replay.py` passes without re-recording.

#### Step 1 finished (2026-09-25)

- **Defaults:** `PASSAGE_TOKENS` 120, `PASSAGE_NEIGHBORS` 1, `N_SEMANTIC` 12 (`config.py`, `.env.example`).
- **The passage index is complete or absent.** Search falls back to the journal chunks only when the index is absent, so an index missing an entry would hide it instead. `passages.index_chunks` therefore:
  - adds to an index built for this model;
  - builds a missing one only when the journal holds nothing but the chunks just written (a new journal's first entry), since that is the whole journal. An install that hasn't run `rebuild_index.py` since the passage index arrived stays on the fallback, rather than get an index of only its newest entry;
  - leaves an index from another model for the rebuild;
  - on any failure, drops the index and says to run `rebuild_index.py`, so search falls back to the chunks, which do hold the new entry. It never fails the write.
- **Write paths:**
  - `sessions._close_locked` and the companion CLI's `store_entry` call `index_chunks` after writing their chunks;
  - `bulk_import.run_import` and `resplit.py` run one `passages.sync()` at the end, since many small writes weaken the index;
  - `import_seed_corpus.py` wipes `journal_passages` with the rest and syncs after importing.
  - `scripts/remove_seed_corpus.py` already matched every collection by date and title, so it removes passages too.
- **`server._embedder_cached`** is False if either model is missing, True if both are there, and None when that can't be told. The UI's wait wording says "embedding models (about 220MB)". Tests are in `tests/test_setup.py`.
- **Context block** (`companion.build_context_block`):
  - passages are shown whole;
  - a passage (or neighbour window) inside the part of a recent entry already shown is skipped, and one that overlaps it shows only the rest;
  - on the fallback, 6 whole chunks, cut at `EXCERPT_CHARS` as before, since 12 would double the block.
  - Search results now carry `start`/`end` for the window shown, not the hit's own passage, which is what the dedup needs.
- **Summaries and dreams on arctic:**
  - `passages.py` gained a small API for any collection searched with this model: `model_collection`, `mirror` (make a collection hold exactly these rows, embedding only what is new or changed, replacing one from another model), `split_documents`, `ranked` (query splitting and turns, shared with `search`), and `search_documents`.
  - Every one of these collections is opened with no embedding function. MiniLM's vectors have the same 384 dimensions, so a stray write without vectors would otherwise be searchable and wrong; this way it fails.
  - A collection from before the switch (no `embed_model`) is still searched with MiniLM until its owner rewrites it; one from another fastembed model is not searched at all.
  - `summarizer.sync_summary_embeddings` mirrors. A resync of the 789 summary documents now takes 0.6 s, where every processing run used to re-embed them all.
  - `get_summary_hits` searches with query splitting. `rag_journal.get_summary_collection` is gone, replaced by `SUMMARY_COLLECTION`.
  - `dreams.sync_collection` owns the whole dream collection: the flagged dream entries, read back from their markdown, keeping `time`/`source` from the collection, and the extracted dreams. `rebuild_index.build_dreams` calls it.
  - Dreams are split at the model's full window (502 tokens), so most stay whole: 69 dreams made 71 passages on the real copy.
  - `store_dream_entry` and the demo installer write through `dreams.put_entry`, which rebuilds the whole collection from files when it isn't built for this model yet.
  - `get_dream_hits` shows each dream once, its passages whole, with "[date] dream, continued:" before a later passage.
- **Search tab** (`server.search_journal`, `mode=semantic`):
  - ranks passages and lists each entry once, by its best passage;
  - a related entry's snippet is the passage that matched;
  - literal matches still always stay;
  - on the fallback, whole chunks as before.
  - The related margin is now 20% of the best distance, not a fixed 0.12. Arctic's distances are tighter: the 12th entry is ~0.05 behind the best, against MiniLM's ~0.15, from bests of ~0.3 and ~0.55. A fixed 0.12 kept all 12 almost every time.
- **Measured on the copy of the real journal:**
  - Retrieval is unchanged: questions 30/38 in the top 6 and 31/38 in the top 12, MRR 0.628; entry replies 3/8 (`real-step1-done.json`).
  - The context block now takes 72 ms median for a question (it was ~500 ms) and 222 ms for an entry reply. Summary search no longer reloads MiniLM.
  - Text shown for the top 12 is a median of ~12,000 characters, the same as 6 chunks trimmed to 2,000.
  - `rebuild_index.py` took 76 s the first time (summaries and dreams converted) and 15 s after that.
  - Search tab: the answer's quote is visible in a snippet for 25/38 test questions, against 0/38 on the old path. It keeps a median of 7.5 related entries per question, against 12 on the old path, and takes ~390 ms against ~300 ms.
- **Tests:**
  - `tests/test_passages.py` (26), covering the splitter, turns, neighbour windows, fallback, each write-path rule, the close, the context block's dedup and trimming, `mirror` and the legacy search, dream entries and long dreams, and the search tab.
  - The demo installer's boundary test also checks `journal_passages`.
  - The accounting tests with fake collections stub `index_chunks`.
  - Full suite: 232 passed, 2 skipped. The demo close replay passes without re-recording.
- **Not done here:** splitting long summary documents (the follow-up below); step 4b's re-ranker for entry replies.

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

### Step 3: keyword search alongside search by meaning

Search by meaning is weak on names, numbers and rare words, and 10 of the 14 questions step 1 misses name such a word. *Revised 2026-09-25:* the original plan looked up only quoted phrases, capitalized words and entity names. Many of the misses are ordinary rare words ("karaoke", "puppets", "kratom"), so this is now full keyword search:

- **Rank every passage by keyword relevance with BM25,** the standard scoring behind most search engines. It rewards a passage for containing the question's words, and rare words count for much more than common ones. Build it over the passage index's text. At about 4,000 passages it fits in memory and scores in milliseconds; rebuild it when the passage index changes. The `rank_bm25` package, or a short implementation, both work. Drop stopwords.
- **Normalize words the same way on both sides:** lowercase, fold accents (reuse `_fold` from `server.py`), drop possessive `'s`, and reduce plurals and simple verb endings, so "Luisa's" matches "luisa" and "puppets" matches "puppet".
- **Fuzzy matching for spelling.** The journal has typos and variant spellings ("tazmanian", "trigylerides"), and a question won't spell them the same way. For a question word with no exact match in the vocabulary, also match words within a small edit distance, or with high character-trigram overlap, at a lower weight. Only for words of 5 or more letters, so short words don't match everything.
- **Merge the two lists by taking turns,** the same way long-query pieces merge. Reciprocal rank fusion is the standard form: a passage's score is the sum of 1/(60 + rank) over the lists it appears in. The rank is what counts, not the raw score, because BM25 scores and distances aren't on the same scale.
- **Long queries:** run BM25 on each query piece, like the vector search, so an entry's ending gets its own keyword matches.
- Known entity names and aliases (`entity_index`) are a cheap extra: a question word that matches an alias can be expanded to the entity's name.

Measure on the real set, with and without fuzzy matching, and check that the short-question hits from step 1 don't drop.

### Step 4: date filters

When a question names a month, a date or a range ("in March", "last summer", "2026-04-12"), limit the passage search to entries in that range. Dates are stored as `YYYY-MM-DD` strings. Use a `$in` filter over the dates that exist in the range, or filter in Python. Start with explicit dates and month names, and add relative phrases later.

Two of the real-set misses are this case: "around Christmas" and "in March", with otherwise generic words. Seasons and holidays ("Christmas", "last summer", "Labor Day") map to ranges. When the question names a range, keep searching outside it too, at a lower weight, so a wrong guess about the range doesn't hide the answer.

### Step 4b: re-rank the candidates

Added 2026-09-25. Vector search compares the question and a passage as two separate lists of numbers. A **cross-encoder** is a second small model that reads the question and a passage *together* and scores how well the passage answers it. That is much more precise, but too slow to run over every passage. So: gather the top ~50 candidates from steps 1–4 cheaply, re-score them with the cross-encoder, and keep the best 12.

- Five of the real-set misses rank 13–30, just outside what is shown. That is the case this fixes.
- Candidates: `Xenova/ms-marco-MiniLM-L-6-v2` (about 80 MB) or `BAAI/bge-reranker-base` (larger, usually better). Both are available through `fastembed` (step 1a), so no second new dependency.
- Local and free. Measure the time: 50 pairs should take roughly 0.2–0.5 s on this machine, which is acceptable next to a reply that takes seconds. If it isn't, re-rank fewer candidates.
- For a long query (an entry reply), score each candidate against the query piece that retrieved it, not the whole entry. The cross-encoder has the same kind of length limit.
- Keep it behind a setting until the real set shows it helps.

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
- Query splitting searches the whole entry, up to 26 searches: twice the longest entry at the time. *At 120-token pieces that no longer held; the owner raised it to 64 on 2026-09-25 (see Next steps, item 3).*
- Step 5 is an option called Smart Search, off by default.

Decided on 2026-09-25:

- Passage size, provisionally: 120 tokens with 1 neighbour shown on each side, and 12 results. Re-run once the embedding model is chosen.
- Test newer embedding models (step 1a) before the size is final. Not fine-tuning: published models, used as they are.
- Step 3 becomes full keyword search (BM25), with word normalization and fuzzy matching for spelling, merged with search by meaning.
- Add a cross-encoder re-ranker (step 4b), behind a setting until it proves itself.
- The owner writes a small holdout set of their own questions for the final check.
- Adding `fastembed` as a dependency needs the owner's approval once it's shown to install cleanly. *Tested 2026-09-25: it installs cleanly. Approved the same day.*
- The embedding model is `snowflake/snowflake-arctic-embed-s`, via `fastembed`. Passages are 120 tokens with 1 neighbour shown, and 12 results. This replaces the provisional MiniLM choice.

If step 0's timing shows 26 searches slowing replies noticeably, bring the ceiling back to the owner rather than lowering it quietly.
