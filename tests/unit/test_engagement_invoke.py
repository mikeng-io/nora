"""`invoke.run` — the timeout/exception behaviour carried over from
`Handler._invoke` (task-5-brief.md). R-5's two invocation-failure reasons;
the strings are pinned by `tests/test_acceptance_rules.py::RULE_TESTS`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nora.domain.models import NormalizedMessage, TagContext
from nora.engagement.stages import invoke
from nora.obs.metrics import Metrics


@dataclass
class _Doctrine:
    sha256: str = "deadbeef"


def _ctx(tag_id: str = "tag-1", conversation_id: str = "g1") -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(
        tag_id=tag_id, message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
    )


class _FakeRunner:
    """Records the call it received; optionally sleeps or raises.

    `stop_reason` is what the real `LiveAgentRunner` returns off Strands'
    `AgentResult` — it is a per-instance value rather than a constant so this
    double cannot answer every invocation the same way, which is the only
    shape in which it could witness R-23 regressing.
    """

    def __init__(
        self, *, delay: float = 0.0, raises: Exception | None = None,
        stop_reason: str | None = "end_turn",
    ) -> None:
        self.delay = delay
        self.raises = raises
        self.stop_reason = stop_reason
        self.calls: list[dict] = []

    async def invoke(self, **kwargs) -> str | None:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.stop_reason


async def _run(runner, *, timeout_seconds=5.0, metrics=None, ctx=None, doctrine=None):
    metrics = metrics or Metrics()
    return await invoke.run(
        runner=runner, doctrine=doctrine or _Doctrine(), tools=["t1"],
        pre_fill=[{"role": "user"}], guard="guard", tag_message="<tag_message/>",
        limits={"turns": 4}, ctx=ctx or _ctx(), timeout_seconds=timeout_seconds,
        metrics=metrics,
    ), metrics


async def test_a_successful_invocation_returns_ok_and_observes_latency():
    runner = _FakeRunner()
    (ok, elapsed, stop_reason), metrics = await _run(runner)

    assert ok is True
    assert stop_reason == "end_turn"
    assert elapsed >= 0.0
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 0
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 0


async def test_the_runner_is_called_with_every_argument():
    runner = _FakeRunner()
    ctx = _ctx()
    doctrine = _Doctrine()
    await _run(runner, ctx=ctx, doctrine=doctrine)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["doctrine"] is doctrine
    assert call["tools"] == ["t1"]
    assert call["pre_fill"] == [{"role": "user"}]
    assert call["guard"] == "guard"
    assert call["tag_message"] == "<tag_message/>"
    assert call["limits"] == {"turns": 4}
    assert call["ctx"] is ctx


async def test_trace_attributes_are_computed_from_ctx_and_doctrine():
    """Matches `Handler._invoke`'s `build_trace_attributes(ctx, doctrine)` call
    (`nora/handler.py:133`) — provenance only, tag_id/conversation_id/
    doctrine_sha256, never message content."""
    runner = _FakeRunner()
    ctx = _ctx(tag_id="tag-42", conversation_id="g9")
    doctrine = _Doctrine(sha256="cafef00d")

    await _run(runner, ctx=ctx, doctrine=doctrine)

    attrs = runner.calls[0]["trace_attributes"]
    assert attrs == {
        "tag_id": "tag-42", "conversation_id": "g9", "doctrine_sha256": "cafef00d",
    }


async def test_a_timeout_returns_not_ok_and_counts_invocation_timeout():
    runner = _FakeRunner(delay=5.0)
    (ok, elapsed, stop_reason), metrics = await _run(runner, timeout_seconds=0.05)

    assert ok is False
    assert stop_reason is None
    assert elapsed >= 0.0
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1


async def test_a_generic_exception_returns_not_ok_and_counts_llm_error():
    runner = _FakeRunner(raises=RuntimeError("omniroute down"))
    (ok, _elapsed, stop_reason), metrics = await _run(runner)

    assert ok is False
    assert stop_reason is None
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 1
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1


async def test_the_stop_reason_is_counted_on_every_completed_invocation():
    """R-23: `nora_llm_stop_reason_total` is how the loop ENDED, recorded for
    every completed turn — not only the ones that ran out of budget. A series
    that only ever carries `limit_*` cannot say what fraction of turns are
    being cut off, and `end_turn` dominating is what makes one legible."""
    metrics = Metrics()
    await _run(_FakeRunner(stop_reason="end_turn"), metrics=metrics)
    await _run(_FakeRunner(stop_reason="end_turn"), metrics=metrics)
    await _run(_FakeRunner(stop_reason="limit_total_tokens"), metrics=metrics)

    assert metrics.get("nora_llm_stop_reason_total", reason="end_turn") == 2
    assert metrics.get("nora_llm_stop_reason_total", reason="limit_total_tokens") == 1


async def test_an_abandoned_invocation_has_no_stop_reason_to_report():
    """A timeout and a raised exception both mean the LOOP never returned, so
    there is no loop outcome — recording one would be an invention, and both
    paths already name their own cause (`invocation_timeout` / `llm_error`)."""
    metrics = Metrics()
    await _run(_FakeRunner(delay=5.0), timeout_seconds=0.05, metrics=metrics)
    await _run(_FakeRunner(raises=RuntimeError("omniroute down")), metrics=metrics)

    stop_reasons = {
        labels for (name, labels) in metrics.snapshot() if name == "nora_llm_stop_reason_total"
    }
    assert stop_reasons == set()


async def test_llm_latency_is_observed_on_every_path():
    metrics = Metrics()
    await _run(_FakeRunner(), metrics=metrics)
    await _run(_FakeRunner(delay=5.0), timeout_seconds=0.05, metrics=metrics)
    await _run(_FakeRunner(raises=ValueError("boom")), metrics=metrics)

    assert len(metrics.observations("nora_llm_latency_seconds")) == 3
