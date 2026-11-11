"""Cognition reads the shared context sources through each agenda room's binding."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from nora.agent.lineage import profile_lines_for, section_permits
from nora.agent.standing_ledger import ledger_target
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import AgendaItem
from nora.domain.context_data import MembersView, RoomLine, TranscriptLine
from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.documents import DocumentAuthority
from nora.domain.lineage import message
from nora.domain.media_join import MediaJoin
from nora.domain.models import TranscriptRow
from nora.domain.profile import ProfileView
from nora.domain.record_query import RecordQuery
from nora.language.profile_render import authored_identity

log = logging.getLogger(__name__)


class CognitionContextReader:
    """Read once into typed state; the renderers do no I/O or policy decisions."""

    def __init__(
        self,
        *,
        policy: Any,
        persons: Any,
        lineage: Any,
        records: Any,
        directory: Any,
        audit: Any,
        profiles: Any,
        lessons: Any,
        media_join: MediaJoin | None,
        room_for: Callable[..., Any],
        screen: Callable[[str], str | None],
        record_chars: int,
        documents: Any = None,
    ) -> None:
        self._documents = documents
        self._policy = policy
        self._persons = persons
        self._lineage = lineage
        self._records = records
        self._directory = directory
        self._audit = audit
        self._profiles = profiles
        self._lessons = lessons
        self._media_join = media_join
        self._room_for = room_for
        self._screen = screen
        self._record_chars = record_chars

    async def read(
        self,
        unit: str,
        items: Sequence[AgendaItem],
        grounding: Grounding,
        agenda_rows: Sequence[tuple[str, str, TranscriptRow, int]],
        *,
        now_ms: int,
    ) -> Grounding:
        agenda = tuple(dict.fromkeys(i.room for i in items))
        if any(
            (room := self._policy.conversation(r.platform, r.conversation_id)) is None
            or room.unit != unit
            for r in agenda
        ):
            raise ValueError("agenda room is outside the unit")
        bindings = tuple(self._room_for(r.platform, r.conversation_id) for r in agenda)
        targets = []
        for r, binding in zip(agenda, bindings, strict=True):
            target = self._lineage.render_room(
                platform=r.platform, conversation_id=r.conversation_id
            )
            try:
                target = await ledger_target(target, binding)
            except Exception:
                log.exception("cognition binding audience unavailable")
            targets.append(target)
        failed: set[str] = set()
        withheld = Counter[str]()

        def visible(platform: str, cid: str) -> bool:
            origin = f"{platform}:{cid}"
            passed = bool(targets) and all(
                section_permits((message(origin),), t, origin=origin) for t in targets
            )
            return passed or self._lineage.mode != "on"

        members: list[MembersView] = []
        for r, binding in zip(agenda, bindings, strict=True):
            label = grounding.room_labels.get(f"{r.platform}:{r.conversation_id}", "unnamed room")
            try:
                view = await binding.members()
                members.append(
                    MembersView(
                        label,
                        view.people,
                        not view.available or view.degraded,
                        view.unnamed,
                        binding=f"{r.platform}:{r.conversation_id}",
                    )
                )
            except Exception:
                log.exception("cognition context source read failed")
                members.append(
                    MembersView(
                        label, unavailable=True, binding=f"{r.platform}:{r.conversation_id}"
                    )
                )
        related = self._related(members, agenda_rows)
        profiles, profile_failed = await self._profiles_read(
            unit, agenda, related, targets, bindings, now_ms=now_ms
        )
        room_lines, transcript, more, read_failed = await self._rooms_and_transcript(
            unit, items, agenda, agenda_rows, related, visible, withheld, now_ms=now_ms
        )
        failed |= read_failed

        lessons: tuple[str, ...] = ()
        try:
            if self._lessons is None:
                raise RuntimeError("lesson source unavailable")
            if self._policy.shows_self_to_unit(unit):
                lessons = tuple(
                    x
                    for x in await self._lessons.bodies(unit=unit, as_of_ms=now_ms)
                    if not self._screen(x)
                )
        except Exception:
            log.exception("cognition context source read failed")
            failed.add("lessons")
        documents, document_rooms, doc_failed = await self._documents_read(
            unit, agenda, visible, now_ms=now_ms
        )
        failed |= doc_failed

        # A partly failed unit read is unavailable, never an apparently complete slice.
        if "transcript" in failed:
            transcript = []
        if "rooms" in failed:
            room_lines = []
        return replace(
            grounding,
            profiles=tuple(profiles),
            profile_unavailable=tuple(profile_failed),
            members=tuple(members),
            rooms=tuple(room_lines),
            transcript=tuple(sorted(transcript, key=lambda x: (x.row.ts, x.room, x.row.id))),
            transcript_more=more,
            context_withheld=tuple(sorted(withheld.items())),
            documents=tuple(documents),
            document_rooms=frozenset(document_rooms),
            lessons=lessons,
            unavailable=grounding.unavailable | failed,
            room_labels={
                **grounding.room_labels,
                **{f"{x.room.platform}:{x.room.conversation_id}": x.room.label for x in room_lines},
            },
            profile_names={pid: f"{p.name} ({pid})" for pid, p in self._persons.by_person.items()},
        )



    def _related(
        self,
        members: Sequence[MembersView],
        agenda_rows: Sequence[tuple[str, str, TranscriptRow, int]],
    ) -> tuple[str, ...]:
        """The agenda's mentioned and active people, most-mentioned first."""
        frequencies: Counter[str] = Counter()
        eligible = {p.person_id: p for v in members for p in v.people}
        # An alias is resolved only among the agenda's identified members.
        aliases: dict[str, set[str]] = {}
        for pid, p in eligible.items():
            for name in (p.name, *p.aliases):
                if name:
                    aliases.setdefault(name.casefold(), set()).add(pid)
        for platform, _, row, _sid in agenda_rows:
            author = self._persons.resolve(platform, row.sender_id)
            author_id = author.person_id if author is not None else row.person_id
            if not row.is_nora and author_id:
                frequencies[author_id] += 1
            for pid in row.mention_persons:
                frequencies.setdefault(self._persons.subject_id_for(pid), 0)
            text = (row.body or "").casefold()
            for alias, ids in aliases.items():
                if len(ids) == 1 and re.search(
                    r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text, flags=re.ASCII
                ):
                    frequencies.setdefault(next(iter(ids)), 0)
        return tuple(sorted(frequencies, key=lambda pid: (-frequencies[pid], pid)))


    async def _profiles_read(
        self,
        unit: str,
        agenda: Sequence[Any],
        related: Sequence[str],
        targets: Sequence[Any],
        bindings: Sequence[Any],
        *,
        now_ms: int,
    ) -> tuple[list[ProfileView], list[tuple[str, str]]]:
        """The profile blocks the agenda names, each fail-closed."""
        profiles: list[ProfileView] = []
        profile_failed: list[tuple[str, str]] = []
        subjects: list[tuple[str, str, str, tuple[str, ...]]] = [
            ("nora", "nora", nora_character_key(unit), ())
        ]
        subjects += [("unit", unit, unit_culture_key(unit), ())]
        for r in agenda:
            c = self._policy.conversation(r.platform, r.conversation_id)
            if c is not None:
                subjects.append(("room", c.primary_id, c.primary_id, ()))
        for pid in related:
            p = self._persons.by_person.get(pid)
            if p is not None:
                subjects.append(
                    (
                        "person",
                        pid,
                        character_key(unit, pid),
                        authored_identity(name=p.name, aliases=p.aliases),
                    )
                )
        for entity, of, key, authored in subjects:
            try:
                if entity == "nora" and not self._policy.shows_self_to_unit(unit):
                    profile_failed.append((entity, of))
                    continue
                if self._profiles is None:
                    raise RuntimeError("profile source unavailable")
                lines = await self._profiles.account_heads(key=key, as_of_ms=now_ms)
                for target, binding in zip(targets, bindings, strict=True):
                    lines = await profile_lines_for(lines, target, binding)
                lines = tuple(x for x in lines if not self._screen(x.text))
                profiles.append(ProfileView(entity=entity, of=of, lines=lines, authored=authored))
            except Exception:
                log.exception("cognition context source read failed")
                profile_failed.append((entity, of))
        return profiles, profile_failed


    async def _rooms_and_transcript(
        self,
        unit: str,
        items: Sequence[AgendaItem],
        agenda: Sequence[Any],
        agenda_rows: Sequence[tuple[str, str, TranscriptRow, int]],
        related: Sequence[str],
        visible: Callable[[str, str], bool],
        withheld: Counter[str],
        *,
        now_ms: int,
    ) -> tuple[list[RoomLine], list[TranscriptLine], int, set[str]]:
        """Room lines and related rows outside the agenda, per room binding."""
        room_lines: list[RoomLine] = []
        transcript: list[TranscriptLine] = []
        more = 0
        failed: set[str] = set()
        try:
            if not agenda or self._directory is None:
                raise RuntimeError("room source unavailable")
            first = agenda[0]
            unit_rooms = await self._directory.rooms(
                platform=first.platform, conversation_id=first.conversation_id
            )
            if unit_rooms.rooms is None:
                raise RuntimeError("unit room source unavailable")
            source_times = [row.ts for _, _, row, _sid in agenda_rows]
            # The existing grounding lookback is context for a quiet agenda too.
            start_ms = max(0, min(source_times, default=now_ms) - 1800_000)
            agenda_keys = {(r.platform, r.conversation_id) for r in agenda}
            item_of = {
                row.id: index
                for index, item in enumerate(items)
                for _p, _c, row, sid in agenda_rows
                if sid in item.stimulus_ids
            }
            for room in unit_rooms.rooms:
                c = self._policy.conversation(room.platform, room.conversation_id)
                if c is None or c.unit != unit:
                    continue
                if not visible(room.platform, room.conversation_id):
                    withheld["rooms"] += 1
                    continue
                if self._screen(room.label) or self._screen(room.description or ""):
                    withheld["rooms"] += 1
                    continue
                try:
                    activity = await self._audit.room_activity(
                        conversation_id=room.conversation_id,
                        since_ts=start_ms,
                        platform=room.platform,
                        as_of_ms=now_ms,
                    )
                    room_lines.append(RoomLine(room, activity.last_ts))
                except Exception:
                    log.exception("cognition context source read failed")
                    room_lines.append(RoomLine(room, activity_unavailable=True))
                try:
                    room_keys = tuple(
                        (room.platform, cid) for cid in sorted(c.ids | {room.conversation_id})
                    )
                    is_agenda_room = any(key in agenda_keys for key in room_keys)
                    hits, extra, _ = await self._records.context_rows(
                        RecordQuery(
                            rooms=room_keys,
                            start_ms=start_ms,
                            end_ms=now_ms + 1,
                            # A dynamic read ceiling: even empty rows cost markup. The
                            # final allocation measures escaped rows under the one cap.
                            limit=max(0, self._record_chars),
                            related_persons=() if is_agenda_room else tuple(related),
                        ),
                        as_of_ms=now_ms,
                    )
                    more += int(extra)
                    rows = []
                    source_rooms = {hit.row.id: (hit.platform, hit.conversation_id) for hit in hits}
                    for hit in hits:
                        if (hit.platform, hit.conversation_id) not in room_keys:
                            raise ValueError("record outside the declared room read")
                        row = hit.row
                        mentioned = set(row.mention_persons)
                        if not is_agenda_room and not ({row.person_id} | mentioned) & set(related):
                            continue
                        if hit.deleted or row.body is None:
                            continue
                        if self._screen(row.body) or (
                            row.edit and self._screen(row.edit.was or "")
                        ):
                            withheld["transcript"] += 1
                            continue
                        rows.append(row)
                    rows = list(self._persons.name_transcript(rows, platform=room.platform))
                    if self._media_join is not None:
                        rows = list(await self._media_join.enrich(rows, path="cognition"))
                    screened = [r for r in rows if not self._screen(r.body or "")]
                    withheld["transcript"] += len(rows) - len(screened)
                    rows = screened
                    transcript.extend(
                        TranscriptLine(
                            row,
                            room.label,
                            item_of.get(row.id),
                            *source_rooms[row.id],
                        )
                        for row in rows
                    )
                except Exception:
                    log.exception("cognition context source read failed")
                    failed.add("transcript")
        except Exception:
            log.exception("cognition context source read failed")
            failed.update(("rooms", "transcript"))
        return room_lines, transcript, more, failed


    async def _documents_read(
        self,
        unit: str,
        agenda: Sequence[Any],
        visible: Callable[[str, str], bool],
        *,
        now_ms: int,
    ) -> tuple[list[Any], frozenset[tuple[str, str]], set[str]]:
        """The document surfaces the agenda rooms may read, fail-closed."""

        documents: list[Any] = []
        failed: set[str] = set()
        document_rooms: set[tuple[str, str]] = set()
        agenda_document_rooms = frozenset(
            (r.platform, cid)
            for r in agenda
            for cid in (
                self._policy.conversation(r.platform, r.conversation_id).ids | {r.conversation_id}
            )
        )
        for r in agenda:
            if not visible(r.platform, r.conversation_id):
                continue
            if self._documents is None:
                failed.add("documents")
                break
            view = await self._documents.read(
                platform=r.platform,
                conversation_id=r.conversation_id,
                person_id=DocumentAuthority(
                    unit,
                    agenda_document_rooms,
                    0,
                    as_of_ms=now_ms,
                ),
            )
            if view is None or view.unavailable:
                failed.add("documents")
                documents = []
                break
            documents.append(view)
            document_rooms.update(
                (r.platform, cid)
                for cid in self._policy.conversation(r.platform, r.conversation_id).ids
                | {r.conversation_id}
            )
        return documents, frozenset(document_rooms), failed
