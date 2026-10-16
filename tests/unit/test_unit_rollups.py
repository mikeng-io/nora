"""What each unit rollup says, on
fixtures: co-location, outing (and its `unlabeled` home case), daybook, tie,
response; the healthy-source proofs deviation rests on; silent
daybook runs; citation by overlap. All pure: `nora.domain.rollup`."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nora.domain.rollup import (
    Citation,
    FollowCandidate,
    HealthRow,
    Home,
    MessageFact,
    ReactionFact,
    ResponseEvidence,
    SendFact,
    StaySpan,
    adapter_up,
    colocations,
    daybooks,
    iso_week,
    lineage_json,
    outings,
    polls_healthy,
    resolve_by_overlap,
    responses,
    silent_runs,
    ties,
    unquoted_follows,
)

_HKT = ZoneInfo("Asia/Hong_Kong")
_MIN = 60_000
_H = 3_600_000


def _t(day: int, hour: float) -> int:
    return int(datetime(2026, 9, day, tzinfo=_HKT).timestamp() * 1000 + hour * _H)


PUB = (22.2800, 114.1500)
HOME_A = (22.4000, 114.1000)
HOME_B = (22.3500, 114.2000)


def _stay(person, start, end, at, *, open_=False, kind=None, label=None):
    return StaySpan(
        person_id=person, started_ts=start, ended_ts=end, lat=at[0], lng=at[1],
        radius_m=40.0, open=open_, place_kind=kind, place_label=label,
    )


HOMES = {
    "a": [Home("a", *HOME_A, 100.0)],
    "b": [Home("b", *HOME_B, 100.0)],
    "c": [Home("c", 22.5, 114.3, 100.0)],
}


def _colo(stays, homes=HOMES, as_of=None):
    return colocations(
        "u", stays, homes=homes, radius_m=150.0, min_overlap_ms=10 * _MIN,
        as_of_ms=as_of if as_of is not None else _t(20, 23),
    )


# -- co-location ------------------------------------------------------------


def test_two_members_at_one_place_at_one_time_are_a_colocation():
    [c] = _colo([
        _stay("a", _t(19, 19), _t(19, 22), PUB),
        _stay("b", _t(19, 20), _t(19, 23), (PUB[0] + 0.0005, PUB[1])),
    ])
    assert c.persons == ("a", "b")
    assert (c.valid_from_ms, c.valid_to_ms) == (_t(19, 20), _t(19, 22))
    assert c.at_home_of == () and c.homes_unknown == ()
    assert c.lineage == (("presence", "a", None), ("presence", "b", None))
    assert lineage_json(c.lineage) == [["presence", "a", None], ["presence", "b", None]]


def test_too_far_apart_or_too_brief_is_not_a_colocation():
    far = (PUB[0] + 0.01, PUB[1])  # ~1.1 km
    assert _colo([_stay("a", _t(19, 19), _t(19, 22), PUB),
                  _stay("b", _t(19, 19), _t(19, 22), far)]) == []
    assert _colo([_stay("a", _t(19, 19), _t(19, 20), PUB),
                  _stay("b", _t(19, 20) - 5 * _MIN, _t(19, 21), PUB)]) == []


def test_one_person_twice_is_not_a_colocation():
    assert _colo([_stay("a", _t(19, 19), _t(19, 20), PUB),
                  _stay("a", _t(19, 19), _t(19, 22), PUB)]) == []


def test_a_chain_of_overlaps_at_one_place_is_one_episode_of_everyone():
    [c] = _colo([
        _stay("a", _t(19, 18), _t(19, 20), PUB),
        _stay("b", _t(19, 19), _t(19, 21), PUB),
        _stay("c", _t(19, 20.5), _t(19, 23), PUB),
    ])
    assert c.persons == ("a", "b", "c")
    assert (c.valid_from_ms, c.valid_to_ms) == (_t(19, 19), _t(19, 21))
    assert c.stay_count == 3


def test_a_colocation_that_runs_to_now_is_open():
    [c] = _colo([
        _stay("a", _t(20, 19), _t(20, 21), PUB, open_=True),
        _stay("b", _t(20, 19.5), _t(20, 21), PUB, open_=True),
    ], as_of=_t(20, 21))
    assert c.open


# -- outing ----------------------------------------------------------------


def test_an_outing_is_a_colocation_away_from_every_members_home():
    found = _colo([_stay("a", _t(19, 19), _t(19, 22), PUB),
                   _stay("b", _t(19, 19), _t(19, 22), PUB)])
    [o] = outings(found)
    assert o.persons == ("a", "b") and o.valid_from_ms == _t(19, 19)


def test_a_visit_to_a_members_home_is_not_an_outing():
    found = _colo([_stay("a", _t(19, 19), _t(19, 22), HOME_A),
                   _stay("b", _t(19, 19), _t(19, 22), HOME_A)])
    assert found[0].at_home_of == ("a",)
    assert outings(found) == []


def test_a_member_with_no_known_home_makes_it_not_an_outing():
    """The `unlabeled` home case: a member with no place labelled home cannot
    be proved away from it, so the co-location is kept (and says whose home
    is unknown) and is not an outing — never a guess."""
    homes = {"a": HOMES["a"]}  # b's home is unlabeled
    found = _colo([_stay("a", _t(19, 19), _t(19, 22), PUB),
                   _stay("b", _t(19, 19), _t(19, 22), PUB)], homes=homes)
    assert found[0].homes_unknown == ("b",)
    assert outings(found) == []


# -- daybook ---------------------------------------------------------------


def test_a_daybook_splits_a_stay_across_local_midnight_and_counts_the_day():
    stays = [
        _stay("a", _t(19, 22), _t(20, 7), HOME_A, kind="home", label="Home"),
        _stay("a", _t(20, 9), _t(20, 18), (22.3, 114.17), kind="work"),
    ]
    messages = [
        MessageFact(id=1, person_id="a", room="signal:g1", ts=_t(20, 12)),
        MessageFact(id=2, person_id="a", room="discord:d1", ts=_t(20, 13)),
        MessageFact(id=3, person_id="a", room="signal:g1", ts=_t(20, 14)),
    ]
    reactions = [ReactionFact(id=1, person_id="a", target_person="b", room="signal:g1",
                              ts=_t(20, 15))]
    books = {
        b.local_date: b for b in daybooks(
            "u", ["a", "b"], stays=stays, found=[], outings_found=[],
            messages=messages, reactions=reactions, as_of_ms=_t(20, 23),
        )
    }
    assert set(books) == {"2026-09-19", "2026-09-20"}
    d19, d20 = books["2026-09-19"], books["2026-09-20"]
    assert d19.stay_ms == d19.home_ms == 2 * _H
    assert d20.home_ms == 7 * _H and d20.stay_ms == 16 * _H
    assert d20.rooms == {"discord:d1": 1, "signal:g1": 2}
    assert (d20.messages, d20.reactions) == (3, 1)
    assert d20.items_posted is None  # no social input was given: not an input
    assert not d20.silent and not d19.silent
    assert ("message", None, "discord:d1") in d20.lineage
    assert ("presence", "a", None) in d20.lineage


def test_a_day_away_from_home_with_nothing_posted_is_silent():
    books = daybooks(
        "u", ["a"], stays=[_stay("a", _t(19, 9), _t(19, 18), PUB)], found=[],
        outings_found=[], messages=[], reactions=[], as_of_ms=_t(20, 1),
    )
    assert [b.silent for b in books] == [True]


def test_silent_runs_count_completed_days_only():
    book = daybooks(
        "u", ["a"], stays=[_stay("a", _t(15, 9), _t(15, 18), HOME_A, kind="home")],
        found=[], outings_found=[], messages=[], reactions=[], as_of_ms=_t(19, 12),
    )
    # 15th at home; 16th, 17th, 18th hold nothing; the 19th is still running.
    assert silent_runs(book, person_id="a", days=2, through_date="2026-09-19") == [
        ("2026-09-16", "2026-09-17"),
    ]
    assert silent_runs(book, person_id="a", days=4, through_date="2026-09-19") == []


# -- tie -------------------------------------------------------------------


def test_a_tie_counts_each_direction_and_the_minutes_together():
    messages = [
        MessageFact(id=1, person_id="a", room="signal:g1", ts=_t(21, 10), quoted_person="b"),
        MessageFact(id=2, person_id="b", room="signal:g1", ts=_t(21, 11), mentioned=("a",)),
        MessageFact(id=3, person_id="b", room="signal:g1", ts=_t(21, 12), mentioned=("b",)),
    ]
    reactions = [ReactionFact(id=1, person_id="b", target_person="a", room="signal:g1",
                              ts=_t(21, 13))]
    found = _colo([_stay("a", _t(21, 19), _t(21, 22), PUB),
                   _stay("b", _t(21, 19), _t(21, 22), PUB)])
    [tie] = ties("u", ["a", "b"], messages=messages, reactions=reactions, found=found,
                 as_of_ms=_t(21, 23))
    assert (tie.person_a, tie.person_b, tie.iso_week) == ("a", "b", iso_week(_t(21, 10)))
    assert (tie.replies_ab, tie.replies_ba) == (1, 0)
    assert (tie.mentions_ab, tie.mentions_ba) == (0, 1)  # a self-mention is not a tie
    assert (tie.reactions_ab, tie.reactions_ba) == (0, 1)
    assert tie.colocation_ms == 3 * _H
    assert ("presence", "a", None) in tie.lineage and ("message", None, "signal:g1") in tie.lineage


def test_a_tie_splits_a_colocation_that_crosses_a_week_boundary():
    # Sunday 2026-09-20 22:00 to Monday 02:00 HKT: two ISO weeks.
    found = _colo([_stay("a", _t(20, 22), _t(21, 2), PUB),
                   _stay("b", _t(20, 22), _t(21, 2), PUB)], as_of=_t(21, 3))
    rows = ties("u", ["a", "b"], messages=[], reactions=[], found=found, as_of_ms=_t(21, 3))
    assert sorted((t.iso_week, t.colocation_ms) for t in rows) == [
        (iso_week(_t(20, 22)), 2 * _H), (iso_week(_t(21, 1)), 2 * _H),
    ]


# -- response --------------------------------------------------------------


SEND = SendFact(id=10, room="signal:g1", platform="signal", conversation_id="g1",
                ts=_t(22, 10))


def _respond(sends, evidence, *, as_of, up=True):
    return {
        r.message_id: r for r in responses(
            "u", sends, evidence, window_ms=_H, as_of_ms=as_of,
            platform_up={s.id: up for s in sends},
        )
    }


def test_a_send_answered_inside_the_window_responded():
    evidence = [
        ResponseEvidence(send_id=10, person_id="a", ts=_t(22, 10.2), kind="reply"),
        ResponseEvidence(send_id=10, person_id="b", ts=_t(22, 10.3), kind="reaction"),
        ResponseEvidence(send_id=10, person_id="a", ts=_t(22, 10.4), kind="edit"),
        ResponseEvidence(send_id=10, person_id="b", ts=_t(22, 12), kind="reply"),  # too late
    ]
    r = _respond([SEND], evidence, as_of=_t(22, 13))[10]
    assert (r.outcome, r.replies, r.reactions, r.edits, r.responders) == (
        "responded", 1, 1, 1, ("a", "b"),
    )
    assert r.lineage == (("message", None, "signal:g1"),)


def test_a_window_still_running_on_the_records_clock_is_open():
    assert _respond([SEND], [], as_of=_t(22, 10.5))[10].outcome == "open"


def test_silence_is_only_called_when_an_answer_was_expected_and_the_room_was_up():
    named = SendFact(id=11, room="signal:g1", platform="signal", conversation_id="g1",
                     ts=_t(22, 10), named=("a",))
    asked = SendFact(id=12, room="signal:g1", platform="signal", conversation_id="g1",
                     ts=_t(22, 10), intent="ask")
    after = _t(22, 12)
    up = _respond([SEND, named, asked], [], as_of=after, up=True)
    assert (up[10].outcome, up[11].outcome, up[12].outcome) == ("unanswered", "silent", "silent")
    down = _respond([named], [], as_of=after, up=False)
    assert down[11].outcome == "unknown"  # an outage is not silence (R-27)
    assert up[10].intent is None  # no intent recorded: never read as `tell`


def test_a_follow_is_a_response_and_is_counted_apart_from_a_quoted_reply():
    evidence = [
        ResponseEvidence(send_id=10, person_id="a", ts=_t(22, 10.01), kind="follow"),
        ResponseEvidence(send_id=10, person_id="b", ts=_t(22, 10.2), kind="reply"),
    ]
    r = _respond([SEND], evidence, as_of=_t(22, 13))[10]
    assert (r.outcome, r.replies, r.follows, r.responders) == ("responded", 1, 1, ("a", "b"))
    only = _respond([SEND], evidence[:1], as_of=_t(22, 13))[10]
    assert (only.outcome, only.replies, only.follows) == ("responded", 0, 1)
    # Late is `responses()`'s call, by its own window: a follow after it is nothing.
    late = [ResponseEvidence(send_id=10, person_id="a", ts=_t(22, 12), kind="follow")]
    assert _respond([SEND], late, as_of=_t(22, 13))[10].outcome == "unanswered"


# -- unquoted follows ------------------------------------------------

_S = 1000  # ms per second
_ROOM = "signal:g1"


def _send(id, at_s, turn=None, room=_ROOM):
    return SendFact(id=id, room=room, platform="signal", conversation_id="g1",
                    ts=at_s * _S, turn=turn)


def _msg(id, at_s, *, names=False, other=False, person="a", room=_ROOM):
    return FollowCandidate(id=id, person_id=person, room=room, ts=at_s * _S,
                           names_nora=names, mentions_other=other)


def _follows(sends, candidates):
    return [(e.send_id, e.person_id, e.ts, e.kind)
            for e in unquoted_follows(sends, candidates, follow_window_ms=120 * _S)]


def test_an_unquoted_message_soon_after_her_send_follows_it():
    assert _follows([_send(1, 0)], [_msg(5, 30)]) == [(1, "a", 30 * _S, "follow")]
    assert _follows([_send(1, 0)], [_msg(5, 120)]) == [(1, "a", 120 * _S, "follow")]


def test_an_unquoted_message_after_the_window_follows_only_when_it_names_her():
    assert _follows([_send(1, 0)], [_msg(5, 300)]) == []
    assert _follows([_send(1, 0)], [_msg(5, 300, names=True)]) == [(1, "a", 300 * _S, "follow")]


def test_a_message_mentioning_someone_else_soon_after_is_not_a_follow():
    assert _follows([_send(1, 0)], [_msg(5, 30, other=True)]) == []
    # ...unless it also names her.
    assert len(_follows([_send(1, 0)], [_msg(5, 30, other=True, names=True)])) == 1


def test_a_quote_of_another_members_message_is_read_like_a_mention_of_them():
    """The store sets `mentions_other` for a quote of any non-Nora message."""
    assert _follows([_send(1, 0)], [_msg(5, 30, other=True)]) == []
    assert _follows([_send(1, 0)], [_msg(5, 300, other=True, names=True)]) == [
        (1, "a", 300 * _S, "follow")]


def test_a_message_before_any_send_or_in_another_room_follows_nothing():
    assert _follows([_send(1, 100)], [_msg(5, 50)]) == []
    assert _follows([_send(1, 0)], [_msg(5, 30, room="signal:other")]) == []
    assert _follows([_send(1, 0)], [_msg(5, 30, names=True, room="signal:other")]) == []


def test_a_message_after_her_next_send_of_another_turn_attaches_to_that_send_only():
    sends = [_send(1, 0, "t1"), _send(2, 50, "t2")]
    assert _follows(sends, [_msg(5, 60)]) == [(2, "a", 60 * _S, "follow")]
    # Naming her late still counts for the LATEST send, never the earlier one.
    assert _follows(sends, [_msg(5, 900, names=True)]) == [(2, "a", 900 * _S, "follow")]


def test_a_multi_part_turn_is_answered_at_its_last_part():
    sends = [_send(1, 0, "t"), _send(2, 5, "t"), _send(3, 8, "t")]
    assert _follows(sends, [_msg(5, 40)]) == [(3, "a", 40 * _S, "follow")]
    # Between the parts it still answers the turn, stamped at the last part.
    assert _follows(sends, [_msg(5, 6)]) == [(3, "a", 8 * _S, "follow")]


def test_a_send_with_no_turn_is_its_own_turn():
    sends = [_send(1, 0), _send(2, 5)]
    assert _follows(sends, [_msg(5, 3)]) == [(1, "a", 3 * _S, "follow")]
    assert _follows(sends, [_msg(5, 7)]) == [(2, "a", 7 * _S, "follow")]


def test_every_message_counts_for_one_send_and_each_is_its_own_evidence():
    out = _follows([_send(1, 0)], [_msg(5, 10), _msg(6, 20, person="b")])
    assert out == [(1, "a", 10 * _S, "follow"), (1, "b", 20 * _S, "follow")]


# -- healthy sources ---------------------------------------------------


def _poll(at, outcome="ok", scope="c1"):
    return HealthRow(source="presence", scope=scope, event="poll", outcome=outcome, at_ms=at)


def test_polls_are_healthy_only_when_regular_and_all_succeeded():
    span = (_t(22, 0), _t(22, 1))
    regular = [_poll(_t(21, 23.9) + i * 5 * _MIN) for i in range(16)]
    assert polls_healthy(regular, scope="c1", start_ms=span[0], end_ms=span[1],
                         max_gap_ms=15 * _MIN)
    failed = [*regular[:5], _poll(regular[5].at_ms, "challenge"), *regular[6:]]
    assert not polls_healthy(failed, scope="c1", start_ms=span[0], end_ms=span[1],
                             max_gap_ms=15 * _MIN)
    gap = [p for p in regular if not (_t(22, 0.2) < p.at_ms < _t(22, 0.6))]
    assert not polls_healthy(gap, scope="c1", start_ms=span[0], end_ms=span[1],
                             max_gap_ms=15 * _MIN)
    assert not polls_healthy([], scope="c1", start_ms=span[0], end_ms=span[1],
                             max_gap_ms=15 * _MIN)
    other_circle = [_poll(p.at_ms, scope="c2") for p in regular]
    assert not polls_healthy(other_circle, scope="c1", start_ms=span[0], end_ms=span[1],
                             max_gap_ms=15 * _MIN)


def test_an_adapter_is_up_for_a_span_only_from_a_recorded_up_state():
    def row(at, state):
        return HealthRow(source="signal", scope="", event="transition", outcome=state, at_ms=at)

    rows = [row(_t(21, 0), "up"), row(_t(22, 5), "stale"), row(_t(22, 6), "up")]
    assert adapter_up(rows, platform="signal", start_ms=_t(21, 1), end_ms=_t(22, 4))
    assert not adapter_up(rows, platform="signal", start_ms=_t(22, 4), end_ms=_t(22, 7))
    assert not adapter_up([], platform="signal", start_ms=_t(22, 4), end_ms=_t(22, 7))


# -- citation by overlap ----------------------------------------------------


def test_a_citation_resolves_to_the_rows_overlapping_its_interval():
    rows = [(1, 100, 200), (2, 300, 400)]
    assert resolve_by_overlap(Citation("stay", "a", 150, 160), rows).row_ids == (1,)
    assert resolve_by_overlap(Citation("stay", "a", 180, 320), rows).row_ids == (1, 2)
    assert resolve_by_overlap(Citation("stay", "a", 250, 250), rows).unsupported
