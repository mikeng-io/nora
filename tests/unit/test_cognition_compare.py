"""`nora.domain.cognition_compare.compare` — matching cognition's mirrored
`remind` calls against the live proactive pass's decisions over a window
(migration step 3a-ii)."""

from __future__ import annotations

from nora.app.cognition_compare import CompareWindow, render
from nora.domain.cognition_compare import (
    CompareResult,
    MirroredAct,
    MirrorRow,
    ProactiveAct,
    compare,
    resolve_chains,
)


def test_a_shared_note_id_and_room_matches():
    m = MirroredAct(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:n1",), at_ms=1000,
    )
    p = ProactiveAct(
        reminder_id="p1", platform="signal", conversation_id="room-a",
        note_id="note:n1", at_ms=1000,
    )
    result = compare((m,), (p,))
    assert result.counts == {"matched": 1, "cognition_only": 0, "proactive_only": 0}
    assert result.matched == ((m, p),)


def test_a_mirrored_act_citing_no_matching_note_is_cognition_only():
    m = MirroredAct(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("event:e1",), at_ms=1000,
    )
    result = compare((m,), ())
    assert result.counts == {"matched": 0, "cognition_only": 1, "proactive_only": 0}


def test_a_proactive_act_with_no_mirrored_counterpart_is_proactive_only():
    p = ProactiveAct(
        reminder_id="p1", platform="signal", conversation_id="room-a",
        note_id="note:n1", at_ms=1000,
    )
    result = compare((), (p,))
    assert result.counts == {"matched": 0, "cognition_only": 0, "proactive_only": 1}


def test_a_different_room_never_matches_even_with_the_same_note_id():
    m = MirroredAct(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:n1",), at_ms=1000,
    )
    p = ProactiveAct(
        reminder_id="p1", platform="signal", conversation_id="room-b",
        note_id="note:n1", at_ms=1000,
    )
    result = compare((m,), (p,))
    assert result.counts == {"matched": 0, "cognition_only": 1, "proactive_only": 1}


def test_a_proactive_act_matches_at_most_one_mirrored_act():
    m1 = MirroredAct(
        reminder_ref="r1", platform="signal", conversation_id="room-a",
        subject_rows=("note:n1",), at_ms=1000,
    )
    m2 = MirroredAct(
        reminder_ref="r2", platform="signal", conversation_id="room-a",
        subject_rows=("note:n1",), at_ms=1100,
    )
    p = ProactiveAct(
        reminder_id="p1", platform="signal", conversation_id="room-a",
        note_id="note:n1", at_ms=1000,
    )
    result = compare((m1, m2), (p,))
    assert result.counts == {"matched": 1, "cognition_only": 1, "proactive_only": 0}


# --------------------------------------------------------------------------
# review of #627: event ids, chains, unkeyed rows, and the footnotes
# --------------------------------------------------------------------------

def _m(ref="r1", subjects=("note:n1",), room="room-a", **kw):
    return MirroredAct(
        reminder_ref=ref, platform="signal", conversation_id=room,
        subject_rows=tuple(subjects), at_ms=1000, **kw,
    )


def _p(pid="p1", room="room-a", **kw):
    return ProactiveAct(reminder_id=pid, platform="signal", conversation_id=room, at_ms=1000, **kw)


def test_an_event_anchored_remind_matches_by_event_id_too():
    result = compare((_m(subjects=("event:e1",)),), (_p(event_id="e1"),))
    assert result.counts == {"matched": 1, "cognition_only": 0, "proactive_only": 0}


def test_an_id_matches_bare_or_typed_on_either_side():
    """The proactive payload stores the bare id; a subject row is typed."""
    assert compare((_m(subjects=("note:n1",)),), (_p(note_id="n1"),)).counts["matched"] == 1
    assert compare((_m(subjects=("n1",)),), (_p(note_id="note:n1"),)).counts["matched"] == 1
    assert compare((_m(subjects=("event:n1",)),), (_p(note_id="note:n1"),)).counts["matched"] == 1


def test_a_proactive_reminder_naming_neither_note_nor_event_is_listed_unkeyed_not_dropped():
    p = _p()
    result = compare((_m(),), (p,))
    assert result.unkeyed == (p,)
    assert result.counts == {"matched": 0, "cognition_only": 1, "proactive_only": 0}


def test_a_cancelled_mirrored_remind_is_in_neither_matched_nor_cognition_only():
    result = compare((_m(cancelled=True),), (_p(note_id="n1"),))
    assert result.counts == {"matched": 0, "cognition_only": 0, "proactive_only": 1}
    assert [m.reminder_ref for m in result.cancelled] == ["r1"]


def test_a_moved_mirrored_remind_is_counted_once_and_reported_moved():
    result = compare((_m(moved=True),), (_p(note_id="n1"),))
    assert result.counts["matched"] == 1
    assert [m.reminder_ref for m in result.moved] == ["r1"]


def test_a_cognition_only_remind_naming_no_note_or_event_is_unkeyable_by_construction():
    result = compare((_m(subjects=("rollup:x",)), _m(ref="r2", subjects=("note:z",))), ())
    assert result.counts["cognition_only"] == 2
    assert [m.reminder_ref for m in result.unkeyable] == ["r1"]


def _row(verb, ref="r1", at=1000, outcome="recorded", subjects=("note:n1",)):
    return MirrorRow(
        verb=verb, reminder_ref=ref, platform="signal", conversation_id="room-a",
        subject_rows=subjects, at_ms=at, outcome=outcome,
    )


def test_a_chain_resolves_to_its_latest_state():
    (act,) = resolve_chains([_row("remind"), _row("remind_move", at=5000)])
    assert (act.at_ms, act.moved, act.cancelled) == (5000, True, False)
    (act,) = resolve_chains([_row("remind"), _row("remind_move", at=5000), _row("remind_cancel")])
    assert (act.at_ms, act.moved, act.cancelled) == (5000, True, True)


def test_a_move_after_a_cancel_and_a_refused_row_change_nothing_and_orphans_are_ignored():
    (act,) = resolve_chains([
        _row("remind"), _row("remind_cancel"), _row("remind_move", at=9000),
        _row("remind_move", at=7000, outcome="refused"),
    ])
    assert act.cancelled and act.at_ms == 1000
    assert resolve_chains([_row("remind_cancel", ref="began-before-the-window")]) == ()


def test_the_render_prints_a_footnote_section_for_everything_left_out_of_the_counts():
    result = compare(
        (_m(ref="c", cancelled=True), _m(ref="mv", moved=True, subjects=("rollup:x",))),
        (_p(pid="unkeyed"),),
    )
    text = "\n".join(render(result, CompareWindow(since_ms=0, until_ms=1000)))
    assert "cancelled:  1" in text
    assert "moved:      1" in text
    assert "unkeyed:    1" in text
    assert "unkeyable:  1" in text
    assert "unkeyed  signal:room-a  id=unkeyed" in text
    assert isinstance(result, CompareResult)
