---
type: design
title: "Self-Reference — one identity, two representations: Mini Nora and Embodied Nora"
description: "Nora is one identity with a native form (Mini Nora, every internet surface) and an embodied form (a human-scale interface for spatial surfaces). The embodied form is an interface, not a human identity, so 05's 'no human-form identity' still holds."
tags: [nora, identity, appearance, avatar, embodiment]
timestamp: '2026-09-26'
edges:
  - {src: 07-native-and-embodied-form, dst: 05-visual-identity-spec, rel: depends_on, provenance: asserted}
  - {src: 07-native-and-embodied-form, dst: 00-overview, rel: realizes, provenance: asserted}
---

# One identity, two representations

Operator, 2026-09-26, adopting a framing worked out in a ChatGPT conversation.
Nora is not a person digitised into a computer, so there is no hierarchy in
which a human is the real Nora and a figure is her avatar. There is one
identity and more than one way to render it:

> **Mini Nora is how Nora represents herself natively on the internet.**
> **Embodied Nora is how Nora manifests when a human-scale physical or spatial
> interface is required.**
>
> **Embodied Nora is an interface, not an underlying human identity.**
>
> **Identity stays constant; embodiment is contextual.**

```text
                    Nora
              identity / cognition
                     │
               representation
              ┌──────┴──────┐
         native form    embodied form
          Mini Nora      human-scale
              │             │
      social, web,     3D / VR / AR,
      avatars, media,  mocap, spatial
      stickers         display, full-body
```

## Mini Nora — the native form

The mascot of 05, 2.5–3 heads tall. It is not a "chibi version" or a mascot
standing in for someone: it is her most native visual identity, because she is
an internet-native entity. Every internet surface uses it: profile pictures,
Discord, Signal, Threads, Instagram, a website, reactions, stickers and any
image she generates. It may be 2.5 heads tall because the internet does not
care about human anatomy.

## Embodied Nora — the human-scale interface

Used only when she enters an environment built at human scale: a 3D virtual
space, a desktop assistant, VR or AR, a spatial display or projection, motion
capture, full-body interaction, realistic gesture and locomotion, face-to-face
conversation. It has 6.5–7 heads, a standard humanoid skeleton, full fingers
and facial blendshapes, **not because it is closer to the real Nora but because
the interface protocol of the human world is the human body.** It is a protocol
adapter: she instantiates an anthropomorphic interface to meet a human-scale
environment, not the other way round.

This is what keeps the framing clear of the virtual-influencer pattern (an AI
presenting as a real young woman), which 05 already rules out.

## How this sits with 05 and the retired human form

05 says "There is no separate photorealistic or human-form identity" and "The
mascot form is Nora". Both still hold: the embodied form is an interface, not a
second identity, and not the 2026-09-16 human-form canon (a woman in her late
20s, 166 cm) that 2026-09-23 retired. That retirement stands. The difference:
the retired form claimed to be *who she is*; the embodied form claims only to
be *how she meets a human-scale space*.

Further representations fit without touching identity: voice-only Nora has no
visual form at all; a desktop widget can be more abstract; a robot would be a
physical embodiment.

## The assets

Both new references sit in `assets/` beside the canon, flat, named by form. None
is ingested (02 is unchanged: exactly one `role: self` asset).

| file | form | role |
|---|---|---|
| `nora-canonical-self.png` | mini | **the** canonical asset, ingested (02) |
| `nora-character-reference-sheet.png` | mini | descriptive source (02) |
| `nora-social-avatar.png`, `nora-social-banner.png` | mini | reference only (02) |
| `nora-mini-icon.png` (1261×1247, transparent) | mini | reference only: cut-out head-and-arms icon for stickers and small surfaces |
| `nora-mini-tpose.png` (1024×1536) | mini | reference only: T-pose front view, the image-to-3D input for the native form's rig |
| `nora-embodied-tpose.png` (1024×1536) | embodied | reference only: T-pose front view, the image-to-3D input for the embodied form's rig |

The two T-poses are the inputs the parked 3D avatar spike was waiting for
(branch `mikeng-io/avatar-3d-spike`, VRM pipeline under `avatar/`). Model files
that pipeline produces belong under its `avatar/source/`, not here.

`assets/` stays flat. Seven images, the canonical one path-pinned by
`scripts/ingest_self_asset.py`, `tests/test_skill_content.py` and
`tests/unit/test_canonical_self_reference.py`; the form is carried by the name
prefix. A subdirectory earns its place when a form gains more than a handful of
files.

## Observations on the references, not yet decided

- **Neither T-pose wears the earphones.** Right for a rig base: the avatar
  spike already adds earphones as a separate decal/mesh, so the cable does not
  deform with the arms.
- **The embodied T-pose departs from 05 beyond proportion, in rendering first
  and wardrobe second.** It is a photorealistic render (realistic face, skin
  and anatomy), where 05 says "Nora is a stylized 3D character, not a
  photorealistic human" and to avoid "virtual-influencer aesthetics" and
  "generic AI-girl imagery": the very pattern this node's framing exists to
  stay clear of. Wardrobe: a cropped, fitted top in place of the plain white
  top, and no earphones. 05 says the core anchors hold
  "across avatars, illustrations, 3D environments"; whether the embodied form
  is bound by the anchors other than proportion (hair, hoodie, earphones,
  jeans, sneakers) and by the rendering exclusions is an operator decision
  this node does not make.

## Runtime: unchanged, deliberately

Nothing she reads changes. Every image she generates is an internet surface, so
the image-generation skill's "that form is you: there is no human or photoreal
version" is exactly the Mini Nora rule, and `identity.md`'s one form sentence
(DEC-205) is the native form. No spatial surface exists yet, so telling her
about an embodied form would give her a fact with no occasion to use it and a
way to answer "draw yourself" with the wrong one. The "identity stays constant;
embodiment is contextual" line becomes doctrine when the first spatial surface
ships, measured with `python -m evals appearance` like DEC-205 was.
