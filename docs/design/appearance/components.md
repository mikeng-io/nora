---
class: design
subject: interface components and how Nora appears in them
owns:
  - Nora in the interface: "where and how Nora herself, her words and her presence are shown among people in the portal"
area: appearance
links:
  - {to: docs/design/appearance/README.md, rel: uses}
  - {to: docs/design/appearance/colour.md, rel: uses}
---

# Components

## Buttons

- **Primary:** a copper pill, 44px, `on-action` text. One primary per view.
- **Quiet:** ink text, transparent, a paper 4 fill on hover.
- **Outline:** border 7, ink text.
- **Destructive:** danger text with an icon; a filled danger button only inside
  a confirmation.
- **Third-party sign-in** keeps the third party's own colour.
- Touch targets are at least 44px.

## Inputs and panels

Inputs: raised fill, border 7, 44px, 10px radius, a 2px copper focus ring,
labels above in sentence case. Panels: surface fill, hairline border, 16px
radius, 16 to 24px padding, no eyebrow labels.

## Nora in the interface

- Among other people's round avatars (a transcript, a member list): her cut-out
  on a petal-wash disc, with a 2px petal ring while she is active.
- Beside the wordmark, as the favicon and as the app icon: the clasp emblem,
  never her cut-out.
- Her words sit on petal wash; everyone else's on surface. Her presence dot is
  petal; other people's are their person hue.

## Navigation

- **Stage:** a thin top bar over the scene, her mark and the NORA wordmark on
  the left, the language on the right. The public site is an introduction, so
  it carries no sign-in. A scene indicator is a list of real links to each
  scene.
- **Desk, desktop:** a left rail on surface with a hairline edge, icon plus
  label, the active item in copper text on paper 4.
- **Desk, mobile:** a bottom bar of at most five cells, the last a More sheet;
  no raised centre button.

## Map

A warm paper basemap by day and a navy basemap by night, never a provider's
default; people in their person hue; routes as a single 2px line; Nora in
petal. The live-position pulse is the map's only loop.

## Data and states

- **Charts:** a single series is indigo; people take their person hue; other
  series step through indigo, copper 7 and paper 8; petal only for Nora.
  Gridlines are hairline, axis text ink-soft, values mono.
- **State** is never colour alone: success and danger carry an icon and a word.
- **Empty:** one sentence in ink-soft saying what would appear here and how it
  gets there. Mini Nora may appear small; never a generic illustration.
