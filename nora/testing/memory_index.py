"""`FakeMemoryIndex`: the memory index's read in memory, for the walk's unit
tests (`nora.agent.memory_search`). It answers through
`nora.domain.memory_index.in_scope` and `narrowed`, the Python statement of the filter
`SqlMemoryIndex` writes in SQL, and `tests/contract/test_memory_index_contract.py`
runs both against the same graph, so the fake cannot drift from the store.

Lexical matching is containment only (score 1 per term held, averaged over
the terms): pg_trgm's word similarity has no faithful Python twin, and a
fake that guessed at it would answer differently from the store on exactly
the near-miss cases. The contract suite uses whole words, where the two
agree.

Vectors: `vectors` maps a node's key to `(model, vector)`. The dense
read is an exact cosine search, which the store's two-stage search equals on
any set no larger than its candidates; given-up nodes (`given_up`) are what
the store counts at `EMBED_MAX_ATTEMPTS`, and `seed(unembedded_of=...)`
reads them with every other embeddable node holding no vector of that
model. The fake has no clock: `fresh` are the waiting nodes that have owed
their vector for less than any positive `fresh_seconds` window (counted
only with a text, as the store counts them), every other waiting node has
waited longer. The fake has no `empty` failures (an `IndexedNode` carries no
`embed_error`). `bag_of_words` is a toy embedding
(one hashed axis per word) under which cosine tracks shared words, so a
test can say «near» and «far» with texts; `FakeQueryEmbedder` embeds a
question with it, the way `LlamaEmbedder.embed_query` does with the model.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence

from nora.domain.memory_index import (
    ATTACHMENT_KINDS,
    EMBEDDED_KINDS,
    HUB_KINDS,
    PERSON_RELATIONS,
    ExactTerm,
    IndexedNode,
    IndexState,
    MemoryScope,
    Neighbour,
    Seed,
    exact_holds,
    in_scope,
    narrowed,
)

__all__ = ["DIMENSIONS", "FakeMemoryIndex", "FakeQueryEmbedder", "bag_of_words", "cosine"]

Edge = tuple[str, str, str, str, str]

#: The production width (`nora.infra.embedder.DIMENSIONS`).
DIMENSIONS = 2048

_TOKEN = re.compile(r"\w+")


def bag_of_words(text: str) -> list[float]:
    """A unit vector with one hashed axis per distinct word of `text`
    (lower-cased); the empty text gets an axis of its own. Two texts' cosine
    is their shared words over the root of the product of their counts."""
    vec = [0.0] * DIMENSIONS
    words = set(_TOKEN.findall(text.lower())) or {"\x00"}
    for word in words:
        vec[int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % DIMENSIONS] += 1
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class FakeQueryEmbedder:
    """`nora.domain.memory_index.QueryEmbedder`: `vectors[text]` when given,
    else `bag_of_words(text)`; raises `fail` when set (the embedder down).
    Records every text it was asked to embed."""

    def __init__(
        self, vectors: Mapping[str, Sequence[float]] | None = None, *,
        fail: BaseException | None = None,
    ) -> None:
        self.vectors = dict(vectors or {})
        self.fail = fail
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail is not None:
            raise self.fail
        vec = list(self.vectors.get(text) or bag_of_words(text))
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec]


class FakeMemoryIndex:
    def __init__(
        self, nodes: Iterable[IndexedNode] = (), edges: Iterable[Edge] = (), *,
        state: IndexState | None = None, fail: BaseException | None = None,
        vectors: Mapping[tuple[str, str], tuple[str, Sequence[float]]] | None = None,
        embed_model: str | None = None, given_up: Iterable[tuple[str, str]] = (),
        fresh: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.nodes: dict[tuple[str, str], IndexedNode] = {n.key: n for n in nodes}
        #: `(src_kind, src_id, rel, dst_kind, dst_id)`; no end need exist.
        self.edges: list[Edge] = list(dict.fromkeys(edges))
        self.state = state or IndexState("complete", 0)
        #: key -> (model fingerprint, vector). `embed_model` embeds every
        #: embeddable node not in `vectors` with `bag_of_words`, as the
        #: worker would have under that model.
        self.vectors: dict[tuple[str, str], tuple[str, list[float]]] = {
            k: (m, list(v)) for k, (m, v) in (vectors or {}).items()
        }
        if embed_model is not None:
            for node in self.nodes.values():
                if node.key not in self.vectors and _embeddable(node):
                    self.vectors[node.key] = (embed_model, bag_of_words(node.text))
        self.given_up = set(given_up)
        self.fresh = set(fresh)
        #: Raised by every read when set: the store being down.
        self.fail = fail
        self.calls: list[str] = []

    def _admits(self, node: IndexedNode, scope: MemoryScope) -> bool:
        """The scope and the question's narrowing (`scope.only`), as
        `SqlMemoryIndex` applies both inside every read."""
        if not in_scope(node, scope):
            return False
        linked = {
            di for sk, si, rel, dk, di in self.edges
            if (sk, si) == node.key and rel in PERSON_RELATIONS and dk == "person"
        }
        return narrowed(node, scope, linked)

    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    async def seed(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
        unembedded_of: str | None = None,
    ) -> list[Seed]:
        self._check("seed")
        wanted = [t for t in dict.fromkeys(t.strip() for t in terms) if t]
        if not wanted or limit <= 0:
            return []
        out: list[Seed] = []
        for node in self.nodes.values():
            if unembedded_of is not None and not (
                _embeddable(node)
                and self.vectors.get(node.key, ("", []))[0] != unembedded_of
            ):
                continue
            held = sum(1 for t in wanted if t.lower() in node.text.lower())
            if held and self._admits(node, scope):
                out.append(Seed(node, held / len(wanted)))
        out.sort(key=lambda s: (-s.score, -(s.node.at_ms or 0), s.node.key))
        return out[:limit]

    async def dense(
        self, vector: Sequence[float], scope: MemoryScope, *, model: str, limit: int,
    ) -> list[Seed]:
        self._check("dense")
        out = [
            Seed(self.nodes[k], cosine(vector, v))
            for k, (m, v) in self.vectors.items()
            if m == model and k in self.nodes and self._admits(self.nodes[k], scope)
        ]
        out.sort(key=lambda s: (-s.score, -(s.node.at_ms or 0), s.node.key))
        return out[:max(0, limit)]

    async def exact(
        self, terms: Sequence[ExactTerm], scope: MemoryScope, *, limit: int,
        vector: Sequence[float] | None = None, model: str | None = None,
    ) -> list[Seed]:
        self._check("exact")
        wanted = [t for t in dict.fromkeys(
            ExactTerm(t.text.strip(), t.cased) for t in terms) if t.text]
        if not wanted or limit <= 0:
            return []
        ranked: list[tuple[int, float | None, IndexedNode]] = []
        for node in self.nodes.values():
            held = sum(1 for t in wanted if exact_holds(t.text, node.text, cased=t.cased))
            if not held or not self._admits(node, scope):
                continue
            stored = self.vectors.get(node.key)
            near = (
                cosine(vector, stored[1])
                if vector is not None and stored is not None and stored[0] == model
                else None
            )
            ranked.append((held, near, node))
        ranked.sort(key=lambda r: (
            -r[0], -(r[1] if r[1] is not None else -2.0), -(r[2].at_ms or 0), r[2].key))
        return [Seed(node, 1.0, near) for _, near, node in ranked[:limit]]

    async def about(
        self, person_id: str, scope: MemoryScope, *, limit: int,
    ) -> list[IndexedNode]:
        self._check("about")
        found = {
            (sk, si) for sk, si, rel, dk, di in self.edges
            if rel == "about" and dk == "person" and di == person_id
        }
        out = [
            self.nodes[k] for k in found
            if k in self.nodes and self._admits(self.nodes[k], scope)
        ]
        out.sort(key=lambda n: (-(n.at_ms or 0), n.key))
        return out[:max(0, limit)]

    async def neighbours(
        self, frontier: Sequence[tuple[str, str]], scope: MemoryScope,
    ) -> list[Neighbour]:
        self._check("neighbours")
        wanted = set(frontier)
        out: list[Neighbour] = []
        for sk, si, rel, dk, di in self.edges:
            for outgoing, near, far in ((True, (sk, si), (dk, di)), (False, (dk, di), (sk, si))):
                node = self.nodes.get(far)
                if near not in wanted or node is None:
                    continue
                if node.kind in HUB_KINDS or self._admits(node, scope):
                    out.append(Neighbour(via=near, rel=rel, outgoing=outgoing, node=node))
        return out

    async def index_state(
        self, *, model: str | None = None, scope: MemoryScope | None = None,
        fresh_seconds: float = 0.0,
    ) -> IndexState:
        self._check("index_state")
        if model is None:
            return IndexState(self.state.status, self.state.queue)
        nodes = [
            n for n in self.nodes.values()
            if _embeddable(n) and (scope is None or self._admits(n, scope))
        ]
        current = {n.key for n in nodes if self.vectors.get(n.key, ("", []))[0] == model}
        given_up = {n.key for n in nodes if n.key in self.given_up} - current
        waiting = {n.key for n in nodes} - current - given_up
        # Fresh only where the lexical seed can read it: a text.
        fresh = {
            k for k in waiting & self.fresh if self.nodes[k].text
        } if fresh_seconds > 0 else set()
        return IndexState(
            self.state.status, self.state.queue, embedded=len(current),
            unembedded=len(waiting), given_up=len(given_up), fresh=len(fresh),
        )


def _embeddable(node: IndexedNode) -> bool:
    """`memory_index._embeddable`'s rule: live, an embedded kind, and a text
    (or an attachment, embedded from its bytes)."""
    return node.state == "live" and node.kind in EMBEDDED_KINDS and (
        bool(node.text) or node.kind in ATTACHMENT_KINDS)
