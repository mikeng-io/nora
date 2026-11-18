"""Envelope entry for guarded delivery and deterministic progress."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol, TypeVar

from nora.domain.models import ConversationTarget, TagContext
from nora.memory import (
    ActAuthority,
    ActEnvelope,
    Occasion,
    Outcome,
    PassContext,
    act_pass,
    declare_act,
)
from nora.obs.tracing import current_trace_id


class _TargetScope(Protocol):
    def room_unit(self, platform: str, room: str) -> str | None: ...

    async def person_id(self, platform: str, account: str | None) -> str | None: ...


class _DeliveryResult(Protocol):
    @property
    def ok(self) -> bool: ...


_Result = TypeVar("_Result", bound=_DeliveryResult)


class DeliveryActs:
    def __init__(
        self, envelope: ActEnvelope, scope: _TargetScope,
        revisions: Callable[[], Mapping[str, str | None]],
    ) -> None:
        self._envelope = envelope
        self._scope = scope
        self._revisions = revisions

    def pass_scope(
        self, ctx: TagContext, occasion: Occasion, model: str, doctrine: str,
        *, intention: str | None = None,
    ) -> AbstractContextManager[None]:
        message = ctx.message
        unit = message.admitted_unit or self._scope.room_unit(
            message.platform, message.conversation_id,
        )
        if unit is None:
            return nullcontext()
        revisions = self._revisions()
        context = PassContext(
            unit, message.conversation_id, message.conversation_type == "dm",
            occasion, "engagement", ctx.tag_id, current_trace_id() or "", model,
            revisions.get("rev_code") or "", doctrine, "",
            revisions.get("rev_config") or "", revisions.get("rev_identity") or "",
            revisions.get("rev_policy") or "",
        )
        return act_pass(
            ActAuthority(
                unit, ctx.tagger.person_id if ctx.tagger else None, intention,
                message.conversation_id, context,
            ),
            self._envelope,
        )

    async def run(
        self, executor: str, target: ConversationTarget, event: str, ordinal: int,
        invoke: Callable[[], Awaitable[_Result]], *, author: str | None = None,
        from_pass: bool = True,
    ) -> _Result | None:
        unit = self._scope.room_unit(target.platform, target.conversation_id)
        person = await self._scope.person_id(target.platform, author)
        act = declare_act(
            executor, f"{target.platform}:{target.conversation_id}", event=event, ordinal=ordinal,
            authority=ActAuthority(unit, person, room=target.conversation_id),
            from_pass=from_pass,
        )
        return await self._envelope.perform(
            act, invoke, lambda result: Outcome(
                "done" if result.ok else "uncertain" if getattr(result, "uncertain", False)
                else "failed"
            ),
        )
