# SPDX-License-Identifier: AGPL-3.0-or-later
"""One real demo install, shared by every test that needs one.

The installer embeds the whole corpus and takes most of a minute, so the
suite builds it once (conftest's `installed` fixture) and tests that need
to write into an install copy it rather than building their own.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIRS = [("journal", "journal_entries"), ("chroma", "chroma_data"),
             ("entity", "entity_graph"), ("summary", "summaries"),
             ("category", "categories"), ("pattern", "patterns"),
             ("dream", "dreams"), ("session", "sessions")]


def install_env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MC_")}
    env.update({"MC_MOCK": "1", "MC_AUTHOR_NAME": "Jordan",
                **{f"MC_{name.upper()}_DIR": str(root / d) for name, d in DATA_DIRS}})
    return env


def run_installer(root: Path, *args: str) -> subprocess.CompletedProcess:
    """A real import into a throwaway install. A subprocess because the
    installer freezes its data dirs at import time -- the same reason
    /api/setup/install-demo spawns it."""
    return subprocess.run(
        [sys.executable, str(ROOT / "seed_corpus" / "import_seed_corpus.py"), *args],
        cwd=str(ROOT), env=install_env(root), capture_output=True, text=True,
        timeout=900)


def copy_install(src: Path, dst: Path) -> Path:
    """A writable copy of the shared install, so it stays as installed."""
    shutil.copytree(src, dst)
    return dst
