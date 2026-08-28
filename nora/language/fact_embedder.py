"""Pass 4 — embedding, and the first thing in this tier whose failure mode is
rows that are WRONG rather than rows that are absent.

`25-knowledge-pipeline.md` §5. One sweep per tick over a workspace's standing
facts: render each as `subject │ predicate │ object`, fingerprint it, and
re-embed the ones whose stored fingerprint disagrees. One comparison catches a
model swap and a changed row together, with no second mechanism and no ordering
trap.

**Three properties this module exists to hold**, and each is a way the pass
could look like it worked while corrupting the store:

1. **A batch is written whole or not at all.** `OllamaEmbedder` already refuses
   a short or mis-shaped reply as `unavailable` rather than a partial success —
   writing three vectors for four triples leaves the fourth looking
   un-embeddable forever. This module keeps that: it writes only what came back
   matched to what it asked for, and a failed batch stops the sweep rather than
   skipping to the next.
2. **A written row's fingerprint always describes its own vector.** The
   fingerprint, the text and the vector go into one row in one statement, so a
   crash between batches leaves earlier rows correct and later rows untouched.
   Partial PROGRESS is fine and expected; partial rows are not.
3. **The sweep never reports a clean pass it did not make.** A database that
   could not be read and a workspace with nothing stale are different outcomes,
   and only one of them means the tier is up to date.

**Ordering is load-bearing: 3 before 4, always** — `25` §5 argues it from
promotion rewriting the triple, which migration 0039 made untrue (see
`nora.infra.embedding.fingerprint`'s own note). It still holds for the reason
that survives: a tick that promotes and then embeds spends one pass, and one
that embeds then promotes leaves rows to re-embed next tick.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

from nora.domain.facts import Fact
from nora.infra.embedding import fingerprint, render_for_embedding
from nora.infra.sql.embeddings import StoredEmbedding

log = logging.getLogger(__name__)

#: The kind this pass writes under. The seam is shared (24 §1); this is the
#: tier's key into it, and it is a constant rather than a literal at each call
#: site because a typo in one of them would write a second, invisible corpus.
FACT_KIND = "fact"

#: R-27 on the sweep as a whole.
SweepStatus = Literal["ok", "nothing_found", "unavailable"]
SWEEP_STATUSES: frozenset[str] = frozenset(get_args(SweepStatus))


@dataclass(frozen=True)
class Sweep:
    """What one pass did. `embedded` counts rows WRITTEN, never rows offered.

    `status` is the sweep's own R-27: `ok` means at least one row was
    re-embedded, `nothing_found` means everything in the batch was already
    current — a real and eventually usual outcome — and `unavailable` means
    some step could not be made, so nothing about the tier's freshness may be
    concluded from this run.
    """

    embedded: int
    stale: int
    status: SweepStatus


class _Embedder(Protocol):
    @property
    def model(self) -> str: ...

    async def embed(self, texts: tuple[str, ...]) -> Any: ...


class _Facts(Protocol):
    async def embeddable(
        self, *, workspace: str, limit: int,
    ) -> tuple[tuple[Fact, ...], str]: ...


class _Embeddings(Protocol):
    async def existing(
        self, *, kind: str, scope: str, row_keys: tuple[str, ...],
    ) -> Any: ...

    async def upsert(
        self, *, rows: tuple[tuple[StoredEmbedding, tuple[float, ...]], ...],
    ) -> int: ...


class FactEmbedder:
    """`FactEmbedder(facts=…, embeddings=…, embedder=…)` — collaborators
    through `__init__` only, the repo's test-seam policy."""

    def __init__(
        self,
        *,
        facts: _Facts,
        embeddings: _Embeddings,
        embedder: _Embedder,
        max_rows_per_sweep: int = 200,
        batch_size: int = 32,
        metrics: Any = None,
    ) -> None:
        """`max_rows_per_sweep` bounds what one tick considers; `batch_size`
        bounds one call to the endpoint.

        Two numbers rather than one because they bound different things: the
        first is the tick's cost, the second is the blast radius of a single
        failed call. A sweep of 200 in batches of 32 that dies on the third
        batch has written 64 correct rows and left 136 stale, which is exactly
        the outcome the design wants.
        """
        self._facts = facts
        self._embeddings = embeddings
        self._embedder = embedder
        self._max_rows = max_rows_per_sweep
        self._batch = batch_size
        self._metrics = metrics

    async def sweep(self, *, workspace: str, now_ms: int) -> Sweep:
        """One pass. Never raises — it runs in the Dreamer's tick beside other
        adaptors, and a sweep fault must cost the sweep rather than the tick.

        The three states are the caller's whole signal, so they are computed
        from what actually happened rather than from whether an exception
        escaped.
        """
        try:
            return await self._sweep(workspace=workspace, now_ms=now_ms)
        except Exception:
            log.exception("fact embedding sweep failed: workspace=%s", workspace)
            self._count("unavailable")
            return Sweep(embedded=0, stale=0, status="unavailable")

    async def _sweep(self, *, workspace: str, now_ms: int) -> Sweep:
        facts, read_status = await self._facts.embeddable(
            workspace=workspace, limit=self._max_rows,
        )
        if read_status == "unavailable":
            # The rows could not be read, so nothing is known about what is
            # stale. Reporting `nothing_found` here would say «the tier is up
            # to date» on a database that was down.
            self._count("unavailable")
            return Sweep(embedded=0, stale=0, status="unavailable")
        if not facts:
            self._count("nothing_found")
            return Sweep(embedded=0, stale=0, status="nothing_found")

        model = self._embedder.model
        wanted = {
            f.id: render_for_embedding(
                subject=f.subject_id, predicate=f.predicate,
                obj=f.object_literal,
            )
            for f in facts
        }
        read = await self._embeddings.existing(
            kind=FACT_KIND, scope=workspace,
            row_keys=tuple(wanted),
        )
        if read.status == "unavailable":
            self._count("unavailable")
            return Sweep(embedded=0, stale=0, status="unavailable")
        current = {r.row_key: r for r in read.rows}

        stale: list[tuple[str, str, str]] = []
        for fact_id, text in wanted.items():
            digest = fingerprint(model=model, text=text)
            held = current.get(fact_id)
            # ONE comparison, and it covers three things: no row at all, a row
            # whose text changed, and a row embedded under another model —
            # because the fingerprint is over (model, text). A row present but
            # with a NULL vector is also stale: pass 4 may have recorded the
            # text and failed to reach the endpoint.
            if (
                held is None
                or held.fingerprint != digest
                or not held.has_vector
            ):
                stale.append((fact_id, text, digest))

        if not stale:
            self._count("nothing_found")
            return Sweep(embedded=0, stale=0, status="nothing_found")

        embedded = 0
        for start in range(0, len(stale), self._batch):
            chunk = stale[start:start + self._batch]
            result = await self._embedder.embed(tuple(t for _i, t, _d in chunk))
            if result.status != "ok":
                # STOPS, rather than trying the next batch. The endpoint is
                # one service: a batch failing because it is down means every
                # later batch fails too, and hammering it is the polling
                # anti-pattern with a worse blast radius. What is already
                # written stays written and correct.
                log.warning(
                    "fact embedding stopped after %d rows: the endpoint "
                    "answered %s (workspace=%s)",
                    embedded, result.status, workspace,
                )
                self._count("unavailable")
                return Sweep(
                    embedded=embedded, stale=len(stale), status="unavailable",
                )
            embedded += await self._embeddings.upsert(rows=tuple(
                (
                    StoredEmbedding(
                        kind=FACT_KIND, row_key=fact_id, scope=workspace,
                        text=text, model=model, fingerprint=digest,
                        embedded_at=now_ms, has_vector=True,
                    ),
                    vector,
                )
                for (fact_id, text, digest), vector in zip(
                    chunk, result.vectors, strict=True,
                )
            ))

        self._count("ok")
        return Sweep(embedded=embedded, stale=len(stale), status="ok")

    def _count(self, status: SweepStatus) -> None:
        # RUNTIME check as well as mypy's: `nora.language` is outside the
        # gate's mypy scope, so a static-only guard here would be a claim.
        if status not in SWEEP_STATUSES:
            raise ValueError(
                f"sweep status must be one of {sorted(SWEEP_STATUSES)}, "
                f"got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc("nora_fact_embed_sweep_total", status=status)
