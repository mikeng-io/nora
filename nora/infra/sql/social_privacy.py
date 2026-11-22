"""An account's privacy, as its latest recorded probe answered it."""

from collections.abc import Collection

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import SOCIAL_POLL


async def public_accounts(
    conn: AsyncConnection, pairs: Collection[tuple[str, str]],
) -> frozenset[tuple[str, str]]:
    """The `(network, person)` pairs among `pairs` whose latest recorded probe
    answered public: the only accounts whose items are placed in units."""
    if not pairs:
        return frozenset()
    p = SOCIAL_POLL
    latest_private = sa.func.array_agg(
        pg.aggregate_order_by(p.c.is_private, p.c.recorded_at.desc(), p.c.id.desc()),
    )[1]
    stmt = (
        sa.select(p.c.network, p.c.person)
        .where(p.c.stage == "probe", sa.tuple_(p.c.network, p.c.person).in_(list(pairs)))
        .group_by(p.c.network, p.c.person)
        .having(latest_private.is_(False))
    )
    return frozenset((r.network, r.person) for r in (await conn.execute(stmt)).all())
