"""`remind` / `list_reminders` / `cancel_reminder` — the model-facing surface.

Built through `ToolRegistry.specs(...)`, never by reaching into
`build_tool_specs` directly, because the wiring IS most of what is under test:
the store, the caps and the clock arrive as registry collaborators, and a test
that bypassed the registry would pass against a `remind` production never
reaches (`nora/app/composition.py` is the other half of that, pinned in
`tests/test_composition.py`).

WHAT EACH GROUP OF TESTS IS FOR
-------------------------------
* **The fire-turn refusal** is the one that is not a cap. `fire_turn` is
  threaded to `build_tool_specs` and read here: without the refusal Nora
  re-arms her own reminder every time one fires, and none of the caps stop
  it, because `max_pending_per_requester` counts PENDING rows and each fire
  frees one before the replacement is written.
* **The five caps** each have exactly one test, naming the ONE condition it
  exercises, so a cap that silently stops being checked has a test with its
  name on it.
* **Scope** — `list_reminders` and `cancel_reminder` are bound to this
  conversation by the closure (DEC-24) and re-checked inside the store, because
  a uuid carries no scope of its own.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta

from nora.agent import tools as tools_module
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.reminders import (
    Recurrence,
    Reminder,
    ReminderCaps,
    ReminderState,
    occurrence_at,
)
from nora.domain.tool_call_log import ToolCallLogEntry
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeMemoryStore,
    FakeToolCallLogStore,
)
from nora.testing.reminders import FakeReminderStore
from tests.fakes import SinkSender

CONVERSATION = "conv-1"
TARGET = ConversationTarget("signal", CONVERSATION, "group")
UNIT = "friends"

# A Wednesday, 19:00 HKT — the clock every test in this file runs against, so
# «two hours from now» is a literal rather than a computation.
T0 = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
SOON = (T0 + timedelta(hours=2)).isoformat()

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")
TYLER = PersonRef(person_id="tyler", name="Tyler Cortez", honcho_peer_id="tyler")


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    """The address book, with BOTH of its lookups.

    `resolve` (platform, sender_id) answers None because these tests inject the
    tagger into `Room` directly rather than through a message. `by_person` is the
    other lookup — by `person_id`, the one `list_reminders` uses to name whoever
    set a row, and the one `FirePath.build_context` uses at fire time. A fake
    carrying only `resolve` would make every owner unresolvable, which is the
    answer the degrade test wants and the WRONG one everywhere else.
    """

    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _room(*, tagger: PersonRef | None = MILES) -> Room:
    return Room(
        platform="signal", conversation_id=CONVERSATION, roster_source=None,
        persons=_FakePersons(MILES, TYLER), tagger=tagger,
    )


def _tools(
    *,
    reminders=None,
    caps: ReminderCaps | None = None,
    clock=lambda: T0,
    fire_turn: bool = False,
    tagger: PersonRef | None = MILES,
    metrics: Metrics | None = None,
    conversation_id: str = CONVERSATION,
    persons: _FakePersons | None = None,
    tool_call_log_store=None,
    presence_source=None,
    presence_circles=(),
    location_trusted: bool = False,
    notes=None,
    room: Room | None = None,
    events=None,
) -> dict:
    """Every native tool, closed over one turn — keyed by name.

    `reminders=None` is a real production state (no store wired), not a test
    shortcut: it is what `remind` must answer `unavailable` for.
    `tool_call_log_store` defaults the same honest way, for `recall_lookups`.
    `presence_source`/`presence_circles`/`location_trusted` default the same
    honest way, unwired, unless a test passes them explicitly.
    """
    registry = ToolRegistry(
        reminders=reminders, reminder_caps=caps, clock=clock,
        tool_call_log_store=tool_call_log_store,
        presence=presence_source, circles=presence_circles,
        notes=notes,
        note_unit=(lambda _p, _c: UNIT) if notes is not None else None,
        events=events,
    )
    specs = registry.specs(
        store=_FakeStore(), conversation_id=conversation_id, conversation_type="group",
        persons=persons if persons is not None else _FakePersons(MILES, TYLER),
        honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=room if room is not None else _room(tagger=tagger),
        fire_turn=fire_turn, location_trusted=location_trusted,
    )
    return {spec.name: spec.call for spec in specs}


def _row(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": CONVERSATION, "conversation_type": "group",
        "text": "洗衫", "created_at": T0, "due_at": T0 + timedelta(hours=2),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return Reminder(**base)


# ---- DEC-24: nothing in these schemas is Nora's to know ----------------------

def test_no_reminder_tool_asks_the_model_where_to_put_it():
    """A `target` parameter would be an unprompted-speech lever into a
    room the requester may not be in, and `guards.py`'s scope counter keys on
    the literal name `conversation_id`, so it would be invisible to
    `nora_tool_scope_violations_total` as well."""
    import inspect

    tools = _tools(reminders=FakeReminderStore())
    forbidden = {"conversation_id", "conversation_type", "target", "platform",
                 "person_id", "peer_id", "requester"}
    for name in ("remind", "list_reminders", "cancel_reminder"):
        params = set(inspect.signature(tools[name]).parameters)
        assert not params & forbidden, f"{name} makes the model supply {params}"
    # The exact allow-list, not merely «none of the forbidden»: a `remind` that
    # grew a parameter would otherwise only be caught if the new one happened to
    # be named in `forbidden`. DEC-85's three recurrence arguments are all about
    # WHEN and HOW OFTEN, never about WHERE — the destination is still the
    # closure's conversation and nothing else.
    assert set(inspect.signature(tools["remind"]).parameters) == {
        # `every_n` joins them: HOW OFTEN, still never WHERE.
        "when", "text", "repeat", "every_n", "times", "until",
        # `note` is WHAT it is about — a note TITLE, resolved to an id in the
        # closure — still never WHERE.
        "note",
    }
    assert set(inspect.signature(tools["list_reminders"]).parameters) == set()
    assert set(inspect.signature(tools["cancel_reminder"]).parameters) == {"reminder_id"}


# ---- the fire-turn refusal --------------------------------------------------

async def test_remind_refuses_on_a_fire_turn():
    """Without this she re-arms her own reminder each time one fires —
    recurrence through the back door, and none of the caps stop it because
    `max_pending_per_requester` counts PENDING rows and each fire frees one.

    The store is asserted UNTOUCHED, not merely the status: a refusal that
    still wrote the row would satisfy a status-only assertion and lose nothing
    but the truth."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, fire_turn=True, metrics=metrics)

    result = await tools["remind"](when=SOON, text="洗衫")

    assert result["status"] == "refused"
    assert result["note"]
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="fire_turn") == 1


async def test_a_tag_turn_is_not_a_fire_turn():
    """The other half of the flag: it defaults False, so the ordinary path is
    untouched. Without this, `remind` refusing unconditionally would pass every
    refusal test in this file."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    result = await tools["remind"](when=SOON, text="洗衫")

    assert result["status"] == "ok"
    assert await store.count_pending("miles") == 1


# ---- mapped identity required -----------------------------------------------

async def test_remind_refuses_an_unmapped_requester():
    """`requester_person_id` is the row's owner and the id the fire-time
    policy re-check runs against; there is nothing to write for a sender
    `identity.toml` does not know, and inventing one would put a reminder in a
    room under nobody's name."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, tagger=None, metrics=metrics)

    result = await tools["remind"](when=SOON, text="洗衫")

    assert result["status"] == "refused"
    assert result["note"]
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="unmapped") == 1


# ---- the three caps and the two clock refusals -------------------------------

async def test_remind_refuses_past_the_pending_cap():
    store = FakeReminderStore()
    caps = ReminderCaps(max_pending_per_requester=2)
    metrics = Metrics()
    for _ in range(2):
        await store.create(_row())
    tools = _tools(reminders=store, caps=caps, metrics=metrics)

    result = await tools["remind"](when=SOON, text="洗衫")

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 2
    assert metrics.get("nora_reminders_created_total", status="too_many") == 1


async def test_remind_refuses_a_time_in_the_past():
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](
        when=(T0 - timedelta(minutes=5)).isoformat(), text="洗衫",
    )

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="past") == 1


async def test_remind_refuses_inside_min_lead_seconds():
    """`min_lead_seconds` is 120 and `tick_seconds` is 60, so a row set
    inside the lead could be due before the tick that would have created it has
    finished — «I'll tell you in a second» answered by saying it now."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, caps=ReminderCaps(min_lead_seconds=120),
                   metrics=metrics)

    result = await tools["remind"](
        when=(T0 + timedelta(seconds=30)).isoformat(), text="洗衫",
    )

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="too_soon") == 1


async def test_remind_refuses_beyond_max_horizon_days():
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, caps=ReminderCaps(max_horizon_days=30),
                   metrics=metrics)

    result = await tools["remind"](
        when=(T0 + timedelta(days=31)).isoformat(), text="洗衫",
    )

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="too_far") == 1


async def test_remind_refuses_a_when_it_cannot_read():
    """The model writes `when`, so an unparseable one is a normal event, not an
    exception. It must not reach `Reminder(due_at=...)` — a `TypeError` inside a
    tool is a silent turn (R-5)."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](when="等陣", text="洗衫")

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="unparseable") == 1


async def test_remind_is_unavailable_rather_than_broken_without_a_store():
    """R-5's honest state for «not configured» — the shape keyless `route` and
    disabled `web_search` already ship in. `offline` and the eval compositions
    wire no store."""
    tools = _tools(reminders=None)

    result = await tools["remind"](when=SOON, text="洗衫")

    assert result["status"] == "unavailable"
    assert result["note"]


class _WriteFailingStore(FakeReminderStore):
    async def create(self, reminder: Reminder) -> None:
        raise ConnectionError("reminder store unreachable")


async def test_every_non_ok_status_carries_a_note():
    """ADR 0015: the description only NAMES `refused`/`unavailable`;
    what to fix or say is the result's `note`. Every non-ok code path this
    file's fakes can reach is driven here, so a path that loses its note goes
    red. (`ok` carries no reporting note: its `note` key is the linked note's
    title, pinned by `test_the_note_link_resolves_a_title_and_refuses_an_unknown_one`.)"""
    later = (T0 + timedelta(days=60)).isoformat()
    full = FakeReminderStore()
    await full.create(_row())
    cases = {
        "refused/fire_turn": (_tools(reminders=FakeReminderStore(), fire_turn=True), {}),
        "unavailable/no_store": (_tools(reminders=None), {}),
        "refused/unmapped": (_tools(reminders=FakeReminderStore(), tagger=None), {}),
        "refused/unparseable": (_tools(reminders=FakeReminderStore()), {"when": "等陣"}),
        "refused/past": (
            _tools(reminders=FakeReminderStore()),
            {"when": (T0 - timedelta(minutes=5)).isoformat()},
        ),
        "refused/too_soon": (
            _tools(reminders=FakeReminderStore()),
            {"when": (T0 + timedelta(seconds=30)).isoformat()},
        ),
        "refused/too_far": (
            _tools(reminders=FakeReminderStore(), caps=ReminderCaps(max_horizon_days=30)),
            {"when": later},
        ),
        "refused/bad_repeat": (
            _tools(reminders=FakeReminderStore()), {"repeat": "fortnightly"},
        ),
        "refused/bad_bound": (_tools(reminders=FakeReminderStore()), {"times": 3}),
        "refused/too_often": (
            _tools(reminders=FakeReminderStore()), {"repeat": "minutely", "every_n": 1},
        ),
        "refused/too_many": (
            _tools(reminders=full, caps=ReminderCaps(max_pending_per_requester=1)), {},
        ),
        "unavailable/write": (_tools(reminders=_WriteFailingStore()), {}),
        "unavailable/notes_off": (
            _tools(reminders=FakeReminderStore()), {"note": "AI Hackathon dinner plan"},
        ),
    }
    # `notes_off` is shared with the note tools, whose notes are held to the same
    # rule, so nothing is exempt.
    shared: set[str] = set()
    for case, (tools, kwargs) in cases.items():
        result = await tools["remind"](**{"when": SOON, "text": "洗衫", **kwargs})
        assert result["status"] == case.split("/")[0], (case, result)
        assert isinstance(result.get("note"), str) and result["note"].strip(), case
        if case not in shared:
            assert "—" not in result["note"] and "–" not in result["note"], case


# ---- what a written row actually holds ---------------------------------------

async def test_a_created_row_carries_the_requesters_own_sentence_and_this_room():
    """The row stores what the REQUESTER said, so at fire time it
    can be put to Nora as a request rather than replayed as a script. And the
    destination is the closure's conversation, never a value the model chose."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](when=SOON, text="記得洗衫")

    assert result["status"] == "ok"
    rows = await store.pending_for(CONVERSATION)
    assert len(rows) == 1
    row = rows[0]
    assert row.text == "記得洗衫"
    assert row.requester_person_id == "miles"
    assert row.conversation_id == CONVERSATION
    assert row.conversation_type == "group"
    assert row.platform == "signal"
    assert row.due_at == T0 + timedelta(hours=2)
    assert row.created_at == T0
    assert row.state is ReminderState.PENDING
    assert result["id"] == str(row.id)
    assert metrics.get("nora_reminders_created_total", status="created") == 1


async def test_a_when_without_an_offset_is_read_in_noras_own_zone():
    """The prefill states the time as «19:00, Hong Kong time (HKT,
    Asia/Hong_Kong)» prose, so a model asked for ISO-8601 will sometimes
    drop the offset. `_HKT` is the zone that header
    named, and reading it there is not a guess; refusing would spend a round
    trip on punctuation. A NAIVE value must never reach the store — the port
    raises `NaiveDatetimeError` on one, by design."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    result = await tools["remind"](when="2026-08-08T21:00:00", text="洗衫")

    assert result["status"] == "ok"
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.due_at == T0 + timedelta(hours=2)      # 21:00 HKT == 13:00 UTC


# ---- list and cancel are conversation-scoped ---------------------------------

async def test_list_reminders_is_scoped_to_this_conversation():
    store = FakeReminderStore()
    mine = _row(text="洗衫")
    theirs = _row(conversation_id="conv-2", text="別人的")
    await store.create(mine)
    await store.create(theirs)
    tools = _tools(reminders=store)

    result = await tools["list_reminders"]()

    assert result["status"] == "ok"
    assert [r["id"] for r in result["reminders"]] == [str(mine.id)]
    assert [r["text"] for r in result["reminders"]] == ["洗衫"]


async def test_list_reminders_names_who_set_each_one():
    """The ownership rule made legible. A reminder is group-owned, so this
    listing shows everybody's and anyone here may cancel any of them — which is
    exactly why the row has to say whose it is. Without `set_by`, 「取消 Tyler
    七點嗰個」 is unanswerable and one person can call off another's with neither
    of them able to see it happened.

    Two DIFFERENT requesters, not one: a `set_by` hard-wired to the tagger — the
    obvious wrong implementation, since the tagger is right there in the closure
    — passes a single-requester test and is wrong about every row it did not set.
    """
    store = FakeReminderStore()
    mine = _row(requester_person_id="miles", text="洗衫",
                due_at=T0 + timedelta(hours=1))
    tylers = _row(requester_person_id="tyler", text="交租",
                  due_at=T0 + timedelta(hours=2))
    await store.create(mine)
    await store.create(tylers)
    tools = _tools(reminders=store)

    result = await tools["list_reminders"]()

    assert result["status"] == "ok"
    assert [(r["text"], r["set_by"]) for r in result["reminders"]] == [
        ("洗衫", "Miles Novak"), ("交租", "Tyler Cortez"),
    ]


async def test_list_reminders_says_so_when_the_setter_is_no_longer_known():
    """identity.toml loads once at startup and a pending row may be thirty days
    old, so the person it names may have left the file — the same lookup the fire
    path does, where it means the row will answer `failed_policy` instead of
    firing.

    Three things are asserted, and each is a way this could go wrong: the row is
    STILL LISTED (dropping it hides a reminder that is genuinely waiting), the
    field is non-empty (a blank reads as «nobody set this», which is false), and
    it is not the raw `person_id` (operator vocabulary from identity.toml, which
    she would say out loud as if the group called him that).
    """
    store = FakeReminderStore()
    orphan = _row(requester_person_id="someone_who_left", text="洗衫")
    await store.create(orphan)
    # The address book still knows Miles — the row's person is the one missing,
    # not the whole book. A `by_person={}` fake would pass against a lookup that
    # was broken for everybody.
    tools = _tools(reminders=store, persons=_FakePersons(MILES))

    result = await tools["list_reminders"]()

    assert result["status"] == "ok"
    [row] = result["reminders"]
    assert row["id"] == str(orphan.id)
    assert row["set_by"]
    assert "someone_who_left" not in row["set_by"]
    assert row["set_by"] == tools_module._UNKNOWN_OWNER


async def test_cancel_refuses_an_id_from_another_conversation():
    """A uuid carries no scope of its own, so the STORE re-checks it —
    `cancel(id, conversation_id=...)`. A tool-side check would be one the next
    caller could forget."""
    store = FakeReminderStore()
    theirs = _row(conversation_id="conv-2")
    await store.create(theirs)
    tools = _tools(reminders=store)

    result = await tools["cancel_reminder"](reminder_id=str(theirs.id))

    assert result["status"] == "refused"
    assert result["note"]
    assert (await store.get(theirs.id)).state is ReminderState.PENDING


async def test_cancel_resolves_a_row_in_this_conversation():
    store = FakeReminderStore()
    mine = _row()
    await store.create(mine)
    tools = _tools(reminders=store)

    result = await tools["cancel_reminder"](reminder_id=str(mine.id))

    assert result["status"] == "ok"
    assert (await store.get(mine.id)).state is ReminderState.CANCELLED


async def test_cancel_refuses_something_that_is_not_a_uuid():
    """The model writes `reminder_id`. `UUID("洗衫")` raises `ValueError`, and an
    exception inside a tool is a silent turn (R-5)."""
    tools = _tools(reminders=FakeReminderStore())

    result = await tools["cancel_reminder"](reminder_id="洗衫")

    assert result["status"] == "refused"
    assert result["note"]


async def test_cancel_says_so_when_the_row_already_fired():
    """A cancel against a resolved row is a no-op, and the tool says so
    rather than reporting success — «I stopped it» about a message already sent
    is the one answer that cannot be repaired."""
    store = FakeReminderStore()
    done = _row(state=ReminderState.DONE)
    await store.create(done)
    tools = _tools(reminders=store)

    result = await tools["cancel_reminder"](reminder_id=str(done.id))

    assert result["status"] == "refused"
    assert (await store.get(done.id)).state is ReminderState.DONE


class _ReadAndCancelFailingStore(FakeReminderStore):
    async def pending_for(self, conversation_id: str):
        raise ConnectionError("reminder store unreachable")

    async def cancel(self, reminder_id, *, conversation_id: str) -> bool:
        raise ConnectionError("reminder store unreachable")


async def test_every_non_ok_list_and_cancel_status_carries_a_note():
    """ADR 0015: `list_reminders` and `cancel_reminder` only NAME their
    statuses; how to report each is the result's `note`. Every non-ok code
    path is driven, so one that loses its note goes red."""
    done = FakeReminderStore()
    fired = _row(state=ReminderState.DONE)
    await done.create(fired)
    cases = {
        "list/unavailable/no_store": _tools(reminders=None)["list_reminders"](),
        "list/unavailable/read": _tools(
            reminders=_ReadAndCancelFailingStore())["list_reminders"](),
        "cancel/unavailable/no_store": _tools(reminders=None)["cancel_reminder"](
            reminder_id=str(uuid.uuid4())),
        "cancel/refused/not_an_id": _tools(
            reminders=FakeReminderStore())["cancel_reminder"](reminder_id="洗衫"),
        "cancel/refused/no_pending_row": _tools(reminders=done)["cancel_reminder"](
            reminder_id=str(fired.id)),
        "cancel/unavailable/write": _tools(
            reminders=_ReadAndCancelFailingStore())["cancel_reminder"](
            reminder_id=str(uuid.uuid4())),
    }
    for case, call in cases.items():
        result = await call
        assert result["status"] == case.split("/")[1], (case, result)
        assert isinstance(result.get("note"), str) and result["note"].strip(), case
        assert "\u2014" not in result["note"] and "\u2013" not in result["note"], case


async def test_a_list_reminders_ok_note_carries_only_the_rules_its_rows_need():
    """The reporting rules arrive with the rows they are about: empty is an
    answer; the rows are the room's; a repeat is a series; an unnamed setter is
    still real. Each clause appears only when its field does."""
    empty = await _tools(reminders=FakeReminderStore())["list_reminders"]()
    assert "real answer" in empty["note"]

    plain_store = FakeReminderStore()
    await plain_store.create(_row(requester_person_id="tyler"))
    plain = await _tools(reminders=plain_store)["list_reminders"]()
    assert "everyone's" in plain["note"]
    for clause in ("real answer", "series", tools_module._UNKNOWN_OWNER):
        assert clause not in plain["note"], clause

    series_store = FakeReminderStore()
    tools = _tools(reminders=series_store)
    await tools["remind"](when=SOON, text="食藥", repeat="daily", times=3)
    series = await tools["list_reminders"]()
    assert "series" in series["note"]
    assert tools_module._UNKNOWN_OWNER not in series["note"]

    orphan_store = FakeReminderStore()
    await orphan_store.create(_row(requester_person_id="someone_who_left"))
    orphan = await _tools(reminders=orphan_store)["list_reminders"]()
    assert tools_module._UNKNOWN_OWNER in orphan["note"]
    assert "series" not in orphan["note"]
    assert "\u2014" not in tools_module._SELF_OWNER


async def test_a_cancel_ok_says_how_to_report_it():
    store = FakeReminderStore()
    mine = _row()
    await store.create(mine)
    result = await _tools(reminders=store)["cancel_reminder"](reminder_id=str(mine.id))
    assert result["status"] == "ok"
    assert "whole series" in result["note"] and "whose" in result["note"]


# ---- the tool table (DEC-10, DEC-32) -----------------------------------------

def test_none_of_the_three_is_a_terminal_verb():
    """The reminder tools add no terminal verb. One would break
    `_terminal_budget_spent`'s AND — a turn would end when the model had merely
    set a reminder and said nothing.

    This asserts only ITS OWN claim: that none of the three reminder tools is
    terminal. The set of terminal tools is owned by `tests/test_guards_terminal.py`
    and `tests/unit/test_tool_registry.py`; a third copy of that pin here would
    go red when they legitimately change while asserting nothing about reminders.
    """
    registry = ToolRegistry()
    for name in ("remind", "list_reminders", "cancel_reminder"):
        assert name not in registry.terminal_tools()
        assert name in registry.budgets()


def test_the_three_tools_are_described_to_the_model():
    """Each reminder tool's SCHEMA docstring must state WHEN to reach for it —
    the schema is the single source of truth (DEC-150). The triggers must be
    distinct per tool, not generic boilerplate."""
    tool_calls = _tools()
    docs = {name: (tool_calls[name].__doc__ or "") for name in
            ("remind", "list_reminders", "cancel_reminder")}
    # The routing trigger is the "Call this when…" / "Call off…" sentence.
    assert "asks you to remember a thing" in docs["remind"]
    assert "asks what's set" in docs["list_reminders"]
    assert "by the id" in docs["cancel_reminder"]


# ---- the counter's own documentation ----------------------------------------

def test_every_status_remind_can_emit_is_named_in_the_metric_help():
    """`nora_reminders_created_total`'s `# HELP` line is the ONLY description of
    what its `status` axis can hold — the series is unlabelled-by-default in
    every dashboard until someone reads that line.

    Every status `_record_created` can emit must be named there, including
    `unavailable` (no store is wired at all) and `fire_turn` (R-39); an operator
    seeing an undocumented one could not tell a genuine status from a typo. Read
    out of the module SOURCE rather than from a hand-kept list here, so adding a
    refusal without documenting it is what turns this red.

    TWO patterns are read, not one (DEC-85). The recurrence refusals do not
    reach `_record_created` as literals — they travel as `_RepeatRefusal`
    values and are recorded through `rule.status` — so a regex that only knew
    the call site would find neither `bad_repeat` nor `bad_bound`. Both
    constructors are matched, and both must name their status in the help text.
    """
    import re
    from pathlib import Path

    from nora.obs.metric_specs import _COUNTERS

    source = Path(tools_module.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'_record_created\(\s*metrics,\s*"([a-z_]+)"', source))
    emitted |= set(re.findall(r'_RepeatRefusal\(\s*\n?\s*"([a-z_]+)"', source))
    assert len(emitted) >= 11, f"expected every status to be a literal; found {emitted}"
    assert {"bad_repeat", "bad_bound"} <= emitted, emitted

    spec = next(c for c in _COUNTERS if c.name == "nora_reminders_created_total")
    undocumented = {s for s in emitted if s not in spec.help_text}
    assert not undocumented, (
        f"nora_reminders_created_total emits {sorted(undocumented)} and its help "
        f"text does not name them: {spec.help_text!r}"
    )


# ---- DEC-85: recurrence, and the three asks that produced it ------------------
#
# Each of the three is verbatim from the group. They are tests rather than an
# anecdote in a design document because what they pin is the SHAPE the model
# has to produce: a recurrence is `when` + `repeat` + a bound, and lead time
# (「前兩個星期」) is arithmetic on `when` rather than a concept anything here
# stores.

# `max_horizon_days` bounds the FIRST occurrence. Two of the three asks name a
# date months out, so they are exercised against a cap widened to a year; the
# 30-day cap the refusal tests use would refuse them.
YEAR = ReminderCaps(max_horizon_days=365)


async def test_the_birthday_ask_becomes_a_yearly_series():
    """「記住我嘅生日係2月24日嗰時候同我講生日快樂」 — an ANNUAL recurrence.

    The first occurrence is the next 24 February; `repeat="yearly"` is what
    makes the one after that exist at all. The bound is the cap, because she was
    not given one — and the answer says what it is, so she can say it back.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=YEAR)

    result = await tools["remind"](
        when="2027-02-24T09:00:00+08:00", text="同我講生日快樂", repeat="yearly",
    )

    assert result["status"] == "ok"
    assert result["repeat"] == "yearly"
    assert result["repeat_left"] == YEAR.max_occurrences_per_series - 1
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.series is not None
    assert row.series.rule.every is Recurrence.YEARLY
    assert row.series.occurrence == 0
    assert row.series.anchor_at == row.due_at


async def test_the_anniversary_ask_becomes_a_bounded_daily_countdown():
    """「幫我記住我同Clara嘅結婚日期係2026年10月11日接近日期前兩個星期幫我每天
    都提一提大家」 — DAILY, bounded to the fortnight before a target date.

    LEAD TIME IS ARITHMETIC, and this is the test that says so: nothing in the
    schema or the tool knows what a wedding date is. The model resolves 「前兩個
    星期」 into a first occurrence (27 September) and a length (14), exactly as it
    already resolves 「等陣」 into an instant, and the series ends on the day.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=YEAR)

    result = await tools["remind"](
        when="2026-09-27T09:00:00+08:00",
        text="同Clara嘅結婚週年10月11號", repeat="daily", times=14,
    )

    assert result["status"] == "ok"
    assert result["repeat"] == "daily"
    assert result["repeat_left"] == 13
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.series is not None
    assert row.series.rule.count == 14
    # ONE pending row for a fourteen-day countdown: the successor is minted when
    # this one is claimed, which is what keeps a series costing 1 against
    # `max_pending_per_requester` instead of 14.
    assert await store.count_pending("miles") == 1


async def test_the_week_before_ask_stays_a_one_off():
    """「幫我前一個禮拜講一次啦」 — a single reminder at an offset from a date.

    The ask that is NOT a recurrence, and the reason `repeat` defaults to
    `none`: 「一次」 is one row, and a model that reached for a recurrence here
    would have committed her to sixty of them. The row is a plain one-off: no
    series, and no `repeat` key in the answer at all.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=YEAR)

    result = await tools["remind"](
        when="2026-10-04T09:00:00+08:00", text="下個禮拜結婚週年",
    )

    assert result["status"] == "ok"
    # No series keys: one row, not a recurrence. The reporting `note` (ADR 0015)
    # is the one key every ok carries.
    assert set(result) == {"status", "id", "due_at", "note"}
    assert "limit" not in result["note"]
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.series is None


# ---- the bounds, and what refuses ---------------------------------------------

async def test_a_repeat_with_no_bound_is_clamped_to_the_cap_and_says_so():
    """Every series is bounded, and the person is TOLD the bound.

    The cap is the answer to «and then what?» — a series that would run forever
    is not representable (`RecurrenceRule.count` is required), so the only open
    question is what she says when nobody named a limit. `repeat_left` is in the
    result because a limit nobody was told about is the part a person is most
    surprised by later.
    """
    store = FakeReminderStore()
    caps = ReminderCaps(max_occurrences_per_series=5)
    tools = _tools(reminders=store, caps=caps)

    result = await tools["remind"](when=SOON, text="食藥", repeat="daily")

    assert result["status"] == "ok"
    assert result["repeat_left"] == 4
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.series is not None
    assert row.series.rule.count == 5


async def test_a_repeat_asking_for_more_than_the_cap_is_clamped_not_refused():
    """Clamped, because refusing 「每日提我」 over an internal number is a worse
    answer than doing it for as long as I can and saying so."""
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=ReminderCaps(max_occurrences_per_series=5))

    result = await tools["remind"](
        when=SOON, text="食藥", repeat="daily", times=500,
    )

    assert result["status"] == "ok"
    assert result["repeat_left"] == 4


async def test_an_until_given_as_a_bare_date_covers_that_whole_day():
    """「提到10月11號為止」 names a DAY, not midnight at the start of it.

    Read as 00:00, the bound would exclude every occurrence on the last day the
    person actually named — so a fortnight's countdown would stop the day before
    the wedding, which is the one day it existed for. The off-by-one is invisible
    in the answer, so it is asserted on the stored bound.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=YEAR)

    result = await tools["remind"](
        when="2026-09-27T09:00:00+08:00", text="結婚週年", repeat="daily",
        until="2026-10-11",
    )

    assert result["status"] == "ok"
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.series is not None
    ends = row.series.rule.until
    assert ends is not None
    local = ends.astimezone(tools_module._HKT)
    assert local.date().isoformat() == "2026-10-11"
    assert (local.hour, local.minute) == (23, 59)


async def test_remind_refuses_a_repeat_it_does_not_have():
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](when=SOON, text="食藥", repeat="fortnightly")

    assert result["status"] == "refused"
    assert result["note"]
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="bad_repeat") == 1


async def test_remind_refuses_a_bound_with_nothing_to_repeat():
    """`times=14` with no `repeat` is a model that meant to ask for a
    recurrence and did not. Writing one reminder and reporting success is the
    reading that cannot be recovered from — she then tells them it is set."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](when=SOON, text="食藥", times=14)

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="bad_bound") == 1


async def test_remind_refuses_an_until_that_precedes_the_first_occurrence():
    """A window that ends before it opens is two dates, one of which is wrong —
    and silently writing a series of one would hide which."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, metrics=metrics)

    result = await tools["remind"](
        when=SOON, text="食藥", repeat="daily",
        until=(T0 - timedelta(days=1)).isoformat(),
    )

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="bad_bound") == 1


async def test_min_lead_seconds_still_holds_for_the_first_occurrence():
    """The lead is about the FIRST occurrence, and recurrence does not buy an
    exemption from it: «I'll tell you in a second, and every day after that» is
    still «I'd be saying it now». Later occurrences are a day out at the
    closest, so the cap has nothing to say about them."""
    store = FakeReminderStore()
    metrics = Metrics()
    tools = _tools(reminders=store, caps=ReminderCaps(min_lead_seconds=120),
                   metrics=metrics)

    result = await tools["remind"](
        when=(T0 + timedelta(seconds=30)).isoformat(), text="食藥", repeat="daily",
    )

    assert result["status"] == "refused"
    assert await store.count_pending("miles") == 0
    assert metrics.get("nora_reminders_created_total", status="too_soon") == 1


async def test_a_yearly_recurrence_gets_one_period_of_horizon_headroom():
    """A yearly reminder's first occurrence is up to a year out BY DEFINITION,
    so measuring it against a 30-day horizon does not bound the recurrence — it
    forbids it, and would refuse 「記住我嘅生日」. One period of headroom, and no
    more: the horizon still bounds a DAILY series to 30 days.
    """
    store = FakeReminderStore()
    caps = ReminderCaps(max_horizon_days=30)
    tools = _tools(reminders=store, caps=caps)

    yearly = await tools["remind"](
        when=(T0 + timedelta(days=200)).isoformat(), text="生日快樂",
        repeat="yearly",
    )
    daily = await tools["remind"](
        when=(T0 + timedelta(days=200)).isoformat(), text="食藥", repeat="daily",
    )

    assert yearly["status"] == "ok"
    assert daily["status"] == "refused"
    assert await store.count_pending("miles") == 1


# ---- what the listing shows ---------------------------------------------------

async def test_list_reminders_shows_a_repeat_as_a_repeat():
    """One row for a fourteen-day countdown, and `repeat` is what stops her
    reading it as a single reminder. `repeat_left` and `repeat_until` are the
    two facts she needs to answer 「提到幾時?」 without guessing."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)
    await tools["remind"](
        when=SOON, text="食藥", repeat="daily", times=14,
        until=(T0 + timedelta(days=13)).isoformat(),
    )

    result = await tools["list_reminders"]()

    assert result["status"] == "ok"
    [row] = result["reminders"]
    assert row["repeat"] == "daily"
    # `repeat_left` is the tighter of the `times` bound and the `until` bound.
    #
    # `times=14` with `until = T0 + 13 days` asks for fourteen but permits
    # thirteen: the first fires at T0+2h and the fourteenth would land at
    # T0+13d+2h, two hours past the bound, so `plan_successor` returns None and
    # it never happens. `repeat_left` is a number she is instructed to SAY, so it
    # must describe firings the series can actually produce.
    assert row["repeat_left"] == 12
    assert row["repeat_until"] is not None


async def test_list_reminders_says_none_for_a_one_shot():
    """The control: `repeat` is present on every row, so «no repeat key» is
    never how she has to work out that something is a one-off."""
    store = FakeReminderStore()
    await store.create(_row())
    tools = _tools(reminders=store)

    result = await tools["list_reminders"]()

    [row] = result["reminders"]
    assert row["repeat"] == "none"
    assert "repeat_left" not in row


async def test_cancelling_a_repeating_reminder_ends_the_whole_series():
    """The cancel story, from the tool: the id in the listing is the series'
    one pending occurrence, and nothing forges the next link but a claim. So
    there is no second verb, and nothing left to cancel afterwards."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)
    created = await tools["remind"](
        when=SOON, text="食藥", repeat="daily", times=14,
    )

    result = await tools["cancel_reminder"](reminder_id=created["id"])

    assert result["status"] == "ok"
    assert await store.pending_for(CONVERSATION) == []
    assert await store.claim(
        uuid.UUID(created["id"]), now=T0 + timedelta(hours=3),
    ) is False
    assert await store.pending_for(CONVERSATION) == []


# ---- `recall_lookups` --------------------------------------------------------

def _log_entry(**kw) -> ToolCallLogEntry:
    base = {
        "id": uuid.uuid4(), "conversation_id": CONVERSATION, "conversation_type": "group",
        "tag_id": "tag-0", "tool_name": "place_info", "tool_input": {"name": "Yoho Mall"},
        "tool_result": {"status": "ok", "rating": 4.2}, "created_at": datetime.now(UTC),
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return ToolCallLogEntry(**base)


async def test_recall_lookups_returns_recent_entries():
    store = FakeToolCallLogStore()
    await store.append(_log_entry())
    tools = _tools(tool_call_log_store=store)

    result = await tools["recall_lookups"]()

    assert result["status"] == "ok"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["tool_name"] == "place_info"
    assert result["entries"][0]["tool_result"] == {"status": "ok", "rating": 4.2}
    assert "age_seconds" in result["entries"][0]


async def test_recall_lookups_is_nothing_found_when_empty():
    tools = _tools(tool_call_log_store=FakeToolCallLogStore())
    result = await tools["recall_lookups"]()
    assert result["status"] == "nothing_found"


async def test_recall_lookups_unavailable_when_not_configured():
    """R-5: no store wired is an honest wiring gap, not a crash and not a
    guess."""
    tools = _tools()  # no tool_call_log_store
    result = await tools["recall_lookups"]()
    assert result["status"] == "unavailable"


async def test_recall_lookups_respects_a_smaller_limit():
    store = FakeToolCallLogStore()
    for _ in range(5):
        await store.append(_log_entry(tool_name="weather", tool_input={}))
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"](limit=2)
    assert len(result["entries"]) == 2


async def test_recall_lookups_is_unavailable_when_the_store_read_fails():
    """R-27: a store read failure is `unavailable`, never an uncaught
    exception (which Strands would wrap into an error `ToolResult` outside
    the ok/nothing_found/unavailable contract) and never `nothing_found`
    (an empty result and a failed read are different answers)."""
    class _BrokenStore:
        async def recent(self, **kwargs):
            raise ConnectionError("db unreachable")

    tools = _tools(tool_call_log_store=_BrokenStore())
    result = await tools["recall_lookups"]()
    assert result["status"] == "unavailable"
    assert "note" in result


async def test_recall_lookups_clamps_a_limit_above_the_max():
    store = FakeToolCallLogStore()
    for _ in range(40):
        await store.append(_log_entry(tool_name="weather", tool_input={}))
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"](limit=999)
    assert len(result["entries"]) == 30  # RECALL_LOOKUPS_MAX


async def test_recall_lookups_has_no_truncated_field_for_a_small_response():
    store = FakeToolCallLogStore()
    await store.append(_log_entry())
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"]()
    assert "truncated" not in result


async def test_recall_lookups_truncates_once_the_char_budget_is_spent():
    """`RECALL_LOOKUPS_MAX` (30 rows) caps count, not size — a handful of
    large `web_search`-shaped results can still blow well past a sane byte
    budget. Rows come back newest-first, so the clamp must drop the OLDEST
    entries and keep the newest, and it must say so via `truncated`."""
    store = FakeToolCallLogStore()
    big_result = {"status": "ok", "results": "x" * 3000}
    base = datetime.now(UTC)
    for i in range(5):
        await store.append(_log_entry(
            tool_name="web_search", tool_input={"query": f"q{i}"},
            tool_result={**big_result, "marker": i},
            created_at=base + timedelta(seconds=i),
        ))
    tools = _tools(tool_call_log_store=store)

    result = await tools["recall_lookups"]()

    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert len(result["entries"]) < 5
    # Newest-first, and the clamp keeps the newest — so the highest markers
    # (the ones appended last) are the ones still present.
    kept_markers = [e["tool_result"]["marker"] for e in result["entries"]]
    assert kept_markers == sorted(kept_markers, reverse=True)
    assert kept_markers[0] == 4


# ---- sub-day recurrence, the floor, and the number she says -----------------


async def test_the_f1_ask_is_one_call_not_twelve():
    """THE case. 「每 10 分鐘報一次, 直到 23:00」 is a sub-day series bounded
    by `until`. Twelve separate reminders would eat twelve of the twenty a
    requester may hold, since a SERIES costs one and a one-off costs one each.

    One call, and `repeat_left` is the number that will actually happen.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    out = await tools["remind"](
        when=SOON, text="F1 lap update", repeat="minutely", every_n=10,
        until=(datetime.fromisoformat(SOON) + timedelta(hours=1)).isoformat(),
    )

    assert out["status"] == "ok"
    assert out["repeat"] == "minutely"
    assert out["every_n"] == 10
    # 13:00 -> 14:00 inclusive at ten-minute steps is seven firings, so six
    # follow the first. Asserted as a NUMBER rather than "> 0" because the number
    # is what she says aloud.
    assert out["repeat_left"] == 6

    # THE STORED RULE, and the firings it will actually produce.
    #
    # Asserting the RESULT DICT alone is not enough: `_occurrences_through`
    # receives `step` separately, so `repeat_left` comes out as 6 even when the
    # rule that will fire has lost its multiplier entirely (a series that finishes
    # in six MINUTES instead of an hour, with every assertion above satisfied).
    #
    # So the times are read off the persisted row. `repeat_left` is what she
    # SAYS; this is what will HAPPEN, and the two must be the same thing.
    [row] = await store.pending_for(CONVERSATION)
    assert row.series is not None
    assert row.series.rule.interval == 10
    fires = [
        occurrence_at(anchor=row.series.anchor_at, rule=row.series.rule, index=i)
        for i in range(row.series.rule.count)
    ]
    gaps = {int((b - a).total_seconds()) for a, b in itertools.pairwise(fires)}
    assert gaps == {600}, "a ten-minute series must fire ten minutes apart"
    assert len(fires) == 7


async def test_an_every_minute_ask_is_refused_with_something_she_can_say():
    """The operator's floor (「if it is by the minute, the minimum recurring
    item is every five minutes」), and it refuses at the CONTRACT rather than
    clamping inside it.

    Clamping would be the F1 failure in a new costume: a series silently
    shorter than the one she just promised. A refusal she can voice —
    「最快五分鐘一次」 — lets her offer the thing the machine can actually do.
    The store is asserted untouched, because a refusal that still wrote a row
    would pass a status-only check.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    out = await tools["remind"](
        when=SOON, text="起身", repeat="minutely", times=30,
    )

    assert out["status"] == "refused"
    assert "5 minutes" in out["note"]
    assert store._rows == {}


async def test_an_hourly_repeat_is_above_the_floor_and_allowed():
    """The positive control for the refusal above. Same code path, one unit
    coarser: without this, `too_often` firing on everything would look
    identical to the floor working."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    out = await tools["remind"](
        when=SOON, text="食藥", repeat="hourly", times=6,
    )

    assert out["status"] == "ok"
    assert out["repeat"] == "hourly"
    assert len(store._rows) == 1


async def test_the_count_she_says_never_outlives_the_end_date():
    """`repeat_left` is documented as "the bound she has to SAY", so a series
    the end date cuts short must not announce the full number. 「每日提到聽日為止」
    with `times=14` can deliver only two, and must say two."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    out = await tools["remind"](
        when=SOON, text="食藥", repeat="daily", times=14,
        until=(datetime.fromisoformat(SOON) + timedelta(days=2)).isoformat(),
    )

    assert out["status"] == "ok"
    assert out["repeat_left"] == 2      # three firings fit, not fourteen


async def test_a_bare_date_until_counts_the_whole_last_day():
    """The derived count against the OTHER `until` form, and the branch where
    an off-by-one would actually live.

    `_parse_until` reads a bare date as the END of that day — 「提到10月11號為止」
    names a day, not midnight at the start of it — so a series bounded by one
    gets the firings ON that day too. Every other derived-count test passes an
    `until` WITH a time, which takes the other branch entirely.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    out = await tools["remind"](
        when=SOON, text="食藥", repeat="daily",
        until=(datetime.fromisoformat(SOON) + timedelta(days=2)).date().isoformat(),
    )

    assert out["status"] == "ok"
    # 8th (the first), 9th, 10th — the 10th counts, which is the whole point of
    # end-of-day. Read as 00:00 this would be 2, and the reminder nearest the
    # thing it is about would be the one that went missing.
    assert out["repeat_left"] == 2


async def test_a_nonsense_every_n_is_refused_rather_than_silently_becoming_one():
    """`every_n` is written by the MODEL, so a bad one is an ordinary event.
    A silent fallback to 1 would be the worst possible default — 「每 10 分鐘」
    would become every minute, ten times too often, in the room.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, metrics=(m := Metrics()))

    unreadable = await tools["remind"](
        when=SOON, text="x", repeat="minutely", every_n="ten",
    )
    assert unreadable["status"] == "refused"

    zero = await tools["remind"](
        when=SOON, text="x", repeat="minutely", every_n=0,
    )
    assert zero["status"] == "refused"

    assert store._rows == {}
    assert m.get("nora_reminders_created_total", status="bad_repeat") == 2


async def test_the_repeat_floor_refusal_is_counted():
    """Every refusal in this file asserts its counter, `too_often` included.
    A refusal nobody can see the rate of is how you find out too late that the
    floor is set wrong for what people actually ask for."""
    store = FakeReminderStore()
    tools = _tools(reminders=store, metrics=(m := Metrics()))

    await tools["remind"](when=SOON, text="起身", repeat="minutely", times=30)

    assert m.get("nora_reminders_created_total", status="too_often") == 1


# ---- `remind` under Dreamer provenance ---------------------------------------


async def _seeded_notes():
    """One open note in the unit `_tools` resolves every room to."""
    from nora.testing import InMemoryNoteStore

    store = InMemoryNoteStore()
    note = await store.create(
        unit=UNIT, title="AI Hackathon dinner plan",
        description="where to eat on Saturday night is open",
        uncertainty="where", closing_condition="a place is picked",
        established_in=CONVERSATION, body="Cyberport, Saturday; venue open.",
        established_by="tag:miles", now_ms=int(T0.timestamp() * 1000),
        anchor_date=20260912,
    )
    return store, note


async def test_the_note_link_resolves_a_title_and_refuses_an_unknown_one():
    """`note` is a TITLE, resolved in code to the id and head revision the
    heartbeat's screen compares against — no id ever enters the prompt. A title
    nothing answers to refuses rather than guessing, and the tag path may link a
    note too, keeping `kind="reminder"`."""
    store = FakeReminderStore()
    notes, note = await _seeded_notes()
    tools = _tools(reminders=store, notes=notes)

    unknown = await tools["remind"](when=SOON, text="提我", note="no such note")
    linked = await tools["remind"](when=SOON, text="提我訂枱", note="AI Hackathon dinner plan")

    assert unknown["status"] == "refused"
    assert linked["status"] == "ok"
    (row,) = await store.pending_for(CONVERSATION)
    assert row.kind == "reminder"
    assert row.requester_person_id == "miles"
    assert row.payload == {
        "note_id": note.id, "revision_id": note.head_revision_id,
        "note_title": "AI Hackathon dinner plan",
    }


async def test_list_reminders_names_a_row_nora_set_herself():
    """The owner of a row she set herself is the ai_peer id, which `persons`
    cannot name — and «someone I can no longer name» would be the one wrong
    answer for the one person here she CAN name."""
    store = FakeReminderStore()
    await store.create(_row(
        requester_person_id="nora", text="nudge about dinner",
    ))

    listing = await _tools(reminders=store)["list_reminders"]()

    assert listing["status"] == "ok"
    assert listing["reminders"][0]["set_by"] == tools_module._SELF_OWNER


async def test_a_remind_ok_says_to_confirm_the_moment_and_only_a_real_limit():
    """ADR 0015: `remind`'s ok reporting rule rides in the result, not the
    description. The limit clause rides only on a series."""
    tools = _tools(reminders=FakeReminderStore(), caps=YEAR)
    once = await tools["remind"](when="2026-10-04T09:00:00+08:00", text="交租")
    series = await tools["remind"](
        when="2026-10-04T09:00:00+08:00", text="食藥", repeat="daily", times=3,
    )
    assert "Say back the moment" in once["note"] and "limit" not in once["note"]
    assert "Say back the moment" in series["note"] and "limit" in series["note"]
    for result in (once, series):
        assert "\u2014" not in result["note"] and "\u2013" not in result["note"]
