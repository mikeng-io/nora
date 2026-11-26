"""The Cognitive Graph search index in an eval sandbox, with real vectors.

A snapshot-seeded sandbox restores no derived `graph_search` rows
(`nora.infra.sql.history.NOT_RESTORED`), and the subject's `memory_search`
reads that index through the graph retrieval surface. Building the legacy
`memory_node` index would therefore seed one index while the subject reads
another. After seeding and before the drive this module:

1. rebuilds `graph_search` with production's own projection,
   `nora.memory.adapters.graph_search.rebuild_graph_search`;
2. adopts the embedding worker's result from the read-only source wherever
   the embedded input is identical. `graph_search` embeds text only
   (`embed_input = 'text'`, migration `0175_graph_search`), so identity is
   the exact indexed text, never a row id; and
3. reads coverage back fail-closed. Below `COVERAGE_FLOOR` of the source's
   coverage the draw is refused: an ERROR, never graded.

`IndexCache` keeps the first build's `IndexImage` — the full `graph_search`
contents, vectors rendered as text — for draws seeding the same graph. Its
key includes the caller's key, the source and model, the digest of the
searchable rows the sandbox's own sources project, and the digest of the
source worker state adoption would copy. A reuse must equal a build exactly:
after an image is applied, the rows actually written are read back inside
the same transaction and must equal the cached image in full, and their
`(graph_id, unit, text)` keys must equal both the sandbox's current source
projection and the projection recorded at the build, or the draw is refused.
There is no node-id sequence, projection queue or `memory_index_state` in
`graph_search`; those legacy elements have no counterpart here.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.ext.asyncio import AsyncConnection

from nora import memory as memory_area
from nora.infra.embedder import EmbedderUnavailableError, LlamaEmbedder, fingerprint, model_file
from nora.infra.sql import create_readonly_sql_engine, create_sql_engine
from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH, REGISTRY
from nora.infra.sql.embed_schedule import EMBED_MAX_ATTEMPTS
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS

__all__ = [
    "COVERAGE_FLOOR",
    "EMBEDDER_FROM_HOST",
    "Coverage",
    "GraphIndexRefusedError",
    "IndexCache",
    "IndexImage",
    "SandboxIndex",
    "check_served_model",
    "image_digest",
    "index_image",
    "index_sandbox",
    "refuse_low_coverage",
    "source_coverage",
]

EMBEDDER_FROM_HOST = "http://127.0.0.1:8092"
COVERAGE_FLOOR = 0.95


class GraphIndexRefusedError(RuntimeError):
    """The graph surface cannot be measured on this source or sandbox."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """`graph_search` rows owing or holding a vector of one model."""

    embedded: int
    owed: int
    given_up: int

    @property
    def ratio(self) -> float:
        """Of the rows the worker owes a vector, the share holding one."""
        total = self.embedded + self.owed
        return 1.0 if total == 0 else self.embedded / total

    def line(self) -> str:
        return (
            f"{self.embedded} with a vector, {self.owed} owed "
            f"({self.given_up} given up): coverage {self.ratio:.3f}"
        )


@dataclass(frozen=True, slots=True)
class SandboxIndex:
    """What `index_sandbox` built, for the per-sandbox read-back line."""

    coverage: Coverage
    source: Coverage
    seconds: float
    reused: bool = False

    def line(self) -> str:
        how = "reused" if self.reused else "built"
        return (
            f"graph search index: {self.coverage.line()} "
            f"(source {self.source.ratio:.3f}), {how} in {self.seconds:.1f}s"
        )


#: `(graph_id, unit, text, vector-as-text, embedding_model, embedded_at,
#: embed_attempts, embed_error, embed_input)`, ordered by `(unit, graph_id)`.
IndexRow = tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class IndexImage:
    """The whole derived `graph_search` table as one deterministic image."""

    rows: tuple[IndexRow, ...]


@dataclass(frozen=True, slots=True)
class _Built:
    image: IndexImage
    #: Exact `(graph_id, unit, text)` projection the first build indexed.
    expected: tuple[tuple[str, str, str], ...]


@dataclass
class IndexCache:
    """Built graph indexes, by key, most recent `size` kept."""

    size: int = 4
    _entries: OrderedDict[Hashable, _Built] = field(default_factory=OrderedDict)

    def get(self, key: Hashable) -> _Built | None:
        built = self._entries.get(key)
        if built is not None:
            self._entries.move_to_end(key)
        return built

    def put(self, key: Hashable, built: _Built) -> None:
        self._entries[key] = built
        self._entries.move_to_end(key)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)


async def _units(conn: AsyncConnection) -> tuple[str, ...]:
    return tuple(str(unit) for unit in await conn.scalars(
        sa.select(REGISTRY.c.unit).distinct().order_by(REGISTRY.c.unit)
    ))


async def _expected_rows(conn: AsyncConnection) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for unit in await _units(conn):
        rows.extend(await memory_area.wire.projected_graph_search_rows(conn, unit))
    return tuple(sorted(rows, key=lambda row: (row[1], row[0])))


def _keys_digest(rows: Iterable[tuple[str, str, str]]) -> str:
    blob = json.dumps(list(rows), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def image_digest(image: IndexImage) -> str:
    """A sha256 over the whole `graph_search` image."""
    blob = json.dumps(list(image.rows), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


async def index_image(conn: AsyncConnection) -> IndexImage:
    """`conn`'s derived graph index as an `IndexImage`."""
    row = GRAPH_SEARCH.c
    result = await conn.execute(sa.select(
        row.graph_id, row.unit, row.text, sa.cast(row.embedding, sa.Text).label("vector"),
        row.embedding_model, row.embedded_at, row.embed_attempts, row.embed_error,
        row.embed_input,
    ).order_by(row.unit, row.graph_id))
    return IndexImage(tuple(tuple(r) for r in result))


async def _apply_index_image(target: AsyncConnection, image: IndexImage) -> None:
    """Replace the derived index with `image` inside the caller's transaction."""
    await target.execute(sa.delete(GRAPH_SEARCH))
    stmt = sa.insert(GRAPH_SEARCH).values(
        graph_id=sa.bindparam("b_graph_id"), unit=sa.bindparam("b_unit"),
        text=sa.bindparam("b_text"),
        embedding=sa.cast(sa.bindparam("b_vector", type_=sa.Text), HALFVEC(EMBEDDING_DIMENSIONS)),
        embedding_model=sa.bindparam("b_embedding_model"),
        embedded_at=sa.bindparam("b_embedded_at"),
        embed_attempts=sa.bindparam("b_embed_attempts"),
        embed_error=sa.bindparam("b_embed_error"), embed_input=sa.bindparam("b_embed_input"),
    )
    params = [
        {
            "b_graph_id": r[0], "b_unit": r[1], "b_text": r[2], "b_vector": r[3],
            "b_embedding_model": r[4], "b_embedded_at": r[5], "b_embed_attempts": r[6],
            "b_embed_error": r[7], "b_embed_input": r[8],
        }
        for r in image.rows
    ]
    for start in range(0, len(params), 500):
        await target.execute(stmt, params[start:start + 500])


async def _coverage(conn: AsyncConnection, model: str) -> Coverage:
    row = GRAPH_SEARCH.c
    current = sa.and_(row.embedding.isnot(None), row.embedding_model == model)
    given_up = sa.and_(~current, row.embed_attempts >= EMBED_MAX_ATTEMPTS)
    result = (await conn.execute(sa.select(
        sa.func.count().filter(current),
        sa.func.count().filter(sa.and_(~current, row.embed_attempts < EMBED_MAX_ATTEMPTS)),
        sa.func.count().filter(given_up),
    ))).one()
    return Coverage(*(int(v) for v in result))


async def _has_vectors(conn: AsyncConnection) -> bool:
    return bool((await conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'graph_search' "
        "AND column_name = 'embedding')"
    ))).scalar_one())


async def source_coverage(
    source_url: str, model_id: str, *, require_vectors: bool = True,
) -> Coverage:
    """The source's `graph_search` coverage for `model_id`, or refused."""
    model = fingerprint(model_id)
    engine = create_readonly_sql_engine(source_url)
    try:
        async with engine.connect() as conn:
            if not await _has_vectors(conn):
                raise GraphIndexRefusedError(
                    "the eval source holds no Cognitive Graph search index with vectors "
                    "(it predates migration 0175_graph_search); refresh it with "
                    "`make eval-source`"
                )
            coverage = await _coverage(conn, model)
    finally:
        await engine.dispose()
    if require_vectors and coverage.embedded == 0:
        raise GraphIndexRefusedError(
            f"the eval source holds no graph-search vector of {model!r}; refresh it with "
            "`make eval-source` once production's backfill has run"
        )
    if require_vectors and coverage.ratio < COVERAGE_FLOOR:
        raise GraphIndexRefusedError(
            f"the eval source's graph-search backfill is incomplete ({coverage.line()}, under "
            f"{COVERAGE_FLOOR}); every search would answer partial. Refresh it with "
            "`make eval-source` once production's backfill has finished"
        )
    return coverage


def refuse_low_coverage(sandbox: Coverage, source: Coverage) -> None:
    """Raise unless `sandbox` holds at least `COVERAGE_FLOOR` of `source`."""
    floor = COVERAGE_FLOOR * source.ratio
    if sandbox.ratio < floor:
        raise GraphIndexRefusedError(
            f"the sandbox's graph search index is under its floor ({sandbox.line()}; "
            f"floor {floor:.3f}, {COVERAGE_FLOOR} of the source's {source.ratio:.3f}); "
            "a draw on it would answer partial where production answers ok"
        )


async def _adoptable_state(conn: AsyncConnection, model: str) -> dict[str, tuple[Any, ...]]:
    """The worker state adoption copies per exact text — the reuse key's input identity."""
    row = GRAPH_SEARCH.c
    current = sa.and_(row.embedding.isnot(None), row.embedding_model == model)
    result = await conn.execute(sa.select(
        row.text, sa.cast(row.embedding, sa.Text).label("vector"), row.embedding_model,
        row.embedded_at, row.embed_attempts, row.embed_error, row.embed_input,
    ).where(sa.or_(current, row.embedding.is_(None))).order_by(
        sa.case((current, 0), else_=1), row.unit, row.graph_id,
    ))
    state: dict[str, tuple[Any, ...]] = {}
    for r in result:
        state.setdefault(str(r.text), tuple(r)[1:])
    return state


def _state_digest(state: dict[str, tuple[Any, ...]]) -> str:
    blob = json.dumps(sorted(state.items()), default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


async def _adopt_worker_state(
    source: AsyncConnection, target: AsyncConnection, *, model: str, batch: int = 500,
) -> None:
    """Copy worker state for exactly equal indexed text."""
    row = GRAPH_SEARCH.c
    source_rows = await _adoptable_state(source, model)
    updates = []
    target_rows = await target.execute(sa.select(
        row.graph_id, row.unit, row.text,
    ).where(row.embedding.is_(None), row.embed_attempts == 0))
    for t in target_rows:
        adopted = source_rows.get(str(t.text))
        if adopted is not None:
            updates.append({
                "b_graph_id": t.graph_id, "b_unit": t.unit, "b_vector": adopted[0],
                "b_embedding_model": adopted[1], "b_embedded_at": adopted[2],
                "b_embed_attempts": adopted[3], "b_embed_error": adopted[4],
                "b_embed_input": adopted[5],
            })
    stmt = sa.update(GRAPH_SEARCH).where(
        row.graph_id == sa.bindparam("b_graph_id"), row.unit == sa.bindparam("b_unit"),
    ).values(
        embedding=sa.cast(sa.bindparam("b_vector", type_=sa.Text), HALFVEC(EMBEDDING_DIMENSIONS)),
        embedding_model=sa.bindparam("b_embedding_model"),
        embedded_at=sa.bindparam("b_embedded_at"),
        embed_attempts=sa.bindparam("b_embed_attempts"),
        embed_error=sa.bindparam("b_embed_error"), embed_input=sa.bindparam("b_embed_input"),
    )
    for start in range(0, len(updates), batch):
        await target.execute(stmt, updates[start:start + batch])


async def _rebuild(conn: AsyncConnection) -> None:
    for unit in await _units(conn):
        await memory_area.wire.rebuild_graph_search(conn, unit)


async def index_sandbox(
    database_url: str, *, source_url: str, model_id: str,
    cache: IndexCache | None = None, key: Hashable = (), require_vectors: bool = True,
) -> SandboxIndex:
    """Build or reuse the sandbox's Cognitive Graph search index."""
    started = time.monotonic()
    model = fingerprint(model_id)
    source_cov = await source_coverage(source_url, model_id, require_vectors=require_vectors)
    target = create_sql_engine(database_url)
    source = create_readonly_sql_engine(source_url)
    reused = False
    image: IndexImage | None = None
    coverage: Coverage | None = None
    try:
        cache_key: Hashable = None
        built = None
        seeded_expected: tuple[tuple[str, str, str], ...] = ()
        if cache is not None:
            async with source.connect() as src, target.connect() as conn:
                seeded_expected = await _expected_rows(conn)
                state_digest = _state_digest(await _adoptable_state(src, model))
            cache_key = (source_url, model, key, _keys_digest(seeded_expected), state_digest)
            built = cache.get(cache_key)
        if built is not None:
            async with target.begin() as dst:
                await _apply_index_image(dst, built.image)
                persisted = await index_image(dst)
                expected = await _expected_rows(dst)
                applied = {(str(r[0]), str(r[1]), str(r[2])) for r in persisted.rows}
                if (persisted != built.image or applied != set(expected)
                        or set(expected) != set(built.expected)):
                    raise GraphIndexRefusedError(
                        "the graph index applied from an earlier build does not fit this "
                        "sandbox: the rows actually written differ from the build's, or "
                        "its searchable source rows differ from the build's; a reuse "
                        "must equal a build"
                    )
            reused = True
        else:
            async with source.connect() as src, target.begin() as dst:
                await _rebuild(dst)
                await _adopt_worker_state(src, dst, model=model)
        async with target.connect() as conn:
            coverage = await _coverage(conn, model)
            if cache is not None and not reused:
                image = await index_image(conn)
    finally:
        await source.dispose()
        await target.dispose()
    if coverage is None:
        raise RuntimeError("index_sandbox finished without reading coverage")
    if require_vectors:
        refuse_low_coverage(coverage, source_cov)
    if cache is not None and image is not None:
        # The exact expected rows were the cache key's measured input; record
        # them so reuse proves the applied image against this sandbox too.
        cache.put(cache_key, _Built(image, seeded_expected))
    return SandboxIndex(coverage, source_cov, time.monotonic() - started, reused=reused)


async def check_served_model(url: str, model_id: str) -> str:
    """The GGUF file the embedder at `url` serves (/props), or refused."""
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
