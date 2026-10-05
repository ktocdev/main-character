# SPDX-License-Identifier: AGPL-3.0-or-later
"""Point every data dir at a throwaway tmp dir *before* config is imported.

`config.py` reads the environment at import time, so this has to run
before any project module is pulled in — otherwise the suite opens
whatever journal is sitting in the working copy it happens to run from.
CI runs against a clean checkout where that would be harmless; a cloner
running pytest in their own working copy is who this protects.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="mc-tests-"))

# Clear out what earlier runs couldn't (see pytest_sessionfinish). The
# prefix is specific to this suite and every match is a dead run's scratch
# dir; the one this run just made is skipped.
for _stale in Path(tempfile.gettempdir()).glob("mc-tests-*"):
    if _stale != _TMP:
        shutil.rmtree(_stale, ignore_errors=True)

# load_dotenv() does not override values already in the environment, so
# setting them here wins over whatever .env the working copy has.
os.environ.update({
    "MC_MOCK": "1",
    "MC_JOURNAL_DIR": str(_TMP / "journal_entries"),
    "MC_CHROMA_DIR": str(_TMP / "chroma_data"),
    "MC_ENTITY_DIR": str(_TMP / "entity_graph"),
    "MC_SUMMARY_DIR": str(_TMP / "summaries"),
    "MC_CATEGORY_DIR": str(_TMP / "categories"),
    "MC_PATTERN_DIR": str(_TMP / "patterns"),
    "MC_DREAM_DIR": str(_TMP / "dreams"),
    "MC_SESSION_DIR": str(_TMP / "sessions"),
})


def pytest_sessionfinish(session, exitstatus):
    """Take the throwaway tree with us instead of leaving one behind per run.

    Best-effort: chroma's Rust core holds chroma.sqlite3 open for the life
    of the process and Windows refuses to delete an open file, so that one
    file (and its directory) can survive. Hence the sweep above — whatever
    this pass can't remove, the next run's does, and the steady state is
    one stale tree rather than one per run. ignore_errors so a stray handle
    never turns a green run red.
    """
    shutil.rmtree(_TMP, ignore_errors=True)
