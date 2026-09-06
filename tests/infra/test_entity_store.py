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

import ast
import inspect
import textwrap

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
    "9 August", "Aug 9", "August", "2026-08-28", "19:30", "40",
    "five", "$40", "three hours", "3 days",
    # CJK, which the first version had NO rule for at all — so every case §6.1
    # names was admitted the moment the room wrote it in the language it
    # actually uses.
    "三個月", "五蚊", "十分鐘", "兩年",
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
    # Every one of these was REFUSED by the first version, which vetoed on the
    # leading token alone: a weekday, a spelled numeral or a month name
    # happening to start a real name. In a Hong Kong room these are not
    # recoverable missing edges, they are permanent holes exactly where the
    # graph is most useful.
    "Sun Hung Kai", "Sun Yat-sen Memorial Park", "Seven Sisters",
    "Ten Thousand Buddhas Monastery", "1881 Heritage", "May Chan",
    "one piece",
    # Tokenised on whitespace ONLY, never on punctuation — which is what keeps
    # this one token rather than the two numerals it would otherwise split
    # into.
    "7-Eleven",
    # The CJK arm stays narrow on purpose: each carries a character outside the
    # numeral and unit sets.
    "七姊妹", "十萬個為什麼",
])
def test_the_veto_admits_things(surface):
    """The positive control, and it is not decoration: a veto that refused
    everything would satisfy every test above while making promotion
    structurally impossible — which is exactly the failure 21 §10 names as
    what would prove the contract wrong."""
    assert not vetoed(surface)


@pytest.mark.parametrize("surface", ["5 gourds", "3 towers"])
def test_a_counted_noun_is_admitted_and_degree_is_what_stops_it(surface):
    """Written down because it looks like a gap and is a decision.

    `5 gourds` IS a quantity, and the veto lets it through: separating it from
    `1881 Heritage` needs to know which words are nouns, and the alternative —
    "a leading numeral plus one word" — refuses the Hong Kong entity to catch
    the gourd. An enumeration of countable nouns is the list §6.1 says nobody
    can finish.

    **Degree is the backstop, and it is the stronger one.** A counted noun has
    to appear in a SECOND edge position before it becomes anything, and
    quantities do not recur that way. The veto's job is the common recurring
    forms — a bare numeral, a price, a date — and the tail is arithmetic's."""
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
# lookup — the tool-facing read (design/dreamer-agent-loop/32 §3.4)
# ---------------------------------------------------------------------------


async def test_lookup_returns_the_entity_with_its_surfaces(store):
    """`resolve` answers with an ID; the tool layer's `get_knowledge` needs
    the SURFACES. `fact.subject_id` holds the claim's text verbatim, not an
    entity id, so the standing-facts read that follows a resolution must be
    asked with the canonical name and every alias — an id answers none of
    that."""
    entities, engine, _ = store
    async with engine.begin() as conn:
        eid, _ = await entities.resolve_or_create(
            conn, workspace=WS, canonical_name="Big Walk",
            aliases=("大步行",), now_ms=NOW,
        )

    found = await entities.lookup(workspace=WS, name="big  walk")

    assert found is not None
    assert found.id == eid
    assert found.workspace == WS
    assert found.canonical_name == "Big Walk"
    assert found.aliases == ("大步行",)


async def test_lookup_resolves_through_an_alias_and_reports_none_for_nothing(
    store,
):
    """Same normalisation as `resolve`, and the same two meanings kept apart:
    an alias answers with the entity, an unknown name answers `None` — never
    the first entity in the workspace."""
    entities, engine, _ = store
    async with engine.begin() as conn:
        eid, _ = await entities.resolve_or_create(
            conn, workspace=WS, canonical_name="House House",
            aliases=("house  house",), now_ms=NOW,
        )

    by_alias = await entities.lookup(workspace=WS, name="HOUSE HOUSE")

    assert by_alias is not None and by_alias.id == eid
    assert await entities.lookup(workspace=WS, name="Big Walk") is None
    assert await entities.lookup(workspace=WS, name="   ") is None


async def test_lookup_raises_on_an_unreachable_store(pg_url):
    """The correction `resolve`'s own docstring states, held on the new verb:
    `None` is «no such entity» and must never ALSO mean «the store could not
    be read» — the tool maps the raise to `unavailable`, and a lookup that
    collapsed the two would report an outage as «nothing is known».

    A NAMED exception, never `pytest.raises(Exception)` (ruff's B017): the
    outage here is a database that does not exist, and a blind assert would
    also pass on a typo'd attribute name."""
    import asyncpg

    down = pg_url.rsplit("/", 1)[0] + "/t_no_such_database"
    engine = create_sql_engine(down)
    try:
        with pytest.raises(asyncpg.InvalidCatalogNameError):
            await SqlEntityStore(engine).lookup(workspace=WS, name="Big Walk")
    finally:
        await engine.dispose()



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


async def test_a_self_referential_fact_does_not_promote_itself(store):
    """Degree is counted over DISTINCT FACTS, not endpoints.

    Counting per endpoint made one row whose subject and object normalise
    alike — `big walk is_called Big Walk`, which is precisely the shape this
    table exists for — reach degree 2 FROM ITSELF, minting an entity whose only
    edge is a self-loop. §6.1's own justification is that a node of degree one
    is a string with extra steps because there is nothing to traverse to, and a
    self-loop is exactly that."""
    entities, engine, _ = store
    await _fact(engine, subject="big walk", obj="Big  Walk", predicate="is_called")

    result = await entities.promote(workspace=WS, now_ms=NOW)

    assert result.entities_created == 0
    assert result.below_degree == 1
    # The positive control: a SECOND, genuinely different fact naming the same
    # thing does promote it, so the zero above is about the self-loop.
    await _fact(engine, subject="melbourne", obj="big walk", predicate="hosts")
    assert (await entities.promote(
        workspace=WS, now_ms=NOW)).entities_created == 1


async def test_a_new_spelling_does_not_duplicate_the_alias_array(store):
    """`promote` passes `aliases=tuple(spellings)`, which CONTAINS the canonical
    name it also passes — so the incoming list is not distinct from itself, and
    a list deduped only against what is already stored grew a duplicate on
    every pass after a new spelling became the most frequent.

    The idempotence test could not see it: it never introduced a new spelling
    between passes, which is the only way to reach the branch."""
    entities, engine, _ = store
    await _fact(engine, subject="s", obj="House House")
    await _fact(engine, subject="House House", obj="Melbourne")
    await entities.promote(workspace=WS, now_ms=NOW)

    # A second spelling, now the most frequent one.
    for _ in range(3):
        await _fact(engine, subject="s2", obj="house house")
    await entities.promote(workspace=WS, now_ms=NOW + 1)
    await entities.promote(workspace=WS, now_ms=NOW + 2)

    async with engine.connect() as conn:
        aliases = (await conn.execute(
            sa.text("select aliases from fact_entity"))).scalar_one()
    assert len(aliases) == len(set(aliases)), aliases
    assert set(aliases) >= {"House House", "house house"}


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
    assert metrics.get("nora_fact_objects_vetoed", workspace=WS) == 1


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


async def test_the_census_readings_are_levels_and_do_not_accumulate(store):
    """`vetoed` and `below_degree` are recomputed from the WHOLE standing table
    on every pass, and promotion runs on every adjudication tick that wrote a
    fact. As counters they measured ticks times rows while
    `entities_created_total` measured real events — and the ratio between those
    two is 21 §10's own test for what would prove the substrate contract wrong,
    so it was uninterpretable.

    Three passes over unchanged data must read the same number, not three times
    it."""
    entities, engine, metrics = store
    await _fact(engine, subject="big walk", obj="five", predicate="gourds")
    await _fact(engine, subject="big walk", obj="House House", predicate="made_by")

    for tick in range(3):
        await entities.promote(workspace=WS, now_ms=NOW + tick)

    assert metrics.get("nora_fact_objects_vetoed", workspace=WS) == 1
    assert metrics.get("nora_fact_objects_below_degree", workspace=WS) == 1
    # The positive control: the EVENT counter is the one that may move, and it
    # did not here because nothing promoted — so the two readings above are
    # levels rather than a pass that never ran.
    assert metrics.get("nora_fact_entities_created_total") == 0


async def test_a_pass_that_found_none_reads_zero_rather_than_absent(store):
    """0 means the pass ran and found none; ABSENT means it has never run for
    this workspace. Collapsing them is how a tier that is not wired looks
    exactly like a tier with nothing to say."""
    entities, engine, metrics = store
    await _fact(engine, subject="s", obj="House House")
    await _fact(engine, subject="House House", obj="Melbourne")

    await entities.promote(workspace=WS, now_ms=NOW)

    assert metrics.get("nora_fact_objects_vetoed", workspace=WS) == 0
    assert metrics.get("nora_fact_entities_created_total") == 1


async def test_promotion_does_not_reach_a_sibling_workspace(store):
    """Degree is counted per workspace. Two rooms in different workspaces each
    naming a thing once do not add up to a thing."""
    entities, engine, _ = store
    await _fact(engine, subject="s", obj="House House", workspace="ws-a")
    await _fact(engine, subject="House House", obj="M", workspace="ws-b")

    result = await entities.promote(workspace="ws-a", now_ms=NOW)

    assert result.entities_created == 0
    assert result.below_degree == 1


async def _pending_candidate(engine, *, subject: str, predicate: str,
                             workspace: str = WS) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "insert into fact_candidate "
                "(id, workspace, subject_id, predicate, object, status, "
                " occurrence_count, first_seen_at, last_seen_at, observed_in, "
                " instruction_sha256, model, established_by) values "
                "(gen_random_uuid(), :ws, :s, :p, 'v', 'pending', 1, :t, :t, "
                " '{}'::text[], :sha, 'm', 'run-1')"
            ),
            {"ws": workspace, "s": subject, "p": predicate, "t": NOW,
             "sha": "a" * 64},
        )


async def _note(engine, *, anchor_entity_ids, workspace: str = WS,
                 retired: bool = False) -> None:
    """The one column the note tier's own queue reads —
    `anchor_entity_ids` — inserted directly rather than through
    `SqlNoteStore`, which this file deliberately does not import (kept
    isolated to `SqlEntityStore`, like `_fact`/`_pending_candidate` above).
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
    _store, engine, _metrics = store
    await _note(engine, anchor_entity_ids=("Grant Carver",))

    assert await _store.unresolved_note_anchor_names(
        workspace=WS, limit=10,
    ) == ("Grant Carver",)


async def test_unresolved_note_anchor_names_excludes_what_already_resolves(store):
    """A name that resolves NOW is not a near-miss — it is an exact match the
    deterministic pass will pick up the next time the note is touched, and
    offering it to the model would ask a question that already has a free
    answer."""
    entities, engine, _metrics = store
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
    _store, engine, _metrics = store
    await _note(engine, anchor_entity_ids=("Grant Carver",), retired=True)

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=10) == ()


async def test_unresolved_note_anchor_names_deduplicates_and_orders(store):
    _store, engine, _metrics = store
    await _note(engine, anchor_entity_ids=("delta", "alpha"))
    await _note(engine, anchor_entity_ids=("alpha", "charlie", "bravo"))

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=3) == (
        "alpha", "bravo", "charlie",
    )


async def test_unresolved_note_anchor_names_never_reaches_another_workspace(store):
    _store, engine, _metrics = store
    await _note(engine, anchor_entity_ids=("only-in-ws-b",), workspace="ws-b")

    assert await _store.unresolved_note_anchor_names(workspace=WS, limit=10) == ()


async def test_unresolved_names_are_ordered_rather_than_whatever_the_plan_gave(
    store,
):
    """**This had no ORDER BY at all**, which is worse than the ties elsewhere
    in the tier rather than better: `SELECT DISTINCT` returns rows in whatever
    order the hash aggregate produces, and that shifts with statistics and
    with parallelism. `[:limit]` over it chose pass 6's per-tick
    entity-resolution budget arbitrarily — which names got adjudicated varied
    run to run, and a name past the limit could be starved indefinitely.

    Asserted with a limit BELOW the population, because that is the case the
    defect bites in: with `limit >= n` every ordering returns the same set and
    the test would pass against an unordered query.

    **This test alone cannot catch the defect, and that is measured rather
    than assumed.** Removing the `ORDER BY` leaves it GREEN: at this size
    Postgres plans the `DISTINCT` as a sort rather than a hash aggregate, so
    the rows come back ordered anyway. What it establishes is the positive
    half — the order is the right one and the limit is applied after the dedup.
    The negative half is `test_the_unresolved_read_declares_its_order_in_sql`
    below, and the two together are the check; either alone is a claim.
    """
    _store, engine, _metrics = store
    for subject in ("delta", "alpha", "charlie", "bravo"):
        await _pending_candidate(engine, subject=subject, predicate="p")

    first = await _store.unresolved_names(workspace=WS, limit=2)
    again = await _store.unresolved_names(workspace=WS, limit=2)

    assert first == ("alpha", "bravo"), first
    assert again == first, "two reads of an unchanged table must agree"


async def test_the_unresolved_read_still_finds_everything_when_unlimited(store):
    """The positive control: an ordering that dropped rows, or a limit applied
    before the dedup, would satisfy the test above while making pass 6 blind
    to most of the queue."""
    _store, engine, _metrics = store
    for subject in ("delta", "alpha", "charlie", "bravo"):
        await _pending_candidate(engine, subject=subject, predicate="p")

    assert set(await _store.unresolved_names(workspace=WS, limit=10)) == {
        "alpha", "bravo", "charlie", "delta",
    }


def test_the_unresolved_read_declares_its_order_in_sql():
    """**The half a behavioural test cannot carry.**

    "Postgres MAY return any order among equals" is a statement about what the
    planner is permitted to do, not about what it does today — so no execution
    can falsify its absence: removing the `ORDER BY` and observing sorted
    output proves only that this size and this statistics snapshot happened to
    plan a sort. CLAUDE.md's rule is to state what a positive result would look
    like before concluding from a null one, and here there is no positive
    result available at all.

    So the ordering clause is asserted STRUCTURALLY, in the one place that can
    see it. Reading the source rather than the plan is deliberate: `EXPLAIN`
    would tell us what this planner chose, which is the thing that is allowed
    to change.
    """
    # THE DOCSTRING IS STRIPPED FIRST, and the first version of this test did
    # not do it — so it passed with the clause deleted, because the docstring
    # explaining the fix contains the words `ORDER BY`. A structural check
    # that reads prose is the same defect one layer up: an instrument that
    # cannot fail. Found by running the mutation rather than by reading it.
    fn = SqlEntityStore.unresolved_names
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
    ):
        del tree.body[0]
    source = ast.unparse(tree)

    assert "order by" in source.lower(), (
        "unresolved_names slices a LIMIT off this query, so an unordered "
        "SELECT DISTINCT makes pass 6's per-tick budget arbitrary and can "
        "starve a name indefinitely"
    )
