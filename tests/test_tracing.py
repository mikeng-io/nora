import json

from nora.agent.telemetry import setup_agent_tracing
from nora.obs.metrics import Metrics
from nora.obs.tracing import build_sampler, resolve_otlp_headers, trace_attributes
from tests.builders import make_tracing_config


def test_trace_attributes_carry_the_provenance_fields(tmp_path):
    from nora.doctrine.loader import DoctrineStore
    from nora.domain.models import NormalizedMessage, TagContext
    from tests.test_doctrine_loader import write

    d = tmp_path / "p"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, body="x")
    doctrine = DoctrineStore(d, canary="C", metrics=Metrics()).get()
    msg = NormalizedMessage("signal", "g1", "group", "+1", "Miles", 1, "hi", {})
    attrs = trace_attributes(TagContext("t1", msg, None, (), (), 0.0), doctrine)
    assert attrs["tag_id"] == "t1"
    assert attrs["conversation_id"] == "g1"
    assert attrs["doctrine_sha256"] == doctrine.sha256


def test_trace_attributes_never_carry_message_content(tmp_path):
    from nora.doctrine.loader import DoctrineStore
    from nora.domain.models import NormalizedMessage, TagContext
    from tests.test_doctrine_loader import write

    d = tmp_path / "p"
    d.mkdir()
    write(d, "identity.md", name="soul", order=10, body="x")
    msg = NormalizedMessage("signal", "g1", "group", "+1", "Miles", 1, "a private thing", {})
    attrs = trace_attributes(TagContext("t1", msg, None, (), (), 0.0),
                             DoctrineStore(d, canary="C", metrics=Metrics()).get())
    assert "a private thing" not in json.dumps(attrs)


# `setup_tracing` is not in this module: `nora.obs` may not import `nora.agent` (the
# layered contract), so the provider assembly is
# `nora.agent.telemetry.setup_agent_tracing`, called directly from `nora/__main__.py`. Two
# consequences for these tests: `setup_agent_tracing` has no `enabled` flag (that check is
# the composition root's, not this layer's), and it does NOT itself swallow a bad-exporter
# failure — `__main__.py`'s own `try/except` around the call does that, so the
# build-failure test asserts the RAISE this module's caller depends on catching.


def test_file_exporter_writes_to_a_path(tmp_path, monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider

    import nora.agent.telemetry as telemetry

    # Like the registration test below, leave OTel's one-shot global untouched.
    registered = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", registered.append)
    cfg = make_tracing_config(enabled=True, endpoint=str(tmp_path / "traces.jsonl"))
    setup_agent_tracing(
        sampler=build_sampler(), service_name="nora",
        exporter=cfg.exporter, endpoint=cfg.endpoint, headers_env=cfg.headers_env,
    )
    assert len(registered) == 1
    provider = registered[0]
    assert isinstance(provider, TracerProvider)
    try:
        with provider.get_tracer("file-export-test").start_as_current_span("file-export-probe"):
            pass
        assert provider.force_flush()
        serialized = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
        assert serialized.strip(), "the file exporter emitted no span"
        exported = json.loads(serialized)
        assert exported["name"] == "file-export-probe"
        assert exported["resource"]["attributes"]["service.name"] == "nora"
    finally:
        provider.shutdown()


def test_otlp_http_setup_does_not_crash_on_an_unreachable_endpoint():
    """PROBE: tracing is never load-bearing. Building the OTLP/HTTP exporter is network-free
    (it only configures a `requests.Session`); later, async export failures never
    propagate either — they are retried and logged on BatchSpanProcessor's own background
    thread, never raised here. `StrandsTelemetry.setup_otlp_exporter` has its own internal
    `try/except Exception: logger.exception(...)`, so this holds even though
    `setup_agent_tracing` itself does not wrap the call."""
    setup_agent_tracing(
        sampler=build_sampler(), service_name="nora", exporter="otlp_http",
        endpoint="http://127.0.0.1:1/v1/traces", headers_env="",
    )


def test_setup_agent_tracing_raises_on_an_unknown_exporter():
    """An unknown exporter value is `setup_agent_tracing`'s own `ValueError`
    — it does not swallow this (the caller, `nora/__main__.py`, does, so a
    bad `config.toml` value never takes the app down; that swallow is
    exercised at the call site, not testable from this module alone without
    a config-loading integration test)."""
    import pytest

    with pytest.raises(ValueError, match="unknown tracing exporter"):
        setup_agent_tracing(
            sampler=build_sampler(), service_name="nora",
            exporter="not-a-real-exporter", endpoint="", headers_env="",
        )


def test_resolve_headers_is_none_when_headers_env_is_unset():
    assert resolve_otlp_headers("") is None


def test_resolve_headers_is_none_when_the_named_env_var_is_absent(monkeypatch):
    monkeypatch.delenv("NORA_TEST_OTLP_HEADERS", raising=False)
    assert resolve_otlp_headers("NORA_TEST_OTLP_HEADERS") is None


def test_resolve_headers_parses_the_otel_header_env_shape(monkeypatch):
    monkeypatch.setenv("NORA_TEST_OTLP_HEADERS", "Authorization=Basic%20abc123")
    assert resolve_otlp_headers("NORA_TEST_OTLP_HEADERS") == {"authorization": "Basic abc123"}


def test_setup_agent_tracing_registers_the_provider_globally(monkeypatch):
    """THE MISSING LINE: registering the provider.

    `StrandsTelemetry.__init__` does NOT register a provider it is handed:

        if tracer_provider:
            self.tracer_provider = tracer_provider   # kept, never registered
        else:
            self._initialize_tracer()                # THIS sets the global

    Nora hands it one, to keep the R-76 sampler. So registration is ours, and without it
    the process global stays `ProxyTracerProvider` — whose tracers are NO-OPS. Every
    `nora.obs.tracing.span(...)` then records nothing and exports nothing, while raising
    nothing, logging nothing and failing no test.

    File export is checked separately above. This test pins the registration
    itself: a locally usable provider is not necessarily the process global.

    `set_tracer_provider` is patched rather than called for real because it is ONE-SHOT
    per process (a second call is ignored with a warning) — see
    `tests/conftest.py::exported_spans`. Asserting the CALL is order independent and
    leaves the global untouched for every test after it.
    """
    from opentelemetry.sdk.trace import TracerProvider

    import nora.agent.telemetry as telemetry

    registered: list[object] = []
    monkeypatch.setattr(
        telemetry.trace, "set_tracer_provider", registered.append, raising=True,
    )

    setup_agent_tracing(
        sampler=build_sampler(), service_name="nora", exporter="otlp_http",
        endpoint="http://127.0.0.1:1/v1/traces", headers_env="",
    )

    assert len(registered) == 1, (
        "setup_agent_tracing did not register a tracer provider; the global "
        "stays ProxyTracerProvider and every nora span becomes a silent no-op"
    )
    provider = registered[0]
    assert isinstance(provider, TracerProvider), (
        f"registered {type(provider).__name__}, want the SDK TracerProvider"
    )
    # It must be the one carrying Nora's sampler, not a bare default: a
    # provider registered without `build_sampler()` would export every
    # ingest-plumbing span R-76 exists to drop, which is the opposite failure
    # and just as invisible.
    # `ParentBased{root:NoraPlumbingIsNotATrace,…}`, not the bare rule: the
    # wrapper is the point, because a child must never re-decide what its root
    # was given (`_PlumbingIsNotATrace`'s own docstring). Matching on the ROOT
    # inside it asserts Nora's rule is the one in force while leaving the
    # wrapper's exact spelling to OTel.
    description = provider.sampler.get_description()
    assert "NoraPlumbingIsNotATrace" in description, (
        f"registered a provider sampling with {description!r} — a default "
        f"sampler would export every ingest-plumbing span R-76 exists to drop, "
        f"which is the opposite failure and just as invisible"
    )
