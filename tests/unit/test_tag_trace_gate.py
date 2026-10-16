"""Exit gate for tag-trace reasoning capture: "a turn's reasoning is
demonstrably captured and stored, with a red-on-revert test that deletes the
reasoning blocks and fails."

This module exercises extraction, sanitization and retention TOGETHER, end to end: a scripted turn
whose `agent.messages` carries a `reasoningContent` block goes through the real
extraction (`nora.agent.tag_trace_extract.extract_tag_trace`),
the real sanitization it calls (`nora.agent.tag_trace_sanitize`), and
the real pipeline retention (`nora.engagement.pipeline`'s unconditional
`self._tag_trace_store.save(trace)`) — then reads the saved
`TagTrace` back and asserts the block is still there.

Reuses `tests/unit/test_engagement_pipeline.py`'s own pipeline-construction
harness (`_build_pipeline`, `_request`, `_drain`) rather than a second copy:
that module already assembles the exact collaborator set
(`EngagementPipeline`'s ~20 constructor arguments, the `_FakeTagTraceStore`
double the pipeline's own tests read `pipeline._tag_trace_store.saved` through) —
duplicating it here is exactly the "interpreting twice is how two copies
drift" trap CLAUDE.md's spine calls out.
"""

from __future__ import annotations

from types import SimpleNamespace

from nora.agent.tag_trace_extract import extract_tag_trace
from nora.obs.metrics import Metrics
from nora.testing import FakeAgentRunner, ScriptedTurn

from .test_engagement_pipeline import _build_pipeline, _drain, _request

REASONING_TEXT = "let me work out today's dinner slot before I answer"


class _ReasoningCapturingRunner(FakeAgentRunner):
    """`FakeAgentRunner`, but the `TagTrace` it hands back is a REAL one.

    `FakeAgentRunner.invoke` (the double every other pipeline test uses)
    deliberately returns a BLANK trace — its own docstring: "scripted mode
    has no `agent.messages` to extract from", because the tool-call script
    it replays is not a Strands message list. Proving reasoning retention
    needs something reasoningContent-SHAPED flowing through the real
    extraction code, so this subclass builds the `agent.messages` list
    `LiveAgentRunner.invoke` would have handed `extract_tag_trace` — a user
    turn, then an assistant turn carrying a `reasoningContent` block ahead of
    its `toolUse`, then the tool result — and calls the REAL
    `nora.agent.tag_trace_extract.extract_tag_trace` over it, rather than
    inventing a second, private notion of what a trace looks like.

    Tool execution and guard dispatch (does `send_reply` actually run, does
    `ReplyGuard` see it) still go through the real `FakeAgentRunner.invoke`
    machinery via `super().invoke` — only the returned TRACE is replaced.
    """

    async def invoke(self, **kwargs):
        stop_reason, final_text, _blank_trace = await super().invoke(**kwargs)
        ctx = kwargs["ctx"]
        agent = SimpleNamespace(messages=[
            {"role": "user", "content": [{"text": kwargs["tag_message"]}]},
            {"role": "assistant", "content": [
                # THE LINE THIS GATE PROTECTS. Comment it out (or replace it
                # with just the `toolUse` block below) to confirm this test
                # goes RED (the red-on-revert drill).
                {"reasoningContent": {"reasoningText": {"text": REASONING_TEXT}}},
                {"toolUse": {
                    "toolUseId": "t1", "name": "send_reply",
                    "input": {"text": final_text or ""},
                }},
            ]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "t1", "status": "success", "content": [{"text": "True"}],
            }}]},
        ])
        trace = extract_tag_trace(
            agent, baseline=0, tag_id=ctx.tag_id,
            conversation_id=ctx.message.conversation_id, transcript=(),
            outcome="send_reply", max_chars=10_000, metrics=Metrics(),
        )
        return stop_reason, final_text, trace


async def test_reasoning_content_survives_into_a_retained_tag_trace():
    """Drive a scripted turn whose assistant message includes a
    `reasoningContent` block through the real extraction+retention path,
    then read the trace back and assert the block is present.

    Red-on-revert drill: comment out
    the `reasoningContent` block in `_ReasoningCapturingRunner.invoke` above,
    run `uv run pytest -k reasoning_content_survives -v`, confirm RED,
    restore, confirm GREEN.
    """
    runner = _ReasoningCapturingRunner([
        ScriptedTurn(tool_calls=[("send_reply", {"text": "今晚七點半得"})],
                     final_text="今晚七點半得"),
    ])
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(runner=runner)

    await pipeline.run(_request())
    await _drain(background)

    got = pipeline._tag_trace_store.saved
    assert len(got) == 1
    assert got[0].outcome == "send_reply"  # the outcome correction still ran
    assert any(
        "reasoningContent" in block
        for msg in got[0].messages
        for block in msg.get("content", [])
    )
