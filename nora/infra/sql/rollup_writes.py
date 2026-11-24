"""Persist the computed rows in a unit's current Rollup build."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from nora.domain.rollup import Colocation, Daybook, Outing, Response, Tie, episode_key, lineage_json
from nora.infra.sql.aggregate_writes import AggregateWrites, rollup_key


def plain(value: Any) -> Any:
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


def _common(row: Any) -> dict[str, Any]:
    return {
        "unit": row.unit, "build_lineage": lineage_json(row.lineage),
        "valid_from_ms": row.valid_from_ms, "valid_to_ms": row.valid_to_ms,
        "as_of_ms": row.as_of_ms,
    }


def colocation_values(c: Colocation) -> dict[str, Any]:
    return {
        **_common(c), "persons": list(c.persons), "lat": c.lat, "lng": c.lng,
        "radius_m": c.radius_m, "place_label": c.place_label,
        "saved_place_id": c.saved_place_id, "at_home_of": list(c.at_home_of),
        "homes_unknown": list(c.homes_unknown), "stay_count": c.stay_count,
        "open": int(c.open),
    }


def outing_values(o: Outing) -> dict[str, Any]:
    return {
        **_common(o), "persons": list(o.persons), "lat": o.lat, "lng": o.lng,
        "place_label": o.place_label, "saved_place_id": o.saved_place_id,
        "open": int(o.open), "district": o.district, "city": o.city,
    }


def daybook_values(d: Daybook) -> dict[str, Any]:
    return {
        **_common(d), "person_id": d.person_id, "local_date": d.local_date,
        "stays": list(d.stays), "stay_ms": d.stay_ms, "home_ms": d.home_ms,
        "colocation_ms": d.colocation_ms, "with_persons": list(d.with_persons),
        "outings": d.outings, "rooms": dict(d.rooms), "messages": d.messages,
        "reactions": d.reactions, "items_posted": d.items_posted,
    }


def tie_values(t: Tie) -> dict[str, Any]:
    return {
        **_common(t), "person_a": t.person_a, "person_b": t.person_b,
        "iso_week": t.iso_week, "replies_ab": t.replies_ab, "replies_ba": t.replies_ba,
        "mentions_ab": t.mentions_ab, "mentions_ba": t.mentions_ba,
        "reactions_ab": t.reactions_ab, "reactions_ba": t.reactions_ba,
        "colocation_ms": t.colocation_ms,
    }


def response_values(r: Response) -> dict[str, Any]:
    return {
        **_common(r), "message_id": r.message_id, "platform": r.platform,
        "conversation_id": r.conversation_id, "sent_ts": r.sent_ts,
        "replies": r.replies, "reactions": r.reactions, "edits": r.edits,
        "follows": r.follows, "responders": list(r.responders),
        "named_persons": list(r.named_persons),
        "intent": r.intent, "outcome": r.outcome,
    }


def _key_of(row: dict[str, Any], key: Sequence[str]) -> tuple[Any, ...]:
    return tuple(tuple(row[k]) if isinstance(row[k], list) else row[k] for k in key)


def outing_key(row: dict[str, Any]) -> str:
    """An outing row's memory-index key: its citation, `unit|persons` and its
    interval (`rollup_read`'s subject for it)."""
    subject = f"{row['unit']}|{','.join(row['persons'])}"
    return episode_key(subject, row["valid_from_ms"], row["valid_to_ms"])


async def diff_apply(
    conn: Any,
    table: sa.Table,
    computed: AggregateWrites,
    *,
    build_id: int,
    unit: str,
    key: Sequence[str],
    rows: Sequence[dict[str, Any]],
    touched: list[dict[str, Any]] | None = None,
) -> int:
    key = (*key, *[name for name in ("valid_from_ms", "valid_to_ms") if name not in key])
    stored = {
        _key_of(dict(r), key): dict(r)
        for r in (
            await conn.execute(
                sa.select(table).where(
                    table.c.build_id == build_id,
                    table.c.unit == unit,
                    table.c.state == "live",
                )
            )
        )
        .mappings()
        .all()
    }
    wanted = {_key_of(r, key): r for r in rows}
    doomed = [row for k, row in stored.items() if k not in wanted]
    await computed.retire(conn, table, doomed)
    if touched is not None:
        touched.extend(doomed)
    changed = len(doomed)
    for k, row in wanted.items():
        prior = stored.get(k)
        inputs = await computed.rollup_inputs(conn, table, row, build_id)
        planes = await computed.annotate(
            conn,
            table,
            row,
            key=rollup_key(table, row),
            unit=unit,
            inputs=inputs,
            asserted_at=prior["recorded_at"] if prior else None,
        )
        values = {**row, **planes}
        if prior is not None and all(
            plain(prior[column]) == plain(value) for column, value in values.items()
        ):
            continue
        if prior is None:
            await conn.execute(sa.insert(table).values(build_id=build_id, **values))
        else:
            await conn.execute(sa.update(table).where(table.c.id == prior["id"]).values(**values))
            if touched is not None:
                touched.append(prior)
        changed += 1
        if touched is not None:
            touched.append(values)
    return changed
