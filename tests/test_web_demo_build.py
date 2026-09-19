# SPDX-License-Identifier: AGPL-3.0-or-later
"""The web demo build: both snapshots captured, and nothing unpublishable in it."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_web_demo_build(tmp_path):
    out = tmp_path / "web-demo"
    build = subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_web_demo.py"), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout + build.stderr

    before = json.loads((out / "data/before.json").read_text(encoding="utf-8"))
    after = json.loads((out / "data/after.json").read_text(encoding="utf-8"))
    status = before["reads"]["/api/status"]["json"]
    assert (status["entries"], status["open_entries"]) == (32, 3)
    status = after["reads"]["/api/status"]["json"]
    assert (status["entries"], status["open_entries"], status["indexed_entries"]) == (32, 0, 32)
    # the close's own outcome is what the second snapshot is for
    assert after["reads"]["/api/seed"]["json"]["candidate_exists"]
    assert before["replies"] and before["close"]["response"]["ok"]
    assert len(after["search"]) == len(before["search"]) + 3

    html = (out / "index.html").read_text(encoding="utf-8")
    # the fake backend has to be in place before the first module asks anything
    assert html.index("js/web-demo/backend.js") < html.index("js/main.js")
    assert not re.search(r"<script>", html), "an inline script would need 'unsafe-inline'"

    needles = {str(tmp_path), str(tmp_path).replace("\\", "/"),
               str(Path.home()), str(Path.home()).replace("\\", "/"),
               str(Path.home()).replace("\\", "\\\\")}
    for path in out.rglob("*"):
        if not path.is_file() or path.suffix in (".ttf", ".png"):
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(out).as_posix()
        if path.suffix in (".html", ".css"):
            assert "/static/" not in text, rel
        for needle in needles:
            assert needle not in text, (rel, needle)
