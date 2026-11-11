"""Cognition-session verbs for the memory tier: the notebook, memos,
the Standing head and the memory/profile writes. Reached from the session
through `nora.memory.wire.session_verbs` (the package root — the same seam
`nora.documents`' session tools use).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Literal

from nora.agent.dream_screen import offence
from nora.app.comprehend import Statement
from nora.app.proactive_screen import CANCELLED_NOTE_RETIRED
from nora.app.session_verbs import (
    _STALE_NOTE,
    SessionDeps,
    _day_number,
    _refused,
    _room_key,
    _write_target,
)
from nora.domain.cognition_session import Room
from nora.domain.memo import (
    MEMO_BODY_MAX_CHARS,
    MEMO_TITLE_MAX_CHARS,
    Memo,
    MemoWithdrawnError,
    StaleMemoError,
    citations_of,
    parse_memo_body,
    screened_parts,
    title_carries_value,
)
from nora.domain.note_body import (
    NoteBodyError,
    check_citations,
    cited_ids_in,
    parse_note_body,
)
from nora.domain.notes import (
    NoteSupersedeReason,
    StaleNoteHeadError,
    default_revisit_at,
    has_duplicate_open_title,
    latest_anchor,
)
from nora.domain.reminders import ReminderState
from nora.domain.turn_record import cited_turn_lineage, turn_handles_in
from nora.infra.sql.standing_revise import standing_revise as live_standing_revise


def session_verbs(d: SessionDeps) -> tuple[tuple[Any, ...], ...]:
    """The session's write verbs, built against the runner's
    collaborators in `d` (`d.runner` is `CognitionSessionRunner`)."""
    return (
        (_make_note_create(d), _make_note_revise(d),
         _make_note_set_status(d), _make_note_merge(d)),
        (_make_memo_create(d), _make_memo_revise(d), _make_memo_set_status(d),),
        (_make_standing_revise(d),),
        (_make_memory_propose(d), _make_profile_read(d), _make_profile_revise(d),),
    )


def _memo_screen(
    d: SessionDeps, verb: str, parts: Mapping[str, object],
) -> dict[str, object] | None:
    """The Dreamer's identifier/coordinate screen over a memo's non-Kept parts."""
    self = d.runner
    found = offence(parts, content_checks=self._content_checks)
    if found is None:
        return None
    reason, path = found
    self._inc("nora_dream_screened_total", adaptor="cognition", reason=reason)
    return _refused(
        reason,
        f"`{path}` carries an {reason}; only a `## Kept` line may hold what "
        f"was said verbatim, so move it there or write {verb} without it",
    )


def _memos_off() -> dict[str, object]:
    return {"status": "unavailable", "reason": "unwired",
            "detail": "the memo store is not wired here; nothing was written"}


async def _memo_body(d: SessionDeps,
    verb: str, title: str, body: str, platform: str, conversation_id: str,
    carried: frozenset[int] = frozenset(), carried_turns: frozenset[str] = frozenset(),
) -> tuple[Any, tuple[str, ...]] | dict[str, object]:
    """`(parsed body, citations)` of a memo write, or its refusal."""
    self, unit, shown_for = d.runner, d.unit, d.shown_for
    if not title.strip() or len(title) > MEMO_TITLE_MAX_CHARS:
        return _refused(
            "bad_title", f"a memo title is 1 to {MEMO_TITLE_MAX_CHARS} characters",
        )
    if len(body) > MEMO_BODY_MAX_CHARS:
        return _refused(
            "too_long", f"a memo body is at most {MEMO_BODY_MAX_CHARS} characters",
        )
    shown = await shown_for(platform, conversation_id, body)
    if isinstance(shown, dict):
        return shown
    try:
        parsed = parse_memo_body(
            body, shown=replace(shown, carried=shown.carried | carried,
                                turn_handles=shown.turn_handles | carried_turns),
        )
        for handle in turn_handles_in(parsed.text):
            trace = await self._tag_trace_store.get(
                turn_id=int(handle[1:]), unit=unit,
            ) if self._tag_trace_store is not None else None
            cited_turn_lineage(trace, unit=unit)
    except NoteBodyError as exc:
        return _refused(exc.rule, str(exc))
    value = title_carries_value(title, parsed)
    if value is not None:
        return _refused(
            "title_carries_value",
            f"the title holds {value!r}, which a `## Kept` line keeps; a title "
            "names the thing (wifi password, door code), never its value",
        )
    screened = _memo_screen(d, verb, screened_parts(title, parsed))
    if screened is not None:
        return screened
    return parsed, citations_of(parsed)


async def _duplicate(d: SessionDeps, title: str, but: str | None = None) -> Memo | None:
    """The kept memo of this unit answering to `title`, other than `but`."""
    self, unit = d.runner, d.unit
    for kept in await self._memos.kept(unit=unit):
        if kept.id != but and has_duplicate_open_title(title, [kept.title]):
            return kept
    return None


async def _memo_head(d: SessionDeps, memo_id: str) -> Memo | dict[str, object]:
    self, unit = d.runner, d.unit
    memo = await self._memos.get(unit=unit, memo_id=memo_id)
    if memo is None:
        return _refused("not_found", "there is no memo by that id in this group")
    if not memo.is_kept:
        return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
    return memo


def _make_note_create(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    established_by, shown_for, wrote = d.established_by, d.shown_for, d.wrote
    _act_receipt, _associations, _object_refs = (
        d.act_receipt, d.associations, d.object_refs)
    async def note_create(
        title: str, description: str, uncertainty: str, closing_condition: str,
        body: str, anchor_date: str | None = None,
        anchor_entities: list[str] | None = None, room: str = "", item: int | None = None,
        associations: list[list[str]] | None = None, object_refs: list[str] | None = None,
    ) -> dict[str, object]:
        """Start carrying an open question that nothing here settles yet.
        Call this when a room is chewing on something that outlives this
        pass.

        A note is tracked uncertainty (whose wedding is when, what the trip
        still lacks, who has not paid), never a settled fact or a
        description of a person. If a note already covers it, update that
        one instead. It MUST have a `closing_condition` (what would settle
        it) and an anchor (`anchor_date`, `anchor_entities` or both), or it
        is refused.

        Returns `status`: ok (with the `note_id`), refused or unavailable,
        with a `reason` and `detail` when it is not ok.

        Args:
            title: A short handle, at most 60 characters, that names the
                thing.
            description: One line, at most 200 characters: what is
                undecided and what would settle it.
            uncertainty: What exactly is not known yet, in a sentence.
            closing_condition: What would settle it.
            body: What is known so far, in sections: ## Decided and ## Open
                always, then ## Owed, ## Proposed or ## Ruled out when one
                has a line. Each line is one "- " item ending in the ids of
                the messages it rests on, as [<id>, <id>]: a message you
                were shown or read, or a Rollup row you read, cited by
                rollup(kind|subject|from/to) from its row. A body that
                breaks this is refused with the rule it broke.
            anchor_date: Optional. The calendar day it is about, as an ISO
                date (year-month-day).
            anchor_entities: Optional. The people or things it is about,
                named the way the room names them.
            room: Optional when the agenda is one room, required when it
                spans several. The room this note is about, as
                `platform:conversation_id`, which the reads block
                shows beside each room's label.
            item: Optional. The agenda item this write is for, by its
                number.
            associations: Optional. Existing matters this note concerns, as
                [kind, id] node keys from the graph you were shown.
            object_refs: Optional. Up to 8 `object` handles shown in this
                session. Keep these source objects in view while this matter
                remains open. Their room scope is resolved by the server;
                current state is read fresh, never copied into the note.
        """
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        targets = _associations(associations)
        if isinstance(targets, dict):
            return targets
        refs = await _object_refs(object_refs) if object_refs is not None else ()
        if isinstance(refs, dict):
            return refs
        anchor_day: int | None = None
        if anchor_date:
            anchor_day = _day_number(anchor_date)
            if anchor_day is None:
                return _refused(
                    "bad_anchor_date", "anchor_date must be an ISO date, year-month-day",
                )
        shown = await shown_for(platform, conversation_id, body)
        if isinstance(shown, dict):
            return shown
        try:
            parsed = parse_note_body(body, shown=shown, require_open=True)
        except NoteBodyError as exc:
            return _refused(exc.rule, str(exc))
        # Under the unit's lock: Strands runs one turn's tool calls
        # concurrently, and two creates of one title would both pass the check.
        async with self._lock(unit):
            if has_duplicate_open_title(title, await self._open_note_titles(unit)):
                return _refused(
                    "duplicate_title",
                    "a note with that title is already open; update it instead",
                )
            try:
                note = await self._notes.create(
                    unit=unit, title=title, description=description,
                    uncertainty=uncertainty, closing_condition=closing_condition,
                    established_in=conversation_id, body=parsed.text,
                    established_by=established_by, now_ms=self._now_ms(),
                    anchor_date=anchor_day,
                    anchor_entity_ids=tuple(anchor_entities or ()),
                    object_refs=refs,
                    receipt=_act_receipt(
                        lambda row: {"status": "ok", "note_id": row.id}),
                )
            except ValueError as exc:
                return _refused("invalid_note", str(exc))
        return wrote(platform, conversation_id, item, note_id=note.id)
    return note_create


def _make_note_revise(d: SessionDeps) -> Any:
    self, unit, state = d.runner, d.unit, d.state
    established_by, shown_for, wrote = d.established_by, d.shown_for, d.wrote
    _act_receipt, _associations, _object_refs = (
        d.act_receipt, d.associations, d.object_refs)
    async def note_revise(
        note_id: str, body: str, parent_revision_id: str,
        room: str = "", item: int | None = None,
        associations: list[list[str]] | None = None,
        object_refs: list[str] | None = None,
    ) -> dict[str, object]:
        """Revise a note. Call this when a note's content changes: a detail
        settles, a plan moves. Give back the WHOLE note with your change
        folded in: `body` replaces the current contents, and the old
        revision is kept.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            note_id: The note's id.
            body: The whole note after your change, in note_create's
                sections, every line ending in the message ids it rests on,
                as [<id>, <id>], or a Rollup row you read, as
                rollup(kind|subject|from/to).
            parent_revision_id: The note's head revision id your last read
                of it showed. A stale one is refused.
            room: Optional when the agenda is one room, required when it
                spans several. The room this note is about, as
                `platform:conversation_id`, which the reads block
                shows beside each room's label.
            item: Optional. The agenda item this write is for, by its
                number.
            associations: Optional. The matters this note now concerns, as
                [kind, id] node keys from the graph you were shown; they
                replace the note's current semantic links.
            object_refs: Optional. Shown object handles the matter now
                tracks; a supplied list replaces the current refs, an
                omitted one keeps them. An empty list clears them.
        """
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        targets = _associations(associations)
        if isinstance(targets, dict):
            return targets
        refs = await _object_refs(object_refs) if object_refs is not None else None
        if isinstance(refs, dict):
            return refs
        shown = await shown_for(platform, conversation_id, body)
        if isinstance(shown, dict):
            return shown
        try:
            parsed = parse_note_body(body, shown=shown)
        except NoteBodyError as exc:
            return _refused(exc.rule, str(exc))
        try:
            revision = await self._notes.append_revision(
                unit=unit, note_id=note_id, body=parsed.text,
                parent_revision_id=parent_revision_id,
                established_by=established_by, now_ms=self._now_ms(),
                evidence_room=_room_key(Room(platform, conversation_id)),
                object_refs=refs,
                receipt=_act_receipt(
                    lambda row: {"status": "ok", "revision_id": row.id}),
            )
        except StaleNoteHeadError:
            return _STALE_NOTE
        except ValueError as exc:
            return _refused("invalid_revision", str(exc))
        return wrote(platform, conversation_id, item, revision_id=revision.id)
    return note_revise


def _make_note_set_status(d: SessionDeps) -> Any:
    self, unit, state, wrote = d.runner, d.unit, d.state, d.wrote
    async def note_set_status(
        note_id: str, reason: Literal["settled", "anchor_passed"],
        base_revision_id: str = "", room: str = "", item: int | None = None,
    ) -> dict[str, object]:
        """Close a note for good. Call this when its question is settled
        (what would close it has happened) or its day has passed and
        nothing is left open about it. A retired note leaves the index; its
        revisions are kept.

        If the group will need the answer again, keep it with memo_create
        first, then retire the note.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            note_id: The note's id.
            reason: `settled` when its closing condition is met,
                `anchor_passed` when the day it was about has gone by and
                nothing about it is still open.
            base_revision_id: Optional. The note's head revision id your
                last read of it showed; a note changed since is not retired.
            room: Optional when the agenda is one room, required when it
                spans several, as for note_revise.
            item: Optional. The agenda item this write is for, by its number.
        """
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        because = {
            "settled": NoteSupersedeReason.RETIRED_CLOSED,
            "anchor_passed": NoteSupersedeReason.RETIRED_EXPIRED,
        }.get(reason)
        if because is None:
            return _refused("bad_reason", "reason is settled or anchor_passed")
        retired = await self._notes.supersede(
            key=unit, target=note_id, replaced_by=None, because=because,
            source=None, now_ms=self._now_ms(), expected_head=base_revision_id or None,
        )
        if not retired:
            return _refused(
                "not_retired",
                "no open note by that id at that revision; read the notebook again",
            )
        for held in await self._reminders.pending_of(self._ai_peer):
            if (held.payload or {}).get("note_id") == note_id:
                await self._reminders.mark(
                    held.id, ReminderState.CANCELLED, outcome=CANCELLED_NOTE_RETIRED,
                )
        return wrote(platform, conversation_id, item)
    return note_set_status


def _make_note_merge(d: SessionDeps) -> Any:
    self, unit, state, established_by, shown_for, wrote = (
        d.runner, d.unit, d.state, d.established_by, d.shown_for, d.wrote)
    async def note_merge(
        survivor_id: str, duplicate_id: str, body: str,
        room: str = "", item: int | None = None,
    ) -> dict[str, object]:
        """Fold a duplicate note into the one that keeps the question. Call
        this when two open notes track the same uncertainty. The survivor
        gets `body` as its new contents, the duplicate is retired pointing
        at it, and a reminder held on either note follows the survivor.
        Source references from both notes survive; if their combined set
        exceeds 8, revise the references before merging.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            survivor_id: The note that keeps the question.
            duplicate_id: The note folded into it.
            body: The survivor's whole note after the fold, in note_create's
                sections, with what the duplicate knew and the survivor did
                not folded in. Every line ends in the ids it rests on, which
                may be any id either note already cites.
            room: Optional when the agenda is one room, required when it
                spans several, as for note_revise.
            item: Optional. The agenda item this write is for, by its number.
        """
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        if survivor_id == duplicate_id:
            return _refused("same_note", "a note cannot be merged into itself")
        survivor = await self._notes.read(unit=unit, note_id=survivor_id)
        duplicate = await self._notes.read(unit=unit, note_id=duplicate_id)
        if survivor is None or duplicate is None:
            return _refused("not_found", "both notes must be open notes of this group")
        shown = await shown_for(platform, conversation_id, body)
        if isinstance(shown, dict):
            return shown
        try:
            parsed = parse_note_body(body, shown=replace(
                shown, carried=shown.carried | cited_ids_in(survivor[1].body)
                | cited_ids_in(duplicate[1].body),
                turn_handles=shown.turn_handles | turn_handles_in(survivor[1].body)
                | turn_handles_in(duplicate[1].body),
            ), require_open=True)
        except NoteBodyError as exc:
            return _refused(exc.rule, str(exc))
        anchor = latest_anchor(survivor[0].anchor_date, duplicate[0].anchor_date)
        now = self._now_ms()
        try:
            await self._notes.merge_into(
                unit=unit, survivor_id=survivor_id, survivor_head=survivor[1].id,
                duplicate_id=duplicate_id, duplicate_head=duplicate[1].id,
                body=parsed.text, anchor_date=anchor,
                revisit_at=default_revisit_at(anchor_date=anchor, now_ms=now),
                reset_strikes=False, established_by=established_by, now_ms=now,
            )
        except StaleNoteHeadError:
            return _STALE_NOTE
        except ValueError as exc:
            return _refused("invalid_revision", str(exc))
        return wrote(platform, conversation_id, item)
    return note_merge


def _make_memo_create(d: SessionDeps) -> Any:
    self, unit, state, established_by, wrote, _act_receipt, _associations = (
        d.runner, d.unit, d.state, d.established_by, d.wrote, d.act_receipt, d.associations)
    async def memo_create(
        title: str, body: str, mode: Literal["told", "deduced", "abduced"] = "told",
        room: str = "", item: int | None = None,
        associations: list[list[str]] | None = None,
    ) -> dict[str, object]:
        """Keep something the group has settled and will need again. Call
        this when a room settles a fact about the group, someone else or the
        world (a password, a deposit split, a booking reference), or asks you
        to keep one. Most settled things need no memo.

        A person's explicit ask to keep something about the group is
        written, not weighed. A fact about the speaker alone is not a memo,
        nor is anything still open (a note) or dated and attended (an
        event). A memo never closes; it is revised or withdrawn. A `##
        Kept` line holds what was said exactly as said; the title names
        the thing, never its value, and a title or condition carrying a
        value, a phone, an email or a key-like string is refused.

        Returns `status`: ok (with the `memo_id`), refused or unavailable,
        with a `reason` and `detail` when it is not ok. A memo already kept
        under that title is refused `duplicate`, naming it: revise that one.

        Args:
            title: A short handle, at most 80 characters, that names the
                thing (wifi password, door code), never its value.
            body: `## Kept`, then optionally `## Conditions` (when it holds,
                its exceptions). Each line is one "- " item ending in the
                ids of the messages it rests on, as [<id>, <id>]: a message
                you were shown or read, your own included (cite the
                person's message asking to keep it too, when there is one).
            mode: `told` when a person said it, `deduced` when it follows
                from what you were shown, `abduced` as a best explanation.
            room: Optional when the agenda is one room, required when it
                spans several. The room it was settled in, as
                `platform:conversation_id`.
            item: Optional. The agenda item this write is for, by its
                number.
            associations: Optional. Existing matters this memo settles, as
                [kind, id] node keys from the graph you were shown.
        """
        if self._memos is None:
            return _memos_off()
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        targets = _associations(associations)
        if isinstance(targets, dict):
            return targets
        checked = await _memo_body(d, "memo_create", title, body, platform, conversation_id)
        if isinstance(checked, dict):
            return checked
        parsed, citations = checked
        async with self._lock(unit):
            twin = await _duplicate(d, title)
            if twin is not None:
                return _refused(
                    "duplicate",
                    f"memo {twin.id} is already kept as {twin.title!r}; revise it "
                    "with memo_revise instead of keeping a second one",
                )
            memo = await self._memos.create(
                unit=unit, platform=platform, conversation_id=conversation_id,
                title=title, body=parsed.text, citations=citations, mode=mode,
                established_by=established_by, model=self._model_id,
                now_ms=self._now_ms(),
                receipt=_act_receipt(
                    lambda row: {"status": "ok", "memo_id": row.id,
                                 "revision_id": row.head.id}),
            )
        return wrote(platform, conversation_id, item, memo_id=memo.id,
                     revision_id=memo.head.id)
    return memo_create


def _make_memo_revise(d: SessionDeps) -> Any:
    self, unit, state, established_by, wrote, _act_receipt, _associations = (
        d.runner, d.unit, d.state, d.established_by, d.wrote, d.act_receipt, d.associations)
    async def memo_revise(
        memo_id: str, base_revision_id: str, body: str, title: str = "",
        mode: Literal["told", "deduced", "abduced"] = "told",
        room: str = "", item: int | None = None,
        associations: list[list[str]] | None = None,
    ) -> dict[str, object]:
        """Revise a kept memo. Call this when what the group settled has
        changed or gained a detail. Give back the WHOLE memo with your change
        folded in: `body` replaces it, and the old revision is kept.

        Returns `status`: ok (with the new `revision_id`), refused or
        unavailable, with a `reason` and `detail` when it is not ok
        (`stale_base` when the memo changed since you read it).

        Args:
            memo_id: The memo's id, as <memos> shows it.
            base_revision_id: The `revision_id` memo_read last showed.
            body: The whole memo after your change, in memo_create's
                sections, every line ending in the ids it rests on.
            title: Optional. A new title; left out, the title stays.
            mode: How you reached the change: `told`, `deduced` or `abduced`.
            room: Optional when the agenda is one room, required when it
                spans several, as for memo_create.
            item: Optional. The agenda item this write is for, by its number.
            associations: Optional. The matters this memo now settles, as
                [kind, id] node keys from the graph you were shown; they
                replace the memo's current semantic links.
        """
        if self._memos is None:
            return _memos_off()
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        targets = _associations(associations)
        if isinstance(targets, dict):
            return targets
        head = await _memo_head(d, memo_id)
        if isinstance(head, dict):
            return head
        new_title = title or head.title
        checked = await _memo_body(d,
            "memo_revise", new_title, body, platform, conversation_id,
            carried=cited_ids_in(head.head.body),
            carried_turns=turn_handles_in(head.head.body),
        )
        if isinstance(checked, dict):
            return checked
        parsed, citations = checked
        async with self._lock(unit):
            twin = await _duplicate(d, new_title, but=head.id) if title else None
            if twin is not None:
                return _refused(
                    "duplicate", f"memo {twin.id} is already kept as {twin.title!r}",
                )
            try:
                revision = await self._memos.append(
                    unit=unit, memo_id=head.id, base_revision_id=base_revision_id,
                    title=new_title, body=parsed.text, citations=citations, mode=mode,
                    state="kept", established_by=established_by, model=self._model_id,
                    now_ms=self._now_ms(),
                    receipt=_act_receipt(
                        lambda row: {"status": "ok", "revision_id": row.id}),
                )
            except StaleMemoError:
                return _refused(
                    "stale_base", f"the memo's head is now {head.head.id}; read it "
                    "again with memo_read and redo your change against what it says",
                )
            except MemoWithdrawnError:
                return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
        return wrote(platform, conversation_id, item, revision_id=revision.id)
    return memo_revise


def _make_memo_set_status(d: SessionDeps) -> Any:
    self, unit, state, established_by, shown_for, wrote = (
        d.runner, d.unit, d.state, d.established_by, d.shown_for, d.wrote)
    async def memo_set_status(
        memo_id: str, base_revision_id: str, cited_ids: list[str],
        room: str = "", item: int | None = None,
    ) -> dict[str, object]:
        """Withdraw a kept memo for good. Call this only when someone in the
        group says it no longer holds (the password changed, the booking was
        cancelled). It leaves <memos>; its revisions are kept.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            memo_id: The memo's id, as <memos> shows it.
            base_revision_id: The `revision_id` memo_read last showed.
            cited_ids: The ids of the messages saying it no longer holds, at
                least one, each one you were shown or read.
            room: Optional when the agenda is one room, required when it
                spans several, as for memo_create.
            item: Optional. The agenda item this write is for, by its number.
        """
        if self._memos is None:
            return _memos_off()
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        head = await _memo_head(d, memo_id)
        if isinstance(head, dict):
            return head
        shown = await shown_for(platform, conversation_id, "")
        if isinstance(shown, dict):
            return shown
        try:
            ids, keys = check_citations(cited_ids, shown, what="memo_set_status")
        except NoteBodyError as exc:
            return _refused(exc.rule, str(exc))
        try:
            await self._memos.append(
                unit=unit, memo_id=head.id, base_revision_id=base_revision_id,
                title=head.title, body=head.head.body,
                citations=(*(str(i) for i in ids), *(f"rollup({k})" for k in keys)),
                mode="told", state="withdrawn", established_by=established_by,
                model=self._model_id, now_ms=self._now_ms(),
            )
        except StaleMemoError:
            return _refused(
                "stale_base", "the memo changed since you read it; read it again first",
            )
        except MemoWithdrawnError:
            return _refused("withdrawn", "that memo was withdrawn; nothing moves it now")
        return wrote(platform, conversation_id, item)
    return memo_set_status


def _make_standing_revise(d: SessionDeps) -> Any:
    self, unit, state, wrote = d.runner, d.unit, d.state, d.wrote
    async def standing_revise(
        where_it_stands: str, hold_open: str | None = None, i_owe: str | None = None,
        mode: Literal["deduced", "abduced", "said"] = "deduced",
        base_revision_id: str | None = None, room: str = "", item: int | None = None,
    ) -> dict[str, object]:
        """Revise a room's Standing: where it stands right now. Call this
        when what a room has settled, or is holding open, has changed. Give
        the whole position, not a delta.

        Returns `status`: ok, refused or unavailable, with a `reason` and
        `detail` when it is not ok.

        Args:
            where_it_stands: The position, in a short paragraph. Required.
            hold_open: Optional. What you are deliberately holding open.
            i_owe: Optional. What you owe the room.
            mode: How you came to it: `deduced` from what was said,
                `abduced` as a best guess, `said` when it rests only on
                your own earlier words.
            base_revision_id: Optional. The `revision` standing_read showed
                for this room's Standing. A stale one is refused.
            room: Optional when the agenda is one room, required when it
                spans several. The room, as `platform:conversation_id`,
                which the reads block shows beside each room's label.
            item: Optional. The agenda item this write is for, by its
                number.
        """
        resolved = _write_target(room, item, state)
        if isinstance(resolved, dict):
            return resolved
        (platform, conversation_id), item = resolved
        async with self._lock(unit):
            head = await self._prior_standing(platform, conversation_id)
            prior = None if head is None else head.standing
            revision = await live_standing_revise(
                store=self._standing, prior=prior, platform=platform,
                conversation_id=conversation_id, where_it_stands=where_it_stands,
                working_set=() if prior is None else prior.working_set,
                hold_open=hold_open, i_owe=i_owe, mode=mode,
                last_folded_row_id=None if prior is None else prior.last_folded_row_id,
                base_revision_id=base_revision_id,
                prior_revision_id=None if head is None else head.revision_id,
                now_ms=self._now_ms(), source="cognition",
            )
        if revision.refusal is not None:
            return _refused(revision.refusal.reason, revision.refusal.detail)
        return wrote(platform, conversation_id, item)
    return standing_revise


def _make_memory_propose(d: SessionDeps) -> Any:
    self, state = d.runner, d.state
    async def memory_propose(statements: list[Statement]) -> dict[str, Any]:
        """Save grounded relations, corrections and readings from shown evidence.
        Call this when a shown row changes what you understand.

        Returns each statement's saved/refused result and connected memory.
        Refusals explain what failed and how to repair a grounded statement;
        valid siblings are saved independently.

        Args:
            statements: The facts to carry forward, with their citations.
        """
        if self._memory is None:
            return {"status": "unavailable", "note": "Memory admission is unavailable."}
        return dict(await self._memory.propose(state, statements=statements))
    return memory_propose


def _make_profile_read(d: SessionDeps) -> Any:
    self, state = d.runner, d.state
    async def profile_read(
        entity: list[str],
    ) -> dict[str, Any]:
        """Read one profile account's lines, open disputes and revision before revising it.

        Returns `status`: ok with handled lines, disputes and revision, refused or unavailable.

        Args:
            entity: The profile subject kind and id: person, room, unit or nora.
        """
        if self._memory is None:
            return {"status": "unavailable"}
        result: dict[str, Any] = await self._memory.account_view(state, entity)
        return result
    return profile_read


def _make_profile_revise(d: SessionDeps) -> Any:
    self, state = d.runner, d.state
    async def profile_revise(
        entity: list[str], ops: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Change a profile account you read this session. Call this on new admitted evidence.

        Returns `status`: ok, refused with `refusals`, conflict or unavailable.

        Args:
            entity: The profile subject kind and id: person, room, unit or nora.
            ops: The changes, each an object with `op`: add (part, kind,
                text, mode, citations, and draws_on, told_by, prediction,
                prediction_observable, prediction_window_end or
                conflicts_with where they apply), keep (line, citations),
                revise (line, citations, text, mode, draws_on,
                conflicts_with) or retire (line, why). A held line is
                named by its `handle` from profile_read; a relation the
                line generalises is `relation:<id>` in draws_on; a shown
                source is cited as `<kind>:<id>`.
        """
        if self._memory is None:
            return {"status": "unavailable"}
        key = await self._memory.act_key(state, "profile_revise",
                                         {"entity": entity, "ops": ops})
        result: dict[str, Any] = await self._memory.profile(
            state, entity, ops, act_key=key)
        return result
    return profile_revise
