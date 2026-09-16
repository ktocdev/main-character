# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture the two keys the batch stages can't reach."""
import os, sys, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
I = "seed_corpus/install"
os.environ.update({
    "RAG_AUTHOR_NAME": "Jordan",
    "RAG_JOURNAL_DIR": f"{I}/journal_entries", "RAG_CHROMA_DIR": f"{I}/chroma_data",
    "MC_ENTITY_DIR": f"{I}/entity_graph", "MC_SUMMARY_DIR": f"{I}/summaries",
    "MC_CATEGORY_DIR": f"{I}/categories", "MC_PATTERN_DIR": f"{I}/patterns",
    "MC_DREAM_DIR": f"{I}/dreams", "MC_SESSION_DIR": f"{I}/sessions",
})
sys.stdout.reconfigure(encoding="utf-8")
import capture_fixtures as C

captured = {}
C._install(captured)

import entities, sessions, config
# suggest_merges: reached only from the server, needs a built entity graph
print("suggest_merges…")
for kind in ("person", "place", "project"):
    groups = entities.suggest_merges(kind)
    print(f"   {kind}: {json.dumps(groups, ensure_ascii=False)[:220]}")

# _generate_title: the build passed explicit hints, so it never fired
print("_generate_title…")
for arc in sorted(pathlib.Path(f"{I}/journal_entries").glob("2026-0[89]*.md"))[:3]:
    text = arc.read_text(encoding="utf-8")[:2000]
    print("  ", sessions._generate_title(config.get_client(), text))

# build_snapshot isn't driven by summarizer.build(), so the batch pass
# never reaches it either
print("build_snapshot…")
import summarizer
summarizer.build_snapshot(quiet=True)

for key, responses in captured.items():
    path = C.FIXTURE_DIR / f"{key}.json"
    existing = json.loads(path.read_text(encoding="utf-8"))["responses"] if path.exists() else []
    merged = list(dict.fromkeys(existing + responses))[:C.MAX_PER_KEY]
    path.write_text(json.dumps({"responses": merged}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  {path.name}: {len(merged)} responses")
