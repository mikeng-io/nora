"""`EngagementPipeline.run` — reproduces `Handler.handle_tag`'s exit-path
guarantees (task-6-brief.md): the in-flight lock is released on every exit
path, the latency/overhead histograms are observed on every path, and the
outer `except BaseException` (not `except Exception`) is what keeps R-5's
silence guarantee unconditional.

Admission is acquired by `nora.engagement.stages.admission` and released by
this pipeline's own `finally` — see both modules' docstrings for the split.
A `ScriptedRunner` stands in for the real `AgentRunner` (Task 8's
composition-root collaborator), replaying scripted tool calls against the
REAL tools/guards, the same shape `tests/integration/harness.py`'s `FakeLLM`
uses for `Handler`.

`_invoke_tool` is imported from `nora.testing.scripted_agent` rather than
re-written here. This module used to keep its own copy that called
`tool(**kwargs)` directly, which raises `TypeError` on any argument the tool
does not declare — a failure mode production does not have, because Strands
drops out-of-schema arguments during pydantic validation
(`nora.testing.scripted_agent._accepted_arguments`). DEC-24 is what made the
difference between the copy and the real thing observable.
"""

from __future__ import annotations

import asyncio
import time

from nora.agent.mcp import McpToolGroup
from nora.agent.registry import ToolRegistry
from nora.app.runtime import BackgroundTasks
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef
from nora.domain.ports import (
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    PlatformIdentity,
    ReactionResult,
    Recollection,
    SendResult,
)
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
    ScriptedTurn,
)
from nora.testing.scripted_agent import _invoke_tool

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
PERSONS = Persons(by_person={"miles": MILES}, index={("signal", "+852111"): "miles"})
LIMITS = {"turns": 4, "output_tokens": 400, "total_tokens": 24000}

G1 = ("signal", "g1")
G2 = ("signal", "g2")


# R-10's lock and R-30's queue are no longer private attributes of the pipeline
# — they are the injected `ConversationSlots` both it and the heartbeat fire
# path share (`nora/engagement/slots.py`). These three read that collaborator
# through its PUBLIC surface; the one private hop (`pipeline._slots`) is spelled
# out here once instead of at each of the twenty assertions below.
def _held(pipeline: EngagementPipeline, key: tuple[str, str] = G1) -> bool:
    return pipeline._slots.held(key)


def _depth(pipeline: EngagementPipeline, key: tuple[str, str] = G1) -> int:
    return pipeline._slots.depth(key)


def _waiting(pipeline: EngagementPipeline) -> int:
    """Conversations with at least one tag still queued."""
    return len(pipeline._slots)


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
        self._succeed = succeed

    async def resolve_identity(self) -> PlatformIdentity:
        return PlatformIdentity(platform=self.platform, primary=self._account)

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult:
        self.sent.append((target, text))
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

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.peers.append(peer_id)

    async def feed(self, *, peer_id: str, text: str, conversation_id: str,
                   metadata: dict | None = None) -> bool:
        self.fed.append((peer_id, text, conversation_id))
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


class _RecordingMemory(_FakeMemory):
    """`_FakeMemory` plus the one endpoint the `about` tool speaks to.

    Unknown peers are reachable-with-nothing, exactly as the live Honcho is
    (200 and an empty string, never a 404) — a fake that 404'd instead would
    make «no such peer» and «could not ask» look like the same event, which is
    the ambiguity this whole feature is built to keep apart.
    """

    def __init__(self) -> None:
        super().__init__()
        # (observer, observed, search_query) — the `about` tool reads Nora's
        # model of a person, `(observer=nora, observed=person)` (design/24 §4).
        self.queries: list[tuple[str, str, str]] = []

    async def representation(
        self, *, peer_id, search_query, max_conclusions=5, target=None,
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


class ScriptedRunner(FakeAgentRunner):
    """`nora.testing.FakeAgentRunner`, plus this file's `calls` bookkeeping.

    It used to be a SECOND, hand-written replay that called `guard.evaluate`
    and `guard.record` directly and never dispatched `BeforeModelCallEvent` at
    all. That is the divergence DEC-33 already removed once from
    `nora.testing.FakeAgentRunner`, kept alive here — and DEC-42 made it
    actively misleading: the guard's before-model hook is now what ENDS a turn
    and what relaxes `tool_choice`, so a double that never fired it would let
    a multi-message scenario "pass" against a mechanism it never exercised.

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


def _build_pipeline(
    *,
    runner=None,
    context_assembler=None,
    memory=None,
    audit=None,
    adapter=None,
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
):
    audit = audit if audit is not None else FakeRecordStore()
    memory = memory if memory is not None else _FakeMemory()
    metrics = metrics if metrics is not None else Metrics()
    background = background if background is not None else BackgroundTasks()
    context_assembler = context_assembler or TranscriptInjection(
        audit=audit, memory=memory, persons=PERSONS, context_window=10, metrics=metrics,
    )
    pipeline = EngagementPipeline(
        adapter=adapter or _FakeAdapter(),
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
        nora_sender_id="+8520000",
        ai_peer="nora",
        context_window=10,
        timeout_seconds=timeout_seconds,
        limits=LIMITS,
        metrics=metrics,
        spawn=background.spawn,
        llm_state=llm_state,
        reactions=reactions,
        roster_source=roster_source,
        slots=ConversationSlots(maxlen=queue_max, spawn=background.spawn),
        policy=policy if policy is not None else permissive_policy(),
    )
    return pipeline, metrics, audit, memory, background


async def _drain(background: BackgroundTasks) -> None:
    """Wait for every background task the pipeline's injected `spawn` (i.e.
    `background.spawn`, see Important 2's fix) has launched.

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
    # else. This used to look for `"[Nora]"` in the fed text — the marker of
    # the two-speaker transcript that made her replies evidence about him.
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


# ---------------------------------------------------------------------------
# Important 2: the feedback feed is spawned through the INJECTED tracker, not
# a private set the pipeline rolls itself — see nora/engagement/pipeline.py's
# `spawn` constructor parameter and nora/app/composition.py:172/offline.py:79,
# which both pass `background.spawn`. Without that wiring, `App.run`'s
# `finally: await deps.background.cancel_all()` (nora/__main__.py:124) would
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
    # actually spawned (the discrimination probe this test is written against
    # — see nora/engagement/pipeline.py's history), `.started` never fires and
    # an unbounded wait would hang the suite instead of failing it.
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


async def test_no_send_call_is_counted_when_the_model_never_calls_send_reply():
    adapter = _FakeAdapter()
    pipeline, metrics, _audit, memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.sent == []
    assert metrics.get("nora_silent_failures_total", reason="no_send_call") == 1
    assert memory.fed == []
    assert _held(pipeline) is False
    # And NO fallback: a plain no-send (end_turn) is a DELIBERATE silence she is
    # allowed — the never-silent fallback fires only on a budget CUT-OFF.
    assert metrics.get("nora_loop_exhausted_fallbacks_total") == 0


async def test_the_never_silent_fallback_delivers_when_the_loop_is_cut_off():
    """operator 2026-08-09: a turn cut off by a token/turn cap BEFORE `send_reply`
    used to leave the group with SILENCE (the repeated weather/carpark failure).
    Now Nora delivers one honest fallback line — and, having recovered, the turn
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
# nora_messages_per_tag — the baseline for multi-message
# ---------------------------------------------------------------------------


async def test_a_delivered_tag_observes_one_message():
    """Flat at 1 by construction today: R-2 latches after the first delivery.

    That is what makes it worth recording NOW. Multi-message's predicted
    failure is that the cap becomes a quota always spent in full, and a
    histogram first written after the behaviour changes has nothing to be a
    change from.
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
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
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

    PROBE: restore admission's old `return Admission.REJECTED` (drop) for the
    in-flight branch and only the first reply is delivered."""
    adapter = _FakeAdapter()
    runner = _EchoRunner(delay=0.2)
    pipeline, metrics, _, _, background = _build_pipeline(adapter=adapter, runner=runner)

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1")))
    await asyncio.sleep(0.02)   # first tag admitted and mid-invoke
    assert _held(pipeline) is True

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
    await asyncio.sleep(0.02)
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
    await asyncio.sleep(0.02)

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
    await asyncio.sleep(0.02)
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
    def __init__(self, *, react_ok: bool = True, react_delay: float = 0.0, **kw) -> None:
        super().__init__(**kw)
        self.reactions: list[tuple[str, str, int, bool]] = []
        self._react_ok = react_ok
        self._react_delay = react_delay

    async def react(self, *, target, emoji, to_author, to_timestamp, remove=False):
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
    wait, instead of the silence a dropped tag used to give them.

    PROBE: move `_begin_indicator` back inside `_run_admitted` (fire on turn
    start, not admission) and the queued tag's 👀 does not appear until the tag
    ahead of it finishes."""
    adapter = _ReactingAdapter()
    runner = ScriptedRunner(_reply_turns(), delay=0.3)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter, runner=runner,
    )

    first = asyncio.create_task(pipeline.run(_req(body="q1", tag_id="t1", ts=1)))
    await asyncio.sleep(0.02)
    await pipeline.run(_req(body="q2", tag_id="t2", ts=2))   # queued behind t1
    # Pump the loop briefly — enough for the queued tag's spawned 👀 to land,
    # but far short of t1's 0.3s turn, so t2 is provably STILL waiting.
    for _ in range(5):
        await asyncio.sleep(0)

    assert _depth(pipeline) == 1                                # t2 still queued
    ack_timestamps = [ts for emoji, _a, ts, remove in adapter.reactions
                      if emoji == indicate.ACK and not remove]
    assert 2 in ack_timestamps   # the 👀 for the QUEUED tag's message fired already

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

    With the cap at 3 (DEC-42), four `send_reply` calls still deliver exactly
    three and the fourth is vetoed — the reaction lifecycle is untouched by any
    of it. (Was `..._so_r2_still_allows_one_reply`, from the cap-of-1 era.)"""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(tool_calls=[
            ("send_reply", {"text": "first"}),
            ("send_reply", {"text": "second"}),
            ("send_reply", {"text": "third"}),
            ("send_reply", {"text": "fourth"}),
        ])]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["first", "second", "third"]
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
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
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
    """Constraint: reactions must not delay the reply measurably. With a
    reaction that takes far longer than the whole turn, the reply is already
    delivered by the time `run` returns while the reactions are still in
    flight — which is only true because they are spawned, not awaited.

    PROBE: `await` the coroutines in `indicate.begin`/`resolve` instead of
    spawning them, and the reply becomes hostage to the reaction's latency;
    the `background` assertion below fails."""
    adapter = _ReactingAdapter(react_delay=0.2)
    pipeline, _metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    started = time.perf_counter()
    await pipeline.run(_request())
    elapsed = time.perf_counter() - started

    assert adapter.texts == ["今晚七點半得"]   # replied already
    assert adapter.emojis == []                # reaction still in flight
    assert len(background) > 0
    assert elapsed < 0.2

    await _drain(background)
    assert adapter.emojis == [indicate.ACK, indicate.DONE]


async def test_a_slow_reaction_is_not_counted_as_tag_overhead():
    """`nora_overhead_seconds` is the metric that would catch a decoration
    creeping onto the critical path, so it is asserted directly rather than
    inferred from wall-clock."""
    adapter = _ReactingAdapter(react_delay=0.2)
    pipeline, metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    overhead = metrics.observations("nora_overhead_seconds")
    await _drain(background)

    assert len(overhead) == 1
    assert overhead[0] < 0.2


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
# DEC-42 — multi-message, shipped inert, and what it breaks when it is not
# ---------------------------------------------------------------------------


class _RaisedCap(ToolRegistry):
    """The real registry with ONE number changed: `send_reply`'s budget.

    Subclassed rather than replaced by a fake, so `build()`, `terminal_tools()`
    and `capability()` are all still the production ones and a scenario runs
    the real tools against the real guard. Raising this number is exactly what
    an operator does to enable multi-message
    (`NATIVE_TOOLS["send_reply"].budget`, DEC-32) — it is not a second cap,
    it IS the cap, and `deliver.build` reads the deliverer's `message_cap` out
    of this same mapping.
    """

    def __init__(self, messages: int) -> None:
        super().__init__()          # DEC-41: the base now wires a TransitClient
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
    """DEC-42 ACTIVATED (was `..._still_produces_exactly_one_message`, the inert
    proof). The shipped `send_reply` budget is now 3, and the machinery that was
    already in place delivers up to three distinct messages with no other edit.
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
    """design/16 §4's own probe: a turn whose SECOND message carries a phone
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
    """Honcho v2 (design/24 §3): the tag path feeds NORA's replies under her
    aiPeer, and a multi-message turn feeds EACH message — the exact thing
    `last_text` got wrong (design/16 §4 fed the last, dropped the rest). All
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
    """THE PROBE for 5c. Two of three messages went out and the loop was cut
    off by R-8's turn cap.

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
    # The turn is NOT silent, and that is the whole point: nothing R-23 built
    # can see it.
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
        runner=ScriptedRunner([ScriptedTurn(final_text="I would have said this")]),
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
    # and one has never been able to answer the other.
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
