"""The live-clock journey's declared initial state.

A `snapshot:` on a live-clock case names the instant the journey's store
starts from: the full SQL restore (`sandbox_as_of`) fills the case's
sandbox exactly once, before the subject is composed, and the journey's
own clock keeps running live — the restore is initial state, never the
clock and never a replay of what the source's Nora wrote after the cut.
A case with no `snapshot:` keeps the generated blank world
(`sandbox_database`), and a restoration declaration with its anchor
missing refuses before the App or any model runs rather than silently
anchoring at now.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.infra.sql import create_readonly_sql_engine, history
from nora.infra.sql.schema import MESSAGES
from nora.infra.sql.snapshot_at import SeedReport

from .eval_evidence import current
from .sandbox import sandbox_as_of, sandbox_database

#: The keys a case's `snapshot:` states, and nothing else: the room and the
#: instant. Culture key and unit resolve from the deployment's policy entry
#: at `obtain()`, so a case cannot state them wrongly.
SNAPSHOT_KEYS = frozenset({"platform", "conversation_id", "before_ms"})


@dataclass(frozen=True, slots=True)
class Anchor:
    """The instant a live-clock journey's store starts from: the room and
    the cut, exactly as the case's `snapshot:` stated them."""

    platform: str
    conversation_id: str
    before_ms: int
    at: datetime


@dataclass(frozen=True, slots=True)
class JourneyState:
    """The sandbox `journey_initial_state` opened, and what to prove about
    it before the App runs.

    `anchor` and `report` are `None` on the generated blank-world path —
    an unanchored case restored nothing and has no receipt to carry.
    `live_ms` is the journey's own start, carried beside the anchor
    because the cut and the live clock are different facts.
    """

    database_url: str
    anchor: Anchor | None
    report: SeedReport | None
    source_url: str
    live_ms: int

    async def verify(self, subject: Any) -> None:
        """Refuse an initial state that is not the one declared, then record
        the declared cut and the restore's own receipt in canonical evidence.

        The refusals, each before the App or any model runs: the subject's
        database is not the one this state was restored into; the world
        composed a room that is not the anchor's; the subject's clock is
        not after the cut (a frozen one would replay, not continue); the
        source held no row for the room at the cut; or the restored room's
        message ids are not the identity of the source's own as-of read —
        the same check `restored_room` makes for a frozen draw.
        """
        evidence = current()
        anchor = self.anchor
        if anchor is None:
            if evidence is not None:
                evidence.record(
                    "journey_initial_state",
                    declared=False, live_start_ms=self.live_ms)
            return
        room = (anchor.platform, anchor.conversation_id)
        if subject.database_url != self.database_url:
            raise ValueError(
                "the subject's database is not the one the declared instant "
                "was restored into; a journey may not compose against a "
                "store its case did not declare")
        if subject.room != room:
            raise ValueError(
                f"the generated world composes room {subject.room!r} but the "
                f"case's declared anchor names {room!r}; a live journey "
                "starts from its own declared room's state, not a "
                "lookalike's")
        clock_ms = int(subject.deps.clock().timestamp() * 1000)
        if clock_ms <= anchor.before_ms:
            raise ValueError(
                f"the subject's clock reads {clock_ms} at or before the "
                f"declared cut {anchor.before_ms}; the restore is the "
                "journey's past and a subject stopped at it would replay "
                "rather than continue")
        held = await _held_ids(self.source_url, anchor)
        if not held:
            raise ValueError(
                f"the source held no row for {room[0]}/{room[1]} at "
                f"{anchor.at.isoformat()}; a moment before the room's first "
                "line is not a moment, and an unknown id reads exactly the "
                "same")
        target = create_async_engine(self.database_url)
        try:
            async with target.connect() as conn:
                restored = {int(i) for i in (await conn.execute(
                    sa.select(MESSAGES.c.id).where(
                        MESSAGES.c.platform == anchor.platform,
                        MESSAGES.c.conversation_id == anchor.conversation_id,
                    ))).scalars()}
        finally:
            await target.dispose()
        if restored != held:
            raise ValueError(
                f"the restore holds {len(restored)} rows for the room and "
                f"the source held {len(held)} at the instant "
                f"({len(restored - held)} extra, {len(held - restored)} "
                "missing); a full restore copies ids verbatim, so the row "
                "map is the identity only when the two agree")
        if evidence is not None:
            report = self.report
            evidence.record(
                "journey_initial_state",
                declared=True,
                platform=anchor.platform,
                conversation_id=anchor.conversation_id,
                before_ms=anchor.before_ms,
                at=anchor.at.isoformat(),
                live_start_ms=self.live_ms,
                subject_clock_ms=clock_ms,
                copied=dict(report.copied) if report is not None else {},
                orphaned=dict(report.orphaned) if report is not None else {},
                history_rows=report.history_rows if report is not None else 0,
                room_message_ids=sorted(restored))


async def _held_ids(source_url: str, anchor: Anchor) -> set[int]:
    source = create_readonly_sql_engine(source_url)
    try:
        held: set[int] = set()
        async with source.connect() as conn:
            async for batch in history.as_of(conn, "messages", anchor.at):
                held |= {
                    int(image["id"]) for image in batch
                    if image.get("platform") == anchor.platform
                    and image.get("conversation_id") == anchor.conversation_id
                }
    finally:
        await source.dispose()
    return held


def _anchor(snapshot: Any, case_name: str, *, live_ms: int) -> Anchor | None:
    if snapshot is None:
        return None
    if not isinstance(snapshot, Mapping):
        raise ValueError(
            f"case {case_name!r}'s `snapshot:` must map "
            f"{sorted(SNAPSHOT_KEYS)}; got {snapshot!r}")
    unknown = sorted(set(snapshot) - SNAPSHOT_KEYS)
    missing = sorted(SNAPSHOT_KEYS - set(snapshot))
    if unknown or missing:
        raise ValueError(
            f"case {case_name!r}'s `snapshot:` is missing {missing} and "
            f"states {unknown}; a live journey's anchor names exactly "
            f"{sorted(SNAPSHOT_KEYS)} — the room's culture key and unit "
            "come from the deployment's policy entry, and its window from "
            "production's config")
    platform = snapshot["platform"]
    conversation_id = snapshot["conversation_id"]
    if (not isinstance(platform, str) or not platform
            or not isinstance(conversation_id, str) or not conversation_id):
        raise ValueError(
            f"case {case_name!r}'s `snapshot:` platform and "
            f"conversation_id must be non-empty strings; got "
            f"{platform!r}/{conversation_id!r}")
    before_ms = snapshot["before_ms"]
    if type(before_ms) is not int:
        raise ValueError(
            f"case {case_name!r}'s `snapshot:` before_ms must be an integer "
            f"millisecond instant; got {before_ms!r}")
    if before_ms > live_ms:
        raise ValueError(
            f"case {case_name!r}'s `snapshot:` before_ms {before_ms} is "
            f"after the journey's live start {live_ms}; an anchored "
            "journey starts from a past instant, never a future one")
    return Anchor(
        platform=platform, conversation_id=conversation_id,
        before_ms=before_ms,
        at=datetime.fromtimestamp(before_ms / 1000, tz=UTC))


@contextlib.asynccontextmanager
async def journey_initial_state(
    snapshot: Any, *, case_name: str, source_url: str,
    template: str | None, full_restore: bool, now: datetime,
) -> AsyncGenerator[JourneyState]:
    """Open the case's store for a live-clock journey.

    `snapshot` is the case's own `metadata.snapshot`: a declared anchor, a
    missing anchor, or a declaration to refuse. An anchored case gets the
    full restore at its instant ONCE — rows keep their own ids and
    timestamps, and whatever the journey writes afterward stays in the
    sandbox. An unanchored case keeps the generated blank world; a
    `full_restore` spec without a `snapshot:` names no instant, a
    restoration declaration with its anchor missing.
    """
    live_ms = int(now.timestamp() * 1000)
    anchor = _anchor(snapshot, case_name, live_ms=live_ms)
    if anchor is None:
        if full_restore:
            raise ValueError(
                f"case {case_name!r} asks for a restored live journey but "
                "states no `snapshot:` — the instant the restore holds. "
                "The live clock is not an anchor: a restore at now would "
                "silently stand in for a moment nobody declared")
        async with sandbox_database(source_url, template=template) as url:
            yield JourneyState(url, None, None, source_url, live_ms)
        return
    async with sandbox_as_of(
        source_url, anchor.at, template=template,
    ) as (url, report):
        yield JourneyState(url, anchor, report, source_url, live_ms)
