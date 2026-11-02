"""`EngagementPipeline.run` — the exit-path guarantees: the in-flight lock is released on every exit
path, the latency/overhead histograms are observed on every path, and the
outer `except BaseException` (not `except Exception`) is what keeps R-5's
silence guarantee unconditional.

Admission is acquired by `nora.engagement.stages.admission` and released by this pipeline's own
`finally` — see both modules' docstrings for the split. A `ScriptedRunner` stands in for the real
`AgentRunner`, replaying scripted tool calls against the REAL tools/guards, the same shape
`tests/integration/harness.py`'s `FakeLLM` uses.

`_invoke_tool` is imported from `nora.testing.scripted_agent` rather than
re-written here: calling `tool(**kwargs)` directly would raise `TypeError` on
any argument the tool does not declare, a failure mode production does not have,
because Strands drops out-of-schema arguments during pydantic validation
(`nora.testing.scripted_agent._accepted_arguments`).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from nora.agent.mcp import McpToolGroup
from nora.agent.registry import ToolRegistry
from nora.app.runtime import BackgroundTasks
from nora.doctrine.loader import Doctrine
from nora.domain.decision_log import DecisionLogRecorder
from nora.domain.errors import PartialReplyError
from nora.domain.models import (
    ConversationTarget,
    NormalizedMessage,
    PersonRef,
)
from nora.domain.ports import (
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    PlatformIdentity,
    ReactionResult,
    Recollection,
    SendResult,
)
from nora.domain.tag_trace import TagTrace
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import indicate
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy, permissive_policy
from nora.tag import Debounce
from nora.testing import (
    FakeAgentRunner,
    FakeRecordStore,
    FakeRosterSource,
    InMemoryDecisionLogStore,
    ScriptedTurn,
)
from nora.testing.scripted_agent import _invoke_tool

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
PERSONS = Persons(by_person={"miles": MILES}, index={("signal", "+852111"): "miles"})
LIMITS = {"turns": 4, "output_tokens": 400, "total_tokens": 24000}

G1 = ("signal", "g1")
G2 = ("signal", "g2")


# R-10's lock and R-30's queue are not private attributes of the pipeline
# — they are the injected `ConversationSlots` both it and the heartbeat fire
# path share (`nora/engagement/slots.py`). These three read that collaborator
# through its PUBLIC surface; the one private hop (`pipeline._slots`) is spelled
# out here once instead of at each assertion.
def _held(pipeline: EngagementPipeline, key: tuple[str, str] = G1) -> bool:
    return pipeline._slots.held(key)


def _depth(pipeline: EngagementPipeline, key: tuple[str, str] = G1) -> int:
    return pipeline._slots.depth(key)


def _waiting(pipeline: EngagementPipeline) -> int:
    """Conversations with at least one tag still queued."""
    return len(pipeline._slots)


async def _wait_for(
    predicate, *, timeout_seconds: float = 5.0, message: str
) -> None:
    """Poll `predicate()` until true, bounded by a DEADLOCK GUARD rather than
    a measurement.

    Used instead of a fixed `asyncio.sleep` followed by a state assertion: a
    fixed window is a guess about how long admission takes to reach
    "mid-invoke", true on an idle loop and false on a loaded one.
    """
    deadline = time.monotonic() + timeout_seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail(message)
        await asyncio.sleep(0.001)


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


class _FakeAdapter:
    platform = "signal"

    def __init__(self, *, account: str = "+852000", succeed: bool = True) -> None:
        self._account = account
        self.sent: list[tuple[ConversationTarget, str]] = []
        self.sent_mentions: list[tuple] = []
        self._succeed = succeed

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(
        self, *, target: ConversationTarget, text: str,
        mentions: tuple = (), styles: tuple = (), preview: object | None = None,
        attachments: tuple = (),
        quote: object | None = None,
    ) -> SendResult:
        self.sent.append((target, text))
        self.sent_mentions.append(tuple(mentions))
        return SendResult(ok=self._succeed)

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


class _FakeMemory:
    def __init__(self) -> None:
        self.fed: list[tuple[str, str, str]] = []
        self.peers: list[str] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str, **_route: str | None) -> None:
        self.peers.append(peer_id)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str,
                   metadata: dict | None = None) -> bool:
        self.fed.append((peer_id, text, conversation_id))
        return True

    async def peer_card(self, *, peer_id: str, **_route: str | None) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


class _RecordingMemory(_FakeMemory):
    """`_FakeMemory` plus the one endpoint the `about` tool speaks to.

    Unknown peers are reachable-with-nothing, exactly as the live Honcho is
    (200 and an empty string, never a 404) — a fake that 404'd instead would
    make «no such peer» and «could not ask» look like the same event.
    """

    def __init__(self) -> None:
        super().__init__()
        # (observer, observed, search_query) — the `about` tool reads Nora's
        # model of a person, `(observer=nora, observed=person)`.
        self.queries: list[tuple[str, str, str]] = []

    async def representation(
        self, *, peer_id, search_query, max_conclusions=5, target=None, **_route,
    ):
        observed = target if target is not None else peer_id
        self.queries.append((peer_id, observed, search_query))
        return Recollection(reachable=True, conclusions=("Miles is from Ridgeline",))


class _FakeDoctrineStore:
    def get(self, *, applies_to: str = "tag") -> Doctrine:
        return Doctrine(
            sections=(), text="<nora_doctrine>be helpful</nora_doctrine>",
            doctrine_text="be helpful", sha256="deadbeef", runtime_context="", loaded_at=0.0,
        )


class _LLMState:
    def __init__(self) -> None:
        self.last_success_at: float | None = None


class _FakeTagTraceStore:
    """Records every `TagTrace` `save` is called with, in call order — the
    unit-level double for `nora.infra.sql.tag_traces.SqlTagTraceStore`.
    No `recent()`: nothing here reads it back through the pipeline, that is the
    integration test's job against the real store."""

    def __init__(self) -> None:
        self.saved: list[TagTrace] = []

    async def save(self, trace: TagTrace) -> None:
        self.saved.append(trace)


class ScriptedRunner(FakeAgentRunner):
    """`nora.testing.FakeAgentRunner`, plus this file's `calls` bookkeeping.

    It reuses the shared double rather than replaying the guard by hand: the
    guard's before-model hook is what ENDS a turn and what relaxes
    `tool_choice`, so a double that never fired it would let a multi-message
    scenario "pass" against a mechanism it never exercised.

    `calls` is the only thing this subclass adds, and it is wired through the
    shared double's own `on_call` seam.
    """

    def __init__(
        self, turns: list[ScriptedTurn] | None = None, *,
        delay: float = 0.0, raises: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        super().__init__(turns, delay=delay, raises=raises, on_call=self.calls.append)


class _CriticalFault(BaseException):
    """A `BaseException`-only fault (not an `Exception` subclass) — proves
    the pipeline's outer `except BaseException` is load-bearing, not
    decorative. See `test_a_base_exception_only_fault_is_absorbed_silently`
    and this module's mandatory probe."""


class _FaultingContextAssembler:
    async def assemble(self, *, request: EngagementRequest):
        raise _CriticalFault("kaboom")


def _msg(conversation_id: str = "g1", sender_id: str = "+852111") -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id=conversation_id, conversation_type="group",
        sender_id=sender_id, sender_name="Miles", timestamp=1, body="nora.000 幾點食飯", raw={},
    )


def _request(msg: NormalizedMessage | None = None, tag_id: str = "tag-1") -> EngagementRequest:
    return EngagementRequest(
        message=msg or _msg(), decision=EngageDecision(engage=True, reason="trigger_text"),
        tag_id=tag_id,
    )


def _reply_turns(text: str = "今晚七點半得") -> list[ScriptedTurn]:
    return [ScriptedTurn(tool_calls=[("send_reply", {"text": text})], final_text=text)]


@pytest.mark.parametrize("cancel_save", [False, True])
async def test_trace_retention_precedes_dispatch_and_cancel_releases(cancel_save):
    entered, finish = asyncio.Event(), asyncio.Event()

    class BlockingStore(_FakeTagTraceStore):
        async def save(self, trace):
            if not entered.is_set():
                entered.set()
                await finish.wait()
            await super().save(trace)

    store = BlockingStore()
    pipeline, _, _, _, background = _build_pipeline(tag_trace_store=store)
    first = asyncio.create_task(pipeline.run(_request(tag_id="first")))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await pipeline.run(_request(tag_id="queued"))
    assert _held(pipeline) and _depth(pipeline) == 1
    if cancel_save:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    else:
        finish.set()
        await first
        assert store.saved[0].tag_id == "first"
    await _drain(background)
    assert not _held(pipeline) and _depth(pipeline) == 0
    assert store.saved[-1].tag_id == "queued"


def _build_pipeline(
    *,
    runner=None,
    context_assembler=None,
    memory=None,
    audit=None,
    adapter=None,
    adapters=None,
    metrics=None,
    llm_state=None,
    timeout_seconds: float = 5.0,
    debounce_seconds: float = 0.0,
    clock=None,
    background=None,
    reactions: bool = True,
    roster_source=None,
    tool_registry=None,
    queue_max: int = 16,
    policy=None,
    tag_trace_store=None,
    debrief_wiring=None,
    decisions=None,
    defer_tools: bool = False,
    turn_clock=None,
):
    # Always a real (fake) store, never `None`: every scenario below can read
    # `pipeline._tag_trace_store.saved` (mirroring how `pipeline._slots` is
    # already read directly elsewhere in this file) without every OTHER test
    # in the suite having to pass one in just to keep the collaborator non-None.
    tag_trace_store = tag_trace_store if tag_trace_store is not None else _FakeTagTraceStore()
    audit = audit if audit is not None else FakeRecordStore()
    memory = memory if memory is not None else _FakeMemory()
    metrics = metrics if metrics is not None else Metrics()
    background = background if background is not None else BackgroundTasks()
    context_assembler = context_assembler or TranscriptInjection(
        audit=audit, memory=memory, persons=PERSONS, context_window=10, metrics=metrics,
    )
    if adapters is None:
        the_adapter = adapter or _FakeAdapter()
        adapters = {the_adapter.platform: the_adapter}
    extra = {}
    pipeline = EngagementPipeline(
        adapters=adapters,
        audit=audit,
        memory=memory,
        persons=PERSONS,
        doctrine_store=_FakeDoctrineStore(),
        context_assembler=context_assembler,
        runner=runner or ScriptedRunner(_reply_turns()),
        tool_registry=tool_registry or ToolRegistry(),
        debounce=Debounce(debounce_seconds, clock=clock or _FakeClock()),
        canary="CANARY-XYZ",
        display_name="Nora",
        ai_peer="nora",
        context_window=10,
        timeout_seconds=timeout_seconds,
        limits=LIMITS,
        defer_tools=defer_tools,
        metrics=metrics,
        spawn=background.spawn,
        llm_state=llm_state,
        reactions=reactions,
        rosters={"signal": roster_source} if roster_source else {},
        slots=ConversationSlots(maxlen=queue_max, spawn=background.spawn),
        policy=policy if policy is not None else permissive_policy(),
        tag_trace_store=tag_trace_store,
        debrief_wiring=debrief_wiring,
        decisions=decisions,
        clock=turn_clock,
        **extra,
    )
    return pipeline, metrics, audit, memory, background


async def _drain(background: BackgroundTasks) -> None:
    """Wait for every background task the pipeline's injected `spawn` (i.e.
    `background.spawn`) has launched.

    Deliberately `asyncio.sleep(0)`, not `asyncio.gather(*background._tasks)`:
    a task's own coroutine can finish (and be reported `done()`) before its
    `add_done_callback` discard runs — done-callbacks are always delivered
    via `call_soon`, one loop tick later, even for a callback registered on
    an ALREADY-done future. `gather()` on already-done futures resolves
    without yielding back to the loop at all, so `while bg: await
    gather(*bg)` can spin forever never letting that pending callback fire.
    `sleep(0)` unconditionally yields once per iteration, which does.
    """
    while len(background):
        await asyncio.gather(*list(background._tasks), return_exceptions=True)
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# DEC-71 / R-40 — the conversation's policy.toml `tools` allowlist is resolved
# and enforced HERE, at assembly (`ToolRegistry.build(..., allowed_mcp=...)`),
# never at egress. An MCP tool a conversation is not allowlisted for must
# never reach `Agent(tools=...)` for that tag at all.
# ---------------------------------------------------------------------------


def _policy_allowing(*, conversation_id: str = "g1", tools: frozenset[str] = frozenset()) -> Policy:
    convo = Conversation(
        platform="signal", ids=frozenset({conversation_id}), persons=None, tools=tools,
    )
    return Policy(conversations=(convo,), _by_id={("signal", conversation_id): convo})


def _registry_with_pulse() -> ToolRegistry:
    groups = {
        "pulse": McpToolGroup(budget=5, tools=(_FakeMcpTool("pulse_server_status"),)),
    }
    return ToolRegistry(mcp_tools=groups)


class _FakeMcpTool:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name


async def test_unauthorized_conversation_never_receives_pulse_tools():
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        tool_registry=_registry_with_pulse(),
        policy=_policy_allowing(tools=frozenset()),  # not authorized for pulse
    )
    await pipeline.run(_request())
    await _drain(background)
    delivered_tools = pipeline._runner.calls[-1]["tools"]
    assert not any(t.tool_name == "pulse_server_status" for t in delivered_tools)


async def test_authorized_conversation_receives_pulse_tools():
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        tool_registry=_registry_with_pulse(),
        policy=_policy_allowing(tools=frozenset({"pulse"})),
    )
    await pipeline.run(_request())
    await _drain(background)
    delivered_tools = pipeline._runner.calls[-1]["tools"]
    assert any(t.tool_name == "pulse_server_status" for t in delivered_tools)


# ---------------------------------------------------------------------------
# `[llm] defer_tools` on the tag path.
# ---------------------------------------------------------------------------


def _prompt_text(call: dict) -> str:
    return "\n".join(
        block.get("text", "")
        for message in call["pre_fill"] for block in message["content"]
    )



async def test_the_clock_line_states_the_injected_clock():
    """The tag turn's clock line reads the injected clock, as the fire path's
    does and as the `now` tool does, so a turn frozen at a past instant (an
    eval sandbox) states one time everywhere.

    Revert `now=` on the pipeline's `build_prefill` call and this goes RED."""
    frozen = datetime(2026, 10, 11, 1, 5, tzinfo=UTC)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        turn_clock=lambda: frozen,
    )
    await pipeline.run(_request())
    await _drain(background)

    assert "Sunday 11 October 2026" in _prompt_text(pipeline._runner.calls[-1])

async def test_deferring_ships_the_hot_set_the_loader_and_the_catalogue():
    """The switch, end to end through the pipeline: the model is handed the
    hot tools plus `load_tools`, the prompt carries `<tool_catalogue>`, and
    the loop gets one more turn for the load. All three come from ONE
    `ToolSurface`, so they cannot disagree about what was deferred."""
    from nora.agent.tools import GRAPH_MEMORY_TOOLS, HOT_TOOLS, LOADER_TOOL

    pipeline, _metrics, _audit, _memory, background = _build_pipeline(defer_tools=True)
    await pipeline.run(_request())
    await _drain(background)
    call = pipeline._runner.calls[-1]
    names = {t.tool_name for t in call["tools"]}
    # no index wired: the Honcho memory surface (DEC-316)
    assert names == ((HOT_TOOLS - GRAPH_MEMORY_TOOLS) | {LOADER_TOOL})
    assert "<tool_catalogue" in _prompt_text(call)
    assert "weather" in _prompt_text(call)  # a shelved tool is still NAMED
    assert call["limits"]["turns"] == LIMITS["turns"] + 1


async def test_a_spoken_tag_is_handed_the_voice_tools_and_a_typed_one_is_not():
    """DEC-256 through the pipeline: the tag path derives the occasion from
    the message's own `voice` flag, so a spoken turn's model call carries the
    tools declared `hot_on` voice and the same words typed do not."""
    import dataclasses

    from nora.agent.surface import DISCORD_ONLY_TOOLS
    from nora.agent.tools import GRAPH_MEMORY_TOOLS, HOT_TOOLS, LOADER_TOOL

    seen: list = []

    class _Spy(ToolRegistry):
        def surface(self, **kw):
            seen.append(kw.get("occasion"))
            return super().surface(**kw)

    handed = {}
    for voice in (True, False):
        pipeline, _metrics, _audit, _memory, background = _build_pipeline(
            defer_tools=True, tool_registry=_Spy(),
            adapters={"discord": _DiscordFakeAdapter(account="555000111")},
        )
        spoken = dataclasses.replace(_discord_msg(), voice=voice)
        await pipeline.run(_request(spoken))
        await _drain(background)
        handed[voice] = {t.tool_name for t in pipeline._runner.calls[-1]["tools"]}
    assert seen == ["voice", None]
    assert handed[False] == (HOT_TOOLS - GRAPH_MEMORY_TOOLS) | {LOADER_TOOL}
    assert {"play_music", "set_music_volume"} <= handed[True]
    assert handed[True] - handed[False] <= DISCORD_ONLY_TOOLS


async def test_without_deferral_nothing_changes():
    """`defer_tools = false`: every admitted schema,
    no loader, no catalogue block, the configured turn ceiling."""
    from nora.agent.tools import LOADER_TOOL

    pipeline, _metrics, _audit, _memory, background = _build_pipeline()
    await pipeline.run(_request())
    await _drain(background)
    call = pipeline._runner.calls[-1]
    names = {t.tool_name for t in call["tools"]}
    assert LOADER_TOOL not in names
    assert "weather" in names
    assert "<tool_catalogue" not in _prompt_text(call)
    assert call["limits"] == LIMITS


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_a_successful_tag_delivers_releases_the_lock_and_feeds_the_exchange():
    adapter = _FakeAdapter()
    llm_state = _LLMState()
    pipeline, metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter, llm_state=llm_state,
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["今晚七點半得"]
    assert _held(pipeline) is False
    assert llm_state.last_success_at is not None
    # DEC-36: the tagger's own words, under the tagger's peer, and nothing
    # else (no `"[Nora]"` marker of a two-speaker transcript in the fed text).
    assert memory.fed == [("nora", "今晚七點半得", "g1")]
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_an_unmapped_sender_delivers_with_no_honcho_feed():
    pipeline, _metrics, _audit, memory, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns()),
    )

    await pipeline.run(_request(_msg(sender_id="+852999999")))
    await _drain(background)

    assert memory.fed == []
    assert _held(pipeline) is False
    # `feedback.run`'s own gate — `if sent and
    # ctx.tagger is not None` — is exactly what leaves `memory.fed == []`
    # above for this UNMAPPED sender. The `TagTrace` save must NOT inherit
    # that gate; this is the one scenario in the suite that actually
    # exercises `ctx.tagger is None` on a turn that still delivered, so it is
    # the only place independence from the `ctx.tagger` half of the gate (as
    # opposed to the `sent` half, which the silent/vetoed/timeout/llm_error
    # tests already cover) can be proven.
    assert len(pipeline._tag_trace_store.saved) == 1
    assert pipeline._tag_trace_store.saved[0].outcome == "send_reply"


# ---------------------------------------------------------------------------
# The feedback feed is spawned through the INJECTED tracker, not a private set
# the pipeline rolls itself — see nora/engagement/pipeline.py's `spawn`
# constructor parameter, which the composition roots pass as `background.spawn`.
# Without that wiring, `App.run`'s
# `finally: await deps.background.cancel_all()` would
# neither see nor cancel a still-running feedback feed on shutdown.
# ---------------------------------------------------------------------------


async def test_the_feedback_feed_is_spawned_through_the_injected_tracker():
    class _SlowMemory(_FakeMemory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def feed(self, *, peer_id: str, text: str, conversation_id: str = "",
                       metadata: dict | None = None) -> bool:
            self.started.set()
            await asyncio.sleep(10)   # never completes within the test
            self.fed.append((peer_id, text, conversation_id))
            return True

    memory = _SlowMemory()
    background = BackgroundTasks()
    pipeline, _metrics, _audit, _memory, _background = _build_pipeline(
        memory=memory, background=background,
    )

    await pipeline.run(_request())
    # Bounded, not a bare `await memory.started.wait()`: if the feed is never
    # actually spawned, `.started` never fires and an unbounded wait would hang
    # the suite instead of failing it.
    await asyncio.wait_for(memory.started.wait(), timeout=2.0)

    # The feedback feed is still in flight — it must be tracked by the
    # injected `background`, the one `deps.background.cancel_all()` reaches.
    #
    # Filtered to tasks that have not finished, rather than `len(background)`:
    # the progress-reaction lifecycle (`nora.engagement.stages.indicate`) is
    # spawned through the SAME injected tracker, deliberately — it must be
    # cancellable at shutdown for exactly the reason this test exists. Against
    # this test's default `_FakeAdapter`, which has no `react` at all, both of
    # those tasks complete on their first scheduling, but a completed task is
    # only discarded from `_tasks` one `call_soon` tick later (see `_drain`'s
    # docstring), so a raw count here would be racing that callback rather than
    # asserting anything about the feed. The discrimination is unchanged: if the
    # feed were not spawned through this tracker, `memory.started` would never
    # fire and the `wait_for` above would fail first; if it were spawned and
    # immediately awaited, nothing would be pending here at all.
    pending = [task for task in background._tasks if not task.done()]
    assert len(pending) == 1

    await background.cancel_all()

    assert len(background) == 0
    assert memory.fed == [], "cancel_all() must reach the spawned feed and stop it mid-await"


# ---------------------------------------------------------------------------
# R-5 / R-10: the lock is released and the group never sees an error
# ---------------------------------------------------------------------------


async def test_a_timeout_releases_the_lock_and_counts_invocation_timeout():
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=adapter, runner=ScriptedRunner(_reply_turns(), delay=5.0),
        timeout_seconds=0.05,
    )

    await pipeline.run(_request())

    assert adapter.sent == []
    assert _held(pipeline) is False
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_an_llm_error_releases_the_lock_and_counts_llm_error():
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=adapter, runner=ScriptedRunner(raises=RuntimeError("omniroute down")),
    )

    await pipeline.run(_request())

    assert adapter.sent == []
    assert _held(pipeline) is False
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 1
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_a_max_tokens_partial_reply_is_salvaged_not_silent():
    """A `max_tokens` cut-off with real prose is delivered, not dropped.

    Strands appends the partial message to `agent.messages` before raising, `factory.invoke`
    carries the words up as `PartialReplyError`, and the pipeline must
    deliver them — silence here would be the failure the group sees.
    """
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner(raises=PartialReplyError("密碼寫咗喺島上面，去搵下啦")),
    )

    await pipeline.run(_request())

    assert adapter.texts == ["密碼寫咗喺島上面，去搵下啦"]
    assert _held(pipeline) is False
    # NOT a silent failure: the words were delivered.
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 0
    assert metrics.get("nora_salvaged_replies_total") == 1
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_a_max_tokens_cut_off_with_no_prose_stays_silent():
    """A `max_tokens` cut-off whose partial message carried only tool-use
    blocks has nothing to salvage — `PartialReplyError.partial_text` is empty
    and the turn stays silent (R-5), exactly like a generic `llm_error`."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner(raises=PartialReplyError("")),
    )

    await pipeline.run(_request())

    assert adapter.sent == []
    assert _held(pipeline) is False
    assert metrics.get("nora_salvaged_replies_total") == 0
    assert metrics.get("nora_silent_failures_total", reason="llm_error") == 0


async def test_a_base_exception_only_fault_is_absorbed_silently():
    """R-5: even a fault that is not an `Exception` (a bare `BaseException`
    subclass) must never propagate out of `run` or leave the lock held."""
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        context_assembler=_FaultingContextAssembler(),
    )

    await pipeline.run(_request())   # must not raise

    assert _held(pipeline) is False
    assert metrics.get("nora_silent_failures_total", reason="handler_error") == 1
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_a_reply_written_without_calling_send_reply_is_recovered():
    """DEC-93: a turn that ends in prose with no `send_reply` is recovered.

    The deployment's route can ACCEPT `tool_choice="required"` AND IGNORE IT, so
    prose does not distinguish "chose not to speak" from "spoke without using
    the tool". The words are recovered and delivered rather than discarded."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert [t for _target, t in adapter.sent] == ["I would have said this"]
    assert metrics.get("nora_recovered_replies_total") == 1
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 0
    # NOT the canned fallback — that is for a budget CUT-OFF, and these are
    # different failures with different fixes.
    assert metrics.get("nora_loop_exhausted_fallbacks_total") == 0
    # And it DOES feed Honcho, unlike the fallback. The exclusion at
    # `Deliverer.deliver` is `source != "fallback"` on purpose: the fallback is
    # a canned apology nobody wrote, so feeding it would teach her self-model a
    # sentence she never composed. A recovered reply is the opposite — these
    # ARE her words, she simply failed to route them through the tool, and the
    # group read them. Excluding them would make her memory of her own speech
    # incomplete exactly where it is most confusing to her later.
    assert [t for _peer, t, _conv in memory.fed] == ["I would have said this"]


async def test_a_partial_multi_message_turn_is_not_topped_up_by_the_recovery():
    """DEC-93's interaction with DEC-40.

    `send_reply`'s budget is 3, so a turn may send one message and then write
    its SECOND as prose. The recovery must not deliver that: the group already
    heard from her, and appending an unrouted second message would turn a
    partial reply into a duplicate-looking one. `not deliverer.delivered` is the
    guard — flip it to `not deliverer.called` and this reds."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(
            tool_calls=[("send_reply", {"text": "first"})],
            final_text="and this second bit never went through the tool",
        )]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert [t for _target, t in adapter.sent] == ["first"]
    assert metrics.get("nora_recovered_replies_total") == 0


async def test_a_heads_up_then_work_then_prose_answer_is_recovered(monkeypatch):
    """DEC-294's heads-up meets DEC-93. A line sent with `more=true` IS
    delivered, so the recovery's «nothing delivered» gate must not drop an
    answer she then wrote as prose while the turn reads as `replied`. The
    heads-up owes its answer until a send lands after the work.

    `reply_still_owed` is pinned here: this double hands hooks the tool's raw
    return, not Strands' envelope, so the guard cannot read `sent` in it. The
    guard's own reading is proven on the real Strands loop in
    `tests/integration/test_guards_reply_more.py`; this proves the pipeline
    honours it."""
    from nora.agent.guards import ReplyGuard

    monkeypatch.setattr(ReplyGuard, "reply_still_owed", property(lambda _self: True))
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(
            tool_calls=[("send_reply", {"text": "let me check", "heads_up": True}),
                        ("now", {})],
            final_text="it is Thursday",
        )]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert [t for _target, t in adapter.sent] == ["let me check", "it is Thursday"]
    assert metrics.get("nora_recovered_replies_total") == 1


async def test_a_refused_send_reply_followed_by_prose_is_still_recovered():
    """Which predicate guards the recovery — `delivered` or `called` — and the
    only case that tells them apart.

    A `send_reply` the Deliverer REFUSED (here R-18, an E.164 in the text)
    leaves `called=True` and `delivered=False`. The group heard nothing, so the
    recovery SHOULD fire; guarding on `called` would suppress it and leave her
    silent on exactly the turn where she tried twice.

    Safe by construction rather than by luck: the recovered text goes through
    the same `deliver`, so whatever refused the first attempt gets to refuse
    this one too — this is a second chance through the guards, never around
    them. Swap the guard to `not handle.deliverer.called` and this reds."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(
            # R-18 refuses this INSIDE `deliver` — an E.164 in the reply. It
            # has to be a guard `ReplyGuard.evaluate` does NOT also catch, or
            # the tool call is vetoed before `deliver` is entered and `called`
            # never flips, which is what makes the two predicates distinguishable.
            tool_calls=[("send_reply", {"text": "佢喺 +85298765432 嗰邊"})],
            final_text="the answer she actually wrote",
        )]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert [t for _target, t in adapter.sent] == ["the answer she actually wrote"]
    assert metrics.get("nora_recovered_replies_total") == 1


async def test_a_turn_that_wrote_nothing_at_all_is_still_a_silent_failure():
    """The bound on the recovery, and the reason it cannot swallow the signal.

    `no_send_call` still has to mean something. A model that ends its turn
    having produced neither a tool call NOR any text has genuinely said nothing,
    there is nothing to recover, and that must still be counted — otherwise
    DEC-93 would have replaced a visible failure with an invisible one."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.sent == []
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert metrics.get("nora_recovered_replies_total") == 0
    assert memory.fed == []
    assert _held(pipeline) is False


async def test_the_never_silent_fallback_delivers_when_the_loop_is_cut_off():
    """A turn cut off by a token/turn cap BEFORE `send_reply` must not leave the
    group with SILENCE: Nora delivers one honest fallback line — and, having recovered, the turn
    is NOT also counted a silent failure. One tool call spends the `total_tokens`
    cap, so the next cycle trips `limit_total_tokens` before any reply."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner(
            [ScriptedTurn(tool_calls=[("now", {})], total_tokens=24_000)]
        ),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert len(adapter.sent) == 1                                    # a reply, not a void
    assert metrics.get(
        "nora_loop_exhausted_fallbacks_total", reason="limit_total_tokens"
    ) == 1
    # recovered → NOT a silent failure (the double-count the pre-check ordering avoids)
    assert metrics.get(
        "nora_silent_failures_total", reason="limit_total_tokens"
    ) == 0


async def test_the_fallback_reply_is_not_fed_to_honchos_self_model():
    """The fallback is a canned apology, NOT Nora's authored
    words, so it must NOT feed her Honcho self-model card — only real replies do.
    It still DELIVERED (the group saw it), it is just kept out of `sent_texts`."""
    adapter = _FakeAdapter()
    pipeline, _metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner(
            [ScriptedTurn(tool_calls=[("now", {})], total_tokens=24_000)]
        ),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert len(adapter.sent) == 1        # delivered — the group saw the fallback
    assert memory.fed == []              # but nothing fed to her self-model


# ---------------------------------------------------------------------------
# The placeholder `TagTrace.outcome` `invoke.run` hands back
# (`LiveAgentRunner`/`FakeAgentRunner` both stamp `"send_reply"` unconditionally)
# is CORRECTED here to the turn's real disposition, mirroring
# `deliver.check_outcome`'s own delivered/called split, and the corrected trace
# is unconditionally saved — on every completed turn, not gated on
# `feedback.run`'s own `if sent and ctx.tagger is not None`.
# ---------------------------------------------------------------------------


async def test_a_normal_send_reply_is_saved_with_outcome_send_reply():
    adapter = _FakeAdapter()
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "send_reply"
    assert saved[0].conversation_id == "g1"


async def test_a_recovered_reply_is_saved_with_outcome_recovered():
    adapter = _FakeAdapter()
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "recovered"


async def test_a_silent_turn_is_saved_with_outcome_silent():
    """A turn that produced neither a tool call nor text — genuinely nothing
    to recover (`test_a_turn_that_wrote_nothing_at_all_is_still_a_silent_failure`
    is the metrics-side sibling of this test)."""
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner([ScriptedTurn(final_text="")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "silent"


async def test_a_vetoed_turn_is_saved_with_outcome_vetoed():
    """`called=True, delivered=False` with NO recovery possible — both the
    tool call and the would-be recovery carry the same R-18-tripping text, so
    the guard refuses both attempts (the same fixture shape
    `test_a_refused_send_reply_followed_by_prose_is_still_recovered` uses,
    except the recovery text ALSO leaks here, so nothing ever delivers)."""
    leaking_text = "佢喺 +85298765432 嗰邊"
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner([ScriptedTurn(
            tool_calls=[("send_reply", {"text": leaking_text})],
            final_text=leaking_text,
        )]),
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "vetoed"


async def test_a_never_silent_fallback_delivery_is_saved_with_outcome_send_reply():
    """The canned budget-exhausted fallback is DELIVERED (`deliverer.delivered`
    is True) but is not `"recovered"` — `recovered_delivered` only flips for the
    DEC-93 prose-recovery branch, never the fallback branch, so this lands on
    `"send_reply"`, the same bucket a normal tool-routed reply does. (The
    fallback text itself is never fed to Honcho or attributed to her as
    authored words — see `test_the_fallback_reply_is_not_fed_to_honchos_self_model`
    — but `TagTrace.outcome` only has six values to choose from and none of
    them is "fallback"; this is the documented judgment call, not an oversight.)"""
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(
            [ScriptedTurn(tool_calls=[("now", {})], total_tokens=24_000)]
        ),
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "send_reply"


async def test_a_timeout_is_saved_with_outcome_timeout():
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns(), delay=5.0), timeout_seconds=0.05,
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "timeout"


async def test_an_llm_error_is_saved_with_outcome_llm_error():
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(raises=RuntimeError("omniroute down")),
    )

    await pipeline.run(_request())
    await _drain(background)

    saved = pipeline._tag_trace_store.saved
    assert len(saved) == 1
    assert saved[0].outcome == "llm_error"


async def test_a_fault_before_invoke_run_ever_returns_saves_nothing():
    """The outer `except BaseException` case (a context-assembly fault): no
    `agent.messages`, no transcript window, `invoke.run` never ran — nothing
    to attribute a trace to, so `finally` must not try (`trace` stays `None`,
    the guard clause at the save site skips it)."""
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        context_assembler=_FaultingContextAssembler(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert pipeline._tag_trace_store.saved == []


async def test_a_tag_trace_save_failure_never_takes_down_an_otherwise_successful_turn():
    """Failure containment: a
    `SqlTagTraceStore`-shaped save is SPAWNED (`self._spawn`, `BackgroundTasks`),
    not awaited inline — so a raising store logs (`BackgroundTasks._on_done`)
    and the turn's own delivery, metrics and lock release are all unaffected."""
    class _RaisingTagTraceStore:
        async def save(self, trace: TagTrace) -> None:
            raise RuntimeError("db down")

    adapter = _FakeAdapter()
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, tag_trace_store=_RaisingTagTraceStore(),
    )

    await pipeline.run(_request())
    await _drain(background)   # must not raise

    assert adapter.texts == ["今晚七點半得"]
    assert _held(pipeline) is False


class _FaultingMetricsAfterInvoke:
    """Wraps a real `Metrics`, forwarding every call, EXCEPT the first `inc`
    of `fault_name` — which raises instead. Every existing fault double in
    this file (`_FaultingContextAssembler`, `ScriptedRunner(raises=...)`)
    only reaches BEFORE or DURING `invoke.run` — none can land a fault
    strictly AFTER `invoke.run` returns but BEFORE the pipeline's outcome
    correction (`dataclasses.replace`).
    `"nora_loop_exhausted_fallbacks_total"` is incremented at exactly one
    site in the whole codebase (`pipeline.py`, immediately after the
    never-silent-fallback's `deliver()` call succeeds) — squarely inside that
    window and nowhere else, so faulting only that name cannot also trip any
    OTHER `metrics.inc`/`.observe` call this turn makes."""

    def __init__(self, real: Metrics, *, fault_name: str) -> None:
        self._real = real
        self._fault_name = fault_name
        self._fired = False

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        if name == self._fault_name and not self._fired:
            self._fired = True
            raise RuntimeError("metrics backend down")
        self._real.inc(name, value, **labels)

    def __getattr__(self, attr):
        return getattr(self._real, attr)


async def test_a_crash_after_invoke_run_never_saves_a_stale_placeholder_outcome():
    """A fault landing between `invoke.run` returning and the outcome correction
    must not leave `trace` bound with its PLACEHOLDER `"send_reply"` outcome:
    the `except BaseException` handler absorbs the fault (as R-5 requires) and
    resets `trace`, so the unconditional `finally` save does not persist a turn
    that crashed and delivered nothing as if it had succeeded normally.

    Reuses the never-silent-fallback fixture
    (`test_the_never_silent_fallback_delivers_when_the_loop_is_cut_off`'s
    shape: one tool call that spends the whole `total_tokens` budget, so the
    next cycle trips `limit_total_tokens` before any reply) and faults the
    ONE `metrics.inc` call that fires immediately after that fallback
    delivers — after `invoke.run` has returned, before this turn's outcome is
    ever corrected."""
    metrics = _FaultingMetricsAfterInvoke(
        Metrics(), fault_name="nora_loop_exhausted_fallbacks_total",
    )
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        metrics=metrics,
        runner=ScriptedRunner(
            [ScriptedTurn(tool_calls=[("now", {})], total_tokens=24_000)]
        ),
    )

    await pipeline.run(_request())
    await _drain(background)   # must not raise

    # R-5 held: the outer `except BaseException` absorbed the fault silently.
    assert metrics.get("nora_silent_failures_total", reason="handler_error") == 1
    assert _held(pipeline) is False
    # Nothing saved at all, rather than a stale "send_reply".
    assert pipeline._tag_trace_store.saved == []


async def test_latency_and_overhead_are_observed_on_every_path():
    metrics = Metrics()
    ok_pipeline, _, _, _, ok_background = _build_pipeline(metrics=metrics)
    await ok_pipeline.run(_request(tag_id="t1"))
    await _drain(ok_background)

    timeout_pipeline, _, _, _, _bg2 = _build_pipeline(
        metrics=metrics, runner=ScriptedRunner(_reply_turns(), delay=5.0), timeout_seconds=0.05,
    )
    await timeout_pipeline.run(_request(tag_id="t2"))

    error_pipeline, _, _, _, _bg3 = _build_pipeline(
        metrics=metrics, runner=ScriptedRunner(raises=RuntimeError("boom")),
    )
    await error_pipeline.run(_request(tag_id="t3"))

    fault_pipeline, _, _, _, _bg4 = _build_pipeline(
        metrics=metrics, context_assembler=_FaultingContextAssembler(),
    )
    await fault_pipeline.run(_request(tag_id="t4"))

    assert len(metrics.observations("nora_tag_latency_seconds")) == 4
    assert len(metrics.observations("nora_overhead_seconds")) == 4


# ---------------------------------------------------------------------------
# nora_messages_per_tag
# ---------------------------------------------------------------------------


async def test_a_delivered_tag_observes_one_message():
    """Flat at 1 by construction today: R-2 latches after the first delivery.

    That makes it a baseline: multi-message's predicted failure is that the cap
    becomes a quota always spent in full.
    """
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(metrics=metrics)

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.observations("nora_messages_per_tag") == [1.0]


async def test_a_silent_tag_observes_zero_rather_than_nothing_at_all():
    """A histogram that only records successes cannot answer "how often does
    she say nothing", which is the same question. Every admitted tag
    contributes exactly one observation."""
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        metrics=metrics,
        runner=ScriptedRunner([# DEC-93: a turn that wrote NOTHING. Prose would be RECOVERED and
        # delivered; this test is about a genuinely silent turn.
        ScriptedTurn(final_text="")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.observations("nora_messages_per_tag") == [0.0]


async def test_a_failed_send_observes_zero_not_one():
    """The count is `Deliverer.delivered_count`, which counts what the ADAPTER
    accepted — R-2's latch counts attempts, and reading the latch instead would
    report a message the group never received."""
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        metrics=metrics, adapter=_FakeAdapter(succeed=False),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.observations("nora_messages_per_tag") == [0.0]


async def test_a_tag_that_faults_before_there_is_a_deliverer_still_observes_zero():
    """The `finally` runs on a fault raised inside `context.assemble`, before
    the pipeline has built a `DeliveryHandle` at all. A histogram silently
    missing its most interesting exit path is R-5's failure mode again."""
    metrics = Metrics()
    pipeline, _, _, _, _background = _build_pipeline(
        metrics=metrics, context_assembler=_FaultingContextAssembler(),
    )

    await pipeline.run(_request())

    assert metrics.observations("nora_messages_per_tag") == [0.0]


# ---------------------------------------------------------------------------
# R-10 / R-29: admission owns acquisition; a tag for a busy conversation is
# QUEUED (not dropped) and drained one turn at a time, in arrival order, while
# tags for OTHER conversations still run concurrently.
# ---------------------------------------------------------------------------


class _EchoRunner:
    """Replies through the REAL `send_reply` tool with a reply derived from the
    tagging message, so each tag's reply is distinguishable in delivery order
    and lands in the audit store the NEXT tag's context is assembled from.

    Records the transcript bodies it was handed on every invocation, so a test
    can prove tag 2 saw tag 1's reply — the correctness reason a conversation is
    serialised. Not the same-answer-every-time fake CLAUDE.md forbids: the reply
    varies with the input and the delivery genuinely exercises `Deliverer` +
    `ReplyGuard`."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.seen_transcripts: list[tuple[str | None, ...]] = []

    async def invoke(self, *, tools, guard, ctx, **_kwargs) -> str | None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.seen_transcripts.append(tuple(r.body for r in ctx.transcript))
        args = {"text": f"reply-to-{ctx.message.body}"}
        tools_by_name = {t.tool_name: t for t in tools}
        if guard.evaluate("send_reply", args) is None:
            result = tools_by_name["send_reply"](**args)
            if hasattr(result, "__await__"):
                await result
            guard.record("send_reply")
        return "end_turn"


def _req(*, conversation_id: str = "g1", body: str, tag_id: str, ts: int = 1):
    return _request(
        NormalizedMessage(
            platform="signal", conversation_id=conversation_id, conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=ts, body=body, raw={},
        ),
        tag_id=tag_id,
    )


async def test_three_tags_for_one_conversation_are_answered_in_fifo_order():
    """R-29, the whole point: three people tagging at once each get a reply, in
    arrival order — not one reply and two silences.

    PROBE: make admission `return Admission.REJECTED` (drop) for the in-flight
    branch and only the first reply is delivered."""
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, metrics, _, _, background = _build_pipeline(adapter=adapter, runner=runner)

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )

    await pipeline.run(_req(body="q2", tag_id="t2"))   # queued behind t1
    await pipeline.run(_req(body="q3", tag_id="t3"))   # queued behind t2
    assert _depth(pipeline) == 2

    await first
    await _drain(background)

    assert adapter.texts == ["reply-to-q1", "reply-to-q2", "reply-to-q3"]
    assert _held(pipeline) is False
    assert _waiting(pipeline) == 0   # drained: nothing retained per-conversation
    assert metrics.get("nora_tags_queued_total") == 2
    assert metrics.get("nora_tags_total", platform="signal") == 3


async def test_a_queued_tag_sees_the_earlier_tags_reply_in_its_context():
    """The correctness argument (not politeness) for serialising a conversation:
    tag 2's context is assembled at DEQUEUE time, so tag 1's reply is already in
    the transcript. Two concurrent turns would both reason from a room missing
    the other's reply.

    PROBE: make `ConversationSlots.release_and_dispatch` spawn the queued turn
    WITHOUT waiting for the in-flight one (e.g. dequeue eagerly in `run`) and
    tag 2's transcript no longer contains tag 1's reply."""
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, _, _, _, background = _build_pipeline(adapter=adapter, runner=runner)

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )
    await pipeline.run(_req(body="q2", tag_id="t2"))
    await first
    await _drain(background)

    # invocation 0 is tag 1 (empty transcript), invocation 1 is tag 2 — its
    # context contains tag 1's already-delivered reply.
    assert runner.seen_transcripts[0] == ()
    assert "reply-to-q1" in runner.seen_transcripts[1]


async def test_different_conversations_still_run_concurrently():
    """The queue is PER conversation: two DIFFERENT conversations must not
    serialise behind each other."""
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, _, _, _, background = _build_pipeline(adapter=adapter, runner=runner)

    a = asyncio.create_task(pipeline.run(_req(conversation_id="g1", body="qa", tag_id="ta")))
    b = asyncio.create_task(pipeline.run(_req(conversation_id="g2", body="qb", tag_id="tb")))
    await _wait_for(
        lambda: _held(pipeline, G1) and _held(pipeline, G2),
        message="both conversations were never held concurrently",
    )

    # BOTH in flight at once — neither queued behind the other.
    assert _held(pipeline, G1) is True
    assert _held(pipeline, G2) is True
    assert _waiting(pipeline) == 0

    await asyncio.gather(a, b)
    await _drain(background)

    assert sorted(adapter.texts) == ["reply-to-qa", "reply-to-qb"]
    assert _held(pipeline, G1) is False
    assert _held(pipeline, G2) is False


async def test_a_flood_past_the_queue_bound_is_shed_with_its_own_counter():
    """R-29's bound guards against unbounded growth, not waiting. With a queue
    max of 2, a fourth tag arriving while one runs and two wait is dropped and
    counted distinctly — everything already accepted keeps its place.

    PROBE: remove the `depth >= maximum` check in admission and the fourth tag
    is queued and answered instead of shed."""
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, metrics, _, _, background = _build_pipeline(
        adapter=adapter, runner=runner, queue_max=2,
    )

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )
    await pipeline.run(_req(body="q2", tag_id="t2"))   # queued (depth 0 -> 1)
    await pipeline.run(_req(body="q3", tag_id="t3"))   # queued (depth 1 -> 2)
    await pipeline.run(_req(body="q4", tag_id="t4"))   # queue full -> shed

    assert _depth(pipeline) == 2
    assert metrics.get("nora_tags_dropped_total", reason="queue_full") == 1

    await first
    await _drain(background)

    assert adapter.texts == ["reply-to-q1", "reply-to-q2", "reply-to-q3"]   # not q4
    assert _held(pipeline) is False
    assert _waiting(pipeline) == 0


async def test_a_debounced_tag_is_never_admitted_and_never_releases_a_lock():
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(metrics=metrics, debounce_seconds=30)

    await pipeline.run(_request(tag_id="first"))
    await _drain(background)
    assert _held(pipeline) is False

    await pipeline.run(_request(tag_id="second"))   # same conversation, inside the window

    assert metrics.get("nora_tags_debounced_total") == 1
    assert _held(pipeline) is False


async def test_a_self_tag_is_refused_and_admission_never_runs_the_pipeline():
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=_FakeAdapter(account="+852111"),
    )

    await pipeline.run(_request(_msg(sender_id="+852111")))

    assert _held(pipeline) is False
    assert metrics.get("nora_tags_total") == 0


# ---------------------------------------------------------------------------
# Progress reactions (nora/engagement/stages/indicate.py).
#
# `_FakeAdapter` above deliberately has NO `react` method, so every test before
# this point is already exercising the "platform that cannot react" path — the
# reaction feature is additive to all of them or they would have broken. The
# tests below add an adapter that can.
# ---------------------------------------------------------------------------


class _ReactingAdapter(_FakeAdapter):
    def __init__(
        self, *, react_ok: bool = True, react_delay: float = 0.0,
        react_gate: asyncio.Event | None = None, **kw,
    ) -> None:
        super().__init__(**kw)
        self.reactions: list[tuple[str, str, int, bool]] = []
        self._react_ok = react_ok
        self._react_delay = react_delay
        # `react_gate`, when given, BLOCKS `react()` until the test sets it —
        # a condition, not a duration, for the one test that needs the
        # reaction to still be unresolved no matter how long `pipeline.run`
        # itself takes under load.
        self._react_gate = react_gate

    async def react(
        self, *, target, emoji, to_author, to_timestamp, to_message_id="", remove=False,
    ):
        if self._react_gate is not None:
            await self._react_gate.wait()
        if self._react_delay:
            await asyncio.sleep(self._react_delay)
        self.reactions.append((emoji, to_author, to_timestamp, remove))
        return ReactionResult(ok=self._react_ok)

    @property
    def emojis(self) -> list[str]:
        return [e for e, _, _, remove in self.reactions if not remove]


async def test_accepting_a_tag_reacts_to_the_tagging_message_then_resolves():
    """The feature, end to end through the real pipeline: 👀 on accept, ✅ once
    the reply is out, both aimed at the tagging message's author/timestamp.

    PROBE: delete the `indicate.begin(...)` call in
    `nora/engagement/pipeline.py::_begin_indicator` and this fails."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.emojis == [indicate.ACK, indicate.DONE]
    assert adapter.reactions[0][1:3] == ("+852111", 1)   # tagger, tagging timestamp
    assert adapter.texts == ["今晚七點半得"]
    assert metrics.get("nora_reactions_total", phase="ack", status="sent") == 1.0


async def test_a_queued_tag_gets_its_eyes_promptly_while_it_waits():
    """The cheap independent win: the 👀 fires at ADMISSION, before a tag's turn
    starts — so a person queued behind another sees they were SEEN while they
    wait, instead of the silence a dropped tag would give them.

    PROBE: move `_begin_indicator` inside `_run_admitted` (fire on turn
    start, not admission) and the queued tag's 👀 does not appear until the tag
    ahead of it finishes."""
    adapter = _ReactingAdapter()
    runner = ScriptedRunner(_reply_turns(), delay=0.3)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, runner=runner,
    )

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1", ts=1)))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )
    await pipeline.run(_req(body="q2", tag_id="t2", ts=2))   # queued behind t1

    def _ack_timestamps() -> list[int]:
        return [ts for emoji, _a, ts, remove in adapter.reactions
                if emoji == indicate.ACK and not remove]

    # Wait for the CONDITION (the queued tag's 👀 landing), not for a fixed
    # count of event-loop turns: pumping a fixed number of bare
    # `asyncio.sleep(0)`s is the same "guess how long it takes" shape as a
    # wall-clock sleep, just measured in turns instead of seconds.
    await _wait_for(
        lambda: 2 in _ack_timestamps(),
        message=f"the queued tag's 👀 never landed; reactions={adapter.reactions}",
    )
    # t2 must still be QUEUED when its 👀 lands — that is the property this
    # test is named for (fires at admission, not at turn start). t1's turn
    # holds for 0.3s, several orders of magnitude longer than the 👀 above
    # can take to land, so this is not a race with the wait above.
    assert _depth(pipeline) == 1                                # t2 still queued

    await first
    await _drain(background)
    assert adapter.texts == ["今晚七點半得", "今晚七點半得"]   # both eventually answered


async def test_a_reaction_failure_never_prevents_the_reply():
    """R-5: a reaction is decoration. If every reaction call fails, the reply
    still goes out, the group sees no error, and the failures are counted.

    PROBE: make `indicate._react` re-raise instead of swallowing, and the
    pipeline's spawned tasks start failing while this assertion on `texts`
    still passes — which is why the metric assertion is here too."""
    adapter = _ReactingAdapter(react_ok=False)
    pipeline, metrics, _audit, memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["今晚七點半得"]
    assert memory.fed == [("nora", "今晚七點半得", "g1")]
    assert metrics.get("nora_reactions_total", status="failed") == 3.0
    assert metrics.get("nora_silent_failures_total") == 0.0
    assert _held(pipeline) is False


async def test_a_platform_that_cannot_react_still_replies_normally():
    """A future adapter with no `react` at all — the degradation case. It must
    reply exactly as before and be counted as unsupported, never crash.

    PROBE: replace `getattr(adapter, "react", None)` in `indicate._react` with
    a direct attribute access and this tag fails with AttributeError."""
    adapter = _FakeAdapter()                     # no `react` method at all
    pipeline, metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["今晚七點半得"]
    assert metrics.get("nora_reactions_total", status="unsupported") == 3.0
    assert metrics.get("nora_silent_failures_total") == 0.0


async def test_reactions_do_not_consume_the_reply_budget():
    """R-2/R-2b with reactions live: reactions are not tools and never touch
    `Deliverer`, so they neither consume nor re-arm the reply budget.

    (cap+1) `send_reply` calls still deliver exactly `cap` and the last is
    vetoed — the reaction lifecycle is untouched by any of it.

    The cap is read from the registry, not spelled here: DEC-10/DEC-32 keep one
    copy of that number, so a literal would go stale."""
    cap = ToolRegistry().budgets()["send_reply"]
    sent = [f"msg{i}" for i in range(cap)]
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(tool_calls=[
            ("send_reply", {"text": t}) for t in [*sent, "one too many"]
        ])]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == sent
    assert metrics.get("nora_guard_vetoes_total", rule="R-2") == 1
    assert adapter.emojis == [indicate.ACK, indicate.DONE]


async def test_the_model_cannot_emit_a_reaction_itself():
    """R-7 default-deny still covers the new side effect: `react` was never
    registered as a tool, so a model that invents one is vetoed before
    anything runs, and no reaction is produced by it."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(tool_calls=[
            ("react", {"conversation_id": "g1", "emoji": "\N{PILE OF POO}"}),
            ("send_reply", {"text": "hi"}),
        ])]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_guard_vetoes_total", rule="R-7") == 1
    assert "\N{PILE OF POO}" not in adapter.emojis
    assert adapter.texts == ["hi"]


async def test_a_turn_that_never_replied_resolves_to_a_caution_and_says_nothing():
    """The failure-resolution decision at pipeline level: the model never calls
    `send_reply`, so nothing is delivered — the 👀 must not be left stranded
    (indistinguishable from "still thinking") and must not become a ✅
    (claiming a reply that does not exist). No text reaches the group either
    way, so R-5 is intact.

    PROBE: pass `delivered=True` unconditionally to `indicate.resolve` in the
    pipeline's `finally` and this fails on the ✅."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([# DEC-93: a turn that wrote NOTHING. Prose would be RECOVERED and
        # delivered; this test is about a genuinely silent turn.
        ScriptedTurn(final_text="")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.sent == []                       # nothing said to the group
    assert adapter.emojis == [indicate.ACK, indicate.GAVE_UP]
    assert indicate.DONE not in adapter.emojis
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1


async def test_a_crashed_turn_still_resolves_the_indicator():
    """The 👀 is resolved from the pipeline's `finally`, so even the outer
    `except BaseException` path — which never reaches `check_outcome` at all —
    leaves a terminal state on the message rather than a stranded 👀.

    PROBE: move `indicate.resolve(...)` out of the `finally` and up next to
    `check_outcome`, and this fails."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, context_assembler=_FaultingContextAssembler(),
    )

    await pipeline.run(_request())                  # must not raise
    await _drain(background)

    assert adapter.emojis == [indicate.ACK, indicate.GAVE_UP]
    assert metrics.get("nora_silent_failures_total", reason="handler_error") == 1


async def test_the_reply_does_not_wait_for_the_reaction():
    """Reactions must not delay the reply measurably — not even by a bounded
    amount.

    `gate` is an `asyncio.Event` the fake reaction BLOCKS on inside `react()`,
    and this test never sets it until after the check below. There is no
    delay for a loaded scheduler to race, and the reaction genuinely cannot
    resolve while `gate` is clear — so `adapter.emojis == []`, and the
    indicator's own placement task (`_place`) still tracked in `background`
    and NOT DONE, are CLOCK-FREE proof that `pipeline.run` returned without
    waiting for the reaction to finish. That catches any UNBOUNDED await:
    `_place(indicator)` awaited directly with no timeout would never resolve,
    so `run` itself would never return — and since this repo runs no
    `pytest-timeout`, an unbounded hang here would otherwise hang `make gate`
    forever rather than fail it (fail-open). `pipeline.run` is therefore
    called through `asyncio.wait_for(..., timeout=5.0)`, so that regression
    surfaces as a clean `TimeoutError`, `pytest.fail`ed with a message, in
    about 5s — not a hang.

    It does NOT catch a BOUNDED wait — production could `wait_for(indicator
    .placed.wait(), timeout=1.5)` before replying, time out, and continue:
    `emojis` and the task-tracking check both still pass, because the
    reaction is still exactly as unresolved as before, yet the reply was 1.5s
    late. `elapsed < 1.0` is kept for exactly that: 4x the 0.23s
    wall-clock flake a loaded box shows, so it catches a bounded wait of
    >= 1.0s. It does NOT catch one under 1.0s — nothing in this test does.

    PROBE 1 (unbounded): make `indicate.begin`/`EngagementPipeline
    ._begin_indicator` `await _place(indicator)` directly instead of
    spawning it, and `pipeline.run` never returns — the `wait_for` below
    times out after 5s and this fails on `pytest.fail("pipeline.run awaited
    the reaction")`, not on any assertion further down.
    PROBE 2 (bounded, 1.5s): add `await asyncio.wait_for(indicator.placed
    .wait(), timeout=1.5)` right after `_begin_indicator` in `pipeline.run`
    (suppressing the timeout). `run` returns well inside the 5s ceiling,
    `emojis == []` and the task check both still pass — only `elapsed < 1.0`
    fails."""
    gate = asyncio.Event()   # never set until after the check below
    adapter = _ReactingAdapter(react_gate=gate)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    started = time.perf_counter()
    try:
        await asyncio.wait_for(pipeline.run(_request()), timeout=5.0)
    except TimeoutError:
        pytest.fail("pipeline.run awaited the reaction (#430)")
    elapsed = time.perf_counter() - started

    assert adapter.texts == ["今晚七點半得"]   # replied already
    assert adapter.emojis == []                # reaction cannot have resolved
    assert elapsed < 1.0                       # catches a BOUNDED wait of >= 1.0s

    reaction_tasks = [
        task for task in background._tasks
        if not task.done() and task.get_coro().__qualname__ == "_place"
    ]
    assert len(reaction_tasks) == 1            # the 👀 placement is tracked, still in flight

    gate.set()
    await _drain(background)
    assert adapter.emojis == [indicate.ACK, indicate.DONE]


async def test_a_slow_reaction_is_not_counted_as_tag_overhead():
    """`nora_overhead_seconds` is the metric that would catch a decoration
    creeping onto the critical path, so it is asserted directly rather than
    inferred from wall-clock.

    The reaction's delay and the bound sit FAR apart on purpose, so the test can
    tell «the reaction is on the critical path» from «the machine is busy»:
    with a 1.0 s reaction, a critical-path regression reads >= 1.0 and a loaded
    box stays well under 0.5."""
    adapter = _ReactingAdapter(react_delay=1.0)
    pipeline, metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    overhead = metrics.observations("nora_overhead_seconds")
    await _drain(background)

    assert len(overhead) == 1
    assert overhead[0] < 0.5


async def test_a_timed_out_turn_resolves_to_a_caution_too():
    """The `if not ok: return` exit after `invoke.run` is a THIRD distinct path
    out of a tag, separate from the crash path and the no_send_call path, and it
    never reaches `check_outcome` at all. It must still terminate the indicator.

    PROBE: this is the case that survives moving `indicate.resolve` from the
    `finally` up next to `check_outcome` — the crash test alone does not catch
    a resolve placed after `invoke.run` but before delivery."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, runner=ScriptedRunner(_reply_turns(), delay=0.2),
        timeout_seconds=0.01,
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.sent == []
    assert adapter.emojis == [indicate.ACK, indicate.GAVE_UP]
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1


async def test_reactions_disabled_puts_nothing_on_the_message():
    """`engagement.reactions.enabled = false`: she replies and reacts to
    nothing. Asserted against the ADAPTER, because the flag's whole promise is
    what the GROUP sees — an assertion that `indicate.begin` went uncalled
    would pass just as well if `resolve` still fired a stray ✅.

    Both the 👀 and its terminal ✅ must be absent: the flag turns the feature
    off, it does not merely skip the opening reaction and strand the closing
    one on a message that never got marked.

    PROBE: drop the `if self._reactions` guard in
    `nora/engagement/pipeline.py::_run_admitted` and this fails."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, reactions=False,
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.reactions == []
    assert adapter.texts == ["今晚七點半得"]          # the reply is untouched
    assert metrics.get("nora_reactions_total", phase="ack", status="sent") == 0.0


# ---------------------------------------------------------------------------
# The room-scoped tools, wired end to end
# ---------------------------------------------------------------------------


class _RoomRunner:
    """Calls one room-scoped tool against the REAL tools the pipeline built,
    then replies — so this asserts the whole chain (pipeline -> ToolRegistry ->
    tools -> Room -> RosterSource), not a hand-built `Room`."""

    def __init__(self, tool: str, **args) -> None:
        self._tool = tool
        self._args = args
        self.result = None

    async def invoke(self, **kwargs) -> None:
        tools = {t.tool_name: t for t in kwargs["tools"]}
        self.result = await _invoke_tool(tools[self._tool], **self._args)
        await _invoke_tool(tools["send_reply"], text="ok")


async def test_the_room_scoped_tools_are_bound_to_the_tagged_conversation():
    """`group_members` takes no argument, so the ONLY thing that can decide
    which group it answers about is what the pipeline closed over."""
    runner = _RoomRunner("group_members")
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=runner,
        roster_source=FakeRosterSource(groups={
            "g1": (frozenset({"+852111"}), frozenset({"+852000"})),
            "g2": (frozenset({"+852999"}),),
        }),
    )
    await pipeline.run(_request(_msg(conversation_id="g1")))
    await _drain(background)
    assert runner.result["status"] == "ok"
    assert runner.result["members"] == ["Miles"]


async def test_nora_is_not_reported_as_a_member_of_her_own_group():
    """Her account is in `listGroups`' member list and maps to nobody in
    identity.toml, so without the identity threaded through the pipeline she
    would be counted as one member with no name — a stranger, who is her."""
    runner = _RoomRunner("group_members")
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=runner,
        adapter=_FakeAdapter(account="+852000"),
        roster_source=FakeRosterSource(groups={
            "g1": (frozenset({"+852111"}), frozenset({"+852000"})),
        }),
    )
    await pipeline.run(_request(_msg(conversation_id="g1")))
    await _drain(background)
    assert runner.result["members_i_have_no_name_for"] == 0


async def test_a_pipeline_with_no_roster_source_degrades_instead_of_emptying_the_room():
    """`nora.app.offline` wires none — there is no daemon in a harness. The
    answer must be «I could not look», never «nobody is here» (R-5)."""
    runner = _RoomRunner("group_members")
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(runner=runner)
    await pipeline.run(_request())
    await _drain(background)
    assert runner.result["status"] == "unavailable"


async def test_recall_asks_about_the_tagging_message_without_being_told_it():
    """DEC-24 end to end: `about(person)` has one parameter, and the QUESTION
    reaches Honcho anyway because the pipeline closed over the tag body."""
    runner = _RoomRunner("about", person="Miles")
    memory = _RecordingMemory()
    pipeline, _metrics, _audit, _memory2, background = _build_pipeline(
        runner=runner, memory=memory,
        roster_source=FakeRosterSource(groups={"g1": (frozenset({"+852111"}),)}),
    )
    await pipeline.run(_request(_msg()))
    await _drain(background)
    assert memory.queries == [("nora", "miles", "nora.000 幾點食飯")]


# ---------------------------------------------------------------------------
# DEC-42 — multi-message
# ---------------------------------------------------------------------------


class _RaisedCap(ToolRegistry):
    """The real registry with ONE number changed: `send_reply`'s budget.

    Subclassed rather than replaced by a fake, so `build()` and
    `terminal_tools()` are all still the production ones and a scenario runs
    the real tools against the real guard. Raising this number is exactly what
    an operator does to enable multi-message
    (`NATIVE_TOOLS["send_reply"].budget`, DEC-32) — it is not a second cap,
    it IS the cap, and `deliver.build` reads the deliverer's `message_cap` out
    of this same mapping.
    """

    def __init__(self, messages: int) -> None:
        super().__init__()          # DEC-41: the base wires a TransitClient
        self._messages = messages

    def budgets(self, *, allowed_mcp: frozenset[str] = frozenset()):
        return {**super().budgets(allowed_mcp=allowed_mcp), "send_reply": self._messages}


def _multi_turns(*texts: str) -> list[ScriptedTurn]:
    """One model cycle per message, which is the shape a real multi-message
    turn has: `send_reply` returns, the loop comes back for another pass."""
    return [
        ScriptedTurn(tool_calls=[("send_reply", {"text": t})], final_text=t)
        for t in texts
    ]


async def test_multi_message_is_active_and_the_default_cap_is_three():
    """The shipped `send_reply` budget is 3, and the pipeline delivers up to
    three distinct messages with no other edit.
    This runs the REAL default registry — not an override — so it pins the actual
    shipped number, and it is the test that has to track any future change to it.
    """
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics,
        runner=ScriptedRunner(_multi_turns("一", "二", "三")),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["一", "二", "三"]
    assert metrics.observations("nora_messages_per_tag") == [3.0]


async def test_the_cap_tracks_the_send_reply_budget_and_nothing_else():
    """The cap is driven by ONE number. Override it to 2 — distinct from the
    shipped default of 3 — and the same three-message script now delivers exactly
    two. If this needed anything else (a config key, a guard edit, a second
    latch), the cap would not track the number, and the default test above would
    be passing for the wrong reason.
    """
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics, tool_registry=_RaisedCap(2),
        runner=ScriptedRunner(_multi_turns("一", "二", "三")),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["一", "二"]
    assert metrics.observations("nora_messages_per_tag") == [2.0]
    assert metrics.get("nora_partial_replies_total", reason="limit_turns") == 0


async def test_the_raised_cap_is_still_a_cap():
    adapter = _FakeAdapter()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, tool_registry=_RaisedCap(2),
        runner=ScriptedRunner(_multi_turns("一", "二", "三", "四")),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["一", "二"]


async def test_the_canary_and_r18_run_on_every_message_not_just_the_first():
    """A turn whose SECOND message carries a phone
    number. A guard that ran once per turn would clear message one and wave
    the rest of the turn through — which turns multi-message from a feature
    into a leak."""
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics, tool_registry=_RaisedCap(3),
        runner=ScriptedRunner(_multi_turns("等我搵下", "佢個號碼係 +85290000101", "問返佢啦")),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["等我搵下", "問返佢啦"]
    assert metrics.get("nora_egress_blocks_total", kind="phone") == 1


async def test_every_message_gets_its_own_audit_row():
    """A9 record-first, per message. Recording a three-message reply as one
    row would leave the audit — and therefore `load_transcript`, and therefore the
    next turn's transcript — holding a conversation that did not happen."""
    adapter = _FakeAdapter()
    pipeline, _, audit, _, background = _build_pipeline(
        adapter=adapter, tool_registry=_RaisedCap(3),
        runner=ScriptedRunner(_multi_turns("一", "二", "三")),
    )
    await pipeline.run(_request())
    await _drain(background)

    nora_rows = [r for r in await audit.recent(conversation_id="g1", limit=20) if r.is_nora]
    assert [r.body for r in nora_rows] == ["一", "二", "三"]


async def test_a_multi_message_turn_feeds_each_of_noras_messages_under_her_peer():
    """The tag path feeds NORA's replies under her aiPeer, and a multi-message
    turn feeds EACH message, not only the last. All
    three go under `nora` into the conversation session; none under the human's
    peer (DEC-36 at the message level)."""
    memory = _FakeMemory()
    pipeline, _, _, _, background = _build_pipeline(
        memory=memory, tool_registry=_RaisedCap(3),
        runner=ScriptedRunner(_multi_turns("一", "二", "三")),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert memory.fed == [("nora", "一", "g1"), ("nora", "二", "g1"),
                          ("nora", "三", "g1")]


# ---- the partial-delivery observable ---------------------------------------


async def test_a_turn_cut_off_between_messages_is_counted_as_partial():
    """THE PROBE: two of three messages went out and the loop was cut off by
    R-8's turn cap.

    Everything else about this turn looks successful: `delivered` is True,
    `check_outcome` returns True, the indicator resolves ✅, Honcho is fed and
    `nora_silent_failures_total` never moves — R-23 only relabels turns that
    said NOTHING. Without this counter the group reads half a thought and no
    series anywhere says so.
    """
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics, tool_registry=_RaisedCap(3),
        runner=ScriptedRunner([
            ScriptedTurn(tool_calls=[("send_reply", {"text": "一"})], total_tokens=20_000),
            ScriptedTurn(tool_calls=[("send_reply", {"text": "二"})], total_tokens=20_000),
            ScriptedTurn(tool_calls=[("send_reply", {"text": "三"})]),
        ]),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["一", "二"]
    assert metrics.get("nora_partial_replies_total", reason="limit_total_tokens") == 1
    # The turn is NOT silent, and that is the whole point: R-23's label
    # cannot see it.
    assert metrics.get("nora_silent_failures_total", reason="limit_total_tokens") == 0
    assert metrics.observations("nora_messages_per_tag") == [2.0]


async def test_a_turn_that_said_nothing_is_a_silent_failure_not_a_partial_one():
    """The control. R-23's label still owns the silent case, and the two
    counters must never both fire for the same turn — a partial reply is a
    delivery problem, a silent one is a "she never spoke" problem, and summing
    them would put the two most different outcomes in one number."""
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        metrics=metrics,
        runner=ScriptedRunner([# DEC-93: a turn that wrote NOTHING. Prose would be RECOVERED and
        # delivered; this test is about a genuinely silent turn.
        ScriptedTurn(final_text="")]),
    )
    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert metrics.get("nora_partial_replies_total", reason="no_send_call") == 0


class _HangsAfterReplying(ScriptedRunner):
    """Replays its script, then hangs past the invocation timeout.

    The shape a slow LAST model call really has, and the one exit path with no
    stop reason at all: the invocation never returned, so there is nothing to
    ask it how the loop ended.
    """

    async def invoke(self, **kwargs):
        result = await super().invoke(**kwargs)
        await asyncio.sleep(5.0)
        return result


async def test_a_timeout_after_the_messages_went_out_is_counted_as_partial():
    """`invocation_failed` names the cause honestly rather than borrowing a
    `limit_*` label, which would aim the next investigation at the token
    budget — DEC-25's argument, applied to this counter."""
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics, tool_registry=_RaisedCap(3),
        runner=_HangsAfterReplying(_multi_turns("一", "二")), timeout_seconds=0.05,
    )
    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["一", "二"]
    assert metrics.get("nora_partial_replies_total", reason="invocation_failed") == 1
    # The timeout is still counted as itself. The two answer different
    # questions — "why did the turn end" and "did the group get everything" —
    # and neither can answer the other.
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1


async def test_a_timeout_that_delivered_nothing_is_not_a_partial_reply():
    """The control: no messages, no partial. Otherwise every timeout would
    read as a truncated reply and the counter would measure timeouts."""
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        metrics=metrics, runner=ScriptedRunner(_reply_turns(), delay=5.0),
        timeout_seconds=0.05,
    )
    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_partial_replies_total", reason="invocation_failed") == 0
    assert metrics.get("nora_silent_failures_total", reason="invocation_timeout") == 1


async def test_a_complete_turn_is_never_counted_as_partial():
    """The control for the observable itself: a turn that finished normally
    must leave this counter alone, or a dashboard built on it reads every
    successful reply as a truncation."""
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(metrics=metrics)
    await pipeline.run(_request())
    await _drain(background)

    for reason in ("limit_turns", "limit_total_tokens", "invocation_failed", "pipeline_error"):
        assert metrics.get("nora_partial_replies_total", reason=reason) == 0


async def test_a_guard_cancelled_turn_never_delivers_the_guards_own_veto_string():
    """THE PROBE at the consumer: a guard-cancelled turn never delivers the
    guard's own veto string.

    `ReplyGuard._before_model` cancels a model call once the terminal budget is
    spent, and Strands turns that cancel into an assistant message carrying the
    veto text with `stop_reason="end_turn"` — shape-identical to the model
    having written prose. On the real loop `final_text` is «Reply already
    sent — the turn is finished.»

    Without `cancelled_model_call` the recovery sends that to the group, writes
    it to the audit as her authored body, and feeds it to Honcho as her own
    speech. It passes R-6 and R-18 trivially, so no other guard can stop it —
    which is the whole reason the signal has to come from the guard itself.

    Delete `not handle.guard.cancelled_model_call` and this reds."""
    # Spend the terminal budget on replies the Deliverer REFUSES (R-18), so
    # `_turn_is_over` fires while `delivered` is still False — the one state in
    # which the recovery would otherwise engage.
    #
    # All `cap` sends in ONE turn, which is both what production does
    # (DEC-95's burst) and what `LIMITS["turns"] = 4` can hold — one send per
    # turn would hit `max_turns` before spending a cap of 6, and that is a
    # BUDGET stop reason, so the never-silent fallback would fire and this would
    # fail on a line it was not written to probe.
    #
    # The turn ends with the budget spent and `delivered` False, a further
    # model call is attempted, and `_before_model` cancels it — cancelling
    # without setting the flag would deliver «Reply already sent — the turn is
    # finished.» to the group in exactly this scripting.
    cap = ToolRegistry().budgets()["send_reply"]
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([
            ScriptedTurn(tool_calls=[
                ("send_reply", {"text": f"佢喺 +8529876{5432 + i} 度"})
                for i in range(cap)
            ]),
            ScriptedTurn(final_text="unreachable"),
        ]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.sent == []
    assert not any("turn is finished" in t for _target, t in adapter.sent)
    assert metrics.get("nora_recovered_replies_total") == 0
    assert memory.fed == []


async def test_the_scripted_double_returns_the_cancel_text_like_the_real_sdk():
    """Pins the DOUBLE, because the double's fidelity is load-bearing.

    `test_a_guard_cancelled_turn_never_delivers_the_guards_own_veto_string` is
    only non-vacuous because `FakeAgentRunner` returns the cancel string: revert
    the double to `""` and that test passes with the production guard DELETED.
    That is the same class as the defect it defends against — a fake that
    answers more tidily than the real server — so the double needs a probe of
    its own.

    Drives the REAL `ReplyGuard`: spending the terminal budget makes
    `_before_model` cancel, exactly as production does, and Strands then
    synthesises `{"text": cancel_text}` with `end_turn`."""
    from nora.agent.guards import ReplyGuard
    from nora.testing.scripted_agent import FakeAgentRunner

    guard = ReplyGuard(
        conversation_id="g1", budgets={"send_reply": 1}, terminal={"send_reply"},
        metrics=Metrics(),
    )
    guard._turn_is_over = True          # a terminal verb has run

    runner = FakeAgentRunner([ScriptedTurn(final_text="never reached")])
    stop_reason, final_text, _trace = await runner.invoke(
        doctrine=None, tools=[], pre_fill=[], guard=guard,
        tag_message="", limits={}, ctx=None, trace_attributes={},
    )

    assert stop_reason == "end_turn"
    assert final_text == "Reply already sent — the turn is finished."
    assert guard.cancelled_model_call is True     # and the setter really fires


# ---------------------------------------------------------------------------
# per-platform outbound routing
# ---------------------------------------------------------------------------


class _DiscordFakeAdapter(_FakeAdapter):
    """A second platform, identical in every respect except which one it is.

    Subclassing `_FakeAdapter` rather than re-declaring it is the point: the
    two doubles cannot drift apart, so a test that shows a reply landing on one
    and not the other is showing ROUTING, not two differently-shaped fakes.
    """

    platform = "discord"


def _discord_msg(conversation_id: str = "111/222") -> NormalizedMessage:
    """`<guild_id>/<channel_id>`, the convention `policy.example.toml` already
    documents for a Discord room."""
    return NormalizedMessage(
        platform="discord", conversation_id=conversation_id, conversation_type="group",
        sender_id="900001", sender_name="Miles", timestamp=1,
        body="nora.000 幾點食飯", raw={},
    )


async def test_a_discord_tag_is_answered_on_discord_not_signal():
    """A reply leaves on the platform its tag arrived on.

    With one adapter every reply would leave on Signal regardless — a Discord
    question answered into a Signal group, in front of people who never asked
    it. Both adapters are wired here and only one may speak.
    """
    # DISTINCT accounts, deliberately: with the same one on both doubles, a turn
    # that resolved the WRONG platform's identity would be invisible — the rows
    # it wrote would be byte-identical either way.
    signal_adapter = _FakeAdapter(account="+852000")
    discord_adapter = _DiscordFakeAdapter(account="555000111")
    pipeline, _m, audit, *_ = _build_pipeline(
        adapters={"signal": signal_adapter, "discord": discord_adapter},
    )

    await pipeline.run(_request(_discord_msg()))

    # Her OWN audited row carries `identity.primary` as its sender_id, so this
    # is where a turn that resolved the wrong platform's identity becomes
    # visible — R-12's self-tag check reads that same per-platform set.
    own = [r for r in audit.rows if r.get("is_nora")]
    assert own and all(r["sender_id"] == "555000111" for r in own), (
        "a Discord turn must resolve DISCORD's identity, not Signal's"
    )
    assert discord_adapter.texts == ["今晚七點半得"], (
        "a Discord tag must be answered through the Discord adapter"
    )
    assert signal_adapter.sent == [], (
        "the Signal adapter must not see a Discord conversation's reply"
    )


async def test_a_signal_tag_still_goes_to_signal_when_discord_is_also_wired():
    """The other direction, because a routing bug that sent EVERYTHING to the
    newest adapter would pass the test above on its own."""
    signal_adapter = _FakeAdapter()
    discord_adapter = _DiscordFakeAdapter()
    pipeline, *_ = _build_pipeline(
        adapters={"signal": signal_adapter, "discord": discord_adapter},
    )

    await pipeline.run(_request(_msg()))

    assert signal_adapter.texts == ["今晚七點半得"]
    assert discord_adapter.sent == []


async def test_each_platform_resolves_its_own_identity_for_the_self_tag_check():
    """R-12 tests membership in the TAGGING platform's alias set.

    Resolving Signal's identity for a Discord tag would compare a Discord
    sender id against Signal aliases — an id space it shares nothing with — so
    Nora could neither recognise her own Discord messages nor be sure she was
    recognising anyone else's.

    Driven through `run()` on purpose: calling
    `_adapter_for(...).resolve_identity()` directly would assert only that a dict
    lookup returns what was put in the dict, and stay green with BOTH call sites
    in the pipeline hardcoded to `self._adapters["signal"]`.
    """
    signal_adapter = _FakeAdapter(account="+852000")
    discord_adapter = _DiscordFakeAdapter(account="555000111")
    pipeline, _m, audit, *_ = _build_pipeline(
        adapters={"signal": signal_adapter, "discord": discord_adapter},
    )

    await pipeline.run(_request(_msg()))
    await pipeline.run(_request(_discord_msg(), tag_id="tag-2"))

    by_platform = {
        r["platform"]: r["sender_id"] for r in audit.rows if r.get("is_nora")
    }
    assert by_platform == {"signal": "+852000", "discord": "555000111"}, (
        "each turn must stamp the identity of ITS OWN platform"
    )


async def test_an_unwired_platform_refuses_rather_than_picking_an_adapter():
    """Fail closed, loudly. A fallback to «whatever adapter is wired» is the
    bug the mapping prevents, so the absence of a route must raise rather
    than quietly deliver a reply into a room on another platform (R-5: a reply
    sent to the wrong group is a wrong answer made public)."""
    signal_adapter = _FakeAdapter()
    pipeline, *_ = _build_pipeline(adapters={"signal": signal_adapter})

    with pytest.raises(KeyError, match="discord"):
        pipeline._adapter_for("discord")

    # And the whole turn refuses the same way rather than degrading into a
    # send on the wrong platform. `run` raises rather than absorbing it: the
    # lookup happens before the turn's own guard block, and in production `run`
    # is spawned through `BackgroundTasks`, whose done-callback logs the
    # failure — loud, and with nothing delivered.
    with pytest.raises(KeyError, match="discord"):
        await pipeline.run(_request(_discord_msg()))

    assert signal_adapter.sent == [], (
        "an unroutable tag must not fall back to another platform's adapter"
    )


# ---------------------------------------------------------------------------
# The debrief stage's wiring (R-69).
#
# The stage's OWN behaviour — the fold, the overflow retry, the screen, the
# `finish_reason` check — is `tests/unit/test_debrief.py`'s. What is asserted
# HERE is the thing only this file can see: that the stage is reached on every
# exit path, that it holds the slot and then gives it back, and that it cannot
# turn a turn that delivered into a turn that reports a failure.
# ---------------------------------------------------------------------------

class _RecordingDebrief:
    """Stands in for `debrief.run`, records what it was given, and can be told
    to fail the way the real one might.

    `fail` is a `BaseException` on purpose and defaults to `CancelledError` in
    the test that uses it: `except Exception` does not catch that, which is the
    whole point of R-69's second clause.
    """

    def __init__(self, *, fail: BaseException | None = None, sleep: float = 0.0) -> None:
        self.calls: list[dict] = []
        self._fail = fail
        self._sleep = sleep

    async def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._fail is not None:
            raise self._fail


def _wiring() -> object:
    """A `debrief.Wiring` whose collaborators are never reached — every test
    below patches `debrief.run` itself. It exists so `_debrief`'s «is this
    configured at all» guard passes; the stage's own collaborators have their
    own suite."""
    from nora.engagement.stages.debrief import Wiring

    return Wiring(store=object(), client=object(),
                  objective=object(), record_store=object(),
                  max_chars=1200, fold_batch=200, timeout_seconds=5.0,
                  store_timeout_seconds=5.0)


def _patch_debrief(monkeypatch, spy) -> None:
    from nora.engagement.stages import debrief as debrief_module

    monkeypatch.setattr(debrief_module, "run", spy)


class _FakePerfCounter:
    """A `time.perf_counter` stand-in that never advances on its own — only
    an explicit `.advance()` call moves it. `nora/engagement/pipeline.py` and
    `nora/engagement/stages/invoke.py` both read the real `time.perf_counter`
    (a single shared module object, so patching either name patches both),
    and this class replaces it for exactly one test: `total`s and `elapsed`s
    computed from it become exact and load-independent instead of a race
    against the scheduler."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _ClockAdvancingDebrief:
    """Stands in for `debrief.run` the way `_RecordingDebrief` does, but
    instead of `asyncio.sleep`ing a fixed duration it jumps `timer` forward
    by `seconds` — its "cost" is exact and it spends no real wall-clock time
    doing it, so the test below runs in milliseconds regardless of how large
    `seconds` is."""

    def __init__(self, timer: _FakePerfCounter, seconds: float) -> None:
        self.calls: list[dict] = []
        self._timer = timer
        self._seconds = seconds

    async def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)
        self._timer.advance(self._seconds)


async def test_no_debrief_wiring_means_the_stage_is_never_reached(monkeypatch):
    """`[standing] enabled = false` — the shipped default and every offline/
    eval composition. It must cost NOTHING: no await, so the slot is held for
    exactly as long as without the stage."""
    spy = _RecordingDebrief()
    _patch_debrief(monkeypatch, spy)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    assert spy.calls == []


@pytest.mark.parametrize("turns, outcome", [
    (_reply_turns(), "send_reply"),
    ([ScriptedTurn(final_text="")], "silent"),
])
async def test_the_debrief_runs_on_a_delivered_turn_and_on_a_silence(
    monkeypatch, turns, outcome,
):
    """The silence is the half that matters. A debrief hung off the end
    of the `try` block would review only the turns that reached
    `completed = True` — which excludes every silence, every veto, every
    timeout — and a stage that only reviews successful replies measures the
    path that already works. R-5 silence is the decision most likely to be
    wrong in a way nobody complains about, and this is the case that proves it
    is looked at.
    """
    spy = _RecordingDebrief()
    _patch_debrief(monkeypatch, spy)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(turns), debrief_wiring=_wiring(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert len(spy.calls) == 1
    assert spy.calls[0]["trace"].outcome == outcome
    assert spy.calls[0]["platform"] == "signal"


async def test_the_debrief_runs_after_a_timeout_too(monkeypatch):
    """The `not ok` early return — one of the three exits that leave the `try`
    before `completed = True`."""
    spy = _RecordingDebrief()
    _patch_debrief(monkeypatch, spy)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns(), delay=1.0), timeout_seconds=0.01,
        debrief_wiring=_wiring(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert len(spy.calls) == 1
    assert spy.calls[0]["trace"].outcome == "timeout"


async def test_a_turn_with_no_attributable_trace_is_not_debriefed(monkeypatch):
    """The mirror of `test_a_fault_before_invoke_run_ever_returns_saves_nothing`.

    A crashed turn's `trace` is cleared deliberately, because its placeholder
    outcome would be a GUESS about what reached the group. Reviewing a turn
    under a guessed label is worse than not reviewing it: the fold writes that
    guess into a row injected into every later turn.
    """
    spy = _RecordingDebrief()
    _patch_debrief(monkeypatch, spy)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        context_assembler=_FaultingContextAssembler(), debrief_wiring=_wiring(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert spy.calls == []
    assert _held(pipeline) is False


async def test_the_debrief_holds_the_slot_and_then_releases_it(monkeypatch):
    """It is a STAGE, which is what «holds the slot» means concretely — the
    lock is still held while it runs, and the conversation's next tag waits.
    The cost lands on the second person queued, never on the person being
    reviewed, and that is only true if the release happens after.
    """
    held_during: list[bool] = []
    pipeline_ref: list = []

    async def _observe(**kwargs) -> None:
        held_during.append(_held(pipeline_ref[0]))

    _patch_debrief(monkeypatch, _observe)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        debrief_wiring=_wiring(),
    )
    pipeline_ref.append(pipeline)

    await pipeline.run(_request())
    await _drain(background)

    assert held_during == [True]
    assert _held(pipeline) is False


async def test_a_debrief_that_blows_up_still_releases_the_lock(monkeypatch):
    """**R-69.** An exception escaping a `finally` skips the REST of that
    `finally` — and the statement immediately after this call is the one that
    releases R-10's per-conversation lock and dispatches R-30's queue. A wedged
    conversation answers nobody, forever, with no error anywhere, which is the
    worst failure available on this path.

    `CancelledError`, not `RuntimeError`: it is a `BaseException`, so a
    `try/except Exception` here would let it through and this goes red.
    """
    _patch_debrief(monkeypatch, _RecordingDebrief(fail=asyncio.CancelledError()))
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        debrief_wiring=_wiring(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert _held(pipeline) is False


async def test_a_debrief_failure_never_reports_a_partial_reply(monkeypatch):
    """**R-69's other half.** The turn is marked
    completed BEFORE the debrief is awaited — expressed here by the debrief
    living in the `finally`, after `completed` is already final. Get that
    ordering wrong and a fault in the debrief leaves `completed` False, which
    `deliver.truncation_reason` reads as «she was cut off mid-reply»: a false
    `nora_partial_replies_total` on a turn that sent a complete reply, and a
    false alarm on a working turn is worse than the stage not running.
    """
    adapter = _FakeAdapter()
    _patch_debrief(monkeypatch, _RecordingDebrief(fail=RuntimeError("boom")))
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, debrief_wiring=_wiring(),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["今晚七點半得"]
    assert metrics.get("nora_partial_replies_total") == 0
    assert metrics.get("nora_silent_failures_total") == 0


async def test_the_debriefs_cost_is_excluded_from_the_tag_latency_series(monkeypatch):
    """Folding the
    debrief into `nora_tag_latency_seconds` would move an existing SLO series by
    changing what it measures — a broken gate wearing a passing number. The
    debrief's cost is real and user-visible; it belongs on
    `nora_debrief_seconds`, which is the series the p95 <= 5s gate reads.

    PROVEN WITHOUT THE WALL CLOCK: a real sleep with a threshold at the
    debrief's own delay has zero margin against scheduling jitter in the
    pipeline's OWN (non-debrief) work.

    `time.perf_counter` is replaced with `_FakePerfCounter`, which only
    moves when told to. `pipeline.py`'s `total = time.perf_counter() -
    started` is read BEFORE the debrief is awaited — so on
    this fake clock `total` is fixed at `0.0` the instant it is computed, no
    matter how large a "cost" the debrief racks up afterwards. The debrief
    (`_ClockAdvancingDebrief`) advances the fake clock by `debrief_seconds`
    — a deliberately absurd 300s, impossible to clear by luck — INSTEAD OF
    sleeping, so proving the exclusion costs the test no real time and
    cannot itself inject wall-clock noise into the pipeline's own portion.
    `pipeline.run` is wrapped in `asyncio.wait_for` so a regression that
    makes it hang fails the test instead of hanging `make gate`.

    PROBE: move `total = time.perf_counter() - started` (pipeline.py) to
    AFTER `await self._debrief(...)`, and `total` becomes `>= debrief_
    seconds` on this fake clock — the assertion below goes red on exactly
    the leaked cost, not on an unrelated scheduling artifact.
    """
    timer = _FakePerfCounter()
    monkeypatch.setattr(time, "perf_counter", timer)
    debrief_seconds = 300.0

    _patch_debrief(monkeypatch, _ClockAdvancingDebrief(timer, debrief_seconds))
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        debrief_wiring=_wiring(),
    )

    await asyncio.wait_for(pipeline.run(_request()), timeout=5.0)
    await _drain(background)

    observed = metrics.observations("nora_tag_latency_seconds")
    assert len(observed) == 1
    assert observed[0] < debrief_seconds / 2, (
        "the debrief's own duration leaked into nora_tag_latency_seconds"
    )


async def test_every_retained_trace_is_counted_by_outcome(monkeypatch):
    """`tag_trace` is
    written in a `finally` on every exit path and `nora_tag_latency_seconds`
    observes once per admitted tag, so the expected ratio is exactly 1.0 — and
    a recording layer that silently stopped writing would otherwise be
    discovered at the first fold, which is precisely when the missing traces
    can no longer be reconstructed."""
    pipeline, metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_tagtrace_writes_total", outcome="send_reply") == 1
    assert metrics.get("nora_tagtrace_writes_total") == len(
        metrics.observations("nora_tag_latency_seconds"))


async def test_nora_engagement_outcomes_total_counts_a_send_reply_turn():
    """The standing outcome counter: one increment per admitted tag, labelled with
    the turn's real disposition. Positive control for the ordinary path —
    `outcome="send_reply"` — paired with the
    recovered/silent/no-store controls below so a `== 0` reading anywhere in
    this file can be told apart from an instrument that never fires."""
    pipeline, metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_engagement_outcomes_total", outcome="send_reply") == 1
    assert metrics.get("nora_engagement_outcomes_total") == 1


async def test_nora_engagement_outcomes_total_counts_a_recovered_reply_as_recovered():
    """DEC-93's own split, on the standing counter rather than a one-off
    Langfuse query. A reply the model wrote as prose (no `send_reply` call)
    must land under `outcome="recovered"`, not `"send_reply"` — collapsing
    the two would hide the recovered share this counter exists to keep
    visible."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_engagement_outcomes_total", outcome="recovered") == 1
    assert metrics.get("nora_engagement_outcomes_total", outcome="send_reply") == 0


async def test_nora_engagement_outcomes_total_counts_a_silent_turn():
    """The third reachable value, so `== 0` above can be trusted: a turn that
    wrote nothing at all lands under `outcome="silent"`, the same turn
    `nora_silent_failures_total{reason="no_send_call"}` already counts."""
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_engagement_outcomes_total", outcome="silent") == 1


async def test_nora_engagement_outcomes_total_does_not_depend_on_the_trace_store():
    """`nora_tagtrace_writes_total` only fires `if trace is not None and
    self._tag_trace_store is not None` — it answers "did retention get
    handed a trace", not "what did the turn end as". `nora_engagement_
    outcomes_total` must keep answering the second question even when the
    first goes dark (an offline/eval composition, or a deployment that has
    not wired a store — `nora.engagement.pipeline.EngagementPipeline`'s own
    docstring on `tag_trace_store`), because a deployment losing retention
    must not silently lose route-health visibility along with it."""
    pipeline, metrics, _audit, _memory, background = _build_pipeline()
    # `_build_pipeline` always substitutes a fake store for `None` (so every
    # OTHER scenario in this file can read `pipeline._tag_trace_store.saved`
    # without wiring one itself) — unwiring it here has to go around that,
    # the same way the module's own `__init__` docstring says a deployment
    # that never wires one would arrive at `self._tag_trace_store = None`.
    pipeline._tag_trace_store = None

    await pipeline.run(_request())
    await _drain(background)

    assert metrics.get("nora_tagtrace_writes_total") == 0
    assert metrics.get("nora_engagement_outcomes_total", outcome="send_reply") == 1


# ---------------------------------------------------------------------------
# ONE TRACE PER TAG. A rootless span IS a trace, so spans without a shared
# parent would render one tagged message as several near-empty Langfuse traces.
#
# These assert on EXPORTED spans (the `exported_spans` fixture), because the
# property lives entirely in the trace ids and the parent links. A test that
# checked `span` had been CALLED would stay green regardless.
# ---------------------------------------------------------------------------


def _by_name(exporter) -> dict[str, list]:
    out: dict[str, list] = {}
    for recorded in exporter.get_finished_spans():
        out.setdefault(recorded.name, []).append(recorded)
    return out


async def test_one_tagged_message_produces_exactly_one_trace(exported_spans):
    """Every span a tag opens shares one trace id, and there is exactly ONE root.

    PROBE: delete the `with span("engagement", ...)` wrapper in `run` and the
    admission and turn spans become roots of their own traces — three trace ids,
    three roots.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    finished = exported_spans.get_finished_spans()
    names = {s.name for s in finished}
    assert {"engagement", "engagement.admission",
            "engagement.turn", "engagement.invoke"} <= names
    assert len({s.context.trace_id for s in finished}) == 1, \
        "one tagged message must be one trace"
    assert [s.name for s in finished if s.parent is None] == ["engagement"]


async def test_every_stage_span_hangs_under_the_tag_root(exported_spans):
    """Not just «one trace» — the right SHAPE. `admission` and `turn` are
    children of the root and `invoke` is a child of `turn`, so a turn that ends
    early ends at a NAMED stage inside a trace that says which tag it was.

    PROBE: open the turn span around only part of `_run_turn`'s body, or hoist
    `engagement.invoke` out of it, and a parent link breaks.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    spans = _by_name(exported_spans)
    root = spans["engagement"][0]
    turn = spans["engagement.turn"][0]
    assert spans["engagement.admission"][0].parent.span_id == root.context.span_id
    assert turn.parent.span_id == root.context.span_id
    assert spans["engagement.invoke"][0].parent.span_id == turn.context.span_id
    assert root.attributes["nora.tag_id"] == "tag-1"
    assert root.attributes["nora.conversation_id"] == "g1"


async def test_the_turn_span_records_the_outcome_the_turn_actually_had(exported_spans):
    """The spans carry more than a name, or Langfuse renders a trace with nothing
    in it. A turn's disposition is only
    known in the `finally`, which is what `annotate` is for.

    PROBE: delete the `annotate(...)` in `_run_turn`'s `finally`.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline()
    await pipeline.run(_request())
    await _drain(background)
    turn = _by_name(exported_spans)["engagement.turn"][0]
    assert turn.attributes["nora.outcome"] == "send_reply"
    assert turn.attributes["nora.delivered"] is True


async def test_a_turn_that_says_nothing_is_recorded_as_silent(exported_spans):
    """The same attribute, a different value — so the assertion above is reading
    the turn rather than a constant. R-5's deliberate silence is the case that
    most needs a trace, because nothing else marks it as a decision."""
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner([]),
    )
    await pipeline.run(_request())
    await _drain(background)
    turn = _by_name(exported_spans)["engagement.turn"][0]
    assert turn.attributes["nora.outcome"] == "silent"
    assert turn.attributes["nora.delivered"] is False


async def test_a_rejected_tag_leaves_a_trace_that_says_it_was_rejected(exported_spans):
    """A self-tag is dropped at admission and never reaches a turn. It still
    leaves a trace, and one that NAMES the verdict rather than an unexplained
    one-span stub.

    PROBE: drop `annotate(nora.admission=...)` in `run` and a refused tag is
    indistinguishable from one that crashed before its first stage.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=_FakeAdapter(account="+852111"),
    )

    await pipeline.run(_request(_msg(sender_id="+852111")))
    await _drain(background)

    spans = _by_name(exported_spans)
    assert "engagement.turn" not in spans
    assert spans["engagement"][0].attributes["nora.admission"] == "REJECTED"


async def test_a_tag_turn_still_reaches_the_collector(exported_spans):
    """R-76's OTHER DIRECTION, and the one a fix aimed at trace volume is most
    likely to break. Ingest is not exported because it has no generation
    in it; a tag turn is the thing the collector exists for, and it must come
    through whole — root, stages, and the invoke span the model call hangs
    under — with the same trace id.

    `exported_spans` runs production's sampler (see `tests/conftest.py`), so
    this is a measurement of the deployed rule and not of an unfiltered
    provider.

    PROBE: declare the `engagement` root `plumbing` instead of `generation`, or
    drop the declaration and invert the sampler's default, and this goes to
    zero while the ingest tests stay green — which is the pair that pins the
    rule to roots that CAN contain a generation rather than to trace volume.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline()

    await pipeline.run(_request())
    await _drain(background)

    spans = _by_name(exported_spans)
    assert {"engagement", "engagement.admission", "engagement.turn",
            "engagement.invoke"} <= set(spans)
    root = spans["engagement"][0]
    assert root.parent is None
    assert root.attributes["nora.trace_kind"] == "generation"
    assert len({s.context.trace_id for s in exported_spans.get_finished_spans()}) == 1


async def test_a_tag_refused_at_admission_still_reaches_the_collector(exported_spans):
    """THE CASE THAT DECIDES THE RULE'S SHAPE. A self-tag is dropped at
    admission and never reaches the model, so this trace contains no generation
    at all — and it must still be exported.

    That is why the declaration is on the ENTRY POINT («work that CAN contain a
    generation») and not on the run that happened to reach one, which a sampler
    could not know at root-start time anyway. A refused tag is still a tag: the
    operator asked, and DEC-141 exists so the answer is a trace that SAYS it was
    refused rather than an unexplained stub.

    PROBE: move the `generation` declaration off `run`'s root onto the invoke
    span and this reddens while the ordinary turn above stays green.
    """
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=_FakeAdapter(account="+852111"),
    )

    await pipeline.run(_request(_msg(sender_id="+852111")))
    await _drain(background)

    spans = _by_name(exported_spans)
    assert "engagement.turn" not in spans, "positive control: it never reached a turn"
    assert spans["engagement"][0].attributes["nora.admission"] == "REJECTED"


async def test_a_queued_tag_lands_in_the_trace_that_admitted_it(exported_spans):
    """THE CONTEXT-PROPAGATION PROPERTY — why one trace per tag is not a one-line wrap.

    A queued tag runs on a task `ConversationSlots.release_and_dispatch` spawns
    from the PREVIOUS turn's `finally`. `asyncio.create_task` COPIES contextvars
    and OTel's context is a contextvar, so left alone that task inherits the
    previous tag's span: tag 2's turn is filed under tag 1's trace, asserting a
    causal relation between two people's messages that does not exist.

    PROBE, and the two halves fail differently: delete `with attached(carrier)`
    in `_run_queued` and tag 2's turn joins tag 1's trace — the second assertion
    reddens. Enqueue without `capture_context()` and tag 2's turn is orphaned
    from its own admission — the first reddens.
    """
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, runner=runner,
    )

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )
    await pipeline.run(_req(body="q2", tag_id="t2"))   # QUEUED behind t1
    await first
    await _drain(background)

    spans = exported_spans.get_finished_spans()
    roots = {s.attributes["nora.tag_id"]: s for s in spans if s.name == "engagement"}
    turns = {s.attributes["nora.tag_id"]: s
             for s in spans if s.name == "engagement.turn"}
    assert set(roots) == {"t1", "t2"} and set(turns) == {"t1", "t2"}

    assert turns["t2"].context.trace_id == roots["t2"].context.trace_id, \
        "a queued tag's turn belongs to the trace that admitted it"
    assert turns["t2"].context.trace_id != roots["t1"].context.trace_id, \
        "a queued tag must never be filed under the tag ahead of it in the queue"
    assert turns["t2"].parent.span_id == roots["t2"].context.span_id


async def test_a_queued_tag_with_no_carried_context_starts_its_own_trace(exported_spans):
    """The fail-closed direction, driven through the real dispatch path.

    `_run_queued`'s `carrier` defaults to `None` and `attached(None)` attaches an EMPTY
    context. So the worst case of a lost capture is a turn that traces on its
    own, never one silently adopted by whatever task happened to spawn it.

    PROBE: make `attached` fall through without attaching when `carrier is None`
    and this turn is parented to the in-flight tag's span instead.
    """
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, runner=runner,
    )

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await _wait_for(
        lambda: _held(pipeline),
        message="t1 was never admitted (pipeline never held)",
    )
    # Enqueued WITHOUT a carrier, through the real `ConversationSlots` rather
    # than by calling `_run_queued` directly — the dispatch is the thing under
    # test, so it has to be the real one.
    pipeline._slots.enqueue(
        ("signal", "g1"),
        lambda: pipeline._run_queued(_req(body="q2", tag_id="t2"), None),
    )
    await first
    await _drain(background)

    spans = exported_spans.get_finished_spans()
    t1_root = next(s for s in spans if s.name == "engagement"
                   and s.attributes["nora.tag_id"] == "t1")
    t2_turn = next(s for s in spans if s.name == "engagement.turn"
                   and s.attributes["nora.tag_id"] == "t2")
    assert t2_turn.parent is None, "an un-carried queued tag is its own root"
    assert t2_turn.context.trace_id != t1_root.context.trace_id


# --- the shared decision log ---------------------------------


def _decision_recorder(store=None, *, metrics=None):
    """A real `DecisionLogRecorder` over the SHARED in-memory store, which
    `tests/contract/test_decision_log_store_contract.py` judges against
    Postgres — never an inline double."""
    return DecisionLogRecorder(
        store=store if store is not None else InMemoryDecisionLogStore(),
        metrics=metrics if metrics is not None else Metrics(),
        clock=lambda: datetime.now(UTC),
    )


async def test_a_tag_turn_that_replies_is_written_to_the_decision_log():
    """**The writer.** The tag path is the one decider never in doubt about
    whether it should speak — somebody addressed her — so it reads nothing from
    the log and only writes. What it writes is what the other two could not
    see: a tag turn one second behind a participation answer would
    re-derive it from scratch.

    `reason` is the disposition `real_outcome` already derived, reused rather
    than derived a second way — a second derivation is a third place the two
    can disagree.

    Falsification: delete the `self._decisions.record(...)` call in
    `EngagementPipeline.run`.
    """
    store = InMemoryDecisionLogStore()
    pipeline, _, _, _, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns()),
        decisions=_decision_recorder(store),
    )

    await pipeline.run(_request())
    await _drain(background)

    rows = await store.recent(conversation_id="g1", since_ms=0)
    assert [(r.writer, r.verdict, r.reason) for r in rows] == [
        ("tag", "replied", "send_reply"),
    ]


async def test_a_tag_turn_that_said_nothing_is_written_too():
    """The row that matters most, and the one a naive writer misses: it is
    written BEFORE the `not delivered` return, so a turn that said nothing is
    logged as loudly as one that replied. A log that recorded only successes
    would answer «this room has been quiet» for a room she has been silent in
    four times, which is the opposite of the fact the next decider needs.

    **`silent`, not `failed`.** R-5's deliberate silence is the healthy
    outcome, and labelling it `failed` would make a working turn read as a
    broken one to whatever renders the block next — DEC-25's rule that a
    `reason` naming the wrong cause is worse than a missing one, moved into a
    prompt. `failed` is kept for a turn a guard vetoed or a fault killed.

    Falsification: move the `record` call below the `if not delivered: return`,
    or collapse the three-way verdict to `replied`/`failed`.
    """
    store = InMemoryDecisionLogStore()
    pipeline, _, _, _, background = _build_pipeline(
        runner=ScriptedRunner([ScriptedTurn(tool_calls=[], final_text="")]),
        decisions=_decision_recorder(store),
    )

    await pipeline.run(_request())
    await _drain(background)

    rows = await store.recent(conversation_id="g1", since_ms=0)
    assert [(r.writer, r.verdict, r.reason) for r in rows] == [
        ("tag", "silent", "silent"),
    ]


async def test_a_decision_log_that_is_down_never_fails_a_tag_reply():
    """The rule the recorder exists for, on the path a person is actually
    waiting on. A store that raises on every verb must cost a counter and
    nothing else."""
    class _Broken:
        async def append(self, *, row):
            raise RuntimeError("decision log down")

        async def recent(self, *, conversation_id, since_ms, limit=20):
            raise RuntimeError("decision log down")

    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        metrics=metrics,
        adapter=adapter, runner=ScriptedRunner(_reply_turns("今晚七點半得")),
        decisions=_decision_recorder(_Broken(), metrics=metrics),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["今晚七點半得"]
    assert metrics.get(
        "nora_errors_total", stage="decision_log_write", platform="signal",
    ) == 1


async def test_a_tag_turn_that_faults_is_written_too():
    """A tag turn that DIES after participation just answered still logs a row.
    The write beside `check_outcome` never runs for such a turn — the fault
    lands in the pipeline's `except BaseException` — so without a row there, the
    log says nothing happened in that room and misses the very turn it exists to
    make visible.

    Written even though the TRACE is deliberately dropped on the same branch,
    and the two are not in tension: the trace is dropped because that branch
    cannot know what reached the group, so any outcome it wrote would be a
    guess. The log's claim is narrower and certain — this turn faulted.

    Falsification: delete the `record` call in `EngagementPipeline.run`'s
    `except BaseException` and this goes red.
    """
    class _ExplodingAssembler:
        """Raises INSIDE the pipeline's own try. A runner that raises is not
        enough — `invoke.run` catches that and answers `llm_error`, which is a
        turn that finished badly rather than a turn that faulted."""

        async def assemble(self, *, request):
            raise RuntimeError("the context assembly died")

    store = InMemoryDecisionLogStore()
    pipeline, metrics, _, _, background = _build_pipeline(
        context_assembler=_ExplodingAssembler(),
        decisions=_decision_recorder(store),
    )

    await pipeline.run(_request())
    await _drain(background)

    # The positive control: the turn really did fault, on the series that
    # already counted it. A `== 0` assertion here would also be true of a turn
    # that never ran.
    assert metrics.get(
        "nora_silent_failures_total", reason="handler_error", audience="tag",
    ) == 1
    rows = await store.recent(conversation_id="g1", since_ms=0)
    assert [(r.writer, r.verdict, r.reason) for r in rows] == [
        ("tag", "failed", "handler_error"),
    ]


async def test_the_debrief_is_handed_the_account_the_turn_read(monkeypatch):
    """DEC-207: `ctx.culture` from the turn's own `assemble` reaches the
    debrief, so the fold reads the SAME account the reply did."""
    import dataclasses

    from nora.domain.culture import CultureRevision

    account = CultureRevision(
        id="r1", culture_key="g1", body="they decide by attrition",
        written_at=1, established_by="dream:culture:test")
    spy = _RecordingDebrief()
    _patch_debrief(monkeypatch, spy)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns()), debrief_wiring=_wiring(),
    )
    real = pipeline._context.assemble

    async def _with_culture(*, request):
        return dataclasses.replace(await real(request=request), culture=account)

    monkeypatch.setattr(pipeline._context, "assemble", _with_culture)
    await pipeline.run(_request())
    await _drain(background)

    assert len(spy.calls) == 1
    assert spy.calls[0]["culture"] is account


# ---------------------------------------------------------------------------
# The Room a tag builds carries the Record row that raised it, so an event the
# turn writes cites it (`Room.trigger_message_ids`).
# ---------------------------------------------------------------------------


async def test_the_tag_s_room_carries_the_record_row_of_the_message_that_raised_it():
    seen: list = []

    class _Spy(ToolRegistry):
        def surface(self, **kw):
            seen.append(kw["room"])
            return super().surface(**kw)

    audit = FakeRecordStore()
    await audit.append(message=_msg("g1", "+852222"), person_id=None)      # someone else's
    mine = await audit.append(message=_msg(), person_id=None)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        audit=audit, tool_registry=_Spy(),
    )
    await pipeline.run(_request())
    await _drain(background)
    assert [r.trigger_message_ids for r in seen] == [(mine,)]


async def test_who_said_searches_the_whole_read_window_not_the_prompt_slice():
    """`[store] prompt_window` trims what `<group_transcript>` shows; the
    lookup tool is handed the deeper read, so a lookup never shrinks with the
    prompt. 12 rows read (`context_window=10` plus the tag), 4 shown."""
    seen: dict = {}

    class _Spy(ToolRegistry):
        def surface(self, **kw):
            seen["bound"] = tuple(r.body for r in kw["transcript"])
            seen["lookup"] = tuple(r.body for r in kw["lookup_window"])
            return super().surface(**kw)

    audit = FakeRecordStore()
    for i in range(12):
        await audit.append(message=NormalizedMessage(
            platform="signal", conversation_id="g1", conversation_type="group",
            sender_id="+852111", sender_name="Miles", timestamp=100 + i, body=f"m{i}", raw={},
        ))
    memory = _FakeMemory()
    metrics = Metrics()
    runner = _EchoRunner()
    pipeline, _m, _a, _mem, background = _build_pipeline(
        audit=audit, memory=memory, metrics=metrics, runner=runner,
        tool_registry=_Spy(),
        context_assembler=TranscriptInjection(
            audit=audit, memory=memory, persons=PERSONS, context_window=10,
            prompt_window=4, metrics=metrics,
        ),
    )
    await pipeline.run(_req(body="q", tag_id="t1", ts=500))
    await _drain(background)

    assert runner.seen_transcripts[0] == ("m8", "m9", "m10", "m11")      # the prompt: 4
    assert seen["bound"] == ("m8", "m9", "m10", "m11")                   # #n / reply_to: shown
    assert seen["lookup"] == tuple(f"m{i}" for i in range(2, 12))        # who_said: all 10
