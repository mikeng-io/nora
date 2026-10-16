"""`Dispatcher` — the glue between the two pipelines that `Handler.on_event`
used to be in one method (`nora/handler.py:63-97`, deleted in the same commit
as this module).

Neither `nora.ingest` nor `nora.engagement` may depend on the other — they are
siblings in the layers contract — so the thing that runs `IngestPipeline`,
reads its `EngageDecision`, and hands an admitted tag to `EngagementPipeline`
cannot live in either package. It lives here, in `nora.app`, next to the
composition root that builds it, and — like `nora.app.runtime` — takes
dependencies only, never `Config`.
"""

from __future__ import annotations

import uuid

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngagementRequest
from nora.engagement.pipeline import EngagementPipeline
from nora.ingest.pipeline import IngestPipeline

from .participation import ParticipationTrigger
from .runtime import BackgroundTasks


class Dispatcher:
    """`on_message` is what a platform adapter's `listen()` loop calls per
    already-parsed message — the direct replacement for `Handler.on_event`.
    Renamed from `on_event` (which took a raw, platform-shaped dict) when the
    platforms phase moved parsing onto the adapter itself
    (`SignalAdapter.listen`, Trap B): `PlatformAdapter` deliberately has no
    `parse()`, so by the time anything reaches this class the event is
    already a `NormalizedMessage`, not a dict of unknown shape.

    Ingest always runs, awaited inline: it is deterministic and never
    constructs an Agent (R-1), so there is nothing in it worth spawning off
    the listener's path. A tag that clears the engage gate is different — a
    model call can take seconds or hang — so it is handed to
    `EngagementPipeline.run` through `background.spawn` rather than awaited
    here, the same fire-and-forget shape `Handler._spawn(self.handle_tag(msg))`
    had. `EngagementPipeline.run` itself owns admission (debounce, in-flight,
    self-tag) as its own first step, so two rapid events racing each other
    resolve inside the pipeline, not here — see `nora.engagement.pipeline`'s
    own docstring and `tests/unit/test_engagement_pipeline.py`'s concurrency
    tests, which drive it the same way (`asyncio.create_task` + a short
    yield) that this dispatcher does in production.
    """

    def __init__(
        self,
        *,
        ingest: IngestPipeline,
        engagement: EngagementPipeline,
        background: BackgroundTasks,
        participation: ParticipationTrigger | None = None,
    ) -> None:
        self._ingest = ingest
        self._engagement = engagement
        self._background = background
        # The participation occasion's trigger, None unless
        # `[participation] enabled`. It is NOT a third pipeline: it only NOTES
        # a candidate message; the quiescence window owns every later
        # decision, and the turn — if one runs — goes through
        # `FirePath.speak`, never through `EngagementPipeline`.
        self._participation = participation

    async def on_message(self, message: NormalizedMessage) -> None:
        result = await self._ingest.run(message=message)
        if result.message is None or result.decision is None:
            return
        if not result.decision.engage:
            # The room verdict (EngageDecision.relevance_gate): an untagged message in an opted-in
            # room is NOT admitted — no R-9 debounce semantics, no
            # EngagementRequest, no agent now, and no gate call yet. It is only
            # NOTED with the quiescence trigger; whether a window closes, whether
            # the Relevance Gate then says GO, and whether a turn runs at all are
            # the trigger's own cadence and the gate's own verdict.
            if result.decision.relevance_gate and self._participation is not None:
                self._participation.note_message(result.message)
            return
        request = EngagementRequest(
            message=result.message, decision=result.decision, tag_id=uuid.uuid4().hex,
        )
        self._background.spawn(self._engagement.run(request))
