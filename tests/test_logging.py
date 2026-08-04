import json
import logging

from nora.obs.logging import JSONFormatter, SecretFilter, setup_logging


def _record(msg, *args, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("nora.test", logging.INFO, __file__, 1, msg, args, None)
    rec.__dict__.update(extra)
    return rec


def test_formatter_emits_one_json_object_per_line():
    out = JSONFormatter().format(_record("hello"))
    assert json.loads(out)["message"] == "hello"
    assert "\n" not in out


def test_formatter_includes_the_tagging_path_fields():
    out = json.loads(JSONFormatter().format(_record(
        "tag start", tag_id="t1", conversation_id="g1", sender_id="+1",
        person_id="miles", persona_sha256="abc", stage="invoke", latency_ms=42,
        trace_id="tr1")))
    for key in ("tag_id", "conversation_id", "sender_id", "person_id",
                "persona_sha256", "stage", "latency_ms", "trace_id"):
        assert key in out


def test_formatter_includes_level_logger_and_timestamp():
    out = json.loads(JSONFormatter().format(_record("x")))
    assert out["level"] == "INFO"
    assert out["logger"] == "nora.test"
    assert out["ts"]


def test_secret_never_logged_in_the_message():
    rec = _record("key is sk-live-abcdef123456")
    SecretFilter(["sk-live-abcdef123456"]).filter(rec)
    assert "sk-live-abcdef123456" not in JSONFormatter().format(rec)


def test_secret_never_logged_in_args():
    rec = _record("key is %s", "sk-live-abcdef123456")
    SecretFilter(["sk-live-abcdef123456"]).filter(rec)
    assert "sk-live-abcdef123456" not in JSONFormatter().format(rec)


def test_secret_never_logged_in_exception_text():
    try:
        raise RuntimeError("auth failed for sk-live-abcdef123456")
    except RuntimeError:
        import sys

        rec = logging.LogRecord("nora.test", logging.ERROR, __file__, 1, "boom",
                                (), sys.exc_info())
    SecretFilter(["sk-live-abcdef123456"]).filter(rec)
    assert "sk-live-abcdef123456" not in JSONFormatter().format(rec)


def test_secret_never_logged_when_passed_as_a_non_string_arg():
    rec = _record("failed: %s", RuntimeError("auth failed for sk-live-abcdef123456"))
    SecretFilter(["sk-live-abcdef123456"]).filter(rec)
    assert "sk-live-abcdef123456" not in JSONFormatter().format(rec)


def test_secret_never_logged_in_extras():
    rec = _record("x", raw_json='{"key":"sk-live-abcdef123456"}')
    SecretFilter(["sk-live-abcdef123456"]).filter(rec)
    assert "sk-live-abcdef123456" not in JSONFormatter().format(rec)


def test_an_empty_secret_list_is_a_no_op():
    rec = _record("nothing secret")
    SecretFilter([]).filter(rec)
    assert "nothing secret" in JSONFormatter().format(rec)


def test_setup_logging_writes_to_the_configured_path(tmp_path):
    from tests.builders import make_logging_config

    path = tmp_path / "nora.log"
    setup_logging(make_logging_config(path=path), secrets=["sk-live-x"])
    logging.getLogger("nora.test").info("hello %s", "sk-live-x")
    logging.shutdown()
    text = path.read_text()
    assert "sk-live-x" not in text
    assert json.loads(text.splitlines()[0])["message"]
