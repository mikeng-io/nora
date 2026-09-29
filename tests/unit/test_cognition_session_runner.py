"""`nora.app.cognition_session.CognitionSessionRunner` — the session loop
that turns a would-wake into ONE Cognition session in shadow (migration
step 3a-ii; design/cognition-loop/30-cognition.md §1-§2).

`FakeCognitionAgentRunner` is the test seam
(`nora.app.cognition_session.CognitionAgentRunner`): it never touches
Strands or a model, and its `.run()` calls the closures it is HANDED by
name — exactly what a real model would do through Strands' tool-calling
loop, with none of the SDK machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
HOME = Room("signal", "home-room")
BUDGET = AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)


@dataclass
class FakeCognitionAgentRunner:
    """Scripted: on `.run()`, calls `script` (a list of `(tool_name, kwargs)`
    pairs) against the tools it was handed, in order, then returns
    `stop_reason`."""

    script: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    calls: int = 0
    last_tool_names: list[str] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.calls += 1
        by_name = {getattr(t, "__name__", None): t for t in tools}
        self.last_tool_names = sorted(n for n in by_name if n)
        for name, kwargs in self.script:
            await by_name[name](**kwargs)
        return CognitionRunResult(stop_reason="end_turn")


def _pending(unit: str, n: int = 1) -> tuple[Stimulus, ...]:
    return tuple(
        Stimulus(
            id=i, unit=unit, kind="message", item="message", ref=f"messages:{i}",
            at_ms=NOW, recorded_at_ms=NOW, platform="signal", conversation_id="home-room",
        )
        for i in range(1, n + 1)
    )


def _runner(
    *, session_mode: str = "shadow", agent_runner: FakeCognitionAgentRunner | None = None,
    dry_run_store: InMemoryCognitionDryRunStore | None = None,
) -> tuple[CognitionSessionRunner, InMemoryCognitionDryRunStore, FakeCognitionAgentRunner]:
    store = dry_run_store or InMemoryCognitionDryRunStore()
    fake = agent_runner or FakeCognitionAgentRunner()
    runner = CognitionSessionRunner(
        dry_run_store=store, stimulus_store=InMemoryStimulusStore(), agent_runner=fake,
        home_rooms={"unit-1": HOME}, read_tools_for=lambda unit, room: [],
        skill_text_for=lambda kinds: "", doctrine_text="DOCTRINE",
        doctrine_sha256="sha", open_note_titles=_async_const(()),
        open_proposed_events=_async_const2(()), prior_standing=_async_const2(None),
        pending_self_wakes=_async_const(()), pending_mirrored_reminds=_async_const(()),
        session_mode=session_mode, budget=BUDGET, cooldown_seconds=300.0, max_items=12,
        max_strikes=3, mint_ref=_counting_ref(),
    )
    return runner, store, fake


def _async_const(value):
    async def f(*args, **kwargs):
        return value
    return f


def _async_const2(value):
    async def f(*args, **kwargs):
        return value
    return f


def _counting_ref():
    n = 0
    def f():
        nonlocal n
        n += 1
        return f"ref{n}"
    return f


# --------------------------------------------------------------------------
# off / empty agenda: zero model calls
# --------------------------------------------------------------------------

async def test_session_mode_off_makes_no_model_call():
    runner, store, fake = _runner(session_mode="off")
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert outcome is None
    assert fake.calls == 0
    assert store.sessions == []


async def test_an_empty_agenda_makes_no_model_call():
    runner, store, fake = _runner()
    view = UnitView(unit="unit-1", pending=(), last_activity_ms=None, last_wake=None)
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert outcome is None
    assert fake.calls == 0
    assert store.sessions == []


async def test_an_unknown_unit_with_no_home_room_makes_no_model_call():
    runner, _store, fake = _runner()
    view = UnitView(
        unit="ghost-unit", pending=_pending("ghost-unit"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="ghost-unit", view=view, now_ms=NOW)
    assert outcome is None
    assert fake.calls == 0


# --------------------------------------------------------------------------
# a real session: records itself, runs the model exactly once, closes items
# --------------------------------------------------------------------------

async def test_a_session_with_a_nonempty_agenda_records_itself_and_calls_the_model_once():
    fake = FakeCognitionAgentRunner(script=[])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1", n=3), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=42)
    assert fake.calls == 1
    assert outcome is not None
    assert len(store.sessions) == 1
    session_id, session, _ = store.sessions[0]
    assert session.wake_id == 42
    assert session.mode == "shadow"
    assert session_id == outcome.session_id


async def test_an_item_never_closed_by_the_model_is_closed_waiting_by_code():
    runner, store, _fake = _runner(agent_runner=FakeCognitionAgentRunner(script=[]))
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert len(outcome.closed) == 1
    assert outcome.closed[0].reason == "waiting"
    assert len(store.closes) == 1


async def test_the_model_can_close_an_item_explicitly():
    fake = FakeCognitionAgentRunner(script=[("close", {"item_index": 0, "reason": "no_change"})])
    runner, _store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert len(outcome.closed) == 1
    assert outcome.closed[0].reason == "no_change"


async def test_the_tool_list_offers_every_write_pace_and_remind_verb():
    fake = FakeCognitionAgentRunner(script=[])
    runner, _store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert set(fake.last_tool_names) >= {
        "close", "note_create", "note_update", "event_propose", "event_revise",
        "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
        "remind", "remind_move", "remind_cancel",
    }


async def test_a_would_write_call_is_recorded_against_this_sessions_id():
    fake = FakeCognitionAgentRunner(script=[(
        "standing_revise", {"where_it_stands": "dinner is on", "working_set": []},
    )])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert len(store.would_writes) == 1
    _, write, _ = store.would_writes[0]
    assert write.session_id == outcome.session_id
    assert write.outcome == "would_commit"


async def test_a_wake_at_call_is_recorded_with_a_minted_ref():
    fake = FakeCognitionAgentRunner(script=[(
        "wake_at", {"subject_rows": ["event:1"], "at_ms": NOW + 3_600_000, "why": "check it"},
    )])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert len(store.paces) == 1
    _, pace, _ = store.paces[0]
    assert pace.outcome == "recorded"
    assert pace.wake_ref == "ref1"


async def test_a_remind_call_is_recorded_and_never_reaches_a_send_path():
    fake = FakeCognitionAgentRunner(script=[(
        "remind", {
            "room_platform": "signal", "room_conversation_id": "home-room",
            "intent": "tell", "subject_rows": ["note:1"], "at_ms": NOW + 60_000,
        },
    )])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW)
    assert len(store.remind_mirrors) == 1
    _, mirror, _ = store.remind_mirrors[0]
    assert mirror.outcome == "recorded"
