"""A full-restore sandbox's room members are the people who spoke there.

`nora.subject.sandbox_roster` replaces the platform's directory read, which the
sandbox cannot make, so every lineage tool stops answering `unavailable` on a
real restored room. What it must and must not count is pinned here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.subject.sandbox_roster import WINDOW, RecordedRoster, recorded_rosters

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
DAY = 86_400_000


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _say(engine: Any, *rows: tuple[str, str, str | None, int, int]) -> None:
    async with engine.begin() as conn:
        for conversation, sender, person, ts, is_nora in rows:
            await conn.execute(sa.text(
                "INSERT INTO messages (platform, conversation_id, conversation_type, "
                "sender_id, ts, is_nora, person_id) VALUES "
                "('signal', :c, 'group', :s, :t, :n, :p)"
            ), {"c": conversation, "s": sender, "t": ts, "n": is_nora, "p": person})


async def test_the_members_are_the_speakers_inside_the_window(engine: Any, pg_url: str) -> None:
    await _say(
        engine,
        # one person reaching Nora under two id forms is ONE member
        ("g1", "+852111", "p1", NOW_MS - DAY, 0),
        ("g1", "aci-p1", "p1", NOW_MS - 2 * DAY, 0),
        # a sender no row resolved is a member of their own
        ("g1", "+852999", None, NOW_MS - DAY, 0),
        # Nora is in the room; she is not a member of it
        ("g1", "nora-self", None, NOW_MS - DAY, 1),
        # outside the window, after the instant, another room: none count
        ("g1", "+852222", "p2", NOW_MS - int(WINDOW / timedelta(milliseconds=1)) - DAY, 0),
        ("g1", "+852333", "p3", NOW_MS + DAY, 0),
        ("g2", "+852444", "p4", NOW_MS - DAY, 0),
    )
    got = await RecordedRoster(platform="signal", database_url=pg_url, now=NOW).roster(
        conversation_id="g1")
    assert got.available
    assert set(got.members) == {frozenset({"+852111", "aci-p1"}), frozenset({"+852999"})}


async def test_a_room_nobody_spoke_in_is_unavailable_not_empty(engine: Any, pg_url: str) -> None:
    got = await RecordedRoster(platform="signal", database_url=pg_url, now=NOW).roster(
        conversation_id="nobody-here")
    assert got.available is False


async def test_a_failed_read_is_unavailable_and_never_raises() -> None:
    got = await RecordedRoster(
        platform="signal", database_url="postgresql+asyncpg://nobody@127.0.0.1:1/none",
        now=NOW,
    ).roster(conversation_id="g1")
    assert got.available is False


def test_the_patch_is_for_one_subject_and_is_put_back() -> None:
    from nora.app import composition
    from nora.platform.instagram import roster as instagram_roster
    from nora.platform.signal.roster import SignalRoster

    original_instagram = instagram_roster.InstagramRoster
    with recorded_rosters(database_url="postgresql+asyncpg://x/y", now=NOW):
        assert isinstance(composition.SignalRoster(base_url="u", account="a"), RecordedRoster)
        assert isinstance(instagram_roster.InstagramRoster("u"), RecordedRoster)
        with pytest.raises(RuntimeError), recorded_rosters(
                database_url="postgresql+asyncpg://x/z", now=NOW):
            pass
    assert composition.SignalRoster is SignalRoster
    assert instagram_roster.InstagramRoster is original_instagram
