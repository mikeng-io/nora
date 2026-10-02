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
    SessionCoverage,
    ShadowReport,
    act_tier,
    compare_tier,
    match_writes,
    render_text,
    report_json,
    room_id,
    standing_writer,
    verdict_lines,
)

COVERAGE = SessionCoverage(
    sessions=4, units_with_sessions=1, policy_units=2, days=2.0, active_days=2, wakes=6,
    wakes_without_session=2, wakes_failed_without_session=1, sessions_without_skills=4,
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
    # The one explaining close is a `waiting`: the model's own say-so, so it is
    # counted as self-attested and NOT as closed (this assertion used to read
    # `closed == 1`, which counted the model's word as a closed row).
    assert sum(w.closed for w in result.writers) == 0
    assert sum(w.closed_self_attested for w in result.writers) == 1
    # `open` is "not closed by code": the self-attested row stays in it, so all three
    # rows are open and no number reads as progress the model's own word produced
    assert sum(w.open for w in result.writers) == 3


def test_each_row_takes_the_nearest_unused_close_in_time_order():
    ledger = CloseLedger([_close(at=105, session_id=1), _close(at=140, session_id=2)], 50)
    assert _claim(ledger, at=100) == "waiting"   # the 105 close, not the 140 one
    assert _claim(ledger, at=130) == "waiting"   # the 140 close is still there for it
    assert _claim(ledger, at=131) is None


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
    report = ShadowReport(
        since_ms=0, until_ms=1000, tolerance_ms=50, unit=None, tiers=(result,),
        coverage=COVERAGE,
    )
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


# ---- what a close proves: the zero session case, all self-attested, and a mix ---------------

def _rows(*ids: str) -> list[ClockedWrite]:
    return [
        ClockedWrite(writer="standing-debrief", tier="standing", id=i, room="r", at_ms=100 + n)
        for n, i in enumerate(ids)
    ]


def _report(tier, coverage=COVERAGE, error=None):
    return ShadowReport(
        since_ms=0, until_ms=1000, tolerance_ms=50, unit=None, tiers=(tier,) if tier else (),
        coverage=coverage, error=error,
    )


def test_no_session_is_one_error_line_and_no_writer_lines_never_zeros():
    empty = SessionCoverage(
        sessions=0, units_with_sessions=0, policy_units=2, days=7.0, active_days=0, wakes=5,
        wakes_without_session=5, wakes_failed_without_session=0, sessions_without_skills=0,
    )
    report = _report(None, coverage=empty, error="no Cognition session ran in this window")
    assert report.any_error
    assert verdict_lines(report) == ["ERROR: no Cognition session ran in this window"]
    text = "\n".join(render_text(report, RoomDirectory({}, {})))
    assert "ERROR: no Cognition session ran" in text and "matched" not in text.split("ERROR")[1]
    assert "0 clocked-only" not in text and "0 matched" not in text
    data = report_json(report, RoomDirectory({}, {}))
    assert data["status"] == "error" and data["tiers"] == [] and data["coverage"]["sessions"] == 0


def test_a_report_with_unread_coverage_is_an_error_not_a_report():
    report = _report(None, coverage=None)
    assert report.any_error
    assert verdict_lines(report) == ["ERROR: session coverage was not read; nothing to compare"]


def test_a_window_where_every_session_closes_by_its_own_say_so_closes_nothing():
    closes = [
        Close(session_id=1, room="r", reason="no_change", at_ms=110),
        Close(session_id=2, room="r", reason="waiting", at_ms=111),
        Close(session_id=3, room="r", reason="covered", at_ms=112),     # no write backs it
    ]
    tier = compare_tier(
        "standing", cognition=[], refusals=[], clocked=_rows("a", "b", "c"), closes=closes,
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    w = tier.writers[0]
    # all three are open: the three closes are the model's, and none is evidence
    assert (len(w.clocked_only), w.closed, w.closed_self_attested, w.open) == (3, 0, 3, 3)
    line = verdict_lines(_report(tier))[0]
    assert (
        "0 closed by code, 3 closed self-attested: not evidence, 3 open: everything not "
        "closed by code" in line
    )
    assert all(not g.closed_by_code for g in w.clocked_only)


def _note_rows(*ids: str) -> list[ClockedWrite]:
    return [
        ClockedWrite(
            writer="note-curation", tier="note", id=f"rev-{i}", room="r", at_ms=100 + n, key=i,
        )
        for n, i in enumerate(ids)
    ]


def _noted(reason: str, key: str, *, at: int = 110, session: int = 1, writes=frozenset()):
    return Close(
        session_id=session, room="r", reason=reason, at_ms=at, kind="message",
        cited=frozenset({key}), session_writes=writes,
    )


def test_a_mix_counts_the_proven_close_apart_from_the_say_so_ones_and_the_open_one():
    closes = [
        _noted("covered", "n1", writes=frozenset({("note", "r", "n1")})),     # backed
        _noted("no_change", "n2", at=111, session=2),                         # say-so
    ]
    tier = compare_tier(
        "note", cognition=[], refusals=[], clocked=_note_rows("n1", "n2", "n3"), closes=closes,
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    w = next(v for v in tier.writers if v.writer == "note-curation")
    assert (w.closed, w.closed_self_attested, w.open) == (1, 1, 2)
    assert w.closed + w.open == len(w.clocked_only)             # open = not closed by code
    assert sorted((g.closed_by, g.closed_by_code) for g in w.clocked_only if g.closed_by) == [
        ("covered", True), ("no_change", False),
    ]


def test_a_proven_close_is_taken_before_a_say_so_one_at_the_same_distance():
    backed = frozenset({("note", "r", "n1")})
    ledger = CloseLedger([
        _noted("covered", "n1", session=1),                                   # unbacked
        _noted("covered", "n1", session=2, writes=backed),
    ], tolerance_ms=50)
    assert ledger.claim_with_proof(tier="note", room="r", at_ms=100, key="n1") == (
        "covered", True,
    )


def test_a_row_with_no_id_is_never_closed_by_code_however_much_the_session_wrote():
    # The reviewer's case: a `covered` rollup close plus ANY standing write of the same
    # session and room, hours away. Nothing links that write to THIS clocked row but the
    # room and the hour, which is not a proof.
    backed = frozenset({("standing", "r", ""), ("standing", "r", "anything")})
    tier = compare_tier(
        "standing", cognition=[], refusals=[], clocked=_rows("a"),
        closes=[Close(session_id=1, room="r", reason="covered", at_ms=110, kind="rollup",
                      session_writes=backed)],
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    w = tier.writers[0]
    assert (w.closed, w.closed_self_attested, w.open) == (0, 1, 1)
    # and a keyless PROACTIVE row is the same
    ledger = CloseLedger([Close(session_id=1, room="r", reason="covered", at_ms=110,
                                kind="message", session_writes=frozenset({("act", "r", "")}))],
                         tolerance_ms=50)
    assert ledger.claim_with_proof(tier="act", room="r", at_ms=100, key="") == ("covered", False)


def test_a_creation_or_another_tier_or_another_id_cannot_prove_a_note_row():
    def proof(writes):
        ledger = CloseLedger([_noted("covered", "n1", writes=frozenset(writes))], 50)
        got = ledger.claim_with_proof(tier="note", room="r", at_ms=100, key="n1")
        assert got is not None                        # the close explains the row either way
        return got[1]
    assert proof({("note", "r", "n1")}) is True
    assert proof({("note", "r", "")}) is False        # a `note_create` names no existing id
    assert proof({("note", "r", "n2")}) is False      # another note
    assert proof({("event", "r", "n1")}) is False     # another tier, even for the same id
    assert proof({("event", "r", "")}) is False
    assert proof({("note", "elsewhere", "n1")}) is False   # another room


def test_a_close_is_proven_only_for_the_same_tier_room_and_id():
    backed = frozenset({("note", "r", "n1")})
    cited = frozenset({"n1", "n2"})

    def claim(tier, key, room="r"):
        ledger = CloseLedger([Close(
            session_id=1, room="r", reason="covered", at_ms=110, kind="message", cited=cited,
            session_writes=backed,
        )], tolerance_ms=50)
        return ledger.claim_with_proof(tier=tier, room=room, at_ms=100, key=key)

    assert claim("note", "n1") == ("covered", True)
    assert claim("note", "n2") == ("covered", False)        # explains it, proves nothing
    assert claim("event", "") == ("covered", False)
    assert claim("note", "n1", room="elsewhere") is None    # not even an explanation


def test_the_report_says_what_matched_means_and_that_a_say_so_is_not_evidence():
    tier = compare_tier(
        "standing", cognition=[], refusals=[], clocked=_rows("a"), closes=[],
        tolerance_ms=50, since_ms=0, until_ms=1000,
    )
    text = "\n".join(render_text(_report(tier), RoomDirectory({}, {})))
    assert "matched = the same KIND" in text and "does NOT mean the two writes say the same" in text
    assert "is its own say-so" in text and "never closed or matched" in text
    assert "no skills injected in 4 of 4 session(s)" in text
    assert "2 of 6 wake(s) never became a session (1 of those failed)" in text
    assert "2.0 per day over 2.0 day(s)" in text
