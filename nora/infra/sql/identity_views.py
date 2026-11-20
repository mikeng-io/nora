"""Selected identity families and canonical transcript views; Record rows stay original."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import RoomMessage
from nora.infra.sql.audit import transcript_row
from nora.infra.sql.schema_managed import MANAGED_ALIAS, MANAGED_PERSON

IdentityLookup = Callable[[str], Awaitable[Sequence[str] | None]]


async def stored_identity_family(engine: AsyncEngine, person: str) -> tuple[str, ...] | None:
    """Read the canonical identity and reverse aliases in one database snapshot."""
    a, p = MANAGED_ALIAS.c, MANAGED_PERSON.c
    canonical = sa.select(sa.func.coalesce(
        sa.select(a.person_id).where(a.former_person_id == person).scalar_subquery(),
        sa.select(p.person_id).where(p.person_id == person).scalar_subquery(),
    ).label("person_id")).subquery()
    stmt = sa.select(canonical.c.person_id, a.former_person_id).select_from(
        canonical.outerjoin(MANAGED_ALIAS, a.person_id == canonical.c.person_id)
    ).order_by(a.former_person_id)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    if rows[0].person_id is None:
        return None
    return (rows[0].person_id, *(r.former_person_id for r in rows if r.former_person_id))


async def identity_family(person: str, lookup: IdentityLookup | None) -> tuple[str, ...]:
    if lookup is None:
        return (person,)
    ids = await lookup(person)
    if not ids:
        raise RuntimeError("identity family is unavailable")
    return tuple(dict.fromkeys(ids))


def project_messages(rows: Sequence[Any], family: Sequence[str] = ()) -> list[RoomMessage]:
    """Fold the subject view while retaining original Record, sender and mention fields."""
    out = []
    for source in rows:
        row = transcript_row(source)
        if family and row.person_id in family and row.person_id != family[0]:
            row = replace(row, person_id=family[0], person_name=None)
        out.append(RoomMessage(room=source.conversation_id, row=row))
    return out
