"""`nora person forget <id> [--apply]` / `nora person unforget <id> [--apply]`
(design/cognition-loop/70-migration.md §2 row 2c, §9; migration
`0075_withdrawal`, DEC-238).

**Soft, per the operator's 2026-09-28 ruling (70 §9).** `forget_person` hides
(`messages.hidden_at`) every message `person_id` authored — never a delete —
and cascades `unsupported` (`nora.app.withdrawal`) to whatever `note`,
`standing` or `culture` head cites one of those messages. `unforget_person`
clears `hidden_at`; it does NOT clear a cascade's `unsupported_at`, because
those two are distinct per 10 §2 rule 7's own text — "hiding" is about the
withdrawn row itself, the cascade is about rows that only CITE it, and
un-hiding a person's messages does not retroactively make an already-marked
row's OTHER evidence sound again. A row a cascade marked is re-derived by its
writer's next session (10 §2 rule 7's last clause), the same as any other
cascade mark; `unforget` is not that.

**Scoped to what exists today** (module docstring of `0075_withdrawal`; 70
§9's fuller list — social items, templates, reference photos, perceptions,
person-scoped Character and Standing — has no table until steps 5, 6 and 8).
`messages` is hidden, and so is every document the person CREATED.

**Documents** (operator ruling 2026-10-01, DEC-259, design/artifact §33). A document
is the forgotten person's when `created_by` is them: one they only appear in as a
participant of its room is not theirs and is left alone. Each is SOFT-DELETED through the
`DocumentStore` port: one more `document_state` row, `deleted`, with `set_by` =
`forget:<person_id>` so a later reader can tell the forget from a person's own delete.
Nothing is purged and every revision stays (the operator can restore one by writing
another state row; Nora and the portal cannot, see below). It is idempotent: a
document already `deleted` is not written again, so a re-run reports 0 and a document a
person had deleted themselves keeps its own `set_by`.

**It stays forgotten.** A document whose latest state row carries the forget prefix
cannot be restored or archived by Nora or the portal (`DocumentService.set_state`
answers it like an unknown id), and its title is left out of the room's `<documents>`
block. `unforget` does NOT restore it either: only the operator, in the database.

**Race and failure.** The write is CONDITIONAL on the state the snapshot saw
(`DocumentStore.set_state(only_if_state=)`, checked under the row lock), so a change a
person made since the snapshot is never overwritten, and only the rows this run wrote
are counted. A failure part-way through the documents does not raise away what was done:
the report carries what was hidden and deleted so far, says so, and a re-run completes it.

**The cognition tables hold ids, never a canonical name** (#637 second review).
`cognition_session.situation` is append-only (`APPEND_ONLY`), so `forget`
cannot redact it after the fact; what it records of a Record row's author is
the `person_id` (`nora.domain.cognition_ground.Grounding.as_dict`), and a name
is rendered to the model at read time and never serialized. A cognition table
therefore needs no cascade step: a forgotten person leaves an id there, which
resolves to nothing a reader can show. (The model's own prose in a would-write
or mirror row is derived text, cited by message id, and is a shadow-only
record no reader renders.)

**Dry run by default, like `nora.app.events_backfill`.** Without `--apply`
this reads and reports what WOULD be hidden or restored; only `--apply`
writes, and the hide-then-cascade is one transaction (the module docstring
of `nora.app.withdrawal` explains why).

**`--clear-unsupported` (this module's `clear_unsupported`) is the operator
lever for an accidentally cascaded row.** `unforget` un-hides messages but
never reverses a cascade mark — see above — so a person forgotten and then
unforgotten by mistake leaves every `note`/`standing`/`culture` row the
cascade touched stuck `unsupported`, even though every message it cited is
visible again. This is a narrow, explicit fix for that one case, not a
general "re-derive support" tool: it clears `unsupported_at` on a row ONLY
when EVERY message it cites (via the `citation` edge table) is now visible
(`hidden_at IS NULL`) — a row with even one still-hidden or dangling citation
is left unsupported, because that citation genuinely is not sound. It is not
scoped to one person's cascade (the `citation` table does not record which
`forget` produced a mark), so it re-checks every currently-unsupported
note/standing/culture row; that is safe because the predicate re-derives
soundness from the citations themselves, not from who ran `unforget`.
Written through the normal write path — a plain `UPDATE` on `note`/
`standing`/`culture`, all three already in `HISTORY_TABLES`
(`0068_system_time_history`) — so clearing is captured as a new
`row_history` version like any other write, same as the cascade's own mark.
Dry run by default, like `forget`/`unforget`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.withdrawal import WithdrawalReport, withdraw_messages
from nora.domain.documents import FORGET_SET_BY_PREFIX, DocumentState, DocumentStore
from nora.infra.sql.schema import CITATION, CULTURE, EVENTS, MESSAGES, NOTE, STANDING

log = logging.getLogger("nora.person_forget")

__all__ = [
    "FORGET_SET_BY_PREFIX",
    "ClearUnsupportedReport",
    "ForgetReport",
    "UnforgetReport",
    "clear_unsupported",
    "forget_person",
    "render_clear_unsupported",
    "render_forget",
    "render_unforget",
    "unforget_person",
]


@dataclass
class ForgetReport:
    person_id: str
    applied: bool
    hidden: int = 0
    already_hidden: int = 0
    cascade: WithdrawalReport | None = None
    documents: int = 0
    documents_already_deleted: int = 0
    #: Documents the snapshot saw whose state changed before this run reached them: left as
    #: they are now (a re-run decides again).
    documents_skipped: int = 0
    #: Non-empty when the run stopped part-way through the documents: what failed.
    failure: str = ""


@dataclass
class UnforgetReport:
    person_id: str
    applied: bool
    restored: int = 0


@dataclass
class ClearUnsupportedReport:
    applied: bool
    cleared: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_cleared(self) -> int:
        return sum(len(v) for v in self.cleared.values())


#: One page of a person's documents. A person has a handful; the loop only exists so no
#: read is unbounded.
_DOCUMENT_PAGE = 200


async def _documents_created_by(
    documents: DocumentStore, person_id: str, states: tuple[DocumentState, ...],
) -> list[tuple[UUID, DocumentState]]:
    """Every document `person_id` created whose state is in `states`, with the state it was
    in, read before any write so the pages do not shift under the loop."""
    found: list[tuple[UUID, DocumentState]] = []
    while True:
        page = await documents.list_created_by(
            person_id=person_id, states=states, limit=_DOCUMENT_PAGE, offset=len(found),
        )
        found.extend((d.id, d.state) for d in page)
        if len(page) < _DOCUMENT_PAGE:
            return found


async def forget_person(
    engine: AsyncEngine, *, documents: DocumentStore, person_id: str, apply: bool, now_ms: int,
) -> ForgetReport:
    async with engine.connect() as conn:
        already_hidden = (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGES).where(
                MESSAGES.c.person_id == person_id,
                MESSAGES.c.hidden_at.is_not(None),
            )
        )).scalar_one()
        candidate_ids = (await conn.execute(
            sa.select(MESSAGES.c.id).where(
                MESSAGES.c.person_id == person_id, MESSAGES.c.hidden_at.is_(None),
            )
        )).scalars().all()
    report = ForgetReport(
        person_id=person_id, applied=apply, already_hidden=already_hidden,
    )
    live = await _documents_created_by(documents, person_id, ("active", "archived"))
    report.documents_already_deleted = len(
        await _documents_created_by(documents, person_id, ("deleted",))
    )
    if not apply:
        report.hidden = len(candidate_ids)
        report.documents = len(live)
        return report
    if candidate_ids:
        async with engine.begin() as conn:
            result = await conn.execute(
                sa.update(MESSAGES)
                .where(MESSAGES.c.id.in_(candidate_ids))
                .values(hidden_at=now_ms)
                .returning(MESSAGES.c.id)
            )
            hidden_ids = [r.id for r in result.all()]
            report.hidden = len(hidden_ids)
            report.cascade = await withdraw_messages(
                conn, message_ids=hidden_ids, now_ms=now_ms,
            )
    try:
        for document_id, seen in live:
            # Conditional on the state the snapshot saw, under the row lock: a person who
            # archived or restored it since is not overwritten, and it is not counted.
            if await documents.set_state(
                document_id=document_id, state="deleted",
                set_by=f"{FORGET_SET_BY_PREFIX}{person_id}", only_if_state=(seen,),
            ):
                report.documents += 1
            else:
                report.documents_skipped += 1
    except Exception as exc:  # reported with what was done, never swallowed
        log.exception("person forget stopped part-way through the documents")
        report.failure = type(exc).__name__
    return report


async def unforget_person(
    engine: AsyncEngine, *, person_id: str, apply: bool,
) -> UnforgetReport:
    async with engine.connect() as conn:
        hidden_count = (await conn.execute(
            sa.select(sa.func.count()).select_from(MESSAGES).where(
                MESSAGES.c.person_id == person_id,
                MESSAGES.c.hidden_at.is_not(None),
            )
        )).scalar_one()
    report = UnforgetReport(person_id=person_id, applied=apply)
    if not apply:
        report.restored = hidden_count
        return report
    if not hidden_count:
        return report
    async with engine.begin() as conn:
        result = await conn.execute(
            sa.update(MESSAGES)
            .where(
                MESSAGES.c.person_id == person_id,
                MESSAGES.c.hidden_at.is_not(None),
            )
            .values(hidden_at=None)
            .returning(MESSAGES.c.id)
        )
        report.restored = len(result.all())
    return report


def _no_hidden_or_dangling_citation(
    citing_store: str, citing_key_col: sa.ColumnElement[Any],
) -> Any:
    """A row qualifies to have `unsupported_at` cleared when it has NO
    citation edge whose target is missing (a dangling id, never resolved to
    a live `unsupported_at` clear) or still hidden — i.e. EVERY message it
    cites is visible. A row with zero citations at all also has none
    unresolved, so it qualifies too (it was marked by some other future
    cascade kind, not a message withdrawal, and nothing here contradicts
    that)."""
    bad = (
        sa.select(sa.literal(1))
        .select_from(
            CITATION.outerjoin(
                MESSAGES, MESSAGES.c.id == sa.cast(CITATION.c.cited_id, sa.BigInteger),
            )
        )
        .where(
            CITATION.c.citing_store == citing_store,
            CITATION.c.citing_key == citing_key_col,
            CITATION.c.cited_store == "message",
            sa.or_(MESSAGES.c.id.is_(None), MESSAGES.c.hidden_at.is_not(None)),
        )
    )
    return ~sa.exists(bad)


async def clear_unsupported(engine: AsyncEngine, *, apply: bool) -> ClearUnsupportedReport:
    """The `--clear-unsupported` lever: clear `unsupported_at` on every
    `note`/`standing`/`culture` row whose citations are ALL visible again.
    See the module docstring for the predicate and why it is not scoped to
    one person."""
    report = ClearUnsupportedReport(applied=apply)
    async with engine.connect() as conn:
        note_ids = (await conn.execute(
            sa.select(NOTE.c.id).where(
                NOTE.c.unsupported_at.is_not(None),
                _no_hidden_or_dangling_citation("note", sa.cast(NOTE.c.id, sa.Text)),
            )
        )).scalars().all()
        standing_rows = (await conn.execute(
            sa.select(STANDING.c.platform, STANDING.c.conversation_id).where(
                STANDING.c.unsupported_at.is_not(None),
                _no_hidden_or_dangling_citation(
                    "standing",
                    STANDING.c.platform + sa.literal(":") + STANDING.c.conversation_id,
                ),
            )
        )).all()
        culture_keys = (await conn.execute(
            sa.select(CULTURE.c.key).where(
                CULTURE.c.unsupported_at.is_not(None),
                _no_hidden_or_dangling_citation("culture", CULTURE.c.key),
            )
        )).scalars().all()
        event_ids = (await conn.execute(
            sa.select(EVENTS.c.id).where(
                EVENTS.c.unsupported_at.is_not(None),
                _no_hidden_or_dangling_citation("event", sa.cast(EVENTS.c.id, sa.Text)),
            )
        )).scalars().all()
    if note_ids:
        report.cleared["note"] = [str(i) for i in note_ids]
    if standing_rows:
        report.cleared["standing"] = [f"{r.platform}:{r.conversation_id}" for r in standing_rows]
    if culture_keys:
        report.cleared["culture"] = list(culture_keys)
    if event_ids:
        report.cleared["event"] = [str(i) for i in event_ids]
    if not apply or not report.total_cleared:
        return report
    async with engine.begin() as conn:
        if note_ids:
            await conn.execute(
                sa.update(NOTE).where(NOTE.c.id.in_(note_ids)).values(unsupported_at=None)
            )
        for r in standing_rows:
            await conn.execute(
                sa.update(STANDING)
                .where(
                    STANDING.c.platform == r.platform,
                    STANDING.c.conversation_id == r.conversation_id,
                )
                .values(unsupported_at=None)
            )
        if culture_keys:
            await conn.execute(
                sa.update(CULTURE).where(CULTURE.c.key.in_(culture_keys))
                .values(unsupported_at=None)
            )
        if event_ids:
            await conn.execute(
                sa.update(EVENTS).where(EVENTS.c.id.in_(event_ids))
                .values(unsupported_at=None)
            )
    return report


def render_forget(report: ForgetReport) -> list[str]:
    verb = "hid" if report.applied else "would hide"
    lines = [
        f"person forget {report.person_id} "
        f"({'APPLIED' if report.applied else 'dry run: nothing written'})",
        f"  {verb} {report.hidden} message(s)"
        + (f"; {report.already_hidden} already hidden" if report.already_hidden else ""),
        f"  {'deleted' if report.applied else 'would delete'} {report.documents} document(s)"
        + (
            f"; {report.documents_already_deleted} already deleted"
            if report.documents_already_deleted
            else ""
        )
        + (
            f"; {report.documents_skipped} changed since the read, left as they are"
            if report.documents_skipped
            else ""
        ),
    ]
    if report.failure:
        lines.append(
            f"  FAILED part-way ({report.failure}): what is listed above was written and "
            "stays written; re-run this command with --apply to complete the rest"
        )
    if report.cascade is not None and report.cascade.total_marked:
        for store, keys in report.cascade.marked.items():
            lines.append(f"  cascade: {len(keys)} {store} row(s) now unsupported")
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines


def render_unforget(report: UnforgetReport) -> list[str]:
    verb = "restored" if report.applied else "would restore"
    lines = [
        f"person unforget {report.person_id} "
        f"({'APPLIED' if report.applied else 'dry run: nothing written'})",
        f"  {verb} {report.restored} message(s)",
    ]
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines


def render_clear_unsupported(report: ClearUnsupportedReport) -> list[str]:
    verb = "cleared" if report.applied else "would clear"
    lines = [
        f"person unforget --clear-unsupported "
        f"({'APPLIED' if report.applied else 'dry run: nothing written'})",
    ]
    if not report.cleared:
        lines.append(f"  {verb} 0 row(s)")
    for store, keys in report.cleared.items():
        lines.append(f"  {verb} {len(keys)} {store} row(s)")
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
