# Mini Nora icon: provenance

## Current: 2D anime Bauhinia — 2026-10-26

The supplied final transparent portrait follows the approved Mini Bauhinia
production setting: compact high bun, broad hair locks, five-petal clasp,
option08 eyes, one tiny mouth without a nose marker and the new outfit.
No earphones or cables. Parent viewed the final crop and bun visibility PASS.
It remains an icon derivative, never the full-body canonical self reference.

The central inventory records the canonical semantic parent, dated prompt
intent and all app/site/showreel outputs. Existing icon and OG processors keep
bare transparent icons and full-bleed studio squares where required. Two PWA
screenshots were recaptured using the documented synthetic mock workflow;
no real account or live data was used. External profile publication, media
ingest and deployment were not performed.

## Earlier option08 Balanced refresh — historical, 2026-10-26

The operator approved option08 eyes for Mini and requested this portrait refreshed
from the new canonical image: visible sclera, brown irises, dark pupils, restrained
highlights and upper lid line; no nose marker and one tiny mouth. The transparent
head-and-shoulders crop retains the bun, cheek blush, hoodie and wired earphones.
It is an icon derivative, not a replacement full-body reference.

`assets/visual-assets.json` declares every consumer and derivative. Run
`uv run python scripts/visual_assets.py sync` to rebuild app/site icons, the
portal portrait and showreel images through their existing processors.
Transparent exports remain bare; iOS and maskable icons use the studio square
from `DESIGN.md`. Original generated PNG metadata stays in the local archive;
repository PNGs omit generation metadata. No external profile publication or
runtime media ingest is performed by this pipeline.

## Historical icon, 2026-10-20 (superseded)

Generated with the built-in imagegen tool on 2026-10-20. The operator selected
the second candidate for the repository and web app. This replaces the previous
chin-on-hands icon; the canonical full-body image and character sheet are unchanged.

All three images were passed directly to the generator, in this order:

1. The previous `assets/nora-mini-icon.png` (available in Git history).
2. `assets/nora-canonical-self.png` (authoritative identity).
3. `assets/nora-character-reference-sheet.png` (supporting character details).

The selected PNG is kept as generated, including its alpha channel. The
portrait retains the bun, minimal face, blush, grey hoodie and wired earphones;
jeans and sneakers are outside this head-and-shoulders crop. This is an icon
render of Mini Nora, not a new canonical full-body reference.

The prompt requested a more compact bun and less fine detail. The output only
partly achieved that: it remains hair-heavy, so no claim of measured favicon
legibility improvement is made.

## Generation prompt

```text
Use case: identity-preserve. Create a refined square transparent app icon of the SAME Nora from the attached three authoritative references (existing icon, canonical full body, character sheet). Preserve exact character identity: warm rounded mascot face, dark vertical oval eyes without sclera, tiny neutral horizontal mouth, blush, dark brown messy high bun, side fringe, grey zip hoodie, white wired earphones and visible inline control. Tactile stylized 3D, soft matte materials and warm studio lighting.
CRITICAL COMPOSITION CHANGE: optimize the FACE for 32px icons. Nora's face must be centered in the square, not low down. Place the eyes approximately 54 percent down the canvas. Face spans about 65 percent of canvas width. Make the high bun COMPACT, fully visible and unmistakable, occupying only the upper 22 percent of the canvas, not half the image. Crop at shoulders with only the hoodie collar and upper chest visible at the bottom. No arms or hands. She must still match the canonical reference with her rounded head shape and small neck. Preserve long face-framing locks but simplify flyaways into only a few clean deliberate tufts, broad hair locks instead of individual wisps. Subtle smooth hoodie material, reduce microtexture. Calm slightly deadpan neutral expression, no smile.
Single bare character cutout on true transparent alpha background, clean edge, no fringe, no surrounding disc or tile or frame, no background shadow. No text, no sheets, no monogram, no vector logo, no sparkle, no wireless earbuds. This is a carefully art-directed small icon portrait of the referenced Nora, not a new character or full-body illustration.
```

## Exports

- Portal icons and favicon: `uv run python app/scripts/make-icons.py`.
- Public site icons, favicon and link previews: `site/scripts/make_images.py`
  (its docstring lists the font and Python prerequisites).
- Portal UI portrait: the portal generator's `bare_icon(512)`, WebP quality 90,
  method 6, exact transparent RGB, alpha quality 100.
- Showreel portrait: `showreel/pipeline/process_art.py`'s `trim`, WebP quality 92,
  method 6, exact transparent RGB, alpha quality 100.

Transparent exports stay bare; iOS and maskable icons use the existing studio
square treatment from `DESIGN.md`.
