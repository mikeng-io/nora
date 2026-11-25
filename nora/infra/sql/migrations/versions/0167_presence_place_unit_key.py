"""Store one saved-place head per observing unit, preserving its old history."""

import sqlalchemy as sa
from alembic import op

revision = "0167_presence_place_unit_key"
down_revision = "0166_social_item_unit_key"
branch_labels = None
depends_on = None

_TABLE = "presence_places"
_KEY = "uq_presence_places_unit"


_PLATFORM_HEAD_KEY = "uq_platform_object_state_object"
_PLATFORM_HEAD_COLUMNS = ("platform", "account_key", "conversation_id", "thread",
                          "source_kind", "object_ref")


_TOMBSTONE_NULLABLE = {
    "presence_places": ("name", "lat", "lng"),
    "fetched_pages": ("blob_key", "provenance"),
    "platform_object_state": ("source_state", "coverage"),
    "platform_observation": ("payload", "coverage"),
    "presence_corridors": ("sequence", "sample_count"),
    "presence_derived_places": ("lat", "lng", "radius_m", "place_confidence"),
    "presence_occupancy": ("dwell_ms",),
    "presence_place_rhythm": ("sample_count",),
    "presence_stays": ("lat", "lng", "radius_m", "fix_count"),
    "presence_trips": ("distance_m", "avg_speed_kmh", "fix_count"),
    "rollup_colocation": ("lat", "lng", "radius_m", "at_home_of", "homes_unknown", "stay_count"),
    "rollup_daybook": (
        "stays",
        "stay_ms",
        "home_ms",
        "colocation_ms",
        "with_persons",
        "outings",
        "rooms",
        "messages",
        "reactions",
    ),
    "rollup_outing": ("lat", "lng"),
    "rollup_response": (
        "replies",
        "reactions",
        "edits",
        "follows",
        "responders",
        "named_persons",
        "outcome",
    ),
    "rollup_tie": (
        "replies_ab",
        "replies_ba",
        "mentions_ab",
        "mentions_ba",
        "reactions_ab",
        "reactions_ba",
        "colocation_ms",
    ),
    "source_health": ("detail",),
}


_ITEM_HISTORY = {
    "fetched_pages": ("fetched_at", ("conversation_id", "url")),
    "social_item": ("recorded_at", ("id",)),
    "social_item_count": ("recorded_at", ("id",)),
    "social_item_state": ("recorded_at", ("id",)),
    "social_poll": ("recorded_at", ("id",)),
    "source_health": ("recorded_at", ("id",)),
}


def _capture_item_history() -> None:
    for table, (at, columns) in _ITEM_HISTORY.items():
        key = ", ".join(f"'{column}', x.{column}" for column in columns)
        args = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608
            f"SELECT '{table}', jsonb_build_object({key}), 'backfill', to_jsonb(x), x.{at} "
            f'FROM "{table}" x'
        )
        op.execute(
            f'CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE ON "{table}" '
            f"FOR EACH ROW EXECUTE FUNCTION nora_row_history({args})"
        )


def _live_content_check(columns: tuple[str, ...]) -> str:
    expr = " AND ".join(f"{column} IS NOT NULL" for column in columns)
    return f"state = 'tombstoned' OR ({expr})"


def _history_key(*columns: str) -> None:
    op.execute("DROP TRIGGER nora_row_history ON presence_places")
    keys = ", ".join(f"'{column}'" for column in columns)
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        f"ON presence_places FOR EACH ROW EXECUTE FUNCTION nora_row_history({keys})"
    )


def upgrade() -> None:
    op.drop_constraint(_PLATFORM_HEAD_KEY, "platform_object_state", type_="unique")
    op.create_unique_constraint(
        _PLATFORM_HEAD_KEY, "platform_object_state", [*_PLATFORM_HEAD_COLUMNS, "unit"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("presence_places_pkey", _TABLE, type_="primary")
    op.create_unique_constraint(
        _KEY,
        _TABLE,
        ["circle_id", "place_id", "unit"],
        postgresql_nulls_not_distinct=True,
    )
    op.execute(
        """UPDATE row_history
           SET row_key = row_key || '{"unit": null}'::jsonb
           WHERE table_name = 'presence_places' AND NOT jsonb_exists(row_key, 'unit')"""
    )
    _history_key("circle_id", "place_id", "unit")
    _capture_item_history()
    for table, columns in _TOMBSTONE_NULLABLE.items():
        for column in columns:
            op.alter_column(table, column, nullable=True)
        op.create_check_constraint(
            f"ck_{table}_tombstone_content",
            table,
            _live_content_check(columns),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("""
        SELECT 1 FROM row_history
        WHERE table_name = 'presence_places' AND jsonb_typeof(row_key -> 'unit') <> 'null'
        LIMIT 1
    """)
    ).first():
        raise RuntimeError("0158 downgrade cannot preserve per-unit saved-place history")
    for table in _ITEM_HISTORY:
        op.execute(f'DROP TRIGGER nora_row_history ON "{table}"')
        bind.execute(
            sa.text("DELETE FROM row_history WHERE table_name = :table"), {"table": table},
        )
    for table, columns in _TOMBSTONE_NULLABLE.items():
        op.drop_constraint(f"ck_{table}_tombstone_content", table, type_="check")
        for column in columns:
            op.alter_column(table, column, nullable=False)
    op.drop_constraint(_PLATFORM_HEAD_KEY, "platform_object_state", type_="unique")
    op.create_unique_constraint(
        _PLATFORM_HEAD_KEY, "platform_object_state", list(_PLATFORM_HEAD_COLUMNS),
    )
    op.drop_constraint(_KEY, _TABLE, type_="unique")
    op.create_primary_key("presence_places_pkey", _TABLE, ["circle_id", "place_id"])
    op.execute(
        "UPDATE row_history SET row_key = row_key - 'unit' WHERE table_name = 'presence_places'"
    )
    _history_key("circle_id", "place_id")
