"""The Notebook read tools resolve and return notes within one unit."""

from __future__ import annotations

from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.obs.metrics import Metrics
from nora.testing import FakeRosterSource
from nora.testing.note_store import InMemoryNoteStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak")
GROUP = "signal:group-1"
UNIT = "household"
NOW = 1_770_000_000_000


class _NullStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _room(*, tagger: PersonRef | None = MILES, conversation_id: str = GROUP) -> Room:
    source = FakeRosterSource(groups={conversation_id: (frozenset({"miles"}),)})
    return Room(
        platform="signal", conversation_id=conversation_id, roster_source=source,
        persons=_FakePersons(MILES), tagger=tagger,
    )


def _tools(
    *, notes: InMemoryNoteStore | None, unit: str | None = UNIT,
    metrics: Metrics | None = None, tagger: PersonRef | None = MILES,
    conversation_id: str = GROUP, rooms: dict[str, str] | None = None,
) -> dict:
    # `note_unit` RESOLVES a room to its unit rather than naming one.
    # The resolver here READS its arguments — a lambda ignoring them would make
    # every test below pass whether or not the tool consults the room, which is
    # exactly the decorative test CLAUDE.md names. `rooms` maps
    # conversation_id -> unit, the shape `policy.toml` actually has.
    book = rooms if rooms is not None else {conversation_id: unit}

    def _resolve(_platform: str, cid: str) -> str | None:
        return book.get(cid)

    registry = ToolRegistry(notes=notes, note_unit=_resolve)
    specs = registry.specs(
        store=_NullStore(), conversation_id=conversation_id,
        conversation_type="group",
        persons=_FakePersons(MILES), deliverer=SinkSender(),
        metrics=metrics or Metrics(),
        room=_room(tagger=tagger, conversation_id=conversation_id),
        transcript=_WINDOW,
    )
    return {spec.name: spec.call for spec in specs}


#: The transcript the tools' turn rendered: `[#1]` is row 501, Miles's. A body's
#: `[#n]` is mapped to the row id of that handle.
_WINDOW = (
    TranscriptRow(
        id=501, sender_id="+85200000001", sender_name="miles", ts=NOW - 60_000,
        body="dinner on the 9th?", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name="Miles Novak", person_id="miles",
    ),
)


async def _seed(
    store: InMemoryNoteStore, *, title: str, body: str,
    unit: str = UNIT, description: str = "one line about it",
):
    return await store.create(
        unit=unit, title=title, description=description,
        uncertainty="not settled yet", closing_condition="somebody decides",
        established_in=GROUP, body=body, established_by="tag:miles", now_ms=NOW,
        anchor_date=20261011,
    )


def test_note_reads_keep_their_budgets():
    assert NATIVE_TOOLS["note_read"].budget == 3
    assert NATIVE_TOOLS["note_search"].budget == 2


def test_no_note_tool_ends_the_turn():
    for name in ("note_read", "note_search"):
        assert NATIVE_TOOLS[name].terminal is False


async def test_every_note_tool_answers_unavailable_when_no_store_is_wired():
    tools = _tools(notes=None)
    calls = (
        ("note_read", {"title": "Dinner"}),
        ("note_search", {"query": "hotel"}),
    )
    for name, kwargs in calls:
        assert (await tools[name](**kwargs))["status"] == "unavailable", name


async def test_a_store_with_no_unit_is_unavailable_not_empty():
    """The other half of the same wiring gap: a store wired without the
    unit it is keyed on cannot answer, and «there is no such note» would
    be a lie about a notebook that was never consulted."""
    tools = _tools(notes=InMemoryNoteStore(), unit="")
    assert (await tools["note_read"](title="Dinner"))["status"] == "unavailable"
    assert (await tools["note_search"](query="hotel"))["status"] == "unavailable"


async def test_note_read_resolves_the_title_the_index_shows_and_returns_the_body():
    store = InMemoryNoteStore()
    note = await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked yet.")
    tools = _tools(notes=store)

    out = await tools["note_read"](title="Dinner, 9 Sept")

    assert out["status"] == "ok"
    assert out["body"] == "Nobody has booked yet."
    assert out["title"] == "Dinner, 9 Sept"
    assert out["revision_id"] == note.head_revision_id


async def test_note_read_matches_a_title_case_insensitively():
    """`(unit, lower(title))` is what the partial unique index is on, so
    case is not part of a note's identity — and 「dinner」 typed back at the
    index's 「Dinner, 9 Sept」 must not be a miss."""
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked yet.")
    tools = _tools(notes=store)
    assert (await tools["note_read"](title="  dinner, 9 sept "))["status"] == "ok"


async def test_note_read_on_a_merged_title_reaches_its_survivor():
    """The turn's `<notes>` was rendered before a merge, so the
    model names the duplicate's title after it retired. «No note by that
    title» would be false of the question, which lives on in the survivor —
    the read forwards, and says which note it actually read."""
    from nora.domain.notes import NoteSupersedeReason

    store = InMemoryNoteStore()
    survivor = await _seed(store, title="Saturday dinner", body="time and place open")
    duplicate = await _seed(store, title="星期六飯局細節未定", body="asked twice")
    await store.supersede(
        key=UNIT, target=duplicate.id, replaced_by=survivor.id,
        because=NoteSupersedeReason.RETIRED_MERGED, source=None, now_ms=NOW + 1,
    )
    tools = _tools(notes=store)

    out = await tools["note_read"](title="星期六飯局細節未定")

    assert out["status"] == "ok"
    assert out["title"] == "Saturday dinner"
    assert out["body"] == "time and place open"


async def test_note_read_answers_nothing_found_for_a_title_that_is_not_there():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="b")
    tools = _tools(notes=store)
    out = await tools["note_read"](title="Grant's wedding")
    assert out["status"] == "nothing_found"


async def test_note_read_refuses_an_ambiguous_title_and_names_both():
    """Ambiguity REFUSES, it never picks one. Guessing which note was
    meant is how a revision lands on the wrong body — and the refusal has to
    name the candidates or she cannot ask which was meant."""
    store = InMemoryNoteStore()
    # The database forbids this with a partial unique index; the store double
    # does not, which is exactly why the TOOL has to refuse rather than trust
    # the index to have made it impossible.
    await _seed(store, title="Wedding", body="Grant's")
    await _seed(store, title="wedding", body="Blake's")
    tools = _tools(notes=store)

    out = await tools["note_read"](title="Wedding")

    assert out["status"] == "refused"
    assert sorted(out["candidates"]) == ["Wedding", "wedding"]


async def test_note_read_cannot_reach_another_units_note():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="b", unit="somebody-else")
    tools = _tools(notes=store)
    assert (await tools["note_read"](title="Dinner, 9 Sept"))["status"] == "nothing_found"


async def test_note_search_reaches_into_a_body_the_title_says_nothing_about():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Ada suggested a hotel buffet.")
    tools = _tools(notes=store)

    out = await tools["note_search"](query="hotel")

    assert out["status"] == "ok"
    assert [m["title"] for m in out["matches"]] == ["Dinner, 9 Sept"]
    assert "hotel" in out["matches"][0]["body"]


async def test_note_search_does_not_match_on_the_title_alone():
    """The injected index already shows every title, so a title search
    would duplicate the prompt and earn nothing. A hit on a word that appears
    ONLY in the title means the search is reading the wrong column."""
    store = InMemoryNoteStore()
    await _seed(store, title="Hotel for Grant", body="Nobody has decided.")
    tools = _tools(notes=store)
    assert (await tools["note_search"](query="Hotel"))["status"] == "nothing_found"


#
# `not_read` is a reading about the MODEL — it tried to rewrite a body it had
# not seen. `title_moved` and `stale_head` are readings about CONTENTION — the
# note it reached was not the note it read, or the head moved under the call.
# Each reason is its own label on one series, so a query can separate «the
# latch is working» from «two writers are fighting».


#
# Every test below asserts through `evaluate`'s refusal rather than by reading
# the latch set. The set is invisible to the model; the veto is where a miss
# costs something, and a test that reads the set goes green against a latch
# that never vetoes anything.

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()


async def test_two_rooms_in_different_units_see_different_notebooks():
    """The leak this resolver exists to prevent, and it fails if the unit
    stops being read from the ROOM.

    `policy.toml` puts the operator's own rooms in `ridgeline` and the group in
    `friends`, deliberately never paired (DEC-105/R-49 — a unit is the
    boundary `send_to` may not cross). A process-wide unit string hands
    one room reveals the other room's notebook.

    Both calls below go through the SAME resolver and the same store; only the
    room differs. That is what makes this red when the tool stops consulting
    the room — with one unit configured, every other test in this file
    passes either way.
    """
    store = InMemoryNoteStore()
    await store.create(
        unit="friends", title="Group dinner", description="undecided",
        uncertainty="where", closing_condition="somebody picks",
        established_in="signal:friends-room", body="three places",
        established_by="miles", now_ms=1, anchor_date=20260909,
    )
    book = {"friends-room": "friends", "ridgeline-room": "ridgeline"}

    in_friends = await _tools(
        notes=store, rooms=book, conversation_id="friends-room",
    )["note_read"](title="Group dinner")
    in_ridgeline = await _tools(
        notes=store, rooms=book, conversation_id="ridgeline-room",
    )["note_read"](title="Group dinner")

    assert in_friends["status"] == "ok"
    # Same store, same title, different room: not there, and not readable.
    assert in_ridgeline["status"] == "nothing_found"


async def test_a_room_policy_cannot_name_gets_unavailable_never_a_guess():
    """An unlisted room has no unit, and the verbs must say so.

    Defaulting to any particular notebook would write a note into one the room
    is not part of — and `standing` would then render it to people who never
    saw the conversation it came from. `unavailable` is «I could not check»;
    `nothing_found` would be the lie.
    """
    store = InMemoryNoteStore()
    out = await _tools(notes=store, unit=None)["note_read"](title="anything")
    assert out["status"] == "unavailable"


async def test_every_non_ok_note_status_carries_a_note():
    store = InMemoryNoteStore()
    await _seed(store, title="Dinner, 9 Sept", body="Nobody has booked.")
    await _seed(store, title="Wedding", body="Grant's")
    await _seed(store, title="wedding", body="Blake's")
    tools = _tools(notes=store)
    cases = {
        "unavailable/read_no_store": (_tools(notes=None)["note_read"], {"title": "x"}),
        "nothing_found/read": (tools["note_read"], {"title": "Grant's wedding"}),
        "refused/read_ambiguous": (tools["note_read"], {"title": "Wedding"}),
        "refused/search_empty": (tools["note_search"], {"query": "  "}),
        "nothing_found/search": (tools["note_search"], {"query": "hotel"}),
    }
    for case, (call, kwargs) in cases.items():
        out = await call(**kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_a_missing_title_note_says_to_check_the_index_not_to_guess_variants():
    """The rule «the titles in <notes> are the only ones there are, so check it
    rather than trying variations» rides on the `nothing_found` it is about,
    not in note_read's description."""
    store = InMemoryNoteStore()
    out = await _tools(notes=store)["note_read"](title="Grant's wedding")
    assert out["status"] == "nothing_found"
    assert "<notes>" in out["note"] and "variations" in out["note"]
