---
type: design
title: "Self-Reference — Files touched, enforcing tests, DEC-193"
description: "Files touched, enforcing tests and the DEC-193 index row, as BUILT (2026-09-23): an image-generation skill, the resolver exemption, the ingest. The design-time list it replaces was stale against the code; 06 records why."
tags: [nora, design, tests, dec]
timestamp: '2026-08-27'
edges:
  - {src: 04-tests-and-files, dst: 00-overview, rel: realizes, provenance: asserted}
  - {src: 04-tests-and-files, dst: 01-appearance-doctrine, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 02-canonical-assets, rel: depends_on, provenance: asserted}
  - {src: 04-tests-and-files, dst: 03-reference-rules, rel: depends_on, provenance: asserted}
---

# Files touched, enforcing tests, DEC-193

## Files touched (as built, 2026-09-23)

The design-time list here named a doctrine file, three doctrine test files
and an exact assembly list that still included a `capability` section DEC-150
had deleted. None of that was built: the operator placed the content in an
on-demand skill (01's banner, 06 §1). What was built:

```
nora/skills/image-generation/SKILL.md          # new — in-the-picture judgement, whose photo is
                                               #   whose, her appearance (05), per-model notes
nora/domain/ports.py                           # ImageReference docstring: the exemption
nora/agent/tools.py                            # resolve: conversation first, then the full-sha
                                               #   role == "self" row; URL/data refusal; docstring
scripts/ingest_self_asset.py                   # operator one-shot, checked against the skill's handle
scripts/mutations/dec-193-self-reference.json  # the revert proofs (06 §6)
tests/unit/test_canonical_self_reference.py    # the exemption and every negative
tests/unit/test_ingest_self_asset.py           # the ingest
tests/test_skill_content.py                    # the skill (and a per-skill line ceiling)
tests/test_factory.py                          # available-skill list
design/09-decision-index.md                    # DEC-193 row: built, with selectors
```

No config change and no migration.

## Enforcing tests (as built)

| Layer | Test | Pins |
|---|---|---|
| Resolver | `tests/unit/test_canonical_self_reference.py` | canonical full sha resolves in a room that never held it; a role other than exactly `"self"` never gets the exemption; a short handle never reaches it; a conversation blob sharing the short handle wins, two resolve to nothing; URL/data refused before the provider; no store or no bytes → nothing; the skill's handle resolves after ingesting the real asset |
| Ingest | `tests/unit/test_ingest_self_asset.py` | role/source marked; a pre-existing row marked; a sha the skill does not name refused with nothing written; an unreadable index refused; idempotent |
| Skill | `tests/test_skill_content.py` | discoverable, triggers on any image; handle equals the asset's sha256; in-the-picture judgement first and not a phrase menu; no-guess rule; every load-bearing clause of 05 and eight anchors; one section per configured model; no docstring restatement; not in any doctrine audience |
| Live smoke | vision-verified manual check (owed, not run) | "draw yourself" with no user reference yields an image consistent with the canonical design |

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
   heartbeat turns can also generate images, widen. **Moot 2026-09-23:** the
   code shows `generate_image` in every occasion's tool set (fire turns
   included), and the content became an on-demand skill, which has no
   audience field at all.

## DEC-193 (index row for `design/09-decision-index.md`)

*Renumber history:* this decision was drafted as **DEC-139**, moved to **DEC-141**
when 139/140 were allocated to `event-abstraction` and `standing` on the design
branch, and renumbered to **DEC-193** at PR time when `origin/master` was found to
already hold DEC-141…144 (the trace series, issue #64) and DEC-145…192. The row was
never previously registered in the repo-wide index, so the move collides with no
published number.

(The live row is in `design/09-decision-index.md`; it was rewritten when this was built, and is not copied here, so the two cannot drift.)
