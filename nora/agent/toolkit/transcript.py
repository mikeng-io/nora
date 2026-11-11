from __future__ import annotations

import mimetypes
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from nora.agent.toolkit.clock import _HKT
from nora.agent.toolkit.rooms import _speaker
from nora.domain.external_files import (
    render_file_links,
)
from nora.domain.media import LABELS as MEDIA_LABELS
from nora.domain.models import (
    REACTION_PAIRS_MAX,
    TranscriptRow,
)
from nora.domain.prompt import stamp
from nora.domain.quote_window import first_by_ts

ALONE_MAX_CHARS = 20


def _file_handles(text: str | None) -> str | None:
    """`render_file_links` over a body a tool hands back, None kept."""
    return render_file_links(text) if text else text


def _source_event_item(
    e: Any,
    *,
    now: datetime,
    on: int | None = None,
) -> dict:
    """One typed source event as a tool result item, interleaved into a"""
    item: dict[str, Any] = {
        "source_event": {
            "kind": e.kind,
            "op": e.operation,
            "object": e.handle,
            "summary": e.summary,
        },
    }
    if on is not None:
        item["source_event"]["on"] = on
    elif e.target_message_id is not None:
        # The affected message the result does not otherwise show: the
        # recorded row's own words and author as labelled `target` context
        # — never the event actor's words — or `target_unresolved` when a
        # named target cannot be shown (hidden, deleted elsewhere, or past
        # the bound), never a guessed quote.
        if e.target is not None:
            item["source_event"]["target"] = _target_item(e.target, now=now)
        else:
            item["source_event"]["target_unresolved"] = True
    # `when` is the SOURCE's clock — it appears only when the source gave a
    # time. An unknown clock shows `time: unknown` plus `received` (the
    # bridge's own clock), never a receipt dressed as occurrence.
    if e.at_ms > 0 and e.time_quality != "unknown":
        item["source_event"]["when"] = stamp(e.at_ms, now)
    if e.time_quality != "upstream":
        item["source_event"]["time"] = e.time_quality or "unknown"
    if e.received_ms and e.time_quality != "upstream":
        item["source_event"]["received"] = stamp(e.received_ms, now)
    if e.actor:
        item["source_event"]["actor"] = e.actor
    elif e.actor_status == "anonymous":
        item["source_event"]["actor"] = "anonymous"
    if e.revision:
        item["source_event"]["rev"] = e.revision
    if e.coverage:
        item["source_event"]["coverage"] = e.coverage
    return item


def _target_item(t: Any, *, now: datetime) -> dict:
    """One resolved affected message as a `target` dict — the same keys a"""
    item: dict[str, Any] = {"sender": t.author}
    if t.at_ms > 0:
        item["when"] = stamp(t.at_ms, now)
    if t.deleted:
        item["deleted"] = True
    else:
        item["body"] = _file_handles(t.text)
        if t.edited_ms:
            item["edited"] = stamp(t.edited_ms, now)
            item["was"] = _file_handles(t.was)
    return item


def _transcript_messages(
    rows: Sequence[TranscriptRow],
    *,
    now: datetime,
    with_ids: bool = False,
    display_name: str = "",
    source_events: Sequence[Any] | None = None,
    on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
) -> tuple[list[dict], int]:
    """Stored rows as the model reads them in a tool result, and how many were"""
    by_ts = first_by_ts(rows)
    out: list[dict] = []
    threaded = 0
    for r in rows:
        item: dict[str, Any] = {
            # A shared file link as its handle, never the URL (R-18): this is a
            # tool result.
            "sender": _speaker(r, display_name),
            "body": _file_handles(r.body),
            "when": stamp(r.ts, now),
        }
        if with_ids:
            # A Dreamer pass's citation handle: the same number its batch lines carry
            # as `n`. Never on a tag turn (R-18).
            item = {"n": r.id, **item}
        if r.turn_handle is not None:
            item["turn"] = r.turn_handle
        if r.edit is not None:
            item["edited"] = stamp(r.edit.ts, now)
            item["was"] = _file_handles(r.edit.was)
        if r.reactions is not None and r.reactions.recorded:
            # Additive provenance (platform-observations, first reactions
            # slice): the same fold the prompt annotates with. `reactions`
            # carries the standing counts `record_query` defined, each stored
            # emoji projected to its bounded display handle (a custom emoji's
            # platform id never reaches a tool result), merged when handles
            # collide and capped at REACTION_PAIRS_MAX pairs with the rest in
            # `reactions_more`; `reactions_at` is the newest recorded SOURCE
            # event, distinct from `when`; `reactions_source` is the
            # coverage — recorded transitions only, never the complete
            # upstream set — and `partial` marks a state the read's own bound
            # cut short.
            pairs = r.reactions.display_counts
            if r.reactions.coverage == "partial":
                item["reactions_partial"] = True
            elif pairs:
                item["reactions"] = dict(pairs[:REACTION_PAIRS_MAX])
                if len(pairs) > REACTION_PAIRS_MAX:
                    item["reactions_more"] = sum(n for _, n in pairs[REACTION_PAIRS_MAX:])
            else:
                item["reactions_removed"] = True
            if r.reactions.observed_at is not None:
                item["reactions_at"] = stamp(r.reactions.observed_at, now)
            item["reactions_source"] = r.reactions.coverage
        parent = by_ts.get(r.quoted_ts) if r.quoted_ts is not None else None
        if parent is not None and parent.id != r.id:
            item["reply_to"] = _speaker(parent, display_name)
            threaded += 1
        out.append(item)
    if on_rows is not None:
        on_rows([replace(row, body=item["body"]) for row, item in zip(rows, out, strict=True)])
    # Typed source events of the same window, interleaved at their own
    # time — the same chronological merge the prompt's transcript does.
    # A message the source pinned an event to is named by its own item
    # number: `on` counts only message items (1-based), the same positional
    # handle the prompt's `on="n"` gives the `#n` index.
    if source_events:
        index_of = {r.id: i for i, r in enumerate(rows, 1)}
        merged: list[dict] = []
        i = 0
        for e in sorted(source_events, key=lambda e: e.at_ms):
            while i < len(rows) and rows[i].ts <= e.at_ms:
                merged.append(out[i])
                i += 1
            merged.append(_source_event_item(e, now=now, on=index_of.get(e.target_message_id)))
        merged.extend(out[i:])
        out = merged
    return out, threaded


_BEFORE_FOLDED = re.compile(
    r"\b(?:HKT|mon|tue|wed|thu|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\b",
    re.IGNORECASE,
)

_BEFORE_SHAPE = re.compile(r"(\d{4}-\d{2}-\d{2})(?: (\d{1,2}:\d{2})(?::(\d{2})(?:\.(\d{3}))?)?)?")


def _before_bound(before: str | None, now: datetime) -> int | None:
    """`before` as an exclusive epoch-ms bound on `ts`, or `None` to read the"""
    if before is None:
        return None
    text = re.sub(r"\((?:just now|\d+[mhd] ago)\)", " ", str(before))
    text = re.sub(r"(?<=\d)T(?=\d)", " ", text)
    text = _BEFORE_FOLDED.sub(" ", text)
    text = " ".join(text.split())
    if not text:
        return None
    shape = _BEFORE_SHAPE.fullmatch(text)
    if shape is None:
        raise ValueError(before)
    day, minute, second, millis = shape.groups()
    if minute is None:
        moment = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_HKT) + timedelta(days=1)
    else:
        start = datetime.strptime(
            f"{day} {minute}:{second or '00'}",
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=_HKT)
        if millis is not None:
            moment = start + timedelta(milliseconds=int(millis))
        elif second is not None:
            moment = start + timedelta(seconds=1)
        else:
            moment = start + timedelta(minutes=1)
    if moment >= now:
        return None
    return int(moment.timestamp() * 1000)


_OBSERVATION_KINDS = (
    "poll",
    "room",
    "membership",
    "pin",
    "artifact",
    "reaction",
    "message_metadata",
)

_DISCOVERY_LIMIT = 20

_HISTORY_LIMIT = 50

_RECORD_SOURCE_MAX = 24

_TARGET_MAX = 8

_RECORD_QUERY_OFF = "I can't search what was said right now. Say so; do not guess what was said."

_RECORD_QUERY_TIME = (
    'I could not read that time. Give it as "YYYY-MM-DD HH:MM" Hong Kong time, '
    'or "YYYY-MM-DD" for a whole day.'
)

_RECORD_QUERY_NO_AUTHOR = (
    "Nobody in this conversation goes by that. Check the name with "
    "lookup_person; do not guess who was meant."
)

_RECORD_QUERY_NONE = (
    "No recorded message in that stretch matches. Say you found none there, "
    "not that it was never said."
)

_RECORD_QUERY_WITHHELD = " Rooms whose people are not all in this conversation were not searched."

_RECORD_QUERY_MORE = (
    "More messages matched than are shown. Narrow start and end, or add author or text."
)

_RECORD_QUERY_DELETED = (
    "A message marked deleted was taken back by its author: say it was deleted, never what it said."
)

_RECORD_QUERY_NEAR = (
    "A message marked near has words close to these but not these words; do "
    "not quote it as saying them."
)

_RECORD_QUERY_AS_OF_TARGET = "An as-of read does not attach the affected message's current text."

_REACTIONS_NOTE = (
    "Reaction counts fold recorded changes, including captured clears and "
    "snapshots. They are not the complete upstream set; uncaptured changes "
    "remain unknown. `reactions_removed` means no recorded reaction remains; "
    "`reactions_partial` means incomplete or unproven state with counts withheld, "
    "`reactions_more` how many standing reactions were left out, and "
    "`reactions_at` the newest recorded reaction event's time."
)


def _start_bound(start: str, now: datetime) -> int:
    """`start` as an INCLUSIVE epoch-ms bound on `ts`: the beginning of the"""
    text = re.sub(r"\((?:just now|\d+[mhd] ago)\)", " ", str(start))
    text = re.sub(r"(?<=\d)T(?=\d)", " ", text)
    text = " ".join(_BEFORE_FOLDED.sub(" ", text).split())
    shape = _BEFORE_SHAPE.fullmatch(text)
    if shape is None:
        raise ValueError(start)
    day, minute, second, millis = shape.groups()
    moment = datetime.strptime(
        f"{day} {minute or '00:00'}:{second or '00'}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=_HKT)
    if millis is not None:
        moment += timedelta(milliseconds=int(millis))
    return int(moment.timestamp() * 1000)


def _record_interval(start: str, end: str | None, now: datetime) -> tuple[int, int]:
    """`record_query`'s `[start, end)` in epoch ms. `end` is `_before_bound`'s"""
    upper = _before_bound(end, now) if end is not None else None
    now_ms = int(now.timestamp() * 1000)
    return _start_bound(start, now), (upper if upper is not None else now_ms + 1)


_BEFORE_UNREADABLE = (
    'I could not read that `before`. Give it as "YYYY-MM-DD HH:MM" Hong Kong '
    'time, or "YYYY-MM-DD" for the end of that day.'
)


async def _read_window(
    store: Any,
    *,
    conversation_id: str,
    asked: int,
    before_ts: int | None,
    as_of_ms: int | None = None,
) -> tuple[list[TranscriptRow], bool]:
    """The newest `asked` rows (ending before `before_ts` when given), and"""
    fetch = asked + 1
    bound = before_ts
    if as_of_ms is not None:
        bound = as_of_ms + 1 if bound is None else min(bound, as_of_ms + 1)
    if bound is None:
        rows = await store.recent(conversation_id=conversation_id, limit=fetch)
    else:
        rows = await store.recent_before(
            conversation_id=conversation_id,
            before_ts=bound,
            limit=fetch,
            as_of_ms=as_of_ms,
        )
    return _trim_window(rows, asked=asked)


def _trim_window(
    rows: Sequence[TranscriptRow],
    *,
    asked: int,
) -> tuple[list[TranscriptRow], bool]:
    """Trim a probed window without splitting its oldest timestamp group."""
    if len(rows) <= asked:
        return list(rows), False
    kept = list(rows[-asked:])
    tie = rows[-asked - 1].ts
    if kept[0].ts == tie:
        untied = [r for r in kept if r.ts != tie]
        if untied:
            kept = untied
    return kept, True


def _older_note(rows: Sequence[TranscriptRow]) -> str:
    """The paging hint, paid only when older rows exist (ADR 0015 §3). It"""
    oldest = datetime.fromtimestamp(rows[0].ts / 1000, tz=_HKT)
    return (
        "Older messages exist before the first one here. To read further back, "
        f'call again with before="{oldest:%Y-%m-%d %H:%M:%S}.{rows[0].ts % 1000:03d}".'
    )


_MEDIA_MARKER_RE = re.compile(
    r"\[(?:"
    + "|".join(re.escape(x) for x in sorted(MEDIA_LABELS, key=len, reverse=True))
    + r")(?:[ \t]+[A-Za-z0-9_-]{4,48}(?::[^\]]*)?)?\]",
    re.IGNORECASE,
)


def _strip_media_markers(text: str) -> str:
    """Remove `[image a3f2c1]`-style markers from outbound reply text."""
    if not text:
        return text
    return _MEDIA_MARKER_RE.sub("", text)


_INBOX_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,8}")


def _inbox_path(sha256: str, media_type: str, filename: str) -> str:
    """Where `computer_import` puts a file: ALWAYS `inbox/<12 hex>[.ext]`, the"""
    suffix = ""
    dot = filename.rfind(".")
    if dot > 0 and _INBOX_SUFFIX.fullmatch(filename[dot:]):
        suffix = filename[dot:].lower()
    else:
        guessed = mimetypes.guess_extension(media_type.split(";", 1)[0].strip()) or ""
        suffix = guessed if _INBOX_SUFFIX.fullmatch(guessed) else ""
    return f"inbox/{sha256[:12]}{suffix}"
