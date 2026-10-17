"""`remember` on the memory graph's surface: a Persona `told` line
(DEC-317, design/unified-memory/45 §3).

Through the real store and Postgres, so the line that lands is the one the
judge passed: told by the speaker, citing the message that raised the turn,
about the speaker or another listed member. The judge's own rules are
`tests/unit/test_persona_rules.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.agent.lineage import LineagePolicy
from nora.agent.memory_search import MemorySearch
from nora.agent.room import Room
from nora.agent.tools import build_tools
from nora.domain.lineage import RoomFacts
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
    _call,
    _NullStore,
)

UNIT = "u1"
_FRIENDS = RoomFacts(
    "signal", frozenset({GROUP}), UNIT, frozenset({"miles_novak", "tyler_cortez"}),
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
        yield SqlPersonaStore(engine)
    finally:
        await engine.dispose()


def _remember(persons: Any, store: Any, *, trigger: tuple[int, ...] = (41,),
              lineage: LineagePolicy = _LINEAGE, wired: bool = True) -> Any:
    room = Room(
        platform="signal", conversation_id=GROUP,
        roster_source=FakeRosterSource(groups={GROUP: (MILES, TYLER, NORA)}),
        persons=persons, tagger=persons.by_person["miles_novak"], self_ids=NORA,
        trigger_message_ids=trigger,
    )
    built = build_tools(
        store=_NullStore(), conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=Metrics(), room=room, transit_source=_UNREACHABLE_TRANSIT,
        question="", context_window=40, lineage=lineage, supplied_unit=UNIT,
        memory_retrieval=MemorySearch(FakeMemoryIndex([]), Metrics()),
        persona=store if wired else None,
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
    assert result["status"] == "unavailable" and "記低咗" in result["note"]
    assert await store.account_heads(key=f"{UNIT}#character:miles_novak") == ()
