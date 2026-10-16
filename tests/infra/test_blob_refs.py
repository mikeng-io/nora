"""`nora.infra.sql.blob_refs`: every column that names a blob key is read
before a blob is deleted.

A store added later that names blobs in a column `held_elsewhere` does not
read would let a story frame's deletion take bytes that store still needs.
This fails on any schema column shaped like a key (a name a key column has
carried) or holding JSON that is in neither `BLOB_KEY_COLUMNS` nor
`NOT_BLOB_KEYS`, and on a listed column the schema no longer has. That each
listed holder actually holds is
`tests/integration/test_story_media_expiry.py`'s.
"""

from __future__ import annotations

import sqlalchemy as sa

from nora.infra.sql.blob_refs import BLOB_KEY_COLUMNS, NOT_BLOB_KEYS
from nora.infra.sql.schema import METADATA

#: The column names blob keys have been stored under.
_KEY_SHAPED = {"sha256", "blob_key", "media_json", "media_ref", "media", "output_ref"}


def _is_json(column: sa.Column[object]) -> bool:
    return isinstance(column.type, sa.JSON)


def test_every_column_shaped_like_a_blob_key_is_read_or_excused() -> None:
    """Key-shaped names, and every JSON column, which can carry a key under
    any field name (`row_history.image`, `media_objects.provenance`)."""
    shaped = {
        (table.name, column.name)
        for table in METADATA.tables.values() for column in table.columns
        if column.name in _KEY_SHAPED or _is_json(column)
    }
    unread = shaped - BLOB_KEY_COLUMNS - set(NOT_BLOB_KEYS)
    assert not unread, (
        f"{sorted(unread)} may name blob keys: read them in "
        "nora.infra.sql.blob_refs.held_elsewhere, or say why not in NOT_BLOB_KEYS"
    )


def test_every_listed_column_exists() -> None:
    columns = {
        (table.name, column.name)
        for table in METADATA.tables.values() for column in table.columns
    }
    assert not (BLOB_KEY_COLUMNS | set(NOT_BLOB_KEYS)) - columns


def test_every_excuse_says_why() -> None:
    assert all(reason.strip() for reason in NOT_BLOB_KEYS.values())
