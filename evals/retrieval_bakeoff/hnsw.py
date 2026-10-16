"""The two-stage arm inside Postgres: the HNSW approximation, measured
against the exact arms.

The design: store the full vector as `halfvec(<full>)`, index ONLY its
leading `subvector(embedding, 1, <search>)::halfvec(<search>)` with HNSW and
`halfvec_cosine_ops` (pgvector's default `m` and `ef_construction`), search
that index for the top `k`, then re-rank those `k` by cosine on the full
`halfvec` and keep the order. Both stages are one SQL statement, so what is
timed and checked is what would run.

Fail-closed:

- the database must be the bake-off's throwaway: its name starts with
  `bakeoff` and it carries `lexical_seed.MARKER` (the trigram arm's claim),
  or nothing is written;
- every query's plan is read (`EXPLAIN`): one that does not scan the
  expression index is `index_unused`, an ERROR, never a ranking. A small
  table makes the planner prefer a sequential scan, so `enable_seqscan` is
  off for the session, and recorded;
- a query whose candidate set is smaller than `k` while the pool is not is
  `short_candidates`, an ERROR: HNSW returns at most `hnsw.ef_search` rows,
  so the caller sets it at least `k` (`run` refuses less) and it is recorded.

Latency is client wall time over loopback, per query, after one warm pass:
the stage-1 statement alone, and the two-stage statement.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

from nora.subject import lexical_seed

__all__ = ["STAGE1_ORDER", "plan_uses_index", "rerank", "short_candidates"]

#: Stage 1's ORDER BY: the index's own expression against the query's, then
#: the id, so the item admitted at the k boundary among equal distances is the
#: same on every run (the exact arm breaks ties the same way).
STAGE1_ORDER = ("subvector(embedding, 1, {s})::halfvec({s}) "
                "<=> subvector(%(q)s::text::halfvec({f}), 1, {s})::halfvec({s}), id")
_SAFE = re.compile(r"[a-z][a-z0-9_]*")


def short_candidates(n: int, k: int, n_pool: int) -> bool:
    """Fewer candidates than `k` while the pool holds at least `k`."""
    return n < min(k, n_pool)


def plan_uses_index(plan: Any, index: str) -> bool:
    """Whether an `EXPLAIN (FORMAT JSON)` plan scans `index` anywhere."""
    if isinstance(plan, list):
        return any(plan_uses_index(p, index) for p in plan)
    if isinstance(plan, dict):
        if plan.get("Index Name") == index and plan.get("Node Type") in (
                "Index Scan", "Index Only Scan"):
            return True
        return any(plan_uses_index(v, index) for v in plan.values()
                   if isinstance(v, (list, dict)))
    return False


def _literal(v: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in v) + "]"


def _conninfo(dsn: str) -> tuple[str, str]:
    url = make_url(dsn)
    name = url.database or ""
    plain = url.set(drivername="postgresql").render_as_string(hide_password=False)
    return plain, name


def rerank(dsn: str, pool: Mapping[str, Sequence[float]], queries: Mapping[str, Sequence[float]],
           *, table: str, search_dim: int, full_dim: int, k: int,
           ef_search: int,
           ) -> tuple[dict[str, list[str] | str], dict[str, list[float]], dict[str, Any]]:
    """Per query id, the `k` candidates in re-ranked order, or an error kind
    (`index_unused`, `short_candidates`); then each query's milliseconds
    (`stage1`, `two_stage`) and the provenance. `pool` and `queries` hold
    full-dimension vectors."""
    if not _SAFE.fullmatch(table):
        raise ValueError(f"table name {table!r}")
    conninfo, name = _conninfo(dsn)
    if not name.startswith("bakeoff"):
        raise lexical_seed.NotAThrowawayDatabaseError(f"refusing database {name!r}")
    index = f"{table}_hnsw"
    order = STAGE1_ORDER.format(s=search_dim, f=full_dim)
    # The table matched _SAFE and the dimensions are ints: nothing here is input text.
    stage1 = f"SELECT id FROM {table} ORDER BY {order} LIMIT %(k)s"  # noqa: S608
    both = (f"WITH c AS MATERIALIZED (SELECT id, embedding FROM {table} ORDER BY {order} "  # noqa: S608
            f"LIMIT %(k)s) SELECT id, (SELECT count(*) FROM c) FROM c "
            f"ORDER BY embedding <=> %(q)s::text::halfvec({full_dim}), id")
    out: dict[str, list[str] | str] = {}
    t_stage1: list[float] = []
    t_both: list[float] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        marked = conn.execute("SELECT to_regclass(%s) IS NOT NULL",
                              (f"public.{lexical_seed.MARKER}",)).fetchone()
        if not (marked and marked[0]):
            raise lexical_seed.NotAThrowawayDatabaseError(
                f"{name!r} carries no {lexical_seed.MARKER} marker")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(f"CREATE TABLE {table} (id text PRIMARY KEY, "
                     f"embedding halfvec({full_dim}) NOT NULL)")
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {table} VALUES (%s, %s::text::halfvec({full_dim}))",  # noqa: S608
                            [(pid, _literal(v)) for pid, v in pool.items()])
        t0 = time.perf_counter()
        conn.execute(f"CREATE INDEX {index} ON {table} USING hnsw "
                     f"((subvector(embedding, 1, {search_dim})::halfvec({search_dim})) "
                     f"halfvec_cosine_ops)")
        build_ms = (time.perf_counter() - t0) * 1000
        conn.execute(f"ANALYZE {table}")
        conn.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(ef_search),))
        conn.execute("SET enable_seqscan = off")
        params = {qid: {"q": _literal(v), "k": k} for qid, v in queries.items()}
        for p in params.values():  # one warm pass, untimed
            conn.execute(both, p).fetchall()
        for qid, p in params.items():
            plan = conn.execute(f"EXPLAIN (FORMAT JSON) {both}", p).fetchone()
            if not plan_uses_index(plan[0] if plan else None, index):
                out[qid] = "index_unused"
                continue
            t0 = time.perf_counter()
            conn.execute(stage1, p).fetchall()
            t_stage1.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            rows = conn.execute(both, p).fetchall()
            t_both.append((time.perf_counter() - t0) * 1000)
            n = int(rows[0][1]) if rows else 0
            out[qid] = ("short_candidates" if short_candidates(n, k, len(pool))
                        else [r[0] for r in rows])
        version = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        reloptions = conn.execute("SELECT reloptions FROM pg_class WHERE relname = %s",
                                  (index,)).fetchone()
        settings = {s: (conn.execute("SELECT current_setting(%s, true)", (s,)).fetchone()
                        or (None,))[0]
                    for s in ("hnsw.ef_search", "hnsw.iterative_scan", "enable_seqscan")}
        server = conn.execute("SHOW server_version").fetchone()
    prov = {"arm": "HNSW on the leading subvector, re-ranked by full halfvec cosine, in SQL",
            "column": f"halfvec({full_dim})",
            "index": f"hnsw ((subvector(embedding, 1, {search_dim})::halfvec({search_dim})) "
                     "halfvec_cosine_ops)",
            "reloptions": reloptions[0] if reloptions else None,
            "build_defaults": "pgvector's: m=16, ef_construction=64",
            "k": k, "settings": settings, "pgvector": version[0] if version else None,
            "postgres": server[0] if server else None, "n_pool": len(pool),
            "precision": "both vectors stored and compared as halfvec (fp16)",
            "index_build_ms": round(build_ms, 1)}
    return out, {"stage1": t_stage1, "two_stage": t_both}, prov
