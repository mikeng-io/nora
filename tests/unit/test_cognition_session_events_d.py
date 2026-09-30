"""The Cognition session's event verbs carry evidence (fix PR D, review of
2026-09-30 M2 and M4, and of #662 M8/E1a): `cited_ids`, a `reasoning` and a
`confidence`, held to what the session was shown or fetched exactly as a note's
citations are, an abduced value kept as a question and never as a stated field,
and every status before `happened` reachable.

Drives the REAL runner through its REAL tool closures with a scripted agent, the
way `test_cognition_session_verbs_c.py` does. Names are invented.

What each test catches if its fix is reverted is named in
`scripts/mutations/cognition-d.json`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from nora.domain.gatherings import Gathering, GatheringStatus
from tests.unit.test_cognition_session_verbs_c import (
    KEY,
    _read_tool,
    _reasons,
    _session,
    _stream,
)

WHEN = "2026-10-02T19:00:00+08:00"
ROWS = {"status": "ok", "rows": [{
    "kind": "stay", "subject": "p_ada", "from": "2026-10-01T09:00:00+08:00",
    "to": "2026-10-01T11:00:00+08:00", "place": "home",
}]}


def _event(status: GatheringStatus = GatheringStatus.CONFIRMED, **over: Any) -> Gathering:
    base: dict[str, Any] = {
        "id": uuid.uuid4(), "conversation_id": "room-a", "title": "dinner",
        "created_by": "p_ada", "when": None, "venue_text": "place one",
        "venue_place_id": None, "venue_name": None, "venue_address": None,
        "venue_rating": None, "venue_price_level": "", "venue_maps_url": None,
        "venue_resolved_at": None, "status": status, "reminder_id": None,
        "created_at": datetime(2026, 9, 29, tzinfo=UTC), "unit_id": "u",
        "head_revision_id": uuid.uuid4(),
    }
    base.update(over)
    return Gathering(**base)


def _candidates(store: Any) -> list[dict[str, Any]]:
    return [w.candidate for _, w, _ in store.would_writes if w.outcome == "would_commit"]


# ---------------------------------------------------------------------------
# M2: cited ids, held to what the session saw
# ---------------------------------------------------------------------------

async def test_a_proposal_citing_a_message_the_session_was_shown_would_commit_with_its_evidence():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["101"],
            when=WHEN, venue_text="place one",
        ))

    store, _ = await _session(behave)
    assert answers[0]["status"] == "ok", answers[0]
    [candidate] = _candidates(store)
    assert candidate["cited_ids"] == ["101"]
    assert {m["field"]: (m["reasoning"], m["confidence"]) for m in candidate["field_meta"]} == {
        "what": ("told", "high"), "start": ("told", "high"),
        "place": ("told", "high"), "status": ("told", "high"),
    }
    assert candidate["evidence_parts"] == ["what", "start", "place", "status"]


async def test_a_proposal_citing_a_message_it_was_never_shown_is_refused_unknown_id():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["999"],
        ))
        # 201 is room B's: a room's write is shown that room's rows only.
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["201"],
        ))

    store, _ = await _session(behave)
    assert [a["reason"] for a in answers] == ["unknown_id", "unknown_id"]
    assert _candidates(store) == []


async def test_a_proposal_with_no_citation_or_one_of_nora_s_own_messages_is_refused():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=[],
        ))
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["301"],
        ))
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["the dinner one"],
        ))

    store, _ = await _session(behave)
    assert [a["reason"] for a in answers] == ["uncited", "cites_nora", "uncited"]
    assert _candidates(store) == []


async def test_a_rollup_row_is_citable_only_once_the_session_read_it():
    answers: list[Any] = []
    cited = [f"rollup({KEY})"]

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="deduced", confidence="medium", cited_ids=cited,
        ))
        await _stream(by["rollup_read"], query="")
        answers.append(await by["event_propose"](
            title="dinner", reasoning="deduced", confidence="medium", cited_ids=cited,
        ))

    store, _ = await _session(behave, reads=[_read_tool("rollup_read", ROWS)])
    assert answers[0]["reason"] == "unknown_rollup_key"
    assert answers[1]["status"] == "ok", answers[1]
    assert _candidates(store)[0]["cited_ids"] == cited


async def test_a_message_the_session_fetched_with_record_query_is_citable():
    rows = {"status": "ok", "messages": [{"n": 555, "sender": "Ada", "text": "older"}]}
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["555"],
        ))
        await _stream(by["record_query"], query="dinner")
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["555"],
        ))

    await _session(behave, reads=[_read_tool("record_query", rows)])
    assert [a.get("reason") or a["status"] for a in answers] == ["unknown_id", "ok"]


async def test_a_mode_the_event_tier_does_not_allow_is_refused():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="induced", confidence="high", cited_ids=["101"],
        ))
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="certain", cited_ids=["101"],
        ))

    store, _ = await _session(behave)
    assert _reasons(store) == ["bad_reasoning", "bad_confidence"]


# ---------------------------------------------------------------------------
# 40 §4: an abduced value is a question to ask, never a stated field
# ---------------------------------------------------------------------------

async def test_an_abduced_proposal_creates_no_event_and_records_the_question():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner at place two", reasoning="abduced", confidence="low",
            cited_ids=["101"], venue_text="place two",
        ))

    store, _ = await _session(behave)
    assert answers[0]["status"] == "ok"
    [candidate] = _candidates(store)
    assert candidate["kind"] == "question_to_ask" and candidate["reasoning"] == "abduced"
    assert {"field": "place", "value": "place two"} in candidate["questions_to_ask"]
    # No stated event: no id, no title, no field_meta a renderer could read as told.
    assert "title" not in candidate and "field_meta" not in candidate


async def test_an_abduced_revision_leaves_the_event_as_it_was_and_asks():
    current = _event()
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_revise"](
            event_id=str(current.id), reasoning="abduced", confidence="low",
            cited_ids=["101"], venue_text="place two",
        ))

    store, _ = await _session(behave, events=(current,))
    [candidate] = _candidates(store)
    assert candidate["kind"] == "question_to_ask"
    assert candidate["event_id"] == str(current.id)
    assert candidate["questions_to_ask"] == [{"field": "place", "value": "place two"}]
    assert "venue_text" not in candidate


# ---------------------------------------------------------------------------
# M4: every status before `happened` is reachable
# ---------------------------------------------------------------------------

async def test_a_confirmed_event_can_be_marked_happened_or_cancelled():
    happened, cancelled = _event(), _event()
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_set_status"](
            event_id=str(happened.id), status="happened", reasoning="told",
            confidence="high", cited_ids=["101"],
        ))
        answers.append(await by["event_set_status"](
            event_id=str(cancelled.id), status="cancelled", reasoning="told",
            confidence="high", cited_ids=["102"],
        ))

    store, _ = await _session(behave, events=(happened, cancelled))
    assert [a["status"] for a in answers] == ["ok", "ok"], answers
    assert [c["status"] for c in _candidates(store)] == ["happened", "cancelled"]


async def test_a_staged_or_confirmed_event_can_be_revised():
    staged, confirmed = _event(GatheringStatus.STAGED), _event()
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        for event in (staged, confirmed):
            answers.append(await by["event_revise"](
                event_id=str(event.id), reasoning="told", confidence="high",
                cited_ids=["101"], venue_text="place three",
            ))

    store, _ = await _session(behave, events=(staged, confirmed))
    assert [a["status"] for a in answers] == ["ok", "ok"], answers
    assert {c["venue_text"] for c in _candidates(store)} == {"place three"}
    # Only the place was stated, so only the place carries the stated mode.
    assert [m["field"] for m in _candidates(store)[0]["field_meta"]] == ["place"]


async def test_an_unknown_event_reads_not_found_and_a_finished_one_does_not_move():
    done = _event(GatheringStatus.HAPPENED)
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_set_status"](
            event_id=str(uuid.uuid4()), status="cancelled", reasoning="told",
            confidence="high", cited_ids=["101"],
        ))
        answers.append(await by["event_set_status"](
            event_id=str(done.id), status="cancelled", reasoning="told",
            confidence="high", cited_ids=["101"],
        ))

    store, _ = await _session(behave, events=(done,))
    assert _reasons(store) == ["not_found", "terminal_status"]


async def test_a_move_to_lapsed_may_cite_nothing_but_no_other_may():
    current = _event(GatheringStatus.PROPOSED)
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_set_status"](
            event_id=str(current.id), status="lapsed", reasoning="deduced",
            confidence="high", cited_ids=[],
        ))
        answers.append(await by["event_set_status"](
            event_id=str(current.id), status="cancelled", reasoning="deduced",
            confidence="high", cited_ids=[],
        ))

    store, _ = await _session(behave, events=(current,))
    assert [a.get("reason") or a["status"] for a in answers] == ["ok", "uncited"]
    assert _candidates(store)[0]["status"] == "lapsed"


async def test_a_staged_event_is_a_proposal_the_room_has_not_made_and_does_not_block_one():
    two_staged = (_event(GatheringStatus.STAGED), _event(GatheringStatus.STAGED))
    proposals = (_event(GatheringStatus.PROPOSED), _event(GatheringStatus.PROPOSED))
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["101"],
            status="staged",
        ))

    store, _ = await _session(behave, events=two_staged)
    assert answers[0]["status"] == "ok"
    assert _candidates(store)[0]["status"] == "staged"

    answers.clear()

    async def again(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["101"],
        ))

    store, _ = await _session(again, events=proposals)
    assert answers[0]["reason"] == "ambiguous_open_gatherings"


async def test_an_event_can_be_proposed_or_staged_and_nothing_later():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["event_propose"](
            title="dinner", reasoning="told", confidence="high", cited_ids=["101"],
            status="confirmed",
        ))

    store, _ = await _session(behave)
    assert _reasons(store) == ["bad_status"]
