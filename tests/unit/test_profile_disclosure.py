"""A Profile line's `exclude_persons` is a disclosure boundary, held by
`nora.agent.lineage.excluded` on every read surface the prompt switch added —
the tag assembler (`TranscriptInjection._profiles`), the fire path
(`FirePath._read_profiles`) and the `character_read` tool.

`excluded` denies MORE than a listed-members intersection: a `*` room lists
nobody and still denies, a person present only in the live roster (an
unmapped-then-mapped account, a member the policy predates) counts, and a
roster that is unreadable, degraded or holds an unnamed account denies every
excluded line. These tests pin all four on each surface, with the listed-room
clean-roster case as the positive control.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.app.fire import FirePath
from nora.domain.events import Event
from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.profile import ProfileLine
from nora.engagement.context import TranscriptInjection
from nora.infra.transit import TransitClient
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
from tests.fakes import SinkSender

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

# Ada and Ben are listed in the room; Cy is not, but her account is in the
# LIVE roster (she joined after the policy was written — the listed-only
# intersection this file exists to remove never sees her).
_PERSONS = Persons(
    by_person={
        "ada": PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada"),
        "ben": PersonRef(person_id="ben", name="Ben", honcho_peer_id="ben"),
        "cy": PersonRef(person_id="cy", name="Cy", honcho_peer_id="cy"),
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


def _assembler(
    convo: Conversation, reader: _Heads, roster: FakeRosterSource,
) -> TranscriptInjection:
    return TranscriptInjection(
        audit=FakeRecordStore(), memory=FakeMemoryStore(), persons=_PERSONS,
        context_window=10, metrics=Metrics(), policy=_policy(convo),
        profile=reader, rosters={"signal": roster},
        lineage=LineagePolicy.from_policy(_policy(convo)),
        self_ids=frozenset({"+852999"}),
    )


def _msg() -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+85200000001", sender_name="Ada", timestamp=1,
        body="nora.000 hi", raw={},
    )


async def _unit_view(assembler: TranscriptInjection):
    views = await assembler._profiles(_msg(), _PERSONS.by_person["ada"], [])
    return next((v for v in views if v.entity == "unit"), None)


async def test_a_wildcard_room_is_never_shown_an_excluded_line() -> None:
    """The reviewer's repro: a `*` room lists nobody, so a listed-members
    intersection is empty and the excluded line leaks. `excluded` denies."""
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    view = await _unit_view(_assembler(_WILDCARD, reader, _ROSTER_CLEAN))
    assert view is not None and view.lines == ()


async def test_a_member_present_only_in_the_live_roster_still_excludes() -> None:
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    view = await _unit_view(_assembler(_LISTED, reader, _ROSTER_WITH_CY))
    assert view is not None and view.lines == ()


async def test_an_unreadable_roster_withholds_every_excluded_line() -> None:
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    view = await _unit_view(_assembler(_LISTED, reader, _ROSTER_DOWN))
    assert view is not None and view.lines == ()


async def test_a_clean_roster_shows_a_line_excluding_someone_absent() -> None:
    """The positive control: a listed room whose live roster holds neither the
    excluded person nor an unknown account reads the line."""
    reader = _Heads({"u1#unit": (
        _line("surprise for Cy", exclude=("cy",)),
        _line("the group decides late"),
    )})
    view = await _unit_view(_assembler(_LISTED, reader, _ROSTER_CLEAN))
    assert view is not None
    assert [line.text for line in view.lines] == [
        "surprise for Cy", "the group decides late",
    ]


async def test_a_listed_member_named_by_a_line_still_excludes_it() -> None:
    """Listed-member denial is the pre-existing case `excluded` subsumes."""
    reader = _Heads({"u1#unit": (
        _line("surprise for Ada", exclude=("ada",)),
        _line("the group decides late"),
    )})
    view = await _unit_view(_assembler(_LISTED, reader, _ROSTER_CLEAN))
    assert view is not None
    assert [line.text for line in view.lines] == ["the group decides late"]


async def test_no_roster_is_read_when_no_line_names_an_exclusion() -> None:
    """The daemon round trip stays out of a tag turn that needs no exclusion
    check (the pipeline's `Room` laziness, kept)."""
    roster = FakeRosterSource(
        groups={"g1": (frozenset({"+85200000001"}), frozenset({"+85200000002"}))},
    )
    reader = _Heads({"u1#unit": (_line("the group decides late"),)})
    view = await _unit_view(_assembler(_LISTED, reader, roster))
    assert view is not None and len(view.lines) == 1
    assert roster.calls == []


def _fire_path(convo: Conversation, reader: _Heads, roster: FakeRosterSource) -> FirePath:
    return FirePath(
        persons=_PERSONS, records=FakeRecordStore(), memory=FakeMemoryStore(),
        doctrine_store=None, metrics=Metrics(),  # type: ignore[arg-type]
        runner=None, adapters={}, registry=None,  # type: ignore[arg-type]
        canary="test-canary", display_name="Nora",
        ai_peer="nora", context_window=40, limits={},
        timeout_seconds=120.0, clock=lambda: NOW,
        policy=_policy(convo), profile=reader, rosters={"signal": roster},
        lineage=LineagePolicy.from_policy(_policy(convo)),
    )


def _occasion() -> Event:
    return Event(
        kind="proactive", platform="signal", conversation_id="g1",
        conversation_type="group", person_id=None, body="",
        header=lambda _ref, _now: "",
    )


async def test_the_fire_path_holds_the_same_boundary() -> None:
    """Wildcard room: the fire path's reader leaks identically without the
    predicate, and `excluded` denies identically with it."""
    reader = _Heads({"u1#unit": (_line("surprise for Cy", exclude=("cy",)),)})
    path = _fire_path(_WILDCARD, reader, _ROSTER_CLEAN)
    views = await path._read_profiles(_occasion(), None, [])
    unit = next((v for v in views if v.entity == "unit"), None)
    assert unit is not None and unit.lines == ()


@pytest.mark.parametrize("surface", ["tag", "fire", "tool"])
@pytest.mark.parametrize("case", ["wildcard", "live-only", "unavailable", "unmapped", "clean"])
async def test_each_profile_surface_checks_the_actual_room(surface, case):
    convo = _WILDCARD if case == "wildcard" else _LISTED
    roster = {
        "wildcard": _ROSTER_CLEAN, "live-only": _ROSTER_WITH_CY,
        "unavailable": _ROSTER_DOWN, "clean": _ROSTER_CLEAN,
        "unmapped": FakeRosterSource(groups={"g1": (frozenset({"unknown"}),)}),
    }[case]
    lines = (_line("surprise for Cy", exclude=("cy",)), _line("public observation"))
    reader = _Heads({"g1": lines, "u1#unit": lines})
    if surface == "tag":
        view = await _unit_view(_assembler(convo, reader, roster))
        assert view is not None
        text = " ".join(line.text for line in view.lines)
    elif surface == "fire":
        views = await _fire_path(convo, reader, roster)._read_profiles(_occasion(), None, [])
        text = " ".join(line.text for v in views if v.entity == "unit" for line in v.lines)
    else:
        tools = build_tools(
            store=FakeRecordStore(), conversation_id="g1", conversation_type="group",
            persons=_PERSONS, honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=Room(platform="signal", conversation_id="g1",
                roster_source=roster, persons=_PERSONS),
            transit_source=TransitClient(kmb_base_url="http://127.0.0.1:1",
                mtr_schedule_url="http://127.0.0.1:1/s", mtr_routes_url="http://127.0.0.1:1/r",
                timeout_seconds=0.5),
            clock=lambda: NOW, profile_read=reader,
            lineage=LineagePolicy.from_policy(_policy(convo)),
        )
        tool = next(t for t in tools if t.tool_name == "character_read")
        result = await tool._tool_func(entity="room", subject="Room")
        assert result["status"] == "ok"
        text = str(result["accounts"])
    assert "public observation" in text
    assert ("surprise for Cy" in text) == (case == "clean")


async def test_unit_self_reader_cannot_clear_exclusions_without_live_rosters():
    from nora.app.cognition_grounding import unit_self_profile_reader
    from nora.domain.culture import nora_character_key

    reader = _Heads({nora_character_key("u1"): (
        _line("surprise for Cy", exclude=("cy",)), _line("public observation"),
    )})
    read = unit_self_profile_reader(reader, _policy(_LISTED))
    assert read is not None
    pending = read("u1")
    assert pending is not None
    assert [line.text for line in await pending] == ["public observation"]


@pytest.mark.parametrize("locations,allowed", [((False, False), False),
    ((True, False), False), ((True, True), True)])
async def test_cognition_self_location_passes_presence_in_every_target_room(locations, allowed):
    from dataclasses import replace

    from nora.agent.whereis import Circle
    from nora.app.cognition_grounding import unit_self_profile_reader
    from nora.domain.culture import nora_character_key
    from nora.domain.profile import ProfileView
    from nora.language.profile_render import render_profile

    rooms = tuple(replace(_LISTED, ids=frozenset({cid}), primary_id=cid, location=location)
                  for cid, location in zip(("g1", "g2"), locations, strict=True))
    policy = Policy(conversations=rooms,
        _by_id={(c.platform, c.primary_id): c for c in rooms})
    private = replace(_line("visits Lotus Library"), location=True,
                      strength={"location_subjects": ["ada"]})
    unknown = replace(private, strength={})
    public = _line("public observation")
    reader = _Heads({nora_character_key("u1"): (private, unknown, public)})
    roster = FakeRosterSource(groups=dict.fromkeys(("g1", "g2"), _ROSTER_CLEAN.groups["g1"]))
    def room_for(platform, conversation_id):
        return Room(platform=platform, conversation_id=conversation_id,
                    roster_source=roster, persons=_PERSONS)
    read = unit_self_profile_reader(reader, policy,
        lineage=LineagePolicy.from_policy(policy, circles=(Circle(
            circle_id="fictional", person_ids=frozenset({"ada", "ben"})),)),
        room_for=room_for)
    pending = read("u1")
    if len(set(locations)) > 1:
        assert pending is None
        return
    assert pending is not None
    lines = await pending
    assert lines == ((private, public) if allowed else (public,))
    block = render_profile(ProfileView(entity="nora", of="nora", lines=lines),
                           name_of=lambda pid: pid, now=NOW)
    assert block is not None
    assert ("Lotus Library" in str(block)) == allowed


@pytest.mark.parametrize("surface", ["tag", "fire"])
async def test_a_room_profile_names_the_room_by_its_policy_label_never_its_platform_id(surface):
    """ADR 0029: a room is its `policy.toml` id in the prompt. `primary_id`
    keys the store and is often a platform conversation id, which must not
    reach `of=`."""
    reader = _Heads({"g1": (_line("plans late"),)})
    if surface == "tag":
        views = await _assembler(_LISTED, reader, _ROSTER_CLEAN)._profiles(
            _msg(), _PERSONS.by_person["ada"], [])
    else:
        views = await _fire_path(_LISTED, reader, _ROSTER_CLEAN)._read_profiles(
            _occasion(), None, [])
    room = next(v for v in views if v.entity == "room")
    assert room.of == "Room"
    assert room.of != _LISTED.primary_id
