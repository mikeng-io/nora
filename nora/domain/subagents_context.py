"""`SubagentsView`: what the `<subagents>` block renders.

A plain value the reader builds and the renderer only formats, the shape
`nora.domain.documents_context` gives `<documents>`. It carries `now`, so every age is
computed against the moment the turn read, never the moment the prompt was rendered.

The view holds only what code read: the room's job rows and, for a job still running, what
the worker reports about itself. A fact the read could not get is a field that says so
(`progress_known`), never a default that reads as a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from nora.domain.checkpoint import Checkpoint

#: A finished job is shown for this long after it finished. A running one has no window.
FINISHED_WINDOW = timedelta(hours=24)
#: Jobs shown, running first, then finished, each newest first. The rest are counted.
SUBAGENTS_SHOWN = 5
#: Rows read from the store. A room that has more than this in the store is told «at
#: least», because the count of what was left out is then a floor.
ROWS_READ = 50
#: A job's description (its objective) is Nora's words restating a member's; cut to this on one
#: line. (Its title is shown whole: `nora.domain.subagent.TITLE_CHARS` already bounds it.)
OBJECTIVE_CHARS = 80
#: A report is the worker's prose; the block carries its opening, `subagent_read` the rest.
REPORT_CHARS = 200
#: A document title is a model's words, cut to this on one line.
TITLE_CHARS = 50


class RunStatus(Protocol):
    """What the backend answers for a job it still holds in memory."""

    @property
    def running(self) -> bool: ...

    @property
    def rounds(self) -> int: ...

    @property
    def last_tool(self) -> str | None: ...


class SubagentWorker(Protocol):
    def status(self, job_id: str) -> RunStatus | None: ...


@dataclass(frozen=True)
class SubagentLine:
    """One job. `state` is one of `running`, `ended` (the worker stopped and the result has
    not reached the room yet), `delivering`, `done`, `failed`, `cancelled`."""

    id: str
    state: str
    #: Nora's own name for the job (`job_title`), the first thing a line says.
    title: str
    #: The job's objective as she wrote it, the line's description.
    objective: str
    created_at: datetime
    finished_at: datetime | None = None
    #: Running only. `progress_known` is False when no worker is attached or it did not
    #: answer, and then `rounds` and `last_tool` are not to be read.
    progress_known: bool = False
    rounds: int = 0
    last_tool: str | None = None
    #: Running only: the worker's last checkpoint, from the worker's
    #: memory or else the row; `None` when it has written none.
    checkpoint: Checkpoint | None = None
    report: str | None = None
    #: `(document_id, title)`; the title is `""` when it could not be read.
    documents: tuple[tuple[str, str], ...] = ()
    failure_reason: str | None = None
    #: True only when the row records that the result reached the room.
    delivered: bool = False


@dataclass(frozen=True)
class SubagentsView:
    lines: tuple[SubagentLine, ...]
    now: datetime
    #: Jobs in the window that did not fit `SUBAGENTS_SHOWN`; `omitted_at_least` when the
    #: read itself was capped, so the number is a floor.
    omitted: int = 0
    omitted_at_least: bool = False
