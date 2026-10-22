"""One live Cognition session over in-memory stores, driven by a scripted agent
(no model): what the agent calls is what a model would call, and what lands in
the stores is what production's stores would hold. Shared by the session's
verb tests. Names are invented and no message is quoted."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nora.agent.room_routing import route_reads
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.app.gatherings import GatheringService
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import Room
from nora.domain.standing import StandingHead
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_grounding import StaticGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.gatherings import FakeGatheringStore
from nora.testing.memo_store import InMemoryMemoStore
from nora.testing.note_store import InMemoryNoteStore
from nora.testing.reminders import FakeReminderStore
from nora.testing.standing_store import InMemoryStandingStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
A = Room("signal", "room-a")
B = Room("signal", "room-b")
BUDGET = AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)
ROSTER = {"Ada": "p_ada", "Ben": "p_ben"}

# Message ids: 101 is Ada's and 102 Ben's (room A), 201 is Ada's in room B.
RECORDS = {
    "messages:1": RecordItem(
        ref="messages:1", kind="message", message_id=101, room=A, author="Ada",
        at_ms=NOW, text="we are going", person_id="p_ada",
    ),
    "messages:2": RecordItem(
        ref="messages:2", kind="message", message_id=102, room=A, author="Ben",
        at_ms=NOW, text="i will book", person_id="p_ben",
    ),
    "messages:3": RecordItem(
        ref="messages:3", kind="message", message_id=201, room=B, author="Ada",
        at_ms=NOW, text="elsewhere", person_id="p_ada",
    ),
}

NOTE = {
    "title": "dinner", "description": "where", "uncertainty": "the place",
    "closing_condition": "a booking", "anchor_entities": ["Ada"],
}
GOOD_BODY = "## Decided\n- going ahead (Ada) [101]\n## Open\n- which place [101]\n"


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _stimulus(i: int, room: Room) -> Stimulus:
    return Stimulus(
        id=i, unit="u", kind="message", item="message", ref=f"messages:{i}", at_ms=NOW,
        recorded_at_ms=NOW, platform=room.platform, conversation_id=room.conversation_id,
    )


def _const(value: Any) -> Callable[..., Awaitable[Any]]:
    async def f(*args: Any, **kwargs: Any) -> Any:
        return value
    return f


@dataclass
class Script:
    """Scripted agent: `behave(by_name, tools)` runs once with every tool the
    session handed it."""

    behave: Callable[[dict[str, Any], list[Any]], Awaitable[None]]
    total_tokens: int | None = None

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        by = {getattr(t, "tool_name", None) or t.__name__: t for t in tools}
        await self.behave(by, tools)
        return CognitionRunResult(stop_reason="end_turn", total_tokens=self.total_tokens)


async def stream(tool: Any, **arguments: Any) -> dict[str, Any]:
    events = [e async for e in tool.stream(
        {"toolUseId": "t1", "name": tool.tool_name, "input": arguments}, {"agent": None},
    )]
    return json.loads(events[-1].tool_result["content"][0]["text"])  # type: ignore[no-any-return]


@dataclass
class Stores:
    """The live stores a session writes through, as a tag turn's would."""

    cognition: InMemoryCognitionStore = field(default_factory=InMemoryCognitionStore)
    reminders: FakeReminderStore = field(default_factory=FakeReminderStore)
    notes: InMemoryNoteStore = field(default_factory=InMemoryNoteStore)
    gatherings: FakeGatheringStore = field(default_factory=FakeGatheringStore)
    standing: InMemoryStandingStore = field(default_factory=InMemoryStandingStore)
    memos: InMemoryMemoStore = field(default_factory=InMemoryMemoStore)

    def events(self) -> GatheringService:
        return GatheringService(
            store=self.gatherings, route_source=None, honcho=None,
            ai_peer="nora", now=lambda: datetime.fromtimestamp(NOW / 1000, UTC),
        )


async def session(
    behave: Callable[[dict[str, Any], list[Any]], Awaitable[None]], *,
    rooms: tuple[Room, ...] = (A,), stores: Stores | None = None,
    head: StandingHead | None = None, metrics: Any = None, tokens: int | None = None,
    runner_kwargs: Mapping[str, Any] | None = None,
) -> tuple[Stores, Any]:
    """Run one session over a wake whose agenda holds one message item per
    room in `rooms`, and return the stores it wrote and its outcome."""
    stores = stores or Stores()
    stimuli = InMemoryStimulusStore(ledger=stores.cognition)
    stimuli.seed_wake("u", 1)
    ids = {A: 1, B: 3}
    pending = tuple(_stimulus(ids[r], r) for r in rooms)

    async def open_titles(unit: str) -> list[str]:
        return [n.title for n in await stores.notes.standing(unit=unit)]

    async def open_events(unit: str, conversation_id: str) -> Any:
        return await stores.gatherings.list_for_conversation(
            conversation_id=conversation_id, unit_id=unit,
        )

    wiring: dict[str, Any] = {
        "store": stores.cognition, "stimulus_store": stimuli,
        "agent_runner": Script(behave, total_tokens=tokens),
        "units": ("u",), "session_timeout_seconds": 900.0, "home_rooms": {"u": A},
        "read_tools_for": lambda unit, rs: route_reads(
            {f"{r.platform}:{r.conversation_id}": [] for r in rs},
        ) if len(rs) > 1 else [],
        "skill_text_for": lambda kinds: "",
        "grounder": StaticGrounder(Grounding(records=dict(RECORDS))),
        "doctrine_text": "D", "doctrine_sha256": None, "open_note_titles": open_titles,
        "open_events": open_events, "prior_standing": _const(head),
        "budget": BUDGET, "cooldown_seconds": 300.0, "max_items": 12,
        "max_strikes": 3, "clock": lambda: NOW / 1000, "roster_of": _const(ROSTER),
        "nora_names": frozenset({"Nora"}), "metrics": metrics,
        "notes": stores.notes, "events": stores.events(), "standing": stores.standing,
        "reminders": stores.reminders, "ai_peer": "nora", "memos": stores.memos,
        "room_proactive": lambda platform, conversation_id: True,
        "max_pending_per_requester": 20, "remind_min_lead_seconds": 120,
        "remind_max_horizon_days": 30,
    }
    runner = CognitionSessionRunner(**{**wiring, **(runner_kwargs or {})})
    view = UnitView(unit="u", pending=pending, last_activity_ms=None, last_wake=None)
    outcome = await runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1)
    return stores, outcome
