"""`render_computer`: the `<computer>` block .

A deterministic window, never a model's fold, over `ComputerView`:

- open tasks: the `max_open_sessions` newest, then «+N more»;
- lapsed tasks (expired within `lapsed_visible`): `max_lapsed_sessions`;
- tmux sessions: `max_tmux`, newest first, running or idle;
- processes the room's user owns (`nohup … &` jobs have no tmux session):
  `max_processes`, newest first;
- files changed within `recent_file_days`: `max_files`, newest first;
- links the room made with `computer_export`: `max_links`, newest
  first, each with the artifact_id that revises it under the same link;
- recent commands: `max_runs`, the command cut to 80 characters, with its
  exit code.

**An age on EVERY line**, the «+N more» lines included: every judgement this
block feeds turns on WHEN (is it still going, is it stale), and a line with
no time cannot ground one (AGENTS.md, the spine).

**A hard ceiling.** When the body's estimated tokens pass `max_tokens`, the
OLDEST item lines go first, whatever their section, and one closing line
says how many went and how recent the newest of them was. The estimate is
deliberately high (an ASCII token per three characters, a token per other
character), so a CJK-heavy objective cannot slip a block past its ceiling.

Every line is collapsed to one line and escaped (`render_bullets`): an
objective is a member's words and a command is a model's, and neither may
forge a line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain.blocks import ContextBlock, render_bullets
from ..domain.computer_context import ComputerView, SessionLine
from ..domain.computer_sessions import age, progress_text


@dataclass(frozen=True)
class _Line:
    text: str
    at: datetime | None  # None: a summary line, never dropped by the ceiling


#: Why a task stopped, in words that cannot be read as work lost: an expiry
#: never deletes a file or ends a job.
_WHY_STOPPED = {
    "idle": "no activity; files kept",
    "jobs_ended": "its tmux jobs ended; files kept",
    "running_past_limit": "no activity for days while a job ran; the job was left running",
}


def estimate_tokens(text: str) -> int:
    """A deliberately high token estimate: ASCII runs about four characters a
    token (counted as three), anything else about one."""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return -(-ascii_chars // 3) + (len(text) - ascii_chars)


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _session(line: SessionLine, now: datetime, *, lapsed: bool) -> _Line:
    s = line.session
    objective = f'"{s.objective}"' if s.objective else "(no message)"
    if lapsed:
        why = _WHY_STOPPED.get(s.expired_reason or "", "stopped")
        head = f"lapsed task, stopped {age(s.expired_at or s.updated_at, now)} ({why})"
    else:
        head = f"open task, {line.shown}, last active {age(s.last_activity_at, now)}"
    progress = (progress_text(line.progress, now=now) if line.progress is not None
                else "progress unknown: its record could not be read")
    return _Line(f"{head} · opened {age(s.created_at, now)} · {objective} · {progress}",
                 s.last_activity_at if not lapsed else (s.expired_at or s.updated_at))


def _more(n: int, what: str, oldest: datetime, now: datetime) -> _Line:
    return _Line(f"+{n} more {what}, the oldest {age(oldest, now)}", None)


def render_computer(view: ComputerView | None) -> ContextBlock | None:
    """The block, or None when there is no view (the room is not using the
    computer, or `[computer]` is off)."""
    if view is None:
        return None
    now, caps = view.now, view.caps
    lines: list[_Line] = []

    shown = view.open_sessions[: caps.max_open_sessions]
    lines += [_session(s, now, lapsed=False) for s in shown]
    rest = view.open_sessions[caps.max_open_sessions:]
    if rest:
        lines.append(_more(len(rest), "open tasks",
                           min(s.session.last_activity_at for s in rest), now))

    lapsed = view.lapsed_sessions[: caps.max_lapsed_sessions]
    lines += [_session(s, now, lapsed=True) for s in lapsed]
    rest = view.lapsed_sessions[caps.max_lapsed_sessions:]
    if rest:
        lines.append(_more(len(rest), "lapsed tasks",
                           min(s.session.expired_at or s.session.updated_at for s in rest),
                           now))

    if not view.machine_read:
        lines.append(_Line(f"the computer itself could not be read, checked {age(now, now)}",
                           None))
    else:
        tmux = sorted(view.tmux or (), key=lambda t: t.created, reverse=True)
        for t in tmux[: caps.max_tmux]:
            state = "running" if t.running else "idle, nothing running"
            lines.append(_Line(f'tmux "{t.name}" · {state} · started {age(t.created, now)}',
                               t.created))
        if len(tmux) > caps.max_tmux:
            lines.append(_more(len(tmux) - caps.max_tmux, "tmux sessions",
                               min(t.created for t in tmux[caps.max_tmux:]), now))
        if view.tmux is None:
            lines.append(_Line(f"running jobs could not be listed, checked {age(now, now)}",
                               None))
        # Processes the room's user owns, jobs with or without tmux. The
        # command is as given: `render_bullets` escapes every line once.
        procs = view.processes or ()
        for proc in procs[: caps.max_processes]:
            lines.append(_Line(f"process {proc.pid} `{proc.command}` · running · "
                               f"started {age(proc.started, now)}", proc.started))
        if len(procs) > caps.max_processes:
            lines.append(_more(len(procs) - caps.max_processes, "processes",
                               min(p.started for p in procs[caps.max_processes:]), now))
        if view.processes is None:
            lines.append(_Line(f"running processes could not be listed, checked "
                               f"{age(now, now)}", None))
        for f in view.files[: caps.max_files]:
            lines.append(_Line(f"file {f.path} · {_size(f.size)} · changed {age(f.mtime, now)}",
                               f.mtime))
        if len(view.files) > caps.max_files:
            lines.append(_more(len(view.files) - caps.max_files, "recent files",
                               min(f.mtime for f in view.files[caps.max_files:]), now))

    for link in view.links[: caps.max_links]:
        title = f'"{link.title}"' if link.title else "(untitled)"
        made = "revised" if link.revised else "made"
        lines.append(_Line(f"link {title} · artifact_id {link.artifact_id} · {link.url} · "
                           f"from {link.path or '(unknown path)'} · {made} {age(link.at, now)}",
                           link.at))
    if len(view.links) > caps.max_links:
        lines.append(_more(len(view.links) - caps.max_links, "links",
                           min(link.at for link in view.links[caps.max_links:]), now))

    for r in view.runs[: caps.max_runs]:
        outcome = (f"exit {r.exit_code}" if r.exit_code is not None
                   else "did not finish" if r.finished else "still running or cut off")
        lines.append(_Line(f"ran `{r.command}` · {outcome} · {age(r.at, now)}", r.at))
    if len(view.runs) > caps.max_runs:
        lines.append(_more(len(view.runs) - caps.max_runs, "commands",
                           min(r.at for r in view.runs[caps.max_runs:]), now))

    lines = _within_ceiling(lines, caps.max_tokens, now)
    body = render_bullets([line.text for line in lines], mark="")
    if not body:
        return None
    return ContextBlock(name="computer", body=body)


def _within_ceiling(lines: list[_Line], max_tokens: int, now: datetime) -> list[_Line]:
    """Drop the oldest item lines until the body fits, then say so."""
    def cost(kept: list[_Line], dropped: list[_Line]) -> int:
        # Measured on what is SENT: escaped and collapsed, `&` as `&amp;`.
        texts = [line.text for line in kept]
        if dropped:
            texts.append(_older(dropped, now).text)
        return estimate_tokens(render_bullets(texts, mark=""))

    kept: list[_Line] = list(lines)
    dropped: list[_Line] = []
    while cost(kept, dropped) > max_tokens:
        droppable = [line for line in kept if line.at is not None]
        if not droppable:
            break
        oldest = min(droppable, key=lambda line: line.at)  # type: ignore[arg-type, return-value]
        kept.remove(oldest)
        dropped.append(oldest)
    return kept + ([_older(dropped, now)] if dropped else [])


def _older(dropped: list[_Line], now: datetime) -> _Line:
    newest = max(line.at for line in dropped if line.at is not None)
    return _Line(f"+{len(dropped)} older lines left out to fit, the newest of them "
                 f"{age(newest, now)}", None)
