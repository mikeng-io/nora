---
type: design
title: "Self-Reference — Layer 3: reference rules (doctrine text)"
description: "Doctrine teaches Nora when to use her canonical self handle — and the deterministic no-guess rule that carries the operator-photo ambiguity, since v1 pins no operator asset — and why tool-level intent detection is rejected."
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
doctrine tells her which is herself and which photos are not. The failure mode
of a wrong guess is a wrong image — normal model error, not a security or
custody issue.

## The rules

- "When asked to draw yourself / your avatar, set reference=`<SELF_HANDLE>`."
- "When the user sends a photo of themselves, that is the USER, not you —
  use their photo as reference, not yours."
- "If the user asks to merge their photo with your style, use BOTH
  references."
- "If two user photos could both be the requested subject, ASK which one —
  never guess."

## Deterministic no-guess rule (the reproduce-prevent for the live incident)

When more than one user-supplied photo could be the requested subject, Nora
ASKS which one — she never guesses. This is what the 2026-08-27 failure
actually calls for: the transcript held two user photos (牛來 meme `ef0606`,
avatar `1fce23`) and she guessed wrong.

**This rule is now load-bearing, not a backstop.** v1 pins no `operator` asset
(operator decision 2026-09-16), so there is no canonical handle that would
resolve the common case instantly — the ask rule is the **only** cover for the
whole operator-photo ambiguity class. That is the accepted v1 trade-off. If the
ask rule proves insufficient in practice, the resolution is to pin an
`operator` asset (a new DEC), NOT to weaken this rule.

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
