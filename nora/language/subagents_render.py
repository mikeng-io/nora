"""`render_subagents`: the `<subagents>` block.

A deterministic window, never a model's fold, over `SubagentsView`: one line per job of THIS
room, running first and then finished, each newest first. **An age on every line** (when it
started, when it finished): whether a job is overdue, whether its result is news, whether to
mention it again all turn on WHEN (AGENTS.md, the spine).

A line is led by its state, so `done` cannot be read of a job that is running. `delivered to
the room` is said where the record says the result reached it, and `not delivered` where it
does not, because a result already in the room is not to be announced again. A running job's
progress is the backend's own figure, or the line says it is unknown; it is never estimated.

An objective is a member's words restated, a report and a title are a model's: each is
collapsed to one line, cut, stripped of the straight quote its line wraps it in, and escaped
by `render_bullets`, so none can forge a line, a field or the closing tag. `row.error` never
reaches here; the failure is the controlled word `failure_reason`.
"""

from __future__ import annotations

from ..domain.blocks import ContextBlock, render_bullets
from ..domain.computer_sessions import age
from ..domain.subagent import TITLE_CHARS as JOB_TITLE_CHARS
from ..domain.subagents_context import (
    OBJECTIVE_CHARS,
    REPORT_CHARS,
    TITLE_CHARS,
    SubagentLine,
    SubagentsView,
)
from .checkpoint_render import render_checkpoint


def _cut(text: str, limit: int) -> str:
    # A straight quote would close the quote the line wraps the text in, and what follows
    # it could then pose as the next field.
    one = " ".join(text.replace('"', "'").split())
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _line(line: SubagentLine, view: SubagentsView) -> str:
    now = view.now
    head = (f'"{_cut(line.title, JOB_TITLE_CHARS) or "(untitled)"}" · job {line.id} · '
            f'description "{_cut(line.objective, OBJECTIVE_CHARS) or "(none)"}"')
    started = f"started {age(line.created_at, now)}"
    if line.state == "running":
        progress = (
            f"{line.rounds} rounds used, last tool {line.last_tool or 'none yet'}"
            if line.progress_known else "progress unknown: no live status for this job"
        )
        return f"running · {head} · {started} · {progress}"
    if line.state == "ended":
        return (f"ended, its result has not reached the room yet · {head} · {started}")
    if line.state == "delivering":
        return f"its result is being delivered to the room now · {head} · {started}"

    finished = age(line.finished_at or line.created_at, now)
    if line.state == "cancelled":
        outcome = f"cancelled, nothing was delivered · {head} · {started} · stopped {finished}"
    else:
        what = line.state
        if line.state == "failed":
            what += f" ({_cut(line.failure_reason, 40)})" if line.failure_reason else ""
        sent = ("delivered to the room already" if line.delivered
                else "not delivered: the record does not show it reached the room")
        outcome = f"{what} · {head} · {started} · finished {finished} · {sent}"
    parts = [outcome]
    if line.report:
        parts.append(f'report "{_cut(line.report, REPORT_CHARS)}"')
    if line.documents:
        parts.append("documents " + ", ".join(
            f'"{_cut(title, TITLE_CHARS) or "(title not read)"}" (id {doc})'
            for doc, title in line.documents
        ))
    return " · ".join(parts)


def render_subagents(view: SubagentsView | None) -> ContextBlock | None:
    """The block, or None when there is no view (the sub-agent is off, the room has no job
    in the window, or the read did not come back)."""
    if view is None or not view.lines:
        return None
    rows: list[str] = []
    for line in view.lines:
        rows.append(render_bullets([_line(line, view)], mark=""))
        # a running job's own doing and next, under its line: already
        # escaped by the renderer, so it is added after `render_bullets`, never through it
        if line.state == "running" and line.checkpoint is not None:
            nested = render_checkpoint(
                line.checkpoint, None, title=line.title or "(untitled)", now=view.now,
                mode="block",
            )
            if nested:
                rows.append(nested)
    if view.omitted:
        floor = "at least " if view.omitted_at_least else ""
        rows.append(render_bullets([
            f"{floor}{view.omitted} more jobs in the window are left out; "
            "subagent_list shows them"], mark=""))
    body = "\n".join(r for r in rows if r)
    if not body:
        return None
    return ContextBlock(name="subagents", body=body)
