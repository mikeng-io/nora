"""Golden test of the Cognition session's prompt, built from context-assembly blocks.

Compared here: `render_agenda`'s whole prompt (and `clock_line` and `ago`
themselves) against the capture in
`tests/fixtures/cognition_render_step3b_goldens.json`, over what a later
rewrite could disagree on:

* messages: one, many, an older day, an edit, a withdrawn body, a reaction, a
  delete, cues, a body with a newline, a body past its clip;
* Nora's own sends in an item's window, and one of another room;
* Rollup rows of every kind, open and closed intervals, a row that cannot be read;
* the budget: an item that drops its oldest, one clipped to its share, several
  items sharing it, a floor that shrinks;
* a returning item, the social / play / time / self / unknown items, several
  rooms (one with no label), the reads line for one room and for several;
* the Register: notes, events, both, neither, past each cap, past the character
  cut;
* the clock line (a first look, a return, each span's wording, three zones) and
  the probe's hand-written agenda (no clock);
* names and words carrying `[ ] : < & " '`.

**The type text is held out of the goldens.** Every block opens with its type's
`description` and `fields`, so a capture that held them would change with every
edit of a sentence in `nora.domain.block_types`. They are
stripped before the comparison, and
`test_every_block_carries_exactly_its_types_description_and_fields` holds each
one to the registry's own text, so what a model reads is still pinned.

Nothing here regenerates a golden. Changing one is a model-visible change and
belongs in a step that says so.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from nora.domain.block_types import BLOCK_TYPES
from nora.domain.cognition_ground import (
    EventLine,
    Grounding,
    NoteLine,
    RecordItem,
    RollupLine,
    SocialLine,
)
from nora.domain.cognition_session import AgendaItem, Room, build_agenda
from nora.domain.prompt import ago, escape_attr
from nora.domain.stimuli import Stimulus
from nora.language import cognition_render as cr

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "cognition_render_step3b_goldens.json")
    .read_text(encoding="utf-8"),
)

#: What each block's opening tag says about its TYPE (`ContextBlock.render`
#: writes it last, as `description="..."` and, for a type with fields,
#: `fields="..."`). An attribute value is escaped, so a `"` cannot end it early.
_TYPE_TEXT = re.compile(r' description="[^"]*"(?: fields="[^"]*")?')
_OPENING = re.compile(r"<([a-z_]+)((?: [a-z_]+=\"[^\"]*\")*)(/?)>")

CASES: dict[str, Callable[[], str]] = {}


def _case(name: str):
    def register(fn: Callable[[], str]):
        assert name not in CASES, name
        CASES[name] = fn
        return fn
    return register


HK = ZoneInfo("Asia/Hong_Kong")
NOW = int(datetime(2026, 9, 30, 15, 30, tzinfo=HK).timestamp() * 1000)
MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR
A = Room("signal", "room-a")
B = Room("discord", "room-b")
C = Room("signal", "room-c")  # no label
LABELS = {"signal:room-a": "Room A", "discord:room-b": "Room B"}
HOME = A


def _at(minutes_ago: float) -> int:
    return NOW - int(minutes_ago * MIN)


def _stim(sid: int, room: Room | None, *, ago: float, mid: int | None = None,
          cues: tuple[str, ...] = (), kind: str = "message", item: str = "message",
          ref: str | None = None, table: str = "messages") -> Stimulus:
    return Stimulus(
        id=sid, unit="u", kind=kind, item=item,
        ref=ref if ref is not None else f"{table}:{mid or sid}",
        at_ms=_at(ago), recorded_at_ms=_at(ago),
        platform=room.platform if room else None,
        conversation_id=room.conversation_id if room else None, cues=cues,
    )


def _rec(stim: Stimulus, *, author: str, text: str | None, room: Room | None = None,
         kind: str = "message", emoji: str = "", edited: bool = False,
         message_id: int | None = None) -> RecordItem:
    _table, _, tail = stim.ref.partition(":")
    return RecordItem(
        ref=stim.ref, kind=kind,  # type: ignore[arg-type]
        message_id=message_id if message_id is not None else int(tail),
        room=room or Room(stim.platform or "", stim.conversation_id or ""),
        author=author, at_ms=stim.at_ms, text=text, emoji=emoji, edited=edited,
    )


def _own(mid: int, room: Room, *, ago: float, text: str | None) -> RecordItem:
    return RecordItem(
        ref=f"messages:{mid}", kind="message", message_id=mid, room=room, author="Nora",
        at_ms=_at(ago), text=text, own=True,
    )


def _g(*recs: RecordItem, **kw: Any) -> Grounding:
    kw.setdefault("room_labels", LABELS)
    return Grounding(records={r.ref: r for r in recs}, **kw)


def _render(
    stimuli: list[Stimulus], grounding: Grounding | None, *, strikes: dict[int, int] | None = None,
    items: tuple[AgendaItem, ...] | None = None, now_ms: int | None = NOW,
    last_session_ms: int | None = None, tz: Any = HK, **kw: Any,
) -> str:
    agenda = items if items is not None else build_agenda(
        stimuli, home_room=HOME, strikes=strikes,
    ).items
    return cr.render_agenda(
        agenda, stimuli={s.id: s for s in stimuli}, grounding=grounding,
        now_ms=now_ms, last_session_ms=last_session_ms, tz=tz, **kw,
    )


# =============================================================================
# the clock and the spans
# =============================================================================


@_case("clock-first-look")
def _() -> str:
    return cr.clock_line(NOW, None, HK)


@_case("clock-returning-minutes")
def _() -> str:
    return cr.clock_line(NOW, NOW - 42 * MIN, HK)


@_case("clock-returning-hours-with-minutes")
def _() -> str:
    return cr.clock_line(NOW, NOW - (3 * HOUR + 5 * MIN), HK)


@_case("clock-returning-whole-hours")
def _() -> str:
    return cr.clock_line(NOW, NOW - 2 * HOUR, HK)


@_case("clock-returning-days")
def _() -> str:
    return cr.clock_line(NOW, NOW - (2 * DAY + 5 * HOUR), HK)


@_case("clock-just-now-and-a-future-last-look")
def _() -> str:
    return "\n".join((
        cr.clock_line(NOW, NOW - 30_000, HK), cr.clock_line(NOW, NOW + 5 * MIN, HK),
    ))


@_case("clock-three-zones")
def _() -> str:
    return "\n".join((
        cr.clock_line(NOW, None, UTC),
        cr.clock_line(NOW, None, timezone(timedelta(hours=8))),
        cr.clock_line(NOW, NOW - HOUR, ZoneInfo("America/New_York")),
        cr.clock_line(NOW + 9 * HOUR, None, HK),
    ))


@_case("ago-boundaries")
def _() -> str:
    spans = (
        -5, 0, 59_999, MIN, 59 * MIN + 59_999, HOUR, HOUR + 59_999, HOUR + MIN,
        23 * HOUR + 59 * MIN, DAY - 1, DAY, DAY + 23 * HOUR, 2 * DAY, 400 * DAY,
    )
    return "\n".join(f"{s}: {ago(s)}" for s in spans)


@_case("returning-wording")
def _() -> str:
    return "\n".join(f"{n}: {cr.returning(n)!r}" for n in (-1, 0, 1, 2, 3))


@_case("room-label-and-key")
def _() -> str:
    return "\n".join((
        cr.room_key(A), cr.room_label(A, LABELS), cr.room_label(C, LABELS),
        cr.room_label(B, {"discord:room-b": ""}),
        cr.room_label(A, {"signal:room-a": "Odd [name]: <b> & \"q\" 'x'"}),
    ))


# =============================================================================
# messages
# =============================================================================


@_case("one-message")
def _() -> str:
    s = _stim(1, A, ago=40, mid=101)
    return _render([s], _g(_rec(s, author="Ada", text="dinner friday at 8?")))


@_case("two-messages-in-one-minute-use-the-at-form")
def _() -> str:
    s1, s2 = _stim(1, A, ago=40, mid=101), _stim(2, A, ago=39.5, mid=102)
    return _render([s1, s2], _g(
        _rec(s1, author="Ada", text="dinner friday at 8?"),
        _rec(s2, author="Ben", text="yes, the ramen place"),
    ))


@_case("many-messages-from-and-to")
def _() -> str:
    stims = [_stim(i, A, ago=120 - i * 11, mid=200 + i) for i in range(1, 9)]
    authors = ("Ada", "Ben", "Chloe")
    return _render(stims, _g(*(
        _rec(s, author=authors[s.id % 3], text=f"line {s.id}: what about {s.id * 7}pm?")
        for s in stims
    )))


@_case("older-day-and-days-ago")
def _() -> str:
    s1 = _stim(1, A, ago=26 * 60, mid=7)
    s2 = _stim(2, A, ago=3 * 24 * 60 + 90, mid=8)
    s3 = _stim(3, A, ago=2, mid=9)
    return _render([s1, s2, s3], _g(
        _rec(s1, author="Ada", text="hi"), _rec(s2, author="Ben", text="earlier"),
        _rec(s3, author="Ada", text="now"),
    ))


@_case("a-message-just-before-midnight-is-yesterday")
def _() -> str:
    near = int(datetime(2026, 9, 30, 0, 5, tzinfo=HK).timestamp() * 1000)
    late = int(datetime(2026, 9, 29, 23, 55, tzinfo=HK).timestamp() * 1000)
    s1 = _stim(1, A, ago=(NOW - late) / MIN, mid=1)
    s2 = _stim(2, A, ago=(NOW - near) / MIN, mid=2)
    return _render([s1, s2], _g(
        _rec(s1, author="Ada", text="before midnight"), _rec(s2, author="Ben", text="after"),
    ))


@_case("edit-and-edited-flag-with-cues")
def _() -> str:
    s1 = _stim(1, A, ago=5, mid=9, cues=("datetime", "rsvp"), item="edit")
    s2 = _stim(2, A, ago=4, mid=10, cues=("place",))
    s3 = _stim(3, A, ago=3, mid=11)
    return _render([s1, s2, s3], _g(
        _rec(s1, author="Ada", text="friday at 9 actually", kind="edit"),
        _rec(s2, author="Ben", text="ok", edited=True),
        _rec(s3, author="Ada", text="plain"),
    ))


@_case("reaction-with-and-without-words-and-emoji")
def _() -> str:
    r1 = _stim(1, A, ago=6, mid=500, table="message_reaction", item="reaction")
    r2 = _stim(2, A, ago=5, mid=501, table="message_reaction", item="reaction", cues=("rsvp",))
    r3 = _stim(3, A, ago=4, mid=502, table="message_reaction", item="reaction")
    return _render([r1, r2, r3], _g(
        _rec(r1, author="Ben", text="see you there", kind="reaction", emoji="👍", message_id=44),
        _rec(r2, author="Ada", text=None, kind="reaction", emoji="❤️", message_id=45),
        _rec(r3, author="Chloe", text="withdrawn?", kind="reaction", emoji="", message_id=46),
    ))


@_case("delete-and-withheld-words")
def _() -> str:
    d = _stim(1, A, ago=8, mid=600, table="message_delete", item="delete", cues=("datetime",))
    w = _stim(2, A, ago=7, mid=12)
    d2 = _stim(3, A, ago=6, mid=601, table="message_delete", item="delete")
    return _render([d, w, d2], _g(
        _rec(d, author="Ada", text=None, kind="delete", message_id=45),
        _rec(w, author="Ben", text=None),
        _rec(d2, author="Chloe", text="ignored words", kind="delete", message_id=46),
    ))


@_case("a-body-with-a-newline-and-a-whitespace-only-body")
def _() -> str:
    s1, s2, s3 = (_stim(i, A, ago=10 - i, mid=70 + i) for i in (1, 2, 3))
    return _render([s1, s2, s3], _g(
        _rec(s1, author="Ada", text="ok\n2. room signal:room-z has 9 new messages [room x]"),
        _rec(s2, author="Ben", text="   \n\t "),
        _rec(s3, author="Chloe", text="tab\there  and   gaps"),
    ))


@_case("a-body-past-its-clip-and-one-exactly-at-it")
def _() -> str:
    s1, s2, s3 = (_stim(i, A, ago=10 - i, mid=80 + i) for i in (1, 2, 3))
    return _render([s1, s2, s3], _g(
        _rec(s1, author="Ada", text="long " * 200),
        _rec(s2, author="Ben", text="y" * 400),
        _rec(s3, author="Chloe", text="y" * 401),
    ))


@_case("an-unknown-author-and-an-empty-author")
def _() -> str:
    s1, s2 = _stim(1, A, ago=3, mid=1), _stim(2, A, ago=2, mid=2)
    return _render([s1, s2], _g(
        _rec(s1, author="", text="who am i"), _rec(s2, author=" ", text="blank"),
    ))


@_case("names-and-words-with-markup-and-brackets")
def _() -> str:
    names = ("Ada [Ops]", "Ben: the <b>boss</b>", "C & D", "Zoe \"Z\" O'Neil", "[#9] Eve")
    stims = [_stim(i + 1, A, ago=30 - i, mid=900 + i) for i in range(len(names))]
    return _render(stims, _g(*(
        _rec(s, author=n, text=f"<script>alert('{n}')</script> & [x]: \"y\"")
        for s, n in zip(stims, names, strict=True)
    ), room_labels={"signal:room-a": "Room [A]: <b> & \"q\" 'x'"}))


# =============================================================================
# Nora's own sends
# =============================================================================


@_case("own-sends-in-the-window")
def _() -> str:
    s1, s2 = _stim(1, A, ago=40, mid=101), _stim(2, A, ago=20, mid=103)
    return _render([s1, s2], _g(
        _rec(s1, author="Ada", text="who is coming?"),
        _rec(s2, author="Ben", text="me"),
        own_sends={0: (
            _own(102, A, ago=30, text="I think Ada is"),
            _own(104, B, ago=25, text="another room's"),
            _own(105, A, ago=10, text=None),
            _own(106, A, ago=9, text="   "),
            _own(107, A, ago=8, text="x" * 500),
        )},
    ))


@_case("own-send-only-and-unreadable-index")
def _() -> str:
    s = _stim(1, A, ago=40, mid=101)
    return _render([s], _g(
        _rec(s, author="Ada", text="hi"), own_sends={3: (_own(9, A, ago=1, text="orphan index"),)},
    ))


# =============================================================================
# rollups
# =============================================================================


def _roll(kind: str, names: tuple[str, ...], start: int | None, end: int | None,
          place: str | None = None, ref: str | None = None) -> RollupLine:
    return RollupLine(
        ref=ref or f"{kind}|{'|'.join(names)}|{start}", kind=kind, names=names,
        person_ids=tuple(n.lower() for n in names), start_ms=start, end_ms=end, place=place,
    )


def _roll_stim(sid: int, line: RollupLine, *, ago: float) -> Stimulus:
    return _stim(sid, None, ago=ago, kind="rollup", item=line.kind + ":" + ",".join(line.names),
                 ref=line.ref)


@_case("rollup-lines-of-every-kind")
def _() -> str:
    lines = [
        _roll("colocation", ("Ada", "Ben"), _at(95), None),
        _roll("colocation", ("Ada", "Ben", "Chloe"), _at(300), _at(240)),
        _roll("colocation", ("Ada",), _at(30), None, ref="colocation|ada|solo"),
        _roll("arrival", ("Ben",), _at(20), None, place="Ramen Ya"),
        _roll("arrival", ("Chloe",), _at(15), None, place=None),
        _roll("deviation_silence", ("Ada",), _at(26 * 60), _at(5)),
        _roll("deviation_anomaly", ("Ben",), _at(2 * 60), _at(10)),
        _roll("something_new", ("Chloe",), _at(50), _at(40)),
        _roll("colocation", ("Ada", "Ben"), None, None, ref="colocation|nostart"),
    ]
    stims = [_roll_stim(i + 1, ln, ago=60 - i * 5) for i, ln in enumerate(lines)]
    return _render(stims, Grounding(
        rollups={ln.ref: ln for ln in lines}, room_labels=LABELS,
    ))


@_case("rollup-that-cannot-be-read-beside-one-that-can")
def _() -> str:
    ok = _roll("arrival", ("Ben",), _at(20), None, place="Ramen Ya")
    lost = _roll("colocation", ("Ada", "Ben"), _at(95), None)
    return _render(
        [_roll_stim(1, ok, ago=20), _roll_stim(2, lost, ago=95)],
        Grounding(rollups={ok.ref: ok}, room_labels=LABELS),
    )


@_case("rollup-and-messages-in-one-agenda")
def _() -> str:
    m = _stim(1, A, ago=12, mid=300)
    ln = _roll("deviation_silence", ("Ada",), _at(26 * 60), _at(5))
    return _render([m, _roll_stim(2, ln, ago=5)], _g(
        _rec(m, author="Ben", text="anyone seen Ada?"), rollups={ln.ref: ln},
    ))


# =============================================================================
# the budget
# =============================================================================


def _chatty(count: int = 40, *, room: Room = A, base: int = 1000, width: int = 200):
    stims = [_stim(i, room, ago=100 - i, mid=base + i) for i in range(1, count + 1)]
    recs = [_rec(s, author="Ada", text=f"message number {s.id} " + "x" * width) for s in stims]
    return stims, recs


@_case("budget-drops-the-oldest-and-says-which")
def _() -> str:
    stims, recs = _chatty()
    return _render(stims, _g(*recs), budget=cr.RenderBudget(record_chars=3000, min_item_chars=500))


@_case("budget-drops-exactly-one-earlier-message")
def _() -> str:
    stims, recs = _chatty(count=5, width=80)
    # 645 is the old format's `need - 40` for these five rows (685 characters of
    # old lines): a literal, so the budget is the same whatever the rendering.
    # Only the old format drops exactly one message at it; the parity test
    # treats every `budget-*` case as a tight budget.
    return _render(stims, _g(*recs), budget=cr.RenderBudget(
        record_chars=645, min_item_chars=50,
    ))


@_case("budget-shared-by-need-a-chatty-room-and-a-quiet-one")
def _() -> str:
    chatty, crecs = _chatty(30, room=A, base=1, width=300)
    quiet = _stim(100, B, ago=5, mid=100)
    return _render([*chatty, quiet], _g(
        *crecs, _rec(quiet, author="Bo", text="one short line"),
    ), budget=cr.RenderBudget(record_chars=2500, min_item_chars=400))


@_case("budget-single-message-over-its-share-is-clipped")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    return _render([s], _g(_rec(s, author="Ada", text="z" * 5000)), budget=cr.RenderBudget(
        record_chars=100, min_item_chars=50, message_chars=120,
    ))


@_case("budget-floor-shrinks-with-many-items")
def _() -> str:
    rooms = [Room("signal", f"r{i}") for i in range(6)]
    labels = {f"signal:r{i}": f"Room {i}" for i in range(6)}
    stims, recs = [], []
    for i, room in enumerate(rooms):
        for j in range(4):
            s = _stim(i * 10 + j + 1, room, ago=60 - j, mid=i * 100 + j + 1)
            stims.append(s)
            recs.append(_rec(s, author=f"P{i}", text=f"room {i} word {j} " + "w" * 90))
    return _render(stims, _g(*recs, room_labels=labels), budget=cr.RenderBudget(
        record_chars=900, min_item_chars=900, message_chars=100,
    ))


@_case("budget-default-with-twelve-items")
def _() -> str:
    rooms = [Room("signal", f"r{i}") for i in range(12)]
    labels = {f"signal:r{i}": f"Room {i}" for i in range(12)}
    stims, recs = [], []
    for i, room in enumerate(rooms):
        for j in range(15):
            s = _stim(i * 100 + j + 1, room, ago=300 - j, mid=i * 1000 + j + 1)
            stims.append(s)
            recs.append(_rec(s, author=f"P{i % 3}", text=f"r{i} m{j} " + "t" * (20 + 15 * i)))
    return _render(stims, _g(*recs, room_labels=labels))


@_case("budget-zero-allowance-keeps-one-clipped-line")
def _() -> str:
    stims, recs = _chatty(count=3, width=50)
    return _render(stims, _g(*recs), budget=cr.RenderBudget(
        record_chars=0, min_item_chars=0, message_chars=400,
    ))


@_case("could-not-be-shown-singular-and-plural-with-readable-lines")
def _() -> str:
    ok = _stim(1, A, ago=9, mid=1)
    gone = [_stim(i, A, ago=9 - i, mid=i) for i in (2, 3, 4)]
    wrong_room = _stim(5, A, ago=2, mid=5)
    bad_ref = _stim(6, A, ago=1, ref="weird:ref")
    return _render([ok, *gone, wrong_room, bad_ref], _g(
        _rec(ok, author="Ada", text="visible"),
        _rec(wrong_room, author="Ben", text="LEAK", room=B),
    ))


@_case("could-not-be-shown-exactly-one")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    return _render([s], Grounding(room_labels=LABELS))


# =============================================================================
# the agenda: kinds, strikes, rooms, probes
# =============================================================================


@_case("returning-items-one-and-many-sessions")
def _() -> str:
    s1, s2, s3, s4 = (_stim(i, A, ago=50 - i * 10, mid=i) for i in (1, 2, 3, 4))
    return _render([s1, s2, s3, s4], _g(
        _rec(s1, author="Ada", text="left open once"),
        _rec(s2, author="Ben", text="left open twice"),
        _rec(s3, author="Ada", text="also twice"),
        _rec(s4, author="Chloe", text="fresh"),
    ), strikes={1: 1, 2: 2, 3: 2})


@_case("social-play-time-self-and-unknown-items")
def _() -> str:
    stims = [
        _stim(1, A, ago=15, kind="social", item="story_frame,story_frame",
              ref="social_read:1"),
        _stim(2, A, ago=14, kind="social", item="collab:post,reel", ref="social_read:2"),
        _stim(3, A, ago=13, kind="play", item="sample", ref="play:3"),
        _stim(4, None, ago=0, kind="time", item="2026-09-30 evening plan", ref="time:4"),
        _stim(5, None, ago=1, kind="self", item="wake-9", ref="self:5"),
        _stim(6, None, ago=2, kind="weird", item="x [y]: <z>", ref="weird:6"),
    ]
    return _render(stims, Grounding(room_labels=LABELS, socials={
        s.ref: SocialLine(ref=s.ref, name=name, person_id=name.lower(), item=s.item,
                          at_ms=s.at_ms)
        for s, name in ((stims[0], "Ada"), (stims[1], "Ben"))
    }))


@_case("several-rooms-with-an-unlabelled-one-and-events")
def _() -> str:
    a1, a2 = _stim(1, A, ago=30, mid=1), _stim(2, A, ago=20, mid=2)
    b1 = _stim(3, B, ago=25, mid=3)
    c1 = _stim(4, C, ago=15, mid=4)
    return _render([a1, a2, b1, c1], _g(
        _rec(a1, author="Ada", text="in a"), _rec(a2, author="Ben", text="also a"),
        _rec(b1, author="Bo", text="in b"), _rec(c1, author="Cy", text="in c"),
        events=(
            EventLine(id="e-9", room=A, title="Dinner", when_ms=NOW + HOUR, venue="Ramen Ya",
                      status="proposed", revision="rev-3"),
            EventLine(id="e-8", room=B, title="Games", when_ms=NOW + DAY, venue="",
                      status="confirmed"),
            EventLine(id="e-7", room=C, title="Walk", when_ms=None, venue="the pier",
                      status="proposed"),
            EventLine(id="e-6", room=Room("signal", "room-z"), title="Elsewhere",
                      when_ms=None, venue="", status="proposed"),
        ),
    ))


@_case("reads-line-one-room")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    note = f"Your read tools look through room {cr.room_label(A, LABELS)}."
    return _render([s], _g(_rec(s, author="Ada", text="hi")), reads_note=note)


@_case("reads-line-several-rooms-with-first-look")
def _() -> str:
    a, b = _stim(1, A, ago=3, mid=1), _stim(2, B, ago=2, mid=2)
    keys = [cr.room_key(A), cr.room_key(B)]
    named = [cr.room_label(A, LABELS), cr.room_label(B, LABELS)]
    note = (
        "Your read tools take a `room`, one of: "
        + ", ".join(f"`{k}` ({n})" for k, n in zip(keys, named, strict=True))
        + ". Each read resolves through the room you name and shows only what that "
        "room may be shown; a room that is not listed is refused."
    )
    return _render([a, b], _g(
        _rec(a, author="Ada", text="a"), _rec(b, author="Bo", text="b"),
    ), reads_note=note, last_session_ms=None)


@_case("reads-line-and-a-return")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    return _render(
        [s], _g(_rec(s, author="Ada", text="hi")),
        reads_note="Your read tools look through room «Room A».", last_session_ms=NOW - 3 * HOUR,
    )


@_case("probe-no-clock-no-grounding")
def _() -> str:
    items = (
        AgendaItem(kind="message", room=A, summary="2 new messages in this room",
                   stimulus_ids=(1, 2), cues=(), surprise=1.0),
        AgendaItem(kind="message", room=B, summary="1 new message in this room",
                   stimulus_ids=(3,), cues=(), surprise=1.0, strike=1),
        AgendaItem(kind="time", room=C, summary="a time stimulus is due: dinner",
                   stimulus_ids=(4,), cues=(), surprise=1.0, strike=2),
    )
    return cr.render_agenda(items, grounding=Grounding(
        room_labels=LABELS, notes=(NoteLine(id="n-1", title="ignored", description="ignored"),),
    ), reads_note="Your read tools look through room «Room A».", last_session_ms=NOW)


@_case("probe-no-clock-with-grounding-ignored")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    return _render([s], _g(_rec(s, author="Ada", text="hi")), now_ms=None)


@_case("no-items-with-a-clock")
def _() -> str:
    return cr.render_agenda([], now_ms=NOW, last_session_ms=NOW - 5 * MIN, tz=HK)


@_case("no-items-no-clock")
def _() -> str:
    return cr.render_agenda([])


@_case("an-item-names-a-stimulus-that-is-not-in-the-claimed-set")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    item = AgendaItem(kind="message", room=A, summary="2 new messages in this room",
                      stimulus_ids=(1, 99), cues=(), surprise=1.0)
    return _render([s], _g(_rec(s, author="Ada", text="hi")), items=(item,))


@_case("no-grounding-with-a-clock")
def _() -> str:
    s = _stim(1, A, ago=3, mid=12)
    return _render([s], None)


# =============================================================================
# the Register
# =============================================================================


def _one_message() -> tuple[list[Stimulus], list[RecordItem]]:
    s = _stim(1, A, ago=3, mid=12)
    return [s], [_rec(s, author="Ada", text="hi")]


@_case("register-notes-only")
def _() -> str:
    stims, recs = _one_message()
    return _render(stims, _g(*recs, notes=(
        NoteLine(id="n-77", title="Trip to Tokyo", description="dates and who is flying"),
        NoteLine(id="n-78", title="Gift [for] Ben: <b>", description="line one\nline two & \"q\""),
        NoteLine(id="n-79", title="Empty description", description=""),
        NoteLine(id="n-80", title="Long", description="d" * 300),
    )))


@_case("register-events-only")
def _() -> str:
    stims, recs = _one_message()
    return _render(stims, _g(*recs, events=(
        EventLine(id="e-9", room=A, title="Dinner", when_ms=NOW + HOUR, venue="Ramen Ya",
                  status="proposed", revision="rev-3"),
        EventLine(id="e-10", room=A, title="No time \"set\"\nsecond line", when_ms=None, venue="",
                  status="proposed"),
        EventLine(id="e-11", room=A, title="Far", when_ms=NOW + 400 * DAY, venue="v" * 200,
                  status="confirmed", revision=""),
        EventLine(id="e-12", room=A, title="Midnight", when_ms=NOW + 9 * HOUR + 30 * MIN,
                  venue="Pier 9", status="cancelled", revision="r1"),
    )))


@_case("register-notes-and-events")
def _() -> str:
    stims, recs = _one_message()
    return _render(stims, _g(
        *recs,
        notes=(NoteLine(id="n-77", title="Trip", description="dates"),),
        events=(EventLine(id="e-9", room=A, title="Dinner", when_ms=NOW + HOUR, venue="Ramen Ya",
                          status="proposed", revision="rev-3"),),
    ))


@_case("register-none-has-no-trailing-block")
def _() -> str:
    stims, recs = _one_message()
    return _render(stims, _g(*recs))


@_case("register-note-and-event-line-caps")
def _() -> str:
    stims, recs = _one_message()
    notes = tuple(NoteLine(id=f"n-{i}", title=f"Note {i}", description=f"d{i}") for i in range(7))
    events = tuple(
        EventLine(id=f"e-{i}", room=A, title=f"Event {i}", when_ms=NOW + i * HOUR, venue="",
                  status="proposed") for i in range(7)
    )
    return _render(stims, _g(*recs, notes=notes, events=events), budget=cr.RenderBudget(
        register_lines=3,
    ))


@_case("register-cut-at-its-character-budget")
def _() -> str:
    stims, recs = _one_message()
    notes = tuple(
        NoteLine(id=f"n-{i}", title=f"Note number {i}", description="w" * 150) for i in range(30)
    )
    return _render(stims, _g(*recs, notes=notes), budget=cr.RenderBudget(register_chars=1000))


@_case("register-lists-only-the-agendas-rooms")
def _() -> str:
    a, b = _stim(1, A, ago=3, mid=1), _stim(2, B, ago=2, mid=2)
    return _render([a, b], _g(
        _rec(a, author="Ada", text="a"), _rec(b, author="Bo", text="b"),
        events=(
            EventLine(id="e-b", room=B, title="Games", when_ms=NOW + DAY, venue="",
                      status="proposed"),
            EventLine(id="e-a", room=A, title="Dinner", when_ms=NOW + HOUR, venue="",
                      status="proposed"),
            EventLine(id="e-c", room=C, title="Not on the agenda", when_ms=None, venue="",
                      status="proposed"),
        ),
    ))


@_case("register-never-shown-in-a-probe")
def _() -> str:
    stims, recs = _one_message()
    return _render(stims, _g(
        *recs, notes=(NoteLine(id="n-1", title="t", description="d"),),
    ), now_ms=None)


# =============================================================================
# a whole realistic session, every part at once
# =============================================================================


@_case("a-whole-session")
def _() -> str:
    m1, m2 = _stim(1, A, ago=95, mid=101), _stim(2, A, ago=80, mid=103)
    m3 = _stim(3, A, ago=12, mid=107, cues=("datetime",))
    r = _stim(4, A, ago=11, mid=700, table="message_reaction", item="reaction")
    d = _stim(5, B, ago=9, mid=800, table="message_delete", item="delete")
    b1 = _stim(6, B, ago=40, mid=301)
    ln = _roll("arrival", ("Ben",), _at(20), None, place="Ramen Ya")
    st = _roll_stim(7, ln, ago=20)
    social = _stim(8, A, ago=6, kind="social", item="post", ref="social_read:8")
    stims = [m1, m2, m3, r, d, b1, st, social]
    return _render(stims, Grounding(
        records={x.ref: x for x in (
            _rec(m1, author="Ada", text="dinner friday at 8?"),
            _rec(m2, author="Ben", text="yes, the ramen place"),
            _rec(m3, author="Ada", text="friday at 9 actually", edited=True),
            _rec(r, author="Ben", text="friday at 9 actually", kind="reaction", emoji="👍",
                 message_id=107),
            _rec(d, author="Bo", text=None, kind="delete", message_id=299),
            _rec(b1, author="Bo", text="who has the car? [urgent]: <now>"),
        )},
        rollups={ln.ref: ln},
        socials={social.ref: SocialLine(ref=social.ref, name="Ben", person_id="ben",
                                        item=social.item, at_ms=social.at_ms)},
        own_sends={0: (_own(104, A, ago=70, text="I think Friday works"),)},
        notes=(NoteLine(id="n-77", title="Dinner Friday", description="who, when and where"),),
        events=(EventLine(id="e-9", room=A, title="Dinner", when_ms=NOW + 2 * DAY, venue="Ramen Ya",
                          status="proposed", revision="rev-3"),),
        room_labels=LABELS,
    ), strikes={6: 1}, last_session_ms=NOW - (2 * HOUR + 10 * MIN),
        reads_note="Your read tools look through room «Room A».")


# =============================================================================
# the criterion
# =============================================================================


def stripped(text: str) -> str:
    """The prompt without each block's type text (held to the registry below)."""
    return _TYPE_TEXT.sub("", text)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_prompt_is_what_step_3b_captured(name: str):
    assert stripped(CASES[name]()) == GOLDEN[name]


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_block_carries_exactly_its_types_description_and_fields(name: str):
    """The text the goldens hold out is the registry's, on every opening tag."""
    text = CASES[name]()
    for tag, attrs, _closed in _OPENING.findall(text):
        if ' description="' not in attrs:
            continue       # an element (`m`, `note`, ...), not a block: a block says what it is
        block_type = BLOCK_TYPES[tag]
        want = f' description="{escape_attr(block_type.description)}"'
        if block_type.annotations:
            fields = "; ".join(f"`{k}` is {v}" for k, v in block_type.annotations)
            want += f' fields="{escape_attr(fields)}"'
        assert attrs.endswith(want), (name, tag)


def test_the_goldens_vary_what_this_step_is_about():
    """What each golden holds, so «every part present» is a checked property of
    the fixtures and not a claim about them."""
    must = {
        "one-message": ('<m n="101" at="14:50 (40 min ago)" from="Ada">dinner friday at 8?</m>',),
        "many-messages-from-and-to": ('window="from ', "the newest"),
        "two-messages-in-one-minute-use-the-at-form": ('window="at ',),
        "older-day-and-days-ago": ('at="Tue 29 Sep 13:30 (1 d ago)"', '(3 d ago)"'),
        "edit-and-edited-flag-with-cues": ('edited="yes"', 'cues="datetime, rsvp"'),
        "reaction-with-and-without-words-and-emoji": ('n="44"', 'reaction="👍"', 'reaction="?"'),
        "delete-and-withheld-words": ('deleted="yes"', 'withdrawn="yes"'),
        "own-sends-in-the-window": ('own="yes"',),
        "rollup-lines-of-every-kind": (
            "were in the same place", "arrived at Ramen Ya", "arrived ", "never at home",
            "scored anomalous", "something_new", '<rollup kind="colocation">',
        ),
        "budget-drops-the-oldest-and-says-which": (
            'earlier_not_shown="', 'earlier_range="1001 to ',
        ),
        "budget-drops-exactly-one-earlier-message": ('earlier_not_shown="',),
        "could-not-be-shown-singular-and-plural-with-readable-lines": ('unreadable="4"',),
        "could-not-be-shown-exactly-one": ('unreadable="1"',),
        "returning-items-one-and-many-sessions": (
            'returning="left open by 1 earlier session"',
            'returning="left open by 2 earlier sessions"',
        ),
        "social-play-time-self-and-unknown-items": (
            "new social items", "Ada posted 2 story frames</social>",
            "Ben posted a collab post and a reel</social>", "new play sample",
            "a time stimulus is due:",
            "a self-wake is due:", "1 weird stimuli:",
        ),
        "several-rooms-with-an-unlabelled-one-and-events": (
            "«a room with no name»", '<room_events room="«Room B»">',
        ),
        "reads-line-one-room": ("<reads>\nYour read tools look through room «Room A».\n</reads>",),
        "reads-line-several-rooms-with-first-look": (
            "Your read tools take a `room`, one of:", "This is your first look",
        ),
        "reads-line-and-a-return": ("You last looked at this group 3 h ago.",),
        "register-notes-only": ("<note_index>", 'n="n-77"'),
        "register-events-only": ('when="no time set"', 'revision="rev-3"'),
        "register-note-and-event-line-caps": ('not_listed="4"',),
        "register-cut-at-its-character-budget": ('not_listed="',),
        "probe-no-clock-no-grounding": ('returning="left open by 2 earlier sessions"',),
        "a-whole-session": ("<note_index>", "<events>", 'own="yes"', "returning"),
    }
    for name, parts in must.items():
        for part in parts:
            assert part in GOLDEN[name], (name, part)
    assert "<note_index" not in GOLDEN["register-none-has-no-trailing-block"]
    assert GOLDEN["register-none-has-no-trailing-block"].endswith("</agenda>")
    assert "<clock" not in GOLDEN["probe-no-clock-no-grounding"]
    assert "<m " not in GOLDEN["probe-no-clock-with-grounding-ignored"]
    assert "<note_index" not in GOLDEN["register-never-shown-in-a-probe"]
    assert GOLDEN["no-items-no-clock"] == ""
    assert "LEAK" not in GOLDEN["could-not-be-shown-singular-and-plural-with-readable-lines"]
