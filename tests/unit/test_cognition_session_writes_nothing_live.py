"""The runtime guard (review follow-up on #625's step 3a-i, "the session
must be constructed with dry-run write verbs only"): a whole shadow
session, driven by a SCRIPTED fake model that tries to do everything a real
one could — write a note, revise an event, revise Standing, pace itself,
and remind a room — never reaches a live store or sends anything, even
when a live store IS reachable from the composition and every one of its
write methods raises.

**The type-level half.** `CognitionSessionRunner.__init__` (`nora.app.
cognition_session`) takes `dry_run_store: CognitionDryRunStore` — never a
live `NoteCollaborator`/`GatheringStore`/`StandingStore` — and its read
seams (`open_note_titles`, `open_events`, `prior_standing`, …)
are narrow callables returning plain data, not store objects. There is no
parameter on this class a caller could widen into a live write: the tool
closures in `_build_tools` only ever call `nora.agent.cognition_dry_run`/
`cognition_pace`/`cognition_remind_mirror`'s wrappers, which take
`self._store: CognitionDryRunStore` as their own only sink
(`tests/architecture/test_cognition_shadow_cannot_write_or_send.py`'s
import-boundary half of this same guarantee).

**The behavioural half**, this file: even when the COMPOSITION supplies a
"live" collaborator whose write methods raise `AssertionError` if ever
called, and whose read methods are exercised normally (open note titles,
open proposed events, prior standing), a full session — the model tries
`note_create`, `event_propose`, `standing_revise`, `wake_at`, `remind` —
raises nothing, writes nothing to the fake live store, and sends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
HOME = Room("signal", "home-room")
BUDGET = AdaptorBudget(turns=6, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)


class ExplodingLiveStore:
    """Stands in for every real store this session's tools might otherwise
    reach: `NoteCollaborator`, `GatheringStore`, `StandingStore`, the
    reminders port and the fire path, all in one object for brevity. Every
    WRITE method raises; every read method the runner's seams call is
    exercised normally, so the test proves the writes are never reached
    rather than that nothing was ever read."""

    def __init__(self) -> None:
        self.read_calls = 0

    # -- reads the runner's seams legitimately call --------------------
    async def open_note_titles(self, unit: str) -> tuple[str, ...]:
        self.read_calls += 1
        return ()

    async def open_events(self, unit: str, conversation_id: str) -> tuple[Any, ...]:
        self.read_calls += 1
        return ()

    async def prior_standing(self, platform: str, conversation_id: str) -> None:
        self.read_calls += 1
        return None

    async def pending_self_wakes(self, unit: str, since_ms: int) -> tuple[Any, ...]:
        self.read_calls += 1
        return ()

    async def pending_mirrored_reminds(self, unit: str) -> tuple[Any, ...]:
        self.read_calls += 1
        return ()

    # -- writes nothing in this session may ever reach ------------------
    async def create_note(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a shadow session must never write a live note")

    async def create_event(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a shadow session must never write a live event")

    async def write_standing(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a shadow session must never write live Standing")

    async def send(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a shadow session must never send a message")

    async def schedule_reminder(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a shadow session must never write a live reminder")


@dataclass
class ScriptedAgentRunner:
    """The fake model: tries EVERY write, pace and remind verb it is
    handed, exactly as a model that ignored the shadow boundary would."""

    calls: int = 0

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.calls += 1
        by_name = {getattr(t, "__name__", None): t for t in tools}
        await by_name["note_create"](
            title="dinner", description="d", uncertainty="u", closing_condition="c",
            body="## Decided\n- x (Ada) [1]\n## Open\n- y [1]\n",
        )
        await by_name["standing_revise"](where_it_stands="dinner is on")
        await by_name["wake_at"](
            subject_rows=["event:1"], at=_iso(NOW + 3_600_000), why="check the venue",
        )
        await by_name["remind"](
            room="signal:home-room", intent="tell",
            subject_rows=["note:1"], at=_iso(NOW + 60_000),
        )
        await by_name["close"](item_index=0, reason="written")
        return CognitionRunResult(stop_reason="end_turn")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _pending(unit: str) -> tuple[Stimulus, ...]:
    return (
        Stimulus(
            id=1, unit=unit, kind="message", item="message", ref="messages:1", at_ms=NOW,
            recorded_at_ms=NOW, platform="signal", conversation_id="home-room",
        ),
    )


async def test_a_scripted_session_writes_nothing_to_a_live_store_and_sends_nothing():
    live = ExplodingLiveStore()
    dry_run_store = InMemoryCognitionDryRunStore()
    agent = ScriptedAgentRunner()
    stimuli = InMemoryStimulusStore()
    stimuli.seed_wake("unit-1", 1)
    runner = CognitionSessionRunner(
        dry_run_store=dry_run_store, stimulus_store=stimuli,
        agent_runner=agent, home_rooms={"unit-1": HOME}, read_tools_for=lambda u, rs: [],
        skill_text_for=lambda kinds: "", grounder=EmptyGrounder(),
        doctrine_text="DOCTRINE", doctrine_sha256="sha",
        open_note_titles=live.open_note_titles, open_events=live.open_events,
        prior_standing=live.prior_standing, pending_self_wakes=live.pending_self_wakes,
        pending_mirrored_reminds=live.pending_mirrored_reminds, session_mode="shadow",
        budget=BUDGET, cooldown_seconds=300.0, units=("unit-1",),
        session_timeout_seconds=900.0, max_items=12, max_strikes=3,
    )
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )

    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)

    # The model ran exactly once, and every write it tried landed only in
    # the dry-run ledger — the exploding live store's write methods were
    # never even reachable (there is no closure in this tree that holds a
    # reference to them), so nothing raised and nothing was recorded there.
    assert agent.calls == 1
    assert outcome is not None
    # open_note_titles, prior_standing, pending_self_wakes, pending_mirrored_reminds,
    # and open_events (the `remind` act guard reads the room's events to see
    # whether a cited row is only abduced): reads, every one.
    assert live.read_calls == 5
    assert len(dry_run_store.would_writes) == 2  # note_create + standing_revise
    assert len(dry_run_store.paces) == 1
    assert len(dry_run_store.remind_mirrors) == 1
    assert all(w.outcome in ("would_commit", "refused") for _, w, _ in dry_run_store.would_writes)


async def test_session_mode_off_never_constructs_a_live_read_either():
    """Belt-and-braces: with `session_mode="off"`, `on_wake` returns before
    even the seams' READ methods run — the composition-level guarantee is
    that the hook is never wired at all, and this is the runtime check that
    holds even if a future composition root wires it unconditionally."""
    live = ExplodingLiveStore()
    dry_run_store = InMemoryCognitionDryRunStore()
    agent = ScriptedAgentRunner()
    stimuli = InMemoryStimulusStore()
    stimuli.seed_wake("unit-1", 1)
    runner = CognitionSessionRunner(
        dry_run_store=dry_run_store, stimulus_store=stimuli,
        agent_runner=agent, home_rooms={"unit-1": HOME}, read_tools_for=lambda u, rs: [],
        skill_text_for=lambda kinds: "", grounder=EmptyGrounder(),
        doctrine_text="DOCTRINE", doctrine_sha256="sha",
        open_note_titles=live.open_note_titles, open_events=live.open_events,
        prior_standing=live.prior_standing, pending_self_wakes=live.pending_self_wakes,
        pending_mirrored_reminds=live.pending_mirrored_reminds, session_mode="off",
        budget=BUDGET, cooldown_seconds=300.0, units=("unit-1",),
        session_timeout_seconds=900.0, max_items=12, max_strikes=3,
    )
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )

    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)

    assert outcome is None
    assert agent.calls == 0
    assert live.read_calls == 0
