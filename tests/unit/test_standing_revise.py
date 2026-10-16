"""`standing_revise` — the write verb for room Standing.

Two halves, two test classes: `nora.domain.standing_revise` (the pure checks
— mode, schema, stale base) needs no store and is tested directly; the async
wrapper `nora.infra.sql.standing_revise.standing_revise` is tested against
`InMemoryStandingStore`, the same double `tests/contract/
test_standing_store_contract.py` binds to `SqlStandingStore` so the two
cannot drift.
"""

from __future__ import annotations

import pytest

from nora.domain.standing import STANDING_BODY_CEILING_CHARS, Standing, WorkingSetItem
from nora.domain.standing_revise import (
    ALLOWED_MODES,
    check_mode,
    check_schema,
    check_stale_base,
)
from nora.infra.sql.standing_revise import standing_revise
from nora.testing.standing_store import InMemoryStandingStore

# --- nora.domain.standing_revise: the pure checks -------------------------


def test_every_allowed_mode_passes():
    for mode in ALLOWED_MODES:
        assert check_mode(mode) is None


def test_computed_told_and_induced_are_not_standing_modes():
    """Standing allows only `deduced`, `abduced`, `said` — the three modes
    other tiers do not share are exactly the ones refused."""
    for mode in ("computed", "told", "induced"):
        refusal = check_mode(mode)
        assert refusal is not None
        assert refusal.reason == "bad_mode"


def test_where_it_stands_is_required():
    refusal = check_schema(where_it_stands="   ", hold_open=None, i_owe=None)
    assert refusal is not None
    assert refusal.reason == "missing_required_section"


def test_a_given_optional_section_must_not_be_blank():
    ok = check_schema(where_it_stands="x", hold_open="whether B is coming", i_owe=None)
    assert ok is None
    refusal = check_schema(where_it_stands="x", hold_open="   ", i_owe=None)
    assert refusal is not None
    assert refusal.reason == "blank_section"


def test_an_omitted_optional_section_is_never_refused():
    assert check_schema(where_it_stands="x", hold_open=None, i_owe=None) is None


def test_an_oversized_hold_open_or_i_owe_is_refused_by_check_schema():
    """R-27: `check_schema` mirrors the body-length ceiling
    (`STANDING_BODY_CEILING_CHARS`) for both optional sections, not only that
    `hold_open`/`i_owe` are non-blank. Left to `Standing.__post_init__` alone,
    an oversized section would reach `standing_revise` (the impure half) and
    raise `ValueError` OUTSIDE this function, where a caller's `except
    Exception` around the whole call reads it as a store failure. The refusal
    is typed and returned here, before a `Standing` is ever built."""
    too_long = "x" * (STANDING_BODY_CEILING_CHARS + 1)
    for kwargs in (
        {"hold_open": too_long, "i_owe": None},
        {"hold_open": None, "i_owe": too_long},
    ):
        refusal = check_schema(where_it_stands="x", **kwargs)
        assert refusal is not None
        assert refusal.reason == "section_too_long"


def test_an_oversized_where_it_stands_is_refused_by_check_schema():
    too_long = "x" * (STANDING_BODY_CEILING_CHARS + 1)
    refusal = check_schema(where_it_stands=too_long, hold_open=None, i_owe=None)
    assert refusal is not None
    assert refusal.reason == "section_too_long"


def test_no_base_asserted_never_refuses():
    assert check_stale_base(base_revision_id=None, current_revision_id=None) is None
    assert check_stale_base(base_revision_id=None, current_revision_id="7") is None


def test_a_base_against_a_room_with_no_revision_yet_is_stale():
    """`current_revision_id is None` is its own branch, ahead of the generic
    mismatch comparison that would also catch it (any id is `!= None`) — kept
    for the friendlier `detail` rather than a bare mismatch message, so the
    assertion pins that detail specifically: removing the branch would not
    change `reason`, only the message a caller logs."""
    refusal = check_stale_base(base_revision_id="0", current_revision_id=None)
    assert refusal is not None
    assert refusal.reason == "stale_base"
    assert "no revision to write against" in refusal.detail


def test_a_base_matching_the_current_revision_passes():
    assert check_stale_base(base_revision_id="3", current_revision_id="3") is None


def test_a_base_behind_the_current_revision_is_stale():
    refusal = check_stale_base(base_revision_id="2", current_revision_id="3")
    assert refusal is not None
    assert refusal.reason == "stale_base"


# --- nora.infra.sql.standing_revise: the async wrapper ---------------------


async def test_a_cold_start_write_creates_fold_count_one():
    store = InMemoryStandingStore()
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="They are picking a restaurant.",
        last_folded_row_id=10, now_ms=1_000,
    )
    assert revision.committed
    assert revision.standing is not None
    assert revision.standing.fold_count == 1
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.fold_count == 1


async def test_increments_fold_count_is_the_one_thing_that_differs_between_callers():
    """`nora.ingest.stages.standing_event`'s own module docstring: the
    tag path's fold counts; the rollup fold does not. One parameter, both
    behaviours."""
    store = InMemoryStandingStore()
    await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="first", last_folded_row_id=1, now_ms=1_000,
    )
    prior = await store.read(platform="signal", conversation_id="g1")
    tag_fold = await standing_revise(
        store=store, prior=prior, platform="signal", conversation_id="g1",
        where_it_stands="second", last_folded_row_id=2, now_ms=2_000,
    )
    assert tag_fold.standing is not None and tag_fold.standing.fold_count == 2

    rollup_fold = await standing_revise(
        store=store, prior=tag_fold.standing, platform="signal", conversation_id="g1",
        where_it_stands="third", last_folded_row_id=3, now_ms=3_000,
        increments_fold_count=False,
    )
    assert rollup_fold.standing is not None and rollup_fold.standing.fold_count == 2


async def test_a_cold_start_rollup_fold_starts_fold_count_at_zero():
    """The rollup's own cold-start case (`nora.ingest.stages.
    standing_event`'s `_fold_batch`, `prior.fold_count if prior is not None
    else 0`, preserved through this verb)."""
    store = InMemoryStandingStore()
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="first", last_folded_row_id=1, now_ms=1_000,
        increments_fold_count=False,
    )
    assert revision.standing is not None and revision.standing.fold_count == 0


async def test_a_blank_where_it_stands_is_refused_and_writes_nothing():
    store = InMemoryStandingStore()
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="   ", last_folded_row_id=1, now_ms=1_000,
    )
    assert not revision.committed
    assert revision.refusal is not None
    assert revision.refusal.reason == "missing_required_section"
    assert await store.read(platform="signal", conversation_id="g1") is None


async def test_a_bad_mode_is_refused_and_writes_nothing():
    store = InMemoryStandingStore()
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="x", last_folded_row_id=1, now_ms=1_000, mode="told",
    )
    assert not revision.committed
    assert revision.refusal is not None and revision.refusal.reason == "bad_mode"
    assert await store.read(platform="signal", conversation_id="g1") is None


async def test_a_stale_base_is_refused_with_the_current_row():
    store = InMemoryStandingStore()
    await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="first", last_folded_row_id=1, now_ms=1_000,
    )
    prior = await store.read(platform="signal", conversation_id="g1")
    assert prior is not None
    revision = await standing_revise(
        store=store, prior=prior, platform="signal", conversation_id="g1",
        where_it_stands="second", last_folded_row_id=2, now_ms=2_000,
        base_revision_id="rev-old", prior_revision_id="rev-now",
    )
    assert not revision.committed
    assert revision.refusal is not None and revision.refusal.reason == "stale_base"
    assert revision.standing == prior
    # Refused: the store still holds the FIRST position, unchanged.
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None and row.body == "first"


async def test_a_matching_base_revision_id_is_accepted():
    store = InMemoryStandingStore()
    await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="first", last_folded_row_id=1, now_ms=1_000,
    )
    prior = await store.read(platform="signal", conversation_id="g1")
    assert prior is not None
    revision = await standing_revise(
        store=store, prior=prior, platform="signal", conversation_id="g1",
        where_it_stands="second", last_folded_row_id=2, now_ms=2_000,
        base_revision_id="rev-now", prior_revision_id="rev-now",
    )
    assert revision.committed


async def test_an_oversized_hold_open_is_refused_not_raised():
    """The async wrapper (`nora.infra.sql.standing_revise.standing_revise`)
    must never let `Standing.__post_init__`'s `ValueError` escape uncaught: a
    caller's `except Exception` around the whole call would read it as
    `unavailable` («I could not check»), when a schema violation is a
    `refused` outcome the caller's own domain, not its store, is responsible
    for. `check_schema` refuses this before `Standing(...)` is built, so this
    asserts the outcome as the caller sees it: a `Revision` with
    `committed=False`, never an exception."""
    store = InMemoryStandingStore()
    too_long = "x" * (STANDING_BODY_CEILING_CHARS + 1)
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="x", hold_open=too_long, last_folded_row_id=1, now_ms=1_000,
    )
    assert not revision.committed
    assert revision.refusal is not None
    assert revision.refusal.reason == "section_too_long"
    assert await store.read(platform="signal", conversation_id="g1") is None


async def test_hold_open_and_i_owe_round_trip_through_the_verb():
    store = InMemoryStandingStore()
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="They chose C.",
        hold_open="whether B is coming", i_owe="a reply to A's question",
        last_folded_row_id=1, now_ms=1_000,
    )
    assert revision.committed
    row = await store.read(platform="signal", conversation_id="g1")
    assert row is not None
    assert row.hold_open == "whether B is coming"
    assert row.i_owe == "a reply to A's question"


async def test_the_verb_refuses_a_prior_from_a_different_room():
    """`prior` is a parameter, not a read this function makes (module
    docstring) — a caller handing the wrong room's prior is a programming
    error this asserts against rather than silently mis-checking staleness."""
    store = InMemoryStandingStore()
    other_room = Standing(
        platform="signal", conversation_id="OTHER", body="x",
        fold_count=9, updated_at=1,
    )
    with pytest.raises(ValueError, match="different room"):
        await standing_revise(
            store=store, prior=other_room, platform="signal",
            conversation_id="g1", where_it_stands="x",
            last_folded_row_id=1, now_ms=1_000,
        )


async def test_working_set_passes_through_unvalidated():
    """`Found, not yet given` is code-written, never a model argument this
    verb parses — its own validator
    (`nora.engagement.stages.debrief._working_set`) already ran before this
    verb is reached, so this only asserts the value survives unchanged."""
    store = InMemoryStandingStore()
    item = WorkingSetItem(what="a place", source="web_search", found_at=1_000)
    revision = await standing_revise(
        store=store, prior=None, platform="signal", conversation_id="g1",
        where_it_stands="x", working_set=(item,), last_folded_row_id=1, now_ms=1_000,
    )
    assert revision.standing is not None
    assert revision.standing.working_set == (item,)
