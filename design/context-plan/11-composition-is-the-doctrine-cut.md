---
type: design
title: "Composition is the doctrine cut — law fixed, the rest grown or loaded"
description: "Reframe after nodes 09-10: the doctrine is not a set of units to select per turn but a mix of law, practice and character written as fixed prose, standing in for tiers that now exist. Whole-prompt composition and self-growth step 5 are one piece of work. Target prompt shape, sequence, risks and the operator's inputs."
tags: [nora, context-plan, prompt-composition, doctrine, self-growth, lessons, character, skills]
timestamp: '2026-10-12'
edges:
  - {dst: 09-what-a-turn-carries.md, rel: constrained_by, provenance: asserted}
  - {dst: 10-doctrine-audit.md, rel: constrained_by, provenance: asserted}
  - {dst: ../self-growth/30-doctrine-sort.md, rel: extends, provenance: asserted}
---

# Composition is the doctrine cut

## 1. The question, as the operator put it (2026-10-12)

Not "which wording is stale", and not "which doctrine section does this turn
need". Does the doctrine system make sense for the whole context at all, or is it
abstract ideas — given that the cognition loop now grows Nora?

## 2. What the doctrine is today

[Self-growth 31](../self-growth/31-doctrine-audit.md) sorted every doctrine part
(DEC-291, `nora/doctrine/classification.yaml`) by the test in
[30 §1](../self-growth/30-doctrine-sort.md):

| bucket | parts | tag-audience tokens (node 10) | its real home |
|---|---:|---:|---|
| law | 58 | ~2,890 | the doctrine, fixed |
| practice | 68 | ~3,490 | Lessons, learned from closed response episodes |
| character | 28 | ~2,230 | Character · Nora and `<self>`, grown by the cognition loop |

Two thirds of the ~9k doctrine tokens every turn carries (node [09](09-what-a-turn-carries.md))
is practice and character written as fixed prose. It was the only place to put
them when the doctrine was written. It no longer is, so the doctrine is doing
the job of tiers that exist.

Those tiers are live, not planned: Character for room, unit, person and Nora
(DEC-290), the `<self>` block (DEC-292), Lessons written since 2026-10-08 and
rendered as `<lessons>` since 2026-10-09 (DEC-297, DEC-301; 4 lessons on
2026-10-12), Standing, Culture, notes and memos.

## 3. A fourth kind self-growth did not sort: operating knowledge

How to work the computer, generate an image or a video, publish a document, or
reply when the reply will be spoken. This is neither law (breaking it lies to
nobody) nor practice learned from a room, nor who she is. It is knowledge of her
own instruments, and its home is the tool description and the runtime skill
(ADR 0015, ADR 0016), loaded when the instrument is.

Node 10 found most of the "doctrine never mentions the computer" gap is already
covered there, that some doctrine text duplicates it (`pictures.md` with
`media_fetch`, `conduct.md` documents with `document_publish`), and one real gap:
nothing tells the model a reply will be spoken.

One limit, measured before (2026-09-24, image generation): a rule she must
always follow cannot live only in an optional skill, because she skipped the
skill 3 of 6 times. An instrument's must-hold fact goes in law; its how-to goes
in the skill.

## 4. The target shape of a turn's prompt

Ordered for the prefix cache: what changes least goes first.

| layer | contents | changes when | selected by |
|---|---|---|---|
| 1. Law core | law rules; an authored minimum of character for surfaces with no unit (30 §4) | a doctrine edit | nothing; always, byte-stable |
| 2. Tool core | hot tool schemas and a catalogue | a tool or config change | admission (room scope) |
| 3. The unit's own | `<self>`, `<character>`, `<lessons>`, `<culture>`, `<unit_culture>` | the cognition loop writes | the unit (already, R-31) |
| 4. The turn's situation | Standing, notes, memos, peer cards and history, presence, computer, documents, transcript depth | every turn | rules first; a model only where measured to pay |
| 5. Instruments on demand | skills and deferred tool schemas | the model asks | the model (`load_tools`, skills) |
| 6. The message | the tag, quoted message, context header | every turn | — |

What disappears from the always-on prompt is practice and character prose, and
capability how-to that duplicates a tool or skill. On node 10's estimates the
always-on doctrine falls from ~9.1k to ~2.9k (law only) or ~4.2k (law plus a
minimal voice and character), before any per-turn selection.

"Context router" is the wrong name for this. Most of it is not routing: layers
1-3 are fixed or unit-scoped, layer 5 is already the model's own choice. The
per-turn decision is layer 4 only. This node calls the whole **composition**.

## 5. What node 08's finding still says

A model selector over layer 4 alone did not pay (node 08: ~2.6k tokens and 1.7 s
per turn for a 16% first-call cut that the cache mostly absorbed). That stands.
Layer 4 is selected by rules (code-decidable conditions: a picture present, a
spoken turn, computer state, a document in scope, transcript depth) until a
measured quality problem says otherwise.

## 6. Sequence

1. **Operator mark-up of [31](../self-growth/31-doctrine-audit.md)** — the
   law/practice/character split, especially rows marked AMBIGUOUS. Input only the
   operator holds; nothing below moves the text before it.
2. **Move law out of practice-looking sections.** Node 10 found law lines inside
   sections that read as conditional (no invented quotes, no fake concession,
   find it before you say it). They join the law core before anything is cut.
3. **Operating knowledge to its instruments.** Capability how-to that duplicates
   a tool or skill leaves the doctrine; a spoken-turn unit is added (a code
   condition, `msg.voice`); each instrument's must-hold fact stays law.
4. **The cut (self-growth step 5), per bucket, each a before/after eval:**
   practice when Lessons carries it, character when Character · Nora and `<self>`
   carry it without her going flat.
5. **Layer 4 rules** in the composition contract beside `ContextBlock`
   (node 06 §1), after the cut, so they select from a prompt that is mostly
   situation.
6. **The tool surface** (25% of every call, node 09) gets its own audit; it has
   none yet.

## 7. Risks

- **The receivers are thin.** 4 lessons exist; `<lessons>` and `<self>` are
  hidden by R-31 in a location-off room of a unit that has a location room
  (40 §8.1), so a cut practice rule can have no receiver in that room. The cut
  per bucket must check where the receiver actually renders.
- **Grown character may be flatter than authored.** The self-block before/after
  could not be run ([20 §5](../self-growth/20-self-block.md)); character text is
  cut only after an eval that can see the difference.
- **Practice prose that is really law.** Compression once deleted 8 rules called
  restatement; step 2 is a falsifying read, not a skim.
- **Lessons are checked against law** (L3). A practice rule cut from the
  doctrine is no longer something a lesson can contradict; that is the intent,
  and it is also how a bad lesson goes unchecked.
- **One doctrine per audience is assumed downstream**: `LeakGuard`'s haystack,
  `doctrine.sha256`, `classification.yaml` hashes (node 10 §Risks). A per-unit
  layer 3 is already outside the doctrine, so the cut keeps this assumption; only
  layer-4 doctrine units would break it.
- **Cost is not tokens.** Layers 1-2 are cached; the saving from the cut is
  partly quality and latency, not price. Price the cached and uncached split.

## 8. Open for the operator

1. The mark-up of 31 (step 1).
2. Whether the tag audience keeps an authored minimum of voice and character in
   layer 1, or relies on `<self>` from the first cut.
3. Whether `<lessons>` should keep following `<self>`'s R-31 gate (open since
   DEC-301).
4. The name: composition, or another.
