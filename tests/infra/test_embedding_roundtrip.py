"""The async path to the `vector` column, exercised — because nothing else is.

**Every schema test in this repo connects over SYNC psycopg** (the parity
suite, the alembic suite, the baseline dump). The application connects with
asyncpg. So the gate is structurally blind to the driver that actually runs,
and "the embedding column works" was an inference from the DDL rather than a
reading from the path production uses.

The inference under review was that the async pool needs `register_vector`,
because pgvector's asyncpg helper installs a codec and nothing in `nora/` calls
it — therefore the first insert raises. That was measured and it is FALSE: the
SQLAlchemy `Vector` type does its own bind and result processing over TEXT, and
asyncpg handles text natively. Installing the codec is the dangerous move, not
the safe one — it would put a BINARY codec under a type that hands it a string.

So this file is not a fix. It is the test that pins the behaviour, so that if
anyone later adds `register_vector` to the engine because the reasoning above
looks compelling on paper, something reddens instead of production.

**The residual hazard, and it is real**: raw SQL that binds a Python list to a
vector parameter DOES fail — `DataError: invalid input for query argument`.
Anything hand-rolled for this column (a bulk upsert, a similarity query with a
literal probe vector) must bind a string or go through the type. The last test
here fixes that boundary so it is a known edge rather than a surprise.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import EMBEDDING, EMBEDDING_DIMENSIONS

NOW = 1_755_200_000_000


def _row(**over):
    row = {
        "kind": "fact",
        "row_key": "0191f2c4-0000-7000-8000-00000000000a",
        "scope": "ws-main",
        "text": "big walk │ means │ a walking activity",
        "model": "bge-m3",
        "fingerprint": "a" * 64,
        "embedded_at": NOW,
        "v1024": [0.1] * EMBEDDING_DIMENSIONS,
    }
    row.update(over)
    return row


async def test_a_vector_goes_in_and_comes_back_over_the_async_driver(pg_url):
    """The round trip nothing exercised: a Python list of floats through the
    declared `Vector` type, over asyncpg, and back out as a sequence of the
    same width and the same values.

    Asserted on the VALUES and not merely on the width — a codec that silently
    truncated or reordered would satisfy a length check.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(EMBEDDING), [_row()])
        async with engine.connect() as conn:
            got = (await conn.execute(
                sa.select(EMBEDDING.c.v1024).where(
                    EMBEDDING.c.kind == "fact",
                )
            )).scalar_one()
    finally:
        await engine.dispose()

    assert got is not None
    assert len(got) == EMBEDDING_DIMENSIONS
    assert [round(float(x), 4) for x in got[:4]] == [0.1, 0.1, 0.1, 0.1]


async def test_cosine_distance_ranks_over_the_async_driver(pg_url):
    """The read the retrieval verb will actually make: `<=>` against a probe,
    ordered. It is a different code path from the insert — the probe is BOUND,
    not stored — and it is the one that would break first if a codec were
    installed under the text-shaped type.

    Ranking is asserted rather than distance values: the property retrieval
    depends on is the ORDER, and pinning float distances would make this test
    fail on an ordinary pgvector upgrade for no reason.
    """
    near = [0.1] * EMBEDDING_DIMENSIONS
    far = [0.1] * (EMBEDDING_DIMENSIONS - 1) + [-9.0]
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(EMBEDDING), [
                _row(row_key="near", v1024=near),
                _row(row_key="far", v1024=far),
            ])
        async with engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(EMBEDDING.c.row_key)
                .where(EMBEDDING.c.kind == "fact")
                .order_by(EMBEDDING.c.v1024.cosine_distance(near))
            )).all()
    finally:
        await engine.dispose()

    assert [r.row_key for r in rows] == ["near", "far"], (
        "cosine ordering over the async driver is what every retrieval read "
        "will depend on"
    )


async def test_a_null_vector_is_a_row_awaiting_its_embedding(pg_url):
    """`v1024` is NULLABLE and that is load-bearing: pass 4 writes the row
    when the text changes and fills the vector when the endpoint answers, so a
    NULL is «queued», not «broken». A NOT NULL here would force the two into
    one transaction with an outbound HTTP call inside it."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(EMBEDDING), [_row(v1024=None)])
        async with engine.connect() as conn:
            got = (await conn.execute(
                sa.select(EMBEDDING.c.v1024)
            )).scalar_one()
    finally:
        await engine.dispose()

    assert got is None


async def test_raw_sql_binding_a_python_list_is_the_known_edge(pg_url):
    """**The residual hazard, fixed as a test rather than left as folklore.**

    The `Vector` type's bind processor is what turns a list into the text
    Postgres wants. Raw SQL bypasses it, hands asyncpg a list for a `vector`
    parameter, and asyncpg has no codec for that — `DataError`. Stage 4's
    hand-rolled SQL, if any, must bind a string or go through the type.

    Asserted as a RAISE, so the day a codec does get installed this test fails
    and forces the decision to be made deliberately rather than discovered.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            with pytest.raises(Exception) as caught:
                await conn.execute(
                    sa.text(
                        "insert into embedding "
                        "(kind, row_key, scope, text, model, fingerprint, "
                        " embedded_at, v1024) values "
                        "(:k, :r, :s, :t, :m, :f, :e, :v)"
                    ),
                    {
                        "k": "fact", "r": "raw", "s": "ws-main", "t": "x",
                        "m": "bge-m3", "f": "b" * 64, "e": NOW,
                        "v": [0.1] * EMBEDDING_DIMENSIONS,
                    },
                )
    finally:
        await engine.dispose()

    assert "vector" in str(caught.value).lower() or "argument" in str(
        caught.value).lower()
