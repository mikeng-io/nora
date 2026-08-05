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
"""

from __future__ import annotations

import asyncio
import dataclasses
import time

from nora.agent.registry import ToolRegistry
from nora.app.runtime import BackgroundTasks
from nora.domain.models import ConversationTarget, NormalizedMessage, PersonRef
from nora.domain.ports import (
    EngageDecision,
    EngagementRequest,
    MemoryHealth,
    PlatformIdentity,
    ReactionResult,
    SendResult,
)
from nora.engagement.context import TranscriptInjection
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.stages import indicate
from nora.obs.metrics import Metrics
from nora.persona.loader import Persona
from nora.persons import Persons
from nora.tag import Debounce
from nora.testing import FakeAuditStore

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
PERSONS = Persons(by_person={"miles": MILES}, index={("signal", "+852111"): "miles"})
LIMITS = {"turns": 4, "output_tokens": 400, "total_tokens": 24000}


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
        self.fed: list[tuple[str, str]] = []
        self.peers: list[str] = []

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, *, peer_id: str) -> None:
        self.peers.append(peer_id)

    async def feed(self, *, peer_id: str, text: str) -> bool:
        self.fed.append((peer_id, text))
        return True

    async def peer_card(self, *, peer_id: str) -> list[str]:
        return []

    async def health(self) -> MemoryHealth:
        return MemoryHealth(reachable=True)

    async def aclose(self) -> None:
        return None


class _FakePersonaStore:
    def get(self, *, applies_to: str = "tag") -> Persona:
        return Persona(
            sections=(), text="<nora_persona>be helpful</nora_persona>",
            persona_text="be helpful", sha256="deadbeef", runtime_context="", loaded_at=0.0,
        )


class _LLMState:
    def __init__(self) -> None:
        self.last_success_at: float | None = None


@dataclasses.dataclass
class ScriptedTurn:
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""
    bypass_guard: bool = False


class ScriptedRunner:
    """Replays scripted tool calls against the REAL tools/guards the pipeline
    built — mirrors `tests/integration/harness.py::FakeLLM`, the same shape
    for the same reason: proves *our* orchestration, not Strands' agent loop.
    """

    def __init__(
        self, turns: list[ScriptedTurn] | None = None, *,
        delay: float = 0.0, raises: Exception | None = None,
    ) -> None:
        self.turns = turns or []
        self.delay = delay
        self.raises = raises
        self.invocations = 0
        self.calls: list[dict] = []

    async def invoke(self, **kwargs) -> None:
        self.invocations += 1
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        tools_by_name = {t.tool_name: t for t in kwargs["tools"]}
        guard = kwargs["guard"]
        for turn in self.turns:
            for name, args in turn.tool_calls:
                if not turn.bypass_guard:
                    veto = guard.evaluate(name, args)
                    if veto:
                        continue
                await _invoke_tool(tools_by_name[name], **args)
                guard.record(name)


async def _invoke_tool(tool, **kwargs):
    result = tool(**kwargs)
    if asyncio.iscoroutine(result):
        result = await result
    return result


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


def _reply_turns(text: str = "今晚七點半得", conv: str = "g1") -> list[ScriptedTurn]:
    return [ScriptedTurn(tool_calls=[("send_reply", {
        "conversation_id": conv, "conversation_type": "group", "text": text,
    })], final_text=text)]


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
):
    audit = audit if audit is not None else FakeAuditStore()
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
        persona_store=_FakePersonaStore(),
        context_assembler=context_assembler,
        runner=runner or ScriptedRunner(_reply_turns()),
        tool_registry=ToolRegistry(),
        debounce=Debounce(debounce_seconds, clock=clock or _FakeClock()),
        canary="CANARY-XYZ",
        display_name="Nora",
        nora_sender_id="+8520000",
        context_window=10,
        timeout_seconds=timeout_seconds,
        limits=LIMITS,
        metrics=metrics,
        spawn=background.spawn,
        llm_state=llm_state,
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
    assert pipeline._in_flight == set()
    assert llm_state.last_success_at is not None
    assert len([f for f in memory.fed if "[Nora]" in f[1]]) == 1
    assert len(metrics.observations("nora_tag_latency_seconds")) == 1
    assert len(metrics.observations("nora_overhead_seconds")) == 1


async def test_an_unmapped_sender_delivers_with_no_honcho_feed():
    pipeline, _metrics, _audit, memory, background = _build_pipeline(
        runner=ScriptedRunner(_reply_turns()),
    )

    await pipeline.run(_request(_msg(sender_id="+852999999")))
    await _drain(background)

    assert memory.fed == []
    assert pipeline._in_flight == set()


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

        async def feed(self, *, peer_id: str, text: str) -> bool:
            self.started.set()
            await asyncio.sleep(10)   # never completes within the test
            self.fed.append((peer_id, text))
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
    assert pipeline._in_flight == set()
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
    assert pipeline._in_flight == set()
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

    assert pipeline._in_flight == set()
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
    assert pipeline._in_flight == set()


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
# R-10: admission owns acquisition; a rejected admission never touches
# another tag's lock.
# ---------------------------------------------------------------------------


async def test_a_concurrent_tag_for_the_same_conversation_is_dropped_not_queued():
    adapter = _FakeAdapter()
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(
        adapter=adapter, metrics=metrics,
        runner=ScriptedRunner(_reply_turns(), delay=0.2),
    )

    first = asyncio.create_task(pipeline.run(_request(tag_id="first")))
    await asyncio.sleep(0.02)   # let the first tag be admitted and start invoking

    assert pipeline._in_flight == {("signal", "g1")}

    await pipeline.run(_request(tag_id="second"))   # dropped: still in flight

    assert metrics.get("nora_tags_dropped_total", reason="in_flight") == 1
    assert pipeline._in_flight == {("signal", "g1")}   # the FIRST tag's lock, untouched

    await first
    await _drain(background)

    assert pipeline._in_flight == set()
    assert adapter.texts == ["今晚七點半得"]   # exactly one reply — the first tag's


async def test_a_debounced_tag_is_never_admitted_and_never_releases_a_lock():
    metrics = Metrics()
    pipeline, _, _, _, background = _build_pipeline(metrics=metrics, debounce_seconds=30)

    await pipeline.run(_request(tag_id="first"))
    await _drain(background)
    assert pipeline._in_flight == set()

    await pipeline.run(_request(tag_id="second"))   # same conversation, inside the window

    assert metrics.get("nora_tags_debounced_total") == 1
    assert pipeline._in_flight == set()


async def test_a_self_tag_is_refused_and_admission_never_runs_the_pipeline():
    pipeline, metrics, _audit, _memory, _background = _build_pipeline(
        adapter=_FakeAdapter(account="+852111"),
    )

    await pipeline.run(_request(_msg(sender_id="+852111")))

    assert pipeline._in_flight == set()
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
    `nora/engagement/pipeline.py::_run_admitted` and this fails."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(adapter=adapter)

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.emojis == [indicate.ACK, indicate.DONE]
    assert adapter.reactions[0][1:3] == ("+852111", 1)   # tagger, tagging timestamp
    assert adapter.texts == ["今晚七點半得"]
    assert metrics.get("nora_reactions_total", phase="ack", status="sent") == 1.0


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
    assert len([f for f in memory.fed if "[Nora]" in f[1]]) == 1
    assert metrics.get("nora_reactions_total", status="failed") == 3.0
    assert metrics.get("nora_silent_failures_total") == 0.0
    assert pipeline._in_flight == set()


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


async def test_reactions_do_not_consume_the_reply_budget_so_r2_still_allows_one_reply():
    """R-2/R-2b with reactions live: two `send_reply` calls, still exactly one
    text delivered, and the reaction lifecycle unaffected by the veto.

    A reaction is not a tool and never touches `Deliverer`, so the attempt
    latch is neither consumed nor re-armed by it — the second call is vetoed by
    `ReplyGuard` exactly as it was before this feature existed."""
    adapter = _ReactingAdapter()
    pipeline, metrics, _audit, _memory, background = _build_pipeline(
        adapter=adapter,
        runner=ScriptedRunner([ScriptedTurn(tool_calls=[
            ("send_reply", {"conversation_id": "g1", "conversation_type": "group",
                            "text": "first"}),
            ("send_reply", {"conversation_id": "g1", "conversation_type": "group",
                            "text": "second"}),
        ])]),
    )

    await pipeline.run(_request())
    await _drain(background)

    assert adapter.texts == ["first"]
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
            ("send_reply", {"conversation_id": "g1", "conversation_type": "group",
                            "text": "hi"}),
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
