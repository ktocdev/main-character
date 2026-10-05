# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rebuild all demo data, only while the demo is stopped.

Visitor entries, archives, embeddings, derived data and backups are removed
before reinstalling the shipped corpus. The real journal is never a target.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL = HERE / "install"
DATA_DIRS = {
    "JOURNAL": "journal_entries", "CHROMA": "chroma_data",
    "ENTITY": "entity_graph", "SUMMARY": "summaries",
    "CATEGORY": "categories", "PATTERN": "patterns",
    "DREAM": "dreams", "SESSION": "sessions",
}


def restore(install: Path = INSTALL) -> list[Path]:
    """Rebuild the designated install; a failed build remains retryable."""
    install = Path(install).absolute()
    if install.is_symlink() or install.resolve() != INSTALL.absolute():
        raise OSError("refusing to reset a path outside the demo install")
    if not install.exists():
        return []
    import config
    active = [Path(getattr(config, name + "_DIR")).resolve() for name in DATA_DIRS]
    if any(path == install or install in path.parents for path in active):
        raise OSError("restart back to your own journal before resetting the demo")
    env = os.environ.copy()
    env.update({"MC_MOCK": "1", "MC_AUTHOR_NAME": "Jordan"})
    env.update({"MC_" + name + "_DIR": str(install / directory)
                for name, directory in DATA_DIRS.items()})
    shutil.rmtree(install)
    done = subprocess.run(
        [sys.executable, str(HERE / "import_seed_corpus.py")],
        cwd=str(HERE.parent), env=env, capture_output=True, text=True, timeout=900)
    if done.returncode:
        raise OSError("demo rebuild failed; retry loading the demo")
    return [install]


def main() -> None:
    try:
        written = restore()
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"could not reset demo: {exc}")
    print("demo fully reset" if written else "no demo installed")


if __name__ == "__main__":
    main()
