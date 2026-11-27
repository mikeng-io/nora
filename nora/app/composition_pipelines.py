from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from nora.agent.egress import LeakGuard
from nora.agent.shared_state import shared_reader
from nora.app.assembler import build_assembler
from nora.app.composition_helpers import _reasoning_effort
from nora.app.composition_lesson_check import build_graph_lesson_check
from nora.app.dispatch import Dispatcher
from nora.app.raw_clients import _DebriefModelClient
from nora.app.wake_rooms import WakeRoomSlots
from nora.engagement.pipeline import EngagementPipeline
from nora.engagement.stages.debrief import Wiring as DebriefWiring
from nora.engagement.stages.graph_debrief import Wiring as GraphDebriefWiring
from nora.infra.sql.record_query import SqlRecordQuery
from nora.ingest.pipeline import IngestPipeline

"""Ingest and engagement pipeline wiring steps."""


@dataclass(frozen=True)
class SharedWiring:
    _spawn: Any
    assembler: Any
    shared: Any
    action_state: Any


def build_shared(
    access: Any, allowlist: Any, audit: Any, background: Any, cfg: Any,
    clock: Any, computer_context: Any, documents_context: Any,
    engine: Any, ledger_store: Any, media_join: Any,
    memo_store: Any, metrics: Any,
    note_store: Any, observation_store: Any, persons: Any, presence_reader: Any,
    quote_window: Any, registry: Any,
    rosters: Any, standing_store: Any, subagents_context: Any, tag_trace_store: Any,
    unit_rooms: Any,
) -> SharedWiring:
    shared = shared_reader(
        ledger=ledger_store,
        record_query=SqlRecordQuery(engine),
        standing=standing_store if cfg.standing.enabled else None,
        traces=tag_trace_store,
        ai_peer=cfg.agent.name,
        rooms_of_unit=lambda unit: unit_rooms.get(unit, ()),
    )
    action_state = shared_reader(
        record_query=SqlRecordQuery(engine), traces=tag_trace_store,
        ai_peer=cfg.agent.name, rooms_of_unit=lambda unit: unit_rooms.get(unit, ()),
    )
    assembler = build_assembler(
        cfg,
        access=access,
        audit=audit,
        persons=persons,
        metrics=metrics,
        clock=clock,
        rosters=rosters,
        self_ids=registry.self_aliases(),
        # `None` while `[notes] enabled = false`. `policy=allowlist` is the SAME policy.toml
        # instance the ingest pipeline and `EngagementPipeline` are given below — the assembler
        # needs it for one thing only, naming the unit a room belongs to (`Conversation.unit`,…
        notes=note_store,
        memos=memo_store,
        policy=allowlist,
        # Standing's READ half. `None` while `[standing] enabled = false`, the shipped default,
        # so `<standing>` renders nothing. Unlike `notes` beside it this needs no `policy=` at
        # all: a position is keyed `(platform, conversation_id)` and is NOT shared across a…
        shared=shared,
        # The media join. The same instance every other transcript builder holds — the
        # comprehension a spawned job wrote is the one the transcript renders, the one the note
        # tier reads and the one `media_fetch` serves, and separate copies of that decision…
        media_join=media_join,
        # The shared quote-window resolver (DEC-195). Same instance
        # `FirePath` below is given — the point of building it once.
        quote_window=quote_window,
        presence=presence_reader,
        # `<computer>`: `None` while
        # `[computer]` is off; the same reader the fire path gets.
        computer=computer_context,
        documents=documents_context,
        # `<subagents>`: `None` while `[subagent]` is off. The worker is
        # attached below, beside the desk's. Not given to the fire path.
        subagents=subagents_context,
        # The typed source events of the transcript window and the room's
        # folded object heads for the standing slot: the ONE store the bridge
        # writes (docs/design/platform/observations.md).
        observations=observation_store,
    )

    def _spawn(coro: Coroutine[Any, Any, None]) -> None:

        # IngestPipeline's `spawn` is `Callable[[Coroutine], None]` — it never needs the Task
        # back. `BackgroundTasks.spawn` returns one (callers that DO want it, like Dispatcher,
        # call it directly), so this drops the return value rather than widening the pipeline's…
        background.spawn(coro)

    return SharedWiring(
        _spawn=_spawn, assembler=assembler, shared=shared, action_state=action_state,
    )


@dataclass(frozen=True)
class IngestWiring:
    ingest: Any


def build_ingest(
    _spawn: Any, access: Any, allowlist: Any, audit: Any, blobs: Any,
    cfg: Any, comprehension: Any, media_objects: Any, media_sources: Any,
    metrics: Any, policy: Any,
    sticker_reader: Any, transcriber: Any,
) -> IngestWiring:
    ingest = IngestPipeline(
        audit=audit,
        policy=policy,
        allowlist=allowlist,
        access=access,
        metrics=metrics,
        spawn=_spawn,
        media_sources=media_sources,
        blobs=blobs,
        media_store=media_objects,
        stickers=sticker_reader,
        transcriber=transcriber,
        comprehension=comprehension,
        language=cfg.speech.stt.language,
    )
    return IngestWiring(ingest=ingest)


@dataclass(frozen=True)
class StandingWiring:
    debrief_wiring: Any


async def standing(
    audit: Any, cfg: Any, clock: Any, content_checks: Any,
    dreamer_instructions: Any, ledger_store: Any, lineage: Any, media_join: Any,
    metrics: Any, standing_store: Any, tag_trace_store: Any,
) -> StandingWiring:
    # The debrief's collaborators, as ONE object that is either present or absent
    # (`debrief.Wiring` — the shape is deliberate: four optional parameters would let a
    # deployment wire the store and forget the client, which runs the stage, finds nothing…
    debrief_wiring: DebriefWiring | None = None
    if cfg.standing.enabled:
        debrief_wiring = DebriefWiring(
            store=standing_store,
            media_join=media_join,
            client=_debrief_client(cfg),
            objective=dreamer_instructions,
            record_store=audit,
            max_chars=cfg.standing.max_chars,
            fold_batch=cfg.standing.fold_batch,
            timeout_seconds=cfg.standing.timeout_seconds,
            store_timeout_seconds=cfg.standing.store_timeout_seconds,
            working_set_max_items=cfg.standing.working_set_max_items,
            working_set_max_chars=cfg.standing.working_set_max_chars,
            content_checks=content_checks,
        )
    return StandingWiring(debrief_wiring=debrief_wiring)


def _debrief_client(cfg: Any) -> _DebriefModelClient:
    return _DebriefModelClient(
        base_url=cfg.endpoint("standing")[0],
        api_key=cfg.endpoint("standing")[1],
        max_tokens=cfg.llm.max_tokens,
        temperature=cfg.llm.temperature,
        model=cfg.endpoint("standing", cfg.standing.model or cfg.llm.model)[2],
        timeout_seconds=cfg.standing.timeout_seconds,
        # Empty means inherit `[llm] reasoning_effort` — the same
        # defaulting `model` above uses.
        reasoning_effort=_reasoning_effort(
            cfg.standing.reasoning_effort or cfg.llm.reasoning_effort,
            source=(
                "[standing] reasoning_effort"
                if cfg.standing.reasoning_effort
                else "[llm] reasoning_effort (inherited)"
            ),
        ),
    )


def build_graph_debrief(
    memory_wire: Any, objective: Any, cfg: Any, engine: Any,
    access: Any, lineage: Any,
    persons: Any, policy: Any, doctrine_store: Any, content_checks: bool, clock: Any,
    metrics: Any, slots: Any, model_client_for: Any,
) -> GraphDebriefWiring:
    return GraphDebriefWiring(
        store=memory_wire.debrief_store(engine),
        admission=memory_wire.admission(
            engine, persons, policy,
            LeakGuard(doctrine_store.get(applies_to="tag"), cfg.doctrine.canary),
            content_checks=content_checks, access=access, room_slots=WakeRoomSlots(policy, slots),
            lesson_checker=build_graph_lesson_check(
                cfg, doctrine_store, objective, model_client_for, metrics,
            ),
        ),
        assembler=memory_wire.pass_assembler(engine, access, lineage, None, None),
        client=_debrief_client(cfg), objective=objective, clock=clock,
        revisions=engine.get_execution_options()["item_registration"].revisions,
        metrics=metrics, timeout_seconds=cfg.standing.timeout_seconds,
        store_timeout_seconds=cfg.standing.store_timeout_seconds,
    )


@dataclass(frozen=True)
class EngagementWiring:
    engagement: Any


def build_engagement(
    access: Any, allowlist: Any, assembler: Any, audit: Any, background: Any,
    cfg: Any, clock: Any, debounce: Any, debrief_wiring: GraphDebriefWiring | None,
    decision_log: Any, doctrine_store: Any, limits: Any, llm_state: Any,
    metrics: Any, persons: Any, preview_source: Any,
    reasoning_strip_store: Any, registry: Any, room_commands: Any,
    rosters: Any, runner: Any,
    slots: Any, tag_trace_store: Any, tier: Any, tool_call_log_store: Any,
    tool_registry: Any, graph: Any, standing_replay_wiring: DebriefWiring | None = None,
) -> EngagementWiring:

    engagement = EngagementPipeline(
        access=access,
        # EVERY built adapter, not just Signal's: a tag is answered on the
        # platform it arrived from, and `registry.adapters` is exactly the set
        # of platforms this process can both hear and speak on.
        adapters=dict(registry.adapters),
        audit=audit,
        persons=persons,
        doctrine_store=doctrine_store,
        context_assembler=assembler, graph=graph,
        runner=runner,
        tool_registry=tool_registry,
        # `allowlist` — policy.toml, the SAME instance the ingest pipeline already checked this
        # conversation is admitted under (DEC-18). This is the second read of it: which MCP
        # servers, not whether to engage at all (DEC-71).
        policy=allowlist,
        debounce=debounce,
        canary=cfg.doctrine.canary,
        display_name=cfg.doctrine.display_name,
        ai_peer=cfg.agent.name,
        context_window=cfg.store.context_window,
        timeout_seconds=tier.timeout_seconds,
        limits=limits,
        defer_tools=cfg.llm.defer_tools,
        metrics=metrics,
        spawn=background.spawn,
        llm_state=llm_state,
        reactions=cfg.engagement.reactions.enabled,
        rosters=rosters,
        preview_source=preview_source,
        slots=slots,
        clock=clock,
        tool_call_log_store=tool_call_log_store,
        reasoning_strip_store=reasoning_strip_store,
        tag_trace_store=tag_trace_store,
        debrief_wiring=debrief_wiring, standing_replay_wiring=standing_replay_wiring,
        # The shared decision log. The tag path only WRITES: somebody addressed her, so it is
        # never in doubt about whether to speak. What it writes is what the other two deciders
        # could not see.
        decisions=decision_log,
        # The `!!!member` invocation — the SAME object the tool registry's
        # `member_update` calls, so command and semantic tool share one
        # authenticated path.
        room_commands=room_commands,
    )
    return EngagementWiring(engagement=engagement)


@dataclass(frozen=True)
class DispatcherWiring:
    dispatcher: Any


def build_dispatcher(
    background: Any, engagement: Any, ingest: Any, participation_trigger: Any,
    room_command: Any = None,
) -> DispatcherWiring:

    dispatcher = Dispatcher(
        ingest=ingest,
        engagement=engagement,
        background=background,
        participation=participation_trigger,
        room_command=room_command,
    )
    return DispatcherWiring(dispatcher=dispatcher)
