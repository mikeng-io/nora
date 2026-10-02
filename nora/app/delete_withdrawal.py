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

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.app.withdrawal import EVIDENCE_STORES, withdraw_message, withdraw_messages
from nora.domain.delete_authorship import AUTHOR_ONLY_DELETES, deleter_is_author
from nora.domain.ports import DeleteWithdrawal
from nora.infra.sql.audit import orphan_delete_of
from nora.infra.sql.schema import MESSAGE_DELETE, MESSAGE_EDIT, MESSAGES, ORPHAN_DELETE
from nora.platform.whatsapp.parser import revoker_ids

log = logging.getLogger("nora.delete_withdrawal")

__all__ = [
    "ORPHAN_TTL_MS",
    "ReconcileReport",
    "SqlDeleteWithdrawer",
    "genuine_delete_ids",
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


#: An orphan delete older than this that never found its message is purged
#: (`orphan_expired`): nothing is going to arrive for it, and the table is not an archive.
ORPHAN_TTL_MS = 30 * 24 * 3600 * 1000


@dataclass(frozen=True)
class _Record:
    """One `message_delete` record beside the message it names."""

    message_id: int
    platform: str
    deleter_sender_id: str
    deleter_person_id: str | None
    raw_json: str | None
    author_sender_id: str
    author_person_id: str | None
    hidden: bool

    @property
    def genuine(self) -> bool:
        """The SAME authorship rule ingest applies (`nora.domain.delete_authorship`),
        applied to a record. Before this change a WhatsApp delete was recorded with no
        author check, so a record can be a non-author's, which must not be applied: on
        Signal and WhatsApp it counts only when its deleter is the message's author, by
        id, by person, or by any id form the event carried (WhatsApp's `raw_json` holds
        the phone, LID and JID)."""
        if self.platform not in AUTHOR_ONLY_DELETES:
            return True
        ids: tuple[str, ...] = ()
        if self.platform == "whatsapp" and self.raw_json:
            try:
                event = json.loads(self.raw_json)
            except ValueError:
                event = None
            if isinstance(event, dict):
                ids = revoker_ids(event)
        return deleter_is_author(
            author_sender_id=self.author_sender_id, author_person_id=self.author_person_id,
            deleter_sender_id=self.deleter_sender_id,
            deleter_person_id=self.deleter_person_id, deleter_ids=ids,
        )


async def _records(
    conn: AsyncConnection, *, message_ids: Sequence[int] | None = None,
) -> list[_Record]:
    stmt = (
        sa.select(
            MESSAGE_DELETE.c.message_id, MESSAGES.c.platform,
            MESSAGE_DELETE.c.sender_id, MESSAGE_DELETE.c.person_id,
            MESSAGE_DELETE.c.raw_json, MESSAGES.c.sender_id.label("author"),
            MESSAGES.c.person_id.label("author_person"),
            MESSAGES.c.hidden_at.is_not(None).label("hidden"),
        )
        .join(MESSAGES, MESSAGES.c.id == MESSAGE_DELETE.c.message_id)
        .order_by(MESSAGE_DELETE.c.id)
    )
    if message_ids is not None:
        stmt = stmt.where(MESSAGE_DELETE.c.message_id.in_(list(message_ids)))
    return [
        _Record(
            message_id=r.message_id, platform=r.platform, deleter_sender_id=r.sender_id,
            deleter_person_id=r.person_id, raw_json=r.raw_json,
            author_sender_id=r.author, author_person_id=r.author_person,
            hidden=bool(r.hidden),
        )
        for r in (await conn.execute(stmt)).all()
    ]


async def genuine_delete_ids(
    conn: AsyncConnection, *, message_ids: Sequence[int],
) -> set[int]:
    """Of `message_ids`, those a GENUINE delete record names (`_Record.genuine`).
    `nora person unforget` uses it: a record that fails the authorship rule is not a
    delete, so it must not keep a message hidden."""
    if not message_ids:
        return set()
    return {r.message_id for r in await _records(conn, message_ids=message_ids) if r.genuine}


@dataclass
class ReconcileReport:
    applied: bool
    #: Distinct messages a GENUINE `message_delete` record names whose row is still visible.
    visible: int = 0
    hidden: int = 0
    already_hidden: int = 0
    not_stored: int = 0
    failed: int = 0
    #: Distinct messages whose only delete records fail the authorship rule: not
    #: applied (a forged or admin revoke recorded before the rule existed).
    refused: int = 0
    #: Derived rows newly marked `unsupported`, by store.
    marked: dict[str, int] = field(default_factory=dict)
    #: Rows marked by re-scanning the edges of messages ALREADY hidden by a delete: a
    #: note or event written at the moment of the delete that the cascade could not see.
    healed: dict[str, int] = field(default_factory=dict)
    #: Held deletes applied to a message that has since been recorded.
    orphans_applied: int = 0
    #: Held deletes older than `ORPHAN_TTL_MS` that found nothing, purged.
    orphans_expired: int = 0

    @property
    def total_marked(self) -> int:
        return sum(self.marked.values())

    @property
    def total_healed(self) -> int:
        return sum(self.healed.values())


async def reconcile_deletes(
    engine: AsyncEngine, *, apply: bool, now_ms: int,
) -> ReconcileReport:
    """Finish what the live path may have left, at every process start and as
    `nora withdrawal reconcile-deletes`:

    1. every message a GENUINE `message_delete` record names that is still visible is
       hidden and cascaded (a hide that was rolled back, a process that died first, a
       message something un-hid). A record that fails the authorship rule is skipped
       and counted `refused`, never applied;
    2. the edges of messages a delete ALREADY hid are scanned again, so a note or event
       written at the moment of the delete, which the cascade could not see, is marked
       (`healed`);
    3. every held delete (`orphan_delete`) whose message has since been recorded is
       applied, and one older than `ORPHAN_TTL_MS` that found nothing is purged.

    The dry run runs the SAME withdrawal and rolls the whole thing back, so its
    counts are exactly what `--apply` writes, not an estimate. `--apply` is one
    transaction per message, as the live path is, so one failure leaves the rest
    done and a re-run finishes it."""
    async with engine.connect() as conn:
        records = await _records(conn)
    genuine = {r.message_id for r in records if r.genuine}
    visible = sorted({r.message_id for r in records if r.genuine and not r.hidden})
    hidden = sorted({r.message_id for r in records if r.genuine and r.hidden})
    refused = {r.message_id for r in records} - genuine
    report = ReconcileReport(
        applied=apply, visible=len(visible), refused=len(refused - set(visible)),
    )
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
                for message_id in visible:
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
                await _heal(conn, report, hidden, now_ms)
                await _orphans(conn, report, now_ms)
            finally:
                await trans.rollback()
        return report
    for message_id in visible:
        try:
            async with engine.begin() as conn:
                result, marked = await _withdraw_in(conn, message_id=message_id, now_ms=now_ms)
        except Exception:
            log.exception("withdrawal of message %d failed; rolled back", message_id)
            report.failed += 1
            continue
        _count(report, result, marked)
    try:
        async with engine.begin() as conn:
            await _heal(conn, report, hidden, now_ms)
    except Exception:
        log.exception("re-scan of the edges of hidden messages failed; rolled back")
        report.failed += 1
    async with engine.connect() as conn:
        orphan_ids = [
            int(i) for i in (await conn.execute(
                sa.select(ORPHAN_DELETE.c.id).order_by(ORPHAN_DELETE.c.id)
            )).scalars()
        ]
    for orphan_id in orphan_ids:
        try:
            async with engine.begin() as conn:
                await _orphans(conn, report, now_ms, only=orphan_id)
        except Exception:
            log.exception("held delete %d could not be resolved; rolled back", orphan_id)
            report.failed += 1
    return report


async def _heal(
    conn: AsyncConnection, report: ReconcileReport, hidden_ids: Sequence[int], now_ms: int,
) -> None:
    """The cascade over messages a delete already hid, in ONE batch lookup: marks only
    what is not yet marked, so it is idempotent and costs one query at a start."""
    if not hidden_ids:
        return
    cascade = await withdraw_messages(
        conn, message_ids=list(hidden_ids), now_ms=now_ms, stores=EVIDENCE_STORES,
    )
    for store, keys in cascade.marked.items():
        report.healed[store] = report.healed.get(store, 0) + len(keys)


async def _orphans(
    conn: AsyncConnection, report: ReconcileReport, now_ms: int, *, only: int | None = None,
) -> None:
    """Resolve held deletes against what is stored: a message that matches (the same
    key and authorship rule as the pipeline's check, `RecordStore.match_orphan_deletes`)
    gets the delete's record, the hide and the cascade and the row is removed, in one
    transaction; one that matches nothing and is older than `ORPHAN_TTL_MS` is purged."""
    stmt = sa.select(ORPHAN_DELETE).order_by(ORPHAN_DELETE.c.id)
    if only is not None:
        stmt = stmt.where(ORPHAN_DELETE.c.id == only)
    for row in (await conn.execute(stmt)).all():
        orphan = orphan_delete_of(row)
        key = []
        if orphan.handle:
            key.append(MESSAGES.c.platform_message_id == orphan.handle)
        if orphan.sent_ts is not None:
            key.append(sa.or_(
                MESSAGES.c.ts == orphan.sent_ts,
                sa.exists().where(
                    MESSAGE_EDIT.c.message_id == MESSAGES.c.id,
                    MESSAGE_EDIT.c.ts == orphan.sent_ts,
                ),
            ))
        candidates = (await conn.execute(
            sa.select(MESSAGES.c.id, MESSAGES.c.sender_id, MESSAGES.c.person_id)
            .where(
                MESSAGES.c.platform == orphan.platform,
                MESSAGES.c.conversation_id == orphan.conversation_id,
                sa.or_(*key),
            )
            .order_by(MESSAGES.c.id)
        )).all()
        author_only = orphan.platform in AUTHOR_ONLY_DELETES
        target = next((
            c for c in candidates
            if not author_only or deleter_is_author(
                author_sender_id=c.sender_id, author_person_id=c.person_id,
                deleter_sender_id=orphan.deleter_id,
                deleter_person_id=orphan.deleter_person_id,
                deleter_ids=orphan.deleter_ids,
            )
        ), None)
        if target is None:
            if orphan.recorded_at < now_ms - ORPHAN_TTL_MS:
                await conn.execute(
                    sa.delete(ORPHAN_DELETE).where(ORPHAN_DELETE.c.id == orphan.id)
                )
                report.orphans_expired += 1
            continue
        await conn.execute(sa.insert(MESSAGE_DELETE).values(
            message_id=target.id, sender_id=orphan.deleter_id,
            person_id=orphan.deleter_person_id, ts=orphan.event_ts, raw_json=None,
        ))
        _, marked = await _withdraw_in(conn, message_id=target.id, now_ms=now_ms)
        await conn.execute(sa.delete(ORPHAN_DELETE).where(ORPHAN_DELETE.c.id == orphan.id))
        report.orphans_applied += 1
        for store, n in marked.items():
            report.marked[store] = report.marked.get(store, 0) + n


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
    if report.refused:
        lines.append(
            f"  {report.refused} message(s) have only a delete record from someone who is "
            "not their author: not applied"
        )
    for store, n in sorted(report.marked.items()):
        lines.append(f"  cascade: {n} {store} row(s) now unsupported")
    for store, n in sorted(report.healed.items()):
        lines.append(f"  re-scan of hidden messages: {n} {store} row(s) now unsupported")
    if report.orphans_applied or report.orphans_expired:
        lines.append(
            f"  held deletes: {report.orphans_applied} applied, "
            f"{report.orphans_expired} expired and purged"
        )
    if report.failed:
        lines.append(
            f"  FAILED for {report.failed} item(s), rolled back; re-run to retry them"
        )
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
