"""The Cognition loop eval pushes Record sequences past the source's ids."""

import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from scripts.cognition_loop.advance import bump_sequences


async def test_bump_sequences_skips_uuid_ids_and_moves_serial_ones(pg_module_url):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "SELECT setval(pg_get_serial_sequence('messages', 'id'), 1, false)"))
        source_max = (await conn.execute(sa.text(
            "SELECT COALESCE(MAX(id), 0) FROM messages"))).scalar_one()
        assert (await conn.execute(sa.text(
            "SELECT pg_get_serial_sequence('social_item', 'id')"))).scalar_one() is None

    await bump_sequences(engine, engine)

    async with engine.connect() as conn:
        nxt = (await conn.execute(sa.text(
            "SELECT nextval(pg_get_serial_sequence('messages', 'id'))"))).scalar_one()
    assert nxt > source_max
    await engine.dispose()
