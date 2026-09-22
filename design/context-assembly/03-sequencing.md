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

**Unmeasured today:** which of the four are identical. That measurement is the
first task of S3 and costs nothing but a fixture.

## S4 — Widen

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
