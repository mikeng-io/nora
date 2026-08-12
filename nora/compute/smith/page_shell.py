"""The shared, true-neutral Mono page-shell every skill's output renders
into (Nora Compute spec §10) -- validated first as the "Smith Spike" artifact
before being written down as code. Plain HTML/CSS, no component framework, no
build step: cards/timeline/table/disclosure/modal/form-control patterns are
native HTML elements (`<details>`, `<dialog>`, `<select>`) styled from these
tokens, not a React/shadcn dependency (spec §10's own reasoning against one).
"""

from __future__ import annotations

import html as html_module
import re

_BASE_CSS = """
:root {
  --bg: #fafafa; --surface: #ffffff; --surface-2: #ededed;
  --ink: #141414; --muted: #6b6b6b; --line: #dcdcdc;
  --accent: var(--shell-accent, #141414);
  --font-display: ui-serif, Charter, Georgia, serif;
  --font-body: -apple-system, "Segoe UI", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141414; --surface: #1e1e1e; --surface-2: #262626;
    --ink: #ececec; --muted: #9a9a9a; --line: #333333;
  }
}
:root[data-theme="dark"] {
  --bg: #141414; --surface: #1e1e1e; --surface-2: #262626;
  --ink: #ececec; --muted: #9a9a9a; --line: #333333;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-body); line-height: 1.55;
}
.page {
  max-width: 46rem; margin: 0 auto;
  padding: clamp(1.25rem, 4vw, 3rem) clamp(1rem, 5vw, 1.5rem) 4rem;
  display: flex; flex-direction: column; gap: clamp(2rem, 5vw, 3rem);
}
h1 { font-family: var(--font-display); font-size: clamp(1.6rem, 5vw, 2.2rem); }
.section { overflow-x: auto; }
.card {
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 4px; padding: clamp(1rem, 3vw, 1.5rem);
}
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 0.5rem; text-align: left; }
details { border: 1px solid var(--line); border-radius: 4px; padding: 0.75rem; }
dialog { border: 1px solid var(--line); border-radius: 4px; }
"""

_HEX_COLOR = re.compile(
    r"^#[0-9a-fA-F]{3}$|^#[0-9a-fA-F]{4}$|^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{8}$"
)


def page_shell(*, title: str, sections: list[str], accent: str | None = None) -> str:
    # `accent` ultimately comes from an LLM tool call and is interpolated
    # directly into a <style> block below -- unvalidated, it is an
    # attribute/CSS-breakout injection vector into an artifact meant to be
    # self-contained and script-free (the same-shaped bug render_map had,
    # fixed in 640be5d). A hex color is the only format this codebase's own
    # examples use, so that's the accepted grammar; anything else is rejected
    # outright rather than escaped, since there is no safe way to escape
    # arbitrary text into a CSS custom-property value.
    if accent is not None and not _HEX_COLOR.match(accent):
        raise ValueError(
            f"accent must be a hex color like '#2f6f62', got: {accent!r}"
        )
    accent_style = (
        f"<style>:root {{ --shell-accent: {accent}; }}</style>" if accent else ""
    )
    body = "\n".join(
        f'<div class="section card">{section}</div>' for section in sections
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{html_module.escape(title)}</title>
<style>{_BASE_CSS}</style>
{accent_style}
</head>
<body>
<div class="page">
<h1>{html_module.escape(title)}</h1>
{body}
</div>
</body>
</html>"""
