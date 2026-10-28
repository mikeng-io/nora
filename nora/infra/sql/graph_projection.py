"""Record/Rollup projection, on the source writer's connection and savepoint.

The composition root installs immutable scope data on its engine's execution
options. Standalone stores have no graph option and do no graph work.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.memory_graph import (
    POINTER_KINDS,
    RELATION_RULES,
    Entity,
    Key,
    Relation,
    WorkItem,
    pointer_entity,
    validate_relation,
)
from nora.domain.rollup import episode_key
from nora.infra.sql.memory_graph import enqueue, relation_values, upsert_entity
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_NODE,
    MESSAGE_REACTION,
    MESSAGES,
    PRESENCE_STAYS,
    RELATION,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
    ROLLUP_RESPONSE,
)

CODE_RELATIONS = tuple(
    name for name, rule in RELATION_RULES.items() if rule.layer in ("record", "rollup")
)


@dataclass(frozen=True)
class ProjectionContext:
    rooms: Mapping[str, str]
    members: Mapping[str, frozenset[str] | None]
    produce: bool = True
    metrics: Any = None
    names: Mapping[Key, str] = field(default_factory=dict)

    def _units(self, persons: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            u for u, members in self.members.items() if members is None or set(persons) <= members
        )


def context(conn: AsyncConnection) -> ProjectionContext | None:
    if conn.sync_connection is None:
        return None
    return conn.sync_connection.get_execution_options().get("memory_graph")


def stable_id(*parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(parts, default=str, ensure_ascii=True)))


def instant(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


async def work(
    conn: AsyncConnection,
    source: Key,
    unit: str,
    revisions: object,
    as_of: datetime,
    *,
    historical: bool = False,
) -> None:
    cfg = context(conn)
    if cfg is None or not cfg.produce:
        return
    digest = hashlib.sha256(json.dumps(revisions, sort_keys=True, default=str).encode()).hexdigest()
    await enqueue(
        conn,
        WorkItem(
            stable_id(unit, source, digest), source, unit, digest, as_of, historical=historical
        ),
    )


async def _endpoint(conn: AsyncConnection, key: Key, unit: str) -> Key:
    if key[0] in POINTER_KINDS:
        cfg = context(conn)
        name = cfg.names.get(key, key[1]) if cfg is not None else key[1]
        entity = pointer_entity(key[0], unit, key[1], name)
        await upsert_entity(conn, entity)
        return entity.key
    if key[0] == "place":
        entity = Entity(stable_id(unit, *key), "place", unit, key[1])
        await upsert_entity(conn, entity)
        return entity.key
    # The index calls Register entries `event`; the graph's `event` is an entity.
    return ("event_entry", key[1]) if key[0] == "event" else key


async def write_relation(
    conn: AsyncConnection,
    *,
    owner: str,
    unit: str,
    src: Key,
    rel: str,
    dst: Key,
    persons: tuple[str, ...],
    at: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str | None:
    src = await _endpoint(conn, src, unit)
    dst = await _endpoint(conn, dst, unit)
    rule = RELATION_RULES[rel]
    r = Relation(
        stable_id(owner, unit, src, rel, dst, start, end),
        src,
        rel,
        dst,
        rule.layer,
        unit,
        persons,
        valid_from=start,
        valid_to=end,
        asserted_at=at,
        written_by=owner,
    )
    if validate_relation(r, (), {}) is not None:
        return None
    stmt = insert(RELATION).values(**relation_values(r))
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[RELATION.c.id],
            set_={
                "status": "current",
                "persons": stmt.excluded.persons,
                "asserted_at": stmt.excluded.asserted_at,
            },
        )
    )
    return r.id


async def _scopes(
    conn: AsyncConnection, key: Key, cfg: ProjectionContext, seen: frozenset[Key] = frozenset()
) -> dict[str, tuple[str, ...]]:
    if key in seen:
        return {}
    row = (
        (
            await conn.execute(
                sa.select(MEMORY_NODE).where(
                    MEMORY_NODE.c.kind == key[0], MEMORY_NODE.c.source_id == key[1]
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {}
    if row["exclude_persons"]:
        # The relation port cannot express exclusions yet. No relation from
        # this node may widen its audience to someone the source excludes.
        if cfg.metrics is not None:
            cfg.metrics.inc("nora_memory_graph_projection_total", outcome="excluded")
        return {}
    persons = tuple(row["persons"])
    if row["unit"]:
        return {row["unit"]: persons}
    if row["origin_room"]:
        unit = cfg.rooms.get(row["origin_room"])
        return {unit: persons} if unit else {}
    if persons:
        return dict.fromkeys(cfg._units(persons), persons)
    # Place containment derives scope from the rows referencing the place,
    # never by declaring a place globally visible.
    scopes: dict[str, tuple[str, ...]] = {}
    if key[0] == "place":
        for source in (
            await conn.execute(
                sa.select(MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id).where(
                    MEMORY_EDGE.c.dst_kind == key[0], MEMORY_EDGE.c.dst_id == key[1]
                )
            )
        ).all():
            scopes.update(await _scopes(conn, tuple(source), cfg, seen | {key}))
    return scopes


async def project_source(conn: AsyncConnection, kind: str, sid: str) -> None:
    cfg = context(conn)
    if cfg is None:
        return
    if kind in ("reminder", "decision", "commitment", "thread"):
        await project_register(conn, kind, sid)
        return
    owner = f"project:{kind}:{sid}"
    # Retain previous statements as retired when their source changes or hides.
    await conn.execute(
        sa.update(RELATION)
        .where(RELATION.c.written_by == owner, RELATION.c.status == "current")
        .values(status="retired")
    )
    root = (
        (
            await conn.execute(
                sa.select(MEMORY_NODE).where(
                    MEMORY_NODE.c.kind == kind, MEMORY_NODE.c.source_id == sid
                )
            )
        )
        .mappings()
        .first()
    )
    if kind == "media":
        for media in (
            await conn.execute(sa.select(MEMORY_NODE).where(MEMORY_NODE.c.sha256 == sid))
        ).mappings():
            for unit in await _scopes(conn, (media["kind"], media["source_id"]), cfg):
                await work(
                    conn,
                    ("media", media["source_id"]),
                    unit,
                    [
                        "media",
                        media["source_id"],
                        sid,
                        hashlib.sha256(media["text"].encode()).hexdigest(),
                    ],
                    media["projected_at"],
                )
        return
    if root is None:
        return
    keys = {(kind, sid)}
    for row in (
        await conn.execute(
            sa.select(MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id).where(
                MEMORY_EDGE.c.dst_kind == kind,
                MEMORY_EDGE.c.dst_id == sid,
                MEMORY_EDGE.c.rel == "attached_to",
            )
        )
    ).all():
        keys.add(tuple(row))
    edges: list[Any] = list(
        (
            await conn.execute(
                sa.select(MEMORY_EDGE).where(
                    sa.or_(
                        *(
                            sa.and_(MEMORY_EDGE.c.src_kind == k, MEMORY_EDGE.c.src_id == i)
                            for k, i in keys
                        ),
                        sa.and_(
                            MEMORY_EDGE.c.dst_kind == kind,
                            MEMORY_EDGE.c.dst_id == sid,
                            MEMORY_EDGE.c.rel == "during",
                        ),
                    ),
                    MEMORY_EDGE.c.rel.in_(CODE_RELATIONS),
                )
            )
        ).mappings()
    )
    scopes = await _scopes(conn, (kind, sid), cfg)
    if kind == "message":
        for unit in scopes:
            await message_arrival(conn, sid, unit, root["origin_room"])
    # Column-owned extras absent from the legacy index.
    if kind == "message":
        message = (
            (await conn.execute(sa.select(MESSAGES).where(MESSAGES.c.id == int(sid))))
            .mappings()
            .one()
        )
        from nora.domain.external_files import refs_from_external_files_json

        for position, _ref in enumerate(
            refs_from_external_files_json(message["external_files_json"])
        ):
            link = f"{sid}:{position}"
            edges.extend(
                [
                    {
                        "src_kind": "link",
                        "src_id": link,
                        "rel": "attached_to",
                        "dst_kind": "message",
                        "dst_id": sid,
                    },
                    {
                        "src_kind": "link",
                        "src_id": link,
                        "rel": "in_room",
                        "dst_kind": "room",
                        "dst_id": root["origin_room"],
                    },
                ]
            )
            if message["person_id"]:
                edges.append(
                    {
                        "src_kind": "link",
                        "src_id": link,
                        "rel": "shared_by",
                        "dst_kind": "person",
                        "dst_id": message["person_id"],
                    }
                )
        if message["is_nora"]:
            edges.append(
                {
                    "src_kind": "send",
                    "src_id": sid,
                    "rel": "authored_by",
                    "dst_kind": "nora",
                    "dst_id": "nora",
                }
            )
        if message["quoted_ts"] is not None:
            parents = (
                (
                    await conn.execute(
                        sa.select(MESSAGES.c.id).where(
                            MESSAGES.c.platform == message["platform"],
                            MESSAGES.c.conversation_id == message["conversation_id"],
                            MESSAGES.c.ts == message["quoted_ts"],
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(parents) == 1:
                edges.append(
                    {
                        "src_kind": "message",
                        "src_id": sid,
                        "rel": "reply_to",
                        "dst_kind": "message",
                        "dst_id": str(parents[0]),
                    }
                )
        for reaction in (
            await conn.execute(
                sa.select(MESSAGE_REACTION).where(MESSAGE_REACTION.c.message_id == int(sid))
            )
        ).mappings():
            edges.append(
                {
                    "src_kind": "reaction",
                    "src_id": str(reaction["id"]),
                    "rel": "reacted_to",
                    "dst_kind": "message",
                    "dst_id": sid,
                }
            )
    for edge in edges:
        if edge["rel"] == "during":
            continue  # The Rollup writer owns interval relations, including late arrivals.
        for unit, persons in scopes.items():
            await write_relation(
                conn,
                owner=owner,
                unit=unit,
                src=(edge["src_kind"], edge["src_id"]),
                rel=edge["rel"],
                dst=(edge["dst_kind"], edge["dst_id"]),
                persons=persons,
                at=instant(root["at_ms"]) if root["at_ms"] is not None else root["projected_at"],
            )
    # A venue's containment is written beside the row that supplied its area.
    place_edges = (
        (
            await conn.execute(
                sa.select(MEMORY_EDGE).where(
                    MEMORY_EDGE.c.src_kind == kind,
                    MEMORY_EDGE.c.src_id == sid,
                    MEMORY_EDGE.c.dst_kind == "place",
                )
            )
        )
        .mappings()
        .all()
    )
    for edge in place_edges:
        if edge["dst_kind"] == "place":
            target = edge["dst_id"]
            for area in (
                await conn.execute(
                    sa.select(MEMORY_EDGE).where(
                        MEMORY_EDGE.c.src_kind == "place",
                        MEMORY_EDGE.c.src_id == target,
                        MEMORY_EDGE.c.rel == "located_in",
                    )
                )
            ).mappings():
                for unit, persons in scopes.items():
                    await write_relation(
                        conn,
                        owner=owner,
                        unit=unit,
                        src=("place", target),
                        rel="located_in",
                        dst=("place", area["dst_id"]),
                        persons=persons,
                        at=(
                            instant(root["at_ms"])
                            if root["at_ms"] is not None
                            else root["projected_at"]
                        ),
                    )
    if kind in ("note", "memo", "event", "lesson", "profile_line", "social"):
        for unit in scopes:
            # Identity + source revision, distinct from phase 2's rendered input.
            revision = await source_revision(conn, kind, sid)
            await work(
                conn,
                ("event_entry" if kind == "event" else kind, sid),
                unit,
                [kind, sid, revision, root["state"]],
                instant(root["at_ms"]) if root["at_ms"] is not None else root["projected_at"],
            )


async def project_rollups(conn: AsyncConnection) -> None:
    cfg = context(conn)
    if cfg is None:
        return
    build = (
        await conn.execute(sa.select(ROLLUP_BUILD.c.id).where(ROLLUP_BUILD.c.status == "current"))
    ).scalar_one_or_none()
    if build is None:
        return
    owner = "project:rollups"
    await conn.execute(
        sa.update(RELATION)
        .where(RELATION.c.written_by == owner, RELATION.c.status == "current")
        .values(status="retired")
    )
    # No coordinates or private saved-place names are placed on an entity.
    for r in (
        await conn.execute(sa.select(PRESENCE_STAYS).where(PRESENCE_STAYS.c.build_id == build))
    ).mappings():
        if not r["address"]:
            continue
        for unit in cfg._units((r["subject_id"],)):
            key = episode_key(r["subject_id"], r["started_ts"], r["ended_ts"])
            await during(
                conn,
                owner,
                unit,
                ("stay", key),
                (r["subject_id"],),
                r["started_ts"],
                r["ended_ts"],
                r["ended_ts"],
            )
            await write_relation(
                conn,
                owner=owner,
                unit=unit,
                src=("person", r["subject_id"]),
                rel="stayed_at",
                dst=("place", r["address"]),
                persons=(r["subject_id"],),
                at=instant(r["ended_ts"]),
                start=instant(r["started_ts"]),
                end=instant(r["ended_ts"]),
            )
    for r in (
        await conn.execute(
            sa.select(ROLLUP_COLOCATION).where(ROLLUP_COLOCATION.c.build_id == build)
        )
    ).mappings():
        persons = tuple(r["persons"])
        key = episode_key(f"{r['unit']}|{','.join(persons)}", r["valid_from_ms"], r["valid_to_ms"])
        await during(
            conn,
            owner,
            r["unit"],
            ("co_location", key),
            persons,
            r["valid_from_ms"],
            r["valid_to_ms"],
            r["as_of_ms"],
        )
        for a, b in combinations(persons, 2):
            await write_relation(
                conn,
                owner=owner,
                unit=r["unit"],
                src=("person", a),
                rel="co_located_with",
                dst=("person", b),
                persons=persons,
                at=instant(r["as_of_ms"]),
                start=instant(r["valid_from_ms"]),
                end=instant(r["valid_to_ms"]),
            )
        if not r["open"]:
            await work(
                conn,
                ("co_location", key),
                r["unit"],
                ["co_location", key, r["as_of_ms"]],
                instant(r["as_of_ms"]),
            )
    from nora.infra.sql.schema import ROLLUP_OUTING

    for r in (
        await conn.execute(sa.select(ROLLUP_OUTING).where(ROLLUP_OUTING.c.build_id == build))
    ).mappings():
        persons = tuple(r["persons"])
        key = episode_key(f"{r['unit']}|{','.join(persons)}", r["valid_from_ms"], r["valid_to_ms"])
        await during(
            conn,
            owner,
            r["unit"],
            ("outing", key),
            persons,
            r["valid_from_ms"],
            r["valid_to_ms"],
            r["as_of_ms"],
        )
        if not r["open"]:
            await work(
                conn,
                ("outing", key),
                r["unit"],
                ["outing", key, r["as_of_ms"]],
                instant(r["as_of_ms"]),
            )
    for r in (
        await conn.execute(sa.select(ROLLUP_RESPONSE).where(ROLLUP_RESPONSE.c.build_id == build))
    ).mappings():
        await write_relation(
            conn,
            owner=owner,
            unit=r["unit"],
            src=("episode", str(r["message_id"])),
            rel="outcome_of",
            dst=("send", str(r["message_id"])),
            persons=tuple(r["named_persons"]),
            at=instant(r["as_of_ms"]),
            start=instant(r["valid_from_ms"]),
            end=instant(r["valid_to_ms"]),
        )
        if r["outcome"] != "open":
            await work(
                conn,
                ("episode", str(r["message_id"])),
                r["unit"],
                ["episode", r["message_id"], r["as_of_ms"], r["outcome"]],
                instant(r["as_of_ms"]),
            )


async def message_arrival(conn: AsyncConnection, sid: str, unit: str, room: str) -> None:
    from nora.infra.sql.schema import MEMORY_GRAPH_ARRIVAL, MESSAGE_EDIT

    cfg = context(conn)
    if cfg is None or not cfg.produce:
        return
    message = (
        (await conn.execute(sa.select(MESSAGES).where(MESSAGES.c.id == int(sid)))).mappings().one()
    )
    if message["is_nora"]:
        return  # As in extraction, her sends are context rather than new human sources.
    edit = (
        await conn.execute(
            sa.select(sa.func.max(MESSAGE_EDIT.c.id)).where(MESSAGE_EDIT.c.message_id == int(sid))
        )
    ).scalar_one()
    revision = hashlib.sha256(
        json.dumps([edit, message["person_id"], message["delivery_status"]], default=str).encode()
    ).hexdigest()
    # A not-yet-batched older revision is replaced, never rendered twice in
    # one batch. Already produced work remains retained with its original hash.
    await conn.execute(
        sa.update(MEMORY_GRAPH_ARRIVAL)
        .where(
            MEMORY_GRAPH_ARRIVAL.c.unit == unit,
            MEMORY_GRAPH_ARRIVAL.c.message_id == int(sid),
            MEMORY_GRAPH_ARRIVAL.c.revision != revision,
            MEMORY_GRAPH_ARRIVAL.c.batched.is_(False),
        )
        .values(batched=True)
    )
    await conn.execute(
        insert(MEMORY_GRAPH_ARRIVAL)
        .values(
            unit=unit,
            message_id=int(sid),
            revision=revision,
            room=room,
        )
        .on_conflict_do_nothing()
    )


async def form_batch(conn: AsyncConnection, *, now: datetime) -> int:
    """Parallel to extraction: 20 sources maximum, oldest ready after two seconds.

    This only produces durable work; it never claims/consumes graph work items.
    """
    from datetime import timedelta

    from nora.infra.sql.schema import MEMORY_GRAPH_ARRIVAL as ARRIVAL

    await conn.execute(sa.text("SELECT pg_advisory_xact_lock(1290082)"))
    await conn.execute(
        sa.update(ARRIVAL)
        .where(
            ARRIVAL.c.batched.is_(False),
            ~sa.exists().where(
                MESSAGES.c.id == ARRIVAL.c.message_id, MESSAGES.c.hidden_at.is_(None)
            ),
        )
        .values(batched=True)
    )
    first = (
        (
            await conn.execute(
                sa.select(ARRIVAL)
                .where(
                    ARRIVAL.c.batched.is_(False),
                    ARRIVAL.c.queued_at <= now - timedelta(seconds=2),
                )
                .order_by(ARRIVAL.c.queued_at, ARRIVAL.c.message_id)
                .limit(1)
            )
        )
        .mappings()
        .first()
    )
    if first is None:
        return 0
    rows = (
        (
            await conn.execute(
                sa.select(ARRIVAL)
                .where(
                    ARRIVAL.c.batched.is_(False),
                    ARRIVAL.c.unit == first["unit"],
                    ARRIVAL.c.room == first["room"],
                )
                .order_by(ARRIVAL.c.queued_at, ARRIVAL.c.message_id)
                .limit(20)
            )
        )
        .mappings()
        .all()
    )
    identities = [(r["message_id"], r["revision"]) for r in rows]
    ids = [r["message_id"] for r in rows]
    as_of = (
        await conn.execute(sa.select(sa.func.max(MESSAGES.c.ts)).where(MESSAGES.c.id.in_(ids)))
    ).scalar_one()
    await work(conn, ("message_batch", json.dumps(ids)), first["unit"], identities, instant(as_of))
    for row in rows:
        await conn.execute(
            sa.update(ARRIVAL)
            .where(
                ARRIVAL.c.unit == row["unit"],
                ARRIVAL.c.message_id == row["message_id"],
                ARRIVAL.c.revision == row["revision"],
            )
            .values(batched=True)
        )
    return len(rows)


async def source_revision(conn: AsyncConnection, kind: str, sid: str) -> object:
    from nora.infra.sql import schema

    if kind in ("note", "event"):
        table = schema.NOTE if kind == "note" else schema.EVENTS
        return (
            await conn.execute(sa.select(table.c.head_revision_id).where(table.c.id == sid))
        ).scalar_one_or_none()
    if kind in ("memo", "lesson"):
        table = schema.MEMO_REVISION if kind == "memo" else schema.LESSON_REVISION
        owner = table.c.memo_id if kind == "memo" else table.c.lesson_id
        return (
            await conn.execute(
                sa.select(table.c.id).where(owner == sid).order_by(table.c.seq.desc()).limit(1)
            )
        ).scalar_one_or_none()
    if kind == "profile_line":
        return sid
    network, _, item = sid.partition(":")
    state = (
        await conn.execute(
            sa.select(schema.SOCIAL_ITEM_STATE.c.id)
            .where(
                schema.SOCIAL_ITEM_STATE.c.network == network,
                schema.SOCIAL_ITEM_STATE.c.item_id == item,
            )
            .order_by(
                schema.SOCIAL_ITEM_STATE.c.recorded_at.desc(), schema.SOCIAL_ITEM_STATE.c.id.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return (sid, state)


async def project_register(conn: AsyncConnection, kind: str, sid: str) -> None:
    from uuid import UUID

    from nora.infra.sql.schema import LEDGER_COMMITMENT, LEDGER_DECISION, LEDGER_THREAD, REMINDERS

    cfg = context(conn)
    if cfg is None:
        return
    if kind == "reminder":
        row = (
            (await conn.execute(sa.select(REMINDERS).where(REMINDERS.c.id == UUID(sid))))
            .mappings()
            .first()
        )
        if row is None:
            return
        unit = cfg.rooms.get(f"{row['platform']}:{row['conversation_id']}")
        if unit is not None:
            await work(
                conn,
                (kind, sid),
                unit,
                [sid, row["state"], row["fired_at"], row["outcome"]],
                row["fired_at"] or row["created_at"],
            )
        return
    table = {"decision": LEDGER_DECISION, "commitment": LEDGER_COMMITMENT, "thread": LEDGER_THREAD}[
        kind
    ]
    row = (await conn.execute(sa.select(table).where(table.c.id == sid))).mappings().first()
    if row is not None:
        await work(conn, (kind, sid), row["unit"], [sid, row["revision"]], instant(row["as_of"]))


async def register_arrival(
    conn: AsyncConnection, kind: str, sid: object, *, metrics: Any = None
) -> None:
    if context(conn) is not None:
        from nora.infra.sql import memory_index

        # Use the source projection's savepoint, retry queue and counter. These
        # Register kinds have no search-index node; this only produces work.
        await memory_index.project(conn, kind, str(sid), metrics=metrics)


async def during(
    conn: AsyncConnection,
    owner: str,
    unit: str,
    episode: Key,
    persons: tuple[str, ...],
    start: int,
    end: int,
    at: int,
) -> None:
    cfg = context(conn)
    if cfg is None:
        return
    rooms = [room.partition(":")[::2] for room, u in cfg.rooms.items() if u == unit]
    if not rooms:
        return
    rows = (
        await conn.execute(
            sa.select(MESSAGES.c.id).where(
                MESSAGES.c.person_id.in_(persons),
                MESSAGES.c.hidden_at.is_(None),
                MESSAGES.c.ts >= start,
                MESSAGES.c.ts < end,
                sa.or_(
                    *(
                        sa.and_(MESSAGES.c.platform == platform, MESSAGES.c.conversation_id == room)
                        for platform, room in rooms
                    )
                ),
            )
        )
    ).scalars()
    for mid in rows:
        keys = [("message", str(mid))]
        keys.extend(
            tuple(r)
            for r in (
                await conn.execute(
                    sa.select(MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id).where(
                        MEMORY_EDGE.c.dst_kind == "message",
                        MEMORY_EDGE.c.dst_id == str(mid),
                        MEMORY_EDGE.c.rel == "attached_to",
                        MEMORY_EDGE.c.src_kind == "media",
                    )
                )
            ).all()
        )
        for key in keys:
            await write_relation(
                conn,
                owner=owner,
                unit=unit,
                src=key,
                rel="during",
                dst=episode,
                persons=persons,
                at=instant(at),
                start=instant(start),
                end=instant(end),
            )
