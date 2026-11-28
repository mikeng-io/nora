"""Quote custody and the last-look clock in the shared context renderer."""

from dataclasses import replace
from datetime import UTC, datetime

from nora.domain.context_data import TranscriptLine
from nora.language.context_render import render_clock, transcript_rows
from nora.testing.fakes import transcript_rows as seed_rows

NOW = datetime(2026, 9, 29, 4, 0, tzinfo=UTC)


def _lines(rows, *, platform="signal", rooms=None):
    return [
        TranscriptLine(
            row=row, room="Room", platform=platform,
            conversation_id=(rooms[i] if rooms else "room-a"),
        )
        for i, row in enumerate(rows)
    ]


def _rendered(lines):
    return transcript_rows(lines, display_name="Nora", now=NOW)


def test_a_quote_resolves_to_the_first_row_sharing_its_timestamp():
    first, second, reply = seed_rows(
        ["one", "two", "answer"], sender_id="+852111", sender_name="Miles",
        person_id="miles_novak", ts_base=1_000_000,
    )
    second = replace(second, ts=first.ts)
    reply = replace(reply, quoted_ts=first.ts)
    rendered = _rendered(_lines([first, second, reply]))
    assert 'replying_to="1"' in rendered
    assert 'replying_to="2"' not in rendered


def test_identically_timestamped_rooms_keep_separate_quote_custody():
    a, b, reply = seed_rows(
        ["in a", "in b", "answer"], sender_id="+852111", sender_name="Miles",
        person_id="miles_novak", ts_base=1_000_000,
    )
    b = replace(b, ts=a.ts)
    reply = replace(reply, quoted_ts=a.ts)
    rendered = _rendered(_lines([a, b, reply], rooms=["room-a", "room-b", "room-b"]))
    assert 'replying_to="2"' in rendered
    assert 'replying_to="1"' not in rendered


def test_the_clock_says_how_long_ago_the_last_look_was_to_the_minute():
    block = render_clock(now=NOW, last_session_ms=int(NOW.timestamp() * 1000) - 42 * 60_000)
    assert "You last looked at this group 42 min ago." in block.body.text
