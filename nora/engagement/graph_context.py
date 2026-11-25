"""Assemble the room floor and graph presentations for an engagement pass."""

import asyncio
import logging
from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, Protocol

from nora.access import IdentityAccess
from nora.agent.prefill import engagement_prefill
from nora.agent.room import Room, trigger_message_ids
from nora.domain.blocks import ContextBlock
from nora.domain.composed import compose_body
from nora.domain.lineage import LiveRoom
from nora.domain.managed import room_scope_id
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import TagContext
from nora.domain.quote_window import first_by_ts
from nora.memory import (
    AmbientInput,
    AnchorItem,
    AssembledPass,
    CalledInput,
    PassInput,
    PassName,
    PassScope,
    ProactiveInput,
    ReadingRoom,
)
from nora.obs.tracing import annotate, span

log = logging.getLogger(__name__)

EngagementPass = Literal["called", "ambient", "proactive"]
_CALLED: EngagementPass = "called"
_AMBIENT: EngagementPass = "ambient"
_PROACTIVE: EngagementPass = "proactive"


class MessageIds(Protocol):
    async def read(
        self, room: ReadingRoom, unit: str, ids: Collection[int],
    ) -> dict[int, str]: ...


class Assembler(Protocol):
    async def assemble(
        self, pass_name: PassName, data: PassInput, floor: Sequence[ContextBlock],
        scope: PassScope, now: datetime,
    ) -> AssembledPass: ...


class EngagementGraph:
    def __init__(
        self, access: IdentityAccess, ids: MessageIds | None = None,
        assembler: Assembler | None = None,
    ) -> None:
        self._access = access
        self._ids = ids
        self._assembler = assembler

    async def prompt(
        self, pass_name: EngagementPass, ctx: TagContext, *, floor: Sequence[ContextBlock],
        unit: str | None, display_name: str, now: datetime, header: str,
        row_id: int | None = None, room: Room | None = None,
        show_nora_profile: bool = True,
    ) -> tuple[list[dict[str, Any]], str, frozenset[str]]:
        msg = ctx.message
        scope = PassScope(ReadingRoom(msg.platform, msg.conversation_id), unit or "",
                          nora_name=display_name)
        with span("agent.prefill", **{"nora.tag_id": ctx.tag_id}):
            assembled = await self._assemble(
                pass_name, ctx, floor, scope, now, row_id, room, show_nora_profile,
            )
            annotate(**{
                "nora.prefill.blocks_declared": len(assembled.blocks),
                "nora.prefill.blocks_rendered": ",".join(
                    block.name for block in assembled.blocks
                    if block.body or block.when_empty != "drop"
                ),
                "nora.prefill.blocks_empty": ",".join(
                    block.name for block in assembled.blocks if not block.body
                ),
                "nora.prefill.size_chars": sum(len(block.body.text) for block in assembled.blocks),
            })
            return engagement_prefill(header), assembled.render(), assembled.shown

    async def _assemble(
        self, pass_name: EngagementPass, ctx: TagContext, floor: Sequence[ContextBlock],
        scope: PassScope, now: datetime, row_id: int | None, room: Room | None,
        show_nora_profile: bool,
    ) -> AssembledPass:
        if self._assembler is None:
            return _unavailable(floor, "graph_reader_unavailable")
        try:
            async with asyncio.timeout(12):
                try:
                    if scope.unit and not ctx.room_members and room is not None:
                        view = await room.scope()
                        live = LiveRoom(frozenset(person.person_id for person in view.people),
                                        view.unnamed, view.available, view.degraded)
                        scope = replace(scope, room=replace(scope.room, live=live))
                    data = await self._inputs(pass_name, ctx, scope, row_id, show_nora_profile)
                except Exception:
                    log.warning("Engagement anchors unavailable", exc_info=True)
                    scope = replace(scope, anchor_failure="message_anchor_read_failed")
                    event = AnchorItem(None, ctx.message.body)
                    if pass_name == _PROACTIVE:
                        data = ProactiveInput(None, event, show_nora_profile)
                    else:
                        data = CalledInput(event) if pass_name == _CALLED else AmbientInput(())
                return await self._assembler.assemble(pass_name, data, floor, scope, now)
        except TimeoutError:
            return _unavailable(floor, "graph_read_timeout")

    async def _inputs(
        self, pass_name: EngagementPass, ctx: TagContext, scope: PassScope, row_id: int | None,
        show_nora_profile: bool,
    ) -> CalledInput | AmbientInput | ProactiveInput:
        msg = ctx.message
        if pass_name == _PROACTIVE:
            actor = (ctx.tagger.person_id,) if ctx.tagger is not None else ()
            return ProactiveInput(None, AnchorItem(None, msg.body, actor), show_nora_profile)
        if pass_name == _AMBIENT:
            ids = await self._read_ids(scope, tuple(row.id for row in ctx.transcript))
            return AmbientInput(tuple(
                AnchorItem(ids.get(row.id), row.body or "", tuple(dict.fromkeys(
                    ((row.person_id,) if row.person_id else ()) + row.mention_persons
                )))
                for row in ctx.transcript
            ))
        if row_id is None:
            candidates = trigger_message_ids(
                ctx.transcript, sender_id=msg.sender_id, timestamp=msg.timestamp,
            )
            row_id = candidates[0] if len(candidates) == 1 else None
        quote = first_by_ts(ctx.transcript).get(msg.quote.quoted_ts) if msg.quote else None
        quote_id = quote.id if quote is not None else None
        ids = await self._read_ids(scope, tuple(i for i in (row_id, quote_id) if i is not None))
        identity_scope = Scope(ScopeKind.ROOM, room_scope_id(msg.platform, msg.conversation_id))
        people = [ctx.tagger.person_id] if ctx.tagger else []
        for account in sorted(msg.mentions):
            ref = await self._access.resolve_ref(msg.platform, account, scope=identity_scope)
            if ref is not None:
                people.append(ref.person_id)
        return CalledInput(
            AnchorItem(ids.get(row_id) if row_id is not None else None, msg.body,
                       tuple(dict.fromkeys(people))),
            ids.get(quote_id) if quote_id is not None else None,
        )

    async def _read_ids(self, scope: PassScope, ids: tuple[int, ...]) -> dict[int, str]:
        if not scope.unit or self._ids is None:
            return {}
        return await self._ids.read(scope.room, scope.unit, ids)


def _unavailable(floor: Sequence[ContextBlock], cause: str) -> AssembledPass:
    attrs = (("status", "unavailable"), ("cause", cause))
    blocks = tuple(ContextBlock(
        name, compose_body(""), attrs=attrs, when_empty="self_close",
    ) for name in ("graph_profile", "graph_lessons", "graph_standing", "related"))
    return AssembledPass((*blocks[:2], *floor, *blocks[2:]), "unavailable", cause)
