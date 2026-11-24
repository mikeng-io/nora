from __future__ import annotations

import logging

from nora.agent.toolkit.ports import NoteCollaborator
from nora.agent.toolkit.results import _record
from nora.domain.notes import (
    Note,
    note_handle,
)
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
        """Read the body of a note in <notes>. Call this when the index line
        does not answer a question about that note.

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
        note_heads[note.id] = head.id
        _record(metrics, "note_read", "ok", chars=len(head.body))
        return {
            "status": "ok",
            "title": note.title,
            "uncertainty": note.uncertainty,
            "closing_condition": note.closing_condition,
            "body": head.body,
            # Provenance the model can quote when discussing a revision.
            "revision_id": head.id,
            # The source objects this note tracks.
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
