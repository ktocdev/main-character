# RAG Journal

A journal with infinite memory — powered by retrieval-augmented generation.

## What is this?

RAG Journal imports your Claude conversation exports and builds a searchable, context-aware journal on top of them. A companion persona uses semantic search over your history to respond with real memory — not generic advice, but grounded references to what you've actually written.

## What exists today

- **Bulk import pipeline** (`bulk_import.py`) — parses Claude export JSON, extracts user messages, chunks them, and stores them in a local vector store with markdown backups
- **Design docs** — roadmap, companion persona spec, and persona iteration history in `docs/discovery/`

## What's planned

- Core retrieval module and first end-to-end import
- Interactive CLI with a companion persona that matures over time
- Entity graph tracking people, projects, places, and patterns
- Multi-level summaries (entry, weekly arc, domain, status snapshot)
- Dream layer with separate realm tracking and cross-realm linking
- Web UI (Vue 3 + TypeScript PWA) with a custom component library
- Life Spectrum — a color-mapped timeline visualization of journal embeddings

## Stack

- Python + ChromaDB (local vector store)
- Anthropic Claude API (companion + metadata extraction)
- Vue 3 + TypeScript (future web UI)

## Status

Early build. See [docs/discovery/rag-journal-roadmap.md](docs/discovery/rag-journal-roadmap.md) for the full roadmap.
