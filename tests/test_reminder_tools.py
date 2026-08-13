"""`remind` / `list_reminders` / `cancel_reminder` — the model-facing surface
(design spec §7, §9, §12).

Built through `ToolRegistry.specs(...)`, never by reaching into
`build_tool_specs` directly, because the wiring IS most of what is under test:
the store, the caps and the clock arrive as registry collaborators, and a test
that bypassed the registry would pass against a `remind` production never
reaches (`nora/app/composition.py` is the other half of that, pinned in
`tests/test_composition.py`).

WHAT EACH GROUP OF TESTS IS FOR
-------------------------------
* **The fire-turn refusal** is the one that is not a cap. `fire_turn` is
  threaded to `build_tool_specs` by Task 5 and read here: without the refusal
  Nora re-arms her own reminder every time one fires, and none of §9's caps stop
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

import uuid
from datetime import UTC, datetime, timedelta

from nora.agent import tools as tools_module
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import ConversationTarget, PersonRef
from nora.domain.ports import JobResult, JobStatus
from nora.domain.reminders import Recurrence, Reminder, ReminderCaps, ReminderState
from nora.obs.metrics import Metrics
from nora.testing import FakeComputeBackend, FakeComputeJobStore
from nora.testing.reminders import FakeReminderStore
from tests.fakes import FakeHoncho, SinkSender

CONVERSATION = "conv-1"
TARGET = ConversationTarget("signal", CONVERSATION, "group")

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
    carrying only `resolve` made every owner unresolvable, which is the answer
    the degrade test wants and the WRONG one everywhere else.
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
    compute_backend=None,
    compute_jobs=None,
) -> dict:
    """Every native tool, closed over one turn — keyed by name.

    `reminders=None` is a real production state (no store wired), not a test
    shortcut: it is what `remind` must answer `unavailable` for. `compute_backend`/
    `compute_jobs` default the same honest way, for `make_artifact`.
    """
    registry = ToolRegistry(
        reminders=reminders, reminder_caps=caps, clock=clock,
        compute_backend=compute_backend, compute_jobs=compute_jobs,
    )
    specs = registry.specs(
        store=_FakeStore(), conversation_id=conversation_id, conversation_type="group",
        persons=persons if persons is not None else _FakePersons(MILES, TYLER),
        honcho=FakeHoncho(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=_room(tagger=tagger),
        fire_turn=fire_turn,
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
    """§7.2. A `target` parameter would be an unprompted-speech lever into a
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
        "when", "text", "repeat", "times", "until",
    }
    assert set(inspect.signature(tools["list_reminders"]).parameters) == set()
    assert set(inspect.signature(tools["cancel_reminder"]).parameters) == {"reminder_id"}


# ---- §7.1: the fire-turn refusal --------------------------------------------

async def test_remind_refuses_on_a_fire_turn():
    """§7.1: without this she re-arms her own reminder each time one fires —
    recurrence through the back door, and none of §9's caps stop it because
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


# ---- §12: mapped identity required ------------------------------------------

async def test_remind_refuses_an_unmapped_requester():
    """§12. `requester_person_id` is the row's owner and the id the fire-time
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


# ---- §9: the three caps and the two clock refusals ---------------------------

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
    """§9: `min_lead_seconds` is 120 and `tick_seconds` is 60, so a row set
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


# ---- what a written row actually holds ---------------------------------------

async def test_a_created_row_carries_the_requesters_own_sentence_and_this_room():
    """§2's premise: the row stores what the REQUESTER said, so at fire time it
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
    """The prefill states the time as «19:00 (HKT)» prose, so a model asked for
    ISO-8601 will sometimes drop the offset. `_HKT` is the zone that header
    named, and reading it there is not a guess; refusing would spend a round
    trip on punctuation. A NAIVE value must never reach the store — the port
    raises `NaiveDatetimeError` on one, by design."""
    store = FakeReminderStore()
    tools = _tools(reminders=store)

    result = await tools["remind"](when="2026-08-08T21:00:00", text="洗衫")

    assert result["status"] == "ok"
    row = (await store.pending_for(CONVERSATION))[0]
    assert row.due_at == T0 + timedelta(hours=2)      # 21:00 HKT == 13:00 UTC


# ---- §7.3: list and cancel are conversation-scoped ---------------------------

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
    """§7.3's ownership rule made legible. A reminder is group-owned, so this
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
    firing (§12).

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
    """§7.3: a uuid carries no scope of its own, so the STORE re-checks it —
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
    """§8.3: a cancel against a resolved row is a no-op, and the tool says so
    rather than reporting success — «I stopped it» about a message already sent
    is the one answer that cannot be repaired."""
    store = FakeReminderStore()
    done = _row(state=ReminderState.DONE)
    await store.create(done)
    tools = _tools(reminders=store)

    result = await tools["cancel_reminder"](reminder_id=str(done.id))

    assert result["status"] == "refused"
    assert (await store.get(done.id)).state is ReminderState.DONE


# ---- the tool table (DEC-10, DEC-32) -----------------------------------------

def test_none_of_the_three_is_a_terminal_verb():
    """§6.1 adds no terminal verb. One would break
    `_terminal_budget_spent`'s AND — a turn would end when the model had merely
    set a reminder and said nothing — and `tests/test_guards_terminal.py` pins
    `terminal_tools() == frozenset({"send_reply"})`."""
    registry = ToolRegistry()
    assert registry.terminal_tools() == frozenset({"send_reply"})
    for name in ("remind", "list_reminders", "cancel_reminder"):
        assert name not in registry.terminal_tools()
        assert name in registry.budgets()


def test_the_three_tools_are_described_to_the_model():
    """The capability block is generated from the same table the tools are
    built from (DEC-29), so a tool she has but is never told when to reach for
    is the `soul.md` failure that block exists to prevent."""
    block = ToolRegistry().capability()
    for name in ("remind", "list_reminders", "cancel_reminder"):
        assert f"- {name} — " in block


# ---- the counter's own documentation ----------------------------------------

def test_every_status_remind_can_emit_is_named_in_the_metric_help():
    """`nora_reminders_created_total`'s `# HELP` line is the ONLY description of
    what its `status` axis can hold — the series is unlabelled-by-default in
    every dashboard until someone reads that line.

    It listed four values while `_record_created` emitted nine, so five real
    statuses — including `unavailable`, which means no store is wired at all,
    and `fire_turn`, which is R-39 — were undocumented. An operator seeing one
    could not tell a genuine status from a typo. Read out of the module SOURCE
    rather than from a hand-kept list here, so adding a tenth refusal without
    documenting it is what turns this red.

    TWO patterns are read, not one (DEC-85). The recurrence refusals do not
    reach `_record_created` as literals — they travel as `_RepeatRefusal`
    values and are recorded through `rule.status` — so a regex that only knew
    the call site would have found neither `bad_repeat` nor `bad_bound` and
    reported success, which is exactly the decorative shape this file exists to
    keep out. Both constructors are matched, and both must name their status in
    the help text.
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
# Each of the three is verbatim from the group, and each was REFUSED before this
# decision — the first two with 「我 set 唔到 recurring 每日提嘅 reminder」. They
# are tests rather than an anecdote in a design document because what they pin
# is the SHAPE the model has to produce: a recurrence is `when` + `repeat` +
# a bound, and lead time (「前兩個星期」) is arithmetic on `when` rather than a
# concept anything here stores.

# `max_horizon_days` bounds the FIRST occurrence and ships at 30. Two of the
# three asks name a date months out, so they are exercised against an operator
# who has widened it — which is a config change, not a code one, and stating it
# here is what stops a green test from implying the shipped default is enough.
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
    would have committed her to sixty of them. The row is byte-identical to a
    pre-DEC-85 one — no series, and no `repeat` key in the answer at all.
    """
    store = FakeReminderStore()
    tools = _tools(reminders=store, caps=YEAR)

    result = await tools["remind"](
        when="2026-10-04T09:00:00+08:00", text="下個禮拜結婚週年",
    )

    assert result["status"] == "ok"
    assert set(result) == {"status", "id", "due_at"}
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
    forbids it, which is exactly how 「記住我嘅生日」 was refused. One period of
    headroom, and no more: the horizon still bounds a DAILY series to 30 days.
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
    assert row["repeat_left"] == 13
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


# ---- make_artifact (Nora Compute spec §4, Task 5) --------------------------
#
# Built through the same `_tools()` helper as everything above it — the
# wiring (registry -> build_tool_specs -> a real closure) IS what is under
# test, the same argument this file's own module docstring makes for
# `remind`. `compute_backend=None` (the `_tools()` default) is the honest
# "not configured" state, exercised explicitly below.

async def test_make_artifact_unavailable_when_compute_backend_not_configured():
    """R-5: no backend wired is an honest wiring gap, not a crash and not a
    guess."""
    tools = _tools()

    result = await tools["make_artifact"](task="write a haiku")

    assert result == {"status": "unavailable"}


async def test_make_artifact_returns_the_result_inline_when_fast():
    """`FakeComputeBackend.launch` defaults to immediate SUCCEEDED (its own
    docstring), so the very first poll inside the sync window already sees a
    non-RUNNING result and the slow path never engages."""
    backend = FakeComputeBackend()
    jobs = FakeComputeJobStore()
    tools = _tools(compute_backend=backend, compute_jobs=jobs)

    result = await tools["make_artifact"](task="write a haiku")

    assert result["status"] == "ok"
    assert "url" in result
    assert "summary" in result
    assert jobs._jobs == {}  # the fast path never writes a row


async def test_make_artifact_falls_back_to_still_working_when_slow(monkeypatch):
    """Arrange a job that stays RUNNING past the sync poll window --
    `FakeComputeBackend.set_result`'s `polls_before_done` (Phase 2 Task 1) --
    and shrink the window itself so the test does not spend real wall-clock
    seconds sleeping through it. `_SYNC_POLL_SECONDS`/`_SYNC_POLL_INTERVAL`
    are module constants precisely so a test can monkeypatch them directly
    rather than needing a new hook threaded through production code."""
    monkeypatch.setattr(tools_module, "_SYNC_POLL_SECONDS", 0.03)
    monkeypatch.setattr(tools_module, "_SYNC_POLL_INTERVAL", 0.01)
    backend = FakeComputeBackend()
    jobs = FakeComputeJobStore()
    # `FakeComputeBackend`'s ids are assigned in order starting at "job_000001"
    # -- this is the FIRST (and only) job this backend instance ever launches,
    # so it is deterministic to arrange before `make_artifact` calls `launch`.
    backend.set_result(
        job_id="job_000001",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="ok"),
        polls_before_done=999,
    )
    tools = _tools(compute_backend=backend, compute_jobs=jobs)

    result = await tools["make_artifact"](task="a long research task")

    assert result == {"status": "ok", "still_working": True}
    assert len(jobs._jobs) == 1  # a durable row was written
    [job] = jobs._jobs.values()
    assert job.task == "a long research task"
    assert job.backend_job_id == "job_000001"
    assert job.requester_person_id == MILES.person_id
    assert job.conversation_id == CONVERSATION
    assert job.state.value == "pending"


async def test_make_artifact_unavailable_when_slow_and_no_tagger(monkeypatch):
    """Mirrors `remind`'s own unmapped-sender refusal: with nobody to deliver
    the eventual result TO, writing a durable row would be a job with nowhere
    to land, so this degrades to `unavailable` instead."""
    monkeypatch.setattr(tools_module, "_SYNC_POLL_SECONDS", 0.03)
    monkeypatch.setattr(tools_module, "_SYNC_POLL_INTERVAL", 0.01)
    backend = FakeComputeBackend()
    jobs = FakeComputeJobStore()
    backend.set_result(
        job_id="job_000001",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="ok"),
        polls_before_done=999,
    )
    tools = _tools(compute_backend=backend, compute_jobs=jobs, tagger=None)

    result = await tools["make_artifact"](task="a long research task")

    assert result == {"status": "unavailable"}
    assert jobs._jobs == {}
