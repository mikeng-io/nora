"""`FirePath` — the turn one due reminder becomes.

`build_context` is exercised for the blank-sender trap, and `run()` for its
observability contract: a fire turn that half-arrived, a fault before the
model, and the two shared LLM series that must say which audience was speaking.
The model side is the scripted double (`nora.testing.FakeAgentRunner`) replaying
against the REAL tools and the REAL guards — the same seam the tag path's own
tests use, so a fire turn here spends the real `send_reply` budget and is cut
off by the real `limits`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.agent.prefill import build_prefill
from nora.agent.registry import ToolRegistry
from nora.app import fire as fire_module
from nora.app.fire import (
    FirePath,
    compute_occasion,
    participation_occasion,
    reminder_occasion,
)
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.decision_log import DecisionLogRecorder, DecisionRow
from nora.domain.events import Event
from nora.domain.framing import ReminderHeader
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.notes import Note, NoteRevision
from nora.domain.ports import (
    JobResult,
    JobStatus,
    PlatformIdentity,
    SendResult,
    SteamPlayerState,
)
from nora.domain.reminders import Reminder, ReminderCaps, ReminderState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy, permissive_policy
from nora.testing import (
    FakeAgentRunner,
    FakeRosterSource,
    FakeSteamPresenceProvider,
    InMemoryDecisionLogStore,
    InMemoryNoteStore,
    ScriptedTurn,
)
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore
from nora.testing.reminders import FakeReminderStore

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
        runner=None, adapters={}, registry=None,                # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
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

    ctx = await fire_path.build_context(reminder_occasion([reminder]))

    assert ctx.tagger is not None
    assert ctx.tagger.person_id == reminder.requester_person_id
    assert ctx.message.body == reminder.text          # the request itself
    assert ctx.message.sender_name != ""              # NOT Persons.display_name
    assert ctx.message.sender_id == ""                # so no reaction is placed
    assert ctx.message.timestamp == 0                 # falsy AND an int


async def test_the_peer_card_is_read_for_the_requester(
    fire_path: FirePath, memory_store: FakeMemoryStore,
) -> None:
    """`peer_card` goes silently empty when the tagger falls out — it is keyed on
    `ref.honcho_peer_id`, so a `None` tagger yields `()` with nothing raising."""
    ctx = await fire_path.build_context(reminder_occasion([_reminder()]))

    assert ctx.peer_card == ("drinks his coffee black",)


async def test_the_transcript_is_the_conversation_the_row_names(
    fire_path: FirePath, record_store: FakeRecordStore,
) -> None:
    """A fire turn with an empty transcript passes every gate and answers a
    reminder with no idea what the room has been saying."""
    await record_store.append(message=_inbound("conv-1", "去邊食"))
    await record_store.append(message=_inbound("conv-OTHER", "唔關事"))

    ctx = await fire_path.build_context(reminder_occasion([_reminder()]))

    assert [row.body for row in ctx.transcript] == ["去邊食"]


async def test_several_rows_for_one_requester_are_one_turn(
    fire_path: FirePath,
) -> None:
    """The tick groups by (conversation, requester), so `rows` can be more than
    one — and every sentence has to reach her or a reminder is silently eaten
    by a turn that only spoke for the first."""
    first = _reminder(text="洗衫")
    second = _reminder(text="打電話俾阿媽")

    ctx = await fire_path.build_context(reminder_occasion([first, second]))

    assert "洗衫" in ctx.message.body
    assert "打電話俾阿媽" in ctx.message.body


async def test_an_unmapped_requester_leaves_the_tagger_none(
    fire_path: FirePath,
) -> None:
    """identity.toml is loaded once at startup and a row may be thirty days
    old, so the person a row names may no longer be in it. `build_context`
    reports that honestly rather than inventing a PersonRef; `run()` is what
    refuses the turn."""
    ctx = await fire_path.build_context(reminder_occasion([_reminder(requester_person_id="ghost")]))

    assert ctx.tagger is None


async def test_build_context_chases_an_unresolved_quote_within_the_window(
    persons: Persons, record_store: FakeRecordStore, memory_store: FakeMemoryStore,
    doctrine_store, metrics: Metrics,
) -> None:
    """DEC-195 on the fire path's shape: a
    participation occasion carries no single triggering message
    (`participation_occasion`'s `body=""`, `person_id=None`), so there is no
    ONE quote to check the way the tag path has. `recent_for_window` reads
    the plain `context_window` first and, finding a row INSIDE it whose OWN
    `quoted_ts` points outside what was read, grows to reach it — the exact
    condition `_parent_of` (`nora.agent.prefill`) would otherwise fail to
    resolve when this transcript renders.

    25 rows seeded (`ts`/id 0..24), `context_window=10` — row 24 (the most
    recent) quotes `ts=5`, 20 rows back, outside the plain 15..24 window.
    `depth_to(5)` is 20, past `context_window`, so this grows to
    `depth + margin` (default 10) = 30, reaching every row."""
    from nora.domain.models import NormalizedMessage, Quote
    from nora.domain.quote_window import QuoteWindow

    for i in range(24):
        await record_store.append(message=NormalizedMessage(
            platform="signal", conversation_id="conv-1", conversation_type="group",
            sender_id="+85212345678", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    await record_store.append(message=NormalizedMessage(
        platform="signal", conversation_id="conv-1", conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=24, body="m24", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=5),
    ))
    path = FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapters={}, registry=None,                 # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=10, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
        quote_window=QuoteWindow(audit=record_store, metrics=metrics),
    )

    ctx = await path.build_context(participation_occasion(
        platform="signal", conversation_id="conv-1",
    ))

    assert ctx.transcript[0].body == "m0"
    assert any(row.body == "m5" for row in ctx.transcript)


async def test_build_context_with_no_quote_window_wired_is_unchanged(
    fire_path: FirePath, record_store: FakeRecordStore,
) -> None:
    """The `fire_path` fixture (`context_window=40`) passes no `quote_window`
    — every other test in this file relies on that being a genuine no-op, not
    an accident this one pins directly. 50 rows seeded, the most recent
    quoting `ts=5` (35 rows back, outside a 40-row window's OLDEST edge is
    `ts=10`): with no `QuoteWindow` wired this must stay at the plain
    40-row window rather than grow to reach it."""
    from nora.domain.models import NormalizedMessage, Quote

    for i in range(49):
        await record_store.append(message=NormalizedMessage(
            platform="signal", conversation_id="conv-1", conversation_type="group",
            sender_id="+85212345678", sender_name="Miles", timestamp=i, body=f"m{i}", raw={},
        ))
    await record_store.append(message=NormalizedMessage(
        platform="signal", conversation_id="conv-1", conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=49, body="m49", raw={},
        quote=Quote(author_id="x", text="", quoted_ts=5),
    ))

    ctx = await fire_path.build_context(participation_occasion(
        platform="signal", conversation_id="conv-1",
    ))

    assert [row.body for row in ctx.transcript] == [f"m{i}" for i in range(10, 50)]


def _inbound(conversation_id: str, body: str):
    from nora.domain.models import NormalizedMessage
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id="+85212345678", sender_name="Miles", timestamp=1_754_000_000_000,
        body=body, raw={},
    )


# ---------------------------------------------------------------------------
# `run()` — the fire turn's own accounting
# ---------------------------------------------------------------------------

# The shipped shape of `[llm.limits]`: `total_tokens` is what the scripted
# multi-message turns below actually spend against, so a turn is cut off by the
# REAL cap rather than by a flag the double was told to return.
LIMITS = {"turns": 4, "output_tokens": 400, "total_tokens": 24_000}


class _FireAdapter:
    """The `PlatformAdapter` surface a fire turn touches: identity + send.

    `identity_raises` is how the handler-error probe reaches the ONE stage
    that runs before anything else in `_turn` — a fault there is the wiring
    fault `failed_error` cannot otherwise be told apart from an ordinary model
    error.
    """

    platform = "signal"

    def __init__(
        self, *, identity_raises: Exception | None = None, send_ok: bool = True,
    ) -> None:
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.sent_mentions: list[tuple] = []
        self._identity_raises = identity_raises
        # A send that REACHES the platform and is rejected there — the real
        # adapters' `SendResult(ok=False)`, not an exception. `Deliverer.deliver`
        # returns False for that exactly as it does for a guard refusal, which
        # is the confusion the shadow callers must not report.
        self._send_ok = send_ok

    async def resolve_identity(self) -> PlatformIdentity:
        if self._identity_raises is not None:
            raise self._identity_raises
        return PlatformIdentity(platform=self.platform, primary="+85200000000")

    async def send(
        self, *, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
        quote: object | None = None,
    ) -> SendResult:
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        return SendResult(ok=self._send_ok)

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
    adapters: dict | None = None,
    memory: FakeMemoryStore | None = None,
    records: FakeRecordStore | None = None,
    registry: ToolRegistry | None = None,
    rosters: dict | None = None,
    participation_runner=None,
    shadow_channel: str | None = None,
    shadow_platform: str = "discord",
    gate_shadow_channel: str | None = None,
    gate_shadow_platform: str = "discord",
    timeout_seconds: float = 5.0,
    event_registry: dict | None = None,
    standing=None,
    standing_store_timeout_seconds: float = 5.0,
    decisions=None,
    policy=None,
    defer_tools: bool = False,
) -> FirePath:
    extra = {"standing": standing,
             "standing_store_timeout_seconds": standing_store_timeout_seconds,
             "decisions": decisions}
    if policy is not None:
        extra["policy"] = policy
    if event_registry is not None:
        extra["event_registry"] = event_registry
    return FirePath(
        persons=persons,
        records=records if records is not None else FakeRecordStore(),
        memory=memory if memory is not None else FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics, runner=runner,
        adapters=adapters if adapters is not None else {
            "signal": adapter if adapter is not None else _FireAdapter()
        },
        registry=registry if registry is not None else ToolRegistry(),
        rosters=rosters,
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40,
        limits=LIMITS, timeout_seconds=timeout_seconds, clock=lambda: T0,
        defer_tools=defer_tools,
        participation_runner=participation_runner,
        shadow_channel=shadow_channel, shadow_platform=shadow_platform,
        gate_shadow_channel=gate_shadow_channel,
        gate_shadow_platform=gate_shadow_platform,
        **extra,
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
    assert outcome == "sent"                        # delivered, so `sent`
    assert metrics.get(
        "nora_partial_replies_total", reason="limit_total_tokens", platform="signal",
    ) == 1
    # NOT silent, and that is the whole point.
    assert metrics.get("nora_silent_failures_total", reason="limit_total_tokens") == 0


async def test_a_timeout_after_the_reminder_went_out_is_a_partial_delivery(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The contradiction `docs/runbook.md` sends an operator into: the invoke stage
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
    """A fire turn must not silently move a tag-path series. The histogram would
    mix samples bounded by `[heartbeat] timeout_seconds` with tag samples bounded
    by `[llm] timeout_seconds`, and the alarm on
    `nora_llm_stop_reason_total{reason=~"limit_.*"}` could not aim an
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
    """The handler-error probe on the fire path. Everything between `resolve_identity`
    and `build_tag_message` runs before any counter moves, so a wiring fault
    would reach `HeartbeatLoop._run_turn` as a bare `failed_error` — the same string
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
    first."""
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
    """DEFENSIVE. The shipped `HonchoClient.peer_card` returns `[]` on
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


# ---------------------------------------------------------------------------
# The two refusals `run()` OWNS, pinned on `run()` itself: pinning them only
# indirectly (through `build_context`, or a tool test that passes the flag by
# hand) would leave deleting either line inside `run()` turning nothing red.
# ---------------------------------------------------------------------------


async def test_a_reminder_whose_requester_left_identity_toml_sends_nothing(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The fire-time identity re-check, pinned on `run()` rather than on
    `build_context`.

    `build_context` reporting `tagger is None` is only half the rule: what
    matters is what `run()` DOES with it — `failed_policy`, nothing sent,
    nothing counted. Without that assertion, deleting the early return in
    `_turn` would leave a row naming a person identity.toml no longer knows
    running a full turn on somebody else's behalf.

    All three halves are here, and the counters are the half that is easy to
    miss: `failed_policy` is not a FAILURE of Nora's, so an R-5 alarm that moved
    for it would charge a withdrawn grant to the silent-failure budget. The
    counter that SHOULD move is `nora_reminders_fired_total{failed_policy}`, and
    that one is the heartbeat's to write (`_resolve`), not this path's.
    """
    adapter = _FireAdapter()
    runner = FakeAgentRunner(_send_turns("記得食藥"))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=runner,
    )

    outcome = await path.run([_reminder(requester_person_id="ghost")])

    assert outcome == "failed_policy"
    # Nothing reached the group, and the model was never asked anything.
    assert adapter.sent == []
    assert runner.invocations == 0
    # Nothing was counted. Read with NO labels, which `Metrics.get` sums over
    # every dimension — an assertion naming a reason would pass against a turn
    # that raised under a different one.
    for series in ("nora_silent_failures_total", "nora_llm_stop_reason_total",
                   "nora_partial_replies_total", "nora_replies_total",
                   "nora_errors_total"):
        assert metrics.get(series) == 0, series
    assert metrics.observations("nora_llm_latency_seconds") == []


async def test_an_occasion_nobody_asked_for_still_speaks(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A person-less occasion (DEC-97's shape), on the real turn rather than
    through a stub.

    Such an occasion names NO person — nobody asked — and the test above
    (`…requester_person_id="ghost"`) shows what the identity re-check does to a turn whose person
    cannot be resolved: it refuses, silently, as `failed_policy`. Those two states are one line
    apart in `_turn`, so without this a person-less occasion would be refused by a rule about a
    grant it never held, and every caller's own test would still pass (they stub `speak`).

    `tagger` is therefore `None` here on purpose, and the assertions below pin
    the consequences as ACCEPTED rather than accidental: the turn runs, the group
    hears it, and the `<tag_message>` names the occasion's own speaker rather
    than attributing the words to a person who never said them.
    """
    adapter = _FireAdapter()
    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter,
        runner=FakeAgentRunner(_send_turns("其實我識講返自己version㗎"),
                               on_call=calls.append),
    )

    outcome = await path.speak(Event(
        platform="signal", conversation_id="conv-1", conversation_type="group",
        person_id=None, body="You can ask her which version she is running.",
        speaker="the people who build you", kind="compute_lost",
        header=lambda tagger, now: "Nobody has just called you.",
    ))

    assert outcome == "sent"
    assert adapter.texts == ["其實我識講返自己version㗎"]
    tag_message = calls[-1]["tag_message"]
    assert 'from="the people who build you"' in tag_message
    assert "You can ask her which version she is running." in tag_message


async def test_an_occasion_that_names_a_missing_person_is_still_refused(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The control for the test above: relaxing the identity re-check for a person-LESS occasion
    must not relax it for one that names somebody who has left identity.toml.

    Asserted on `speak` directly, with an occasion built by hand, so it cannot
    be satisfied by `reminder_occasion` happening to be the only caller that
    sets `person_id`.
    """
    adapter = _FireAdapter()
    runner = FakeAgentRunner(_send_turns("記得食藥"))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=runner,
    )

    outcome = await path.speak(Event(
        platform="signal", conversation_id="conv-1", conversation_type="group",
        person_id="ghost", body="洗衫", kind="reminder",
        header=lambda tagger, now: "never rendered",
    ))

    assert outcome == "failed_policy"
    assert adapter.sent == []
    assert runner.invocations == 0


async def test_the_fire_path_hands_its_tools_the_turn_provenance(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """R-39, pinned at the ONE place that connects it to production.

    `remind`'s refusal has its own test, but that test passes `fire_turn=True`
    into the registry by hand — it proves the tool reads the flag, not that
    anything ever sets it. The single line that does is
    `registry.build(..., fire_turn=True)` in `FirePath._turn`.

    So this builds the registry the way the composition root does, runs a REAL
    fire turn whose script calls `remind`, and asserts the refusal actually
    happened: without the flag she re-arms her own reminder every time one comes
    due, which is recurrence with none of the recurrence caps able to stop it.
    """
    store = FakeReminderStore()
    registry = ToolRegistry(
        reminders=store, reminder_caps=ReminderCaps(), clock=lambda: T0,
    )
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, registry=registry,
        runner=FakeAgentRunner([
            # Cycle one: she tries to set a fresh reminder from inside the turn
            # that is a reminder going off. Cycle two: she answers, so the turn
            # is an ordinary successful fire and the assertions below are about
            # the refusal alone.
            ScriptedTurn(tool_calls=[
                ("remind", {"when": (T0 + timedelta(hours=2)).isoformat(),
                            "text": "再提我一次"}),
            ]),
            *_send_turns("記得食藥"),
        ]),
    )

    assert await path.run([_reminder()]) == "sent"
    assert adapter.texts == ["記得食藥"]

    # The refusal is the assertion; the empty store is what makes it mean
    # something (a refusal that still wrote the row would satisfy a
    # status-only check and lose nothing but the truth).
    assert metrics.get("nora_reminders_created_total", status="fire_turn") == 1
    assert metrics.get("nora_reminders_created_total", status="created") == 0
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_tool_calls_total", tool="remind", status="refused") == 1


def test_the_audience_label_is_the_one_constant_every_fire_counter_carries() -> None:
    """A spelling check, because every assertion above depends on it and a typo
    would make them all pass against a series nobody alerts on."""
    assert fire_module.AUDIENCE == "heartbeat"


# ---- DEC-85: the header says a repeat is a repeat -----------------------------

def _series(occurrence: int = 2, count: int = 14):
    from nora.domain.reminders import Recurrence, RecurrenceRule, Series

    anchor = T0 - timedelta(days=occurrence)
    return Series(
        id=uuid.uuid5(uuid.NAMESPACE_URL, "series"), anchor_at=anchor,
        occurrence=occurrence,
        rule=RecurrenceRule(
            every=Recurrence.DAILY, tz="Asia/Hong_Kong", count=count,
        ),
    )


def test_a_one_shot_fire_header_is_unchanged_by_recurrence() -> None:
    """The control, and DEC-85's contract: a one-shot turn's prompt carries no
    recurring-header clause. Every clause the recurring header
    adds is asserted ABSENT here, so a clause that leaked into the ordinary path
    — telling her every reminder is a standing arrangement — is caught by the
    test that says the ordinary path did not change."""
    header = ReminderHeader((_reminder(),), MILES).render()

    assert "every" not in header
    assert "repeat" not in header
    assert header.startswith("You are in a group chat")


def test_a_recurring_fire_header_says_it_repeats_and_how_much_is_left() -> None:
    """Third of fourteen, and she is told so.

    Without it she has no way to know this is a standing arrangement, and the
    failure is specific rather than vague: she announces the reminder as if it
    were new («記住喇, 我會日日提你») on every one of the fourteen days, which is
    how a daily reminder becomes a thing people mute. The COUNT is in the line
    as well because «11 more» and «last one» are different things to say.
    """
    row = _reminder(series=_series(occurrence=2, count=14))

    header = ReminderHeader((row,), MILES).render()

    assert "(every day, 11 more after this)" in header
    assert "A repeat is not a new arrangement" in header


def test_the_final_occurrence_says_so_rather_than_counting_zero() -> None:
    """«0 more after this» is a sentence about arithmetic; «last one» is a
    sentence about the thing. She is being asked to speak, not to report."""
    row = _reminder(series=_series(occurrence=13, count=14))

    header = ReminderHeader((row,), MILES).render()

    assert "(every day, last one)" in header


# ---- compute_occasion --------------------------------
#
# The pure-function half, beside `_fire_header`'s above: what a finished — or
# failed — compute job becomes when it is put to her. `ComputeJobPoller`'s own
# tests (`tests/unit/test_compute_poller.py`) cover the delivery around it.


def _compute_job(**kw: object) -> ComputeJob:
    base = {
        "id": uuid.uuid4(), "backend_job_id": "job_1", "task": "chart the rainfall",
        "artifact_id": None, "platform": "signal", "conversation_id": "conv-1",
        "conversation_type": "group", "requester_person_id": "miles",
        "created_at": T0, "state": ComputeJobState.PENDING, "completed_at": None,
        "result_url": None, "result_summary": None, "error": None,
        "task_sha256": "hash-task", "context_sha256": "hash-context", "kind": "subagent",
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return ComputeJob(**base)                                   # type: ignore[arg-type]


def test_a_finished_job_puts_its_summary_and_link_to_her() -> None:
    """The control for the failure test below: on success she is told the
    summary AND the url, because a report she cannot link to is not delivered.
    """
    occasion = compute_occasion(
        _compute_job(),
        JobResult(
            status=JobStatus.SUCCEEDED, url="https://portal.example.net/artifacts/abc",
            summary="12 wet days in July",
        ),
    )

    assert "It finished: 12 wet days in July" in occasion.body
    assert "https://portal.example.net/artifacts/abc" in occasion.body
    assert "chart the rainfall" in occasion.body   # what was asked, still there
    assert occasion.person_id == "miles"
    assert occasion.kind == "compute"


def test_a_delivery_names_the_job_by_its_title_and_an_older_row_by_its_task() -> None:
    """A sub-agent job carries a title, and the delivery names it by that
    title; a payload without one (a row from before titles, or another kind) falls back to
    the task text."""
    result = JobResult(status=JobStatus.SUCCEEDED, summary="done")
    titled = compute_occasion(
        _compute_job(payload={"title": "Rainfall chart", "objective": "chart the rainfall"}),
        result)
    assert titled.body.startswith("(asked earlier) Rainfall chart")
    assert "chart the rainfall" not in titled.body
    untitled = compute_occasion(_compute_job(), result)
    assert untitled.body.startswith("(asked earlier) chart the rainfall")


def test_a_failed_job_is_put_to_her_as_a_failure_not_as_a_result() -> None:
    """The branch nothing exercised. A `JobResult` that is not SUCCEEDED must
    reach her AS a failure — she composes the group-facing message from this
    body, so a failure worded like a result is how she ends up announcing an
    artifact that does not exist. The backend's own error rides along because
    «it didn't work» with no reason is not something anybody can act on.
    """
    occasion = compute_occasion(
        _compute_job(),
        JobResult(status=JobStatus.FAILED, error="the sandbox ran out of memory"),
    )

    assert "It failed: the sandbox ran out of memory" in occasion.body
    assert "It finished" not in occasion.body
    assert "chart the rainfall" in occasion.body
    # The header still names the situation as one SHE has to explain.
    assert "or failed" in occasion.header(MILES, T0)


def test_a_failure_with_no_error_says_so_rather_than_reading_as_success() -> None:
    """`error` is `str | None` on `JobResult`, and `None` is reachable — a
    backend that reports FAILED without a message. Formatted naively that
    becomes «It failed: None»; left out entirely it becomes «It failed:», which
    a model fills in for itself. Neither is an answer, so the absence is named.
    """
    occasion = compute_occasion(_compute_job(), JobResult(status=JobStatus.FAILED))

    assert "It failed: no reason given" in occasion.body
    assert "None" not in occasion.body


def test_a_finished_job_puts_its_report_and_documents_to_her_not_a_build() -> None:
    """Kind-neutral wording: the report (not the summary) is what came back, each read-back
    document carries its title and link, and the header does not say she was asked to build."""
    from nora.domain.compute import DocumentReadBack, ReadBackDocument

    occasion = compute_occasion(
        _compute_job(),
        JobResult(
            status=JobStatus.SUCCEEDED, summary="short", report="sorted 2 sheets",
            document_ids=("d1", "d2"),
        ),
        DocumentReadBack(
            found=(ReadBackDocument("d1", "Sheet one", "https://nora.example/documents/aaaaaa"),),
            missing=("d2",),
        ),
    )

    assert "It finished: sorted 2 sheets" in occasion.body
    assert "short" not in occasion.body
    assert "Sheet one: https://nora.example/documents/aaaaaa" in occasion.body
    assert "could not be found" in occasion.body and "d2" in occasion.body
    assert occasion.document_urls == ("https://nora.example/documents/aaaaaa",)
    header = occasion.header(MILES, T0)
    assert "build" not in header and "or failed" in header


def test_a_result_naming_documents_nobody_read_back_reports_them_all_missing() -> None:
    """Fail closed: no read-back means no document is vouched for or linked."""
    occasion = compute_occasion(
        _compute_job(), JobResult(status=JobStatus.SUCCEEDED, report="done", document_ids=("d1",)),
    )

    assert "could not be found" in occasion.body and "d1" in occasion.body
    assert occasion.document_urls == ()


class _DiscordFireAdapter(_FireAdapter):
    """A second platform for the fire path, identical but for which one it is."""

    platform = "discord"


async def test_a_fire_turn_speaks_on_the_platform_the_occasion_names(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Fire-path eligibility is explicitly NOT platform-specific, so a reminder
    owed in a Discord channel fires there.

    Speaking every occasion into one platform's adapter regardless of where it
    was owed would be unrecoverable for an at-most-once occasion: it is CLAIMED
    for the Discord room and delivered to a Signal one, and the claim bars a
    second attempt forever.
    """
    signal_adapter = _FireAdapter()
    discord_adapter = _DiscordFireAdapter()
    records = FakeRecordStore()
    fire = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(
            tool_calls=[("send_reply", {"text": "收到"})], final_text="收到",
        )]),
        records=records,
        adapters={"signal": signal_adapter, "discord": discord_adapter},
    )

    outcome = await fire.speak(reminder_occasion(
        [_reminder(platform="discord", conversation_id="111/222")]
    ))

    assert outcome == fire_module.SENT
    assert discord_adapter.texts == ["收到"]
    assert signal_adapter.sent == [], (
        "an occasion owed on Discord must not be spoken into Signal"
    )


async def test_a_fire_turn_for_an_unwired_platform_fails_rather_than_speaking(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The absent-route case, and why `_adapter_for` raises rather than falling
    back: speaking a reminder into a room on another platform is worse than not
    speaking it. `_turn`'s outer guard turns the raise into `failed_error` plus
    `nora_silent_failures_total{reason="handler_error"}` — loud and counted,
    with nothing delivered."""
    signal_adapter = _FireAdapter()
    fire = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(final_text="never reached")]),
        adapters={"signal": signal_adapter},
    )

    outcome = await fire.speak(reminder_occasion(
        [_reminder(platform="discord", conversation_id="111/222")]
    ))

    assert outcome == fire_module.FAILED_ERROR
    assert signal_adapter.sent == [], "an unroutable occasion must not fall back"
    assert metrics.get(
        "nora_silent_failures_total", reason="handler_error", audience="heartbeat",
    ) >= 1.0


# ---------------------------------------------------------------------------
# DEC-139 — kind dispatch, registry fail-closed
# ---------------------------------------------------------------------------


async def test_run_dispatches_by_kind_not_by_row_order(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Two kinds due for the same requester+conversation become two turns
    (the heartbeat's `_by_turn` key includes kind), whatever the sort order."""
    from nora.app.heartbeat import _by_turn

    rows = [
        _reminder(id=uuid.uuid4(), text="買奶", kind="reminder"),
        _reminder(id=uuid.uuid4(), text="dinner", kind="proactive"),
    ]
    grouped = _by_turn(rows)
    assert len(grouped) == 2
    kinds = {key[3] for key in grouped}
    assert kinds == {"reminder", "proactive"}


async def test_unregistered_kind_fails_closed(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """An unknown `kind` on a due row is a wiring bug: `run` raises BEFORE
    speak, and nothing reaches the group. The heartbeat's per-group guard
    catches it and `recover()` resolves the row — never a malformed turn."""
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(final_text="never reached")]),
    )

    with pytest.raises(KeyError, match="no event factory registered"):
        await path.run([_reminder(kind="no_such_kind")])


async def test_legacy_reminder_row_still_fires_after_the_kind_columns_land(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A row written BEFORE DEC-139
    (no kind — defaults to "reminder", no payload) round-trips and fires
    through `reminder_occasion` exactly as it did before. The columns are
    additive; nothing about an old row's rendering changes."""
    adapter = _FireAdapter()
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("記得食藥")), adapter=adapter,
    )
    row = _reminder(text="記得食藥")  # kind defaults to "reminder", payload None

    outcome = await path.run([row])

    assert outcome == fire_module.SENT
    assert adapter.texts == ["記得食藥"]


# --- <standing> on the fire path --------------------------------------

async def _fire_path_with_standing(
    persons, record_store, memory_store, doctrine_store, metrics,
    standing, store_timeout: float = 5.0,
) -> FirePath:
    """`fire_path`'s own builder plus the standing collaborator — same
    model-side-collaborators-are-None shape, for the same reason: these tests
    call `build_context` only."""
    return FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapters={}, registry=None,                # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
        standing=standing, standing_store_timeout_seconds=store_timeout,
    )


async def test_the_fire_path_renders_the_conversations_position(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """`<standing>` reaches the fire turn's prefill. `build_prefill` is shared
    and parameterised only by `header=`, so SUPPRESSING `<standing>` here is
    the special case needing justification — and a `build_context` that does
    not pass `standing=` suppresses it silently, because the field defaults
    to `None`.

    It matters most on this path: a fire turn speaks into a room UNPROMPTED, so
    «where had this conversation got to» is the context she is least likely to
    have any other way — there is no tagging message to infer it from.
    """
    from nora.domain.standing import Standing
    from nora.testing import InMemoryStandingStore

    store = InMemoryStandingStore()
    fire = await _fire_path_with_standing(
        persons, record_store, memory_store, doctrine_store, metrics, store)
    occasion = reminder_occasion([_reminder()])
    await store.write(standing=Standing(
        platform=occasion.platform, conversation_id=occasion.conversation_id,
        body="They settled on somewhere for the 9th; Grant is travelling.",
        fold_count=2, updated_at=1,
    ))

    ctx = await fire.build_context(occasion)

    assert ctx.standing is not None
    assert "Grant is travelling" in ctx.standing.body


async def test_a_hanging_standing_read_cannot_wedge_a_fire_turn(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """**An unbounded read here wedges the conversation.**

    The heartbeat holds this conversation's slot around the whole fire turn
    (`try_acquire` -> `release_and_dispatch`) and `create_sql_engine` sets no
    `command_timeout`, so an unbounded standing read would hold the slot
    indefinitely.

    Falsification: drop the `asyncio.wait_for` in `_read_standing` and this
    hangs instead of failing.
    """
    class _Hangs:
        async def read(self, **kw):
            await asyncio.sleep(30)
            raise AssertionError("should have been cut off")

    fire = await _fire_path_with_standing(
        persons, record_store, memory_store, doctrine_store, metrics,
        _Hangs(), store_timeout=0.05)

    ctx = await asyncio.wait_for(
        fire.build_context(reminder_occasion([_reminder()])), timeout=5)

    assert ctx.standing is None
    assert metrics.get("nora_errors_total", stage="fire_standing") == 1


# ---------------------------------------------------------------------------
# The note tier on the fire path
# ---------------------------------------------------------------------------
# The participation determination grounds on «transcript + standing/notes»;
# these pin the read itself, the fail-soft accounting, and the wiring-gap state.

async def _fire_path_with_tiers(
    persons, record_store, memory_store, doctrine_store, metrics, *,
    notes=None, policy="permissive", culture=None, standing=None, memos=None,
) -> FirePath:
    """`fire_path`'s own builder plus the tier collaborators — same
    model-side-collaborators-are-None shape, for the same reason: these tests
    call `build_context` only. `policy="permissive"` resolves every room to
    the `nora` unit, matching where the seeds below are keyed."""
    return FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapters={}, registry=None,                # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
        notes=notes, culture=culture, standing=standing, memos=memos,
        policy=permissive_policy() if policy == "permissive" else None,
    )


def _seeded_note_store() -> InMemoryNoteStore:
    store = InMemoryNoteStore()
    store.notes["11111111-0000-4000-8000-000000000001"] = Note(
        id="11111111-0000-4000-8000-000000000001", unit="nora",
        title="Raft roadmap", description="when the next Raft update ships",
        uncertainty="no date announced", closing_condition="Raft announces a date",
        established_in="conv-1", created_at=1,
        head_revision_id="22222222-0000-4000-8000-000000000002",
        anchor_date=20260101,
    )
    store.revisions["22222222-0000-4000-8000-000000000002"] = NoteRevision(
        id="22222222-0000-4000-8000-000000000002",
        note_id="11111111-0000-4000-8000-000000000001",
        body="still no date", parent_revision_id=None,
        written_at=1, established_by="test-seed",
    )
    return store


async def test_the_fire_path_reads_the_note_index(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """The determination's `<notes>` block: the standing index for the room's
    UNIT, not the room — DEC-105's keying, mirrored from
    `TranscriptInjection._notes`."""
    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics,
        notes=_seeded_note_store(),
    )

    ctx = await fire.build_context(reminder_occasion([_reminder()]))

    assert [n.title for n in ctx.notes] == ["Raft roadmap"]


async def test_a_standing_and_culture_head_render_in_a_fire_turn(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """A room holding a Standing head AND a culture head renders both in a
    reminder-fired turn."""
    from nora.domain.standing import Standing
    from nora.testing import InMemoryStandingStore
    from nora.testing.culture_store import InMemoryCultureStore

    occasion = reminder_occasion([_reminder()])
    standing = InMemoryStandingStore()
    await standing.write(standing=Standing(
        platform=occasion.platform, conversation_id=occasion.conversation_id,
        body="a position", fold_count=1, updated_at=1,
    ))
    culture = InMemoryCultureStore()
    await culture.append(
        key=occasion.conversation_id, body="an account", written_at=1,
        established_by="fold",
    )
    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics,
        culture=culture, standing=standing,
    )
    ctx = await fire.build_context(occasion)
    assert ctx.standing is not None and ctx.standing.body == "a position"
    assert ctx.culture is not None and ctx.culture.body == "an account"


async def test_a_failed_note_read_is_counted_not_silent(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """R-27 on this path: an index that exists but could not be read is
    `unavailable` (counted), never folded silently into «there are none»."""
    class _Down:
        async def standing(self, *, unit: str):
            raise RuntimeError("db down")

    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics,
        notes=_Down(),
    )

    ctx = await fire.build_context(reminder_occasion([_reminder()]))

    assert ctx.notes == ()
    assert metrics.get("nora_errors_total", stage="fire_notes") == 1


async def test_a_wired_tier_without_a_policy_is_a_counted_wiring_gap(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """A store wired but no `Policy` to scope it by: the tier defaults safely
    AND the gap is counted ONCE for the turn — guessing a unit would
    render another social unit's notes into this room (DEC-105)."""
    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics,
        notes=_seeded_note_store(), policy=None,
    )

    ctx = await fire.build_context(reminder_occasion([_reminder()]))

    assert ctx.notes == ()
    assert metrics.get("nora_errors_total", stage="fire_unit") == 1


async def test_the_dormant_default_reads_nothing_and_counts_nothing(
    fire_path: FirePath, metrics,
) -> None:
    """No tier collaborator wired (the shipped `[notes] enabled = false`):
    empty field, and no wiring-gap count — a deployment with the tier off does
    not HAVE the gap."""
    ctx = await fire_path.build_context(reminder_occasion([_reminder()]))

    assert ctx.notes == ()
    assert metrics.get("nora_errors_total", stage="fire_unit") == 0


# ---------------------------------------------------------------------------
# participation — the fifth Event
# ---------------------------------------------------------------------------
# The occasion that goes THROUGH `FirePath.speak` like the four reminder-row
# kinds, with three occasion-gated differences this section pins: its own
# runner (composed with `tool_choice="auto"`), NO DEC-93 recovery on any
# branch (a draft written instead of a send IS the model declining the
# floor), and a quiet turn that answers SILENT rather than a failure string.

def _participation(conversation_id: str = "conv-1") -> Event:
    return participation_occasion(platform="signal", conversation_id=conversation_id)


async def test_a_participation_turn_carries_the_gate_verdict_on_its_root(
    persons, doctrine_store, metrics, exported_spans,
) -> None:
    """The gate's verdict rides on the turn's root span, joining the
    `relevance_gate` trace (a separate root decided upstream) to the turn
    that ran behind it — so the room's whole story reads off one span."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([ScriptedTurn(final_text="")]),
    )

    outcome = await path.speak(
        _participation(), gate_verdict="go", gate_score=0.6,
    )

    assert outcome == fire_module.SILENT
    (root,) = [s for s in exported_spans.get_finished_spans()
               if s.name == "participation"]
    attrs = dict(root.attributes or {})
    assert attrs["nora.relevance_gate.verdict"] == "go"
    assert attrs["nora.relevance_gate.score"] == 0.6


async def test_the_participation_turn_never_passes_through_indicate(
    fire_path: FirePath,
) -> None:
    """The participation trigger fires on a REAL message, but the turn must
    still skip the indicate stage — `indicate.begin` would otherwise 👀→⚠️ on
    every silent determination. The mechanism is the fire path's own: it never
    calls indicate at all, and its synthetic message has a falsy sender, which
    is the exact condition `indicate.begin` returns None on.
    """
    assert not hasattr(fire_module, "indicate"), (
        "FirePath must not gain an indicate stage — pin for the participation "
        "occasion, whose every silent turn would leave a ⚠️ behind"
    )

    ctx = await fire_path.build_context(_participation())

    assert ctx.tagger is None                       # the floor, not a requester
    assert ctx.message.sender_id == ""              # indicate.begin -> None
    assert ctx.message.timestamp == 0


async def test_a_quiet_participation_turn_is_silent_not_a_failure(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The healthy default: the model was offered the floor and declined it.
    `silent`, an increment of `nora_participation_silence_total`, and NOTHING
    in `nora_silent_failures_total` — the runbook's `{audience="tag"}` filter
    is load-bearing and this turn must never reach it. Also the runner-selection
    pin: the turn runs on the participation runner, never the tag/heartbeat one.
    """
    adapter = _FireAdapter()
    runner = FakeAgentRunner(_send_turns("the tag runner must not run"))
    participation_runner = FakeAgentRunner([ScriptedTurn(final_text="")])
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=runner, participation_runner=participation_runner,
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert adapter.sent == []
    assert participation_runner.invocations == 1
    assert runner.invocations == 0
    assert metrics.get("nora_participation_silence_total", platform="signal") == 1
    assert metrics.get("nora_silent_failures_total", audience="participation") == 0
    assert metrics.get("nora_silent_failures_total", audience="tag") == 0


async def test_participation_never_recovers_a_written_draft(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The DEC-93 branch, occasion-gated OFF. Under `tool_choice="auto"` prose
    without a `send_reply` call is the model DECLINING the floor; recovering it
    would deliver exactly the turn the determination declined."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([
            ScriptedTurn(final_text="今日天氣唔錯"),   # end_turn, prose, no send
        ]),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert adapter.sent == []
    assert metrics.get("nora_recovered_replies_total") == 0
    assert metrics.get("nora_participation_silence_total", platform="signal") == 1


async def test_a_participation_turn_that_speaks_is_sent_and_counted(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The other half of may-speak: a real send goes through the same
    `deliver.build`/`Deliverer`/audit machinery every occasion uses, answers
    `sent`, and moves its own counter — never the silence one."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SENT
    assert adapter.texts == ["咁啱嘅"]
    assert metrics.get("nora_participation_spoke_total", platform="signal") == 1
    assert metrics.get("nora_participation_silence_total") == 0


async def test_a_budget_cut_participation_turn_is_an_ordinary_silent_failure(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The silence counter is gated on a NON-budget stop: a turn R-8's caps cut
    off before it could decide is a malfunction, not a healthy silence, and
    lands in `nora_silent_failures_total` under the PARTICIPATION audience —
    still never the tag one."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([
            # A benign tool call so the loop checks its caps (a tool-less
            # cycle ends end_turn before the check); 25k against the 24k cap.
            ScriptedTurn(tool_calls=[("now", {})], total_tokens=25_000),
        ]),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert adapter.sent == []
    assert metrics.get("nora_participation_silence_total") == 0
    assert metrics.get(
        "nora_silent_failures_total",
        reason="limit_total_tokens", audience="participation",
    ) == 1
    assert metrics.get("nora_silent_failures_total", audience="tag") == 0


async def test_a_send_held_by_the_cooldown_is_held_at_the_last_inch(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The enforcement half: the trigger DECIDES `send_permitted`, the
    `Deliverer` ENFORCES it after the R-6/R-18 guards and before the audit row.
    `called` stays True — a held send was policy, so nothing reads it as a
    `no_send_call` failure, and nothing counts it as silence either: the model
    DID find grounded value; the room was simply not due to hear it."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(_send_turns("held back")),
    )

    outcome = await path.speak(_participation(), send_permitted=False)

    assert outcome == fire_module.SILENT
    assert adapter.sent == []
    assert metrics.get("nora_participation_silence_total") == 0
    assert metrics.get("nora_participation_spoke_total") == 0
    assert metrics.get("nora_silent_failures_total") == 0


async def test_a_participation_turn_without_a_runner_is_a_wiring_fault(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """`[participation] enabled = true` with no `participation_runner` composed
    must not quietly downgrade to the tag runner (whose `required` pin would
    demand a send): it raises, and `_turn`'s guard files it as a handler_error
    under the participation audience."""
    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.FAILED_ERROR
    assert adapter.sent == []
    assert metrics.get(
        "nora_silent_failures_total", reason="handler_error", audience="participation",
    ) == 1


async def test_the_participation_turn_reads_the_participation_doctrine(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The turn runs on the `applies_to="participation"` assembly — never the
    tag one, and never the GATE's instruction.

    The participation assembly carries a participation-only file that the tag
    one does not: it earns its place by one thing a tag never has, a WINDOW.
    Several people, several threads, sometimes several things worth answering
    at once, none of them addressed to her. `conduct.md` is `[tag, heartbeat,
    participation]` and must not carry a rule that is false on two of the
    three.

    And `participation.md` — the gate's instruction — must still be absent:
    it describes an instrument, on an audience no file of hers declares.
    """
    adapter = _FireAdapter()
    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(
            [ScriptedTurn(final_text="")], on_call=calls.append,
        ),
    )

    await path.speak(_participation())

    doctrine = calls[-1]["doctrine"]
    names = {section.name for section in doctrine.sections}
    assert "participation-conduct" in names, "the ambient-only conduct is missing"
    assert "participation" not in names, "the gate's instruction reached her turn"
    assert doctrine.sha256 != doctrine_store.get(applies_to="tag").sha256, (
        "the participation assembly is byte-identical to the tag one — the "
        "occasion-specific file is not reaching it"
    )


# The live-state tool on the participation occasion. `FirePath._turn` builds
# the tool list through `registry.build(...)` with NO per-occasion filter, so
# the participation turn carries the full native catalogue by construction —
# there is no decision to test. These three pin the exposure itself, the
# room-roster input domain, and the tagger-less fail-closed branch.

_MILES_ONLINE = SteamPlayerState(
    steam_id="111", personastate=1, profile_public=True,
    game="Counter-Strike 2", game_id="730",
)


def _online_persons() -> Persons:
    """The `persons` fixture plus the `pulse` index entry `online_now` reads —
    Miles's Steam id, so the room roster resolves to a checkable account."""
    return Persons(
        by_person={"miles": MILES},
        index={
            ("signal", "+85212345678"): "miles",
            ("pulse", "111"): "miles",
        },
    )


def _captured_online_now(calls: list):
    """The `online_now` tool the turn was ACTUALLY handed — found in the
    `tools` kwarg the participation runner's `invoke` received (the same list
    `LiveAgentRunner.invoke` gets in production), driven through
    `_tool_func`, the seam every adapted-tool test in this repo uses."""
    tools = calls[-1]["tools"]
    return next(t for t in tools if t.tool_name == "online_now")


async def test_a_participation_turn_exposes_the_online_now_tool(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The exposure pin. The participation turn
    is `FirePath.speak`, and `speak` builds its tools with no per-occasion
    filter — `online_now` is on the turn by construction, not by decision.
    That is precisely the shape that regresses silently: a future occasion
    filter that excluded tools would strip `online_now` from the one
    unprompted occasion («有冇人喺度», asked of a quiet room with nobody to
    tag) and every test in this file would stay green."""
    adapter = _FireAdapter()
    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        registry=ToolRegistry(online=FakeSteamPresenceProvider()),
        participation_runner=FakeAgentRunner(
            [ScriptedTurn(final_text="")], on_call=calls.append,
        ),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT         # the full turn really ran
    assert "online_now" in {t.tool_name for t in calls[-1]["tools"]}


async def test_a_participation_turn_answers_online_now_from_the_room_roster(
    doctrine_store, metrics: Metrics,
) -> None:
    """The input domain on this occasion. A participation turn has no tagger
    and no `online_scope` override (that seam belongs to the proactive decide
    turn alone), so the tool's domain must be the INVOKING room's own roster —
    `room.scope()` over the fire path's per-platform roster source. Driving
    the tool the turn was actually handed proves the answer comes from THAT
    roster: Miles, in the room, online — never a degraded one-person fallback
    and never an empty domain."""
    steam = FakeSteamPresenceProvider(players={"111": _MILES_ONLINE})
    roster = FakeRosterSource(groups={"conv-1": (frozenset({"+85212345678"}),)})
    adapter = _FireAdapter()
    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=_online_persons(),
        metrics=metrics, adapter=adapter, runner=FakeAgentRunner([]),
        registry=ToolRegistry(online=steam), rosters={"signal": roster},
        participation_runner=FakeAgentRunner(
            [ScriptedTurn(final_text="")], on_call=calls.append,
        ),
    )

    await path.speak(_participation())

    out = await _captured_online_now(calls)._tool_func(what="online", names=["Miles"])

    assert out["status"] == "ok"
    (miles,) = out["people"]
    assert miles["person"] == "Miles"
    assert miles["state"] == "online"
    assert miles["game"] == "Counter-Strike 2"
    # The roster WAS the domain: the room's one conversation was read, and
    # Miles's id went out in the single batched Steam call the budget promises.
    assert roster.calls == ["conv-1"]
    assert steam.calls == [frozenset({"111"})]


async def test_a_participation_turn_with_an_unreadable_room_makes_online_now_unavailable(
    doctrine_store, metrics: Metrics,
) -> None:
    """The fail-closed half, for the one occasion with NO tagger. A tag turn
    whose roster read fails still narrows to the tagger alone (the degraded
    branch); a participation turn has nobody to narrow TO, so `room.scope()`
    is simply `available=False` and the tool must answer `unavailable` —
    «I couldn't check» — never `nothing_found` («nobody is online») and never
    a guessed one-person room. And no Steam read happens behind a room the
    turn could not confirm."""
    steam = FakeSteamPresenceProvider(players={"111": _MILES_ONLINE})
    adapter = _FireAdapter()
    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=_online_persons(),
        metrics=metrics, adapter=adapter, runner=FakeAgentRunner([]),
        registry=ToolRegistry(online=steam),
        rosters={"signal": FakeRosterSource(fail=True)},
        participation_runner=FakeAgentRunner(
            [ScriptedTurn(final_text="")], on_call=calls.append,
        ),
    )

    await path.speak(_participation())

    out = await _captured_online_now(calls)._tool_func(what="online")

    assert out["status"] == "unavailable"
    assert steam.calls == []


# ---------------------------------------------------------------------------
# The shadow mirror
# ---------------------------------------------------------------------------


def _shadow_path(
    *, doctrine_store, persons: Persons, metrics: Metrics,
    room: _FireAdapter, shadow: _FireAdapter | None,
    participation_runner, shadow_channel: str | None = "mirror-1",
) -> FirePath:
    """A fire path with the shadow mirror wired: the room on signal, the mirror
    channel on discord (the operator-facing lean) — or `shadow=None` for the
    missing-adapter case."""
    adapters: dict[str, _FireAdapter] = {"signal": room}
    if shadow is not None:
        adapters["discord"] = shadow
    return _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapters=adapters, runner=FakeAgentRunner([]),
        participation_runner=participation_runner,
        shadow_channel=shadow_channel,
    )


async def test_the_shadow_mirror_posts_a_spoke_determination(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """TRUE SHADOW: a speak determination while the
    mirror is wired NEVER live-sends — the room hears NOTHING, the shadow
    channel gets the verdict, the actual text and the source room, through
    the same `deliver.build`/guards, under its own audience. The mirror is
    NOT a live send: `nora_participation_spoke_total` stays 0 (the held
    message never reached the room), and the draft is reviewable."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow,
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
    )

    outcome = await path.speak(_participation())

    # True shadow: the room hears nothing; the determination is reviewable.
    assert outcome == fire_module.SILENT
    assert room.texts == []
    assert len(shadow.sent) == 1
    target, mirror = shadow.sent[0]
    assert target.platform == "discord"
    assert target.conversation_id == "mirror-1"
    assert "signal/conv-1" in mirror            # the source room
    assert "SHADOW MODE held it" in mirror      # true shadow wording
    assert "咁啱嘅" in mirror                    # the draft, never sent
    # Live sends only: the mirror never reads as room speech, and the held
    # draft never counts as a spoke.
    assert metrics.get("nora_participation_spoke_total") == 0
    assert metrics.get("nora_participation_spoke_total", platform="signal") == 0
    assert metrics.get("nora_participation_spoke_total", platform="discord") == 0


async def test_the_shadow_mirror_posts_a_silent_determination_with_the_draft(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The silence half: what she WOULD have said (the decline draft, which
    doubles as her reasoning) and the verdict — the review surface the flip
    decision is made from."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow,
        participation_runner=FakeAgentRunner([
            ScriptedTurn(final_text="nothing here needs me"),
        ]),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert room.sent == []
    assert len(shadow.sent) == 1
    _, mirror = shadow.sent[0]
    assert "SILENT" in mirror
    assert "declined the floor" in mirror
    assert "nothing here needs me" in mirror    # the draft, never sent
    assert metrics.get("nora_participation_spoke_total") == 0
    assert metrics.get("nora_participation_silence_total", platform="signal") == 1


async def test_the_shadow_mirror_shows_what_a_held_send_would_have_said(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The held-text seam: the cooldown-held branch retains the text
    (`Deliverer.held_texts`), so a concluded-speak inside the cooldown is
    reviewable — the one case whose words the room's history never records.

    Shadow is OFF here (`shadow_channel=None`): with the mirror wired, the
    shadow gate holds the send and the wording says SHADOW MODE (covered by
    the spoke test above). This test exercises the cooldown wording alone."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, shadow_channel=None,
        participation_runner=FakeAgentRunner(_send_turns("held back")),
    )

    outcome = await path.speak(_participation(), send_permitted=False)

    assert outcome == fire_module.SILENT
    assert room.sent == []
    # Shadow off = no review surface: the cooldown-held text is retained by
    # the Deliverer (the held-text seam) but there is no mirror to post it.
    # The reviewable held-draft case is the shadow-mode spoke test above,
    # which shows the held text under the SHADOW MODE wording.
    assert shadow.sent == []
    assert metrics.get("nora_participation_spoke_total") == 0


async def test_shadow_off_posts_nothing(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The shipped default (`shadow = false` -> no channel wired): no mirror,
    no matter the verdict."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, shadow_channel=None,
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SENT
    assert room.texts == ["咁啱嘅"]
    assert shadow.sent == []


async def test_a_missing_shadow_adapter_fails_soft_and_is_counted(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A shadow platform with no adapter wired must not break the reviewed
    turn — review traffic never changes the outcome — but it is counted, not
    silent (R-5): `nora_errors_total{stage="participation_shadow"}`.

    TRUE SHADOW: the shadow channel is wired, so
    the turn NEVER live-sends even though the mirror adapter is missing — an
    operator who turned shadow on expects the room to hear nothing, and a
    broken mirror must not silently become a live send."""
    room = _FireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=None,
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
    )

    outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert room.texts == []
    assert metrics.get("nora_errors_total", stage="participation_shadow") == 1


def test_a_sub_day_reminder_renders_its_cadence_in_the_fire_header():
    """R-89/DEC-159: every `Recurrence` member has a cadence word.

    `CADENCE_UNITS` maps every `Recurrence` member to a word and `RepeatClause`
    subscripts it BARE, so a missing member (a sub-day `minutely`/`hourly`)
    would raise KeyError inside `_fire_header` — which runs AFTER `claim` has
    minted the successor and BEFORE the model is called. Every occurrence would
    claim a row, say nothing, and arm the next one: the «promised eleven
    updates, delivered zero» failure.

    The INTERVAL is asserted too, not just the unit. Reading only `rule.every`
    renders a fortnight as «every week» and a ten-minute cadence as «every
    minute» — wrong by the whole multiplier, in the one paragraph whose stated
    job is stopping her misdescribing a standing arrangement.
    """
    from nora.domain.framing import CADENCE_UNITS, RepeatClause
    from nora.domain.reminders import Recurrence

    # Total, not just the two added: a future unit that forgets this table
    # breaks the fire path, not the domain, and would be found in production.
    assert set(CADENCE_UNITS) == set(Recurrence) - {Recurrence.NONE}

    assert "every 10 minutes" in RepeatClause(
        _recurring_row(Recurrence.MINUTELY, interval=10).series).render()
    assert "every hour" in RepeatClause(
        _recurring_row(Recurrence.HOURLY, interval=1).series).render()
    assert "every 2 weeks" in RepeatClause(
        _recurring_row(Recurrence.WEEKLY, interval=2).series).render()
    # The existing cadences render unchanged.
    assert "every day" in RepeatClause(
        _recurring_row(Recurrence.DAILY, interval=1).series).render()


def _recurring_row(unit, *, interval: int):
    """One PENDING occurrence of a series, the shape `_fire_header` receives."""
    import uuid
    from datetime import UTC, datetime

    from nora.domain.reminders import (
        Recurrence,
        RecurrenceRule,
        Reminder,
        ReminderState,
        Series,
    )

    assert unit is not Recurrence.NONE
    now = datetime.now(UTC)
    rule = RecurrenceRule(
        every=unit, tz="Asia/Hong_Kong", count=7, interval=interval,
    )
    series_id = uuid.uuid4()
    return Reminder(
        id=uuid.uuid5(series_id, "0"), requester_person_id="p_miles",
        platform="discord", conversation_id="c1", conversation_type="group",
        text="F1 lap update", created_at=now, due_at=now,
        state=ReminderState.PENDING, fired_at=None, outcome=None,
        series=Series(id=series_id, anchor_at=now, occurrence=0, rule=rule),
    )


# ---- every occasion gets a root span --------------------------------


@pytest.fixture
def fire_spans():
    """Captured spans from the REAL fire path.

    Deliberately not the pattern in `tests/test_span_skeleton.py`, whose own
    docstring says why: a test that opens its own spans proves the helper
    nests and nothing else. Driving the real path is what shows whether every
    occasion gets a root.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # The RAW slot, not `get_tracer_provider()`: with no provider set that
    # returns the global PROXY, and restoring the proxy into the slot makes it
    # resolve to itself, so every later `get_tracer` in the same worker
    # recurses.
    previous = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    try:
        yield exporter
    finally:
        trace._TRACER_PROVIDER = previous


async def test_a_reminder_turn_produces_one_named_root_span(
    persons: Persons, doctrine_store, metrics: Metrics, fire_spans,
) -> None:
    """A reminder firing yields one trace with a ROOT span carrying the `nora.*`
    attributes and the OUTCOME.

    Without a root the spans are siblings with no name and none of the `nora.*`
    attributes: N traces, not one trace with gaps. An outcome such as
    `failed_timeout` would then be in Postgres and NOWHERE in the trace, because
    there is no span to annotate it onto.

    An unprompted path is where this matters most: nobody is waiting on it, so
    a silent failure has no human to notice it.
    """
    adapter = _FireAdapter()
    fire = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(
            tool_calls=[("send_reply", {"text": "收到"})], final_text="收到",
        )]),
        adapters={"discord": adapter},
    )

    outcome = await fire.speak(reminder_occasion(
        [_reminder(platform="discord", conversation_id="111/222")]
    ))

    finished = fire_spans.get_finished_spans()
    roots = [s for s in finished if s.parent is None]
    assert len(roots) == 1, (
        f"one root per entry point; got {[s.name for s in roots]}"
    )
    root = roots[0]
    assert root.name == "reminder"
    # The OUTCOME, which is the whole point: `sent` vs `failed_timeout` must be
    # readable in the trace, not only in the reminder row.
    assert root.attributes["nora.outcome"] == outcome
    assert root.attributes["nora.occasion"] == "reminder"
    assert root.attributes["nora.platform"] == "discord"
    assert root.attributes["nora.conversation_id"] == "111/222"
    # `generation`, because the sampler selects on it and this turn contains a
    # real model call. A root that declared otherwise would never reach
    # Langfuse at all.
    assert root.attributes["nora.trace_kind"] == "generation"


async def test_a_failed_fire_turn_still_says_so_on_its_root(
    persons: Persons, doctrine_store, metrics: Metrics, fire_spans,
) -> None:
    """A turn that FAILS must still be legible: the failure case, and the
    positive control for the test above.

    Asserting only the happy path would leave a fire turn that delivered
    nothing untested. An unroutable platform is the cheapest way to drive
    `_turn`'s outer guard to `failed_error` without a clock.
    """
    fire = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(final_text="never reached")]),
        adapters={"signal": _FireAdapter()},
    )

    outcome = await fire.speak(reminder_occasion(
        [_reminder(platform="discord", conversation_id="111/222")]
    ))

    assert outcome == fire_module.FAILED_ERROR
    [root] = [s for s in fire_spans.get_finished_spans() if s.parent is None]
    assert root.name == "reminder"
    assert root.attributes["nora.outcome"] == fire_module.FAILED_ERROR
# ---------------------------------------------------------------------------
# The Relevance Gate's review post
# ---------------------------------------------------------------------------


def _gate_path(
    *, doctrine_store, persons: Persons, metrics: Metrics,
    room: _FireAdapter, shadow: _FireAdapter | None,
    records: FakeRecordStore | None = None,
    channel: str | None = "review-1",
) -> FirePath:
    adapters: dict[str, _FireAdapter] = {"signal": room}
    if shadow is not None:
        adapters["discord"] = shadow
    return _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapters=adapters, runner=FakeAgentRunner([]), records=records,
        gate_shadow_channel=channel,
    )


async def test_the_gate_review_post_goes_to_the_review_channel_not_the_room(
    persons: Persons, doctrine_store, metrics: Metrics,
    record_store: FakeRecordStore,
) -> None:
    """The property shadow mode exists for: Nora is never involved and the ROOM
    HEARS NOTHING.

    Retargeting `post_gate_verdict` from the shadow channel to the source
    room would otherwise pass the suite, leaving "shadow cannot cause a
    room-visible send" unpinned on the one path where being wrong means
    speaking uninvited into somebody's group.
    """
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    await record_store.append(message=_inbound("conv-1", "邊度有好嘢食"))
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, records=record_store,
    )

    await path.post_gate_verdict(
        platform="signal", conversation_id="conv-1", score=0.81,
    )

    assert room.texts == [], "the room heard the gate — shadow is not shadow"
    assert len(shadow.sent) == 1
    target, post = shadow.sent[0]
    assert (target.platform, target.conversation_id) == ("discord", "review-1")
    assert "signal/conv-1" in post          # which room
    assert "邊度有好嘢食" in post             # what was said
    assert "go" in post                     # the verdict
    assert "0.81" in post                   # the score


async def test_an_absent_score_renders_a_dash_and_never_a_fabricated_zero(
    persons: Persons, doctrine_store, metrics: Metrics,
    record_store: FakeRecordStore,
) -> None:
    """The operator tunes the threshold on that column. `0.00` where the model
    gave no number reads as a weak admission that never happened."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    await record_store.append(message=_inbound("conv-1", "hi"))
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, records=record_store,
    )

    await path.post_gate_verdict(
        platform="signal", conversation_id="conv-1", score=None,
    )

    _target, post = shadow.sent[0]
    assert post.endswith("— go — —")
    assert "0.00" not in post


async def test_a_guard_refused_review_post_is_counted_not_silently_dropped(
    persons: Persons, doctrine_store, metrics: Metrics,
    record_store: FakeRecordStore,
) -> None:
    """`Deliverer.deliver` RETURNS False on an R-18 refusal; it does not raise.

    So the `except Exception` around the post never sees one, and the refusal
    would vanish while `nora_relevance_gate_total{verdict="go"}` had already
    counted the GO — the operator tunes the flip on a review stream, which
    would have invisible holes in it. A message quoting a phone number is
    enough to open one.
    """
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    await record_store.append(message=_inbound("conv-1", "call me on +85298765432"))
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, records=record_store,
    )

    await path.post_gate_verdict(
        platform="signal", conversation_id="conv-1", score=0.9,
    )

    assert shadow.sent == [], "R-18 should have refused this post"
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_shadow", platform="signal",
    ) == 1


async def test_a_review_post_the_platform_rejected_is_not_reported_as_a_guard_refusal(
    persons: Persons, doctrine_store, metrics: Metrics,
    record_store: FakeRecordStore, caplog,
) -> None:
    """A platform-rejected send is not reported as a guard refusal. `deliver`
    returns False for BOTH a guard refusal and a send the platform rejected.

    A `shadow_channel` missing its guild half sends every GO to
    `/channels//messages`, which Discord answers 405; naming that "REFUSED by a
    guard" sends the operator to the egress screen, the one place the fault is
    not. The counters are right either way; the sentence must be too.
    """
    room = _FireAdapter()
    shadow = _DiscordFireAdapter(send_ok=False)
    await record_store.append(message=_inbound("conv-1", "邊度有好嘢食"))
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, records=record_store,
    )

    with caplog.at_level(logging.ERROR, logger="nora.app.fire"):
        await path.post_gate_verdict(
            platform="signal", conversation_id="conv-1", score=0.81,
        )

    assert len(shadow.sent) == 1, "the post reached the platform and was rejected there"
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "send_failed" in line
    assert "REFUSED by a guard" not in line
    # Still counted — naming the cause correctly must not cost the error.
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_shadow", platform="signal",
    ) == 1


async def test_a_guard_refusal_is_still_named_as_one(
    persons: Persons, doctrine_store, metrics: Metrics,
    record_store: FakeRecordStore, caplog,
) -> None:
    """The other half of the same discrimination: a post R-18 refused says so.

    Without this, "stop claiming a guard refused it" is satisfied by a line
    that never names a guard again — which would lose the reading that made
    the original message worth having.
    """
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    await record_store.append(message=_inbound("conv-1", "call me on +85298765432"))
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, records=record_store,
    )

    with caplog.at_level(logging.ERROR, logger="nora.app.fire"):
        await path.post_gate_verdict(
            platform="signal", conversation_id="conv-1", score=0.9,
        )

    assert shadow.sent == []
    assert "egress_block" in "\n".join(r.getMessage() for r in caplog.records)


async def test_no_review_channel_wired_posts_nothing_and_raises_nothing(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Shadow off. The poster is simply not wired in that case, but the
    method must stay inert rather than fault the trigger's loop."""
    room, shadow = _FireAdapter(), _DiscordFireAdapter()
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=shadow, channel=None,
    )
    await path.post_gate_verdict(
        platform="signal", conversation_id="conv-1", score=0.5,
    )
    assert shadow.sent == [] and room.texts == []


async def test_a_missing_review_adapter_fails_soft_and_is_counted(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Review traffic must never break the path it reviews — here that means
    a failed post must not stop the NEXT window being judged."""
    room = _FireAdapter()
    path = _gate_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=None,
    )
    await path.post_gate_verdict(
        platform="signal", conversation_id="conv-1", score=0.5,
    )
    assert metrics.get(
        "nora_errors_total", stage="relevance_gate_shadow", platform="signal",
    ) == 1


# ---- the proactive occasion --------------


def _proactive_row(**kw: object) -> Reminder:
    base = {
        "requester_person_id": "nora", "kind": "proactive",
        "text": "ask where everyone is coming from before dinner",
        "payload": {
            "note_id": "00000000-0000-4000-8000-000000000001",
            "revision_id": "00000000-0000-4000-8000-000000000002",
            "note_title": "AI Hackathon dinner plan",
            "intent": "ask where everyone is coming from before dinner",
        },
    }
    base.update(kw)
    return _reminder(**base)


def test_the_proactive_occasion_is_owed_to_nobody_and_names_the_note() -> None:
    """`person_id=None` — participation's shape — because the row's
    owner is the ai_peer id, which no `PersonRef` resolves; the body is the
    decide turn's own intent; the header says she decided it, when, and
    about which note, and states the right to decline."""
    row = _proactive_row()

    ev = fire_module.proactive_occasion([row])

    assert ev.kind == "proactive"
    assert ev.person_id is None
    assert ev.body == "ask where everyone is coming from before dinner"
    assert ev.platform == "signal" and ev.conversation_id == "conv-1"
    header = ev.header(None, T0)
    assert "YOU decided" in header
    assert "«AI Hackathon dinner plan»" in header
    assert "silence is a complete answer" in header
    assert "never as «I set myself a reminder»" in header


def test_the_proactive_header_keeps_the_whole_apparatus_out_of_the_message() -> None:
    """The header's clause says what the apparatus IS, not one forbidden sentence.

    A single named sentence («I set myself a reminder») generalises to none: the
    same turn's prompt also hands her the note title, the `<notes>` index and the
    intent her earlier pass wrote, so a turn can explain the mechanism to the
    room and read a curator-written summary back at the person it was about. So
    the clause says what the apparatus IS (why she is here, not what she says),
    names the note alongside the reminder, and points at what she says instead."""
    header = fire_module.proactive_occasion([_proactive_row()]).header(None, T0)

    assert "never as «I set myself a reminder»" in header
    assert "«my note says»" in header
    assert "never in the note's own words" in header
    # WHY vs WHAT.
    assert "WHY you are here, never WHAT you have to say" in header
    # And what she says instead is already two sentences up, unchanged. The
    # clause deliberately does NOT repeat it: restating «raise the thing itself,
    # out of what you can see in front of you now» duplicates the header's own
    # «Read what is in front of you NOW … decide again».
    assert "Read what is in front of you NOW" in header


def test_a_proactive_occasion_without_a_note_renders_no_note_clause() -> None:
    header = fire_module.proactive_occasion(
        [_proactive_row(payload={"intent": "hi"})]
    ).header(None, T0)
    assert "about the note" not in header


async def test_a_quiet_proactive_turn_is_silent_not_a_failure(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The act turn reads the room again and declines its own earlier
    intention. `silent`, its own counter, NOTHING in `nora_silent_failures_total`
    under any audience — and it ran on the proactive runner (tool_choice
    auto), never the tag/heartbeat one."""
    adapter = _FireAdapter()
    runner = FakeAgentRunner(_send_turns("the tag runner must not run"))
    proactive_runner = FakeAgentRunner([ScriptedTurn(final_text="nothing to add")])
    path = FirePath(
        persons=persons, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics, runner=runner,
        adapters={"signal": adapter}, registry=ToolRegistry(),
        canary="test-canary", display_name="Nora", ai_peer="nora",
        context_window=40, limits=LIMITS, timeout_seconds=5.0, clock=lambda: T0,
        proactive_runner=proactive_runner,
        event_registry={"proactive": fire_module.proactive_occasion},
    )

    outcome = await path.run([_proactive_row()])

    assert outcome == fire_module.SILENT
    assert adapter.sent == []
    assert proactive_runner.invocations == 1
    assert runner.invocations == 0
    assert metrics.get("nora_proactive_silence_total", platform="signal") == 1
    # Prose under `auto` is the decline: never recovered as a reply (DEC-93).
    assert metrics.get("nora_recovered_replies_total") == 0
    for audience in ("tag", "heartbeat", "participation", "proactive"):
        assert metrics.get("nora_silent_failures_total", audience=audience) == 0


async def test_a_proactive_turn_that_speaks_is_sent_and_counted(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    adapter = _FireAdapter()
    path = FirePath(
        persons=persons, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics, runner=FakeAgentRunner([]),
        adapters={"signal": adapter}, registry=ToolRegistry(),
        canary="test-canary", display_name="Nora", ai_peer="nora",
        context_window=40, limits=LIMITS, timeout_seconds=5.0, clock=lambda: T0,
        proactive_runner=FakeAgentRunner(_send_turns("星期六食飯，大家幾點到？")),
        event_registry={"proactive": fire_module.proactive_occasion},
    )

    outcome = await path.run([_proactive_row()])

    assert outcome == fire_module.SENT
    assert adapter.texts == ["星期六食飯，大家幾點到？"]
    assert metrics.get("nora_proactive_spoke_total", platform="signal") == 1
    assert metrics.get("nora_proactive_silence_total") == 0


def _intent_path(persons, doctrine_store, metrics):
    records = FakeRecordStore()
    path = FirePath(
        persons=persons, records=records, memory=FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics, runner=FakeAgentRunner([]),
        adapters={"signal": _FireAdapter()}, registry=ToolRegistry(),
        canary="test-canary", display_name="Nora", ai_peer="nora",
        context_window=40, limits=LIMITS, timeout_seconds=5.0, clock=lambda: T0,
        proactive_runner=FakeAgentRunner(_send_turns("大家幾點到？")),
        event_registry={"proactive": fire_module.proactive_occasion},
    )
    return path, records


@pytest.mark.parametrize(("payload_intent", "recorded"), [
    ("ask", "ask"), ("tell", "tell"),
    # A row holding free text is not an intent: never read as `tell`.
    ("ask where everyone is coming from before dinner", None),
])
async def test_a_proactive_send_records_its_briefs_intent_on_the_sent_row(
    persons: Persons, doctrine_store, metrics: Metrics, payload_intent, recorded,
) -> None:
    path, records = _intent_path(persons, doctrine_store, metrics)
    row = _proactive_row(payload={"note_title": "dinner", "intent": payload_intent})

    assert await path.run([row]) == fire_module.SENT

    sent = [r for r in records.rows if r["is_nora"]]
    assert len(sent) == 1
    assert json.loads(sent[0]["raw_json"]).get("intent") == recorded


def _determinations(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == "determination"]


async def test_a_proactive_decline_is_readable_as_a_determination(
    persons: Persons, doctrine_store, metrics: Metrics,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The decline, in words rather than as a counter.

    The proactive act turn has no shadow mirror and no cooldown, so without the
    determination sink its decline would leave only `nora_proactive_silence_total`, a row
    with `outcome="silent"`, and NOTHING that said what it declined or what it
    would have said. The counter answers how many; only this answers what.
    """
    path = FirePath(
        persons=persons, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([]), adapters={"signal": _FireAdapter()},
        registry=ToolRegistry(), canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits=LIMITS,
        timeout_seconds=5.0, clock=lambda: T0,
        proactive_runner=FakeAgentRunner([
            ScriptedTurn(final_text="she already said it herself"),
        ]),
        event_registry={"proactive": fire_module.proactive_occasion},
    )

    with caplog.at_level(logging.DEBUG, logger="nora.app.fire"):
        outcome = await path.run([_proactive_row()])

    assert outcome == fire_module.SILENT
    shape = _determinations(caplog)
    assert len(shape) == 1, "a proactive decline files exactly one determination"
    assert shape[0].occasion == "proactive"
    assert shape[0].disposition == "declined"
    # Never `held_*`: proactive has no send cooldown and no mirror to hold it.
    assert shape[0].verdict == "silent"
    content = [r for r in caplog.records if r.message == "determination.content"]
    assert content[0].texts == ["she already said it herself"]


async def test_a_proactive_send_is_readable_as_a_determination(
    persons: Persons, doctrine_store, metrics: Metrics,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other half: a spoke determination reaches the same sink, so the two
    outcomes are counted AND read in one place rather than one each."""
    path = FirePath(
        persons=persons, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([]), adapters={"signal": _FireAdapter()},
        registry=ToolRegistry(), canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits=LIMITS,
        timeout_seconds=5.0, clock=lambda: T0,
        proactive_runner=FakeAgentRunner(_send_turns("星期六幾點到？")),
        event_registry={"proactive": fire_module.proactive_occasion},
    )

    with caplog.at_level(logging.INFO, logger="nora.app.fire"):
        outcome = await path.run([_proactive_row()])

    assert outcome == fire_module.SENT
    shape = _determinations(caplog)
    assert len(shape) == 1
    assert (shape[0].occasion, shape[0].disposition) == ("proactive", "spoke")
    # The INFO record carries no speech: `_turn` already decided group speech
    # does not go to INFO, and a determination carries her words too.
    assert "星期六幾點到？" not in str(shape[0].__dict__)


async def test_a_participation_determination_is_logged_without_a_mirror(
    persons: Persons, doctrine_store, metrics: Metrics,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gap the sink closes for participation: with no `shadow_channel` the
    mirror returns early, so without the sink a room without an operator-wired
    review channel would produce no readable determination at all."""
    room = _FireAdapter()
    path = _shadow_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        room=room, shadow=None, shadow_channel=None,
        participation_runner=FakeAgentRunner([
            ScriptedTurn(final_text="nothing here needs me"),
        ]),
    )

    with caplog.at_level(logging.INFO, logger="nora.app.fire"):
        outcome = await path.speak(_participation())

    assert outcome == fire_module.SILENT
    assert room.sent == []
    shape = _determinations(caplog)
    assert len(shape) == 1
    assert (shape[0].occasion, shape[0].disposition) == (
        "participation", "declined",
    )


async def test_a_proactive_turn_with_no_runner_is_a_counted_wiring_fault(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A proactive row reaching a `FirePath` with no proactive runner is a
    wiring fault, never a turn quietly run on the tag runner (which could
    not stay silent)."""
    adapter = _FireAdapter()
    runner = FakeAgentRunner(_send_turns("must not run"))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=runner,
        event_registry={"proactive": fire_module.proactive_occasion},
    )

    outcome = await path.run([_proactive_row()])

    assert outcome == fire_module.FAILED_ERROR
    assert runner.invocations == 0
    assert adapter.sent == []
    assert metrics.get(
        "nora_silent_failures_total", reason="handler_error", audience="proactive",
    ) == 1


def test_the_proactive_doctrine_audience_carries_her_doctrine(doctrine_store) -> None:
    """The occasion-neutral files reach the proactive audience; an
    audience missing any of them would deliver with no persona and no
    constraints. The may-speak rule is `ProactiveHeader`'s."""
    text = doctrine_store.get(applies_to="proactive").text
    for name in ("identity", "voice", "conduct", "constraints"):
        assert f'name="{name}"' in text, name
    assert 'name="participation-conduct"' not in text


# --- the shared decision log ---------------------------------


def _decision_recorder(store=None, *, metrics=None, unit_of=None):
    """A real `DecisionLogRecorder` over the SHARED in-memory store, which
    `tests/contract/test_decision_log_store_contract.py` judges against
    Postgres. The clock is `T0`, the same instant `_fire_path` runs at."""
    return DecisionLogRecorder(
        store=store if store is not None else InMemoryDecisionLogStore(),
        metrics=metrics if metrics is not None else Metrics(),
        clock=lambda: T0,
        unit_of=unit_of,
    )


async def test_a_participation_turn_that_speaks_is_written_to_the_decision_log(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """**The writer.** The participation determination is recorded where the
    gate deciding whether to admit the next window in this room, and the
    proactive pass deciding what to raise in it, can read it; a log file and an
    operator-wired mirror channel are readable by neither.

    `reason` is the DISPOSITION, never her words: her reply stays in the audit
    rows and the DEBUG half of the determination log.

    Falsification: delete the `_log_decision` call in `FirePath._determined`.
    """
    store = InMemoryDecisionLogStore()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
        decisions=_decision_recorder(store),
    )

    await path.speak(_participation())

    rows = await store.recent(conversation_id="conv-1", since_ms=0)
    assert [(r.writer, r.verdict, r.reason) for r in rows] == [
        ("participation", "spoke", "spoke"),
    ]
    assert "咁啱嘅" not in str(rows)


async def test_a_participation_turn_that_declines_is_written_too(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Silence is the HEALTHY default and it is the row that matters
    most: «she looked at this room an hour ago and had nothing to add» is
    exactly what stops the next decider re-deriving the same judgement. A log
    that recorded only the speech would answer «nothing has happened here» for
    a room she has considered four times."""
    store = InMemoryDecisionLogStore()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([
            ScriptedTurn(tool_calls=[], final_text="nothing to add here"),
        ]),
        decisions=_decision_recorder(store),
    )

    assert await path.speak(_participation()) == fire_module.SILENT

    rows = await store.recent(conversation_id="conv-1", since_ms=0)
    assert [(r.writer, r.verdict, r.reason) for r in rows] == [
        ("participation", "silent", "declined"),
    ]
    # Her unrouted draft doubles as her reasoning and stays OUT of the row —
    # it is a whole reply, and the row rides into a cheap external gate call.
    assert "nothing to add here" not in str(rows)


async def test_a_reminder_occasion_writes_no_decision_row(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Only the two occasions that CAN decline the floor are decisions. A
    reminder firing was decided when the row was written; logging it here
    would dilute the block with rows no decider can act on.

    **What actually enforces it is that `_turn` calls `_determined` from the
    participation and proactive branches only** — the `writer is None` guard in
    `_log_decision` is a second belt for a future occasion routed through it,
    and a mutation of that guard alone leaves this green. Stated rather than
    dressed up: the mutation table names the occasion MAPPING instead, which is
    the line a wrong edit would actually touch.
    """
    store = InMemoryDecisionLogStore()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("reminder text")),
        decisions=_decision_recorder(store),
    )

    await path.speak(reminder_occasion([_reminder()]))

    assert await store.recent(conversation_id="conv-1", since_ms=0) == ()


async def test_a_decision_log_that_is_down_never_fails_a_fire_turn(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The rule the recorder exists for. A participation turn that SPOKE must
    still answer `sent` when the log is unreachable — review and record traffic
    must never be able to break the path it records, which is the same
    judgement `_mirror` one method down already makes."""
    class _Broken:
        async def append(self, *, row):
            raise RuntimeError("decision log down")

        async def recent(self, *, conversation_id, since_ms, limit=20):
            raise RuntimeError("decision log down")

    adapter = _FireAdapter()
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=adapter, runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(_send_turns("咁啱嘅")),
        decisions=_decision_recorder(_Broken(), metrics=metrics),
    )

    assert await path.speak(_participation()) == fire_module.SENT
    assert adapter.texts == ["咁啱嘅"]
    assert metrics.get(
        "nora_errors_total", stage="decision_log_write", platform="signal",
    ) == 1


async def test_a_participation_turn_sees_what_she_already_raised_in_this_room(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """**The read.** This is the turn deciding whether to speak into a room
    nobody addressed her in, so «she raised this two hours ago» is the fact
    that changes the answer.

    Rendered as `<loop_decisions>` with an age on every row: every question the
    block answers is a question about WHEN, and an undated block cannot ground
    that judgement.

    Falsification: drop `decisions=await self._read_decisions(occasion)` from
    `FirePath.build_context`, or the `_decisions_block` term from
    `nora.agent.prefill.build_prefill`.
    """
    store = InMemoryDecisionLogStore()
    await store.append(row=DecisionRow(
        id="r1", writer="proactive_decide", platform="discord",
        # The HOME room, on the other platform. A room-scoped read would answer
        # nothing here.
        conversation_id="home-1", unit="friends", note_id="n-1",
        verdict="remind", reason="",
        ts=int(T0.timestamp() * 1000) - 7_200_000,
    ))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([]),
        decisions=_decision_recorder(store),
        policy=_decision_policy(),
    )

    ctx = await path.build_context(_participation())
    prefill = build_prefill(
        ctx, display_name="Nora", context_window=40, now=T0,
    )
    rendered = "\n".join(str(m) for m in prefill)

    assert "<loop_decisions" in rendered
    # The room LABEL, the writer, the verdict, the note by ID, and an age. No
    # title, no model text, no conversation id.
    assert "(2h ago) Friends HQ proactive_decide remind — note n-1" in rendered
    block = rendered.split("<loop_decisions")[1].split("</loop_decisions>")[0]
    assert "home-1" not in block


async def test_a_proactive_act_turn_sees_the_raise_its_sibling_row_just_made(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """**A second proactive row in the same room reads the first, end to end.**

    The shape: a row about note A fires at 20:00 and takes the floor; 31
    minutes later a row about note B comes due in the same room. Without the
    block, the second turn's only trace of the first is an unlabelled line of her
    own speech in the transcript, and it raises again. The block is what says «that
    was you, unprompted, 31 minutes ago» — with an AGE, because every question
    it is read to answer is a question about when.

    Falsification: drop `"proactive"` from `_DECISION_WRITERS_BY_KIND`.
    """
    store = InMemoryDecisionLogStore()
    await store.append(row=DecisionRow(
        id="r1", writer="proactive_act", platform="signal",
        conversation_id="conv-1", unit="friends", note_id=None,
        verdict="spoke", reason="spoke",
        ts=int(T0.timestamp() * 1000) - 31 * 60_000,
    ))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]), participation_runner=FakeAgentRunner([]),
        decisions=_decision_recorder(store), policy=_decision_policy(),
    )

    ctx = await path.build_context(
        fire_module.proactive_occasion([_proactive_row()]),
    )
    rendered = "\n".join(str(m) for m in build_prefill(
        ctx, display_name="Nora", context_window=40, now=T0,
    ))

    # `spoke` once, not twice: `decision_line` suppresses a reason that merely
    # repeats the verdict. The AGE is the half this test is about.
    assert "(31m ago) Friends proactive_act spoke" in rendered
    block = rendered.split("<loop_decisions")[1].split("</loop_decisions>")[0]
    assert "conv-1" not in block


async def test_a_participation_turn_never_reads_back_its_own_rows(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A turn handed its own last verdict is a turn agreeing with itself, and
    on the ambient path that is the direction the silence bias already
    leans. The tag rows are excluded too: a tag is somebody asking, so what it
    answered is in the transcript underneath, where it already is.

    Falsification: drop the `row.writer in (...)` filter in
    `FirePath._read_decisions`.
    """
    store = InMemoryDecisionLogStore()
    ts = int(T0.timestamp() * 1000)
    for row_id, writer, verdict, reason in (
        ("a", "participation", "silent", "declined"),
        ("b", "tag", "replied", "send_reply"),
        ("c", "proactive_decide", "remind", ""),
        ("d", "proactive_act", "spoke", "spoke"),
    ):
        await store.append(row=DecisionRow(
            id=row_id, writer=writer, platform="signal",
            conversation_id="conv-1", unit="friends", note_id=None,
            verdict=verdict, reason=reason, ts=ts,
        ))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]), participation_runner=FakeAgentRunner([]),
        decisions=_decision_recorder(store), policy=_decision_policy(),
    )

    ctx = await path.build_context(_participation())

    assert [row.writer for _, rows in ctx.decisions for row in rows] == [
        "proactive_decide", "proactive_act",
    ]


async def test_only_the_floor_weighing_occasions_read_the_decision_log(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Participation and the proactive act turn read it; a reminder does not.

    A reminder or a gathering firing was decided when its row was written and
    is not weighing whether to take a floor, so the block would cost a read and
    change nothing. Asserted as the triple, so deleting
    `_DECISION_WRITERS_BY_KIND` cannot pass by making every side empty.

    Falsification: drop `"proactive"` from `_DECISION_WRITERS_BY_KIND` and the
    second assertion reddens; drop the whole mapping lookup and the third does.
    """
    # `proactive_act` is the ONE writer both readers carry, so a single row
    # serves all three assertions and neither side can pass by being empty for
    # a reason unrelated to the `kind` check.
    store = InMemoryDecisionLogStore()
    await store.append(row=DecisionRow(
        id="r1", writer="proactive_act", platform="signal",
        conversation_id="conv-1", unit="friends", note_id=None,
        verdict="spoke", reason="spoke", ts=int(T0.timestamp() * 1000),
    ))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]), participation_runner=FakeAgentRunner([]),
        decisions=_decision_recorder(store), policy=_decision_policy(),
    )

    assert (await path.build_context(_participation())).decisions != ()
    assert (await path.build_context(
        fire_module.proactive_occasion([_proactive_row()]),
    )).decisions != ()
    assert (await path.build_context(reminder_occasion([_reminder()]))).decisions == ()


async def test_the_proactive_act_turn_reads_its_siblings_and_not_its_own_decide_row(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The writer set a proactive act turn reads.

    Two proactive rows about two notes come due 30 minutes apart in one room;
    `heartbeat._by_turn` coalesces only rows due in the SAME tick, so they are
    two turns, and the second would raise what the first had already said. The
    second turn's window DOES carry the first message — as an unlabelled line of
    her own speech. `proactive_act` in this set is what labels it.

    `proactive_decide` is excluded for the rule's own reason: it is the
    deliberation that produced THIS turn, `_proactive_header` already puts it
    in front of her, and reading it back leans toward speaking. `tag` is
    excluded because what a tag answered is in the transcript underneath, and
    because it is the highest-volume writer — 20 rows of it would evict the one
    `proactive_act` row this set exists to carry.

    Falsification: add `proactive_decide` to `PROACTIVE_ACT_DECISION_WRITERS`,
    or drop `proactive_act` from it.
    """
    store = InMemoryDecisionLogStore()
    ts = int(T0.timestamp() * 1000)
    for row_id, writer, verdict, reason in (
        ("a", "participation", "silent", "declined"),
        ("b", "tag", "replied", "send_reply"),
        ("c", "proactive_decide", "remind", ""),
        ("d", "proactive_act", "spoke", "spoke"),
    ):
        await store.append(row=DecisionRow(
            id=row_id, writer=writer, platform="signal",
            conversation_id="conv-1", unit="friends", note_id=None,
            verdict=verdict, reason=reason, ts=ts,
        ))
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        runner=FakeAgentRunner([]), participation_runner=FakeAgentRunner([]),
        decisions=_decision_recorder(store), policy=_decision_policy(),
    )

    ctx = await path.build_context(
        fire_module.proactive_occasion([_proactive_row()]),
    )

    assert [row.writer for _, rows in ctx.decisions for row in rows] == [
        "participation", "proactive_act",
    ]


def _decision_policy() -> Policy:
    """A two-room unit: the Signal room the window closed in, and the
    Discord HOME room a proactive row fires into.

    A REAL `Policy`, not a stub, because the shape under test is cross-room:
    the decision was made in the other room on the other platform, and a
    room-scoped read would answer nothing. `permissive_policy` cannot stand in
    — it holds no conversations, so it resolves no unit and no label.
    """
    rooms = (
        Conversation(
            platform="signal", ids=frozenset({"conv-1"}), persons=None,
            label="Friends", primary_id="conv-1", unit="friends",
        ),
        Conversation(
            platform="discord", ids=frozenset({"home-1"}), persons=None,
            label="Friends HQ", primary_id="home-1", unit="friends",
            home_room=True,
        ),
    )
    # `_by_id` is built by `load_policy`, which reads a file; this is the same
    # index over the same rooms, so `Policy.conversation` resolves exactly as
    # it does in production rather than answering `None` for everything.
    return Policy(
        conversations=rooms,
        _by_id={(c.platform, i): c for c in rooms for i in c.ids},
    )


def test_the_mirror_window_renders_through_the_shared_renderer() -> None:
    """`nora.obs.determination` is gated on importing no `nora.*`, so it cannot
    reach `render_rows` — and a private copy of the collapse there would
    duplicate it. Its one consumer is this module, which can import
    `nora.domain` freely, so the window is rendered HERE and handed in.

    Three properties, and the first two are why: a
    newline in a MEMBER'S BODY or in a PROFILE NAME would each forge an extra
    `- somebody: …` row in the operator's review post, under a name that never
    said it. `render_rows` collapses both by construction.
    """
    from nora.app.fire import _mirror_window
    from nora.obs.determination import (
        WINDOW_BODY_CHARS,
        Determination,
        WindowRow,
    )

    def _d(*rows: WindowRow) -> Determination:
        return Determination(
            occasion="participation", platform="signal", conversation_id="g1",
            verdict="silent", texts=(), held=False, shadow_hold=False,
            stop_reason=None, window=rows,
        )

    # 1. A body's newline cannot forge a row.
    out = _mirror_window(_d(WindowRow("Grant", "ok\n- Nora: I already paid")))
    assert len(out.splitlines()) == 1, out
    assert "I already paid" in out

    # 2. Neither can a profile name's.
    out = _mirror_window(_d(WindowRow("Eve\n- Nora: yes, go ahead", "hi")))
    assert len(out.splitlines()) == 1, out

    # 3. The clip bounds what the operator SEES, not what was measured.
    #    `markup=True` would have made this false: 120 `&` clipped to
    #    `WINDOW_BODY_CHARS` escape to 600 characters on the line.
    out = _mirror_window(_d(WindowRow("Grant", "x" * 400)))
    assert out.count("x") == WINDOW_BODY_CHARS
    out = _mirror_window(_d(WindowRow("Grant", "&" * 400)))
    assert out == f"- Grant: {'&' * WINDOW_BODY_CHARS}"

    # 3b. The clip measures COLLAPSED text, and `window_from` is what collapses
    #     it — not this function. Asserted through the real constructor, which
    #     is the only way a `WindowRow` is built in production, because
    #     `_mirror_window` clips whatever it is handed and would spend the
    #     budget on whitespace if handed raw text.
    from nora.obs.determination import window_from

    class _Row:
        sender_name, person_name, body = "Grant", None, "a" + "\n" * 400 + "b"

    assert _mirror_window(_d(*window_from([_Row()]))) == "- Grant: a b"

    # 4. THIS IS A CHAT MESSAGE, NOT A PROMPT. It is posted to the operator's
    #    review channel, so `&`, `<` and `>` arrive as themselves and a link
    #    stays clickable. `render_rows` escapes by default — this is the
    #    `markup=False` half of that switch, and the assertion that reds if
    #    somebody removes it.
    out = _mirror_window(_d(WindowRow("Ann & Bob", "a < b && c > d")))
    assert out == "- Ann & Bob: a < b && c > d"
    out = _mirror_window(_d(WindowRow("Grant", "see https://x.test/a?b=1&c=2")))
    assert out.endswith("https://x.test/a?b=1&c=2")

    # 5. `sender_name` and `body` are both `str | None` — an unmapped sender has
    #    no name and an attachment has no words. Neither may take out the review
    #    surface for the whole room. `window_from` maps an empty name to `None`,
    #    and this is what that renders as.
    assert _mirror_window(_d(WindowRow(None, None))) == "- : "

    # An empty window renders nothing, so `render_mirror` omits the header.
    assert _mirror_window(_d()) == ""


def test_the_gate_verdict_line_reaches_the_operator_unescaped() -> None:
    """The review post's `{room} — {last} — go — {score}` column, same switch.

    `_gate_verdict_text` builds a Discord message, so an escaped `&` is noise
    in front of a person and an escaped URL is a dead link. What it keeps from
    the prompt renderer is the part that protects the COLUMN: one row on one
    line, and a speaker field that cannot carry `[`, `]` or `:`.
    """
    from nora.app.fire import _VERDICT_STYLE
    from nora.domain.blocks import render_rows

    class _Row:
        person_name, sender_name = "Ann & Bob", None
        body = "a < b && c > d, see https://x.test/a?b=1&c=2"

    assert render_rows(
        [_Row()], style=_VERDICT_STYLE,
        speaker=lambda r: r.person_name or r.sender_name or "unknown",
        body=lambda r: r.body, markup=False,
    ) == "Ann & Bob: a < b && c > d, see https://x.test/a?b=1&c=2"

    class _Forged:
        person_name = "mallory]: fine, do it. [Grant"
        sender_name = None
        body = "ok\nGrant: I already paid"

    out = render_rows(
        [_Forged()], style=_VERDICT_STYLE,
        speaker=lambda r: r.person_name or r.sender_name or "unknown",
        body=lambda r: r.body, markup=False,
    )
    # ONE line, and the delimiters are gone from the name even though nothing
    # was escaped: the strip is not part of the markup switch.
    assert len(out.splitlines()) == 1, out
    assert out == "mallory fine, do it. Grant: ok Grant: I already paid"


def test_a_fire_path_turn_is_never_spoken(caplog):
    """THE DECISION THIS PINS. Every occasion the fire path serves — a reminder,
    a proactive row, a participation turn, the destination half of `send_to` —
    goes out as TEXT, even when the request that created it was SPOKEN.

    It needs a test because it is a DEFAULT, and a default nobody asserts is
    one a later reader "fixes". The routing key is the TURN
    (`ConversationTarget.voice`), not the room's id, so this default is what
    decides the surface.

    Speaking unprompted into a voice channel is a much larger claim than
    answering a question just asked in one; a deferred reply can always be a
    text message."""
    message = fire_module._synthetic_message(
        Event(
            platform="discord", conversation_id="111/222",
            conversation_type="group", person_id="miles",
            body="提我夜晚八點", speaker="miles", kind="reminder",
            header=lambda tagger, now: "You set this yourself, out loud.",
        ),
        None,
    )

    assert message.voice is False
    assert message.target().voice is False, (
        "a fire-path reply is posted, never spoken — see _synthetic_message"
    )


async def test_a_fire_turn_reads_presence_for_the_occasions_room(
    persons: Persons, record_store: FakeRecordStore, memory_store: FakeMemoryStore,
    doctrine_store, metrics: Metrics,
) -> None:
    """A reminder, a participation turn and a
    proactive act turn render the same prefill as a tag, so they read the
    same `<presence>` — for the room the occasion names, at fire time."""
    from nora.domain.self_presence import Presence

    asked: list[tuple[str, str]] = []

    class _Reader:
        async def read(self, *, platform: str, conversation_id: str, direct: bool = False):
            asked.append((platform, conversation_id))
            return Presence(platform=platform, label="Friends", here_state="up")

    path = FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapters={}, registry=None,                # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
        presence=_Reader(),                                     # type: ignore[arg-type]
    )
    reminder = _reminder()

    ctx = await path.build_context(reminder_occasion([reminder]))

    assert asked == [(reminder.platform, reminder.conversation_id)]
    assert ctx.presence is not None and ctx.presence.label == "Friends"


async def test_the_fire_context_reads_run_concurrently_not_one_after_another(
    persons: Persons, record_store: FakeRecordStore, memory_store: FakeMemoryStore,
    doctrine_store, metrics: Metrics,
) -> None:
    """The standing read below cannot finish until the presence read has
    STARTED. Awaited in sequence (standing first), presence never starts, the
    standing read hits its own timeout and the turn gets no `<standing>`;
    gathered, both complete. So a green run is only possible concurrently."""
    import asyncio

    from nora.domain.self_presence import Presence
    from nora.domain.standing import Standing

    presence_started = asyncio.Event()

    class _Reader:
        async def read(self, *, platform: str, conversation_id: str, direct: bool = False):
            presence_started.set()
            return Presence(platform=platform)

    class _Standing:
        async def read(self, *, platform: str, conversation_id: str):
            await presence_started.wait()
            return Standing(platform=platform, conversation_id=conversation_id,
                            body="dinner is still open", fold_count=1, updated_at=1)

    path = FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapters={}, registry=None,                # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: T0,
        standing=_Standing(),                                   # type: ignore[arg-type]
        standing_store_timeout_seconds=0.5,
        presence=_Reader(),                                     # type: ignore[arg-type]
    )

    ctx = await path.build_context(reminder_occasion([_reminder()]))

    assert ctx.standing is not None and ctx.standing.body == "dinner is still open"
    assert ctx.presence is not None

async def test_a_deferred_participation_turn_gets_the_same_surface_as_a_tag(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The fire path takes its tools from the
    SAME `ToolRegistry.surface()` the tag path does, so `[llm] defer_tools`
    means the same thing on both — hot tools plus the loader, the catalogue
    in the prompt, one more turn for the load."""
    from nora.agent.tools import GRAPH_MEMORY_TOOLS, HOT_TOOLS, LOADER_TOOL

    calls: list = []
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        adapter=_FireAdapter(), runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner(
            [ScriptedTurn(final_text="")], on_call=calls.append,
        ),
        defer_tools=True,
    )
    await path.speak(_participation())

    call = calls[-1]
    # No index wired: the Honcho memory surface, so the graph's hot verb is
    # not built and not resident (DEC-316).
    assert {t.tool_name for t in call["tools"]} == (
        HOT_TOOLS - GRAPH_MEMORY_TOOLS) | {LOADER_TOOL}
    prompt = "\n".join(
        block.get("text", "") for m in call["pre_fill"] for block in m["content"]
    )
    assert "<tool_catalogue" in prompt
    assert call["limits"]["turns"] == LIMITS["turns"] + 1


async def test_a_fire_turn_publishes_the_heartbeat_deadline_not_the_loops(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A fire turn is bounded by `[heartbeat].timeout_seconds` (120 in
    production), while the vision describer is built from the `[llm]` loop
    (360). Without the heartbeat's own deadline published, one slow read takes
    the whole heartbeat budget and the reminder turn dies (DEC-197)."""
    from nora.domain.turn_deadline import seconds_left

    seen: list[float | None] = []

    class _Reads(FakeAgentRunner):
        async def invoke(self, **kwargs: object) -> str | None:
            seen.append(seconds_left())
            return await super().invoke(**kwargs)

    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        timeout_seconds=7.0, runner=_Reads(_send_turns("記得食藥")),
    )

    await path.run([_reminder()])

    assert seen and seen[0] is not None and 6.0 < seen[0] <= 7.0, seen


async def test_a_fire_turn_reads_its_units_kept_memos_and_counts_a_failed_read(
    persons, record_store, memory_store, doctrine_store, metrics,
) -> None:
    """`<memos>` on the fire path (DEC-306), `_read_notes` mirrored: the unit's
    kept memos, another unit's never, and a failed read counted."""
    from nora.testing.memo_store import InMemoryMemoStore

    memos = InMemoryMemoStore()
    for unit in ("nora", "elsewhere"):
        await memos.create(
            unit=unit, platform="signal", conversation_id="conv-1", title=f"{unit} wifi",
            body="## Kept\n- kept [1]", citations=("1",), mode="told",
            established_by="cognition:u", model=None, now_ms=1,
        )
    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics, memos=memos,
    )
    ctx = await fire.build_context(reminder_occasion([_reminder()]))
    assert [m.title for m in ctx.memos] == ["nora wifi"]

    class _Down:
        async def kept(self, *, unit: str):
            raise RuntimeError("db down")

    fire = await _fire_path_with_tiers(
        persons, record_store, memory_store, doctrine_store, metrics, memos=_Down(),
    )
    ctx = await fire.build_context(reminder_occasion([_reminder()]))
    assert ctx.memos == ()
    assert metrics.get("nora_errors_total", stage="fire_memos") == 1


@pytest.mark.parametrize("location", [True, False])
async def test_a_fire_turn_carries_its_rooms_location_trust_into_where_is(
    persons: Persons, doctrine_store, metrics: Metrics, location: bool,
) -> None:
    """DEC-74 on the fire path: the room's `location` flag reaches the tool
    surface, as it does on a tag. Left at the surface's `False` default,
    `where_is` would refuse a proactive turn in a `location = true` room whose
    roster holds sealed-sender accounts, where a tag in the same room answers."""
    seen: list[bool] = []

    class _Spy(ToolRegistry):
        def surface(self, **kw):  # type: ignore[override]
            seen.append(kw.get("location_trusted", False))
            return super().surface(**kw)

    room = Conversation(
        platform="signal", ids=frozenset({"conv-1"}), persons=None,
        label="Friends", primary_id="conv-1", unit="friends", location=location,
    )
    policy = Policy(conversations=(room,), _by_id={("signal", "conv-1"): room})
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        registry=_Spy(), policy=policy, runner=FakeAgentRunner(_send_turns("記得食藥")),
    )

    await path.run([_reminder()])

    assert seen == [location]


async def test_a_relayed_turn_is_told_it_is_a_relay_and_a_reminder_is_not(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """8765_44837 on the fire path: a relayed message's turn carries `relay_turn` into the
    surface, so `send_to` is left off it; a reminder's turn, which is no relay, does not."""
    from nora.app.fire import relay_occasion

    seen: list[bool] = []

    class _Spy(ToolRegistry):
        def surface(self, **kw):  # type: ignore[override]
            seen.append(kw.get("relay_turn", False))
            return super().surface(**kw)

    room = Conversation(
        platform="signal", ids=frozenset({"conv-1"}), persons=None,
        label="Friends", primary_id="conv-1", unit="friends",
    )
    policy = Policy(conversations=(room,), _by_id={("signal", "conv-1"): room})
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        registry=_Spy(), policy=policy, runner=FakeAgentRunner(_send_turns("好")),
    )

    await path.speak(relay_occasion(
        destination=room, requester=MILES, text="dinner at 8", from_platform="discord",
    ))
    await path.run([_reminder()])

    assert seen == [True, False]


async def test_a_shadow_held_participation_turn_stays_strict_in_a_location_room(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A held participation turn's text goes to the review channel, a room
    other than the one `where_is` checks, so the room's trust is not lent to
    it."""
    seen: list[bool] = []

    class _Spy(ToolRegistry):
        def surface(self, **kw):  # type: ignore[override]
            seen.append(kw.get("location_trusted", False))
            return super().surface(**kw)

    room = Conversation(
        platform="signal", ids=frozenset({"conv-1"}), persons=None,
        label="Friends", primary_id="conv-1", unit="friends", location=True,
    )
    policy = Policy(conversations=(room,), _by_id={("signal", "conv-1"): room})
    path = _fire_path(
        doctrine_store=doctrine_store, persons=persons, metrics=metrics,
        registry=_Spy(), policy=policy, adapter=_FireAdapter(),
        runner=FakeAgentRunner([]),
        participation_runner=FakeAgentRunner([ScriptedTurn(final_text="")]),
        shadow_channel="review-1",
    )

    await path.speak(_participation())

    assert seen == [False]
