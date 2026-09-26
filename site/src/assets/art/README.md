# Site art: provenance

Six renders of Mini Nora made for the public site on 2026-09-26, with the
image-generation tool of Codex CLI (`codex-cli 0.157.0`, `codex exec`). Each
was given the same two references, `assets/nora-canonical-self.png` and
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
| `notebook.webp` | memory | transparent |
| `quiet.webp` | what she won't do | transparent |
| `wave.webp` | the portal panel | transparent |
| `lost.webp` | 404 | transparent |

## Shared brief

```text
You are an art director's image-generation collaborator. Use your built-in image generation tool (do not draw with code, do not use PIL/SVG).

The attached images are the canonical reference for a character called Nora (Mini Nora): a stylized soft 3D mascot, about 2.5-3 heads tall, very large rounded head, messy dark-brown high bun with loose strands, long face-framing strands, side-parted messy fringe, minimal face (large vertical dark oval eyes with no visible iris, tiny short line mouth, nose effectively absent, soft pink blush on both cheeks), oversized light-grey zip hoodie worn open over a plain white top, loose baggy blue-grey jeans, chunky white low-top sneakers, and WHITE WIRED EARPHONES in both ears with the thin white cable falling across her front and a small white inline remote on it. Stay exactly on-model with the reference: same proportions, same render style and material quality (soft 3D, subsurface skin, fine hair strands), same outfit, earphones and cable visible. Expression minimal and calm, slightly deadpan; never over-acted, never anime. No logos, no text, no jewellery, no watermark.
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

## `notebook`

```text
Generate ONE image, square 1:1, on a TRANSPARENT background (PNG with alpha; if transparency is impossible, a flat plain #F4F2F2 background with no shadow).
- Nora sitting on a small plain wooden stool, a small open paper notebook on her lap, writing in it with a pencil, looking down at the page; earphones in, cable visible. Three-quarter view. Calm, focused.
- Full body in frame with some margin. Only the stool, notebook and pencil as props.
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
