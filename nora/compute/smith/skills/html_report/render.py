"""`render_html_report` -- the composing skill (Nora Compute spec §6): takes
already-rendered HTML fragments (from render_mermaid, render_markdown,
render_map, or Smith's own lieflat-charts-guided code-exec output -- this
function doesn't know or care which) and wraps them in the shared page-shell.
The default "just produce something readable" skill when nothing more
specific fits.
"""

from __future__ import annotations

from page_shell import page_shell


def render_html_report(
    *, title: str, sections: list[str], accent: str | None = None
) -> str:
    return page_shell(title=title, sections=sections, accent=accent)
