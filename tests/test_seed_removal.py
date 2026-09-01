"""The seed-removal script's key rules.

Both of these are here because both were wrong once, in the same way: an
entry is filed under a *sanitised* name on disk and under its real title in
metadata, and code that knows only one of them deletes the markdown while
leaving the chunks, the observation blocks and the summaries behind. That
failure is silent -- the entry keeps showing up in History with nothing
under it -- so it needs a test rather than a careful reading.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "remove_seed_corpus", ROOT / "scripts" / "remove_seed_corpus.py")
remover = importlib.util.module_from_spec(spec)
sys.modules["remove_seed_corpus"] = remover
spec.loader.exec_module(remover)

import entities


def test_the_key_matches_the_one_the_pipeline_writes():
    for date, title in [
        ("2026-09-09", "Lena's referral"),      # apostrophe -> dash
        ("2026-08-08", "Walk with Dev"),        # spaces -> dashes
        ("2026-08-25", "Dream - 2026-08-25 03:15"),
        ("2026-01-01", "A title long enough to be cut at forty characters"),
    ]:
        conv = {"date": date, "title": title}
        assert remover.key_of(date, title) == entities.conversation_cache_key(conv)


def test_both_spellings_of_a_title_are_looked_for():
    """The filename drops the apostrophe; the `# heading` keeps it. Chroma is
    keyed by the second, so finding only the first deletes the file and
    leaves the chunks."""
    keys = remover.seed_entries()
    assert ("2026-09-09", "Lenas referral") in keys       # as it is filed
    assert ("2026-09-09", "Lena's referral") in keys      # as it is titled
