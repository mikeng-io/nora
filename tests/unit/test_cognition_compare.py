"""`nora.domain.cognition_compare.compare` — matching cognition's mirrored
`remind` calls against the live proactive pass's decisions over a window
(migration step 3a-ii)."""

from __future__ import annotations

from nora.domain.cognition_compare import MirroredAct, ProactiveAct, compare


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
