---
name: render-mermaid
description: Render Mermaid diagram source to a static, self-contained SVG.
---

# render_mermaid

Call `render_mermaid(diagram_source=...)` with valid Mermaid syntax
(flowchart, sequence, etc.) to get back an `<svg>` string. This is a static
render — no interactivity, works offline the moment the artifact is opened.
Embed the returned string directly as one section when calling
`render_html_report`.

If the diagram source is malformed, `render_mermaid` raises rather than
returning something broken — catch it and either fix the source or fall back
to describing the same thing in prose within `render_markdown`.
