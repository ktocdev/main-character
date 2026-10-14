# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Exit 0 if this Python can install the journal, 1 if not. The launchers
(journal, journal.cmd) run it to choose a Python for .venv and to check an
existing .venv before installing into it.

3.15 is left out until onnxruntime publishes 3.15 wheels. It has no source
build, so pip cannot fall back to compiling it, and several other locked
packages would need a C or Rust compiler. Keep the README's range in step
with SUPPORTED.

With --venv it explains why, since the person has to delete .venv by hand.
Runs under any Python 3, so no f-strings.
"""
import sys

SUPPORTED = ((3, 12), (3, 13), (3, 14))

ok = tuple(sys.version_info[:2]) in SUPPORTED
if not ok and "--venv" in sys.argv:
    print(".venv was made with Python %d.%d, but the journal needs %d.%d to %d.%d."
          % (tuple(sys.version_info[:2]) + SUPPORTED[0] + SUPPORTED[-1]))
    print("Delete the .venv folder and run install again.")
sys.exit(0 if ok else 1)
