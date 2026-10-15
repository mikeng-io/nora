"""The two-stage arm in Postgres (`evals.retrieval_bakeoff.hnsw`): a real
`halfvec` column, the expression HNSW index on its leading subvector, the
re-rank in SQL. Every plan must scan the index, a candidate set short of k
is an ERROR, and only the bake-off's marked throwaway database is written."""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from evals.retrieval_bakeoff import hnsw, run
from evals.retrieval_bakeoff.run import Embedded
from nora.subject import lexical_seed

FULL, SEARCH = 32, 8


def _unit(rng: random.Random) -> list[float]:
    v = [rng.gauss(0, 1) for _ in range(FULL)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


RNG = random.Random(7)
POOL = {f"p{i:02}": _unit(RNG) for i in range(60)}
QUERIES = {f"q{i}": _unit(RNG) for i in range(5)}


def _db(pg_admin, name: str, *, marked: bool) -> Iterator[str]:
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    if marked:
        with psycopg.connect(pg_admin.dsn(name), autocommit=True) as c:
            c.execute(f"CREATE TABLE {lexical_seed.MARKER} (created_at timestamptz)")
    yield (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
           f"@{pg_admin.host}:{pg_admin.port}/{name}")
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture(scope="module")
def dsn(pg_admin) -> Iterator[str]:
    yield from _db(pg_admin, f"bakeoff_{uuid.uuid4().hex}", marked=True)


def _rerank(dsn: str, **kw):
    args = {"table": "bakeoff_hnsw_t", "search_dim": SEARCH, "full_dim": FULL, "k": 20,
            "ef_search": 60, **kw}
    return hnsw.rerank(dsn, POOL, QUERIES, **args)


def test_the_index_is_scanned_and_its_candidates_re_ranked_in_sql(dsn):
    got, times, prov = _rerank(dsn)
    exact, _ = run.rank_rerank(Embedded(vectors=QUERIES), Embedded(vectors=POOL),
                               search_dim=SEARCH, k=20)
    for q in QUERIES:
        assert isinstance(got[q], list) and len(got[q]) == 20
        # ef_search covers the pool: the graph search is exhaustive here, so
        # the candidates are exact; fp16 storage may only swap near-ties.
        assert set(got[q]) == set(exact[q])
        assert got[q][0] == exact[q][0]
    assert len(times["stage1"]) == len(times["two_stage"]) == len(QUERIES)
    assert prov["settings"]["hnsw.ef_search"] == "60"
    assert prov["settings"]["enable_seqscan"] == "off"
    assert prov["column"] == f"halfvec({FULL})" and prov["reloptions"] is None


def test_a_plan_that_does_not_scan_the_index_is_an_error(dsn, monkeypatch):
    # The full-vector cosine: no index covers it.
    monkeypatch.setattr(hnsw, "STAGE1_ORDER", "embedding <=> %(q)s::text::halfvec({f})")
    got, _, _ = _rerank(dsn)
    assert set(got.values()) == {"index_unused"}


def test_hnsw_returning_fewer_than_k_is_an_error(dsn):
    # HNSW returns at most ef_search rows; the pool holds 60.
    got, _, _ = _rerank(dsn, ef_search=10)
    assert set(got.values()) == {"short_candidates"}


def test_an_unmarked_or_foreign_database_is_refused(pg_admin):
    gen = _db(pg_admin, f"bakeoff_{uuid.uuid4().hex}", marked=False)
    url = next(gen)
    try:
        with pytest.raises(lexical_seed.NotAThrowawayDatabaseError):
            _rerank(url)
    finally:
        next(gen, None)
    with pytest.raises(lexical_seed.NotAThrowawayDatabaseError):
        _rerank(url.rsplit("/", 1)[0] + "/nora")
