---
class: design
subject: typography, shape and depth
owns:
  - Tabular Rule: "a number that updates reserves its width and uses tabular figures"
  - Real-Text Rule: "every word on the stage is live text in the document, even when a scene draws it over WebGL"
area: appearance
links:
  - {to: docs/design/appearance/README.md, rel: uses}
---

# Typography, shape and depth

## Families

| family | for |
|---|---|
| **Bricolage Grotesque** | display, headings and the wordmark, always set in capitals: NORA |
| **Inter** | interface and reading text |
| **Noto Sans HK**, then PingFang HK | every Chinese glyph, in both families' stacks |
| **JetBrains Mono**, tabular | IDs, hashes, counts, times, coordinates |

Character: confident and contemporary, never shouting at the desk.

| role | size | weight | line height | tracking |
|---|---|---|---|---|
| display | `clamp(3rem, 9vw, 9.5rem)` | 650 | 0.92 | -0.035em |
| wordmark | 1.5rem | 650 | 1 | -0.02em |
| heading | 1.5rem | 600 | 1.15 | -0.015em |
| body | 0.9375rem (stage 1.0625rem) | 400 | 1.6 (zh-Hant 1.75) | normal |
| label | 0.75rem | 500 | 1.3 | 0.01em |
| mono | 0.8125rem, tabular | 450 | 1.4 | normal |

- **Stage display** may be UPPERCASE, set huge and tight. One word may take
  accent-text. Chinese display keeps normal tracking and a 1.15 line height
  and is never forced into a faux uppercase.
- **Desk type** is sentence case everywhere: headings, labels, buttons. No
  uppercase wide-tracked micro-labels.
- Body is capped at 70ch and nothing is heavier than 650.

**Tabular.** A number that updates reserves its width and uses tabular figures.

**Real text.** Every word on the stage is live HTML text in the document, in
reading order, translatable and selectable, even when a scene draws it over
WebGL. Type baked into an image is decoration, never content.

## Shape

Controls are 10px, panels 16px, sheets 24px; buttons, chips and avatars are
fully round. Square and rounded corners never mix on one surface. Spacing steps
are 4, 8, 16, 24 and 40px, and the stage's margin is `clamp(24px, 5vw, 80px)`.

## Depth

**Desk depth** comes from the paper steps and hairlines. A shadow only on what
floats: `0 8px 24px rgb(26 33 52 / 0.08)` by day, none by night (use the
border). No nested cards: inside a panel, rows and dividers.

**Stage depth** is real: layered planes in a 3D scene, fog between them, light
falling on paper. Interface over the scene stays flat: type, a pill, a
hairline.
