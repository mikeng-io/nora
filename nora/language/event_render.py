"""Renders recorded events into an INDEX: one element per event, never a line.

Used by the Cognition session, whose register lists the events already
recorded for a room. A title is a member's words shaped by a model, and a flat
line (`- event <id> "<title>", <status>, <when>, at <venue>, revision <rev>`)
would allow the forgeries `note_render` closes for notes: a title carrying a
newline would write a second index entry, and one carrying `", confirmed, `
would write a status that was never recorded. As an element every FIELD is an
attribute, where `escape_attr` closes the quote a value cannot write, and the
title is the content, collapsed to one line so one event stays one row.

**What it carries**: the id (what the
session's `event_revise` and `event_set_status` take as `event_id`), the status,
when it is for (`no time set` when nothing is), the venue as it was said (cut to
`VENUE_CHARS`), and the head revision (what their `base_revision_id` takes),
dropped when there is none. Pure: rows in, a string out, no clock but the zone it
is handed.

It renders rows and nothing else, like `note_render.render_note_rows`: the
envelope (the `events` block, its room, how many rows a budget left out) belongs
to the occasion that serves it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, tzinfo

from nora.domain.blocks import render_elements
from nora.domain.cognition_ground import EventLine
from nora.domain.prompt import clip, one_line

#: A venue as it was said, cut to this many characters.
VENUE_CHARS = 80

#: What an event with no time says, as an attribute value: «no time set» is a
#: fact about the event, and a dropped attribute would read as «unknown».
NO_TIME = "no time set"


def event_when(when_ms: int | None, tz: tzinfo) -> str:
    """`Wed 30 Sep 2026 16:30` in the room's zone, or `NO_TIME`."""
    if when_ms is None:
        return NO_TIME
    return f"{datetime.fromtimestamp(when_ms / 1000, tz=tz):%a %-d %b %Y %H:%M}"


def render_event_rows(events: Sequence[EventLine], *, tz: tzinfo) -> str:
    """The index's ROWS, one `<event>` per event and nothing else.

    `venue` and `revision` DROP their attribute when there is nothing to say
    (`render_elements`' rule), so an event with no venue carries no `venue=""`.
    """
    return render_elements(
        events, tag="event",
        attrs=lambda e, _i: (
            ("id", e.id),
            ("status", e.status),
            ("when", event_when(e.when_ms, tz)),
            ("venue", clip(e.venue, VENUE_CHARS) if e.venue else None),
            ("revision", e.revision or None),
        ),
        body=lambda e: one_line(e.title),
    )
