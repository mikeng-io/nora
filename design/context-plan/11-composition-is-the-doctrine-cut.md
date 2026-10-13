---
type: design
title: "Composition is the doctrine cut — law fixed, the rest grown or loaded"
description: "Reframe after nodes 09-10: the doctrine is not a set of units to select per turn but a mix of law, practice and character written as fixed prose, standing in for tiers that now exist. Whole-prompt composition and self-growth step 5 are one piece of work. Target prompt shape against the real cache boundary, sequence, risks and the operator's inputs."
tags: [nora, context-plan, prompt-composition, doctrine, self-growth, lessons, character, skills]
timestamp: '2026-10-12'
edges:
  - {dst: 09-what-a-turn-carries.md, rel: constrained_by, provenance: asserted}
  - {dst: 10-doctrine-audit.md, rel: constrained_by, provenance: asserted}
  - {dst: ../self-growth/30-doctrine-sort.md, rel: extends, provenance: asserted}
---

# Composition is the doctrine cut

> **Amended by node [12](12-the-sort-ruled.md) (2026-10-12):** layer A carries an
> authored identity seed on every surface and the seed is never cut; silence.md
> dissolves into the turn's occasion (layer E); location is a layer-E fact on
> both loops for a location-trusted room, and its law is rewritten.

> **Amended 2026-10-12 by [node 14](14-a-decision-model-composes-the-prompt.md):**
> the composer is a trained decision model over the whole prompt, law units
> included, with targets of <5k for a greeting and ~10k for a typical turn;
> code rules and the doctrine cut are no longer the main lever.

## 1. The question, as the operator put it (2026-10-12)

Not "which wording is stale", and not "which doctrine section does this turn
need". Does the doctrine system make sense for the whole context at all, or is it
abstract ideas — given that the cognition loop now grows Nora?

## 2. What the doctrine is today

[Self-growth 31](../self-growth/31-doctrine-audit.md) sorted every doctrine part
(DEC-291, `nora/doctrine/classification.yaml`) by the test in
[30 §1](../self-growth/30-doctrine-sort.md). For the **tag audience** (the seven
files a tag turn reads; counted with `classify()` on 2026-10-12):

| bucket | tag parts | tag tokens, estimated (node 10) | its real home |
|---|---:|---:|---|
| law | 38 | ~2,890 | the doctrine, fixed |
| practice | 35 | ~3,490 | Lessons, learned from closed response episodes |
| character | 25 | ~2,230 | Character · Nora and `<self>`, grown by the cognition loop |

Token figures are node 10's character-count estimates, not tokenizer counts; the
three exclude ~500 tokens of section wrappers. Across every audience the sort is
58 / 68 / 28 parts; 33 of the 68 practice parts are in `instruments/` and
`public/`, which no `<lessons>` reaches (it renders on tag and fire turns only).

About 63% of the ~9.1k doctrine tokens a tag turn carries (node
[09](09-what-a-turn-carries.md)) is practice and character written as fixed
prose. It was the only place to put them when the doctrine was written. It no
longer is: the doctrine is doing the job of tiers that exist.

Those tiers are live: Character for room, unit, person and Nora (DEC-290), the
`<self>` block (DEC-292), Lessons written since 2026-10-08 and rendered as
`<lessons>` since 2026-10-09 (DEC-297, DEC-301). Read back 2026-10-12: 4 lessons,
`<lessons>` in 13 of the 25 measured turns since the first
([lessons-readback.txt](evidence/composition-2026-10-12/lessons-readback.txt)).

## 3. A fourth kind self-growth did not sort: operating knowledge

How to work the computer, generate an image or a video, publish a document, or
reply when the reply will be spoken. This is neither law (breaking it lies to
nobody), nor practice learned from a room, nor who she is. It is knowledge of
her own instruments; its home is the tool description and the runtime skill
(ADR 0015, accepted; ADR 0016, still `proposed`), loaded with the instrument.

Node 10 found most of the "doctrine never mentions the computer" gap is already
covered there, that some doctrine text duplicates it (`pictures.md` with
`media_fetch`, `conduct.md` documents with `document_publish`), and one real gap:
nothing tells the model a reply will be spoken.

One limit, measured before (`design/sandbox-runtime/00-design.md`, image
generation): a rule she must always follow cannot live only in an optional
skill; she skipped the skill 3 of 6 times. An instrument's must-hold fact stays
law; its how-to goes in the skill.

## 4. The target shape, against the real cache boundary

Two facts fix the shape. The cached head is the **system prompt (doctrine) and
the resident tool schemas**; every `_BLOCKS` row is in the per-turn message by
rule (`nora/agent/prefill.py`, pinned by
`test_no_block_in_this_table_belongs_in_the_cached_head`). And loading a
deferred tool schema changes the tools array, so the prefix misses: deferral
moved ~36% of input from cached to uncached and cost more
([tool-catalogue/05](../tool-catalogue/05-measurement.md)). Where the schemas sit
relative to the system prompt on the deployed route is still node 10's open
question 7.

| part | contents | changes when | selected by |
|---|---|---|---|
| **Cached head** | | | |
| A. Law core | law rules; an authored minimum for surfaces with no unit (practice and character, 30 §2) | a doctrine edit | nothing; byte-stable |
| B. Resident tools | hot tool schemas | a tool or config change | admission (room scope) |
| **Per-turn message, steady front** | | | |
| C. The unit's own | `<self>`, `<lessons>` (unit-keyed, R-31-gated) | the cognition loop writes | the unit, already |
| D. The room's own | `<tool_catalogue>`, `<room_members>`; and `<character>`, `<culture>`, `<unit_culture>` (room-keyed, rows 54-56 today) | config, or the loop writes | the room |
| **Per-turn message, the turn** | | | |
| E. Situation | presence, Standing, notes, memos, peer cards and history, computer, documents, transcript depth, and a spoken-turn marker | every turn | rules first; a model only where measured to pay |
| F. The message | tag, quoted message, context header | every turn | — |
| **On demand** | | | |
| G. Instruments | skill bodies (arrive as tool results, after the prefix: cache-safe); deferred schemas (change the tools array: break the prefix) | the model asks | the model |

Moving `<character>` and `<culture>` into D, ahead of presence, reverses a
deliberate choice: presence is row 5 so the turn opens on when and where
(design/self-awareness/02, cited at prefill.py's presence row). Whether D's
room-steady blocks should precede presence is a decision, not a consequence; the
cache gain is small, since the whole per-turn message is uncached on the first
call of a turn.

What leaves the always-on prompt is practice and character prose, and capability
how-to that duplicates a tool or skill. On node 10's estimates the always-on
doctrine falls from ~9.1k to ~2.9k (law only) or ~4.2k (law plus a minimal voice
and character), before any per-turn selection.

Spoken turns go in E as a situation marker, not as a conditional doctrine unit:
the doctrine stays one text per audience (LeakGuard's haystack,
`doctrine.sha256`, the classification hashes all assume it), and a voice turn
differs in what it is, not in what the law is.

"Context router" is the wrong name. A and B are fixed, C and D are keyed by unit
and room, G is already the model's own choice. The per-turn decision is E only.
This node calls the whole **composition**.

## 5. What node 08's finding still says

A model selector over the optional situation blocks did not pay (node 08: ~2.6k
tokens and 1.7 s per turn for a 16% first-call cut the cache mostly absorbed).
That stands. E is selected by rules (code-decidable conditions: a picture
present, a spoken turn, computer state, a document in scope, transcript depth)
until a measured quality problem says otherwise.

## 6. Sequence

1. **Operator mark-up of [31](../self-growth/31-doctrine-audit.md)** — the
   law/practice/character split, especially rows marked AMBIGUOUS. Input only the
   operator holds; nothing below moves text before it.
2. **Move law out of practice-looking sections.** *(Done 2026-10-12: `rooms/constraints.md`, `public/constraints.md`; DEC-310.)* Node 10 found law lines inside
   sections that read as conditional (no invented quotes, no fake concession,
   find it before you say it). They join the law core before anything is cut.
3. *(Ruled down 2026-10-12: production runs `defer_tools = true`, so only
   `HOT_TOOLS` schemas are resident; for every other instrument the doctrine line
   is the one copy she reads before the load decision, and a must-hold rule kept
   only in a skill was skipped 3 of 6 live. No how-to leaves the doctrine while
   deferral is on. What step 3 shipped is the spoken-turn marker, `<spoken_turn>`,
   a per-turn block; the typing-assuming doctrine lines it should outweigh are
   step 4's.)* **Operating knowledge to its instruments.** Capability how-to that duplicates
   a tool or skill leaves the doctrine; each instrument's must-hold fact stays
   law; the spoken-turn marker is added to E (`msg.voice`).
4. **The cut (self-growth step 5), per bucket, each a before/after eval,** under
   30 §4's gates: practice once Lessons is live **and its labelled set passes**
   (the `unanswered`-sends check, 40 row L2, not yet run); character once
   Character · Nora and `<self>` carry it without her going flat.
5. **E's rules** in a composition contract beside `ContextBlock`
   (`nora/domain/blocks.py`; node 06 "Contract and ownership"), after the cut, so
   they select from a prompt that is mostly situation.
6. **The tool surface** (25% of the first call, node 09) gets its own audit,
   starting from node 10's question 7.

## 7. Risks

- **The receivers are thin.** 4 lessons exist. `<lessons>` and `<self>` are
  hidden by R-31 in a location-off room of a unit that has a location room
  (40 §8.1), so a cut practice rule has no receiver there. Each cut checks where
  its receiver actually renders.
- **Grown character may be flatter than authored.** The self-block before/after
  could not be run ([20 §5](../self-growth/20-self-block.md)); character text is
  cut only after an eval that can see the difference.
- **Practice prose that is really law.** Compression once deleted 8 rules called
  restatement; step 2 is a falsifying read, not a skim.
- **Precedence goes with the cut.** The doctrine above `<self>` and `<lessons>`
  "wins" (prefill.py, rows 1-2). The L3 doctrine check reads law only (DEC-298),
  so it loses nothing; but a practice rule cut from the doctrine no longer
  outranks a lesson that disagrees with it. That is the intent, and it is also
  how a bad lesson goes unopposed in the prompt.
- **Cost is not tokens.** A and B are cached; the cut saves partly quality and
  latency, not price. Price the cached and uncached split.

## 8. Open for the operator

1. The mark-up of 31 (step 1).
2. Whether the tag audience keeps an authored minimum of voice and character in
   A, or relies on `<self>` from the first cut.
3. Whether D's room-steady blocks should precede presence (§4).
4. Whether `<lessons>` should keep following `<self>`'s R-31 gate (open since
   DEC-301). Measured 2026-10-13 ([40 §8.2](../self-growth/40-lessons.md)): the
   blocks reach 59% of her sends; the gap is the `ridgeline` unit's main room
   (41% of sends), hidden by two location-on rooms that barely speak. Kept on
   R-31; the reach question is which `ridgeline` rooms hold location trust.
5. The name: composition, or another.
