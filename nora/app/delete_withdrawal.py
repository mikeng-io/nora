"""A message its platform deleted or unsent is withdrawn: hidden, and everything
derived from it marked `unsupported` (design/cognition-loop/10-tiers.md §2 rule
7; 70-migration.md §2 row 2c; migration `0075_withdrawal`, DEC-238).

**What was broken.** All four platform parsers emitted the delete and the touch
stage recorded it as a `message_delete` row, and nothing else happened: the
message kept rendering in every transcript, and every note, Standing, culture
head and event that cited it kept rendering too. `withdraw_message` had no
caller. This module is the caller: `SqlDeleteWithdrawer.withdraw`, handed to
the touch stage (`nora.ingest.stages.touch`), and `reconcile_deletes`, the
one-off that applies the same thing to the deletes recorded before it existed.

**What a delete withdraws, and what it does not** (the orchestrator's decision,
2026-10-02, DEC-276). The rows whose citations are EVIDENCE: notes and events that
cite the message, and the staged `note_candidate` proposals read off it
(`nora.app.withdrawal.EVIDENCE_STORES`). NOT Standing or culture. Their citation
is a watermark (`last_folded_row_id`, the room's newest message when the fold
wrote), edges pile up across revisions, so a delete of any message that was ever a
fold boundary marked the CURRENT head unsupported for good while a delete in the
middle of a fold left it standing. They re-fold from the live transcript, which no
longer holds the hidden message. Residual risk, for the operator: a fold keeps what
it wrote in its own raw prior (`previous_account`, `standing_prior`) until it
rewrites it, so a deleted message's content can survive there. `nora person forget`
keeps marking every store.

**One transaction.** The hide (`messages.hidden_at`, the same hide `nora person
forget` uses) and the cascade (`nora.app.withdrawal`) are one fact
(`nora.app.withdrawal`'s docstring), so a cascade that raises rolls the hide
back: `cascade_failed`, the message still visible, counted. The delete's own
`message_delete` record is committed FIRST, in its own transaction
(`nora.ingest.stages.touch`), so a withdrawal that failed is always findable:
the pipeline retries it a bounded number of times, and `reconcile_deletes` runs at
every process start (the process restarts several times a day) and as `nora
withdrawal reconcile-deletes`. If even the record cannot be written the platform
event is dropped, counted and logged without a word of the message, and the hide is
still attempted.

**Idempotent.** The hide is `WHERE hidden_at IS NULL`, so a redelivered or
second delete of the same message hides nothing twice (`already_hidden`, the
original timestamp kept). The cascade runs again regardless: it marks only rows
not already `unsupported`, and a delete redelivered after something that cites the
message was written must still reach it. A row written AFTER the hide is marked at
write time (`nora.infra.sql.citation.mark_if_cites_hidden`). A delete whose target
is not on record writes nothing (`not_stored`). Two concurrent withdrawals of one
row serialise on its row lock: one hides, the other reports `already_hidden`.

**Who may delete.** Where only the author can (Signal, WhatsApp:
`touch.AUTHOR_ONLY_DELETES`) a delete from anyone else is refused
(`refused_not_author`) and leaves no record: a WhatsApp REVOKE is forwarded by the
bridge with no check the server could make (end-to-end encrypted), and a group
admin's removal of a member's message cannot be told from a forgery without group
metadata, so it is NOT honoured and the message stays visible. Discord and
Instagram events name no deleter and are the platform's word.

**Nora's own message** (a send she, or a moderator, took back) is withdrawn the
same way. A message gone from the room is not something she may quote, and a note
that cites her own line goes `unsupported` like one that cites a member's. The
cost: Cognition's `own_sends` marked an unsent send `deleted` and kept its place
(DEC-245); a withdrawn one is now absent from that read as from every other (the
marker remains only for a delete whose hide has not landed). Reaching it is another
matter: her rows carry no platform message id, so only a Signal delete (found by its
timestamp) ever resolves to one; on the other three platforms her unsend is
`not_stored`.

**What a deleted message leaves behind, stated.** The hide is permanent: nothing
here un-hides, `nora person unforget` leaves a delete-hidden message hidden
(`UnforgetReport.kept_deleted`), and a head the cascade marked stays withdrawn (the
operator's separate ruling on «a withdrawn head never comes back»). **Honcho keeps
what it was fed:** it has no per-message delete (`HonchoClient` offers a
whole-session delete and nothing narrower), so the text of a message Honcho already
read, an earlier edit's feed, a photo's comprehension, and the conclusions derived
from them may remain (`design/context-assembly/04-every-occasion.md` §7). Nothing
in Nora feeds a hidden row again: every replay reads through `transcript_select` or
`RecordStore.unattributed`, which both exclude it, and the comprehension job asks
whether its message is withdrawn before the vision call and before the feed.

Logs and counts here carry ids and numbers only, never a body or a name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.app.withdrawal import EVIDENCE_STORES, withdraw_message
from nora.domain.ports import DeleteWithdrawal
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGES

log = logging.getLogger("nora.delete_withdrawal")

__all__ = [
    "ReconcileReport",
    "SqlDeleteWithdrawer",
    "reconcile_deletes",
    "render_reconcile",
]


async def _withdraw_in(
    conn: AsyncConnection, *, message_id: int, now_ms: int,
) -> tuple[DeleteWithdrawal, dict[str, int]]:
    """Hide the row and run the cascade on `conn`: the caller owns the
    transaction. The cascade runs even for a row that was already hidden: it is
    idempotent, and a row hidden by something that did not cascade is then
    still covered."""
    newly = (await conn.execute(
        sa.update(MESSAGES)
        .where(MESSAGES.c.id == message_id, MESSAGES.c.hidden_at.is_(None))
        .values(hidden_at=now_ms)
        .returning(MESSAGES.c.id)
    )).first()
    if newly is None:
        held = (await conn.execute(
            sa.select(MESSAGES.c.id).where(MESSAGES.c.id == message_id)
        )).first()
        if held is None:
            return DeleteWithdrawal(outcome="not_stored"), {}
    report = await withdraw_message(
        conn, message_id=message_id, now_ms=now_ms, stores=EVIDENCE_STORES,
    )
    marked = {store: len(keys) for store, keys in report.marked.items()}
    return (
        DeleteWithdrawal(
            outcome="hidden" if newly is not None else "already_hidden",
            marked=report.total_marked,
        ),
        marked,
    )


class SqlDeleteWithdrawer:
    """Satisfies `nora.domain.ports.DeleteWithdrawer`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def withdraw(self, *, message_id: int, now_ms: int) -> DeleteWithdrawal:
        try:
            async with self._engine.begin() as conn:
                result, _ = await _withdraw_in(conn, message_id=message_id, now_ms=now_ms)
        except Exception:
            # Rolled back as a whole by `begin()`: the message is still
            # visible, which is the failure this reports.
            log.exception("delete withdrawal of message %d failed; rolled back", message_id)
            return DeleteWithdrawal(outcome="cascade_failed")
        return result


@dataclass
class ReconcileReport:
    applied: bool
    #: Distinct messages with a `message_delete` record whose row is still visible.
    visible: int = 0
    hidden: int = 0
    already_hidden: int = 0
    not_stored: int = 0
    failed: int = 0
    #: Derived rows newly marked `unsupported`, by store.
    marked: dict[str, int] = field(default_factory=dict)

    @property
    def total_marked(self) -> int:
        return sum(self.marked.values())


async def reconcile_deletes(
    engine: AsyncEngine, *, apply: bool, now_ms: int,
) -> ReconcileReport:
    """Withdraw every message that has a `message_delete` record and is still
    visible: the deletes recorded before the touch stage withdrew them, a hide
    that was rolled back (`cascade_failed`), and a message `nora person
    unforget` un-hid.

    The dry run runs the SAME withdrawal and rolls the whole thing back, so its
    counts are exactly what `--apply` writes, not an estimate. `--apply` is one
    transaction per message, as the live path is, so one failure leaves the rest
    done and a re-run finishes it."""
    async with engine.connect() as conn:
        ids = list((await conn.execute(
            sa.select(MESSAGE_DELETE.c.message_id)
            .join(MESSAGES, MESSAGES.c.id == MESSAGE_DELETE.c.message_id)
            .where(MESSAGES.c.hidden_at.is_(None))
            .distinct()
            .order_by(MESSAGE_DELETE.c.message_id)
        )).scalars().all())
    report = ReconcileReport(applied=apply, visible=len(ids))
    if not ids:
        return report
    if not apply:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                # ONE transaction, so its counts are exactly what a sequence of
                # `--apply` transactions writes (a note citing two deleted messages
                # counts once). It holds a row lock on every message and derived
                # row it touches until it ends, so it is bounded: a lock or a
                # statement that waits long is an error, never a stall on ingest.
                await conn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                await conn.execute(sa.text("SET LOCAL statement_timeout = '60s'"))
                for message_id in ids:
                    try:
                        async with conn.begin_nested():
                            result, marked = await _withdraw_in(
                                conn, message_id=message_id, now_ms=now_ms,
                            )
                    except Exception:
                        log.exception("dry-run withdrawal of message %d failed", message_id)
                        report.failed += 1
                        continue
                    _count(report, result, marked)
            finally:
                await trans.rollback()
        return report
    for message_id in ids:
        try:
            async with engine.begin() as conn:
                result, marked = await _withdraw_in(conn, message_id=message_id, now_ms=now_ms)
        except Exception:
            log.exception("withdrawal of message %d failed; rolled back", message_id)
            report.failed += 1
            continue
        _count(report, result, marked)
    return report


def _count(
    report: ReconcileReport, result: DeleteWithdrawal, marked: dict[str, int],
) -> None:
    if result.outcome == "hidden":
        report.hidden += 1
    elif result.outcome == "already_hidden":
        report.already_hidden += 1
    elif result.outcome == "not_stored":
        report.not_stored += 1
    for store, n in marked.items():
        report.marked[store] = report.marked.get(store, 0) + n


def render_reconcile(report: ReconcileReport) -> list[str]:
    verb = "hid" if report.applied else "would hide"
    lines = [
        "withdrawal reconcile-deletes "
        f"({'APPLIED' if report.applied else 'dry run: nothing written'})",
        f"  {report.visible} deleted message(s) still visible",
        f"  {verb} {report.hidden} message(s)"
        + (f"; {report.already_hidden} already hidden" if report.already_hidden else "")
        + (f"; {report.not_stored} no longer on record" if report.not_stored else ""),
    ]
    for store, n in sorted(report.marked.items()):
        lines.append(f"  cascade: {n} {store} row(s) now unsupported")
    if report.failed:
        lines.append(
            f"  FAILED for {report.failed} message(s), rolled back and still visible; "
            "re-run to retry them"
        )
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
