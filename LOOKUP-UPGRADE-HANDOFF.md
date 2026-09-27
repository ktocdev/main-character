# Handoff: make lookup find exact text, not just summaries

Written 2026-09-23. Step 0 done 2026-09-25 (results under step 0). Step 1 done 2026-09-25 on branch `JRNL-47`: results under steps 1 and 1a, and in "Step 1 finished". The passage index is in `cef2a55`, the switch to arctic-embed-s in `55445c8`, and the rest of step 1 in `5c9ed67`. **Where to pick up: [Next steps](#next-steps).**

## Next steps

As of 2026-09-25, stopped here:

1. **Decided 2026-09-25: switch to `snowflake/snowflake-arctic-embed-s`,** with 120-token passages and 1 neighbour. On the real set it finds 30/38 in the top 6, against 20 with MiniLM and 2 at baseline (step 1a results).
2. **Switch the model: done 2026-09-25** (see "The switch, as built" under step 1a). Through the real code path it finds 30/38 in the top 6 and 31 in the top 12, with MRR 0.628, which matches the comparison exactly. Entry replies are 3/8 in both the top 6 and top 12; MiniLM got 3/8 and 5/8.
3. **Finish step 1: done 2026-09-25** (see "Step 1 finished" under step 1a).
   - **The search ceiling is 64, decided by the owner 2026-09-25** (`passages.MAX_QUERY_PIECES`, was 26). It was sized for 254-token pieces, which add ~224 new tokens each, so 26 covered twice the longest entry. At 120 tokens each piece adds ~90, so 26 cover ~2,340 tokens. On the real journal, 5 of 395 entries need more (27–32 pieces, up to 2,824 tokens). At 26 those were searched end to end by 26 pieces spread evenly, with some text between them skipped. Measured on the copy, the longest chunk (11,697 characters, 31 pieces) takes 394 ms to search at 26 and 502 ms at 64, and its whole context block 1.3 s and 1.6 s. At 64 every entry is covered twice over, for about 0.1 s of search and 0.3 s of context block on the longest entries only.
4. **Step 1b** (the companion's prompt): built 2026-09-26 on branch `JRNL-48` (see "Step 1b as built"). Real replies checked the same day. Then **steps 2, 3, 4 and 4b**, measuring each with `eval_retrieval.py --compare`. Step 2 built 2026-09-26 (see "Step 2 as built"). Step 3 built 2026-09-26 on `JRNL-51` (see "Step 3 as built"); Step 4 built 2026-09-26 on `JRNL-53` (see "Step 4 as built"). Step 4b deferred by the owner 2026-09-26: after steps 3 and 4 every question's fact is in the top 12, which was what it was for.
5. **Opus 5.5** (side task): done 2026-09-26 on `JRNL-49`, voice comparison included (see "Opus 5.5 as built").
6. **Holdout.** The owner's own questions are in `docs/retrieval-eval/my-questions.txt` (gitignored, moved there 2026-09-26). Convert it to `holdout.json`. Only check that each quote is found in its entry. Don't read it for tuning, and run it only at the final check. *Done 2026-09-27 on `JRNL-54`* (see "Holdout and final check").
7. **Final check: done 2026-09-27** (see "Holdout and final check"). **Next: the real journal,** done by the owner: stop 8144, `python backup.py`, then `python rebuild_index.py` (builds the passage index; the keyword index needs nothing, it is built in memory on the first search). Then restart 8144; with no model set in `.env`, its companion becomes Opus 5.5.
   *Real journal moved 2026-09-27:* backed up (`backups/journal_backup_20260927_105317.zip`, 395 entries), `rebuild_index.py` (397 chunks, 4,117 passages embedded, 69 dream docs, 789 summary docs; 57 chunks marked `source: rebuild_index`, harmless because `sessions/current.json` exists), and 8144 restarted on Opus 5.5.
8. **Step 5** (Smart Search): built 2026-09-27 on `JRNL-56` (see "Step 5 as built"). Real replies checked by the owner the same day and judged very good; about $0.60 a question. **Possible next:** a cache breakpoint on the latest message in each round, to cut that cost. The "close chapter" side task was done 2026-09-26 on `JRNL-49`.

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
- **Fallback:** if the passage index is empty, search the old collection as it does today. Existing installs keep working until they rebuild. *As built: the fallback applies when the index is absent or was built by another model, and the write paths keep the index complete or absent (see "Step 1 finished").*

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

**Why the other 14 questions miss** (120-token index): 10 name a rare word that only 1–4 passages contain (a film title, a drink, a hobby, a substance). Search by meaning ranks those low, and step 3 targets them. 5 are ranked 13–30, just outside what is shown, which is what step 4b targets. 2 give a time ("around Christmas", "in March") with otherwise generic words, which step 4 targets. 2 have the word that ties question to answer in the passage just before, which step 3 plus neighbours targets. These groups overlap.

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
- **Split long queries.** When the query is longer than one passage (an entry reply, or the reflection query), split it with the same splitter, run one search per piece, then merge, remove duplicates and keep the best matches. Search the **whole** entry, not only its first few pieces: the 90th-percentile entry is about 6,000 characters, and its ending matters as much as its opening. The ceiling is **26 searches per query** (*raised to 64 on 2026-09-25, for 120-token pieces*), which covers an entry twice as long as the journal's longest on 2026-09-23 (11,777 characters, 2,824 tokens; each piece adds about 224 new tokens after the overlap). Every entry written so far is searched in full. If a longer one comes along, pick 26 pieces spread evenly across it so the ending is still searched. The ceiling applies separately to the passage search and to `get_summary_hits`. Do the same for `get_summary_hits`. This is local and free. There is no summary step and no extra Claude call: the reply prompt still contains the full entry, and only the choice of past passages changes. Without this, step 1 helps chat-screen questions but not entry replies.
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

Done when: the step 0 test shows a clear improvement on the deep-fact questions, the context viewer shows the deep passages on the sandbox copy of the real journal, the search time stays a small fraction of the reply time, the full test suite passes, and `tests/test_demo_close_replay.py` passes without re-recording. *All met on 2026-09-25; see "Step 1 finished".*

#### Step 1 finished (2026-09-25)

- **Defaults:** `PASSAGE_TOKENS` 120, `PASSAGE_NEIGHBORS` 1, `N_SEMANTIC` 12 (`config.py`, `.env.example`). The search ceiling, `MAX_QUERY_PIECES`, is 64 (Next steps, item 3).
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
  - A fresh demo build takes ~16 s with both models already downloaded, so the UI's "around twenty seconds" still holds.
  - Search tab: the answer's quote is visible in a snippet for 25/38 test questions, against 0/38 on the old path. It keeps a median of 7.5 related entries per question, against 12 on the old path, and takes ~390 ms against ~300 ms.
- **Tests:**
  - `tests/test_passages.py` (26), covering the splitter, turns, neighbour windows, fallback, each write-path rule, the close, the context block's dedup and trimming, `mirror` and the legacy search, dream entries and long dreams, and the search tab.
  - The demo installer's boundary test also checks `journal_passages`.
  - The accounting tests with fake collections stub `index_chunks`.
  - Full suite: 232 passed, 2 skipped. The demo close replay passes without re-recording.
- **Docs updated:** `README.md` (models, retrieval, dreams, bulk import, demo download), `HOW-IT-WORKS.md` (the models, the context block's layers and counts, the close steps, and a new search tab section), `.env.example` and `config.py` comments.
- **Not done here:** splitting long summary documents (the follow-up below); step 4b's re-ranker for entry replies.

### Step 1b: tell the companion how it works

The companion's prompt (`SYSTEM_PROMPT` in `companion.py`) only half explains where its knowledge comes from. The passage index changes what it receives, so fix the prompt at the same time. Found on 2026-09-23:

- **The opening contradicts the rest.** It calls the companion a friend "who has read every previous entry and remembers what matters", then says it only has retrieved excerpts. Keep the second idea: it doesn't remember everything, it knows where to look.
- **"The user's journal spans <a month> to the present" is hard-coded.** That is the owner's start date, wrong for the demo (Jordan's journal) and for anyone else. Fill it in from the journal's first entry date. The system prompt is cached, so the date must stay stable between turns; the first entry's date is.
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

#### Step 1b as built (2026-09-26)

- **`companion.py`:**
  - The opening no longer claims it "has read every previous entry": it knows the journal well, "not every line by heart, but where to look".
  - A **"How you work"** section: part of Main Character, run on the person's own computer; the write-tab conversation is a chapter that becomes memory when closed; the open chapter isn't searched yet; say so rather than invent; with only a summary or a date, point to the history tab (by date and title) or the search tab; and "use this, don't narrate it", with the March 12 example.
  - **`CONTEXT_SECTIONS`** names each section of the context block and says which are the person's words (`<recent_entries>`, `<related_history>`) and which the app wrote. Both prompts use it. `tests/test_prompts.py` fails if `build_context_block` gains a tag the list doesn't explain, so steps 2–5 can't forget.
  - "Quote their own language back" now adds: only their entries, never a summary or profile.
  - **The start date** comes from `journal_span(collection)`: the earliest date in the journal collection, filled into `{journal_span}` by `system_prompt()` each turn. It only changes when the first entry does, so the cache holds. An empty journal says nothing has been closed yet.
  - **`LOOKUP_PROMPT` and `stream_lookup`** for the chat tab: a finder, not the companion. Answer first with exact dates, quote only the person's own words, say plainly when it has only a summary and where to read the entry, suggest search words when nothing is found, mention that the open chapter isn't searched, and no commentary unless asked. It still gets the seed summary and the same context block. `/api/lookup` calls it.
  - `REFLECTION_REQUEST` says "the life summary" instead of "the snapshot". `SEED_PREAMBLE` says "across chapters".
  - Every turn still goes through `_stream_turn` (now with a `prompt` argument), so `mock_client` files lookups under `companion._stream_turn` as before. Its canned replies are picked by the message text, not the system prompt, and the web demo picks its own, so the demo is unchanged. None of the recorded close calls use these prompts.
- **Tests:** `tests/test_prompts.py` (7): the span from the first date, an empty journal, no hard-coded date, every context tag explained, no "snapshot", the chat tab's prompt, the companion's persona. The two lookup tests in `test_settings.py` patch `stream_lookup`.
- **Docs:** `HOW-IT-WORKS.md` ("What the companion sees" and "The chat screen").
- **Real replies (layer 3), 2026-09-26,** on the sandbox copy of the real journal, Opus 4.6, $1.05 for 11 calls: the reflection, six chat-tab questions (r07, r10, r16 found; r03, r32, r38 missed), "the last few days" on the chat tab, r17 as a write-tab chat message, and entry replies e02 and e01. The sample leans on misses on purpose, and there was no before run, so it checks behaviour, not improvement.
  - Quotes (r07, r10, r16, and a July entry) are word for word. No summary was quoted as the person's words.
  - Nothing found (r32, r17): said so plainly and suggested the search tab. These are step 3's misses.
  - The chat tab answered like a finder; "the last few days" said the three newest days might be in the open chapter, which was right.
  - Entry replies and the reflection stayed in the companion's voice without mentioning the app. Neither entry reply reached its link (search, not the prompt: step 4b).
  - **Two fixes after the run:** r03 gave the date and details from a summary without saying so or where to read it, so the chat prompt now says to always make clear whether it's quoting or going from a summary. The rerun of r03 pointed to History; r38 then called the person "she", copied from the app's third-person summaries, so `CONTEXT_SECTIONS` now says to always talk to them as "you". That last fix hasn't been rerun.

### Step 2: go from a summary to the entry it summarizes

In `companion.get_summary_hits`, when a hit's level is `entry summary`, its metadata already carries `date` and `title`. Query the passage index again, limited to that entry (`where={"date": ..., "title": ...}`), for the one or two passages that best match the question, and add them under the summary. A summary hit then brings the actual text with it.

#### Step 2 as built (2026-09-26, branch `JRNL-49`)

- **`companion.get_summary_hits`** returns `(level, text, passages)`. For an entry summary it runs `passages.search` with `where` on the summary's `date` and `title`, for `SUMMARY_PASSAGES` (1) hit with its neighbours.
- **`build_context_block`** shows that passage under the summary, headed "From the entry itself, in their words: [date] title", unless it overlaps a span already shown in recent entries or related history (tracked as `shown_spans`).
- **The prompt:** `CONTEXT_SECTIONS` describes the new passage and counts it among the person's own words; the chat prompt's quote rule names it too.
- **`eval_retrieval.py`** gains "in block": whether the fact is anywhere in the whole context block, plus the block's size, so a layer other than related history can be measured. The before run is `real-step2-before.json`, the after `real-step2.json`.
- **Tests:** two in `tests/test_passages.py` (a summary brings its entry's words; a passage already shown isn't repeated).

Measured on the sandbox copy of the real journal:

| | before | after |
|---|---|---|
| questions: fact in the block | 31/38 | 31/38 |
| entry replies: fact in the block | 4/8 | 4/8 |
| entry summaries shown with none of their entry's words (56 shown over the 46 tests) | 35 | 0 |
| context block, median | 29,700 / 39,200 chars | 30,200 / 41,100 chars |
| context block time, median | 68 / 193 ms | 105 / 376 ms |

(Pairs are questions / entry replies.)

- **It does what it was for:** a summary no longer arrives without the entry's words. 35 of the 56 entry summaries had none before; all 35 now carry about 1,000 characters of the entry.
- **It doesn't reach the eval's misses,** because the right entry's summary almost never ranks in the top 3. For 7 of the 11 misses it isn't in the top 40. The questions ask about details (a film title, a substance, a nickname) that a summary leaves out. When the entry *is* known, a search inside it finds the fact at rank 1 or 2 for r03, r17, r32, r38 and e01, so the gap is finding the entry, which is what steps 3 (rare words) and 4 (dates) are for.
- **Cost:** about 200 more tokens per turn on average, and up to three more searches (~40 ms for a question, ~180 ms for an entry reply). Small next to a reply.

### Step 3: keyword search alongside search by meaning

Search by meaning is weak on names, numbers and rare words, and 10 of the 14 questions step 1 misses name such a word. *Revised 2026-09-25:* the original plan looked up only quoted phrases, capitalized words and entity names. Many of the misses are ordinary rare words (a hobby, a craft, a substance), so this is now full keyword search:

- **Rank every passage by keyword relevance with BM25,** the standard scoring behind most search engines. It rewards a passage for containing the question's words, and rare words count for much more than common ones. Build it over the passage index's text. At about 4,000 passages it fits in memory and scores in milliseconds; rebuild it when the passage index changes. The `rank_bm25` package, or a short implementation, both work. Drop stopwords.
- **Normalize words the same way on both sides:** lowercase, fold accents (reuse `_fold` from `server.py`), drop possessive `'s`, and reduce plurals and simple verb endings, so "Robin's" matches "robin" and "lanterns" matches "lantern".
- **Fuzzy matching for spelling.** The journal has typos and variant spellings (misspelled names and medical terms), and a question won't spell them the same way. For a question word with no exact match in the vocabulary, also match words within a small edit distance, or with high character-trigram overlap, at a lower weight. Only for words of 5 or more letters, so short words don't match everything.
- **Merge the two lists by taking turns,** the same way long-query pieces merge. Reciprocal rank fusion is the standard form: a passage's score is the sum of 1/(60 + rank) over the lists it appears in. The rank is what counts, not the raw score, because BM25 scores and distances aren't on the same scale.
- **Long queries:** run BM25 on each query piece, like the vector search, so an entry's ending gets its own keyword matches.
- Known entity names and aliases (`entity_index`) are a cheap extra: a question word that matches an alias can be expanded to the entity's name.

Measure on the real set, with and without fuzzy matching, and check that the short-question hits from step 1 don't drop.

#### Step 3 as built (2026-09-26, branch `JRNL-51`)

- **`keywords.py`** (new, no new dependency): BM25 over the passage index's text, built in memory on the first search (0.6 s for the real journal's 4,117 passages and 8,589 distinct words) and rebuilt after any write to the passage index (`passages._upsert`, `_delete_sources`, `drop`, `mirror` call `keywords.invalidate()`), or when the collection's size changes (a rebuild in another process). About 1 ms per query.
  - Words: lowercased, accents folded (`keywords.fold`, which `server.py` now imports in place of its own `_fold`), possessive `'s` dropped, a crude stemmer for plurals, `-ing`, `-ed` and a final `e`, and English stopwords removed.
  - A word in more than 20% of passages is skipped (`MAX_DF_SHARE`; never in a journal under 500 passages, `MIN_DF_CUT`).
  - Fuzzy: a question word of 5+ letters that the journal never uses matches journal words within 1 edit (2 for 8+ letters, a swap of neighbours counting as one), at half weight. Candidates come from shared trigrams.
  - **A floor** (`FLOOR`, 0.25): keyword hits scoring under a quarter of the best are left off the list. Without it, a question word that is in many passages (a person's name) gives each of them a keyword rank, and in the merge a rank counts the same whatever the score behind it, so a dozen passages that only share the name outvote the one with the rare word. `test_a_rare_word_finds_its_passage_among_many_alike` fails without it.
  - `where` filters are applied in Python (`keywords.matches`: `$and`, `$or`, `$eq`, `$ne`, `$in`, `$nin`), so step 2's search inside one entry gets keywords too. Any other operator falls back to meaning only.
- **`passages.fused`**, used by `passages.search` (so by `query_journal` and step 2): for each query piece, the best `FUSION_DEPTH` (30) by meaning and by keyword, merged by reciprocal rank fusion (k = 60), then the pieces take turns as before (`_take_turns`, shared with `ranked`). A passage found only by keyword gets its cosine distance to the piece from its stored embedding. `passages.KEYWORDS` turns it off.
- **Unchanged:** `passages.ranked` and so the search tab, which relies on distances; `search_documents` (summaries and dreams), meaning only.
- **`eval_retrieval.py`** gains `--no-keywords` and `--no-fuzzy`.
- **Tests:** `tests/test_keywords.py` (normalization, rare words, fuzzy, short words, filters) and four in `tests/test_passages.py` (a rare word the meaning search ranks last, a keyword-only hit's distance, the index following writes, a filter). Example words in code and tests are made up, not from the journal.

Measured on the sandbox copy of the real journal (`real-step3.json`, compared with `real-step2.json`):

| | step 2 | step 3 |
|---|---|---|
| questions: fact in top 6 / top 12 | 30 / 31 of 38 | 35 / 37 of 38 |
| questions: fact in the block | 31/38 | 37/38 |
| questions: MRR | 0.628 | 0.775 |
| entry replies: fact in top 6 / top 12 | 3 / 3 of 8 | 6 / 6 of 8 |
| entry replies: fact in the block | 4/8 | 7/8 |
| search alone, median | 24 / 81 ms | 27 / 124 ms |
| context block time, median | 105 / 376 ms | 110 / 489 ms |
| context block, median | 30,200 / 41,100 chars | 30,800 / 39,200 chars |

(Pairs are questions / entry replies.)

- Found now: r01, r02, r03, r17, r32, r38 and e01, e05, e06. No earlier hit was lost; r08, r23 and r35 moved up.
- Still missed: **r11** (needs the month: step 4), **e07**, and **e04** in the block but not in related history.
- **Fuzzy matching changes nothing on this set** (`real-step3-nofuzzy.json`, run before the floor: identical counts). r17's misspelled word is found through the question's other words. It's kept, since it costs nothing when every question word is in the journal, but it's unproven.
- **The floor** changed nothing on the counts (`real-step3-nofloor.json`); r35 moved from 2 to 1, e03 from 2 to 3.
- `--no-keywords` reproduces step 2 exactly.
- Not done: expanding entity aliases (the plan's "cheap extra"). None of the remaining misses needs it.

### Step 4: date filters

When a question names a month, a date or a range ("in March", "last summer", "2026-04-12"), limit the passage search to entries in that range. Dates are stored as `YYYY-MM-DD` strings. Use a `$in` filter over the dates that exist in the range, or filter in Python. Start with explicit dates and month names, and add relative phrases later.

Two of the real-set misses are this case: "around Christmas" and "in March", with otherwise generic words. Seasons and holidays ("Christmas", "last summer", "Labor Day") map to ranges. When the question names a range, keep searching outside it too, at a lower weight, so a wrong guess about the range doesn't hide the answer.

#### Step 4 as built (2026-09-26, branch `JRNL-53`)

- **`timeframe.py`** (new) reads the time a question names: an ISO date or month, a month and day either way round (with or without a year), a month (with or without a year), a year after a word like "in", holidays (Christmas, New Year's, Thanksgiving, Halloween, Valentine's, Easter, Labor Day, Memorial Day, the Fourth of July; each a window of a few days around it) and seasons. Without a year it means every year the journal has; "last" picks the latest that has ended and "this" the current one. Words that are also ordinary ("may", "march", "august", "fall", "spring") count only with a clue: a day or year after, or a cue word before ("in", "early", "last"…, past a "the"). `dates_within` turns the ranges into the journal dates inside them. Relative phrases ("last week", "three days ago") aren't read yet.
- **`passages.fused`:** when a question names a time, the search by meaning and the keyword search run again within that time's dates (`$in`), and those two lists join the reciprocal rank fusion. A passage from then can be on four lists, and one from any other time on two, so the time counts for a lot without hiding the rest. Only for a one-piece query with no filter of its own: whole entries (entry replies) name dates in passing. `passages.DATES` turns it off; the journal's dates come from the keyword index (`keywords.Index.dates`).
- **`eval_retrieval.py`** gains `--no-dates`.
- **Tests:** `tests/test_timeframe.py` (ranges, holidays, everyday words, clues, the journal's dates) and three in `tests/test_passages.py` (a month brings its entry forward, a month with no entries changes nothing, a whole entry isn't read for dates).
- **Not changed:** the companion's prompts. The context block has the same sections; the dates only change which passages are in it.

Measured on the sandbox copy (`real-step4.json`, compared with `real-step3.json`): questions 38/38 in the top 12 and in the block (was 37/38), 36/38 in the top 6 (was 35), MRR 0.778 (was 0.775). r11 is found at rank 10; r01 moved from 7 to 6; no other question changed. Entry replies are unchanged (6/8 in the top 12, 7/8 in the block), as they should be. Times are unchanged (search ~29 ms for a question).

Only two questions in the real set name a time, so this measures little; the holdout will say more if the owner's own questions use dates.

### Step 4b: re-rank the candidates

*Deferred by the owner 2026-09-26.* It was for facts ranked 13–30; after steps 3 and 4 every question's fact is in the top 12 (`real-step4.json`). Worth another look if the holdout shows near misses.

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

#### Step 5 as built (2026-09-27, branch `JRNL-56`)

*Renamed by the owner the same day:* on screen it is **smart replies**, since "search" on the chat tab was confusing next to the search tab. Code names followed (`SMART_REPLY_ROUNDS`, `SMART_REPLY_PROMPT`, `tests/test_smart_replies.py`); the step keeps its name here. The chat and search tabs each got a short description at the top (`.tab-intro`) saying what the screen does, whether it calls Claude, and pointing to the other.

- **Toggle, not a setting (owner, 2026-09-27):** a toggle chip by send (now **smart replies**) on the chat tab, off by default, remembered in the browser (`localStorage` `rag_lookup_smart`), its tooltip saying it can take several calls, costs more and takes longer. It is sent with each question (`POST /api/lookup {message, smart}`), so it takes effect at once, no restart, and can be switched mid-conversation: a plain turn after a Smart Search one is accepted with the tool blocks in its history (checked with `count_tokens`). The cap is `config.SMART_REPLY_ROUNDS = 5` calls per question. A first version put it in Settings as `MC_SMART_SEARCH`; that was taken out.
- **Tools** (`lookup_tools.py`, all local): `search_journal(query, mode, date_from, date_to)`, where `meaning` is `passages.search` (keywords and neighbours included) with the range as a `$in` filter on the journal's own dates, and `exact` is every entry holding the words, newest first, with counts and a snippet (25 listed); `read_entry(date, title)`, the whole entry through `sessions.conversation_text`, cut at 40,000 characters and said so, with the day's titles listed when the title doesn't match; `list_entries(date_from, date_to)`, oldest first with lengths, up to 200 rows. A bad date or unknown tool comes back as an `is_error` result the model can read.
- **Loop** (`companion.stream_smart_lookup`): the first user turn is the same context block as the plain lookup, so a question the first search answers still takes one call. The system prompt is `LOOKUP_PROMPT` plus `SMART_REPLY_PROMPT` as its own block (the cache breakpoint moves to it). Up to 5 streamed calls; calls 1–4 have `tool_choice: auto`, the 5th `none`, so it has to answer (Opus 5.5 refuses forced tool choice, `none` is allowed). Text streams as it arrives; on Opus 5.5 text between tool calls comes back as thinking, so the reply is normally only the answer. Every round goes into the lookup history as it came back (thinking and tool blocks included, append-only for preserved thinking and the cache); a failed round, a dropped connection included, takes the history back to before the question.
- **Metering:** the loop is in `companion`, so every call is on the companion bucket and goes through `get_client()`'s caps. A cap reached mid-loop stops it like any other refused call.
- **Demo:** the route runs Smart Search only when `smart` is true and it isn't mock mode, so the demo and the seed instance keep one-search lookup; the toggle is hidden whenever `/api/status` says `mock`, which the web demo's backend does too.
- **Checked:** the request shape (tools, `tool_choice` auto and none, adaptive thinking, a second round with a tool result) accepted by the API through `count_tokens` on `claude-opus-5-5`, which is free. **Real replies, 2026-09-27:** the owner asked two questions on the real journal with smart replies on and judged both answers very good ("this is what i wanted all along"). Their shapes, with made-up stand-ins: a count of how often a named person said something ("how many times did Robin say she was tired?"), and a superlative over the whole journal ("when was my worst migraine?"), the kind one search can't answer. The owner also asked h06, which one search now misses (see "Holdout and final check"), and it came back right. Cost: 8 companion calls, $1.20 on Opus 5.5 for the session, about $0.60 a question. Most of it was uncached input (269k tokens, against 61k read from cache): the cache breakpoint is on the system prompt only, so each round resends the context block and earlier tool results at full price.
- **Tests:** `tests/test_smart_replies.py` (10): the tools on a small journal, the loop against a scripted client, and the route's choice of turn (the toggle, and the mock guard).
- **Docs:** `HOW-IT-WORKS.md` (the chat screen), the help tab's chat section.

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

### Side task: Opus 5.5 in Settings, and the companion's default

Added 2026-09-26 at the owner's request. Not part of the search work; it can be merged on its own, but do it before the holdout and final check (Next steps, item 5).

Facts to build on (Claude API reference as of 2026-09-26; check Anthropic's pricing and models pages when doing it, as `config.py` asks):

- Model id `claude-opus-5-5`, label "Opus 5.5". $4 in / $20 out per million tokens (cache reads $0.20), against $5 / $25 for Opus 4.6. It was marked "launching": before switching the default, confirm the owner's key can call it (one real call, or `client.models.retrieve`).
- Effort levels: `low`, `medium`, `high`, `xhigh`, `max`. Its default when omitted is `medium`, not `high`, but the app always sends `MC_COMPANION_EFFORT` (default `high`), so that doesn't change anything here.
- **Thinking can't be turned off.** `{"type": "disabled"}` returns a 400 at every effort level; omitting `thinking` runs adaptive.
- Tokenizer: the one introduced with Opus 4.7. The same text is about 1–1.35× as many tokens as on Opus 4.6, so a reply costs less than 4.6's, but by less than the price cut suggests.

What to change:

1. **`config.py`:** add `claude-opus-5-5` to `MODEL_EFFORT_LEVELS` (all five), `MODEL_LABELS`, and `MODEL_PRICES`. Change the default `MC_COMPANION_MODEL` to `claude-opus-5-5`, and `.env.example` with it.
2. **The trap: processing calls.** `processing_thinking_kwargs()` sends `{"type": "disabled"}` to every model whose `MODEL_THINKING_SUPPORT` is True. Anyone who picks Opus 5.5 as the *processing* model would get a 400 on every close step (seed, categories, entities, summaries, dreams), the chat title and patterns. A plain True/False can't say "always on", so give the map a third state (for example `"always"`) that processing omits the parameter for. The tight `max_tokens` sites the release plan lists (`sessions.py` title at 30, `summarizer.py` at 400, 2,000 elsewhere) then have thinking eating into them: either raise those limits for such models or keep Opus 5.5 out of the processing picker. **Decided by the owner 2026-09-26: companion only.** Keep Opus 5.5 out of the processing picker for now, and have the settings route refuse it for `MC_PROCESSING_MODEL` (a hand-edited `.env` too), so the 400 can't be reached. The third thinking state is still worth adding so the map is truthful, but processing never sees it yet. Check `server.py`'s settings route (it sends `thinking` support to the page, line ~1168) and the Settings page for how they read the map.
3. **Settings:** the pickers are built from `MODEL_EFFORT_LEVELS` and `MODEL_LABELS`, so the new model should appear with its effort levels on its own. Check the companion picker's warning copy and that an existing `.env` with `MC_COMPANION_MODEL` set keeps its choice (only the default changes, so the owner's real journal stays on whatever its `.env` says until changed in Settings).
4. **Docs:** `README.md` / `HOW-IT-WORKS.md` wherever the companion model is named, and `docs/releasing/release-plan.md` Phase 0 items 6–8 (the default and why, the model lineup, and the thinking and effort tables).
5. **While there:** `MODEL_PRICES` has Sonnet 5 at $3 / $15; the reference used above lists $2 / $10. Check it on the pricing page and fix it if stale, since the cost meter and the caps are computed from these rows.

Nothing here touches the demo: `mock_client.request_fingerprint` leaves the model out, and the recorded close calls don't depend on it. Run the full suite and `tests/test_demo_close_replay.py` anyway.

Checking: the default was Opus 4.6 on purpose ("it reads best as the companion", release plan item 6), so this changes the voice. Tests can't judge that. On the sandbox copy with a key, send the same few real-reply requests as step 1b's check (`%TEMP%
t1b.py` did this; it's scratch, so recreate it if it's gone), once on 4.6 and once on 5.5, and let the owner compare the replies side by side before the default changes. Roughly $1 per set.

#### Chapter rename as built (2026-09-26)

- **Buttons:** the write tab's ⋯ menu says **close chapter**; history's button says **close chapter & start the next**. Both tooltips keep "this is the summarize point".
- **Other interface text:** the seed banner and seed editor, the status tooltip, the close confirm and its notes, the first-run note, the demo build and wizard notes, the spend-cap help in Settings, history's "new chapter" / "current chapter", and the help tab (write, chat, search, history, categories and the entities habit). "Write or chat first" became "Write or send something first", on the server (`sessions.close_session`) and in the web demo's `backend.js`.
- **Docs:** `README.md` and `HOW-IT-WORKS.md` ("When you close a chapter").
- **Left as they were:** the chat tab and "chat screen"; code, routes, comments; the close pipeline's prompts, including the title prompt (`sessions.py`, recorded in `mock_fixtures/demo_close/`); the fallback title `Journal chat {date}` (a stored title); `(picking our chat back up)`, which only the model sees.
- No test asserted on the old strings. Full suite passes.

#### Opus 5.5 as built (2026-09-26)

- **`config.py`:** `claude-opus-5-5` added (label "Opus 5.5", all five effort levels, $4 / $20, cache reads at 0.05× input) and made the `MC_COMPANION_MODEL` default. `MODEL_THINKING_SUPPORT` gains `"always"` for it, and `processing_thinking_kwargs` leaves the parameter out for such a model instead of sending a disabled that would 400. `metering.price` reads an optional per-model `cache_read` ratio.
- **Processing is Sonnet 5 and Haiku 4.5 only** (owner, 2026-09-26: no Opus models there for now). `config.PROCESSING_MODELS` holds the list; the settings payload marks each model `processing`, the Settings page builds the processing picker from those, and saving an Opus processing model is refused. A `.env` that already names one locks the picker (as any unlisted value does) and keeps working.
- **Sonnet 5's price corrected to $2 / $10.** Anthropic's pricing page says the introductory price became the standard one and the $3 / $15 increase won't happen.
- **Your real journal switches too.** Its `.env` sets no model, so 8144 runs the defaults: after this merges and 8144 restarts, the companion is Opus 5.5. To stay on 4.6, set it in Settings (or `MC_COMPANION_MODEL=claude-opus-4-6`).
- **Tests:** the processing lineup and its refusal, Opus 5.5 as a companion model, its cache price, and its processing thinking kwarg. Docs: `.env.example`, `README.md`, `HOW-IT-WORKS.md`, and a dated note in `docs/releasing/release-plan.md` item 6 (local; `docs/` is untracked).
- **Voice comparison, 2026-09-26:** the reflection, entry replies e02 and e01, the r17 chat message, and lookups r07, r38 and "the last few days", on the sandbox copy with its open chapter trimmed to before step 1b's test turns, once per model, in-process (so neither run saw the other's replies). The owner's key reaches Opus 5.5.
  - **Cost:** 5.5 was 13% *more* ($0.60 against $0.54 for the seven calls): 36% more tokens, from its tokenizer and longer replies. Don't expect the lower price to lower the bill.
  - **5.5 better:** it found the links (e02 reached an April visit to friends, e01 a January garden problem); it said exactly when it was going from a summary and quoted earlier entries with dates and titles (r38); its misses were more useful (search suggestions, nearest things it did have). 4.6 slipped a banned phrase ("That's not nothing — wait, I mean…").
  - **4.6 better:** shorter, lighter entry replies. 5.5's e01 went through nearly every item in six paragraphs, against the persona's "react to one detail, skip the rest", and it ended every write-tab reply with a question.
  - **Outcome:** Opus 5.5 kept as the default. Watch entry-reply length in real use; if it drags, try a persona line or `medium` effort (neither tested).

### Side task: ask to close a long chapter (2026-09-27, branch `JRNL-54`)

Asked for by the owner before step 5. The open chapter is resent whole with every reply and can't be searched until it's closed, so a long one is worth closing. Owner's decisions: count the author's own writing only (what a close turns into entries, dreams aside), ask at 30,000 characters (a little over the median of the 8 chapters closed by hand, 27k), and after "not yet" ask again once it has grown by 10,000 more.

- **Server:** `config.CHAPTER_CLOSE_CHARS` (`MC_CHAPTER_CLOSE_CHARS`, blank for the default, 0 never asks); `sessions.close_prompt()` and `decline_close()`, the decline kept in `current.json` as `close_declined_at`, so it survives a restart and ends with the chapter. `/api/sessions/current` carries `close_prompt`; `POST /api/sessions/close/not-yet` records the decline.
- **Write tab:** after an entry is saved or a message sent, `askToCloseIfLong()` asks through the close's own confirm, opening with "Your chapter has grown long enough to close and process. Are you ready?", so there is one dialog. OK closes; Cancel is "not yet". History's close button now calls `closeSession()` without passing its click event.
- **Settings:** "Ask to close a chapter at", in the Journal group, validated as a whole number (commas allowed).
- **Docs:** the help tab's write section and `.env.example`. The web demo never asks (its backend sends no `close_prompt`).
- **Tests:** `tests/test_chapter_close_prompt.py` (6).

## Holdout and final check (2026-09-27, branch `JRNL-54`)

- **`holdout.json`** (local, gitignored): the owner's 10 questions from `my-questions.txt`, word for word, as h01–h10. Each fact is a short quote from the entry the owner's answer names, found by the answer's date and title; `eval_retrieval.py` checks each is in an entry of its date. The set is marked `"deep": false`, so a fact may sit anywhere in its entry (the owner asked what they asked, wherever the answer is); the eval skips the 1,000-character rule for such a set. Nothing was tuned against it.
- **Results** on the sandbox copy (`holdout-final.json`, compared with `holdout-step2.json`, the same code with `--no-keywords --no-dates`):

| | steps 1–2 | final |
|---|---|---|
| fact in the top 6 | 5/10 | 6/10 |
| fact in the top 12 (and in the block) | 6/10 | 8/10 |
| MRR | 0.316 | 0.449 |
| search, median | 27 ms | 31 ms |

- Found now: h04, h09, h10. Moved up: h03 (5 to 2), h05 (2 to 1), h08 (3 to 2).
- **Lost: h06** (rank 8 before, 20 now). Its only informative keyword is a person's name (the other words are in over 20% of passages), so the keyword list is that person's passages, where the fact ranks 36th; merged at equal weight they push the meaning search's hit out. A known cost of rank fusion. Not tuned here, since that would spoil the holdout: to work on it, add questions of that shape (a name plus common words) to `real.json` and tune there, for example by weighting a keyword list by how much its words say.
  - *With smart replies (2026-09-27):* the owner asked h06 on the real journal with smart replies on and got the right answer. The ranking above is unchanged, so the one-search chat still misses it; smart replies get there by searching again, for example by the name with exact search.
- **Missed both times: h07,** a "when did I first..." question: meaning ranks the fact 30th, keywords 16th. Order in time is what step 5 (Smart Search) is for.
- The real set's final numbers are step 4's (`real-step4.json`): 38/38 questions and 7/8 entry replies with the fact in the block.

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

**Keep the journal out of the repo.** The test sets quote the real journal, and it is easy to copy a word from them into something that gets committed. In code, tests, comments and committed docs (this handoff included), examples are made up ("Robin", "Quillon"), and results name tests by id (r17, e04), never by what they ask about. `scripts/check_leaks.sh` checks for the owner's people (`third-party-names.txt`) and for other journal words (`private-terms.txt`, from `python scripts/gen_private_terms.py`); both lists are gitignored, so only a local run checks them. `bash scripts/install_hooks.sh` makes it run before every push. Run it before committing too.

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

If the timing ever shows the search ceiling (64 searches) slowing replies noticeably, bring it back to the owner rather than lowering it quietly.
