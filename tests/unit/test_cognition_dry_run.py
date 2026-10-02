"""`nora.agent.cognition_dry_run` — Cognition's note, event and Standing
write verbs, in shadow (design/cognition-loop/70-migration.md step 3a).

Every test proves TWO things at once, the way `standing-revise.json`'s
mutation table already does one tier over: (1) the SAME validation the live
verb runs is what refuses or accepts here, and (2) nothing is ever
committed — `InMemoryCognitionDryRunStore` is the only thing that records
anything, and it is never a live tier store.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.agent.cognition_dry_run import (
    dry_run_event_propose,
    dry_run_event_revise,
    dry_run_event_set_status,
    dry_run_note_create,
    dry_run_note_update,
    dry_run_standing_revise,
)
from nora.domain.cognition_write import WouldWrite
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.domain.note_body import Shown, ShownMessage
from nora.domain.ports import NoteHead
from nora.domain.standing import Standing
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore

SHOWN = Shown(
    rendered=(ShownMessage(1, "Ada", "p_ada"),),
    roster={"Ada": "p_ada"},
    nora_names=frozenset({"Nora"}),
)
CONFORMANT_BODY = "## Decided\n- dinner is on (Ada) [1]\n## Open\n- which place [1]\n"


def _gathering(**overrides: object) -> Gathering:
    base: dict[str, object] = {
        "id": uuid.uuid4(), "conversation_id": "room-a", "title": "dinner",
        "created_by": "p_ada", "when": None, "venue_text": None, "venue_place_id": None,
        "venue_name": None, "venue_address": None, "venue_rating": None,
        "venue_price_level": "", "venue_maps_url": None, "venue_resolved_at": None,
        "status": GatheringStatus.PROPOSED, "reminder_id": None,
        "created_at": datetime(2026, 9, 29, tzinfo=UTC), "unit_id": "unit-1",
        "head_revision_id": uuid.uuid4(),
    }
    base.update(overrides)
    return Gathering(**base)  # type: ignore[arg-type]


# -- note -------------------------------------------------------------------

async def test_note_create_commits_nothing_and_records_would_commit():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_create(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", title="dinner", description="where and when",
        uncertainty="which restaurant", closing_condition="booked",
        body=CONFORMANT_BODY, established_by="cognition", now_ms=1000,
        anchor_date=20261003, shown=SHOWN,
    )
    assert write.outcome == "would_commit"
    assert write.candidate is not None
    assert len(ledger.would_writes) == 1
    assert ledger.sessions == []  # nothing else was touched


async def test_note_create_refuses_a_body_that_breaks_the_schema_and_records_why():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_create(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", title="dinner", description="d",
        uncertainty="u", closing_condition="c",
        body="not a schema body at all", established_by="cognition",
        now_ms=1000, anchor_date=20261003, shown=SHOWN,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason is not None
    assert len(ledger.would_writes) == 1
    assert ledger.would_writes[0][1].outcome == "refused"


async def test_note_create_refuses_with_no_anchor_the_same_way_note_post_init_does():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_create(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", title="dinner", description="d",
        uncertainty="u", closing_condition="c", body=CONFORMANT_BODY,
        established_by="cognition", now_ms=1000, shown=SHOWN,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "invalid_note"


async def test_note_update_would_commit_a_well_formed_revision():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_update(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", note_id="note-1", body=CONFORMANT_BODY,
        parent_revision_id="rev-1", established_by="cognition", now_ms=1000,
        shown=SHOWN, head=NoteHead(head_revision_id="rev-1"),
    )
    assert write.outcome == "would_commit"
    assert write.base_revision_id == "rev-1"


async def _update(head: NoteHead | None, parent: str = "rev-1"):
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_update(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", note_id="note-1", body=CONFORMANT_BODY,
        parent_revision_id=parent, established_by="cognition", now_ms=1000,
        shown=SHOWN, head=head,
    )
    return write, ledger


async def test_note_update_against_a_head_that_moved_is_refused_stale_base_and_recorded():
    # The tool text promises «A stale one is refused» and the live store refuses
    # (`SqlNoteStore._append_on_conn`'s compare-and-swap); the dry run used to
    # record it `would_commit`, which overstated what the live path would write.
    write, ledger = await _update(NoteHead(head_revision_id="rev-2"))
    assert (write.outcome, write.refusal_reason) == ("refused", "stale_base")
    assert "rev-2" in (write.refusal_detail or "")
    assert write.base_revision_id == "rev-1"
    assert [w.outcome for _, w, _ in ledger.would_writes] == ["refused"]


async def test_note_update_of_a_note_the_unit_does_not_hold_is_refused_not_found():
    write, _ = await _update(None)
    assert (write.outcome, write.refusal_reason) == ("refused", "not_found")


async def test_note_update_of_a_retired_note_is_refused_even_at_its_head():
    write, _ = await _update(NoteHead(head_revision_id="rev-1", retired=True))
    assert (write.outcome, write.refusal_reason) == ("refused", "note_retired")


async def test_a_note_update_whose_body_is_bad_is_refused_on_the_body_first():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_update(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", note_id="note-1", body="no sections at all",
        parent_revision_id="rev-1", established_by="cognition", now_ms=1000,
        shown=SHOWN, head=NoteHead(head_revision_id="rev-2"),
    )
    assert write.outcome == "refused" and write.refusal_reason != "stale_base"


async def test_note_update_without_a_parent_revision_is_refused():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_update(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", note_id="note-1", body=CONFORMANT_BODY,
        parent_revision_id="", established_by="cognition", now_ms=1000,
        shown=SHOWN, head=NoteHead(head_revision_id="rev-1"),
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "invalid_revision"


# -- event --------------------------------------------------------------

async def test_event_propose_would_commit_a_well_formed_event():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_event_propose(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", now_ms=1000, id=uuid.uuid4(), title="dinner",
        created_by="p_ada", when=None, venue_text=None, venue_place_id=None,
        venue_name=None, venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=None,
        status=GatheringStatus.PROPOSED, reminder_id=None,
        created_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert write.outcome == "would_commit"
    assert write.candidate["title"] == "dinner"


async def test_event_propose_refuses_a_bad_reasoning_mode():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_event_propose(
        shown=SHOWN, reasoning="not-a-real-mode", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", now_ms=1000, id=uuid.uuid4(), title="dinner",
        created_by="p_ada", when=None, venue_text=None, venue_place_id=None,
        venue_name=None, venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=None,
        status=GatheringStatus.PROPOSED, reminder_id=None,
        created_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "bad_reasoning"


async def test_event_revise_would_commit_against_the_current_head():
    ledger = InMemoryCognitionDryRunStore()
    current = _gathering()
    write = await dry_run_event_revise(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", current=current,
        base_revision_id=str(current.head_revision_id), now_ms=1000,
        title="dinner, moved",
    )
    assert write.outcome == "would_commit"
    assert write.candidate["title"] == "dinner, moved"


async def test_event_revise_refuses_a_stale_base_with_the_current_row():
    ledger = InMemoryCognitionDryRunStore()
    current = _gathering()
    write = await dry_run_event_revise(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", current=current,
        base_revision_id=str(uuid.uuid4()), now_ms=1000, title="dinner, moved",
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "stale_base"


async def test_event_revise_with_no_base_revision_id_always_passes_staleness():
    ledger = InMemoryCognitionDryRunStore()
    current = _gathering()
    write = await dry_run_event_revise(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", current=current, base_revision_id=None,
        now_ms=1000, title="dinner, moved",
    )
    assert write.outcome == "would_commit"


async def test_event_set_status_would_commit_a_cancellation():
    ledger = InMemoryCognitionDryRunStore()
    current = _gathering()
    write = await dry_run_event_set_status(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", current=current,
        base_revision_id=str(current.head_revision_id),
        status=GatheringStatus.CANCELLED, now_ms=1000,
    )
    assert write.outcome == "would_commit"
    assert write.candidate["status"] == "cancelled"


# -- standing -------------------------------------------------------------

async def test_standing_revise_would_commit_a_well_formed_position():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=None, where_it_stands="dinner is on",
        now_ms=1000,
    )
    assert write.outcome == "would_commit"
    assert write.candidate["fold_count"] == 1


async def test_standing_revise_refuses_a_blank_where_it_stands():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=None, where_it_stands="   ",
        now_ms=1000,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "missing_required_section"


async def test_standing_revise_refuses_a_bad_mode():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=None, where_it_stands="dinner is on",
        mode="told", now_ms=1000,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "bad_mode"


async def test_standing_revise_refuses_a_stale_base_with_the_current_row():
    ledger = InMemoryCognitionDryRunStore()
    prior = Standing(
        platform="signal", conversation_id="room-a", body="was here",
        fold_count=3, updated_at=500,
    )
    write = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=prior, where_it_stands="now here",
        base_revision_id="41", prior_revision_id="42", now_ms=1000,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "stale_base"
    assert write.base_revision_id == "41"


async def test_standing_revise_stale_base_is_the_revision_id_not_the_fold_count():
    """M6: the ingest fold rewrites the head WITHOUT moving `fold_count`, so a
    base taken as the fold count passed as fresh over a changed position. The
    token is the `standing_revision.id`: equal fold counts, different ids, is
    stale; the same id is fresh."""
    ledger = InMemoryCognitionDryRunStore()
    prior = Standing(
        platform="signal", conversation_id="room-a", body="rewritten by the fold",
        fold_count=3, updated_at=900,
    )
    stale = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=prior, where_it_stands="mine",
        base_revision_id="17", prior_revision_id="18", now_ms=1000,
    )
    fresh = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=prior, where_it_stands="mine",
        base_revision_id="18", prior_revision_id="18", now_ms=1000,
    )
    assert (stale.outcome, stale.refusal_reason) == ("refused", "stale_base")
    assert fresh.outcome == "would_commit"


async def test_standing_revise_would_commit_increments_the_would_be_fold_count():
    ledger = InMemoryCognitionDryRunStore()
    prior = Standing(
        platform="signal", conversation_id="room-a", body="was here",
        fold_count=3, updated_at=500,
    )
    write = await dry_run_standing_revise(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", prior=prior, where_it_stands="now here",
        base_revision_id="9", prior_revision_id="9", now_ms=1000,
    )
    assert write.outcome == "would_commit"
    assert write.candidate["fold_count"] == 4


# -- WouldWrite's own shape invariant --------------------------------------

def test_would_write_refuses_a_would_commit_carrying_no_candidate():
    with pytest.raises(ValueError):
        WouldWrite(
            tier="standing", verb="standing_revise", platform="signal",
            conversation_id="room-a", args={}, outcome="would_commit",
            candidate=None,
        )


def test_would_write_refuses_a_refusal_carrying_no_reason():
    with pytest.raises(ValueError):
        WouldWrite(
            tier="standing", verb="standing_revise", platform="signal",
            conversation_id="room-a", args={}, outcome="refused",
            refusal_reason=None,
        )


# -- review follow-up on #625's step 3a-i: validation parity ---------------

async def test_note_create_refuses_a_duplicate_open_title_the_same_way_the_live_verb_does():
    """`nora.agent.tools.note_create` refuses design 41 §7.2's duplicate
    open title by calling `nora.domain.notes.has_duplicate_open_title` —
    the dry run calls the SAME function against `open_titles`, its own
    READ-ONLY input, rather than a second copy of the comparison."""
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_create(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", title="Dinner plans", description="d",
        uncertainty="u", closing_condition="c", body=CONFORMANT_BODY,
        established_by="cognition", now_ms=1000, anchor_date=20261003, shown=SHOWN,
        open_titles=("dinner PLANS",),
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "duplicate_title"
    assert len(ledger.would_writes) == 1


async def test_note_create_with_no_matching_open_title_still_would_commit():
    ledger = InMemoryCognitionDryRunStore()
    write = await dry_run_note_create(
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", title="Dinner plans", description="d",
        uncertainty="u", closing_condition="c", body=CONFORMANT_BODY,
        established_by="cognition", now_ms=1000, anchor_date=20261003, shown=SHOWN,
        open_titles=("something else entirely",),
    )
    assert write.outcome == "would_commit"


async def test_event_propose_refuses_two_open_proposed_events_the_same_way_the_live_verb_does():
    """`GatheringService.create_event`'s I1 backstop
    (`AmbiguousOpenGatheringsError`): two-or-more STILL-OPEN proposed events
    already exist for the conversation. The dry run calls the same
    `nora.domain.gatherings.still_open_gatherings` filter against
    `open_proposed_events`, its own READ-ONLY input."""
    ledger = InMemoryCognitionDryRunStore()
    open_events = (
        _gathering(when=None), _gathering(when=None),
    )
    write = await dry_run_event_propose(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", now_ms=1000, id=uuid.uuid4(), title="dinner",
        created_by="p_ada", when=None, venue_text=None, venue_place_id=None,
        venue_name=None, venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=None,
        status=GatheringStatus.PROPOSED, reminder_id=None,
        created_at=datetime(2026, 9, 29, tzinfo=UTC), open_proposed_events=open_events,
    )
    assert write.outcome == "refused"
    assert write.refusal_reason == "ambiguous_open_gatherings"


async def test_event_propose_ignores_open_events_already_in_the_past():
    """I1's own fix: a PROPOSED event that already happened does not count
    toward the ambiguity backstop."""
    ledger = InMemoryCognitionDryRunStore()
    past = datetime(2020, 1, 1, tzinfo=UTC)
    open_events = (_gathering(when=past), _gathering(when=past))
    write = await dry_run_event_propose(
        shown=SHOWN, reasoning="told", confidence="high", cited_ids=["1"],
        ledger=ledger, session_id=1, unit="unit-1", platform="signal",
        conversation_id="room-a", now_ms=1_700_000_000_000, id=uuid.uuid4(),
        title="dinner", created_by="p_ada", when=None, venue_text=None,
        venue_place_id=None, venue_name=None, venue_address=None, venue_rating=None,
        venue_price_level="", venue_maps_url=None, venue_resolved_at=None,
        status=GatheringStatus.PROPOSED, reminder_id=None,
        created_at=datetime(2026, 9, 29, tzinfo=UTC), open_proposed_events=open_events,
    )
    assert write.outcome == "would_commit"
