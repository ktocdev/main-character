# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Stop the running journal from a terminal and free its port -- including one
whose terminal is gone, which otherwise has to be found and killed by PID.

It asks the server to stop through /api/shutdown, which has the same guard
as /api/restart: work in flight (a reply still arriving, the memory
pipeline's tagging and summaries) is refused, never interrupted.

    python scripts/stop_journal.py
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

# The port the journal is actually on: .env can move it, same as config.py.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
PORT = int(os.getenv("MC_PORT", "8144"))
BASE = f"http://127.0.0.1:{PORT}"


def instance():
    try:
        with urllib.request.urlopen(f"{BASE}/api/status", timeout=3) as r:
            return json.load(r).get("instance")
    except (OSError, ValueError):
        return None


def listening():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        return True
    except OSError:
        return False


def main():
    if instance() is None:
        if listening():
            print(f"something is using port {PORT}, but it is not a journal, "
                  "so this will not stop it")
            return 1
        print(f"no journal running at {BASE}")
        return 0

    req = urllib.request.Request(f"{BASE}/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            print("this journal was started before `journal stop` existed. "
                  "Stop it with Ctrl+C in its terminal, or end its python "
                  "process in Task Manager.")
            return 1
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

    print("stopping...", end="", flush=True)
    for _ in range(40):
        time.sleep(0.5)
        if not listening():
            print(f" stopped. Port {PORT} is free.")
            return 0
        print(".", end="", flush=True)
    print(f"\nthe journal is still on port {PORT}. Try again in a moment.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
