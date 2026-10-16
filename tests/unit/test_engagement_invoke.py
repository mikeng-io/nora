"""`invoke.run` — the timeout/exception behaviour. R-5's two invocation-failure reasons;
the strings are pinned by `tests/test_acceptance_rules.py::RULE_TESTS`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nora.domain.models import NormalizedMessage, TagContext
from nora.domain.notes import Note
from nora.domain.tag_trace import TagTrace
from nora.engagement.stages import invoke
from nora.obs.metrics import Metrics


@dataclass
class _Doctrine:
    sha256: str = "deadbeef"


def _note(note_id: str) -> Note:
    return Note(
        id=note_id, unit="ridgeline", title=f"note {note_id}",
        description="one line about it", uncertainty="not settled",
        closing_condition="somebody decides", established_in="g1",
        created_at=1, head_revision_id=f"{note_id}-r1", anchor_date=20261011,
    )


def _ctx(
    tag_id: str = "tag-1", conversation_id: str = "g1",
    notes: tuple[Note, ...] = (),
) -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(
        tag_id=tag_id, message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
        notes=notes,
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
        stop_reason: str | None = "end_turn", final_text: str = "",
    ) -> None:
        self.delay = delay
        self.raises = raises
        self.stop_reason = stop_reason
        self.final_text = final_text
        self.calls: list[dict] = []

    async def invoke(self, **kwargs) -> tuple[str | None, str, TagTrace]:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        ctx = kwargs["ctx"]
        trace = TagTrace(
            tag_id=ctx.tag_id, conversation_id=ctx.message.conversation_id,
            messages=({"role": "assistant", "content": []},), transcript=(),
            outcome="send_reply", created_at=0,
        )
        # `(stop_reason, final_text, trace)` — the prose
        # the model wrote WITHOUT calling send_reply (empty on every healthy
        # turn) and the TagTrace this invocation produced.
        return self.stop_reason, self.final_text, trace


async def _run(runner, *, timeout_seconds=5.0, metrics=None, ctx=None, doctrine=None):
    metrics = metrics or Metrics()
    return await invoke.run(
        runner=runner, doctrine=doctrine or _Doctrine(), tools=["t1"],
        pre_fill=[{"role": "user"}], guard="guard", tag_message="<tag_message/>",
        limits={"turns": 4}, ctx=ctx or _ctx(), timeout_seconds=timeout_seconds,
        metrics=metrics, audience="tag",
    ), metrics


async def test_a_successful_invocation_returns_ok_and_observes_latency():
    runner = _FakeRunner()
    (ok, elapsed, stop_reason, _text, _trace), metrics = await _run(runner)

    assert ok is True
    assert stop_reason == "end_turn"
    assert elapsed >= 0.0
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 0
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 0


async def test_invoke_run_returns_a_tag_trace_on_success():
    """The `TagTrace` the runner built comes back, with its `transcript`
    filled in here from `ctx` (the runner itself has no access to the
    assembled window)."""
    ctx = _ctx(tag_id="tag-99", conversation_id="g5")
    runner = _FakeRunner()
    (ok, _elapsed, _stop_reason, _text, trace), _metrics = await _run(runner, ctx=ctx)

    assert ok is True
    assert trace.tag_id == "tag-99"
    assert trace.conversation_id == "g5"
    assert trace.outcome == "send_reply"
    assert trace.messages != ()


async def test_invoke_run_returns_a_blank_trace_on_timeout():
    """A runner that hangs past the timeout never returns a trace at all — the
    invocation is abandoned, so `invoke.run` builds a blank one itself, with
    `outcome="timeout"`."""
    ctx = _ctx(tag_id="tag-100")
    runner = _FakeRunner(delay=5.0)
    (ok, _elapsed, _stop_reason, _text, trace), _metrics = await _run(
        runner, ctx=ctx, timeout_seconds=0.05,
    )

    assert ok is False
    assert trace.tag_id == "tag-100"
    assert trace.messages == ()
    assert trace.outcome == "timeout"


async def test_invoke_run_returns_a_blank_trace_on_llm_error():
    """Same shape as the timeout case, but `outcome="llm_error"` — the other
    ok=False path."""
    ctx = _ctx(tag_id="tag-101")
    runner = _FakeRunner(raises=RuntimeError("omniroute down"))
    (ok, _elapsed, _stop_reason, _text, trace), _metrics = await _run(runner, ctx=ctx)

    assert ok is False
    assert trace.tag_id == "tag-101"
    assert trace.messages == ()
    assert trace.outcome == "llm_error"


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
    """Matches the stage's `build_trace_attributes(ctx, doctrine)` call —
    provenance only, tag_id/conversation_id/
    doctrine_sha256 and one COUNT, never message content.

    Asserted as an EXACT dict rather than by key, and that is the point of the
    test: it is the gate on what leaves this process for Langfuse, so a key
    added anywhere has to be added here too and looked at once.
    """
    runner = _FakeRunner()
    ctx = _ctx(tag_id="tag-42", conversation_id="g9")
    doctrine = _Doctrine(sha256="cafef00d")

    await _run(runner, ctx=ctx, doctrine=doctrine)

    attrs = runner.calls[0]["trace_attributes"]
    assert attrs == {
        "tag_id": "tag-42", "conversation_id": "g9", "doctrine_sha256": "cafef00d",
        "note_index_entries": 0,
    }


async def test_the_trace_reports_how_many_notes_the_turn_was_shown():
    """The cold start is invisible in a trace without this. A turn that saw an
    empty notebook and a turn where the tier was never wired produce the same
    spans, the same tools and the same prompt shape."""
    runner = _FakeRunner()
    notes = tuple(
        _note(f"n{i}") for i in range(3)
    )

    await _run(runner, ctx=_ctx(notes=notes))

    assert runner.calls[0]["trace_attributes"]["note_index_entries"] == 3


async def test_a_timeout_returns_not_ok_and_counts_invocation_timeout():
    runner = _FakeRunner(delay=5.0)
    (ok, elapsed, stop_reason, _text, _trace), metrics = await _run(runner, timeout_seconds=0.05)

    assert ok is False
    assert stop_reason is None
    assert elapsed >= 0.0
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1
    assert len(metrics.observations("nora_llm_latency_seconds")) == 1


async def test_a_generic_exception_returns_not_ok_and_counts_llm_error():
    runner = _FakeRunner(raises=RuntimeError("omniroute down"))
    (ok, _elapsed, stop_reason, _text, _trace), metrics = await _run(runner)

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


async def test_this_stage_labels_both_llm_series_audience_tag():
    """This stage is the TAG path's, exclusively (see the module docstring), so
    both series it moves say so as a literal.

    Without the label the latency histogram mixes samples bounded by `[llm]
    timeout_seconds = 60` with fire-turn samples bounded by `[heartbeat]
    timeout_seconds = 120`, and the `limit_.*` alarm on the stop-reason series
    cannot say which path ran out of budget.
    """
    metrics = Metrics()
    await _run(_FakeRunner(stop_reason="limit_turns"), metrics=metrics)

    assert metrics.get(
        "nora_llm_stop_reason_total", reason="limit_turns", audience="tag",
    ) == 1
    assert metrics.get(
        "nora_llm_stop_reason_total", reason="limit_turns", audience="heartbeat",
    ) == 0
    assert len(metrics.observations("nora_llm_latency_seconds", audience="tag")) == 1
    assert metrics.observations("nora_llm_latency_seconds", audience="heartbeat") == []


async def test_the_turn_publishes_its_deadline_to_the_tools_it_runs():
    """DEC-197: a tool spending wall clock on its own model call (the
    vision describer) must see how much of THIS turn is left. The runner is
    inside the `wait_for`, so what it reads is what every tool reads."""
    from nora.domain.turn_deadline import seconds_left

    seen: list[float | None] = []

    class _Reads(_FakeRunner):
        async def invoke(self, **kwargs):
            seen.append(seconds_left())
            return await super().invoke(**kwargs)

    await _run(_Reads(), timeout_seconds=42.0)

    assert seen[0] is not None and 41.0 < seen[0] <= 42.0, seen
    assert seconds_left() is None
