"""
Reset the installed demo (seed instance) to its pristine opening state.

Restores three files from the seed-corpus source of truth into the live
install (seed_corpus/install/), so the demo's Write page opens the way it
ships:

  - sessions/current.json           -> empty session (no open entry)
  - summaries/seed_summary.md        -> the pre-upload live seed
  - summaries/seed_summary.candidate.md -> the pending candidate, which is
                                        what makes the "upload the candidate
                                        seed" banner appear

Why this is needed: the demo writes into seed_corpus/install/, so using it
(pasting entries, closing a chat) mutates the open session's `base` and
retires the pending candidate. That's normal app behaviour, but it means the
first-run demo state is single-use. Run this to get it back:

    python seed_corpus/reset_demo_state.py

The session and candidate are read per request, so a browser refresh is
enough to see the reset. (A restart is only needed if you also changed a
mock fixture, whose responses are cached for the life of the process.)
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL = HERE / "install"

# (source, destination) — source is the shipped truth, destination is live.
COPIES = [
    (HERE / "sessions" / "current.json",
     INSTALL / "sessions" / "current.json"),
    (HERE / "summaries" / "seed_summary.md",
     INSTALL / "summaries" / "seed_summary.md"),
    (HERE / "summaries" / "seed_summary.candidate.md",
     INSTALL / "summaries" / "seed_summary.candidate.md"),
]


def main() -> None:
    missing = [src for src, _ in COPIES if not src.exists()]
    if missing:
        names = ", ".join(str(m) for m in missing)
        sys.exit(f"refusing to reset: source file(s) missing: {names}")

    for src, dst in COPIES:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  {src.name} -> {dst}")

    print("\ndemo reset to opening state: empty session + pending candidate.")
    print("refresh the browser to see it (no restart needed).")


if __name__ == "__main__":
    main()
