# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Restart the running journal from a terminal -- the Settings button's
"restart the journal", for when the browser isn't the handiest place.

It asks the server to restart itself through the same /api/restart route the
button uses, so it gets the same guard: work in flight (a reply still
arriving, the memory pipeline's tagging and summaries) is refused, never
interrupted. Ctrl+C and a fresh start has no such guard and throws that
paid-for work away.

The replacement process keeps printing in whichever terminal the journal was
started from, not this one. Like the button, it waits for /api/status to
answer from a *different* instance -- a draining server keeps answering on its
way out, so "it responded" is not proof the new one is up.

    python scripts/restart_journal.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

# The port the journal is actually on: .env can move it, same as config.py.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
BASE = f"http://127.0.0.1:{int(os.getenv('MC_PORT', '8144'))}"


def instance():
    try:
        with urllib.request.urlopen(f"{BASE}/api/status", timeout=3) as r:
            return json.load(r).get("instance")
    except (OSError, ValueError):
        return None


def main():
    before = instance()
    if before is None:
        print(f"no journal answering at {BASE} -- start it with: journal start")
        return 1

    req = urllib.request.Request(
        f"{BASE}/api/restart", data=b'{"into": "journal"}', method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.load(r)
    except urllib.error.HTTPError as e:
        # 409 (busy), 501 (not started from server.py): the server's own
        # wording says what to do next.
        try:
            print(json.load(e).get("error", e))
        except ValueError:
            print(e)
        return 1
    except OSError as e:
        print(f"could not reach the journal: {e}")
        return 1

    print("restarting...", end="", flush=True)
    for _ in range(90):
        time.sleep(0.7)
        now = instance()
        if now and now != before:
            print(f" back at {BASE}")
            return 0
        print(".", end="", flush=True)
    print("\nthe journal did not come back. Start it again with: journal start")
    return 1


if __name__ == "__main__":
    sys.exit(main())
