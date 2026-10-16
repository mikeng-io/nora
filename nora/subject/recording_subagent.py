"""The sub-agent worker an eval subject composes: a recorder, never a worker.

A routing case needs the four `subagent_*` tools PRESENT (the registry withholds
them unless `[subagent] enabled`) and needs `subagent_create` to succeed, so the
model's choice to call it is what is measured. It must not run a real worker: that
is a Strands loop on the Dreamer's model, minutes of model calls, for a case that
only asks whether she delegated.

So under `[subagent] enabled` the subject registers this in the worker's place:
`launch` records the request and returns an id, `poll` stays RUNNING for ever (the
compute poller therefore never delivers), `cancel` is a no-op, nothing calls a model.

THE SEAM. `build_dependencies` takes no collaborators, deliberately (this package's
docstring), and an eval branch inside production composition is the thing
the boundaries rules refuse. The one name composition resolves at call time for
this is the module-level `build_subagent_backend`; `recording_subagent_worker`
replaces it for the span of one composition and puts it back.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nora.domain.ports import JobResult, JobStatus, LaunchRequest


@dataclass(frozen=True, slots=True)
class _RecordedStatus:
    job_id: str
    running: bool = True
    started_at: datetime | None = None
    rounds: int = 0
    last_tool: str | None = None
    trace_id: str | None = None


class RecordingSubagentBackend:
    """A `ComputeBackend` + `SubagentWorker` that runs nothing."""

    def __init__(self) -> None:
        self.launched: list[LaunchRequest] = []
        self.cancelled: list[str] = []
        self._ids: set[str] = set()

    async def launch(self, *, request: LaunchRequest) -> str:
        job_id = uuid.uuid4().hex
        self.launched.append(request)
        self._ids.add(job_id)
        return job_id

    async def poll(self, *, job_id: str) -> JobResult:
        return JobResult(status=JobStatus.RUNNING)

    async def cancel(self, *, job_id: str) -> None:
        self.cancelled.append(job_id)

    def status(self, job_id: str) -> _RecordedStatus | None:
        return _RecordedStatus(job_id=job_id) if job_id in self._ids else None


@contextlib.contextmanager
def recording_subagent_worker() -> Iterator[None]:
    """Make `build_dependencies` register a `RecordingSubagentBackend` for kind
    `subagent` instead of the real worker, for the duration of the block.

    Re-entrant, so two subjects composing at once (the runner may parallelise)
    cannot restore each other's patch: the depth and the original live on the
    installed replacement itself (no module-level state), and the original goes
    back only when the LAST block leaves."""
    from nora.app import composition

    current: Any = composition.build_subagent_backend
    if getattr(current, "_recording", False):
        recorder = current
    else:
        def recorder(**_kwargs: Any) -> RecordingSubagentBackend:
            return RecordingSubagentBackend()

        recorder._recording = True  # type: ignore[attr-defined]
        recorder._depth = 0  # type: ignore[attr-defined]
        recorder._original = current  # type: ignore[attr-defined]
        composition.build_subagent_backend = recorder  # type: ignore[assignment]
    recorder._depth += 1
    try:
        yield
    finally:
        recorder._depth -= 1
        if recorder._depth == 0:
            composition.build_subagent_backend = recorder._original
