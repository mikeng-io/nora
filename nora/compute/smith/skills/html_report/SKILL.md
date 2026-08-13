---
name: render-html-report
description: Compose already-rendered HTML fragments (charts, diagrams, markdown, maps) into one responsive, self-contained page.
---

# render_html_report

Call `render_html_report(title=..., sections=[...])` with a list of
already-rendered HTML strings -- output from `render_mermaid`,
`render_markdown`, `render_map`, or HTML you wrote yourself following the
charts skill (`skills/charts/SKILL.md`). This is the LAST step: call it once,
with everything the artifact needs, and write its return value to the file
you register with `register_artifact`.

Pass `accent=` (a hex color) only when the artifact's own content genuinely
calls for one color to encode something -- otherwise leave it unset and the
page stays neutral grayscale. Never introduce a second color in the same
delivery.
