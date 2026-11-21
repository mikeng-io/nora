---
name: Nora
description: Paper and thread. Ivory paper, indigo ink, copper thread, the colours of her Bauhinia clothes. Quiet where you read, cinematic where she lives.
colors:
  # Each role points at a step of a 12-step scale (§3). Light is "paper", dark is "night".
  canvas: "#F4F1EA"          # paper 3
  surface: "#FAF9F4"         # paper 2
  raised: "#FEFDFA"          # paper 1
  hairline: "#DED9CD"        # paper 6
  border: "#D3CCBE"          # paper 7
  ink: "#1A2134"             # indigo 12
  ink-soft: "#6B6253"        # paper 11
  indigo: "#324575"          # indigo 9
  action: "#AF5321"          # copper 9
  action-hover: "#9F440F"    # copper 10
  on-action: "#FEFDFA"       # paper 1
  accent-text: "#974A25"     # copper 11
  petal: "#BB6954"           # petal 10
  petal-wash: "#FEEDE6"      # petal 3
  chestnut: "#623426"
  success: "#216D4B"         # success 11
  danger: "#AB2D47"          # danger 11
  night-canvas: "#0D0E10"    # paper 1 (night)
  night-surface: "#121417"   # paper 2 (night)
  night-raised: "#1B1E23"    # paper 3 (night)
  night-hairline: "#303640"  # paper 6 (night)
  night-border: "#3E4552"    # paper 7 (night)
  night-ink: "#E8EDF8"       # paper 12 (night)
  night-ink-soft: "#BBC4D8"  # paper 11 (night)
  night-indigo: "#A1B7E8"    # indigo 9 (night)
  night-action: "#F4A16D"    # copper 9 (night)
  night-action-hover: "#FEB482"
  night-on-action: "#0D0E10"
  night-accent-text: "#F8B284"
  night-petal: "#EDA28D"
  night-petal-wash: "#2A1916"
  night-chestnut: "#DEAA93"
  night-success: "#7DDBAE"
  night-danger: "#FFAAAA"
typography:
  display:
    fontFamily: "Bricolage Grotesque, 'Noto Sans HK', system-ui, sans-serif"
    fontSize: "clamp(3rem, 9vw, 9.5rem)"
    fontWeight: 650
    lineHeight: 0.92
    letterSpacing: "-0.035em"
  wordmark:
    fontFamily: "Bricolage Grotesque, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
    lineHeight: 1
    letterSpacing: "-0.02em"
  heading:
    fontFamily: "Bricolage Grotesque, 'Noto Sans HK', system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.015em"
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
  stage: "clamp(24px, 5vw, 80px)"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-action}"
    rounded: "{rounded.pill}"
    padding: "0 20px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.on-action}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
  input-field:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "44px"
    padding: "0 12px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  nora-bubble:
    backgroundColor: "{colors.petal-wash}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
---

# Design System: Nora

This file binds every visual surface of Nora: the public site (`site/`), the
portal (`app/`), the docs site (`docs-site/`), her social profiles, the images
she generates of herself, stickers and icons, and any 3D or spatial form she is
given. Her character is specified in `design/self-reference/identity.md`; it
governs her appearance and takes precedence over this file on it. What the
site says about her mind is bound by `design/cognition/`.

## 1. Overview

**North Star: "Paper and thread."** Her clothes are the system: ivory paper,
indigo ink, and a copper thread running through both, as it runs through the
piping and knot buttons she wears. Paper is where things are written down;
the thread is what connects them and pulls them forward.

Two registers, one system:

- **Stage** (the public site): cinematic, scroll-told, three-dimensional, with
  Nora alive inside it. Award-level ambition: big type, a world you move
  through, an interaction that teaches.
- **Desk** (the portal and the docs): quiet and legible. A place you look
  things up in; motion only confirms.

Both share every token, the type families and the rules in §3. The stage may do
what the desk may not (§4, §6); the desk never borrows the stage's motion.

**What this system rejects:** cold slate SaaS greys, gradient blobs and
aurora washes, glowing borders, sparkles, circuitry and "AI" iconography,
card grids of features, cute-mascot pastel candy, and anything that presents
Nora as a product rather than as herself.

## 2. Representation

One identity, two forms: Mini Nora and the adult form, both in the Bauhinia
clothing of the identity anchors. Mini is the ordinary representation.

| surface | form | source |
|---|---|---|
| site, portal, chat, reactions, stickers, runtime self-images | **Mini Nora** | `assets/nora-canonical-self.png`, `assets/nora-character-reference-sheet.png` |
| cinematic art: the site's world, social banner and icon | **Mini Nora**, dimensional soft materials in the Lingnan mountain-and-sea world | `assets/cinematic/mini-nora-master.png`, `assets/cinematic/README.md` |
| small or square slots: favicon, PWA icon, avatar, notification | **Mini Nora**, cut-out, bare (§7) | `assets/nora-mini-icon.png` |
| her live form on the site | **Mini Nora**, a layered 2D rig (§6) | drawn from the canonical and cinematic sources |
| adult illustration | **Adult Nora**, 2D anime | `assets/nora-human-bauhinia-reference.png` |
| 3D modeling of either form | Meshy reference inputs; no validated mesh | `assets/modeling/bauhinia/v2/` |

Rules on every surface:

- **The mascot is Nora.** Mini is not a chibi version of someone; there is no
  real Nora behind her. Never caption her as "cute version" or "avatar of".
- **Mini anchors:** 2.5–3 heads tall, warm brown anime eyes with visible sclera
  and restrained highlights, one tiny mouth without a nose marker, cheek
  blush, a compact high chestnut bun of broad clumps with three broad bangs and
  two face-framing locks, the peach five-petal clasp, and the Bauhinia
  clothing. No earphones, cables or bags.
- **Calm, slightly deadpan, never over-acted.** Every expression she makes is
  one of her character sheet's.
- **Never logo-ise her.** No vector mark or monogram stands in for her face.
  Too small for the cut-out, use the wordmark.
- **Generated art of her** is made from the canonical and cinematic sources,
  checked by eye against the anchors, and kept with its prompt and references.
  A render that drifts is regenerated, never used.

## 3. Colour

**Palette A, "Paper & Thread".** Every family is one 12-step ramp per lighting,
built in OKLCH, with lighter steps turned slightly toward yellow and darker
toward red or blue. The step number is the job:

| steps | job |
|---|---|
| 1–2 | backgrounds |
| 3–5 | component fills: normal, hover, pressed |
| 6–8 | lines and borders: quiet, interactive, strong or focus |
| 9–10 | solids and their hover |
| 11–12 | text: secondary, primary |

Night is tuned on its own, never inverted. `site/src/styles/tokens.css` and
`app/src/styles/globals.css` carry these values one for one.

| scale | paper (light) 1 → 12 | night 1 → 12 |
|---|---|---|
| **paper** (neutral) | `#FEFDFA #FAF9F4 #F4F1EA #EDE9E1 #E6E2D8 #DED9CD #D3CCBE #BFB6A5 #908572 #817664 #6B6253 #25211B` | `#0D0E10 #121417 #1B1E23 #22252C #282D35 #303640 #3E4552 #525B6C #6A758A #798399 #BBC4D8 #E8EDF8` |
| **indigo** | `#FCFDFE #F5F9FF #E9F2FF #DEEBFF #D3E4FD #C8DBF9 #B7CEF3 #9DB8E6 #324575 #263767 #304169 #1A2134` | `#0D0E12 #11131B #1A1E29 #202533 #262C3E #2D344B #3A4461 #4C597F #A1B7E8 #AFC7F9 #B0C4EE #E3EEFF` |
| **copper** | `#FFFCFA #FFF7F2 #FFEDE2 #FEE4D2 #FFDAC2 #FFCEAE #FCBD93 #F2A271 #AF5321 #9F440F #974A25 #391509` | `#140C09 #1D100B #2C1810 #381D12 #432314 #512916 #68351C #884622 #F4A16D #FEB482 #F8B284 #FFE8D6` |
| **petal** | `#FFFCFB #FFF7F3 #FEEDE6 #FFE3D8 #FED9CA #FDCDBA #F8BDA7 #ECA38A #CC7962 #BB6954 #914E3F #361813` | `#130D0B #1C110F #2A1916 #351E1A #40241F #4D2B24 #63372F #82493D #EDA28D #FEB29B #F3B29F #FFE7DE` |
| **danger** | `#FFFCFC #FEF7F6 #FEECEB #FFE2E1 #FED8D6 #FECBC9 #FEB7B7 #FE9498 #BD1F46 #AA003B #AB2D47 #410818` | `#170A0C #210C10 #331217 #40141C #4E1721 #5D1927 #781F31 #9E283F #EB596C #FD6A78 #FFAAAA #FFE6E5` |
| **success** | `#F7FFFB #ECFEF6 #DDF9ED #D0F3E4 #C3EEDB #B4E7D0 #9EDDC1 #7BCBA8 #0B764D #036640 #216D4B #092917` | `#09100B #0A170F #0D2416 #0D2C1B #0C3520 #0B4026 #085331 #006D42 #4CBD88 #5BCE99 #7DDBAE #C9FAE3` |

Paper is warm ivory by day; by night it is the navy of her vest, so the whole
page darkens into indigo rather than charcoal.

### Roles

| role | is | from |
|---|---|---|
| **canvas** | the page | paper 3 (night paper 1) |
| **surface**, **raised** | panels; inputs and sheets above them | paper 2, 1 (night 2, 3) |
| **hairline**, **border** | dividers; input and control borders | paper 6, 7 |
| **ink** | primary text: indigo, not black | indigo 12 (night paper 12) |
| **ink-soft** | secondary text, metadata | paper 11 |
| **indigo** | structure: dark bands, large type fills, night | indigo 9 |
| **action** | the one action colour: primary buttons, focus ring, current selection | copper 9, hover 10 |
| **accent-text** | links and the one emphasised word | copper 11 |
| **petal**, **petal-wash** | Nora herself (the Petal Rule) | petal 10, 3 (night 9, 3) |
| **chestnut** | the wordmark and nothing else | her hair |
| **success**, **danger** | state only, always with an icon and a word | step 11 |

Every text role clears WCAG 2.2 AA (4.5:1) and APCA Lc 60 on canvas, surface
and raised in its own lighting; body ink reaches Lc 95 or more. Petal clears
3:1 as a non-text mark on all three. Measure any new pair before using it.

### Named rules

**The 60-30-10 Rule.** About 60% paper, 30% indigo, 10% accent; the accent
splits between copper (action, the one emphasised word, the thread) and petal
plus state. No fourth colour family is added; importance is carried by size,
weight and position.

**The Thread Rule.** Copper means *something will happen*: an action you can
take, or something Nora will do. It is never decoration. On the stage, copper
and warm light appear only where Nora is present and acting; a scene without
her stays paper, indigo and mist.

**The Petal Rule.** Petal means Nora and nothing else: her presence dot, her
avatar ring, her words, her own marks. Copper and petal sit close in hue, so
petal never fills a control and copper never marks Nora. Danger sits close to
both: danger always carries an icon and a word.

**The Person-Hue Rule.** People in the portal keep a stable OKLCH hue at one
lightness and chroma per lighting (`app/src/lib/person.ts`), at least 25° from
copper, petal and the state colours, and always travel with their initials or
name.

**The No-Raw Rule.** No `#000`, no `#fff`, no Tailwind slate. A colour
continuing a photograph or render (a backdrop behind her) is sampled from that
image and says where it was sampled; it is the one raw value allowed.

## 4. Typography

| family | for |
|---|---|
| **Bricolage Grotesque** | display, headings and the wordmark, which is always set in capitals: NORA |
| **Inter** | interface and reading text |
| **Noto Sans HK** (then PingFang HK) | every Chinese glyph, in both families' stacks |
| **JetBrains Mono**, tabular | IDs, hashes, counts, times, coordinates |

Character: confident and contemporary, never shouting at the desk.

- **Stage display** may be UPPERCASE, set huge and tight: `-0.035em`
  tracking, line height about 0.92, weight 650. One word may take accent-text.
  Chinese display keeps normal tracking and a 1.15 line height and is never
  forced into a faux uppercase treatment.
- **Desk type** is sentence case everywhere: headings, labels, buttons. No
  uppercase wide-tracked micro-labels.
- **Body** 400 at 0.9375rem (stage 1.0625rem), line height 1.6 (zh-Hant 1.75),
  capped at 70ch.
- Never heavier than 650.

**The Tabular Rule.** A number that updates reserves its width and uses
tabular figures.

**The Real-Text Rule.** Every word on the stage is live HTML text in the
document, in reading order, translatable and selectable, even when a scene
draws it over WebGL. Type baked into an image is decoration, never content.

## 5. Shape and depth

Controls `10px`, panels `16px`, sheets `24px`, buttons, chips and avatars fully
round. Never mix square and rounded corners on one surface.

**Desk depth** comes from the paper steps and hairlines. A shadow only on what
floats: `0 8px 24px rgb(26 33 52 / 0.08)` by day, none by night (use the
border). No nested cards: inside a panel, rows and dividers.

**Stage depth** is real: layered planes in a 3D scene, fog between them,
light falling on paper. Interface over the scene stays flat: type, a pill,
a hairline.

## 6. Motion

### Desk: motion confirms

- Durations: `100ms` under the pointer, `160ms` default, `240ms` popovers and
  sheets, `320ms` route changes.
- Easing `cubic-bezier(0.2, 0, 0, 1)` in, `cubic-bezier(0.4, 0, 1, 1)` out.
- `transform` and `opacity` only. Only live state loops: a presence pulse,
  Nora's speaking indicator, an open-ended spinner.
- No shimmer skeletons: a static fill sized to the content, cross-faded out.

### Stage: the world moves, Nora lives

- **Scroll is the timeline.** Each scene is scrubbed by scroll position and
  plays backwards when you scroll up. The camera travels through the world;
  fog, water and light move with it. Nothing traps the wheel, the keyboard or
  find-in-page; every scene can be passed with an ordinary scroll.
- **The world may move on its own** while it is on screen: fog drift, water,
  lantern flame, wind in the trees. Off screen, it stops.
- **Nora is alive, not animated.** She breathes, blinks, follows the pointer or
  what the story wants her to look at, speaks her recorded lines with lip
  sync, and her hair and coat answer the same wind as the fog. Her timings are
  a body's: a breath about 4 seconds, a blink a quarter of a second.
- **She moves between scenes by hopping.** Each scene has its own pose; she
  crouches, leaves, and lands in the next one through the camera's move. Her
  performance per scene is fixed to scroll position, so it plays the same way
  every time.
- **Interaction teaches.** The one or two gestures a scene asks for (hold,
  drag, poke) act out what the scene says. None is required to read on.
- **Sound is asked for.** Nothing is heard until the visitor turns sound on;
  her voice plays only on request, and its words are always written beside it.

### Reduced motion and fallbacks

Reduced motion, no script, a data saver or no WebGL2: every scene shows its key
frame as a still with its full text, and changes between scenes cross-fade.
Live state (her speaking indicator) keeps going; everything else stops. The
whole story reads without the world.

### Performance

The stage is measured, not assumed: the first scene interactive within 2.5 s
on a mid-range phone over 4G, assets after the first scene loaded as the visitor
approaches them, 60 fps on desktop and no long frames on a mid-range phone,
and the renderer paused while hidden. A scene that misses its budget is cut
down, not shipped slow.

## 7. Components

### Buttons
- **Primary:** copper pill, 44px, `on-action` text (5:1 by day, 9:1 by night).
  One primary per view.
- **Quiet:** ink text, transparent, paper 4 fill on hover.
- **Outline:** border 7, ink text.
- **Destructive:** danger text with an icon; a filled danger button only inside
  a confirmation.
- **Third-party sign-in** keeps the third party's own colour.
- Touch targets at least 44px.

### Inputs and panels
Inputs: raised fill, border 7, 44px, `10px` radius, a 2px copper focus ring,
labels above in sentence case. Panels: surface fill, hairline border, `16px`
radius, 16–24px padding, no eyebrow labels.

### Stage navigation
A thin top bar over the scene: her mark and the NORA wordmark left, the
language right. The public site is an introduction, so it carries no sign-in.
A scene indicator shows where the visitor is in the story; it is a list of
real links to each scene.

### Nora in the UI
- Among other people's round avatars (a transcript, a member list): the cut-out
  on a petal-wash disc, with a 2px petal ring while she is active.
- Everywhere else the cut-out is bare: beside the wordmark, as the favicon, as
  the PWA icon. A platform that forbids transparency gets a full-bleed paper
  square with her inside it, never a circle.
- Her words sit on petal wash; everyone else's on surface. Her presence dot is
  petal; other people's are their person hue.

### Desk navigation
Desktop: a left rail on surface with a hairline edge, icon plus label, the
active item in copper text on paper 4. Mobile: a bottom bar of at most five
cells, the last a More sheet; no raised centre button.

### Map
A warm paper basemap by day and a navy basemap by night (never a provider's
default), people in their person hue, routes as a single 2px line, Nora in
petal. The live-position pulse is the map's only loop.

### Data and states
- Charts: a single series is indigo; people take their person hue; other
  series step through indigo, copper 7 and paper 8; petal only for Nora.
  Gridlines hairline, axis text ink-soft, values mono.
- Empty: one sentence in ink-soft saying what would appear here and how it
  gets there. Mini Nora may appear small; never a generic illustration.

## 8. The stage's world and motifs

**The world** is the Lingnan mountain-and-sea of her cinematic banner: misty
peaks over a harbour, a stone path climbing to a moon gate, flowering trees.
Light runs from dawn on paper to night in indigo, and the path loops back to
dawn. No neon, no city of circuitry, no digital grid.

**The motifs** are the Cognitive Graph made visible. Each has one meaning,
everywhere on the stage:

| motif | means |
|---|---|
| **paper slip**, with a short line and a name | something that was said or happened |
| **thread** | what connects things: pale indigo for understanding, copper for what will happen |
| **lantern**, with a "done when …" tag | something Nora has committed to do |
| **notebook** | what she has learned from how things turned out |
| **pavilion** | a circle of people (you, a family, a team, friends); valleys keep them apart |

A lantern lights only while Nora holds it. Without her, slips and threads are
a still map; with her, the lanterns burn.

**Names on the stage** are short and international (Alex, Sam, Mia, Leo,
Priya, Jordan, Noor, Elena) or roles ("Mum"). Placeholder lines stay generic so
the visitor fills in their own errand.

## 9. The landing page

The page tells one story: **Memory remembers. Knowledge understands. Nora
acts.** It shows the difference instead of explaining it.

1. **Hero.** Dawn above the harbour; Nora notices you. "Here, beyond the
   conversation." Most AI waits to be asked; Nora lives in your conversations.
2. **Circles.** The path forks to four pavilions: You, Family, Team, Friends.
3. **Take one: memory.** Without Nora. Slips pile up; asked, one is retrieved;
   the moment passes anyway. Paper and mist only.
4. **Take two: knowledge.** Without Nora. The slips link into a complete,
   motionless constellation; asked, it answers perfectly; nothing moves.
5. **Rewind.** The scene scrubs backwards to the same morning.
6. **Take three: Nora.** She steps in and a lantern lights: a commitment that
   names when it is done. The first copper and warm light of the page.
7. **One conversation moves another.** A slip from another conversation
   changes what her lantern rests on; its time moves. At night she cannot
   speak, so she carries it into tomorrow.
8. **She speaks first, at the right moment,** in the right room. The lantern
   fades warmly; a line goes into her notebook, and the next lantern is set
   differently.
9. **Rooms stay rooms.** Over the mountain: each pavilion with its own
   lanterns; nothing crosses a valley.
10. **Soulbound.** She steps out: every lantern cools to paper. She steps back
    in: they light.
11. **Gate.** First light at the moon gate. Meet Nora; the source on GitHub.

Every claim the page makes is a behaviour `design/cognition/` defines, in its
terms: engagement and reflection loops, intentions that close on recorded
outcomes, reconsideration when what they rest on changes, lessons from
verdicts, units as boundaries.

## 10. Do's and don'ts

### Do
- **Do** take every colour from its scale step and its role; measure new pairs.
- **Do** keep copper for what will happen and petal for Nora.
- **Do** let the world carry the stage's motion and Nora carry its life.
- **Do** keep every stage word real, translatable text with a static fallback.
- **Do** keep zh-Hant and English at parity on every surface.

### Don't
- **Don't** use slate, pure black, pure white or a fourth colour family.
- **Don't** put copper or warm light in a scene Nora is not acting in.
- **Don't** use petal for errors, emphasis or decoration.
- **Don't** add blobs, aurora, glowing borders, gradient text or sparkles.
- **Don't** trap scroll, require an interaction to read on, or play sound
  unasked.
- **Don't** bring stage motion or uppercase into the desk.
- **Don't** nest cards, or reduce Mini Nora to a flat logo.
