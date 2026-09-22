# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canned replies are the demo's, and only the demo's.

`MC_MOCK` used to be a documented `.env` toggle, which meant the one
combination nothing wants was a line away: canned replies against your own
journal, where storage is not mocked and the writes are real. Mock mode is
now read from the process environment before `.env` is loaded, so the demo,
`run_demo.sh`, this suite and CI can all still set it, and a `.env` cannot.

Each case runs in a fresh interpreter because `config.py` reads the
environment once, at import.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

# What a .env does, stripped to the part that matters: values are put into
# os.environ only where the environment has not already spoken. Patched in
# before config imports it, the way scripts/build_web_demo.py switches it
# off for the same reason -- every module calls it at import.
AS_IF_DOTENV_SET_IT = """
import os, sys
sys.path.insert(0, %r)
import dotenv
def fake_load(*a, **k):
    os.environ.setdefault("MC_MOCK", "1")
    return True
dotenv.load_dotenv = fake_load
import config
print("MOCK_MODE", config.MOCK_MODE)
print("LEFT_IN_ENV", "MC_MOCK" in os.environ)
""" % str(ROOT)

FROM_THE_ENVIRONMENT = """
import sys
sys.path.insert(0, %r)
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
import config
print("MOCK_MODE", config.MOCK_MODE)
""" % str(ROOT)


def _run(script, **env_overrides):
    env = os.environ.copy()
    # conftest sets it for the suite; each case decides for itself.
    env.pop("MC_MOCK", None)
    env.update(env_overrides)
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return done


def test_dotenv_cannot_turn_mock_on():
    done = _run(AS_IF_DOTENV_SET_IT)
    assert "MOCK_MODE False" in done.stdout, (
        "a .env set MC_MOCK=1 and it was honoured: canned replies would be "
        "running against the journal that .env configures")


def test_dotenv_mock_is_reported_not_swallowed():
    """Ignoring it silently is worse than not supporting it: someone who
    wrote MC_MOCK=1 expects canned replies, and the bill is the wrong place
    to find out they were live."""
    done = _run(AS_IF_DOTENV_SET_IT)
    assert "MC_MOCK in .env is ignored" in done.stderr
    assert "--demo" in done.stderr, "say where the supported no-key path is"


def test_dotenv_mock_does_not_reach_child_processes():
    """/api/restart and the demo boot spawn children off os.environ. A value
    left there would be inherited and read as the environment speaking."""
    done = _run(AS_IF_DOTENV_SET_IT)
    assert "LEFT_IN_ENV False" in done.stdout


def test_the_environment_still_turns_mock_on():
    """The demo (server.SEED_ENV), run_demo.sh, this suite and CI."""
    done = _run(FROM_THE_ENVIRONMENT, MC_MOCK="1")
    assert "MOCK_MODE True" in done.stdout


def test_the_environment_still_turns_mock_off():
    done = _run(FROM_THE_ENVIRONMENT, MC_MOCK="0")
    assert "MOCK_MODE False" in done.stdout
