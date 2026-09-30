"""The pure half of the shadow-week comparison
(`nora.domain.cognition_shadow_compare`): room identity, the Standing writer
derivation, and the greedy one-to-one match."""

from __future__ import annotations

from nora.domain.cognition_compare import CompareResult, ProactiveAct
from nora.domain.cognition_shadow_compare import (
    ClockedWrite,
    Close,
    CloseLedger,
    CognitionWrite,
    RoomDirectory,
    act_tier,
    compare_tier,
    match_writes,
    room_id,
    standing_writer,
)


def test_a_room_is_its_bare_conversation_id_in_any_of_the_three_spellings():
    assert room_id("signal:abc=") == room_id("abc=") == "abc="
    assert room_id("123456789") == "123456789"


def test_the_standing_writer_is_the_one_that_moved_fold_count():
    assert standing_writer(fold_count=2, previous_fold_count=1) == "standing-debrief"
    assert standing_writer(fold_count=2, previous_fold_count=2) == "standing-ingest-fold"
    assert standing_writer(fold_count=1, previous_fold_count=None) == "standing-debrief"
    assert standing_writer(fold_count=0, previous_fold_count=None) == "standing-ingest-fold"


def _c(i, at, **kw):
    return CognitionWrite(id=i, tier="standing", verb="standing_revise", room="r", at_ms=at, **kw)


def _w(i, at, **kw):
    return ClockedWrite(writer="standing-debrief", tier="standing", id=i, room="r", at_ms=at, **kw)


def test_the_match_is_one_to_one_nearest_first_and_deterministic():
    pairs, cog_left, clocked_left = match_writes(
        [_c("1", 100), _c("2", 140)], [_w("a", 130), _w("b", 135), _w("c", 900)], tolerance_ms=50,
    )
    assert [(c.id, w.id) for c, w in pairs] == [("2", "a"), ("1", "b")]
    assert [w.id for w in clocked_left][-1] == "c"
    assert match_writes(
        [_c("1", 100), _c("2", 140)], [_w("a", 130), _w("b", 135), _w("c", 900)], tolerance_ms=50,
    ) == (pairs, cog_left, clocked_left)


def test_a_pair_straddling_the_window_edge_counts_once_and_an_unmatched_outside_row_not_at_all():
    result = compare_tier(
        "standing", cognition=[_c("1", 95), _c("9", 500)], refusals=[],
        clocked=[_w("a", 105), _w("z", 700)], closes=[], tolerance_ms=50,
        since_ms=100, until_ms=200,
    )
    w = result.writers[0]
    assert w.coincident == 1 and w.matched == 0
    assert not w.clocked_only and not result.cognition_only


def test_an_act_keyless_live_row_is_clocked_only_and_flagged():
    live = ProactiveAct(reminder_id="p", platform="signal", conversation_id="r", at_ms=5)
    result = act_tier(
        "act", CompareResult(matched=(), cognition_only=(), proactive_only=(), unkeyed=(live,)),
        closes=[], tolerance_ms=1,
    )
    (gap,) = result.writers[0].clocked_only
    assert gap.keyless and gap.closed_by is None


def test_a_room_without_a_label_is_a_stable_hash_never_the_id():
    d = RoomDirectory(labels={}, unit_rooms={})
    assert d.label("signal:secret-id") == d.label("secret-id")
    assert "secret" not in d.label("secret-id")


def test_only_a_closing_reason_explains_a_clocked_only_row():
    for reason, closed_by in (
        ("no_change", "no_change"), ("waiting", "waiting"), ("covered", "covered"),
        ("written", None), ("struck", None),
    ):
        result = compare_tier(
            "standing", cognition=[], refusals=[], clocked=[_w("a", 100)],
            closes=[Close(session_id=1, room="r", reason=reason, at_ms=100)],
            tolerance_ms=50, since_ms=0, until_ms=1000,
        )
        assert result.writers[0].clocked_only[0].closed_by == closed_by, reason


# ---- a close is narrow: room, tier, id, after, once --------------------------

def _claim(ledger, *, tier="standing", room="r", at=100, key=""):
    return ledger.claim(tier=tier, room=room, at_ms=at, key=key)


def _close(at=110, **kw):
    kw.setdefault("reason", "waiting")
    return Close(session_id=kw.pop("session_id", 1), room=kw.pop("room", "r"), at_ms=at, **kw)


def test_one_close_explains_at_most_one_row():
    ledger = CloseLedger([_close()], tolerance_ms=50)
    assert _claim(ledger, at=100) == "waiting"
    assert _claim(ledger, at=101) is None


def test_the_reviewers_scenario_one_waiting_close_does_not_close_three_writers_rows():
    result = compare_tier(
        "standing", cognition=[], refusals=[],
        clocked=[
            ClockedWrite(writer="standing-debrief", tier="standing", id="a", room="r", at_ms=100),
            ClockedWrite(writer="standing-debrief", tier="standing", id="b", room="r", at_ms=101),
            ClockedWrite(
                writer="standing-ingest-fold", tier="standing", id="c", room="r", at_ms=102,
            ),
        ],
        closes=[_close(at=110)], tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    assert sum(w.closed for w in result.writers) == 1 and sum(w.open for w in result.writers) == 2


def test_each_row_takes_the_nearest_unused_close_in_time_order():
    ledger = CloseLedger([_close(at=105, session_id=1), _close(at=140, session_id=2)], 50)
    assert _claim(ledger, at=100) == "waiting"   # takes the 105 close
    assert _claim(ledger, at=104) == "waiting"   # only the 140 close is left
    assert _claim(ledger, at=106) is None


def test_a_close_before_the_clocked_write_does_not_explain_it():
    assert _claim(CloseLedger([_close(at=99)], 50), at=100) is None
    assert _claim(CloseLedger([_close(at=100)], 50), at=100) == "waiting"


def test_a_close_outside_the_tolerance_or_in_another_room_does_not_explain_it():
    assert _claim(CloseLedger([_close(at=151)], 50), at=100) is None
    assert _claim(CloseLedger([_close(room="other")], 50), at=100) is None
    assert _claim(CloseLedger([_close(room="signal:r")], 50), at=100) == "waiting"


def test_a_close_explains_only_the_tiers_its_item_kind_is_about():
    for kind, tier, expected in (
        ("message", "note", "waiting"), ("message", "standing", "waiting"),
        ("rollup", "standing", "waiting"), ("rollup", "note", None), ("time", "standing", None),
        ("time", "note", "waiting"), ("self", "note", None), ("social", "standing", None),
        ("play", "event", None), ("message", "culture", None),
    ):
        assert _claim(CloseLedger([_close(kind=kind)], 50), tier=tier) == expected, (kind, tier)


def test_a_row_with_an_id_is_explained_only_by_a_close_that_names_it():
    named = _close(cited=frozenset({"n-1"}))
    assert _claim(CloseLedger([named], 50), tier="note", key="n-1") == "waiting"
    assert _claim(CloseLedger([named], 50), tier="note", key="n-2") is None
    assert _claim(CloseLedger([_close()], 50), tier="note", key="n-1") is None


def test_a_close_that_names_no_id_explains_a_row_without_one():
    assert _claim(CloseLedger([_close()], 50), tier="standing", key="") == "waiting"


def test_a_refused_would_write_nearby_does_not_close_a_row():
    from nora.domain.cognition_shadow_compare import Refusal
    result = compare_tier(
        "standing", cognition=[], refusals=[Refusal(tier="standing", room="r", at_ms=100)],
        clocked=[_w("a", 100)], closes=[], tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    (gap,) = result.writers[0].clocked_only
    assert gap.closed_by is None and gap.refused_nearby == 1


def test_a_standing_pair_is_coincident_never_matched():
    result = compare_tier(
        "standing", cognition=[_c("1", 100)], refusals=[], clocked=[_w("a", 110)], closes=[],
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    w = result.writers[0]
    assert (w.matched, w.coincident) == (0, 1)


def test_the_live_tag_path_is_its_own_section_and_in_no_verdict():
    from nora.domain.cognition_shadow_compare import ShadowReport, tag_path_lines, verdict_lines
    tag = ClockedWrite(writer="event:tag", tier="event", id="e", room="r", at_ms=100, key="ev")
    result = compare_tier(
        "event", cognition=[], refusals=[], clocked=[tag],
        closes=[_close(kind="message", cited=frozenset({"ev"}))],
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    assert result.writers == () and [w.writer for w in result.tag_path] == ["event:tag"]
    assert result.tag_path[0].clocked_only[0].closed_by is None
    report = ShadowReport(since_ms=0, until_ms=1000, tolerance_ms=50, unit=None, tiers=(result,))
    assert verdict_lines(report) == [] and "event:tag" in tag_path_lines(report)[0]


def _note_c(title="", anchor=(20260930, ())):
    return CognitionWrite(
        id="1", tier="note", verb="note_create", room="r", at_ms=100, anchor=anchor, title=title,
    )


def _note_w(title="", anchor=(20260930, ())):
    return ClockedWrite(
        writer="note-candidates", tier="note", id="a", room="r", at_ms=100, create=True,
        anchor=anchor, title=title,
    )


def test_a_note_create_needs_the_same_title_or_a_shared_entity_on_top_of_the_anchor():
    def pairs(c, w):
        return len(match_writes([c], [w], tolerance_ms=50)[0])
    assert pairs(_note_c("Dinner  Friday"), _note_w("dinner friday")) == 1
    assert pairs(_note_c("Dinner"), _note_w("Lunch")) == 0          # same day, other note
    assert pairs(_note_c("x", (20260930, ("p1",))), _note_w("y", (20260930, ("p1",)))) == 1
    assert pairs(_note_c("", (20260930, ())), _note_w("", (20260930, ()))) == 0
