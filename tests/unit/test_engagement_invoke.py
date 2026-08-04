"""`invoke.run` — the timeout/exception behaviour carried over from
`Handler._invoke` (task-5-brief.md). R-5's two invocation-failure reasons;
the strings are pinned by `tests/test_acceptance_rules.py::RULE_TESTS`.
"""

from __future__ import annotations

import asyncio

from nora.domain.models import NormalizedMessage, TagContext
from nora.engagement.stages import invoke
from nora.obs.metrics import Metrics


def _ctx(tag_id: str = "tag-1") -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(
        tag_id=tag_id, message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
    )


class _FakeRunner:
    """Records the call it received; optionally sleeps or raises."""

    def __init__(self, *, delay: float = 0.0, raises: Exception | None = None) -> None:
        self.delay = delay
        self.raises = raises
        self.calls: list[dict] = []

    async def invoke(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises


async def _run(runner, *, timeout_seconds=5.0, metrics=None, ctx=None):
    metrics = metrics or Metrics()
    return await invoke.run(
        runner=runner, persona="persona", tools=["t1"], pre_fill=[{"role": "user"}],
        guard="guard", tag_message="<tag_message/>", limits={"turns": 4},
        ctx=ctx or _ctx(), timeout_seconds=timeout_seconds, metrics=metrics,
    ), metrics


async def test_a_successful_invocation_returns_ok_and_observes_latency():
    runner = _FakeRunner()
    (ok, elapsed), metrics = await _run(runner)

    assert ok is True
    assert elapsed >= 0.0
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 0
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 0


async def test_the_runner_is_called_with_every_argument():
    runner = _FakeRunner()
    ctx = _ctx()
    await _run(runner, ctx=ctx)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["persona"] == "persona"
    assert call["tools"] == ["t1"]
    assert call["pre_fill"] == [{"role": "user"}]
    assert call["guard"] == "guard"
    assert call["tag_message"] == "<tag_message/>"
    assert call["limits"] == {"turns": 4}
    assert call["ctx"] is ctx


async def test_a_timeout_returns_not_ok_and_counts_invocation_timeout():
    runner = _FakeRunner(delay=5.0)
    (ok, elapsed), metrics = await _run(runner, timeout_seconds=0.05)

    assert ok is False
    assert elapsed >= 0.0
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1


async def test_a_generic_exception_returns_not_ok_and_counts_llm_error():
    runner = _FakeRunner(raises=RuntimeError("omniroute down"))
    (ok, _elapsed), metrics = await _run(runner)

    assert ok is False
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 1
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1


async def test_llm_latency_is_observed_on_every_path():
    metrics = Metrics()
    await _run(_FakeRunner(), metrics=metrics)
    await _run(_FakeRunner(delay=5.0), timeout_seconds=0.05, metrics=metrics)
    await _run(_FakeRunner(raises=ValueError("boom")), metrics=metrics)

    assert len(metrics.observations("nora_llm_latency_seconds")) == 3
