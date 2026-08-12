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
# SKILL.md's own single-file output skeleton pairs the preconnect `<link>`
# above (caught by GOOGLE_FONTS_LINK_PATTERN) with a second `<link>` that
# pulls the stylesheet from whatever URL `mono-tokens.js`'s FONT.link holds.
# Once that property no longer points at Google Fonts (see
# FONT_LINK_PROPERTY_PATTERN below), the tag has nothing left to load --
# dropped outright, same as the templates never re-add a `<link>` for system
# fonts.
MONO_FONT_STYLESHEET_LINK_PATTERN = re.compile(
    r'<link href="\{MONO\.FONT\.link\}" rel="stylesheet">\s*'
)
SYSTEM_FONT_STACK = (
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
)
# Matches the CSS property `font-family:` through to its terminating `;` or
# `}`, but ONLY when the value actually names Inter somewhere in between --
# the HYPHEN in `font-family` is still load-bearing (see below), and the
# `Inter` requirement guards against over-matching a `font-family:` rule that
# never mentioned Inter to begin with. The whole value is consumed (not just
# the quoted `'Inter'` token) so the replacement doesn't leave the original
# stack's trailing fallback fonts dangling after it, e.g.
# `font-family:'Inter',sans-serif;` naively becoming
# `font-family: <stack>,sans-serif;` -- valid CSS, but a malformed-looking
# double tail; consuming to the terminator and reusing it verbatim avoids
# that.
#
# The HYPHEN is load-bearing: a blind `.replace("'Inter'", ...)` (an earlier
# draft of this script) also rewrote every OTHER quoted occurrence of the
# string "Inter" in the file, including inside <script> blocks: Chart.js/
# ECharts config objects use the camelCase JS property `fontFamily:'Inter'`
# (no hyphen, so this regex never matches it and never touches it), and
# canvas font-shorthand strings like `'700 13px Inter'`. A blind replace
# turned `fontFamily:'Inter'` into
# `fontFamily:ui-sans-serif, system-ui, ...,fontSize:12` -- unquoted,
# comma-laden, and a JS SYNTAX ERROR that broke every Glance-family chart's
# script block outright (confirmed by an independent subagent review that
# actually ran this exact substitution against the real vendored HTML).
FONT_FAMILY_DECLARATION_PATTERN = re.compile(
    r"font-family\s*:\s*[^;}]*Inter[^;}]*"
)
# mono-tokens.js's FONT.link is a JS object-literal property, not a CSS
# `font-family:` declaration or an HTML `<link>` tag -- a different shape
# entirely, so it needs its own pattern rather than reusing the two above.
# It is what Smith actually inlines into every chart it generates (per
# SKILL.md's skeleton), so leaving it pointed at Google Fonts would still
# reach an external CDN at render time even after the templates and CSS
# declarations were fixed. Scoped tightly to the literal googleapis.com URL
# shape so it can't accidentally match an unrelated `link:` property
# elsewhere.
FONT_LINK_PROPERTY_PATTERN = re.compile(
    r"link:\s*'https://fonts\.googleapis\.com[^']*'"
)


def _substitute_fonts(text: str) -> str:
    text = GOOGLE_FONTS_LINK_PATTERN.sub("", text)
    text = MONO_FONT_STYLESHEET_LINK_PATTERN.sub("", text)
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
    text = FONT_FAMILY_DECLARATION_PATTERN.sub(
        f"font-family: {SYSTEM_FONT_STACK}", text,
    )
    return FONT_LINK_PROPERTY_PATTERN.sub("link: ''", text)


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", REPO_URL, tmp], check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "checkout", PINNED_COMMIT], check=True, capture_output=True)
        src = Path(tmp)

        # LICENSE is copied verbatim -- byte-identical, per the license's own
        # `Required Notice:` obligation (see module docstring). Everything
        # else is what Smith actually reads to generate charts (SKILL.md's
        # output skeleton, the mono-tokens.js/color-presets.js token files
        # it inlines, and the templates/ reference gallery it copies code
        # patterns from), so the font substitution runs over all of it, not
        # just templates/ -- a font-CDN reference left in a token file Smith
        # inlines into every chart it builds is just as live a leak as one
        # left in a reference template.
        shutil.copy2(src / "LICENSE", DEST / "LICENSE")
        for name in ("SKILL.md", "catalog.md", "mono-tokens.js", "color-presets.js"):
            (DEST / name).write_text(_substitute_fonts((src / name).read_text()))

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
reference has been replaced with a system font stack throughout the vendored
tree -- every template under `templates/`, the `mono-tokens.js`/
`color-presets.js` token files, and `SKILL.md`'s own single-file output
skeleton (the FONT.link property and the `<link>` tags that load it) -- so no
vendored artifact, including a newly-generated chart built from this skill,
reaches an external CDN for its type at render time (Nora Compute spec
§7/§10). Chart.js and Apache ECharts (loaded
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
