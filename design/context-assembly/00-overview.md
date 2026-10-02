---
type: design
title: "Context Assembly — one brick, six walls, one of them declared"
description: "Every occasion that builds a model-facing prompt uses the same ContextBlock primitive and then assembles it by hand. Exactly one of the six declares what it carries. The same block name is built by two different functions from two different inputs, and nothing anywhere can answer «which blocks does proactive get» without tracing control flow."
tags: [nora, context, prompt, blocks, assembly, drift]
timestamp: '2026-09-22'
edges:
  - {dst: 01-the-registry.md, rel: child, provenance: asserted}
  - {dst: 02-the-gate-stays-closed.md, rel: child, provenance: asserted}
  - {dst: 03-sequencing.md, rel: child, provenance: asserted}
---

# Context Assembly

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## The measurement this starts from

Six occasions build a model-facing prompt. Measured 2026-09-22 by reading every
`ContextBlock(...)` construction in `nora/`:

| occasion | blocks it assembles | declared? |
|---|---|---|
| **tag / participation** (`agent/prefill.py`) | `presence` `self_card` `peer_card` `peer_history` `people_in_window` `notes` `loop_decisions` **`culture`** `standing` `group_transcript` `replying_to` `tag_message` | **YES** — `_BLOCKS`, with order and `BlockKey` |
| **proactive** (`language/proactive.py`) | `self_card` `notes` `decisions` `people` `rooms` `presence` (was `home_room` until 2026-09-23, design/self-awareness/02) `now` `loop_decisions` | **YES** (S1, 2026-09-22) |
| **note curator** (`language/note_curator.py`) | `note` `evidence` `own_sends` `today` | **YES** (S1, 2026-09-22) |
| **note candidates** (`language/note_candidates.py`) | `batch` `room` `notes` | **YES** (S1, 2026-09-22) |
| **culture** (`language/culture.py`) | `previous_account` `room` `room_messages` `standing` `notes` | **YES** (S1, 2026-09-22) |
| **relevance gate** (`ingest/relevance_gate.py`) | `standing` `group_transcript` | **YES** (S1+S2, 2026-09-22) — closed on purpose, see `02` |

`nora.domain.blocks.ContextBlock` is imported by all of them and by seven more
modules. **The brick is already shared.** What is not shared is the wall.

> **CORRECTED 2026-09-22, during S1.** The bold `loop_decisions`/`notes`/
> `standing` cells above were missing from the table when this document was
> written and reviewed. The original survey read every literal
> `ContextBlock(name=...)` construction in each occasion's own file — that is
> what "reading every `ContextBlock(...)` construction" meant in practice —
> and missed three blocks built by a call to a builder SHARED with another
> occasion (`nora.domain.decision_log.render_decisions`,
> `nora.language.note_render.render_notes`,
> `nora.language.standing_render.render_standing`), which is not a literal
> `ContextBlock(name=...)` in the occasion's own module. Found while declaring
> each occasion's `_BLOCKS` table, which forced enumerating what actually
> renders rather than what the file's own literals show. The corrected counts
> are what `_BLOCKS` in each module now asserts, each pinned by a test.

## What that costs, concretely

**1. The same name is not the same block.** `self_card`, `notes`, `standing`
and `group_transcript` are each built in TWO places — a `_x_block(ctx)`
function in `prefill.py` and a separate inline construction in `proactive.py`
or `relevance_gate.py`. Different functions, different input types
(`TagContext` vs a bespoke context vs a `(str, int)` tuple), one tag name. So
`<notes>` can render differently depending on which occasion emitted it, and
nothing compares them.

**2. «What does proactive carry?» is not answerable by reading.** It is seven
`ContextBlock(...)` calls spread through a 1400-line module, some conditional.
For the tag path the same question is `_BLOCKS` — ten rows, at the top of the
file. The difference is not style; it is whether a reviewer can check a claim.

**3. `BlockKey` is declared once and applied once — as DISCIPLINE, not yet as
routing.** `BlockKey = Literal["global", "unit", "room", "person", "turn"]`
exists in `domain/blocks.py:84` and only `_BLOCKS` annotates a table with it.
The other five occasions emit blocks with no declared tier at all.

**Stated precisely, because the first draft overstated it.** No code anywhere
reads `key` to route a block into a cached head rather than the per-turn
message — that routing does not exist yet, and `BlockKey`'s own docstring says
so (*"Every `ContextBlock` is in the volatile message today… what this catches
is a future `global` key"*). So prefill gets no cache WIN from its keys that
the other five are missing. What it has is a declared intention the others
cannot express, which matters on the day routing is wired and not before.

**4. Adding a tier means editing six places, or forgetting five.** The culture
tier is asked to reach proactive, the note passes and the gate.

> **CORRECTED 2026-09-22, and the correction is the better evidence.** The
> first draft of this section said `render_culture` "currently has ZERO callers
> in the whole repository". That was true when it was written and was false a
> few hours later: PR #382 landed #324's production half onto this same
> worktree while this bundle was under review, which is why the table above now
> carries `culture` at order 55. The reviewer caught the bundle falsifying its
> own claim mid-review.
>
> **What that single edit shows is the argument, not against it.** Adding
> culture to the tag path was ONE ROW in `_BLOCKS` — `("culture", 55, "room")`
> — because that occasion declares its set as data. Adding it to the remaining
> three (proactive, note candidates, note curator) is three inline edits into
> three hand-built assemblies, plus one deliberate refusal at the gate. One row
> against three edits, for the same tier, is the cost this bundle is about.

## The drift this has already produced

Not hypothetical, and not about this subsystem alone — the same shape, twice in
one day, 2026-09-22:

* `guards.py` said *"Under the cap of 3 (DEC-42)"* in prose while
  `NATIVE_TOOLS` said `6`. The number lived in two places and one of them
  rotted. Nobody noticed until the code was read for an unrelated reason.
* `design/instagram-adoption/05` listed `interop_messaging_user_fbid` among
  "14 measured keys" on `GetProfile`. The live wire does not send it. The claim
  was true of a different call.

A convention that is written down once and applied by hand five more times is
that shape. The registry in `01` is the attempt to make it data instead.

## What this bundle does NOT claim

**It is not a claim that every occasion should carry the same blocks.** They
should not. The gate carries two by design (`02`), the note passes look at
evidence rather than at people, and proactive is the only one that reads across
rooms. Unification here means **one way to say what an occasion carries**, not
one set carried everywhere.

**It is not a rewrite of any renderer.** Every `_x_block` function stays.
Declaring the sets changes no output, which is exactly what makes the first
step measurable against the existing suite — see `03`.
