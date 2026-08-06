"""structlog-backed logging: JSON shape, redaction (R-16), and the tag_id
contextvar. Integration-style throughout — `setup_logging` wires structlog
onto the root stdlib logger, so the realistic way to exercise it is a real
`logging.getLogger(...)` call followed by reading the file back, not
constructing processors in isolation.
"""

import json
import logging

from nora.obs.logging import bind_tag_id, clear_tag_id, setup_logging


def _configure(tmp_path):
    from tests.builders import make_logging_config

    path = tmp_path / "nora.log"
    cfg = make_logging_config(path=path)
    setup_logging(level=cfg.level, path=cfg.path, format=cfg.format, secrets=["sk-live-x"])
    return path


def _last_line(path) -> dict:
    logging.shutdown()
    lines = path.read_text().splitlines()
    return json.loads(lines[-1])


def test_setup_logging_writes_to_the_configured_path(tmp_path):
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
    from tests.builders import make_logging_config

    path = tmp_path / "nora.log"
    cfg = make_logging_config(path=path)
    setup_logging(level=cfg.level, path=cfg.path, format=cfg.format, secrets=[])
    logging.getLogger("nora.test").info("nothing secret")
    assert "nothing secret" in path.read_text()


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
