"""The retry schedule shared by the embedding worker's two queues."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.base import ReadOnlyColumnCollection

EMBED_BACKOFF_BASE_MS = 60_000
EMBED_BACKOFF_CAP_MS = 6 * 3_600_000
EMBED_MAX_ATTEMPTS = 8


def embed_due(row: ReadOnlyColumnCollection[Any, Any], now_ms: int) -> sa.ColumnElement[bool]:
    """A first attempt is immediate and later attempts wait exponentially."""
    wait = sa.cast(sa.func.least(
        EMBED_BACKOFF_BASE_MS * sa.func.power(2, sa.func.least(row.embed_attempts - 1, 30)),
        EMBED_BACKOFF_CAP_MS,
    ), sa.BigInteger)
    return sa.and_(
        row.embed_attempts < EMBED_MAX_ATTEMPTS,
        sa.or_(row.embed_attempts == 0, row.embedded_at <= now_ms - wait),
    )


def failed_attempts(attempts: Any, *, counts: bool) -> Any:
    """Outages back off without bringing an input to the cap."""
    bumped = attempts + 1
    return bumped if counts else sa.func.greatest(
        attempts, sa.func.least(bumped, EMBED_MAX_ATTEMPTS - 1),
    )
