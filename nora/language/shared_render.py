"""Renders the unit's shared cognitive state, ONCE, for every audience.

`design/notebook-memory-types/20-shared-cognitive-state.md`: the tag path's
prefill and the Cognition session's agenda both draw from this — two
renderers over one `SharedView` would be two answers to «what stands».
`nora.agent.shared_state` reads the stores; this module only formats.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from nora.domain.blocks import ContextBlock
from nora.domain.prompt import age, escape
from nora.domain.shared_state import SharedView


def render_shared_state(
    view: SharedView | None, *, now_ms: int, tz: tzinfo = UTC,
) -> ContextBlock | None:
    """The `<shared_cognitive_state>` block, or `None` for `nothing_found`.

    `unavailable` still renders — the coverage line is exactly what a model
    needs to not conclude «nothing is pending» from a failed read.
    """
    if view is None or view.status == "nothing_found":
        return None
    now = datetime.fromtimestamp(now_ms / 1000, tz=tz)
    coverage = " · ".join(f"{name} {status}" for name, status in view.sources)
    open_entries = [
        e for e in view.entries
        if e.kind in ("note", "decision", "commitment", "thread")
        and e.status == "open"
    ]
    waiting = [
        e for e in view.entries
        if e.kind in ("act", "wake") and e.status == "pending"
    ]
    recent = [
        e for e in view.entries
        if e.kind == "act" and e.status != "pending"
    ]
    said = [e for e in view.entries if e.kind == "send"]
    findings = [e for e in view.entries if e.kind == "finding"]
    positions = [e for e in view.entries if e.kind == "standing"]
    concluded = [e for e in view.entries if e.kind == "conclusion"]
    lines = [
        f"the unit's shared state as of {now.isoformat(timespec='seconds')} "
        f"— status={view.status}",
        f"coverage: {coverage}",
    ]
    if positions:
        lines.append("## Positions")
        for e in positions:
            if not e.permitted:
                lines.append("- a position in another room · withheld")
                continue
            ago = f" · {age(e.at_ms, now)} ago" if e.at_ms else ""
            lines.append(f"- {e.scope}{ago} · {e.subject}")
            if e.detail:
                lines.append(f"    {e.detail}")
    if open_entries:
        lines.append("## Open")
        for e in open_entries:
            if not e.permitted:
                lines.append(f"- {e.kind} · open in another room · withheld")
                continue
            rev = f" rev{e.revision}" if e.revision else ""
            head = f"- {e.kind} {e.id}{rev} · {e.scope} · {e.subject}"
            lines.append(f"{head}\n    {e.detail}" if e.detail else head)
    if waiting:
        lines.append("## Waiting")
        for e in waiting:
            when = ""
            if e.at_ms:
                if e.at_ms <= now_ms:
                    when = f"due {age(e.at_ms, now)} ago"
                else:
                    due_at = datetime.fromtimestamp(e.at_ms / 1000, tz=tz)
                    when = f"due {due_at.strftime('%m-%d %H:%M')}"
            if not e.permitted:
                lines.append(
                    f"- {e.kind} · waiting in another room · withheld")
                continue
            head = f"- {e.kind} {e.id}"
            if e.kind == "act":
                head += f" · {e.destination} · {e.action or 'remind'}"
                if e.subject_ref:
                    head += f" · {e.subject_ref}"
            if when:
                head += f" · {when}"
            lines.append(f"{head} · {e.subject}" if e.subject else head)
    if recent:
        lines.append("## Recent")
        for e in recent[:12]:
            if not e.permitted:
                lines.append("- an act in another room · withheld")
                continue
            ago = f" · {age(e.at_ms, now)} ago" if e.at_ms else ""
            delivery = (
                "delivered" if e.delivery == "sent"
                else "may have sent" if e.delivery == "unknown"
                else "no send" if e.delivery == "none" else e.status
            )
            lines.append(
                f"- {e.kind} {e.id} · {e.destination} · {e.status} "
                f"({delivery}){ago} · {e.subject}")
    if said:
        lines.append("## Said lately")
        for e in said[:8]:
            if not e.permitted:
                lines.append("- said something in another room · withheld")
                continue
            ago = f" · {age(e.at_ms, now)} ago" if e.at_ms else ""
            lines.append(f"- {e.scope}{ago} · {e.subject}")
    if concluded:
        lines.append("## Concluded lately")
        for e in concluded[:8]:
            if not e.permitted:
                lines.append("- a conclusion in another room · withheld")
                continue
            ago = f" · {age(e.at_ms, now)} ago" if e.at_ms else ""
            lines.append(f"-{ago} · {e.subject}")
            if e.detail:
                lines.append(f"    {e.detail}")
            evidence = " · ".join((*e.refs, e.provenance) if e.provenance else e.refs)
            if evidence:
                lines.append(f"    sources: {evidence}")
    for item in view.unresolved:
        lines.append(f"- unresolved · {item}")
    for e in findings[:8]:
        if not e.permitted:
            lines.append("- a finding in another room · withheld")
            continue
        ago = f" · {age(e.at_ms, now)} ago" if e.at_ms else ""
        lines.append(f"- found{ago} · {e.subject}")
        if e.detail:
            lines.append(f"    {e.detail}")
    return ContextBlock(
        name="shared_cognitive_state", body=escape("\n".join(lines)),
    )
