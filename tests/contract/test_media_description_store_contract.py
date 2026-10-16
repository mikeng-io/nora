"""`MediaDescriptionStore` has two implementations, so it gets one suite both
pass — CLAUDE.md's rule, and the reason the fake cannot quietly drift into
answering differently from Postgres.

The properties that matter are the ones the ingest stage relies on to skip
work: a miss at either level must be distinguishable from a hit, `remember`
must make BOTH levels hit afterwards, and a second `remember` for the same
identity must not raise (a sticker is seen many times).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.domain.media import MediaBlob
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import STICKER_INDEX
from nora.testing import FakeMediaDescriptionStore

BLOB = MediaBlob(sha256="a" * 64, media_type="image/webp", size=11798)
TOKEN = "sticker:8c6ce85a3156b8e498e1ba0e97bc6620:15"  # noqa: S105


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        yield FakeMediaDescriptionStore()
        return
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield SqlMediaDescriptionStore(engine)
    await engine.dispose()


async def test_an_unknown_identity_resolves_to_none(store):
    assert await store.resolve(platform="signal", token=TOKEN) is None


async def test_an_unknown_hash_describes_to_none(store):
    assert await store.describe(sha256="b" * 64) is None


async def test_an_index_placeholder_row_describes_to_none(store):
    """The DEC-138 M4 inbound write gives every held byte a
    `description=""` ROW before any vision model has run — the row is the
    index entry, not a description. `describe` MUST NOT serve that
    placeholder as a cached description, or the first `media_fetch` of an
    undescribed medium gets ok+chars=0 and never invokes the describer.
    Empty and missing must be indistinguishable at the cache boundary: both
    None."""
    await store.remember(
        platform="signal", token="sticker:empty:1",  # noqa: S106
        blob=MediaBlob(sha256="e" * 64, media_type="video/mp4", size=13383492),
        description="", model="",
    )

    assert await store.describe(sha256="e" * 64) is None
    # And a later real description wins — the placeholder is just index.
    await store.remember(
        platform="signal", token="sticker:empty:1",  # noqa: S106
        blob=MediaBlob(sha256="e" * 64, media_type="video/mp4", size=13383492),
        description="a cat video", model="k3-256k",
    )
    assert await store.describe(sha256="e" * 64) == "a cat video"


async def test_remember_makes_both_levels_hit(store):
    await store.remember(
        platform="signal", token=TOKEN, blob=BLOB,
        description="a cartoon cat collapsing onto a table", model="mimo",
    )

    got = await store.resolve(platform="signal", token=TOKEN)
    assert got == BLOB, "the whole MediaBlob comes back, not just the hash"
    assert await store.describe(sha256=BLOB.sha256) == (
        "a cartoon cat collapsing onto a table"
    )


async def test_remembering_the_same_sticker_twice_is_not_an_error(store):
    """A sticker is seen many times; the second sighting re-remembers only if
    something upstream missed. It must be an upsert, not a constraint
    violation that costs the message."""
    for _ in range(2):
        await store.remember(
            platform="signal", token=TOKEN, blob=BLOB,
            description="a cartoon cat", model="mimo",
        )

    assert await store.describe(sha256=BLOB.sha256) == "a cartoon cat"


async def test_remember_moves_all_three_co_dependent_columns_together(store):
    """`sha256`, `media_type` and `size` are one indivisible fact about one
    blob — `sha256` is a pure function of the bytes. A second `remember` for
    the same identity with a DIFFERENT blob must move all three together, or
    `resolve` hands back a row asserting a size that does not match the bytes
    its own hash names (the untrue record A9/DEC-43 forbids)."""
    first = MediaBlob(sha256="c" * 64, media_type="image/webp", size=100)
    second = MediaBlob(sha256="d" * 64, media_type="image/png", size=999)

    await store.remember(platform="signal", token=TOKEN, blob=first,
                         description="first", model="mimo")
    await store.remember(platform="signal", token=TOKEN, blob=second,
                         description="second", model="mimo")

    assert await store.resolve(platform="signal", token=TOKEN) == second


async def test_an_empty_token_writes_the_description_but_no_junk_index_row(pg_url):
    """`media_fetch` (`VisionCache.aput`) has no platform-side sticker
    identity for a handle-pointed image — it calls `remember` with
    `platform=""`, `token=""`. SQL-only: without the `if token:` guard this
    would insert one `sticker_index` row keyed `("", "")` that every
    `media_fetch` write fights over, carrying whichever hash wrote last. The
    description row must still land."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    store = SqlMediaDescriptionStore(engine)
    try:
        await store.remember(
            platform="", token="", blob=BLOB,
            description="a red one-pixel square", model="mimo",
        )
        assert await store.describe(sha256=BLOB.sha256) == "a red one-pixel square"
        async with engine.connect() as conn:
            count = (await conn.execute(
                sa.select(sa.func.count()).select_from(STICKER_INDEX).where(
                    STICKER_INDEX.c.platform == "", STICKER_INDEX.c.fetch_token == "",
                )
            )).scalar_one()
        assert count == 0, "an empty-token remember must not create a junk index row"
    finally:
        await engine.dispose()


async def test_the_same_bytes_under_a_second_identity_share_one_description(store):
    """The content level is what makes the same art in two packs one vision
    call. If this fails, dedup only works per-pack."""
    await store.remember(platform="signal", token=TOKEN, blob=BLOB,
                         description="a cartoon cat", model="mimo")
    await store.remember(platform="discord", token="sticker:900:png", blob=BLOB,  # noqa: S106
                         description="a cartoon cat", model="mimo")

    assert await store.resolve(platform="discord", token="sticker:900:png") == BLOB  # noqa: S106
    assert await store.describe(sha256=BLOB.sha256) == "a cartoon cat"
