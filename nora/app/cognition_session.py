"""The Cognition session loop — the async half `nora.domain.cognition_session`'s
own docstring names as "deferred past 3a-i" (design/cognition-loop/
30-cognition.md; migration step 3a-ii).

**What this module wires, and nothing else.** `[cognition] session_mode`
governs it (`nora.config.CognitionConfig`): `"off"` means this module is
never called at all — the composition root simply does not register
`CognitionSessionRunner.on_wake` as the wake evaluator's hook, so `"off"`
makes literally zero model calls by construction, not by a runtime check
inside a call nobody skipped (`tests/unit/test_cognition_session_runner.py`
still asserts the belt-and-braces check below, in case a future composition
root wires the hook unconditionally). `"shadow"` runs one session per
would-wake the evaluator (`nora.app.wake.WakeEvaluator`) records, respecting
single-flight, the cooldown and the global concurrency bound — ALL THREE
ALREADY ENFORCED by the evaluator's own brakes (`nora.domain.stimuli.decide`,
step 1): a would-wake IS treated as an occupied session slot for
`session_seconds`, so this module adds no second copy of that arithmetic. It
only has to run when told to, and to never run when it should not.

**What a session gets** (30-cognition.md §1-§2): the situation and agenda
(`nora.domain.cognition_session.build_situation`, already pure and already
shipped), the read tools its rooms permit (built by the CALLER — the
composition root, from the existing `nora.agent.dreamer` tool-source
machinery every other adaptor already uses; this module does not rebuild
disclosure, lineage or withdrawal, it is handed the finished tool list),
skills injected per agenda kind present, and ONLY the dry-run write/pace/
remind-mirror verbs as tools — see `_build_verb_tools` below, and
`tests/behavioral/test_cognition_session_writes_nothing_live.py` for the
runtime guard this buys.

**An agenda with no items is no session** (30-cognition.md §1): `on_wake`
returns before building any tool or calling `agent_runner` at all. The ONE
call site in this whole module that can make a model call is the single
`await self._agent_runner.run(...)` inside `_run_session` — everything
before it is pure bookkeeping, which is what makes "off, or an empty
agenda, makes zero model calls" a property of the control flow rather than
a claim about what a mock was not called with.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from nora.agent.cognition_dry_run import (
    dry_run_event_propose,
    dry_run_event_revise,
    dry_run_event_set_status,
    dry_run_note_create,
    dry_run_note_update,
    dry_run_standing_revise,
)
from nora.agent.cognition_pace import dry_run_wake_at, dry_run_wake_cancel, dry_run_wake_move
from nora.agent.cognition_remind_mirror import (
    dry_run_remind,
    dry_run_remind_cancel,
    dry_run_remind_move,
)
from nora.domain.cognition_pace import PendingSelfWake
from nora.domain.cognition_remind import PendingMirroredRemind
from nora.domain.cognition_session import (
    AgendaItem,
    CloseOutcome,
    Room,
    build_situation,
    close_or_strike,
)
from nora.domain.gatherings import Gathering
from nora.domain.note_body import Shown
from nora.domain.ports import (
    CognitionDryRunStore,
    NewCognitionSession,
    NewCognitionSessionClose,
    StimulusStore,
)
from nora.domain.standing import Standing
from nora.domain.stimuli import UnitView

log = logging.getLogger("nora.app.cognition_session")

#: `established_by`'s prefix for every Register write cognition makes in
#: shadow — code-stamped provenance, never taken from a model argument
#: (the same reason `nora.agent.tools.TAG_PATH_PREFIX` exists for the tag
#: path: "who wrote it" is not something the writer gets to assert).
COGNITION_PATH_PREFIX = "cognition:"


def default_mint_ref() -> str:
    return uuid.uuid4().hex[:16]


@dataclass(frozen=True)
class AdaptorBudget:
    """The Cognition session's own ceiling — the same shape
    `nora.agent.dreamer.AdaptorBudget` already is for every other adaptor,
    restated here so this module never imports `nora.agent.dreamer` (see
    `tests/architecture/test_cognition_shadow_cannot_write_or_send.py`'s
    forbidden-prefix list, which already names that module)."""

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class CognitionRunResult:
    """What one model call returned. `stop_reason` is Strands' own (R-23);
    `None` on a run this module never made (belt-and-braces callers only)."""

    stop_reason: str | None = None


class CognitionAgentRunner(Protocol):
    """The test seam (CLAUDE.md's policy: a constructor argument, never an
    `_fn` hook). `nora.app.cognition_agent_runner.LiveCognitionAgentRunner`
    is the production implementation, wrapping `nora.agent.factory.
    create_dream_agent`; `tests/behavioral/
    test_cognition_session_writes_nothing_live.py`'s `ScriptedAgentRunner`
    drives a fixed sequence of tool calls with no model and no Strands."""

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult: ...


@dataclass(frozen=True)
class CognitionSessionOutcome:
    session_id: int
    items: int
    closed: tuple[CloseOutcome, ...]
    stop_reason: str | None


# ---------------------------------------------------------------------------
# What the runner needs from the world, each a narrow, explicit seam — never
# a live store handed through whole. "the session factory takes only the
# dry-run store type" (review follow-up 5c): `CognitionSessionRunner`'s own
# `__init__` below never accepts anything typed as a live NoteBook,
# GatheringStore or StandingStore — only these READ-ONLY callables, each
# returning plain data. A caller wiring a live store in has to narrow it
# to one of these shapes itself; there is no parameter here it could widen.
# ---------------------------------------------------------------------------

OpenNoteTitles = Callable[[str], Awaitable[Sequence[str]]]
OpenProposedEvents = Callable[[str, str], Awaitable[Sequence[Gathering]]]
PriorStanding = Callable[[str, str], Awaitable[Standing | None]]
PendingSelfWakes = Callable[[str], Awaitable[Sequence[PendingSelfWake]]]
PendingMirroredReminds = Callable[[str], Awaitable[Sequence[PendingMirroredRemind]]]
ReadToolsFor = Callable[[str, Room], list[Any]]
SkillsFor = Callable[[Sequence[str]], str]


@dataclass
class _RunState:
    """Mutable bookkeeping for exactly one `on_wake` call — held on the
    stack, never on `self`, so two concurrent sessions (up to
    `max_concurrent_sessions`) never share it."""

    session_id: int
    unit: str
    now_ms: int
    items_by_index: dict[int, AgendaItem]
    closed_indices: set[int] = field(default_factory=set)
    closes: list[CloseOutcome] = field(default_factory=list)


class CognitionSessionRunner:
    def __init__(
        self, *, dry_run_store: CognitionDryRunStore, stimulus_store: StimulusStore,
        agent_runner: CognitionAgentRunner, home_rooms: Mapping[str, Room],
        read_tools_for: ReadToolsFor, skill_text_for: SkillsFor,
        doctrine_text: str, doctrine_sha256: str | None,
        open_note_titles: OpenNoteTitles, open_proposed_events: OpenProposedEvents,
        prior_standing: PriorStanding, pending_self_wakes: PendingSelfWakes,
        pending_mirrored_reminds: PendingMirroredReminds,
        session_mode: str, budget: AdaptorBudget, cooldown_seconds: float,
        max_items: int, max_strikes: int, max_self_wakes: int = 5,
        model_id: str | None = None, model_route: str | None = None,
        metrics: Any = None, mint_ref: Callable[[], str] = default_mint_ref,
    ) -> None:
        self._store = dry_run_store
        self._stimuli = stimulus_store
        self._agent_runner = agent_runner
        self._home_rooms = home_rooms
        self._read_tools_for = read_tools_for
        self._skill_text_for = skill_text_for
        self._doctrine_text = doctrine_text
        self._doctrine_sha256 = doctrine_sha256
        self._open_note_titles = open_note_titles
        self._open_proposed_events = open_proposed_events
        self._prior_standing = prior_standing
        self._pending_self_wakes = pending_self_wakes
        self._pending_mirrored_reminds = pending_mirrored_reminds
        # "off" makes this belt-and-braces only — see the module docstring;
        # the real gate is the composition root never wiring the hook.
        self._session_mode = session_mode
        self._budget = budget
        self._cooldown_seconds = cooldown_seconds
        self._max_items = max_items
        self._max_strikes = max_strikes
        self._max_self_wakes = max_self_wakes
        self._model_id = model_id
        self._model_route = model_route
        self._metrics = metrics
        self._mint_ref = mint_ref

    def _inc(self, name: str, **labels: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, **labels)

    async def on_wake(
        self, *, unit: str, view: UnitView, now_ms: int, wake_id: int | None = None,
    ) -> CognitionSessionOutcome | None:
        """Called by the wake evaluator's hook on every would-wake it
        records — see `nora.app.wake.WakeEvaluator`'s optional `on_wake`
        parameter. Returns `None` on every path that makes no model call."""
        if self._session_mode != "shadow":
            return None
        home_room = self._home_rooms.get(unit)
        if home_room is None:
            log.warning("cognition session: no home room configured for unit %s", unit)
            self._inc("nora_cognition_session_skipped_total", reason="no_home_room")
            return None
        situation = build_situation(
            unit=unit, pending=view.pending, home_room=home_room, now_ms=now_ms,
            last_session_ms=view.last_activity_ms, max_items=self._max_items,
        )
        if situation.agenda.is_empty:
            self._inc("nora_cognition_session_skipped_total", reason="empty_agenda")
            return None

        session = NewCognitionSession(
            unit=unit, mode="shadow", started_at_ms=now_ms,
            from_stimulus_id=min(s.id for s in view.pending),
            through_stimulus_id=max(s.id for s in view.pending),
            situation=situation.as_dict(),
            agenda={"items": [i.as_dict() for i in situation.agenda.items]},
            doctrine_sha256=self._doctrine_sha256, skill_versions={},
            config_revision_id=None, model_id=self._model_id,
            model_route=self._model_route, wake_id=wake_id,
        )
        session_id = await self._store.record_session(session=session, recorded_at_ms=now_ms)
        self._inc("nora_cognition_session_total", mode="shadow")

        state = _RunState(
            session_id=session_id, unit=unit, now_ms=now_ms,
            items_by_index=dict(enumerate(situation.agenda.items)),
        )
        tools = await self._build_tools(unit=unit, state=state, now_ms=now_ms)
        kinds = sorted({item.kind for item in situation.agenda.items})
        system_prompt = self._doctrine_text + self._skill_text_for(kinds)
        prompt = _render_agenda(situation.agenda.items)
        result = await self._agent_runner.run(
            system_prompt=system_prompt, prompt=prompt, tools=tools,
            budget=self._budget,
            trace_attributes={"cognition.unit": unit, "cognition.session_id": str(session_id)},
        )

        for index, item in enumerate(situation.agenda.items):
            if index in state.closed_indices:
                continue
            outcome = close_or_strike(
                item, item_index=index, reason="waiting", max_strikes=self._max_strikes,
            )
            await self._record_close(state, outcome)

        return CognitionSessionOutcome(
            session_id=session_id, items=len(situation.agenda.items),
            closed=tuple(state.closes), stop_reason=result.stop_reason,
        )

    async def _record_close(self, state: _RunState, outcome: CloseOutcome) -> None:
        await self._store.record_close(
            close=NewCognitionSessionClose(
                session_id=state.session_id, item_index=outcome.item_index,
                kind=outcome.kind, reason=outcome.reason, strike=outcome.strike,
            ),
            recorded_at_ms=state.now_ms,
        )
        state.closed_indices.add(outcome.item_index)
        state.closes.append(outcome)

    # -- tools --------------------------------------------------------------

    async def _build_tools(
        self, *, unit: str, state: _RunState, now_ms: int,
    ) -> list[Any]:
        rooms = {item.room for item in state.items_by_index.values()}
        read_tools: list[Any] = []
        for room in rooms:
            read_tools.extend(self._read_tools_for(unit, room))

        async def close(item_index: int, reason: str) -> dict[str, object]:
            """`close(item, reason)` — 30-cognition.md §2. Refuses an
            already-closed, out-of-range, or invalid-reason call rather
            than raising, the same answered-refusal shape every write verb
            here uses."""
            if item_index in state.closed_indices:
                return {"status": "refused", "reason": "already_closed"}
            item = state.items_by_index.get(item_index)
            if item is None:
                return {"status": "refused", "reason": "no_such_item"}
            try:
                outcome = CloseOutcome(item_index=item_index, kind=item.kind, reason=reason)
            except ValueError as exc:
                return {"status": "refused", "reason": "bad_reason", "detail": str(exc)}
            await self._record_close(state, outcome)
            return {"status": "ok"}

        async def note_create(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            kwargs.pop("established_by", None)
            open_titles = tuple(await self._open_note_titles(unit))
            write = await dry_run_note_create(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id,
                open_titles=open_titles, now_ms=now_ms,
                established_by=f"{COGNITION_PATH_PREFIX}{unit}",
                shown=kwargs.pop("shown", Shown(rendered={})),  # type: ignore[arg-type]
                **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def note_update(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            kwargs.pop("established_by", None)
            write = await dry_run_note_update(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                established_by=f"{COGNITION_PATH_PREFIX}{unit}",
                shown=kwargs.pop("shown", Shown(rendered={})),  # type: ignore[arg-type]
                **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def event_propose(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            open_events = await self._open_proposed_events(unit, conversation_id)
            write = await dry_run_event_propose(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                open_proposed_events=tuple(open_events), **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def event_revise(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            write = await dry_run_event_revise(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def event_set_status(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            write = await dry_run_event_set_status(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, now_ms=now_ms,
                **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def standing_revise(**kwargs: object) -> dict[str, object]:
            platform, conversation_id = _room_of(kwargs, state)
            prior = await self._prior_standing(platform, conversation_id)
            write = await dry_run_standing_revise(
                ledger=self._store, session_id=state.session_id, unit=unit,
                platform=platform, conversation_id=conversation_id, prior=prior,
                now_ms=now_ms, **kwargs,  # type: ignore[arg-type]
            )
            return _verb_result(write)

        async def wake_at(subject_rows: Sequence[str], at_ms: int, why: str) -> dict[str, object]:
            pending = await self._pending_self_wakes(unit)
            wake_ref = self._mint_ref()
            await dry_run_wake_at(
                ledger=self._store, stimuli=self._stimuli, session_id=state.session_id,
                unit=unit, subject_rows=subject_rows, at_ms=at_ms, why=why, now_ms=now_ms,
                wake_ref=wake_ref, cooldown_seconds=self._cooldown_seconds, pending=pending,
                max_self_wakes=self._max_self_wakes,
            )
            return {"status": "ok", "wake_id": wake_ref}

        async def wake_move(wake_id: str, at_ms: int, why: str) -> dict[str, object]:
            pending = await self._pending_self_wakes(unit)
            await dry_run_wake_move(
                ledger=self._store, stimuli=self._stimuli, session_id=state.session_id,
                unit=unit, wake_ref=wake_id, at_ms=at_ms, why=why, now_ms=now_ms,
                cooldown_seconds=self._cooldown_seconds, pending=pending,
                created_by_session_id=state.session_id,
            )
            return {"status": "ok"}

        async def wake_cancel(wake_id: str, why: str) -> dict[str, object]:
            pending = await self._pending_self_wakes(unit)
            await dry_run_wake_cancel(
                ledger=self._store, session_id=state.session_id, unit=unit,
                wake_ref=wake_id, why=why, now_ms=now_ms, pending=pending,
                created_by_session_id=state.session_id,
            )
            return {"status": "ok"}

        async def remind(
            room_platform: str, room_conversation_id: str, intent: str,
            subject_rows: Sequence[str], at_ms: int,
        ) -> dict[str, object]:
            pending = await self._pending_mirrored_reminds(unit)
            ref = self._mint_ref()
            await dry_run_remind(
                ledger=self._store, session_id=state.session_id, unit=unit,
                reminder_ref=ref, platform=room_platform,
                conversation_id=room_conversation_id, intent=intent,
                subject_rows=subject_rows, at_ms=at_ms, now_ms=now_ms, pending=pending,
            )
            return {"status": "ok", "reminder_id": ref}

        async def remind_move(reminder_id: str, at_ms: int, why: str) -> dict[str, object]:
            pending = await self._pending_mirrored_reminds(unit)
            platform, conversation_id = next(
                ((p.platform, p.conversation_id) for p in pending
                 if p.reminder_ref == reminder_id), ("", ""),
            )
            await dry_run_remind_move(
                ledger=self._store, session_id=state.session_id, unit=unit,
                reminder_ref=reminder_id, at_ms=at_ms, why=why, platform=platform,
                conversation_id=conversation_id, now_ms=now_ms, pending=pending,
                created_by_session_id=state.session_id,
            )
            return {"status": "ok"}

        async def remind_cancel(reminder_id: str, why: str) -> dict[str, object]:
            pending = await self._pending_mirrored_reminds(unit)
            platform, conversation_id = next(
                ((p.platform, p.conversation_id) for p in pending
                 if p.reminder_ref == reminder_id), ("", ""),
            )
            await dry_run_remind_cancel(
                ledger=self._store, session_id=state.session_id, unit=unit,
                reminder_ref=reminder_id, why=why, platform=platform,
                conversation_id=conversation_id, now_ms=now_ms, pending=pending,
                created_by_session_id=state.session_id,
            )
            return {"status": "ok"}

        return [
            *read_tools, close, note_create, note_update, event_propose,
            event_revise, event_set_status, standing_revise, wake_at, wake_move,
            wake_cancel, remind, remind_move, remind_cancel,
        ]


def _room_of(kwargs: dict[str, object], state: _RunState) -> tuple[str, str]:
    platform = kwargs.pop("platform", None)
    conversation_id = kwargs.pop("conversation_id", None)
    if isinstance(platform, str) and isinstance(conversation_id, str):
        return platform, conversation_id
    # Falls back to the FIRST agenda item's room — a session with one item
    # never has to be told which room its own write is about.
    first = next(iter(state.items_by_index.values()), None)
    return (first.room.platform, first.room.conversation_id) if first is not None else ("", "")


def _verb_result(write: Any) -> dict[str, object]:
    if write.outcome == "refused":
        return {
            "status": "refused", "reason": write.refusal_reason,
            "detail": write.refusal_detail,
        }
    return {"status": "ok"}


def _render_agenda(items: Sequence[AgendaItem]) -> str:
    """The agenda as the model's own prompt — one line per item, a fact
    never a conclusion (30-cognition.md §1), matching `AgendaItem.summary`'s
    own construction."""
    lines = [f"{i}. {item.summary}" for i, item in enumerate(items)]
    return "\n".join(lines)


def now_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
