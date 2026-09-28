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
Only `messages` is hidden.

**Dry run by default, like `nora.app.events_backfill`.** Without `--apply`
this reads and reports what WOULD be hidden or restored; only `--apply`
writes, and the hide-then-cascade is one transaction (the module docstring
of `nora.app.withdrawal` explains why).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.withdrawal import WithdrawalReport, withdraw_messages
from nora.infra.sql.schema import MESSAGES

__all__ = [
    "ForgetReport",
    "UnforgetReport",
    "forget_person",
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


@dataclass
class UnforgetReport:
    person_id: str
    applied: bool
    restored: int = 0


async def forget_person(
    engine: AsyncEngine, *, person_id: str, apply: bool, now_ms: int,
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
    if not apply:
        report.hidden = len(candidate_ids)
        return report
    if not candidate_ids:
        return report
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


def render_forget(report: ForgetReport) -> list[str]:
    verb = "hid" if report.applied else "would hide"
    lines = [
        f"person forget {report.person_id} "
        f"({'APPLIED' if report.applied else 'dry run: nothing written'})",
        f"  {verb} {report.hidden} message(s)"
        + (f"; {report.already_hidden} already hidden" if report.already_hidden else ""),
    ]
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
