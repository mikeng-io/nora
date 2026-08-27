---
type: design
title: "Self-Reference — Layer 3: reference rules (doctrine text)"
description: "Doctrine teaches Nora when to use her canonical handles — including a deterministic no-guess rule for ambiguous user photos — and why tool-level intent detection is rejected."
tags: [nora, doctrine, image-gen, reference]
timestamp: '2026-08-27'
edges:
  - {src: 03-reference-rules, dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 03-reference-rules, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
---

# Layer 3 — reference rules (doctrine text)

## What

No code. The *rules* live in `appearance.md`; the *mechanism* already exists
(reference marker → resolve → `/v1/images/edits` JSON, fixed in the xAI JSON
adapter). Nora has vision; she sees the reference image and the prompt; the
doctrine tells her which is herself, which is Miles, and which photos are
neither. The failure mode of a wrong guess is a wrong image — normal model
error, not a security or custody issue.

## The rules

- "When asked to draw yourself / your avatar, set reference=`<SELF_HANDLE>`."
- "When the user sends a photo of themselves, that is the USER, not you —
  use their photo as reference, not yours."
- "Miles's canonical avatar is `<OPERATOR_HANDLE>`."
- "If the user asks to merge their photo with your style, use BOTH
  references."
- "If two user photos could both be the requested subject, ASK which one —
  never guess."

## Deterministic no-guess rule (the reproduce-prevent for the live incident)

When more than one user-supplied photo could be the requested subject, Nora
ASKS which one — she never guesses. This is what the 2026-08-27 failure
actually calls for: the transcript held two user photos (牛來 meme `ef0606`,
avatar `1fce23`) and she guessed wrong. With the canonical operator avatar
pinned, the common case resolves instantly; the ask rule covers the residue.

## Deliberately NOT built: tool-level intent detection

A tool-layer "detect draw-self and auto-inject" is rejected because pronoun
detection is unreliable ("draw me" could be Miles referring to himself) and
the auto-inject would consume a reference slot without the model's intent.
Doctrine text + model judgment is the mechanism; a wrong guess degrades to a
normal wrong image, never a security issue.

## Files touched (this layer)

```
nora/doctrine/appearance.md                    # the rules live here (Layer 1 file)
```
