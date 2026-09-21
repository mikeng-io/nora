---
type: design
title: "Self-Reference — Files touched, enforcing tests, DEC-141"
description: "Consolidated build checklist, enforcing tests across doctrine/media/contract layers, and the DEC-141 index row for design/09-decision-index.md."
tags: [nora, design, tests, dec]
timestamp: '2026-08-27'
edges:
  - {src: 04-tests-and-files, dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 04-tests-and-files, dst: 01-appearance-doctrine, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 03-reference-rules, rel: depends_on, provenance: asserted}
---

# Files touched, enforcing tests, DEC-141

## Files touched (when built)

```
nora/doctrine/appearance.md                    # new — kind: identity, order 15
nora/domain/ports.py                           # ImageReference docstring: canonical-role exemption
nora/agent/tools.py                            # resolver closure: role in (self,operator) bypasses
                                               #   conversation scope; conversation-first precedence;
                                               #   full-sha match for canonical handles
nora/infra/sql/media_objects.py                # query for provenance->>'role' IN ('self','operator')
scripts/ingest_self_asset.py                   # operator one-shot: ingest design image + mark role
tests/contract/test_image_generator_contract.py # + canonical-role resolves cross-conversation;
                                               #   non-canonical still refused; collision resolves
                                               #   to nothing; external URL still refused; doctrine
                                               #   embedded handles resolve to canonical rows
tests/test_doctrine_content.py                 # authored set + exact-order now include appearance.md
tests/test_doctrine_assembly.py                # exact section-list assertions include appearance.md
tests/test_doctrine_audiences.py               # appearance is tag-only; excluded from AUTHORED,
                                               #   mirroring silence
design/09-decision-index.md                    # + DEC-141 row (see below)
config.toml                                    # (none expected — asset handle goes in doctrine, not config)
```

## Enforcing tests

| Layer | Test | Pins |
|---|---|---|
| Doctrine content | `tests/test_doctrine_content.py` | appearance.md present, kind=identity, no constraint keywords; exact-order list includes appearance |
| Doctrine assembly | `tests/test_doctrine_assembly.py` | exact section lists `[identity, appearance, voice, silence, conduct, capability, constraints]` (and the no-capability variant) |
| Doctrine audiences | `tests/test_doctrine_audiences.py` | applies_to includes tag; appearance excluded from AUTHORED mirroring silence (so the heartbeat-degeneracy test stays green) |
| Resolver contract | `tests/contract/test_image_generator_contract.py` | canonical-role resolves cross-conversation; a non-canonical row in another conversation is still refused; collision between conversation asset and canonical asset resolves to nothing; external URL still refused; doctrine-embedded handles resolve to canonical rows (a re-ingest that changes a sha fails loudly at test time instead of silently producing a dead handle) |
| Live smoke | vision-verified manual check | "draw yourself" with no user reference yields an image consistent with the canonical design |

## Open questions (operator decision needed)

1. **Which image is canonical for `self`?** **DECIDED 2026-09-16.** Two files,
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
2. **Which image is canonical for `operator`?** The avatar Miles asked Nora to
   merge in the failing run is `1fce23` — unless Miles has a different
   canonical avatar in mind. **Decision needed: confirm the operator asset.**
   (Default proposal: `self` = Codex design image, `operator` = `1fce23`.)
3. **Doctrine `appearance.md` text should be written from the canonical
   image.** If Miles picks a different image than the one Norty has seen, the
   description must be re-derived from that image before the doctrine is
   finalized.
4. **Should `appearance.md` also be injected for `heartbeat` audiences?**
   Default: `[tag]` only (image generation happens in tagged turns). If
   heartbeat turns can also generate images, widen.

## DEC-141 (index row for `design/09-decision-index.md`)

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-141 | **Nora has a canonical visual identity: a doctrine `appearance.md` (kind: identity) describing her look and the operator's avatar, plus two canonical assets in her media library (marked `provenance.role` = `self` and `operator`), which resolve as image references in ANY conversation (narrow custody exemption, operator-provided assets only) so she can reference her own design image when asked to draw herself and Miles's avatar when asked to draw him; when more than one user photo could be the requested subject she ASKS rather than guessing; doctrine text, not tool-level intent detection, decides when canonical assets are used.** | `tests/contract/test_image_generator_contract.py` (canonical-role resolves cross-conversation; a non-canonical row in another conversation is still refused; collision between conversation asset and canonical asset resolves to nothing; external URL still refused; doctrine-embedded handles resolve to canonical rows), `tests/test_doctrine_content.py` (appearance.md present, kind=identity, no constraint keywords), `tests/test_doctrine_assembly.py` (exact section lists include appearance), `tests/test_doctrine_audiences.py` (applies_to includes tag; appearance excluded from AUTHORED mirroring silence), vision-verified smoke: "draw yourself" with no user reference yields an image consistent with the canonical design |
