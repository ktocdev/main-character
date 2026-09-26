---
name: sandbox-reset
description: Stop the port-8145 first-run sandbox and hand back the command that relaunches it from a clean first run (no key, no journal, no demo), for testing the onboarding wizard and the demo flow. Use when asked to kill, stop or reset 8145, restart the sandbox, or test onboarding or the demo from scratch. Never touches the real journal on 8144.
---

# Sandbox reset — stop 8145, hand back a fresh launch

The sandbox is a copy of this repo under `%TEMP%\mc-wizard` with its own data
dirs, its own `.env`, and no API key, served on **8145**. The real journal is on
**8144** and is never touched by anything here.

## 1. Stop whatever is on 8145

From the repo root:

```
powershell -NoProfile -File .claude/skills/sandbox-reset/stop-sandbox.ps1
```

Relay its status line — it asks the server what it is before stopping it, so you
can tell the user whether they left it on the wizard (`configured=False`), a
configured journal, or the demo instance (`seed_instance=True`).

- Refuses if the port is held by something that isn't python. Report it and
  stop; don't reach for `taskkill` or anything broader.
- Exits 1 if the port is still held after ten seconds of retries. Report the PID;
  don't escalate.
- If the user only asked whether 8145 is up, pass `-DryRun` instead.

**Never stop a powershell process.** The launcher runs in the user's own
terminal, and stopping it closes their window. The script only stops python.

## 2. Hand back the launch command — don't run it

The user launches it in their own terminal so they can watch the log and Ctrl+C
it. Give them, to run from the repo root:

```
powershell -File .claude\skills\sandbox-reset\run-sandbox.ps1
```

→ http://127.0.0.1:8145

Tell them what "fresh" means, briefly: it removes the sandbox's `.env`, `_d\`
and `seed_corpus\install\`, copies every tracked file forward from the repo (so
they're testing current code), and drops `ANTHROPIC_API_KEY` for that process
only. The wizard opens and the demo has to be built again.

For testing search or replies on real entries, add `-CopyJournal`: it replaces
the sandbox's journal with a copy of the real one (every data dir except
`chroma_data`) and rebuilds the index there, locally and free, in under a
minute. Safe while 8144 runs. Combine with `-Keep` to keep the sandbox's key.
`scripts/show_context.py --sandbox` and `scripts/eval_retrieval.py --sandbox`
read that copy.

Mention `-Keep` when the test spans two sittings — e.g. build the demo once, quit,
then test switching back to it — since it skips the wipe.

## Guards the launcher enforces

Both exist because of real incidents; don't work around them.

- **It won't wipe while 8145 is listening.** A wipe under a live server takes the
  markdown and leaves the locked Chroma index — a journal reporting entries with
  nothing behind them. That's why step 1 comes first.
- **It only wipes `mc-wizard` under TEMP**, and refuses to run from the copy of
  itself that the sync puts inside the sandbox.

## Known limit

The embedding model cache lives in the user's home (`~/.cache/chroma`), not the
sandbox, so a reset doesn't clear it. On a machine that has ever embedded
anything, the demo build shows the "around twenty seconds" wording, never the
first-download one.
