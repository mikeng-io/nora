---
type: design
title: "Doctrine sort: law, practice, character; classify first, cut last"
description: "Every doctrine rule is sorted into law (stays), practice (Lessons) or character (Character · Nora, plus an authored minimum for surfaces with no unit). The sort ships before Lessons with no text change, so the doctrine check reads law only. The cut ships last, when the receivers exist. Amends 55-self §2, which lists voice and conduct as 'given'."
tags: [nora, doctrine, law, lessons, character]
status: step 3 built (DEC-291, 2026-10-07): classified in nora/doctrine/classification.yaml, audit in 31-doctrine-audit.md, no text change; step 5 (the cut) not built
timestamp: '2026-10-07'
edges:
  - {dst: 40-lessons, rel: sequences, provenance: asserted}
  - {dst: 31-doctrine-audit, rel: realizes, provenance: asserted}
---

# 30 · Doctrine sort

## 1. The three buckets

| bucket | test | goes to |
|---|---|---|
| **law** | breaking it lies, harms someone, crosses a trust boundary, or claims a capability she lacks, in any unit | stays in the doctrine |
| **practice** | a unit could reasonably want it otherwise; learnable from outcomes | Lessons ([40](40-lessons.md)) |
| **character** | describes who she is or how she sounds | Character · Nora ([10](10-character.md)); an authored minimum for surfaces with no unit |

Examples, to be confirmed in the audit:

- Larsen: never invent a preference; never claim a write that did not succeed;
  say "I couldn't check" when a lookup broke; no action promised that no tool
  schema offers; she is an AI and never pretends otherwise.
- Practice: usually one reply; short is the default; go deep on one or two
  people; weave memory in rather than recite it; how often a GIF lands.
- Character: the banter, the swearing calibration, the humour, a friend's
  place in the group.

## 2. Step 3: classify (before Lessons)

- A line-by-line audit of every file under `nora/doctrine/`, written as a
  table for the operator to mark up.
- Each section, or each rule where a section mixes buckets, carries its
  bucket. Where it is carried (frontmatter, a section marker, or a side table
  the loader reads) is decided in that PR.
- `nora/doctrine/loader.py` exposes the law subset. The doctrine-check
  instrument reads only that.
- **No text change.** The assembled doctrine her turns read stays
  byte-identical, so `doctrine_sha256` and behaviour do not move.

**Built 2026-10-07 (DEC-291).** The bucket is carried in a side table,
`nora/doctrine/classification.yaml` (the scan is `**/*.md`, so the table is never
doctrine and no digest moves); `DoctrineStore.law_rules()` returns the law subset;
[31](31-doctrine-audit.md) is the operator's table, with the judgement calls and the
before/after digests. 58 of 154 parts are law, 68 practice, 28 character. One
widening the audit found: practice text on a surface with no unit (`public/`, the
summons, and `core/` there) has no Lessons receiver either, so the authored minimum
in §4 covers practice there as well as character.

## 3. Design amendment

**Landed 2026-10-07 (DEC-291):** 55-self §2 and §3 and 10-tiers §1 are amended and
marked; `50-character-and-standing.md` §6 now says the doctrine check reads the law
subset.

`design/cognition-loop/55-self.md` §2 lists "given: constraints, voice,
conduct" and `design/cognition-loop/10-tiers.md` §1 defines doctrine as what
wins over anything derived about her. Narrowing "given" to law amends both. It
needs a DEC row (`nora-decision`) in the PR that ships the classification.

## 4. Step 5: cut (last)

- Practice text is deleted from the doctrine once Lessons is live and its
  labelled set passes.
- Character text is deleted once Character · Nora and the self block are live
  and the eval shows no loss.
- Surfaces with no unit (`nora/doctrine/public/`, summons) keep an authored
  minimum of character, since no `self` block reaches them.
- Each cut is a doctrine change: before/after eval (`nora-eval`), live at the
  next pull on the deploy host (doctrine is bind-mounted).

## 5. Risk

Cutting before the receivers exist leaves her flat. Character may only cite
the Record and Rollup, so an authored seed account is not an option.
