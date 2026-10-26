# Site art: provenance

## Current 2D Bauhinia sources — 2026-10-26

All seven site character sources and the shared notebook master now use the
parent-accepted 2D anime Mini Bauhinia artwork, including closed-eye quiet.
The canonical Mini supplies identity: option08 eyes, Mini proportions, one tiny
mouth without a nose marker, compact broad hair clumps, five-petal clasp and
Bauhinia clothing. The prior compositions supply scene/pose intent only.
Transparent sources retain alpha; encoding follows the existing WebP recipe
at each source's actual aspect ratio. The standalone stool is unchanged.

The [central inventory](../../../../assets/visual-assets.json) records semantic
repository image parents, dated prompt intent, consumers and byte fingerprints.
Original PNGs and generation metadata remain private. The notebook master is
shared by the app/showreel; its old placement on this site remains retired.
Fresh static/rig alignment and browser acceptance are separate from source-art
acceptance. Runtime media ingest and deployment were not performed.

## September generation history

The following dates describe the original generation, before the option08
refresh. The brief and prompts below are historical recipes; prior eye wording is
preserved in the original operator spec and Git history.

Renders of Mini Nora made for the public site with the image-generation tool
of Codex CLI (`codex-cli 0.157.0`, `codex exec`): six on 2026-09-26, and on
2026-09-27 two more poses and a prop for her live presence
(`src/scripts/nora/`, `src/assets/rig/README.md`). Each pose was given the
same two references, `assets/nora-canonical-self.png` and
`assets/nora-character-reference-sheet.png`, the shared brief below, and its
own prompt. None is a `role: self` asset: none is ingested, and
`assets/nora-canonical-self.png` stays her one canonical image
(`design/self-reference/02-canonical-assets.md`).

Each was checked by eye against the eight anchors of
`design/self-reference/05-visual-identity-spec.md` before it was kept. The
first `wave` came back about four heads tall (anchor 1 says 2.5 to 3) and was
rejected; the kept one was re-prompted with proportions first.

Stored as WebP (quality 90, lossless alpha), transparent ones trimmed to their
alpha bounds plus a 4% margin. Astro re-encodes each at build time.

| file | used in | background |
|---|---|---|
| `night-portrait.webp` | hero and the form plate, dark theme: the canonical portrait's late-night twin, same pose | opaque |
| `group-chat.webp` | the chat demo (she sits on the window), link-preview images | transparent |
| `ledge.webp` | memory: she sits on her stool, writing (2026-09-27) | transparent |
| `stool.webp` | memory: the stool she sits on, which stays when she is elsewhere (2026-09-27) | transparent |
| `hop.webp` | never shown still: the pose she is in while she hops between places (2026-09-27) | transparent |
| `quiet.webp` | what she won't do | transparent |
| `wave.webp` | the portal panel | transparent |
| `lost.webp` | 404 | transparent |

`notebook.webp` (her on a stool, writing, one render) was the memory art until
2026-09-27. It went because she now moves between places and the stool does
not: a stool that arrives and leaves with her reads as wrong. `ledge.webp`
(her, sitting on nothing) on `stool.webp` (the stool alone, generated from
`notebook.webp` so it matches the one she used to sit on) replaced it; the two
are placed in `components/Memory.astro`, measured on both renders.

## Historical shared brief — superseded by the 2D production setting

```text
You are an art director's image-generation collaborator. Use your built-in image generation tool (do not draw with code, do not use PIL/SVG).

The attached images are the canonical reference for a character called Nora (Mini Nora): a stylized soft 3D mascot, about 2.5-3 heads tall, very large rounded head, messy dark-brown high bun with loose strands, long face-framing strands, side-parted messy fringe, option08 Balanced face (visible warm sclera, large brown irises, dark pupils, restrained highlights and a defined upper lid line, one tiny short-line mouth, no nose marker, soft pink blush on both cheeks), oversized light-grey zip hoodie worn open over a plain white top, loose baggy blue-grey jeans, chunky white low-top sneakers, and WHITE WIRED EARPHONES in both ears with the thin white cable falling across her front and a small white inline remote on it. Stay exactly on-model with the reference: same proportions, same render style and material quality (soft 3D, subsurface skin, fine hair strands), same outfit, earphones and cable visible. Expression minimal and calm, slightly deadpan; never over-acted, preserve mascot body proportions. No logos, no text, no jewellery, no watermark.
```

## `night-portrait`

```text
Generate ONE image, portrait 2:3: the "late night" twin of the first reference image.
- Exactly the same framing and pose as the first reference (standing, full body, facing camera, hands in hoodie pockets, centred, generous headroom and floor).
- Setting: the same seamless studio sweep after hours: backdrop a deep warm charcoal (around #1B1718 to #241F20, a faint warm cast, never blue-black), lit by one soft warm key light from upper left like a desk lamp just out of frame, gentle warm rim light on the hair, soft floor shadow. Quiet, calm, slightly sleepy mood; eyes open.
- No neon, no coloured gradients, no city lights, no sparkles, no props.
```

## `group-chat`

```text
Generate ONE image, square 1:1, on a TRANSPARENT background (PNG with alpha; if transparency is impossible, a flat plain #F4F2F2 background with no shadow).
- Nora sitting on the floor cross-legged, holding a phone in both hands at chest height and looking down at it, reading a group chat; earphones in, cable visible. Three-quarter view from the front. Calm, attentive.
- Full body in frame with some margin. No other props.
```

## `notebook` (retired 2026-09-27)

```text
Generate ONE image, square 1:1, on a TRANSPARENT background (PNG with alpha; if transparency is impossible, a flat plain #F4F2F2 background with no shadow).
- Nora sitting on a small plain wooden stool, a small open paper notebook on her lap, writing in it with a pencil, looking down at the page; earphones in, cable visible. Three-quarter view. Calm, focused.
- Full body in frame with some margin. Only the stool, notebook and pencil as props.
```

## `ledge` (2026-09-27)

Her eyes look at the viewer in this render; the live figure draws them looking
down at the page (`src/assets/rig/rig.json`, `gaze`).

```text
Generate ONE image, square 1:1, on a TRANSPARENT background (PNG with alpha).
- PROPORTIONS ARE THE PRIORITY: exactly the chibi proportions of the first reference: 2.5 to 3 heads tall, the head as wide as her shoulders plus arms, compact torso, short legs, small feet.
- Nora sitting on the very edge of a flat ledge that is NOT drawn at all: her bottom rests on an invisible horizontal line, and her short legs hang down in front of it, relaxed, sneakers dangling a little apart. A small open paper notebook rests on her lap; she writes in it with a short pencil, looking down at the page; earphones in, cable visible. Three-quarter view from the front. Calm, focused.
- Full body in frame with some margin. Only the notebook and the pencil as props. Do not draw the ledge, a bench, a stool, a floor or any shadow: everything around her is transparent.
```

## `hop` (2026-09-27)

```text
Generate ONE image, portrait 4:5, on a TRANSPARENT background (PNG with alpha).
- PROPORTIONS ARE THE PRIORITY: exactly the chibi proportions of the first reference: 2.5 to 3 heads tall, the head as wide as her shoulders plus arms, compact torso, short legs, small feet. She must not look taller or more adult than the reference.
- Nora in the middle of a small, low hop: both sneakers just off the ground, knees a little bent and tucked, both hands out of her pockets and held slightly out from her sides for balance, palms open; the open hoodie flares a little; the bun and the loose strands lift with the jump; the earphone cable swings up in a soft arc but both earbuds stay in her ears. Her face stays calm and deadpan (eyes open, tiny flat mouth), as if hopping is no big deal. Front view, turned very slightly to three-quarter.
- Full body in frame with some margin. No motion lines, no shadow, no ground, no props.
```

## `stool` (2026-09-27)

A prop, not a pose, so not the shared brief: its references were
`notebook.webp` (the stool to copy) and `ledge.webp` (who sits on it).
Trimmed to its alpha bounds plus 12 px.

```text
The first attached image shows a character sitting on a small plain wooden stool. I need THAT STOOL ALONE, with the character removed, as a separate prop in exactly the same render style, material, lighting and camera angle (soft 3D, warm natural wood, gentle soft studio light, three-quarter view from the front, camera at the same slightly-above height).
Generate ONE image, portrait 3:4, on a TRANSPARENT background (PNG with alpha).
- A small, plain, round-seated wooden stool with three simple straight legs and one thin rung, exactly like the stool in the first image: same proportions, same wood tone, same simple shapes.
- Nobody on it and nothing on it. No notebook, no pencil, no character, no hands.
- The whole stool in frame with a small margin, seat seen slightly from above so its top surface is visible.
- No floor, no shadow, no background: everything around the stool is transparent. No text, no watermark.
The second attached image is the character who will be composited sitting on it later; the stool seat must be wide enough for her (about the width of her hips) and low enough that her short legs dangle in front of it.
```

## `quiet`

```text
Generate ONE image, portrait 4:5, on a TRANSPARENT background (PNG with alpha; if transparency is impossible, a flat plain #F4F2F2 background with no shadow).
- Nora standing, eyes gently closed (two soft downward curves), head tilted very slightly, one hand lightly holding the earphone cable near the inline remote, the other hand in her hoodie pocket: she is listening. Front view. Serene, quiet.
- Full body in frame with some margin. No props.
```

## `wave`

```text
Generate ONE image, portrait 4:5, on a TRANSPARENT background (PNG with alpha).
- PROPORTIONS ARE THE PRIORITY: exactly the chibi proportions of the first reference: 2.5 to 3 heads tall, the head as wide as her shoulders plus arms, compact torso, short legs, small feet. She must not look taller or more adult than the reference. Same hair volume and colour (warm dark brown, big messy bun).
- Nora standing, giving a small, understated wave: one short arm raised to about head height with the palm open, the other hand in her hoodie pocket; eyes open, calm, deadpan. Front view, earphones and cable visible.
- Full body in frame with some margin. No props.
```

## `lost`

```text
Generate ONE image, square 1:1, on a TRANSPARENT background (PNG with alpha).
- PROPORTIONS: exactly the chibi proportions of the first reference, 2.5 to 3 heads tall.
- Nora standing, looking up and slightly to one side with the "slightly annoyed" deadpan expression from the character sheet (flat, half-lidded eyes), one hand scratching the back of her head, the other in her hoodie pocket: she has looked for something and it is not there. Three-quarter view. Earphones and cable visible.
- Full body in frame with some margin. No props.
```
