#!/usr/bin/env bash
# Serve the seed corpus, not your journal. Every data dir points into
# seed_corpus/install/ and mock mode is on, so this needs no API key and
# spends nothing — writes land in the sandbox, never in the real stores.
I=seed_corpus/install
export MC_MOCK=1 MC_AUTHOR_NAME=Jordan
export MC_JOURNAL_DIR=$I/journal_entries MC_CHROMA_DIR=$I/chroma_data
export MC_ENTITY_DIR=$I/entity_graph MC_SUMMARY_DIR=$I/summaries
export MC_CATEGORY_DIR=$I/categories MC_PATTERN_DIR=$I/patterns
export MC_DREAM_DIR=$I/dreams MC_SESSION_DIR=$I/sessions
exec python server.py "$@"
