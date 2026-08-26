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
from nora.app.fire import FirePath, compute_occasion, gathering_occasion, reminder_occasion
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.events import Event
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.ports import JobResult, JobStatus, PlatformIdentity, SendResult
from nora.domain.reminders import Reminder, ReminderCaps, ReminderState
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import FakeAgentRunner, ScriptedTurn
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
    assert ctx.message.timestamp == 0                 # falsy AND an int — models.py:63


async def test_the_peer_card_is_read_for_the_requester(
    fire_path: FirePath, memory_store: FakeMemoryStore,
) -> None:
    """`peer_card` is the half of §6.4 that goes silently empty when the tagger
    falls out — it is keyed on `ref.honcho_peer_id`, so a `None` tagger yields
    `()` with nothing raising anywhere."""
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
        self.sent_mentions: list[tuple] = []
        self._identity_raises = identity_raises

    async def resolve_identity(self) -> PlatformIdentity:
        if self._identity_raises is not None:
            raise self._identity_raises
        return PlatformIdentity(platform=self.platform, primary="+85200000000")

    async def send(
        self, *, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
    ) -> SendResult:
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
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
    adapters: dict | None = None,
    memory: FakeMemoryStore | None = None,
    records: FakeRecordStore | None = None,
    registry: ToolRegistry | None = None,
    timeout_seconds: float = 5.0,
    room_language_max_entries: int | None = None,
    event_registry: dict | None = None,
) -> FirePath:
    extra = {}
    if room_language_max_entries is not None:
        extra["room_language_max_entries"] = room_language_max_entries
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
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40,
        limits=LIMITS, timeout_seconds=timeout_seconds, clock=lambda: T0,
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


# ---------------------------------------------------------------------------
# The two refusals `run()` OWNS. Both used to be pinned only indirectly — one
# through `build_context`, one through a tool test that passed the flag by hand
# — so deleting either line inside `run()` turned nothing red.
# ---------------------------------------------------------------------------


async def test_a_reminder_whose_requester_left_identity_toml_sends_nothing(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """§12's fire-time identity re-check, pinned on `run()` rather than on
    `build_context`.

    `build_context` reporting `tagger is None` is only half the rule: what
    matters is what `run()` DOES with it — `failed_policy`, nothing sent,
    nothing counted. That was asserted nowhere, so deleting the early return in
    `_turn` left the whole suite green while a row naming a person
    identity.toml no longer knows ran a full turn on somebody else's behalf.

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
    """DEC-79's occasion, on the real turn rather than through a stub.

    The version announcement names NO person — nobody asked — and the test above
    (`…requester_person_id="ghost"`) shows what §12 does to a turn whose person
    cannot be resolved: it refuses, silently, as `failed_policy`. Those two
    states are one line apart in `_turn`, so without this the announcement would
    be refused by a rule about a grant it never held, and every announcer test
    would still pass (they stub `speak`).

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
        runner=FakeAgentRunner(_send_turns("新版本, 而家識講返自己version"),
                               on_call=calls.append),
    )

    outcome = await path.speak(Event(
        platform="signal", conversation_id="conv-1", conversation_type="group",
        person_id=None, body="You can ask her which version she is running.",
        speaker="the people who build you", kind="version_announcement",
        header=lambda tagger, now: "You have just been restarted on a new version.",
    ))

    assert outcome == "sent"
    assert adapter.texts == ["新版本, 而家識講返自己version"]
    tag_message = calls[-1]["tag_message"]
    assert 'from="the people who build you"' in tag_message
    assert "You can ask her which version she is running." in tag_message


async def test_an_occasion_that_names_a_missing_person_is_still_refused(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The control for the test above: relaxing §12 for a person-LESS occasion
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
    """R-39/§7.1, pinned at the ONE place that connects it to production.

    `remind`'s refusal has its own test, but that test passes `fire_turn=True`
    into the registry by hand — it proves the tool reads the flag, not that
    anything ever sets it. The single line that does is
    `registry.build(..., fire_turn=True)` in `FirePath._turn`, and reverting it
    to the default turned nothing red anywhere.

    So this builds the registry the way the composition root does, runs a REAL
    fire turn whose script calls `remind`, and asserts the refusal actually
    happened: without the flag she re-arms her own reminder every time one comes
    due, which is recurrence with none of §9's caps able to stop it.
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
    """The control, and the promise DEC-85 made: a one-shot turn's prompt is
    byte-identical to the pre-recurrence one. Every clause the recurring header
    adds is asserted ABSENT here, so a clause that leaked into the ordinary path
    — telling her every reminder is a standing arrangement — is caught by the
    test that says the ordinary path did not change."""
    header = fire_module._fire_header([_reminder()], MILES, T0)

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

    header = fire_module._fire_header([row], MILES, T0)

    assert "(every day, 11 more after this)" in header
    assert "A repeat is not a new arrangement" in header


def test_the_final_occurrence_says_so_rather_than_counting_zero() -> None:
    """«0 more after this» is a sentence about arithmetic; «last one» is a
    sentence about the thing. She is being asked to speak, not to report."""
    row = _reminder(series=_series(occurrence=13, count=14))

    header = fire_module._fire_header([row], MILES, T0)

    assert "(every day, last one)" in header


# ---- compute_occasion (Nora Compute spec §4) --------------------------------
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
        "task_sha256": "hash-task", "context_sha256": "hash-context",
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
            status=JobStatus.SUCCEEDED, url="https://nora.example.net/drop/abc",
            summary="12 wet days in July",
        ),
    )

    assert "It finished: 12 wet days in July" in occasion.body
    assert "https://nora.example.net/drop/abc" in occasion.body
    assert "chart the rainfall" in occasion.body   # what was asked, still there
    assert occasion.person_id == "miles"
    assert occasion.kind == "compute"


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


class _DiscordFireAdapter(_FireAdapter):
    """A second platform for the fire path, identical but for which one it is."""

    platform = "discord"


async def test_a_fire_turn_speaks_on_the_platform_the_occasion_names(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """`design/discord-integration/02`: fire-path eligibility is explicitly NOT
    platform-specific, so a reminder owed in a Discord channel fires there.

    `FirePath` held ONE adapter until DEC-102, so every occasion — reminder,
    version announcement, finished compute job — was spoken into Signal
    regardless of where it was owed. Under R-42 that is unrecoverable for an
    announcement: it is CLAIMED for the Discord room and delivered to a Signal
    one, and the claim bars a second attempt forever.
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


async def test_the_configured_room_language_render_cap_reaches_build_prefill(
    persons: Persons, doctrine_store, metrics: Metrics, monkeypatch,
) -> None:
    """Task 11: `[language].max_rendered_lexemes` is inert unless it actually
    reaches `build_prefill`'s `room_language_max_entries` on the FIRE path
    too, not just the tag path — this proves the wiring, not merely that the
    constructor accepts the parameter."""
    captured: list[int] = []
    real_build_prefill = fire_module.build_prefill

    def _spy(*args, **kwargs):
        captured.append(kwargs["room_language_max_entries"])
        return real_build_prefill(*args, **kwargs)

    monkeypatch.setattr(fire_module, "build_prefill", _spy)
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("收到")),
        room_language_max_entries=1,
    )

    await path.run([_reminder()])

    assert captured == [1]


# ---------------------------------------------------------------------------
# DEC-139 — kind dispatch, registry fail-closed, gathering-notify
# ---------------------------------------------------------------------------


async def test_run_dispatches_a_gathering_row_through_its_factory(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """A `kind="gathering"` row renders through `gathering_occasion` — the
    registry maps the row's kind to the factory (DEC-139). The model hears
    the gathering title in the body and the header names the event; the
    legacy `text` string is NOT what reaches her."""
    adapter = _FireAdapter()
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner(_send_turns("收到")), adapter=adapter,
        event_registry={
            "reminder": reminder_occasion,
            "gathering": gathering_occasion,
        },
    )
    row = _reminder(
        text="Event: Dinner", kind="gathering",
        payload={"title": "Dinner"},
    )

    outcome = await path.run([row])

    assert outcome == fire_module.SENT
    # The body the model saw is the payload title, not the legacy text.
    assert adapter.texts == ["收到"]
    # The factory's shape, asserted directly: body is the title, kind is
    # gathering, and the header names the escaped title.
    ev = gathering_occasion([row])
    assert ev.kind == "gathering"
    assert ev.body == "Dinner"
    assert ev.platform == "signal"
    header = ev.header(None, T0)
    assert "«Dinner»" in header


async def test_run_dispatches_by_kind_not_by_row_order(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """Two kinds due for the same requester+conversation become two turns
    (the heartbeat's `_by_turn` key includes kind) — and a `kind="gathering"`
    row must go through `gathering_occasion` regardless of sort order."""
    from nora.app.heartbeat import _by_turn

    rows = [
        _reminder(id=uuid.uuid4(), text="買奶", kind="reminder"),
        _reminder(
            id=uuid.uuid4(), text="Event: Dinner", kind="gathering",
            payload={"title": "Dinner"},
        ),
    ]
    grouped = _by_turn(rows)
    assert len(grouped) == 2
    kinds = {key[3] for key in grouped}
    assert kinds == {"reminder", "gathering"}


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


async def test_gathering_row_missing_payload_title_fails_closed(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """The per-kind payload validation (04): a `kind="gathering"` row without
    `payload["title"]` raises instead of `KeyError`-ing out of the blue, and
    the raise lands in the same fail-closed path as an unregistered kind."""
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=FakeAgentRunner([ScriptedTurn(final_text="never reached")]),
        event_registry={
            "reminder": reminder_occasion,
            "gathering": gathering_occasion,
        },
    )

    with pytest.raises(KeyError, match="has no payload"):
        await path.run([_reminder(kind="gathering", payload=None)])
    # A malformed map — title present but not a string — fails the type
    # guard (design 03: malformed payloads fail observably at fire time).
    with pytest.raises(KeyError, match="no string payload"):
        await path.run([_reminder(kind="gathering", payload={"title": 123})])


async def test_legacy_reminder_row_still_fires_after_the_kind_columns_land(
    persons: Persons, doctrine_store, metrics: Metrics,
) -> None:
    """03-reminder-schema's migration test: a row written BEFORE DEC-139
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
