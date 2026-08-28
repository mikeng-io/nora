"""A claim goes in and comes back out of the retrieval verb — the whole tier,
against real Postgres and real pgvector.

**Every other test in this tier is about one seam.** This one is about the
JOIN between them, and it exists because that is where a tier is wired end to
end and silently inert: each part passes its own suite, the counters look
healthy, and nothing ever travels the whole distance. `24-embedding-seam.md`'s
own catalogue of blindness is a list of exactly that failure.

The embedding endpoint is a STUB and that is the design's own shape (`23` §9:
a port with a fake, so a service that is down reports `unavailable` rather
than raising). It returns a deterministic vector per text, which is what makes
this a test of the PIPELINE — write, render, fingerprint, embed, store, rank,
re-ground — rather than of a model's recall, which no unit test can measure.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.ports import Proposal
from nora.infra.sql.embeddings import SqlEmbeddingStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.entities import SqlEntityStore
from nora.infra.sql.facts import SqlFactStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS
from nora.language.fact_embedder import FactEmbedder
from nora.language.knowledge_search import KnowledgeSearch
from nora.obs.metrics import Metrics

WS = "ws-main"
NOW = 1_755_200_000_000


@dataclass
class _EmbedResult:
    vectors: tuple
    status: str


class _StubEndpoint:
    """A bag-of-tokens vector: one dimension per distinct word, set to 1.0.

    **Deliberately not one axis per TEXT**, which is what the first version
    did — under that scheme every distinct string is orthogonal, so a QUERY
    never matches a DOCUMENT and the dense channel silently contributes
    nothing to any test. Every assertion still passed, carried by the lexical
    channel alone, which is the fake-answers-everything-the-same failure
    wearing its subtlest shape: the fake was not uniform, it was uniformly
    UNHELPFUL on exactly the axis under test.

    Sharing a dimension per word gives real cosine structure: «who made big
    walk» overlaps `big walk │ made_by │ House House` on two dimensions and
    `the wedding │ of │ Grant` on none. That is enough to rank, and it is
    all a pipeline test needs — recall quality is the model's and no unit
    test can measure it.
    """

    def __init__(self) -> None:
        self._axis: dict[str, int] = {}
        self.calls = 0

    @property
    def model(self) -> str:
        return "stub-embedder"

    def _vector(self, text: str) -> tuple[float, ...]:
        v = [0.0] * EMBEDDING_DIMENSIONS
        for token in text.replace("│", " ").split():
            axis = self._axis.setdefault(token.casefold(), len(self._axis))
            v[axis % EMBEDDING_DIMENSIONS] = 1.0
        if not any(v):
            v[0] = 1.0
        return tuple(v)

    async def embed(self, texts):
        self.calls += 1
        return _EmbedResult(
            vectors=tuple(self._vector(t) for t in texts), status="ok",
        )


def _verdict(subject, predicate, obj):
    from nora.domain.facts import FactVerdict
    return FactVerdict(
        subject=subject, predicate=predicate, object=obj, status="supported",
        addresses_claim=True, source_url="https://x.test/a",
        source_excerpt="x", supersedes=None,
    )


def _proposal(v):
    return Proposal(
        payload=v, instruction_sha256="a" * 64, model="m",
        established_by="tick-1",
    )


async def test_a_claim_written_today_is_findable_by_the_verb(pg_url):
    """**The tier's whole point, in one path.**

    Write two facts, sweep, then ask a question in the room's own words. The
    lexical channel has to reach the right triple through `embedding.text`,
    the dense channel through the vector the sweep wrote, and the fusion has
    to put the answer first.

    The unrelated fact is what makes this an assertion rather than a
    tautology: a store returning everything would satisfy «the answer is in
    the list» and fail here.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    metrics = Metrics()
    try:
        entities = SqlEntityStore(engine, metrics=metrics)
        facts = SqlFactStore(engine, entities=entities, metrics=metrics)
        embeddings = SqlEmbeddingStore(engine, metrics=metrics)
        endpoint = _StubEndpoint()

        await facts.propose(
            key=WS,
            proposals=[
                _proposal(_verdict("big walk", "made_by", "House House")),
                _proposal(_verdict("the wedding", "of", "Grant")),
            ],
            now_ms=NOW,
        )

        sweep = await FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=endpoint,
            metrics=metrics,
        ).sweep(workspace=WS, now_ms=NOW)
        assert sweep.status == "ok"
        assert sweep.embedded == 2, "both facts must reach the corpus"

        result = await KnowledgeSearch(
            channels=embeddings, embedder=endpoint, grounding=facts,
            metrics=metrics,
        ).search(scope=WS, query="who made big walk", limit=8)
    finally:
        await engine.dispose()

    assert result.status == "ok"
    assert result.hits, "the claim the room made must come back"
    assert result.hits[0].text == "big walk │ made_by │ House House"
    assert "the wedding │ of │ Grant" not in [h.text for h in result.hits], (
        "an unrelated claim must not be returned; a store that answered with "
        "everything would pass a weaker assertion"
    )
    assert result.hits[0].disputed is False


async def test_a_superseded_claim_stops_being_findable(pg_url):
    """The retraction path, end to end. The embedding is not deleted — nothing
    calls `forget` — so this is the re-grounding read doing its job: the
    corpus still holds the row and the verb refuses to answer with it.

    Without it, a belief the tier has WITHDRAWN comes back through a verb
    whose own description promises everything it returns was checked.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        entities = SqlEntityStore(engine)
        facts = SqlFactStore(engine, entities=entities)
        embeddings = SqlEmbeddingStore(engine)
        endpoint = _StubEndpoint()

        ids = await facts.propose(
            key=WS,
            proposals=[_proposal(_verdict("big walk", "made_by", "House House"))],
            now_ms=NOW,
        )
        await FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=endpoint,
        ).sweep(workspace=WS, now_ms=NOW)

        search = KnowledgeSearch(
            channels=embeddings, embedder=endpoint, grounding=facts,
        )
        before = await search.search(
            scope=WS, query="who made big walk", limit=8)
        assert before.status == "ok", "the control: it was findable first"

        replacement = await facts.propose(
            key=WS,
            proposals=[_proposal(_verdict("big walk", "made_by", "somebody else"))],
            now_ms=NOW + 1,
        )
        await facts.supersede(
            key=WS, target=ids[0], replaced_by=replacement[0],
            because="wrong", source=None, now_ms=NOW + 1,
        )

        after = await search.search(
            scope=WS, query="who made big walk", limit=8)
    finally:
        await engine.dispose()

    assert "House House" not in " ".join(h.text for h in after.hits), (
        "a retracted belief must not come back, even though its embedding is "
        "still in the corpus"
    )


async def test_a_disputed_claim_comes_back_marked(pg_url):
    """R-79 through the whole tier. The mark lives on `fact` and the corpus
    holds only the words, so the verb has to re-ground to carry it — and a
    mark nothing reads is worse than no mark."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        entities = SqlEntityStore(engine)
        facts = SqlFactStore(engine, entities=entities)
        embeddings = SqlEmbeddingStore(engine)
        endpoint = _StubEndpoint()

        ids = await facts.propose(
            key=WS,
            proposals=[_proposal(_verdict("big walk", "means", "a walk"))],
            now_ms=NOW,
        )
        await FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=endpoint,
        ).sweep(workspace=WS, now_ms=NOW)
        await facts.mark_disputed(
            workspace=WS, fact_id=ids[0], person_id="grant",
            reason="it's a video game", tag_id="tag-9", now_ms=NOW + 1,
        )

        result = await KnowledgeSearch(
            channels=embeddings, embedder=endpoint, grounding=facts,
        ).search(scope=WS, query="big walk", limit=8)
    finally:
        await engine.dispose()

    assert [h.text for h in result.hits] == ["big walk │ means │ a walk"]
    assert result.hits[0].disputed is True, (
        "the claim still renders — marked, never muted — because dropping it "
        "would let one objection delete something true for everyone"
    )


async def test_a_second_sweep_over_unchanged_facts_costs_no_model_call(pg_url):
    """The steady state, which is what makes the sweep affordable on a clock:
    a workspace where nothing changed costs one indexed read and no call at
    all. The fingerprint predicate is what buys that, and it is the reason
    pass 4 can run on every tick rather than only on one that wrote."""
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        facts = SqlFactStore(engine, entities=SqlEntityStore(engine))
        embeddings = SqlEmbeddingStore(engine)
        endpoint = _StubEndpoint()
        sweeper = FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=endpoint,
        )
        await facts.propose(
            key=WS,
            proposals=[_proposal(_verdict("big walk", "means", "a walk"))],
            now_ms=NOW,
        )

        first = await sweeper.sweep(workspace=WS, now_ms=NOW)
        after_first = endpoint.calls
        second = await sweeper.sweep(workspace=WS, now_ms=NOW + 1)
    finally:
        await engine.dispose()

    assert first.status == "ok"
    assert second.status == "nothing_found"
    assert endpoint.calls == after_first, (
        "a corpus that is already current must cost no embedding call"
    )


async def test_a_model_swap_re_embeds_and_retrieval_follows_it(pg_url):
    """**R-80 across the whole tier**, which is where it can actually be
    observed: `nearest` filters on the model, so between a swap and the sweep
    that follows it the corpus is invisible to retrieval — and after the
    sweep it is visible again, under the new model.

    Neither half is checkable from one seam. The unit suite pins the
    fingerprint arm (`test_a_model_swap_makes_every_row_stale`) and the store
    suite pins the filter; only this path shows that the two compose into
    «recall shrinks, then recovers» rather than «recall shrinks».

    The stub returns the same vector for the same text under either model, so
    a filter that ignored the model name would have the DENSE channel find the
    row throughout — which is exactly what this distinguishes, since the
    assertion is on which channels contributed rather than on whether anything
    came back.
    """
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        facts = SqlFactStore(engine, entities=SqlEntityStore(engine))
        embeddings = SqlEmbeddingStore(engine)
        old_endpoint = _StubEndpoint()
        await facts.propose(
            key=WS,
            proposals=[_proposal(_verdict("big walk", "means", "a walk"))],
            now_ms=NOW,
        )
        await FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=old_endpoint,
        ).sweep(workspace=WS, now_ms=NOW)

        class _NewModel(_StubEndpoint):
            @property
            def model(self) -> str:
                return "stub-embedder-v2"

        new_endpoint = _NewModel()
        search = KnowledgeSearch(
            channels=embeddings, embedder=new_endpoint, grounding=facts,
        )

        during = await search.search(scope=WS, query="big walk", limit=8)

        swept = await FactEmbedder(
            facts=facts, embeddings=embeddings, embedder=new_endpoint,
        ).sweep(workspace=WS, now_ms=NOW + 1)
        after = await search.search(scope=WS, query="big walk", limit=8)
    finally:
        await engine.dispose()

    # Mid-swap the row is still found — by the LEXICAL channel, which matches
    # on text and has no geometry to be wrong about. That is the correct
    # behaviour and it is sharper than "invisible": what the model filter
    # protects is the DENSE ranking, so the assertion is on which channel
    # contributed, not on whether anything came back. My first version of this
    # test asserted `nothing_found` and was wrong about the code rather than
    # the other way round.
    assert [h.channels for h in during.hits] == [("lexical",)], (
        "mid-swap the row carries the OLD model's geometry, so the dense "
        "channel must not rank it — while the lexical channel legitimately "
        "still finds it"
    )
    assert swept.embedded == 1, "the swap must make the row stale"
    assert [h.text for h in after.hits] == ["big walk │ means │ a walk"]
    assert after.hits[0].channels == ("dense", "lexical"), (
        "and the dense channel must come back once the sweep has caught up"
    )
