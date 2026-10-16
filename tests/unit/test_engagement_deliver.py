"""`deliver.build`/`deliver.check_outcome` — wraps `Deliverer`+`ReplyGuard`
and owns the post-invocation "did anything actually go out" check.
"""

from __future__ import annotations

import time

from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.registry import ToolRegistry
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget, DeliveryStatus
from nora.domain.ports import SendResult
from nora.engagement.stages import deliver
from nora.engagement.stages.deliver import build, check_outcome
from nora.obs.metrics import Metrics
from nora.testing import FakeRecordStore

TARGET = ConversationTarget(platform="signal", conversation_id="g1", conversation_type="group")
BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()


def _doctrine(text: str = "be helpful") -> Doctrine:
    return Doctrine(
        sections=(), text=f"<nora_doctrine>{text}</nora_doctrine>", doctrine_text=text,
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


class _FakeAdapter:
    def __init__(self, *, succeed: bool = True) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self._succeed = succeed

    async def send(
        self, *, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
        quote: object | None = None,
    ) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=self._succeed)


class _LLMState:
    def __init__(self) -> None:
        self.last_success_at: float | None = None


def test_build_wires_the_guard_to_the_same_deliverer():
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL, metrics=Metrics(),
        audience="tag", composer=None,
    )

    assert isinstance(handle.deliverer, Deliverer)
    assert isinstance(handle.guard, ReplyGuard)
    assert handle.guard._deliverer is handle.deliverer


async def test_check_outcome_counts_no_send_call_when_the_model_never_attempted():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
        audience="tag", composer=None,
    )

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
        audience="tag",
    )

    assert delivered is False
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1


async def test_check_outcome_does_not_double_count_when_the_model_attempted_but_failed():
    """`Deliverer.deliver` already counts its own failure reason (e.g.
    send_failed); `check_outcome` must not also count no_send_call for an
    attempt that happened but didn't land."""
    metrics = Metrics()
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=False), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
        audience="tag", composer=None,
    )
    await handle.deliverer.deliver("hello")

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
        audience="tag",
    )

    assert delivered is False
    assert handle.deliverer.attempted is True
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_check_outcome_returns_true_and_sets_llm_state_on_a_delivered_reply():
    metrics = Metrics()
    llm_state = _LLMState()
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
        audience="tag", composer=None,
    )
    await handle.deliverer.deliver("hello")
    before = time.time()

    delivered = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", llm_state=llm_state,
        metrics=metrics, stop_reason="end_turn", audience="tag",
    )

    assert delivered is True
    assert llm_state.last_success_at is not None
    assert llm_state.last_success_at >= before


async def test_check_outcome_tolerates_no_llm_state():
    metrics = Metrics()
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
        audience="tag", composer=None,
    )
    await handle.deliverer.deliver("hello")

    result = deliver.check_outcome(
        deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
        stop_reason="end_turn",
        audience="tag",
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
            store=FakeRecordStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
            canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
            budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
            audience="tag", composer=None,
        )

        delivered = deliver.check_outcome(
            deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
            stop_reason=stop_reason,
            audience="tag",
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
            store=FakeRecordStore(), adapter=_FakeAdapter(), target=TARGET, doctrine=_doctrine(),
            canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
            budgets=BUDGETS, terminal=TERMINAL, metrics=metrics,
            audience="tag", composer=None,
        )

        deliver.check_outcome(
            deliverer=handle.deliverer, tag_id="tag-1", platform="signal", metrics=metrics,
            stop_reason=stop_reason,
            audience="tag",
        )

        assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1, stop_reason


def test_check_outcome_cannot_be_called_without_a_stop_reason():
    """`stop_reason` has no default, so a call site cannot quietly
    label every silent turn `no_send_call` — which is the shape of
    what R-23 forbids, and exactly what a defaulted parameter would allow."""
    import inspect

    param = inspect.signature(deliver.check_outcome).parameters["stop_reason"]
    assert param.default is inspect.Parameter.empty


def test_check_outcome_cannot_be_called_without_an_audience():
    """`audience` has no default either: a
    defaulted "tag" would let a participation failure land inside the budget
    filtered to `audience="tag"` — invisible, exactly the shape the
    required keyword exists to refuse."""
    import inspect

    param = inspect.signature(deliver.check_outcome).parameters["audience"]
    assert param.default is inspect.Parameter.empty


async def test_a_delivered_reply_is_recorded_in_the_audit_store():
    store = FakeRecordStore()
    handle = deliver.build(
        store=store, adapter=_FakeAdapter(succeed=True), target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="+8520000", conversation_id="g1",
        budgets=BUDGETS, terminal=TERMINAL, metrics=Metrics(),
        audience="tag", composer=None,
    )

    await handle.deliverer.deliver("hello")

    nora_rows = [r for r in await store.recent(conversation_id="g1", limit=10) if r.is_nora]
    assert len(nora_rows) == 1
    assert nora_rows[0].delivery_status is DeliveryStatus.SENT


# ---- DEC-40 -----------------------------------------------------------------


def test_build_takes_the_deliverers_message_cap_from_the_same_budget_table():
    """One number, two enforcers (DEC-32, DEC-40). R-7's budget check runs
    BEFORE R-2's latch, so a `Deliverer` capped independently of the guard
    would silently win whenever it was the smaller of the two and file the
    refusal under the other rule's name."""
    handle = deliver.build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="CANARY", tag_id="tag-1",
        nora_sender_id="+8520000", conversation_id="g1",
        budgets={**BUDGETS, "send_reply": 4}, terminal=TERMINAL, metrics=Metrics(),
        audience="tag", composer=None,
    )
    assert handle.deliverer._message_cap == 4


def test_a_finished_turn_has_no_truncation_reason():
    assert deliver.truncation_reason(stop_reason="end_turn", completed=True) is None


def test_a_cap_that_cut_the_loop_names_itself():
    """The reason joins straight against `nora_llm_stop_reason_total`, so
    "which cap truncates replies" is one query rather than a correlation."""
    for reason in deliver.BUDGET_STOP_REASONS:
        assert deliver.truncation_reason(stop_reason=reason, completed=True) == reason


def test_an_invocation_that_never_returned_is_named_apart_from_a_cap():
    """A timeout or an `llm_error` produces NO stop reason at all
    (`nora.engagement.stages.invoke` returns None for exactly those two), and
    borrowing a `limit_*` label would aim the next investigation at the token
    budget — DEC-25's argument."""
    assert deliver.truncation_reason(stop_reason=None, completed=False) == "invocation_failed"


def test_a_fault_after_the_loop_finished_is_named_apart_again():
    """The outer `BaseException`: the loop DID return a stop reason, so the
    turn was not cut off by a cap or a timeout — something after it raised."""
    assert deliver.truncation_reason(stop_reason="end_turn", completed=False) == "pipeline_error"


# ── a guard-refused turn is not ALSO a no_send_call ─────────────────────────


async def test_a_guard_refused_turn_is_not_counted_as_no_send_call():
    """A guard-refused turn is not ALSO a `no_send_call`.

    R-4/R-6/R-18 all `return False` before `_attempts += 1`, so a turn whose
    every reply was refused answered False to `attempted` — and `check_outcome`
    then added `no_send_call` ON TOP of the `egress_block` already counted.
    Two silent-failure increments for one turn, and the second is a false
    statement: she DID call the tool, a guard stopped her, and those have
    different fixes.

    It would also corrupt an instrument: ADR-0006's root-cause investigation
    reasons from `no_send_call` meaning "the loop ended having never called the
    tool". Revert `called` to `attempted` in `check_outcome` and this reds."""
    metrics = Metrics()
    handle = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t1", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    # R-18 refuses this: a real E.164 in the reply text.
    assert await handle.deliverer.deliver("佢喺 +85298765432 嗰邊") is False
    assert handle.deliverer.delivered is False
    assert handle.deliverer.attempted is False        # nothing reached the platform
    assert handle.deliverer.called is True            # but she DID call send_reply

    check_outcome(
        deliverer=handle.deliverer, metrics=metrics, platform="signal",
        tag_id="t1", stop_reason="end_turn", audience="tag",
    )
    assert metrics.get("nora_silent_failures_total", reason="egress_block") == 1
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0


async def test_a_turn_that_never_called_send_reply_still_counts_no_send_call():
    """The other half: the bound on the rule. A model that ends its turn without
    ever calling the tool is the case `no_send_call` exists for, and it must
    still be counted or the correction above would silence the real signal."""
    metrics = Metrics()
    handle = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t1", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    check_outcome(
        deliverer=handle.deliverer, metrics=metrics, platform="signal",
        tag_id="t1", stop_reason="end_turn", audience="tag",
    )
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1


# ---------------------------------------------------------------------------
# THE STRICT-SILENCE SPAN. "She chose not to speak" and "the send failed" must
# not look the same in the trace. These assert on EXPORTED spans
# (`exported_spans`, `tests/conftest.py`), the same discipline
# `test_engagement_pipeline.py` uses.
# ---------------------------------------------------------------------------


async def test_check_outcome_emits_its_own_span(exported_spans):
    """PROBE: delete the `with span("engagement.deliver", ...)` in
    `check_outcome` and this reddens."""
    metrics = Metrics()
    handle = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t1", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    check_outcome(
        deliverer=handle.deliverer, metrics=metrics, platform="signal",
        tag_id="t1", stop_reason="end_turn", audience="tag",
    )
    names = [s.name for s in exported_spans.get_finished_spans()]
    assert names == ["engagement.deliver"]


async def test_a_deliberate_silence_and_a_send_failure_are_different_shapes(
    exported_spans,
):
    """A model that never called
    `send_reply` (she chose not to speak) and a model whose reply the
    PLATFORM rejected (the send failed) must not read the same in the span.

    PROBE: replace `deliverer.last_outcome` with the coarse `delivered` bool
    in the `annotate(...)` call and both turns below collapse to
    `nora.outcome=False` with nothing to tell them apart.
    """
    metrics = Metrics()

    silent = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t1", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    check_outcome(
        deliverer=silent.deliverer, metrics=metrics, platform="signal",
        tag_id="t1", stop_reason="end_turn", audience="tag",
    )

    failed = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=False), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t2", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    await failed.deliverer.deliver("hello")
    check_outcome(
        deliverer=failed.deliverer, metrics=metrics, platform="signal",
        tag_id="t2", stop_reason="end_turn", audience="tag",
    )

    spans = exported_spans.get_finished_spans()
    by_tag = {s.attributes["nora.tag_id"]: s for s in spans}
    assert by_tag["t1"].attributes["nora.outcome"] == "no_send_call"
    assert by_tag["t1"].attributes["nora.called"] is False
    assert by_tag["t2"].attributes["nora.outcome"] == "send_failed"
    assert by_tag["t2"].attributes["nora.called"] is True
    assert by_tag["t2"].attributes["nora.attempted"] is True
    for recorded in by_tag.values():
        assert recorded.attributes["nora.delivered"] is False


async def test_a_delivered_reply_records_the_sent_outcome(exported_spans):
    metrics = Metrics()
    handle = build(
        store=FakeRecordStore(), adapter=_FakeAdapter(succeed=True), target=TARGET,
        doctrine=_doctrine(), canary="C", tag_id="t1", nora_sender_id="nora",
        conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
        metrics=metrics, audience="tag", composer=None,
    )
    await handle.deliverer.deliver("hello")
    check_outcome(
        deliverer=handle.deliverer, metrics=metrics, platform="signal",
        tag_id="t1", stop_reason="end_turn", audience="tag",
    )
    (recorded,) = exported_spans.get_finished_spans()
    assert recorded.attributes["nora.outcome"] == "sent"
    assert recorded.attributes["nora.delivered"] is True

