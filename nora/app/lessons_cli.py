"""`nora lessons tensions` — the lessons the doctrine check refused.

Read only, through the read-only engine every report here uses. The graph's
lessons are read and retired by `nora.app.graph_lessons_cli`.
"""

from __future__ import annotations

from nora.domain.doctrine_check import Tension
from nora.domain.prompt import one_line

__all__ = ["render_tensions"]


def render_tensions(tensions: tuple[Tension, ...]) -> list[str]:
    """Each refused lesson, the law rule it pulled against (file and line), the
    check's reason and the evidence it cited, newest first."""
    if not tensions:
        return ["no tensions"]
    out: list[str] = []
    for t in tensions:
        out.append(f"{t.id}  unit={t.unit}  written_at={t.written_at}  "
                   f"law={t.doctrine_file}:{t.doctrine_line}  check={t.check_model}")
        out.append(f"    would have said: {one_line(t.would_have_said)}")
        out.append(f"    law: {one_line(t.doctrine_text)}")
        out.append(f"    why: {one_line(t.why)}")
        out.append(f"    cites: {', '.join(t.citations)}")
    return out
