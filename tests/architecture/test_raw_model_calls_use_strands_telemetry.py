"""One spelling of "this span is a model call", and it lives upstream.

Hand-rolled `langfuse.observation.*`/`gen_ai.*` attribute blocks at separate
call sites diverge from each other, so the spelling is not this repo's to own:
`strands.telemetry.tracer.Tracer.start_model_invoke_span`/
`end_model_invoke_span`, wrapped by `nora.agent.telemetry.start_raw_model_
span`/`end_raw_model_span`, implement it, upstream-maintained.

A call site could still hand-type the string literal `"gen_ai.operation.name"`
into a plain `nora.obs.tracing.span(**{...})` call without importing `strands`
at all, so this AST scan still has a job. There is no local constant to derive
the forbidden family list from, because the vocabulary is an external standard
(OTel GenAI semantic conventions + Langfuse's own observation namespace);
hand-copying two stable, documented external prefixes here carries no "two
copies drift" risk, because there is no second, in-repo definition for them to
drift from.
"""

from __future__ import annotations

import ast
from pathlib import Path

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from nora.agent.telemetry import end_raw_model_span, start_raw_model_span

ROOT = Path(__file__).resolve().parents[2]

#: The one module allowed to hold `strands.telemetry`, and therefore the one
#: allowed to spell these families directly — enforced independently by
#: `tests/test_boundaries.py::test_strands_import_boundary`. This test
#: additionally checks that nothing OUTSIDE it hand-types the same string
#: literals via a plain `nora.obs.tracing.span(**{...})` call, which
#: importing no library at all would still let through.
_HOME = ROOT / "nora" / "agent" / "telemetry.py"

#: OTel's GenAI semantic conventions and Langfuse's own vendor namespace —
#: external standards, not this repo's vocabulary. See the module docstring
#: for why hand-copying them here does not reintroduce the drift risk the
#: retired `GENERATION_ATTRIBUTES`-derivation avoided.
_FAMILIES = ("gen_ai.", "langfuse.observation.")

# The shared processor also runs in processes without Strands. These identify
# conversations and domain stages; neither declares a model invocation.
_IDENTITY_ATTRIBUTES = {"gen_ai.conversation.id", "langfuse.observation.type"}
_IDENTITY_HOME = ROOT / "nora" / "obs" / "tracing.py"


def _production_modules() -> list[Path]:
    return sorted(
        p for p in (ROOT / "nora").rglob("*.py")
        if ".venv" not in p.parts and p != _HOME
    )


def test_no_module_spells_a_generation_attribute_by_hand() -> None:
    """Every `langfuse.observation.*` / `gen_ai.*` literal outside
    `nora/agent/telemetry.py`.

    A string CONSTANT, read off the AST rather than by grep, so a mention in
    a docstring or a comment — prose about the convention, not a span
    carrying it — does not fire.
    """
    offenders: list[str] = []
    for path in _production_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # not ours to police
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.startswith(_FAMILIES):
                if path == _IDENTITY_HOME and node.value in _IDENTITY_ATTRIBUTES:
                    continue
                offenders.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: {node.value!r}"
                )
    assert not offenders, (
        "these call sites spell a generation attribute by hand instead of "
        "going through nora.agent.telemetry.start_raw_model_span / "
        "end_raw_model_span (or a real Strands Agent invocation), which is "
        "how the two that once existed drifted apart:\n  "
        + "\n  ".join(offenders)
    )


def test_start_and_end_raw_model_span_actually_emit_strands_telemetry(
    monkeypatch,
) -> None:
    """The POSITIVE CONTROL for the test above, and it is not optional
    (AGENTS.md: pair every zero-assertion with one; assert something only a
    run that RAN produces).

    Calls `start_raw_model_span`/`end_raw_model_span` directly against a
    throwaway in-memory exporter — no Agent, no network — and reads back an
    EXPORTED SPAN, not the wrapper's source, for the same reason the retired
    test read an exported span rather than grepping `tracing.py`: a check
    against the helper's own text can never fail, even if the helper stopped
    doing anything.

    **Patches `get_tracer_provider`, not `set_tracer_provider`** — the same
    reasoning `tests/conftest.py`'s `exported_spans` fixture gives for
    patching `get_tracer` instead of installing a provider: `set_tracer_
    provider` is one-shot per process, so a second call anywhere in the run
    would silently no-op. `strands.telemetry.tracer.Tracer.__init__` calls
    `trace_api.get_tracer_provider()` directly (verified against the
    installed package) — patching that function, and resetting the module's
    own `_tracer_instance` cache so `get_tracer()` builds a fresh `Tracer`
    under the patch, isolates this test the same way.

    PROBE: delete `custom_trace_attributes`/`system_prompt` from `start_raw_
    model_span`'s call into `Tracer.start_model_invoke_span` and the
    `nora.test.probe`/system-message assertions below redden.
    """
    import strands.telemetry.tracer as _tracer_module
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create({"service.name": "test"}), sampler=ALWAYS_ON,
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(_tracer_module.trace_api, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(_tracer_module, "_tracer_instance", None)

    span = start_raw_model_span(
        messages=[{"role": "user", "content": [{"text": "hello"}]}],
        model_id="test-model", system_prompt="a system prompt",
        custom_trace_attributes={"nora.test.probe": "raw-model-span"},
    )
    end_raw_model_span(
        span=span, content="hi back", finish_reason="stop",
        input_tokens=3, output_tokens=2, total_tokens=5,
        latency_seconds=0.01,
    )

    (chat,) = [s for s in exporter.get_finished_spans() if s.name == "chat"]
    attrs = dict(chat.attributes or {})
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "test-model"
    assert attrs["nora.test.probe"] == "raw-model-span"
    assert attrs["gen_ai.usage.input_tokens"] == 3
    assert attrs["gen_ai.usage.output_tokens"] == 2
    events = {e.name: dict(e.attributes or {}) for e in chat.events}
    assert "hello" in events["gen_ai.user.message"]["content"]
    assert "a system prompt" in events["gen_ai.system.message"]["content"]
    # "stop" (OpenAI's vocabulary, what was passed in) translated to
    # "end_turn" (Strands' own `StopReason`, `_STOP_REASON_FROM_FINISH_
    # REASON`) — this is the translation actually happening, not the raw
    # value passed through unchanged.
    assert events["gen_ai.choice"]["finish_reason"] == "end_turn"
    assert "hi back" in events["gen_ai.choice"]["message"]
