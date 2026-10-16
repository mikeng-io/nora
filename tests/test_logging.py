"""structlog-backed logging: JSON shape, redaction (R-16), and the tag_id
contextvar. Integration-style throughout — `setup_logging` wires structlog
onto the root stdlib logger, so the realistic way to exercise it is a real
`logging.getLogger(...)` call followed by reading the stream back, not
constructing processors in isolation.

The sink is STDOUT, so `_configure` hands back a buffer standing
in for it rather than a path. `setup_logging` binds `sys.stdout` at call time,
so the redirect has to be in place BEFORE it runs — which is why the fixture
patches first and configures second.
"""

import io
import json
import logging
from contextlib import ExitStack
from unittest.mock import patch

from nora.obs.logging import bind_tag_id, clear_tag_id, setup_logging


class _Stdout:
    """The captured stream, and the lines written to it."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def read_text(self) -> str:
        logging.shutdown()
        return self.buffer.getvalue()

    def splitlines(self) -> list[str]:
        return self.read_text().splitlines()


def _configure(tmp_path):
    from tests.builders import make_logging_config

    out = _Stdout()
    cfg = make_logging_config()
    stack = ExitStack()
    stack.enter_context(patch("sys.stdout", out.buffer))
    setup_logging(level=cfg.level, format=cfg.format, secrets=["sk-live-x"])
    stack.close()
    return out


def _last_line(path) -> dict:
    lines = path.read_text().splitlines()
    return json.loads(lines[-1])


def test_setup_logging_writes_json_lines_to_stdout(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("hello %s", "sk-live-x")
    text = path.read_text()
    assert "sk-live-x" not in text
    assert json.loads(text.splitlines()[0])["message"]


def test_formatter_emits_one_json_object_per_line(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("hello")
    logging.getLogger("nora.test").info("world")
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "hello"
    assert json.loads(lines[1])["message"] == "world"


def test_formatter_includes_level_logger_and_timestamp(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("x")
    record = _last_line(path)
    assert record["level"] == "INFO"
    assert record["logger"] == "nora.test"
    assert record["ts"]


def test_the_ts_is_an_rfc_3339_instant_not_a_naive_local_string(tmp_path):
    """`ts` is machine-read — the JSON lines are ingested by a log store that
    sorts and windows on it — so it must be RFC 3339: an instant that carries a
    UTC offset, not a naive `2026-08-07T15:06:42` a reader has to guess the zone
    of. `fromisoformat` parses the value and it MUST come back timezone-aware.
    Drop the `Z` from the TimeStamper format and `tzinfo` is None, so this is
    red. (This is a LOG timestamp; the prose clock in `prefill._now_line` and
    the operator report in `nora.app.report` are human-facing and stay prose.)
    """
    from datetime import datetime

    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("x")
    ts = _last_line(path)["ts"]
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, f"ts {ts!r} carries no UTC offset — not RFC 3339"


def test_formatter_includes_the_tagging_path_fields(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info(
        "tag start",
        extra={
            "tag_id": "t1", "conversation_id": "g1", "sender_id": "+1",
            "person_id": "miles", "doctrine_sha256": "abc", "stage": "invoke",
            "latency_ms": 42, "trace_id": "tr1",
        },
    )
    record = _last_line(path)
    for key in ("tag_id", "conversation_id", "sender_id", "person_id",
                "doctrine_sha256", "stage", "latency_ms", "trace_id"):
        assert key in record


def test_secret_never_logged_in_the_message(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("key is sk-live-x")
    assert "sk-live-x" not in path.read_text()


def test_secret_never_logged_in_args(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("key is %s", "sk-live-x")
    assert "sk-live-x" not in path.read_text()


def test_secret_never_logged_in_exception_text(tmp_path):
    path = _configure(tmp_path)
    try:
        raise RuntimeError("auth failed for sk-live-x")
    except RuntimeError:
        logging.getLogger("nora.test").exception("boom")
    text = path.read_text()
    assert "sk-live-x" not in text
    assert "exception" in _last_line(path)


def test_secret_never_logged_when_passed_as_a_non_string_arg(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("failed: %s", RuntimeError("auth failed for sk-live-x"))
    assert "sk-live-x" not in path.read_text()


def test_secret_never_logged_in_extras(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("x", extra={"raw_json": '{"key":"sk-live-x"}'})
    assert "sk-live-x" not in path.read_text()


def test_an_empty_secret_list_is_a_no_op(tmp_path):
    """The redactor with nothing to redact still lets the line through — the
    failure this guards is a processor that swallows every record when the
    secret list is empty."""
    from tests.builders import make_logging_config

    out = _Stdout()
    cfg = make_logging_config()
    with patch("sys.stdout", out.buffer):
        setup_logging(level=cfg.level, format=cfg.format, secrets=[])
    logging.getLogger("nora.test").info("nothing secret")
    assert "nothing secret" in out.read_text()


def test_tag_id_rides_the_contextvar_without_being_threaded_through(tmp_path):
    """Nothing on this `logging.getLogger(...).info(...)` call mentions
    tag_id — `bind_tag_id` alone is what makes it show up."""
    path = _configure(tmp_path)
    bind_tag_id("tag-42")
    try:
        logging.getLogger("nora.test").info("inside a tag")
    finally:
        clear_tag_id()
    record = _last_line(path)
    assert record["tag_id"] == "tag-42"


def test_clear_tag_id_stops_it_appearing_on_later_lines(tmp_path):
    path = _configure(tmp_path)
    bind_tag_id("tag-42")
    clear_tag_id()
    logging.getLogger("nora.test").info("outside any tag")
    record = _last_line(path)
    assert "tag_id" not in record


def test_a_third_party_logger_lands_in_the_same_stream(tmp_path):
    """The stdlib bridge: a logger name this codebase never defines (standing
    in for a third-party library) still renders through the same JSON
    pipeline — logger name, level, redaction included."""
    path = _configure(tmp_path)
    logging.getLogger("some.vendor.library").warning("vendor said sk-live-x")
    record = _last_line(path)
    assert record["logger"] == "some.vendor.library"
    assert record["level"] == "WARNING"
    assert "sk-live-x" not in path.read_text()


# --- OTel severity and exception fields -------------------------------------


def test_every_level_carries_the_otel_severity_number(tmp_path):
    """`level` sorts alphabetically — ERROR before INFO before WARNING — so
    "at least a warning" is not expressible as a comparison over it. The OTel
    Logs Data Model's SeverityNumber is, and its BANDS are what carry the
    meaning: >= 17 is an error whatever the level was called.

    Asserted per level rather than on one line, because the mapping is a table
    and a table with one row checked is a table nobody checked.
    """
    path = _configure(tmp_path)
    # DEBUG is below the configured root level, so without this the DEBUG row
    # would simply be absent and the test would be checking four of five.
    logging.getLogger().setLevel(logging.DEBUG)
    log = logging.getLogger("nora.test")
    log.debug("d"), log.info("i"), log.warning("w"), log.error("e"), log.critical("c")
    logging.shutdown()
    by_level = {
        json.loads(line)["level"]: json.loads(line).get("severity_number")
        for line in path.read_text().splitlines()
    }
    assert by_level == {
        "DEBUG": 5, "INFO": 9, "WARNING": 13, "ERROR": 17, "CRITICAL": 21,
    }, "the severity bands no longer match the OTel Logs Data Model"


def test_an_unmapped_level_gets_no_severity_number_rather_than_a_guessed_one(tmp_path):
    """A custom level is genuinely outside this vocabulary. Absent means "not
    stated"; a number would put it in a band nobody chose — the same
    three-state discipline R-27 imposes on every other instrument.
    """
    path = _configure(tmp_path)
    logging.addLevelName(25, "NOTICE")
    logging.getLogger("nora.test").log(25, "something in between")
    record = _last_line(path)
    assert record["level"] == "NOTICE"
    assert "severity_number" not in record


def test_an_exception_is_split_into_the_otel_fields(tmp_path):
    """`exception` is one multi-line blob with the type and message buried in
    its last line, so "how many TimeoutErrors since the deploy" is a substring
    search. The OTel exception semconv names the three parts; the stacktrace is
    kept WHOLE and the blob is left in place so runbook greps still work.
    """
    path = _configure(tmp_path)
    try:
        raise TimeoutError("the gate did not answer")
    except TimeoutError:
        logging.getLogger("nora.test").exception("gate call failed")
    record = _last_line(path)
    assert record["exception.type"] == "TimeoutError"
    assert record["exception.message"] == "the gate did not answer"
    assert "Traceback (most recent call last)" in record["exception.stacktrace"]
    # The original blob survives — removing it would break every saved grep.
    assert "Traceback (most recent call last)" in record["exception"]


def test_a_secret_inside_a_traceback_is_redacted_in_the_derived_fields_too(tmp_path):
    """R-16 holds across the new keys: splitting the traceback must not create
    a third place a secret can sit.

    **Not an ordering test.** Either order of `_exception_fields` and the
    redactor is safe: the redactor walks EVERY key (so a pre-split leak is
    cleaned) and a post-split copy is taken from already-redacted text. What
    this guards is the property itself — a new key that the redactor did not
    walk, say — not an ordering it cannot see.
    """
    path = _configure(tmp_path)
    try:
        raise ValueError("token sk-live-x rejected")
    except ValueError:
        logging.getLogger("nora.test").exception("call failed")
    text = path.read_text()
    assert "sk-live-x" not in text, "the traceback split leaked a secret"
    record = _last_line(path)
    assert record["exception.type"] == "ValueError"
    assert "***REDACTED***" in record["exception.message"]


def test_a_line_with_no_exception_gains_no_exception_fields(tmp_path):
    path = _configure(tmp_path)
    logging.getLogger("nora.test").info("nothing went wrong")
    record = _last_line(path)
    assert not [k for k in record if k.startswith("exception")]


# --- the two highest-volume log sources ------------------------------------


class _Counters:
    """A metrics stand-in whose two numbers the test moves deliberately."""

    def __init__(self) -> None:
        self.values = {"nora_messages_total": 0.0, "nora_tags_total": 0.0}

    def get(self, name: str) -> float:
        return self.values[name]


async def test_the_heartbeat_drops_to_debug_when_nothing_moved(tmp_path):
    """A heartbeat saying at INFO that two counters had not changed since the
    previous minute would dominate the log; it drops to DEBUG when nothing moved.

    Three beats: the FIRST is INFO whatever it says — it records where the
    counters started, which is what makes every later delta readable. The
    second moves them and is INFO. The third does not and is DEBUG.

    Read back off the FILE at the configured level rather than off a caplog
    record, because "does not appear in the log" is the property that matters
    and a captured-but-filtered record would satisfy a level assertion while
    still costing the line.
    """
    import asyncio
    import contextlib

    from nora.obs.logging import heartbeat

    path = _configure(tmp_path)
    counters = _Counters()
    task = asyncio.create_task(heartbeat(counters, interval=0.01))
    await asyncio.sleep(0.03)          # beat 1 — the baseline
    counters.values["nora_messages_total"] = 3.0
    await asyncio.sleep(0.03)          # beat 2 — moved
    await asyncio.sleep(0.03)          # beat 3 — idle
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    logging.shutdown()

    beats = [
        json.loads(line) for line in path.read_text().splitlines()
        if json.loads(line)["logger"] == "nora.heartbeat"
    ]
    assert beats, "the heartbeat wrote nothing at all — the loop did not run"
    # CONTROL: a beat that MOVED is still INFO. Without this, a build that
    # silenced the heartbeat entirely would pass the assertion below.
    assert any(b["level"] == "INFO" for b in beats), (
        "no heartbeat reached INFO — a heartbeat that is never visible is not "
        "a quieter heartbeat, it is a deleted one"
    )
    assert all(b["messages_total"] is not None for b in beats)
    # And the idle beats did not reach the file at the configured INFO level.
    assert all(b["level"] == "INFO" for b in beats), (
        "a DEBUG heartbeat was written at the configured level"
    )
    assert len(beats) <= 2, (
        f"{len(beats)} INFO heartbeats for two distinct counter values — the "
        "idle beat is still being logged at INFO"
    )


async def test_the_health_server_writes_no_per_request_access_line(tmp_path):
    """53% of the deployment's log (108,441 lines of 205,099 over 37 days) was
    `aiohttp.access` at INFO for a server that routes two paths, polled by a
    container healthcheck.

    A REAL request against the real helper, read back off the log file. Asserting
    `runner._kwargs["access_log"] is None` would test the argument this file
    passes rather than the line it stops — it would stay green if aiohttp
    changed which kwarg it honours, and it reaches into a private attribute.
    """
    import aiohttp
    from aiohttp import web

    from nora.obs.health import serve

    path = _configure(tmp_path)
    app = web.Application()
    app.router.add_get("/health", lambda _r: web.Response(text="ok"))
    runner = await serve(app, port=0)
    try:
        port = runner.addresses[0][1]
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"http://127.0.0.1:{port}/health") as response,
        ):
                # CONTROL: the request actually happened. Without it, a server
                # that failed to start would satisfy the assertion below.
                assert response.status == 200
                assert await response.text() == "ok"
    finally:
        await runner.cleanup()
    logging.shutdown()

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert not [ln for ln in lines if ln["logger"].startswith("aiohttp.access")], (
        "the health/metrics server is logging one line per poll again"
    )


def test_the_split_runs_after_the_traceback_is_formatted(tmp_path):
    """THE ordering that has teeth. `_exception_fields` reads the `exception`
    key that `format_exc_info` writes; placed before it, the key does not exist
    yet, no field is written, and nothing anywhere fails — the three fields
    would simply never appear. Probed by swapping the two: this goes red, and
    every other test in this file stays green.
    """
    import structlog

    from nora.obs import logging as mod

    # By IDENTITY, not by name: structlog exposes `format_exc_info` as an
    # `ExceptionRenderer` instance, so a name-based lookup finds neither the
    # stage nor its absence and the test reports "chain changed" for a chain
    # that is fine.
    chain = mod._shared_processors([])
    formatter = next(
        (i for i, p in enumerate(chain) if p is structlog.processors.format_exc_info),
        None,
    )
    splitter = next(
        (i for i, p in enumerate(chain) if p is mod._exception_fields), None,
    )
    assert formatter is not None and splitter is not None, (
        f"the processor chain no longer contains both stages: {chain}"
    )
    assert formatter < splitter, (
        "`_exception_fields` runs before the traceback is formatted, so the "
        "`exception` key it reads does not exist yet and the OTel fields are "
        "silently never written"
    )
