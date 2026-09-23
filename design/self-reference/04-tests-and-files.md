---
type: design
title: "Self-Reference — Files touched, enforcing tests, DEC-193"
description: "Consolidated build checklist, enforcing tests across doctrine/media/contract layers, and the DEC-193 index row for design/09-decision-index.md."
tags: [nora, design, tests, dec]
timestamp: '2026-08-27'
edges:
  - {src: 04-tests-and-files, dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 04-tests-and-files, dst: 01-appearance-doctrine, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 03-reference-rules, rel: depends_on, provenance: asserted}
---

# Files touched, enforcing tests, DEC-193

## Files touched (when built)

```
nora/doctrine/appearance.md                    # new — kind: identity, order 15
nora/domain/ports.py                           # ImageReference docstring: canonical-role exemption
nora/agent/tools.py                            # resolver closure: role == 'self' bypasses
                                               #   conversation scope; conversation-first precedence;
                                               #   full-sha match for the canonical handle
nora/infra/sql/media_objects.py                # query for provenance->>'role' = 'self'
scripts/ingest_self_asset.py                   # operator one-shot: ingest design image + mark role
tests/contract/test_image_generator_contract.py # + canonical-role resolves cross-conversation;
                                               #   non-canonical still refused; collision resolves
                                               #   to nothing; external URL still refused; doctrine
                                               #   embedded handle resolves to the canonical row
tests/test_doctrine_content.py                 # authored set + exact-order now include appearance.md
tests/test_doctrine_assembly.py                # exact section-list assertions include appearance.md
tests/test_doctrine_audiences.py               # appearance is tag-only; excluded from AUTHORED,
                                               #   mirroring silence
design/09-decision-index.md                    # + DEC-193 row (see below)
config.toml                                    # (none expected — asset handle goes in doctrine, not config)
```

## Enforcing tests

| Layer | Test | Pins |
|---|---|---|
| Doctrine content | `tests/test_doctrine_content.py` | appearance.md present, kind=identity, no constraint keywords; exact-order list includes appearance |
| Doctrine assembly | `tests/test_doctrine_assembly.py` | exact section lists `[identity, appearance, voice, silence, conduct, capability, constraints]` (and the no-capability variant) |
| Doctrine audiences | `tests/test_doctrine_audiences.py` | applies_to includes tag; appearance excluded from AUTHORED mirroring silence (so the heartbeat-degeneracy test stays green) |
| Resolver contract | `tests/contract/test_image_generator_contract.py` | canonical-role resolves cross-conversation; a non-canonical row in another conversation is still refused; collision between conversation asset and canonical asset resolves to nothing; external URL still refused; the doctrine-embedded handle resolves to the canonical row (a re-ingest that changes a sha fails loudly at test time instead of silently producing a dead handle) |
| Live smoke | vision-verified manual check | "draw yourself" with no user reference yields an image consistent with the canonical design |

## Open questions (operator decision needed)

1. **Which image is canonical for `self`?** **RE-DECIDED 2026-09-23:** the
   operator replaced the human-form image with the mascot form; the files
   below keep their paths and roles, the bytes changed (asset sha256
   `535e4da5…`, sheet now the 1254×1254 "Mini Form" sheet, spec in
   `05-visual-identity-spec.md`). The 2026-09-16 decision it replaces, kept
   for its reasoning: **DECIDED 2026-09-16.** Two files,
   two roles:
   - **`assets/nora-canonical-self.png`** (1024×1536) is **THE ASSET** — the
     single clean full-body front-facing studio image accepted by the operator
     (`「Just use this one ba」`, 2026-09-16). Individually vision-checked
     before acceptance: single image, no panels/text/watermark, full body
     head-to-feet, eye-level, even soft lighting, plain seamless light-grey
     background, neutral expression. Its known imperfections are recorded and
     accepted: the earbud cable crosses face-side/torso, loose strands soften
     the jaw edge, hair is tied back (full length undocumented), the open
     hoodie hides the shoulder silhouette. The earbuds are CANON (the sheet
     defines them), so the cable is not treated as contamination.
   - **`assets/nora-character-reference-sheet.png`** (1536×1024) is the
     **descriptive source only** — multi-panel; the doctrine text in 01 is
     written from it, and it is deliberately NOT ingested (a multi-panel image
     muddles identity in generation). The earlier Codex neon-Tron setting
     image is NOT the canon and is superseded by both.
   The operator's earlier question («呢一張會唔會太過複雜?») is resolved by this
   split: complexity is kept in the sheet's role (description), not the
   asset's (identity).
2. **Is there an `operator` asset in v1?** **DECIDED 2026-09-16: NO** —
   `「no need operator, just nora」`. v1 pins exactly one canonical asset
   (`role=self`). Consequence, recorded rather than hidden: the 2026-08-27
   failure class (which of two user photos is the operator's) is covered by the
   03 ask rule alone, with no pinned handle to resolve the common case. If that
   proves insufficient in practice, pinning an `operator` asset (e.g. `1fce23`)
   is a **new DEC** — and the resolution is NOT to weaken the ask rule.
3. **Doctrine `appearance.md` text should be written from the canonical
   image.** Satisfied for v1: 01 is written from the reference sheet and
   confirmed against `assets/nora-canonical-self.png`, whose byte-identity the
   operator confirmed (sha256 `4b8825fe…`). If a different `self` image is ever
   substituted, the description must be re-derived and the handle re-pinned
   before merge. **That happened on 2026-09-23**: 01 is re-derived from
   `05-visual-identity-spec.md` and the new asset (`535e4da5…`); nothing was
   built, so no handle needed re-pinning.
4. **Should `appearance.md` also be injected for `heartbeat` audiences?**
   Default: `[tag]` only (image generation happens in tagged turns). If
   heartbeat turns can also generate images, widen.

## DEC-193 (index row for `design/09-decision-index.md`)

*Renumber history:* this decision was drafted as **DEC-139**, moved to **DEC-141**
when 139/140 were allocated to `event-abstraction` and `standing` on the design
branch, and renumbered to **DEC-193** at PR time when `origin/master` was found to
already hold DEC-141…144 (the trace series, issue #64) and DEC-145…192. The row was
never previously registered in the repo-wide index, so the move collides with no
published number.

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-193 | **Nora has a canonical visual identity: a doctrine `appearance.md` (kind: identity) describing her look, plus ONE canonical asset in her media library (marked `provenance.role` = `self` — v1 pins no `operator` asset, operator decision 2026-09-16), which resolves as an image reference in ANY conversation (narrow custody exemption, operator-provided assets only, exact role match) so she can reference her own design image when asked to draw herself; because no operator asset is pinned, when more than one user photo could be the requested subject she ASKS rather than guessing — that ask rule is the sole cover for the operator-photo ambiguity class; doctrine text, not tool-level intent detection, decides when the canonical asset is used.** | `tests/contract/test_image_generator_contract.py` (canonical-role resolves cross-conversation; a non-canonical row in another conversation is still refused; collision between conversation asset and canonical asset resolves to nothing; external URL still refused; the doctrine-embedded handle resolves to the canonical row), `tests/test_doctrine_content.py` (appearance.md present, kind=identity, no constraint keywords), `tests/test_doctrine_assembly.py` (exact section lists include appearance), `tests/test_doctrine_audiences.py` (applies_to includes tag; appearance excluded from AUTHORED mirroring silence), vision-verified smoke: "draw yourself" with no user reference yields an image consistent with the canonical design |
