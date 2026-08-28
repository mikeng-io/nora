"""The render ledger — recognition's precondition, and the thing that turns
"Nora rendered it" from an assumption into a read.

Real Postgres throughout: the upsert, the window read and the prune are all SQL,
and a double would be reimplementing the three behaviours under test.

**Every read here asserts the STATUS as well as the rows.** `()` with `ok` is
«she rendered nothing about this» and licenses ruling a dispute out; `()` with
`unavailable` is «I could not check» and licenses nothing. A suite that only
looked at the tuple would pass with the two collapsed, which is the R-27
inversion the store exists to avoid.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.domain.facts import RenderedClaim
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.rendered_claims import SqlRenderedClaimStore
from nora.obs.metrics import Metrics

NOW = 1_755_100_000_000
CONV = "signal:g1"


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    metrics = Metrics()
    try:
        yield SqlRenderedClaimStore(engine, metrics=metrics), engine, metrics
    finally:
        await engine.dispose()


def _claim(row_key="f1", *, surface="big walk", at=NOW, tag="tag-1",
           conversation_id=CONV, kind="fact"):
    return RenderedClaim(
        conversation_id=conversation_id, kind=kind, row_key=row_key,
        surface=surface, workspace="nora", last_tag_id=tag,
        last_rendered_at=at,
    )


async def test_what_was_rendered_reads_back(store):
    ledger, _engine, metrics = store

    assert await ledger.record(claims=(_claim(),)) == "ok"

    read = await ledger.in_window(conversation_id=CONV, since_ms=NOW - 1)
    assert [c.row_key for c in read.claims] == ["f1"]
    assert [c.surface for c in read.claims] == ["big walk"]
    assert read.status == "ok"
    assert metrics.get("nora_rendered_claim_total", verb="record", status="ok") == 1


async def test_a_second_render_updates_rather_than_appending(store):
    """One row per rendered claim per conversation. Keyed on the tag too, the
    same fact rendered on forty consecutive tags would be forty rows read back
    with `DISTINCT ON` — for provenance `tag_trace` already carries."""
    ledger, engine, _ = store
    await ledger.record(claims=(_claim(at=NOW, tag="tag-1"),))

    await ledger.record(claims=(_claim(at=NOW + 5_000, tag="tag-2"),))

    async with engine.connect() as conn:
        count = (await conn.execute(
            sa.text("select count(*) from rendered_claim"))).scalar_one()
    assert count == 1
    read = await ledger.in_window(conversation_id=CONV, since_ms=NOW)
    assert read.claims[0].last_tag_id == "tag-2"
    assert read.claims[0].last_rendered_at == NOW + 5_000


async def test_the_window_excludes_what_fell_out_of_it(store):
    """The window is what makes «a claim Nora JUST rendered» mean something. A
    read with no floor would target everything she has ever said."""
    ledger, _engine, _ = store
    await ledger.record(claims=(
        _claim("old", at=NOW - 100_000),
        _claim("fresh", at=NOW),
    ))

    read = await ledger.in_window(conversation_id=CONV, since_ms=NOW - 10)

    assert [c.row_key for c in read.claims] == ["fresh"]
    # The positive control: a wider window finds both, so the exclusion above
    # is the floor and not a read that returns one row.
    wide = await ledger.in_window(conversation_id=CONV, since_ms=0)
    assert {c.row_key for c in wide.claims} == {"old", "fresh"}


async def test_the_ledger_never_crosses_a_conversation(store):
    """Recognition targets what THIS room was shown. A ledger that leaked
    across conversations would let a message in one room mark a row another
    room was told about."""
    ledger, _engine, _ = store
    await ledger.record(claims=(
        _claim("mine", conversation_id=CONV),
        _claim("theirs", conversation_id="signal:g2"),
    ))

    read = await ledger.in_window(conversation_id=CONV, since_ms=0)

    assert [c.row_key for c in read.claims] == ["mine"]


async def test_an_empty_turn_is_nothing_found_not_a_skipped_call(store):
    """A turn that rendered nothing is a real outcome. Collapsing it into "do
    not call the store" is how a wiring gap becomes indistinguishable from a
    quiet room one layer up."""
    ledger, _engine, metrics = store

    assert await ledger.record(claims=()) == "nothing_found"
    assert metrics.get(
        "nora_rendered_claim_total", verb="record", status="nothing_found") == 1


async def test_an_empty_window_is_nothing_found_and_not_unavailable(store):
    """The pair that must never collapse. This one licenses ruling a dispute
    out; the other does not."""
    ledger, _engine, metrics = store

    read = await ledger.in_window(conversation_id=CONV, since_ms=NOW)

    assert read.claims == ()
    assert read.status == "nothing_found"
    assert metrics.get(
        "nora_rendered_claim_total", verb="read", status="unavailable") == 0


async def test_an_unreadable_ledger_is_unavailable_and_never_empty(store):
    """The other half, driven by a real broken engine rather than a mock: an
    outage reported as «she rendered nothing» would let recognition rule out a
    dispute it never actually checked for."""
    _ledger, engine, metrics = store
    await engine.dispose()
    broken = SqlRenderedClaimStore(
        create_sql_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none"),
        metrics=metrics,
    )

    read = await broken.in_window(conversation_id=CONV, since_ms=0)

    assert read.claims == ()
    assert read.status == "unavailable"
    assert metrics.get(
        "nora_rendered_claim_total", verb="read", status="nothing_found") == 0


async def test_a_failed_write_is_counted_and_does_not_raise(store):
    """It runs on the tag path: a ledger write failing must not cost a reply.
    It IS counted, because the consequence lands on the NEXT turn's targeting,
    which then has to report `unavailable` rather than pretend the ledger was
    empty — and a silent failure would make that impossible to explain."""
    _ledger, _engine, metrics = store
    broken = SqlRenderedClaimStore(
        create_sql_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none"),
        metrics=metrics,
    )

    assert await broken.record(claims=(_claim(),)) == "unavailable"
    assert metrics.get(
        "nora_rendered_claim_total", verb="record", status="unavailable") == 1


async def test_prune_drops_what_fell_out_of_the_window_and_counts_it(store):
    """A working set that is never pruned is a table nobody decided the size
    of."""
    ledger, _engine, metrics = store
    await ledger.record(claims=(
        _claim("old", at=NOW - 100_000),
        _claim("fresh", at=NOW),
    ))

    removed = await ledger.prune(before_ms=NOW - 10)

    assert removed == 1
    assert metrics.get("nora_rendered_claim_pruned_total") == 1
    # The positive control: what was inside the window survived, so the prune
    # is a horizon and not a truncation.
    read = await ledger.in_window(conversation_id=CONV, since_ms=0)
    assert [c.row_key for c in read.claims] == ["fresh"]


async def test_a_blank_surface_is_refused_by_the_database(store):
    """`''` and `'   '` are non-NULL to every query asking what a rendered
    claim was ABOUT, so a blank surface is a row that matches nothing and
    reports as recorded."""
    _ledger, engine, _ = store

    async with engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(
                sa.text(
                    "insert into rendered_claim (conversation_id, kind, "
                    "row_key, surface, last_tag_id, last_rendered_at) values "
                    "('c', 'fact', 'k', '   ', 't', 1)"
                )
            )


async def test_an_unknown_kind_is_refused(store):
    """`fact` and `lexeme`, and the CHECK is what lets the language tier join
    without a migration while stopping a third kind arriving unannounced."""
    _ledger, engine, _ = store

    async with engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            await conn.execute(
                sa.text(
                    "insert into rendered_claim (conversation_id, kind, "
                    "row_key, surface, last_tag_id, last_rendered_at) values "
                    "('c', 'note', 'k', 's', 't', 1)"
                )
            )
