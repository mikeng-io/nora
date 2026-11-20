"""Compose the graph wake on the Dreamer runtime and the shared admission."""

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

import nora.memory as memory
from nora.access import IdentityAccess
from nora.agent.dreamer import TOOL_RESULT_CAPS, DreamerRuntime
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.app.cognition_session import CognitionAgentRunner
from nora.app.cognition_wiring import cognition_budget
from nora.app.review_pass import ReviewPass
from nora.app.startup import StartupError
from nora.app.wake_debrief import WakeDebriefRetry
from nora.app.wake_floor import WakeRoomFloor
from nora.app.wake_pass import WakePassRunner, WakeRooms
from nora.app.wake_poller import WakePoller
from nora.app.wake_rooms import WakeRoomSlots
from nora.config import Config
from nora.domain.media_join import MediaJoin
from nora.engagement.documents_context import DocumentsContextReader
from nora.engagement.self_presence import PresenceReader
from nora.engagement.slots import ConversationSlots
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.record_query import SqlRecordQuery
from nora.instructions.dreamer import DreamerInstructions
from nora.instructions.frontmatter import InstructionError
from nora.obs.metrics import Metrics
from nora.policy import Policy


def build_wake(
    cfg: Config, runtime: DreamerRuntime | None, instructions: DreamerInstructions,
    engine: AsyncEngine, access: IdentityAccess, lineage: LineagePolicy, policy: Policy,
    debrief: Any, slots: ConversationSlots, audit: SqlRecordStore,
    presence: PresenceReader, documents: DocumentsContextReader | None,
    screen: Callable[[str], str | None], graph_reads: Any,
    metrics: Metrics, clock: Callable[[], datetime], rosters: Mapping[str, Any], registry: Any,
    content_checks: bool, media_join: MediaJoin | None,
    *, review: tuple[CognitionAgentRunner, str] | None = None,
) -> WakePoller | None:
    if not cfg.cognition.enabled:
        return None
    if runtime is None:
        raise StartupError("[cognition] enabled needs a Dreamer runtime")
    try:
        instructions.for_objective("wake")
    except InstructionError as exc:
        raise StartupError(f"Wake objective error: {exc}") from exc

    def room_for(platform: str, id_: str) -> Room:
        return Room(
            platform=platform, conversation_id=id_, roster_source=rosters.get(platform),
            access=access, self_ids=registry.self_aliases(),
        )

    zone = ZoneInfo(cfg.cognition.timezone)
    floor = WakeRoomFloor(
        policy, access, room_for, SqlRecordQuery(engine), audit, presence, documents, screen,
        display_name=cfg.doctrine.display_name, record_chars=cfg.cognition.record_chars,
        media_join=media_join,
    )
    graph = graph_reads or memory.wire.graph_read_tools(engine, access, lineage, None, None)
    surface = memory.wire.WakeToolSurface(
        debrief.admission, graph, room_for, metrics, content_checks=content_checks,
        offered=memory.wire.WAKE_TOOLS, allowed_operations=memory.wire.WAKE_OPERATIONS, clock=clock,
    )
    _, _, model = cfg.endpoint("dreamer", cfg.dreamer_model())
    runner = build_wake_runner(
        cfg, runtime, instructions, floor, debrief.assembler, surface, metrics,
        revisions=debrief.revisions, clock=lambda: clock().astimezone(zone), model=model,
    )
    room_slots = WakeRoomSlots(policy, slots)
    retry = WakeDebriefRetry(debrief, debrief.store, room_slots, slots)
    review_pass = None
    if review is not None:
        judge, judge_model = review
        review_surface = memory.wire.WakeToolSurface(
            debrief.admission, graph, room_for, metrics, content_checks=content_checks,
            offered=memory.wire.WAKE_HISTORY_TOOLS, allowed_operations=("create_claim",),
            allowed_parts=memory.wire.REVIEW_PARTS, clock=clock,
        )
        review_pass = ReviewPass(
            lessons=memory.wire.SqlReviewLessons(engine), rooms=floor, assembler=debrief.assembler,
            surface=review_surface, admission=debrief.admission, agent=judge,
            instructions=instructions, budget=cognition_budget(cfg.dreamer.budget_for("cognition")),
            model=judge_model, revisions=debrief.revisions, policy=policy,
        )
    return WakePoller(memory.wire.wake_claims(engine), runner, retry, policy, metrics, clock,
                      config=cfg.cognition, review=review_pass)


def build_wake_runner(
    cfg: Config, runtime: DreamerRuntime, instructions: DreamerInstructions, rooms: WakeRooms,
    assembler: Any, surface: memory.wire.WakeToolSurface, metrics: Metrics, *,
    revisions: Callable[[], Mapping[str, str | None]], model: str,
    occasion: memory.PassName = "wake", clock: Callable[[], datetime] | None = None,
) -> WakePassRunner:
    from nora.app.cognition_agent_runner import DreamerAgentRunner

    return WakePassRunner(
        rooms=rooms, assembler=assembler, surface=surface, instructions=instructions,
        agent=DreamerAgentRunner(runtime=runtime, metrics=metrics, agent_name=occasion,
                                 result_caps=TOOL_RESULT_CAPS, bound_tool_arguments=True),
        budget=cognition_budget(cfg.dreamer.budget_for("cognition")),
        model=model, revisions=revisions, occasion=occasion, clock=clock,
    )
