import json

from nora.obs.tracing import _resolve_headers, setup_tracing, trace_attributes
from tests.builders import make_tracing_config


def test_trace_attributes_carry_the_provenance_fields(tmp_path):
    from nora.domain.models import NormalizedMessage, TagContext
    from nora.persona.loader import PersonaStore
    from tests.test_persona_loader import write

    d = tmp_path / "p"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, body="x")
    persona = PersonaStore(d, canary="C").get()
    msg = NormalizedMessage("signal", "g1", "group", "+1", "Miles", 1, "hi", {})
    attrs = trace_attributes(TagContext("t1", msg, None, (), (), 0.0), persona)
    assert attrs["tag_id"] == "t1"
    assert attrs["conversation_id"] == "g1"
    assert attrs["persona_sha256"] == persona.sha256


def test_trace_attributes_never_carry_message_content(tmp_path):
    from nora.domain.models import NormalizedMessage, TagContext
    from nora.persona.loader import PersonaStore
    from tests.test_persona_loader import write

    d = tmp_path / "p"
    d.mkdir()
    write(d, "soul.md", name="soul", order=10, body="x")
    msg = NormalizedMessage("signal", "g1", "group", "+1", "Miles", 1, "a private thing", {})
    attrs = trace_attributes(TagContext("t1", msg, None, (), (), 0.0),
                             PersonaStore(d, canary="C").get())
    assert "a private thing" not in json.dumps(attrs)


def test_tracing_disabled_is_a_no_op():
    cfg = make_tracing_config()
    setup_tracing(enabled=cfg.enabled, exporter=cfg.exporter, endpoint=cfg.endpoint)


def test_file_exporter_writes_to_a_path(tmp_path):
    cfg = make_tracing_config(enabled=True, endpoint=str(tmp_path / "traces.jsonl"))
    setup_tracing(enabled=cfg.enabled, exporter=cfg.exporter, endpoint=cfg.endpoint)


def test_otlp_http_setup_does_not_crash_on_an_unreachable_endpoint():
    """PROBE (Task 3): tracing is never load-bearing. Building the OTLP/HTTP
    exporter is network-free (it only configures a `requests.Session`; the
    live PROBE run against `http://127.0.0.1:1/...` confirmed the later,
    async export failures never propagate either — retried and logged on
    BatchSpanProcessor's own background thread, never raised here)."""
    setup_tracing(
        enabled=True, exporter="otlp_http", endpoint="http://127.0.0.1:1/v1/traces",
        headers_env="",
    )


def test_setup_tracing_swallows_a_build_failure_instead_of_crashing():
    """An unknown exporter value raises inside `_build_exporter`; `setup_tracing`
    must swallow it (log + continue), not let it take the app down."""
    setup_tracing(enabled=True, exporter="not-a-real-exporter", endpoint="")


def test_resolve_headers_is_none_when_headers_env_is_unset():
    assert _resolve_headers("") is None


def test_resolve_headers_is_none_when_the_named_env_var_is_absent(monkeypatch):
    monkeypatch.delenv("NORA_TEST_OTLP_HEADERS", raising=False)
    assert _resolve_headers("NORA_TEST_OTLP_HEADERS") is None


def test_resolve_headers_parses_the_otel_header_env_shape(monkeypatch):
    monkeypatch.setenv("NORA_TEST_OTLP_HEADERS", "Authorization=Basic%20abc123")
    assert _resolve_headers("NORA_TEST_OTLP_HEADERS") == {"authorization": "Basic abc123"}
