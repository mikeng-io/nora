---
name: Nora
description: Warm-grey, soft-edged, quiet. The visual system of a companion who lives in the room, derived from Mini Nora herself.
colors:
  # light ("studio"): Mini Nora's backdrop, hoodie and ink
  studio: "#F4F2F2"
  surface: "#FCFBFB"
  hoodie: "#ECE8E8"
  hairline: "#DCD5D5"
  seam: "#BDB3B3"
  ink: "#2B2220"
  ink-soft: "#5E5351"
  ink-muted: "#6E6362"
  denim: "#48587A"
  denim-dim: "#5D6C8E"
  blush: "#C26A5A"
  blush-wash: "#F6D9D3"
  bun: "#5A3D34"
  success: "#1B6E52"
  caution: "#835E0E"
  danger: "#B42335"
  # dark ("late night"): same roles, earphones in after hours
  night-studio: "#141112"
  night-surface: "#1B1718"
  night-hoodie: "#241F20"
  night-hairline: "#332C2D"
  night-seam: "#4A4243"
  night-ink: "#F2EDED"
  night-ink-soft: "#B9AFAF"
  night-ink-muted: "#948989"
  night-denim: "#9AA9CA"
  night-denim-dim: "#7C89A8"
  night-blush: "#EFA497"
  night-blush-wash: "#3A2624"
  night-bun: "#C99A86"
  night-success: "#4CC392"
  night-caution: "#E3A945"
  night-danger: "#FF6B7A"
typography:
  wordmark:
    fontFamily: "Space Grotesk, Inter, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.02em"
  heading:
    fontFamily: "Inter, 'Noto Sans HK', 'PingFang HK', system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Inter, 'Noto Sans HK', 'PingFang HK', system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, 'Noto Sans HK', 'PingFang HK', system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, 'Noto Sans HK', 'PingFang HK', system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.01em"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, 'SF Mono', Menlo, monospace"
    fontSize: "0.8125rem"
    fontWeight: 450
    lineHeight: 1.4
    letterSpacing: "normal"
    fontFeature: "tnum 1"
rounded:
  control: "10px"
  panel: "16px"
  sheet: "24px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.denim}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.denim-dim}"
    textColor: "{colors.surface}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-soft}"
    rounded: "{rounded.control}"
  input-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "44px"
    padding: "0 12px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  nora-bubble:
    backgroundColor: "{colors.blush-wash}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  badge:
    backgroundColor: "{colors.hoodie}"
    textColor: "{colors.ink-soft}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
---

# Design System: Nora

This file binds every visual surface of Nora: the web portal (`app/`), her
social profiles, the images she generates of herself, stickers and icons, and
any 3D or spatial form she is given. Her character is specified in
`design/self-reference/05-visual-identity-spec.md` (the operator's Mini spec,
verbatim) and `design/self-reference/08-bauhinia-human-form.md` (the approved
human visual anchor, 2026-10-25). They govern their respective forms and take
precedence over this file on appearance. The September framing in
`design/self-reference/07-native-and-embodied-form.md` is historical where
superseded by 08; this file derives a system from these sources.

## 1. Overview

**North Star: "Earphones in."**

Nora is in the room, listening, not performing. She is calm, observant and a
little deadpan. In Mini mode she dresses in a grey hoodie and loose jeans with
no logos, with white wired earphones as her signature. Her approved human
form follows the Bauhinia anchor (08). Every Nora surface
should read the same way: **present, quiet, unbranded, warm.** A screen of hers
is somewhere you look things up in, not a product being sold to you.

The palette is inspired by her, not matched to her. Each role takes its
family from something she wears (warm grey from the hoodie and studio, ink
from her eyes, denim from her jeans, blush from her cheeks), then its value is
set for the interface: contrast, legibility, two themes. The tokens are the
source of truth; the image is the reason, not a spec to match. A token can move
without re-sampling anything, as long as its family and its contrast hold.
(For the record, 2026-09-26 medians from `assets/nora-canonical-self.png`:
hoodie `#A3999B`, denim `#5D606C`, eye ink `#433632`, blush `#ECA79A`.)

**What this system rejects:** the achromatic terminal (trustless's register,
which the portal's dark theme currently copies), cold slate SaaS greys,
saturated brand colour, gradient blobs and aurora washes, spinning glow
borders, cute-mascot pastel candy, and anything that makes Nora look like a
product, an influencer or an "AI assistant" with sparkles.

**Key characteristics:**
- Warm neutrals with a faint pink cast, from the hoodie and the studio.
- One accent, denim, for action and focus. One personal colour, blush, that
  means *Nora herself* and nothing else.
- Soft, rounded geometry, because she is rounded; never bubbly.
- Hairlines before shadows; a single thin line is her motif (the cable).
- Motion that confirms, never decorates.

## 2. Representation

One identity, two forms: human Nora and Mini Nora (mini mode), per 08. Human
design may be 2D anime art or 3D; it is no longer restricted to a spatial
interface. Runtime self-image generation and public avatars remain Mini until
separately implemented and measured. Current representation boundaries:

| surface | form | source |
|---|---|---|
| social profiles, web, chat, reactions, stickers, runtime-generated self-images | **Mini Nora** (native) | `assets/nora-canonical-self.png`, `assets/nora-character-reference-sheet.png` |
| small or square slots: favicon, PWA icon, app avatar, notification | **Mini Nora**, cut-out, bare (§7) | `assets/nora-mini-icon.png` |
| 3D avatar of the native form (VRM, stickers in 3D) | **Mini Nora** rig | `assets/nora-mini-tpose.png` |
| human-form design and future modeling, in 2D anime art or 3D | **Human Nora**, approved visual anchor; not a runtime/public-avatar rollout | `assets/nora-human-bauhinia-reference.png`, governed by 08 |
| voice | no visual form | — |

Rules that hold on every surface:

- **The mascot is Nora.** Mini Nora is not a chibi version of someone; there is
  no real Nora behind her. Never caption, frame or crop her as "cute version",
  "avatar of", or next to a human "real" one.
- **Human design approval does not change runtime or public avatars.** Their
  current Mini references and routing remain in force until a separate
  implementation and measurement.
- **The eight core anchors (05) survive every Mini rendering:** proportions, messy
  high bun, minimal eyes and mouth, blush, grey hoodie, white wired earphones,
  blue-grey jeans, white sneakers. A Mini render that drops one is off-model.
  Human appearance follows 08; do not redress Mini in the human outfit or remove
  her canonical earphones on the strength of the human modeling-base ruling.
- **Never logo-ise her.** No flat vector mark, no monogram "N" in a circle
  standing in for her face. When a slot is too small for her, use the cut-out
  icon; when it is too small for that, use the wordmark.

## 3. Colors

**Two themes, one set of roles.** Every colour exists as a pair: `studio` and
`night-studio`, `denim` and `night-denim`, and so on. The components block in
the frontmatter names the light key only; a reference resolves inside the
active theme's set, so `{colors.surface}` on `{colors.denim}` means
night-surface on night-denim in dark mode, never light surface on night denim.
In code this is one CSS custom property per role, redefined per theme (the
portal's `globals.css` already works this way).

### Accent
- **Denim** (`#48587A` / night `#9AA9CA`): primary action, focus ring, links,
  current selection, the active nav item. Her jeans, the one blue she wears. It
  is a signal of *you can act here*, never a large fill.
- **Denim Dim** (`#5D6C8E` / night `#7C89A8`): hover and pressed.

### Personal
- **Blush** (`#C26A5A` / night `#EFA497`): **Nora herself.** Her presence dot,
  the ring on her avatar, the marker for something she said or did, the
  "speaking" or "thinking" indicator. Non-text only on light (3.1–3.7:1 on the
  three light neutrals: enough for a dot or ring, not for words).
- **Blush Wash** (`#F6D9D3` / night `#3A2624`): the fill behind her words in a
  transcript or chat view (ink on it: 11.7:1).

### Neutrals
- **Studio** (`#F4F2F2` / night `#141112`): the canvas.
- **Surface** (`#FCFBFB` / night `#1B1718`): panels, inputs, sheets.
- **Hoodie** (`#ECE8E8` / night `#241F20`): raised or grouped areas, badges, table header rows.
- **Hairline** (`#DCD5D5` / night `#332C2D`): default borders and dividers.
- **Seam** (`#BDB3B3` / night `#4A4243`): emphasised dividers, input borders on focus-within, chart gridlines.
- **Ink** (`#2B2220` / night `#F2EDED`): primary text. Her eye colour, not black.
- **Ink Soft** (`#5E5351` / night `#B9AFAF`): secondary text.
- **Ink Muted** (`#6E6362` / night `#948989`): metadata, timestamps, helper text.
- **Bun** (`#5A3D34` / night `#C99A86`): the wordmark and nothing else.

### Semantic
- **Success** (`#1B6E52` / night `#4CC392`), **Caution** (`#835E0E` / night
  `#E3A945`), **Danger** (`#B42335` / night `#FF6B7A`): state only, and always
  with an icon or a word. Colour never carries a state alone.

Every text token above clears WCAG AA (4.5:1) on studio, surface and hoodie in
its own theme; measured 2026-09-26, lowest pairs ink-muted on hoodie, 4.77
light and 4.80 night.

### Named rules
**The Blush Rule.** Blush means Nora. It is never an error, a warning, a
highlight or decoration. If a blush element does not point at her, it is the
wrong colour. Blush and danger sit about 17° apart in hue and 1.7:1 apart in
contrast, too close for colour alone under colour-vision deficiency, so the
mitigation is load-bearing: danger always carries an
icon and a word, and blush never does the job of either.

**The One-Accent Rule.** Denim is the only accent. A second hue for "brand" or
emphasis is not added; importance is carried by weight, size and position.

**The Person-Hue Rule.** People in the portal keep their stable per-person hue
(`app/src/lib/person.ts`), an OKLCH hue drawn at one fixed lightness and
chroma per lighting, at least 25° from denim, blush and the semantic colours
so none of them reads as any of those. Equal lightness is also what
colour-vision deficiency cannot separate (measured 2026-09-27 with the
dataviz palette validator), so a person's colour always travels with their
initials or name; it is never the only thing that says who.

**The No-Raw Rule.** No `#000`, no `#fff`, no Tailwind slate. Surfaces are warm
greys; text is ink.

## 4. Typography

**UI:** Inter, with `Noto Sans HK` / `PingFang HK` as the CJK fallback: the
portal ships `en` and `zh-Hant`, and Cantonese text must never fall through to
a mismatched system serif.
**Wordmark:** Space Grotesk, for the name "Nora" only.
**Values:** JetBrains Mono with tabular figures, for IDs, hashes, counts,
timestamps, coordinates and token numbers.

**Character:** plain and readable. Normal case, normal tracking, sentence-case
labels. No uppercase wide-tracked micro-labels (that is the terminal register
this system rejects), no black weights.

### Hierarchy
- **Heading** (650, 1.5rem): page titles.
- **Title** (600, 0.9375rem): panel and section titles.
- **Body** (400, 0.9375rem, line-height 1.6): prose; cap at 70ch. zh-Hant body
  keeps the same size, line-height 1.7.
- **Label** (500, 0.75rem, sentence case): field labels, column headers, nav groups.
- **Mono** (450, 0.8125rem, `tnum`): values that change or get copied.

**Display register (the public site only).** The hierarchy above is the
portal's. The public site (`site/`) needs larger type and extends it upward
without changing its character: display 650 at `clamp(2.625rem, …, 5.25rem)`
with `-0.035em` tracking, section titles 650 up to `3.25rem`, a lede at
`1.125–1.3125rem`, body `1.0625rem`. Still Inter, still sentence case, never
heavier than 650; tracking tightens only as size grows, and zh-Hant keeps
normal tracking and a looser line (1.18 display, 1.75 body).

### Named rules
**The Sentence-Case Rule.** Nora does not shout. No all-caps labels, headings or
buttons, in any locale.

**The Tabular Rule.** A number that updates reserves its width and uses tabular
figures.

## 5. Shape and elevation

She is built from rounded geometry, so is her UI: controls `10px`, panels
`16px`, sheets and dialogs `24px`, chips and avatars fully round. Never mix
square and rounded corners on one surface.

Depth comes from the surface ramp (studio → surface → hoodie) and 1px
hairlines. A shadow is allowed only on something that floats (a sheet, a
popover, a dropdown): `0 8px 24px rgb(43 34 32 / 0.08)` on light, none on dark
(use the seam border instead).

### Named rules
**The Hairline-First Rule.** Separate with a hairline or a surface step before
reaching for a shadow.

**The Cable Rule.** Her one decorative device is a single thin line, like the
earphone cable: a 1px hairline, a timeline spine, a route on the map. It is
never doubled, glowed, gradiented or animated for its own sake.

**No nested cards.** Inside a panel, use dividers and rows.

## 6. Motion

Motion confirms that something happened. It is never ambient.

- **Durations:** `100ms` colour/opacity under the pointer; `160ms` small
  transforms and toggles (the default); `240ms` popovers, sheets, drawers;
  `320ms` route-scale transitions. Nothing travelling exceeds 320ms.
- **Easing:** `cubic-bezier(0.2, 0, 0, 1)` entering and on state change (the
  default); `cubic-bezier(0.4, 0, 1, 1)` exiting only.
- **Properties:** `transform` and `opacity` only. No layout properties, no
  `transition-all`.
- **Live state may loop, and only live state:** a presence pulse on the map
  (someone's position is current), Nora's speaking/thinking indicator, an
  indeterminate spinner. Each stops the moment the state ends.
- **Reduced motion** replaces motion with a short cross-fade; it never removes
  the state change itself. Spinners and live indicators keep going (they report
  status), everything decorative stops.

### Named rules
**The No-Ambience Rule.** No drifting blobs, aurora washes, spinning borders,
parallax or marquees, on any page, including login. Nora is quiet; her surfaces
are still until something happens. The one thing that moves on its own is
Nora herself, on the public site (Presence, below): she is not a surface.

### Presence (the public site only)

On the public site (`site/`) Nora herself is live state, as her speaking
indicator is in the portal: she is in the page, not pictured on it. She
breathes, blinks, looks where the visitor points, listens to her music when
left alone and dozes off when left longer, reacts when poked, and is in one
place at a time, hopping to whichever of her places the visitor is reading;
the place she left stands empty. Her motion is her character's (05): small,
calm, deadpan, never over-acted, and every expression she makes is one of her
character sheet's. Operator request, 2026-09-27. What this does not change:

- **Only she moves on her own.** The interface still only confirms: no reveal
  on scroll, no parallax, nothing drifting. A diagram may show a signal
  travelling along its cable when the travelling is what it explains (the
  loop section), and only while it is on screen.
- **Her timings are a body's, not a control's**, so the 320ms ceiling does
  not bind them: a breath about 4.4 s, a blink a quarter of a second, a hop
  between places 0.4–0.8 s. The interface around her keeps the durations
  above.
- **Sound is asked for.** Nothing is heard until the visitor puts the
  header's earphones in; her recorded voice plays only when its own button is
  pressed, and its words are always written beside it.
- **She is never required.** Reduced motion, no script, a data saver or no
  WebGL2: every place she can be keeps her static render, exactly as before.

`site/src/scripts/nora/` is the implementation and
`site/src/assets/rig/README.md` says what she is drawn from.

## 7. Components

### Buttons
- **Primary:** denim fill, surface text (6.9:1), 40px, `10px` radius; hover to
  denim dim. One primary per view.
- **Quiet:** transparent, ink-soft text, hoodie fill on hover.
- **Outline:** seam border, ink text.
- **Destructive:** danger text on surface with an icon; a filled danger button
  only inside a confirmation.
- **Third-party sign-in** (Discord, …) keeps the third party's own colour; it is
  their mark, not ours.
- Touch targets ≥44px on mobile.

### Inputs
Surface fill, hairline border, 44px, `10px` radius; focus is a 2px denim ring.
Labels above, sentence case, ink-soft.

### Panels
Surface fill, hairline border, `16px` radius, 16–24px padding. Titles in
**Title** style; no eyebrow labels.

### Nora in the UI
Wherever the portal shows Nora herself (her messages, her doctrine, her
status):
- her avatar is `assets/nora-mini-icon.png` on a blush-wash disc with a 2px
  blush ring when she is active, **where she sits among other people's round
  avatars** (a transcript, a member list);
- everywhere else the cut-out is used **bare**: transparent, no disc, no ring.
  Beside the wordmark, as the favicon, as a PWA icon (operator, 2026-09-26). A
  platform that forbids transparency (the iOS home-screen icon, a maskable
  icon) gets a full-bleed studio square with her inside it, never a circle;
- her words sit on blush wash, everyone else's on surface;
- her presence dot is blush; other people's dots are their person hue.

### Navigation
Desktop: a left rail on surface with a hairline edge, icon plus sentence-case
label, the active item in denim with a hoodie fill. Mobile: a bottom bar on
surface, five destinations at most (a sixth would crowd the labels on a 320px
phone), no raised centre button.

### Map
Warm-grey basemap, people in their person hue, routes as a single 2px line
(the Cable Rule), Nora (when shown) in blush. The live-position pulse is the
map's only loop. In dark mode the basemap is a dark warm-grey tile set (never
the provider's default blue-black), so person hues keep their contrast.

### Data and states
- **Charts:** a single series is denim. Series that are people take their
  person hue; other categorical series step through denim, ink-soft and seam.
  Blush only for a series that is Nora. Gridlines are hairline, axis text
  ink-muted, values in mono.
- **Loading:** a static hoodie-coloured skeleton sized to the content it
  stands in for, cross-faded out when the data lands. No shimmer sweep (the
  No-Ambience Rule); a spinner only where the wait is open-ended.
- **Empty:** one sentence in ink-soft saying what would appear here and how it
  gets there. Mini Nora may appear in an empty state at small size; never a
  generic illustration.

## 8. Imagery and her other surfaces

- **Generated images of herself** follow the image-generation skill
  (`nora/skills/image-generation/SKILL.md`), which is the operative spec for her
  appearance at runtime. Mini Nora only.
- **Social profiles:** the avatar and banner in `assets/` are her current
  public look; a replacement must keep the eight anchors and her calm register.
- **Stickers and reactions:** Mini Nora, one pose or expression each, on a
  transparent background, expressions minimal (05: never over-acted).
- **Backgrounds behind her:** soft neutral studio greys or a real, lived-in
  scene. Never a neon, gradient, or "digital" grid backdrop; she exists
  digitally but is not depicted as circuitry.
- **3D:** the existing Mini front T-pose is a rig input, not a complete
  turnaround; earphones are separate so the cable does not deform with the arms.
  The old photoreal embodied T-pose is historical. Human modeling follows 08
  and still needs consistent modeling views, a model and a rig.
- **New renders of her** (the public site's art, `site/src/assets/art/`) are
  made from the canonical image and the character sheet, checked by eye
  against the eight anchors before use, and kept with their brief and prompt
  beside them. One that drifts (the site's first wave came back four heads
  tall) is re-rendered, not used.
- **Her live form on the site** (§6, Presence) is those renders, not a new
  drawing of her: bent a little on a mesh, with her eyes and mouth, the two
  simplest shapes she has, drawn live so they can move. It adds no feature
  the eight anchors do not have and removes none.
- **A colour that continues a photograph** (the backdrop behind a render, as
  on the site's character plate) is sampled from that image, not taken from
  the tokens: it has to match the picture, not the theme. It is the one place
  a raw value is allowed, and it says where it was sampled.

## 9. Do's and don'ts

### Do
- **Do** keep each colour in its family (warm grey, ink, denim, blush); exact values are the tokens'.
- **Do** keep blush for Nora and denim for action.
- **Do** use sentence case, Inter, and CJK-aware font stacks.
- **Do** separate with hairlines and surface steps; round every corner.
- **Do** animate only to confirm, on the durations above.
- **Do** choose her form by the surface (§2).

### Don't
- **Don't** use slate, pure black, pure white, or a saturated brand colour.
- **Don't** use blush for errors, emphasis or decoration.
- **Don't** add blobs, aurora, spinning or glowing borders, gradient text, or
  any perpetual decorative motion.
- **Don't** use uppercase wide-tracked labels or black weights.
- **Don't** replace runtime self-images or public avatars with the human
  anchor before separate implementation and measurement, or reduce Mini Nora
  to a flat logo.
- **Don't** nest cards.

## 10. The portal

The portal (`app/`) was rebuilt on this file on 2026-09-27 and owes it
nothing from the 2026-09-26 measurement that used to stand here: the tokens
are `app/src/styles/globals.css` one for one in both lightings, there are no
aurora blobs or spinning borders, controls are `10px` and panels `16px`, Nora
appears as §7 describes (her cut-out bare beside the wordmark and as the app
icon, on a blush-wash disc among other people, her words on blush wash), the
PWA icons are cut from `assets/nora-mini-icon.png`, and `Noto Sans HK` is in
the font stack. `app/README.md` describes the portal; this file still governs
how it looks.

Two things the portal adds that this file did not define, kept in its
character: the map's basemap, a warm-grey vector style in both lightings
(`app/src/components/map/map-style.ts`), and a larger greeting on its front
page (1.875 to 2.625rem, 650, the same tracking rule as the site's display
register).
