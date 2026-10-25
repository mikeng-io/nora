---
type: design
title: "Self-Reference — Layer 1: her appearance, as a section of the image-generation skill"
description: "Her visual form and recognition rules. Designed as an always-loaded doctrine file; BUILT as a section of the on-demand image-generation skill (operator, 2026-09-23). See 06."
tags: [nora, doctrine, identity, appearance]
timestamp: '2026-08-27'
edges:
  - {src: 01-appearance-doctrine, dst: 00-overview, rel: realizes, provenance: asserted}
---

# Layer 1 — her appearance

> **Superseded placement (operator, 2026-09-23).** This node designed
> an `appearance.md` doctrine file (never created), injected every turn. It was never built. The
> operator placed the content in a SKILL instead,
> `nora/skills/image-generation/SKILL.md`, loaded on demand when she makes or
> edits an image, with her appearance as one section: her look is needed only
> then, and design/12's placement rule puts occasional domain instruction in a
> skill. The trade-off: a turn that does not load the skill draws without the
> canon. The CONTENT requirements below (§Contents) still hold and were built;
> the doctrine mechanics (kind, order, `applies_to`, authority fields, the
> doctrine tests) do not apply. `06-implementation-plan.md` is the as-built
> record. The loader line numbers this node cited had drifted and are removed.
>
> **Partly reversed (operator, 2026-09-25, DEC-205, #499).** The trade-off
> above ("a turn that does not load the skill draws without the canon") is
> what a turn that only ASKS about her appearance also paid, and it produced
> two live failures: "what do you look like?" got no always-loaded answer, and
> "draw yourself" with no reference read `identity.md`'s "not a person" as
> "no form". `nora/doctrine/core/identity.md` now carries ONE short, descriptive
> sentence — not this node's full doctrine-file design, and not a second copy
> of §Contents below, which stays the skill's alone. See DEC-205.

## What

As built: the "What you look like" section of
`nora/skills/image-generation/SKILL.md`, an operator-authored skill loaded on
demand through the `skills` tool (DEC-50), with two sibling sections on the
same page: whether she is in the picture at all, and whose photo is whose.
The designed doctrine file (`kind: identity`, order 15, injected every turn)
was not built; see the banner.

## Current appearance amendment, 2026-10-26

[09](09-mini08-balanced.md) supersedes the September eye description: Mini
now has visible sclera, brown irises, pupils, highlights and an upper lid
line, with no nose marker and one tiny mouth. [08](08-bauhinia-human-form.md)
supersedes the old no-human-form statement for human design. The September
content below is historical where those clauses disagree. Runtime images
still use Mini; no live ingest or release is performed by this amendment.

## September contents (appearance amended above)

1. **Visual description** — written from the operator's own spec,
   `05-visual-identity-spec.md` (2026-09-23, verbatim), and confirmed against
   the canonical single image (`assets/nora-canonical-self.png`, 1024×1536,
   the ASSET) and the character sheet
   (`assets/nora-character-reference-sheet.png`, the descriptive source). She
   is a **stylized 3D mascot, about 2.5–3 heads tall**, not a human and not a
   miniaturised person: dark-brown messy high bun with face-framing strands,
   minimal dark oval eyes and a line of a mouth, soft cheek blush, an
   oversized light-grey zip hoodie over a plain white top, loose blue-grey
   jeans, chunky white sneakers, and **white wired earphones with an inline
   remote, always visible where the composition allows**. The spec's eight
   core visual anchors are the minimum the description must carry; its
   proportion constraint (head-to-body ratio wins over realistic anatomy) is
   the one rule a generated image most often breaks, so it is stated as a
   rule, not a detail. The backpack is optional and never default.
   **There is no human form.** The 2026-09-16 description (a slim woman in
   her late 20s, 166 cm) is superseded, not kept as an alternative: the
   spec's identity rule is "the mascot form is Nora". Written in the same
   descriptive register as the existing doctrine: it is *identity text for
   the model*, not prose.
2. **Self-recognition rule** — "Your canonical self reference is
   `[image <SELF_HANDLE>]`; when the task is to draw yourself or your avatar,
   reference it. A photo of a person in the conversation is NOT you."
3. **No operator anchor in v1** — there is **no `OPERATOR_HANDLE`**. v1 pins
   one canonical asset only (operator decision 2026-09-16, `「no need operator,
   just nora」`), so the identity file names no handle for the operator and the
   doctrine must not imply one exists. A photo the user supplies is handled by
   a **user-reference rule** instead: "when the user sends a photo of
   themselves, that is the USER — use their photo as reference"; when more
   than one user photo could be the subject, ask rather than guess (03
   §"Deterministic no-guess rule"). This is the accepted v1 trade-off: the
   2026-08-27 failure class is covered by the ask rule, not by a pinned handle.
4. **Conflict rule** — when a user supplies their own reference image AND asks
   for "your style", use both; the reference slot budget is a **tool-surface
   convention** (three params `reference`/`reference_2`/`reference_3`,
   the `generate_image` signature) rather than a provider limit — the adapter sends N
   images via `images[]` (`XaiImageGenerator`). With no operator anchor, "self
   wins over a second user image" is doctrine guidance, labelled advisory not
   mechanical — it decides slot order, not correctness.
   **Superseded in part 2026-10-09 (DEC-307):** the three slots are now one
   `reference_images` list whose limit IS the active provider's (xAI 5,
   Codex 16; `design/proposed/29-image-generation.md` §12). The ordering
   guidance above is unchanged.

## Authority, ordering, audiences

**SUPERSEDED 2026-09-25 (issue #187 finding 3) for the three frontmatter
keys below — `nora/skills/loader.py::parse_skill` now fails the gate on any
of them, so a skill carries none of the three any more. Historical text
kept as written; it describes the file as built, before that fix.**

Not applicable as designed. A skill carries `authority_above: [situation]`,
`authority_below: [message-content, tool-output]` and `change_policy:
operator-only` like its two siblings, and none of that is enforced by a loader
(#241). There is no assembly order and no `applies_to`: the skill reaches any
occasion whose agent has the skills plugin, when she loads it.

Item 4's "self wins over a second user image" slot order is replaced in the
build by a provider fact (06 §3): the output takes the first reference's
aspect ratio unless one is named, so the skill orders references by the
framing wanted.

## Files touched (as built)

```
nora/skills/image-generation/SKILL.md          # new — the skill; appearance is one section
tests/test_skill_content.py                    # skill content, handle, anchors, not-in-doctrine
tests/test_factory.py                          # available-skill list gains image-generation
```
