#!/usr/bin/env bash
# Install the seed corpus into a scratch dir and capture fixtures from it.
# Every data dir is redirected so a real journal in the default dirs is
# never read, written, or wiped - and RAG_AUTHOR_NAME is forced, because
# leaving it alone is what puts the real author's name in shipped fixtures.
set -e
I=seed_corpus/install
export RAG_AUTHOR_NAME=Jordan
export RAG_JOURNAL_DIR=$I/journal_entries RAG_CHROMA_DIR=$I/chroma_data
export MC_ENTITY_DIR=$I/entity_graph MC_SUMMARY_DIR=$I/summaries
export MC_CATEGORY_DIR=$I/categories MC_PATTERN_DIR=$I/patterns
export MC_DREAM_DIR=$I/dreams MC_SESSION_DIR=$I/sessions
python seed_corpus/import_seed_corpus.py "$@"
python capture_fixtures.py --stages tag,entities,summaries,dreams,patterns,organic \
    --i-am-running-the-seed-corpus
python capture_fixtures.py --check
