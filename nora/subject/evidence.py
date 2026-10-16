"""The records a recall case names, read from its sandbox AS THEY STOOD AT THE
INSTANT — the ground a judge grades a recalled claim against.

A recall reply is graded on
whether what it claims to remember is supported, and the support is not the
window she was shown: it is the photo's stored reading, the note's revision
chain, the outing the Rollup recorded. A case names those records by TYPED ID
(`evals.recall.EVIDENCE_KEYS`); this reads them.

FROM THE SANDBOX, AT THE INSTANT. A full-restore sandbox (`sandbox_as_of`) holds
every store as of the case's instant with its own ids and its own
`row_history` up to then. By judge time the turn has run and may have written
(a note revision, a retired note, an event), so the heads are no longer the
instant's. Every read here is therefore the as-of read production's history
defines (`nora.infra.sql.history`): an in-place table's version open at the
instant in `row_history`, an append-only table's rows recorded by then. The
turn's own writes are recorded at the wall clock, years after a historical
instant, so none of them is read.

FAIL-CLOSED. An id the sandbox did not hold at the instant, or one outside the
case's own room (a message, an event) or unit (a note, an outing), RAISES. A
judge handed half the evidence grades a confident recall against nothing, and
that must be an ERROR, never a verdict.

What the judge may see. No account id leaves here as a name: speakers are
person ids for the renderer to name through the roster, and a sender the roster
does not know keeps only its self-set profile name, as `judge_source` does.
Coordinates are never read. A stored reading that holds an identifier is
WITHHELD, the screen the media join applies before a reading reaches any model
(`nora.domain.media_join`'s injected `scan_identifiers`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from nora.agent.egress import scan_identifiers
from nora.infra.sql import create_readonly_sql_engine

from .obtain import Subject

__all__ = [
    "Evidence",
    "EvidenceUnavailableError",
    "HeldEvent",
    "HeldMedia",
    "HeldMessage",
    "HeldNote",
    "HeldOuting",
    "HeldRevision",
    "evidence_at",
]

_DAY_MS = 86_400_000


class EvidenceUnavailableError(ValueError):
    """A named record the sandbox did not hold at the instant, or one outside
    the case's room or unit. The case cannot be judged on it."""


@dataclass(frozen=True, slots=True)
class HeldMessage:
    id: int
    ts: int
    is_nora: bool
    #: The roster's person id, for the renderer to name; `None` when the row
    #: carried none.
    person_id: str | None
    #: The sender's SELF-SET profile name, the fallback `judge_source` uses.
    sender_name: str
    body: str
    #: `(sha256, media_type)` of each attachment, from `media_json`.
    media: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class HeldMedia:
    sha256: str
    media_type: str
    title: str
    description: str
    content: str
    #: The reading held an identifier and is not handed on.
    withheld: bool


@dataclass(frozen=True, slots=True)
class HeldRevision:
    written_at: int
    body: str
    #: The note's head at the instant.
    current: bool


@dataclass(frozen=True, slots=True)
class HeldNote:
    id: str
    title: str
    anchor_date: int | None
    retired: bool
    revisions: tuple[HeldRevision, ...]


@dataclass(frozen=True, slots=True)
class HeldEvent:
    id: str
    title: str
    #: When it starts and ends, epoch ms; `None` when the row set none.
    when_ms: int | None
    end_ms: int | None
    venue: str | None
    status: str
    people: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HeldOuting:
    id: int
    valid_from_ms: int
    valid_to_ms: int
    persons: tuple[str, ...]
    place_label: str | None


@dataclass(frozen=True, slots=True)
class Evidence:
    at: datetime
    messages: tuple[HeldMessage, ...] = ()
    media: tuple[HeldMedia, ...] = ()
    notes: tuple[HeldNote, ...] = ()
    events: tuple[HeldEvent, ...] = ()
    outings: tuple[HeldOuting, ...] = ()
    #: `None` when the case names no window; otherwise its length in days and
    #: every outing of the unit's current Rollup build inside it.
    window_days: int | None = None
    window_outings: tuple[HeldOuting, ...] = ()


#: The version of one in-place row open at `:at`, `history.as_of`'s own
#: predicate narrowed to one key.
_HELD = sa.text(
    "SELECT image FROM row_history WHERE table_name = :t "
    "AND row_key = CAST(:k AS jsonb) AND recorded_at <= :at "
    "AND (superseded_at IS NULL OR superseded_at > :at) AND op <> 'delete' "
    "ORDER BY recorded_at DESC, id DESC LIMIT 1"
)
#: Every version of a table open at `:at` whose image matches a predicate on
#: `image`, for the Rollup reads.
_OPEN_VERSIONS = (
    "SELECT image FROM row_history WHERE table_name = :t AND recorded_at <= :at "
    "AND (superseded_at IS NULL OR superseded_at > :at) AND op <> 'delete' AND "
)


async def evidence_at(
    subject: Subject,
    *,
    at: datetime,
    messages: Sequence[int] = (),
    media: Sequence[str] = (),
    #: Notes and events by the first 8 hex of their uuid (`evals.recall`).
    notes: Sequence[str] = (),
    events: Sequence[str] = (),
    outings: Sequence[int] = (),
    window_days: int | None = None,
) -> Evidence:
    """The named records as `subject`'s sandbox held them at `at`. Raises
    `EvidenceUnavailableError` for any it did not hold, or that lie outside
    the subject's room or unit."""
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware")
    platform, conversation_id = subject.room
    engine = create_readonly_sql_engine(subject.database_url)
    try:
        async with engine.connect() as conn:

            async def resolve(table: str, prefix: str) -> str:
                """The one uuid in `table` the sandbox held at `at` that
                starts with `prefix`; none or several is unavailable."""
                if not re.fullmatch(r"[0-9a-f]{8}", prefix):
                    raise EvidenceUnavailableError(f"{prefix!r} is not an 8-hex id prefix")
                found = (await conn.execute(sa.text(
                    "SELECT DISTINCT row_key->>'id' FROM row_history WHERE table_name = :t "
                    "AND row_key->>'id' LIKE :p AND recorded_at <= :at"
                ), {"t": table, "p": f"{prefix}%", "at": at})).scalars().all()
                if len(found) != 1:
                    raise EvidenceUnavailableError(
                        f"{len(found)} {table} rows held at {at.isoformat()} start with {prefix}"
                    )
                return str(found[0])

            async def held(table: str, key: dict[str, Any]) -> dict[str, Any]:
                image = (await conn.execute(_HELD, {
                    "t": table, "k": json.dumps(key), "at": at,
                })).scalar_one_or_none()
                if image is None:
                    raise EvidenceUnavailableError(
                        f"the sandbox held no {table} row {key} at {at.isoformat()}"
                    )
                return dict(image)

            out_messages = []
            for message_id in messages:
                row = await held("messages", {"id": int(message_id)})
                if (row.get("platform"), row.get("conversation_id")) != (platform, conversation_id):
                    raise EvidenceUnavailableError(
                        f"message {message_id} is not in the case's own room"
                    )
                out_messages.append(_message(row))

            out_media = []
            for sha in media:
                row = await held("media_objects", {"sha256": str(sha)})
                out_media.append(_media(row))

            out_notes = []
            for prefix in notes:
                note_id = await resolve("note", str(prefix))
                row = await held("note", {"id": note_id})
                if row.get("unit") != subject.unit:
                    raise EvidenceUnavailableError(f"note {note_id} is not in the case's own unit")
                revisions = (await conn.execute(sa.text(
                    "SELECT id, written_at, body FROM note_revision "
                    "WHERE note_id = CAST(:n AS uuid) AND recorded_at <= :at "
                    "ORDER BY written_at, recorded_at"
                ), {"n": str(note_id), "at": at})).all()
                if not revisions:
                    raise EvidenceUnavailableError(
                        f"note {note_id} held no revision at {at.isoformat()}"
                    )
                head = str(row.get("head_revision_id") or "")
                retired_at = row.get("retired_at")
                out_notes.append(HeldNote(
                    id=str(note_id), title=str(row.get("title") or ""),
                    anchor_date=row.get("anchor_date"),
                    retired=retired_at is not None and int(retired_at) <= _ms(at),
                    revisions=tuple(
                        HeldRevision(
                            written_at=int(r.written_at), body=str(r.body or ""),
                            current=str(r.id) == head,
                        )
                        for r in revisions
                    ),
                ))

            out_events = []
            for prefix in events:
                event_id = await resolve("events", str(prefix))
                row = await held("events", {"id": event_id})
                in_room = row.get("conversation_id") == conversation_id
                if not in_room and row.get("unit_id") != subject.unit:
                    raise EvidenceUnavailableError(
                        f"event {event_id} is neither in the case's room nor its unit"
                    )
                out_events.append(HeldEvent(
                    id=str(event_id), title=str(row.get("title") or ""),
                    when_ms=_instant(row.get("when"), what=f"event {event_id}'s start"),
                    end_ms=_instant(row.get("end"), what=f"event {event_id}'s end"),
                    venue=row.get("venue_name") or row.get("venue_text") or None,
                    status=str(row.get("status") or ""),
                    people=tuple(str(p) for p in row.get("people") or ()),
                ))

            out_outings = []
            if outings:
                current = await _current_build(conn, at=at)
            for outing_id in outings:
                row = await held("rollup_outing", {"id": int(outing_id)})
                if row.get("unit") != subject.unit:
                    raise EvidenceUnavailableError(
                        f"outing {outing_id} is not in the case's own unit"
                    )
                # An outing of a build that was not current at the instant is
                # one the Rollup had already replaced (or not yet switched to):
                # not what it recorded then.
                if int(row.get("build_id") or 0) != current:
                    raise EvidenceUnavailableError(
                        f"outing {outing_id} is of Rollup build {row.get('build_id')}, "
                        f"and build {current} was the current one at {at.isoformat()}"
                    )
                out_outings.append(_outing(row))

            window: tuple[HeldOuting, ...] = ()
            if window_days is not None:
                window = await _window(conn, unit=subject.unit, at=at, days=int(window_days))
    finally:
        await engine.dispose()
    return Evidence(
        at=at, messages=tuple(out_messages), media=tuple(out_media),
        notes=tuple(out_notes), events=tuple(out_events), outings=tuple(out_outings),
        window_days=window_days, window_outings=window,
    )


async def _current_build(conn: Any, *, at: datetime) -> int:
    """The id of the ONE Rollup build current at `at`. None is unavailable,
    never «no outings» (the Rollup did not exist yet), and several is a
    Rollup in a state no single answer describes."""
    builds = (await conn.execute(sa.text(
        _OPEN_VERSIONS + "image->>'status' = 'current'"
    ), {"t": "rollup_build", "at": at})).scalars().all()
    if len(builds) != 1:
        raise EvidenceUnavailableError(
            f"{len(builds)} Rollup builds were current at {at.isoformat()}; outings "
            "are read from exactly one"
        )
    return int(dict(builds[0])["id"])


async def _window(conn: Any, *, unit: str, at: datetime, days: int) -> tuple[HeldOuting, ...]:
    """Every outing of `unit` in the Rollup build that was CURRENT at `at`
    whose window overlaps the `days` before it."""
    build_id = await _current_build(conn, at=at)
    since = _ms(at) - days * _DAY_MS
    rows = (await conn.execute(sa.text(
        _OPEN_VERSIONS + "image->>'unit' = :u AND (image->>'build_id')::bigint = :b"
    ), {"t": "rollup_outing", "at": at, "u": unit, "b": build_id})).scalars().all()
    held = [_outing(dict(r)) for r in rows]
    return tuple(sorted(
        (o for o in held if o.valid_to_ms >= since and o.valid_from_ms <= _ms(at)),
        key=lambda o: (o.valid_from_ms, o.id),
    ))


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _instant(value: Any, *, what: str) -> int | None:
    """A `timestamptz` as `row_history` holds it (an ISO string in the jsonb
    image), as epoch ms. Unreadable RAISES: a time the judge cannot be told
    is not rendered as some other time."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise EvidenceUnavailableError(f"{what} is not a time: {value!r}") from exc
    if parsed.tzinfo is None:
        raise EvidenceUnavailableError(f"{what} carries no timezone: {value!r}")
    return _ms(parsed)


def _message(row: dict[str, Any]) -> HeldMessage:
    raw = row.get("media_json")
    try:
        items = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
    except ValueError:
        items = []
    media = tuple(
        (str(m.get("sha256") or ""), str(m.get("media_type") or ""))
        for m in items if isinstance(m, dict) and m.get("sha256")
    )
    return HeldMessage(
        id=int(row["id"]), ts=int(row["ts"]), is_nora=bool(row.get("is_nora")),
        person_id=row.get("person_id") or None,
        sender_name=str(row.get("sender_name") or ""),
        body=str(row.get("body") or ""), media=media,
    )


def _media(row: dict[str, Any]) -> HeldMedia:
    title = str(row.get("title") or "")
    description = str(row.get("description") or "")
    content = str(row.get("content") or "")
    withheld = any(scan_identifiers(part) for part in (title, description, content) if part)
    return HeldMedia(
        sha256=str(row["sha256"]), media_type=str(row.get("media_type") or ""),
        title="" if withheld else title,
        description="" if withheld else description,
        content="" if withheld else content,
        withheld=withheld,
    )


def _outing(row: dict[str, Any]) -> HeldOuting:
    # `lat`/`lng` are on the row and are never read: a coordinate does not
    # leave for a third-party judge (R-28).
    return HeldOuting(
        id=int(row["id"]), valid_from_ms=int(row["valid_from_ms"]),
        valid_to_ms=int(row["valid_to_ms"]),
        persons=tuple(str(p) for p in row.get("persons") or ()),
        place_label=row.get("place_label") or None,
    )
