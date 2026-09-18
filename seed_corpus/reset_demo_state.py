# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reset the installed demo (seed instance) to its pristine opening state.

Restores three files from the seed-corpus source of truth into the live
install (seed_corpus/install/), so the demo's Write page opens the way it
ships:

  - sessions/current.json           -> the open session: 9/15-9/17 written
                                        since the 9/14 close, not yet closed
  - summaries/seed_summary.md        -> the pre-upload live seed
  - summaries/seed_summary.candidate.md -> the pending candidate, which is
                                        what makes the "upload the candidate
                                        seed" banner appear

Why this is needed: the demo writes into seed_corpus/install/, so using it
(writing entries, closing or discarding the chat) mutates the open session
and retires the pending candidate. That's normal app behaviour, but it means
the opening state is single-use.

The server runs restore() on every restart into the demo, so each arrival is
pristine. Run this by hand to get it back without a restart:

    python seed_corpus/reset_demo_state.py

The session and candidate are read per request, so a browser refresh is
enough to see the reset. (A restart is only needed if you also changed a
mock fixture, whose responses are cached for the life of the process.)

Only these three files, ever -- never chroma, entries or entity data. A
closed chat's entry stays in the demo's index until the next rebuild.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL = HERE / "install"

# Relative paths: the shipped truth is HERE/<path>, the live copy is
# <install>/<path>. Relative so the server can restore into whichever
# install root it was pointed at.
COPIES = [
    Path("sessions") / "current.json",
    Path("summaries") / "seed_summary.md",
    Path("summaries") / "seed_summary.candidate.md",
]


def installed(install: Path = INSTALL) -> bool:
    """Same test the server uses: the database file, not the directory,
    because an interrupted build leaves the folder behind."""
    return (install / "chroma_data" / "chroma.sqlite3").exists()


def restore(install: Path = INSTALL) -> list[Path]:
    """Copy the three opening-state files over the install. Returns what was
    written; an empty list means no demo is installed, which is a no-op
    rather than an error -- there is nothing to reset. Raises
    FileNotFoundError when a shipped source is missing, before copying any
    of them, so a half-reset never happens."""
    if not installed(install):
        return []
    missing = [HERE / rel for rel in COPIES if not (HERE / rel).exists()]
    if missing:
        raise FileNotFoundError(
            "source file(s) missing: " + ", ".join(str(m) for m in missing))

    written = []
    for rel in COPIES:
        dst = install / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / rel, dst)
        written.append(dst)
    return written


def main() -> None:
    try:
        written = restore()
    except FileNotFoundError as e:
        sys.exit(f"refusing to reset: {e}")
    if not written:
        sys.exit(f"nothing to reset: no demo installed at {INSTALL}")

    for dst in written:
        print(f"  {dst.name} -> {dst}")
    print("\ndemo reset to opening state: open session (9/15-9/17) + "
          "pending candidate.")
    print("refresh the browser to see it (no restart needed).")


if __name__ == "__main__":
    main()
