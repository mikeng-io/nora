"""Unit binding through the real tool factory; room provenance remains separate."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.computer import computer_scope, computer_unit_scope
from nora.domain.computer_context import ComputerContextCaps
from nora.domain.computer_leases import ComputerLeases, lease_key
from nora.engagement.computer_context import ComputerContextReader
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputerEventStore,
    FakeComputerSessionStore,
    FakeRosterSource,
    FakeSandboxRuntime,
)

ROOMS = {
    ("signal", "s"): "team",
    ("discord", "d"): "team",
    ("whatsapp", "w"): "team",
    ("signal", "private"): "separate",
}


@pytest.fixture
def kit(tmp_path):
    identity = tmp_path / "identity.toml"
    identity.write_text('[persons.miles]\nfirst_name = "Miles"\n')
    persons = load_persons(identity)
    runtime, sessions = FakeSandboxRuntime(), FakeComputerSessionStore()
    events = FakeComputerEventStore(sessions=sessions)
    leases, browser = ComputerLeases(live_wait_seconds=0.02), FakeBrowserRunner()

    def tools(platform, cid, *, job="", background=False, kind="group"):
        room = Room(
            platform=platform,
            conversation_id=cid,
            persons=persons,
            roster_source=FakeRosterSource(groups={cid: ()}),
            self_ids=frozenset(),
        )
        specs = build_tool_specs(
            store=None,
            conversation_id=cid,
            conversation_type=kind,
            persons=persons,
            honcho=None,
            deliverer=None,
            metrics=Metrics(),
            room=room,
            transit_source=None,
            blob_store=FakeBlobStore(),
            computer=runtime,
            computer_events=events,
            computer_sessions=sessions,
            computer_leases=leases,
            browser=browser,
            unit_for=lambda p, c: ROOMS.get((p, c)),
            question="work",
            subagent_job=job,
            dream_adaptor="test" if background else "",
        )
        return {s.name: s.call for s in specs if s.name.startswith("computer_")}

    return tools, runtime, events, sessions, leases, browser


def test_the_unit_key_hashes_the_exact_domain_prefixed_policy_name():
    assert computer_unit_scope("team") == hashlib.sha256(b"unit:team").hexdigest()[:32]
    assert len({computer_unit_scope(u) for u in ("team", "Team", "team ", "組")}) == 4
    assert computer_unit_scope("team") != computer_scope("signal", "team")


async def test_signal_discord_and_whatsapp_share_one_unit_but_private_room_has_its_own(kit):
    tools, runtime, events, sessions, _, _ = kit
    signal, discord, whatsapp = tools("signal", "s"), tools("discord", "d"), tools("whatsapp", "w")
    await signal["computer_write"](path="report.txt", content="first")
    assert (await discord["computer_read"](path="report.txt"))["content"] == "first"
    await whatsapp["computer_edit"](path="report.txt", old="first", new="second")
    assert (await signal["computer_read"](path="report.txt"))["content"] == "second"
    private = tools("signal", "private")
    assert (await private["computer_read"](path="report.txt"))["status"] == "nothing_found"
    listed = await discord["computer_session_list"]()
    assert listed["status"] == "ok" and len(listed["sessions"]) == 1
    shared = [
        s for s in sessions.sessions.values() if s.workspace_scope == computer_unit_scope("team")
    ]
    assert len(shared) == 1 and shared[0].platform == "signal" and shared[0].conversation_id == "s"
    assert {
        (e.platform, e.conversation_id) for e in events.events if e.tool == "computer_edit"
    } == {("whatsapp", "w")}
    assert runtime.files_by_scope[computer_unit_scope("team")]["report.txt"] == b"second"
    assert (await private["computer_session_list"]())["sessions"][0]["objective"] == "work"
    for call in signal.values():
        assert not {"scope", "unit", "platform", "conversation_id"} & set(
            inspect.signature(call).parameters
        )


@pytest.mark.parametrize(
    "platform,cid,kind", [("signal", "unknown", "group"), ("signal", "s", "direct")]
)
async def test_unresolved_rooms_and_private_dms_never_get_a_group_workspace(
    kit, platform, cid, kind
):
    tools, runtime, _, sessions, _, _ = kit
    calls = tools(platform, cid, kind=kind)
    assert (await calls["computer_run"](command="true"))["status"] == "unavailable"
    assert (await calls["computer_browse"](objective="anything"))["status"] == "unavailable"
    assert runtime.run_calls == [] and sessions.sessions == {}


async def test_different_sessions_in_one_unit_cannot_split_the_lease(kit):
    tools, runtime, events, _, leases, _ = kit
    held = await leases.acquire(lease_key(computer_unit_scope("team"), uuid4()), live=True)
    assert held is not None
    try:
        assert (await tools("discord", "d")["computer_run"](command="blocked"))[
            "status"
        ] == "unavailable"
        assert (
            await tools("whatsapp", "w", background=True)["computer_write"](
                path="blocked", content="x"
            )
        )["status"] == "unavailable"
        assert (await tools("discord", "d")["computer_browse"](objective="blocked"))[
            "status"
        ] == "unavailable"
        assert (await tools("signal", "private")["computer_run"](command="other"))["status"] == "ok"
        assert (await tools("signal", "s", job="job-1")["computer_run"](command="worker"))[
            "status"
        ] == "ok"
    finally:
        leases.release(held)
    assert [c["command"] for c in runtime.run_calls] == ["other", "worker"]
    job_rows = [e for e in events.events if e.actor == "subagent" and e.tool == "computer_lease"]
    assert job_rows[0].request["key"] == "job:job-1"
    assert (job_rows[0].platform, job_rows[0].conversation_id) == ("signal", "s")


async def test_browser_and_context_bind_the_same_unit_as_the_tools(kit):
    tools, runtime, events, sessions, _, browser = kit
    for platform, cid in [("signal", "s"), ("discord", "d"), ("whatsapp", "w")]:
        assert (await tools(platform, cid)["computer_browse"](objective="tab"))["status"] == "ok"
    assert {c[0] for c in browser.calls} == {computer_unit_scope("team")}
    reader = ComputerContextReader(
        sessions=sessions,
        events=events,
        runtime=runtime,
        caps=ComputerContextCaps(),
        clock=lambda: datetime.now(UTC),
        metrics=Metrics(),
        unit_for=lambda p, c: ROOMS.get((p, c)),
    )
    signal, discord, private = await asyncio.gather(
        reader.read(platform="signal", conversation_id="s"),
        reader.read(platform="discord", conversation_id="d"),
        reader.read(platform="signal", conversation_id="private"),
    )
    assert signal is not None and discord is not None and private is None
    assert signal.open_sessions[0].session.id == discord.open_sessions[0].session.id
    assert await reader.read(platform="signal", conversation_id="unknown") is None


async def test_migrated_recent_audit_keeps_unit_context_visible_after_the_lapsed_window(kit):
    tools, runtime, events, sessions, _, _ = kit
    await tools("signal", "s")["computer_run"](command="legacy work")
    [session] = sessions.sessions.values()
    now = datetime.now(UTC)
    old = computer_scope("signal", "s")
    sessions.sessions[session.id] = replace(
        session,
        scope=old,
        status="expired",
        expired_at=now - timedelta(hours=25),
        expired_reason="idle",
        stopped_at_detail={
            "workspace_scope": computer_unit_scope("team"),
            "resume_prefix": f"rooms/{old}/",
        },
    )
    events.events = [replace(e, scope=old) for e in events.events]
    reader = ComputerContextReader(
        sessions=sessions,
        events=events,
        runtime=runtime,
        caps=ComputerContextCaps(),
        clock=lambda: now,
        metrics=Metrics(),
        unit_for=lambda p, c: ROOMS.get((p, c)),
    )
    view = await reader.read(platform="discord", conversation_id="d")
    assert view is not None and len(view.runs) == 1 and view.machine_read
    assert view.runs[0].command == "legacy work"
    assert await reader.read(platform="signal", conversation_id="private") is None
