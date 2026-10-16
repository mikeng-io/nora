"""What a Cognition session is handed, end to end through the REAL evaluator and
the REAL runner (DEC-245): the clock line
counts from when she last LOOKED, a rollup she cannot read is not an item, a
room is named by its label, and a session that closed records no failure cause.

Each test reads the prompt the model was actually handed, or the row the run
wrote, never the object either was built from.

What each test would catch if its fix were reverted is named in
`scripts/mutations/cognition-input.json`.
"""

from __future__ import annotations

import dataclasses

from nora.domain.cognition_ground import Grounding, RollupLine
from nora.domain.stimuli import NewStimulus
from nora.testing.cognition_grounding import StaticGrounder
from tests.unit.cognition_view import items, named
from tests.unit.test_cognition_failure_backoff import _run, _until_calls
from tests.unit.test_cognition_session_lifecycle import POLICY, ScriptedAgent, T, _rig, ok

HOUR = 3600


# ---- the clock ---------------------------------------------------------------------------

async def test_the_clock_line_counts_from_when_the_last_session_ended_not_the_last_message():
    """`last_session_ms` is when she last looked, not `view.last_activity_ms`
    (the newest PERSON message): «you last looked» must not say «just now» about
    a room whose last look was two hours ago. The view here is a real one: wake,
    session, outcome."""
    rig = _rig()
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    first_ended = rig.clock.ms                 # the session closed at the fake clock's now
    rig.clock.advance(2 * HOUR)
    await rig.say()                             # the room speaks again...
    rig.clock.advance(30)                       # ...30 seconds before this wake
    await rig.tick()
    await rig.settle()
    assert len(rig.agent.prompts) == 2
    assert "This is your first look at this group." in rig.agent.prompts[0]
    assert "You last looked at this group 2 h ago." in rig.agent.prompts[1]
    assert "just now" not in rig.agent.prompts[1].splitlines()[0]
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert view.last_session is not None and view.last_session.ended_at_ms is not None
    assert view.last_session.ended_at_ms >= first_ended


async def test_a_unit_that_never_ran_a_session_says_it_is_the_first_look_however_active_the_room():
    rig = _rig()
    for _ in range(3):
        await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert "This is your first look at this group." in rig.agent.prompts[0]


# ---- a rollup the session cannot read is not an agenda item ---------------------------

def _rollup(unit: str, ref: str, at: int, item: str = "deviation_silence") -> NewStimulus:
    return NewStimulus(unit=unit, kind="rollup", item=item, ref=ref, at_ms=at,
                       person_id="ada")


async def _floor(rig) -> int:
    """A daily `time` floor due now. A Rollup stimulus starts no wake of its own
    (DEC-279): it rides the next one, and a floor's is the one that bounds it."""
    sid = await rig.stimuli.append(stimulus=NewStimulus(
        unit="u1", kind="time", item="floor", ref=f"floor:{rig.clock.ms}",
        at_ms=rig.clock.ms, due_ms=rig.clock.ms,
    ), recorded_at_ms=rig.clock.ms)
    assert sid is not None
    return sid


def _rollup_items(session) -> list[dict]:
    return [i for i in session.agenda["items"] if i["kind"] == "rollup"]


def _readable(ref: str) -> RollupLine:
    return RollupLine(ref=ref, kind="deviation_silence", names=("Ada",), person_ids=("ada",),
                      start_ms=T, end_ms=T + HOUR * 1000)


KNOWN = {"ada"}
BAD = "deviation|silence|ghost|2"          # a person the roster does not know


def _known(person_id: str) -> bool:
    return person_id in KNOWN


async def test_a_malformed_rollup_is_skipped_recorded_and_consumed_and_a_readable_one_is_an_item():
    good, bad = "deviation|silence|ada|1", "deviation|silence||2"     # no person: never readable
    rig = _rig(person_known=_known)
    for ref in (good, bad):
        await rig.stimuli.append(stimulus=_rollup("u1", ref, T), recorded_at_ms=T)
    await _floor(rig)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    (_, session, _), = rig.ledger.sessions
    (item,) = _rollup_items(session)
    assert item["stimulus_ids"] == [1]
    assert session.agenda["skipped_stimulus_ids"] == [2]        # recorded, never silent
    assert session.agenda["retained_stimulus_ids"] == []
    assert rig.metrics.get("nora_cognition_stimuli_skipped_total", reason="unreadable_rollup") == 1
    assert rig.ends() == ["closed"]
    assert 2 not in {sid for sid, *_ in rig.carried()}          # consumed: no carry, no wedge
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert 2 not in {x.id for x in view.pending}


async def test_a_readable_rollup_cut_by_max_items_is_deferred_and_carried_not_skipped():
    """Readability is decided from the roster, not by the grounder: the grounder
    only builds lines for the items the agenda kept, so a readable rollup cut at
    `max_items` would be labelled unreadable, dropped from `deferred` and lost,
    with no carry. Two readable deviation rollups, room for one."""
    rig = _rig(person_known=_known, max_items=1)
    for ref, item in (("deviation|silence|ada|1", "deviation_silence"),
                      ("deviation|anomaly|ada|2", "deviation_anomaly")):     # two ITEMS
        await rig.stimuli.append(stimulus=_rollup("u1", ref, T, item), recorded_at_ms=T)
    await _floor(rig)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    (_, session, _), = rig.ledger.sessions
    assert [i["stimulus_ids"] for i in session.agenda["items"]] == [[1]]
    assert 2 in session.agenda["deferred_stimulus_ids"]
    assert session.agenda["skipped_stimulus_ids"] == []
    assert rig.metrics.get("nora_cognition_stimuli_skipped_total", reason="unreadable_rollup") == 0
    assert (2, 1, "carried", False) in rig.carried()             # returned to the next agenda
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert 2 in {x.id for x in view.pending}          # not consumed


async def test_a_rollup_the_roster_does_not_know_yet_is_retained_and_returns_once_it_does():
    """The choice (DEC-245): a roster miss is not permanent. The stimulus is
    RETAINED as a carry row that waits, so it is never consumed unseen, never
    raises the `rollup` trigger (a waiting stimulus does not wake), and is an
    agenda item again in the first session after the roster knows the person."""
    KNOWN.discard("ghost")
    rig = _rig(person_known=_known)
    await rig.stimuli.append(stimulus=_rollup("u1", BAD, T), recorded_at_ms=T)
    await _floor(rig)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    (_, first, _), = rig.ledger.sessions
    assert _rollup_items(first) == [] and rig.ends() == ["closed"]
    assert (1, 0, "carried", True) in rig.carried()
    assert rig.metrics.get("nora_cognition_stimuli_skipped_total",
                           reason="roster_miss_rollup") == 1
    # ... it is still pending, and it does NOT wake the unit again, however long we wait
    for _ in range(3):
        rig.clock.advance(6 * HOUR)
        assert await rig.tick() == []
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert (1, True) in [(x.id, x.waits) for x in view.pending]
    # The roster learns her; the next wake for any reason shows it as an item.
    KNOWN.add("ghost")
    try:
        await rig.say()
        rig.clock.advance(1)
        await rig.tick()
        await rig.settle()
    finally:
        KNOWN.discard("ghost")
    (_, session, _) = rig.ledger.sessions[-1]
    assert any(1 in i["stimulus_ids"] for i in session.agenda["items"])
    assert session.agenda["skipped_stimulus_ids"] == []


async def test_a_roster_miss_rides_a_session_that_runs_and_is_retained_beside_its_carries():
    """The same retention when the session DOES run for another reason: the
    skipped rollup is written as a waiting carry in the wake's close, next to the
    item's own outcome, and is not an item of this session."""
    rig = _rig(person_known=_known)
    await rig.stimuli.append(stimulus=_rollup("u1", BAD, T), recorded_at_ms=T)
    await rig.say()
    rig.clock.advance(1900)
    await rig.tick()
    await rig.settle()
    (_, session, _), = rig.ledger.sessions
    assert [i["kind"] for i in session.agenda["items"]] == ["message"]
    assert session.agenda["skipped_stimulus_ids"] == [1]
    assert session.agenda["retained_stimulus_ids"] == [1]
    assert (1, 0, "carried", True) in rig.carried()
    assert rig.ends() == ["closed"]


async def test_unreadable_rollups_riding_a_floor_are_skipped_and_the_floor_is_the_only_item():
    rig = _rig(person_known=_known)
    await rig.stimuli.append(stimulus=_rollup("u1", "deviation|silence||1", T),
                             recorded_at_ms=T)
    await _floor(rig)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    (_, session, _), = rig.ledger.sessions
    assert [i["kind"] for i in session.agenda["items"]] == ["time"]
    assert session.agenda["skipped_stimulus_ids"] == [1]
    assert rig.ends() == ["closed"]
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert 1 not in {x.id for x in view.pending}                 # skipped, not wedged


# ---- the label, and the id only where a tool needs it ---------------------------------

async def test_the_prompt_names_a_room_by_its_label_and_lists_ids_beside_a_tools_room_argument():
    grounding = Grounding(room_labels={
        "signal:room-a": "Weekend Plans", "signal:room-b": "Work Chat",
    })
    rig = _rig(grounder=StaticGrounder(grounding))
    await rig.say(room="room-a")
    await rig.say(room="room-b")
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    prompt = rig.agent.prompts[0]
    agenda = items(prompt)
    assert len(agenda) == 2
    assert all(
        "room-a" not in i.attrib["room"] and "room-b" not in i.attrib["room"]
        for i in agenda
    )
    assert "«Weekend Plans»" in {i.attrib["room"] for i in agenda}
    (reads,) = named(prompt, "reads")
    assert "`signal:room-a` («Weekend Plans»)" in (reads.text or "")


async def test_a_single_room_agenda_says_only_its_label_in_the_reads_line():
    rig = _rig(grounder=StaticGrounder(Grounding(room_labels={"signal:room-a": "Weekend Plans"})))
    await rig.say(room="room-a")
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    prompt = rig.agent.prompts[0]
    assert "Your read tools look through room «Weekend Plans»." in prompt
    assert "room-a" not in prompt


# ---- a session that closed records no failure cause -----------------------------------

async def test_a_closed_wake_records_no_cause_and_a_failed_one_records_its_own():
    from tests.unit.test_cognition_session_lifecycle import ScriptedAgent, boom

    rig = _rig(agent=ScriptedAgent(script=[ok, boom]))
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    rig.clock.advance(1000)
    await rig.say()
    await rig.tick()
    await rig.settle()
    closed, failed = (o for _, o in sorted(rig.stimuli.outcomes.items()))
    assert (closed.outcome, closed.cause) == ("closed", "none")     # never `infra`
    assert (failed.outcome, failed.cause) == ("failed", "infra")


# ---- what a failing wake struck is what it CLAIMED -----------------------------------

async def test_striking_a_failing_floor_wake_never_strikes_the_messages_it_left_pending():
    """A floor wake (carrying the rollup that rode it, DEC-279) that fails its
    agenda five times strikes what it claimed. The unsettled room's message sat
    INSIDE its id range but was never claimed, so it must not be retracted
    unseen: the strike reads the failed wake's watermark, unclaimed ids
    excluded."""
    from tests.unit.test_cognition_session_lifecycle import agenda_fault

    ref = "deviation|silence|ada|1"
    quiet = dataclasses.replace(
        POLICY, quiet_seconds=10**9, max_age_seconds=10**9, batch_max=10**6,
    )
    rig = _rig(
        agent=ScriptedAgent(script=[agenda_fault]), policy=quiet,
        grounder=StaticGrounder(Grounding(rollups={ref: _readable(ref)})),
    )
    message = await rig.say()                       # id 1: the room is live, never settles
    rolled = await rig.stimuli.append(stimulus=_rollup("u1", ref, T), recorded_at_ms=T)
    assert rolled is not None and rolled > message
    floor = await _floor(rig)
    await _until_calls(rig, 5)
    await rig.settle()
    await _run(rig, ticks=2, chatty=False)          # the tick that reads five agenda failures
    assert rig.stimuli.retracted == {rolled, floor}  # what it claimed, and nothing else
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=10)
    assert [s.id for s in view.pending] == [message]


def test_an_outcome_row_written_before_0085_reads_no_cause_and_a_failure_keeps_its_own():
    """Migration 0085 rewrites no row (the table is append-only), so a closed or
    cancelled outcome written with `infra` is normalised when it is READ."""
    from nora.domain.stimuli import wake_state

    def cause(outcome: str, stored: str) -> str:
        state = wake_state(tracked=True, recorded_at_ms=1, outcome=outcome, outcome_at_ms=2,
                           stale_before_ms=None, cause=stored)
        assert state is not None
        return state.cause

    assert cause("closed", "infra") == "none"
    assert cause("cancelled", "infra") == "none"
    assert cause("failed", "infra") == "infra"
    assert cause("timeout", "agenda") == "agenda"
