"""What a Cognition session is handed, end to end through the REAL evaluator and
the REAL runner (the 2026-09-30 whole-loop review, PR A, DEC-245): the clock line
counts from when she last LOOKED, a rollup she cannot read is not an item, a
room is named by its label, and a session that closed records no failure cause.

Each test reads the prompt the model was actually handed, or the row the run
wrote, never the object either was built from.

What each test would catch if its fix were reverted is named in
`scripts/mutations/cognition-input.json`.
"""

from __future__ import annotations

from nora.domain.cognition_ground import Grounding, RollupLine
from nora.domain.stimuli import NewStimulus
from nora.testing.cognition_grounding import StaticGrounder
from tests.unit.test_cognition_session_lifecycle import T, _rig, ok

HOUR = 3600


# ---- B2: the clock ---------------------------------------------------------------------------

async def test_the_clock_line_counts_from_when_the_last_session_ended_not_the_last_message():
    """Before: `last_session_ms` was `view.last_activity_ms`, the newest PERSON
    message, so «you last looked» said «just now» about a room whose last look
    was two hours ago. The view here is a real one: wake, session, outcome."""
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
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=10)
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


# ---- B2: a rollup the session cannot read is not an agenda item ---------------------------

def _rollup(unit: str, ref: str, at: int) -> NewStimulus:
    return NewStimulus(unit=unit, kind="rollup", item="deviation_silence", ref=ref, at_ms=at,
                       person_id="ada")


def _readable(ref: str) -> RollupLine:
    return RollupLine(ref=ref, kind="deviation_silence", names=("Ada",), person_ids=("ada",),
                      start_ms=T, end_ms=T + HOUR * 1000)


async def test_an_unreadable_rollup_is_skipped_and_recorded_and_a_readable_one_is_an_item():
    good, bad = "deviation|silence|ada|1", "deviation|silence|ghost|2"
    rig = _rig(grounder=StaticGrounder(Grounding(rollups={good: _readable(good)})))
    for ref in (good, bad):
        await rig.stimuli.append(stimulus=_rollup("u1", ref, T), recorded_at_ms=T)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    (_, session, _), = rig.ledger.sessions
    (item,) = session.agenda["items"]
    assert item["stimulus_ids"] == [1]
    assert session.agenda["skipped_stimulus_ids"] == [2]        # recorded, never silent
    assert rig.metrics.get("nora_cognition_stimuli_skipped_total", reason="unreadable_rollup") == 1
    assert rig.ends() == ["closed"]


async def test_a_wake_whose_only_rollups_are_unreadable_makes_no_model_call():
    rig = _rig(grounder=StaticGrounder(Grounding()))
    await rig.stimuli.append(stimulus=_rollup("u1", "deviation|silence|ghost|1", T),
                             recorded_at_ms=T)
    rig.clock.advance(120)
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 0
    assert rig.ends() == ["closed"] and "unreadable" in rig.detail()
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=10)
    assert view.pending == ()                                    # skipped, not wedged


# ---- m5: the label, and the id only where a tool needs it ---------------------------------

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
    items = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
    assert len(items) == 2
    assert all("room-a" not in ln and "room-b" not in ln for ln in items)
    assert "[room «Weekend Plans»]" in items[0] or "[room «Weekend Plans»]" in items[1]
    reads = next(ln for ln in prompt.splitlines() if "read tools" in ln)
    assert "`signal:room-a` («Weekend Plans»)" in reads


async def test_a_single_room_agenda_says_only_its_label_in_the_reads_line():
    rig = _rig(grounder=StaticGrounder(Grounding(room_labels={"signal:room-a": "Weekend Plans"})))
    await rig.say(room="room-a")
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    prompt = rig.agent.prompts[0]
    assert "Your read tools look through room «Weekend Plans»." in prompt
    assert "room-a" not in prompt


# ---- m2: a session that closed records no failure cause -----------------------------------

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
