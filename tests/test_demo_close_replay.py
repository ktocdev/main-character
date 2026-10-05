# SPDX-License-Identifier: AGPL-3.0-or-later
"""The demo's close must replay the recorded transition, never random buckets."""
import json
import os
from pathlib import Path
import subprocess
import sys

import mock_client

ROOT = Path(__file__).resolve().parent.parent


def test_recording_identity_ignores_only_domain_wall_clock():
    first = {"messages": [{"role": "user", "content":
             "Today is 2026-09-18.\n<entry date=2026-09-17>new text</entry>"}]}
    later = json.loads(json.dumps(first).replace("2026-09-18", "2027-01-01"))
    fingerprint = mock_client.request_fingerprint
    assert fingerprint("summarizer.build_domains", first) == fingerprint("summarizer.build_domains", later)
    assert fingerprint("seed._call", first) != fingerprint("seed._call", later)
    changed = json.loads(json.dumps(first).replace("new text", "different entry"))
    assert fingerprint("summarizer.build_domains", first) != fingerprint("summarizer.build_domains", changed)


def test_demo_close_matches_real_capture_without_api_calls(tmp_path):
    env = os.environ.copy()
    env.update(MC_MOCK="1", MC_AUTHOR_NAME="Jordan", ANTHROPIC_API_KEY="",
               MC_DISABLED_CATEGORIES="",
               MC_SPEND_FILE=str(tmp_path / "spend.json"))
    for key, directory in {
        "JOURNAL": "journal_entries", "CHROMA": "chroma_data",
        "ENTITY": "entity_graph", "SUMMARY": "summaries",
        "CATEGORY": "categories", "PATTERN": "patterns",
        "DREAM": "dreams", "SESSION": "sessions",
    }.items():
        env[f"MC_{key}_DIR"] = str(tmp_path / directory)
    install = subprocess.run(
        [sys.executable, str(ROOT / "seed_corpus/import_seed_corpus.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert install.returncode == 0, install.stdout + install.stderr
    verify = subprocess.run([sys.executable, str(ROOT / "tests/verify_demo_close.py"), str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert verify.returncode == 0, verify.stdout + verify.stderr
