---
type: design
title: "The self block: Character · Nora rendered below the doctrine, self_card retired"
description: "One block, one PR: her whole Character · Nora for this unit, capped in lines, placed directly after the doctrine on tag, fire and cognition turns. self_card leaves the tag and fire turns. Measured before and after with the eval, because self_card has been in every prompt."
tags: [nora, self, context-assembly, self-card]
timestamp: '2026-10-07'
edges:
  - {dst: 30-doctrine-sort, rel: sequences, provenance: asserted}
---

# 20 · The self block

## 1. What it is

- **Content:** the current Character · Nora account for the turn's unit
  ([10](10-character.md) §4), capped in lines.
- **Position:** directly after the doctrine and before every other block, so
  the doctrine is read first and wins (`design/cognition-loop/55-self.md` §3).
- **Occasions:** tag, fire and Cognition. Not the relevance gate, not the
  public surface or summons, which have no unit.
- **Assembly:** a row in `_BLOCKS` and a renderer, through context assembly
  like every other block.
- **Lineage:** each section renders only where its lineage passes for the
  room (`design/cognition-loop/10-tiers.md` §3).

## 2. What leaves

`self_card` leaves the tag and fire turns in the same PR
(`design/cognition-loop/55-self.md` §3 schedules it). It is not a second
account of who she is: `self_card_render.py` and `_self_card_block` in
`nora/agent/prefill.py` frame it as episodic continuity, what she said and
did before, not self-description. So what is lost is that continuity (what
she said, offered or agreed to earlier), and the eval must measure that loss,
not only how she sounds. If it is lost, the continuity moves to a pulled
read or to Standing, not back into a pushed Honcho block.

## 3. Check

- Architecture: the block is declared and assembled (ADR 0021's enforcer).
- A unit with no Character · Nora yet renders no block, not an empty one.
- Behaviour: a before/after eval run (`nora-eval`), with cases that turn on
  something she said or agreed to earlier, since that is what `self_card`
  carried. A green gate is not enough.

## 4. Effort

One PR. The plumbing is small; the eval is the cost.
