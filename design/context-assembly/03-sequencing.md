---
type: design
title: "Sequencing — declare, then close, then merge, then widen"
description: "Four steps with acceptance criteria. The first three change no output and are therefore measurable against the existing suite; only the fourth needs the eval suite. Culture reaching proactive and the note passes is step 4, and it is a row rather than an edit only because steps 1 to 3 happened."
tags: [nora, context, sequencing, acceptance, migration]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 01-the-registry.md, rel: implements, provenance: asserted}
  - {dst: 02-the-gate-stays-closed.md, rel: implements, provenance: asserted}
---

# Sequencing

```
  S1  DECLARE — a table per occasion, no behaviour change
      │   gate first (02), then the other five
      ▼
  S2  CLOSE — the gate's rows pinned by a test that names the reason
      │
      ▼
  S3  MERGE — the four duplicated names, each only after its two
      │   outputs are diffed on real inputs
      ▼
  S4  WIDEN — culture as a row, per occasion, each with its own arm
```

**S1 to S3 change no model-facing output.** That is the property that makes
them checkable by the existing suite and by mutation, with no eval spend. S4 is
the only step that changes what a model reads, and it is the only one that
needs the suite.

## S1 — Declare

Give each of the six occasions a `(name, order, BlockKey)` table, registered
per `(occasion, name)` — `01`'s option (c). Builders are untouched; the table
records what the code already does.

**Acceptance:**
1. Every `ContextBlock(...)` construction **in the six occasion modules named
   in `00`** is reachable from exactly one occasion table. A test walks the
   registry and asserts no block is built outside it within that set.

   **Scoped deliberately, and the first draft was self-contradictory here.** It
   said "every `ContextBlock(...)` construction in `nora/`" while the closing
   section of this same document scoped `engagement/debrief_render.py` and
   `skills/loader.py` OUT. A test written to the first wording cannot pass —
   both of those build real blocks (`standing_prior`, `conversation`,
   `what_happened`, `reasoning`, `turn`, `available_skills`). Invoking the
   "totality is a claim about the tree" discipline and then excluding two files
   from the tree is exactly the error that discipline names, so the criterion
   now says which tree it is a claim about.

   The exclusion is safe for a concrete reason rather than by assertion: none
   of those six names collides with any of the six occasions' names, so there
   is no merge hazard in S3 from leaving them out. `standing_prior` is not
   `standing`.
2. Rendered output for all six occasions is **byte-identical** before and
   after. Pinned by golden-output tests on fixed inputs, not by inspection.
3. Every row carries a `BlockKey`. No occasion emits an untiered block.
4. `make gate` green with no test deleted or weakened.

## S2 — Close the gate

**Acceptance:**
1. A test asserts the gate's rows are exactly `{standing, group_transcript}`
   and its failure message names why (`02`: #144/#147, #216) rather than just
   reporting a set difference.
2. Adding a seventh row anywhere else does not make that test fail — it is
   scoped to the gate, not a global count, or it becomes noise everyone learns
   to update.
3. R-13 escaping is asserted for the gate's rendered output specifically, not
   inherited by assumption from the shared assembler.

## S3 — Merge the duplicated builders

`self_card`, `notes`, `standing`, `group_transcript` — one at a time.

**Acceptance, per name:**
1. The two current builders are run on the SAME real inputs and their outputs
   diffed. The diff is recorded in the PR, whatever it shows.
2. If byte-identical: merge, no eval run needed, and say so.
3. If they differ: **the merge is a prompt change** and does not land on the
   strength of "they had the same name". It either keeps both (renaming one, so
   the tag stops lying) or takes an eval run. Which, is decided by the diff,
   not in advance.
4. **`BlockKey` equivalence is asked, not assumed.** A merged builder carries
   ONE key. Prefill declares `standing` and `group_transcript` as `"room"`; the
   gate's hand-built versions declare nothing and default to `"turn"`. Those
   may differ because the gate's prompt is **not part of the tag path's cached
   prefix at all** — it is a separate call pattern, not the agent conversation
   loop — which would make a single shared key meaningless rather than merely
   wrong. A key is scoped to ONE prompt's cache boundary; it is not a global
   namespace.
   So a merge must state which key the merged builder carries and why that is
   right for BOTH occasions, or keep the builders separate on those grounds.
   Byte-identical output is NOT sufficient evidence to merge: content
   equivalence and cache-tier equivalence are different questions, and only the
   first is visible in a diff. Today nothing routes on `key` (`00` §3) so
   getting this wrong is inert — which is precisely why it would ship
   unnoticed and surface the day routing lands.
5. Falsified: reverting the merge reddens a named test.

**Measured 2026-09-22, one name merged and three kept separate — recorded
here rather than left as "unmeasured", because that was the whole point of
doing the diff first.**

* **`self_card` — MERGED, byte-identical.** `prefill._self_card_block`
  (`TagContext.self_card`) and `proactive._self_card`
  (`Recollection.conclusions`) were both, verbatim,
  `ContextBlock(name="self_card", body=render_bullets(conclusions))` over a
  `tuple[str, ...]` of the same shape. Not a coincidence discovered by
  running fixtures side by side — reading the two functions was the diff, and
  it showed the same computation written twice with nothing comparing them.
  Now `nora.language.self_card_render.render_self_card`, one builder, both
  occasions call it and stamp their own order/key after. No eval run: S3
  acceptance #2 applies directly.

* **`notes` — three of four ALREADY shared, the fourth genuinely differs, NOT
  merged.** `prefill._notes_block`, `note_candidates.inputs` and
  `culture.inputs` all call `nora.language.note_render.render_notes` directly
  — this was never actually a "prefill vs one other" duplicate the way `00`'s
  original claim read; three occasions already share one function. The real
  divergence is `proactive._open_notes_block`, which does NOT call
  `render_notes`: it renders through `render_note_rows` with an `extra`
  callback adding `revisit`/`raised` attributes `render_notes` has no
  parameter for, and it falls back to `<notes>(none)</notes>` when nothing is
  standing rather than dropping the block the way `render_notes` (via
  `assemble`) does. Two real content differences, not one. Per acceptance
  #3, this is a prompt change on the pass that decides whether to speak
  unprompted — not something to fold in on the strength of a shared name, and
  not merged here.

* **`standing` — prefill and culture ALREADY share `render_standing`; the
  gate's is NOT merged, and the difference is deliberate.**
  `prefill._standing_block` and `culture.inputs` both call
  `nora.language.standing_render.render_standing`, so — same correction as
  `notes` — this was already one builder in two of the three places `00`
  implied were separate. `relevance_gate.render_context` hand-builds its own:
  `render_standing` returns `None` (block dropped) when there is no position,
  while the gate's docstring states outright that `<standing>` renders
  `(none)` rather than nothing because "the instruction leans on this block
  being the only thing that can establish what she owes the room, so its
  absence has to be READABLE." That is acceptance #4's exact scenario — a
  content difference that exists for a stated reason, on the one occasion
  `02` already holds to a higher bar than the rest. Kept separate rather than
  renamed: the gate's `<standing>` already documents, in its own module, why
  it is not the shared renderer, which is what "the tag stops lying" is
  asking for — a rename would be a second way of saying the same fact, spent
  on the gate's hottest path for no new information.

* **`group_transcript` — NOT merged; three deliberate differences, not
  drift.** `prefill._transcript_block` and the gate's `render_context` both
  call `nora.domain.blocks.render_messages`, but configured differently on
  purpose: the gate substitutes a neutral `"assistant"` for Nora's own
  messages where prefill substitutes her display name (the gate's
  determination is meant to read as unbranded admission control, not as
  Nora), the gate passes no `replying_to` resolver at all (the gate does not
  need reply-chain structure for a go/no-go verdict), and the gate
  pre-filters rows that collapse to nothing while prefill does not. Each is
  argued for in its own module's comments; none reads as an accident. Per
  acceptance #3, kept separate rather than merged or renamed, for the same
  reason as `standing` above.

**What this means for S4.** Only one occasion (proactive) still hand-builds a
`notes`-shaped block outside the shared renderer, and it is not a candidate
for a quick fold-in — extending `render_notes` with an optional `extra`
parameter to absorb it, or accepting the two renderers as permanently
separate, is a real design question with its own measurement, not a
mechanical merge. It is out of this bundle's S3, which only merges what a
diff shows to be free.

## S4 — Widen

> **LANDED AS ONE CHANGE 2026-09-25 — DEC-207.** The operator widened culture
> to every runtime at once (the tag turn and every Dreamer pass, not only the
> three below), so it shipped as one PR with a row per occasion rather than
> one PR each. Criterion 2 is met (`render_culture` has eight callers); the
> per-occasion arm (criterion 3) is run against the eval suite's corpora after
> the fact and recorded in the PR, with the reopen condition in
> `design/culture-tier/03-decisions.md` «DEC-207». The gate stays out.

Culture into proactive, note-candidates, note-curator. One row each, one PR
each, one arm each.

**Acceptance, per occasion:**
1. The row is added to that occasion's table and nowhere else.
2. `render_culture` gains a caller — it currently has **zero** in the whole
   repository, which is the concrete form of "the culture tier reaches nothing".
3. An eval arm measures whether the block changes that occasion's decision, and
   the result is recorded whether or not it is favourable. `design/culture-tier
   /02-measurement.md` is the shape; PR #324's participation arm is the worked
   example.
4. A reversal condition is stated before the run, not after.

**Order within S4, cheapest risk first:**
* **note candidates / note curator** — they write notes rather than deciding
  whether to speak. A wrong note is visible and correctable.
* **proactive** — it speaks unprompted, so a wrong read reaches the room.
  Budget more samples: `proactive_decide` recall is already known to be noise at
  n=5.
* **the gate** — separate decision, `02`, and not part of this bundle.

## The blocker that sat in front of all of it — RESOLVED 2026-09-22

The first draft said: *"PR #324 is `CONFLICTING` and 291 commits behind master…
the true first action is: rebase and land #324. Then S1."*

**That has happened, and not as a rebase.** #324 split into a superseded half
and a live one:

* its **measurement arm** (~810 lines) targeted files the eval rebuild
  (#334/#368) had deleted — `evals/cases/participation/culture.yaml`,
  `harness.py`, `run.py`, `nora/app/eval_support.py`. The cases were re-homed
  into `evals/corpora/participation.yaml`, where the harness reconstructs a
  room's real culture at a real instant instead of reading an authored one.
  Superseded, not pending;
* its **production wiring** (~215 lines) landed as **PR #382**, three-way
  applied and adapted to #314 (no fourth `_BLOCKS` column, no `*_HEADER`
  constants) and to the `workspace`→`unit` rename.

So S1 starts against a `prefill._BLOCKS` that already carries
`("culture", 55, "room")`, and the remaining widening is proactive, note
candidates and note curator — `03`'s S4, unchanged.

## What this bundle leaves open, deliberately

* Whether S3 ends in one builder per name or in renamed siblings. Decided by
  the diffs, per name.
* Whether the gate's RENDERER is ever unified. `02` closes its SET; the renderer
  is a separate question with no forcing reason.
* Whether a seventh occasion (debrief, skills) joins the registry. Both build
  blocks today and neither was in scope for the operator's question; they should
  not be swept in silently.
