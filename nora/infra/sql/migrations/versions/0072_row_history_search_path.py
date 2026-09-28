"""row_history_search_path -- `nora_row_history()` (added by `0068`, and the
tables `0069`/`0071` add to its trigger list) names `row_history` and
`row_history_id_seq` unqualified. A data-only `pg_dump` restore clears
`search_path`: `pg_dump` emits `SELECT pg_catalog.set_config('search_path',
'', false);` ahead of the data section (`tests/infra/test_schema_parity.py`'s
own `_normalise` already strips that exact line out of its schema dumps, so
its presence in a data dump is not new), then restores every table with
`COPY` -- which fires the same AFTER INSERT/UPDATE/DELETE row triggers a live
write would. With an empty search_path and an unqualified `row_history`, the
restore fails: `relation "row_history" does not exist`. Found measuring
design/cognition-loop step 2a.

FIX: pin the function's OWN search_path at DDL time (`ALTER FUNCTION ... SET
search_path`) rather than rewriting every statement in the body with
schema-qualified names. A plpgsql function resolves an unqualified name
against the search_path in force when it RUNS, not the one in force when it
was written, so any caller that clears or changes the path -- `pg_dump`'s
data restore, `SET ROLE`, a caller connected with a non-default path --
breaks the same way; pinning the function is the one-statement fix Postgres's
own guidance gives for exactly this class of failure, and it cannot drift
from the body the way a hand-requalified copy could. `nora_row_history_
refuse_truncate()` references no table or sequence -- only `current_setting`
and the special `TG_TABLE_NAME` -- so it cannot hit this failure today, but
is pinned for the same reason `0068` covered every append-triggering write
site with one trigger rather than trusting each call site to remember: a
function this migration owns should not depend on the caller's path for
anything it does not explicitly need.

>>> RE-POINT AT MERGE TIME. <<< `down_revision` is `0071_rollup_builds`, the
head of master when this branch was cut. A parallel branch adds
`0072_lineage` off the same head; whichever merges second re-points onto the
other (AGENTS.md, "Never assume your migration's parent is unclaimed") --
`uv run alembic heads` must print exactly ONE line after the merge.

Test: `tests/integration/test_row_history_restore.py` -- a data-only dump of
`tag_debounce_state` (one row, so `nora_row_history()` fires once) restored
into a second, freshly migrated database. Red before this migration (the
restore's COPY raises `relation "row_history" does not exist`), green after.

Revision ID: 0072_row_history_search_path
Revises: 0071_rollup_builds
Create Date: 2026-09-29
"""

from __future__ import annotations

from alembic import op

revision = "0072_row_history_search_path"
down_revision = "0071_rollup_builds"
branch_labels = None
depends_on = None

#: Both functions `0068` created; `0072`'s docstring explains why both are
#: pinned even though only the first is known to hit the failure today.
_FUNCTIONS = ("nora_row_history", "nora_row_history_refuse_truncate")


def upgrade() -> None:
    for fn in _FUNCTIONS:
        op.execute(f"ALTER FUNCTION {fn}() SET search_path = public, pg_temp")


def downgrade() -> None:
    for fn in _FUNCTIONS:
        op.execute(f"ALTER FUNCTION {fn}() RESET search_path")
