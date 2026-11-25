"""Ambient pass context and closing inside the fire path's room slot."""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from nora.agent.prefill import engagement_floor
from nora.agent.room import Room
from nora.domain.computer_context import ComputerView
from nora.domain.documents_context import DocumentsView
from nora.domain.models import PersonRef, TagContext
from nora.domain.self_presence import Presence
from nora.domain.subagents_context import SubagentsView
from nora.engagement.graph_context import EngagementGraph
from nora.engagement.stages import graph_debrief
from nora.engagement.subagents_context import SubagentsContextReader
from nora.memory import ActAuthority, seed_pass_shown


@dataclass(frozen=True)
class AmbientEngagement:
    graph: EngagementGraph
    debrief: graph_debrief.Wiring | None = None
    subagents: SubagentsContextReader | None = None

    async def floor(
        self, ctx: TagContext, *, presence: Awaitable[Presence | None],
        computer: Awaitable[ComputerView | None], documents: Awaitable[DocumentsView | None],
        members: Awaitable[tuple[PersonRef, ...]],
    ) -> TagContext:
        msg = ctx.message

        async def room_jobs() -> SubagentsView | None:
            if self.subagents is None:
                return None
            return await self.subagents.read(
                platform=msg.platform, conversation_id=msg.conversation_id, person_id=None,
            )

        where, machine, files, people, jobs = await asyncio.gather(
            presence, computer, documents, members, room_jobs(),
        )
        return replace(ctx, presence=where, computer=machine, documents=files,
                       room_members=people, subagents=jobs)

    async def prompt(
        self, ctx: TagContext, *, unit: str | None, display_name: str, speaker: str,
        context_window: int, now: datetime, catalogue: str, header: str, room: Room,
        proactive_event: bool = False, show_nora_profile: bool = True,
    ) -> tuple[list[dict[str, Any]], str]:
        floor = engagement_floor(
            ctx, display_name=display_name, tagger_name=speaker,
            context_window=context_window, now=now, catalogue=catalogue,
            proactive_event=proactive_event,
        )
        prefill, message, shown = await self.graph.prompt(
            "proactive" if proactive_event else "ambient",
            ctx, floor=floor, unit=unit, display_name=display_name,
            now=now, header=header, room=room, show_nora_profile=show_nora_profile,
        )
        seed_pass_shown(shown)
        return prefill, message

    async def retry(self, unit: str | None, platform: str, room: str) -> None:
        await graph_debrief.retry(self.debrief, unit, platform, room)

    async def close(self, authority: ActAuthority | None, ctx: TagContext) -> None:
        await graph_debrief.run(self.debrief, authority, ctx.message, ctx.transcript)
