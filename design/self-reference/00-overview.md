---
type: design
title: "Nora Self-Reference — Overview & Scope"
description: "Nora gains a canonical visual identity: an appearance doctrine file, two canonical media assets (self + operator), and doctrine-driven reference rules so she stops guessing what she and the operator look like. Why, what changes, what is out of scope, reading order."
tags: [nora, doctrine, image-gen, media, identity, custody]
timestamp: '2026-08-27'
edges: []
---

# Nora Self-Reference — Overview & Scope

## Why

Nora can already generate images (DEC-137) and hold a unified media library
(DEC-138). What she cannot do is **know what she — or the operator — looks
like**. Her doctrine gives her an identity (`identity.md`), a voice
(`voice.md`) and conduct rules (`conduct.md`), but nothing about appearance.
Every time a task involves her own appearance — "draw yourself", "make an
avatar", "merge my photo with your style" — she has no source of truth, so
she improvises from transcript fragments.

That improvisation failed live on 2026-08-27. Miles asked Nora to merge his
avatar photo (`1fce23`) with a rainy Tin Shui Chi scene. Nora's reasoning
(recorded in Langfuse) said *"combining his avatar (the 牛來 image ef0606)"* —
she confused a 牛來 meme from earlier in the transcript with Miles's avatar,
because she had **no authoritative description of what the images in her
library are**. The result (`eee623`) was Miles's avatar character rendered as a
cow mascot. The reference mechanism worked; the *identity model* did not.

Note the precise shape of the failure: Nora did not misidentify *herself* —
she misidentified **which of two user-supplied images was Miles's avatar**. So
this DEC must give Nora two canonical anchors, not one: her own appearance AND
the operator's avatar (role: `operator`), plus a deterministic no-guess rule
for the ambiguous case.

The failure mode is silent: the image looks plausible, and Miles has to *look
at it* to discover it is wrong. This is the same shape of problem
`identity.md` solved for *behaviour* — a canonical, injected, authoritative
definition — now applied to *appearance*.

## What this change is, and is not

**Is:** a doctrine file (`appearance.md`, kind: identity) describing Nora's
visual form and the operator's avatar; two canonical media assets in her own
library marked `provenance.role` = `self` / `operator`; a narrow custody
exemption so those two canonical handles resolve in any conversation; and
doctrine text teaching her when to use them — including a deterministic
no-guess rule for ambiguous user photos.

**Is not:** a new tool, a general asset registry, a schema migration (the
role marker rides the existing `provenance` jsonb), tool-level intent
detection for "draw yourself", or any change to the provider-agnostic
`ImageGenerator` contract.

## Design constraints

1. **Provider-agnostic, per DEC-137.** The self-reference mechanism must not
   depend on xAI; it rides the existing `ImageGenerator` port and the existing
   `ImageReference` custody model.
2. **Custody must not weaken (DEC-24, R-21).** The existing rule — a reference
   code resolves *inside this conversation only*, to a blob Nora already holds
   — is a security boundary against external-content injection. The canonical
   assets are an intentional, narrow exception: Nora's own operator-provided
   assets, not user-supplied content, so the exfiltration surface is zero.
3. **Keep it simple (Miles).** The smallest change that fixes the observed
   failure. Do not build a general "asset registry" abstraction; a `role`
   marker on two canonical rows suffices.
4. **Doctrine is the injection path.** `loader.py` already auto-discovers
   `*.md` in `nora/doctrine/` and injects them into the system prompt by
   `applies_to` audience. A new `appearance.md` with `kind: identity` needs
   **zero code changes** to reach the model every turn.
5. **No tool-level intent detection.** Detecting "draw me/yourself" in the
   prompt is unreliable (pronouns are ambiguous — "draw me" could be Miles
   referring to himself). Instead: teach Nora *when* to use her canonical
   assets via doctrine, and let her vision-capable judgment decide. If she
   guesses wrong, the outcome is a normal wrong image, not a security failure.

## What is explicitly out of scope

- Any new trigger, tool, or send path. `generate_image` stays; only its
  reference resolution gains the canonical-role exemption.
- A general asset registry / multi-character library. Two canonical assets
  (self, operator), marker in existing jsonb. If video/pets/other character
  assets appear later, the `role` pattern extends naturally — that is a
  future DEC.
- Auto-inject on pronoun detection (see `03-reference-rules.md`).
- The Standing diagnostic (whether Standing injection exists / reasoning
  content is retained) — verified present in Langfuse this session; tracked
  outside this bundle, which does not touch `nora/domain/standing.py`.

## Reading order

1. `01-appearance-doctrine.md` — Layer 1: the `appearance.md` doctrine file,
   its content, authority fields and ordering (zero code).
2. `02-canonical-assets.md` — Layer 2: the self + operator media assets, the
   custody exemption, and resolver precedence (small code, no migration).
3. `03-reference-rules.md` — Layer 3: the doctrine reference rules including
   the deterministic no-guess rule, and why tool-level detection is rejected.
4. `04-tests-and-files.md` — the full files-touched list, enforcing tests,
   and the DEC-141 index row.

## Status / Checkpoint

_2026-08-27_ — bundle drafted from the self-reference design discussion with
Miles, after an adversarial review (REQUEST CHANGES) of the earlier
single-file draft; all findings addressed (operator asset added, no-guess
rule, test list completed, authority fields specified, precedence pinned).
Supersedes the single-file draft at `design/proposed/31-self-reference.md`
(removed — this bundle is the source of truth going forward). Nothing built
yet; pending operator decision on the two canonical images (§5 questions in
`04-tests-and-files.md`).

_2026-09-16_ — **self-canon material changed, and the asset is now decided.**
The operator sent a character reference sheet (1536×1024, multi-panel) and
asked the design docs be updated to it: appearance is now casual minimal
Korean style (white tank, grey hoodie, jeans, wired earbuds), not the earlier
neon-grid Tron setting. A simplified single image was then produced and
accepted — **`assets/nora-canonical-self.png`** (1024×1536) is THE canonical
asset; the sheet stays the descriptive source (02 §"Source of the canonical
image", 04 §5.1). 01's description is written from the sheet and confirmed
against the asset. Both files travel with the branch so the bundle is
self-contained. Still open: the operator (`role=operator`) asset (04 §5.2).
