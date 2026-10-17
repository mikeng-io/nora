"""The areas a place lies in: a district and a city, from the geocoder's
structured address components.

design/unified-memory/00-intent.md «Sequence» step 3a, 25-memory-graph.md §6a.
Three stores gain the two columns, each beside the place it describes:

- `events` and `event_revision`: `venue_district`, `venue_city`, resolved with
  the venue (`venue_place_id`) and snapshotted into the revision like the
  venue's other fields;
- `geocode_cache`: `district`, `city`, from the same reverse-geocode as the
  cached address;
- `rollup_outing`: `district`, `city`, read from `geocode_cache` near the
  outing's centre when the outing is at no saved place (R-35).

Nullable, no backfill: a row resolved before this has no area, and the memory
index gives its place no `located_in` edge until it is resolved again.

Follows `0115_watermark_bigint`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0116_place_area
Revises: 0115_watermark_bigint
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0116_place_area"
down_revision = "0115_watermark_bigint"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("events", "venue_district"), ("events", "venue_city"),
    ("event_revision", "venue_district"), ("event_revision", "venue_city"),
    ("geocode_cache", "district"), ("geocode_cache", "city"),
    ("rollup_outing", "district"), ("rollup_outing", "city"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.Text, nullable=True))


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)
