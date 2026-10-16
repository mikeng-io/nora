"""The REAL evaluator composed with the REAL session runner: a would-wake produces a session
row through the one typed hook, the brakes count sessions that are really in flight, and a session
that fails does not move the watermark.

Only the model is scripted (`ScriptedAgent`): every store is an in-memory
double bound to its SQL twin by a contract suite, and both objects share one
fake clock, so «how long a session ran» is a fact the test sets.

What each test would catch if the fix were reverted is named in
`scripts/mutations/cognition-step3a2-session.json`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
    NotReadOnlyToolError,
)
from nora.app.wake import WakeEvaluator
from nora.domain.cognition_session import Room
from nora.domain.stimuli import NewStimulus, UnitView, WakeHook, WakePolicy
from nora.obs.metrics import Metrics
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore
from tests.unit.cognition_view import items as agenda_items

HK = ZoneInfo("Asia/Hong_Kong")


def iso(ms: int) -> str:
    """A session verb's time: ISO 8601 with an offset (M5)."""
    return dt.datetime.fromtimestamp(ms / 1000, HK).isoformat()


T = int(dt.datetime(2026, 9, 28, 15, 0, tzinfo=HK).timestamp() * 1000)
S = 1000
HOME = Room("signal", "home")
POLICY = WakePolicy(
    quiet_seconds=0, cue_quiet_seconds=0, batch_max=1000,
    max_age_seconds=3600, cooldown_seconds=300, max_concurrent_sessions=2,
    session_seconds=60, session_timeout_seconds=900,
)

Behaviour = Callable[["ScriptedAgent", list[Any]], Awaitable[CognitionRunResult]]


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


async def ok(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    return CognitionRunResult(stop_reason="end_turn")


async def close_written(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    """Closes every agenda item, so nothing returns to the next one. `no_change`,
    not `written`: a `written` close needs a write of the session's own (B3)."""
    by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
    for i in range(len(agent.prompts[-1].splitlines())):
        await by["close"](item_index=i, reason="no_change")
    return CognitionRunResult(stop_reason="end_turn")


async def boom(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    raise RuntimeError("the model loop blew up")


async def agenda_fault(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    """A failure the AGENDA causes, already PROVEN (`classify_failure`: cause
    `agenda`, the only cause that strikes)."""
    from nora.domain.stimuli import AgendaError

    raise AgendaError("the agenda cannot be processed")


async def overflow(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    """What Strands raises for a context-length error, with NO proof it was the
    agenda's size (a 400 «maximum context length»)."""
    from strands.types.exceptions import ContextWindowOverflowException

    raise ContextWindowOverflowException(
        "This model's maximum context length is 8192 tokens; too many tokens",
    )


@dataclass
class ScriptedAgent:
    """The fake model. Each `run` takes the next behaviour off `script` (the
    last one repeats), so a test can make the first session fail and the second
    succeed. `release` blocks a behaviour until the test lets it go."""

    script: list[Behaviour] = field(default_factory=lambda: [ok])
    calls: int = 0
    prompts: list[str] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    tools_seen: list[list[Any]] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.calls += 1
        self.prompts.append(prompt)
        self.tools_seen.append(tools)
        behaviour = self.script[min(self.calls - 1, len(self.script) - 1)]
        return await behaviour(self, tools)

    async def block(self, tools: list[Any]) -> CognitionRunResult:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return CognitionRunResult(stop_reason="end_turn")


async def blocked(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
    return await agent.block(tools)


def _const(value: Any) -> Callable[..., Awaitable[Any]]:
    async def f(*args: Any, **kwargs: Any) -> Any:
        return value
    return f


def _counter() -> Callable[[], str]:
    n = 0

    def mint() -> str:
        nonlocal n
        n += 1
        return f"ref{n}"
    return mint


@dataclass
class Rig:
    ledger: InMemoryCognitionStore
    stimuli: InMemoryStimulusStore
    clock: Clock
    metrics: Metrics
    agent: ScriptedAgent
    runner: CognitionSessionRunner
    ev: WakeEvaluator

    async def say(
        self, *, unit: str = "u1", room: str = "room-a", cues: tuple[str, ...] = (),
    ) -> int:
        sid = await self.stimuli.append(stimulus=NewStimulus(
            unit=unit, kind="message", item="message",
            ref=f"messages:{len(self.stimuli.stimuli) + 1}", at_ms=self.clock.ms,
            platform="signal", conversation_id=room, cues=cues,
        ), recorded_at_ms=self.clock.ms)
        assert sid is not None
        return sid

    async def tick(self) -> list[int]:
        return await self.ev.tick()

    async def settle(self) -> None:
        """Let every started session finish (or fail) and be recorded."""
        await self.ev.drain()

    def ends(self) -> list[str]:
        """Each wake's outcome, in wake order."""
        return [o.outcome for _, o in sorted(self.stimuli.outcomes.items())]

    def detail(self, n: int = 0) -> str:
        return [o.detail for _, o in sorted(self.stimuli.outcomes.items())][n]

    def carried(self) -> list[tuple[int, int, str, bool]]:
        return [(c.stimulus_id, c.strike, c.state, c.waits) for _, _, c in self.stimuli.carries]


def _rig(
    *, agent: ScriptedAgent | None = None, units: tuple[str, ...] = ("u1",),
    timeout: float = 30.0, max_items: int = 12, max_strikes: int = 3,
    policy: WakePolicy = POLICY,
    read_tools_for: Callable[[str, Sequence[Room]], list[Any]] | None = None,
    read_only_tools: frozenset[str] = frozenset(), max_self_wakes: int = 5,
    max_self_wakes_per_day: int = 12,
    model_window_tokens: int | None = None, overflow_window_fraction: float = 0.8,
    grounder: Any = None, person_known: Callable[[str], bool] | None = None,
) -> Rig:
    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    clock = Clock(T)
    metrics = Metrics()
    agent = agent or ScriptedAgent()

    async def _pending(unit: str, since_ms: int) -> Any:
        rows = await ledger.pending_self_wakes(unit=unit, since_ms=since_ms)
        await asyncio.sleep(0)  # a real store yields here; so must the double
        return rows

    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli, agent_runner=agent,
        home_rooms=dict.fromkeys(units, HOME),
        read_tools_for=read_tools_for or (lambda unit, rooms: []),
        read_only_tools=read_only_tools,
        skill_text_for=lambda kinds: "", grounder=grounder or EmptyGrounder(),
        doctrine_text="DOCTRINE", doctrine_sha256="sha",
        open_note_titles=_const(()), open_events=_const(()),
        prior_standing=_const(None),
        budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=100,
                             timeout_seconds=timeout),
        cooldown_seconds=300.0, units=units,
        session_timeout_seconds=policy.session_timeout_seconds,
        max_items=max_items, max_strikes=max_strikes,
        max_self_wakes=max_self_wakes, max_self_wakes_per_day=max_self_wakes_per_day,
        metrics=metrics, mint_ref=_counter(), clock=clock,
        model_window_tokens=model_window_tokens,
        overflow_window_fraction=overflow_window_fraction, person_known=person_known,
    )
    ev = WakeEvaluator(
        store=stimuli, units=lambda: units, policy=policy, metrics=metrics, tz=HK,
        clock=clock, settle_seconds=0, on_wake=runner.wake_hook,
    )
    return Rig(ledger, stimuli, clock, metrics, agent, runner, ev)


# ---------------------------------------------------------------------------
# the evaluator and the runner share ONE typed hook
# ---------------------------------------------------------------------------

async def test_a_would_wake_through_the_real_evaluator_produces_a_session_row():
    rig = _rig()
    await rig.say()
    rig.clock.advance(1)
    wake_ids = await rig.tick()
    await rig.settle()

    assert len(wake_ids) == 1
    assert len(rig.ledger.sessions) == 1
    session_id, session, _ = rig.ledger.sessions[0]
    assert session.wake_id == wake_ids[0]
    assert session.unit == "u1"
    assert rig.agent.calls == 1
    assert rig.ends() == ["closed"]
    assert rig.metrics.get("nora_errors_total", stage="cognition_session") == 0
    assert session_id == 1


def test_the_runner_hook_and_the_evaluators_call_share_one_signature():
    """The evaluator calls `WakeHook` by keyword; the runner's `on_wake` must
    take exactly those keywords (it once took `now_ms` the evaluator never
    sent, and the evaluator sent positionally what the runner took by keyword)."""
    proto = inspect.signature(WakeHook.__call__)
    wanted = {n for n, p in proto.parameters.items() if n != "self"}
    assert wanted == {"unit", "view", "wake_id", "now_ms"}
    have = inspect.signature(CognitionSessionRunner.on_wake).parameters
    for name in wanted:
        assert have[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    required = {n for n, p in have.items() if n != "self" and p.default is inspect.Parameter.empty}
    assert required <= wanted


async def test_a_hook_failure_is_recorded_on_the_session_and_counted_not_only_logged():
    rig = _rig(agent=ScriptedAgent(script=[boom]))
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    assert len(rig.ledger.sessions) == 1
    assert rig.ends() == ["failed"]
    assert "the model loop blew up" in rig.detail()
    assert rig.metrics.get("nora_cognition_session_ended_total", outcome="failed") == 1
    assert rig.metrics.get("nora_errors_total", stage="cognition_session") == 1


async def test_a_failure_before_the_model_is_recorded_failed_and_the_model_never_runs():
    def explode(unit: str, room: Room) -> list[Any]:
        raise RuntimeError("the tool source is down")

    rig = _rig(read_tools_for=explode)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    assert rig.agent.calls == 0
    assert rig.ends() == ["failed"]


# ---------------------------------------------------------------------------
# the watermark does not advance on a session that did not close
# ---------------------------------------------------------------------------

async def test_a_failed_sessions_stimuli_are_not_lost():
    rig = _rig(agent=ScriptedAgent(script=[boom, ok]))
    for _ in range(3):
        await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["failed"]

    # The watermark did NOT move: the wake row exists, but every stimulus is
    # still pending, and the unit's mark is back at zero.
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1, 2, 3]
    assert view.mark is not None and view.mark.through_stimulus_id == 0
    assert view.last_wake is not None  # the brakes still see the wake

    # Inside the cooldown nothing wakes (a failed session backs the unit off) …
    rig.clock.advance(60)
    assert await rig.tick() == []
    # … and after it the SAME three stimuli are the next session's agenda.
    rig.clock.advance(300)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["failed", "closed"]
    _, second, _ = rig.ledger.sessions[1]
    assert second.from_stimulus_id == 1 and second.through_stimulus_id == 3
    assert rig.stimuli.wakes[1].from_stimulus_id == 0
    assert "3 new messages" in rig.agent.prompts[1]


async def test_a_closed_session_does_advance_the_watermark():
    """The control for the test above: the same flow with a session that
    closes its items leaves nothing pending."""
    rig = _rig(agent=ScriptedAgent(script=[close_written]))
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["closed"]
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert view.pending == ()
    assert view.mark is not None and view.mark.through_stimulus_id == 1


async def test_a_timed_out_session_is_cancelled_recorded_timeout_and_strands_nothing():
    async def hang(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        return await agent.block(tools)

    agent = ScriptedAgent(script=[hang])
    rig = _rig(agent=agent, timeout=0.05)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    assert agent.cancelled, "the model loop must be cancelled at the wall clock"
    assert rig.ends() == ["timeout"]
    assert rig.metrics.get("nora_cognition_session_ended_total", outcome="timeout") == 1
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1]


async def test_a_long_session_blocks_a_second_on_the_same_unit_through_a_strong_cue_cut():
    agent = ScriptedAgent(script=[blocked, ok])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    first = await rig.tick()
    await agent.started.wait()
    assert len(first) == 1

    # 200 s in: past the 60 s a would-wake is ASSUMED to run, inside the
    # cooldown, with a strong cue that could cut it. The session is still
    # running, so nothing may start.
    rig.clock.advance(200)
    await rig.say(cues=("datetime",))
    assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == 1
    assert agent.calls == 1
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="single_flight") >= 1

    agent.release.set()
    await rig.settle()
    assert rig.ends() == ["closed"]


async def test_the_cooldown_runs_from_the_sessions_end_not_from_the_wake():
    agent = ScriptedAgent(script=[blocked, ok])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    rig.clock.advance(1000)              # the session ran 1000 s
    agent.release.set()
    await rig.settle()
    await rig.say()
    rig.clock.advance(100)               # 100 s after it ended: inside the cooldown
    assert await rig.tick() == []
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="cooldown") >= 1
    rig.clock.advance(250)
    assert len(await rig.tick()) == 1


async def test_a_session_still_running_after_a_restart_still_holds_its_unit():
    """Single-flight is derived from ROWS, so an evaluator with no memory of
    the task (a restart) still sees the unit in flight."""
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    rig.clock.advance(400)
    await rig.say()

    calls: list[int] = []

    async def noop(*, unit: str, view: UnitView, wake_id: int, now_ms: int) -> None:
        calls.append(wake_id)

    reborn = WakeEvaluator(
        store=rig.stimuli, units=lambda: ("u1",), policy=POLICY, metrics=Metrics(),
        tz=HK, clock=rig.clock, settle_seconds=0, on_wake=noop,
    )
    assert await reborn.tick() == []
    assert calls == []
    agent.release.set()
    await rig.settle()


async def test_a_wake_with_no_outcome_blocks_its_unit_and_no_later_wake_swallows_it():
    """M1: a tracked wake whose session never wrote anything (a kill between
    the wake row and the session row) holds its unit while it is young — so no
    W3 can start inside that window and take W2's range — and past the timeout
    it is expired: its stimuli are pending again and the next wake's session
    SEES them (its range starts below W2's)."""
    from nora.domain.stimuli import NewWake

    rig = _rig(agent=ScriptedAgent(script=[close_written]))
    first = await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["closed"]

    rig.clock.advance(400)
    w2_sid = await rig.say()
    # W2, written by an evaluator that died before its hook ran: no session
    # row, no outcome.
    w2 = await rig.stimuli.record_wake(wake=NewWake(
        unit="u1", mode="live", reason="settled", from_stimulus_id=first,
        through_stimulus_id=w2_sid, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=rig.clock.ms, cut_cooldown=False, at_ms=rig.clock.ms,
        tracked=True,
    ), recorded_at_ms=rig.clock.ms, guard=_guard_of(rig.stimuli))
    assert w2 is not None

    # Inside the timeout: more speech arrives, a strong cue, the cooldown long
    # past. Nothing may wake: W2 is OPEN.
    for _ in range(3):
        rig.clock.advance(250)
        await rig.say(cues=("datetime",))
        assert await rig.tick() == []
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="single_flight") >= 3
    assert rig.agent.calls == 1

    # Past the timeout (and its cooldown) W2 is expired: the next wake starts
    # from W1's mark and its session is handed W2's stimulus too.
    rig.clock.advance(900)
    (w3,) = await rig.tick()
    await rig.settle()
    wake3 = rig.stimuli.wakes[-1]
    assert wake3.id == w3 and wake3.from_stimulus_id == first
    _, session, _ = rig.ledger.sessions[-1]
    assert session.wake_id == w3
    assert session.from_stimulus_id == w2_sid
    assert rig.ends() == ["closed", "closed"]      # W2 never got one; W3 closed
    assert w2 not in rig.stimuli.outcomes


async def test_record_session_raising_keeps_the_stimuli_pending_for_the_next_wake():
    """M1: `record_session` failing (a ledger outage at the first write) is an
    exit before any session row. The wake is recorded `failed` — keyed by the
    wake, which needs no session — and its stimuli go to the next wake."""
    rig = _rig(agent=ScriptedAgent(script=[close_written]))
    rig.ledger.fail_on.add("record_session")
    for _ in range(2):
        await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 0 and rig.ledger.sessions == []
    assert rig.ends() == ["failed"] and "record_session" in rig.detail()
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1, 2]

    rig.ledger.fail_on.clear()
    rig.clock.advance(400)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["failed", "closed"]
    _, session, _ = rig.ledger.sessions[0]
    assert (session.from_stimulus_id, session.through_stimulus_id) == (1, 2)


async def test_an_exit_whose_failure_cannot_be_recorded_waits_out_the_timeout():
    """The same exit with the outcome write ALSO failing: nothing is written,
    so the wake stays OPEN (the unit held) until it expires; then the same
    stimuli go to the next wake. Never consumed unseen."""
    rig = _rig(agent=ScriptedAgent(script=[close_written]))
    rig.ledger.fail_on.add("record_session")
    rig.stimuli.fail_on.add("record_outcome")
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == []
    rig.ledger.fail_on.clear()
    rig.stimuli.fail_on.clear()

    rig.clock.advance(800)
    assert await rig.tick() == []                   # still OPEN
    rig.clock.advance(100 + 300)
    assert len(await rig.tick()) == 1               # expired, cooled, woken
    await rig.settle()
    _, session, _ = rig.ledger.sessions[0]
    assert (session.from_stimulus_id, session.through_stimulus_id) == (1, 1)
    assert rig.ends() == ["closed"]


async def test_a_session_task_cancelled_before_its_first_step_loses_nothing():
    """M1: shutdown cancelling a hook task the loop never started runs none of
    `on_wake`, so nothing records the wake. It is OPEN, then expired, and its
    stimulus is the next wake's."""
    rig = _rig(agent=ScriptedAgent(script=[close_written]))
    await rig.say()
    rig.clock.advance(1)
    (wake_id,) = await rig.tick()
    await rig.ev.cancel_in_flight()               # before the task ever ran
    assert rig.agent.calls == 0 and rig.ledger.sessions == []
    assert wake_id not in rig.stimuli.outcomes

    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert view.pending == ()                     # claimed while OPEN …
    rig.clock.advance(600)
    assert await rig.tick() == []                 # … and the unit held
    rig.clock.advance(300 + 300)
    assert len(await rig.tick()) == 1
    await rig.settle()
    assert rig.agent.calls == 1
    _, session, _ = rig.ledger.sessions[0]
    assert session.from_stimulus_id == 1


async def test_a_close_that_cannot_be_written_is_not_processed_twice():
    """Minor 1: the session ran, and its `closed` write failed. Nothing of the
    close lands (no carry), and the failure write fails too, so the wake stays
    OPEN and then expires. Its stimuli come back ONCE, with no strike — not
    once through carry and once through the watermark, and not with a strike
    for a session that never closed."""
    rig = _rig(agent=ScriptedAgent(script=[ok, ok]))
    await rig.say()
    rig.clock.advance(1)
    rig.stimuli.fail_on.add("record_outcome")
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 1
    assert rig.ends() == [] and rig.stimuli.carries == []
    rig.stimuli.fail_on.clear()

    rig.clock.advance(600)
    assert await rig.tick() == []                 # OPEN: no second session yet
    rig.clock.advance(300 + 300)
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 2
    _, second, _ = rig.ledger.sessions[1]
    (item,) = second.agenda["items"]  # type: ignore[misc]
    assert item["stimulus_ids"] == [1] and item["strike"] == 0  # type: ignore[index]
    assert rig.carried() == [(1, 1, "carried", True)]          # one strike, not two


async def test_more_than_two_units_in_flight_is_refused_on_real_sessions():
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent, units=("u1", "u2", "u3"))
    for unit in ("u1", "u2", "u3"):
        await rig.say(unit=unit)
    rig.clock.advance(1)
    written = await rig.tick()
    await asyncio.sleep(0)
    assert len(written) == 2
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="concurrency") == 1

    # 120 s later, counting 60 s per would-wake would call both sessions over and
    # admit the third. They are still running.
    rig.clock.advance(120)
    assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == 2
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="concurrency") == 2

    agent.release.set()
    await rig.settle()
    rig.clock.advance(400)
    third = await rig.tick()
    await rig.settle()
    assert [rig.stimuli.wakes[-1].unit] == ["u3"] and len(third) == 1


# ---------------------------------------------------------------------------
# strikes and deferred items
# ---------------------------------------------------------------------------

async def test_items_past_max_items_come_back_on_the_next_agenda_with_a_strike():
    async def close_all(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        for i in (0, 1):
            await by["close"](item_index=i, reason="no_change")
        return CognitionRunResult(stop_reason="end_turn")

    agent = ScriptedAgent(script=[close_all, ok])
    rig = _rig(agent=agent, max_items=2)
    for room in ("room-a", "room-b", "room-c"):
        await rig.say(room=room)
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    _, first, _ = rig.ledger.sessions[0]
    assert first.agenda["deferred_stimulus_ids"] == [3]
    assert len(first.agenda["items"]) == 2  # type: ignore[arg-type]
    assert rig.carried() == [(3, 1, "carried", False)]

    # The deferred one is pending again, and wakes on its own (it was never seen).
    rig.clock.advance(400)
    await rig.tick()
    await rig.settle()
    _, second, _ = rig.ledger.sessions[1]
    (item,) = second.agenda["items"]  # type: ignore[misc]
    assert item["stimulus_ids"] == [3] and item["strike"] == 1  # type: ignore[index]
    assert item["conversation_id"] == "room-c"  # type: ignore[index]
    assert "room-c" not in rig.agent.prompts[1]  # the prompt says the label, never the id


async def test_an_unclosed_item_returns_with_a_strike_and_is_struck_at_max_strikes():
    rig = _rig(max_strikes=2)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert [(c.reason, c.strike) for _, c, _ in rig.ledger.closes] == [("waiting", 1)]

    # Waiting stimuli ride the next wake and never start one.
    rig.clock.advance(7200)
    assert await rig.tick() == []

    # A new message wakes the unit. The returning item is on the agenda WITH its
    # strike and the fresh message is its OWN item at strike 0 (minor 7: a
    # strike is the item's; the fresh one does not inherit it). Both are left
    # open: code strikes the returning one out and gives the fresh one its
    # first strike.
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    _, second, _ = rig.ledger.sessions[1]
    items = sorted(
        (i["stimulus_ids"], i["strike"]) for i in second.agenda["items"]  # type: ignore[union-attr,index]
    )
    assert items == [([1], 1), ([2], 0)]
    assert [i.attrib.get("returning") for i in agenda_items(rig.agent.prompts[1])] == [
        "left open by 1 earlier session", None,
    ]
    assert sorted((c.reason, c.strike) for _, c, _ in rig.ledger.closes) == [
        ("struck", 2), ("waiting", 1), ("waiting", 1),
    ]
    states = {c.stimulus_id: (c.state, c.strike) for _, _, c in rig.stimuli.carries}
    assert states == {1: ("struck", 2), 2: ("carried", 1)}
    # Struck is closed for good: the fresh one is still carried, alone.
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [2] and dict(view.carry) == {2: 1}
    assert rig.metrics.get("nora_cognition_carry_total", state="struck") == 1


async def test_a_model_close_waiting_takes_a_strike_like_an_unclosed_item():
    async def wait_it_out(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        assert (await by["close"](item_index=0, reason="waiting"))["status"] == "ok"
        assert (await by["close"](item_index=0, reason="written"))["reason"] == "already_closed"
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[wait_it_out]))
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert [(c.reason, c.strike) for _, c, _ in rig.ledger.closes] == [("waiting", 1)]


# ---------------------------------------------------------------------------
# MINOR 6: a room is named, never guessed; a read tool is on the allowlist
# ---------------------------------------------------------------------------



async def test_a_read_tool_off_the_allowlist_is_refused_and_the_session_fails_closed():
    async def send_reply(text: str) -> dict[str, object]:
        return {"status": "ok"}

    rig = _rig(read_tools_for=lambda unit, room: [send_reply],
               read_only_tools=frozenset({"now"}))
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 0
    assert rig.ends() == ["failed"]
    assert "send_reply" in rig.detail() and "read-only allowlist" in rig.detail()


async def test_no_allowlist_means_no_read_tool_and_an_allowlisted_name_may_not_shadow_a_verb():
    async def now() -> str:
        return "t"

    async def note_create(title: str) -> dict[str, object]:
        return {}

    rig = _rig(read_tools_for=lambda unit, room: [now], read_only_tools=frozenset())
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["failed"]

    shadow = _rig(
        read_tools_for=lambda unit, room: [note_create],
        read_only_tools=frozenset({"note_create"}),
    )
    await shadow.say()
    shadow.clock.advance(1)
    await shadow.tick()
    await shadow.settle()
    assert shadow.ends() == ["failed"]
    assert "shadows a session verb" in shadow.detail()

    good = _rig(read_tools_for=lambda unit, room: [now], read_only_tools=frozenset({"now"}))
    await good.say()
    good.clock.advance(1)
    await good.tick()
    await good.settle()
    assert good.ends() == ["closed"]
    assert "now" in [getattr(t, "__name__", None) for t in good.agent.tools_seen[0]]


def test_the_guard_error_is_a_value_error():
    assert issubclass(NotReadOnlyToolError, ValueError)


# ---------------------------------------------------------------------------
# MINOR 7: shutdown cancels what is in flight and records it
# ---------------------------------------------------------------------------

async def test_shutdown_cancels_an_in_flight_session_and_records_it_cancelled():
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()

    await rig.runner.shutdown()
    await rig.settle()

    assert agent.cancelled
    assert rig.ends() == ["cancelled"]
    assert rig.metrics.get("nora_cognition_session_ended_total", outcome="cancelled") == 1
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1]


async def test_the_evaluator_can_cancel_its_in_flight_sessions_too():
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    await rig.ev.cancel_in_flight()
    assert rig.ends() == ["cancelled"]


async def test_a_hook_that_raises_synchronously_does_not_abort_the_tick_for_other_units():
    seen: list[str] = []

    def hook(*, unit: str, view: UnitView, wake_id: int, now_ms: int) -> Awaitable[None]:
        seen.append(unit)
        if unit == "u1":
            raise TypeError("a hook that cannot be called")

        async def run() -> None:
            return None
        return run()

    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    clock = Clock(T)
    metrics = Metrics()
    ev = WakeEvaluator(
        store=stimuli, units=lambda: ("u1", "u2"), policy=POLICY, metrics=metrics, tz=HK,
        clock=clock, settle_seconds=0, on_wake=hook,
    )
    for unit in ("u1", "u2"):
        await stimuli.append(stimulus=NewStimulus(
            unit=unit, kind="message", item="message", ref=f"messages:{unit}", at_ms=T,
            platform="signal", conversation_id="r",
        ), recorded_at_ms=T)
    clock.advance(1)
    written = await ev.tick()
    await ev.drain()
    assert len(written) == 2
    assert seen == ["u1", "u2"]
    assert metrics.get("nora_errors_total", stage="cognition_session") == 1


# ---------------------------------------------------------------------------
# MINOR 7: the pace verbs' read-check-write is one critical section
# ---------------------------------------------------------------------------

async def test_two_concurrent_wake_at_calls_cannot_both_pass_the_pending_cap():
    answers: list[dict[str, object]] = []

    async def two_at_once(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        answers.extend(await asyncio.gather(
            by["wake_at"](subject_rows=["event:1"], at=iso(T + 3_600_000), why="a"),
            by["wake_at"](subject_rows=["event:2"], at=iso(T + 3_600_000), why="b"),
        ))
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[two_at_once]), max_self_wakes=1)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    statuses = sorted(str(a["status"]) for a in answers)
    assert statuses == ["ok", "refused"]
    assert next(a for a in answers if a["status"] == "refused")["reason"] == "too_many_pending"
    assert len(await rig.ledger.pending_self_wakes(unit="u1", since_ms=0)) == 1
    assert len([p for _, p, _ in rig.ledger.paces if p.outcome == "recorded"]) == 1


async def test_wake_at_is_capped_per_unit_per_day_and_yesterdays_do_not_count():
    answers: list[dict[str, object]] = []
    holder: list[Rig] = []

    async def three(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        for n in range(3):
            answers.append(await by["wake_at"](
                subject_rows=[f"event:{n}"], at=iso(holder[0].clock.ms + 3_600_000), why="w",
            ))
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[three, three]), max_self_wakes=10,
               max_self_wakes_per_day=2)
    holder.append(rig)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert [a["status"] for a in answers] == ["ok", "ok", "refused"]
    assert answers[2]["reason"] == "daily_cap"

    # A day later the rolling window has emptied.
    answers.clear()
    await rig.say()
    rig.clock.advance(25 * 3600)
    await rig.tick()
    await rig.settle()
    assert [a["status"] for a in answers][:2] == ["ok", "ok"]


# ---------------------------------------------------------------------------
# MINOR 5: a cancelled self-wake never wakes; a moved one wakes once
# ---------------------------------------------------------------------------

def _wake_script(rig_holder: list[Rig]) -> tuple[list[dict[str, object]], Behaviour, Behaviour]:
    """Session 1 sets a self-wake an hour out; session 2 (built by the caller)
    cancels or moves it."""
    answers: list[dict[str, object]] = []

    async def set_it(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        answers.append(await by["wake_at"](
            subject_rows=["event:1"], at=iso(rig_holder[0].clock.ms + 3_600_000), why="venue",
        ))
        return CognitionRunResult(stop_reason="end_turn")

    async def cancel_it(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        answers.append(await by["wake_cancel"](wake_id="ref1", why="settled"))
        return CognitionRunResult(stop_reason="end_turn")

    return answers, set_it, cancel_it


async def test_a_cancelled_self_wake_never_fires_and_an_uncancelled_one_does():
    holder: list[Rig] = []
    answers, set_it, cancel_it = _wake_script(holder)
    rig = _rig(agent=ScriptedAgent(script=[set_it, cancel_it, ok]))
    holder.append(rig)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert answers[0]["status"] == "ok"

    rig.clock.advance(400)
    await rig.say()
    await rig.tick()
    await rig.settle()
    assert answers[1] == {"status": "ok"}
    wakes_before = len(rig.stimuli.wakes)

    rig.clock.advance(4 * 3600)               # well past the hour it was set for
    assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == wakes_before
    assert await rig.ledger.pending_self_wakes(unit="u1", since_ms=0) == ()

    # CONTROL: the same flow with no cancel wakes on `self`.
    holder2: list[Rig] = []
    _, set2, _ = _wake_script(holder2)
    rig2 = _rig(agent=ScriptedAgent(script=[set2, ok]))
    holder2.append(rig2)
    await rig2.say()
    rig2.clock.advance(1)
    await rig2.tick()
    await rig2.settle()
    rig2.clock.advance(3600)                  # just past the hour it was set for
    assert len(await rig2.tick()) == 1
    assert rig2.stimuli.wakes[-1].reason == "self"


async def test_a_moved_self_wake_fires_once_at_the_new_time_and_never_at_the_old():
    holder: list[Rig] = []
    _answers, set_it, _cancel = _wake_script(holder)

    async def move_it(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        moved = await by["wake_move"](
            wake_id="ref1", at=iso(holder[0].clock.ms + 3 * 3600_000), why="pushed back",
        )
        assert moved == {"status": "ok"}, moved
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[set_it, move_it, ok]))
    holder.append(rig)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    rig.clock.advance(400)
    await rig.say()
    await rig.tick()
    await rig.settle()
    wakes_before = len(rig.stimuli.wakes)

    rig.clock.ms = T + 1 + 3600 + 1_000       # just past the ORIGINAL time
    assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == wakes_before

    rig.clock.ms = T + 1 + 400 * 1000 + 3 * 3600_000 + 1_000   # just past the NEW time
    assert len(await rig.tick()) == 1
    await rig.settle()
    assert rig.stimuli.wakes[-1].reason == "self"
    fired = [p for _, p, _ in rig.ledger.paces if p.transition == "fired"]
    assert [p.wake_ref for p in fired] == ["ref1"]
    # and it is counted (`nora_self_wake_total`), once per move/create/fire it saw
    assert rig.metrics.get("nora_self_wake_total", outcome="fired") == 1
    assert rig.metrics.get("nora_self_wake_total", outcome="created") == 1
    assert rig.metrics.get("nora_self_wake_total", outcome="moved") == 1

    after_self = len(rig.stimuli.wakes)
    rig.clock.advance(3600)
    # The self wake claimed only its own stimulus (D2), so the second message it
    # found pending is still pending and wakes on its own once settled; what must
    # not happen is a second self wake.
    await rig.tick()
    await rig.settle()
    assert [w.reason for w in rig.stimuli.wakes[after_self:]] in ([], ["settled"])
    assert await rig.tick() == []             # and nothing more, exactly once
    assert await rig.ledger.pending_self_wakes(unit="u1", since_ms=0) == ()


@pytest.mark.parametrize("unit", ["u1"])
async def test_a_superseded_self_wake_is_retracted_so_only_the_replacement_fires(unit):
    holder: list[Rig] = []

    async def replace_it(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        await by["wake_at"](subject_rows=["event:1"], at=iso(holder[0].clock.ms + 3_600_000),
                            why="first")
        await by["wake_at"](subject_rows=["event:1"], at=iso(holder[0].clock.ms + 7_200_000),
                            why="second")
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[replace_it, ok]))
    holder.append(rig)
    await rig.say(unit=unit)
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()

    rig.clock.advance(3600 + 60)              # the FIRST one's time
    assert await rig.tick() == []
    rig.clock.advance(3600)                   # the replacement's
    assert len(await rig.tick()) == 1
    await rig.settle()
    live = await rig.ledger.pending_self_wakes(unit=unit, since_ms=0)
    assert live == ()                          # fired, and the superseded one is gone


# ---------------------------------------------------------------------------
# M2: a cancelled or moved self-wake never fires through carry
# ---------------------------------------------------------------------------

def _carried_self_wake_rig(
    second: Callable[[Callable[[str], Any], Rig], Awaitable[None]],
) -> tuple[Rig, list[Rig]]:
    """Session 1 closes its item and sets self-wake `ref1` an hour out. An
    hour later a strong-cue message and the due self-wake wake session 2 with
    `max_items=1`: the message outranks the self-wake, so the self-wake is
    DEFERRED — carried to the next agenda, not fired — and session 2 runs
    `second` on it (cancel or move) before it closes."""
    holder: list[Rig] = []

    def tools_of(tools: list[Any]) -> Callable[[str], Any]:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        return lambda name: by[name]

    async def first(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        tool = tools_of(tools)
        await tool("close")(item_index=0, reason="no_change")
        answer = await tool("wake_at")(
            subject_rows=["event:1"], at=iso(holder[0].clock.ms + 3_600_000), why="venue",
        )
        assert answer["status"] == "ok", answer
        return CognitionRunResult(stop_reason="end_turn")

    async def then(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        tool = tools_of(tools)
        await second(tool, holder[0])
        await tool("close")(item_index=0, reason="no_change")
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[first, then, close_written]), max_items=1)
    holder.append(rig)
    return rig, holder


async def _defer_the_self_wake(rig: Rig) -> None:
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    rig.clock.advance(3600)
    await rig.say(cues=("datetime",))
    await rig.tick()
    await rig.settle()
    _, second, _ = rig.ledger.sessions[1]
    assert [i["kind"] for i in second.agenda["items"]] == ["message"]  # type: ignore[union-attr,index]
    self_sid = next(s.id for s in rig.stimuli.stimuli if s.kind == "self")
    assert second.agenda["deferred_stimulus_ids"] == [self_sid]
    assert any(c.stimulus_id == self_sid and c.state == "carried"
               for _, _, c in rig.stimuli.carries)


async def test_a_self_wake_cancelled_while_carried_never_fires_through_carry():
    async def cancel(tool: Callable[[str], Any], rig: Rig) -> None:
        assert (await tool("wake_cancel")(wake_id="ref1", why="settled")) == {"status": "ok"}

    rig, _ = _carried_self_wake_rig(cancel)
    await _defer_the_self_wake(rig)
    wakes_before = len(rig.stimuli.wakes)
    for _ in range(4):
        rig.clock.advance(3600)
        assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == wakes_before
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert view.pending == () and dict(view.carry) == {}


async def test_a_self_wake_moved_while_carried_fires_once_at_its_new_time_only():
    async def move(tool: Callable[[str], Any], rig: Rig) -> None:
        answer = await tool("wake_move")(
            wake_id="ref1", at=iso(rig.clock.ms + 3 * 3_600_000), why="pushed back",
        )
        assert answer == {"status": "ok"}, answer

    rig, _ = _carried_self_wake_rig(move)
    await _defer_the_self_wake(rig)
    moved_to = next(s.due_ms for s in reversed(rig.stimuli.stimuli) if s.kind == "self")
    assert moved_to is not None
    wakes_before = len(rig.stimuli.wakes)
    rig.clock.advance(3600)                       # cooldown long past, before the new time
    assert await rig.tick() == []
    assert len(rig.stimuli.wakes) == wakes_before
    rig.clock.ms = moved_to + 1000
    assert len(await rig.tick()) == 1
    await rig.settle()
    assert rig.stimuli.wakes[-1].reason == "self"
    rig.clock.advance(3600)
    assert await rig.tick() == []


# ---------------------------------------------------------------------------
# Minor 8: the per-day cap reads the clock, not the session's start
# ---------------------------------------------------------------------------

async def test_the_per_day_cap_counts_back_from_the_call_not_from_the_sessions_start():
    """Session 1 sets two self-wakes. Session 2 starts 23 h later and makes its
    `wake_at` call 2 h into its run (the clock moves inside the session). A
    day back from the CALL excludes session 1's two; a day back from the
    session's frozen start would count them and refuse."""
    answers: list[dict[str, object]] = []
    holder: list[Rig] = []

    async def two(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        for n in range(2):
            answers.append(await by["wake_at"](
                subject_rows=[f"event:{n}"], at=iso(holder[0].clock.ms + 3 * 86_400_000),
                why="w",
            ))
        return CognitionRunResult(stop_reason="end_turn")

    async def late(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        holder[0].clock.advance(2 * 3600)
        answers.append(await by["wake_at"](
            subject_rows=["event:9"], at=iso(holder[0].clock.ms + 3_600_000), why="w",
        ))
        return CognitionRunResult(stop_reason="end_turn")

    rig = _rig(agent=ScriptedAgent(script=[two, late]), max_self_wakes=10,
               max_self_wakes_per_day=2)
    holder.append(rig)
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert [a["status"] for a in answers] == ["ok", "ok"]

    rig.clock.advance(23 * 3600)
    await rig.say()
    await rig.tick()
    await rig.settle()
    assert answers[2] == {"status": "ok", "wake_id": "ref3"}, answers[2]


# ---------------------------------------------------------------------------
# M1 / minor 6: refused at startup, not discovered per wake
# ---------------------------------------------------------------------------

def test_a_unit_with_no_home_room_is_refused_when_the_runner_is_built():
    from nora.app.cognition_session import MissingHomeRoomError

    with pytest.raises(MissingHomeRoomError, match="u2"):
        CognitionSessionRunner(**_runner_kwargs(units=("u1", "u2"), home_rooms={"u1": HOME}))
    CognitionSessionRunner(**_runner_kwargs(units=("u1",), home_rooms={"u1": HOME}))


def test_a_session_budget_that_can_outlive_its_wake_is_refused():
    from nora.app.cognition_session import SESSION_TIMEOUT_MARGIN_SECONDS

    edge = 900 - SESSION_TIMEOUT_MARGIN_SECONDS
    CognitionSessionRunner(**_runner_kwargs(budget_timeout=edge, session_timeout=900))
    for too_long in (edge + 1, 900, 1200):
        with pytest.raises(ValueError, match="session_timeout_seconds"):
            CognitionSessionRunner(**_runner_kwargs(budget_timeout=too_long, session_timeout=900))


def _guard_of(stimuli: Any) -> Any:
    """The guard a writer that just read the unit would hold: its newest wake
    and that wake's outcome (a tracked wake is refused without one)."""
    from nora.domain.stimuli import WakeGuard

    last = stimuli.wakes[-1] if stimuli.wakes else None
    row = None if last is None else stimuli.outcomes.get(last.id)
    return WakeGuard(
        last_wake_id=None if last is None else last.id,
        last_outcome=None if row is None else row.outcome,
    )


def _runner_kwargs(
    *, units: tuple[str, ...] = ("u1",), home_rooms: dict[str, Room] | None = None,
    budget_timeout: float = 30.0, session_timeout: float = 900.0,
) -> dict[str, Any]:
    ledger = InMemoryCognitionStore()
    return {
        "store": ledger, "stimulus_store": InMemoryStimulusStore(ledger=ledger),
        "agent_runner": ScriptedAgent(), "home_rooms": home_rooms or {"u1": HOME},
        "read_tools_for": lambda unit, rooms: [], "skill_text_for": lambda kinds: "",
        "grounder": EmptyGrounder(),
        "doctrine_text": "D", "doctrine_sha256": None, "open_note_titles": _const(()),
        "open_events": _const(()), "prior_standing": _const(None),
        "budget": AdaptorBudget(turns=1, total_tokens=1, output_tokens=1,
                                timeout_seconds=budget_timeout),
        "cooldown_seconds": 300.0, "units": units,
        "session_timeout_seconds": session_timeout, "max_items": 12, "max_strikes": 3,
    }


async def test_a_wake_for_a_unit_with_no_home_room_is_recorded_failed_not_consumed():
    """Belt and braces for a unit list that changed after startup: the wake is
    recorded `failed` (its stimuli stay pending), never returned from silently."""
    rig = _rig(units=("u1",))
    rig.runner._home_rooms = {}  # the unit's room vanished after startup
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 0
    assert rig.ends() == ["failed"] and "no home room" in rig.detail()
    view = await rig.stimuli.view(unit="u1", mode="live", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1]


async def test_a_second_cancel_cannot_interrupt_the_cancelled_outcome_write():
    """`shutdown` and the evaluator's `cancel_in_flight` both cancel one
    task. The second cancel lands while the first is writing `cancelled`; the
    write still lands."""
    agent = ScriptedAgent(script=[blocked])
    rig = _rig(agent=agent)
    real = rig.stimuli.record_outcome
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def slow(**kw: Any) -> Any:
        entered.set()
        await proceed.wait()
        return await real(**kw)

    rig.stimuli.record_outcome = slow  # type: ignore[method-assign]
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await agent.started.wait()
    (task,) = tuple(rig.ev._background)
    task.cancel()
    await entered.wait()
    task.cancel()                                  # the second cancel
    await asyncio.sleep(0)
    proceed.set()
    await rig.runner.shutdown()
    await rig.settle()
    assert rig.ends() == ["cancelled"]


# ---------------------------------------------------------------------------
# The guarded wake write and a close that fails on its own
# ---------------------------------------------------------------------------

async def test_a_late_close_between_the_read_and_the_write_refuses_the_new_wake():
    """The evaluator reads W1 as EXPIRED (no outcome, past the timeout) and
    decides a new wake over W1's stimuli. If W1's session closes before the
    write, the decision is stale: the guarded write refuses it, so W1's
    stimuli are not handed to a second session."""
    from nora.domain.ports import NewWakeOutcome
    from nora.domain.stimuli import NewWake

    rig = _rig()
    sid = await rig.say()
    rig.clock.advance(1)
    w1 = await rig.stimuli.record_wake(wake=NewWake(
        unit="u1", mode="live", reason="settled", from_stimulus_id=0,
        through_stimulus_id=sid, stimulus_count=1, eligible_count=1,
        oldest_pending_ms=T, cut_cooldown=False, at_ms=rig.clock.ms, tracked=True,
    ), recorded_at_ms=rig.clock.ms, guard=_guard_of(rig.stimuli))
    assert w1 is not None
    rig.clock.advance(900 + 300 + 1)

    real_view = rig.stimuli.view

    async def view_then_close(**kw: Any) -> Any:
        view = await real_view(**kw)
        if w1 not in rig.stimuli.outcomes:          # W1's session lands, late
            await rig.stimuli.record_outcome(outcome=NewWakeOutcome(
                wake_id=w1, unit="u1", outcome="closed", at_ms=rig.clock.ms,
            ), recorded_at_ms=rig.clock.ms)
        return view

    rig.stimuli.view = view_then_close  # type: ignore[method-assign]
    assert await rig.tick() == []
    assert [w.id for w in rig.stimuli.wakes] == [w1]
    assert rig.agent.calls == 0
    assert rig.metrics.get("nora_cognition_wake_deferred_total", brake="single_flight") == 1


async def test_a_failed_close_is_recorded_failed_and_its_stimuli_return_with_no_strike():
    """The `closed` write fails (and only it): the session records `failed`
    at once — not left OPEN for the timeout — and none of its carries land,
    so the next session after the cooldown sees the item at strike 0."""
    rig = _rig(agent=ScriptedAgent(script=[ok, ok]))
    real = rig.stimuli.record_outcome

    async def closed_fails(**kw: Any) -> Any:
        if kw["outcome"].outcome == "closed" and rig.agent.calls == 1:
            raise RuntimeError("the close transaction failed")
        return await real(**kw)

    rig.stimuli.record_outcome = closed_fails  # type: ignore[method-assign]
    await rig.say()
    rig.clock.advance(1)
    await rig.tick()
    await rig.settle()
    assert rig.ends() == ["failed"] and "close transaction" in rig.detail()
    assert rig.stimuli.carries == []

    rig.clock.advance(400)                       # the cooldown, not the timeout
    await rig.tick()
    await rig.settle()
    assert rig.agent.calls == 2
    _, second, _ = rig.ledger.sessions[1]
    (item,) = second.agenda["items"]  # type: ignore[misc]
    assert item["strike"] == 0  # type: ignore[index]
    assert rig.ends() == ["failed", "closed"]
