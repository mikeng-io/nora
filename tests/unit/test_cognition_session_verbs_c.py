"""The session's verbs held to what the session actually did and saw (fix PR C,
review of 2026-09-30: B3, B4, M3, M5, M6 and m4).

Every test drives the REAL runner through its REAL tool closures with a scripted
agent (no model): what the agent calls is what a model would call, and what
comes back is what it would read. Names are invented and no message is quoted.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-c.json`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from nora.agent.fetch_watch import Fetched, watch_reads
from nora.agent.ports import ToolSpec, adapt
from nora.agent.room_routing import route_reads
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
)
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_remind import PendingMirroredRemind
from nora.domain.cognition_session import Room
from nora.domain.note_body import NORA_NAMES, rollup_key
from nora.domain.standing import Standing, StandingHead
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.cognition_grounding import EmptyGrounder, StaticGrounder
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000
A = Room("signal", "room-a")
B = Room("signal", "room-b")
BUDGET = AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)
ROSTER = {"Ada": "p_ada", "Ben": "p_ben"}

# Message ids: 101 is Ada's and 102 Ben's (both room A), 201 is Ada's in room B,
# 301 is one of Nora's own.
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
    "messages:4": RecordItem(
        ref="messages:4", kind="message", message_id=301, room=A, author="Nora",
        at_ms=NOW, text="noted", person_id=None,
    ),
}


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
    """Scripted agent: `behave(by_name, tools)` runs once, with every tool the
    session handed it, closures by name and Strands tools by name alike."""

    behave: Callable[[dict[str, Any], list[Any]], Awaitable[None]]
    answers: list[Any] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        by = {getattr(t, "tool_name", None) or t.__name__: t for t in tools}
        await self.behave(by, tools)
        return CognitionRunResult(stop_reason="end_turn")


async def _stream(tool: Any, **arguments: Any) -> dict[str, Any]:
    events = [e async for e in tool.stream(
        {"toolUseId": "t1", "name": tool.tool_name, "input": arguments}, {},
    )]
    return json.loads(events[-1].tool_result["content"][0]["text"])  # type: ignore[no-any-return]


def _read_tool(name: str, answer: dict[str, Any]) -> Any:
    async def call(query: str = "") -> dict[str, Any]:
        """Look.

        Args:
            query: What to look for.
        """
        return answer

    call.__name__ = name
    return adapt(ToolSpec(name=name, description="", schema={}, call=call, budget=5))


async def _session(
    behave: Callable[[dict[str, Any], list[Any]], Awaitable[None]], *,
    rooms: tuple[Room, ...] = (A,), records: Mapping[str, RecordItem] | None = None,
    roster: Mapping[str, str] | str | None = "default",
    head: StandingHead | None = None, reads: list[Any] | None = None,
    cooldown: float = 300.0, own_sends: Mapping[int, tuple[RecordItem, ...]] | None = None,
    extra: tuple[Stimulus, ...] = (),
    pending_reminds: tuple[PendingMirroredRemind, ...] = (),
) -> tuple[InMemoryCognitionDryRunStore, Any]:
    store = InMemoryCognitionDryRunStore()
    stimuli = InMemoryStimulusStore(ledger=store)
    stimuli.seed_wake("u", 1)
    # One stimulus per room, the ids the RECORDS table keys the rows by.
    ids = {A: 1, B: 3}
    pending = (*(_stimulus(ids[r], r) for r in rooms), *extra)

    async def roster_of(platform: str, conversation_id: str) -> Mapping[str, str] | None:
        if roster == "default":
            return ROSTER
        return roster  # type: ignore[return-value]

    runner = CognitionSessionRunner(
        dry_run_store=store, stimulus_store=stimuli, agent_runner=Script(behave),
        units=("u",), session_timeout_seconds=900.0, home_rooms={"u": A},
        read_tools_for=lambda unit, rs: (
            route_reads({f"{r.platform}:{r.conversation_id}": list(reads or []) for r in rs})
            if len(rs) > 1 else list(reads or [])
        ),
        read_only_tools=frozenset(t.tool_name for t in (reads or [])),
        skill_text_for=lambda kinds: "",
        grounder=StaticGrounder(Grounding(
            records=dict(records or RECORDS), own_sends=dict(own_sends or {}),
        )) if records != {}
        else EmptyGrounder(),
        doctrine_text="D", doctrine_sha256=None, open_note_titles=_const(()),
        open_proposed_events=_const(()), prior_standing=_const(head),
        pending_self_wakes=_const(()), pending_mirrored_reminds=_const(pending_reminds),
        session_mode="shadow", budget=BUDGET, cooldown_seconds=cooldown, max_items=12,
        max_strikes=3, clock=lambda: NOW / 1000, roster_of=roster_of,
        nora_names=frozenset({"Nora"}),
    )
    view = UnitView(unit="u", pending=pending, last_activity_ms=None, last_wake=None)
    outcome = await runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=1)
    return store, outcome


NOTE = {
    "title": "dinner", "description": "where", "uncertainty": "the place",
    "closing_condition": "a booking", "anchor_entities": ["Ada"],
}
GOOD_BODY = "## Decided\n- going ahead (Ada) [101]\n## Open\n- which place [101]\n"


def _outcomes(store: InMemoryCognitionDryRunStore) -> list[str]:
    return [w.outcome for _, w, _ in store.would_writes]


def _reasons(store: InMemoryCognitionDryRunStore) -> list[str | None]:
    return [w.refusal_reason for _, w, _ in store.would_writes]


# ---------------------------------------------------------------------------
# B3: a `written` close is backed by a write, not by the model's say-so
# ---------------------------------------------------------------------------

async def test_written_with_no_write_in_the_session_is_refused_and_the_item_stays_open():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["close"](item_index=0, reason="written"))

    store, outcome = await _session(behave)
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "no_write_for_item"
    # The refused close recorded nothing: code closes the item `waiting`, with a strike.
    assert [c.reason for c in outcome.closed] == ["waiting"]
    assert [c.reason for _, c, _ in store.closes] == ["waiting"]


async def test_written_is_accepted_once_a_write_of_the_session_would_commit_in_that_room():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](where_it_stands="settled on dinner"))
        answers.append(await by["close"](item_index=0, reason="written"))

    _, outcome = await _session(behave)
    assert answers[0]["status"] == "ok" and answers[1]["status"] == "ok"
    assert [c.reason for c in outcome.closed] == ["written"]


async def test_a_write_in_one_room_does_not_back_a_written_close_of_another_rooms_item():
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["standing_revise"](where_it_stands="settled", room="signal:room-a")
        answers["b"] = await by["close"](item_index=1, reason="written")
        answers["a"] = await by["close"](item_index=0, reason="written")

    _, outcome = await _session(behave, rooms=(A, B))
    assert answers["b"]["reason"] == "no_write_for_item"
    assert answers["a"]["status"] == "ok"
    assert {c.item_index: c.reason for c in outcome.closed} == {0: "written", 1: "waiting"}


async def test_a_refused_write_does_not_back_a_written_close():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        # Refused by the schema (an uncited line), so nothing would commit.
        answers.append(await by["note_create"](**NOTE, body="## Decided\n- x\n## Open\n- y\n"))
        answers.append(await by["close"](item_index=0, reason="written"))

    store, _ = await _session(behave)
    assert answers[0]["status"] == "refused"
    assert answers[1]["reason"] == "no_write_for_item"
    assert "would_commit" not in _outcomes(store)


async def test_a_reminder_or_a_self_wake_is_not_a_write_that_backs_a_close():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["wake_at"](
            subject_rows=["event:1"], at=iso(NOW + 3_600_000), why="look again",
        ))
        answers.append(await by["remind"](
            intent="tell", subject_rows=["note:1"], at=iso(NOW + 3_600_000),
        ))
        answers.append(await by["close"](item_index=0, reason="written"))

    _, _ = await _session(behave)
    assert [a["status"] for a in answers[:2]] == ["ok", "ok"]
    assert answers[2]["reason"] == "no_write_for_item"


def _time_item(i: int = 9) -> Stimulus:
    """A second agenda item in the home room (A): only `message` stimuli are
    room-bound, so a `time` stimulus lands in the unit's home room."""
    return Stimulus(
        id=i, unit="u", kind="time", item="dinner", ref=f"time:{i}", at_ms=NOW,
        recorded_at_ms=NOW, platform=None, conversation_id=None,
    )


async def test_a_write_for_one_item_does_not_back_the_written_close_of_another_in_its_room():
    """B3 per item (#663 review, minor): two items share room A. A write that
    names item 1 backs item 1 and not item 0."""
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers["write"] = await by["standing_revise"](where_it_stands="settled", item=1)
        answers["other"] = await by["close"](item_index=0, reason="written")
        answers["named"] = await by["close"](item_index=1, reason="written")

    _, outcome = await _session(behave, extra=(_time_item(),))
    assert answers["write"]["status"] == "ok", answers["write"]
    assert answers["other"]["reason"] == "no_write_for_item"
    assert answers["named"]["status"] == "ok"
    assert {c.item_index: c.reason for c in outcome.closed} == {0: "waiting", 1: "written"}


async def test_an_untagged_write_backs_a_close_only_when_its_room_holds_one_item():
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await by["standing_revise"](where_it_stands="settled")          # no `item`
        answers["two"] = await by["close"](item_index=0, reason="written")

    await _session(behave, extra=(_time_item(),))
    assert answers["two"]["reason"] == "no_write_for_item"


async def test_a_write_naming_an_item_of_another_room_or_none_is_refused():
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers["none"] = await by["standing_revise"](where_it_stands="x", item=7)
        answers["mismatch"] = await by["standing_revise"](
            where_it_stands="x", item=0, room="signal:room-b")

    store, _ = await _session(behave, rooms=(A, B))
    assert answers["none"]["reason"] == "no_such_item"
    assert answers["mismatch"]["reason"] == "item_room_mismatch"
    assert store.would_writes == []


async def test_the_other_close_reasons_need_no_write():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["close"](item_index=0, reason="no_change"))

    _, outcome = await _session(behave)
    assert answers[0]["status"] == "ok"
    assert [c.reason for c in outcome.closed] == ["no_change"]


# ---------------------------------------------------------------------------
# B4: `remind` is bound to a room on the agenda, like every other write verb
# ---------------------------------------------------------------------------

async def test_remind_resolves_its_room_through_the_agenda_and_records_that_room():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="ask", subject_rows=["note:1"], at=iso(NOW + 60_000), room="signal:room-b",
        ))

    store, _ = await _session(behave, rooms=(A, B))
    assert answers[0]["status"] == "ok"
    [(_, mirror, _)] = store.remind_mirrors
    assert (mirror.platform, mirror.conversation_id) == ("signal", "room-b")


async def test_remind_refuses_a_room_that_is_not_on_the_agenda_and_records_nothing():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="tell", subject_rows=["note:1"], at=iso(NOW + 60_000),
            room="signal:elsewhere",
        ))

    store, _ = await _session(behave, rooms=(A, B))
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "unknown_room"
    assert store.remind_mirrors == []


async def test_remind_move_and_cancel_refuse_a_reminder_whose_room_is_off_the_agenda():
    off = PendingMirroredRemind(
        reminder_ref="r-off", platform="signal", conversation_id="room-b",
        subject_rows=("note:1",), created_by_session_id=None,
    )
    on = PendingMirroredRemind(
        reminder_ref="r-on", platform="signal", conversation_id="room-a",
        subject_rows=("note:2",), created_by_session_id=None,
    )
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        later = iso(NOW + 3_600_000)
        answers["move_off"] = await by["remind_move"](reminder_id="r-off", at=later, why="w")
        answers["cancel_off"] = await by["remind_cancel"](reminder_id="r-off", why="w")
        answers["move_on"] = await by["remind_move"](reminder_id="r-on", at=later, why="w")
        answers["cancel_on"] = await by["remind_cancel"](reminder_id="r-on", why="w")

    store, _ = await _session(behave, pending_reminds=(off, on))        # agenda: room A only
    for key in ("move_off", "cancel_off"):
        assert answers[key]["status"] == "refused" and answers[key]["reason"] == "unknown_room"
    assert answers["move_on"]["status"] == "ok", answers["move_on"]
    assert answers["cancel_on"]["status"] == "ok", answers["cancel_on"]
    assert {m.reminder_ref for _, m, _ in store.remind_mirrors} == {"r-on"}


async def test_remind_on_a_several_room_agenda_must_name_its_room():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="tell", subject_rows=["note:1"], at=iso(NOW + 60_000),
        ))

    store, _ = await _session(behave, rooms=(A, B))
    assert answers[0]["reason"] == "room_required"
    assert store.remind_mirrors == []


async def test_remind_on_a_one_room_agenda_needs_no_room():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["remind"](
            intent="tell", subject_rows=["note:1"], at=iso(NOW + 60_000),
        ))

    store, _ = await _session(behave)
    assert answers[0]["status"] == "ok"
    assert (store.remind_mirrors[0][1].platform, store.remind_mirrors[0][1].conversation_id) == (
        "signal", "room-a")


# ---------------------------------------------------------------------------
# M3: the note dry run runs the tag path's validator on what it was shown
# ---------------------------------------------------------------------------

async def _note(body: str, **kw: Any) -> tuple[Any, InMemoryCognitionDryRunStore]:
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_create"](**NOTE, body=body))

    store, _ = await _session(behave, **kw)
    return answers[0], store


OWED = (
    "## Decided\n## Open\n- which place [101]\n"
    "## Owed\n- {owner} -> book a table -> no date given [102]\n"
)


async def test_an_owed_line_naming_a_member_of_the_room_passes():
    answer, store = await _note(OWED.format(owner="Ben"))
    assert answer["status"] == "ok", answer
    assert _outcomes(store) == ["would_commit"]


async def test_an_owed_line_naming_a_stranger_is_refused_unknown_owner():
    answer, store = await _note(OWED.format(owner="Zed"))
    assert answer["status"] == "refused" and answer["reason"] == "unknown_owner"
    assert _reasons(store) == ["unknown_owner"]


async def test_an_owed_line_owed_by_nora_is_refused():
    answer, _ = await _note(OWED.format(owner="Nora"), roster={**ROSTER, "Nora": "self"})
    assert answer["status"] == "refused" and answer["reason"] == "unknown_owner"


async def test_a_line_attributed_to_nora_is_refused_misattributed():
    answer, _ = await _note(
        "## Decided\n- going ahead (Nora) [101]\n## Open\n- which place [101]\n",
    )
    assert answer["status"] == "refused" and answer["reason"] == "misattributed"


async def test_a_line_naming_someone_who_did_not_write_the_message_it_cites_is_refused():
    """No unverifiable-attribution pass-through: Ben wrote 102, not 101."""
    answer, _ = await _note(
        "## Decided\n- going ahead (Ben) [101]\n## Open\n- which place [101]\n",
    )
    assert answer["status"] == "refused" and answer["reason"] == "misattributed"


async def test_a_line_citing_one_of_noras_own_messages_is_refused():
    answer, _ = await _note("## Decided\n## Open\n- which place [301]\n")
    assert answer["status"] == "refused" and answer["reason"] == "cites_nora"


async def test_one_of_her_own_sends_shown_in_the_window_is_refused_as_hers_not_as_unknown():
    """D1 (#646): her own sends in an item's window are shown, marked hers. A
    note citing one is `cites_nora`; were it unknown it would read as a stray id."""
    mine = RecordItem(
        ref="messages:401", kind="message", message_id=401, room=A, author="the assistant",
        at_ms=NOW, text="sent", own=True,
    )
    answer, _ = await _note(
        "## Decided\n## Open\n- which place [401]\n", own_sends={0: (mine,)},
    )
    assert answer["status"] == "refused" and answer["reason"] == "cites_nora"


async def test_a_message_of_another_room_is_not_shown_to_this_rooms_write():
    answer, _ = await _note("## Decided\n## Open\n- which place [201]\n")
    assert answer["status"] == "refused" and answer["reason"] == "unknown_id"


async def test_an_id_the_session_was_never_shown_is_refused():
    answer, _ = await _note("## Decided\n## Open\n- which place [999]\n")
    assert answer["status"] == "refused" and answer["reason"] == "unknown_id"


async def test_an_unreadable_roster_is_unavailable_not_every_owner_refused():
    answer, store = await _note(OWED.format(owner="Ben"), roster=None)
    assert answer["status"] == "unavailable" and answer["reason"] == "roster"
    assert store.would_writes == []


async def test_a_note_with_no_owed_line_does_not_need_the_roster():
    answer, _ = await _note(GOOD_BODY, roster=None)
    assert answer["status"] == "ok"


async def test_a_deleted_message_whose_words_were_withheld_cannot_be_cited():
    deleted = RecordItem(
        ref="messages:1", kind="message", message_id=101, room=A, author="Ada",
        at_ms=NOW, text=None, person_id="p_ada",
    )
    answer, _ = await _note(GOOD_BODY, records={"messages:1": deleted})
    assert answer["reason"] == "unknown_id"


# ---------------------------------------------------------------------------
# m4: a note may cite what the session fetched, and a Rollup row it read
# ---------------------------------------------------------------------------

KEY = rollup_key("stay", "p_ada", "2026-10-01T09:00:00+08:00", "2026-10-01T11:00:00+08:00")


async def test_a_note_may_cite_an_id_an_existing_note_read_this_session_carries():
    read = _read_tool("note_read", {"status": "ok", "body": "## Decided\n## Open\n- q [555]\n"})
    body = "## Decided\n## Open\n- q [555]\n"
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_create"](**NOTE, body=body))      # not read yet
        await _stream(by["note_read"], query="dinner")
        answers.append(await by["note_create"](**NOTE, body=body))      # after the read

    _, _ = await _session(behave, reads=[read])
    assert answers[0]["reason"] == "unknown_id"
    assert answers[1]["status"] == "ok", answers[1]


async def test_a_note_may_cite_a_rollup_row_it_read_and_no_other():
    rows = {"status": "ok", "rows": [{
        "kind": "stay", "subject": "p_ada", "from": "2026-10-01T09:00:00+08:00",
        "to": "2026-10-01T11:00:00+08:00", "place": "home",
    }]}
    read = _read_tool("rollup_read", rows)
    cited = f"## Decided\n## Open\n- is Ada home [rollup({KEY})]\n"
    other = "## Decided\n## Open\n- is Ada home [rollup(stay|p_ada|2026-10-02T09:00:00+08:00/x)]\n"
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_create"](**NOTE, body=cited))     # not read yet
        await _stream(by["rollup_read"], query="")
        answers.append(await by["note_create"](**NOTE, body=cited))
        answers.append(await by["note_create"](**NOTE, body=other))

    await _session(behave, reads=[read])
    assert answers[0]["reason"] == "unknown_rollup_key"
    assert answers[1]["status"] == "ok", answers[1]
    assert answers[2]["reason"] == "unknown_rollup_key"


async def test_a_line_resting_on_a_carried_id_may_not_be_attributed_to_nora():
    """The pass-through for a carried id names an author nothing shows; Nora is
    the one name it never passes."""
    read = _read_tool("note_read", {"status": "ok", "body": "## Decided\n## Open\n- q [555]\n"})
    body = "## Decided\n- on (Nora) [555]\n## Open\n- q [555]\n"
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await _stream(by["note_read"], query="x")
        answers.append(await by["note_create"](**NOTE, body=body))

    await _session(behave, reads=[read])
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "misattributed"


async def test_a_row_read_through_one_room_is_not_citable_in_a_note_filed_to_another():
    """The reviewer's probe P2 (#663 review, major 1): a `rollup_read` routed
    through room A made its row citable in a note written to room B."""
    rows = {"status": "ok", "rows": [{
        "kind": "stay", "subject": "p_ada", "from": "2026-10-01T09:00:00+08:00",
        "to": "2026-10-01T11:00:00+08:00", "place": "home",
    }]}
    note = f"## Decided\n## Open\n- is Ada home [rollup({KEY})]\n"
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await _stream(by["rollup_read"], query="", room="signal:room-a")
        answers["b"] = await by["note_create"](**NOTE, body=note, room="signal:room-b")
        answers["a"] = await by["note_create"](**NOTE, body=note, room="signal:room-a")

    await _session(behave, rooms=(A, B), reads=[_read_tool("rollup_read", rows)])
    assert answers["b"]["status"] == "refused" and answers["b"]["reason"] == "unknown_rollup_key"
    assert answers["a"]["status"] == "ok", answers["a"]


async def test_a_carried_id_and_a_fetched_message_are_per_room_too():
    carried = _read_tool("note_read", {"status": "ok", "body": "## Decided\n## Open\n- q [555]\n"})
    body = "## Decided\n## Open\n- q [555]\n"
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await _stream(by["note_read"], query="x", room="signal:room-a")
        answers["b"] = await by["note_create"](**NOTE, body=body, room="signal:room-b")
        answers["a"] = await by["note_create"](**NOTE, body=body, room="signal:room-a")

    await _session(behave, rooms=(A, B), reads=[carried])
    assert answers["b"]["reason"] == "unknown_id"
    assert answers["a"]["status"] == "ok", answers["a"]


async def test_a_read_that_names_no_room_on_the_agenda_keeps_nothing():
    rows = {"status": "ok", "body": "## Decided\n## Open\n- q [555]\n"}
    body = "## Decided\n## Open\n- q [555]\n"
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        refused = await _stream(by["note_read"], query="x", room="signal:elsewhere")
        assert refused["status"] == "refused"
        answers.append(await by["note_create"](**NOTE, body=body, room="signal:room-a"))

    await _session(behave, rooms=(A, B), reads=[_read_tool("note_read", rows)])
    assert answers[0]["reason"] == "unknown_id"


async def test_watching_a_read_changes_nothing_the_model_reads_and_keeps_only_ok_results():
    answer = {"status": "ok", "body": "## Decided\n## Open\n- q [555]\n"}
    fetched = Fetched()
    [watched] = watch_reads([_read_tool("note_read", answer)], fetched, ["signal:room-a"])
    assert await _stream(watched, query="x") == answer
    assert fetched.of("signal:room-a").carried_ids == {555}

    # A refused or unavailable answer keeps nothing, even one that carries rows.
    empty = Fetched()
    stray = {"status": "refused", "body": "## Decided\n## Open\n- q [555]\n"}
    [refused] = watch_reads([_read_tool("note_read", stray)], empty, ["signal:room-a"])
    await _stream(refused, query="x")
    assert empty.of("signal:room-a").carried_ids == set()


def test_a_tool_that_hands_out_nothing_citable_is_not_wrapped():
    plain = _read_tool("about", {"status": "ok"})
    assert watch_reads([plain], Fetched(), ()) == [plain]


# ---------------------------------------------------------------------------
# M5: one time base on every session verb
# ---------------------------------------------------------------------------

BAD_TIMES = ["2026-10-03T19:40", "tomorrow at 7", "", "1800003600000", "2026-10-03"]


@pytest.mark.parametrize("bad", BAD_TIMES)
async def test_a_time_with_no_offset_or_no_meaning_is_refused_on_every_verb(bad: str):
    answers: dict[str, Any] = {}

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers["wake_at"] = await by["wake_at"](subject_rows=["event:1"], at=bad, why="w")
        answers["wake_move"] = await by["wake_move"](wake_id="ref", at=bad, why="w")
        answers["remind"] = await by["remind"](intent="tell", subject_rows=["note:1"], at=bad)
        answers["remind_move"] = await by["remind_move"](reminder_id="ref", at=bad, why="w")

    store, _ = await _session(behave)
    for verb, answer in answers.items():
        assert answer["status"] == "refused" and answer["reason"] == "bad_time", (verb, answer)
    assert store.paces == [] and store.remind_mirrors == []


async def test_an_offset_time_is_converted_to_the_same_instant_whatever_its_zone():
    target = NOW + 3_600_000
    in_utc = datetime.fromtimestamp(target / 1000, UTC).isoformat().replace("+00:00", "Z")
    in_hk = datetime.fromtimestamp(target / 1000, UTC).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Hong_Kong"),
    ).isoformat()
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=in_utc, why="a"))
        answers.append(await by["remind"](intent="tell", subject_rows=["note:2"], at=in_hk))

    store, _ = await _session(behave)
    assert [a["status"] for a in answers] == ["ok", "ok"]
    assert store.paces[0][1].at_ms == target
    assert store.remind_mirrors[0][1].at_ms == target


async def test_a_notes_anchor_date_is_an_iso_date_stored_as_the_number_the_tier_holds():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["note_create"](
            title="dinner", description="where", uncertainty="the place",
            closing_condition="a booking", body=GOOD_BODY, anchor_date="2026-10-05",
        ))
        for bad in ("20261005", "2026-02-30", "next friday", "2026-10-05T10:00+08:00"):
            answers.append(await by["note_create"](
                title="dinner two", description="where", uncertainty="the place",
                closing_condition="a booking", body=GOOD_BODY, anchor_date=bad,
            ))

    store, _ = await _session(behave)
    assert answers[0]["status"] == "ok", answers[0]
    assert store.would_writes[0][1].candidate["anchor_date"] == 20261005
    assert [a["reason"] for a in answers[1:]] == ["bad_anchor_date"] * 4
    assert len(store.would_writes) == 1


# ---------------------------------------------------------------------------
# M6: the Standing CAS token is the revision id
# ---------------------------------------------------------------------------

def _head(revision: str | None, fold_count: int = 3) -> StandingHead:
    return StandingHead(
        standing=Standing(
            platform="signal", conversation_id="room-a", body="was here",
            fold_count=fold_count, updated_at=NOW - 1000,
        ),
        revision_id=revision,
    )


async def test_a_standing_revise_against_a_stale_revision_id_is_refused_and_a_fresh_one_passes():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](
            where_it_stands="now here", base_revision_id="40"))
        answers.append(await by["standing_revise"](
            where_it_stands="now here", base_revision_id="41"))
        answers.append(await by["standing_revise"](where_it_stands="now here"))

    store, _ = await _session(behave, head=_head("41"))
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "stale_base"
    assert answers[1]["status"] == "ok" and answers[2]["status"] == "ok"
    assert _outcomes(store) == ["refused", "would_commit", "would_commit"]


async def test_the_fold_count_is_no_longer_the_token():
    """The head's fold count is 3: a base of 3 was fresh before M6, and is now
    a token that names no revision."""
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](
            where_it_stands="now here", base_revision_id="3"))

    await _session(behave, head=_head("41", fold_count=3))
    assert answers[0]["reason"] == "stale_base"


async def test_a_base_against_a_head_with_no_revision_id_is_stale():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["standing_revise"](
            where_it_stands="now here", base_revision_id="1"))

    await _session(behave, head=_head(None))
    assert answers[0]["reason"] == "stale_base"


def test_the_session_and_the_tag_path_go_by_one_set_of_names_for_nora():
    """One source (#663 review, minor): `NORA_NAMES` is what `_speaker` prints for
    her rows, the tag path's validator and this session's validator both take it,
    and the composition passes no second name that could make them differ."""
    import inspect

    from nora.app.cognition_wiring import build_cognition_runner

    assert frozenset({"Nora"}) == NORA_NAMES
    for fn in (CognitionSessionRunner.__init__, build_cognition_runner):
        assert inspect.signature(fn).parameters["nora_names"].default is NORA_NAMES
    assert "nora_names=NORA_NAMES" in inspect.getsource(
        __import__("nora.agent.tools", fromlist=["x"]),
    )
    assert "nora_names=" not in inspect.getsource(
        __import__("nora.app.composition", fromlist=["x"]),
    ).split("cognition_runner = build_cognition_runner(")[1].split("homes=homes")[0]
