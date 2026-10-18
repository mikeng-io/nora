"""`nora.agent.social_read.render_items`: the character bound, newest first.

Pure: no store and no Strands. The item cap and the scope are the tool's,
in `tests/infra/test_social_read.py`. Words are placeholders.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from nora.agent.social_read import counts_line, render_items
from nora.domain.social_stream import RecordedCounts, RecordedItem

NOW = datetime(2026, 10, 7, 4, 0, tzinfo=UTC)


def _item(caption: str, *, hours_ago: int, location: str | None = None) -> RecordedItem:
    return RecordedItem(
        network="instagram", kind="post", collab=False,
        taken_at=NOW - timedelta(hours=hours_ago), expires_at=None,
        caption=caption, location=location,
    )


def test_past_the_character_bound_every_older_item_is_cut_and_counted():
    items = [_item("x" * 40, hours_ago=1), _item("y" * 40, hours_ago=2),
             _item("z", hours_ago=3)]
    shown, withheld, cut = render_items(items, by="Ada", place=False, now=NOW, max_chars=60)
    assert [i["caption"] for i in shown] == ["x" * 40]
    # The third would fit on its own; it is older than one that did not, so it
    # is cut too and the answer has no gap in its middle.
    assert (withheld, cut) == (0, 2)


def test_the_newest_item_is_shown_even_when_it_alone_exceeds_the_bound():
    shown, _, cut = render_items([_item("x" * 90, hours_ago=1)], by="Ada", place=False,
                                 now=NOW, max_chars=60)
    assert len(shown) == 1 and cut == 0


def test_a_location_carrying_a_coordinate_is_dropped_alone():
    shown, withheld, _ = render_items(
        [_item("hello", hours_ago=1, location="10.5, 20.5")], by="Ada", place=True,
        now=NOW,
    )
    assert withheld == 0 and shown[0]["caption"] == "hello" and "location" not in shown[0]


def _counted(counts: RecordedCounts | None, *, caption: str = "hello") -> RecordedItem:
    return dataclasses.replace(_item(caption, hours_ago=5), counts=counts)


def test_an_items_counts_say_when_they_were_read_and_leave_out_what_is_unknown():
    read = NOW - timedelta(hours=3)
    shown, _, _ = render_items(
        [_counted(RecordedCounts(31, 4, None, False, read)),
         _counted(RecordedCounts(None, 1, 2, True, read)),
         _counted(RecordedCounts(1, None, None, False, read)),
         _counted(None)],
        by="Ada", place=False, now=NOW,
    )
    assert [i.get("counts") for i in shown] == [
        "31 likes, 4 comments, read 3h ago",
        "likes hidden by the author, 1 comment, 2 reposts, read 3h ago",
        "1 like, read 3h ago",
        None,
    ], "never counted carries none, and an unknown count is never 0"


def test_counts_spend_the_character_bound():
    counts = RecordedCounts(31, 4, None, False, NOW)
    line = counts_line(counts, NOW)
    items = [_counted(counts, caption="x" * 40), _counted(counts, caption="y")]
    room = 40 + len(line) + 1 + len(line)
    shown, _, cut = render_items(items, by="Ada", place=False, now=NOW, max_chars=room)
    assert (len(shown), cut) == (2, 0)
    shown, _, cut = render_items(items, by="Ada", place=False, now=NOW, max_chars=room - 1)
    assert (len(shown), cut) == (1, 1), "the second item's counts do not fit"
