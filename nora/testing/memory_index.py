"""`FakeMemoryIndex`: the memory index's read in memory, for the walk's unit
tests (`nora.agent.memory_search`). It answers through
`nora.domain.memory_index.in_scope`, the Python statement of the filter
`SqlMemoryIndex` writes in SQL, and `tests/contract/test_memory_index_contract.py`
runs both against the same graph, so the fake cannot drift from the store.

Lexical matching is containment only (score 1 per term held, averaged over
the terms): pg_trgm's word similarity has no faithful Python twin, and a
fake that guessed at it would answer differently from the store on exactly
the near-miss cases. The contract suite uses whole words, where the two
agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from nora.domain.memory_index import (
    HUB_KINDS,
    IndexedNode,
    IndexState,
    MemoryScope,
    Neighbour,
    Seed,
    in_scope,
)

__all__ = ["FakeMemoryIndex"]

Edge = tuple[str, str, str, str, str]


class FakeMemoryIndex:
    def __init__(
        self, nodes: Iterable[IndexedNode] = (), edges: Iterable[Edge] = (), *,
        state: IndexState | None = None, fail: BaseException | None = None,
    ) -> None:
        self.nodes: dict[tuple[str, str], IndexedNode] = {n.key: n for n in nodes}
        #: `(src_kind, src_id, rel, dst_kind, dst_id)`; no end need exist.
        self.edges: list[Edge] = list(dict.fromkeys(edges))
        self.state = state or IndexState("complete", 0)
        #: Raised by every read when set: the store being down.
        self.fail = fail
        self.calls: list[str] = []

    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    async def seed(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
    ) -> list[Seed]:
        self._check("seed")
        wanted = [t for t in dict.fromkeys(t.strip() for t in terms) if t]
        if not wanted or limit <= 0:
            return []
        out: list[Seed] = []
        for node in self.nodes.values():
            held = sum(1 for t in wanted if t.lower() in node.text.lower())
            if held and in_scope(node, scope):
                out.append(Seed(node, held / len(wanted)))
        out.sort(key=lambda s: (-s.score, -(s.node.at_ms or 0), s.node.key))
        return out[:limit]

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
            if k in self.nodes and in_scope(self.nodes[k], scope)
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
                if node.kind in HUB_KINDS or in_scope(node, scope):
                    out.append(Neighbour(via=near, rel=rel, outgoing=outgoing, node=node))
        return out

    async def index_state(self) -> IndexState:
        self._check("index_state")
        return self.state
