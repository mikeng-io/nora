"""The withdrawal cascade (design/cognition-loop/10-tiers.md §2 rule 7, §8;
70-migration.md §2 row 2c; migration `0075_withdrawal`, DEC-238).

**What it does.** Given one or more withdrawn Record item ids (a message
deleted or unsent, the PII scrub, a person leaving a unit — 70 §9), it walks
the `citation` edge table (`nora.infra.sql.schema.CITATION`) to find every
`note`, `standing`, `culture` and `events` HEAD row whose citation chain reaches
one of them, and marks it `unsupported_at` (epoch ms) — **it never parses a body**
(10 §2 rule 7's own words). Marking is the only effect: the row is neither
deleted nor rewritten, "stops rendering" is the readers' job
(`nora.infra.sql.notes.SqlNoteStore.standing`/`.search`,
`nora.engagement.context`'s culture/standing render reads — see each site's
own comment for why it filters there and not in the store's general `read`;
for an event, `SqlGatheringStore.search`/`.list_for_conversation`/
`.list_for_person`, `GatheringService`'s by-id reads, the cognition grounding,
`held_reminder_ids` for the listing of the reminder it owns, and the heartbeat's
`GatheringScreen`, which HOLDS that reminder rather than announcing or cancelling it),
and re-deriving it is the writer's next session's job (10 §2 rule 7's last
clause) — a job the Cognition and Character adaptors do not exist to do yet
(step 3+), so an unsupported row today simply stays unsupported until an
operator or a later step revises it.

**Which stores** (`stores=`). `forget_person` marks all four
(`ALL_MARKED_STORES`). A platform delete reaches `EVIDENCE_STORES` only: notes,
events and the pending `note_candidate` proposals read off the message, which are
DELETED (a proposal copies the message's words; the live transcript re-proposes
what still stands). Standing and culture are not on that list on purpose, see
`EVIDENCE_STORES`. A row written after the withdrawal is marked when it is written
(`nora.infra.sql.citation.mark_if_cites_hidden`).

**Idempotent.** A row already `unsupported_at` keeps its ORIGINAL timestamp:
`WHERE unsupported_at IS NULL` on every UPDATE means a repeat cascade over
the same withdrawn id marks nothing twice, and `Report.marked` only ever
lists what THIS call newly marked — the property a caller needs to log "N
rows went unsupported just now" rather than "N rows are unsupported in
total".

**Callers.** `forget_person` (`nora.app.person_forget`, a person's whole history
at once) and `nora.app.delete_withdrawal` (one message a platform deleted or
unsent: the touch stage's live path, and `nora withdrawal reconcile-deletes` for
the deletes recorded before it existed). The PII scrub and a person leaving a
unit have no caller: there is no such action yet.

**Must run in the caller's own transaction.** Every function here takes an
`AsyncConnection`, never an `AsyncEngine`: the row that withdrew the message
(a hide, a delete) and the cascade that follows from it are one fact, the
same argument `nora.infra.sql.culture`'s docstring makes for the append and
the head-move riding in one transaction. A caller that ran this in a second
transaction could withdraw a message and crash before the cascade, leaving a
row that renders on a citation that no longer exists — silently, since
nothing would ever re-check it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import CITATION, CULTURE, EVENTS, NOTE, NOTE_CANDIDATE, STANDING

__all__ = [
    "ALL_MARKED_STORES",
    "EVIDENCE_STORES",
    "WithdrawalReport",
    "withdraw_message",
    "withdraw_messages",
]

#: What `nora person forget` marks: every head that cites the withdrawn message,
#: whatever the citation stands for. The default.
ALL_MARKED_STORES: tuple[str, ...] = ("note", "standing", "culture", "event")

#: What a platform DELETE withdraws (`nora.app.delete_withdrawal`): the rows whose
#: citations are EVIDENCE the message was read for (a note or an event cites the
#: messages it rests on), and the pending proposals staged from it
#: (`note_candidate`, deleted: a proposal copies the message's words and has no
#: body to keep once its evidence is gone). NOT Standing or culture: their
#: citation is a WATERMARK (`last_folded_row_id`, the room's newest message when
#: the fold wrote), edges accumulate across revisions, so a delete of any message
#: that was ever a fold boundary would hide the CURRENT head for good while a
#: delete in the middle of a fold left it standing. They re-fold from the live
#: transcript, which no longer holds the hidden message (the orchestrator's
#: decision, 2026-10-02; design/context-assembly/04-every-occasion.md §7).
EVIDENCE_STORES: tuple[str, ...] = ("note", "event", "note_candidate")


@dataclass
class WithdrawalReport:
    """`marked[store]` is the list of citing keys THIS call newly marked
    `unsupported_at` — never a row that was already unsupported (see the
    module docstring's idempotence note)."""

    withdrawn: tuple[str, ...]
    marked: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_marked(self) -> int:
        return sum(len(v) for v in self.marked.values())


async def withdraw_messages(
    conn: AsyncConnection, *, message_ids: Sequence[int], now_ms: int,
    stores: Sequence[str] = ALL_MARKED_STORES,
) -> WithdrawalReport:
    """The cascade over several withdrawn message ids at once — one lookup
    against `citation` rather than one per id, for `forget_person`'s case
    (a person's whole message history withdrawn together).

    `stores` is which kinds of derived row this withdrawal reaches
    (`ALL_MARKED_STORES` by default, `EVIDENCE_STORES` for a delete); a store not
    named is not looked up and not touched. `note_candidate` is withdrawn by
    deleting the pending and refused proposals attributed to the message."""
    ids = [str(i) for i in message_ids]
    report = WithdrawalReport(withdrawn=tuple(ids))
    if not ids:
        return report
    if "note_candidate" in stores:
        doomed = await _withdraw_candidates(conn, message_ids)
        if doomed:
            report.marked["note_candidate"] = doomed
    edges = (await conn.execute(
        sa.select(CITATION.c.citing_store, CITATION.c.citing_key)
        .where(CITATION.c.cited_store == "message", CITATION.c.cited_id.in_(ids))
        .distinct()
    )).all()
    by_store: dict[str, list[str]] = {}
    for row in edges:
        by_store.setdefault(row.citing_store, []).append(row.citing_key)
    if "note" in stores and by_store.get("note"):
        marked = await _mark_note(conn, by_store["note"], now_ms)
        if marked:
            report.marked["note"] = marked
    if "standing" in stores and by_store.get("standing"):
        marked = await _mark_standing(conn, by_store["standing"], now_ms)
        if marked:
            report.marked["standing"] = marked
    if "culture" in stores and by_store.get("culture"):
        marked = await _mark_culture(conn, by_store["culture"], now_ms)
        if marked:
            report.marked["culture"] = marked
    if "event" in stores and by_store.get("event"):
        marked = await _mark_event(conn, by_store["event"], now_ms)
        if marked:
            report.marked["event"] = marked
    return report


async def withdraw_message(
    conn: AsyncConnection, *, message_id: int, now_ms: int,
    stores: Sequence[str] = ALL_MARKED_STORES,
) -> WithdrawalReport:
    """One message withdrawn — a delete, an unsend, one row the PII scrub
    took. See `withdraw_messages` for the batch form and the module
    docstring for what "withdrawn" triggers this."""
    return await withdraw_messages(
        conn, message_ids=[message_id], now_ms=now_ms, stores=stores,
    )


async def _withdraw_candidates(
    conn: AsyncConnection, message_ids: Sequence[int],
) -> list[str]:
    """The staged note proposals read off a withdrawn message, deleted. A
    `note_candidate` of `kind='create'` carries the proposal's title, description
    and body (copied from what the messages said) and `message_ids`, the messages
    it was read off; with its evidence gone it has nothing to keep, and the live
    transcript re-proposes whatever still stands. `pending` and `refused` only:
    a `created` candidate's text is the note's, which the cascade marks. The
    `miss` kind holds a probe, not a message's words."""
    ids = [int(i) for i in message_ids]
    result = await conn.execute(
        sa.delete(NOTE_CANDIDATE)
        .where(
            NOTE_CANDIDATE.c.kind == "create",
            NOTE_CANDIDATE.c.status.in_(("pending", "refused")),
            NOTE_CANDIDATE.c.message_ids.overlap(ids),
        )
        .returning(NOTE_CANDIDATE.c.id)
    )
    return [str(r.id) for r in result.all()]


async def _mark_note(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    result = await conn.execute(
        sa.update(NOTE)
        .where(NOTE.c.id.in_(keys), NOTE.c.unsupported_at.is_(None))
        .values(unsupported_at=now_ms)
        .returning(NOTE.c.id)
    )
    return [str(r.id) for r in result.all()]


async def _mark_standing(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    """`keys` are `<platform>:<conversation_id>` (`citation.citing_key`'s
    shape for this store, `nora.domain.lineage.room_key`'s own format); the
    composite primary key means one UPDATE per room rather than one `IN`."""
    marked: list[str] = []
    for key in keys:
        platform, sep, conversation_id = key.partition(":")
        if not sep or not platform or not conversation_id:
            continue
        result = await conn.execute(
            sa.update(STANDING)
            .where(
                STANDING.c.platform == platform,
                STANDING.c.conversation_id == conversation_id,
                STANDING.c.unsupported_at.is_(None),
            )
            .values(unsupported_at=now_ms)
            .returning(STANDING.c.platform, STANDING.c.conversation_id)
        )
        marked.extend(f"{r.platform}:{r.conversation_id}" for r in result.all())
    return marked


async def _mark_culture(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    result = await conn.execute(
        sa.update(CULTURE)
        .where(CULTURE.c.key.in_(keys), CULTURE.c.unsupported_at.is_(None))
        .values(unsupported_at=now_ms)
        .returning(CULTURE.c.key)
    )
    return [r.key for r in result.all()]


async def _mark_event(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    """`keys` are `events.id` as text (`citation.citing_key`'s shape for this
    store). A key that is not a UUID names no event and is skipped, never an
    error that aborts the cascade over the stores after it.

    The reminder the event owns is not written here: its hold is DERIVED from
    this mark (`SqlGatheringStore.unsupported_reminder_ids`,
    `nora.app.gathering_screen`), so the mark is the one copy of the fact, it
    holds in the same transaction by construction, and clearing it
    (`clear_unsupported`) releases the reminder with nothing else to undo. Only
    the event's CURRENT edges reach it (`record_event_citations` replaces a
    restated field's), so a withdrawn citation an earlier revision rested on
    marks nothing."""
    ids: list[UUID] = []
    for key in keys:
        try:
            ids.append(UUID(key))
        except ValueError:
            continue
    if not ids:
        return []
    result = await conn.execute(
        sa.update(EVENTS)
        .where(EVENTS.c.id.in_(ids), EVENTS.c.unsupported_at.is_(None))
        .values(unsupported_at=now_ms)
        .returning(EVENTS.c.id)
    )
    return [str(r.id) for r in result.all()]
