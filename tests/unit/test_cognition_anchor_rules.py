"""GENERATED messages; no private corpus or model calls."""
import pytest

from scripts.find_cognition_anchors import Moment, candidates

DAY = 86_400_000


def moments(*turns):
    return [Moment("generated-room", at, sender, body) for at, sender, body in turns]


def has_scenario(rows, scenario):
    return any(r["scenario"] == scenario and r["before_ms"] == rows[-1].at_ms + 1
               for r in candidates(rows, {"generated-room": "room_generated"}))


@pytest.mark.parametrize(("scenario", "positive", "negative"), [
    (1, [(1, "a", "I'm downstairs")], [(1, "a", "arrivederci")]),
    (2, [(1, "a", "clouds today"), (2, "b", "k")],
        [(1, "a", "dinner tomorrow"), (2, "b", "k")]),
    (3, [(1, "a", "dinner at 7?"), (2, "b", "ok")],
        [(1, "a", "dinner at 7?"), (2, "a", "ok")]),
    (4, [(1, "a", "Saturday dinner at 7?"), (2, "b", "Saturday's off, sorry")],
        [(1, "a", "Monday lunch?"), (2, "b", "cancel Saturday")]),
    (5, [(1, "a", "downstairs"), (DAY + 1, "a", "arrived"),
         (2 * DAY + 1, "a", "到咗")],
        [(1, "a", "downstairs"), (2, "a", "arrived"), (3, "a", "樓下")]),
    (6, [(1, "a", "bought concert tickets")], [(1, "a", "ticket prices look steep")]),
    (11, [(1, "a", "Friday dinner at 7?"), (2, "b", "Friday dinner or lunch?"),
          (3, "a", "Friday dinner at 8?")],
         [(1, "a", "why?"), (2, "b", "how?"), (3, "a", "Friday dinner at 8?")]),
    (12, [(1, "a", "racing again"), (DAY + 1, "a", 'racing: I like "Generated Racer"')],
         [(1, "a", "racing again"), (2, "a", "racing again"),
          (3, "a", 'racing: I like "Generated Racer"')]),
    (13, [(1, "a", "hello"), (2, "c", "welcome"),
          (3, "b", "what does Generated Guest like?")],
         [(1, "a", "hello"), (2, "c", "welcome"), (3, "b", "do you like rain?")]),
])
def test_scenario_preconditions(scenario, positive, negative):
    assert has_scenario(moments(*positive), scenario)
    assert not has_scenario(moments(*negative), scenario)


def test_purchase_followed_by_attendance_is_not_a_ticket_only_anchor():
    rows = moments((1, "a", "bought concert tickets"), (2, "a", "went to the concert"))
    assert not any(r["scenario"] == 6 for r in candidates(rows, {"generated-room": "room_g"}))
    assert has_scenario(rows[:1], 6)


def test_ack_requires_a_recent_plan_from_another_speaker():
    rows = moments((1, "a", "dinner at 7?"), (900_002, "b", "k"))
    assert not has_scenario(rows, 3)
    assert has_scenario(rows, 2)


def test_cap_prefers_strong_history_and_is_deterministic():
    rows = moments(*[(i * DAY + 1, "a", "downstairs") for i in range(30)])
    result = candidates(rows, {"generated-room": "room_g"})
    visits = [r for r in result if r["scenario"] == 5]
    assert len(visits) == 20
    assert visits[0]["before_ms"] == 29 * DAY + 2
    assert visits[-1]["before_ms"] == 10 * DAY + 2
    assert candidates(reversed(rows), {"generated-room": "room_g"}) == result
    assert not {8, 10} & {r["scenario"] for r in result}


def test_window_includes_moment_and_nora_and_excludes_lower_boundary():
    rows = moments((1, "a", "dinner at 7?"),
                   *[(i, "a", "ambient") for i in range(2, 31)], (31, "b", "k"))
    assert has_scenario(rows, 2)  # question is outside the last 30, including k
    rows = [Moment("generated-room", 1, "nora", "dinner at 7?", True),
            Moment("generated-room", 2, "b", "k")]
    assert has_scenario(rows, 3)
    assert not has_scenario(rows, 2)
    rows = moments((1, "a", "dinner at 7?"), (DAY + 1, "b", "k"))
    result = candidates(rows, {"generated-room": "room_g"})
    assert result[-1]["counts"] == {"prior": 0, "questions": 0, "same_sender": 0}


def test_alias_validation_and_timestamp_ties():
    rows = [Moment("generated-room", 1, "a", "k", id=1),
            Moment("generated-room", 1, "b", "dinner at 7?", id=2)]
    assert candidates(rows, {"generated-room": "private-room"}) == []
    assert not has_scenario(rows, 2)
