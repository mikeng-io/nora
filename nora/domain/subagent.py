"""The sub-agent's typed brief and the facts code adds to it.

The worker starts blank: no transcript, no peer card, no room block. What it needs is in the
brief, interpreted ONCE by Nora when she launched it, and the worker never re-reads the chat.
Two kinds of field, kept apart because the model reading them must be able to tell them
apart:

- `SubagentBrief`: what NORA wrote. The objective, what was settled with the room (each
  decision with who made it), the workspace paths to work from, the documents to revise and
  what done looks like. It is the launch payload (`LaunchRequest.payload`), JSON-shaped through
  `as_payload`, and is what `compute_jobs.payload` keeps in full.
- `SubagentLaunch`: what CODE adds and the model never chooses: the job, the room, the
  requester, the launching turn, the time and the timezone.

Rendered by one formatter (`nora.language.subagent_render`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from nora.domain.stimuli import LIMIT_STOP_REASONS

#: The kind a sub-agent job's row carries (`compute_jobs.kind`); the poller resolves the
#: backend by it.
SUBAGENT_KIND = "subagent"

#: A job's title is a name the room would recognise: required at launch, at most this long.
TITLE_CHARS = 60

#: Where a job works, in the room's computer workspace (relative to it).
JOB_FOLDER_ROOT = "projects"


#: Why a job ended `failed` (`compute_jobs.failure_reason`): the loop's own bounds, the wall
#: clock, and the worker's own failures. `lost` (a restart) is the poller's, written by boot
#: recovery, not by the worker.
FAILURE_REASONS: tuple[str, ...] = (
    *LIMIT_STOP_REASONS, "limit_max_tokens", "timeout", "no_report", "tools", "bad_inputs",
    "inputs_unavailable", "error",
)

#: What `nora_subagent_jobs_total{outcome}` counts: a job that finished, was cancelled, or
#: failed for one of `FAILURE_REASONS`. Derived, so the metric's vocabulary cannot drift.
SUBAGENT_OUTCOMES: tuple[str, ...] = ("done", "cancelled", *FAILURE_REASONS)


def workspace_path(raw: str) -> str | None:
    """A workspace-relative path with nothing that leaves the workspace, or None. The one rule
    `subagent_create` refuses a bad input by and the runner stages by."""
    path = PurePosixPath(raw.strip())
    if not raw.strip() or path.is_absolute() or ".." in path.parts or str(path) == ".":
        return None
    return str(path)


def job_folder(job_id: str) -> str:
    return f"{JOB_FOLDER_ROOT}/{job_id}"


@dataclass(frozen=True)
class Decision:
    """One thing settled before the launch. `by` says who decided: a person's name, or
    `nora` for an assumption she made without asking, so the worker can tell "the operator
    decided" from "Nora assumed"."""

    question: str
    answer: str
    by: str


@dataclass(frozen=True)
class SubagentBrief:
    objective: str
    #: A short name the room would recognise (<= `TITLE_CHARS`), written by Nora. Empty only
    #: on a payload that lacks the field: read it through `job_title`.
    title: str = ""
    decisions: tuple[Decision, ...] = ()
    inputs: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    deliverable: str = ""

    def as_payload(self) -> dict[str, Any]:
        """The brief as JSON-shaped data (lists, never tuples, so the row reads back the same
        from the in-memory store and from JSONB), for `compute_jobs.payload`."""
        out = asdict(self)
        for key in ("decisions", "inputs", "document_ids"):
            out[key] = list(out[key])
        return out

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SubagentBrief:
        return cls(
            objective=str(payload.get("objective", "")),
            title=str(payload.get("title", "") or ""),
            decisions=tuple(
                Decision(str(d.get("question", "")), str(d.get("answer", "")), str(d.get("by", "")))
                for d in payload.get("decisions") or ()
            ),
            inputs=tuple(str(p) for p in payload.get("inputs") or ()),
            document_ids=tuple(str(i) for i in payload.get("document_ids") or ()),
            deliverable=str(payload.get("deliverable", "")),
        )


def job_title(payload: Mapping[str, Any] | None, objective: str) -> str:
    """The job's title: the one Nora wrote, or, for a row with none, the objective cut
    to `TITLE_CHARS` on one line. Never empty unless the objective is."""
    written = " ".join(str((payload or {}).get("title") or "").split())
    if written:
        return written[:TITLE_CHARS]
    one = " ".join(objective.split())
    return one if len(one) <= TITLE_CHARS else one[: TITLE_CHARS - 1].rstrip() + "…"


def written_title(payload: Mapping[str, Any] | None) -> str:
    """The title Nora wrote, or `""` for a payload that has none (a delivery then names the
    job by its task text)."""
    return " ".join(str((payload or {}).get("title") or "").split())[:TITLE_CHARS]


@dataclass(frozen=True)
class SubagentLaunch:
    """What code adds to a brief. Never a model argument."""

    job_id: str
    platform: str
    conversation_id: str
    conversation_type: str
    requester_person_id: str
    requester_name: str
    tag_id: str | None
    launched_at: datetime
    tz: str
    folder: str = field(default="")
