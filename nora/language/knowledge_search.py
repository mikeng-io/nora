"""Pass 5 — the retrieval verb (`23-retrieval.md`).

**Two surfaces, and conflating them is the trap** (23 §1). The BLOCK runs every
turn and costs no model and no embedding; the VERB runs when Nora reaches for
it and pays for three channels and a fusion. This module is the verb. Nothing
here may be moved onto the turn's own path — 23 §2 gives three reasons and the
third is the one that bites: ordinary chatter is the majority of messages and
the minority of questions, so per-turn retrieval pays most where it returns
least.

**Three channels, fused by reciprocal rank, and the fusion is arithmetic**
(23 §5). "Reranker" names three things and only one is a model; this uses the
two that are not. There is no cross-encoder, and the reason is document length:
a triple is six tokens, so there is very little for joint attention to exploit
that lexical and dense recall did not already surface, and the cost would be N
forward passes inside a call on the reply path.

**RRF, not score addition**, and that is not a style choice. The channels
return incomparable numbers — a cosine distance and a trigram similarity live
on different scales with different distributions — so adding or averaging them
lets whichever channel happens to have the wider range dominate. Rank is the
only thing they share.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

log = logging.getLogger(__name__)

#: `23` §9's fusion constant, and it is the standard value the prior art uses.
#: The SHAPE is what transfers from that prior art, never the tuning.
RRF_K = 60

#: `23` §9's per-channel candidate cap, before fusion. Enough that fusion has
#: something to fuse; small enough that an expansion off it stays bounded.
TOP_K_PER_CHANNEL = 20

#: `23` §9. A maximum cosine DISTANCE, named for the direction it works in.
DENSE_DISTANCE_FLOOR = 0.5

#: `23` §9, and measured in `SqlEmbeddingStore.lexical`'s own docstring.
LEXICAL_THRESHOLD = 0.3

SearchStatus = Literal["ok", "nothing_found", "unavailable"]
SEARCH_STATUSES: frozenset[str] = frozenset(get_args(SearchStatus))

#: The channels, DERIVED here and never hand-copied at a call site: the metric's
#: label vocabulary is `get_args` of this.
Channel = Literal["lexical", "dense"]
CHANNELS: tuple[str, ...] = get_args(Channel)


@dataclass(frozen=True)
class Hit:
    """One retrieved row, with the channels that found it.

    `channels` is carried rather than collapsed to a score because it is the
    only thing that makes a result explainable: «both channels agree» and «the
    dense channel alone reached this» are different confidences, and an
    operator reading a trace needs to tell them apart.
    """

    row_key: str
    text: str
    score: float
    channels: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    """R-27 on the verb. `unavailable` is «I could not look», which is what a
    caller must not report as «there is nothing about that»."""

    hits: tuple[Hit, ...]
    status: SearchStatus


class _Channels(Protocol):
    async def nearest(
        self, *, kind: str, scope: str, model: str, probe: tuple[float, ...],
        limit: int, floor: float,
    ) -> Any: ...

    async def lexical(
        self, *, kind: str, scope: str, query: str, limit: int,
        threshold: float,
    ) -> Any: ...


class _Embedder(Protocol):
    @property
    def model(self) -> str: ...

    async def embed(self, texts: tuple[str, ...]) -> Any: ...


def fuse(
    ranked: dict[str, tuple[str, ...]], *, k: int = RRF_K,
) -> list[tuple[str, float, tuple[str, ...]]]:
    """Reciprocal rank fusion over `{channel: (row_key, ...) in rank order}`.

    `1 / (k + rank)` summed across the channels that returned a row, with rank
    starting at 1. Returns `(row_key, score, channels)` best first.

    **`k` damps the top of every list**, which is the whole point of the
    constant: without it a rank-1 hit scores 1.0 and a rank-2 hit 0.5, so one
    channel's first result would outweigh two channels agreeing on their
    seconds. At 60 those become 0.0164 and 0.0161, and agreement wins — which
    is the behaviour a two-channel fusion is FOR.

    **Ties are broken on `row_key`**, deterministically. Two rows found at the
    same rank by the same channels score identically, and that is the ordinary
    case in a small corpus, not the edge — the same property the tie-break work
    established for every other ordering in this tier that feeds a limit.
    """
    scores: dict[str, float] = {}
    found: dict[str, list[str]] = {}
    for channel in sorted(ranked):
        for rank, row_key in enumerate(ranked[channel], start=1):
            scores[row_key] = scores.get(row_key, 0.0) + 1.0 / (k + rank)
            found.setdefault(row_key, []).append(channel)
    return [
        (row_key, scores[row_key], tuple(found[row_key]))
        for row_key in sorted(scores, key=lambda r: (-scores[r], r))
    ]


class KnowledgeSearch:
    """`KnowledgeSearch(channels=…, embedder=…)` — collaborators through
    `__init__` only."""

    def __init__(
        self,
        *,
        channels: _Channels,
        embedder: _Embedder | None = None,
        kind: str = "fact",
        top_k: int = TOP_K_PER_CHANNEL,
        dense_floor: float = DENSE_DISTANCE_FLOOR,
        lexical_threshold: float = LEXICAL_THRESHOLD,
        metrics: Any = None,
    ) -> None:
        """`embedder` is OPTIONAL, and its absence is a real deployment rather
        than a broken one: with no endpoint configured the verb runs on the
        lexical channel alone. That degrades RECALL — cross-lingual and
        paraphrased questions stop being reachable, which is measured in
        `SqlEmbeddingStore.lexical` — and it is counted, because a tier
        answering fewer questions than it could must not do so silently.
        """
        self._channels = channels
        self._embedder = embedder
        self._kind = kind
        self._top_k = top_k
        self._dense_floor = dense_floor
        self._lexical_threshold = lexical_threshold
        self._metrics = metrics

    async def search(
        self, *, scope: str, query: str, limit: int,
    ) -> SearchResult:
        """The verb. Never raises: it is reached from a tool call inside a
        turn, and a retrieval fault must cost the answer rather than the reply.
        """
        try:
            return await self._search(scope=scope, query=query, limit=limit)
        except Exception:
            log.exception("knowledge search failed (scope=%s)", scope)
            self._count("unavailable")
            return SearchResult(hits=(), status="unavailable")

    async def _search(
        self, *, scope: str, query: str, limit: int,
    ) -> SearchResult:
        if not query.strip() or limit <= 0:
            self._count("nothing_found")
            return SearchResult(hits=(), status="nothing_found")

        ranked: dict[str, tuple[str, ...]] = {}
        texts: dict[str, str] = {}
        statuses: list[str] = []

        lex = await self._channels.lexical(
            kind=self._kind, scope=scope, query=query, limit=self._top_k,
            threshold=self._lexical_threshold,
        )
        statuses.append(lex.status)
        self._count_channel("lexical", lex.status)
        if lex.hits:
            ranked["lexical"] = tuple(m.row_key for m in lex.hits)
            texts.update({m.row_key: m.text for m in lex.hits})

        dense_status = await self._dense(
            scope=scope, query=query, ranked=ranked, texts=texts,
        )
        statuses.append(dense_status)

        if not ranked:
            # BOTH channels must have been able to look before this is
            # «there is nothing about that». One channel unavailable and the
            # other empty is «I could not check», because the one that failed
            # is exactly the one that answers what the other cannot.
            status: SearchStatus = (
                "unavailable" if "unavailable" in statuses else "nothing_found"
            )
            self._count(status)
            return SearchResult(hits=(), status=status)

        hits = tuple(
            Hit(row_key=row_key, text=texts[row_key], score=score,
                channels=channels)
            for row_key, score, channels in fuse(ranked)[:limit]
        )
        self._count("ok")
        return SearchResult(hits=hits, status="ok")

    async def _dense(
        self, *, scope: str, query: str, ranked: dict[str, tuple[str, ...]],
        texts: dict[str, str],
    ) -> str:
        """The dense channel, and every way it can decline to run.

        Returns the channel's R-27 status. `unavailable` when the endpoint is
        configured and could not be reached — that is a channel that should
        have answered and did not, and it is the difference between «no
        cross-lingual match exists» and «I could not look for one».
        """
        if self._embedder is None:
            self._count_channel("dense", "unavailable")
            return "unavailable"
        result = await self._embedder.embed((query,))
        if result.status != "ok" or not result.vectors:
            self._count_channel("dense", "unavailable")
            return "unavailable"
        read = await self._channels.nearest(
            kind=self._kind, scope=scope, model=self._embedder.model,
            probe=tuple(result.vectors[0]), limit=self._top_k,
            floor=self._dense_floor,
        )
        if read.hits:
            ranked["dense"] = tuple(m.row_key for m in read.hits)
            texts.update({m.row_key: m.text for m in read.hits})
        self._count_channel("dense", read.status)
        return str(read.status)

    def _count(self, status: SearchStatus) -> None:
        # RUNTIME check as well as mypy's: `nora.language` is outside the
        # gate's mypy scope, so a static-only guard here would be a claim.
        if status not in SEARCH_STATUSES:
            raise ValueError(
                f"search status must be one of {sorted(SEARCH_STATUSES)}, "
                f"got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_knowledge_search_total", status=status)

    def _count_channel(self, channel: str, status: str) -> None:
        if channel not in CHANNELS:
            raise ValueError(
                f"channel must be one of {sorted(CHANNELS)}, got {channel!r}"
            )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_knowledge_channel_total", channel=channel, status=status,
            )
