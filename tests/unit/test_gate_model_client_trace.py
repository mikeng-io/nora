"""What the model call puts on the span it runs under.

The gate is a GENERATION in Langfuse, and a generation with no token counts
reports as costing nothing. Read back live on 2026-09-08, the two real
verdicts in the deployment showed `usage: {input: 0, output: 0, total: 0}` —
and the debrief and the standing fold had done the same since they shipped.
That is the number the cost of running this path has to be judged against.

The clients moved onto the Agent SDK afterwards, so the counts now arrive as
an accumulated triple rather than off a response object; the property is the
same and this is the seam it goes through.
"""

from __future__ import annotations

import pytest

from nora.app.composition import _annotate_usage_triple
from nora.obs.tracing import span

pytestmark = pytest.mark.anyio


def _attrs(exported, name: str) -> dict:
    (recorded,) = [s for s in exported.get_finished_spans() if s.name == name]
    return dict(recorded.attributes or {})


async def test_the_token_usage_reaches_the_span_it_runs_under(exported_spans):
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        _annotate_usage_triple((1830, 12, 1842))

    attrs = _attrs(exported_spans, "relevance_gate")
    assert attrs["gen_ai.usage.input_tokens"] == 1830
    assert attrs["gen_ai.usage.output_tokens"] == 12
    assert attrs["gen_ai.usage.total_tokens"] == 1842


async def test_a_provider_that_reports_no_usage_records_no_usage(exported_spans):
    """Not zero. A provider that omitted the field has told us nothing, and
    writing 0 would assert the call was free — the same argument
    `finish_reason=... or ""` makes one field over."""
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        _annotate_usage_triple(None)

    assert "gen_ai.usage.total_tokens" not in _attrs(exported_spans, "relevance_gate")


async def test_the_gate_client_annotates_what_the_sdk_returned(monkeypatch, exported_spans):
    """The wiring, not just the helper: a client that computed the counts and
    never annotated them would pass both tests above."""
    from nora.agent import structured
    from nora.app import composition

    async def _fake(**_kw):
        return structured.StructuredResult(
            content='{"verdict": "no_go", "relevance_score": 0.0}',
            finish_reason="tool_use", usage=(999, 7, 1006),
        )

    monkeypatch.setattr(composition, "ask_structured", _fake)
    client = composition._RelevanceGateModelClient(
        base_url="http://x/v1", api_key="k", model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        got = await client.complete(system="rules", prompt="room")

    assert got.content == '{"verdict": "no_go", "relevance_score": 0.0}'
    assert _attrs(exported_spans, "relevance_gate")["gen_ai.usage.total_tokens"] == 1006
