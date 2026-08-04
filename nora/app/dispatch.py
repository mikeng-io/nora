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
from typing import Any

from nora.domain.ports import EngagementRequest
from nora.engagement.pipeline import EngagementPipeline
from nora.ingest.pipeline import IngestPipeline

from .runtime import BackgroundTasks


class Dispatcher:
    """`on_event` is what a platform adapter's `listen()` loop calls per raw
    event — the direct replacement for `Handler.on_event`.

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
    ) -> None:
        self._ingest = ingest
        self._engagement = engagement
        self._background = background

    async def on_event(self, raw_event: dict[str, Any]) -> None:
        result = await self._ingest.run(raw_event=raw_event)
        if result.message is None or result.decision is None or not result.decision.engage:
            return
        request = EngagementRequest(
            message=result.message, decision=result.decision, tag_id=uuid.uuid4().hex,
        )
        self._background.spawn(self._engagement.run(request))
