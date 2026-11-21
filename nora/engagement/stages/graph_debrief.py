"""Carry the recorded pass into the graph through intention admission."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar
from xml.sax.saxutils import escape

from nora.domain.blocks import BlockDeclaration, ContextBlock, assemble_declared
from nora.domain.composed import Composed, compose_body
from nora.domain.external_files import render_file_links
from nora.domain.models import NormalizedMessage, TranscriptRow
from nora.engagement.stages.debrief import ModelClient, ObjectiveSource
from nora.memory import (
    ActAuthority,
    AdmissionResult,
    CloseIntention,
    DebriefInput,
    DebriefObligation,
    IntentionProposal,
    PassContext,
    PassItem,
    PassName,
    PassScope,
    ProposalId,
    ReadingRoom,
    Refusal,
    Retire,
    Revise,
    Unavailable,
    fields,
    limit_to_delivery,
    parse_intention_operation,
    without_reasoning,
)
from nora.obs.metrics import Metrics
from nora.obs.tracing import annotate, current_trace_id, span

_OBJECTIVE = "debrief"
_BLOCKS: tuple[BlockDeclaration, ...] = (
    BlockDeclaration("debrief_items", 0, "Identify this pass's evidence and its outcomes."),
    BlockDeclaration("debrief_messages", 1, "Read the room context for those outcomes."),
    BlockDeclaration("graph_standing", 2, "Re-judge the open intentions this pass could change."),
)
log = logging.getLogger(__name__)


class Store(Protocol):
    async def begin(self, row: DebriefObligation) -> bool: ...
    async def take_oldest(self, unit: str, room: str) -> DebriefObligation | None: ...
    async def record_proposal_ids(
        self, row: DebriefObligation, proposal_ids: tuple[ProposalId, ...],
    ) -> None: ...
    async def settle(self, row: DebriefObligation, now: datetime) -> None: ...
    async def confirmed(self, row: DebriefObligation) -> bool: ...
    async def items(self, row: DebriefObligation) -> tuple[PassItem, ...]: ...
    async def room_messages(self, row: DebriefObligation) -> tuple[PassItem, ...]: ...


class Assembled(Protocol):
    @property
    def status(self) -> str: ...
    @property
    def shown(self) -> frozenset[str]: ...
    @property
    def blocks(self) -> Sequence[ContextBlock]: ...


class Assembler(Protocol):
    async def assemble(
        self, pass_name: PassName, data: DebriefInput, floor: Sequence[ContextBlock],
        scope: PassScope, now: datetime,
    ) -> Assembled: ...


class Admitter(Protocol):
    async def admit(
        self, proposals: Sequence[IntentionProposal], context: PassContext,
        shown: frozenset[str],
    ) -> AdmissionResult: ...


@dataclass(frozen=True)
class Wiring:
    store: Store
    admission: Admitter
    assembler: Assembler
    client: ModelClient
    objective: ObjectiveSource
    clock: Callable[[], datetime]
    revisions: Callable[[], Mapping[str, str | None]]
    metrics: Metrics
    timeout_seconds: float
    store_timeout_seconds: float


class _StoreUnavailableError(Exception):
    pass


T = TypeVar("T")


async def _bounded(wiring: Wiring, call: Awaitable[T]) -> T:
    try:
        return await asyncio.wait_for(call, wiring.store_timeout_seconds)
    except Exception as exc:
        raise _StoreUnavailableError from exc


async def run(
    wiring: Wiring | None, authority: ActAuthority | None, message: NormalizedMessage,
    transcript: Sequence[TranscriptRow],
) -> None:
    if wiring is None or authority is None or authority.pass_context is None:
        return
    await _run(wiring, finished=(authority, message, transcript))


async def retry(wiring: Wiring | None, unit: str | None, platform: str, room: str) -> None:
    if wiring is not None and unit is not None:
        await _run(wiring, unit=unit, room=f"{platform}:{room}")


def _obligation(
    authority: ActAuthority, message: NormalizedMessage, transcript: Sequence[TranscriptRow],
    began: datetime,
) -> DebriefObligation:
    context = authority.pass_context
    if context is None:
        raise ValueError("a debrief requires pass provenance")
    return DebriefObligation(
        context.unit, f"{message.platform}:{message.conversation_id}", context.pass_id,
        context.occasion, authority.intention, began,
        transcript[0].id if transcript else None, transcript[-1].id if transcript else None,
    )


async def _run(
    wiring: Wiring, *, unit: str = "", room: str = "",
    finished: tuple[ActAuthority, NormalizedMessage, Sequence[TranscriptRow]] | None = None,
) -> None:
    started = time.perf_counter()
    outcome: str | None = "error"
    with span("engagement.debrief"):
        try:
            if finished is None:
                row = await _bounded(wiring, wiring.store.take_oldest(unit, room))
                if row is None:
                    outcome = None
                    return
            else:
                row = _obligation(*finished, wiring.clock())
                if not await _bounded(wiring, wiring.store.begin(row)):
                    outcome = None
                    return
            if row.proposal_ids is not None:
                resolved = await _bounded(wiring, wiring.store.confirmed(row))
                outcome = "written" if resolved else "unavailable"
            else:
                outcome = await _judge(wiring, row)
            if outcome == "written":
                await _bounded(wiring, wiring.store.settle(row, wiring.clock()))
        except _StoreUnavailableError as exc:
            outcome = "unavailable"
            log.warning("Graph debrief store unavailable (%s)", type(exc.__cause__).__name__)
        except TimeoutError:
            outcome = "timeout"
        except asyncio.CancelledError:
            outcome = "cancelled"
        except BaseException:
            log.exception("Graph debrief failed")
        finally:
            if outcome is not None:
                wiring.metrics.inc("nora_debrief_total", outcome=outcome)
                wiring.metrics.observe("nora_debrief_seconds", time.perf_counter() - started)
                annotate(**{"nora.debrief.outcome": outcome})


async def _input(
    wiring: Wiring, row: DebriefObligation, now: datetime,
) -> tuple[DebriefInput, Assembled]:
    items = await wiring.store.items(row)
    messages = await wiring.store.room_messages(row)
    data = DebriefInput(tuple(item.id for item in items))
    platform, _, room = row.room.partition(":")
    attrs: tuple[tuple[str, str], ...] = (
        ("occasion", row.occasion), ("room", row.room), ("began", row.created.isoformat()),
    )
    if row.intention is not None:
        attrs += (("delivered_intention", row.intention),)
    floor = (
        ContextBlock(name="debrief_items", body=_evidence_body(items), attrs=attrs),
        ContextBlock(name="debrief_messages", body=_evidence_body(messages)),
    )
    assembled = await wiring.assembler.assemble(
        "debrief", data, floor, PassScope(ReadingRoom(platform, room), row.unit), now,
    )
    return data, assembled


def _evidence_body(rows: Sequence[PassItem]) -> Composed:
    lines = [
        json.dumps({"id": row.id, "kind": row.kind, "at": row.at.isoformat(),
                    "who": row.who, "content": without_reasoning(row.content),
                    **({"delivery": row.delivery} if row.delivery is not None else {})},
                   ensure_ascii=False, default=str)
        for row in rows
    ]
    return compose_body(escape(render_file_links("\n".join(lines))))


async def _judge(wiring: Wiring, row: DebriefObligation) -> str:
    data, assembled = await _bounded(wiring, _input(wiring, row, wiring.clock()))
    if assembled.status == "unavailable":
        return "unavailable"
    completion = await asyncio.wait_for(wiring.client.complete(
        system=wiring.objective.objective_body(_OBJECTIVE),
        prompt=assemble_declared(_BLOCKS, assembled.blocks),
    ), wiring.timeout_seconds)
    if completion.finish_reason == "length":
        return "unavailable"
    try:
        response = json.loads(completion.content)
    except ValueError:
        return "malformed"
    if not isinstance(response, dict) or not isinstance(response.get("operations"), list):
        return "malformed"
    context = _context(wiring, row)
    items = frozenset(data.items)
    parsed = [operation(raw, context, items, assembled.shown, row.intention)
              for raw in response["operations"]]
    proposals = tuple(entry for entry in parsed if not isinstance(entry, Refusal))
    if not proposals:
        return "written"
    refs = tuple(_proposal_id(proposal) for proposal in proposals)
    await _bounded(wiring, wiring.store.record_proposal_ids(row, refs))
    result = await _bounded(
        wiring, wiring.admission.admit(proposals, context, items | assembled.shown),
    )
    unresolved = tuple(ref for ref, value in zip(refs, result.outcomes, strict=True)
                       if isinstance(value, Unavailable))
    if unresolved:
        await _bounded(wiring, wiring.store.record_proposal_ids(row, unresolved))
        return "unavailable"
    return "written"


def _context(wiring: Wiring, row: DebriefObligation) -> PassContext:
    revisions = wiring.revisions()
    return PassContext(
        row.unit, row.room, False, "debrief", "engagement", row.pass_id,
        current_trace_id() or "", wiring.client.model,
        revisions.get("rev_code") or "", revisions.get("rev_instruction") or "",
        revisions.get("rev_skill") or "", revisions.get("rev_config") or "",
        revisions.get("rev_identity") or "", revisions.get("rev_policy") or "",
    )


def operation(
    raw: object, context: PassContext, items: frozenset[str],
    open_intentions: frozenset[str], intention: str | None,
) -> IntentionProposal | Refusal:
    if not isinstance(raw, Mapping) or raw.get("op") not in ("open", "revise", "drop", "close"):
        return Refusal("not_a_commit", "op")
    parsed = parse_intention_operation(raw, context)
    if isinstance(parsed, Refusal):
        return parsed
    if intention is not None and (refusal := limit_to_delivery(parsed, intention)) is not None:
        return refusal
    if not items.issuperset(fields(parsed).citations):
        return Refusal("citation_outside_pass", "citations")
    if isinstance(parsed, (CloseIntention, Retire, Revise)):
        base = parsed.id if isinstance(parsed, CloseIntention) else parsed.base
        if base not in open_intentions:
            return Refusal("stale_base", "base")
    return parsed


def _proposal_id(proposal: IntentionProposal) -> ProposalId:
    return {"id": fields(proposal).id,
            "witness": proposal.witness if isinstance(proposal, CloseIntention) else None}
