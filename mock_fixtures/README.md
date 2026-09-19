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

The shipped demo's close transition has a more precise replay under
`demo_close/`: each JSON file is keyed by a SHA-256 fingerprint of the call
type, messages, system context, and output schema. These exact recordings
take priority over the generic response buckets. Only the wall-clock date
in domain-summary prompts is normalized; entry dates and content still have
to match. Changing the model setting does not change this recorded input.

The starting demo contains the September 14 live seed and September 15–17
as an open chat. Closing that unchanged chat replays a real recorded close:
a September 17 candidate, new category/entity extractions, and updated
weekly, domain, and entry summaries. Patterns and organic categories update
through their normal explicit refresh buttons; those calls are recorded too.
The seed remains unchanged until the candidate is uploaded.

To recapture, run `seed_corpus/capture_demo_close.py --live --phase before`,
then `--live --phase close`, then `--live --phase extras`. These are paid
authoring calls using only the fictional corpus, with all data and the spend
ledger redirected under `seed_corpus/build/close_capture/`. Successful calls
are checkpointed immediately and reused on retries. An existing completed
capture must be moved aside before starting a new one.

Run `--phase promote` to publish the starting derived files, recordings, and
content manifests into the working tree. The after-state files are produced
by the real application during playback, not copied over the running demo.
`tests/test_demo_close_replay.py` installs a fresh demo, calls the real close
endpoint with no API key, requires every model call to match an exact
recording, and compares all derived files with the real captured states.

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
