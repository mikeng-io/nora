"""`WorkspaceReader.read_workspace_bytes` (nora/agent/computer_workspace.py,
S3a): the audited read other Nora code (the documents path) uses instead of
calling `SandboxRuntime.fetch` directly.

Pinned: the scope is derived from the room given, so another room's file is
out of reach; two `computer_events` rows on every path, under the calling
operation's name and the actor's; no read without its `started` row; the
lease is taken (a background reader never waits on a busy room); the size
and digest are Nora's; and it never raises.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import pytest

from nora.agent.computer_workspace import WorkspaceReader
from nora.domain.computer import BytesResult, SandboxStatus, computer_scope
from nora.domain.computer_leases import ComputerLeases, lease_key
from nora.obs.metrics import Metrics
from nora.testing import (
    FakeComputerEventStore,
    FakeComputerSessionStore,
    FakeSandboxRuntime,
)

ROOM, OTHER = "g-docs", "g-other"
SPEC = b'{"type": "document", "blocks": []}'
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


class _CountingRuntime(FakeSandboxRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.fetches = 0

    async def fetch(self, *, scope, path, max_bytes):
        self.fetches += 1
        return await super().fetch(scope=scope, path=path, max_bytes=max_bytes)


class _TamperingRuntime(FakeSandboxRuntime):
    async def fetch(self, *, scope, path, max_bytes):
        got = await super().fetch(scope=scope, path=path, max_bytes=max_bytes)
        return BytesResult(got.status, data=got.data + b"x", sha256=got.sha256)


class _CapIgnoringRuntime(FakeSandboxRuntime):
    async def fetch(self, *, scope, path, max_bytes):
        return await super().fetch(scope=scope, path=path, max_bytes=10**9)


class _RaisingRuntime(FakeSandboxRuntime):
    async def fetch(self, *, scope, path, max_bytes):
        raise RuntimeError("boom")


def _reader(runtime=None, events=None, leases=None):
    runtime = runtime if runtime is not None else FakeSandboxRuntime()
    events = events if events is not None else FakeComputerEventStore()
    return WorkspaceReader(runtime=runtime, events=events, leases=leases,
                           metrics=Metrics(), clock=lambda: NOW), runtime, events


def _put(runtime, room, path, data):
    runtime.files_by_scope.setdefault(computer_scope("signal", room), {})[path] = data


async def _read(reader, room=ROOM, path="projects/p/doc.json", max_bytes=1000, actor="tag"):
    return await reader.read_workspace_bytes(
        platform="signal", conversation_id=room, path=path, max_bytes=max_bytes,
        actor=actor, operation="document_publish",
    )


def _rows(events):
    assert [e.phase for e in events.events] == ["started", "finished"], events.events
    assert events.events[0].operation_id == events.events[1].operation_id
    return events.events


async def test_a_read_returns_the_bytes_and_writes_two_rows_under_the_operation():
    reader, runtime, events = _reader()
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader)
    assert (got.status, got.data) == (SandboxStatus.OK, SPEC)
    assert got.sha256 == hashlib.sha256(SPEC).hexdigest()
    started, finished = _rows(events)
    assert (started.tool, started.actor, started.scope) == (
        "document_publish", "tag", computer_scope("signal", ROOM))
    assert started.request == {"path": "projects/p/doc.json", "max_bytes": 1000}
    assert (finished.status, finished.result["sha256"]) == ("ok", got.sha256)


async def test_the_scope_is_the_rooms_so_another_rooms_file_is_out_of_reach():
    reader, runtime, events = _reader()
    _put(runtime, OTHER, "projects/p/doc.json", SPEC)
    got = await _read(reader, room=ROOM)
    assert got.status is SandboxStatus.NOTHING_FOUND
    assert _rows(events)[1].status == "nothing_found"


async def test_no_read_without_its_started_row():
    events = FakeComputerEventStore()
    events.fail = {"started"}
    runtime = _CountingRuntime()
    reader, _, _ = _reader(runtime=runtime, events=events)
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader)
    assert got.status is SandboxStatus.UNAVAILABLE
    assert runtime.fetches == 0


async def test_a_digest_that_disagrees_is_unavailable():
    reader, runtime, events = _reader(runtime=_TamperingRuntime())
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader)
    assert got.status is SandboxStatus.UNAVAILABLE and got.data == b""
    assert _rows(events)[1].status == "unavailable"


async def test_the_cap_holds_against_a_runtime_that_ignores_it():
    reader, runtime, events = _reader(runtime=_CapIgnoringRuntime())
    _put(runtime, ROOM, "projects/p/doc.json", b"x" * 20)
    got = await _read(reader, max_bytes=10)
    assert got.status is SandboxStatus.DENIED and got.data == b""
    assert _rows(events)[1].status == "denied"


async def _session_held_like_computer_write(sessions, leases):
    """What `computer_write` holds in a room with an open session: the
    SESSION's key (`lease_key`), not the scope's."""
    session, _ = await sessions.continue_or_open(
        platform="signal", conversation_id=ROOM, scope=computer_scope("signal", ROOM),
        objective="write the spec", actor="tag", tag_id="t", now=NOW,
    )
    return await leases.acquire(lease_key(computer_scope("signal", ROOM), session.id),
                                live=True)


async def test_a_background_reader_defers_while_a_write_holds_the_session():
    """FALSIFIER (review S1): the reader locks the key the tools lock. On the
    scope's key it read mid-write while `computer_write` held the session's."""
    leases, sessions = ComputerLeases(), FakeComputerSessionStore()
    held = await _session_held_like_computer_write(sessions, leases)
    runtime = _CountingRuntime()
    reader = WorkspaceReader(runtime=runtime, events=FakeComputerEventStore(), leases=leases,
                             sessions=sessions, metrics=Metrics(), clock=lambda: NOW)
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader, actor="dreamer:proactive")
    assert (got.status, got.reason) == (SandboxStatus.UNAVAILABLE, "busy")
    assert runtime.fetches == 0
    leases.release(held)
    assert (await _read(reader, actor="dreamer:proactive")).status is SandboxStatus.OK


async def test_a_live_reader_waits_for_the_write_then_reads():
    leases, sessions = ComputerLeases(live_wait_seconds=2.0), FakeComputerSessionStore()
    held = await _session_held_like_computer_write(sessions, leases)
    runtime = _CountingRuntime()
    reader = WorkspaceReader(runtime=runtime, events=FakeComputerEventStore(), leases=leases,
                             sessions=sessions, metrics=Metrics(), clock=lambda: NOW)
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    reading = asyncio.create_task(_read(reader, actor="tag"))
    await asyncio.sleep(0.05)
    assert not reading.done() and runtime.fetches == 0  # waiting on the write
    leases.release(held)
    got = await reading
    assert got.status is SandboxStatus.OK and runtime.fetches == 1


async def test_a_read_never_opens_a_session():
    sessions = FakeComputerSessionStore()
    runtime = FakeSandboxRuntime()
    reader = WorkspaceReader(runtime=runtime, events=FakeComputerEventStore(),
                             leases=ComputerLeases(), sessions=sessions, metrics=Metrics(),
                             clock=lambda: NOW)
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    assert (await _read(reader)).status is SandboxStatus.OK
    assert sessions.sessions == {}


@pytest.mark.parametrize("max_bytes", [0, -1])
async def test_a_cap_that_is_not_positive_is_denied(max_bytes):
    """Refused before anything: the computer is never asked, nothing is
    recorded as a read."""
    reader, runtime, events = _reader(runtime=_CountingRuntime())
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader, max_bytes=max_bytes)
    assert got.status is SandboxStatus.DENIED and got.data == b""
    assert runtime.fetches == 0 and events.events == []


async def test_it_never_raises_and_still_writes_both_rows():
    reader, runtime, events = _reader(runtime=_RaisingRuntime())
    _put(runtime, ROOM, "projects/p/doc.json", SPEC)
    got = await _read(reader)
    assert got.status is SandboxStatus.UNAVAILABLE
    assert _rows(events)[1].status == "unavailable"


async def test_the_computer_off_is_unavailable_and_still_audited():
    events = FakeComputerEventStore()
    reader = WorkspaceReader(runtime=None, events=events, leases=None, metrics=Metrics())
    got = await _read(reader)
    assert got.status is SandboxStatus.UNAVAILABLE
    assert _rows(events)[1].result["reason"] == "the computer is off here"
