"""`deliver.build`/`deliver.check_outcome` — wraps `Deliverer`+`ReplyGuard`
and owns the post-invocation "did anything actually go out" check carried
over from `handler.py:205-212`.
"""

from __future__ import annotations

import time

from nora.agent.guards import Deliverer, ReplyGuard
from nora.domain.models import ConversationTarget, DeliveryStatus
from nora.engagement.stages import deliver
from nora.obs.metrics import Metrics
from nora.persona.loader import Persona
from nora.testing import FakeAuditStore

TARGET = ConversationTarget(platform="signal", conversation_id="g1", conversation_type="group")


def _persona(text: str = "be helpful") -> Persona:
    return Persona(
        sections=(), text=f"<nora_persona>{text}</nora_persona>", persona_text=text,
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


class _FakeAdapter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self._succeed = succeed

    async def send(self, target: ConversationTarget, text: str) -> bool:
        self.sent.append((target, text))
        return self._succeed


class _LLMState:
    def __init__(self) -> None:
        self.last_success_at: float | None = None


def test_build_wires_the_guard_to_the_same_deliverer():
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, persona=_persona(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", metrics=Metrics(),
    )

    assert isinstance(handle.deliverer, Deliverer)
    assert isinstance(handle.guard, ReplyGuard)
    assert handle.guard._deliverer is handle.deliverer


async def test_check_outcome_counts_no_send_call_when_the_model_never_attempted():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, persona=_persona(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        metrics=metrics,
    )

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", metrics=metrics,
    )

    assert delivered is False
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1


async def test_check_outcome_does_not_double_count_when_the_model_attempted_but_failed():
    """`Deliverer.deliver` already counts its own failure reason (e.g.
    send_failed); `check_outcome` must not also count no_send_call for an
    attempt that happened but didn't land."""
    metrics = Metrics()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(succeed=False), target=TARGET,
        persona=_persona(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", metrics=metrics,
    )
    await handle.deliverer.deliver("hello")

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", metrics=metrics,
    )

    assert delivered is False
    assert handle.deliverer.attempted is True
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_check_outcome_returns_true_and_sets_llm_state_on_a_delivered_reply():
    metrics = Metrics()
    llm_state = _LLMState()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        persona=_persona(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", metrics=metrics,
    )
    await handle.deliverer.deliver("hello")
    before = time.time()

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", llm_state=llm_state, metrics=metrics,
    )

    assert delivered is True
    assert llm_state.last_success_at is not None
    assert llm_state.last_success_at >= before


async def test_check_outcome_tolerates_no_llm_state():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        persona=_persona(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", metrics=metrics,
    )
    await handle.deliverer.deliver("hello")

    result = deliver.check_outcome(deliverer=handle.deliverer, tag_id="tag-1", metrics=metrics)
    assert result is True


async def test_a_delivered_reply_is_recorded_in_the_audit_store():
    store = FakeAuditStore()
    handle = deliver.build(
        store=store, adapter=_FakeAdapter(succeed=True), target=TARGET, persona=_persona(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        metrics=Metrics(),
    )

    await handle.deliverer.deliver("hello")

    nora_rows = [r for r in await store.recent(conversation_id="g1", limit=10) if r.is_nora]
    assert len(nora_rows) == 1
    assert nora_rows[0].delivery_status is DeliveryStatus.SENT
