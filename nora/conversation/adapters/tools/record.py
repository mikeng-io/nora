from __future__ import annotations

import logging
from types import SimpleNamespace

from nora.agent.catalog.groups import LOAD_TRANSCRIPT_DEFAULT, LOAD_TRANSCRIPT_MAX
from nora.agent.standing_ledger import ledger_permits, ledger_target
from nora.agent.toolkit.clock import _HKT
from nora.agent.toolkit.results import _memory_result, _record
from nora.agent.toolkit.rooms import _asked_about, _said, _speaker
from nora.agent.toolkit.transcript import (
    _BEFORE_UNREADABLE,
    _REACTIONS_NOTE,
    _before_bound,
    _file_handles,
    _older_note,
    _read_window,
    _transcript_messages,
    _trim_window,
)
from nora.agent.turn_recording import record_requirement
from nora.domain.lineage import message as message_lineage
from nora.domain.lineage import (
    room_key,
)
from nora.domain.models import (
    TranscriptRow,
)
from nora.domain.observations import (
    SourceEvent,
    SourceScope,
)
from nora.domain.turn_record import DisclosureRequirement
from nora.obs.tracing import annotate, span

log = logging.getLogger("nora.tools")


def _make_who_said(deps):
    async def who_said(words: str) -> dict:
        """Check who said something, in the messages you can see right now. Call
        this BEFORE you put words in somebody's mouth.

        It searches only what you were already shown, where the line usually is.
        For older lines, `load_transcript` reads further back in this
        conversation and `search_memory` further still. Give it the words as
        written, not a paraphrase.

        Returns `status`: ok (`said_by`, oldest first), nothing_found or
        unavailable, with a `note` on how to report the last two.

        Args:
            words: The words you are about to put in somebody's mouth, as they
                were written.
        """
        c = SimpleNamespace(
            words=words,
            content_checks=deps.content_checks,
            display_name=deps.display_name,
            metrics=deps.metrics,
            lookup_rows=deps.lookup_rows,
        )
        k_, _, v_ = await _who_said__query(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return who_said


def _make__thread_window(deps):
    ledger = deps.ledger
    lineage = deps.lineage
    room = deps.room
    bound_conversation_id = deps.bound_conversation_id

    async def _thread_window(
        handle: str,
        *,
        platform: str,
        conversation_id: str,
        asked: int,
        before_ts: int | None,
        as_of_ms: int | None = None,
    ) -> tuple[list[TranscriptRow], bool]:
        if ledger is None or lineage is None:
            raise RuntimeError("thread reader is unavailable")
        target = await ledger_target(
            lineage.render_room(
                platform=room.platform,
                conversation_id=bound_conversation_id,
            ),
            room,
        )
        if target.room is None:
            raise RuntimeError("thread reader is unavailable")
        live = target.live
        if (
            live is None
            or not live.available
            or live.degraded
            or live.unnamed
            or not live.person_ids
        ):
            raise RuntimeError("thread reader is unavailable")
        entries = await ledger.read(
            unit=target.room.unit, platform=room.platform, conversation_id=bound_conversation_id
        )
        entry = next((e for e in entries if e.kind == "thread" and e.id == handle), None)
        if entry is None:
            return [], False
        if not ledger_permits(entry, target):
            raise RuntimeError("thread reader is unavailable")
        span = next(
            (
                s
                for s in entry.spans
                if s.platform == platform and s.conversation_id == conversation_id
            ),
            None,
        )
        if span is None:
            return [], False
        rows = await ledger.span_rows(span, limit=asked, before_ts=before_ts, as_of_ms=as_of_ms)
        return _trim_window(rows, asked=asked)

    return _thread_window


def _make_load_transcript(deps):
    async def load_transcript(
        limit: int = LOAD_TRANSCRIPT_DEFAULT,
        before: str | None = None,
        thread: str | None = None,
    ) -> dict:
        """Read this room's older messages or a Standing thread. Call this when
        you need earlier context.

        You already have the most recent messages. Returns `messages`, oldest
        first, each with `sender`, `body` and `when` it was sent.

        Args:
            thread: A thread id from Standing; reads its span in this room.
            limit: How many messages to fetch (max 60).
            before: Read the messages sent before this Hong Kong time,
                "YYYY-MM-DD HH:MM", or a date alone for the end of that day.
                Leave it out for the latest ones.
        """
        from nora.conversation.adapters.tools.query import _make__room_source_events

        c = SimpleNamespace(
            limit=limit,
            before=before,
            thread=thread,
            display_name=deps.display_name,
            media_join=deps.media_join,
            metrics=deps.metrics,
            observations=deps.observations,
            on_rows=deps.on_rows,
            persons=deps.persons,
            room=deps.room,
            store=deps.store,
            _room_source_events=_make__room_source_events(deps),
            _thread_window=_make__thread_window(deps),
            bound_conversation_id=deps.bound_conversation_id,
            now_fn=deps.now_fn,
        )
        k_, _, v_ = await _load_transcript__window(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return load_transcript


async def _who_said__query(c):
    with span(
        "tool.who_said",
        **{"nora.tool": "who_said", "nora.who_said.window_rows": len(c.lookup_rows)},
    ):
        if not c.lookup_rows:
            annotate(**{"nora.tool.status": "unavailable", "nora.who_said.matches": 0})
            _record(c.metrics, "who_said", "unavailable")
            return (
                True,
                None,
                {
                    "status": "unavailable",
                    **_asked_about(c.words, content_checks=c.content_checks),
                    (
                        "note"
                    ): "I can't re-read the messages in front of me, so I can't check who said "
                    "that. Say you couldn't check, and never say nobody said it.",
                },
            )
        c.needle = _said(c.words)
        c.hits = [
            {"who": _speaker(row, c.display_name), "line": _file_handles(row.body)}
            | ({"was": _file_handles(row.edit.was)} if row.edit is not None else {})
            for row in c.lookup_rows
            if c.needle
            and (
                c.needle in _said(row.body)
                or (row.edit is not None and c.needle in _said(row.edit.was or ("")))
            )
        ]
        if not c.hits:
            annotate(**{"nora.tool.status": "nothing_found", "nora.who_said.matches": 0})
            _record(c.metrics, "who_said", "nothing_found", rows=len(c.lookup_rows))
            return (
                True,
                None,
                {
                    "status": "nothing_found",
                    **_asked_about(c.words, content_checks=c.content_checks),
                    (
                        "note"
                    ): "Nobody in the messages you can see said that. Do not put it in anyone's "
                    "mouth. Look further back with load_transcript or search_memory, or say you "
                    "can't find it.",
                },
            )
        annotate(**{"nora.tool.status": "ok", "nora.who_said.matches": len(c.hits)})
        _record(c.metrics, "who_said", "ok", rows=len(c.lookup_rows), matches=len(c.hits))
        return (
            True,
            None,
            _memory_result(
                {
                    "status": "ok",
                    "said_by": c.hits,
                    **_asked_about(c.words, content_checks=c.content_checks),
                }
            ),
        )
    return (False, None, None)


async def _load_transcript__window(c):
    c.now = c.now_fn().astimezone(_HKT)
    c.as_of_ms = int(c.now.timestamp() * 1000)
    try:
        c.before_ts = _before_bound(c.before, c.now)
    except (ValueError, OverflowError):
        _record(c.metrics, "load_transcript", "unavailable", reason="before")
        return (True, None, {"status": "unavailable", "messages": [], "note": _BEFORE_UNREADABLE})
    c.asked = LOAD_TRANSCRIPT_DEFAULT
    try:
        c.asked = max(1, min(int(c.limit), LOAD_TRANSCRIPT_MAX))
        if c.thread is not None:
            c.rows, c.older = await c._thread_window(
                c.thread,
                platform=c.room.platform,
                conversation_id=c.bound_conversation_id,
                asked=c.asked,
                before_ts=c.before_ts,
                as_of_ms=c.as_of_ms,
            )
        else:
            c.rows, c.older = await _read_window(
                c.store,
                conversation_id=c.bound_conversation_id,
                asked=c.asked,
                before_ts=c.before_ts,
                as_of_ms=c.as_of_ms,
            )
    except Exception:
        log.exception("load_transcript failed")
        _record(c.metrics, "load_transcript", "unavailable")
        if c.thread is not None:
            return (
                True,
                None,
                _memory_result(
                    {
                        "status": "unavailable",
                        "messages": [],
                        "note": ("The thread could not be read."),
                    }
                ),
            )
        return (
            True,
            None,
            _memory_result(
                {
                    "status": "unavailable",
                    "messages": [],
                    "note": (
                        "The conversation could not be read; this does not mean it was empty."
                    ),
                }
            ),
        )
    c.rows = c.persons.name_transcript(c.rows, platform=c.room.platform)
    c.origin = room_key(c.room.platform, c.bound_conversation_id)
    record_requirement(DisclosureRequirement(c.origin, (message_lineage(c.origin),)))
    if c.media_join is not None:
        c.rows = list(await c.media_join.enrich(c.rows, path="load_transcript"))
    c.this_events: tuple[SourceEvent, ...] | None = None
    c.this_events_more = 0
    if c.observations is not None and c.rows:
        c.found = await c._room_source_events(
            SourceScope(
                platform=c.room.platform, account_key="", conversation_id=c.bound_conversation_id
            ),
            since_ms=min(r.ts for r in c.rows),
            as_of_ms=c.as_of_ms,
        )
        if c.found is not None:
            c.this_events, c.this_events_more = c.found
    c.out, c.threaded = _transcript_messages(
        c.rows,
        now=c.now,
        with_ids=c.on_rows is not None,
        display_name=c.display_name,
        source_events=c.this_events,
        on_rows=c.on_rows,
    )
    _record(
        c.metrics,
        "load_transcript",
        "ok",
        limit=c.asked,
        rows=len(c.out),
        threaded=c.threaded,
        before=c.before_ts is not None,
        older=c.older,
    )
    c.result: dict = {"messages": c.out}
    c.notes = []
    if c.observations is not None and c.rows and (c.this_events is None):
        c.notes.append(
            "The room's recorded source events could not be read; the messages below are complete."
        )
    if c.this_events_more:
        c.notes.append("More source events are recorded for this window than are interleaved here.")
    if any("was" in m for m in c.out):
        c.notes.append(
            "A message with `was` was edited after sending: `body` is what it says "
            "now, `was` what it first said, and `edited` when it changed."
        )
    if any("reactions_source" in m for m in c.out):
        c.notes.append(_REACTIONS_NOTE)
    if c.older:
        c.notes.append(_older_note(c.rows))
    if not c.out and c.before_ts is not None:
        c.notes.append("Nothing in this conversation is on record before that time.")
    if c.notes:
        c.result["note"] = " ".join(c.notes)
    return (True, None, _memory_result(c.result))
    return (False, None, None)
