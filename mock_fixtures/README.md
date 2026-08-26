# Mock fixtures

Captured Claude responses that `mock_client.py` replays when `MC_MOCK=1`.
One file per call type, named `<module>.<function>.json` after the
pipeline function that made the call:

```json
{ "responses": ["...", "..."] }
```

Selection is by hash of the prompt, so the same input replays the same
response on every run — mock mode is reproducible, not randomized. Where
no file exists for a call type, `mock_client.py` synthesizes a shape-valid
response from that call's own `json_schema`, visibly marked `[mock]`. The
app works either way; fixtures are what make it look real.

**These ship publicly — they are source, not data.**

- Capture from the curated seed corpus only, never from a real journal.
  `capture_fixtures.py` refuses to run without an explicit flag saying so.
- Run `python capture_fixtures.py --check` before committing anything
  here. It leak-greps for keys, emails, phone numbers, and home paths.

Regenerate with:

```
.venv\Scripts\python.exe capture_fixtures.py --i-am-running-the-seed-corpus
.venv\Scripts\python.exe capture_fixtures.py --check
```
