import pytest

from nora.domain.culture import RoomMessage, character_key
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.profile import ProfileDispute, ProfileLine
from nora.persons import Persons
from nora.web.memory import build_memory_payload

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


_NOW = 1_790_000_000_000


class _Profile:
    """The portal's two reads, answered from dicts keyed by account key."""

    def __init__(self, heads=None, disputes=None):
        self._heads = heads or {}
        self._disputes = disputes or {}
        self.reads = []

    async def account_heads(self, *, key):
        self.reads.append(key)
        return self._heads.get(key, ())

    async def open_disputes(self, *, key, limit):
        return self._disputes.get(key, ())[:limit]


class _Cited:
    def __init__(self, rows):
        self._rows = rows

    async def rows_by_id(self, *, ids):
        return [r for r in self._rows if r.row.id in ids]


def _row(row_id: int, body: str, room: str, person_id: str | None = "miles") -> RoomMessage:
    return RoomMessage(room=room, row=TranscriptRow(
        id=row_id, sender_id="sig-1", sender_name=None, ts=1000, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_id=person_id,
    ))


def _line(line_id: str, text: str, **overrides) -> ProfileLine:
    defaults = {
        "id": line_id, "profile_key": character_key("friends", "miles"),
        "part": "facts", "kind": "attribute", "text": text, "mode": "told",
        "citations": ("7",), "state": "current",
        "unit": "friends", "written_at": _NOW, "written_by_session": "tag",
        "told_by": "miles",
    }
    return ProfileLine(**(defaults | overrides))


async def test_returns_only_the_callers_own_accounts_not_everyone_elses():
    """User-scoped: a caller resolved to `miles` must never see `ada`'s account
    (no "showcase everyone to everyone"), though the store holds hers."""
    miles_key, ada_key = character_key("friends", "miles"), character_key("friends", "ada")
    profile = _Profile(heads={
        miles_key: (_line("m1", "cycles to work"),),
        ada_key: (_line("a1", "plays guitar", profile_key=ada_key, told_by="ada"),),
    })

    payload = await build_memory_payload(
        persons=_persons(MILES, ADA), person_id="miles",
        profile=profile, profile_scopes_of=lambda pid: (("friends", "67"),),
    )

    [account] = payload
    assert [line["text"] for line in account["lines"]] == ["legacy untyped: cycles to work"]
    assert profile.reads == [miles_key]


@pytest.mark.parametrize("person_id", [None, "stranger"])
async def test_no_resolved_person_returns_empty_not_everyone(person_id):
    """Fail-closed (R-5's shape): no resolved identity must never fall back to
    showing every whitelisted person's data. The store holds an account for
    `miles` and is wired, so a leaking fallback would return it."""
    profile = _Profile(heads={character_key("friends", "miles"): (_line("l1", "cycles"),)})

    payload = await build_memory_payload(
        persons=_persons(MILES), person_id=person_id,
        profile=profile, profile_scopes_of=lambda pid: (("friends", "67"),),
    )

    assert payload == []
    assert profile.reads == []


async def test_without_a_profile_reader_the_payload_is_empty_and_the_reader_is_what_fills_it():
    """The payload carries nothing but Profile accounts, so with the switch off
    it is empty. The same call with the reader wired is not, which is what makes
    the empty list a measurement rather than an inert default."""
    heads = {character_key("friends", "miles"): (_line("l1", "cycles to work"),)}
    wired = {"persons": _persons(MILES), "person_id": "miles",
             "profile_scopes_of": lambda pid: (("friends", "67"),)}

    assert await build_memory_payload(**wired) == []
    assert len(await build_memory_payload(**wired, profile=_Profile(heads=heads))) == 1


async def test_one_account_per_unit_the_caller_is_listed_in_each_in_a_workspace_they_belong_to():
    """DEC-214: a person in units of two Nora workspaces has a separate account
    in each. They are shown apart, in unit order, and only a workspace the
    caller belongs to shows its account."""
    heads = {
        character_key("friends", "miles"): (_line("f1", "cycles to work"),),
        character_key("climbers", "miles"): (
            _line("c1", "lives near Sha Tin", unit="climbers",
                  profile_key=character_key("climbers", "miles")),),
    }
    scopes = lambda pid: (("climbers", "ridgeline"), ("friends", "67"))  # noqa: E731

    both = await build_memory_payload(
        persons=_persons(MILES), person_id="miles", profile=_Profile(heads=heads),
        profile_scopes_of=scopes, workspaces_of=lambda pid: ("67", "ridgeline"),
    )
    only_67 = await build_memory_payload(
        persons=_persons(MILES), person_id="miles", profile=_Profile(heads=heads),
        profile_scopes_of=scopes, workspaces_of=lambda pid: ("67",),
    )

    assert [a["unit"] for a in both] == ["climbers", "friends"]
    assert [a["unit"] for a in only_67] == ["friends"]


async def test_a_caller_in_no_workspace_sees_nothing_rather_than_a_guess():
    """A person no listed room names belongs to no workspace, so an account the
    store holds for them still shows nowhere."""
    profile = _Profile(heads={character_key("friends", "miles"): (_line("l1", "cycles"),)})
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles", profile=profile,
        profile_scopes_of=lambda pid: (("friends", "67"),),
        workspaces_of=lambda _pid: (),
    )
    assert payload == []


async def test_a_callers_own_profile_accounts_come_back_one_per_unit():
    """The switch's portal half (cognition-loop/data.md §4): the caller sees the
    account about them per unit that lists them, each in its
    own object — every chain head with its state, the evidence behind it, and the
    open disputes."""
    key = character_key("friends", "miles")
    profile = _Profile(
        heads={key: (
            _line("l1", "cycles to work"),
            _line("l2", "maybe vegetarian", part="guesses", kind="explanation",
                  mode="abduced", state="disputed", told_by=None,
                  citations=("response:9", "social:instagram:p1", "rollup:outing:friends|x"),
                  prediction="orders veg", prediction_observable="rollup:outing:friends|x",
                  prediction_window_end=_NOW + 1000, verdict="open"),
        )},
        disputes={key: (ProfileDispute(
            id="d1", profile_key=key, line_id="l2", disputed_by="miles",
            kind="dispute", claim="maybe vegetarian", message_id=7,
            unit="friends", written_at=_NOW,
        ),)},
    )
    cited = _Cited([_row(7, "i cycle to work", "g1"), _row(9, "her send", "g1", None)])
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        workspaces_of=lambda _pid: ("67",),
        profile=profile,
        profile_scopes_of=lambda _pid: (("friends", "67"),),
        cited=cited,
        rooms_of=lambda _pid: frozenset({("signal", "g1")}),
    )
    [account] = payload
    assert account["unit"] == "friends"
    told, hypothesis = account["lines"]
    assert (told["state"], told["mode"], told["told_by"]) == ("current", "told", "Miles")
    assert told["evidence"] == [
        {"ref": "7", "kind": "message", "text": "i cycle to work",
         "said_at": 1000, "by": "Miles"},
    ]
    assert hypothesis["state"] == "disputed"
    assert (hypothesis["prediction"], hypothesis["verdict"]) == ("orders veg", "open")
    assert hypothesis["evidence"] == [
        {"ref": "response:9", "kind": "episode", "text": "her send",
         "said_at": 1000, "by": None},
        {"ref": "social:instagram:p1", "kind": "social"},
        {"ref": "rollup:outing:friends|x", "kind": "rollup"},
    ]
    assert account["disputes"] == [
        {"kind": "dispute", "claim": "maybe vegetarian", "disputed_by": "Miles",
         "written_at": _NOW},
    ]


async def test_evidence_text_shows_only_where_the_viewer_is_still_listed():
    """The write checked the rooms of THAT day; the page checks TODAY's: a
    citation said in a room the viewer no longer lists renders as its bare
    ref, and so does a row the Record has hidden."""
    key = character_key("friends", "miles")
    profile = _Profile(heads={key: (_line("l1", "cycles to work"),)})
    cited = _Cited([_row(7, "i cycle to work", "g1")])
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        profile=profile, profile_scopes_of=lambda _pid: (("friends", "default"),),
        cited=cited,
        rooms_of=lambda _pid: frozenset(),  # no longer listed in g1
    )
    assert payload[0]["lines"][0]["evidence"] == [
        {"ref": "7", "kind": "message"},
    ]


async def test_an_account_never_written_is_absent_not_empty():
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        profile=_Profile(),
        profile_scopes_of=lambda _pid: (("friends", "default"),),
    )
    assert payload == []


async def test_a_person_no_room_lists_has_no_profile_read_at_all():
    profile = _Profile()
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        profile=profile, profile_scopes_of=lambda _pid: (),
    )
    assert payload == []
    assert profile.reads == []


async def test_an_account_whose_workspace_has_no_entry_is_dropped_not_rehomed():
    """Fail-closed across the grouping boundary: the unit's workspace is not one the
    caller belongs to, so the account shows NOWHERE rather than as another
    workspace's."""
    key = character_key("friends", "miles")
    profile = _Profile(heads={key: (_line("l1", "cycles to work"),)})
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        workspaces_of=lambda _pid: ("ridgeline",),
        profile=profile,
        profile_scopes_of=lambda _pid: (("friends", "67"),),
    )
    assert payload == []


@pytest.mark.parametrize("same_unit", [False, True])
async def test_typed_relationship_names_resolve_only_inside_the_accounts_unit(same_unit):
    key = character_key("friends", "miles")
    line = _line("relation", "conflicting prose", kind="relationship", fact_key="relationship",
                 fact_value={"relation": "friend", "person_id": "ada"})
    payload = await build_memory_payload(
        persons=_persons(MILES, ADA), person_id="miles",
        profile=_Profile(heads={key: (line,)}),
        profile_scopes_of=lambda pid: (
            ("friends" if pid == "miles" or same_unit else "other", "default"),),
    )
    text = payload[0]["lines"][0]["text"]
    assert text == ("relationship: friend: " + ("Ada" if same_unit else "someone")
                    + " [told] (told by Miles)")
    assert "conflicting prose" not in text


async def test_fallback_portal_keeps_json_but_displays_shared_formatted_truth():

    key = character_key("friends", "miles")
    value = {"name": "height", "value": 178, "unit": "cm"}
    line = _line("height", "conflicting prose", fact_key="attribute", fact_value=value,
                 as_of=1000)
    payload = await build_memory_payload(
        persons=_persons(MILES), person_id="miles",
        profile=_Profile(heads={key: (line,)}),
        profile_scopes_of=lambda _: (("friends", "default"),),
    )
    shown = payload[0]["lines"][0]
    assert shown["fact_value"] == value and shown["as_of"] == 1000
    from nora.domain.character_card import format_line
    assert shown["text"] == format_line(line, name_of=lambda _: "Miles")
    assert shown["text"].startswith("height: 178 cm (asserted ")
