"""Step one's frozen Record input, retained pages and append-only shadow receipts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.comprehend import ComprehendItem
from nora.domain.memory_graph import Admission, Evidence, Key, Layer, Source, WorkItem
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.infra.sql.extraction import link_replies, source_rows
from nora.infra.sql.schema import (
    COMPREHEND_RESULT as RESULT,
)
from nora.infra.sql.schema import (
    COMPREHEND_WEB_PAGE as PAGE,
)
from nora.infra.sql.schema import (
    EXTRACTION,
    MEMORY_EDGE,
    MEMORY_NODE,
    MESSAGES,
    ROLLUP_RESPONSE,
    SOCIAL_ITEM,
    SOCIAL_ITEM_COUNT,
)

READING_VERSION = 2  # Cognition step one; version one is the single-pass reader.


class SqlComprehendRecords:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        media_join: Any,
        rooms_by_unit: Mapping[str, list[tuple[str, str]]],
        persons_by_unit: Mapping[str, set[str]],
        location_units: frozenset[str] = frozenset(),
        can_release_place: Any = None,
        social_off_ids: frozenset[str] = frozenset(),
        model_id: str = "cognition-runtime",
    ) -> None:
        self.engine, self.media_join = engine, media_join
        self.rooms_by_unit, self.persons_by_unit = rooms_by_unit, persons_by_unit
        self.location_units = location_units
        self.can_release_place = can_release_place
        self.social_off_ids = social_off_ids
        self.model_id = model_id

    async def act_result(self, key: str, session: int, result: Any = None) -> Any:
        async with self.engine.begin() as conn:
            if result is not None:
                await conn.execute(sa.insert(RESULT).values(work_id=key, input_hash="",
                    session_id=session, part="act_result", payload=result))
            return (await conn.execute(sa.select(RESULT.c.payload).where(
                RESULT.c.work_id == key, RESULT.c.part == "act_result")
                .order_by(RESULT.c.id).limit(1))).scalar_one_or_none()

    async def failures(self, unit: str, sid: int, *, reset: bool = False) -> int:
        key = f"stimulus:{unit}:{sid}"
        async with self.engine.begin() as conn:
            if reset:
                await conn.execute(sa.insert(RESULT).values(work_id=key, input_hash="",
                    session_id=0, part="stimulus_requeue", payload={}))
            reset_id = sa.select(sa.func.coalesce(sa.func.max(RESULT.c.id), 0)).where(
                RESULT.c.work_id == key, RESULT.c.part == "stimulus_requeue").scalar_subquery()
            return int((await conn.execute(sa.select(sa.func.count()).select_from(RESULT)
                .where(RESULT.c.work_id == key, RESULT.c.part == "stimulus_failure",
                       RESULT.c.id > reset_id))).scalar_one())

    async def fail_stimulus(self, unit: str, sid: int, session: int | None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.insert(RESULT).values(work_id=f"stimulus:{unit}:{sid}",
                input_hash="", session_id=session or 0, part="stimulus_failure",
                payload={"state": "failed"}))

    async def load(self, work: WorkItem) -> ComprehendItem:
        """A full source with its owned media, reply and cited Record rows.

        Projection text is the canonical per-kind rendering, not a search hit or
        truncated snippet. Query by exact source key AND unit before expanding.
        """
        kind, ref = work.source
        if kind == "message_batch":
            ids = json.loads(ref)
            if not isinstance(ids, list) or not ids or len(ids) > 20 or any(
                type(i) is not int for i in ids
            ):
                raise ValueError("invalid message batch")
            items = [await self.load(replace(work, source=("message", str(i)))) for i in ids]
            batch_sources = {s.key: s for item in items for s in item.sources}
            snapshot: dict[str, Any] = {
                lane: list({r["id"]: r for item in items
                            for r in item.snapshot[lane]}.values())
                for lane in ("messages", "context")
            }
            snapshot["source_keys"] = {str(i): ["message", str(i)] for i in ids}
            return replace(items[0], work=work, sources=tuple(batch_sources.values()),
                           mentions=tuple(dict.fromkeys(p for item in items
                               for p in (item.author, *item.mentions) if p)), snapshot=snapshot)
        if kind == "episode":
            return await self._response(work)
        # event is the old index spelling for a Register event entry.
        index_kind = "event" if kind == "event_entry" else kind
        async with self.engine.begin() as conn:
            node = (
                (
                    await conn.execute(
                        sa.select(MEMORY_NODE).where(
                            MEMORY_NODE.c.kind == index_kind,
                            MEMORY_NODE.c.source_id == ref,
                        )
                    )
                )
                .mappings()
                .one()
            )
            if kind in ("social", "social_media", "social_location") and (
                set(node["persons"]) & self.social_off_ids
            ):
                raise ValueError("social consent withdrawn")
            room_key = node["origin_room"] or ""
            room_keys = {f"{p}:{r}" for p, r in self.rooms_by_unit.get(work.unit, [])}
            if node["unit"] not in (None, work.unit):
                raise ValueError("source outside unit")
            if node["unit"] is None and not (
                room_key in room_keys
                or (
                    node["persons"]
                    and set(node["persons"]) <= self.persons_by_unit.get(work.unit, set())
                )
            ):
                raise ValueError("unscoped source")
            if not room_key and self.rooms_by_unit.get(work.unit):
                platform, room = self.rooms_by_unit[work.unit][0]
                room_key = f"{platform}:{room}"
            related = (
                (
                    await conn.execute(
                        sa.select(MEMORY_EDGE).where(
                            sa.or_(
                                sa.and_(
                                    MEMORY_EDGE.c.src_kind == index_kind,
                                    MEMORY_EDGE.c.src_id == ref,
                                    MEMORY_EDGE.c.rel.in_(["cites", "in_room", "authored_by"]),
                                ),
                                sa.and_(
                                    MEMORY_EDGE.c.dst_kind == index_kind,
                                    MEMORY_EDGE.c.dst_id == ref,
                                    MEMORY_EDGE.c.rel == "attached_to",
                                ),
                            ),
                        )
                    )
                )
                .mappings()
                .all()
            )
            keys = {
                (r["src_kind"], r["src_id"])
                if r["rel"] == "attached_to"
                else (r["dst_kind"], r["dst_id"])
                for r in related
            }
            rooms = [r["dst_id"] for r in related if r["rel"] == "in_room"]
            if rooms:
                room_key = rooms[0]
            platform, _, room = room_key.partition(":")
            if not room:
                raise ValueError("work item has no scoped read room")
            author = next((r["dst_id"] for r in related if r["rel"] == "authored_by"), "")
            source_ids = [int(k[1]) for k in keys if k[0] == "message" and k[1].isdigit()]
            if kind == "message":
                source_ids.append(int(ref))
            messages = await source_rows(
                conn, source_ids, include_reply=True, as_of_ms=int(work.as_of.timestamp() * 1000)
            )
            context: list[dict[str, Any]] = []
            reply_parent = None
            if kind == "message":
                own = (
                    (
                        await conn.execute(
                            sa.select(MESSAGES).where(
                                MESSAGES.c.id == int(ref),
                                MESSAGES.c.platform == platform,
                                MESSAGES.c.conversation_id == room,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                author = own["person_id"] or ""
                if own["quoted_ts"] is not None:
                    parent_ids = list(
                        (
                            await conn.execute(
                                sa.select(MESSAGES.c.id).where(
                                    MESSAGES.c.platform == platform,
                                    MESSAGES.c.conversation_id == room,
                                    MESSAGES.c.ts == own["quoted_ts"],
                                )
                            )
                        ).scalars()
                    )
                    if len(parent_ids) == 1:
                        context = await source_rows(
                            conn, parent_ids, as_of_ms=int(work.as_of.timestamp() * 1000)
                        )
                        reply_parent = ("message", str(parent_ids[0]))
            for row in messages + context:
                raw = (
                    (await conn.execute(sa.select(MESSAGES).where(MESSAGES.c.id == row["id"])))
                    .mappings()
                    .one()
                )
                if f"{raw['platform']}:{raw['conversation_id']}" not in room_keys:
                    raise ValueError("cited source outside unit")
                joined = await self.media_join.enrich(
                    (
                        TranscriptRow(
                            row["id"],
                            "",
                            None,
                            row["ts"],
                            row["body"],
                            row["is_nora"],
                            DeliveryStatus.SENT,
                            None,
                            media_json=raw["media_json"],
                        ),
                    ),
                    path="dreamer",
                )
                row["body"] = joined[0].body or ""
                row["source_persons"] = tuple((await conn.execute(
                    sa.select(MEMORY_NODE.c.persons).where(
                        MEMORY_NODE.c.kind == "message", MEMORY_NODE.c.source_id == str(row["id"])
                    )
                )).scalar_one())
            link_replies(messages, context)
            others = []
            for key in keys:
                if key[0] == "social_location":
                    permitted = work.unit in self.location_units
                    if self.can_release_place is not None:
                        permitted = bool(node["persons"]) and all(
                            [await self.can_release_place(work.unit, p) for p in node["persons"]]
                        )
                    if not permitted:
                        continue
                if key[0] in ("person", "room", "unit", "nora", "message"):
                    continue
                # Child nodes inherit their parent's scope. They are reached only
                # by the exact owned carries/keyed_with/cites edge, never by a body handle.
                child = (
                    (
                        await conn.execute(
                            sa.select(MEMORY_NODE).where(
                                MEMORY_NODE.c.kind == key[0],
                                MEMORY_NODE.c.source_id == key[1],
                                sa.or_(
                                    MEMORY_NODE.c.unit == work.unit, MEMORY_NODE.c.unit.is_(None)
                                ),
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if child:
                    others.append(
                        Source(key, "record", work.unit, tuple(child["persons"]), child["text"])
                    )
            sources = [
                Source(
                    ("message", str(r["id"])),
                    "record",
                    work.unit,
                    r["source_persons"],
                    r["body"],
                )
                for r in messages + context
            ]
            if kind != "message":
                layer: Layer = (
                    "notebook"
                    if kind in ("note", "memo", "event_entry")
                    else (
                        "rollup"
                        if kind in ("stay", "trip", "outing", "episode", "co_location")
                        else "record"
                    )
                )
                sources.insert(
                    0, Source(work.source, layer, work.unit, tuple(node["persons"]), node["text"])
                )
            sources.extend(others)
            # Claims from social captions and media readings use local numeric slots,
            # mapped back to real source keys in the persisted claim content.
            claim_rows = list(messages)
            source_keys = {str(r["id"]): ["message", str(r["id"])] for r in messages}
            next_id = max([r["id"] for r in messages + context] + [0]) + 1
            for source in sources:
                if source.key[0] == "message" or source.layer != "record":
                    continue
                claim_rows.append(
                    {
                        "id": next_id,
                        "person_id": author or None,
                        "ts": int(work.as_of.timestamp() * 1000),
                        "body": source.text,
                        "mentions": [],
                    }
                )
                source_keys[str(next_id)] = list(source.key)
                next_id += 1
            accounts: tuple[str, ...] = ()
            metadata: dict[str, Any] = {}
            if kind == "social":
                from nora.infra.sql.memory_index import _hidden

                network, _, item_id = ref.partition(":")
                social = (
                    (
                        await conn.execute(
                            sa.select(SOCIAL_ITEM).where(
                                SOCIAL_ITEM.c.network == network,
                                SOCIAL_ITEM.c.item_id == item_id,
                                ~_hidden(SOCIAL_ITEM),
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                accounts = (social["account"],)
                counts = (
                    (
                        await conn.execute(
                            sa.select(SOCIAL_ITEM_COUNT)
                            .where(
                                SOCIAL_ITEM_COUNT.c.network == network,
                                SOCIAL_ITEM_COUNT.c.item_id == item_id,
                                SOCIAL_ITEM_COUNT.c.recorded_at <= work.as_of,
                            )
                            .order_by(SOCIAL_ITEM_COUNT.c.recorded_at.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
                metadata = {
                    "network": network,
                    "account": social["account"],
                    "kind": social["kind"],
                    "taken_at": social["taken_at"],
                    "counts_at_read": dict(counts) if counts else None,
                }
            text = "\n".join(s.text for s in sources)
            urls = tuple(re.findall(r"https?://[^\s<>\"']+", text))
            # Pages already held for these links are shown with their actual fetch
            # time. History never sees a cached page fetched after its own clock.
            cached = (
                (
                    await conn.execute(
                        sa.select(PAGE)
                        .where(
                            PAGE.c.unit == work.unit,
                            PAGE.c.url.in_(urls),
                            PAGE.c.fetched_at <= work.as_of,
                        )
                        .order_by(PAGE.c.url, PAGE.c.fetched_at.desc())
                        .distinct(PAGE.c.url)
                    )
                )
                .mappings()
                .all()
            )
            metadata["pages"] = [
                {
                    "key": ["web_page", p["id"]],
                    "url": p["url"],
                    "fetched_at": p["fetched_at"],
                    "content_hash": p["content_hash"],
                }
                for p in cached
            ]
            sources.extend(
                Source(("web_page", p["id"]), "record", work.unit, text=p["text"]) for p in cached
            )
            return ComprehendItem(
                work,
                tuple(sources),
                platform,
                room,
                author,
                tuple(p for r in messages for p in r["mentions"]),
                reply_parent,
                urls,
                accounts=accounts,
                place=next((s.text for s in others if s.key[0] == "social_location"), ""),
                snapshot={
                    "messages": claim_rows,
                    "context": context,
                    "source_keys": source_keys,
                    "metadata": json.loads(json.dumps(metadata, default=str)),
                },
            )

    async def _response(self, work: WorkItem) -> ComprehendItem:
        """The existing Lessons citation key is response:<send id>."""
        from nora.infra.sql.mobility import current_build_id

        ref = work.source[1].removeprefix("response:")
        if not ref.isdigit():
            raise ValueError("unknown episode source key")
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        sa.select(ROLLUP_RESPONSE).where(
                            ROLLUP_RESPONSE.c.build_id == current_build_id(),
                            ROLLUP_RESPONSE.c.unit == work.unit,
                            ROLLUP_RESPONSE.c.message_id == int(ref),
                        )
                    )
                )
                .mappings()
                .one()
            )
            if (row["platform"], row["conversation_id"]) not in self.rooms_by_unit.get(
                work.unit, []
            ):
                raise ValueError("response outside unit")
            closed = row["outcome"] not in ("open", "unknown")
            reply_ids = list(
                (
                    await conn.execute(
                        sa.select(MESSAGES.c.id).where(
                            MESSAGES.c.platform == row["platform"],
                            MESSAGES.c.conversation_id == row["conversation_id"],
                            MESSAGES.c.quoted_ts == row["sent_ts"],
                            MESSAGES.c.ts >= row["sent_ts"],
                            MESSAGES.c.ts <= row["valid_to_ms"],
                        )
                    )
                ).scalars()
            )
        base = await self.load(replace(work, source=("message", ref)))
        sources = list(base.sources)
        messages = list(base.snapshot["messages"])
        for reply in reply_ids:
            read = await self.load(replace(work, source=("message", str(reply))))
            sources.extend(s for s in read.sources if s.key not in {v.key for v in sources})
            messages.extend(
                r for r in read.snapshot["messages"] if r["id"] not in {m["id"] for m in messages}
            )
        snapshot = {
            **base.snapshot,
            "messages": messages,
            "metadata": json.loads(json.dumps(dict(row), default=str)),
        }
        sources.append(
            Source(work.source, "rollup", work.unit, text=json.dumps(dict(row), default=str))
        )
        return replace(
            base, work=work, sources=tuple(sources), snapshot=snapshot, response_closed=closed
        )

    async def receipt(
        self, work: WorkItem, session: int, part: str, payload: dict[str, Any]
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.insert(RESULT).values(
                    work_id=work.id,
                    input_hash=work.input_hash,
                    session_id=session,
                    part=part,
                    payload=payload,
                )
            )

    async def record_refusal(self, session: int, part: str, reason: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.insert(RESULT).values(
                    work_id="",
                    input_hash="",
                    session_id=session,
                    part="refusal",
                    payload={"part": part, "reason": reason},
                )
            )

    async def web_page(
        self,
        *,
        unit: str,
        session_id: int,
        url: str,
        fetched_at: datetime,
        text: str,
        tool_call: dict[str, Any],
    ) -> Key:
        ref = str(uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.insert(PAGE).values(
                    id=ref,
                    unit=unit,
                    session_id=session_id,
                    url=url,
                    fetched_at=fetched_at,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    text=text,
                    tool_call=tool_call,
                )
            )
        return "web_page", ref

    async def readings(
        self, work: WorkItem, session: int, item: ComprehendItem, claims: list[dict[str, Any]]
    ) -> None:
        from nora.infra.sql.extraction import write_claims

        async with self.engine.begin() as conn:
            # Transaction lock is per work/hash, not over a model await.
            await conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": work.id + work.input_hash},
            )
            if (
                await conn.execute(
                    sa.select(RESULT.c.id).where(
                        RESULT.c.work_id == work.id,
                        RESULT.c.input_hash == work.input_hash,
                        RESULT.c.part == "readings",
                    )
                )
            ).first():
                return
            source_keys = item.snapshot.get("source_keys", {})
            contents = [
                {
                    **c,
                    "source_keys": source_keys,
                    "writer": "cognition:comprehend",
                    "session_id": session,
                }
                for c in claims
            ]
            extraction = (
                await conn.execute(
                    sa.insert(EXTRACTION)
                    .values(
                        unit=work.unit,
                        platform=item.platform,
                        origin_room=item.room,
                        source_ids=[int(s.key[1]) for s in item.sources if s.key[0] == "message"],
                        context_ids=[],
                        snapshot=item.snapshot,
                        version=READING_VERSION,
                        model=self.model_id,
                        state="done",
                        built_at=datetime.now(UTC),
                    )
                    .returning(EXTRACTION.c.id)
                )
            ).scalar_one()
            await write_claims(
                conn,
                extraction_id=extraction,
                unit=work.unit,
                platform=item.platform,
                origin_room=item.room,
                claims=contents,
            )
            await conn.execute(
                sa.insert(RESULT).values(
                    work_id=work.id,
                    input_hash=work.input_hash,
                    session_id=session,
                    part="readings",
                    payload={"extraction_id": extraction},
                )
            )

    async def graph_write(self, work: WorkItem, session: int, admission: Admission) -> None:
        # Retain changed endpoints for reshape if its model fails after admission.
        await self.receipt(
            work, session, "graph", json.loads(json.dumps(asdict(admission), default=str))
        )

    async def changed_graph_keys(self, work: WorkItem) -> list[Key]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(RESULT.c.part, RESULT.c.payload).where(
                        RESULT.c.work_id == work.id,
                        RESULT.c.input_hash == work.input_hash,
                        RESULT.c.part.in_(["graph", "corrected_entities"]),
                    )
                )
            ).all()
        keys = {
            tuple(r[end])
            for part, p in rows
            if part == "graph"
            for r in p["relations"]
            for end in ("src", "dst")
        }
        keys.update(tuple(k) for part, p in rows if part == "corrected_entities" for k in p["keys"])
        return [(k[0], k[1]) for k in keys]

    async def shadow(
        self,
        work: WorkItem,
        session: int,
        entity: Key,
        proposals: list[dict[str, Any]],
        parent: int,
    ) -> None:
        await self.receipt(
            work,
            session,
            "reshape",
            {"entity": list(entity), "would_writes": proposals, "parent_session": parent},
        )

    async def prior_evidence(self, work: WorkItem) -> tuple[Evidence, ...]:
        async with self.engine.connect() as conn:
            payloads = (
                (
                    await conn.execute(
                        sa.select(RESULT.c.payload).where(
                            RESULT.c.work_id == work.id,
                            RESULT.c.input_hash == work.input_hash,
                            RESULT.c.part == "graph",
                        )
                    )
                )
                .scalars()
                .all()
            )
        return tuple(
            Evidence(**{**e, "source": tuple(e["source"])}) for p in payloads for e in p["evidence"]
        )
