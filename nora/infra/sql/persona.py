"""`SqlPersonaStore`: Persona accounts and their lines (design/unified-memory/45).

**One write path.** `write` opens one transaction, locks the account row,
reads every line of the account, asks `nora.domain.persona.plan_write`
whether the ops may stand, and inserts exactly the rows it returns. A write
that any op fails inserts nothing and returns every refusal; there is no
other way into `persona_line`, so the reasoning-mode rules hold for every
row the tables carry.

`persona_line` is append-only (`nora.infra.sql.history.APPEND_ONLY`): an
account is its chain heads, the rows nothing supersedes. `persona` changes in
place and keeps `row_history`.

# ponytail: lines are not projected into the memory index yet. 45 §1 makes
# them index nodes, but a node is a `memory_search` hit, and shadow changes
# no prompt (45 §6); they join the index at the switch (00 «Sequence» step 8).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.persona import (
    Account,
    PersonaCitable,
    PersonaLine,
    PersonaOp,
    PersonaRefusal,
    PersonaWriteResult,
    plan_write,
)
from nora.infra.sql.schema import PERSONA, PERSONA_LINE

__all__ = ["SqlPersonaStore", "chain_heads"]


def _line(row: sa.Row[Any]) -> PersonaLine:
    return PersonaLine(
        id=str(row.id), persona_key=row.persona_key, part=row.part, kind=row.kind,
        text=row.text, mode=row.mode, citations=tuple(row.citations), state=row.state,
        unit=row.unit, written_at=row.written_at, written_by_session=row.written_by_session,
        draws_on=tuple(str(d) for d in row.draws_on), told_by=row.told_by,
        prediction=row.prediction, prediction_observable=row.prediction_observable,
        prediction_window_end=row.prediction_window_end, verdict=row.verdict,
        verdict_by=row.verdict_by, verdict_at=row.verdict_at,
        supersedes=None if row.supersedes is None else str(row.supersedes),
        support_count=row.support_count, last_supported_at=row.last_supported_at,
        exclude_persons=tuple(row.exclude_persons), as_of=row.as_of,
    )


def _values(line: PersonaLine) -> dict[str, Any]:
    return {
        "id": line.id, "persona_key": line.persona_key, "part": line.part,
        "kind": line.kind, "text": line.text, "mode": line.mode,
        "citations": list(line.citations), "draws_on": list(line.draws_on),
        "told_by": line.told_by, "prediction": line.prediction,
        "prediction_observable": line.prediction_observable,
        "prediction_window_end": line.prediction_window_end, "state": line.state,
        "verdict": line.verdict, "verdict_by": line.verdict_by, "verdict_at": line.verdict_at,
        "supersedes": line.supersedes, "support_count": line.support_count,
        "last_supported_at": line.last_supported_at, "unit": line.unit,
        "exclude_persons": list(line.exclude_persons), "as_of": line.as_of,
        "written_at": line.written_at, "written_by_session": line.written_by_session,
    }


def chain_heads(lines: Sequence[PersonaLine]) -> tuple[tuple[PersonaLine, ...], dict[str, str]]:
    """Every row of one account reduced to its chain heads (the rows nothing
    supersedes), and each row id's head id (`plan_write`'s `aliases`)."""
    successor = {line.supersedes: line.id for line in lines if line.supersedes is not None}
    heads = tuple(line for line in lines if line.id not in successor)
    aliases: dict[str, str] = {}
    for line in lines:
        head = line.id
        while head in successor:
            head = successor[head]
        aliases[line.id] = head
    return heads, aliases


class SqlPersonaStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def account_heads(self, *, key: str) -> tuple[PersonaLine, ...]:
        """The account as it stands: every chain head, in every state."""
        stmt = (sa.select(PERSONA_LINE).where(PERSONA_LINE.c.persona_key == key)
                .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return chain_heads([_line(r) for r in rows])[0]

    async def persona_write(
        self, *, account: Account, ops: Sequence[PersonaOp], citable: PersonaCitable,
        now_ms: int, written_by_session: str, screen: Callable[[str], str | None],
        apply_decay: bool, expected_revision: int | None = None,
    ) -> PersonaWriteResult:
        """Judge and apply one write atomically (45 §4). `expected_revision`
        is the revision the writer read its handles at; a write against a
        newer one is a `conflict` and changes nothing."""
        try:
            return await self._write(
                account=account, ops=ops, citable=citable, now_ms=now_ms,
                written_by_session=written_by_session, screen=screen,
                apply_decay=apply_decay, expected_revision=expected_revision,
            )
        except sa.exc.IntegrityError as exc:
            if "uq_persona_line_supersedes" not in str(exc):
                raise
            return PersonaWriteResult(status="conflict", revision=None)

    async def _write(
        self, *, account: Account, ops: Sequence[PersonaOp], citable: PersonaCitable,
        now_ms: int, written_by_session: str, screen: Callable[[str], str | None],
        apply_decay: bool, expected_revision: int | None,
    ) -> PersonaWriteResult:
        async with self._engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(
                pg_insert(PERSONA).values(
                    key=account.key, entity_kind=account.entity_kind, unit=account.unit,
                    revision=0, updated_at=now_ms,
                ).on_conflict_do_nothing(index_elements=[PERSONA.c.key])
            )
            head = (await conn.execute(
                sa.select(PERSONA.c.entity_kind, PERSONA.c.revision)
                .where(PERSONA.c.key == account.key).with_for_update()
            )).one()
            if head.entity_kind != account.entity_kind:
                await trans.rollback()
                return PersonaWriteResult(status="refused", revision=head.revision, refusals=(
                    PersonaRefusal("malformed", f"this account is a {head.entity_kind}'s"),))
            if expected_revision is not None and head.revision != expected_revision:
                await trans.rollback()
                return PersonaWriteResult(status="conflict", revision=head.revision)
            rows = (await conn.execute(
                sa.select(PERSONA_LINE).where(PERSONA_LINE.c.persona_key == account.key)
                .order_by(PERSONA_LINE.c.written_at, PERSONA_LINE.c.id)
            )).all()
            heads, aliases = chain_heads([_line(r) for r in rows])
            plan = plan_write(
                account=account, heads=heads, ops=ops, citable=citable, now_ms=now_ms,
                written_by_session=written_by_session, screen=screen,
                apply_decay=apply_decay, aliases=aliases,
            )
            if plan.refused:
                await trans.rollback()
                return PersonaWriteResult(
                    status="refused", revision=head.revision, refusals=plan.refusals)
            if plan.inserts:
                await conn.execute(sa.insert(PERSONA_LINE), [_values(x) for x in plan.inserts])
            revision = head.revision + 1
            await conn.execute(
                sa.update(PERSONA).where(PERSONA.c.key == account.key)
                .values(revision=revision, updated_at=now_ms)
            )
            await trans.commit()
        return PersonaWriteResult(
            status="ok", revision=revision, written=tuple(x.id for x in plan.inserts))
