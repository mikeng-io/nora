"""Gather the rows the four measures decide on, and pair the one join that can
be wired wrongly and still look plausible.

`nora.domain.participation_measures` decides; this reads. The split is in that
module's docstring and the important half of it is here: **`refusals` comes
from a different and much weaker instrument than everything else.** Notes,
decisions and messages are durable rows in Postgres; gate refusals are
reconstructed from a Prometheus counter that resets whenever the process does.
So refusals arrive as an ARGUMENT, and
the day a durable per-verdict row exists, only the caller changes.

Read-only throughout, through `create_readonly_sql_engine` — same guarantee
`nora groups` and `nora reminders list` hold, for the same reason: an
instrument that can write is an instrument that can be blamed for what it
measured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa

from nora.domain.participation_measures import (
    Measure,
    Refusal,
    Spoke,
    Tag,
    expiry_without_action,
    holding_refusals,
    ignored,
    overrides,
)
from nora.infra.sql import create_readonly_sql_engine
from nora.infra.sql.schema import DECISION_LOG, MESSAGES, NOTE

__all__ = ["SPOKE_JOIN_TOLERANCE_MS", "GatheredRows", "gather", "measure", "pair_spoke"]

#: How far back from a `participation/spoke` decision row to look for the
#: message it describes. The row is written AFTER the send (`fire.py` records
#: `d.verdict`, the outcome), so the message is always EARLIER, never later —
#: the window is one-sided for that reason and a symmetric one would let a
#: LATER turn's message be claimed by an earlier decision.
#:
#: 120s is a bound, not a measurement: wide enough for a slow deliver, narrow
#: enough that two participation turns in the same room inside it are the
#: pathological case.
#:
#: EPOCH MILLISECONDS, like every instant these measures handle — both
#: `decision_log.ts` and `messages.ts` are ms, and a seconds-valued tolerance
#: here would pair nothing at all while looking like a working join.
SPOKE_JOIN_TOLERANCE_MS = 120_000


@dataclass(frozen=True, slots=True)
class _Note:
    id: str
    established_in: str
    created_at: int
    anchor_date: int | None
    retired_at: int | None


@dataclass(frozen=True, slots=True)
class _Decision:
    note_id: str | None
    verdict: str


@dataclass(frozen=True, slots=True)
class _Message:
    id: str
    conversation_id: str
    ts: int
    quoted_ts: int | None


@dataclass(frozen=True, slots=True)
class GatheredRows:
    """Everything read, kept separately from what was decided.

    Returned rather than consumed in place so a caller can re-run the measures
    at a different threshold without paying for the reads again — every window
    in these measures is a placeholder, so sweeping them is the expected use.
    """

    notes: tuple[_Note, ...]
    reminds: tuple[_Decision, ...]
    tags: tuple[Tag, ...]
    spoke: tuple[Spoke, ...]
    human_messages: tuple[_Message, ...]


def pair_spoke(
    decisions: Sequence[tuple[str, int]],
    nora_messages: Sequence[tuple[str, int]],
    *,
    tolerance: int = SPOKE_JOIN_TOLERANCE_MS,
) -> tuple[Spoke, ...]:
    """Pair each `participation/spoke` decision with the message it describes.

    `decisions` and `nora_messages` are `(conversation_id, ts)`. The result
    carries the MESSAGE's ts, because `ignored` decides engagement by matching
    a reply's `quoted_ts` against it exactly — handed the decision's ts instead
    it would match nothing and report every turn as ignored, silently, with a
    number that looks like a finding.

    A decision with no message inside the window yields NOTHING rather than a
    `Spoke` at the decision's own ts. That loses a turn from the denominator,
    which is the safe direction: a fabricated pairing would put a real turn in
    the numerator of a measure about being ignored.
    """
    by_room: dict[str, list[int]] = {}
    for room, ts in nora_messages:
        by_room.setdefault(room, []).append(ts)
    for tss in by_room.values():
        tss.sort()

    out: list[Spoke] = []
    for room, decided_at in decisions:
        candidates = [
            ts for ts in by_room.get(room, ())
            if decided_at - tolerance <= ts <= decided_at
        ]
        if candidates:
            out.append(Spoke(conversation_id=room, at=candidates[-1]))
    return tuple(out)


async def gather(url: str, *, since: int) -> GatheredRows:
    """Read every durable row the measures need, from `since` onward.

    `since` is EPOCH MILLISECONDS. `decision_log.ts` and `messages.ts` are
    BIGINT ms, so a seconds-valued bound is smaller than every row and the
    window silently becomes ALL TIME.

    One engine for the command's life, disposed by the caller's `async with`.
    `note` is read WITHOUT a `since` filter: a note established months ago can
    pass its anchor inside this window, and bounding it by creation time would
    hide exactly the long-lived commitments `expiry_without_action` exists to
    catch.
    """
    engine = create_readonly_sql_engine(url)
    try:
        async with engine.connect() as conn:
            notes = tuple(
                _Note(r.id, r.established_in, r.created_at, r.anchor_date, r.retired_at)
                for r in (await conn.execute(sa.select(
                    NOTE.c.id, NOTE.c.established_in, NOTE.c.created_at,
                    NOTE.c.anchor_date, NOTE.c.retired_at,
                ))).all()
            )
            decisions = (await conn.execute(
                sa.select(
                    DECISION_LOG.c.writer, DECISION_LOG.c.conversation_id,
                    DECISION_LOG.c.note_id, DECISION_LOG.c.verdict, DECISION_LOG.c.ts,
                ).where(DECISION_LOG.c.ts >= since).order_by(DECISION_LOG.c.ts)
            )).all()
            messages = (await conn.execute(
                sa.select(
                    MESSAGES.c.id, MESSAGES.c.conversation_id, MESSAGES.c.ts,
                    MESSAGES.c.quoted_ts, MESSAGES.c.is_nora,
                ).where(
                    MESSAGES.c.ts >= since, MESSAGES.c.hidden_at.is_(None),
                ).order_by(MESSAGES.c.ts)
            )).all()
    finally:
        await engine.dispose()

    return GatheredRows(
        notes=notes,
        reminds=tuple(
            _Decision(r.note_id, r.verdict) for r in decisions
            if r.writer == "proactive_decide"
        ),
        tags=tuple(
            Tag(r.conversation_id, r.ts) for r in decisions if r.writer == "tag"
        ),
        spoke=pair_spoke(
            [(r.conversation_id, r.ts) for r in decisions
             if r.writer == "participation" and r.verdict == "spoke"],
            [(m.conversation_id, m.ts) for m in messages if m.is_nora],
        ),
        human_messages=tuple(
            _Message(m.id, m.conversation_id, m.ts, m.quoted_ts)
            for m in messages if not m.is_nora
        ),
    )


def measure(
    rows: GatheredRows, refusals: Sequence[Refusal], *, now: int,
) -> tuple[Measure, ...]:
    """The four, in a fixed order.

    Returned as a tuple rather than a dict so the order is explicit and a
    reader never has to sort.
    """
    return (
        expiry_without_action(rows.notes, rows.reminds, now=now),
        holding_refusals(refusals, rows.notes),
        overrides(refusals, rows.tags),
        ignored(rows.spoke, rows.human_messages, tags=rows.tags),
    )
