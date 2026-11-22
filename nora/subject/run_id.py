"""A run's id: the instant it started, carried in the name.

`sandbox._blank_template` names a private Postgres blank template with it, and
`sweep_stale_private_blank_templates` reads the instant back to tell a killed
run's leftover from a live one: `pg_database` has no creation-timestamp column.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_run_id(now: datetime | None = None) -> str:
    """A run's id: when it started, plus four random hex characters so two
    runs started in the same second (two arms) cannot share one."""
    at = now or datetime.now(UTC)
    return f"{at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:4]}"
