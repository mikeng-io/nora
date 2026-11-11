from __future__ import annotations

import logging
from types import SimpleNamespace

from nora.agent.toolkit.ports import NoteCollaborator
from nora.agent.toolkit.results import _record
from nora.agent.toolkit.rooms import _speaker
from nora.domain.lineage import (
    room_key,
)
from nora.domain.note_body import (
    NoteBodyError,
    Shown,
    ShownMessage,
    cited_ids_in,
    parse_note_body,
    resolve_positions,
)
from nora.domain.notes import (
    TAG_PATH_PREFIX,
    Note,
    StaleNoteHeadError,
    has_duplicate_open_title,
    note_handle,
)
from nora.domain.turn_record import turn_handles_in
from nora.memory.adapters.tools.shared import NOTE_SEARCH_LIMIT

log = logging.getLogger("nora.tools")


def _make__resolve(deps):
    _unit = deps._unit

    async def _resolve(book: NoteCollaborator, title: str) -> list[Note]:
        """Title → the standing notes that answer to it."""
        wanted = note_handle(title)
        unit = _unit() or ""
        found = [n for n in await book.standing(unit=unit) if note_handle(n.title) == wanted]
        if found:
            return found
        # A MERGED NOTE FORWARDS. The turn's `<notes>` was rendered before the merge,
        # so the model can name the duplicate's title after it retired; «no note by
        # that title» would then be false of the question, which lives on in the
        survivor = await book.merged_successor_by_title(unit=unit, title=title)
        return [survivor] if survivor is not None else []

    return _resolve


def _make__ambiguous(deps):
    metrics = deps.metrics

    def _ambiguous(candidates: list[Note]) -> dict:
        _record(metrics, "note_read", "refused", reason="ambiguous")
        return {
            "status": "refused",
            "candidates": [n.title for n in candidates],
            "note": "More than one note answers to that title. Ask which one is "
            "meant, and do not pick.",
        }

    return _ambiguous


def _make_note_read(deps):
    metrics = deps.metrics
    _ambiguous = _make__ambiguous(deps)
    _notebook = deps._notebook
    _resolve = _make__resolve(deps)
    _unit = deps._unit
    note_heads = deps.note_heads
    notes_off = deps.notes_off

    async def note_read(title: str) -> dict:
        """Read what a note in <notes> actually says, the only way to reach the
        note itself. Call this before note_revise, always.

        <notes> shows each note's title and one line: enough to know a note
        exists, not what is in it. Pass the title exactly as <notes> shows it.

        Returns `status`: ok (`body` and the note's fields), nothing_found,
        refused or unavailable, with a `note` on how to report the last three.

        Args:
            title: The note's title, exactly as <notes> shows it.
        """
        book = _notebook()
        if book is None:
            _record(metrics, "note_read", "unavailable")
            return dict(notes_off)
        try:
            candidates = await _resolve(book, title)
            if len(candidates) > 1:
                return _ambiguous(candidates)
            if not candidates:
                _record(metrics, "note_read", "nothing_found")
                return {
                    "status": "nothing_found",
                    "note": "There's no note by that title. Only the ones in "
                    "<notes> exist, so check it rather than trying "
                    "variations, and don't describe one that isn't there.",
                }
            found = await book.read(unit=_unit() or "", note_id=candidates[0].id)
        except Exception:
            log.exception("note_read failed")
            _record(metrics, "note_read", "unavailable")
            return dict(notes_off)
        if found is None:
            # The note was standing a moment ago and is not readable now — a
            # note_set_status (or note_merge) took it out from under this call, or
            # the unit moved. «I could not read it», never «there is no such
            _record(metrics, "note_read", "unavailable", reason="vanished")
            return dict(notes_off)
        note, head = found
        # The CAS anchor, recorded HERE and not asked of the model.
        note_heads[note.id] = head.id
        _record(metrics, "note_read", "ok", chars=len(head.body))
        return {
            "status": "ok",
            "title": note.title,
            "uncertainty": note.uncertainty,
            "closing_condition": note.closing_condition,
            "body": head.body,
            # Provenance the model can quote, and the one field the guard's
            # latch has no use for: it is here so a revision can be discussed
            # («this changed since I read it»), not so it can be passed back.
            "revision_id": head.id,
            # The source objects this note tracks (note_create's
            # `object_refs`), so a revise can keep or replace them.
            **({"object_refs": [r.handle for r in note.object_refs]} if note.object_refs else {}),
        }

    return note_read


def _make_note_search(deps):
    metrics = deps.metrics
    _notebook = deps._notebook
    _unit = deps._unit
    notes_off = deps.notes_off

    async def note_search(query: str) -> dict:
        """Look INSIDE notes for something their titles don't say. Reach for it
        when somebody mentions a detail that might already be written down.

        <notes> already shows every title; this searches note contents. It is a
        plain substring match that does not stem or translate, so give the word
        the way somebody here would have written it.

        Returns `status`: ok (`matches`, each with its title and body),
        nothing_found, refused or unavailable, with a `note` on how to report
        the last three.

        Args:
            query: The word or phrase to look for inside notes.
        """
        book = _notebook()
        if book is None:
            _record(metrics, "note_search", "unavailable")
            return dict(notes_off)
        probe = query.strip()
        if not probe:
            _record(metrics, "note_search", "refused", reason="empty")
            return {
                "status": "refused",
                "note": "Give me a word or phrase to look for.",
            }
        try:
            hits = await book.search(
                unit=_unit() or "",
                query=probe,
                limit=NOTE_SEARCH_LIMIT,
            )
        except Exception:
            log.exception("note_search failed")
            _record(metrics, "note_search", "unavailable")
            return dict(notes_off)
        if not hits:
            _record(metrics, "note_search", "nothing_found")
            return {
                "status": "nothing_found",
                "note": "Nothing written down mentions that. That's a real "
                "answer, so say so rather than inventing one.",
            }
        _record(metrics, "note_search", "ok", count=len(hits))
        return {
            "status": "ok",
            "matches": [
                {"title": note.title, "description": note.description, "body": body}
                for note, body in hits
            ],
        }

    return note_search


def _make__held_to_schema(deps):
    display_name = deps.display_name
    metrics = deps.metrics
    persons = deps.persons
    bound_window = deps.bound_window

    def _held_to_schema(
        tool: str,
        body: str,
        *,
        carried: frozenset[int],
        carried_turns: frozenset[str] = frozenset(),
    ) -> str | dict:
        """The tag path's body held to the note-body schema: her `[#n]`"""
        rows = [r.id for r in bound_window]
        try:
            mapped = resolve_positions(body, rows=rows, carried=carried)
            parsed = parse_note_body(
                mapped,
                shown=Shown(
                    rendered=tuple(
                        ShownMessage(
                            id=r.id,
                            sender=_speaker(r, display_name),
                            person_id=r.person_id,
                            is_nora=r.is_nora,
                            turn_handle=r.turn_handle,
                        )
                        for r in bound_window
                    ),
                    carried=carried,
                    turn_handles=carried_turns,
                    roster={
                        ref.name: pid for pid, ref in getattr(persons, "by_person", {}).items()
                    },
                    nora_names=frozenset({display_name}) if display_name else frozenset(),
                ),
            )
        except NoteBodyError as refused:
            _record(metrics, tool, "refused", reason="schema", rule=refused.rule)
            return {
                "status": "refused",
                "note": f"Nothing was written: the body is not in the note's "
                f"form ({refused}). Fix that line and call again.",
            }
        return parsed.text

    return _held_to_schema


def _make_note_create(deps):
    metrics = deps.metrics
    room = deps.room
    _held_to_schema = _make__held_to_schema(deps)
    _notebook = deps._notebook
    _now_ms = deps._now_ms
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id
    note_heads = deps.note_heads
    notes_off = deps.notes_off

    async def note_create(
        title: str,
        description: str,
        uncertainty: str,
        closing_condition: str,
        body: str,
        anchor_date: int | None = None,
        anchor_entities: list[str] | None = None,
    ) -> dict:
        """Start carrying an open question that nothing here settles yet.
        Call this when the room is chewing on something that outlives this
        conversation.

        A note is tracked uncertainty (whose wedding is when, what the trip still
        lacks, who has not paid), never a settled fact or a description of a
        person. Check <notes> first: if a note already covers this, read and
        update it instead.

        It MUST have a `closing_condition` (what would settle it) and an anchor
        (`anchor_date`, `anchor_entities` or both), or it is refused.

        Returns `status`: ok, refused or unavailable, with a `note` on how to
        report the last two.

        Args:
            title: A short handle, at most 60 characters, that names the thing. It
                is what <notes> shows and what note_read takes.
            description: One line, at most 200 characters: what is undecided and
                what would settle it. It rides in <notes> on every turn and is all
                you have when deciding whether to read the body, so make it say why
                the note matters now. Not a summary: the title already says what it
                is about.
            uncertainty: What exactly is not known yet, in a sentence.
            closing_condition: What would settle it.
            body: What is known so far, in sections: ## Decided and ## Open
                always, then ## Owed, ## Proposed or ## Ruled out when one has a
                line. Each line is one "- " item ending in the transcript messages
                it rests on, as [#n]; a body that breaks this is refused with the
                rule it broke.
            anchor_date: Optional. The calendar day it is about, as YYYYMMDD.
            anchor_entities: Optional. The people or things it is about, named
                the way this group names them."""
        book = _notebook()
        if book is None:
            _record(metrics, "note_create", "unavailable")
            return dict(notes_off)
        # WHO WROTE IT. `established_by` is not decoration: the strike reset fires
        # only on a tag-path revision, and a revision written under nobody's name
        # makes the strike-reset loop breaker reset itself forever. Same refusal
        tagger = room.tagger
        if tagger is None:
            _record(metrics, "note_create", "refused", reason="no_tagger")
            return {
                "status": "refused",
                "note": "I don't know who you are well enough to write this down "
                "under anybody's name. Say you can't, and don't guess.",
            }
        # THE BODY'S FORM: refused before anything is read
        # or written, so a malformed body never costs a store round trip.
        held = _held_to_schema("note_create", body, carried=frozenset())
        if isinstance(held, dict):
            return held
        body = held
        # AN OPEN TITLE IS REFUSED. Nothing in the schema stops a second open note
        # with the same title, and a duplicate is an amplifier: a later `updates`
        # naming it would match two notes. The room's question already has a note; the
        try:
            open_titles = [n.title for n in await book.standing(unit=_unit() or "")]
            if has_duplicate_open_title(title, open_titles):
                _record(metrics, "note_create", "refused", reason="duplicate_title")
                return {
                    "status": "refused",
                    "note": "A note with that title is already open. Read it "
                    "with note_read and update it instead of starting "
                    "a second one.",
                }
        except Exception:
            log.exception("note_create could not check the open titles")
            _record(metrics, "note_create", "unavailable")
            return dict(notes_off)
        try:
            note = await book.create(
                unit=_unit() or "",
                title=title,
                description=description,
                uncertainty=uncertainty,
                closing_condition=closing_condition,
                established_in=bound_conversation_id,
                body=body,
                established_by=f"{TAG_PATH_PREFIX}{tagger.person_id}",
                now_ms=_now_ms(),
                anchor_date=anchor_date,
                anchor_entity_ids=tuple(anchor_entities or ()),
            )
        except ValueError as exc:
            # THE DOMAIN'S OWN SENTENCE, surfaced rather than swallowed.
            # `nora.domain.notes` refuses a note with no anchor, no closing
            # condition, or an over-long index entry, and each refusal says
            _record(metrics, "note_create", "refused", reason="domain")
            return {"status": "refused", "note": str(exc)}
        except Exception:
            log.exception("note_create failed")
            _record(metrics, "note_create", "unavailable")
            return dict(notes_off)
        note_heads[note.id] = note.head_revision_id
        _record(metrics, "note_create", "ok")
        return {"status": "ok", "title": note.title}

    return note_create


def _make_note_revise(deps):
    async def note_revise(title: str, body: str) -> dict:
        """Revise a note you have JUST read this turn. Call this when a
        note's content changes: a detail settles, a plan moves.

        You MUST call note_read on this note first, in this same turn, or it is
        refused. Give back the WHOLE note with your change folded in: `body`
        replaces the current contents. The old revision is kept, so a bad one
        can be undone.

        Returns `status`: ok, refused, nothing_found or unavailable, with a
        `note` on how to report the last three.

        Args:
            title: The note's title, exactly as <notes> shows it.
            body: The whole note after your change, in note_create's sections,
                every line ending in [#n] or in the ids it already cites."""
        c = SimpleNamespace(
            title=title,
            body=body,
            metrics=deps.metrics,
            room=deps.room,
            _ambiguous=_make__ambiguous(deps),
            _held_to_schema=_make__held_to_schema(deps),
            _notebook=deps._notebook,
            _now_ms=deps._now_ms,
            _resolve=_make__resolve(deps),
            _unit=deps._unit,
            bound_conversation_id=deps.bound_conversation_id,
            note_heads=deps.note_heads,
            notes_off=deps.notes_off,
        )
        k_, _, v_ = await _note_revise__notebook(c)
        if k_:
            return v_
        k_, _, v_ = await _note_revise__append(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return note_revise


async def _note_revise__notebook(c):
    c.book = c._notebook()
    if c.book is None:
        _record(c.metrics, "note_revise", "unavailable")
        return (True, None, dict(c.notes_off))
    c.tagger = c.room.tagger
    if c.tagger is None:
        _record(c.metrics, "note_revise", "refused", reason="no_tagger")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "I don't know who you are well enough "
                    "to write this down under anybody's name. Say you can't, and don't guess."
                ),
            },
        )
    try:
        c.candidates = await c._resolve(c.book, c.title)
    except Exception:
        log.exception("note_revise could not resolve the title")
        _record(c.metrics, "note_revise", "unavailable")
        return (True, None, dict(c.notes_off))
    if len(c.candidates) > 1:
        return (True, None, c._ambiguous(c.candidates))
    if not c.candidates:
        _record(c.metrics, "note_revise", "nothing_found")
        return (
            True,
            None,
            {
                "status": "nothing_found",
                "note": ("There's no note by that title to revise. Check <notes>."),
            },
        )
    c.note = c.candidates[0]
    c.parent = c.note_heads.get(c.note.id)
    if c.parent is None:
        _record(c.metrics, "note_revise", "refused", reason="not_read")
        c.metrics.inc("nora_note_revise_refused_total", reason="title_moved")
        return (
            True,
            None,
            {
                "status": "refused",
                "note": (
                    "You haven't read that note this turn. "
                    "Call note_read on it first, because you cannot revise a body you have not "
                    "seen."
                ),
            },
        )
    try:
        c.current = await c.book.read(unit=c._unit() or "", note_id=c.note.id)
    except Exception:
        log.exception("note_revise could not read the head it revises")
        _record(c.metrics, "note_revise", "unavailable")
        return (True, None, dict(c.notes_off))
    c.held = c._held_to_schema(
        "note_revise",
        c.body,
        carried=cited_ids_in(c.current[1].body) if c.current is not None else frozenset(),
        carried_turns=turn_handles_in(c.current[1].body) if c.current is not None else frozenset(),
    )
    if isinstance(c.held, dict):
        return (True, None, c.held)
    c.body = c.held
    return (False, None, None)


async def _note_revise__append(c):
    try:
        c.revision = await c.book.append_revision(
            unit=c._unit() or "",
            note_id=c.note.id,
            body=c.body,
            parent_revision_id=c.parent,
            established_by=(f"{TAG_PATH_PREFIX}{c.tagger.person_id}"),
            now_ms=c._now_ms(),
            evidence_room=room_key(c.room.platform, c.bound_conversation_id),
        )
    except StaleNoteHeadError:
        c.note_heads.pop(c.note.id, None)
        _record(c.metrics, "note_revise", "unavailable", reason="stale_head")
        c.metrics.inc("nora_note_revise_refused_total", reason="stale_head")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "This note changed under you, so "
                    "read it again and redo your change against what it says now. I have not "
                    "written "
                    "anything."
                ),
            },
        )
    except ValueError as _w1:
        c.exc = _w1
        _record(c.metrics, "note_revise", "refused", reason="domain")
        return (True, None, {"status": "refused", "note": str(c.exc)})
    except Exception:
        log.exception("note_revise failed")
        _record(c.metrics, "note_revise", "unavailable")
        return (True, None, dict(c.notes_off))
    c.note_heads[c.note.id] = c.revision.id
    _record(c.metrics, "note_revise", "ok", chars=len(c.body))
    return (True, None, {"status": "ok", "title": c.note.title})
    return (False, None, None)
