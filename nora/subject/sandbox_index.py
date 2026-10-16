"""The memory graph surface's index in an eval sandbox, with real vectors.

A sandbox holds rows, never an index: the four `memory_*` tables are
derived and NOT restored (`nora.infra.sql.history.NOT_RESTORED`), and a
snapshot-seeded sandbox starts from the blank template. A graph-surface
subject composed against it would answer every `memory_search` from an
empty or vectorless index, which measures the sandbox, not the surface.
So, after a graph-surface sandbox is
seeded and before the drive (`evals.backbone._drive_draw`, both seeding
paths):

1. **Project** its index with production's own `rebuild`, against the
   sandbox: every node is what live projection would have written for those
   rows.
2. **Adopt** the embedding worker's result from the source, the
   deployment's clone, wherever a node's embedded input is identical
   (`nora.infra.sql.memory_index.adopt_worker_state` says why that equals
   re-embedding it, and so is as-of safe). The worker itself never runs in a
   subject (`nora.subject.obtain` composes none), so nothing here calls the
   live embedder to write a vector, and nothing competes with production's
   backfill.
3. **Read the coverage back, fail-closed.** The share of the nodes the
   worker owes a vector that hold one (`Coverage`, the search's own count:
   a node the worker gave up on, or an `empty` attachment, is owed none,
   `nora.infra.sql.memory_read`). Below `COVERAGE_FLOOR` of the source's
   own coverage, the draw is refused: an ERROR, never graded.

**Why `COVERAGE_FLOOR` is 0.95, twice.** The source must cover at least
0.95 of what it owes (`source_coverage`): below that its backfill is still
running, and every search in a scope holding an owed node answers
`partial`, which measures the backfill. A finished backfill holds all but
the nodes whose embedding is still in flight (production read 0.996 when
this was written). A sandbox must then hold at least 0.95 of the
source's coverage: what it misses beyond the source's own gaps is only the
nodes whose input changed after the instant (edits, later readings) and the
rows a case seeds itself (its lessons), a few percent at most. More
than that is a defect in the copy (a fingerprint that does not match, a
source refreshed from another model, a projector that renders text
differently), and a draw measured on it would read `partial` where
production reads `ok`.

**A node the copy misses is still owed**, so a search whose scope holds one
answers `partial`, exactly as production does for a node the worker has not
reached yet. The new line a turn's case delivers is one: production embeds
it on the next drain tick, after the turn has searched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.embedder import EmbedderUnavailableError, LlamaEmbedder, fingerprint, model_file
from nora.infra.sql import create_readonly_sql_engine, create_sql_engine, memory_index
from nora.infra.sql.memory_index import EmbedCounts

__all__ = [
    "COVERAGE_FLOOR",
    "EMBEDDER_FROM_HOST",
    "Coverage",
    "GraphIndexRefusedError",
    "SandboxIndex",
    "check_served_model",
    "index_sandbox",
    "refuse_low_coverage",
    "source_coverage",
]

#: The `embedder` compose service, as the eval suite on the deploy host
#: reaches it. Nora reaches it as `embedder:8080` on the `embedder` network,
#: which is internal, so neither the name nor the container's address works
#: from the host; compose publishes it on loopback at this port
#: (`docker-compose.yml`, `tests/unit/test_embedder.py` holds the two equal).
EMBEDDER_FROM_HOST = "http://127.0.0.1:8092"

#: See the module docstring.
COVERAGE_FLOOR = 0.95


class GraphIndexRefusedError(RuntimeError):
    """The graph surface cannot be measured on this source or sandbox."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """The live, embeddable nodes of one database, as the search counts them."""

    #: Holding a vector of the configured model.
    embedded: int
    #: Still owed one: not embedded, not given up, not `empty`.
    owed: int
    given_up: int
    empty: int

    @classmethod
    def of(cls, counts: EmbedCounts) -> Coverage:
        return cls(
            embedded=counts.embedded,
            owed=counts.pending - counts.given_up - counts.empty,
            given_up=counts.given_up, empty=counts.empty,
        )

    @property
    def ratio(self) -> float:
        """Of the nodes the worker owes a vector, the share holding one; 1.0
        when it owes none (nothing to search is not a gap)."""
        total = self.embedded + self.owed
        return 1.0 if total == 0 else self.embedded / total

    def line(self) -> str:
        return (
            f"{self.embedded} with a vector, {self.owed} owed "
            f"({self.given_up} given up, {self.empty} empty): coverage {self.ratio:.3f}"
        )


@dataclass(frozen=True, slots=True)
class SandboxIndex:
    """What `index_sandbox` built, for the per-sandbox read-back line."""

    coverage: Coverage
    source: Coverage
    seconds: float

    def line(self) -> str:
        return (
            f"memory index: {self.coverage.line()} "
            f"(source {self.source.ratio:.3f}), built in {self.seconds:.1f}s"
        )


async def _has_vectors(conn: AsyncConnection) -> bool:
    """Whether the database has the index with its dense channel (0106)."""
    return bool((await conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'memory_node' "
        "AND column_name = 'embedding')"
    ))).scalar_one())


async def source_coverage(source_url: str, model_id: str) -> Coverage:
    """The source's coverage for `model_id` (`<gguf>@<sha256 prefix>`), or
    refused: no index (a clone older than migration 0106), no vector of this
    model, or a backfill under `COVERAGE_FLOOR`. Never a lexical run in its
    place."""
    model = fingerprint(model_id)
    engine = create_readonly_sql_engine(source_url)
    try:
        async with engine.connect() as conn:
            if not await _has_vectors(conn):
                raise GraphIndexRefusedError(
                    "the eval source holds no memory index with vectors (it predates "
                    "migration 0106); refresh it with `make eval-source`"
                )
            coverage = Coverage.of(await memory_index.embed_counts(conn, model))
    finally:
        await engine.dispose()
    if coverage.embedded == 0:
        raise GraphIndexRefusedError(
            f"the eval source holds no vector of {model!r}; refresh it with "
            "`make eval-source` once production's backfill has run"
        )
    if coverage.ratio < COVERAGE_FLOOR:
        raise GraphIndexRefusedError(
            f"the eval source's backfill is incomplete ({coverage.line()}, under "
            f"{COVERAGE_FLOOR}); every search would answer partial. Refresh it with "
            "`make eval-source` once production's backfill has finished"
        )
    return coverage


def refuse_low_coverage(sandbox: Coverage, source: Coverage) -> None:
    """Raise unless `sandbox` holds at least `COVERAGE_FLOOR` of `source`'s
    coverage."""
    floor = COVERAGE_FLOOR * source.ratio
    if sandbox.ratio < floor:
        raise GraphIndexRefusedError(
            f"the sandbox's memory index is under its floor ({sandbox.line()}; "
            f"floor {floor:.3f}, {COVERAGE_FLOOR} of the source's {source.ratio:.3f}); "
            "a draw on it would answer partial where production answers ok"
        )


async def index_sandbox(database_url: str, *, source_url: str, model_id: str) -> SandboxIndex:
    """Project the sandbox's index, adopt the source's vectors of `model_id`
    (`[memory_index] embedder_model`), and read the coverage back; refused
    under the floor (module docstring)."""
    started = time.monotonic()
    model = fingerprint(model_id)
    source_cov = await source_coverage(source_url, model_id)
    target = create_sql_engine(database_url)
    source = create_readonly_sql_engine(source_url)
    try:
        await memory_index.rebuild(target)
        async with source.connect() as src, target.begin() as dst:
            await memory_index.adopt_worker_state(src, dst, model=model)
        async with target.connect() as conn:
            coverage = Coverage.of(await memory_index.embed_counts(conn, model))
    finally:
        await source.dispose()
        await target.dispose()
    refuse_low_coverage(coverage, source_cov)
    return SandboxIndex(coverage, source_cov, time.monotonic() - started)


async def check_served_model(url: str, model_id: str) -> str:
    """The GGUF file the embedder at `url` serves (/props), or refused when
    it does not answer or serves another file than `model_id` names: every
    query would be embedded by nothing, or by another model."""
    want = model_file(model_id)
    try:
        served = await LlamaEmbedder(url, timeout_seconds=10.0).served_model()
    except EmbedderUnavailableError as exc:
        raise GraphIndexRefusedError(
            f"the embedder at {url} did not answer /props ({exc}); bring it up with "
            "`docker compose --profile embedder up -d embedder`"
        ) from exc
    if served != want:
        raise GraphIndexRefusedError(
            f"the embedder at {url} serves {served!r}, not {want!r}"
        )
    return served
