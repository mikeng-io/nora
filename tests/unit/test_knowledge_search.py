"""Pass 5 — the retrieval verb, and the arithmetic under it.

The fusion is tested as a pure function first, because it is the part with a
provable property: two channels agreeing must beat one channel's first place.
That is what the RRF constant BUYS, and a fusion that got it wrong would still
return plausible lists.

The doubles match production where the verb branches: each channel returns
`(rows, status)` with the three real statuses, and the embedder returns the
three `OllamaEmbedder` really returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.infra.sql.embeddings import ChannelRead
from nora.language.knowledge_search import (
    RRF_K,
    KnowledgeSearch,
    fuse,
)
from nora.obs.metrics import Metrics

WS = "ws-main"


@dataclass(frozen=True)
class _Match:
    row_key: str
    text: str
    distance: float = 0.1
    kind: str = "fact"
    scope: str = WS


@dataclass
class _EmbedResult:
    vectors: tuple
    status: str


class _FakeEmbedder:
    def __init__(self, *, status: str = "ok", model: str = "bge-m3") -> None:
        self._status = status
        self._model = model
        self.calls: list[tuple[str, ...]] = []

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts):
        self.calls.append(texts)
        if self._status != "ok":
            return _EmbedResult(vectors=(), status=self._status)
        return _EmbedResult(vectors=((0.1, 0.2),), status="ok")


class _FakeChannels:
    def __init__(self, *, lexical=(), lexical_status="ok",
                 dense=(), dense_status="ok") -> None:
        self._lexical = tuple(lexical)
        self._lexical_status = lexical_status
        self._dense = tuple(dense)
        self._dense_status = dense_status
        self.dense_calls: list[dict] = []

    async def lexical(self, *, kind, scope, query, limit, threshold):
        return ChannelRead(hits=self._lexical, status=self._lexical_status)

    async def nearest(self, *, kind, scope, model, probe, limit, floor):
        self.dense_calls.append({"model": model, "floor": floor})
        return ChannelRead(hits=self._dense, status=self._dense_status)


# ---------------------------------------------------------------------------
# the fusion — arithmetic with a provable property
# ---------------------------------------------------------------------------


def test_two_channels_agreeing_beat_one_channels_first_place():
    """**This is what the RRF constant buys**, and it is the reason `k` is not
    zero.

    Without `k`, a rank-1 hit scores 1.0 and a rank-2 hit 0.5, so one
    channel's first result outweighs two channels agreeing on their seconds —
    which defeats the entire purpose of running two channels. At 60 those
    become 0.0164 and 0.0161 and agreement wins.
    """
    fused = fuse({
        "lexical": ("alone", "both"),
        "dense": ("other", "both"),
    })

    assert fused[0][0] == "both"
    assert fused[0][2] == ("dense", "lexical")


def test_the_constant_is_what_makes_agreement_win():
    """The falsification of the test above, as a test: with `k = 0` the same
    input ranks the single-channel hit first. Stated so the constant reads as
    load-bearing rather than as a number copied from prior art."""
    naive = fuse(
        {"lexical": ("alone", "both"), "dense": ("other", "both")}, k=0,
    )

    assert naive[0][0] in {"alone", "other"}
    assert RRF_K == 60


def test_a_row_found_by_one_channel_still_ranks():
    """Fusion is a union, not an intersection. Requiring both channels would
    delete the cross-lingual hit the dense channel exists to find, which no
    lexical scheme can reach."""
    fused = fuse({"dense": ("only-dense",)})

    assert [r[0] for r in fused] == ["only-dense"]
    assert fused[0][2] == ("dense",)


def test_fusion_ties_are_broken_deterministically():
    """Two rows at the same rank from the same channels score identically, and
    in a small corpus that is the ordinary case rather than the edge — the
    same property every other ordering in this tier that feeds a limit had to
    establish."""
    # The INSERTION order is deliberately the reverse of the sorted order.
    # Channels are visited in sorted name order, so `dense` first putting "b"
    # ahead of "a" makes the score dict's own order `b, a` — and Python's sort
    # is stable, so a fusion sorting on score alone returns `b, a` while one
    # that also breaks on `row_key` returns `a, b`. Without this the two
    # implementations are indistinguishable and the test proves nothing, which
    # is what the first version of it did.
    first = fuse({"lexical": ("a", "b"), "dense": ("b", "a")})
    again = fuse({"lexical": ("a", "b"), "dense": ("b", "a")})

    assert [r[0] for r in first] == ["a", "b"]
    assert first == again


# ---------------------------------------------------------------------------
# the verb — R-27, and what each channel's silence means
# ---------------------------------------------------------------------------


async def test_both_channels_are_consulted_and_the_result_is_fused():
    channels = _FakeChannels(
        lexical=(_Match("f1", "big walk │ means │ a walk"),),
        dense=(_Match("f2", "大步行 │ made_by │ House House"),
               _Match("f1", "big walk │ means │ a walk")),
    )
    metrics = Metrics()
    search = KnowledgeSearch(
        channels=channels, embedder=_FakeEmbedder(), metrics=metrics,
    )

    result = await search.search(scope=WS, query="big walk", limit=8)

    assert result.status == "ok"
    assert [h.row_key for h in result.hits] == ["f1", "f2"]
    assert result.hits[0].channels == ("dense", "lexical")
    assert metrics.get("nora_knowledge_search_total", status="ok") == 1


async def test_the_dense_probe_is_embedded_with_the_stores_own_model():
    """The model reaches `nearest` from the EMBEDDER rather than from config,
    so the probe and the corpus can never be from different models. A verb
    that passed a configured name would rank a bge-m3 probe against bge-m2
    vectors the moment the two disagreed."""
    # NOT the default model name. The first version of this test used
    # "bge-m3" — which is what a hard-coded literal in the verb would also
    # pass — so it could not tell the two implementations apart.
    channels = _FakeChannels(dense=(_Match("f1", "x"),))
    embedder = _FakeEmbedder(model="some-other-embedder")

    await KnowledgeSearch(channels=channels, embedder=embedder).search(
        scope=WS, query="q", limit=8,
    )

    assert channels.dense_calls[0]["model"] == "some-other-embedder"
    assert embedder.calls == [("q",)]


async def test_nothing_found_needs_both_channels_to_have_looked():
    """**The R-27 case that matters**, and the one a naive implementation gets
    wrong. One channel unavailable and the other empty is «I could not check»,
    because the channel that failed is exactly the one that answers what the
    other cannot — reporting `nothing_found` there tells Nora the room has
    never discussed something she simply could not search for."""
    metrics = Metrics()
    search = KnowledgeSearch(
        channels=_FakeChannels(lexical=(), lexical_status="unavailable"),
        embedder=_FakeEmbedder(), metrics=metrics,
    )

    result = await search.search(scope=WS, query="big walk", limit=8)

    assert result.hits == ()
    assert result.status == "unavailable"
    assert metrics.get(
        "nora_knowledge_search_total", status="unavailable") == 1


async def test_both_channels_empty_is_a_real_nothing_found():
    """The positive control for the test above: a verb that reported
    `unavailable` whenever it found nothing could never say «the room has not
    discussed this», which is a true and useful answer."""
    metrics = Metrics()
    search = KnowledgeSearch(
        channels=_FakeChannels(lexical=(), lexical_status="nothing_found",
                               dense=(), dense_status="nothing_found"),
        embedder=_FakeEmbedder(), metrics=metrics,
    )

    result = await search.search(scope=WS, query="nothing at all", limit=8)

    assert result.status == "nothing_found"
    assert metrics.get(
        "nora_knowledge_search_total", status="nothing_found") == 1


async def test_no_endpoint_degrades_to_lexical_and_is_counted():
    """A deployment with no embedding endpoint is real rather than broken —
    the verb still answers on the lexical channel. But recall drops in a
    specific, measured way: cross-lingual and paraphrased questions stop being
    reachable. That must be COUNTED, or a tier answering fewer questions than
    it could does so silently."""
    metrics = Metrics()
    search = KnowledgeSearch(
        channels=_FakeChannels(lexical=(_Match("f1", "x"),)),
        embedder=None, metrics=metrics,
    )

    result = await search.search(scope=WS, query="big walk", limit=8)

    assert result.status == "ok"
    assert [h.row_key for h in result.hits] == ["f1"]
    assert metrics.get(
        "nora_knowledge_channel_total", channel="dense",
        status="unavailable") == 1


async def test_an_unreachable_endpoint_is_the_same_as_none():
    """A configured endpoint that cannot be reached is a channel that SHOULD
    have answered and did not — the same reading as no endpoint, and both are
    `unavailable` rather than a quiet single-channel search."""
    metrics = Metrics()
    search = KnowledgeSearch(
        channels=_FakeChannels(lexical=(_Match("f1", "x"),)),
        embedder=_FakeEmbedder(status="unavailable"), metrics=metrics,
    )

    await search.search(scope=WS, query="q", limit=8)

    assert metrics.get(
        "nora_knowledge_channel_total", channel="dense",
        status="unavailable") == 1


async def test_an_empty_query_costs_no_channel_call():
    channels = _FakeChannels(lexical=(_Match("f1", "x"),))
    embedder = _FakeEmbedder()

    result = await KnowledgeSearch(
        channels=channels, embedder=embedder,
    ).search(scope=WS, query="   ", limit=8)

    assert result.status == "nothing_found"
    assert embedder.calls == []
    assert channels.dense_calls == []


async def test_the_verb_never_raises_into_the_turn():
    """It is reached from a tool call inside a turn, so a fault must cost the
    answer and not the reply."""
    class _Exploding:
        async def lexical(self, **kw):
            raise RuntimeError("the pool is gone")

        async def nearest(self, **kw):
            raise RuntimeError("the pool is gone")

    metrics = Metrics()
    result = await KnowledgeSearch(
        channels=_Exploding(), embedder=_FakeEmbedder(), metrics=metrics,
    ).search(scope=WS, query="q", limit=8)

    assert result.status == "unavailable"
    assert result.hits == ()


async def test_the_limit_caps_what_comes_back_after_fusion_not_before():
    """Capping each channel before fusion would let one channel's tail crowd
    out the other channel's head. The per-channel cap is `top_k` and it is
    twenty; `limit` applies to the fused list."""
    channels = _FakeChannels(
        lexical=tuple(_Match(f"l{i}", f"t{i}") for i in range(5)),
        dense=tuple(_Match(f"d{i}", f"t{i}") for i in range(5)),
    )

    result = await KnowledgeSearch(
        channels=channels, embedder=_FakeEmbedder(),
    ).search(scope=WS, query="q", limit=3)

    assert len(result.hits) == 3
