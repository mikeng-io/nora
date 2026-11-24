"""A Profile line's `exclude_persons` is a disclosure boundary, held by
`nora.agent.lineage.profile_lines_for` for typed Profile readers.

`excluded` denies MORE than a listed-members intersection: a `*` room lists
nobody and still denies, a person present only in the live roster (an
unmapped-then-mapped account, a member the policy predates) counts, and a
roster that is unreadable, degraded or holds an unnamed account denies every
excluded line. These tests pin all four, with the listed-room clean-roster case
as the positive control.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.domain.models import PersonRef
from nora.domain.profile import ProfileLine
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeRosterSource

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

# Ada and Ben are listed in the room; Cy is not, but her account is in the
# LIVE roster (she joined after the policy was written — the listed-only
# intersection this file exists to remove never sees her).
_PERSONS = Persons(
    by_person={
        "ada": PersonRef(person_id="ada", name="Ada"),
        "ben": PersonRef(person_id="ben", name="Ben"),
        "cy": PersonRef(person_id="cy", name="Cy"),
    },
    index={
        ("signal", "+85200000001"): "ada",
        ("signal", "+85200000002"): "ben",
        ("signal", "+85200000003"): "cy",
    },
)

_LISTED = Conversation(
    platform="signal", ids=frozenset({"g1"}), persons=frozenset({"ada", "ben"}),
    primary_id="g1", unit="u1", label="Room",
)
_WILDCARD = Conversation(
    platform="signal", ids=frozenset({"g1"}), persons=None,
    primary_id="g1", unit="u1", label="Room",
)
_ROSTER_WITH_CY = FakeRosterSource(
    groups={"g1": (frozenset({"+85200000001"}), frozenset({"+85200000002"}),
                   frozenset({"+85200000003"}))},
)
_ROSTER_CLEAN = FakeRosterSource(
    groups={"g1": (frozenset({"+85200000001"}), frozenset({"+85200000002"}))},
)
_ROSTER_DOWN = FakeRosterSource(fail=True)


def _policy(convo: Conversation) -> Policy:
    return Policy(conversations=(convo,), _by_id={("signal", "g1"): convo})


def _line(text: str, *, exclude: tuple[str, ...] = (), key: str = "u1#unit") -> ProfileLine:
    return ProfileLine(
        id="l1", profile_key=key, part="traits", kind="topic",
        text=text, mode="induced", content={"statement": text, "context": "unknown",
            "interest": "books", "depth": "unknown", "stance": "unknown"},
        citations=(), state="current", unit="u1",
        written_at=1, written_by_session="s", exclude_persons=exclude,
    )


class _Heads:
    """`ProfileHeads`: the one read, keyed exactly as the store is."""

    def __init__(self, by_key: dict[str, tuple[ProfileLine, ...]]) -> None:
        self._by_key = by_key

    async def account_heads(self, *, key: str) -> tuple[ProfileLine, ...]:
        return self._by_key.get(key, ())


async def _unit_lines(convo, reader, roster):
    from nora.agent.lineage import profile_lines_for

    room = Room(platform="signal", conversation_id="g1", roster_source=roster, persons=_PERSONS)
    target = await LineagePolicy.from_policy(_policy(convo)).read_room(
        platform="signal", conversation_id="g1",
    )
    return await profile_lines_for(await reader.account_heads(key="u1#unit"), target, room)


async def test_a_wildcard_room_is_never_shown_an_excluded_line() -> None:
    """The reviewer's repro: a `*` room lists nobody, so a listed-members
    intersection is empty and the excluded line leaks. `excluded` denies."""
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    lines = await _unit_lines(_WILDCARD, reader, _ROSTER_CLEAN)
    assert lines == ()


async def test_a_member_present_only_in_the_live_roster_still_excludes() -> None:
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    lines = await _unit_lines(_LISTED, reader, _ROSTER_WITH_CY)
    assert lines == ()


async def test_an_unreadable_roster_withholds_every_excluded_line() -> None:
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    lines = await _unit_lines(_LISTED, reader, _ROSTER_DOWN)
    assert lines == ()


async def test_a_clean_roster_shows_a_line_excluding_someone_absent() -> None:
    """The positive control: a listed room whose live roster holds neither the
    excluded person nor an unknown account reads the line."""
    reader = _Heads({"u1#unit": (
        _line("surprise for Cy", exclude=("cy",)),
        _line("the group decides late"),
    )})
    lines = await _unit_lines(_LISTED, reader, _ROSTER_CLEAN)
    assert [line.text for line in lines] == [
        "surprise for Cy", "the group decides late",
    ]


async def test_a_listed_member_named_by_a_line_still_excludes_it() -> None:
    """Listed-member denial is the pre-existing case `excluded` subsumes."""
    reader = _Heads({"u1#unit": (
        _line("surprise for Ada", exclude=("ada",)),
        _line("the group decides late"),
    )})
    lines = await _unit_lines(_LISTED, reader, _ROSTER_CLEAN)
    assert [line.text for line in lines] == ["the group decides late"]


async def test_no_roster_is_read_when_no_line_names_an_exclusion() -> None:
    """The daemon round trip stays out of a read that needs no exclusion
    check (the `Room` laziness, kept)."""
    roster = FakeRosterSource(
        groups={"g1": (frozenset({"+85200000001"}), frozenset({"+85200000002"}))},
    )
    reader = _Heads({"u1#unit": (_line("the group decides late"),)})
    lines = await _unit_lines(_LISTED, reader, roster)
    assert len(lines) == 1
    assert roster.calls == []
