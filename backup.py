# SPDX-License-Identifier: AGPL-3.0-or-later
"""
The same export, as one dated zip you can put somewhere else.

    python backup.py                 # -> backups/journal_backup_<stamp>.zip
    python backup.py --out FILE

`export.py` writes a folder you can read; this writes a file you can move.
That is the whole difference, and it is the reason both exist: the folder is
for looking at your writing without the app, the zip is for the copy that
lives on another disk.

**It is `export.py`'s output, not a second definition of "everything".** The
export is built into a temporary directory and zipped from there, so the two
commands cannot drift into disagreeing about what a backup contains -- which
they would, eventually, if each walked the data directories itself.

**Not a substitute for a real backup habit.** It writes next to the journal
by default, on the same disk, which protects against a bad edit and nothing
else. Move the file somewhere that survives the machine.
"""

import argparse
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import config
import export

ROOT = Path(__file__).resolve().parent


def default_dest() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "backups" / f"journal_backup_{stamp}.zip"


def write_backup(dest: Path) -> dict:
    """Build the export in a temp dir, zip it, return its manifest.

    The zip is written to a `.part` name and renamed once it is complete: a
    backup interrupted halfway is worse than no backup, because it is the one
    thing nobody checks until they need it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mc_backup_") as tmp:
        tmp = Path(tmp)
        staged = tmp / dest.stem
        info = export.export_journal(staged)
        # make_archive appends ".zip" to the base name it is given, so the
        # archive is built beside the staging folder and copied in whole.
        made = Path(shutil.make_archive(str(tmp / "archive"), "zip",
                                        root_dir=staged.parent,
                                        base_dir=staged.name))
        part = dest.with_name(dest.name + ".part")
        shutil.copy2(made, part)
    part.replace(dest)
    info["bytes"] = dest.stat().st_size
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="Back the journal up as a zip.")
    ap.add_argument("--out", type=Path, default=None,
                    help="zip to write (default: backups/journal_backup_<stamp>.zip)")
    args = ap.parse_args()

    if not export.read_entries():
        print(f"no entries found under {config.JOURNAL_DIR}")
        return 1

    dest = args.out or default_dest()
    if dest.exists():
        print(f"{dest} already exists.")
        return 1

    info = write_backup(dest)
    mb = info["bytes"] / 1_000_000
    print(f"  entries : {info['entries']} "
          f"({info['first_entry']} to {info['last_entry']})")
    print(f"  size    : {mb:.1f} MB")
    print(f"\nwritten to {dest}")
    print("This is on the same disk as the journal. Move it somewhere that "
          "survives the machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
