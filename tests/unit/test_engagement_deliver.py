"""`deliver.build`/`deliver.check_outcome` — wraps `Deliverer`+`ReplyGuard`
and owns the post-invocation "did anything actually go out" check carried
over from `handler.py:205-212`.
"""

from __future__ import annotations

import time

from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.domain.models import ConversationTarget, DeliveryStatus
from nora.domain.ports import SendResult
from nora.engagement.stages import deliver
from nora.obs.metrics import Metrics
from nora.doctrine.loader import Doctrine
from nora.testing import FakeAuditStore

TARGET = ConversationTarget(platform="signal", conversation_id="g1", conversation_type="group")
BUDGETS = ToolRegistry().budgets()


def _doctrine(text: str = "be helpful") -> Doctrine:
    return Doctrine(
        sections=(), text=f"<nora_doctrine>{text}</nora_doctrine>", doctrine_text=text,
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


class _FakeAdapter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self._succeed = succeed

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=self._succeed)


class _LLMState:
    def __init__(self) -> None:
        self.last_success_at: float | None = None


def test_build_wires_the_guard_to_the_same_deliverer():
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, metrics=Metrics(),
    )

    assert isinstance(handle.deliverer, Deliverer)
    assert isinstance(handle.guard, ReplyGuard)
    assert handle.guard._deliverer is handle.deliverer


async def test_check_outcome_counts_no_send_call_when_the_model_never_attempted():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        budgets=BUDGETS, metrics=metrics,
    )

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
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
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, metrics=metrics,
    )
    await handle.deliverer.deliver("hello")

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
    )

    assert delivered is False
    assert handle.deliverer.attempted is True
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_check_outcome_returns_true_and_sets_llm_state_on_a_delivered_reply():
    metrics = Metrics()
    llm_state = _LLMState()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, metrics=metrics,
    )
    await handle.deliverer.deliver("hello")
    before = time.time()

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", llm_state=llm_state,
        metrics=metrics, stop_reason="end_turn",
    )

    assert delivered is True
    assert llm_state.last_success_at is not None
    assert llm_state.last_success_at >= before


async def test_check_outcome_tolerates_no_llm_state():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeAuditStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, metrics=metrics,
    )
    await handle.deliverer.deliver("hello")

    result = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
    )
    assert result is True


async def test_a_turn_cut_off_by_a_budget_cap_is_not_labelled_no_send_call():
    """R-23, at `check_outcome`'s own level.

    Every one of R-8's three caps ends the loop by RETURNING — Strands raises
    nothing and logs nothing — so from here a turn that ran out of budget and
    a model that chose not to speak are the same two booleans
    (`delivered=False`, `attempted=False`). Only `stop_reason` separates them,
    and the whole point is that the `reason` label names the true cause.
    """
    for stop_reason in ("limit_total_tokens", "limit_turns", "limit_output_tokens"):
        metrics = Metrics()
        handle = deliver.build(
            store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
            canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
            budgets=BUDGETS, metrics=metrics,
        )

        delivered = deliver.check_outcome(
            deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
            stop_reason=stop_reason,
        )

        assert delivered is False
        assert metrics.get("nora_silent_failures_total", reason=stop_reason) == 1
        assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_an_ordinary_stop_reason_still_counts_no_send_call():
    """The other half of R-23: only the BUDGET reasons are relabelled. A model
    that finished its turn (`end_turn`) or was cut off by the provider's own
    per-call ceiling (`max_tokens`) and still never called `send_reply` chose
    not to speak, and must keep saying so — a relabelling that swallowed
    `no_send_call` entirely would trade one blind spot for another."""
    for stop_reason in ("end_turn", "tool_use", "max_tokens", None):
        metrics = Metrics()
        handle = deliver.build(
            store=FakeAuditStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
            canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
            budgets=BUDGETS, metrics=metrics,
        )

        deliver.check_outcome(
            deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
            stop_reason=stop_reason,
        )

        assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1, stop_reason


def test_check_outcome_cannot_be_called_without_a_stop_reason():
    """`stop_reason` has no default, so a future call site cannot quietly go
    back to labelling every silent turn `no_send_call` — which is the shape of
    the bug R-23 fixes, and exactly what a defaulted parameter would allow."""
    import inspect

    param = inspect.signature(deliver.check_outcome).parameters["stop_reason"]
    assert param.default is inspect.Parameter.empty


async def test_a_delivered_reply_is_recorded_in_the_audit_store():
    store = FakeAuditStore()
    handle = deliver.build(
        store=store, adapter=_FakeAdapter(succeed=True), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        budgets=BUDGETS, metrics=Metrics(),
    )

    await handle.deliverer.deliver("hello")

    nora_rows = [r for r in await store.recent(conversation_id="g1", limit=10) if r.is_nora]
    assert len(nora_rows) == 1
    assert nora_rows[0].delivery_status is DeliveryStatus.SENT
