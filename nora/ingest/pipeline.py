"""`IngestPipeline` — runs the five ambient stages in order (spec §4.3).

A readable table of contents, not a decision-maker: every branch belongs to
the stage that owns it (`nora.ingest.stages.*`). This module only sequences
the five calls and assembles the result — nothing here inspects message
content, catches an exception, or decides anything.

`message=None` in the returned `IngestResult` means the event was dropped —
either the adapter declined to normalize it (stage 1) or the audit write
failed (stage 2, which drops rather than re-raises). A message that made it
past both is always paired with a `decision`, even for an unmapped sender
(D6) or a policy error (stage 5 degrades closed rather than propagating).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import AuditStore, EngageDecision, EngagePolicy, MemoryStore
from nora.persons import Persons

from .stages import audit as audit_stage
from .stages import gate as gate_stage
from .stages import identity as identity_stage
from .stages import memory_feed as memory_feed_stage
from .stages import parse as parse_stage
from .stages.parse import ParsingAdapter


@dataclass(frozen=True)
class IngestResult:
    message: NormalizedMessage | None
    decision: EngageDecision | None


class IngestPipeline:
    def __init__(
        self,
        *,
        adapter: ParsingAdapter,
        audit: AuditStore,
        memory: MemoryStore,
        persons: Persons,
        policy: EngagePolicy,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], None],
    ) -> None:
        self._adapter = adapter
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._policy = policy
        self._metrics = metrics
        self._spawn = spawn

    async def run(self, *, raw_event: dict) -> IngestResult:
        m = self._metrics
        message = await parse_stage.run(adapter=self._adapter, raw_event=raw_event, metrics=m)
        if message is None:
            return IngestResult(message=None, decision=None)

        recorded = await audit_stage.run(message=message, audit=self._audit, metrics=m)
        if not recorded:
            return IngestResult(message=None, decision=None)

        person = await identity_stage.run(message=message, persons=self._persons)
        await memory_feed_stage.run(
            message=message, person=person, memory=self._memory, metrics=m, spawn=self._spawn,
        )

        decision = await gate_stage.run(message=message, policy=self._policy, metrics=m)
        return IngestResult(message=message, decision=decision)
