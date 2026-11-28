"""Selected identity families and canonical transcript views; Record rows stay original."""

from collections.abc import Awaitable, Callable, Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

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

