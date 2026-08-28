"""The CHECK constraints are the half `alembic check` is blind to, which is
why they are asserted from Postgres's own catalogue (DEC-11/DEC-53) via a
real bad INSERT rather than a metadata comparison.

There is no repo-wide `pg_engine` fixture (the only shared Postgres fixture
in `tests/conftest.py` is `pg_url`, a fresh, unmigrated database URL — see
`tests/infra/test_room_portrait_store.py`'s `store` fixture for the same
composition). `pg_engine` here is that same composition — `pg_url` +
`run_migrations` + `create_sql_engine` — kept local to this file because
nothing else in the suite needs a bare migrated `AsyncEngine` to run raw SQL
against; every other Postgres-backed test either goes through a store class
or uses `sa.create_engine(sync_url_for(...))` directly, as
`test_schema_parity.py`'s `migrated` fixture does.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations


@pytest.fixture
async def pg_engine(pg_url) -> AsyncEngine:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    yield engine
    await engine.dispose()


async def test_a_fact_cannot_be_written_without_a_full_instruction_digest(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'black_tower', "
                "'https://x.test/a', 'x', 1, 1, 'deadbeef', 'gpt', 'tick-1')"
            ))


async def _seed_entity(conn, name: str = "House House") -> str:
    return (await conn.execute(sa.text(
        "insert into fact_entity (id, workspace, canonical_name, created_at) "  # noqa: S608 - fixed test literals, no external input
        f"values (gen_random_uuid(), 'nora', '{name}', 1) returning id::text"
    ))).scalar_one()


async def test_a_resolved_fact_carries_both_an_entity_id_and_the_literal(pg_engine):
    """Migration 0038, reversing `fact_one_object_check`. The old CHECK forced
    promotion to DESTROY the surface string the room used; this asserts the
    state that was previously illegal and is now the whole point — the words
    AND the entity they resolve to, on one row."""
    async with pg_engine.begin() as conn:
        entity_id = await _seed_entity(conn)
        await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_entity_id, object_literal, source_url, source_excerpt, "
            "valid_from, created_at, instruction_sha256, model, "
            "established_by) values "
            f"(gen_random_uuid(), 'nora', 'e1', 'made_by', '{entity_id}', "
            f"'house house', 'https://x.test/a', 'x', 1, 1, '{'a' * 64}', "
            "'gpt', 'tick-1')"
        ))
        # Positive control: the row is really there, with both halves intact.
        row = (await conn.execute(sa.text(
            "select object_entity_id::text, object_literal from fact "
            "where predicate = 'made_by'"
        ))).one()
        assert row[0] == entity_id
        assert row[1] == "house house"


async def test_a_fact_with_no_literal_object_is_refused(pg_engine):
    """`object_literal` is NOT NULL as of 0038. An entity id alone is the row
    shape that broke the negative cache — dedup compares `object_literal`,
    and `NULL = 'x'` is NULL, so the claim was re-adjudicated every tick
    forever."""
    async with pg_engine.begin() as conn:
        entity_id = await _seed_entity(conn)
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_entity_id, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by) values "
                f"(gen_random_uuid(), 'nora', 'e1', 'has', '{entity_id}', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_an_object_entity_id_naming_no_entity_is_refused(pg_engine):
    """The defect 0038 closes: `object_entity_id` was `text` against a `uuid`
    PK with NO foreign key, so it would accept any string and tell nobody —
    a second free-text column wearing an id's name. Now the database refuses
    a pointer to nothing."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_entity_id, object_literal, source_url, source_excerpt, "
                "valid_from, created_at, instruction_sha256, model, "
                "established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', "
                "'0191f2c4-0000-7000-8000-0000000000ff', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_an_object_entity_id_that_is_not_a_uuid_is_refused(pg_engine):
    """The column is `uuid`, not `text`. Before 0038 `'House House'` was a
    legal value here."""
    async with pg_engine.begin() as conn:
        # `DBAPIError` alone: `IntegrityError` is a subclass of it, so the
        # tuple was redundant AND the wider arm accepted a connection failure
        # as a pass.
        with pytest.raises(sa.exc.DBAPIError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_entity_id, object_literal, source_url, source_excerpt, "
                "valid_from, created_at, instruction_sha256, model, "
                "established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'House House', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_a_blank_model_is_refused_not_stored(pg_engine):
    """'' and '   ' are non-NULL to every query asking which rows are
    attributable — a value that reads as recorded and names nothing turns
    R-27's `unavailable` into a false `ok`."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', '   ', 'tick-1')"
            ))


async def test_superseded_by_requires_superseded_at(pg_engine):
    """`superseded_by` set alone names a successor with no record of when the
    supersession happened — half a merge record, not a full one.

    `superseded_by` now FOREIGN KEYs to `fact.id`, so a random, non-existent
    UUID would trip the FK instead of the CHECK under test here — the exact
    "isolate to the one constraint" discipline this file is written to keep.
    A real fact is seeded first and its own id is used as `superseded_by`, so
    the FK is satisfied and only `fact_supersession_is_timed_check` fails."""
    async with pg_engine.begin() as conn:
        seed_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'seed', "
            f"'https://x.test/seed', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1') "
            "returning id"
        ))).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "superseded_by) "
                "values (gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                f"'{seed_id}')"
            ))


async def test_superseded_at_requires_superseded_by(pg_engine):
    """The direction the old one-directional implication missed:
    `superseded_at` set with `superseded_by` NULL is accepted by
    `(superseded_by is null) or (superseded_at is not null)` — but
    `Fact.is_standing` and the partial `idx_fact_standing` both key on
    `superseded_at IS NULL` alone, so such a row is hidden from every read
    with no record of what replaced it. This is precisely the Graphiti issue
    #1771 failure the constraint exists to prevent: a merge computed and then
    thrown away.

    `supersede_reason` is now required whenever `superseded_at` is set
    (`fact_supersede_reason_is_timed_check`); without supplying one here that
    CHECK would ALSO fail on this insert, so `supersede_reason` is set to
    isolate the failure to `fact_supersession_is_timed_check` alone."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "superseded_at, supersede_reason) "
                "values (gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                "1, 'stale')"
            ))


async def test_fact_warrant_kind_is_constrained(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, warrant_kind, valid_from, created_at, "
                "instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 'guess', "
                f"1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_source_url_required_when_warrant_is_source(pg_engine):
    """Replaces the old unconditional NOT NULL on `source_url`: warrant_kind
    defaults to 'source', so an excerpt with no url is a fact with half its
    warrant missing."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_excerpt, valid_from, created_at, "
                "instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 'x', "
                f"1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_source_excerpt_required_when_warrant_is_source(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, valid_from, created_at, "
                "instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_source_url_forbidden_when_warrant_is_not_source(pg_engine):
    """The other half of the discriminated warrant: a `premises`/`observation`/
    `stated` fact carrying a stray `source_url` is undefined in meaning — is
    it corroborating, decorative? — which is the ambiguity this CHECK exists
    to kill, not merely a symmetrical NOT NULL."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, warrant_kind, source_url, "
                "valid_from, created_at, instruction_sha256, model, "
                "established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 'observation', "
                f"'https://x.test/a', 1, 1, "
                f"'{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_fidelity_defaults_to_tentative_not_proven(pg_engine):
    """A default is what a row gets when nobody thought about it — in a
    fail-closed codebase that value must underclaim, never overclaim
    (2026-08-21 correction: this column first shipped defaulting to
    'proven', reasoned from today's only writer path rather than from the
    invariant; fixed before any row existed — see the migration)."""
    async with pg_engine.begin() as conn:
        fidelity = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
            f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1') "
            "returning fidelity"
        ))).scalar_one()
        assert fidelity == "tentative"


async def test_fidelity_is_constrained(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "fidelity) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                "'sure')"
            ))


async def test_supersede_reason_requires_superseded_at(pg_engine):
    """The reason a fact was superseded cannot be reconstructed from the row
    afterwards at any price — recording one with no `superseded_at` names a
    supersession that never happened."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "supersede_reason) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                "'stale')"
            ))


async def test_supersede_source_requires_superseded_at(pg_engine):
    """`supersede_source` (`DreamSink.supersede`'s `source` argument, a
    WARRANT — a URL or equivalent pointer, never a row id) is optional even
    on a superseded row, so the CHECK is a one-directional implication, not
    the biconditional `fact_supersede_reason_is_timed_check` uses above — but
    it must still never be set on a STANDING row: a citation for a
    supersession that never happened is exactly the same unreconstructable
    hole `supersede_reason` closes.

    Asserts the CONSTRAINT NAME, not just `IntegrityError` — this table has
    eight other CHECKs, several sharing columns (`superseded_at`,
    `supersede_reason`), so a malformed insert can trip the WRONG one and
    still make the assertion pass for the wrong reason."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError) as ei:
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "supersede_source) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                "'https://x.test/correction')"
            ))
    assert "fact_supersede_source_is_timed_check" in str(ei.value.orig)


async def test_supersede_reason_is_constrained(pg_engine):
    """A real successor is seeded first so `superseded_by`'s FK is satisfied
    and only `fact_supersede_reason_check` fails on the invalid value."""
    async with pg_engine.begin() as conn:
        seed_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'seed', "
            f"'https://x.test/seed', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1') "
            "returning id"
        ))).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "superseded_at, superseded_by, supersede_reason) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                f"5, '{seed_id}', 'fabricated')"
            ))


async def test_superseded_by_must_reference_an_existing_fact(pg_engine):
    """`superseded_by` previously had no FK at all — it could name a fact
    that does not exist. `superseded_at` and `supersede_reason` are both set
    correctly here so only the new FK fails, not either biconditional."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by, "
                "superseded_at, superseded_by, supersede_reason) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1', "
                "5, gen_random_uuid(), 'stale')"
            ))


async def test_a_well_formed_premises_fact_is_accepted(pg_engine):
    """The positive case for the discriminated warrant's other branch: a
    `premises`-warranted fact with no source url/excerpt is satisfiable, not
    just the `source` default. Its premises are separate `fact_premise` rows
    — nothing in the database requires one to exist for this insert to
    succeed (see the migration's note on where that invariant now lives, or
    rather does not yet)."""
    async with pg_engine.begin() as conn:
        await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, warrant_kind, valid_from, "
            "created_at, instruction_sha256, model, established_by, "
            "fidelity) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'black_tower', "
            f"'premises', 1, 1, '{'a' * 64}', "
            "'gpt', 'tick-1', 'tentative')"
        ))
        count = (await conn.execute(sa.text(
            "select count(*) from fact where warrant_kind = 'premises'"
        ))).scalar_one()
        assert count == 1


async def test_a_fact_premise_edge_cannot_be_self_referential(pg_engine):
    """`fact_premise_no_self_reference_check` — the one cycle cheap to rule
    out at the row level. A real fact is seeded first so both FKs are
    satisfied and only the self-reference CHECK fails."""
    async with pg_engine.begin() as conn:
        seed_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'seed', "
            f"'https://x.test/seed', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1') "
            "returning id"
        ))).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                f"insert into fact_premise (fact_id, premise_fact_id) "  # noqa: S608 - fixed test literals, no external input
                f"values ('{seed_id}', '{seed_id}')"
            ))


async def test_a_fact_premise_edge_requires_the_fact_to_exist(pg_engine):
    """`fact_premise_fact_id_fkey` — a premise recorded for a fact that does
    not exist. The premise side is a real, seeded fact so only the `fact_id`
    FK fails, not the premise side's."""
    async with pg_engine.begin() as conn:
        premise_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'premise', "
            f"'https://x.test/premise', 'x', 1, 1, '{'a' * 64}', 'gpt', "
            "'tick-1') returning id"
        ))).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact_premise (fact_id, premise_fact_id) "  # noqa: S608 - fixed test literals, no external input
                f"values (gen_random_uuid(), '{premise_id}')"
            ))


async def test_a_fact_premise_edge_requires_the_premise_to_exist(pg_engine):
    """`fact_premise_premise_fact_id_fkey` — the mirror of the test above.
    `superseded_by`'s FK proved the "existence, not standing-ness" point
    already; this is the same shape one table over."""
    async with pg_engine.begin() as conn:
        fact_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, warrant_kind, valid_from, created_at, "
            "instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'derived', 'premises', "
            f"1, 1, '{'a' * 64}', 'gpt', 'tick-1') returning id"
        ))).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact_premise (fact_id, premise_fact_id) "  # noqa: S608 - fixed test literals, no external input
                f"values ('{fact_id}', gen_random_uuid())"
            ))


async def test_a_well_formed_fact_premise_edge_is_accepted_and_reverse_looked_up(
    pg_engine,
):
    """The positive case, AND the reason this table replaced `premise_ids
    UUID[]`: "which facts depend on this one" (the transitive-supersession
    question §13.5 leaves open) is this exact query — an indexed lookup on
    `premise_fact_id`, not an array-containment scan."""
    async with pg_engine.begin() as conn:
        premise_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'premise', "
            f"'https://x.test/premise', 'x', 1, 1, '{'a' * 64}', 'gpt', "
            "'tick-1') returning id"
        ))).scalar_one()
        fact_id = (await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, warrant_kind, valid_from, created_at, "
            "instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'derived', 'premises', "
            f"1, 1, '{'a' * 64}', 'gpt', 'tick-1') returning id"
        ))).scalar_one()
        await conn.execute(sa.text(
            "insert into fact_premise (fact_id, premise_fact_id) "  # noqa: S608 - fixed test literals, no external input
            f"values ('{fact_id}', '{premise_id}')"
        ))
        dependents = (await conn.execute(sa.text(
            "select fact_id from fact_premise where "  # noqa: S608 - fixed test literals, no external input
            f"premise_fact_id = '{premise_id}'"
        ))).scalars().all()
        assert dependents == [fact_id]


def _candidate_insert(
    *, status: str = "'pending'", instruction: str = f"'{'a' * 64}'",
    model: str = "'gpt'",
) -> str:
    """One well-formed `fact_candidate` INSERT with exactly one field varied.

    Written as a builder rather than five hand-copied literals because
    migration 0027 added two NOT NULL columns to this table and the old
    hand-copied INSERT — which omitted them — would have gone on raising
    `IntegrityError` for a NOT-NULL violation while its test claimed to be
    measuring `fact_candidate_status_check`. That is the decorative-green
    shape `test_a_well_formed_fact_is_accepted` exists to rule out, so the
    positive control below is what proves each variation reaches the CHECK it
    names.
    """
    return (
        "insert into fact_candidate (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
        "object, first_seen_at, last_seen_at, occurrence_count, status, "
        "established_by, instruction_sha256, model) values "
        f"(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 1, 1, 1, {status}, "
        f"'tag-1', {instruction}, {model})"
    )


async def test_candidate_status_is_constrained(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(_candidate_insert(status="'refuted'")))


async def test_a_candidate_cannot_be_written_without_an_instruction_digest(pg_engine):
    """Migration 0027 chose NOT NULL over nullable because this table had no
    rows written before the columns existed (unlike the four Dreamer tables).
    A candidate with no digest is refused outright rather than recorded as
    provenance-unknown."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact_candidate (id, workspace, subject_id, "
                "predicate, object, first_seen_at, last_seen_at, "
                "occurrence_count, status, established_by, model) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 1, 1, 1, "
                "'pending', 'tag-1', 'gpt')"
            ))


async def test_a_candidate_cannot_be_written_with_a_partial_instruction_digest(
    pg_engine,
):
    """`fact_candidate_instruction_sha256_check` — the same predicate `fact`
    carries, and with no `IS NULL` arm because the column is NOT NULL."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                _candidate_insert(instruction="'deadbeef'")
            ))


async def test_a_candidate_cannot_be_written_with_a_non_hex_instruction_digest(
    pg_engine,
):
    """Right length, wrong alphabet: the case a `length() = 64` CHECK would
    admit."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                _candidate_insert(instruction=f"'{'z' * 64}'")
            ))


async def test_a_candidate_cannot_be_written_with_a_blank_model(pg_engine):
    """`fact_candidate_model_is_named_check` — `length(btrim(model)) > 0`.
    `'   '` is non-NULL to every query asking which rows are attributable, so
    the CHECK asserts the one thing a model name provably is: not empty."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(_candidate_insert(model="'   '")))


async def test_a_well_formed_candidate_is_accepted(pg_engine):
    """The positive control for the four candidate refusals above, for the
    reason `test_a_well_formed_fact_is_accepted` states one table over: each
    of them must be failing on the constraint it names, not on the two NOT
    NULL columns 0027 added."""
    async with pg_engine.begin() as conn:
        await conn.execute(sa.text(_candidate_insert()))
        count = (await conn.execute(
            sa.text("select count(*) from fact_candidate")
        )).scalar_one()
        assert count == 1


async def test_a_well_formed_fact_is_accepted(pg_engine):
    """The positive case: every CHECK above is satisfiable, not just violable.
    Without this, all six red tests above could be passing because the insert
    is malformed for some OTHER reason (a missing NOT NULL column, a typo in a
    column name) and never reaches the constraint under test at all."""
    async with pg_engine.begin() as conn:
        await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, source_url, source_excerpt, valid_from, "
            "created_at, instruction_sha256, model, established_by) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'black_tower', "
            f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
        ))
        count = (await conn.execute(sa.text("select count(*) from fact"))).scalar_one()
        assert count == 1
