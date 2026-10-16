"""A room's members in a full-restore sandbox, read from the restored record.

The sandbox has no signal-cli daemon and no Instagram bridge, so every roster
read would answer `available=False`. Every room-scoped tool that runs under
lineage (`where_been`, `rollup_read`, the presence pre-flight) would then
answer `unavailable`, so a case asking «where have we been together» would
measure a Nora who could not look, which production is not.

`RecordedRoster` answers instead with the people who SPOKE in the room in the
`WINDOW` before the subject's instant, from the restored `messages` rows. Each
person's sender ids are one member (`Roster.members` pairs a person's id forms,
as `listGroups` does); a sender no row resolved to a person is a member of
their own.

This is a declared divergence from production, in both directions:

- a member who never spoke inside the window is missing, so the read is
  narrower than the platform's, which lineage already treats as safe (a strict
  subset of the room can never resolve somebody outside it);
- a person who spoke inside the window and has since LEFT is present, which
  the platform's read would not say. The window bounds that.

Only for a full restore: a snapshot-seeded sandbox holds synthetic speakers,
and every other suite's baseline was measured with the roster dark.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from nora.domain.ports import Roster
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.schema import MESSAGES

#: How far back a speaker still counts as a member. `where_been`'s own cap.
WINDOW = timedelta(days=90)


class RecordedRoster:
    """`RosterSource` for one platform, over the restored `messages` rows."""

    def __init__(self, *, platform: str, database_url: str, now: datetime) -> None:
        self.platform = platform
        self._url = database_url
        self._end_ms = int(now.timestamp() * 1000)
        self._start_ms = int((now - WINDOW).timestamp() * 1000)

    async def roster(self, *, conversation_id: str) -> Roster:
        """Never raises (the port's contract): any failure is `available=False`."""
        try:
            engine = create_readonly_sql_engine(self._url)
            try:
                async with engine.connect() as conn:
                    rows = (await conn.execute(
                        sa.select(MESSAGES.c.sender_id, MESSAGES.c.person_id)
                        .where(
                            MESSAGES.c.platform == self.platform,
                            MESSAGES.c.conversation_id == conversation_id,
                            MESSAGES.c.is_nora == 0,
                            MESSAGES.c.ts >= self._start_ms,
                            MESSAGES.c.ts <= self._end_ms,
                        )
                        .distinct()
                    )).all()
            finally:
                await engine.dispose()
        except Exception:  # noqa: BLE001 - the port never raises; ERROR reads as unavailable
            return Roster(available=False)
        if not rows:
            # Nobody spoke here in the window: «I could not look», never «empty».
            return Roster(available=False)
        by_person: dict[str, set[str]] = {}
        alone: list[frozenset[str]] = []
        for sender_id, person_id in rows:
            if person_id:
                by_person.setdefault(person_id, set()).add(sender_id)
            else:
                alone.append(frozenset({sender_id}))
        members = tuple(frozenset(ids) for _, ids in sorted(by_person.items()))
        return Roster(available=True, members=members + tuple(sorted(alone, key=sorted)))


@contextlib.contextmanager
def recorded_rosters(*, database_url: str, now: datetime) -> Iterator[None]:
    """Make every Signal and Instagram roster `build_dependencies` composes
    inside the block a `RecordedRoster`. Composition holds the instance it built
    for its life, so the patch is needed only while it composes.

    NOT re-entrant, unlike `landed_signal_sends`: that patch is the same for
    every subject, and this one names ONE database, so a second subject
    composing inside the block would read the first one's record. Refused."""
    from nora.app import composition
    from nora.platform.instagram import roster as instagram_roster
    from nora.platform.signal.roster import SignalRoster

    if composition.SignalRoster is not SignalRoster:
        raise RuntimeError("recorded_rosters is already active for another subject")

    def _signal(**_: Any) -> RecordedRoster:
        return RecordedRoster(platform="signal", database_url=database_url, now=now)

    def _instagram(*_: Any, **__: Any) -> RecordedRoster:
        return RecordedRoster(platform="instagram", database_url=database_url, now=now)

    signal_was: Any = composition.SignalRoster
    instagram_was: Any = instagram_roster.InstagramRoster
    setattr(composition, "SignalRoster", _signal)  # noqa: B010
    setattr(instagram_roster, "InstagramRoster", _instagram)  # noqa: B010
    try:
        yield
    finally:
        setattr(composition, "SignalRoster", signal_was)  # noqa: B010
        setattr(instagram_roster, "InstagramRoster", instagram_was)  # noqa: B010
