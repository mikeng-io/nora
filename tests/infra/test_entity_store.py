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

from nora.domain.facts import normalise_name
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.entities import SqlEntityStore, vetoed
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.metrics import Metrics

NOW = 1_755_100_000_000
WS = "ws"


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    metrics = Metrics()
    try:
        yield SqlEntityStore(engine, metrics=metrics), engine, metrics
    finally:
        await engine.dispose()


async def _fact(engine, *, subject: str, obj: str, predicate: str = "has",
                workspace: str = WS, superseded: bool = False) -> str:
    async with engine.begin() as conn:
        fid = str((await conn.execute(
            sa.text(
                "insert into fact (id, workspace, subject_id, predicate, "
                "object_literal, source_url, source_excerpt, valid_from, "
                "created_at, instruction_sha256, model, established_by) "
                "values (gen_random_uuid(), :w, :s, :p, :o, "
                "'https://x.test/a', 'x', 1, 1, :sha, 'm', 'run') "
                "returning id::text"
            ),
            {"w": workspace, "s": subject, "p": predicate, "o": obj,
             "sha": "a" * 64},
        )).scalar_one())
        if superseded:
            await conn.execute(
                sa.text(
                    "update fact set superseded_at = 2, superseded_by = id, "
                    "supersede_reason = 'wrong' where id = :i"
                ),
                {"i": fid},
            )
    return fid


async def _objects(engine):
    async with engine.connect() as conn:
        return {
            r[0]: r[1] for r in (await conn.execute(sa.text(
                "select object_literal, object_entity_id::text from fact"
            ))).all()
        }


# ---------------------------------------------------------------------------
# the veto — rules, not a list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", [
    "9 August", "2026-08-28", "19:30", "five", "5 gourds", "$40", "three hours",
    "no black tower", "https://example.test/a", "www.example.test",
    "唔係一個地方", "冇嘢", "x" * 65,
])
def test_the_veto_refuses_what_is_not_a_thing(surface):
    """§6.1's list as rules. A date is a SHAPE — an enumeration of dates is a
    list nobody can finish."""
    assert vetoed(surface)


@pytest.mark.parametrize("surface", [
    "House House", "big walk", "the black tower", "打風", "大浪灣",
    "Hong Kong Observatory", "Marco's car",
])
def test_the_veto_admits_things(surface):
    """The positive control, and it is not decoration: a veto that refused
    everything would satisfy every test above while making promotion
    structurally impossible — which is exactly the failure 21 §10 names as
    what would prove the contract wrong."""
    assert not vetoed(surface)


def test_the_operator_veto_file_is_additive():
    """21 §10 keeps the veto's CONTENTS operator-owned. The extra list narrows
    what promotes; it can never widen it, because the rules above run
    regardless."""
    assert vetoed("gourd", extra=frozenset({normalise_name("Gourd")}))
    assert not vetoed("gourd")


# ---------------------------------------------------------------------------
# resolve_or_create — MERGE, in the caller's transaction
# ---------------------------------------------------------------------------


async def test_a_second_spelling_joins_the_entity_it_already_has(store):
    """The live failure this table exists to fix: `Big Walk` and `big walk`
    were two rows that could not see each other, which is the precondition for
    either ever superseding the other."""
    entities, engine, _ = store
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
    entities, engine, _ = store
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
    entities, engine, _ = store
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
# promote — pass 3
# ---------------------------------------------------------------------------


async def test_degree_two_promotes_and_degree_one_does_not(store):
    """§6.1's whole criterion. `made_by "House House"` is a literal while it is
    the only House House claim; both rows promote the moment a second one
    arrives. Traversal becomes possible exactly when it becomes meaningful."""
    entities, engine, metrics = store
    await _fact(engine, subject="big walk", obj="House House", predicate="made_by")

    first = await entities.promote(workspace=WS, now_ms=NOW)

    assert first.entities_created == 0
    assert first.rows_resolved == 0
    assert first.below_degree == 1
    assert (await _objects(engine))["House House"] is None

    # A second edge position for the same normalised name — here as a SUBJECT,
    # which is half of what §6.1 counts and the half a test over objects alone
    # would miss.
    await _fact(engine, subject="house  house", obj="Melbourne",
                predicate="based_in")

    second = await entities.promote(workspace=WS, now_ms=NOW)

    assert second.entities_created == 1
    assert second.rows_resolved == 1
    assert (await _objects(engine))["House House"] is not None
    assert metrics.get("nora_fact_entities_created_total") == 1
    assert metrics.get("nora_fact_objects_resolved_total") == 1


async def test_promotion_keeps_the_words_the_room_used(store):
    """The property 23 §6.2 depends on: the embedded text is the rendered
    triple, and a triple whose object was eaten by canonicalisation embeds
    something the room never said. Resolution POINTS AT a thing; it does not
    rewrite what was written."""
    entities, engine, _ = store
    await _fact(engine, subject="big walk", obj="house  house")
    await _fact(engine, subject="house house", obj="Melbourne")

    await entities.promote(workspace=WS, now_ms=NOW)

    objects = await _objects(engine)
    assert "house  house" in objects
    assert objects["house  house"] is not None


async def test_the_canonical_name_is_the_most_frequent_spelling(store):
    """And every distinct spelling becomes an alias — which is how `big walk`
    and `Big Walk` stop being two rows that cannot see each other."""
    entities, engine, _ = store
    for _ in range(3):
        await _fact(engine, subject="s", obj="House House")
    await _fact(engine, subject="s", obj="house house")
    await _fact(engine, subject="House House", obj="Melbourne")

    await entities.promote(workspace=WS, now_ms=NOW)

    async with engine.connect() as conn:
        name, aliases = (await conn.execute(sa.text(
            "select canonical_name, aliases from fact_entity"
        ))).one()
    assert name == "House House"
    assert set(aliases) >= {"House House", "house house"}


async def test_promotion_counts_only_standing_facts(store):
    """Unadjudicated noise must not mint nodes, and neither may retracted
    belief: a superseded row is history. Two sightings where one is superseded
    is degree ONE."""
    entities, engine, _ = store
    await _fact(engine, subject="s", obj="House House")
    await _fact(engine, subject="House House", obj="Melbourne", superseded=True)

    result = await entities.promote(workspace=WS, now_ms=NOW)

    assert result.entities_created == 0
    assert result.below_degree == 1


async def test_a_vetoed_object_never_promotes_however_often_it_recurs(store):
    """Degree cannot rescue a veto. `"five"` appearing in twenty claims is
    still not a thing, and a node named five is one the graph would happily
    traverse to from every unrelated claim that mentions it."""
    entities, engine, metrics = store
    await _fact(engine, subject="big walk", obj="five", predicate="gourds")
    await _fact(engine, subject="five", obj="Melbourne")

    result = await entities.promote(workspace=WS, now_ms=NOW)

    assert result.entities_created == 0
    assert result.vetoed == 1
    assert metrics.get("nora_fact_objects_vetoed_total") == 1


async def test_promotion_is_idempotent(store):
    """30 §4: promotion is downstream of adjudication and re-runs. A concurrent
    or repeated pass must converge, not double-write — an alias union that
    appended on every run would grow without bound, and a second entity for the
    same name would fragment the thing this table exists to unify."""
    entities, engine, _ = store
    await _fact(engine, subject="s", obj="House House")
    await _fact(engine, subject="House House", obj="Melbourne")

    first = await entities.promote(workspace=WS, now_ms=NOW)
    second = await entities.promote(workspace=WS, now_ms=NOW + 1)

    assert first.rows_resolved == 1
    assert second.rows_resolved == 0
    async with engine.connect() as conn:
        count = (await conn.execute(
            sa.text("select count(*) from fact_entity"))).scalar_one()
        aliases = (await conn.execute(
            sa.text("select aliases from fact_entity"))).scalar_one()
    assert count == 1
    assert len(aliases) == len(set(aliases))


async def test_promotion_does_not_reach_a_sibling_workspace(store):
    """Degree is counted per workspace. Two rooms in different workspaces each
    naming a thing once do not add up to a thing."""
    entities, engine, _ = store
    await _fact(engine, subject="s", obj="House House", workspace="ws-a")
    await _fact(engine, subject="House House", obj="M", workspace="ws-b")

    result = await entities.promote(workspace="ws-a", now_ms=NOW)

    assert result.entities_created == 0
    assert result.below_degree == 1
