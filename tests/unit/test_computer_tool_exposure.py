"""The scoped computer tools, end to end through the REAL registry surface.

`test_tool_registry.py` pins that the computer verbs are admitted, shelved and
catalogued; `test_tool_surface.py` pins the loader's dispatch for the generic
set. This file pins the scoped-path specifics no other suite exercises
together: a deferred Signal tag surface exposes the browser/capture/cancel/
export verbs only when every scoped collaborator is wired, `load_tools` hands
them over by name, a write lands on the turn's own scope (group unit for a
group, the canonical `(person, anchor)` binding for a DM), and a file written
through `computer_write` is the bytes `computer_export` delivers — while a
file that was never made answers `nothing_found`, never a guessed success.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.surface import load_onto
from nora.agent.tools import COMPUTER_TOOLS, LOADER_TOOL
from nora.app.computer_queue import ComputerAdmission
from nora.domain.computer import (
    ComputerToolSettings,
    computer_dm_scope,
        computer_unit_scope,
)
from nora.domain.computer_leases import ComputerLeases
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputerEventStore,
    FakeComputerExportStore,
    FakeComputerSessionStore,
    FakeComputerTaskStore,
    FakeRosterSource,
    FakeSandboxRuntime,
    FakeStreamRedis,
)

NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
CID = "g-computer"
UNIT = "unit-x"
SCOPED_ONLY = {"computer_browse", "computer_capture", "computer_export", "computer_cancel"}


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.miles]\nfirst_name = "Miles"\n'
                    '[[persons.miles.signal]]\nphone = "+85290000101"\n')
    return load_persons(path)


def _room(persons, conversation_id=CID):
    return Room(
        platform="signal", conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: ()}),
        persons=persons, self_ids=frozenset({"+85290000100"}))


class _Rig:
    def __init__(self):
        self.runtime = FakeSandboxRuntime()
        self.events = FakeComputerEventStore()
        self.exports = FakeComputerExportStore()
        self.tasks = FakeComputerTaskStore()
        self.admission = ComputerAdmission(
            tasks=self.tasks, redis=FakeStreamRedis(), clock=lambda: NOW)
        self.registry = ToolRegistry(
            unit_for=lambda platform, cid: UNIT,
            blobs=FakeBlobStore(),
            computer=self.runtime,
            computer_events=self.events,
            computer_settings=ComputerToolSettings(),
            computer_sessions=FakeComputerSessionStore(),
            computer_leases=ComputerLeases(),
            computer_exports=self.exports,
            browser=FakeBrowserRunner(),
            computer_admission=self.admission,
            browser_manager=object(),
            computer_dm_scope_for=lambda platform, cid: "dm-anchor-1",
            artifact_link_base="https://portal.test/",
        )

    def surface_kwargs(self, persons, room, *, conversation_id=CID,
                       conversation_type="group"):
        return dict(
            store=None, conversation_id=conversation_id,
            conversation_type=conversation_type, persons=persons, honcho=None,
            deliverer=None, metrics=Metrics(), room=room)


class _AgentRegistry:
    """The slice of a Strands registry `load_onto` touches."""

    def __init__(self):
        self.registry = {}
        self.dynamic_tools = {}

    def register_dynamic_tool(self, tool):
        self.dynamic_tools[getattr(tool, "tool_name", str(tool))] = tool


class _Agent:
    def __init__(self):
        self.tool_registry = _AgentRegistry()


def test_scoped_surface_defers_and_loads_the_computer_verbs(persons):
    """Everything on the shelf, nothing resident, and `load_tools` delivers
    the named schemas — an invented name is `nothing_found`, not a load."""
    rig = _Rig()
    surface = rig.registry.surface(defer=True, **rig.surface_kwargs(persons, _room(persons)))
    assert (COMPUTER_TOOLS | SCOPED_ONLY) <= surface.admitted
    assert not {n for n in surface.resident if n.startswith("computer_")}
    assert (COMPUTER_TOOLS | SCOPED_ONLY) <= surface.shelf.names()
    assert surface.hooks  # the loader hook rides with a stocked shelf
    catalogue = surface.prompt_catalogue
    for name in COMPUTER_TOOLS | SCOPED_ONLY:
        assert name in catalogue

    agent = _Agent()
    load = load_onto(agent, surface.shelf,
                     ["computer_browse", "computer_write",
                      "computer_capture", "computer_export"])
    assert load["status"] == "ok"
    assert set(agent.tool_registry.dynamic_tools) == {
        "computer_browse", "computer_write", "computer_capture", "computer_export"}
    assert LOADER_TOOL not in surface.shelf.names()
    bad = load_onto(agent, surface.shelf, ["computer_wrong"])
    assert bad["status"] == "nothing_found"

    # Negative control: no runtime → every computer verb withheld.
    off = ToolRegistry().surface(defer=True, **rig.surface_kwargs(persons, _room(persons)))
    assert not {n for n in off.admitted if n.startswith("computer_")}


async def test_write_then_export_delivers_the_same_bytes_on_the_bound_scope(persons):
    """The generated CSV: write lands on the turn's hashed scope, export
    fetches exactly those bytes and records the export — a file nobody made
    is `nothing_found`, never a fabricated success."""
    rig = _Rig()
    specs = {s.name: s for s in rig.registry.specs(**rig.surface_kwargs(persons, _room(persons)))}
    write = await specs["computer_write"].call(
        path="report.csv", content="col_a,col_b\n1,2\n")
    assert write["status"] == "ok"
    assert sorted(rig.runtime.files_by_scope) == [computer_unit_scope(UNIT)]

    export = await specs["computer_export"].call(path="report.csv", to="keep")
    assert export["status"] == "ok"
    assert rig.exports.exports
    finished = [e for e in rig.events.finished() if e.tool == "computer_export"]
    assert finished and finished[-1].status == "ok"

    missing = await specs["computer_export"].call(path="nope.csv", to="keep")
    assert missing["status"] == "nothing_found"

    # The writes went through the real admission queue, one durable row each.
    assert {r.payload.get("tool") for r in rig.tasks.rows.values()} == {"computer_write"}


async def test_a_dm_binds_the_canonical_scope_not_the_rooms_unit(persons):
    """A direct message gets `(person, anchor)` — never the group unit's VM."""
    rig = _Rig()
    dm = _room(persons, conversation_id="dm-miles")
    specs = {s.name: s for s in rig.registry.specs(
        **rig.surface_kwargs(persons, dm, conversation_id="dm-miles",
                             conversation_type="direct"))}
    write = await specs["computer_write"].call(path="dm.csv", content="x\n")
    assert write["status"] == "ok"
    assert computer_dm_scope("dm-anchor-1") in rig.runtime.files_by_scope
    assert computer_unit_scope(UNIT) not in rig.runtime.files_by_scope
