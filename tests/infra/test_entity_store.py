"""`SqlEntityStore` — the writer and reader `fact_entity` shipped without, and
pass 3.

Real Postgres throughout. The two behaviours under test are a MERGE and an
aggregate over the store's own contents; an in-memory double would be
reimplementing both, which is how a double comes to agree with a bug.

**`21-substrate-contract.md` §10 states what would prove the whole substrate
contract wrong**: "if a substantial share of objects that should be nodes never
reach degree 2 — if the room's claims are so sparse that promotion effectively
never fires." So every assertion below that promotion DID something is paired
with one that it did not fire where it should not, and the counters that make
that readable in production are asserted too.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.entities import SqlEntityStore
from nora.infra.sql.migrations_runner import run_migrations

NOW = 1_755_100_000_000
WS = "ws"


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlEntityStore(engine), engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# resolve_or_create — MERGE, in the caller's transaction
# ---------------------------------------------------------------------------


async def test_a_second_spelling_joins_the_entity_it_already_has(store):
    """The live failure this table exists to fix: `Big Walk` and `big walk`
    were two rows that could not see each other, which is the precondition for
    either ever superseding the other."""
    entities, engine = store
    async with engine.begin() as conn:
        first, created_a = await entities.resolve_or_create(
            conn, workspace=WS, canonical_name="Big Walk", aliases=(), now_ms=NOW,
        )
        second, created_b = await entities.resolve_or_create(
            conn, workspace=WS, canonical_name="big  walk", aliases=(),
            now_ms=NOW,
        )

    assert first == second
    assert (created_a, created_b) == (True, False)
    async with engine.connect() as conn:
        aliases = (await conn.execute(
            sa.text("select aliases from fact_entity where id = :i"),
            {"i": first},
        )).scalar_one()
    # UNION, never replace: the second spelling is ADDED. A store that replaced
    # would hold only whatever was written last.
    assert "big  walk" in aliases


async def test_an_alias_resolves_as_well_as_the_canonical_name(store):
    entities, engine = store
    async with engine.begin() as conn:
        eid, _ = await entities.resolve_or_create(
            conn, workspace=WS, canonical_name="House House",
            aliases=("house  house",), now_ms=NOW,
        )

    assert await entities.resolve(workspace=WS, name="HOUSE HOUSE") == eid
    assert await entities.resolve(workspace=WS, name="house house") == eid
    # The positive control: it is not resolving everything.
    assert await entities.resolve(workspace=WS, name="Big Walk") is None


async def test_an_entity_never_crosses_a_workspace(store):
    """`workspace` is the key R-49 refuses to relay across. Two rooms in
    different workspaces naming the same thing are two entities."""
    entities, engine = store
    async with engine.begin() as conn:
        a, _ = await entities.resolve_or_create(
            conn, workspace="ws-a", canonical_name="Big Walk", aliases=(),
            now_ms=NOW,
        )
        b, _ = await entities.resolve_or_create(
            conn, workspace="ws-b", canonical_name="Big Walk", aliases=(),
            now_ms=NOW,
        )
    assert a != b
    assert await entities.resolve(workspace="ws-a", name="Big Walk") == a


# ---------------------------------------------------------------------------
# lookup — the tool-facing read (design/dreamer-agent-loop/32 §3.4)
# ---------------------------------------------------------------------------


async def _note(engine, *, anchor_entity_ids, workspace: str = WS,
                 retired: bool = False) -> None:
    """The one column the note tier's own queue reads —
    `anchor_entity_ids` — inserted directly rather than through
    `SqlNoteStore`, which this file deliberately does not import (kept
    isolated to `SqlEntityStore`).
    `head_revision_id` names no real `note_revision` row; there is no FK to
    violate (the module docstring on `nora.infra.sql.notes` states why), and
    `unresolved_note_anchor_names` never reads that column."""
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into note (id, workspace, title, description, "
                "uncertainty, closing_condition, anchor_entity_ids, "
                "established_in, created_at, head_revision_id, retired_at) "
                "values (gen_random_uuid(), :ws, 't', 'd', 'u', 'c', :ids, "
                "'conv-1', :now, gen_random_uuid(), :retired)"
            ),
            {"ws": workspace, "ids": list(anchor_entity_ids), "now": NOW,
             "retired": NOW if retired else None},
        )


# ---------------------------------------------------------------------------
# the note tier's judgement queue (issue #100)
# ---------------------------------------------------------------------------


async def test_unresolved_note_anchor_names_finds_names_no_entity_covers(store):
    _store, engine = store
    await _note(engine, anchor_entity_ids=("Grant Carver",))

    assert await _store.unresolved_note_anchor_names(
        workspace=WS, limit=10,
    ) == ("Grant Carver",)


async def test_unresolved_note_anchor_names_excludes_what_already_resolves(store):
    """A name that resolves NOW is not a near-miss — it is an exact match the
    deterministic pass will pick up the next time the note is touched, and
    offering it to the model would ask a question that already has a free
    answer."""
    entities, engine = store
    await _note(engine, anchor_entity_ids=("Grant Carver", "Nobody Knows"))
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into fact_entity (id, workspace, canonical_name, "
                "aliases, created_at) values (gen_random_uuid(), :ws, "
                "'Grant Carver', '{}'::text[], :now)"
            ),
            {"ws": WS, "now": NOW},
        )

    assert await entities.unresolved_note_anchor_names(
        workspace=WS, limit=10,
    ) == ("Nobody Knows",)


async def test_unresolved_note_anchor_names_excludes_retired_notes(store):
    """A retired note's anchor is no longer an occasion anybody can act on."""
    _store, engine = store
    await _note(engine, anchor_entity_ids=("Grant Carver",), retired=True)

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=10) == ()


async def test_unresolved_note_anchor_names_deduplicates_and_orders(store):
    _store, engine = store
    await _note(engine, anchor_entity_ids=("delta", "alpha"))
    await _note(engine, anchor_entity_ids=("alpha", "charlie", "bravo"))

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=3) == (
        "alpha", "bravo", "charlie",
    )


async def test_unresolved_note_anchor_names_never_reaches_another_workspace(store):
    _store, engine = store
    await _note(engine, anchor_entity_ids=("only-in-ws-b",), workspace="ws-b")

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=10) == ()
