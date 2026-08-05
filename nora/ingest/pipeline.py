"""`IngestPipeline` — runs the four ambient stages in order (spec §4.3).

A readable table of contents, not a decision-maker: every branch belongs to
the stage that owns it (`nora.ingest.stages.*`). This module only sequences
the four calls and assembles the result — nothing here inspects message
content, catches an exception, or decides anything.

`run` takes an already-normalized `message`, not a raw event: parsing (the
former stage 1) moved onto the adapter itself
(`nora.platform.signal.adapter.SignalAdapter.listen`), which drops anything
that parses to `None` — a DM, a sync message, a self-send, an empty body —
before this pipeline ever sees it. `PlatformAdapter` deliberately has no
`parse()` (`nora.domain.ports`'s module docstring), so `IngestPipeline` is not
the place that could do this parsing generically even if it wanted to. See
Trap B in `docs/superpowers/plans/2026-08-05-nora-phase4-platforms.md`.

`message=None` in the returned `IngestResult` now means only that the audit
write failed (the audit stage drops rather than re-raises) — the only
remaining way `run` returns with nothing to show for its input. A message
that made it past that is always paired with a `decision`, even for an
unmapped sender (D6) or a policy error (the gate stage degrades closed rather
than propagating).
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


@dataclass(frozen=True)
class IngestResult:
    message: NormalizedMessage | None
    decision: EngageDecision | None


class IngestPipeline:
    def __init__(
        self,
        *,
        audit: AuditStore,
        memory: MemoryStore,
        persons: Persons,
        policy: EngagePolicy,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], None],
    ) -> None:
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._policy = policy
        self._metrics = metrics
        self._spawn = spawn

    async def run(self, *, message: NormalizedMessage) -> IngestResult:
        """`message` is already normalized — the caller (the adapter's `listen()`
        loop, via `nora.app.dispatch.Dispatcher.on_message`) never hands this a
        `None`. The `nora_messages_total` increment replaces what the deleted
        parse stage used to do right after a successful `adapter.parse()`."""
        m = self._metrics
        m.inc("nora_messages_total", platform=message.platform)

        recorded = await audit_stage.run(message=message, audit=self._audit, metrics=m)
        if not recorded:
            return IngestResult(message=None, decision=None)

        person = await identity_stage.run(message=message, persons=self._persons)
        await memory_feed_stage.run(
            message=message, person=person, memory=self._memory, metrics=m, spawn=self._spawn,
        )

        decision = await gate_stage.run(message=message, policy=self._policy, metrics=m)
        return IngestResult(message=message, decision=decision)
