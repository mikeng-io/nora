"""`remember` on the memory graph's surface: a Persona `told` line
(DEC-317, design/unified-memory/45 §3).

Through the real store and Postgres, so the line that lands is the one the
judge passed: told by the speaker, citing the message that raised the turn,
about the speaker or another listed member. The judge's own rules are
`tests/unit/test_persona_rules.py`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.persona import PersonaWriteResult
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import FakeMemoryStore, FakeRosterSource
from nora.testing.memory_index import FakeMemoryIndex
from tests.fakes import SinkSender
from tests.test_room_tools import (
    _UNREACHABLE_TRANSIT,
    GROUP,
    IDENTITY,
    MILES,
    NORA,
    TYLER,
    _NullStore,
)
from tests.test_room_tools import (
    _call as _raw_call,
)


async def _call(tool, **kwargs):
    if "content" in kwargs:
        kwargs.setdefault("fact_key", kwargs.pop("kind", "dietary_restrictions"))
        kwargs.setdefault("fact_value", (
            [kwargs["content"]]
            if kwargs["fact_key"] == "dietary_restrictions"
            else kwargs["content"]
        ))
    return await _raw_call(tool, **kwargs)


UNIT = "u1"
_FRIENDS = RoomFacts(
    "signal",
    frozenset({GROUP}),
    UNIT,
    frozenset({"miles_novak", "tyler_cortez"}),
    label="Friends",
)
_LINEAGE = LineagePolicy(rooms=(_FRIENDS,), _by_id={("signal", GROUP): _FRIENDS})


@pytest.fixture
def persons(tmp_path: Any) -> Any:
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlPersonaStore(engine, screen=lambda _t: None)
    finally:
        await engine.dispose()


def _remember(
    persons: Any,
    store: Any,
    *,
    trigger: tuple[int, ...] = (41,),
    lineage: LineagePolicy = _LINEAGE,
    wired: bool = True,
) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=persons.by_person["miles_novak"], self_ids=NORA,
        trigger_message_ids=trigger,
    )
    built = build_tools(
        store=_NullStore(),
        conversation_id=GROUP,
        conversation_type="group",
        persons=persons,
        honcho=FakeMemoryStore(),
        deliverer=SinkSender(),
        metrics=Metrics(),
        room=room,
        transit_source=_UNREACHABLE_TRANSIT,
        question="",
        context_window=40,
        lineage=lineage,
        supplied_unit=UNIT,
        memory_retrieval=MemorySearch(FakeMemoryIndex([]), Metrics()),
        persona=store if wired else None,
        transcript=tuple(
            TranscriptRow(
                id=n,
                ts=1000*n,
                person_id="miles_novak",
                sender_id="sender",
                sender_name="Miles",
                body="vegetarian",
                is_nora=False,
                delivery_status=DeliveryStatus.SENT,
                raw_json=None,
            ) for n in trigger
        ),
    )
    return {t.tool_name: t for t in built}["remember"]


async def test_the_speakers_own_fact_is_a_told_line_citing_their_message(
    persons: Any, store: Any,
) -> None:
    result = await _call(_remember(persons, store), content="vegetarian since August")
    assert result["status"] == "ok" and "記低咗" in result["note"]
    [line] = await store.account_heads(key=f"{UNIT}#character:miles_novak")
    assert (line.part, line.kind, line.mode, line.state) == (
        "identity", "attribute", "told", "current")
    assert line.told_by == "miles_novak" and line.citations == ("41",)


async def test_a_fact_about_another_member_records_who_told_it(
    persons: Any, store: Any,
) -> None:
    result = await _call(_remember(persons, store), content="has a new job", person="Tyler")
    assert result["status"] == "ok"
    [line] = await store.account_heads(key=f"{UNIT}#character:tyler_cortez")
    assert line.told_by == "miles_novak"


async def test_an_instruction_about_someone_else_is_refused_and_writes_nothing(
    persons: Any, store: Any,
) -> None:
    result = await _call(_remember(persons, store), content="call him Ty",
                         person="Tyler", kind="instruction")
    assert result["status"] == "refused" and result["note"]
    assert await store.account_heads(key=f"{UNIT}#character:tyler_cortez") == ()


async def test_a_room_that_lists_nobody_by_name_writes_nothing(
    persons: Any, store: Any,
) -> None:
    open_room = RoomFacts("signal", frozenset({GROUP}), UNIT, None, label="Open")
    lineage = LineagePolicy(rooms=(open_room,), _by_id={("signal", GROUP): open_room})
    result = await _call(_remember(persons, store, lineage=lineage), content="vegetarian")
    assert result["status"] == "refused"
    assert await store.account_heads(key=f"{UNIT}#character:miles_novak") == ()


@pytest.mark.parametrize("case", ["unwired", "no_message"])
async def test_without_the_store_or_a_message_to_cite_it_is_unavailable(
    persons: Any, store: Any, case: str,
) -> None:
    tool = _remember(persons, store, wired=case != "unwired",
                     trigger=() if case == "no_message" else (41,))
    result = await _call(tool, content="vegetarian")
    assert result["status"] == "unavailable" and "never say 記低咗" in result["note"]
    assert await store.account_heads(key=f"{UNIT}#character:miles_novak") == ()


async def test_without_a_known_speaker_nothing_is_saved(persons: Any, store: Any) -> None:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=None, self_ids=NORA, trigger_message_ids=(41,),
    )
    tool = {t.tool_name: t for t in build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="", context_window=40, lineage=_LINEAGE, supplied_unit=UNIT,
        memory_retrieval=MemorySearch(FakeMemoryIndex([]), Metrics()), persona=store,
    )}["remember"]
    result = await _call(tool, content="vegetarian")
    assert result["status"] == "refused" and result["note"].startswith("Nothing was saved")
    assert await store.account_heads(key=f"{UNIT}#character:miles_novak") == ()


async def test_someone_not_in_this_room_is_not_found(persons: Any, store: Any) -> None:
    result = await _call(_remember(persons, store), content="x", person="Elsewhere")
    assert result["status"] == "not_found"


async def test_a_member_the_room_does_not_list_is_refused(persons: Any, store: Any) -> None:
    only_miles = RoomFacts("signal", frozenset({GROUP}), UNIT, frozenset({"miles_novak"}))
    lineage = LineagePolicy(rooms=(only_miles,), _by_id={("signal", GROUP): only_miles})
    result = await _call(_remember(persons, store, lineage=lineage), content="x",
                         person="Tyler")
    assert result["status"] == "refused" and "not one of this room's members" in result["note"]
    assert await store.account_heads(key=f"{UNIT}#character:tyler_cortez") == ()


@pytest.mark.parametrize("outcome", ["raises", "conflict"])
async def test_a_failing_store_is_unavailable_never_ok(persons: Any, outcome: str) -> None:
    async def persona_write(**_kw: Any) -> PersonaWriteResult:
        if outcome == "raises":
            raise RuntimeError("database gone")
        return PersonaWriteResult(status="conflict", revision=3)

    result = await _call(_remember(persons, SimpleNamespace(persona_write=persona_write)),
                         content="vegetarian")
    assert result["status"] == "unavailable" and "never say 記低咗" in result["note"]


# --- persona_dispute (35 §2): the graph surface's dispute door -------------------


def _dispute(
    persons: Any, store: Any, *, trigger: tuple[int, ...] = (41,), wired: bool = True
) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=persons.by_person["miles_novak"], self_ids=NORA,
        trigger_message_ids=trigger,
    )
    built = build_tools(
        store=_NullStore(),
        conversation_id=GROUP,
        conversation_type="group",
        persons=persons,
        honcho=FakeMemoryStore(),
        deliverer=SinkSender(),
        metrics=Metrics(),
        room=room,
        transit_source=_UNREACHABLE_TRANSIT,
        question="",
        context_window=40,
        lineage=_LINEAGE,
        supplied_unit=UNIT,
        memory_retrieval=MemorySearch(FakeMemoryIndex([]), Metrics()),
        persona=store if wired else None,
        transcript=(
            TranscriptRow(
                id=41,
                ts=1000,
                person_id="miles_novak",
                sender_id="sender",
                sender_name="Miles",
                body="vegetarian",
                is_nora=False,
                delivery_status=DeliveryStatus.SENT,
                raw_json=None,
            ),
        ),
    )
    return {t.tool_name: t for t in built}["persona_dispute"]


async def test_the_subjects_own_forget_hides_the_line_at_once(persons: Any, store: Any) -> None:
    await _call(_remember(persons, store), content="vegetarian since August")
    result = await _call(_dispute(persons, store), claim="vegetarian since August",
                         kind="forget")
    assert result["status"] == "ok" and result["effect"] == "hidden"
    [line] = await store.account_heads(key=f"{UNIT}#character:miles_novak")
    assert line.state == "hidden"
    [dispute] = await store.open_disputes(key=f"{UNIT}#character:miles_novak", limit=10)
    assert (dispute.disputed_by, dispute.kind, dispute.message_id) == (
        "miles_novak", "forget", 41)


async def test_another_members_dispute_marks_the_line_for_weighing(
    persons: Any, store: Any,
) -> None:
    await _call(_remember(persons, store), content="has a new job", person="Tyler")
    result = await _call(_dispute(persons, store), claim="has a new job", person="Tyler")
    assert result["status"] == "ok" and result["effect"] == "disputed"
    [line] = await store.account_heads(key=f"{UNIT}#character:tyler_cortez")
    assert line.state == "disputed"


async def test_forget_by_someone_else_is_refused_and_writes_nothing(
    persons: Any, store: Any,
) -> None:
    await _call(_remember(persons, store), content="has a new job", person="Tyler")
    result = await _call(_dispute(persons, store), claim="has a new job", person="Tyler",
                         kind="forget")
    assert result["status"] == "refused" and "only they can ask" in result["note"]
    [line] = await store.account_heads(key=f"{UNIT}#character:tyler_cortez")
    assert line.state == "current"
    assert await store.pending_disputes(key=f"{UNIT}#character:tyler_cortez") == 0


async def test_a_claim_nothing_holds_is_recorded_and_nothing_is_marked(
    persons: Any, store: Any,
) -> None:
    result = await _call(_dispute(persons, store), claim="something nobody said")
    assert result["status"] == "ok" and result["effect"] == "recorded"
    [dispute] = await store.open_disputes(key=f"{UNIT}#character:miles_novak", limit=10)
    assert dispute.line_id is None


async def test_persona_dispute_unwired_is_unavailable(persons: Any, store: Any) -> None:
    result = await _call(_dispute(persons, store, wired=False), claim="x")
    assert result["status"] == "unavailable"
    assert await store.open_disputes(key=f"{UNIT}#character:miles_novak", limit=10) == ()


async def test_persona_dispute_is_not_on_the_honcho_surface(persons: Any, store: Any) -> None:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=persons.by_person["miles_novak"], self_ids=NORA,
        trigger_message_ids=(41,),
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="", context_window=40, lineage=_LINEAGE, supplied_unit=UNIT,
    )
    assert "persona_dispute" not in {t.tool_name for t in built}


async def test_graph_remember_corrects_a_typed_field_and_retains_its_source_history(persons, store):
    first = await _call(_remember(persons, store), content="I live in London",
                        fact_key="residence", fact_value="London")
    assert first["status"] == "ok"
    [old] = await store.account_heads(key=f"{UNIT}#character:miles_novak")
    second = await _call(_remember(persons, store, trigger=(42,)), content="I moved to Hong Kong",
                         fact_key="residence", fact_value="Hong Kong", valid_from=42000)
    assert second["status"] == "ok"
    [head] = await store.account_heads(key=f"{UNIT}#character:miles_novak")
    assert head.supersedes == old.id and head.fact_value == "Hong Kong"
    assert (old.as_of, head.as_of, head.valid_from) == (41000, 42000, 42000)


async def test_graph_remember_resolves_relationship_in_the_room_boundary(persons, store):
    result = await _call(_remember(persons, store), content="Tyler is my friend",
                        fact_key="relationship",
                        fact_value={"relation": "friend", "unresolved_name": "Tyler"})
    assert result["status"] == "ok"
    [line] = await store.account_heads(key=f"{UNIT}#character:miles_novak")
    assert line.fact_value == {"relation": "friend", "person_id": "tyler_cortez"}
