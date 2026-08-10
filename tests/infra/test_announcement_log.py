"""`SqlAnnouncementLog` — the claim that makes R-42 true, against real Postgres.

Against a REAL database and never a dict, for a reason specific to this class:
the whole guarantee lives in `INSERT … ON CONFLICT DO NOTHING` and the rowcount
Postgres returns from it. A double would be re-implementing the very statement
under test, and would pass whatever it was written to believe.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.infra.sql.announcements import ANNOUNCING, DONE, FAILED, SqlAnnouncementLog
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations

T0 = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
ROOM = {"platform": "signal", "conversation_id": "g1"}


@pytest.fixture
async def log(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlAnnouncementLog(engine)
    finally:
        await engine.dispose()


async def test_the_first_claim_wins_and_the_second_does_not(log):
    """The mechanism, in one assertion pair. Not "a boolean is returned" — the
    SECOND call is the one that matters, and it is what a restart makes."""
    assert await log.claim(version="V3", now=T0, **ROOM) is True
    assert await log.claim(version="V3", now=T0, **ROOM) is False


async def test_a_version_claimed_but_never_resolved_still_bars_a_second_claim(log):
    """The crash-mid-turn case, which is the ONE a state check would get wrong.

    The row sits in `announcing` because the process died between claiming and
    delivering — and the group may well have heard the message already. R-42
    (and R-37b's argument before it) says stay quiet: nothing links a claim to a
    delivered message, so a retry double-posts anything that died after egress.
    """
    await log.claim(version="V3", now=T0, **ROOM)
    assert await log.announced(version="V3") == [("signal", "g1", ANNOUNCING)]

    assert await log.claim(version="V3", now=T0, **ROOM) is False


async def test_a_failed_announcement_is_never_retried(log):
    """A turn that timed out or errored resolves the row FAILED — and a failed
    row bars a re-claim exactly as a successful one does. Losing a note visibly
    beats posting it twice; `nora_version_announcements_total` is its trace."""
    await log.claim(version="V3", now=T0, **ROOM)
    await log.resolve(version="V3", state=FAILED, outcome="failed_timeout", **ROOM)

    assert await log.claim(version="V3", now=T0, **ROOM) is False
    assert await log.announced(version="V3") == [("signal", "g1", FAILED)]


async def test_each_room_is_claimed_separately(log):
    """She lives in more than one room, each room is its own turn that can
    separately fail, and one room's claim must not consume another's."""
    assert await log.claim(
        version="V3", platform="signal", conversation_id="g1", now=T0) is True
    assert await log.claim(
        version="V3", platform="signal", conversation_id="g2", now=T0) is True

    assert await log.announced(version="V3") == [
        ("signal", "g1", ANNOUNCING), ("signal", "g2", ANNOUNCING),
    ]


async def test_a_different_version_is_a_different_claim(log):
    """The bar is per VERSION: announcing V3 must not make V4 unannounceable,
    or the feature would fire exactly once in the lifetime of a deployment."""
    await log.claim(version="V3", now=T0, **ROOM)
    await log.resolve(version="V3", state=DONE, outcome="sent", **ROOM)

    assert await log.claim(version="V4", now=T0, **ROOM) is True
    assert await log.announced(version="V3") == [("signal", "g1", DONE)]


async def test_resolving_records_the_outcome_the_fire_path_reported(log):
    await log.claim(version="V3", now=T0, **ROOM)
    await log.resolve(version="V3", state=DONE, outcome="sent", **ROOM)

    assert await log.announced(version="V3") == [("signal", "g1", DONE)]


async def test_resolving_a_row_nobody_claimed_writes_nothing(log):
    """`resolve` is an UPDATE and deliberately not an upsert: if the row is
    gone, something deleted a claimed announcement, and silently re-creating it
    would hide that — and would hand back a version that is now re-claimable."""
    await log.resolve(version="V3", state=DONE, outcome="sent", **ROOM)

    assert await log.announced(version="V3") == []
