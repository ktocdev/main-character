# SPDX-License-Identifier: AGPL-3.0-or-later
"""Subprocess assertions: isolated configuration and real endpoint writes."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
import mock_client
import server
import seed
import sessions
from seed_corpus.demo_close_state import content_manifest


def main():
    root = Path(sys.argv[1])
    expected = json.loads((ROOT / "seed_corpus/demo_close_expected.json").read_text(encoding="utf-8"))
    mock_client.DELAYS = {key: 0 for key in mock_client.DELAYS}
    mock_client.DEFAULT_DELAY = 0
    mock_client.STREAM_CHUNK_DELAY = 0
    calls = []
    misses = []
    original = mock_client._recorded

    def recorded(key, kwargs):
        text = original(key, kwargs)
        calls.append(key)
        if text is None:
            misses.append((key, mock_client.request_fingerprint(key, kwargs)))
            raise AssertionError(f"missing exact recording: {misses[-1]}")
        return text

    mock_client._recorded = recorded

    def check(stage):
        actual = content_manifest(root)
        wanted = expected[stage]
        different = [key for key in set(actual) | set(wanted) if actual.get(key) != wanted.get(key)]
        assert not different, (stage, different)

    with TestClient(server.app, base_url="http://127.0.0.1:8144") as client:
        check("before")
        # the three open-chat entries are saved already, so they count now
        status = client.get("/api/status").json()
        assert (status["entries"], status["open_entries"],
                status["indexed_entries"], status["journal_chunks"]) == (32, 3, 29, 29)
        assert not seed.status()["candidate_exists"]
        assert sessions.load_current()["messages"]
        original_seed = seed.load_seed()
        result = client.post("/api/sessions/close", json={})
        assert result.status_code == 200, result.text
        assert not misses, misses
        assert not server._CLOSE["failed"], server._CLOSE
        check("after")
        candidate = seed.CANDIDATE_FILE.read_text(encoding="utf-8")
        assert candidate != original_seed
        assert "September 17, 2026" in candidate
        assert "9/15" in candidate and "9/16" in candidate and "9/17" in candidate
        assert "Fern" in candidate and "27" in candidate
        assert seed.load_seed() == original_seed
        assert sessions.load_current()["messages"] == []
        assert sessions.load_archive(result.json()["key"])["messages"]
        # closing moves them into journal memory without counting them again
        status = client.get("/api/status").json()
        assert (status["entries"], status["open_entries"],
                status["indexed_entries"], status["journal_chunks"]) == (32, 0, 32, 32)
        docs = server.STATE["collection"].get()
        new = [m for m in docs["metadatas"] if m["date"] >= "2026-09-15"]
        assert len(new) == 3
        assert {m["date"] for m in new} == {"2026-09-15", "2026-09-16", "2026-09-17"}
        before = len(calls)
        assert client.post("/api/sessions/close", json={}).status_code == 400
        assert len(calls) == before  # no second archive or processing of empty chat
        assert client.post("/api/patterns/build", json={}).status_code == 200
        assert client.post("/api/organic/scan", json={}).status_code == 200
        assert not misses, misses
        check("after_refresh")
    assert "anthropic" not in sys.modules
    print(f"Verified every captured file and {len(calls)} exact mock calls")


if __name__ == "__main__":
    main()
