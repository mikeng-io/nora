"""Explicit projection doubles for the retained legacy-index substrate.

Production writers no longer project. These test doubles perform the same
projection immediately after the source write so the projector's behaviour
keeps its cover without re-creating the production write-through path.
"""

from __future__ import annotations

from typing import Any

from nora.domain.rollup import episode_key
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.culture import SqlCultureStore
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.media_descriptions import SqlMediaDescriptionStore
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.memory_index import social_source_id
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.social_stream import SqlSocialStreamStore


async def _project(store: Any, kind: str, source_id: Any) -> None:
    async with store._engine.begin() as conn:
        await memory_index.project(conn, kind, source_id, metrics=getattr(store, "_metrics", None))


async def _reproject_media(store: Any, sha256: str) -> None:
    async with store._engine.begin() as conn:
        await memory_index.reproject_media(conn, sha256, metrics=getattr(store, "_metrics", None))


class ProjectingRecordStore(SqlRecordStore):
    async def append(self, **kwargs: Any) -> int:
        row_id = await super().append(**kwargs)
        await _project(self, "message", row_id)
        return row_id

    async def append_edit(self, *, message_id: int, **kwargs: Any) -> None:
        await super().append_edit(message_id=message_id, **kwargs)
        await _project(self, "message", message_id)

    async def append_reaction(self, *, message_id: int, **kwargs: Any) -> int:
        row_id = await super().append_reaction(message_id=message_id, **kwargs)
        await _project(self, "message", message_id)
        return row_id

    async def append_delete(self, *, message_id: int, **kwargs: Any) -> None:
        await super().append_delete(message_id=message_id, **kwargs)
        await _project(self, "message", message_id)

    async def hide_message(self, *, message_id: int, **kwargs: Any) -> bool:
        hid = await super().hide_message(message_id=message_id, **kwargs)
        if hid:
            await _project(self, "message", message_id)
        return hid

    async def set_delivery_outcome(
        self, *, row_id: int, ts: int | None = None, **kwargs: Any,
    ) -> None:
        await super().set_delivery_outcome(row_id=row_id, ts=ts, **kwargs)
        if ts is not None:
            await _project(self, "message", row_id)

    async def set_person_id(self, *, row_id: int, **kwargs: Any) -> None:
        await super().set_person_id(row_id=row_id, **kwargs)
        await _project(self, "message", row_id)


class ProjectingMediaObjectStore(SqlMediaObjectStore):
    async def save(self, *, obj: Any) -> bool:
        saved = await super().save(obj=obj)
        if saved:
            await _reproject_media(self, obj.sha256)
        return saved

    async def comprehend(self, *, sha256: str, **kwargs: Any) -> bool:
        saved = await super().comprehend(sha256=sha256, **kwargs)
        if saved:
            await _reproject_media(self, sha256)
        return saved


class ProjectingMediaDescriptionStore(SqlMediaDescriptionStore):
    async def remember(self, *, blob: Any, **kwargs: Any) -> None:
        await super().remember(blob=blob, **kwargs)
        await _reproject_media(self, blob.sha256)


class ProjectingNoteStore(SqlNoteStore):
    async def create(self, **kwargs: Any) -> Any:
        note = await super().create(**kwargs)
        await _project(self, "note", note.id)
        return note

    async def append_revision(self, *, note_id: str, **kwargs: Any) -> Any:
        revision = await super().append_revision(note_id=note_id, **kwargs)
        await _project(self, "note", note_id)
        return revision

    async def supersede(self, *, target: str, **kwargs: Any) -> int:
        written = await super().supersede(target=target, **kwargs)
        if written:
            await _project(self, "note", target)
        return written

    async def merge_into(self, *, survivor_id: str, duplicate_id: str, **kwargs: Any) -> Any:
        revision = await super().merge_into(
            survivor_id=survivor_id, duplicate_id=duplicate_id, **kwargs,
        )
        await _project(self, "note", survivor_id)
        await _project(self, "note", duplicate_id)
        return revision


class ProjectingMemoStore(SqlMemoStore):
    async def create(self, **kwargs: Any) -> Any:
        memo = await super().create(**kwargs)
        await _project(self, "memo", memo.id)
        return memo

    async def append(self, *, memo_id: str, **kwargs: Any) -> Any:
        revision = await super().append(memo_id=memo_id, **kwargs)
        await _project(self, "memo", memo_id)
        return revision


class ProjectingLessonStore(SqlLessonStore):
    async def create(self, **kwargs: Any) -> Any:
        lesson = await super().create(**kwargs)
        await _project(self, "lesson", lesson.id)
        return lesson

    async def append(self, *, lesson_id: str, **kwargs: Any) -> Any:
        revision = await super().append(lesson_id=lesson_id, **kwargs)
        await _project(self, "lesson", lesson_id)
        return revision


class ProjectingCultureStore(SqlCultureStore):
    async def append(self, *, key: str, **kwargs: Any) -> Any:
        revision = await super().append(key=key, **kwargs)
        await _project(self, "character", key)
        return revision


class ProjectingGatheringStore(SqlGatheringStore):
    async def update(self, event: Any, **kwargs: Any) -> Any:
        updated = await super().update(event, **kwargs)
        await _project(self, "event", str(updated.id))
        return updated


class ProjectingSocialStreamStore(SqlSocialStreamStore):
    async def insert_item(self, item: Any, **kwargs: Any) -> bool:
        wrote = await super().insert_item(item, **kwargs)
        if wrote:
            await _project(self, "social", social_source_id(item.network, item.item_id))
        return wrote

    async def record_state(self, *, network: str, item_id: str, **kwargs: Any) -> None:
        await super().record_state(network=network, item_id=item_id, **kwargs)
        await _project(self, "social", social_source_id(network, item_id))

    async def record_place(self, *, network: str, item_id: str, **kwargs: Any) -> None:
        await super().record_place(network=network, item_id=item_id, **kwargs)
        await _project(self, "social", social_source_id(network, item_id))

    async def record_perception(self, *, network: str, item_id: str, **kwargs: Any) -> None:
        await super().record_perception(network=network, item_id=item_id, **kwargs)
        await _project(self, "social", social_source_id(network, item_id))



class ProjectingMobilityStore(SqlMobilityStore):
    def scoped(self, build_id: int) -> ProjectingMobilityStore:
        return type(self)(
            self._engine, metrics=self._metrics, build_id=build_id,
            registration=self._aggregates.registration,
        )

    def unbound(self) -> ProjectingMobilityStore:
        return type(self)(
            self._engine, metrics=self._metrics,
            registration=self._aggregates.registration,
        )

    async def commit_fold(self, *, stays: list[Any], trips: list[Any], **kwargs: Any) -> None:
        await super().commit_fold(stays=stays, trips=trips, **kwargs)
        build = self._bound()
        async with self._engine.begin() as conn:
            for kind, spans in (("stay", stays), ("trip", trips)):
                await memory_index.project_episodes(
                    conn, build, kind,
                    [episode_key(x.subject_id, x.started_ts, x.ended_ts) for x in spans],
                    metrics=self._metrics,
                )


class ProjectingRollupStore(SqlRollupStore):
    async def switch_build(self, *, build_id: int) -> None:
        await super().switch_build(build_id=build_id)
        async with self._engine.begin() as conn:
            await memory_index.reproject_episodes(conn, metrics=self._metrics)

    async def apply(self, **kwargs: Any) -> dict[str, int]:
        changes = await super().apply(**kwargs)
        async with self._engine.begin() as conn:
            await memory_index.reproject_episodes(conn, metrics=self._metrics)
        return changes
