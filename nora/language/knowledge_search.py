"""Pass 5 — the retrieval verb (`23-retrieval.md`).

**Two surfaces, and conflating them is the trap** (23 §1). The BLOCK runs every
turn and costs no model and no embedding; the VERB runs when Nora reaches for
it and pays for three channels and a fusion. This module is the verb. Nothing
here may be moved onto the turn's own path — 23 §2 gives three reasons and the
third is the one that bites: ordinary chatter is the majority of messages and
the minority of questions, so per-turn retrieval pays most where it returns
least.

**TWO channels today, fused by reciprocal rank, and the fusion is arithmetic**
(23 §5). The design specifies three — lexical, dense and STRUCTURAL (1-hop and
2-hop expansion from resolved entities, `25` §7) — and the structural one is
not built. It is named here rather than left to a reader to notice: a module
docstring that claims a channel the module does not have is the same
stale-prose failure as a method described in a docstring and absent from the
class.

**Two other pieces of `23` are deliberately absent and recorded as absent.**
§4's SHORT-CIRCUIT — skip the dense pass when the lexical top hit is strong in
absolute terms and clearly ahead of the runner-up — is not implemented, so
every call pays the embedding round trip even when an exact canonical-name hit
already settled it, which §4 calls the common case in this store. And §9's
return carries each edge's warrant, asserter and distance where this returns
text plus a dispute mark. Both are cost and richness rather than correctness,
and both are gaps rather than decisions.

"Reranker" names three things and only one is a model; this uses the
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

#: A channel's own outcome vocabulary, which is R-27's three plus one: a
#: deployment with NO embedding endpoint configured is not a failed lookup, it
#: is an install without that channel. Collapsing it into `unavailable` made
#: such a deployment unable to ever report `nothing_found`, so it could never
#: say a topic was genuinely new.
ChannelStatus = Literal["ok", "nothing_found", "unavailable", "not_configured"]
CHANNEL_STATUSES: frozenset[str] = frozenset(get_args(ChannelStatus))


@dataclass(frozen=True)
class Hit:
    """One retrieved row, with the channels that found it.

    `channels` is carried rather than collapsed to a score because it is the
    only thing that makes a result explainable: «both channels agree» and «the
    dense channel alone reached this» are different confidences, and an
    operator reading a trace needs to tell them apart.

    `disputed` is R-79 on THIS read path. The `<facts>` block renders the mark;
    without it here the second path would drop it silently, on a verb whose own
    description tells the model «everything it returns is something somebody
    here said and I checked». A mark nothing reads is worse than no mark.
    """

    row_key: str
    text: str
    score: float
    channels: tuple[str, ...]
    disputed: bool = False


@dataclass(frozen=True)
class SearchResult:
    """R-27 on the verb. `unavailable` is «I could not look», which is what a
    caller must not report as «there is nothing about that».

    `partial` says a channel could not look WHILE another returned rows. The
    status alone cannot carry that: `ok` with hits is what a full search and a
    half one both look like, so a Cantonese or paraphrased question — the
    dense channel's whole job, measured — would come back with lexical-only
    results labelled `ok`. `unavailable` mattering only when the result is
    empty is the case where it matters least.
    """

    hits: tuple[Hit, ...]
    status: SearchStatus
    partial: bool = False


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


class _Grounding(Protocol):
    """`SqlFactStore.standing_among`, seen from here — the re-grounding read.

    The corpus is a SECOND read path and it does not carry what the first one
    carries: `embedding.text` holds the words and `fact` holds supersession
    and the dispute mark. Without this, a belief the tier RETRACTED comes back
    indistinguishable from a current one, and R-79's mark is dropped silently.
    """

    async def standing_among(
        self, *, workspace: str, fact_ids: tuple[str, ...],
    ) -> tuple[dict[str, bool], str]: ...


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
        grounding: _Grounding | None = None,
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
        self._grounding = grounding
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
        partial = False

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
        # PARTIAL is about a channel that could not look while another found
        # rows. `status` alone cannot carry it — `ok` with hits looks the same
        # for a full search and a half one — and the half that is missing is
        # the one that answers what the other cannot.
        partial = "unavailable" in statuses and bool(ranked)

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

        fused = fuse(ranked)
        standing, ground_status = await self._ground(
            scope=scope, row_keys=tuple(r for r, _s, _c in fused),
        )
        if ground_status == "unavailable":
            # RETURN NOTHING rather than text that may be retracted. The
            # corpus alone cannot tell a standing claim from one the tier has
            # withdrawn, so an unusable grounding read makes every hit
            # unverifiable — and silence beats a wrong answer, especially one
            # the verb's own description promises was checked.
            self._count("unavailable")
            return SearchResult(hits=(), status="unavailable", partial=partial)
        hits = tuple(
            Hit(row_key=row_key, text=texts[row_key], score=score,
                channels=channels, disputed=standing[row_key])
            for row_key, score, channels in fused
            # A key the grounding read did not return is SUPERSEDED or gone.
            # Dropped rather than marked: a retracted belief is not a disputed
            # one, and rendering it at all would answer with something the
            # tier has decided is no longer true.
            if row_key in standing
        )[:limit]
        if not hits:
            # Everything retrieved was retracted. That is a real
            # `nothing_found` — the room discussed it and the tier no longer
            # holds it — and it must not be reported as `ok` with no rows.
            self._count("nothing_found")
            return SearchResult(hits=(), status="nothing_found", partial=partial)
        self._count("ok")
        return SearchResult(hits=hits, status="ok", partial=partial)

    async def _ground(
        self, *, scope: str, row_keys: tuple[str, ...],
    ) -> tuple[dict[str, bool], str]:
        """Re-ground what the corpus returned against the tier's own rows.

        With no grounding collaborator every hit is treated as standing and
        undisputed — which is the pre-existing behaviour and is honest for a
        corpus of another `kind`, since supersession is the fact tier's idea.
        The fact tier always supplies one.
        """
        if self._grounding is None or not row_keys:
            return dict.fromkeys(row_keys, False), "ok"
        standing, status = await self._grounding.standing_among(
            workspace=scope, fact_ids=row_keys,
        )
        return standing, status

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
            # NOT unavailable. A deployment with no endpoint configured is a
            # deployment where the dense channel does not exist — a settled
            # fact about the install, not a failure of this call — and
            # reporting `unavailable` made it impossible for such a
            # deployment to EVER say `nothing_found`, so she could never
            # truthfully tell the room a topic was new. It is still counted,
            # under its own status, because a tier answering fewer questions
            # than it could must not do so silently.
            self._count_channel("dense", "not_configured")
            return "nothing_found"
        try:
            result = await self._embedder.embed((query,))
        except Exception:
            # WRAPPED, because the blanket handler in `search` would discard
            # the lexical hits already in hand: a transient raise cost the
            # whole answer while a REPORTED `unavailable` from the same
            # endpoint degraded gracefully. Same fault, two contracts.
            log.exception("dense channel unreachable (scope=%s)", scope)
            self._count_channel("dense", "unavailable")
            return "unavailable"
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
        # BOTH axes checked. The first version derived the channel vocabulary
        # and let `status` through untouched — and `status` is the label that
        # CROSSES a module boundary, arriving from the store, so a value
        # drifting there mints a phantom series in a metric nobody is reading
        # closely. Half the rule applied is the half that does not protect the
        # exposed side.
        if channel not in CHANNELS:
            raise ValueError(
                f"channel must be one of {sorted(CHANNELS)}, got {channel!r}"
            )
        if status not in CHANNEL_STATUSES:
            raise ValueError(
                f"channel status must be one of {sorted(CHANNEL_STATUSES)}, "
                f"got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_knowledge_channel_total", channel=channel, status=status,
            )
