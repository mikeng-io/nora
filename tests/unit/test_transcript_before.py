"""`before` on the transcript tools.

Both tools return only the newest rows (at most 60), so «what did we say
yesterday» cannot reach yesterday in a room that had said more than a
window's worth since. These pin what `before` must do: jump straight to the
time, page without a hole, say when older rows exist and exactly how to reach
them, and never answer an unreadable time with today's messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nora.agent.room import Room
from nora.agent.tools import _before_bound, build_tools
from nora.domain.models import NormalizedMessage
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from tests.fakes import SinkSender

HKT = timezone(timedelta(hours=8), "HKT")
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=HKT)          # Friday morning
CONV = "g1"

_UNREACHABLE_TRANSIT = TransitClient(
    kmb_base_url="http://127.0.0.1:1", mtr_schedule_url="http://127.0.0.1:1/s",
    mtr_routes_url="http://127.0.0.1:1/r.csv", timeout_seconds=0.5,
)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _at(day: int, hour: int, minute: int, second: int = 0) -> int:
    return _ms(datetime(2026, 9, day, hour, minute, second, tzinfo=HKT))


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    return load_persons(path)


async def _store(*stamped: tuple[int, str]) -> FakeRecordStore:
    store = FakeRecordStore()
    for ts, body in stamped:
        await store.append(message=NormalizedMessage(
            "signal", CONV, "group", "+852111", "Miles", ts, body, {}))
    return store


def _load_transcript(persons, store):
    room = Room(platform="signal", conversation_id=CONV, roster_source=None, persons=persons)
    tools = build_tools(
        store=store, conversation_id=CONV, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="what did we say yesterday", context_window=40,
        clock=lambda: NOW,
    )
    return {t.tool_name: t for t in tools}["load_transcript"]


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


# ---- the bound ---------------------------------------------------------------


def test_a_minute_a_person_gives_includes_the_whole_minute():
    assert _before_bound("2026-09-24 21:14", NOW) == _at(24, 21, 15)
    assert _before_bound("2026-09-24 21:14:30", NOW) == _at(24, 21, 14, 31)


def test_the_paging_cursor_is_an_exact_exclusive_moment():
    assert _before_bound("2026-09-24 21:14:30.250", NOW) == _at(24, 21, 14, 30) + 250


def test_a_date_alone_is_the_end_of_that_day():
    assert _before_bound("2026-09-24", NOW) == _at(25, 0, 0)


def test_a_when_copied_from_a_row_reads_as_its_minute():
    """What she is most likely to pass back: a row's own `when`."""
    for copied in ("2026-09-24 Thu 21:14 (13h ago)", "2026-09-24T21:14",
                   "2026-09-24 21:14 HKT", " 2026-09-24  21:14 "):
        assert _before_bound(copied, NOW) == _at(24, 21, 15), copied
    # An ISO time with seconds names that second, not the minute.
    assert _before_bound("2026-09-24T21:14:00", NOW) == _at(24, 21, 14, 1)


def test_now_or_later_and_nothing_at_all_are_the_newest_read():
    for newest in (None, "", "2026-09-25 10:00", "2026-09-26", "2027-01-01 00:00"):
        assert _before_bound(newest, NOW) is None, newest


def test_a_time_it_cannot_read_is_an_error_never_a_guess():
    for unreadable in ("yesterday", "24/09/2026", "2026-09-24 25:00", "2026-02-30",
                       "last week", "yesterday 2026-09-24", "Sep 2026-09-24"):
        with pytest.raises(ValueError):
            _before_bound(unreadable, NOW)


def test_another_zone_is_unreadable_not_eight_hours_off():
    """A three-letter word is not dropped blindly: «21:14 UTC» must not read as
    21:14 Hong Kong time. Only a weekday and HKT fold."""
    for zoned in ("2026-09-24 21:14 UTC", "2026-09-24 21:14 GMT",
                  "2026-09-24 21:14 PST", "2026-09-24 21:14 (UTC)"):
        with pytest.raises(ValueError):
            _before_bound(zoned, NOW)
    for fine in ("2026-09-24 Thursday 21:14", "2026-09-24 thu 21:14", "2026-09-24 21:14 hkt"):
        assert _before_bound(fine, NOW) == _at(24, 21, 15), fine


# ---- load_transcript ---------------------------------------------------------


async def test_before_reaches_yesterday_past_a_full_window_of_today(persons):
    """The case the argument exists for: 70 messages this morning bury last
    night's three, and the newest read cannot reach them."""
    today = [(_at(25, 8, 0) + i * 1000, f"today {i}") for i in range(70)]
    last_night = [(_at(24, 20, 0), "venue is the rooftop"),
                  (_at(24, 20, 5), "7pm on Saturday"),
                  (_at(24, 20, 9), "bring cash")]
    tool = _load_transcript(persons, await _store(*last_night, *today))

    newest = await _call(tool, limit=60)
    assert "venue is the rooftop" not in [m["body"] for m in newest["messages"]]

    out = await _call(tool, before="2026-09-24")
    assert [m["body"] for m in out["messages"]] == [
        "venue is the rooftop", "7pm on Saturday", "bring cash",
    ]
    assert out["messages"][0]["when"] == "2026-09-24 Thu 20:00 (14h ago)"
    assert "note" not in out                  # nothing older, nothing to say


async def test_a_full_window_names_the_exact_value_that_reads_further_back(persons):
    rows = [(_at(24, 12, i), f"line {i}") for i in range(30)]
    tool = _load_transcript(persons, await _store(*rows))

    out = await _call(tool, limit=10)
    assert out["messages"][0]["body"] == "line 20"
    assert 'before="2026-09-24 12:20:00.000"' in out["note"]


async def test_paging_across_a_crowded_minute_moves_on_and_skips_nothing(persons):
    """Four rows share 12:00 and a page holds three. The cursor is
    the oldest row's exact moment, not its minute: a minute-inclusive cursor
    would hand back the same page forever here."""
    rows = [(_at(24, 11, 58), "a"), (_at(24, 11, 59), "b"),
            (_at(24, 12, 0, 5), "c"), (_at(24, 12, 0, 20), "d"),
            (_at(24, 12, 0, 40), "e"), (_at(24, 12, 0, 50), "f"),
            (_at(24, 12, 1), "g")]
    tool = _load_transcript(persons, await _store(*rows))

    seen: list[str] = []
    page = await _call(tool, limit=3)
    for _ in range(5):
        seen = [m["body"] for m in page["messages"]] + seen
        if "note" not in page:
            break
        before = page["note"].split('before="')[1].split('"')[0]
        page = await _call(tool, limit=3, before=before)
    assert "note" not in page, "paging never reached the oldest row"
    assert seen == list("abcdefg")            # every row once, in order


async def test_paging_never_strands_a_row_that_shares_its_timestamp(persons):
    """WhatsApp stamps whole seconds, so b and c share one `ts`. With the page
    ending between them, an exclusive cursor at that `ts` would leave b
    unreachable (pages read a, c, d, e)."""
    same = _at(24, 12, 0, 2)
    rows = [(_at(24, 12, 0, 1), "a"), (same, "b"), (same, "c"),
            (_at(24, 12, 0, 3), "d"), (_at(24, 12, 0, 4), "e")]
    tool = _load_transcript(persons, await _store(*rows))

    seen: list[str] = []
    page = await _call(tool, limit=3)
    for _ in range(5):
        seen = [m["body"] for m in page["messages"]] + seen
        if "note" not in page:
            break
        before = page["note"].split('before="')[1].split('"')[0]
        page = await _call(tool, limit=3, before=before)
    assert seen == list("abcde")


async def test_a_year_past_the_calendar_is_unreadable_not_a_crash(persons):
    """9999-12-31 overflows `datetime`; it must not escape the tool as an
    OverflowError."""
    tool = _load_transcript(persons, await _store((_at(25, 9, 0), "today")))

    out = await _call(tool, before="9999-12-31")
    assert out["messages"] == []
    assert '"YYYY-MM-DD HH:MM"' in out["note"]


async def test_an_unreadable_before_reads_nothing_and_says_the_format(persons):
    store = await _store((_at(25, 9, 0), "today"))
    tool = _load_transcript(persons, store)

    out = await _call(tool, before="yesterday")
    assert out["messages"] == []
    assert '"YYYY-MM-DD HH:MM"' in out["note"]
    assert "untrusted" not in out            # no member's words in it


async def test_an_empty_before_read_says_so(persons):
    tool = _load_transcript(persons, await _store((_at(25, 9, 0), "today")))

    out = await _call(tool, before="2026-09-01")
    assert out["messages"] == []
    assert "before that time" in out["note"]


async def test_leaving_before_out_reads_the_newest_rows_as_it_always_did(persons):
    rows = [(_at(24, 12, i), f"line {i}") for i in range(5)]
    tool = _load_transcript(persons, await _store(*rows))

    out = await _call(tool)
    assert [m["body"] for m in out["messages"]] == [f"line {i}" for i in range(5)]
    assert "note" not in out


# ---------------------------------------------------------------------------
# what a Dreamer pass LOADED is citable
# ---------------------------------------------------------------------------


def _loader_with_rows(persons, store, on_rows):
    room = Room(platform="signal", conversation_id=CONV, roster_source=None, persons=persons)
    tools = build_tools(
        store=store, conversation_id=CONV, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="", context_window=40, clock=lambda: NOW, on_rows=on_rows,
    )
    return {t.tool_name: t for t in tools}["load_transcript"]


async def test_a_dreamer_load_carries_each_rows_id_and_reports_the_rows(persons):
    """A note body cites a message by its id; a row read through a tool with no
    id could never be cited, and a row the runtime never heard of could never
    be admitted as shown. Both halves, on one read."""
    store = await _store((_at(24, 21, 0), "dinner saturday?"), (_at(24, 21, 5), "sure"))
    seen: list = []
    result = await _call(_loader_with_rows(persons, store, seen.extend))
    ids = [m.get("n") for m in result["messages"]]
    assert all(isinstance(i, int) for i in ids), result["messages"]
    assert [r.id for r in seen] == ids, "the rows reported are the rows returned"


async def test_a_tag_turn_load_carries_no_id(persons):
    """`on_rows` unset — every tag turn — is the unchanged result (R-18)."""
    store = await _store((_at(24, 21, 0), "dinner saturday?"))
    result = await _call(_load_transcript(persons, store))
    assert all("n" not in m for m in result["messages"])
