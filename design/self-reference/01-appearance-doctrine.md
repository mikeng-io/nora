---
type: design
title: "Self-Reference — Layer 1: appearance.md doctrine file"
description: "A new authored doctrine file (kind: identity, order 15) describing Nora's visual form and her recognition rule. Zero code: loader auto-discovery injects it every turn."
tags: [nora, doctrine, identity, appearance]
timestamp: '2026-08-27'
edges:
  - {src: 01-appearance-doctrine, dst: 00-overview, rel: realizes, provenance: asserted}
---

# Layer 1 — appearance.md (doctrine file)

## What

A new authored file in `nora/doctrine/`, `kind: identity` (same kind as
`identity.md`/`voice.md`, so it inherits the existing authority model and
audience filtering). The loader auto-discovers `*.md` (`loader.py:536`),
orders by numeric `order` (`loader.py:573`), filters by `applies_to`
(`loader.py:615`), and injects the assembled doctrine into the system prompt —
so a new file needs **zero code changes** to reach the model every turn.

## Contents

1. **Visual description** — derived from the canonical character-design
   reference sheet (`assets/nora-character-reference-sheet.png`, the
   descriptive source) and confirmed by the canonical single image
   (`assets/nora-canonical-self.png`, 1024×1536, the ASSET): slim East Asian
   woman, visual late 20s, 166 cm, dark brown hair and eyes, worn loosely
   tied back with wispy face-framing strands; casual minimal Korean style —
   white ribbed scoop-neck tank top under a light heather-grey zip-up hoodie
   worn open, mid-blue straight-leg jeans, white wired earbuds with the cable
   down the front. Written in the same descriptive register as the existing
   doctrine — it is *identity text for the model*, not prose. The sheet also
   carries her public-facing brand line (values: Clarity, Connection,
   Empathy, Impact; tagline: "Every voice matters. People first."), which the
   doctrine references where the model renders herself as the public
   assistant. **The wired earbuds are canon, not clutter** — they are part of
   the look defined by the sheet and are kept in the asset deliberately.
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
   `tools.py:3265-3266`) rather than a provider limit — the adapter sends N
   images via `images[]` (`xai_adapter.py:162`). With no operator anchor, "self
   wins over a second user image" is doctrine guidance, labelled advisory not
   mechanical — it decides slot order, not correctness.

## Authority fields (loader-enforced)

The loader requires `authority_above` / `authority_below` / `not_here` /
`change_policy` and enforces cross-file symmetry (`loader.py:51-54`,
`459-472`). Explicit here so the build does not rediscover the loader by
error:

- `appearance.md` is a **peer of `identity`/`voice`** — same tier, names
  neither.
- `authority_above: [constraints]`
- `authority_below: [tool-output]` (both terminals, no reciprocity)
- a substantive `not_here` (len > 40, per `test_doctrine_content.py:71-73`)

## Ordering

`order: 15` places it between `identity` and `voice` in the assembly. The
exact section-list assertions in `tests/test_doctrine_content.py:55-56` and
`tests/test_doctrine_assembly.py:44-45,56-57` must both include `appearance`
(see `04-tests-and-files.md`).

## Files touched (this layer)

```
nora/doctrine/appearance.md                    # new — kind: identity, order 15
tests/test_doctrine_content.py                 # authored set + exact-order include appearance.md
tests/test_doctrine_assembly.py                # exact section-list assertions include appearance.md
tests/test_doctrine_audiences.py               # appearance is tag-only; excluded from AUTHORED,
                                               #   mirroring silence
```
