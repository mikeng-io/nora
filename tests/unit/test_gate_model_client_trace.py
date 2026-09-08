"""What the Relevance Gate's model client puts on the gate's span.

The gate is a GENERATION in Langfuse, and a generation with no token counts
reports as costing nothing. Read back live on 2026-09-08, the two real
verdicts in the deployment showed `usage: {input: 0, output: 0, total: 0}` —
which is the number the decision to flip `shadow = false` has to be priced
against, so a permanent zero there is not a cosmetic gap.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.app.composition import _RelevanceGateModelClient
from nora.obs.tracing import span

pytestmark = pytest.mark.anyio


class _Usage:
    def __init__(self, p: int, c: int, t: int) -> None:
        self.prompt_tokens, self.completion_tokens, self.total_tokens = p, c, t


class _Response:
    def __init__(self, usage: Any) -> None:
        self.usage = usage
        self.choices = [
            type("C", (), {
                "message": type("M", (), {"content": '{"verdict": "no_go"}'})(),
                "finish_reason": "stop",
            })(),
        ]


class _Completions:
    def __init__(self, usage: Any) -> None:
        self._usage = usage

    async def create(self, **_kw: Any) -> _Response:
        return _Response(self._usage)


class _OpenAI:
    """Shaped like the real client's `chat.completions.create` path, so a
    signature change breaks this rather than passing on a permissive mock."""

    def __init__(self, usage: Any) -> None:
        self.chat = type("Chat", (), {"completions": _Completions(usage)})()


def _client(usage: Any) -> _RelevanceGateModelClient:
    return _RelevanceGateModelClient(
        client=_OpenAI(usage), model="nora-gate", max_tokens=512,
        temperature=0.0, timeout_seconds=20.0, reasoning_effort="none",
    )


async def test_the_gate_call_puts_its_token_usage_on_the_gates_span(exported_spans):
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        got = await _client(_Usage(1830, 12, 1842)).complete(prompt="hi")

    assert got.content == '{"verdict": "no_go"}'
    (recorded,) = [s for s in exported_spans.get_finished_spans() if s.name == "relevance_gate"]
    attrs = dict(recorded.attributes or {})
    assert attrs["gen_ai.usage.input_tokens"] == 1830
    assert attrs["gen_ai.usage.output_tokens"] == 12
    assert attrs["gen_ai.usage.total_tokens"] == 1842


async def test_a_provider_that_reports_no_usage_records_no_usage(exported_spans):
    """Not zero. A provider that omitted the field has told us nothing, and
    writing 0 would assert the call was free — the same argument
    `finish_reason=... or ""` makes one field over."""
    with span("relevance_gate", **{"nora.trace_kind": "generation"}):
        await _client(None).complete(prompt="hi")

    (recorded,) = [s for s in exported_spans.get_finished_spans() if s.name == "relevance_gate"]
    attrs = dict(recorded.attributes or {})
    assert "gen_ai.usage.total_tokens" not in attrs
