"""Proves no per-tag state is retained.

A `ReplyGuard` retained per tag pins a `Deliverer` -> doctrine -> adapter chain,
an unbounded memory leak. The same shape of bug appears the moment anything —
production or test — starts accumulating `ReplyGuard` instances in a collection
nothing ever drains.

This test drives real tags through the real `EngagementPipeline` (fakes only at the
adapter/memory/doctrine boundary — the model runner is a minimal stand-in that
still exercises the real tools and the real `ReplyGuard`) and counts *live*
`ReplyGuard` instances via `gc.get_objects()` after each batch. It asserts that
count for two different batch sizes rather than just once: a leak that always
retains exactly one stale guard would pass a single `count == 0` check by
accident if the assertion ran before that guard was created, but it cannot pass
both a small-N and a much-larger-N check unless the count is genuinely
independent of N.
"""

from __future__ import annotations

import asyncio
import gc

from nora.agent.guards import ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.app.runtime import BackgroundTasks
from nora.doctrine.loader import Doctrine
from nora.domain.models import NormalizedMessage
from nora.domain.ports import (
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    PlatformIdentity,
    SendResult,
)
from nora.domain.tag_trace import TagTrace
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.tag import Debounce
from nora.testing import FakeRecordStore


class _FakeAdapter:
    platform = "signal"

    def __init__(self) -> None:
        self._account = "+852000"
        self.sent: list[tuple] = []

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(
        self, *, target, text: str, mentions=(), styles=(), preview=None,
        attachments=(),
        quote: object | None = None,
    ) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=True)


class _FakeMemory:
    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str) -> None:
        return None

    async def feed(self, *, peer_id: str, text: str) -> bool:
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


class _FakeDoctrineStore:
    def get(self, *, applies_to: str = "tag") -> Doctrine:
        return Doctrine(
            sections=(), text="<nora_doctrine>be helpful</nora_doctrine>",
            doctrine_text="be helpful", sha256="deadbeef", runtime_context="", loaded_at=0.0,
        )


class _ReplyingRunner:
    """Always sends one reply through the real `send_reply` tool, so a real
    `ReplyGuard` and `Deliverer` are genuinely constructed and exercised on every
    tag — not just allocated and immediately discarded unused."""

    async def invoke(self, *, tools, guard, ctx, **_kwargs) -> tuple[str | None, str, TagTrace]:
        tools_by_name = {t.tool_name: t for t in tools}
        args = {"text": "ok"}
        if guard.evaluate("send_reply", args) is None:
            result = tools_by_name["send_reply"](**args)
            if hasattr(result, "__await__"):
                await result
            guard.record("send_reply")
        # `(stop_reason, final_text, trace)` (DEC-93). The empty
        # text is the point: this double SENDS through the tool, so there is
        # no un-routed prose to recover and the recovery path must stay
        # untaken. `trace` is blank — this test is about `ReplyGuard`
        # lifecycle, not reasoning continuity.
        trace = TagTrace(
            tag_id=ctx.tag_id, conversation_id=ctx.message.conversation_id,
            messages=(), transcript=(), outcome="send_reply", created_at=0,
        )
        return "end_turn", "", trace


def _build_pipeline() -> tuple[EngagementPipeline, BackgroundTasks]:
    audit = FakeRecordStore()
    memory = _FakeMemory()
    metrics = Metrics()
    background = BackgroundTasks()
    persons = Persons(by_person={}, index={})
    assembler = TranscriptInjection(
        audit=audit, memory=memory, persons=persons, context_window=10, metrics=metrics,
    )
    pipeline = EngagementPipeline(
        adapters={"signal": _FakeAdapter()},
        audit=audit, memory=memory, persons=persons,
        doctrine_store=_FakeDoctrineStore(), context_assembler=assembler,
        runner=_ReplyingRunner(), tool_registry=ToolRegistry(),
        policy=permissive_policy(),
        debounce=Debounce(0.0), canary="CANARY-XYZ",
        display_name="Nora", ai_peer="nora", context_window=10,
        timeout_seconds=5.0, limits={"turns": 4, "output_tokens": 400, "total_tokens": 24000},
        metrics=metrics, spawn=background.spawn,
        slots=ConversationSlots(maxlen=16, spawn=background.spawn),
    )
    return pipeline, background


def _msg(conversation_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )


async def _run_n_tags(
    pipeline: EngagementPipeline, background: BackgroundTasks, n: int, prefix: str
) -> None:
    for i in range(n):
        conversation_id = f"{prefix}{i}"
        request = EngagementRequest(
            message=_msg(conversation_id),
            decision=EngageDecision(engage=True, reason="trigger_text"),
            tag_id=f"{prefix}-{i}",
        )
        await pipeline.run(request)
        while len(background):
            await asyncio.gather(*list(background._tasks), return_exceptions=True)
            await asyncio.sleep(0)


def _live_reply_guard_count() -> int:
    gc.collect()
    return sum(1 for obj in gc.get_objects() if isinstance(obj, ReplyGuard))


async def test_reply_guard_count_does_not_grow_with_tags_processed():
    pipeline, background = _build_pipeline()
    baseline = _live_reply_guard_count()

    await _run_n_tags(pipeline, background, 5, "a")
    after_small_batch = _live_reply_guard_count()

    await _run_n_tags(pipeline, background, 50, "b")
    after_large_batch = _live_reply_guard_count()

    assert after_small_batch == baseline, "a ReplyGuard survived the first batch"
    assert after_large_batch == baseline, (
        f"ReplyGuard count grew with N ({baseline} -> {after_small_batch} -> "
        f"{after_large_batch}) — a per-tag object is being retained"
    )
