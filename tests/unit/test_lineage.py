"""The lineage predicates and their application at assembly
(DEC-222, DEC-225).

One predicate per kind, each with the room it must pass and the room it must
not; unknown is deny; a surprise withholds; a failing section is dropped and
the rest of its row renders; and a same-room read renders byte-for-byte what
it rendered before lineage existed.

Rooms, units and people are placeholders.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.agent.lineage import (
    LineagePolicy,
    derived_view,
    entry_permits,
    excluded,
    section_permits,
    visible_sections,
)
from nora.agent.prefill import build_prefill
from nora.domain.culture import CultureRevision
from nora.domain.lineage import (
    BODY,
    ROW,
    UNRESOLVED,
    WORKING_SET,
    Lineage,
    LineageEntry,
    LiveRoom,
    RenderRoom,
    RoomFacts,
    message,
    note_revision_lineage,
    own_room_lineage,
    unresolved,
)
from nora.domain.models import NormalizedMessage, TagContext
from nora.domain.notes import Note
from nora.domain.standing import Standing, WorkingSetItem

# A unit of three rooms: A lists p1..p3, B lists p1..p2 (a subset of A), C
# lists `*`. D is in another unit and lists p1 alone.
A = RoomFacts(platform="signal", ids=frozenset({"a"}), unit="u1",
              persons=frozenset({"p1", "p2", "p3"}), location=True, label="A")
B = RoomFacts(platform="discord", ids=frozenset({"b"}), unit="u1",
              persons=frozenset({"p1", "p2"}), location=True, label="B")
C = RoomFacts(platform="whatsapp", ids=frozenset({"c"}), unit="u1", persons=None)
D = RoomFacts(platform="signal", ids=frozenset({"d"}), unit="u2",
              persons=frozenset({"p1"}))
ROOMS = (A, B, C, D)
CIRCLE = frozenset({"p1", "p2"})


def _in(room: RoomFacts, *, live: LiveRoom | None = None, mode: str = "on") -> RenderRoom:
    return RenderRoom(room=room, rooms=ROOMS, circles=(CIRCLE,), live=live, mode=mode)  # type: ignore[arg-type]


LIVE_OK = LiveRoom(person_ids=frozenset({"p1", "p2"}))


# ---- message:<A> --------------------------------------------------------------


def test_message_passes_in_its_own_room_and_in_a_subset_room_of_the_unit():
    assert entry_permits(message("signal:a"), _in(A), origin="a")
    assert entry_permits(message("signal:a"), _in(B, live=LIVE_OK), origin="a")


@pytest.mark.parametrize("live, why", [
    (None, "no live roster was read"),
    (LiveRoom(person_ids=frozenset({"p1"}), available=False), "roster unreadable"),
    (LiveRoom(person_ids=frozenset({"p1"}), degraded=True), "roster degraded"),
    (LiveRoom(person_ids=frozenset({"p1"}), unnamed=1), "an account nobody can name"),
    (LiveRoom(person_ids=frozenset({"p1", "p9"})), "p9 is in B and not listed in A"),
])
def test_message_needs_who_is_actually_in_the_room_not_only_who_it_lists(live, why):
    """B lists a subset of A's people; who is IN B right now must be too.
    Unverifiable is refused."""
    assert not entry_permits(message("signal:a"), _in(B, live=live), origin="a"), why


def test_message_is_refused_in_a_wider_room_a_star_room_or_another_unit():
    # Each with a live roster that would pass, so the named clause is what refuses.
    assert not entry_permits(message("discord:b"), _in(A, live=LIVE_OK), origin="b")  # A lists p3
    assert not entry_permits(message("signal:a"), _in(C, live=LIVE_OK), origin="a")  # C lists `*`
    assert not entry_permits(message("whatsapp:c"), _in(B, live=LIVE_OK), origin="c")  # C lists `*`
    only_p1 = LiveRoom(person_ids=frozenset({"p1"}))
    assert not entry_permits(message("signal:a"), _in(D, live=only_p1), origin="a")  # another unit
    assert not entry_permits(message("signal:gone"), _in(B), origin="gone")
    assert not entry_permits(message("signal:a"), RenderRoom(room=None, rooms=ROOMS), origin="a")


# ---- presence -----------------------------------------------------------------


def test_presence_needs_the_flag_and_the_live_guard_for_that_person():
    entry = LineageEntry(kind="presence", subject="p1", origin="b")
    assert entry_permits(entry, _in(B, live=LIVE_OK), origin="b")


@pytest.mark.parametrize("room, live, why", [
    (replace(B, location=False), LIVE_OK, "the room's location flag is off"),
    (B, None, "no live roster was read"),
    (B, LiveRoom(person_ids=frozenset({"p1"}), available=False), "roster unreadable"),
    (B, LiveRoom(person_ids=frozenset({"p1"}), degraded=True), "roster degraded"),
    (B, LiveRoom(person_ids=frozenset({"p1", "p3"})), "p3 is outside the circle"),
    (B, LiveRoom(person_ids=frozenset({"p1", "p2"}), unnamed=1),
     "an unmapped account is in the room (8765_44856)"),
])
def test_presence_is_refused(room, live, why):
    entry = LineageEntry(kind="presence", subject="p1", origin="b")
    assert not entry_permits(entry, _in(room, live=live), origin="b"), why


def test_presence_about_someone_in_no_circle_or_no_one_is_refused():
    assert not entry_permits(
        LineageEntry(kind="presence", subject="p3"), _in(B, live=LIVE_OK), origin="b")
    assert not entry_permits(
        LineageEntry(kind="presence", subject=None), _in(B, live=LIVE_OK), origin="b")


# ---- social, play, face, perception --------------------------------------------


@pytest.mark.parametrize("kind", ["social", "play"])
def test_social_and_play_need_the_person_listed_in_the_room(kind):
    assert entry_permits(LineageEntry(kind=kind, subject="p3"), _in(A), origin=None)
    assert not entry_permits(LineageEntry(kind=kind, subject="p3"), _in(B), origin=None)
    assert not entry_permits(LineageEntry(kind=kind, subject="p3"), _in(C), origin=None)


def test_face_passes_in_the_media_room_or_where_listed_and_the_media_passes():
    face = LineageEntry(kind="face", subject="p1", origin="signal:a", reads=(message("signal:a"),))
    assert entry_permits(face, _in(A), origin="a")
    assert entry_permits(face, _in(B, live=LIVE_OK), origin="a")  # p1 listed, a -> b passes
    other = LineageEntry(kind="face", subject="p3", origin="signal:a",
                         reads=(message("signal:a"),))
    assert not entry_permits(other, _in(B), origin="a")     # p3 not listed in B
    wide = LineageEntry(kind="face", subject="p1", origin="discord:b",
                        reads=(message("discord:b"),))
    assert not entry_permits(wide, _in(A), origin="b")      # b -> a fails


def test_perception_passes_where_the_item_it_reads_passes():
    reads_a = LineageEntry(kind="perception", origin="signal:a", reads=(message("signal:a"),))
    assert entry_permits(reads_a, _in(B, live=LIVE_OK), origin="a")
    reads_b = LineageEntry(kind="perception", origin="discord:b", reads=(message("discord:b"),))
    assert not entry_permits(reads_b, _in(A), origin="b")
    bare = LineageEntry(kind="perception", origin="signal:a")
    assert entry_permits(bare, _in(A), origin="a")
    assert not entry_permits(bare, _in(B), origin="a")


# ---- unknown is deny ------------------------------------------------------------


@pytest.mark.parametrize("kind", [UNRESOLVED, "memory", "rollup", ""])
def test_an_unknown_kind_shows_only_in_the_rows_origin_room(kind):
    """The revert-and-red target for «deny unknown kinds»: B is a subset room of
    A, so every KNOWN predicate would let it through; an unknown one must not."""
    entry = LineageEntry(kind=kind, subject="a", origin="a")
    assert entry_permits(entry, _in(A), origin="a")
    assert not entry_permits(entry, _in(B), origin="a")
    assert not entry_permits(entry, _in(A), origin=None)


def test_an_empty_section_is_unknown():
    assert section_permits((), _in(A), origin="a")
    assert not section_permits((), _in(B), origin="a")
    assert not section_permits((), _in(A), origin=None)


def test_every_entry_must_pass():
    assert section_permits((message("signal:a"),), _in(B, live=LIVE_OK), origin="a")
    assert not section_permits((message("signal:a"), unresolved("a")), _in(B), origin="a")


# ---- surprises ------------------------------------------------------------------


def test_exclude_persons_refuses_a_room_holding_one_of_them():
    """The revert-and-red target for exclude_persons: A lists p3."""
    assert excluded(("p3",), _in(A, live=LIVE_OK))
    assert excluded(("p3",), _in(B, live=LiveRoom(person_ids=frozenset({"p1", "p3"}))))


@pytest.mark.parametrize("room, live, why", [
    (C, LIVE_OK, "a `*` room can hold anyone"),
    (B, None, "no live roster was read"),
    (B, LiveRoom(person_ids=frozenset({"p1"}), unnamed=1), "an account nobody can name"),
    (B, LiveRoom(person_ids=frozenset({"p1"}), degraded=True), "roster degraded"),
])
def test_exclude_persons_refuses_what_it_cannot_verify(room, live, why):
    assert excluded(("p3",), _in(room, live=live)), why


def test_exclude_persons_allows_a_verified_room_without_them_and_empty_never_excludes():
    assert not excluded(("p3",), _in(B, live=LIVE_OK))
    assert not excluded((), _in(C))


def test_a_surprise_withholds_every_section():
    lineage = own_room_lineage("b", (BODY, WORKING_SET))
    shown = visible_sections(lineage, (BODY, WORKING_SET), _in(A, live=LIVE_OK),
                             origin="b", exclude_persons=("p3",))
    assert shown == {BODY: False, WORKING_SET: False}


# ---- the derivation rule ---------------------------------------------------------


def test_a_note_revision_rests_on_its_rooms_and_unresolved_when_it_cannot_say():
    b, a = "discord:b", "signal:a"
    first = note_revision_lineage(cited=(1, 2), rooms={1: b, 2: a}, origin="a",
                                  origin_key=a, first=True)
    assert set(first.section(ROW)) == {message(a), message(b)}
    pruned = note_revision_lineage(cited=(1, 9), rooms={1: b}, origin="a",
                                   origin_key=a, first=False)
    assert set(pruned.section(ROW)) == {message(b), unresolved(a)}
    silent = note_revision_lineage(cited=(), rooms={}, origin="a", origin_key=a,
                                   first=False)
    assert set(silent.section(ROW)) == {unresolved(a)}
    from_b = note_revision_lineage(cited=(), rooms={}, origin="a", origin_key=a,
                                   first=False, evidence_room=b)
    assert set(from_b.section(ROW)) == {message(b)}
    # A first revision whose room has nothing on record to name its platform.
    unkeyed = note_revision_lineage(cited=(), rooms={}, origin="a", origin_key=None,
                                    first=True)
    assert set(unkeyed.section(ROW)) == {unresolved("a")}


# ---- assembly: the four derived blocks ------------------------------------------


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _note(note_id: str, title: str, origin: str, lineage: Lineage) -> Note:
    return Note(
        id=note_id, unit="u1", title=title, description="a thing to settle",
        uncertainty="u", closing_condition="c", established_in=origin,
        created_at=1, head_revision_id=note_id.replace("a", "b"),
        anchor_date=20261001, lineage=lineage,
    )


def _standing(room: str) -> Standing:
    platform = {"a": "signal", "b": "discord", "c": "whatsapp"}[room]
    return Standing(
        platform=platform, conversation_id=room, body="where it had got to",
        fold_count=1, updated_at=1,
        working_set=(WorkingSetItem(what="an option", source="web_search",
                                    found_at=int(NOW.timestamp() * 1000)),),
        lineage=own_room_lineage(f"{platform}:{room}", (BODY, WORKING_SET)),
    )


def _culture(room: str) -> CultureRevision:
    return CultureRevision(
        id="0191f2c4-0000-7000-8000-0000000000cc", culture_key=room,
        body="how this room talks", written_at=1, established_by="culture",
        lineage=own_room_lineage(room, (BODY,)),
    )


def _ctx(room: RoomFacts, *, notes=(), standing=None, culture=None,
         lineage_room: RenderRoom | None = None) -> TagContext:
    cid = sorted(room.ids)[0]
    msg = NormalizedMessage(room.platform, cid, "group", "s1", "someone", 1, "hi", {})
    return TagContext(
        "tag-1", msg, None, (), (), 0.0, notes=tuple(notes), standing=standing,
        culture=culture, lineage_room=lineage_room,
    )


def _body(ctx: TagContext) -> str:
    content = build_prefill(ctx, display_name="Nora", context_window=40, now=NOW)[1]["content"]
    return content if isinstance(content, str) else "".join(c["text"] for c in content)


def _body_at(ctx: TagContext, now: datetime) -> str:
    content = build_prefill(ctx, display_name="Nora", context_window=40, now=now)[1]["content"]
    return content if isinstance(content, str) else "".join(c["text"] for c in content)


def test_the_working_set_ages_against_the_clock_the_prefill_was_given():
    """The block is aged against `build_prefill`'s `now`, not the wall clock.

    The lineage tests pin `now` and give the item `found_at=NOW`; a block that
    fell back to `datetime.now()` would expire the item on a later day and turn
    `test_a_same_room_read_renders_exactly_what_it_rendered_without_lineage`
    red. A prefill built for a given instant must render the same on any day;
    that is also what makes a replayed turn reproducible."""
    ctx = _ctx(A, standing=_standing("a"))
    assert "<standing_working_set" in _body_at(ctx, NOW + timedelta(hours=47))
    assert "<standing_working_set" not in _body_at(ctx, NOW + timedelta(hours=49))


# Backfilled rows, as `0073_lineage` writes them for a room's own data.
NOTE_A = _note("0191f2c4-0000-7000-8000-0000000000a1", "raised here", "a",
               Lineage.of({ROW: [message("signal:a")]}))
NOTE_B = _note("0191f2c4-0000-7000-8000-0000000000a2", "raised in the smaller room", "b",
               Lineage.of({ROW: [message("discord:b")]}))
NOTE_A_LEGACY = _note("0191f2c4-0000-7000-8000-0000000000a3", "legacy revision", "a",
                      Lineage.of({ROW: [message("signal:a"), unresolved("a")]}))


def test_a_same_room_read_renders_exactly_what_it_rendered_without_lineage():
    """Every block a backfilled room's OWN rows produce is byte-identical with
    lineage on and with it absent."""
    rows = {"notes": (NOTE_A, NOTE_A_LEGACY), "standing": _standing("a"),
            "culture": _culture("a")}
    policy = LineagePolicy(rooms=ROOMS, circles=(CIRCLE,), mode="on",
                           _by_id={("signal", "a"): A})
    on = _ctx(A, **rows, lineage_room=policy.render_room(platform="signal", conversation_id="a"))
    off = _ctx(A, **rows)
    assert _body(on) == _body(off)
    for tag in ("<notes", "<standing", "<standing_working_set", "<culture"):
        assert tag in _body(on)


def test_a_note_is_not_rendered_in_a_wider_room_and_the_rest_are():
    """B's people are a subset of A's. A note resting on B is refused in A (it
    holds p3, who was not there); one resting on A is shown in B; one with an
    unresolved citation stays in A."""
    in_b = derived_view(notes=(NOTE_A, NOTE_B, NOTE_A_LEGACY), standing=None,
                        culture=None, target=_in(B, live=LIVE_OK))
    assert [n.title for n in in_b.notes] == ["raised here", "raised in the smaller room"]
    assert dict(in_b.withheld) == {"notes": 1}
    # Block assembly reads no live roster today: the note from A is withheld.
    unread = derived_view(notes=(NOTE_A, NOTE_B), standing=None, culture=None,
                          target=_in(B))
    assert [n.title for n in unread.notes] == ["raised in the smaller room"]
    in_a = derived_view(notes=(NOTE_A, NOTE_B, NOTE_A_LEGACY), standing=None,
                        culture=None, target=_in(A))
    assert [n.title for n in in_a.notes] == ["raised here", "legacy revision"]
    body = _body(_ctx(A, notes=(NOTE_A, NOTE_B), lineage_room=_in(A)))
    assert "raised here" in body
    assert "raised in the smaller room" not in body


def test_a_failing_section_is_dropped_and_the_rest_of_its_row_renders():
    standing = replace(_standing("b"), lineage=Lineage.of({
        BODY: [message("discord:b")], WORKING_SET: [message("discord:b"), unresolved("c")],
    }))
    ctx = _ctx(B, standing=standing, lineage_room=_in(B))
    view = derived_view(notes=(), standing=standing, culture=None, target=_in(B))
    assert (view.standing, view.standing_working_set) == (True, True)
    elsewhere = _ctx(A, standing=standing, lineage_room=_in(A))
    body = _body(elsewhere)
    # In A the body (message:b, b -> a fails) and the working set both fail.
    assert "<standing" not in body
    # Make only the working set fail in B: the position still renders.
    split = replace(standing, lineage=Lineage.of({
        BODY: [message("discord:b")], WORKING_SET: [LineageEntry(kind="presence", subject="p1")],
    }))
    body = _body(replace(ctx, standing=split))
    assert "where it had got to" in body
    assert "<standing_working_set" not in body


def test_shadow_computes_the_same_answer_and_renders_everything():
    shadow = _ctx(A, notes=(NOTE_A, NOTE_B), lineage_room=_in(A, mode="shadow"))
    off = _ctx(A, notes=(NOTE_A, NOTE_B))
    assert _body(shadow) == _body(off)
    assert "raised in the smaller room" in _body(shadow)


def test_mode_off_computes_nothing():
    """`off` is not a shadow that happens to render everything: no view is
    built, so nothing is computed and nothing lands on the prefill span."""
    from nora.agent.prefill import _lineage

    off = _ctx(A, notes=(NOTE_B,), lineage_room=_in(A, mode="off"))
    assert _lineage(off) is None
    shadow = _ctx(A, notes=(NOTE_B,), lineage_room=_in(A, mode="shadow"))
    view = _lineage(shadow)
    assert view is not None and dict(view.withheld) == {"notes": 1}



def test_a_rows_origin_may_be_a_room_key_or_a_bare_id():
    """Room keys are `platform:conversation_id`; a note's
    `established_in` is a bare id, matched within the unit. A key on another
    platform is not this room."""
    assert A.is_room("signal:a") and A.is_room("a")
    assert not A.is_room("discord:a")
    assert entry_permits(unresolved("a"), _in(A), origin="a")
    assert entry_permits(unresolved("signal:a"), _in(A), origin="signal:a")
    assert not entry_permits(unresolved("discord:a"), _in(A), origin="discord:a")


def test_a_room_with_nobody_resolved_verifies_nothing_so_no_cross_room_entry_shows():
    """An available, fully named roster with NO person in it is «could not verify»,
    never a vacuous subset: `frozenset() <= anything`."""
    empty = LiveRoom(person_ids=frozenset())
    assert not entry_permits(message("signal:a"), _in(B, live=empty), origin="a")
    assert not entry_permits(
        LineageEntry(kind="presence", subject="p1", origin="a"), _in(B, live=empty),
        origin="a",
    )
