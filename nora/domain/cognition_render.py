"""The agenda as the model reads it: a clock, one block per item and the index
lines of what is already recorded (design/cognition-loop/30-cognition.md §1;
#637 review, major 2). Pure: strings in, a string out, no store, no clock.

**Every question the session is asked, the input answers first** (AGENTS.md's
spine). She is asked whether a room settled a plan, is still deciding, or is
talking around it: that turns on WHO said WHAT, WHEN, and whether anyone
answered, so each item carries its messages with an author, a time and an age
(a block that cannot say which end is newest reads every line as «just now»,
measured on the relevance gate); a note may cite only the ids it rests on, so
each line carries its `[#id]`; «is this already written down» turns on the
Register, so the index lines follow the agenda. A line states what happened and
never what to conclude (§1): tags say «edited» and name a cue's kind, and stop
there.

**Bounded, and it says when it cut.** The whole Record block spends at most
`RenderBudget.record_chars`, shared across the items by what each needs, and a
message is clipped at `message_chars`. When an item's messages do not fit, the
OLDEST are dropped and a line says how many and which ids: a session that is
not told it was handed a slice reads the slice as the whole conversation.

**Group-derived text is one line.** A message body is collapsed to one line
(`nora.domain.prompt.one_line`) before it is quoted: a body that contained a
newline would otherwise write a second line indistinguishable from a real
agenda line, the forgery the relevance gate measured.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from nora.domain.blocks import speaker_name
from nora.domain.cognition_ground import (
    EventLine,
    Grounding,
    NoteLine,
    RecordItem,
    StandingLine,
    record_ref,
)
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.prompt import one_line
from nora.domain.stimuli import Stimulus


@dataclass(frozen=True, slots=True)
class RenderBudget:
    """How much of the prompt the grounding may spend. Every value is a
    STARTING guess like every number in this migration, sized from a shadow
    week's sessions and set in `[cognition]`."""

    #: All items' Record lines together.
    record_chars: int = 8000
    #: One message's words.
    message_chars: int = 400
    #: What a single item is always allowed, however many items share the rest.
    min_item_chars: int = 900
    #: The Register index lines, notes and per-room lines each.
    register_lines: int = 40
    register_chars: int = 3600


def room_key(room: Room) -> str:
    return f"{room.platform}:{room.conversation_id}"


def ago(delta_ms: int) -> str:
    """A span as a person says it: «just now», «42 min», «3 h 5 min», «2 d»."""
    minutes = max(delta_ms, 0) // 60_000
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 24 * 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} h {rest} min ago" if rest else f"{hours} h ago"
    return f"{minutes // (24 * 60)} d ago"


def clock_line(now_ms: int, last_session_ms: int | None, tz: tzinfo) -> str:
    """The time now in the group's own day, and how long since the last look.
    An agenda whose input cannot say WHEN reads every item as «just now»
    (AGENTS.md: a judgement that turns on time needs the time in its input)."""
    now = datetime.fromtimestamp(now_ms / 1000, tz=tz)
    stamp = f"{now:%A %-d %B %Y, %H:%M} ({getattr(tz, 'key', None) or now.tzname()})"
    since = (
        "This is your first look at this group."
        if last_session_ms is None
        else f"You last looked at this group {ago(now_ms - last_session_ms)}."
    )
    return f"It is {stamp}. {since}"


def _when(at_ms: int, now_ms: int, tz: tzinfo) -> str:
    """`14:05 (1 h 12 min ago)`, with the day when it is not today."""
    at = datetime.fromtimestamp(at_ms / 1000, tz=tz)
    today = datetime.fromtimestamp(now_ms / 1000, tz=tz).date()
    face = f"{at:%H:%M}" if at.date() == today else f"{at:%a %-d %b %H:%M}"
    return f"{face} ({ago(now_ms - at_ms)})"


def _item_age(item: AgendaItem, times: Sequence[int], now_ms: int, tz: tzinfo) -> str:
    if not times:
        return ""
    newest, oldest = max(times), min(times)
    at = f"{datetime.fromtimestamp(oldest / 1000, tz=tz):%H:%M}"
    if newest - oldest < 60_000:
        return f" (at {at}, {ago(now_ms - newest)})"
    to = f"{datetime.fromtimestamp(newest / 1000, tz=tz):%H:%M}"
    return f" (from {at} to {to}, the newest {ago(now_ms - newest)})"


def returning(strike: int) -> str:
    """A returning item says so: without it two items of one room (the fresh
    messages and the ones an earlier session left open) read the same."""
    if strike <= 0:
        return ""
    noun = "session" if strike == 1 else "sessions"
    return f" (returning: left open by {strike} earlier {noun})"


def _clip(text: str, limit: int) -> str:
    line = one_line(text)
    if len(line) <= limit:
        return line
    return line[: max(limit - 1, 1)].rstrip() + "…"


def _record_line(
    rec: RecordItem, cues: Sequence[str], *, now_ms: int, tz: tzinfo, message_chars: int,
) -> str:
    who = speaker_name(rec.author)
    when = _when(rec.at_ms, now_ms, tz)
    tags = f" [cues: {', '.join(cues)}]" if cues else ""
    if rec.kind == "delete":
        return (
            f"  [#{rec.message_id}] {who}, {when}: deleted by its author "
            f"(the words are withdrawn){tags}"
        )
    words = "" if rec.text is None else _clip(rec.text, message_chars)
    if rec.kind == "reaction":
        return (
            f"  someone reacted {rec.emoji or '?'} to [#{rec.message_id}] {who}"
            f"{': ' + words if words else ''}, {when}{tags}"
        )
    edited = " (edited)" if rec.kind == "edit" or rec.edited else ""
    body = words if rec.text is not None else "(the words are withdrawn)"
    return f"  [#{rec.message_id}] {who}, {when}{edited}: {body}{tags}"


def _fit(lines: list[str], allowance: int) -> tuple[list[str], int]:
    """The NEWEST lines that fit `allowance`, in order, and how many older ones
    were left out. At least one line is always kept: an item whose newest
    message is longer than its share still shows it (clipped)."""
    kept: list[str] = []
    spent = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if kept and spent + cost > allowance:
            break
        kept.append(line)
        spent += cost
    kept.reverse()
    return kept, len(lines) - len(kept)


def _shares(needs: Sequence[int], total: int, floor: int) -> list[int]:
    """Each item's allowance: the smallest need first, each given what it
    needs or an even share of what is left (never under `floor`), so a chatty
    room does not starve a quiet one and unused budget flows to the room that
    needs it."""
    order = sorted(range(len(needs)), key=lambda i: needs[i])
    out = [0] * len(needs)
    left = total
    for pos, i in enumerate(order):
        fair = max(floor, left // (len(order) - pos))
        out[i] = min(needs[i], fair)
        left = max(left - out[i], 0)
    return out


def _item_block(
    index: int, item: AgendaItem, stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int, tz: tzinfo, allowance_of: Mapping[int, int], message_chars: int,
) -> list[str]:
    rows = [stimuli[sid] for sid in item.stimulus_ids if sid in stimuli]
    head = (
        f"{index}. {item.summary}{returning(item.strike)} [room {room_key(item.room)}]"
        f"{_item_age(item, [s.at_ms for s in rows], now_ms, tz)}"
    )
    lines: list[tuple[int, int, str]] = []
    unreadable = 0
    for s in rows:
        ref = record_ref(s)
        if ref is None:
            continue
        rec = grounding.records.get(s.ref)
        if rec is None or rec.room != item.room:
            unreadable += 1
            continue
        lines.append((rec.at_ms, rec.message_id, _record_line(
            rec, s.cues, now_ms=now_ms, tz=tz, message_chars=message_chars,
        )))
    lines.sort(key=lambda t: (t[0], t[1]))
    body = [t[2] for t in lines]
    kept, cut = _fit(body, allowance_of.get(index, 0))
    out = [head]
    if cut:
        dropped = sorted(t[1] for t in lines[:cut])
        out.append(
            f"  ({cut} earlier message{'s' if cut != 1 else ''} not shown: "
            f"#{dropped[0]} to #{dropped[-1]})"
        )
    out.extend(kept)
    if unreadable:
        noun = "one" if unreadable == 1 else str(unreadable)
        out.append(f"  ({noun} of these could not be shown)")
    return out


def _needs(
    items: Sequence[AgendaItem], stimuli: Mapping[int, Stimulus], grounding: Grounding,
    *, now_ms: int, tz: tzinfo, message_chars: int,
) -> list[int]:
    needs = []
    for item in items:
        total = 0
        for sid in item.stimulus_ids:
            s = stimuli.get(sid)
            rec = None if s is None else grounding.records.get(s.ref)
            if s is not None and rec is not None and rec.room == item.room:
                total += len(_record_line(
                    rec, s.cues, now_ms=now_ms, tz=tz, message_chars=message_chars,
                )) + 1
        needs.append(total)
    return needs


def _note_line(n: NoteLine) -> str:
    return f"- note {n.id} \"{one_line(n.title)}\": {_clip(n.description, 200)}"


def _event_line(e: EventLine, now_ms: int, tz: tzinfo) -> str:
    when = "no time set" if e.when_ms is None else (
        f"{datetime.fromtimestamp(e.when_ms / 1000, tz=tz):%a %-d %b %Y %H:%M}"
    )
    venue = f", at {_clip(e.venue, 80)}" if e.venue else ""
    rev = f", revision {e.revision}" if e.revision else ""
    return f"- event {e.id} \"{one_line(e.title)}\", {e.status}, {when}{venue}{rev}"


def _standing_line(s: StandingLine, now_ms: int) -> str:
    return (
        f"- Standing (revision {s.revision}, folded {ago(now_ms - s.updated_at_ms)}): "
        f"{_clip(s.text, 300)}"
    )


def _register(
    rooms: Sequence[Room], grounding: Grounding, *, now_ms: int, tz: tzinfo, budget: RenderBudget,
) -> list[str]:
    out: list[str] = []
    if grounding.notes:
        out.append("Open notes of this group (index lines; note_read gives the body):")
        shown = grounding.notes[: budget.register_lines]
        out.extend(_note_line(n) for n in shown)
        if len(grounding.notes) > len(shown):
            out.append(f"- ({len(grounding.notes) - len(shown)} more open notes not listed)")
    for room in rooms:
        events = [e for e in grounding.events if e.room == room]
        standing = [s for s in grounding.standing if s.room == room]
        if not events and not standing:
            continue
        out.append(f"Already recorded for room {room_key(room)}:")
        out.extend(_event_line(e, now_ms, tz) for e in events[: budget.register_lines])
        out.extend(_standing_line(s, now_ms) for s in standing)
    if not out:
        return []
    text = "\n".join(out)
    if len(text) > budget.register_chars:
        cut = text[: budget.register_chars].rsplit("\n", 1)[0]
        return [*cut.split("\n"), "- (more index lines not listed)"]
    return out


def render_agenda(
    items: Sequence[AgendaItem], *, stimuli: Mapping[int, Stimulus] | None = None,
    grounding: Grounding | None = None, reads_note: str = "", now_ms: int | None = None,
    last_session_ms: int | None = None, tz: tzinfo = UTC,
    budget: RenderBudget | None = None,
) -> str:
    """The session's prompt. `stimuli` is the claimed stimuli by id (an item
    names its own by id), `grounding` what code read for them.

    Without `now_ms` (a probe's hand-written item) there is no clock and no
    age; without `grounding` an item is its summary alone, which is exactly
    what the eval probe hands the model and nothing else does."""
    spend = budget or RenderBudget()
    ground = grounding or Grounding()
    times = stimuli or {}
    header = []
    if now_ms is not None:
        header.append(clock_line(now_ms, last_session_ms, tz))
    if reads_note:
        header.append(reads_note)
    now = 0 if now_ms is None else now_ms
    needs = _needs(items, times, ground, now_ms=now, tz=tz, message_chars=spend.message_chars)
    shares = _shares(needs, spend.record_chars, spend.min_item_chars)
    allowance = dict(enumerate(shares))
    blocks: list[str] = []
    for i, item in enumerate(items):
        if now_ms is None:
            blocks.append(
                f"{i}. {item.summary}{returning(item.strike)} [room {room_key(item.room)}]"
            )
            continue
        blocks.extend(_item_block(
            i, item, times, ground, now_ms=now_ms, tz=tz, allowance_of=allowance,
            message_chars=spend.message_chars,
        ))
    rooms: list[Room] = []
    for item in items:
        if item.room not in rooms:
            rooms.append(item.room)
    register = (
        [] if now_ms is None
        else _register(rooms, ground, now_ms=now_ms, tz=tz, budget=spend)
    )
    text = "\n".join([*header, *blocks])
    return f"{text}\n\n" + "\n".join(register) if register else text


__all__ = [
    "RenderBudget", "ago", "clock_line", "render_agenda", "returning", "room_key",
]
