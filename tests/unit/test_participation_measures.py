"""The four measures.

Written negative-first. Each measure's value is that it can FAIL to fire —
a measure that fires on everything is the `no_go` problem one layer over, where
a verdict and health are the same reading.
"""

from __future__ import annotations

from dataclasses import dataclass

from nora.domain.participation_measures import (
    NEAR_MS,
    Refusal,
    Spoke,
    Tag,
    expiry_without_action,
    holding_refusals,
    ignored,
    overrides,
)

#: Epoch MILLISECONDS — the unit every instant in these measures uses.
T0 = 1_757_000_000_000
#: A `YYYYMMDD` calendar day whose last instant is before T0.
DAY_BEFORE_T0 = 20250901


@dataclass(frozen=True)
class _Note:
    id: str
    established_in: str = "discord/g1"
    created_at: int = T0
    anchor_date: int | None = None
    retired_at: int | None = None


@dataclass(frozen=True)
class _Decision:
    note_id: str | None
    verdict: str


@dataclass(frozen=True)
class _Msg:
    id: str
    conversation_id: str = "discord/g1"
    ts: int = T0
    quoted_ts: int | None = None


# --- expiry without action -------------------------------------------------


def test_an_open_note_whose_anchor_passed_unremarked_is_a_hit():
    m = expiry_without_action([_Note("n1", anchor_date=DAY_BEFORE_T0)], [], now=T0)
    assert [o.subject for o in m.hits] == ["n1"]
    assert m.considered == 1 and m.rate == 1.0


def test_a_note_she_reminded_on_is_not_a_hit():
    """The measure grades the DECISION, so a `remind` clears it and a
    `decline` — the decision being graded — does not."""
    notes = [_Note("n1", anchor_date=DAY_BEFORE_T0)]
    assert expiry_without_action(notes, [_Decision("n1", "remind")], now=T0).hits == ()
    assert len(expiry_without_action(notes, [_Decision("n1", "decline")], now=T0).hits) == 1


def test_a_retired_note_and_a_future_anchor_are_not_hits():
    assert expiry_without_action(
        [_Note("n1", anchor_date=DAY_BEFORE_T0, retired_at=T0 - 5)], [], now=T0,
    ).hits == ()
    assert expiry_without_action([_Note("n2", anchor_date=20991231)], [], now=T0).hits == ()


def test_a_dateless_note_is_excluded_from_the_DENOMINATOR_not_counted_as_clean():
    """A living thread has no anchor and can never expire. Counting it as a
    pass would dilute the rate with rows that cannot fire — the same error as
    reading an absent series as a measured zero."""
    m = expiry_without_action([_Note("n1"), _Note("n2", anchor_date=DAY_BEFORE_T0)], [], now=T0)
    assert m.considered == 1


# --- holding refusals ------------------------------------------------------


def test_a_refusal_while_an_open_note_lived_in_that_room_is_a_hit():
    m = holding_refusals(
        [Refusal("discord/g1", T0 + 100)], [_Note("n1", created_at=T0)],
    )
    assert [o.subject for o in m.hits] == ["n1"]


def test_a_refusal_in_a_room_holding_nothing_is_not_a_hit():
    """Born elsewhere, and born LATER, are both misses — the second is the one
    a naive join gets wrong, because the note exists by the time anyone looks."""
    notes = [_Note("n1", established_in="signal/g9", created_at=T0)]
    assert holding_refusals([Refusal("discord/g1", T0 + 100)], notes).hits == ()
    assert holding_refusals(
        [Refusal("discord/g1", T0)], [_Note("n2", created_at=T0 + 500)],
    ).hits == ()


def test_a_refusal_after_the_note_retired_is_not_a_hit():
    assert holding_refusals(
        [Refusal("discord/g1", T0 + 900)],
        [_Note("n1", created_at=T0, retired_at=T0 + 100)],
    ).hits == ()


def test_the_ceiling_names_the_standing_arm_and_the_counter():
    """The standing-arm caveat and the counter's lossiness both change how the
    number reads, so neither may travel only in a docstring."""
    m = holding_refusals([Refusal("discord/g1", T0, provenance="counter")], [])
    assert "#222" in m.ceiling and "COUNTER" in m.ceiling
    row = holding_refusals([Refusal("discord/g1", T0, provenance="row")], [])
    assert "COUNTER" not in row.ceiling


# --- overrides -------------------------------------------------------------


def test_a_tag_shortly_after_a_refusal_overrules_it():
    """The founding anecdote: a no_go, 34 seconds later the member gives up
    and tags, then a correct answer."""
    m = overrides([Refusal("discord/g1", T0)], [Tag("discord/g1", T0 + 34)])
    assert len(m.hits) == 1 and m.considered == 1


def test_a_tag_before_the_refusal_or_too_late_or_elsewhere_does_not():
    r = [Refusal("discord/g1", T0)]
    assert overrides(r, [Tag("discord/g1", T0 - 1)]).hits == ()
    assert overrides(r, [Tag("discord/g1", T0 + NEAR_MS + 1)]).hits == ()
    assert overrides(r, [Tag("signal/g9", T0 + 34)]).hits == ()


def test_one_refusal_is_overruled_once_however_much_traffic_follows():
    """«Was this refusal overruled» — not «how busy was the room after it»."""
    m = overrides(
        [Refusal("discord/g1", T0)],
        [Tag("discord/g1", T0 + 10), Tag("discord/g1", T0 + 20)],
    )
    assert len(m.hits) == 1


# --- ignored ---------------------------------------------------------------


def test_she_spoke_and_the_next_human_line_did_not_engage():
    m = ignored([Spoke("discord/g1", T0)], [_Msg("m1", ts=T0 + 30)])
    assert [o.subject for o in m.hits] == ["m1"]


def test_a_quote_of_her_turn_is_engagement():
    assert ignored(
        [Spoke("discord/g1", T0)], [_Msg("m1", ts=T0 + 30, quoted_ts=T0)],
    ).hits == ()


def test_a_quote_of_something_else_is_not_engagement():
    """The join is exact on purpose: quoting an earlier line is the room
    carrying on without her."""
    assert len(ignored(
        [Spoke("discord/g1", T0)], [_Msg("m1", ts=T0 + 30, quoted_ts=T0 - 900)],
    ).hits) == 1


def test_a_tag_in_the_window_is_engagement():
    assert ignored(
        [Spoke("discord/g1", T0)], [_Msg("m1", ts=T0 + 30)],
        tags=[Tag("discord/g1", T0 + 40)],
    ).hits == ()


def test_a_quiet_room_is_excluded_rather_than_scored_as_ignoring_her():
    """No human line inside the window is evidence of bedtime, not rejection.
    `considered == 0` and `rate is None` — never 0.0, which would read as
    «measured, and it never happens»."""
    m = ignored([Spoke("discord/g1", T0)], [_Msg("m1", ts=T0 + NEAR_MS + 1)])
    assert m.considered == 0 and m.hits == () and m.rate is None


def test_only_the_NEXT_human_line_is_judged():
    """Engagement three messages later is the room coming back to her, which
    is a different thing and not this measure's."""
    m = ignored(
        [Spoke("discord/g1", T0)],
        [_Msg("m1", ts=T0 + 10), _Msg("m2", ts=T0 + 20, quoted_ts=T0)],
    )
    assert [o.subject for o in m.hits] == ["m1"]


# --- the one join that can be wrong and still look plausible ---------------


def test_pair_spoke_carries_the_MESSAGE_ts_not_the_decisions():
    """`ignored` matches a reply's `quoted_ts` against `Spoke.at` exactly.
    Handed the decision's ts it would match nothing and report every turn as
    ignored — a number that looks like a finding."""
    from nora.app.participation_measures import pair_spoke

    (s,) = pair_spoke([("discord/g1", T0 + 3)], [("discord/g1", T0)])
    assert s.at == T0


def test_pair_spoke_never_looks_FORWARD_from_the_decision():
    """`fire.py` records the outcome AFTER the send, so the message is always
    earlier. A symmetric window would let a LATER turn's message be claimed by
    an earlier decision."""
    from nora.app.participation_measures import pair_spoke

    assert pair_spoke([("discord/g1", T0)], [("discord/g1", T0 + 5)]) == ()


def test_pair_spoke_drops_a_decision_it_cannot_pair_rather_than_inventing_one():
    """Losing a turn from the denominator is the safe direction; a fabricated
    pairing would put a real turn in the numerator of a measure about being
    ignored."""
    from nora.app.participation_measures import (
        SPOKE_JOIN_TOLERANCE_MS,
        pair_spoke,
    )

    far = T0 - SPOKE_JOIN_TOLERANCE_MS - 1
    assert pair_spoke([("discord/g1", T0)], [("discord/g1", far)]) == ()
    assert pair_spoke([("discord/g1", T0)], [("signal/g9", T0)]) == ()


def test_pair_spoke_takes_the_nearest_earlier_message():
    from nora.app.participation_measures import pair_spoke

    (s,) = pair_spoke([("discord/g1", T0)], [("discord/g1", T0 - 90), ("discord/g1", T0 - 2)])
    assert s.at == T0 - 2


# --- the refusal file must not be allowed to look clean by being short -----


def test_a_short_refusal_file_is_REFUSED_not_silently_truncated(tmp_path):
    """A refusal list that stops short makes both refusal-side measures look
    clean — and «clean» is what they would also say if the gate had refused
    nothing. Those two readings must never be confusable; that confusion is
    the whole reason these measures exist."""
    import json

    import pytest

    from nora.app.cli import CliError, _load_refusals

    f = tmp_path / "r.json"
    f.write_text(json.dumps({"covers_from": T0, "refusals": []}))
    with pytest.raises(CliError, match="only covers from"):
        _load_refusals(f, since=T0 - 86400)
    # Wide enough is accepted.
    rows, provenance = _load_refusals(f, since=T0 + 10)
    assert rows == () and provenance == "counter"


def test_a_malformed_refusal_file_is_a_sentence_not_a_traceback(tmp_path):
    import pytest

    from nora.app.cli import CliError, _load_refusals

    f = tmp_path / "r.json"
    f.write_text("{not json")
    with pytest.raises(CliError, match="could not read refusals"):
        _load_refusals(f, since=T0)


# --- the ceiling travels with the number ----------------------------------


def test_every_measure_prints_its_own_ceiling_under_its_own_number():
    """A footnote shared between four measures blind to different things is
    read as applying to none."""
    from nora.app import report

    ms = [
        expiry_without_action([_Note("n1", anchor_date=DAY_BEFORE_T0)], [], now=T0),
        holding_refusals([Refusal("discord/g1", T0)], []),
        overrides([Refusal("discord/g1", T0)], []),
        ignored([], []),
    ]
    lines = report.render_measures(
        ms, source="db", window="last 7d", refusals=1, refusal_source="counter",
    )
    body = "\n".join(lines)
    assert body.count("ceiling:") == 4
    for m in ms:
        assert m.name in body


def test_nothing_considered_renders_as_n_a_never_as_zero_percent():
    """An absent denominator read as a measured zero is the exact error
    these measures exist to forbid, one layer up."""
    from nora.app import report

    lines = report.render_measures(
        [ignored([], [])], source="db", window="last 7d",
        refusals=0, refusal_source="not supplied",
    )
    body = "\n".join(lines)
    assert "n/a (nothing considered)" in body and "0.0%" not in body


# --- units: calendar days against epoch ms ------------------------------------


def test_anchor_date_is_a_CALENDAR_DAY_and_is_not_compared_against_epoch_ms():
    """`note.anchor_date` is `YYYYMMDD`; every other instant here is epoch ms.

    Compared raw, `20260914 > 1_789_...` is false for EVERY note, so every
    anchored note would be classified as expired, a tidy «notes expired
    unremarked» count that is pure unit error.
    """
    future = _Note("n1", anchor_date=20991231)
    assert expiry_without_action([future], [], now=T0).considered == 0
    past = _Note("n2", anchor_date=20250901)
    assert expiry_without_action([past], [], now=T0).considered == 1


def test_a_note_anchored_TODAY_has_not_passed_until_the_day_is_over():
    """An arrangement for today is not missed at 00:01. The measure fires on
    the LAST instant of the anchor day, not the first."""
    from nora.domain.notes import anchor_bounds_ms

    bounds = anchor_bounds_ms(20250901)
    assert bounds is not None
    start, end = bounds
    n = [_Note("n1", anchor_date=20250901)]
    assert expiry_without_action(n, [], now=start).considered == 0
    assert expiry_without_action(n, [], now=end).considered == 1


def test_an_unreadable_anchor_is_reported_in_the_ceiling_not_skipped_silently():
    """A note whose anchor can never fire looks identical to one that has not
    expired yet — unless the count is said out loud."""
    m = expiry_without_action([_Note("n1", anchor_date=999)], [], now=T0)
    assert m.considered == 0
    assert "cannot read as a date" in m.ceiling


def test_the_near_window_is_milliseconds_not_seconds():
    """A seconds-valued window against ms timestamps is 600ms wide — it would
    match almost nothing while looking like a working measure."""
    assert NEAR_MS == 600_000
    assert overrides([Refusal("discord/g1", T0)], [Tag("discord/g1", T0 + 599_000)]).hits != ()
    assert overrides([Refusal("discord/g1", T0)], [Tag("discord/g1", T0 + 601_000)]).hits == ()
