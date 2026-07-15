# RAG Journal

A journal with infinite memory — powered by retrieval-augmented generation.

RAG Journal imports your Claude conversation exports and builds a searchable, context-aware journal on top of them. A companion persona uses semantic search + LLM reasoning over your history to respond with real memory — not generic advice, but grounded references to what you've actually written. The system extracts and tracks entities (people, projects, places), detects recurring patterns, and surfaces relevant context at multiple granularities (chunks, entry summaries, weekly arcs, per-domain living docs, entity profiles).

## What exists today

### Core engine
- **Bulk import** — parses Claude conversation exports, chunks them, stores them in ChromaDB with markdown backups
- **Semantic retrieval** — multi-layer context assembly: (1) recent + snapshot, (2) semantic chunks, (3) entity docs, (4) pattern library
- **Companion persona** — Claude turns retrieval context into thoughtful, grounded responses; streaming output to the browser
- **Reflection mode** — companion initiates conversation by connecting dots across your history
- **Sessions** — one chat stays open for days (about a week in practice); entries, replies, and follow-ups braid into it, surviving refreshes and restarts. Closing a chat is the *summarize point*: your side becomes a journal entry in the same shape as an imported conversation, the full braid is archived, and the memory pipeline (tagging, entities, summaries, dreams) runs in the background. The first session continues your most recent imported conversation.

### Entity graph
- **Extraction** — Claude pulls people, projects, places, and events from every entry
- **Curation** — merge/rename/retype entities by hand; the user reviews all changes before saving
- **Entity profiles** — living docs (1-3 paragraphs) per entity, auto-regenerated weekly as context grows
- **Observations** — timestamped, tagged, browseable by entity with date facets
- **Groups** — hand-made, nestable groupings of entities (e.g. "claude skills"); batch-select entities and file them all at once; a group can *roll up* so its members collapse out of the flat list and reveal inline when you click the group title
- **Triage queue** — keyboard-driven review of extracted entities (keep / merge / correct / rename / alias / retype / delete), 50-deep undo across sessions

### Context + summaries
- **Entry summaries** — 2-3 sentence distillations of each entry, cached incrementally
- **Weekly arcs** — short narratives of what happened each week, stitched from entry summaries
- **Domain summaries** — ~500-word living docs per category (work, dating, health, etc.), synthesized from arc context
- **Status snapshot** — one-paragraph status of life right now, updated with every new entry
- **Dream weather** — one-line tone signal from recent dreams, appended to the snapshot

### Category system
- **Automatic tagging** — Claude tags new entries with built-in categories (work, dating, health, emotional, etc.)
- **Organic categories** — place/project entities are clustered by composite embedding (70% context, 30% name); Claude names the clusters; the user confirms/dismisses/defers them
- **Custom categories** — hand-seeded with trigger keywords; entries are auto-tagged by member mention or keyword match
- **Parent rollup** — categories can roll up to a parent (Gardens → Landmarks)

### Pattern library
- **Pattern detection** — Claude scans arcs + domain docs to find recurring emotional cycles, behavioral pipelines, relationship dynamics, etc.; tracks 2+ dated instances per pattern with confidence scoring
- **Smart filtering** — patterns can be dismissed; they re-surface only with new evidence
- **Prompt injection** — patterns injected into companion context only when the question genuinely rhymes

### Dream layer
- **Extraction** — Claude finds every discrete dream in the journal (only entries that mention dreams, cached per conversation)
- **Realm isolation** — dreams live in a separate vector collection; waking queries can never surface them by accident
- **User-flagged dreams** — write mode has a "this was a dream" checkbox; flagged entries go straight to the dream realm
- **Dream weather** — tone signal from recent dreams (nightmare/anxiety/peaceful/etc.), appended to the snapshot
- **Cast + interpretation** — dream people/places are spelled to match the waking entity graph; your own readings are captured (never invented)

### Web UI
- **Write tab** — the one conversation surface: journal entries ("save entry", markdown-backed immediately) and questions ("send") share the open chat; the companion's replies stream in between; everything persists on the server across refreshes and restarts; entries commit to journal memory when the chat closes; dream checkbox available
- **Chat tab** — a throwaway "look something up" conversation with the companion; never stored as journal data
- **Search tab** — search the whole journal by meaning (semantic similarity) or exact text
- **Entities tab** — browse all people/projects/places with profiles, observations, groups, and edit options
- **Categories tab** — browse entries by category; see domain summaries; tag new entries; propose and manage organic/custom categories
- **Patterns tab** — view the pattern library with confidence, dated instances, and reasoning; dismiss individual patterns
- **Dreams tab** — browse all extracted dreams with narrative, cast, tones, and your interpretation; extract dreams from history
- **History tab** — lands on the open chat (the conversation it continues + every entry, reply, and follow-up in one braid); past chats in a sidebar, one open at a time; "summarize & start new chat" closes the session
- **Triage tab** — keyboard-driven queue for reviewing extracted entities (keep / merge / correct / rename / alias / retype / delete / skip), with undo
- **Help tab** — documentation of the system and your involvement

## What's planned

- **Phase 0: Short-message handling** — adapt the write flow for phone-sized bursts (deferred; pending design discussion)
- **Dream layer v2** — scene-level chunking, parallel dream-entity graph, emergent symbol detection, interpretation patterns, cross-realm correlations, weekly dream digest
- **Phase 4: Vue 3 + Prism Components UI** — rebuild the web UI with design system; the user to lead design (deferred; next when she's ready)
- **Life Spectrum** — color-mapped timeline visualization of journal embeddings over time

## How to run locally

### Prerequisites
- Python 3.12+ (or use the `.venv` with `uv`)
- `ANTHROPIC_API_KEY` in `.env` (for chat, writes, and all Claude operations)

### Start the server
From the project root:
```bash
.venv\Scripts\python.exe server.py
```

The journal opens at **http://127.0.0.1:8144**. Static UI reloads on every browser refresh; Python changes need a server restart.

### Notes
- **API costs** — browsing and searching are free (local embeddings, no API calls). Chat, writes, reflection, extraction, and tagging call Claude.
- **Data storage** — everything lives locally:
  - `chroma/` — vector store (ChromaDB)
  - `journal/` — markdown backups of entries and dreams
  - `entity_graph/` — extracted entities, profiles, aliases, groups, and curation history
  - `categories/`, `patterns/`, `dreams/`, `sessions/` — personal data (gitignored)
- **Stopping** — `Ctrl+C` in the terminal running `server.py`
- **Port stuck** — if you can't restart: `Get-NetTCPConnection -LocalPort 8144 -State Listen | Stop-Process -Force`

## Stack

- **Python** — backend server (FastAPI), import pipeline, entity extraction, summarization
- **ChromaDB** — local vector store with cosine similarity; separate collections for waking entries, summaries, dreams, and entity docs
- **Claude API** — retrieval-augmented generation, pattern detection, entity extraction, summarization, naming, interpretation
- **HTML + vanilla JS** — lightweight web UI (no build step, no dependencies on the frontend)

## Architecture

See [docs/discovery/rag-journal-roadmap.md](docs/discovery/rag-journal-roadmap.md) for the full roadmap and design rationale. See `docs/discovery/companion-persona.md` for how the companion is prompted.

## Status

Backend is feature-complete for Phases 0–3. The next major work is Phase 4 (UI redesign with Vue + Prism). See the roadmap for what's shipped vs. deferred.
