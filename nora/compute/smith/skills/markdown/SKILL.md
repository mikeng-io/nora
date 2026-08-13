---
name: render-markdown
description: Render GitHub-flavored Markdown (including fenced code blocks and tables) to self-contained HTML.
---

# render_markdown

Call `render_markdown(text=...)` with GFM markdown. Fenced code blocks
(```` ```python ```` etc.) get syntax-highlighted server-side — the returned
HTML fragment already includes its own `<style>` block, nothing external.
Tables, lists, and headings all render normally. Embed the returned string
as one section when calling `render_html_report`.
