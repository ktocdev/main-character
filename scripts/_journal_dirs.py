# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Point the data dirs at a chosen journal before `config` is imported.

    import _journal_dirs            # first, before anything that imports config
    WHICH = _journal_dirs.apply()   # "journal", "demo" or "sandbox"

config freezes its paths at import time, so the choice has to be made off
sys.argv by hand, before argparse ever runs -- the same reason
seed_corpus/import_seed_corpus.py handles its --demo flag this way.

  --demo     the demo journal, seed_corpus/install/ (the dirs SEED_ENV in
             server.py uses). Build it with
             `python seed_corpus/import_seed_corpus.py --demo`.
  --sandbox  the port-8145 sandbox's data, %TEMP%/mc-wizard/_d/ -- with
             run-sandbox.ps1 -CopyJournal, a copy of the real journal.
  neither    whatever the MC_*_DIR variables and .env say: the real journal.

Everything here only reads. Opening the real journal's Chroma store while
the server on 8144 has it open is best avoided; use the sandbox copy.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_DIRS = {
    "MC_JOURNAL_DIR": "journal_entries",
    "MC_CHROMA_DIR": "chroma_data",
    "MC_ENTITY_DIR": "entity_graph",
    "MC_SUMMARY_DIR": "summaries",
    "MC_CATEGORY_DIR": "categories",
    "MC_PATTERN_DIR": "patterns",
    "MC_DREAM_DIR": "dreams",
    "MC_SESSION_DIR": "sessions",
}

DEMO_ROOT = REPO / "seed_corpus" / "install"
SANDBOX_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "mc-wizard" / "_d"


def apply() -> str:
    if "config" in sys.modules:
        raise RuntimeError("_journal_dirs.apply() must run before config is imported")
    if "--demo" in sys.argv and "--sandbox" in sys.argv:
        raise SystemExit("pick one of --demo and --sandbox")
    if "--demo" in sys.argv:
        which, root = "demo", DEMO_ROOT
        # The corpus is Jordan's; entity matching skips the author's name.
        os.environ["MC_AUTHOR_NAME"] = "Jordan"
    elif "--sandbox" in sys.argv:
        which, root = "sandbox", SANDBOX_ROOT
    else:
        return "journal"
    if not (root / "chroma_data" / "chroma.sqlite3").is_file():
        hint = ("python seed_corpus/import_seed_corpus.py --demo" if which == "demo"
                else "run-sandbox.ps1 -CopyJournal")
        raise SystemExit(f"no {which} journal at {root} -- build it first: {hint}")
    for key, name in _DIRS.items():
        os.environ[key] = str(root / name)
    return which
