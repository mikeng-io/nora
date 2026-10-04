"""The sub-agent tools, step 3 (design/subagent §3, DEC-283): `subagent_create`, `subagent_list`,
`subagent_read`, `subagent_cancel`.

Driven through the real `ToolRegistry` surface and the real `SubagentDesk` over the in-memory
outbox. Two guards are load-bearing and are proved by reverting them (see the commit that added
this file): ROOM SCOPING (`SubagentDesk._own`, so another room's job id is an unknown id) and
the tools being ABSENT unless `[subagent] enabled` (`ToolRegistry._withheld`).
"""

from __future__ import annotations

import asyncio
import inspect
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.subagent_desk import SubagentDesk
from nora.agent.subagent_tools import NEVER, NOT_LOOKUPS, SubagentToolSource, granted_names
from nora.agent.tools import SUBAGENT_TOOLS
from nora.app.compute_poller import ComputeJobPoller
from nora.app.documents_service import DocumentService
from nora.domain.compute import ComputeJobState
from nora.domain.ports import LaunchRequest
from nora.domain.subagent import SUBAGENT_KIND, SubagentBrief
from nora.engagement.slots import ConversationSlots
from nora.obs.logging import bind_tag_id
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import (
    FakeAgentRunner,
    FakeComputeJobStore,
    FakeMemoryStore,
    FakeRosterSource,
    ScriptedTurn,
)
from tests.unit.test_fire_path import _fire_path, _FireAdapter
from tests.unit.test_subagent_worker import (
    MILES,
    PLATFORM,
    ROOM,
    _doc,
    _Persons,
    _Rig,
    _Rows,
    _Script,
    _until_done,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
OTHER_ROOM = "g-other"
PUBLIC = "https://nora.example"


class _FakeWorker:
    """What the desk launches into: records requests and cancels, never runs anything."""

    def __init__(self) -> None:
        self.launched: list[LaunchRequest] = []
        self.cancelled: list[str] = []
        self.live: dict[str, Any] = {}
        self.refuse = False

    async def launch(self, *, request: LaunchRequest) -> str:
        if self.refuse:
            raise RuntimeError("no capacity")
        self.launched.append(request)
        return f"bj{len(self.launched)}"

    async def cancel(self, *, job_id: str) -> None:
        self.cancelled.append(job_id)

    def status(self, job_id: str) -> Any:
        return self.live.get(job_id)


def _room(conversation_id: str = ROOM, *, tagger: Any = MILES) -> Room:
    return Room(
        platform=PLATFORM, conversation_id=conversation_id,
        roster_source=FakeRosterSource(groups={conversation_id: ()}), persons=_Persons(),
        tagger=tagger,
    )


def _specs(registry: ToolRegistry, *, conversation_id: str = ROOM, **kw: Any) -> dict[str, Any]:
    found = registry.specs(
        store=_Rows(), conversation_id=conversation_id, conversation_type="group",
        persons=_Persons(), honcho=FakeMemoryStore(), deliverer=None, metrics=Metrics(),
        room=_room(conversation_id, tagger=kw.pop("tagger", MILES)), **kw,
    )
    return {s.name: s.call for s in found}


def _surface_names(registry: ToolRegistry, **kw: Any) -> set[str]:
    surface = registry.surface(
        store=_Rows(), conversation_id=ROOM, conversation_type="group", persons=_Persons(),
        honcho=FakeMemoryStore(), deliverer=None, metrics=Metrics(), room=_room(), **kw,
    )
    return {getattr(t, "tool_name", "") for t in surface.tools}


class _Desk:
    """A desk over the in-memory outbox, with a fake worker attached."""

    def __init__(self, *, attach: bool = True) -> None:
        self.store = FakeComputeJobStore()
        self.worker = _FakeWorker()
        self.now = NOW
        self.desk = SubagentDesk(jobs=self.store, clock=lambda: self.now)
        if attach:
            self.desk.attach(self.worker)
        self.registry = ToolRegistry(subagent=self.desk)

    def tools(self, conversation_id: str = ROOM, **kw: Any) -> dict[str, Any]:
        return _specs(self.registry, conversation_id=conversation_id, **kw)


BRIEF = {
    "objective": "turn the 8 planning sheets into 8 mobile documents",
    "deliverable": "8 published documents, each read back",
    "decisions": [{"question": "keep the layout?", "answer": "rebuild it", "by": "Miles Novak"}],
    "inputs": ["inbox/sheets.zip"],
    "document_ids": [],
}


async def _create(rig: _Desk, conversation_id: str = ROOM, **over: Any) -> dict:
    bind_tag_id("tag-9")
    return await rig.tools(conversation_id)["subagent_create"](**{**BRIEF, **over})


# -- create ------------------------------------------------------------------------------


async def test_create_writes_the_row_with_the_full_brief_and_the_turns_room_facts():
    rig = _Desk()
    out = await _create(rig)
    assert out["status"] == "started" and out["id"]
    assert "later" in out["note"]
    row = await rig.store.get(UUID(out["id"]))
    assert row is not None
    assert (row.kind, row.state, row.tag_id) == (SUBAGENT_KIND, ComputeJobState.PENDING, "tag-9")
    assert (row.platform, row.conversation_id, row.conversation_type) == (
        PLATFORM, ROOM, "group")
    assert row.requester_person_id == MILES.person_id         # the tagger, from the turn
    assert row.backend_job_id == "bj1" and row.created_at == NOW
    assert dict(row.payload) == {
        "objective": BRIEF["objective"], "deliverable": BRIEF["deliverable"],
        "decisions": [{"question": "keep the layout?", "answer": "rebuild it",
                       "by": "Miles Novak"}],
        "inputs": ["inbox/sheets.zip"], "document_ids": [],
    }
    # and the worker was launched with the same typed brief and the same room
    (request,) = rig.worker.launched
    assert isinstance(request.payload, SubagentBrief)
    assert request.payload.as_payload() == dict(row.payload)
    assert (request.platform, request.conversation_id, request.requester_person_id,
            request.tag_id) == (PLATFORM, ROOM, "miles", "tag-9")


def test_a_model_cannot_name_the_room_or_the_requester():
    """The room, the requester, the tag and the time are code's, so none is a parameter."""
    params = set(inspect.signature(
        _Desk().tools()["subagent_create"]).parameters)
    assert params == {"objective", "deliverable", "decisions", "inputs", "document_ids"}
    for name in ("subagent_list", "subagent_read", "subagent_cancel"):
        assert set(inspect.signature(_Desk().tools()[name]).parameters) <= {"id"}


async def test_create_returns_at_once_and_never_awaits_the_run():
    rig = _Desk()

    async def never_ends(*, request: LaunchRequest) -> str:
        rig.worker.launched.append(request)
        return "bj1"                       # a launch returns an id; the job runs on, unawaited

    rig.worker.launch = never_ends         # type: ignore[method-assign]
    started = time.monotonic()
    out = await asyncio.wait_for(_create(rig), 2.0)
    assert out["status"] == "started" and time.monotonic() - started < 1.0
    row = await rig.store.get(UUID(out["id"]))
    assert row is not None and row.state is ComputeJobState.PENDING


@pytest.mark.parametrize("over", [
    {"objective": "   "},
    {"deliverable": ""},
    {"inputs": ["../outside.txt"]},
    {"inputs": ["/etc/passwd"]},
    {"inputs": [""]},
    {"document_ids": ["not-a-uuid"]},
    {"decisions": [{"question": "q", "answer": "", "by": "Miles"}]},
    {"decisions": ["keep the layout"]},
])
async def test_a_bad_brief_is_refused_and_nothing_starts(over):
    rig = _Desk()
    out = await _create(rig, **over)
    assert out["status"] == "bad_arguments" and out["note"]
    assert rig.worker.launched == []
    assert await rig.store.pending(limit=10) == []


async def test_create_is_unavailable_when_no_worker_is_attached_or_it_would_not_launch():
    unattached = _Desk(attach=False)
    out = await _create(unattached)
    assert (out["status"] == "unavailable" and "nothing was started" in out["note"].lower()) \
        or out["status"] == "unavailable"
    assert await unattached.store.pending(limit=10) == []

    refusing = _Desk()
    refusing.worker.refuse = True
    out = await _create(refusing)
    assert out["status"] == "unavailable"
    assert await refusing.store.pending(limit=10) == []


async def test_a_row_that_cannot_be_written_stops_the_worker_it_launched():
    rig = _Desk()

    async def boom(job: Any) -> None:
        raise RuntimeError("db down")

    rig.store.create = boom                # type: ignore[method-assign]
    out = await _create(rig)
    assert out["status"] == "unavailable"
    assert rig.worker.cancelled == ["bj1"]


async def test_create_without_a_requester_starts_nothing():
    rig = _Desk()
    tools = rig.tools(tagger=None)
    out = await tools["subagent_create"](**BRIEF)
    assert out["status"] == "unavailable"
    assert rig.worker.launched == []


# -- list / read / cancel: this room only ------------------------------------------------


async def test_list_read_and_cancel_see_only_this_room():
    rig = _Desk()
    mine = (await _create(rig, ROOM))["id"]
    theirs = (await _create(rig, OTHER_ROOM))["id"]

    listed = await rig.tools(ROOM)["subagent_list"]()
    assert listed["status"] == "ok"
    assert [j["id"] for j in listed["jobs"]] == [mine]      # positive control: it sees its own

    read = await rig.tools(ROOM)["subagent_read"](id=mine)
    assert read["status"] == "ok" and read["id"] == mine

    # another room's id is an unknown id, never "forbidden": its existence is not revealed
    unknown = await rig.tools(ROOM)["subagent_read"](id="00000000-0000-0000-0000-000000000000")
    for tool in ("subagent_read", "subagent_cancel"):
        foreign = await rig.tools(ROOM)[tool](id=theirs)
        assert foreign["status"] == "nothing_found", tool
        assert foreign == await rig.tools(ROOM)[tool](id="00000000-0000-0000-0000-000000000000")
    assert unknown["status"] == "nothing_found"
    assert theirs not in repr(foreign)
    # the other room's job is untouched and its worker was never stopped
    assert (await rig.store.get(UUID(theirs))).state is ComputeJobState.PENDING
    assert rig.worker.cancelled == []

    # the other room's own tools do see it
    assert (await rig.tools(OTHER_ROOM)["subagent_read"](id=theirs))["status"] == "ok"


async def test_an_empty_room_lists_nothing_found_and_a_malformed_id_reads_nothing_found():
    rig = _Desk()
    assert (await rig.tools()["subagent_list"]())["status"] == "nothing_found"
    assert (await rig.tools()["subagent_read"](id="nope"))["status"] == "nothing_found"


async def test_read_shows_live_progress_while_it_runs_and_no_link_after():
    rig = _Desk()
    job_id = (await _create(rig))["id"]
    rig.worker.live["bj1"] = SimpleNamespace(running=True, rounds=7, last_tool="computer_run")
    rig.now = NOW + timedelta(seconds=95)
    running = await rig.tools()["subagent_read"](id=job_id)
    assert (running["state"], running["rounds"], running["last_tool"], running["age_seconds"]) == (
        "running", 7, "computer_run", 95)
    assert "report" not in running

    # the worker ended; the poller has not delivered yet
    rig.worker.live["bj1"] = SimpleNamespace(running=False, rounds=9, last_tool="document_publish")
    assert (await rig.tools()["subagent_read"](id=job_id))["state"] == "finishing"

    # delivered: the row carries the report and ids, and the read shows no link
    doc = "0b9e1c9e-6f0e-4f43-9a52-3d1f5a4e7a10"
    await rig.store.claim(UUID(job_id))
    await rig.store.mark(
        UUID(job_id), ComputeJobState.DONE, completed_at=NOW, result_report="Made 1 document.",
        result_document_ids=(doc,),
    )
    done = await rig.tools()["subagent_read"](id=job_id)
    assert done["state"] == "done" and done["report"] == "Made 1 document."
    assert [d["document_id"] for d in done["documents"]] == [doc]
    assert "http" not in repr(done)


async def test_a_failed_job_reads_its_reason():
    rig = _Desk()
    job_id = (await _create(rig))["id"]
    await rig.store.claim(UUID(job_id))
    await rig.store.mark(
        UUID(job_id), ComputeJobState.FAILED, completed_at=NOW,
        error="it ran out of rounds before it finished", failure_reason="limit_turns",
    )
    out = await rig.tools()["subagent_read"](id=job_id)
    assert (out["state"], out["failure"]) == ("failed", "limit_turns")


async def test_cancel_ends_it_cancelled_once_and_is_idempotent():
    rig = _Desk()
    job_id = (await _create(rig))["id"]
    first = await rig.tools()["subagent_cancel"](id=job_id)
    assert (first["status"], first["state"]) == ("ok", "cancelled")
    row = await rig.store.get(UUID(job_id))
    assert (row.state, row.failure_reason) == (ComputeJobState.CANCELLED, "cancelled")
    assert rig.worker.cancelled == ["bj1"]
    again = await rig.tools()["subagent_cancel"](id=job_id)
    assert (again["status"], again["state"]) == ("already_present", "cancelled")
    assert rig.worker.cancelled == ["bj1"]                  # the worker was stopped once
    # and the poller has nothing left to deliver for it
    assert await rig.store.pending(limit=10) == []


async def test_cancel_leaves_a_job_the_poller_is_already_delivering_alone():
    rig = _Desk()
    job_id = (await _create(rig))["id"]
    assert await rig.store.claim(UUID(job_id))               # the poller took it first
    out = await rig.tools()["subagent_cancel"](id=job_id)
    assert (out["status"], out["state"]) == ("already_present", "finishing")
    assert rig.worker.cancelled == []


# -- where the tools exist ---------------------------------------------------------------


def test_the_tools_are_absent_unless_the_sub_agent_is_enabled():
    off = ToolRegistry()
    assert _surface_names(off) & SUBAGENT_TOOLS == set()
    on = _Desk().registry
    assert _surface_names(on) >= SUBAGENT_TOOLS             # positive control: it builds them
    # the Relevance Gate's digest offers what a TAG can serve; a window nobody tagged cannot
    # reach these, so it is never told she can answer from them
    assert "subagent_read" not in on.capability_digest()


def test_the_tools_are_tag_turn_only():
    registry = _Desk().registry
    assert _surface_names(registry) >= SUBAGENT_TOOLS
    assert _surface_names(registry, fire_turn=True) & SUBAGENT_TOOLS == set()
    for adaptor in ("cognition", "proactive"):
        assert _surface_names(registry, dream_adaptor=adaptor) & SUBAGENT_TOOLS == set()
    assert _surface_names(registry, subagent_job="j1") & SUBAGENT_TOOLS == set()


async def test_a_closure_built_off_the_tag_path_refuses_to_start_a_job():
    """Defence in depth: even a spec set built for a fire turn (the scope aside) will not
    launch, because the closure reads the same provenance."""
    rig = _Desk()
    out = await rig.tools(fire_turn=True)["subagent_create"](**BRIEF)
    assert out["status"] == "unavailable" and rig.worker.launched == []


def test_the_worker_cannot_hold_a_sub_agent_tool_even_over_a_registry_that_has_them(tmp_path):
    rig = _Rig(tmp_path)
    registry = _Desk().registry
    assert SUBAGENT_TOOLS <= (NEVER | NOT_LOOKUPS)
    assert granted_names(registry) & SUBAGENT_TOOLS == set()
    source = SubagentToolSource(
        registry=registry, store=_Rows(), persons=_Persons(), honcho=FakeMemoryStore(),
        metrics=rig.metrics, rosters={PLATFORM: FakeRosterSource(groups={ROOM: ()})},
    )
    held = {
        t.tool_name for t in source.build(
            job_id="j1", platform=PLATFORM, conversation_id=ROOM, conversation_type="group",
            requester_person_id="miles",
        )
    }
    assert held and held & SUBAGENT_TOOLS == set()


# -- end to end --------------------------------------------------------------------------


async def test_create_runs_a_worker_and_the_poller_delivers_with_the_documents_links(
    tmp_path, doctrine_store,
):
    """create -> the real backend runs a scripted worker that publishes a document -> the real
    poller delivers through the real fire path -> the row is `done` with the report and ids,
    and the link the worker's document has survives R-100 into the sent text."""
    rig = _Rig(tmp_path)
    service = DocumentService(
        store=rig.documents_store, records=_Rows(), media=_media(), media_window=50,
        room_ids=lambda platform, cid: frozenset({cid}),
        is_person=lambda person_id: person_id == "miles", public_url=PUBLIC,
    )
    desk = SubagentDesk(jobs=FakeComputeJobStore(), clock=lambda: NOW, documents=service)
    model = _Script([
        ("tool", "document_publish", {"spec": _doc("Sheet 1")}),
        ("text", "Published 1 document."),
    ])
    backend = rig.backend(model)
    desk.attach(backend)
    registry = ToolRegistry(subagent=desk, documents=service)

    bind_tag_id("tag-e2e")
    started = await _specs(registry)["subagent_create"](**{**BRIEF, "inputs": []})
    assert started["status"] == "started"
    row = await desk._jobs.get(UUID(started["id"]))
    assert row is not None and row.state is ComputeJobState.PENDING

    result = await _until_done(backend, row.backend_job_id)
    assert result.report == "Published 1 document."
    (document_id,) = result.document_ids
    link = (await service.read(
        platform=PLATFORM, conversation_id=ROOM, document_id=UUID(document_id))).url
    assert link.startswith(PUBLIC + "/documents/")

    adapter = _FireAdapter()
    reply = f"All done: {link}"
    path = _fire_path(
        doctrine_store=doctrine_store, persons=Persons(by_person={"miles": MILES}, index={}),
        metrics=Metrics(), adapter=adapter, registry=ToolRegistry(documents=service),
        runner=FakeAgentRunner([
            ScriptedTurn(tool_calls=[("send_reply", {"text": reply})], final_text=reply),
        ]),
    )
    poller = ComputeJobPoller(
        store=desk._jobs, backends={SUBAGENT_KIND: backend}, speak=path.speak,
        slots=ConversationSlots(maxlen=16, spawn=lambda c: None), metrics=Metrics(),
        tick_seconds=1.0, clock=lambda: NOW, documents=service,
    )
    await poller.tick()

    assert adapter.texts and link in adapter.texts[0]          # R-100: the link survived
    done = await desk._jobs.get(row.id)
    assert done is not None and done.state is ComputeJobState.DONE
    assert done.result_report == "Published 1 document."
    assert done.result_document_ids == (document_id,)
    assert done.trace_id is None or isinstance(done.trace_id, str)
    read = await _specs(registry)["subagent_read"](id=started["id"])
    assert read["state"] == "done" and read["documents"][0]["document_id"] == document_id
    assert read["documents"][0]["title"] and "http" not in repr(read)


def _media() -> Any:
    from nora.testing import FakeMediaObjectStore

    return FakeMediaObjectStore((), fail=False)


