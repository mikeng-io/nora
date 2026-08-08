"""`FirePath` — the turn one due reminder becomes.

`build_context` is exercised for §6.4's trap, and `run()` for the observability
contract §6.2/§13 put on it: a fire turn that half-arrived, a fault before the
model, and the two shared LLM series that must say which audience was speaking.
The model side is the scripted double (`nora.testing.FakeAgentRunner`) replaying
against the REAL tools and the REAL guards — the same seam the tag path's own
tests use, so a fire turn here spends the real `send_reply` budget and is cut
off by the real `limits`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.agent.registry import ToolRegistry
from nora.app import fire as fire_module
from nora.app.fire import FirePath
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.ports import PlatformIdentity, SendResult
from nora.domain.reminders import Reminder, ReminderState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeAgentRunner, ScriptedTurn
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore

T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)

MILES = PersonRef(
    person_id="miles", name="Miles", honcho_peer_id="miles", aliases=("阿Miles",),
)


def _reminder(**kw: object) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group",
        "text": "幫我睇下 Tyler 喺邊", "created_at": T0 - timedelta(hours=1),
        "due_at": T0, "state": ReminderState.FIRING, "fired_at": None,
        "outcome": None,
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return Reminder(**base)                                     # type: ignore[arg-type]


@pytest.fixture
def persons() -> Persons:
    return Persons(
        by_person={"miles": MILES},
        index={("signal", "+85212345678"): "miles"},
    )


@pytest.fixture
def record_store() -> FakeRecordStore:
    return FakeRecordStore()


@pytest.fixture
def memory_store() -> FakeMemoryStore:
    return FakeMemoryStore(cards={"miles": ["drinks his coffee black"]})


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def fire_path(
    persons: Persons,
    record_store: FakeRecordStore,
    memory_store: FakeMemoryStore,
    doctrine_store,
    metrics: Metrics,
) -> FirePath:
    """The model-side collaborators (`runner`, `adapter`, `registry`) are None
    because this test never calls `run()` — `build_context` touches none of
    them. Everything `build_context` DOES touch is real."""
    return FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapter=None, registry=None,               # type: ignore[arg-type]
        canary="test-canary", display_name="Nora", nora_sender_id="+85200000000",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
    )


async def test_the_fire_turn_carries_the_requester(fire_path: FirePath) -> None:
    """The trap this test exists for: blanking sender_id is correct for
    reactions (indicate.begin returns None on a falsy author) and wrong for
    everything that reads it. A blank sender_id resolved through
    ContextAssembler gives tagger=None, which empties peer_card, makes
    Room.scope() unavailable, and makes search_memory refuse."""
    reminder = _reminder()

    ctx = await fire_path.build_context([reminder])

    assert ctx.tagger is not None
    assert ctx.tagger.person_id == reminder.requester_person_id
    assert ctx.message.body == reminder.text          # the request itself
    assert ctx.message.sender_name != ""              # NOT Persons.display_name
    assert ctx.message.sender_id == ""                # so no reaction is placed
    assert ctx.message.timestamp == 0                 # falsy AND an int — models.py:63


async def test_the_peer_card_is_read_for_the_requester(
    fire_path: FirePath, memory_store: FakeMemoryStore,
) -> None:
    """`peer_card` is the half of §6.4 that goes silently empty when the tagger
    falls out — it is keyed on `ref.honcho_peer_id`, so a `None` tagger yields
    `()` with nothing raising anywhere."""
    ctx = await fire_path.build_context([_reminder()])

    assert ctx.peer_card == ("drinks his coffee black",)


async def test_the_transcript_is_the_conversation_the_row_names(
    fire_path: FirePath, record_store: FakeRecordStore,
) -> None:
    """A fire turn with an empty transcript passes every gate and answers a
    reminder with no idea what the room has been saying."""
    await record_store.append(message=_inbound("conv-1", "去邊食"))
    await record_store.append(message=_inbound("conv-OTHER", "唔關事"))

    ctx = await fire_path.build_context([_reminder()])

    assert [row.body for row in ctx.transcript] == ["去邊食"]


async def test_several_rows_for_one_requester_are_one_turn(
    fire_path: FirePath,
) -> None:
    """The tick groups by (conversation, requester), so `rows` can be more than
    one — and every sentence has to reach her or a reminder is silently eaten
    by a turn that only spoke for the first."""
    first = _reminder(text="洗衫")
    second = _reminder(text="打電話俾阿媽")

    ctx = await fire_path.build_context([first, second])

    assert "洗衫" in ctx.message.body
    assert "打電話俾阿媽" in ctx.message.body


async def test_an_unmapped_requester_leaves_the_tagger_none(
    fire_path: FirePath,
) -> None:
    """identity.toml is loaded once at startup and a row may be thirty days
    old, so the person a row names may no longer be in it. `build_context`
    reports that honestly rather than inventing a PersonRef; `run()` is what
    refuses the turn."""
    ctx = await fire_path.build_context([_reminder(requester_person_id="ghost")])

    assert ctx.tagger is None


def _inbound(conversation_id: str, body: str):
    from nora.domain.models import NormalizedMessage
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=1_754_000_000_000,
        body=body, raw={},
    )


# ---------------------------------------------------------------------------
# `run()` — the fire turn's own accounting (§6.2, §13)
# ---------------------------------------------------------------------------

# The shipped shape of `[llm.limits]`: `total_tokens` is what the scripted
# multi-message turns below actually spend against, so a turn is cut off by the
# REAL cap rather than by a flag the double was told to return.
LIMITS = {"turns": 4, "output_tokens": 400, "total_tokens": 24_000}


class _FireAdapter:
    """The `PlatformAdapter` surface a fire turn touches: identity + send.

    `identity_raises` is how the §6.2 handler-error probe reaches the ONE stage
    that runs before anything else in `_turn` — a fault there is the wiring
    fault `failed_error` cannot otherwise be told apart from an ordinary model
    error.
    """

    platform = "signal"

    def __init__(self, *, identity_raises: Exception | None = None) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self._identity_raises = identity_raises

    async def resolve_identity(self) -> PlatformIdentity:
        if self._identity_raises is not None:
            raise self._identity_raises
        return PlatformIdentity(platform=self.platform, primary="+85200000000")

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult:
        self.sent.append((target, text))
        return SendResult(ok=True)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class _OutagingMemory(FakeMemoryStore):
    """Honcho answering everything except the one endpoint under test.

    `FakeMemoryStore(fail=True)` degrades the way the shipped `HonchoClient`
    does — `peer_card` returns `[]`, `feed` returns False — which is exactly why
    it cannot express these two cases: the defect being probed is a RAISE, and a
    fake that only ever degrades gracefully would let an unguarded call site
    pass forever.
    """

    def __init__(
        self, *, card_raises: bool = False, feed_raises: bool = False,
        cards: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(cards=cards)
        self._card_raises = card_raises
        self._feed_raises = feed_raises

    async def peer_card(self, *, peer_id: str) -> list[str]:
        if self._card_raises:
            raise RuntimeError("honcho down")
        return await super().peer_card(peer_id=peer_id)

    async def feed(self, **kwargs: object) -> bool:
        if self._feed_raises:
            raise RuntimeError("honcho down")
        return await super().feed(**kwargs)          # type: ignore[arg-type]


class _HangsAfterSending(FakeAgentRunner):
    """Replays its script, then hangs past `[heartbeat] timeout_seconds`.

    The shape a slow LAST model call really has, and the one exit path with no
    stop reason at all — the invocation never returned, so there is nothing to
    ask it how the loop ended.
    """

    async def invoke(self, **kwargs: object) -> str | None:
        result = await super().invoke(**kwargs)
        await asyncio.sleep(5.0)
        return result


def _send_turns(*texts: str, tokens: int = 0) -> list[ScriptedTurn]:
    """One model cycle per message — the shape a real multi-message turn has."""
    return [
        ScriptedTurn(tool_calls=[("send_reply", {"text": t})], final_text=t,
                     total_tokens=tokens)
        for t in texts
    ]


def _fire_path(
    *,
    doctrine_store,
    persons: Persons,
    metrics: Metrics,
    runner,
    adapter: _FireAdapter | None = None,
    memory: FakeMemoryStore | None = None,
    records: FakeRecordStore | None = None,
    timeout_seconds: float = 5.0,
) -> FirePath:
    return FirePath(
        persons=persons,
        records=records if records is not None else FakeRecordStore(),
        memory=memory if memory is not None else FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics, runner=runner,
        adapter=adapter if adapter is not None else _FireAdapter(),
        registry=ToolRegistry(), canary="test-canary", display_name="Nora",
        nora_sender_id="+85200000000", ai_peer="nora", context_window=40,
        limits=LIMITS, timeout_seconds=timeout_seconds, clock=lambda: T0,
    )


async def test_a_fire_turn_cut_off_between_messages_is_counted_as_partial(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """DEC-42's observable, on the path that needs it most.

    Two of three reminder messages went out and the loop was cut off by R-8's
    token cap. Everything else about this turn looks successful — `delivered` is
    True, the outcome is `sent`, the row is DONE — so nothing R-23 built can see
    it. `nora_partial_replies_total`'s own spec says "every increment here is a
    message the group was meant to receive"; without this the sentence is
    unreachable for reminders.
    """
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter,
        runner=FakeAgentRunner(_send_turns("一", "二", "三", tokens=20_000)),
    )

    outcome = await path.run([_reminder()])

    assert adapter.texts == ["一", "二"]
    assert outcome == "sent"                        # delivered, so `sent` — §8.1
    assert metrics.get(
        "nora_partial_replies_total", reason="limit_total_tokens", platform="signal",
    ) == 1
    # NOT silent, and that is the whole point.
    assert metrics.get("nora_silent_failures_total", reason="limit_total_tokens") == 0


async def test_a_timeout_after_the_reminder_went_out_is_a_partial_delivery(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The contradiction `docs/runbook.md` sends an operator into: `_invoke`
    counts `invocation_timeout` for a turn that was NOT silent, while `_turn`
    sees `delivered` and answers `sent`. Both readings are right about
    different halves — and only `nora_partial_replies_total` says the group got
    part of a thought.
    """
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, timeout_seconds=0.05,
        runner=_HangsAfterSending(_send_turns("一", "二")),
    )

    outcome = await path.run([_reminder()])

    assert adapter.texts == ["一", "二"]
    assert outcome == "sent"
    assert metrics.get("nora_partial_replies_total", reason="invocation_failed") == 1
    # The timeout is still counted as itself, under the heartbeat's audience.
    assert metrics.get(
        "nora_silent_failures_total", reason="invocation_timeout", audience="heartbeat",
    ) == 1


async def test_a_complete_fire_turn_is_never_counted_as_partial(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The control for the observable itself: a reminder that arrived whole must
    leave the counter alone, or every delivered reminder reads as a truncation."""
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    assert await path.run([_reminder()]) == "sent"
    for reason in ("limit_turns", "limit_total_tokens", "invocation_failed",
                   "pipeline_error"):
        assert metrics.get("nora_partial_replies_total", reason=reason) == 0


async def test_the_two_shared_llm_series_say_the_heartbeat_was_speaking(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """§13's "must not silently move a tag-path series", applied to the two it
    missed. The histogram now mixes samples bounded by `[heartbeat]
    timeout_seconds` with tag samples bounded by `[llm] timeout_seconds`, and
    `2026-08-08-turn-budget-finding.md:224` alarms on
    `nora_llm_stop_reason_total{reason=~"limit_.*"}` — which cannot aim an
    investigation at a turn it cannot attribute to a path.
    """
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    await path.run([_reminder()])

    assert metrics.get(
        "nora_llm_stop_reason_total", reason="end_turn", audience="heartbeat",
    ) == 1
    assert metrics.get(
        "nora_llm_stop_reason_total", reason="end_turn", audience="tag",
    ) == 0
    assert len(metrics.observations("nora_llm_latency_seconds", audience="heartbeat")) == 1
    assert metrics.observations("nora_llm_latency_seconds", audience="tag") == []


async def test_a_fault_before_the_model_is_counted_as_a_heartbeat_handler_error(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """§6.2's ninth site, on the fire path. Everything between `resolve_identity`
    and `build_tag_message` runs before any counter moves, so a wiring fault
    reached `HeartbeatLoop._run_turn` as a bare `failed_error` — the same string
    an ordinary model error writes — with `nora_silent_failures_total` flat.
    That is R-5's last-resort signal missing exactly where it matters most.
    """
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=_FireAdapter(identity_raises=RuntimeError("signal-cli down")),
        runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    assert await path.run([_reminder()]) == "failed_error"
    assert metrics.get(
        "nora_silent_failures_total", reason="handler_error", audience="heartbeat",
    ) == 1


async def test_a_feedback_failure_names_the_platform_it_happened_on(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """`nora_errors_total` declares `(stage, platform)`. An increment that omits
    the platform records `platform=""` and is invisible to an operator filtering
    `nora_errors_total{platform="signal"}` — the filter the runbook reaches for
    first, and the one line 189 of this module already gets right."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, memory=_OutagingMemory(feed_raises=True),
        runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    # The reminder still arrived; only her memory of saying it did not.
    assert await path.run([_reminder()]) == "sent"
    assert adapter.texts == ["記得食藥"]
    assert metrics.get(
        "nora_errors_total", stage="fire_feedback", platform="signal",
    ) == 1


async def test_a_peer_card_outage_does_not_cost_the_reminder(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """DEFENSIVE (§8.2). The shipped `HonchoClient.peer_card` returns `[]` on
    failure, so this is unreachable today — and at-most-once is what makes it
    worth guarding anyway: a raise here writes `failed_error` and the reminder
    is GONE, where the tag path's equivalent only makes the person re-tag.
    A thinner turn beats no turn, exactly as the transcript read already has it.
    """
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, memory=_OutagingMemory(card_raises=True),
        runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    assert await path.run([_reminder()]) == "sent"
    assert adapter.texts == ["記得食藥"]
    assert metrics.get(
        "nora_errors_total", stage="fire_peer_card", platform="signal",
    ) == 1


def test_the_audience_label_is_the_one_constant_every_fire_counter_carries() -> None:
    """A spelling check, because every assertion above depends on it and a typo
    would make them all pass against a series nobody alerts on."""
    assert fire_module.AUDIENCE == "heartbeat"
