"""Read the wake floor through the room that owns the claimed set."""

import asyncio
from collections.abc import Callable
from datetime import datetime

from nora.access import IdentityAccess
from nora.agent.room import Room
from nora.domain.blocks import ContextBlock
from nora.domain.composed import compose_body
from nora.domain.context_data import MembersView, RoomLine, TranscriptLine
from nora.domain.documents import DocumentAuthority
from nora.domain.lineage import LiveRoom, room_key
from nora.domain.media_join import MediaJoin
from nora.domain.record_query import RecordQuery
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.slot_keys import resolve_slot_room
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.language.context_render import (
    render_clock,
    render_members,
    render_reads,
    render_rooms,
    render_transcript,
)
from nora.language.documents_render import render_documents
from nora.language.presence_render import render_presence
from nora.memory import PassScope, ReadingRoom, WakeClaim
from nora.policy import Conversation, Policy


class WakeRoomFloor:
    def __init__(
        self, policy: Policy, access: IdentityAccess, room_for: Callable[[str, str], Room],
        records: SqlRecordQuery, audit: SqlRecordStore, presence: PresenceReader,
        documents: DocumentsContextReader | None, screen: Callable[[str], str | None], *,
        display_name: str, record_chars: int, media_join: MediaJoin | None = None,
    ) -> None:
        self._policy = policy
        self._access = access
        self._room_for = room_for
        self._records = records
        self._audit = audit
        self._presence = presence
        self._documents = documents
        self._screen = screen
        self._display_name = display_name
        self._record_chars = record_chars
        self._media_join = media_join

    async def scope(self, claim: WakeClaim) -> tuple[PassScope, bool]:
        return await self.scope_for(claim.group.unit, claim.reading_room)

    async def scope_for(self, unit: str, reading_room: str) -> tuple[PassScope, bool]:
        room = resolve_slot_room(self._policy.rooms(), reading_room, unit=unit)
        if room is None or not room.absorbed:
            raise LookupError("Wake reading room unavailable in claim unit")
        platform, id_ = room.platform, room.primary_id
        view = await self._room_for(platform, id_).scope()
        live = LiveRoom(frozenset(person.person_id for person in view.people),
                        view.unnamed, view.available, view.degraded)
        if not view.available and (room.persons is None or not self._access.is_file_backend):
            raise LookupError("Wake reading-room membership unavailable")
        direct = bool(room.ids & await self._access.direct_addresses(platform))
        return PassScope(
            ReadingRoom(platform, id_, live), unit, nora_name=self._display_name,
        ), direct

    async def floor(
        self, claim: WakeClaim, scope: PassScope, now: datetime,
    ) -> tuple[ContextBlock, ...]:
        platform, id_ = scope.room.platform, scope.room.conversation_id
        room = self._conversation(platform, id_, scope.unit)
        key = room_key(platform, id_)
        label = room.label or "Current room"
        since = min((s.eligible_at for s in claim.stimuli), default=now)
        now_ms, since_ms = int(now.timestamp() * 1000), int(since.timestamp() * 1000)
        direct = bool(room.ids & await self._access.direct_addresses(platform))
        async with asyncio.TaskGroup() as reads:
            members = reads.create_task(self._room_for(platform, id_).members())
            presence = reads.create_task(
                self._presence.read(platform=platform, conversation_id=id_, direct=direct),
            )
            directory = reads.create_task(
                self._presence.rooms(platform=platform, conversation_id=id_, direct=direct),
            )
            transcript = reads.create_task(
                self._transcript(room, id_, scope.unit, since_ms, now_ms, now, direct),
            )
            documents = reads.create_task(self._document_index(room, id_, scope.unit, now_ms))
        view = members.result()
        room_index = directory.result()
        rooms = []
        for entry in room_index.rooms or ():
            if self._screen(entry.label) or self._screen(entry.description or ""):
                continue
            activity = await self._audit.room_activity(
                conversation_id=entry.conversation_id, since_ts=since_ms,
                platform=entry.platform, as_of_ms=now_ms,
            )
            rooms.append(RoomLine(entry, activity.last_ts))
        return (
            render_clock(now=now, last_session_ms=None, report_first_look=False),
            render_members(MembersView(label, view.people, not view.available or view.degraded,
                                       view.unnamed, binding=key)),
            render_presence(presence.result(), room_is=room.description),
            transcript.result(),
            render_rooms(rooms, now=now) if room_index.rooms is not None else _unavailable("rooms"),
            render_reads(((key, label),)),
            documents.result(),
        )

    def _conversation(self, platform: str, id_: str, unit: str) -> Conversation:
        room = self._policy.conversation(platform, id_)
        if room is None or room.unit != unit or not room.absorbed:
            raise LookupError("Wake reading room unavailable in claim unit")
        return room

    async def _transcript(
        self, room: Conversation, id_: str, unit: str,
        since_ms: int, now_ms: int, now: datetime, direct: bool,
    ) -> ContextBlock:
        if direct:
            return render_transcript((), display_name=self._display_name, now=now)
        keys = tuple((room.platform, id_) for id_ in sorted(room.ids | {id_}))
        hits, more, _ = await self._records.context_rows(
            RecordQuery(keys, since_ms, now_ms + 1, limit=self._record_chars),
            as_of_ms=now_ms, unit=unit,
        )
        rows = []
        for hit in hits:
            if hit.deleted or hit.row.body is None:
                continue
            if self._screen(hit.row.body) or (
                hit.row.edit and self._screen(hit.row.edit.was or "")
            ):
                continue
            rows.append(hit)
        named = await self._access.name_transcript(
            [hit.row for hit in rows], platform=room.platform,
            scope=self._room_for(room.platform, id_).room_scope,
        )
        if self._media_join is not None:
            named = await self._media_join.enrich(named, path="wake")
        lines = tuple(
            TranscriptLine(row, room.label, platform=hit.platform,
                           conversation_id=hit.conversation_id)
            for row, hit in zip(named, rows, strict=True)
            if not self._screen(row.body or "")
        )
        return render_transcript(lines, display_name=self._display_name, now=now,
                                 not_listed=int(more), withheld=len(hits) - len(lines))

    async def _document_index(
        self, room: Conversation, id_: str, unit: str, now_ms: int,
    ) -> ContextBlock:
        if self._documents is None:
            return _unavailable("documents")
        authority = DocumentAuthority(
            unit, frozenset((room.platform, alias) for alias in room.ids | {id_}),
            0, as_of_ms=now_ms, read_only=True,
        )
        view = await self._documents.read(
            platform=room.platform, conversation_id=id_, person_id=authority,
        )
        return render_documents(view) or _unavailable("documents")


def _unavailable(name: str) -> ContextBlock:
    return ContextBlock(
        name, compose_body(""), attrs=(("status", "unavailable"),), when_empty="self_close",
    )
