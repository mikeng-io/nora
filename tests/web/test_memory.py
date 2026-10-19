import pytest

from nora.domain.culture import RoomMessage, character_key
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.persona import PersonaDispute, PersonaLine
from nora.persons import Persons
from nora.testing import FakeMemoryStore
from nora.web.memory import build_memory_payload

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")


def _persons(*refs: PersonRef) -> Persons:
    return Persons(by_person={r.person_id: r for r in refs}, index={})


async def test_returns_only_the_callers_own_entry_not_everyone_elses():
    """User-scoped: a caller resolved to `miles` must never see `ada`'s memory
    (no "showcase everyone to everyone")."""
    honcho = FakeMemoryStore(
        conclusions={"miles": ["likes climbing", "works in fintech"], "ada": ["plays guitar"]},
        cards={"miles": ["Name: Miles Novak"], "ada": ["Name: Ada"]},
    )
    persons = _persons(MILES, ADA)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert [entry["person_id"] for entry in payload] == ["miles"]
    assert payload[0]["display_name"] == "Miles"
    assert payload[0]["reachable"] is True
    assert payload[0]["episodes"] == ["likes climbing", "works in fintech"]
    assert payload[0]["identity"] == ["Name: Miles Novak"]


async def test_a_missing_person_id_returns_empty_not_everyone():
    """Fail-closed (R-5's shape): no resolved identity must never fall back
    to showing every whitelisted person's data."""
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id=None,
    )

    assert payload == []


async def test_an_unrecognized_person_id_returns_empty_not_an_error():
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="stranger",
    )

    assert payload == []


async def test_a_person_with_no_conclusions_or_card_is_reachable_but_empty():
    honcho = FakeMemoryStore(conclusions={}, cards={})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": True,
            "identity": [], "episodes": [],
        },
    ]


async def test_an_unreachable_honcho_is_reported_unavailable_not_empty():
    """R-27: an unreachable Honcho must never look identical to 'nothing
    known' — FakeMemoryStore(fail=True) mirrors the live client's unreachable
    shape (tests/fakes.py's Recollection(reachable=False)). `identity`
    still comes back `[]` (peer_card's own documented ambiguity — ports.py:
    it answers `[]` for both "down" and "knows nothing"), so `reachable`
    (sourced from `representation` alone) is the only unavailability signal
    this payload carries."""
    honcho = FakeMemoryStore(fail=True)
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload == [
        {
            "person_id": "miles", "display_name": "Miles", "reachable": False,
            "identity": [], "episodes": [],
        },
    ]


async def test_queries_honcho_as_the_ai_peer_observing_the_caller():
    """Nora's MODEL OF the person, not the person's self-model — the same
    (observer=ai_peer, observed=target) shape nora/engagement/context.py's
    _recall_self/_recall_tagger_card already use."""
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    persons = _persons(MILES)

    await build_memory_payload(honcho=honcho, persons=persons, ai_peer="nora", person_id="miles")

    assert honcho.queries == [("nora", "miles", "")]


async def test_identity_is_the_persons_own_self_scoped_peer_card_not_an_observer_pair():
    """`peer_card` takes only `peer_id` — no observer/observed pair — called
    with the PERSON's own honcho_peer_id, distinct from `representation`'s
    (ai_peer, target) shape asserted above."""
    honcho = FakeMemoryStore(cards={"miles": ["Name: Miles Novak", "Aliases: Miles"]})
    persons = _persons(MILES)

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
    )

    assert payload[0]["identity"] == ["Name: Miles Novak", "Aliases: Miles"]


async def test_one_entry_per_workspace_the_caller_belongs_to_each_read_there():
    """DEC-214: a person in units of two Nora workspaces has a separate model
    in each workspace's Honcho workspace. The page shows them apart, labelled,
    each read routed by the WORKSPACE (unscoped across its units) — and only
    ever the caller's own model."""
    honcho = FakeMemoryStore(conclusions={"miles": ["likes climbing"], "ada": ["x"]})
    persons = _persons(MILES, ADA)
    member_of = {"miles": ("67", "ridgeline"), "ada": ("67",)}

    payload = await build_memory_payload(
        honcho=honcho, persons=persons, ai_peer="nora", person_id="miles",
        workspaces_of=lambda pid: member_of.get(pid, ()),
    )

    assert [(e["person_id"], e["workspace"]) for e in payload] == [
        ("miles", "67"), ("miles", "ridgeline"),
    ]
    assert honcho.workspace_reads == [
        ("representation", "67"), ("peer_card", "67"),
        ("representation", "ridgeline"), ("peer_card", "ridgeline"),
    ]
    assert {observed for (_obs, observed, _q) in honcho.queries} == {"miles"}


async def test_a_caller_in_no_workspace_sees_nothing_rather_than_a_guess():
    """A person no listed room names belongs to no workspace, so there is no
    Honcho workspace to read — empty, never a default one."""
    honcho = FakeMemoryStore(conclusions={"miles": ["x"]})
    payload = await build_memory_payload(
        honcho=honcho, persons=_persons(MILES), ai_peer="nora", person_id="miles",
        workspaces_of=lambda _pid: (),
    )
    assert payload == []
    assert honcho.queries == []


# --- Persona (45, the switch) --------------------------------------------------

_NOW = 1_790_000_000_000


class _Persona:
    """The portal's two reads, answered from dicts keyed by account key."""

    def __init__(self, heads=None, disputes=None):
        self._heads = heads or {}
        self._disputes = disputes or {}

    async def account_heads(self, *, key):
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


def _line(line_id: str, text: str, **overrides) -> PersonaLine:
    defaults = {
        "id": line_id, "persona_key": character_key("friends", "miles"),
        "part": "identity", "kind": "attribute", "text": text, "mode": "told",
        "citations": ("7",), "state": "current",
        "unit": "friends", "written_at": _NOW, "written_by_session": "tag",
        "told_by": "miles",
    }
    return PersonaLine(**(defaults | overrides))


async def test_a_callers_own_persona_accounts_ride_their_workspace_entries():
    """The switch's portal half (cognition-loop/50 §4): the caller sees the
    account about them per unit that lists them, each under its workspace's
    entry — every chain head with its state, the evidence behind it, and the
    open disputes."""
    key = character_key("friends", "miles")
    persona = _Persona(
        heads={key: (
            _line("l1", "cycles to work"),
            _line("l2", "maybe vegetarian", part="hypothesis", kind="explanation",
                  mode="abduced", state="disputed", told_by=None,
                  citations=("response:9", "social:instagram:p1", "rollup:outing:friends|x"),
                  prediction="orders veg", prediction_observable="rollup:outing:friends|x",
                  prediction_window_end=_NOW + 1000, verdict="open"),
        )},
        disputes={key: (PersonaDispute(
            id="d1", persona_key=key, line_id="l2", disputed_by="miles",
            kind="dispute", claim="maybe vegetarian", message_id=7,
            unit="friends", written_at=_NOW,
        ),)},
    )
    cited = _Cited([_row(7, "i cycle to work", "g1"), _row(9, "her send", "g1", None)])
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}, cards={"miles": []}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
        workspaces_of=lambda _pid: ("67",),
        persona=persona,
        persona_scopes_of=lambda _pid: (("friends", "67"),),
        cited=cited,
        rooms_of=lambda _pid: frozenset({("signal", "g1")}),
    )
    [account] = payload[0]["persona"]
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
    persona = _Persona(heads={key: (_line("l1", "cycles to work"),)})
    cited = _Cited([_row(7, "i cycle to work", "g1")])
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
        persona=persona, persona_scopes_of=lambda _pid: (("friends", "default"),),
        cited=cited,
        rooms_of=lambda _pid: frozenset(),  # no longer listed in g1
    )
    assert payload[0]["persona"][0]["lines"][0]["evidence"] == [
        {"ref": "7", "kind": "message"},
    ]


async def test_an_account_never_written_is_absent_not_empty():
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
        persona=_Persona(),
        persona_scopes_of=lambda _pid: (("friends", "default"),),
    )
    assert "persona" not in payload[0]


async def test_a_person_no_room_lists_has_no_persona_read_at_all():
    persona = _Persona()
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
        persona=persona, persona_scopes_of=lambda _pid: (),
    )
    assert "persona" not in payload[0]


async def test_an_account_whose_workspace_has_no_entry_is_dropped_not_rehomed():
    """Fail-closed across the grouping boundary: the unit's workspace names no
    entry of this caller, so the account shows NOWHERE rather than under
      another workspace's label."""
    key = character_key("friends", "miles")
    persona = _Persona(heads={key: (_line("l1", "cycles to work"),)})
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
        workspaces_of=lambda _pid: ("ridgeline",),
        persona=persona,
        persona_scopes_of=lambda _pid: (("friends", "67"),),
    )
    assert "persona" not in payload[0]


async def test_without_a_persona_reader_the_payload_is_unchanged():
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(conclusions={"miles": ["x"]}, cards={"miles": ["Name: M"]}),
        persons=_persons(MILES), ai_peer="nora", person_id="miles",
    )
    assert payload == [
        {"person_id": "miles", "display_name": "Miles", "reachable": True,
         "identity": ["Name: M"], "episodes": ["x"]},
    ]


@pytest.mark.parametrize("same_unit", [False, True])
async def test_typed_relationship_names_resolve_only_inside_the_accounts_unit(same_unit):
    key = character_key("friends", "miles")
    line = _line("relation", "conflicting prose", kind="relationship", fact_key="relationship",
                 fact_value={"relation": "friend", "person_id": "ada"})
    payload = await build_memory_payload(
        honcho=FakeMemoryStore(), persons=_persons(MILES, ADA), ai_peer="nora", person_id="miles",
        persona=_Persona(heads={key: (line,)}),
        persona_scopes_of=lambda pid: (
            ("friends" if pid == "miles" or same_unit else "other", "default"),),
    )
    text = payload[0]["persona"][0]["lines"][0]["text"]
    assert text == "relationship: friend: " + ("Ada" if same_unit else "someone")
    assert "conflicting prose" not in text
