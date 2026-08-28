"""`SqlEmbeddingStore` — the reader and writer `embedding` shipped without.

Real Postgres and real pgvector throughout. Both behaviours under test are SQL
— an upsert through a declared type and a cosine ordering — and a double would
be reimplementing the two things that can actually go wrong.

**Every read asserts the STATUS as well as the rows**, for
`SqlRenderedClaimStore`'s reason one table over: `()` with `ok` is «nothing is
stale» and licenses skipping the pass, `()` with `unavailable` is «I could not
check» and licenses nothing. A suite that only looked at the tuple would pass
with the two collapsed.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest
import sqlalchemy as sa

from nora.infra.sql.embeddings import SqlEmbeddingStore, StoredEmbedding
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS
from nora.obs.metrics import Metrics

NOW = 1_755_100_000_000
WS = "ws-main"


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    metrics = Metrics()
    try:
        yield SqlEmbeddingStore(engine, metrics=metrics), engine, metrics
    finally:
        await engine.dispose()


def _row(row_key: str, *, text: str | None = None, fp: str | None = None,
         scope: str = WS) -> StoredEmbedding:
    return StoredEmbedding(
        kind="fact", row_key=row_key, scope=scope,
        text=text or f"{row_key} │ means │ a thing",
        model="bge-m3", fingerprint=fp or (row_key * 8)[:64],
        embedded_at=NOW, has_vector=True,
    )


def _vec(fill: float = 0.1) -> tuple[float, ...]:
    return tuple([fill] * EMBEDDING_DIMENSIONS)


async def test_a_written_embedding_reads_back_with_its_fingerprint(store):
    """The round trip pass 4 depends on: what was written is what comes back,
    and the fingerprint — the whole staleness predicate — survives it."""
    emb, _engine, metrics = store

    assert await emb.upsert(rows=((_row("f1"), _vec()),)) == 1

    read = await emb.existing(kind="fact", scope=WS, row_keys=("f1",))
    assert read.status == "ok"
    assert [r.row_key for r in read.rows] == ["f1"]
    assert read.rows[0].fingerprint == ("f1" * 8)[:64]
    assert read.rows[0].has_vector is True
    assert metrics.get(
        "nora_embedding_store_total", verb="upsert", status="ok") == 1


async def test_an_unembedded_key_is_nothing_found_not_an_empty_ok(store):
    """R-27 at the boundary. Pass 4 treats `nothing_found` as «embed it» and
    `unavailable` as «do not touch the watermark», so collapsing them would
    make a database outage look like a workspace with nothing to do."""
    emb, _engine, metrics = store

    read = await emb.existing(kind="fact", scope=WS, row_keys=("nope",))

    assert read.rows == ()
    assert read.status == "nothing_found"
    assert metrics.get(
        "nora_embedding_store_total", verb="existing",
        status="nothing_found") == 1


async def test_a_read_against_a_closed_engine_is_unavailable(store):
    """The other half of the same rule, and the one a mock cannot produce: a
    real disposed engine, so the failure is the driver's rather than a raise
    the test arranged."""
    _emb, _engine, metrics = store

    # A disposed engine RECONNECTS on next use, so disposing the fixture's
    # would prove nothing. An unreachable URL is the honest way to make the
    # driver itself fail.
    emb_broken = SqlEmbeddingStore(
        create_sql_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none"),
        metrics=metrics,
    )
    read = await emb_broken.existing(kind="fact", scope=WS, row_keys=("f1",))

    assert read.rows == ()
    assert read.status == "unavailable"
    assert metrics.get(
        "nora_embedding_store_total", verb="existing",
        status="unavailable") == 1


async def test_re_embedding_replaces_the_vector_rather_than_duplicating(store):
    """`(kind, row_key)` is the primary key and the write is an UPSERT: a model
    swap re-embeds every row, and a store that inserted instead would double
    the table on every swap and rank the same claim twice."""
    emb, _engine, _metrics = store

    await emb.upsert(rows=((_row("f1", fp="a" * 64), _vec(0.1)),))
    await emb.upsert(rows=((_row("f1", fp="b" * 64), _vec(0.9)),))

    read = await emb.existing(kind="fact", scope=WS, row_keys=("f1",))
    assert len(read.rows) == 1
    assert read.rows[0].fingerprint == "b" * 64


async def test_the_dense_read_ranks_by_distance_and_respects_the_floor(store):
    """The retrieval channel, end to end. The floor is a MAXIMUM DISTANCE —
    rows beyond it are dropped rather than ranked, because a retrieval that
    always returns something cannot say «I found nothing about that»."""
    emb, _engine, _metrics = store
    near = _vec(0.1)
    far = tuple([0.1] * (EMBEDDING_DIMENSIONS - 1) + [-9.0])
    await emb.upsert(rows=(
        (_row("near"), near),
        (_row("far"), far),
    ))

    read = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=near, limit=8, floor=0.5,
    )

    assert read.status == "ok"
    assert [m.row_key for m in read.hits] == ["near"], (
        "the far row is outside the floor and must be dropped, not ranked"
    )
    assert read.hits[0].distance < 0.5


async def test_everything_beyond_the_floor_is_nothing_found(store):
    """The positive control for the test above: a floor that dropped nothing
    would satisfy it whenever the corpus happened to be close, and a store
    that returned `ok` over an empty list would tell the verb it found
    something."""
    emb, _engine, _metrics = store
    await emb.upsert(rows=((
        _row("far"), tuple([0.1] * (EMBEDDING_DIMENSIONS - 1) + [-9.0]),
    ),))

    read = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=_vec(0.1), limit=8, floor=0.5,
    )

    assert read.hits == ()
    assert read.status == "nothing_found"


async def test_the_dense_read_never_crosses_a_scope(store):
    """R-49 in the retrieval verb. A search that could not scope would be the
    relay defect in a new place — one social unit's claims reaching another's
    room — so the filter is in the same statement as the ranking, applied
    before it."""
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("mine", scope=WS), _vec(0.1)),
        (_row("theirs", scope="ws-other"), _vec(0.1)),
    ))

    read = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=_vec(0.1), limit=8, floor=0.5,
    )

    assert [m.row_key for m in read.hits] == ["mine"]


async def test_a_row_awaiting_its_vector_is_not_a_retrieval_hit(store):
    """`v1024` is nullable so pass 4 can record the text before the endpoint
    answers. A NULL must not rank: `<=>` against NULL is NULL, which sorts
    unpredictably and would put an unembedded row anywhere in the list."""
    emb, _engine, _metrics = store
    # THROUGH THE TYPE, not raw SQL. `upsert` used to demand a vector tuple, so
    # the one state the schema explicitly supports — text recorded, vector
    # still to come — could not be expressed at all and this test had to reach
    # around the store to create it. A type that cannot express a state the
    # design requires pushes every caller around it.
    await emb.upsert(rows=(
        (_row("real"), _vec(0.1)),
        (_row("queued"), None),
    ))

    read = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=_vec(0.1), limit=8, floor=0.5,
    )

    assert [m.row_key for m in read.hits] == ["real"]

    # The positive control: the queued row IS in the table and IS visible to
    # the staleness read, so this is the ranking excluding it rather than the
    # insert having failed.
    read = await emb.existing(kind="fact", scope=WS, row_keys=("queued",))
    assert [r.row_key for r in read.rows] == ["queued"]
    assert read.rows[0].has_vector is False


async def test_forget_removes_only_the_named_rows(store):
    """An embedding's lifetime is its row's, so this is called when a row goes
    rather than on a clock. A delete that took the whole kind would silently
    un-embed the workspace on one supersession."""
    emb, _engine, _metrics = store
    await emb.upsert(rows=((_row("a"), _vec()), (_row("b"), _vec())))

    assert await emb.forget(kind="fact", row_keys=("a",)) == 1

    read = await emb.existing(kind="fact", scope=WS, row_keys=("a", "b"))
    assert [r.row_key for r in read.rows] == ["b"]


async def test_the_dense_read_never_mixes_two_models_geometries(store):
    """**A mixed table is a retrieval that quietly ranks on two different
    geometries.**

    A model swap re-embeds one batch at a time, so for the length of that
    sweep the column holds vectors from two models. Cosine distances across
    them are not comparable and NOTHING ABOUT THE RESULT WOULD SAY SO — the
    list comes back ranked, plausible, and wrong. The column's dimension does
    not catch it either: two 1024-d models share a width and share nothing
    else.

    Filtering to the caller's model makes the mid-sweep state honest instead.
    Rows not yet re-embedded are invisible rather than wrongly ranked, which
    is `nothing_found` shrinking for a while — bounded and visible — rather
    than a wrong answer.

    The old row is deliberately the CLOSER one, so a store that ignored the
    model ranks it FIRST and this test fails on the ordering rather than on a
    count — a test where the wrong row merely appeared somewhere could pass on
    a lucky sort.

    That requires the two vectors to be NON-PARALLEL, which the first version
    of this test got wrong: it used fills of 0.1 and 0.2, and `0.2 = 2 * 0.1`
    is the same direction, so cosine distance from the probe was exactly
    0.000000 for both and the tie fell to `row_key`. The docstring claimed a
    property the arithmetic did not have — the same defect as a structural
    check that greps its own explanatory comment. The new row now points
    somewhere else, so «closer» is a real ordering.
    """
    emb, _engine, _metrics = store
    probe = _vec(0.1)
    await emb.upsert(rows=(
        (StoredEmbedding(
            kind="fact", row_key="old", scope=WS, text="x",
            model="bge-m2", fingerprint="a" * 64, embedded_at=NOW,
            has_vector=True,
        ), probe),
        (StoredEmbedding(
            kind="fact", row_key="new", scope=WS, text="y",
            model="bge-m3", fingerprint="b" * 64, embedded_at=NOW,
            has_vector=True,
        # NON-PARALLEL to the probe and still inside the floor: 100 of 1024
        # dimensions flipped puts it at cosine distance 2*100/1024 ~= 0.195,
        # comfortably under 0.5 and strictly further than the old row, which
        # IS the probe and sits at 0. Flipping every dimension would put it at
        # ~2 and it would be dropped by the floor instead of out-ranked, which
        # is a different test.
        ), tuple([-0.1] * 100 + [0.1] * (EMBEDDING_DIMENSIONS - 100))),
    ))

    read = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=probe, limit=8,
        floor=0.5,
    )

    assert [m.row_key for m in read.hits] == ["new"], (
        "the row embedded under another model must not rank at all, however "
        "close its vector happens to be"
    )
    assert read.status == "ok"

    # The positive control: the old row IS in the table and IS reachable — so
    # this is the model filter excluding it from RANKING, not the write having
    # failed.
    read = await emb.existing(kind="fact", scope=WS, row_keys=("old",))
    assert [r.model for r in read.rows] == ["bge-m2"]


async def test_the_lexical_channel_finds_a_term_and_ranks_the_predicate(store):
    """`23` §4's cheap channel, and the ranking is the part that matters: a
    natural-language question must put the predicate it ASKED FOR above the
    other triples about the same subject.

    Measured before it was written (2026-08-28): `who made big walk` scores
    0.824 against `made_by` and 0.588 against `means`, so the ordering is a
    real property of `word_similarity` over a triple rather than an artefact
    of insertion order.
    """
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("made", text="big walk │ made_by │ House House"), _vec()),
        (_row("means", text="big walk │ means │ a walking activity"), _vec()),
        (_row("other", text="the wedding │ of │ Grant"), _vec()),
    ))

    read = await emb.lexical(
        kind="fact", scope=WS, query="who made big walk", limit=8,
    )

    assert read.status == "ok"
    assert [m.row_key for m in read.hits] == ["made", "means"], (
        "the unrelated triple must not clear the threshold, and the predicate "
        "the question asked for must rank first"
    )
    assert read.hits[0].distance < read.hits[1].distance


async def test_the_lexical_channel_matches_cantonese_terms(store):
    """Contrary to the note tier's two-character trigram finding, which is
    about a different unit: that store matched a three-sentence body where a
    short query shares few trigrams out of dozens. A triple is six tokens, so
    the word extent IS the term — `打風` measured at 1.000 on its own row and
    0.000 on every other."""
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("typhoon", text="打風 │ means │ a typhoon"), _vec()),
        (_row("walk", text="big walk │ means │ a walking activity"), _vec()),
    ))

    read = await emb.lexical(
        kind="fact", scope=WS, query="打風", limit=8,
    )

    assert [m.row_key for m in read.hits] == ["typhoon"]


async def test_a_paraphrased_question_falls_below_the_lexical_threshold(store):
    """**`23` §6's split, as a measurement rather than an argument.** The
    Cantonese question `邊個做嘅大步行` scores 0.250 against the row it is
    about — below 0.3 — so the lexical channel correctly reports
    `nothing_found` and the dense channel is what closes it.

    This test exists to fix that boundary: if a later change lowered the
    threshold to "catch more", it would admit noise from every unrelated row
    at the same score, and the dense channel's whole justification would
    quietly become an optimisation instead of a requirement.
    """
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("hh", text="大步行 │ made_by │ House House"), _vec()),
    ))

    read = await emb.lexical(
        kind="fact", scope=WS, query="邊個做嘅大步行", limit=8,
    )

    assert read.hits == ()
    assert read.status == "nothing_found"


async def test_the_lexical_channel_never_crosses_a_scope(store):
    """R-49 again, on the other channel. A guard on one of two channels is not
    a guard."""
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("mine", text="big walk │ means │ a walk", scope=WS), _vec()),
        (_row("theirs", text="big walk │ means │ a walk",
              scope="ws-other"), _vec()),
    ))

    read = await emb.lexical(
        kind="fact", scope=WS, query="big walk", limit=8,
    )

    assert [m.row_key for m in read.hits] == ["mine"]


async def _stored_vector(engine, row_key: str):
    """The column itself, read around the store.

    `StoredEmbedding` deliberately omits the vector — nothing outside the
    store has a use for 1024 floats — and that is right for the type and fatal
    for a test about the column. Reading it here is the same move the queued
    row's positive control makes: when the type cannot see the thing under
    test, the test looks past the type rather than pretending.
    """
    async with engine.connect() as conn:
        return (await conn.execute(sa.text(
            "select v1024::text from embedding where row_key = :k"
        ), {"k": row_key})).scalar_one()


async def test_re_embedding_actually_replaces_the_vector(store):
    """**The test named for this could not see it.**

    Deleting `"v1024": stmt.excluded.v1024` from the upsert's `set_` — so a
    re-embed rewrites text, model, fingerprint and `embedded_at` and leaves
    the OLD VECTOR — left the whole suite green, because every assertion was
    on the fields `StoredEmbedding` carries and the vector is not one of them.

    What that costs is exactly the corruption this module exists to prevent,
    surviving inside the mechanism built to prevent it: a model swap rewrites
    the model name on every row, the sweep reports a clean re-embed, `nearest`
    filters on the NEW model — and ranks on the OLD model's geometry.
    Permanently, and invisibly, because every label says the migration
    succeeded. The model filter makes it worse rather than better, since the
    rows now pass it while carrying foreign vectors.
    """
    emb, engine, _metrics = store
    await emb.upsert(rows=((_row("f1", fp="a" * 64), _vec(0.1)),))
    before = await _stored_vector(engine, "f1")

    await emb.upsert(rows=((_row("f1", fp="b" * 64), _vec(0.9)),))
    after = await _stored_vector(engine, "f1")

    assert before != after, (
        "a re-embed must replace the vector, not only the fingerprint that "
        "describes it"
    )
    assert after.startswith("[0.9")


async def test_a_dense_read_against_a_broken_engine_is_unavailable(store):
    """**Issue #71's defect on the verb that faces a user.** Postgres down,
    retrieval asks, gets `nothing_found`, and she says «I don't know anything
    about that» when the truth is she could not look.

    Only `existing` had an outage test, so changing this branch's status to
    `nothing_found` left the suite green.
    """
    _emb, _engine, metrics = store
    broken = SqlEmbeddingStore(
        create_sql_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none"),
        metrics=metrics,
    )

    read = await broken.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=_vec(0.1), limit=8,
        floor=0.5,
    )

    assert read.hits == ()
    assert read.status == "unavailable"
    assert metrics.get(
        "nora_embedding_store_total", verb="nearest",
        status="unavailable") == 1


async def test_a_lexical_read_against_a_broken_engine_is_unavailable(store):
    """The same rule on the other channel. A guard on one of two channels is
    not a guard, and the verb above collapses `nothing_found` when EITHER
    channel silently reports the wrong one."""
    _emb, _engine, metrics = store
    broken = SqlEmbeddingStore(
        create_sql_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none"),
        metrics=metrics,
    )

    read = await broken.lexical(
        kind="fact", scope=WS, query="big walk", limit=8,
    )

    assert read.status == "unavailable"


async def test_the_staleness_read_never_crosses_a_scope(store):
    """**Worse than a leak.** Key `K` is embedded in `ws-other`; pass 4 sweeps
    `ws-main`; without the scope filter `existing` returns the other
    workspace's row, ITS fingerprint decides `ws-main`'s staleness, `K` is
    never embedded in `ws-main` at all, and one workspace's text silently
    drives the other's decision.

    The sibling test guards `nearest`'s scope. Nobody guarded this one, and
    deleting the filter left the suite green.
    """
    emb, _engine, _metrics = store
    await emb.upsert(rows=(
        (_row("shared", text="theirs", scope="ws-other"), _vec()),
    ))

    read = await emb.existing(
        kind="fact", scope=WS, row_keys=("shared",),
    )

    assert read.rows == ()
    assert read.status == "nothing_found"

    # The positive control: the row IS there under its own scope, so this is
    # the filter excluding it rather than the write having failed.
    theirs = await emb.existing(
        kind="fact", scope="ws-other", row_keys=("shared",),
    )
    assert [r.row_key for r in theirs.rows] == ["shared"]


async def test_the_staleness_read_is_ordered_under_its_own_limit(store):
    """`existing` has no LIMIT today, but it is ordered, and the ordering is
    what makes two reads of unchanged state agree — the property the whole
    tie-break round established for every other read in this tier.

    Written after a review pointed out that this one shipped with a comment
    and no test, which is the exact thing that round was about.
    """
    emb, _engine, _metrics = store
    await emb.upsert(rows=tuple(
        (_row(k), _vec()) for k in ("f3", "f1", "f2")
    ))

    first = await emb.existing(
        kind="fact", scope=WS, row_keys=("f3", "f1", "f2"),
    )
    again = await emb.existing(
        kind="fact", scope=WS, row_keys=("f2", "f3", "f1"),
    )

    assert [r.row_key for r in first.rows] == ["f1", "f2", "f3"]
    assert [r.row_key for r in again.rows] == [r.row_key for r in first.rows], (
        "the answer must not depend on the order the caller asked in"
    )


def test_the_staleness_read_declares_its_order_in_sql():
    """**The behavioural test above cannot falsify this, and I measured that
    rather than assuming it**: deleting the `ORDER BY` leaves it green,
    because at three keys Postgres returns them in a stable order anyway.

    "Postgres may return any order" is a statement about what the planner is
    PERMITTED to do, so its absence is unfalsifiable by execution. The clause
    is asserted in the source instead, with the docstring stripped via the AST
    — a structural check that reads prose is an instrument that cannot fail.
    """
    fn = SqlEmbeddingStore.existing
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
    ):
        del tree.body[0]
    source = ast.unparse(tree)

    assert "order_by" in source, (
        "two reads of unchanged state must agree, and only the clause can "
        "promise that"
    )


async def test_an_empty_probe_is_unavailable_and_a_zero_limit_is_not(store):
    """Two different silences. An empty probe means the caller could not
    compute a query vector — «I could not look» — while `limit <= 0` means
    nobody asked for any rows. The first version returned `nothing_found` for
    both and counted neither, so the verb under-counted while its help text
    promised three states."""
    emb, _engine, metrics = store

    empty = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=(), limit=8, floor=0.5,
    )
    zero = await emb.nearest(
        kind="fact", scope=WS, model="bge-m3", probe=_vec(), limit=0,
        floor=0.5,
    )

    assert empty.status == "unavailable"
    assert zero.status == "nothing_found"
    assert metrics.get(
        "nora_embedding_store_total", verb="nearest",
        status="unavailable") == 1
    assert metrics.get(
        "nora_embedding_store_total", verb="nearest",
        status="nothing_found") == 1


async def test_a_wrong_width_probe_raises_rather_than_reporting_unavailable(
    store,
):
    """**A permanent caller bug must not wear an infrastructure label.**

    Postgres rejects a comparison between a 1024-d column and a 3-d probe;
    `except Exception` caught it and the caller read `unavailable` — so a bug
    that will never fix itself was retried forever and pointed an operator at
    a healthy database. R-27's three states are about the world, not about us.
    """
    emb, _engine, _metrics = store

    with pytest.raises(ValueError, match="dimensions"):
        await emb.nearest(
            kind="fact", scope=WS, model="bge-m3", probe=(0.1, 0.2, 0.3),
            limit=8, floor=0.5,
        )
