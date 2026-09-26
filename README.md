# Main Character

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

**A journal that knows where to look.**

You write an entry. Something you wrote three months ago is relevant, and the journal finds it. You never tagged it. The journal went and looked.

Everything else in this project supports that one feature. Before the companion answers, it searches in three ways: your entries, by meaning rather than keyword; a separate index of summaries (entries, weeks, life areas, and the people and places in them), so a question can land on a whole stretch of time as well as a sentence; and anyone you name, by name, so their history comes with them. When it only has the summary, it can still tell you when you wrote something, so you can read it in full in History. [How it works](HOW-IT-WORKS.md) walks through what runs when you write, close a chat or ask a question.

![The history tab, showing a chat braid with its entry summary and the entries it continued](assets/mc-history.png)

## Start here

Clone it, give it an API key, and start writing. The journal builds itself from your entries, starting with the first one.

If you have a Claude conversation export, you can import it as a starting corpus. See [Importing a Claude export](#importing-a-claude-export). Most people will not have one, and the journal works fine without it.

### 1. Install

```bash
git clone https://github.com/ktocdev/main-character.git
cd main-character
./journal install                            # Windows: .\journal install
```

This creates `.venv`, installs from the lock file and copies `.env.example` to `.env`. It is safe to run again. Python 3.12 or newer. The install is large and slow because it pulls in ChromaDB's full dependency tree, including onnxruntime and tokenizers, plus fastembed, so that embeddings run on your machine instead of over the network. Install from the lock file. `requirements.txt` lists the same dependencies unpinned, for reference only.

### 2. Add a key

Uncomment `ANTHROPIC_API_KEY` in `.env` and paste a key from the [Anthropic Console](https://console.anthropic.com/). Everything else in that file has a working default.

### 3. Run it

```bash
./journal start                              # Windows: .\journal start
```

The journal opens at **http://127.0.0.1:8144**. Write something in the box and press *save entry*.

Stop it with `Ctrl+C`. After pulling a new version, run `./journal restart` (Windows: `.\journal restart`) so the running journal picks it up.

To restart, use `journal restart` instead of `Ctrl+C` and starting again. `Ctrl+C` stops the journal at once, even while a companion reply or the background work that follows an entry is still running, and that work is lost after you have already paid for it. `journal restart` refuses until nothing is running, so you can try again a moment later without losing it.

## Try it without a key

A demo journal ships with the repo: seven weeks of a fictional life, with the companion's replies pre-recorded. You can see how the app works before deciding whether to pay for it.

It opens mid-week, three days after the author last closed a chat. You can read the week so far, then press **summarize & close chat** to watch the memory pipeline run. Each trip into the demo starts from that same point, so anything you write there lasts only until you leave.

```bash
./journal start                              # Windows: .\journal start
```

Then open **Settings → load demo journal**, or choose the demo on the first-run screen if you have not added a key yet. The first visit builds the demo's search index, which takes about twenty seconds, or a few minutes if the embedding models still have to download.

The demo costs nothing because embeddings are computed locally and the replies are real Claude output, captured once and committed to this repo. Nothing calls the API. In demo mode the Anthropic SDK is never constructed, and the test suite checks this.

The demo is a separate journal with its own data directories. Nothing you type there can reach your own entries, and a restart returns you to your own journal. A banner stays visible the whole time you are in the demo.

The same demo also builds as a static site that runs entirely in the browser, with no server behind it. `python scripts/build_web_demo.py` writes it to `dist/web-demo/`. The UI is the real one, and its API calls are answered from reads captured before and after the recorded close. The other tabs are read-only, and a reload starts it over.

## What it costs

- **Reading, searching and browsing are free.** Embeddings are computed on your machine by two small local models. Search by meaning, for the companion and in the search tab, uses snowflake-arctic-embed-s, over entries split into short passages so that every part of a long entry can be found; summaries and dreams use it too. The journal's own index of whole entries uses all-MiniLM-L6-v2. Search never calls an API.
- **Writing costs money.** Each entry gets the companion's reply. Closing a chat triggers a background pass that tags what you wrote, extracts entities and updates summaries, so a close costs more than any one reply.
- **Two models, so you can trade down.** The companion is the voice you read, and it defaults to Opus. Background processing is mechanical, runs in bulk, and uses most of the tokens. It defaults to Sonnet. Both can be changed in Settings.
- **Two spend caps,** one per session and one per calendar month, checked before each call. They exist to catch runaway spending, not to set a budget, so the defaults sit above what a heavy month of ordinary writing would cost.
- **Set a limit in the Anthropic Console too.** Every figure this app shows is an estimate from a hand-maintained price table, and the caps are only as reliable as the code that enforces them. A [spend limit on your Anthropic account](https://console.anthropic.com/settings/limits) holds even if this app's accounting is wrong.

## Your writing is yours

Everything lives in files on your disk. Three commands, also available under Settings → Data, work without a key:

- `python export.py` writes the whole journal to a folder: every entry as markdown, one `entries.json` containing all of them, and everything the pipeline inferred. You do not need this app to read any of it.
- `python backup.py` writes the same export as a single dated zip. The zip lands next to the journal on the same disk, so copy it somewhere that will outlive the machine.
- `python rebuild_index.py` rebuilds the search index from the markdown, for free. The first time, it downloads the search model (about 130 MB, once per machine, to `~/.cache/main-character`). After that it works offline. For a journal of a few hundred entries it takes a few minutes. The export leaves the index out because the index can be rebuilt from what the export already contains.

Where it all sits:

| Directory | What |
|---|---|
| `journal_entries/` | Your entries and dreams, as markdown |
| `chroma_data/` | The search index. Derived and rebuildable |
| `entity_graph/` | Extracted people, projects and places, with profiles |
| `summaries/` | Entry summaries, weekly arcs, domain docs, the seed summary |
| `categories/`, `patterns/`, `dreams/`, `sessions/` | The rest of the pipeline's output |

All of these directories are gitignored. Nothing leaves your machine except the prompt text sent to the Anthropic API when you write.

## What it is not

**Single-user, local-only, and no authentication. This is deliberate.**

The server binds to `127.0.0.1` and refuses requests from anywhere else. There are no accounts, no login and no permission model, because there is exactly one user: you.

This will not change in a later version. The server keeps its open conversation, its loaded entity index and its Chroma handle in one process-global dict (`STATE`, [`server.py:80`](server.py#L80)). A second person on the same instance would not get their own journal. They would get *yours*, mid-sentence. So:

- Do not expose the port to the internet.
- Do not put it behind a reverse proxy and share the URL.
- Do not run it for more than one person. A multi-user deployment is unsafe.

If you want a journal several people can use, start from a different codebase.

## What it does

<details>
<summary><b>The full feature list</b></summary>

### Memory
- **Semantic retrieval.** Context is assembled in layers: recent entries and the seed summary first, then semantically matched chunks, matching summaries, entity docs, then the pattern library.
- **Sessions.** One chat stays open for days. Entries, replies and follow-ups braid into it and survive restarts. Closing the session triggers summarization: your side becomes a journal entry, the braid is archived, and the memory pipeline runs in the background.
- **Reflection.** The companion opens a conversation by connecting threads across your history instead of waiting to be asked.

### Entity graph
- **Extraction.** People, projects, places and events, pulled from every entry.
- **Profiles.** A one to three paragraph doc per entity, regenerated as context accumulates.
- **Observations.** Timestamped and tagged, browseable per entity by date.
- **Groups.** Nestable hand-made groupings. A group can roll up so its members collapse out of the flat list.
- **Triage.** A keyboard-driven review queue for extracted entities (keep, merge, correct, rename, alias, retype, delete) with 50 levels of undo.

### Summaries
- **Entry summaries.** Two or three sentences per entry, cached incrementally.
- **Weekly arcs.** A short narrative per week, stitched from entry summaries.
- **Domain summaries.** A roughly 500-word doc per category, updated over time.
- **Seed summary.** A rolling life summary you co-edit, which opens every chat. Each close drafts an update for you to review. It never replaces yours on its own.

### Categories
- **Automatic tagging** against ten built-in categories. Each can be turned off.
- **Organic categories.** Place and project entities clustered by embedding, named by Claude, and confirmed or dismissed by you.
- **Custom categories,** hand-seeded with trigger keywords.
- **Parent rollup.** Categories can nest.

### Patterns
Recurring emotional cycles, behavioural pipelines and relationship dynamics, each tracked with dated instances and a confidence score. Dismissed patterns resurface only with new evidence. The companion sees the pattern library on every turn, one line per pattern, and is told to bring one up only when the conversation genuinely echoes it.

### Dreams
- **Realm isolation.** Dreams live in their own vector collection, so a waking query can never surface one by accident.
- **Extraction and flagging.** Dreams are found in the journal automatically, or you can mark one with a checkbox as you write.
- **Cast.** Dream people and places are spelled to match the waking entity graph.
- **Dream weather.** A one-line tone signal from recent dreams, included in the companion's context.
- **Interpretations.** Your own interpretations are recorded. The app does not invent any.

</details>

## Importing a Claude export

Optional, and only useful if you already have one. `python bulk_import.py` reads a Claude conversation export, chunks it, embeds it and writes markdown backups, so the journal has a history to work with from day one. Your messages become entries. Claude's replies are never stored as journal memory.

## Stack

Python and FastAPI on the backend, ChromaDB for local vector storage, the Claude API for generation, and plain HTML and vanilla JavaScript on the frontend. There is no build step and there are no frontend dependencies. See [HOW-IT-WORKS.md](HOW-IT-WORKS.md) for how the pieces fit together.

## Contributing

There is no `CONTRIBUTING.md` and no PR template, because the project is not set up for outside contributions. If you have found a bug or want something changed, open an issue.

Read the license before building on this code. It is AGPL-3.0-or-later, and the network-use clause is the part people most often miss. See below.

## Security

See [SECURITY.md](SECURITY.md). In short: local-only with no auth is the design, so running it exposed to the internet is out of scope and not a reportable bug. Key handling, prompt injection, XSS in rendered entries and DNS rebinding are all in scope.

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

The Affero clause is the reason for this choice. If you run a modified version of this app as a network service that other people use, you have to offer them its source. MIT or plain GPL would allow someone to build a hosted, multi-tenant journal on this code without that obligation. Given what this app holds, that matters more here than in most projects.

Every source file carries an `SPDX-License-Identifier: AGPL-3.0-or-later` header, so a file copied out of this repo still points back at its terms.
