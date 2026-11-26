"""`!!!member` room commands through `EngagementPipeline` — the model-free
occasion's slot contract (docs/design/managed-identity-access/management.md
«In-room management», the F1 finding of the first room review).

The slot contract is the tag path's own: admission's RUN_NOW acquires the
conversation slot and EVERY admitted exit — complete, refused, malformed,
faulted or cancelled — releases it exactly once, or whatever queued
behind the command is stranded on a held slot. `RoomManagement` below is
the REAL invocation over the in-memory managed fakes — the same object
`composition.build_dependencies` hands `room_commands=`, driven the way
`tests/unit/test_room_management.py` drives the effect itself. The
pipeline-building helpers come from `test_engagement_pipeline`, the
module that owns the slot guarantee.
"""

from __future__ import annotations

import asyncio

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedPerson,
    MembershipState,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagementRequest, SendResult
from nora.domain.tag_trace import TagTrace
from nora.people import RoomManagement
from nora.people.app.managed import ManagedServices
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)
from tests.unit.test_engagement_pipeline import (
    G1,
    ScriptedRunner,
    _build_pipeline,
    _depth,
    _drain,
    _FakeAdapter,
    _held,
    _invoke_tool,
    _reply_turns,
    _req,
    _request,
)

CMD_OP = "+85290000101"
CMD_TARGET = "+85290000102"
CMD_ROOM = Scope(ScopeKind.ROOM, room_scope_id("signal", "g1"))


def _room_management():
    """`RoomManagement` over the shared managed fakes — `g1` a known scope."""
    bindings = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={room_scope_id("signal", "g1"): ("unit-a", False)},
        units={"unit-a": "ws-main"},
        workspaces={"ws-main"},
    )
    registry = FakeIdentityRegistry()
    services = ManagedServices(
        bindings, backend, topology, registry, FakeLinkRequests()
    )
    return RoomManagement(services), bindings, backend, registry


async def _room_operator(
    bindings: FakeBindingStore,
    backend: FakeAuthorityBackend,
    registry: FakeIdentityRegistry,
) -> None:
    """The command sender as the room's operator (`grant_manage_members`)."""
    await bindings.register(
        person=ManagedPerson(
            person_id="person_op", lifecycle=PersonLifecycle.ACTIVE),
        account=ManagedAccount(
            account_id="acct-op", person_id="person_op", platform="signal",
            identifiers=frozenset(
                {Identifier(platform="signal", kind="phone", value=CMD_OP)}),
            state=AccountState.BOUND,
        ))
    registry.identities["person_op"] = {
        "id": "kratos-op", "state": "active", "traits": {}}
    await backend.put_relation(
        scope=CMD_ROOM, relation="grant_manage_members", person_id="person_op")


def _command(
    body: str = "!!!member register",
    *,
    sender: str = CMD_OP,
    mentions: frozenset[str] = frozenset({CMD_TARGET}),
    tag_id: str = "cmd-1",
) -> EngagementRequest:
    """The request the dispatcher builds for a fresh `!!!member`."""
    return EngagementRequest(
        message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id=sender, sender_name="Op", timestamp=1, body=body, raw={},
            mentions=mentions),
        decision=EngageDecision(engage=False, reason="room_command"),
        tag_id=tag_id, occasion="room_command",
    )


class _GatedAdapter(_FakeAdapter):
    """A send parked on a latch — a transport call in flight while the test
    reads what queued behind the held slot."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.send_entered = asyncio.Event()
        self.send_release = asyncio.Event()

    async def send(self, **kw) -> SendResult:
        self.send_entered.set()
        await self.send_release.wait()
        return await super().send(**kw)


class _ExplodingAdapter(_FakeAdapter):
    async def send(self, **kw) -> SendResult:
        raise RuntimeError("transport blew up mid-send")


class _BodyEcho:
    """Replies `reply-to-<body>` through the real `send_reply` tool — one
    reply per model invoke, derived from the tag's own message so two
    queued tags stay distinguishable in delivery order."""
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, **kwargs):
        self.calls += 1
        body = kwargs["ctx"].message.body
        tools = {t.tool_name: t for t in kwargs["tools"]}
        await _invoke_tool(tools["send_reply"], text=f"reply-to-{body}")
        return "end_turn", "", TagTrace(
            tag_id=kwargs["ctx"].tag_id,
            conversation_id=kwargs["ctx"].message.conversation_id,
            messages=(), transcript=(), outcome="send_reply", created_at=0)


async def test_an_admitted_room_command_releases_its_slot_and_runs_what_queued_behind_it():
    """The queued-followup guard: revert the command path's `finally`
    release and this goes red — the held slot strands the queued tag."""
    management, bindings, backend, registry = _room_management()
    await _room_operator(bindings, backend, registry)
    adapter = _GatedAdapter()
    runner = ScriptedRunner(_reply_turns("queued answer"))
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)

    command = asyncio.create_task(pipeline.run(_command()))
    await asyncio.wait_for(adapter.send_entered.wait(), timeout=5)
    # Admitted and mid-delivery: the slot is held, so a tag arriving now queues.
    await pipeline.run(_request(tag_id="queued"))
    assert _held(pipeline) and _depth(pipeline) == 1

    adapter.send_release.set()
    await command
    await _drain(background)

    # The command's own reply went first, the queued tag's answer second —
    # and only the tag ever invoked the model.
    assert adapter.texts == [
        "registered — they're a member of this room now", "queued answer"]
    assert len(runner.calls) == 1
    assert not _held(pipeline) and _depth(pipeline) == 0
    # The effect really applied — this was the real invocation.
    members = await bindings.members_of(CMD_ROOM)
    assert any(m.state is MembershipState.MEMBER for m in members)


async def test_a_malformed_room_command_replies_its_usage_and_releases_the_slot():
    management, _b, _be, _r = _room_management()
    adapter = _FakeAdapter()
    runner = ScriptedRunner(_reply_turns())
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)
    await pipeline.run(_command("!!!member", mentions=frozenset()))
    await _drain(background)
    assert adapter.texts == [
        "try `!!!member register <account or @who>` to register one account, "
        "or `!!!member add @who` / `!!!member remove @who` for room membership"]
    assert runner.calls == []
    assert not _held(pipeline) and _depth(pipeline) == 0


async def test_a_refused_room_command_delivers_its_refusal_and_releases_the_slot():
    # Nobody granted: the real invocation's own `sender-unregistered` DENIED.
    management, _b, _be, _r = _room_management()
    adapter = _FakeAdapter()
    runner = ScriptedRunner(_reply_turns())
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)
    await pipeline.run(_command("!!!member add", sender="+852999"))
    await _drain(background)
    assert adapter.texts == [
        "your account isn't registered, so I can't take room commands from you"]
    assert runner.calls == []
    assert not _held(pipeline) and _depth(pipeline) == 0


async def test_a_faulting_room_command_is_silent_and_still_releases_the_slot():
    management, bindings, backend, registry = _room_management()
    await _room_operator(bindings, backend, registry)
    adapter = _ExplodingAdapter()
    runner = ScriptedRunner(_reply_turns("after the fault"))
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)
    await pipeline.run(_command())
    # The fault stayed silent AND freed the slot — the next tag ran its
    # model turn and tried to answer (every send blows up here, its own
    # included).
    await pipeline.run(_request(tag_id="after"))
    await _drain(background)
    assert adapter.texts == []
    assert len(runner.calls) == 1
    assert not _held(pipeline) and _depth(pipeline) == 0


async def test_a_durable_room_command_leaves_the_release_to_its_worker():
    """The durable half of the slot contract: `run_durable` hands the stage
    a `disposition` — the marker that the CALLER already holds the slot
    (`WorkRunner._dispatch`'s `try_acquire`) and owns its release. A stage
    that ALSO released would pop one queued tag AND free the slot it
    believes held; the worker's matching `finally` would then pop a SECOND
    — two follow-ups overlapped. Revert the `if disposition is None` guard
    and `run_durable` returns with the queue already drained."""
    management, bindings, backend, registry = _room_management()
    await _room_operator(bindings, backend, registry)
    adapter = _FakeAdapter()
    runner = _BodyEcho()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)
    key = ("signal", "g1")

    # The worker's side of the seam: `_dispatch` try_acquires the slot
    # before `run_durable`, and its own finally owns the one release.
    pipeline._slots.acquire(key)
    await pipeline.run(_req(body="first tag", tag_id="queued-1"))
    await pipeline.run(_req(body="second tag", tag_id="queued-2"))
    assert _depth(pipeline) == 2

    disposition = await pipeline.run_durable(_command())
    assert disposition == "send_reply"
    assert adapter.texts == ["registered — they're a member of this room now"]
    # The stage did NOT release: the slot is still held and BOTH queued
    # tags still wait — nothing dispatched mid-run.
    assert _held(pipeline) and _depth(pipeline) == 2
    assert runner.calls == 0

    # The worker's single release hands the slot to exactly one follow-up;
    # its own stage release dispatches the next — never overlapping.
    pipeline._slots.release_and_dispatch(key)
    await _drain(background)
    assert adapter.texts[-2:] == ["reply-to-first tag", "reply-to-second tag"]
    assert runner.calls == 2
    assert not _held(pipeline) and _depth(pipeline) == 0


async def test_a_cancelled_room_command_still_releases_its_slot():
    management, bindings, backend, registry = _room_management()
    await _room_operator(bindings, backend, registry)
    adapter = _GatedAdapter()
    runner = ScriptedRunner(_reply_turns("after the cancel"))
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, room_commands=management)
    command = asyncio.create_task(pipeline.run(_command()))
    await asyncio.wait_for(adapter.send_entered.wait(), timeout=5)
    await pipeline.run(_request(tag_id="queued"))
    command.cancel()
    adapter.send_release.set()
    # The command path swallows to silence exactly like `_run_turn`'s own
    # `except BaseException` — the await returns rather than raising.
    await command
    await _drain(background)
    assert adapter.texts == ["after the cancel"]
    assert not _held(pipeline) and _depth(pipeline) == 0


async def test_a_durable_tag_leaves_the_slot_release_to_its_worker():
    """`run_durable` hands `_run_turn` a `disposition` — the marker that
    the bridge worker already holds the slot and owns its release. A turn
    that ALSO released would dispatch the queued tag AND free the slot it
    believes held, so the worker's matching `finally` would pop a SECOND
    queued tag — two follow-ups overlapped."""
    adapter = _FakeAdapter()
    runner = ScriptedRunner(_reply_turns("the answer"))
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner)

    pipeline._slots.acquire(G1)   # the worker's try_acquire
    await pipeline.run(_req(body="queued tag", tag_id="queued"))
    assert _depth(pipeline) == 1

    disposition = await pipeline.run_durable(
        _req(body="durable tag", tag_id="durable"))
    assert disposition == "send_reply"
    # Exactly one model invoke and one reply — the queued tag still waits
    # and the slot is still held: the stage did not release the worker's
    # acquisition. A second release here is the double-release reviewed.
    assert adapter.texts == ["the answer"]
    assert len(runner.calls) == 1
    assert _held(pipeline) and _depth(pipeline) == 1

    pipeline._slots.release_and_dispatch(G1)
    await _drain(background)
    assert adapter.texts == ["the answer", "the answer"]
    assert len(runner.calls) == 2
    assert not _held(pipeline) and _depth(pipeline) == 0
