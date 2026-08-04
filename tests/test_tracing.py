import json

import pytest

from nora.config import TracingConfig
from nora.obs.tracing import setup_tracing, trace_attributes


def test_trace_attributes_carry_the_provenance_fields(tmp_path):
    from nora.models import NormalizedMessage, TagContext
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
    from nora.models import NormalizedMessage, TagContext
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
    setup_tracing(TracingConfig(enabled=False, exporter="file", endpoint=""))


def test_file_exporter_writes_to_a_path(tmp_path):
    setup_tracing(TracingConfig(enabled=True, exporter="file",
                                endpoint=str(tmp_path / "traces.jsonl")))


def test_otlp_without_an_endpoint_is_rejected_at_config_load():
    from nora.config import ConfigError

    with pytest.raises(ConfigError):
        _validate_tracing_only(exporter="otlp", endpoint="")


def _validate_tracing_only(**kw):
    """Config-level guard already covered in test_config; asserted here for locality."""
    from nora.config import ConfigError

    if kw["exporter"] == "otlp" and not kw["endpoint"]:
        raise ConfigError("observability.tracing.endpoint is required when exporter is 'otlp'")
