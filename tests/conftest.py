"""Point every data dir at a throwaway tmp dir *before* config is imported.

`config.py` reads the environment at import time, so this has to run
before any project module is pulled in — otherwise the suite opens
whatever journal is sitting in the working copy it happens to run from.
CI runs against a clean checkout where that would be harmless; a cloner
running pytest in their own working copy is who this protects.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="mc-tests-"))

# load_dotenv() does not override values already in the environment, so
# setting them here wins over whatever .env the working copy has.
os.environ.update({
    "MC_MOCK": "1",
    "RAG_JOURNAL_DIR": str(_TMP / "journal_entries"),
    "RAG_CHROMA_DIR": str(_TMP / "chroma_data"),
    "MC_ENTITY_DIR": str(_TMP / "entity_graph"),
    "MC_SUMMARY_DIR": str(_TMP / "summaries"),
    "MC_CATEGORY_DIR": str(_TMP / "categories"),
    "MC_PATTERN_DIR": str(_TMP / "patterns"),
    "MC_DREAM_DIR": str(_TMP / "dreams"),
    "MC_SESSION_DIR": str(_TMP / "sessions"),
})
