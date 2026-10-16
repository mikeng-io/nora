"""`nora events backfill-units` (`nora.app.events_backfill`, DEC-237): sets `unit_id` on every
pre-migration event (`unit_id=""`) from its conversation's `policy.toml`
unit. Dry run by default; only `--apply` writes, through the SAME
`GatheringStore.update` `update_event` writes through."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from nora.app.events_backfill import backfill_unit_ids, render
from nora.domain.gatherings import Gathering, GatheringStatus
from nora.policy import load_policy
from nora.testing.gatherings import FakeGatheringStore

pytestmark = pytest.mark.asyncio

_POLICY = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g-friends"
label = "Friends"
persons = "*"
unit = "friends"

[units.friends]
workspace = "67"
"""


def _event(*, conversation_id: str, unit_id: str, title: str) -> Gathering:
    return Gathering(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        title=title,
        created_by="miles",
        when=None,
        venue_text=None,
        venue_place_id=None,
        venue_name=None,
        venue_address=None,
        venue_rating=None,
        venue_price_level="",
        venue_maps_url=None,
        venue_resolved_at=None,
        status=GatheringStatus.PROPOSED,
        reminder_id=None,
        created_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
        unit_id=unit_id,
    )


@pytest.fixture
def policy(tmp_path):
    p = tmp_path / "policy.toml"
    p.write_text(_POLICY)
    return load_policy(p)


async def test_dry_run_reports_legacy_events_but_writes_nothing(policy):
    store = FakeGatheringStore()
    legacy = await store.create(
        _event(conversation_id="g-friends", unit_id="", title="Pre-migration dinner"),
    )

    report = await backfill_unit_ids(policy=policy, store=store, apply=False)

    assert report.applied is False
    assert report.legacy == 1
    assert report.updated == 0
    back = await store.get(legacy.id)
    assert back.unit_id == "", "a dry run must write nothing"


async def test_apply_sets_unit_id_through_the_normal_update_path(policy):
    store = FakeGatheringStore()
    legacy = await store.create(
        _event(conversation_id="g-friends", unit_id="", title="Pre-migration dinner"),
    )

    report = await backfill_unit_ids(policy=policy, store=store, apply=True)

    assert report.applied is True
    assert report.legacy == 1
    assert report.updated == 1
    back = await store.get(legacy.id)
    assert back.unit_id == "friends"
    # Through `store.update`, not a raw write: a new revision was minted,
    # the same way `update_event` mints one — this is what makes the write
    # visible to `row_history` (see the module docstring) rather than a
    # bespoke bypass of the store's own write path.
    revisions = await store.revisions_for(legacy.id)
    assert len(revisions) == 2


async def test_already_assigned_unit_id_is_never_touched(policy):
    store = FakeGatheringStore()
    already_set = await store.create(
        _event(conversation_id="g-friends", unit_id="friends", title="Already unit-scoped"),
    )

    report = await backfill_unit_ids(policy=policy, store=store, apply=True)

    assert report.legacy == 0
    assert report.updated == 0
    revisions = await store.revisions_for(already_set.id)
    assert len(revisions) == 1, "no write at all — nothing to backfill"


async def test_a_room_not_in_policy_is_never_touched(policy):
    """`g-orphan` is not in `policy.toml` at all — no unit can be named for
    it, so its events are skipped by never being visited, not by a check
    that could get the refusal wrong."""
    store = FakeGatheringStore()
    orphan = await store.create(
        _event(conversation_id="g-orphan", unit_id="", title="Not in policy.toml"),
    )

    report = await backfill_unit_ids(policy=policy, store=store, apply=True)

    assert {r.conversation_id for r in report.rooms} == {"g-friends"}, (
        "a room policy.toml names with no unit is never even visited"
    )
    back = await store.get(orphan.id)
    assert back.unit_id == ""


async def test_render_reports_dry_run_and_applied(policy):
    store = FakeGatheringStore()
    await store.create(_event(conversation_id="g-friends", unit_id="", title="Dinner"))

    dry = await backfill_unit_ids(policy=policy, store=store, apply=False)
    lines = "\n".join(render(dry))
    assert "dry run" in lines
    assert "re-run with --apply" in lines

    applied = await backfill_unit_ids(policy=policy, store=store, apply=True)
    lines = "\n".join(render(applied))
    assert "APPLIED" in lines


async def test_render_dry_run_reports_the_legacy_count_not_updated(policy):
    """`report.updated` is 0 on every dry run (`backfill_unit_ids` never
    attempts a write without `--apply`), so a dry run's "would set" line must
    come from `legacy` — the count of what a subsequent `--apply` would
    change — or it always prints "would set 0" regardless of how many
    pre-migration events there are. Two legacy events in one room, one
    already-assigned event that must not be counted."""
    store = FakeGatheringStore()
    await store.create(_event(conversation_id="g-friends", unit_id="", title="Dinner"))
    await store.create(_event(conversation_id="g-friends", unit_id="", title="Lunch"))
    await store.create(
        _event(conversation_id="g-friends", unit_id="friends", title="Already set"),
    )

    dry = await backfill_unit_ids(policy=policy, store=store, apply=False)
    assert dry.updated == 0, "the bug this guards: updated is never set on a dry run"
    assert dry.legacy == 2
    lines = "\n".join(render(dry))
    assert "would set 2" in lines
    assert "would set 0" not in lines


