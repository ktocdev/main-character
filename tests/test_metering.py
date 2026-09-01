"""
The cost meter: what it counts, how it prices it, and where it files it.

The number this produces is the one a cloner uses to answer "what will this
cost me?", so the failure that matters is not a crash -- it is a total that
is quietly wrong, which nothing in the UI can reveal.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metering


class FakeUsage:
    """Only the fields a given response actually carries. A real response
    omits the cache fields entirely when nothing was cached, and the mock
    client has never had them -- so absence is the normal case, not an edge."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class FakeResponse:
    def __init__(self, usage):
        self.usage = usage


class FakeStream:
    def __init__(self, usage, chunks=("a", "b")):
        self._final = FakeResponse(usage)
        self._chunks = chunks
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, usage):
        self.usage = usage
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return FakeResponse(self.usage)

    def stream(self, **kwargs):
        self.last_stream = FakeStream(self.usage)
        return self.last_stream


class FakeClient:
    def __init__(self, usage):
        self.messages = FakeMessages(usage)
        self.api_key = "not-touched"


@pytest.fixture(autouse=True)
def clean():
    metering.reset()
    yield
    metering.reset()


# ---- pricing ----

def test_fresh_input_and_output_price_at_the_table_rate():
    import config
    rate = config.MODEL_PRICES["claude-sonnet-5"]
    usage = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
    assert metering.price("claude-sonnet-5", usage) == pytest.approx(rate["in"])
    usage = {"input": 0, "output": 1_000_000, "cache_write": 0, "cache_read": 0}
    assert metering.price("claude-sonnet-5", usage) == pytest.approx(rate["out"])


def test_cached_input_is_not_priced_as_fresh_input():
    """The companion caches its system prompt, so from the second turn on
    most of its input is a cache read. Pricing those at the fresh rate would
    overstate every long conversation -- the exact sessions someone checks
    the meter about."""
    fresh = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
    cached = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 1_000_000}
    written = {"input": 0, "output": 0, "cache_write": 1_000_000, "cache_read": 0}
    base = metering.price("claude-opus-5", fresh)
    assert metering.price("claude-opus-5", cached) == pytest.approx(base * 0.10)
    assert metering.price("claude-opus-5", written) == pytest.approx(base * 1.25)


def test_an_unpriced_model_costs_nothing_rather_than_raising():
    """MODEL_PRICES is hand-maintained and will go stale. A model missing
    from it should cost 0.0 in a display figure, not take down the call."""
    usage = {"input": 999, "output": 999, "cache_write": 0, "cache_read": 0}
    assert metering.price("claude-imaginary-9", usage) == 0.0


def test_every_model_offered_in_settings_is_priced():
    """The pickers offer these; an unpriced one silently meters as free."""
    import config
    for model in config.MODEL_EFFORT_LEVELS:
        usage = {"input": 1000, "output": 1000, "cache_write": 0, "cache_read": 0}
        assert metering.price(model, usage) > 0, model


# ---- counting ----

def test_a_call_adds_its_usage_to_the_running_total():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=20)))
    client.messages.create(model="claude-sonnet-5", messages=[])
    bucket = metering.totals()["processing"]
    assert bucket["calls"] == 1
    assert bucket["input"] == 10 and bucket["output"] == 20
    assert bucket["tokens"] == 30
    assert bucket["dollars"] > 0


def test_usage_missing_the_cache_fields_counts_as_zero_not_a_crash():
    """The mock client's usage object has only input/output. So does a real
    response that cached nothing."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=5, output_tokens=5)))
    client.messages.create(model="claude-sonnet-5", messages=[])
    assert metering.totals()["processing"]["cache_read"] == 0


def test_a_response_with_no_usage_at_all_is_skipped_quietly():
    client = metering.wrap(FakeClient(None))
    client.messages.create(model="claude-sonnet-5", messages=[])
    assert metering.totals()["total"]["calls"] == 0


def test_totals_count_cached_tokens_in_the_headline_figure():
    """Dollars are computed from all four counters, so the token figure shown
    beside them has to be too, or the two numbers describe different calls."""
    client = metering.wrap(FakeClient(FakeUsage(
        input_tokens=1, output_tokens=2,
        cache_creation_input_tokens=4, cache_read_input_tokens=8)))
    client.messages.create(model="claude-sonnet-5", messages=[])
    assert metering.totals()["processing"]["tokens"] == 15


def test_calls_accumulate_rather_than_replace():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=10)))
    for _ in range(3):
        client.messages.create(model="claude-sonnet-5", messages=[])
    assert metering.totals()["processing"]["calls"] == 3
    assert metering.totals()["processing"]["input"] == 30


def test_reset_clears_every_bucket():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=10)))
    client.messages.create(model="claude-sonnet-5", messages=[])
    metering.reset()
    assert metering.totals()["total"] == {"calls": 0, "tokens": 0, "dollars": 0.0}


# ---- attribution ----

def _call_as(module_name, client, **kwargs):
    """Run a create() whose calling frame belongs to `module_name`.

    Bucketing reads the stack, so a test that calls create() directly is
    always attributed to the test file. Compiling with a filename is how the
    real module boundary gets reproduced without importing companion.py and
    dragging in a collection, an entity index and a conversation."""
    code = compile("client.messages.create(**kwargs)", f"{module_name}.py", "exec")
    exec(code, {"client": client, "kwargs": kwargs})


def test_a_companion_call_is_filed_under_companion():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=10)))
    _call_as("companion", client, model="claude-opus-5", messages=[])
    assert metering.totals()["companion"]["calls"] == 1
    assert metering.totals()["processing"]["calls"] == 0


def test_the_same_client_object_serves_both_buckets():
    """server.py builds one client at startup and passes it to the companion
    *and* to sessions.close_session, whose title call is processing work.
    Bucketing by client instance would file that title under companion and
    make the split meaningless -- which is why the stack decides."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=10)))
    _call_as("companion", client, model="claude-opus-5", messages=[])
    _call_as("sessions", client, model="claude-haiku-4-5", messages=[])
    totals = metering.totals()
    assert totals["companion"]["calls"] == 1
    assert totals["processing"]["calls"] == 1
    assert totals["total"]["calls"] == 2


def test_an_unrecognized_caller_still_lands_in_the_total():
    """Spend that cannot be attributed is still spend. Dropping it would make
    the headline figure quietly low, which is the one failure the meter must
    not have."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=10, output_tokens=10)))
    _call_as("some_future_module", client, model="claude-sonnet-5", messages=[])
    assert metering.totals()["total"]["calls"] == 1


# ---- streaming ----

def test_a_streamed_reply_is_counted_when_its_final_message_is_read():
    """The companion streams. If streaming went uncounted, the single most
    expensive thing in the app would be invisible to the meter."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=100, output_tokens=200)))
    with client.messages.stream(model="claude-opus-5", messages=[]) as stream:
        list(stream.text_stream)
        stream.get_final_message()
    assert metering.totals()["processing"]["input"] == 100


def test_a_stream_is_counted_once_even_if_asked_twice():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=100, output_tokens=200)))
    with client.messages.stream(model="claude-opus-5", messages=[]) as stream:
        stream.get_final_message()
        stream.get_final_message()
    assert metering.totals()["processing"]["calls"] == 1


def test_a_stream_nobody_finalizes_is_still_counted_on_exit():
    """Both current call sites read the final message, but a future one that
    forgets should cost the meter accuracy, not silence."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=100, output_tokens=200)))
    with client.messages.stream(model="claude-opus-5", messages=[]) as stream:
        list(stream.text_stream)
    assert metering.totals()["processing"]["calls"] == 1


def test_a_stream_that_raises_does_not_replace_the_error():
    """Metering runs in a finally-ish position. An exception from the meter
    would mask the real failure, which is a debugging cost paid forever."""
    class Exploding(FakeStream):
        def get_final_message(self):
            raise RuntimeError("stream is unusable")

    stream = metering._MeteredStream(
        Exploding(FakeUsage(input_tokens=1, output_tokens=1)),
        "processing", "claude-opus-5")
    with pytest.raises(ValueError, match="the real error"):
        with stream:
            raise ValueError("the real error")
    # ...and the same stream on a clean exit swallows the metering failure
    # rather than turning a working call into a broken one.
    clean_stream = metering._MeteredStream(
        Exploding(FakeUsage(input_tokens=1, output_tokens=1)),
        "processing", "claude-opus-5")
    with clean_stream:
        pass
    assert metering.totals()["total"]["calls"] == 0


def test_the_stream_context_manager_is_still_a_context_manager():
    inner = FakeStream(FakeUsage(input_tokens=1, output_tokens=1))
    with metering._MeteredStream(inner, "processing", "claude-opus-5"):
        pass
    assert inner.entered and inner.exited


# ---- the wrapper is transparent ----

def test_the_wrapper_passes_the_request_through_unchanged():
    """Nothing here may alter a call. A meter that changed a request would
    be measuring something the app does not actually do."""
    raw = FakeClient(FakeUsage(input_tokens=1, output_tokens=1))
    client = metering.wrap(raw)
    client.messages.create(model="claude-opus-5", max_tokens=99,
                           messages=[{"role": "user", "content": "hi"}])
    sent = raw.messages.created[0]
    assert sent == {"model": "claude-opus-5", "max_tokens": 99,
                    "messages": [{"role": "user", "content": "hi"}]}


def test_attributes_the_wrapper_does_not_know_about_fall_through():
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=1, output_tokens=1)))
    assert client.api_key == "not-touched"


# ---- the route ----

@pytest.fixture
def api():
    from fastapi.testclient import TestClient
    import server
    # TrustedHostMiddleware refuses "testserver", so the client has to look
    # like the local address the app is actually served on.
    with TestClient(server.app, base_url="http://127.0.0.1:8144") as c:
        yield c


def test_the_route_reports_a_fresh_session_as_zero(api):
    body = api.get("/api/cost").json()
    assert body["total"] == {"calls": 0, "tokens": 0, "dollars": 0.0}
    assert body["companion"]["calls"] == 0 and body["processing"]["calls"] == 0


def test_the_route_reports_the_split_not_just_the_sum(api):
    """The split is the whole reason the meter exists: it is what makes
    "switch processing to Haiku and watch the number drop" observable."""
    client = metering.wrap(FakeClient(FakeUsage(input_tokens=1000,
                                                output_tokens=1000)))
    _call_as("companion", client, model="claude-opus-5", messages=[])
    _call_as("summarizer", client, model="claude-haiku-4-5", messages=[])
    body = api.get("/api/cost").json()
    assert body["companion"]["calls"] == 1 and body["processing"]["calls"] == 1
    assert body["companion"]["dollars"] > body["processing"]["dollars"]
    assert body["total"]["dollars"] == pytest.approx(
        body["companion"]["dollars"] + body["processing"]["dollars"])


def test_the_route_says_the_figure_is_an_estimate(api):
    """List prices from a hand-kept table are not a bill, and the UI can only
    say so if the payload does."""
    assert api.get("/api/cost").json()["estimated"] is True


def test_the_route_reports_whether_the_figure_is_mock(api):
    """In mock mode the figure still climbs, so the popover needs the payload
    to say the spend is not real -- the same claim the monthly card makes.
    Asserted against config rather than a literal so it holds whichever mode
    the suite runs in."""
    import config
    assert api.get("/api/cost").json()["mock"] is config.MOCK_MODE


def test_the_route_names_the_models_the_figures_came_from(api):
    import config
    body = api.get("/api/cost").json()
    assert body["models"]["companion"] == config.MC_COMPANION_MODEL
    assert body["models"]["processing"] == config.MC_PROCESSING_MODEL
    assert body["labels"][config.MC_COMPANION_MODEL]


def test_the_route_never_reports_the_api_key(api):
    """Same constraint as GET /api/settings. A new route reading config is a
    new chance to leak it."""
    blob = api.get("/api/cost").text
    assert "sk-" not in blob and "ANTHROPIC_API_KEY" not in blob
