---
type: design
title: "The self block: Character · Nora rendered below the doctrine, self_card retired"
description: "One block, one PR (built, DEC-292): her whole Character · Nora for this unit, capped in characters, placed directly after the doctrine on tag, fire and cognition turns. self_card leaves the tag and fire turns. The before/after eval could not be run on the suite (section 5); self_card had been in nearly every prompt."
tags: [nora, self, context-assembly, self-card]
timestamp: '2026-10-07'
edges:
  - {dst: 30-doctrine-sort, rel: sequences, provenance: asserted}
---

# 20 · The self block

## 1. What it is

- **Content:** the current Character · Nora account for the turn's unit
  ([10](10-character.md) §4), capped in characters.
- **Position:** directly after the doctrine and before every other block, so
  the doctrine is read first and wins (`design/cognition-loop/55-self.md` §3).
- **Occasions:** tag, fire and Cognition. Not the relevance gate, not the
  public surface or summons, which have no unit.
- **Assembly:** a row in `_BLOCKS` and a renderer, through context assembly
  like every other block.
- **Lineage:** none at render, like `<unit_culture>`: the account is keyed by
  the unit, the read boundary, with one render rule for R-31: the account is
  written from her sends in every room, location rooms included, so a room
  shows it only if it has `location = true` or no room of its unit has
  (`Policy.shows_self_in`); a Cognition session, whose sends can reach any
  room, only if every room of the unit agrees (`Policy.shows_self_to_unit`).
  Cost: a mixed unit's location-off rooms show no `<self>`.

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

## 5. As built, 2026-10-07 (DEC-292)

**Built.** `self` is the first row of the tag and fire prefill `_BLOCKS` (order
1, key `unit`) and of the Cognition prompt's (order 5, before the clock);
`render_self` in `nora/language/culture_render.py` caps it at 4,000 characters
(`SELF_MAX_CHARS`; accounts are prose in 1 to 3 lines, so a line cap bounds
nothing, and the longest live head is 1,797) and says how many it dropped. Tag and fire read `nora_character_key(unit)`
(`TranscriptInjection._self_account`, `FirePath._read_self_account`); the
Cognition grounder reads it into `Grounding.self_account`. No account, no
block. No lineage filter, like `<unit_culture>`: the unit is the read boundary.
`self_card` is deleted with its renderer, both Honcho reads,
`[honcho] self_card_conclusions` and the `fire_self_card` stage. The eval
sandbox copies her account as of the case's instant (`Snapshot.nora_account`,
`Knowledge.nora_account`).

**Not measured, and why.** The before/after on continuity could not be run on
`python -m evals`; nothing was claimed from it.

- The suite cannot give the before arm a `self_card`. Every draw runs against
  a per-draw, empty Honcho workspace (`nora/subject/memory_workspace.py`), a
  corpus case may state only a real room and instant (`CASE_KEYS`), and Honcho
  has no chain to snapshot (`nora/subject/as_of.py`). So on the suite the
  before arm renders no `self_card` either, and the two arms would differ only
  by `<self>`.
- Snapshots are as-of. The three Nora accounts were first written at 07:53 UTC
  on 2026-10-07; a case anchored earlier renders no `<self>`, so the arms are
  identical there. After that moment the record held 40 messages in three
  rooms by the time of the read; the 37 Signal ones were read and none turns
  on something she said earlier (a browser-task run, and members talking
  among themselves); the 3 Discord ones were not read.
- What `self_card` carried is large and measured: it was in 389 of the last 400
  tag turns (2026-09-17 to 2026-10-07, Langfuse `engagement` traces), 4 to 8
  lines each. The three accounts written so far describe how people take her
  (blunt, checks before answering, owns mistakes), with a few concrete
  episodes; none states a pending promise.

**To measure it** the suite needs a counterfactual state for each arm's own
memory (`self_card` seeded, or `<self>` attached at an earlier instant), argued
as `COUNTERFACTUAL_TIERS` entries are, plus continuity cases whose commitment
is older than the transcript window. Until then the loss is a hypothesis; if it
shows, the continuity moves to Standing or a pulled read, as §2 says.
