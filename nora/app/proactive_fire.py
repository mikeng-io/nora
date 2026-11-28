"""Run the due-intention occasion through the fire path's guarded delivery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol

from nora.access import IdentityAccess
from nora.agent.catalog.entries import NATIVE_TOOLS
from nora.agent.outbound import OutboundComposer
from nora.agent.prefill import render_transcript
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.app.proactive_poller import ClaimStore
from nora.doctrine.loader import Doctrine, DoctrineStore
from nora.domain.audience import PROACTIVE
from nora.domain.blocks import BlockDeclaration, ContextBlock, assemble
from nora.domain.composed import compose_body
from nora.domain.context_data import MembersView
from nora.domain.lineage import room_key
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import ConversationTarget, NormalizedMessage, TagContext
from nora.domain.ports import LinkPreviewSource, PlatformAdapter, RecordStore, RosterSource
from nora.domain.quote_window import QuoteWindow
from nora.domain.self_presence import Presence
from nora.engagement.mentions import MentionRenderer
from nora.engagement.self_presence import PresenceReader
from nora.engagement.stages import deliver, graph_debrief, invoke
from nora.language.catalogue_render import render_tool_catalogue
from nora.language.context_render import render_clock, render_members
from nora.language.presence_render import render_presence
from nora.memory import (
    RECHECK_UNAVAILABLE,
    ActAuthority,
    ActEnvelope,
    PassContext,
    PassScope,
    ProactiveClaim,
    ProactiveInput,
    ReadingRoom,
    act_pass,
    seed_pass_shown,
)
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, current_trace_id, span
from nora.persons import Persons
from nora.policy import Policy

_BLOCKS: tuple[BlockDeclaration, ...] = (
    BlockDeclaration("why_now", 0, "Read the due stimulus and decide whether to speak."),
    BlockDeclaration("transcript", 1, "Read the destination's recent conversation."),
    BlockDeclaration("members", 2, "Identify the people in this room."),
    BlockDeclaration("clock", 3, "Interpret time against this pass's clock."),
    BlockDeclaration("presence", 4, "Read Nora's presence in this room."),
    BlockDeclaration("tool_catalogue", 5, "Choose from this pass's tools."),
)
_FLOOR_ORDER = {block.name: block.order for block in _BLOCKS}
_ACTIONS = frozenset({
    "intention_commit", "send_reply", "reaction_send", "send_gif", "media_send",
    "generate_image", "play_music", "skip_music", "top_music", "stop_music", "set_music_volume",
    "document_publish", "document_revise", "document_set_status",
    "computer_write", "computer_edit", "computer_export", "computer_import", "computer_cancel",
})
PROACTIVE_TOOLS = _ACTIONS | frozenset(
    name for name, tool in NATIVE_TOOLS.items() if tool.read_only
)


class RecheckResult(Protocol):
    @property
    def status(self) -> Literal["deliver", "withhold", "unavailable"]: ...

    @property
    def reason(self) -> str | None: ...


class DeliveryRecheck(Protocol):
    async def recheck(
        self, intention_id: str, destination: str, now: datetime, *, occurrence: str | None = None,
    ) -> RecheckResult: ...


class _PassView(Protocol):
    @property
    def intention_visible(self) -> bool: ...

    @property
    def shown(self) -> frozenset[str]: ...

    @property
    def blocks(self) -> Sequence[ContextBlock]: ...


class _Assembler(Protocol):
    async def assemble(
        self, pass_name: Literal["proactive"], data: ProactiveInput,
        floor: Sequence[ContextBlock], scope: PassScope, now: datetime,
    ) -> _PassView: ...


@dataclass(frozen=True, slots=True)
class FireRoom:
    access: IdentityAccess
    records: RecordStore
    persons: Persons
    quote_window: QuoteWindow
    presence: PresenceReader | None
    policy: Policy
    adapters: Mapping[str, PlatformAdapter]
    rosters: Mapping[str, RosterSource]
    previews: LinkPreviewSource | None


@dataclass(frozen=True, slots=True)
class FireAgent:
    runner: invoke.AgentRunner
    registry: ToolRegistry
    doctrine: DoctrineStore
    canary: str
    display_name: str
    context_window: int
    limits: dict[str, Any]
    defer_tools: bool
    timeout_seconds: float
    metrics: Metrics
    revisions: Callable[[], Mapping[str, str | None]]
    tool_call_log: Any
    reasoning_strip: Any


class _ReplyAdmission:
    def __init__(
        self, recheck: DeliveryRecheck, acts: ActEnvelope, authority: ActAuthority,
        target: ConversationTarget, clock: Callable[[], datetime], occurrence: str,
    ) -> None:
        self._recheck = recheck
        self._acts = acts
        self._authority = authority
        self._destination = room_key(target.platform, target.conversation_id)
        self._clock = clock
        self._occurrence = occurrence
        self.withheld = False

    async def allowed(self) -> bool:
        if self.withheld:
            return False
        intention = self._authority.intention
        if intention is None:
            raise ValueError("a due reply requires intention authority")
        result = await self._recheck.recheck(
            intention, self._destination, self._clock(), occurrence=self._occurrence,
        )
        if result.status == "deliver":
            return True
        if result.status == "unavailable":
            await self._acts.record_silence(
                self._authority, reason=RECHECK_UNAVAILABLE, recheck_reason=result.reason,
            )
        else:
            await self._acts.record_silence(self._authority, reason=result.reason or result.status)
        self.withheld = True
        return False


class ProactiveFire:
    def __init__(
        self, room: FireRoom, agent: FireAgent, assembler: _Assembler, claims: ClaimStore,
        acts: ActEnvelope, debrief: graph_debrief.Wiring, recheck: DeliveryRecheck,
        clock: Callable[[], datetime],
    ) -> None:
        self._room = room
        self._agent = agent
        self._assembler = assembler
        self._claims = claims
        self._acts = acts
        self._debrief = debrief
        self._recheck = recheck
        self._clock = clock

    async def retry_pending(self, unit: str, target: ConversationTarget) -> None:
        await graph_debrief.retry(self._debrief, unit, target.platform, target.conversation_id)

    async def run(self, claim: ProactiveClaim, target: ConversationTarget | None) -> None:
        agent = self._agent
        if target is None:
            try:
                unit_authority = self._authority(claim, None, "")
                await self._acts.record_silence(unit_authority, reason="no_destination")
            finally:
                await self._claims.settle(claim, self._clock())
            return
        bind_tag_id(claim.pass_id)
        try:
            with span("proactive", **{
                TRACE_KIND: "generation", "nora.tag_id": claim.pass_id,
                "nora.platform": target.platform, "nora.conversation_id": target.conversation_id,
            }):
                obligation = None
                try:
                    async with asyncio.timeout(agent.timeout_seconds):
                        doctrine = agent.doctrine.get(applies_to=PROACTIVE)
                        authority = self._authority(claim, target, doctrine.sha256)
                        adapter = self._room.adapters[target.platform]
                        identity = await adapter.resolve_identity()
                        ctx = await self._context(claim, target, frozenset(identity.aliases))
                        obligation = graph_debrief.obligation(
                            authority, ctx.message, ctx.transcript, self._clock(),
                        )
                        if await self._claims.stage_debrief(claim, obligation):
                            await self._turn(
                                claim, ctx, authority, doctrine, adapter, identity.primary,
                            )
                finally:
                    settled = await asyncio.wait_for(
                        self._claims.settle(claim, self._clock()), timeout=agent.timeout_seconds,
                    )
                    annotate(**{"nora.outcome": settled or "unavailable"})
                    if settled == "parked":
                        agent.metrics.inc("nora_proactive_stimulus_parked_total")
                    if settled == "closed" and obligation is not None:
                        await graph_debrief.run_finished(self._debrief, obligation)
        finally:
            clear_tag_id()

    def _authority(
        self, claim: ProactiveClaim, target: ConversationTarget | None, doctrine: str,
    ) -> ActAuthority:
        room = target.conversation_id if target is not None else None
        revs = self._agent.revisions()
        context = PassContext(
            claim.due.unit, room, target is not None and target.conversation_type == "dm",
            "proactive", "engagement", claim.pass_id, current_trace_id() or "",
            getattr(self._agent.runner, "model_id", "") if target is not None else "",
            revs.get("rev_code") or "", doctrine, revs.get("rev_skill") or "",
            revs.get("rev_config") or "", revs.get("rev_identity") or "",
            revs.get("rev_policy") or "",
        )
        return ActAuthority(context.unit, intention=claim.due.intention, room=room,
                            pass_context=context)

    async def _context(
        self, claim: ProactiveClaim, target: ConversationTarget, self_ids: frozenset[str],
    ) -> TagContext:
        room, agent = self._room, self._agent
        platform, room_id = target.platform, target.conversation_id
        scope = Scope(ScopeKind.ROOM, room_scope_id(platform, room_id))
        transcript = await room.quote_window.recent_for_window(
            conversation_id=room_id, context_window=agent.context_window,
            platform=platform, stage="fire_context",
        )
        message = NormalizedMessage(
            platform, room_id, target.conversation_type, "", None, 0, "", {},
            admitted_unit=claim.due.unit,
        )
        presence = (await room.presence.read(platform=platform, conversation_id=room_id,
                                             direct=target.conversation_type == "dm")
                    if room.presence is not None else None)
        ctx = TagContext(
            claim.pass_id, message, None,
            await room.access.name_transcript(transcript, platform=platform, scope=scope),
            (), time.perf_counter(), room_members=await room.access.room_refs(platform, room_id),
            presence=presence,
        )
        return await MentionRenderer(
            display_name=agent.display_name, access=room.access, self_ids=self_ids,
            metrics=agent.metrics, content_checks=agent.registry.content_checks,
        ).rehydrate(ctx)

    async def _turn(
        self, claim: ProactiveClaim, ctx: TagContext, authority: ActAuthority,
        doctrine: Doctrine, adapter: PlatformAdapter, sender: str,
    ) -> None:
        agent, room = self._agent, self._room
        registry, message = agent.registry, ctx.message
        target = message.target()
        platform, room_id = target.platform, target.conversation_id
        now = self._clock()
        scope = PassScope(ReadingRoom(platform, room_id), claim.due.unit,
                          nora_name=agent.display_name)
        if getattr(room.records, "delivery_acts", None) is None:
            raise RuntimeError("a due reply requires a durable act envelope")
        admission = _ReplyAdmission(
            self._recheck, self._acts, authority, target, self._clock, claim.due.occurrence,
        )
        with act_pass(authority, self._acts):
            assembled = await self._assembler.assemble(
                "proactive", ProactiveInput(claim.due.intention),
                _floor(claim, ctx, now, agent.display_name), scope, now,
            )
            if not assembled.intention_visible:
                raise LookupError("Due intention is unavailable from the destination's graph scope")
            seed_pass_shown(assembled.shown)
            handle = deliver.build(
                store=room.records, adapter=adapter, target=target, doctrine=doctrine,
                canary=agent.canary, tag_id=ctx.tag_id, nora_sender_id=sender,
                conversation_id=room_id, budgets=registry.budgets(),
                terminal=registry.terminal_tools(),
                metrics=agent.metrics, audience=PROACTIVE, param_names=registry.tool_param_names(),
                cacheable_tools=registry.cacheable_tools(), tool_call_log_store=agent.tool_call_log,
                reasoning_strip_store=agent.reasoning_strip, send_permitted=admission.allowed,
                composer=OutboundComposer(
                    platform=platform, conversation_id=room_id, metrics=agent.metrics,
                    access=room.access, roster_source=room.rosters.get(platform),
                    preview_source=room.previews, content_checks=registry.content_checks,
                ),
                content_checks=registry.content_checks,
            )
            policy_room = room.policy.conversation(platform, room_id)
            surface = registry.surface(
                store=room.records, conversation_id=room_id,
                conversation_type=message.conversation_type, persons=room.persons,
                deliverer=handle.deliverer, metrics=agent.metrics,
                room=Room(platform=platform, conversation_id=room_id, access=room.access,
                          roster_source=room.rosters.get(platform), tagger=None),
                transcript=ctx.transcript, context_window=agent.context_window, fire_turn=True,
                location_trusted=bool(policy_room and policy_room.location), links=handle.links,
                supplied_unit=claim.due.unit, grant=PROACTIVE_TOOLS, defer=agent.defer_tools,
                message=message, engagement=True,
            )
            blocks = assembled.blocks
            if surface.prompt_catalogue:
                blocks = (*blocks, render_tool_catalogue(surface.prompt_catalogue))
            prompt = assemble(tuple(replace(block, order=index)
                                    for index, block in enumerate(blocks)))
            ok, _, reason, _, _ = await invoke.run(
                runner=agent.runner, doctrine=doctrine, tools=surface.tools, pre_fill=[],
                guard=handle.guard, tool_call_log=handle.tool_call_log,
                reasoning_watch=handle.reasoning_watch, tool_hooks=surface.hooks,
                tag_message=prompt, limits=surface.limits(agent.limits),
                ctx=ctx, timeout_seconds=agent.timeout_seconds, metrics=agent.metrics,
                audience=PROACTIVE,
            )
            if (ok and reason not in deliver.BUDGET_STOP_REASONS and not admission.withheld
                    and not handle.deliverer.attempted):
                await self._acts.record_silence(
                    authority, reason=handle.deliverer.last_outcome or None,
                )


def _floor(
    claim: ProactiveClaim, ctx: TagContext, now: datetime, display_name: str,
) -> tuple[ContextBlock, ...]:
    blocks = (
        ContextBlock(name="why_now", body=compose_body(
            f"intention_due at {claim.due.eligible_at.isoformat()}")),
        ContextBlock(name="transcript", body=compose_body(render_transcript(
            ctx.transcript, display_name=display_name, now=now)), when_empty="self_close"),
        render_members(MembersView("this room", ctx.room_members)),
        render_clock(now=now, last_session_ms=None),
        render_presence(ctx.presence or Presence.minimal(
            ctx.message.platform, direct=ctx.message.conversation_type == "dm")),
    )
    return tuple(sorted(blocks, key=lambda block: _FLOOR_ORDER[block.name]))
