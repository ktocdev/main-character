"""
Read and edit the .env file the app is configured from.

Settings are a UI over this file, not a second store: config.py reads the
process environment, python-dotenv puts .env into it at import, and this
module is what edits the file in place without disturbing anything it
doesn't recognize.

Three properties matter more than convenience here:

  - Comments and unknown keys survive. A cloner's hand-added
    ANTHROPIC_BASE_URL, a commented-out experiment, the blank lines that
    make the file readable — a settings save that silently ate those would
    be worse than having no settings UI at all.
  - The write is atomic (temp file in the same directory, then replace).
    A half-written .env is an app that won't start, and this file holds the
    API key, so a truncated write is a lockout, not an inconvenience.
  - A value can never introduce a second assignment. Newlines are refused
    outright: without that, writing one whitelisted key is enough to append
    any other — the exact escape the server's whitelist exists to prevent.

Nothing here re-reads values into the running process. The app loads .env
once at import, so a save takes effect on the next start; the caller is
responsible for saying so.
"""

import os
import re
import tempfile
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"

# `export FOO=bar` is valid in a .env that also gets sourced by a shell, so
# the prefix is matched and preserved rather than treated as part of the name.
_ASSIGN = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=)(.*)$")


def _unquote(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
        if raw.strip()[0] == '"':
            v = v.replace('\\"', '"').replace("\\\\", "\\")
    return v


def _quote(value: str) -> str:
    """Render a value so re-reading it yields exactly what was passed."""
    if value == "":
        return ""
    if re.search(r"[\s#'\"\\]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def read_env(path: Path | None = None) -> dict[str, str]:
    """Every assignment in the file, last one winning — the same rule
    python-dotenv applies, so this reports what the app actually loaded."""
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGN.match(line)
        if m:
            values[m.group(2)] = _unquote(m.group(4))
    return values


def update_env(changes: dict[str, str], path: Path | None = None) -> list[str]:
    """Apply `changes` to the file and return the keys actually written.

    An existing key is rewritten where it already sits, so its surrounding
    comment stays attached to it. A new key is appended. A value of "" means
    *remove the assignment* rather than write an empty one, so clearing a
    setting in the UI restores config.py's default instead of overriding it
    with emptiness — the two are not the same for paths and model names.
    """
    path = path or ENV_PATH
    for key, value in changes.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key}: a value cannot contain a line break")

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()
    remaining = dict(changes)
    out: list[str] = []

    for line in lines:
        m = _ASSIGN.match(line) if not line.lstrip().startswith("#") else None
        key = m.group(2) if m else None
        if key is None or key not in remaining:
            out.append(line)
            continue
        value = remaining.pop(key)
        if value == "":
            continue                      # drop the line: back to the default
        out.append(f"{m.group(1)}{key}{m.group(3)}{_quote(value)}")

    appended = [f"{k}={_quote(v)}" for k, v in remaining.items() if v != ""]
    if appended and out and out[-1].strip():
        out.append("")
    out.extend(appended)

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"

    # same directory, so the replace is a rename within one filesystem and
    # can't leave the real file missing if it fails
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return list(changes)
