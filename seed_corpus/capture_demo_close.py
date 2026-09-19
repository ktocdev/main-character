# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture the shipped demo's actual close pipeline with real model output.

Run with --live to authorize API calls. Resumable: successful requests are
saved immediately and reused on retry. Every data directory and the spend
ledger live under build/close_capture; the author's journal is never opened.
Use --phase before, close, or extras in that order. Promotion is separate.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = HERE / "build" / "close_capture"
DATA = WORK / "data"
RECORDS = WORK / "responses"
DIRS = {"JOURNAL": "journal_entries", "CHROMA": "chroma_data",
        "ENTITY": "entity_graph", "SUMMARY": "summaries",
        "CATEGORY": "categories", "PATTERN": "patterns",
        "DREAM": "dreams", "SESSION": "sessions"}


def configure():
    os.environ.update({"MC_" + key + "_DIR": str(DATA / value)
                       for key, value in DIRS.items()})
    os.environ.update(MC_AUTHOR_NAME="Jordan", MC_MOCK="0",
                      MC_DISABLED_CATEGORIES="",
                      MC_SPEND_FILE=str(WORK / "spend_ledger.json"))
    sys.path.insert(0, str(ROOT))


def install_recorder():
    import anthropic
    import config
    import metering
    from mock_client import _call_key, request_fingerprint, _Response, _Stream
    from capture_fixtures import CLIENT_MODULES

    RECORDS.mkdir(parents=True, exist_ok=True)
    real = anthropic.Anthropic()
    skip = frozenset({"mock_client", "metering", "capture_demo_close"})

    class Messages:
        def create(self, **kwargs):
            key = _call_key(skip)
            digest = request_fingerprint(key, kwargs)
            path = RECORDS / f"{digest}.json"
            if path.exists():
                text = json.loads(path.read_text(encoding="utf-8"))["text"]
                response = _Response(text, "", kwargs.get("model", ""))
                response.usage = SimpleNamespace(input_tokens=0, output_tokens=0)
                return response
            print(f"API {key} {digest[:10]}", flush=True)
            # Streaming also handles long requests without SDK timeout limits.
            with real.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
            if response.stop_reason != "end_turn":
                raise RuntimeError(f"{key}: incomplete response ({response.stop_reason})")
            text = "".join(b.text for b in response.content if b.type == "text")
            path.write_text(json.dumps({"key": key, "text": text,
                                       "model": kwargs.get("model"),
                                       "usage": response.usage.model_dump()},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"SAVED {key}", flush=True)
            return response

        def stream(self, **kwargs):
            response = self.create(**kwargs)
            text = "".join(b.text for b in response.content if b.type == "text")
            # Preserve real usage when the metering wrapper reads the result.
            stream = _Stream(text, "", kwargs.get("model", ""))
            stream._final = response
            return stream

    class Client:
        def __init__(self):
            self.messages = Messages()

    def factory():
        return metering.wrap(Client())

    config.get_client = factory
    for name in CLIENT_MODULES:
        module = __import__(name)
        if hasattr(module, "get_client"):
            module.get_client = factory
    import mock_client
    mock_client.STREAM_CHUNK_DELAY = 0


def snapshot(name):
    target = WORK / name
    if name not in {"before", "after", "after_refresh"} or target.resolve().parent != WORK.resolve():
        raise RuntimeError("unexpected snapshot path")
    if target.exists():
        shutil.rmtree(target)
    for directory in DIRS.values():
        if directory == "chroma_data":
            continue
        source = DATA / directory
        if source.exists():
            shutil.copytree(source, target / directory, dirs_exist_ok=True)


def validate_data():
    """Some pipeline stages log errors and continue; never promote partial data."""
    import categories
    import dreams
    import entities
    import summarizer
    conversations = entities.get_conversations()
    keys = {entities.conversation_cache_key(c) for c in conversations}
    for directory in (categories.RAW_DIR, entities.RAW_DIR, summarizer.ENTRY_DIR):
        missing = keys - {p.stem for p in directory.glob("*.json")}
        if missing:
            raise RuntimeError(f"incomplete {directory.name}: {sorted(missing)}")
    weeks = set(summarizer.group_by_week(conversations))
    if weeks - {p.stem for p in summarizer.ARC_DIR.glob("*.md")}:
        raise RuntimeError("weekly summaries are incomplete")
    for conv in conversations:
        if dreams.DREAM_HINT.search(conv["text"]):
            if not (dreams.RAW_DIR / f"{entities.conversation_cache_key(conv)}.json").exists():
                raise RuntimeError("dream extraction is incomplete")


def promote():
    from demo_close_state import content_manifest, TREES
    for name in ("before", "after", "after_refresh"):
        if not (WORK / name).exists():
            raise RuntimeError(f"missing capture: {name}")
    # These destinations are exclusively shipped fictional demo assets.
    for tree in TREES:
        source = WORK / "before" / tree
        target = HERE / "derived" / tree
        if target.resolve().parent != (HERE / "derived").resolve():
            raise RuntimeError("unexpected promotion path")
        if target.exists():
            shutil.rmtree(target)
        if tree == "summaries":
            for layer in ("arcs", "domains", "entries"):
                if (source / layer).exists():
                    shutil.copytree(source / layer, target / layer)
        else:
            shutil.copytree(source, target)
    target = ROOT / "mock_fixtures" / "demo_close"
    target.mkdir(exist_ok=True)
    for path in RECORDS.glob("*.json"):
        shutil.copy2(path, target / path.name)
    expected = {name: content_manifest(WORK / name)
                for name in ("before", "after", "after_refresh")}
    (HERE / "demo_close_expected.json").write_text(
        json.dumps(expected, indent=2), encoding="utf-8")
    print("Promoted starting dataset, exact responses, and expected manifests")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--phase", choices=["before", "close", "extras", "promote"], required=True)
    args = parser.parse_args()
    if args.phase == "promote":
        promote()
        return
    if not args.live:
        parser.error("--live is required: this records real, paid API output")
    configure()
    WORK.mkdir(parents=True, exist_ok=True)
    if not (DATA / "chroma_data" / ".install-complete").exists():
        subprocess.run([sys.executable, str(HERE / "import_seed_corpus.py")],
                       cwd=ROOT, check=True)
    install_recorder()
    import categories
    import dreams
    import entities
    import patterns
    import organic
    import server
    import sessions
    import summarizer

    if args.phase == "before":
        if (WORK / "closed.json").exists():
            raise RuntimeError("already closed; do not overwrite the before state")
        # Preserve authentic extraction caches only for indexed, closed entries.
        # Rebuild every derived index/doc from this bounded set of sources.
        keys = {entities.conversation_cache_key(c) for c in entities.get_conversations()}
        for directory in (categories.RAW_DIR, entities.RAW_DIR, dreams.RAW_DIR):
            for path in directory.glob("*.json"):
                if path.stem not in keys:
                    path.unlink()
        categories.build()
        entities.build()
        index = json.loads((entities.ENTITY_DIR / "index.json").read_text(encoding="utf-8"))
        referenced = {(entities.ENTITY_DIR / rec["path"]).resolve()
                      for rec in index.values()}
        for path in entities.ENTITY_DIR.rglob("*.md"):
            if path.resolve() not in referenced:
                path.unlink()
        summarizer.build()
        dreams.extract()
        patterns.build()
        organic.scan()
        validate_data()
        snapshot("before")
    elif args.phase == "close":
        if not (WORK / "before").exists():
            raise RuntimeError("capture the before state first")
        server.startup()
        closed = WORK / "closed.json"
        if not closed.exists():
            from fastapi import BackgroundTasks
            tasks = BackgroundTasks()
            result = server.close_session(server.CloseIn(), tasks)
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError(str(result))
            closed.write_text(json.dumps(result), encoding="utf-8")
        result = json.loads(closed.read_text(encoding="utf-8"))
        # These are the same ordered background tasks queued by the endpoint.
        server._close_begin()
        import seed
        if not seed.CANDIDATE_FILE.exists():
            server._after_close_seed(result["key"])
        server._after_close_refresh()
        if server._CLOSE["failed"]:
            raise RuntimeError(f"close stages failed: {server._CLOSE['failed']}")
        validate_data()
        snapshot("after")
    else:
        if not (WORK / "after").exists():
            raise RuntimeError("capture the close first")
        patterns.build()
        organic.scan()
        snapshot("after_refresh")
    print(f"Captured {args.phase} under {WORK}", flush=True)


if __name__ == "__main__":
    main()
