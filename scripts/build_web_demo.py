# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Build the web demo: the demo journal as static files, no server behind it.

The UI is the real one, copied from static/. What it would have asked the
server is captured here, ahead of time, from a throwaway install of the demo
corpus: every read endpoint once before a close and once after, around the
recorded close in mock_fixtures/demo_close/. static/js/web-demo/backend.js
answers from that capture in the browser (see docs/releasing/web-demo-plan.md).

Usage:
    python scripts/build_web_demo.py              -> dist/web-demo/
    python scripts/build_web_demo.py --out DIR

The first run on a machine downloads chroma's embedding model, as any demo
install does. Nothing here needs a key or makes an API call.

Three processes, for the reason the demo installer gives: config reads the
environment once, at import. The install and the capture each get a fresh
interpreter whose environment points every data dir into a temp dir -- and
has .env switched off, so nothing from this machine's own configuration can
end up in a file that is about to be published.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DEFAULT_OUT = ROOT / "dist" / "web-demo"
INSTALLER = ROOT / "seed_corpus" / "import_seed_corpus.py"
REPLIES = ROOT / "mock_fixtures" / "companion._stream_turn.json"

DATA_DIRS = {
    "JOURNAL": "journal_entries", "CHROMA": "chroma_data",
    "ENTITY": "entity_graph", "SUMMARY": "summaries",
    "CATEGORY": "categories", "PATTERN": "patterns",
    "DREAM": "dreams", "SESSION": "sessions",
}

# Reads with no parameters. The parameterized ones are found by walking these.
FIXED_READS = [
    "status", "sessions/current", "sessions", "seed", "entities",
    "entities/duplicates", "groups", "history", "categories", "organic",
    "patterns", "dreams", "cost", "settings",
]


def child_env(tmp: Path) -> dict:
    """The environment both child processes run in.

    Anything MC_* or ANTHROPIC_* inherited from this shell is dropped, and the
    children switch .env off (see _no_dotenv), so the capture is the same on
    every machine and carries none of this one's settings. The time zone is
    pinned for the same reason: left blank it resolves to this machine's.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("MC_", "ANTHROPIC"))}
    env.update(MC_MOCK="1", MC_SEED_INSTANCE="1", MC_AUTHOR_NAME="Jordan",
               ANTHROPIC_API_KEY="", MC_DISABLED_CATEGORIES="",
               MC_TIMEZONE="UTC", MC_SPEND_FILE=str(tmp / "spend.json"),
               PYTHONIOENCODING="utf-8")
    for key, directory in DATA_DIRS.items():
        env[f"MC_{key}_DIR"] = str(tmp / directory)
    return env


def _no_dotenv():
    """Every module calls load_dotenv() at import. Replaced before any of them
    is imported, it reads nothing -- the environment above is the whole story."""
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False


# ---------------------------------------------------------------------------
# STAGE 1: install the demo corpus into the temp dir
# ---------------------------------------------------------------------------

def stage_install():
    _no_dotenv()
    import runpy
    sys.argv = [str(INSTALLER)]
    runpy.run_path(str(INSTALLER), run_name="__main__")


# ---------------------------------------------------------------------------
# STAGE 2: capture every read, before and after the recorded close
# ---------------------------------------------------------------------------

def canonical(path: str, params: dict) -> str:
    """The key a read is stored under. backend.js builds the same string from
    the URL the page asks for: the path, then the parameters sorted by name,
    values decoded."""
    if not params:
        return path
    return path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))


def snapshot(client, server) -> dict:
    reads = {}

    def get(path, **params):
        res = client.get(path, params=params)
        ctype = res.headers.get("content-type", "")
        if "application/json" in ctype:
            entry = {"status": res.status_code, "json": res.json()}
        else:
            entry = {"status": res.status_code, "text": res.text,
                     "type": ctype.split(";")[0]}
            disposition = res.headers.get("content-disposition", "")
            name = re.search(r'filename="?([^";]+)"?', disposition)
            if name:
                entry["filename"] = name.group(1)
        reads[canonical(path, params)] = entry
        return entry.get("json")

    for name in FIXED_READS:
        get("/api/" + name)
    # The server's own clock and process: backend.js supplies both live.
    for field in ("instance", "now"):
        reads["/api/status"]["json"].pop(field, None)

    for name in reads["/api/entities"]["json"]:
        get("/api/entities/observations", name=name)

    for item in reads["/api/sessions"]["json"]["sessions"]:
        if item["kind"] == "archive":
            get("/api/sessions/archive", id=item["id"])
        else:
            get("/api/sessions/conversation", title=item["title"],
                start=item["start"], end=item["end"])

    cats = reads["/api/categories"]["json"]
    domains = set(cats.get("counts", {})) | set(cats.get("custom") or [])
    domains |= {c["name"] for c in reads["/api/organic"]["json"]["custom"]}
    for name in sorted(domains):
        get("/api/summaries/domain", name=name)

    entries = {(c["date"], c["title"]) for c in cats.get("conversations", {}).values()}
    for meta in server.STATE["collection"].get(include=["metadatas"])["metadatas"]:
        entries.add((meta.get("date", ""), meta.get("title", "")))
    for date, title in sorted(entries):
        get("/api/entry", date=date, title=title)

    for which in ("current", "candidate"):
        get("/api/seed/download", which=which)
    return reads


def search_corpus(server) -> list[dict]:
    """Every indexed entry's full text, for backend.js's word matching. One
    record per entry, its chunks rejoined in order -- the same unit
    /api/search reports a hit against."""
    data = server.STATE["collection"].get(include=["documents", "metadatas"])
    chunks = {}
    for doc_id, doc, meta in zip(data["ids"], data["documents"], data["metadatas"]):
        index = re.search(r"_c(\d+)$", doc_id)
        key = (meta.get("date", ""), meta.get("title", ""))
        chunks.setdefault(key, []).append((int(index.group(1)) if index else 0, doc))
    return [{"date": date, "title": title,
             "text": "\n\n".join(doc for _, doc in sorted(parts))}
            for (date, title), parts in sorted(chunks.items(), reverse=True)]


def stage_capture(tmp: Path, data_dir: Path):
    _no_dotenv()
    sys.path.insert(0, str(ROOT))
    import env_file
    # /api/settings reads the file as well as the environment
    env_file.ENV_PATH = tmp / ".env"

    from fastapi.testclient import TestClient
    import mock_client
    import server

    mock_client.DELAYS = {key: 0 for key in mock_client.DELAYS}
    mock_client.DEFAULT_DELAY = 0
    mock_client.STREAM_CHUNK_DELAY = 0
    # The misses guard from tests/verify_demo_close.py: the close must replay
    # the exact recordings. A miss would publish random-bucket output as the
    # demo's after-close state, so it stops the build instead.
    misses = []
    original = mock_client._recorded

    def recorded(key, kwargs):
        text = original(key, kwargs)
        if text is None:
            misses.append((key, mock_client.request_fingerprint(key, kwargs)))
            raise AssertionError(f"missing exact recording: {misses[-1]}")
        return text

    mock_client._recorded = recorded

    with TestClient(server.app, base_url="http://127.0.0.1:8144") as client:
        before = snapshot(client, server)
        search_before = search_corpus(server)
        status = before["/api/status"]["json"]
        assert (status["entries"], status["open_entries"]) == (32, 3), status

        closed = client.post("/api/sessions/close", json={})
        assert closed.status_code == 200, closed.text
        assert not misses, misses
        assert not server._CLOSE["failed"], server._CLOSE

        after = snapshot(client, server)
        search_after = search_corpus(server)
        status = after["/api/status"]["json"]
        assert (status["entries"], status["open_entries"]) == (32, 0), status

    replies = json.loads(REPLIES.read_text(encoding="utf-8"))["responses"]
    data_dir.mkdir(parents=True, exist_ok=True)
    write_json(data_dir / "before.json", {
        "reads": before,
        "search": search_before,
        "replies": [r if isinstance(r, str) else json.dumps(r) for r in replies],
        "close": {
            "response": closed.json(),
            "steps": [{"key": k, "label": label} for k, label in server.CLOSE_STEPS],
        },
    })
    # Only what the close changed; backend.js falls back to `before` for the rest.
    write_json(data_dir / "after.json", {
        "reads": {k: v for k, v in after.items() if before.get(k) != v},
        "search": search_after,
    })


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# STAGE 3: the site
# ---------------------------------------------------------------------------

INLINE_SCRIPT = re.compile(r"<script>\s*(.*?)\s*</script>", re.S)
MAIN_SCRIPT = '<script type="module" src="js/main.js"></script>'


def write_site(out: Path):
    """static/ with its URLs made relative, so it works from any subpath."""
    shutil.copytree(STATIC, out, ignore=shutil.ignore_patterns("__pycache__"))

    index = out / "index.html"
    html = index.read_text(encoding="utf-8").replace('"/static/', '"')
    # The theme pre-paint snippet is the page's one inline script. Moved to a
    # file, so the published site can run under `script-src 'self'`.
    inline = INLINE_SCRIPT.search(html)
    assert inline, "index.html no longer has its inline theme script"
    (out / "js" / "web-demo" / "theme.js").write_text(inline.group(1) + "\n", encoding="utf-8")
    html = html.replace(inline.group(0), '<script src="js/web-demo/theme.js"></script>')
    assert MAIN_SCRIPT in html, "index.html no longer loads js/main.js"
    # A classic script runs before any module, so fetch is replaced by the
    # time main.js asks for anything.
    html = html.replace(MAIN_SCRIPT,
                        '<script src="js/web-demo/backend.js"></script>\n' + MAIN_SCRIPT)
    index.write_text(html, encoding="utf-8")

    tokens = out / "css" / "tokens.css"
    tokens.write_text(tokens.read_text(encoding="utf-8")
                      .replace("url('/static/", "url('../"), encoding="utf-8")


def leak_needles(tmp: Path) -> list[str]:
    """Paths that must never appear in the output, in every spelling they
    could take: native, forward-slashed, and JSON-escaped."""
    needles = set()
    # unresolved as well: on Windows the temp dir can come back as an 8.3
    # short name (DEFAUL~1) that resolve() expands, and either may be written
    for raw in {str(p) for base in (tmp, Path.home()) for p in (base, base.resolve())}:
        needles |= {raw, raw.replace("\\", "/"), raw.replace("\\", "\\\\")}
    return sorted(needles)


def check_output(out: Path, tmp: Path):
    problems = []
    needles = leak_needles(tmp)
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(out).as_posix()
        problems += [f"{rel}: contains {n}" for n in needles if n in text]
        if rel.startswith("data/") and "ANTHROPIC" in text:
            problems.append(f"{rel}: mentions ANTHROPIC")
        if path.suffix in (".html", ".css") and "/static/" in text:
            problems.append(f"{rel}: absolute /static/ URL left")
    if problems:
        sys.exit("web demo build is not publishable:\n  " + "\n  ".join(problems))


def build(out: Path):
    out = out.resolve()
    tmp = Path(tempfile.mkdtemp(prefix="mc-webdemo-"))
    try:
        env = child_env(tmp)
        me = str(Path(__file__).resolve())
        print(f"installing the demo corpus into {tmp}")
        subprocess.run([sys.executable, me, "--stage", "install"],
                       cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
        if out.exists():
            shutil.rmtree(out)
        write_site(out)
        print("capturing reads before and after the recorded close")
        subprocess.run([sys.executable, me, "--stage", "capture", str(tmp), str(out / "data")],
                       cwd=ROOT, env=env, check=True)
        check_output(out, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"web demo -> {out} ({size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stage", choices=["install", "capture"], help=argparse.SUPPRESS)
    parser.add_argument("paths", nargs="*", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stage == "install":
        stage_install()
    elif args.stage == "capture":
        stage_capture(*args.paths)
    else:
        build(args.out)


if __name__ == "__main__":
    main()
