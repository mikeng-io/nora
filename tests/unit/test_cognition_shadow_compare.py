"""The pure half of the shadow-week comparison
(`nora.domain.cognition_shadow_compare`): room identity, the Standing writer
derivation, and the greedy one-to-one match."""

from __future__ import annotations

from nora.domain.cognition_compare import CompareResult, ProactiveAct
from nora.domain.cognition_shadow_compare import (
    ClockedWrite,
    Close,
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
    assert w.matched == 1 and not w.clocked_only and not result.cognition_only


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
