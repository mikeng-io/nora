"""`SqlEmbeddingStore` — the seam `embedding` shipped without.

Migration 0044 created the table, `24-embedding-seam.md` §7.5 argued its shape,
and **nothing read or wrote it**. This is both halves.

**One table for every tier** (24 §1), so this store is deliberately generic in
`kind` and `row_key` and knows nothing about facts. A tier arriving with the
same dimension declares three things — its `kind`, its `scope`, and how to
render a row as text — and writes no schema.

**The staleness predicate is one comparison.** `fingerprint != current` catches
a model swap and a changed row together (`25` §5), so `stale()` returns rows
whose stored fingerprint disagrees with what the caller computed, plus rows with
no embedding at all. There is no second mechanism and no ordering trap.

**R-27 on every read.** A store that answered `()` for both «nothing is stale»
and «I could not look» would make pass 4 report a clean sweep on a database that
was down, and the next pass would believe it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, get_args

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import EMBEDDING

log = logging.getLogger(__name__)

#: R-27's three states, and the metric's label vocabulary is `get_args` of THIS.
EmbeddingStatus = Literal["ok", "nothing_found", "unavailable"]
EMBEDDING_STATUSES: frozenset[str] = frozenset(get_args(EmbeddingStatus))


@dataclass(frozen=True)
class StoredEmbedding:
    """One row's identity and what was embedded for it — never the vector.

    The vector is deliberately absent: nothing outside this module has a use
    for 1024 floats, and returning them would make every staleness sweep carry
    megabytes for a comparison it makes on the fingerprint.
    """

    kind: str
    row_key: str
    scope: str
    text: str
    model: str
    fingerprint: str
    embedded_at: int
    has_vector: bool


@dataclass(frozen=True)
class EmbeddingRead:
    """Rows, and whether the read could be made.

    Two fields for `LedgerRead`'s reason one table over: `()` with `ok` is
    «nothing is stale» and licenses skipping the pass, `()` with `unavailable`
    is «I could not check» and licenses nothing.
    """

    rows: tuple[StoredEmbedding, ...]
    status: EmbeddingStatus


@dataclass(frozen=True)
class Match:
    """One retrieval hit. `distance` is cosine, so SMALLER is closer."""

    kind: str
    row_key: str
    scope: str
    text: str
    distance: float


class SqlEmbeddingStore:
    """`SqlEmbeddingStore(engine)` — a bare `AsyncEngine`, `metrics` optional
    and injected, the shape every sibling in this package uses."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        self._engine = engine
        self._metrics = metrics

    async def existing(
        self, *, kind: str, scope: str, row_keys: tuple[str, ...],
    ) -> EmbeddingRead:
        """What is stored for these keys. The read pass 4 compares against.

        Keyed by `row_key` rather than filtered by fingerprint in SQL, because
        the current fingerprint is computed in PYTHON — it is a hash of the
        rendered text, and rendering is `render_for_embedding`'s job, not the
        database's. Doing the comparison here would mean a second renderer in
        SQL, which is the two-copies-drift failure the spine's first rule
        names.
        """
        if not row_keys:
            self._count("existing", "nothing_found")
            return EmbeddingRead(rows=(), status="nothing_found")
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(
                        EMBEDDING.c.kind, EMBEDDING.c.row_key,
                        EMBEDDING.c.scope, EMBEDDING.c.text,
                        EMBEDDING.c.model, EMBEDDING.c.fingerprint,
                        EMBEDDING.c.embedded_at,
                        EMBEDDING.c.v1024.isnot(None).label("has_vector"),
                    )
                    .where(
                        EMBEDDING.c.kind == kind,
                        EMBEDDING.c.scope == scope,
                        EMBEDDING.c.row_key.in_(row_keys),
                    )
                    .order_by(EMBEDDING.c.row_key.asc())
                )).all()
        except Exception:
            log.exception(
                "embedding read unavailable (kind=%s scope=%s)", kind, scope,
            )
            self._count("existing", "unavailable")
            return EmbeddingRead(rows=(), status="unavailable")
        out = tuple(
            StoredEmbedding(
                kind=r.kind, row_key=r.row_key, scope=r.scope, text=r.text,
                model=r.model, fingerprint=r.fingerprint,
                embedded_at=r.embedded_at, has_vector=bool(r.has_vector),
            )
            for r in rows
        )
        self._count("existing", "ok" if out else "nothing_found")
        return EmbeddingRead(rows=out, status="ok" if out else "nothing_found")

    async def upsert(
        self, *, rows: tuple[tuple[StoredEmbedding, tuple[float, ...]], ...],
    ) -> int:
        """Write vectors. Returns rows written.

        **Through the declared `Vector` type**, never raw SQL: the type's bind
        processor is what turns a list into the text Postgres wants, and raw
        SQL handing asyncpg a Python list for a `vector` parameter raises
        `DataError`. `tests/infra/test_embedding_roundtrip.py` fixes that
        boundary.

        **RAISES rather than degrading.** This runs in the Dreamer's tick, not
        on a tag's critical path, and a silent `0` here would say «nothing to
        write» when the truth is «I could not write», which is the inversion
        the rest of this module exists to avoid.
        """
        if not rows:
            self._count("upsert", "nothing_found")
            return 0
        values = [
            {
                "kind": row.kind, "row_key": row.row_key, "scope": row.scope,
                "text": row.text, "model": row.model,
                "fingerprint": row.fingerprint,
                "embedded_at": row.embedded_at, "v1024": list(vector),
            }
            for row, vector in rows
        ]
        async with self._engine.begin() as conn:
            for value in values:
                stmt = pg.insert(EMBEDDING).values(value)
                await conn.execute(stmt.on_conflict_do_update(
                    index_elements=["kind", "row_key"],
                    set_={
                        "scope": stmt.excluded.scope,
                        "text": stmt.excluded.text,
                        "model": stmt.excluded.model,
                        "fingerprint": stmt.excluded.fingerprint,
                        "embedded_at": stmt.excluded.embedded_at,
                        "v1024": stmt.excluded.v1024,
                    },
                ))
        self._count("upsert", "ok")
        return len(values)

    async def nearest(
        self, *, kind: str, scope: str, model: str, probe: tuple[float, ...],
        limit: int, floor: float,
    ) -> tuple[tuple[Match, ...], EmbeddingStatus]:
        """The dense channel. Cosine distance, closest first, scoped, and
        filtered to ONE MODEL.

        **`model` is not optional and it is not a convenience.** A model swap
        re-embeds the table one batch at a time, so for the length of that
        sweep the column holds vectors from two models — two different
        geometries in one `<=>` ordering. Distances across them are not
        comparable and nothing about the result would say so: retrieval would
        silently rank on a mixture and look exactly like retrieval that
        worked. The column's own dimension does not catch it, because two
        1024-d models share a width and share nothing else.

        Filtering to the caller's current model makes the mid-sweep state
        honest instead: rows not yet re-embedded are invisible to retrieval
        rather than wrongly ranked, which is `nothing_found` shrinking for a
        while — a bounded, visible degradation — instead of a wrong answer.


        `floor` is a MAXIMUM DISTANCE, not a minimum score — `23` §9's 0.5,
        named for the direction it actually works in. Rows beyond it are
        dropped rather than ranked: a retrieval that always returns something
        cannot say «I found nothing about that», and the verb's three states
        are the point.

        Scope is applied BEFORE ranking, in the same statement. A retrieval
        verb that could not scope would be R-49's relay in a new place — one
        social unit's claims reaching another's room.
        """
        if limit <= 0 or not probe:
            return (), "nothing_found"
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(
                        EMBEDDING.c.kind, EMBEDDING.c.row_key,
                        EMBEDDING.c.scope, EMBEDDING.c.text,
                        EMBEDDING.c.v1024.cosine_distance(
                            list(probe)).label("distance"),
                    )
                    .where(
                        EMBEDDING.c.kind == kind,
                        EMBEDDING.c.scope == scope,
                        EMBEDDING.c.model == model,
                        EMBEDDING.c.v1024.isnot(None),
                    )
                    .order_by(
                        sa.text("distance"),
                        # Tie-break. Two identical texts embed identically, and
                        # this feeds a LIMIT.
                        EMBEDDING.c.row_key.asc(),
                    )
                    .limit(limit)
                )).all()
        except Exception:
            log.exception(
                "dense read unavailable (kind=%s scope=%s model=%s)",
                kind, scope, model,
            )
            self._count("nearest", "unavailable")
            return (), "unavailable"
        out = tuple(
            Match(
                kind=r.kind, row_key=r.row_key, scope=r.scope, text=r.text,
                distance=float(r.distance),
            )
            for r in rows
            if float(r.distance) <= floor
        )
        self._count("nearest", "ok" if out else "nothing_found")
        return out, "ok" if out else "nothing_found"

    async def lexical(
        self, *, kind: str, scope: str, query: str, limit: int,
        threshold: float = 0.3,
    ) -> tuple[tuple[Match, ...], EmbeddingStatus]:
        """The lexical channel (`23` §4), over the SAME text the dense channel
        embedded.

        **`embedding.text`, not the fact's columns**, and that is the load-
        bearing choice. Matching against `subject || predicate || object`
        assembled in SQL would be a SECOND renderer beside
        `render_for_embedding` — the two-copies-drift failure the spine's
        first rule names, and it would drift on the first change to the
        separator. Reading the stored text means one renderer and one corpus,
        so the two channels agree about what exists: a row not yet embedded is
        invisible to BOTH, consistently, rather than found by one and missed
        by the other.

        **`word_similarity`, not `similarity` and not `ilike`. Measured**
        against a real database, 2026-08-28, over the triples in this room's
        own examples:

        | query | best `word_similarity` | best `similarity` |
        |---|---|---|
        | `big walk` | **1.000** | 0.409 |
        | `who made big walk` | **0.824** on `made_by`, 0.588 on `means` | 0.560 |
        | `大步行` | **1.000**, 0.000 on every other row | 0.222 |
        | `打風` | **1.000** | 0.158 |
        | `邊個做嘅大步行` | 0.250 | 0.083 |

        Three readings from that, and each decided something:

        - `similarity` is whole-string and useless here — an EXACT term scores
          0.409 against a triple, below any threshold worth setting, because
          the rest of the triple dilutes it. `word_similarity` compares
          against the best word extent, which is the question actually being
          asked.
        - **CJK works**, contrary to the note tier's two-character finding:
          `打風` is two characters and scores 1.000. That store matched a
          three-sentence body where a short query shares few trigrams; a
          triple is six tokens, so the extent IS the term.
        - The Cantonese QUESTION scores 0.250 and falls below the threshold —
          and that is `23` §6's split arriving as a measurement rather than an
          argument. Cross-lingual and paraphrased questions are the dense
          channel's job, and no trigram scheme closes them.

        `threshold` is `23` §9's 0.3: it admits everything at 0.529 and above
        and excludes the 0.250, which is the row the dense channel is there
        for.

        **`distance` is `1 - word_similarity`**, so smaller is closer and the
        field means the same thing it means on the dense channel. Fusion is by
        RANK rather than by score, so the two are never compared numerically —
        but a field called `distance` that ran the other way in one channel
        would be read wrongly by the first person to look at it.
        """
        if limit <= 0 or not query.strip():
            return (), "nothing_found"
        score = sa.func.word_similarity(query, EMBEDDING.c.text)
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(
                        EMBEDDING.c.kind, EMBEDDING.c.row_key,
                        EMBEDDING.c.scope, EMBEDDING.c.text,
                        score.label("score"),
                    )
                    .where(
                        EMBEDDING.c.kind == kind,
                        EMBEDDING.c.scope == scope,
                        score >= threshold,
                    )
                    .order_by(
                        sa.desc(sa.text("score")),
                        # Tie-break. Identical texts score identically and this
                        # feeds a LIMIT.
                        EMBEDDING.c.row_key.asc(),
                    )
                    .limit(limit)
                )).all()
        except Exception:
            log.exception(
                "lexical read unavailable (kind=%s scope=%s)", kind, scope,
            )
            self._count("lexical", "unavailable")
            return (), "unavailable"
        out = tuple(
            Match(
                kind=r.kind, row_key=r.row_key, scope=r.scope, text=r.text,
                distance=1.0 - float(r.score),
            )
            for r in rows
        )
        self._count("lexical", "ok" if out else "nothing_found")
        return out, "ok" if out else "nothing_found"

    async def forget(self, *, kind: str, row_keys: tuple[str, ...]) -> int:
        """Drop embeddings for rows that no longer exist. Returns rows removed.

        Not a retention sweep: an embedding's lifetime is its row's, so this is
        called when a row goes, never on a clock.
        """
        if not row_keys:
            return 0
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(EMBEDDING).where(
                    EMBEDDING.c.kind == kind,
                    EMBEDDING.c.row_key.in_(row_keys),
                )
            )
        return int(result.rowcount)

    def _count(self, verb: str, status: EmbeddingStatus) -> None:
        # RUNTIME check as well as mypy's: the vocabulary constant is what the
        # metric's help text is written from, and a value drifting from it is
        # the label-vocabulary failure CLAUDE.md names.
        if status not in EMBEDDING_STATUSES:
            raise ValueError(
                f"embedding status must be one of "
                f"{sorted(EMBEDDING_STATUSES)}, got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_embedding_store_total", verb=verb, status=status,
            )
