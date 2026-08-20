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


async def test_a_fact_cannot_have_two_objects(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_entity_id, object_literal, source_url, source_excerpt, "
                "valid_from, created_at, instruction_sha256, model, "
                "established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'e2', 'also', "
                f"'https://x.test/a', 'x', 1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


async def test_a_fact_needs_at_least_one_object(pg_engine):
    """The other half of `fact_one_object_check` — `<>` refuses two objects
    AND zero, and only the two-object side is covered by the brief's own
    test. A fact with neither an entity nor a literal names nothing."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "source_url, source_excerpt, valid_from, created_at, "
                "instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', "
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
    to kill, not merely a symmetrical NOT NULL.

    `premise_ids` is deliberately left at its default (`'{}'`, empty) here:
    an earlier version of this test set it to a non-empty array, which made
    the insert ALSO violate `fact_premise_ids_warrant_check` (non-empty
    `premise_ids` with a non-'premises' `warrant_kind`) — so it would have
    kept "passing" even with `fact_source_url_warrant_check` removed,
    exactly the false-isolation failure this file's docstring warns about.
    Leaving `premise_ids` empty isolates the failure to the one CHECK named
    in this test."""
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


async def test_premise_ids_required_when_warrant_is_premises(pg_engine):
    """`premise_ids` is `UUID[] NOT NULL DEFAULT '{}'` — omitting it on a
    `premises`-warranted fact leaves the array empty, which is the same as
    naming no premises at all."""
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
                "object_literal, warrant_kind, valid_from, created_at, "
                "instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 'premises', "
                f"1, 1, '{'a' * 64}', 'gpt', 'tick-1')"
            ))


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
    `premises`-warranted fact with no source url/excerpt and a non-empty
    `premise_ids` is satisfiable, not just the `source` default."""
    async with pg_engine.begin() as conn:
        await conn.execute(sa.text(
            "insert into fact (id, workspace, subject_id, predicate, "  # noqa: S608 - fixed test literals, no external input
            "object_literal, warrant_kind, premise_ids, valid_from, "
            "created_at, instruction_sha256, model, established_by, "
            "fidelity) values "
            "(gen_random_uuid(), 'nora', 'e1', 'has', 'black_tower', "
            f"'premises', array[gen_random_uuid()], 1, 1, '{'a' * 64}', "
            "'gpt', 'tick-1', 'tentative')"
        ))
        count = (await conn.execute(sa.text(
            "select count(*) from fact where warrant_kind = 'premises'"
        ))).scalar_one()
        assert count == 1


async def test_candidate_status_is_constrained(pg_engine):
    async with pg_engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(sa.text(
                "insert into fact_candidate (id, workspace, subject_id, "
                "predicate, object, first_seen_at, last_seen_at, "
                "occurrence_count, status, established_by) values "
                "(gen_random_uuid(), 'nora', 'e1', 'has', 'bt', 1, 1, 1, "
                "'refuted', 'tag-1')"
            ))


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
