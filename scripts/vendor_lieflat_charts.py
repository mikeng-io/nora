"""Fetches `lieflat-charts` (github.com/larashero3-dotcom/lieflat-charts) at
a pinned commit, substitutes its Google-Fonts-loaded Inter reference for
system font stacks (Nora Compute spec §7's fix for the font-CDN gap an
earlier subagent review caught — see docs/superpowers/specs/
2026-08-12-nora-compute-design.md §7), and writes the result into
`nora/compute/smith/skills/charts/`.

Re-run this script (with an updated PINNED_COMMIT) to pick up upstream
changes deliberately — never edit the vendored tree by hand, or a future
re-vendor silently discards the hand edit.

License: PolyForm Noncommercial 1.0.0 (verified against the actual LICENSE
text during this repo's design phase — permitted for Nora's private,
non-commercial use under its "Personal Uses" clause). This script preserves
the license's `Required Notice:` obligation by copying LICENSE verbatim into
the vendored tree and recording the substitution made to it in
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/larashero3-dotcom/lieflat-charts.git"
# Pin to a specific commit, not a moving branch -- re-vendoring is an
# explicit, reviewed act (this docstring), not something that silently
# changes what ships on the next unrelated run of this script.
PINNED_COMMIT = "e05f777261a774c945bdd0157817ef68f3c4766d"

DEST = Path(__file__).parent.parent / "nora" / "compute" / "smith" / "skills" / "charts"
NOTICES_PATH = Path(__file__).parent.parent / "THIRD_PARTY_NOTICES.md"

# What the upstream templates load for the Inter font, and what replaces it --
# a plain CSS font-family stack matching this repo's own system-font decision
# (Nora Compute spec §10), never a live Google Fonts request.
GOOGLE_FONTS_LINK_PATTERN = re.compile(
    r'<link[^>]*fonts\.googleapis\.com[^>]*>\s*'
)
SYSTEM_FONT_STACK = (
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
)
# Matches ONLY the CSS property `font-family: 'Inter'` (or double-quoted, or
# with other stack entries after it) -- the HYPHEN is load-bearing here. A
# blind `.replace("'Inter'", ...)` (an earlier draft of this script) also
# rewrote every OTHER quoted occurrence of the string "Inter" in the file,
# including inside <script> blocks: Chart.js/ECharts config objects use the
# camelCase JS property `fontFamily:'Inter'` (no hyphen, so this regex never
# matches it and never touches it), and canvas font-shorthand strings like
# `'700 13px Inter'`. A blind replace turned `fontFamily:'Inter'` into
# `fontFamily:ui-sans-serif, system-ui, ...,fontSize:12` -- unquoted,
# comma-laden, and a JS SYNTAX ERROR that broke every Glance-family chart's
# script block outright (confirmed by an independent subagent review that
# actually ran this exact substitution against the real vendored HTML).
FONT_FAMILY_DECLARATION_PATTERN = re.compile(
    r"font-family\s*:\s*['\"]Inter['\"]"
)


def _substitute_fonts(html: str) -> str:
    html = GOOGLE_FONTS_LINK_PATTERN.sub("", html)
    # CSS `font-family:` declarations only -- never touches JS string
    # literals (camelCase `fontFamily`, canvas font-shorthand strings), so it
    # cannot corrupt a <script> block the way a blind text replace did.
    # Residual imprecision, accepted rather than chased further: a canvas
    # font-shorthand string ('700 13px Inter') still NAMES Inter after this
    # substitution, since it was never a `font-family:` declaration to begin
    # with -- the browser falls back to a default font when it can't find
    # one by that name (graceful degradation), which is a materially
    # different failure mode than the JS syntax error this fix actually
    # closes, and not worth a second, riskier regex to chase away entirely.
    return FONT_FAMILY_DECLARATION_PATTERN.sub(
        f"font-family: {SYSTEM_FONT_STACK}", html,
    )


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", REPO_URL, tmp], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "checkout", PINNED_COMMIT], check=True, capture_output=True)
        src = Path(tmp)

        for name in ("SKILL.md", "catalog.md", "mono-tokens.js", "color-presets.js", "LICENSE"):
            shutil.copy2(src / name, DEST / name)

        templates_src = src / "templates"
        templates_dest = DEST / "templates"
        shutil.copytree(templates_src, templates_dest)
        for html_file in templates_dest.rglob("*.html"):
            html_file.write_text(_substitute_fonts(html_file.read_text()))

    notice = f"""# Third-Party Notices

## lieflat-charts

Vendored at commit `{PINNED_COMMIT}` from
https://github.com/larashero3-dotcom/lieflat-charts, under the PolyForm
Noncommercial License 1.0.0 (see `nora/compute/smith/skills/charts/LICENSE`).
Verified permitted for Nora's private, non-commercial, self-hosted use under
that license's "Personal Uses" clause (see
`docs/superpowers/specs/2026-08-12-nora-compute-design.md` §7).

Required Notice: Copyright the `lieflat-charts` authors
(https://github.com/larashero3-dotcom/lieflat-charts).

**Modification from upstream:** the Google Fonts-loaded Inter typeface
reference has been replaced with a system font stack in every vendored
template, so no vendored artifact reaches an external CDN for its type at
render time (Nora Compute spec §7/§10). Chart.js and Apache ECharts (loaded
via CDN in the Glance/interactive templates) are UNCHANGED from upstream and
remain third-party dependencies of those templates specifically:

| project | used by | license |
|---|---|---|
| Chart.js | Glance chart rendering | MIT |
| Apache ECharts | Glance + interactive network diagrams | Apache-2.0 |
"""
    NOTICES_PATH.write_text(notice)
    print(f"Vendored lieflat-charts at {PINNED_COMMIT} into {DEST}")
    print(f"Wrote {NOTICES_PATH}")


if __name__ == "__main__":
    main()
