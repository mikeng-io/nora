"""The Cognitive Graph's tables hold their shapes, identities and rules with
no call site: the schema itself is the gate (phase 1).

Covers: the kind columns and edge indexes (item 1), the shared planes and
CHECKs (item 2), the registry foreign keys (item 3) and the kind-condition,
lifecycle and stale-base refusals (item 4).
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
    INTENTION,
    REGISTRY,
)

PLANES = {
    "id", "kind", "basis", "reasoning", "confidence", "unit", "origin_room",
    "identities", "excluded_identities", "lineage", "exposure", "writer", "occasion",
    "pass", "trace", "model", "rev_code", "rev_instruction", "rev_skill",
    "rev_config", "rev_identity", "rev_policy", "occurred_at", "asserted_at",
    "valid_from", "valid_to", "replaces", "state",
}
EXTRAS = {
    "entity": {"name", "aliases", "pointer_kind", "pointer_ref",
               "source_ref_kind", "source_ref_value"},
    "claim": {"part", "content", "prediction", "window_end", "grade"},
    "intention": {
        "owner", "owed_to", "form", "done_when_form", "done_when",
        "holds_while_form", "holds_while", "drop_when_form", "drop_when",
        "deadline", "review_time", "delivery_mode", "trigger", "ending",
        "ended_at",
    },
    "edge": {"type", "src_id", "dst_id"},
}
TABLES = {"entity": ENTITY, "claim": CLAIM, "intention": INTENTION, "edge": EDGE}
_ids = itertools.count()


def _row(kind: str, **over: object) -> dict:
    """A row satisfying every constraint on its table, for `over` to break one."""
    base = {
        "id": f"r-{next(_ids)}", "kind": kind, "unit": "u",
        "exposure": "internal", "writer": "test",
    }
    if kind == "entity":
        base.update(basis="computed", state="admitted", name="generated")
    elif kind == "claim":
        base.update(
            basis="inferred", reasoning="deduced", confidence="high",
            state="live", part="fact", content={"text": "generated"},
        )
    elif kind == "intention":
        base.update(
            basis="committed", state="open", owner="nora", owed_to="person:m",
            form="achieve", done_when_form="act_event",
            done_when={"act": "a"}, delivery_mode="when_it_comes_up",
        )
    else:
        base.update(
            basis="computed", state="live", type="about",
            src_id="ep-src", dst_id="ep-dst",
        )
    base.update(over)
    return base


async def _register(conn, row_id: str, kind: str, unit: str = "u") -> None:
    await conn.execute(sa.insert(REGISTRY).values(id=row_id, kind=kind, unit=unit))


async def _write(
    conn, table: sa.Table, row: dict,
    *, kind: str | None = None, endpoints: bool = True,
) -> None:
    """The store's discipline inline: the registry row, then the kind row."""
    await _register(conn, row["id"], kind or row["kind"], row["unit"])
    if table is EDGE and endpoints:
        for endpoint in ("src_id", "dst_id"):
            await conn.execute(
                sa.insert(REGISTRY).values(
                    id=row[endpoint], kind="entity", unit=row["unit"],
                )
            )
    await conn.execute(sa.insert(table).values(row))


@pytest.fixture
async def engine(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_kind_columns() -> None:
    for name, extras in EXTRAS.items():
        assert extras <= {c.name for c in TABLES[name].c}, name
    assert {c.name for c in REGISTRY.c} == {
        "id", "unit", "kind", "state", "registered_at", "content_hash",
        "tombstoned_at", "occurred_at",
    }


@pytest.mark.asyncio
async def test_edge_has_an_index_leading_on_each_endpoint(engine) -> None:
    async with engine.connect() as conn:
        defs = dict(
            (await conn.execute(sa.text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'edge'"
            ))).all()
        )
    assert "(src_id, type)" in defs["idx_edge_src"]
    assert "(dst_id, type)" in defs["idx_edge_dst"]


@pytest.mark.asyncio
async def test_every_row_and_edge_carries_the_planes(engine) -> None:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT table_name, column_name, udt_name, is_nullable "
            "FROM information_schema.columns WHERE table_name IN "
            "('entity', 'claim', 'intention', 'edge')"
        ))).all()
    columns: dict[str, dict[str, tuple[str, str]]] = {}
    for table, name, udt, nullable in rows:
        columns.setdefault(table, {})[name] = (udt, nullable)
    assert set(columns) == set(EXTRAS)
    for table, cols in columns.items():
        assert set(cols) == PLANES | EXTRAS[table], table
        for plane in PLANES:
            assert cols[plane] == columns["entity"][plane], (table, plane)
        for plane in ("basis", "unit", "exposure"):
            assert cols[plane] == ("text", "NO"), (table, plane)


@pytest.mark.asyncio
async def test_an_edge_joins_two_registered_rows(engine) -> None:
    async with engine.begin() as conn:
        await _write(conn, EDGE, _row("edge", id="e-1"))
        found = (await conn.execute(sa.text(
            "SELECT s.id, d.id FROM edge "
            "JOIN registry s ON s.id = edge.src_id AND s.unit = edge.unit "
            "JOIN registry d ON d.id = edge.dst_id AND d.unit = edge.unit "
            "WHERE edge.id = 'e-1'"
        ))).all()
    assert found == [("ep-src", "ep-dst")]


@pytest.mark.asyncio
async def test_an_edge_to_an_unregistered_id_is_refused(engine) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await _register(conn, "ep-src", "entity")
            await _write(
                conn, EDGE, _row("edge", dst_id="never-registered"),
                endpoints=False,
            )


@pytest.mark.asyncio
async def test_an_edge_across_units_is_refused(engine) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await _register(conn, "ep-src", "entity")
            await _register(conn, "ep-dst", "entity", unit="other-unit")
            await _write(conn, EDGE, _row("edge"), endpoints=False)


@pytest.mark.asyncio
async def test_deleting_a_registry_row_an_edge_points_at_is_refused(engine) -> None:
    async with engine.begin() as conn:
        await _write(conn, EDGE, _row("edge", id="e-1"))
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                sa.delete(REGISTRY).where(
                    REGISTRY.c.id == "ep-src", REGISTRY.c.unit == "u"
                )
            )


@pytest.mark.asyncio
async def test_a_kind_row_without_its_registry_row_is_refused(engine) -> None:
    row = _row("claim", id="orphan-claim")
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(sa.insert(CLAIM).values(row))


@pytest.mark.asyncio
async def test_a_kind_row_whose_registry_kind_disagrees_is_refused(engine) -> None:
    row = _row("claim", id="mismatched-claim")
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await _write(conn, CLAIM, row, kind="intention")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,over", [
    ("entity", {"basis": "committed"}),
    ("entity", {"basis": "observed"}),
    ("entity", {"state": "live"}),
    ("entity", {"pointer_kind": "place"}),
    ("entity", {"pointer_kind": "identity", "pointer_ref": None}),
    ("entity", {"pointer_kind": "identity", "pointer_ref": "p1", "basis": "inferred",
                "reasoning": "told", "confidence": "low"}),
    ("claim", {"basis": "computed"}),
    ("claim", {"state": "archived"}),
    ("claim", {"part": "opinion"}),
    ("claim", {"part": "guess"}),
    ("claim", {"part": "guess", "prediction": "p"}),
    ("claim", {"part": "guess", "window_end": datetime(2026, 1, 1, tzinfo=UTC)}),
    ("claim", {"prediction": "p"}),
    ("claim", {"confidence": None}),
    ("claim", {"reasoning": None}),
    ("claim", {"reasoning": "felt"}),
    ("claim", {"confidence": "sure"}),
    ("intention", {"basis": "inferred", "reasoning": "deduced",
                   "confidence": "high"}),
    ("intention", {"state": "live"}),
    ("intention", {"form": "hold"}),
    ("intention", {"done_when_form": "deadline"}),
    ("intention", {"done_when_form": "someday"}),
    ("intention", {"done_when": None}),
    ("intention", {"holds_while_form": "predicate", "holds_while": {}}),
    ("intention", {"form": "maintain"}),
    ("intention", {"form": "maintain", "done_when_form": None,
                   "done_when": None, "holds_while_form": "untyped",
                   "holds_while": {}, "drop_when_form": "untyped",
                   "drop_when": {}}),
    ("intention", {"form": "maintain", "done_when_form": None,
                   "done_when": None, "holds_while_form": "untyped",
                   "holds_while": {}, "drop_when_form": None,
                   "drop_when": None}),
    ("intention", {"done_when_form": "untyped"}),
    ("intention", {"delivery_mode": "sometime"}),
    ("intention", {"delivery_mode": "choose_moment"}),
    ("intention", {"state": "ended", "ending": "gave_up",
                   "ended_at": datetime(2026, 1, 1, tzinfo=UTC)}),
    ("intention", {"ending": "achieved"}),
    ("intention", {"state": "ended", "ending": "achieved"}),
    ("intention", {"ended_at": datetime(2026, 1, 1, tzinfo=UTC)}),
    ("edge", {"basis": "authored"}),
    ("edge", {"type": "loves"}),
    ("claim", {"part": "grade"}),
    ("claim", {"part": "fact", "grade": "holds"}),
    ("claim", {"part": "grade", "grade": "unknown"}),
    ("edge", {"state": "livex"}),
    ("entity", {"exposure": "public"}),
], ids=repr)
async def test_a_row_breaking_a_closed_rule_is_refused(engine, kind, over) -> None:
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await _write(conn, TABLES[kind], _row(kind, **over))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,over", [
    ("entity", {"pointer_kind": "identity", "pointer_ref": "p1"}),
    ("entity", {"basis": "inferred", "reasoning": "told", "confidence": "low"}),
    ("claim", {"part": "guess", "prediction": "p", "window_end": datetime(2027, 1, 1, tzinfo=UTC)}),
    ("claim", {"part": "grade", "grade": "holds"}),
    ("claim", {"part": "grade", "state": "tombstoned", "grade": None, "content": None}),
    ("intention", {"form": "maintain", "done_when_form": None,
                   "done_when": None, "holds_while_form": "act_event",
                   "holds_while": {}, "drop_when_form": "deadline",
                   "drop_when": {}}),
    ("intention", {"form": "maintain", "done_when_form": None,
                   "done_when": None, "holds_while_form": "untyped",
                   "holds_while": {}, "drop_when_form": "deadline",
                   "drop_when": {}}),
    ("intention", {"done_when_form": "untyped", "deadline": datetime(2027, 1, 1, tzinfo=UTC)}),
    ("intention", {"delivery_mode": "choose_moment", "trigger": {}}),
    ("intention", {"state": "ended", "ending": "achieved",
                   "ended_at": datetime(2026, 1, 1, tzinfo=UTC)}),
], ids=repr)
async def test_a_row_inside_the_closed_rules_is_admitted(engine, kind, over) -> None:
    async with engine.begin() as conn:
        await _write(conn, TABLES[kind], _row(kind, **over))


@pytest.mark.asyncio
async def test_a_revision_against_a_stale_base_is_refused(engine) -> None:
    async with engine.begin() as conn:
        await _write(conn, CLAIM, _row("claim", id="c-0"))
        await _write(conn, CLAIM, _row("claim", id="c-1", replaces="c-0"))
    with pytest.raises(sa.exc.IntegrityError):
        async with engine.begin() as conn:
            await _write(conn, CLAIM, _row("claim", id="c-2", replaces="c-0"))
    async with engine.begin() as conn:
        await _write(conn, CLAIM, _row("claim", id="c-3", replaces="c-1"))
