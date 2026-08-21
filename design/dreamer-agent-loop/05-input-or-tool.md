---
type: decision
title: "Input or Tool — The Rule That Replaces the Blanket Refusal"
description: "Knowable before the call is an input; discovered during reasoning must be a tool. Replaces fact-tier §4.2's general 'no read-of-ourselves' rationale while preserving its correct narrow case."
tags: [nora, dreamer, tools, decision, r-27, selection]
timestamp: '2026-08-22'
edges:
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 04-designed-vs-shipped, rel: motivated_by, provenance: asserted}
---

# Input or Tool — The Rule That Replaces the Blanket Refusal

## 1. The rule

> **If the selection is knowable *before* the call, it is an input.**
> **If the selection depends on what the model concludes *mid-reasoning*, it
> must be a tool.**

That is the whole of it. It is a test on the *selection*, not on the subject
matter, and not on whether the data is "about the world" or "about ourselves".

## 2. Why the existing rule is not wrong, only misapplied

Fact-tier §4.2 refused a fact-read tool because *"selection stays where it can
be capped and counted."* For the case it was written about, that is correct and
this bundle does not disturb it: the adjudicator's pending candidates are
chosen by a deterministic filter that is code precisely so the cost decision is
auditable. Handing that selection to the model would move a cost decision into
a prompt.

The failure is generalisation. §4.2's stated rationale — *"read-only about the
world rather than about ourselves"* — is a claim about **subject matter**, and
subject matter is the wrong axis. It sweeps in every tool that reads the room,
which the same document's §4 grants, ten lines above.

Under the rule above, the two cases separate cleanly and for a reason that
holds:

- **Pending candidates for this workspace** — knowable before the call, capped,
  countable, and the cap *is* the cost decision. **Input.** Unchanged.
- **"What do we already call this thing?"** — not answerable until the model
  has read the batch and formed a hypothesis about what "this thing" is. No
  amount of pre-computation can select it, because the selector does not exist
  until the reasoning has happened. **Tool.**

## 3. Why pre-selection cannot substitute

The tempting alternative is to widen the pre-rendered input: fetch more facts,
render more context, pass the transcript window in. It fails on three counts.

**It cannot be targeted.** Python does not know which subject the model will
land on, so it must either guess or send everything. Guessing is what the
current renderer does — it matches on literal `subject_id` — and it is why a
contradiction between 「big walk」 and `Big Walk` is invisible.

**It scales the wrong thing.** Widening the input grows every prompt on every
tick, including the overwhelming majority that need none of it. A tool call
costs a round trip only when the model decides it needs one.

**It cannot ask twice.** The distinguishing act of research is asking
*differently* after the first answer disappoints — the keystone's own
motivating case is precisely a model that could not do this. A pre-rendered
input is one question, asked before the model has seen anything.

## 4. Worked application

| need | knowable before? | verdict |
|---|---|---|
| the pending candidate set for this workspace | yes, and the cap is the cost decision | **input** (unchanged) |
| the standing facts for those candidates' literal subjects | yes | **input**, but insufficient alone — see below |
| "is this the same thing under a different name?" | no | **tool** |
| "have we already tried and failed to settle this?" | no | **tool** |
| "what does this room mean by this word?" | no | **tool** |
| "who is 你 in this message?" | no | **tool** |
| the current Standing for this conversation, before revising it | yes — exactly one row, known in advance | **input** |
| "have I concluded something like this before, elsewhere?" | no | **tool** |

The standing-facts row is the instructive one. It stays an input *and* a tool
becomes necessary beside it: the pre-fetch answers "what do we believe about
the subjects as named", and the tool answers "is there something here under
another name". Neither replaces the other.

## 5. What this decides, and what it does not

**Decides.** Fact-tier §4.2's narrow answer stands; its general rationale is
superseded. Read-of-ourselves is no longer a category that is refused on
principle. The tool surface that follows is in
[06-tool-surface.md](06-tool-surface.md).

**Does not decide.** Whether a given tool is *safe* to grant is a separate
question with a separate answer — this rule establishes only that the
capability is needed. Disclosure is in [08-disclosure.md](08-disclosure.md),
and it blocks one of the two paths regardless of anything decided here.

**Does not decide.** The write side. The keystone's refusal of any write verb —
*"the model proposes; code disposes"* — is untouched and must stay untouched. A
read tool is a read tool; every mutation continues to leave as a
schema-validated proposal through a sink.

## 6. Precedent, and a caution

The note tier already implements this rule without naming it. Its index carries
every note's title and description on every turn — *awareness*, knowable in
advance, an input. Its `note_search` reaches note **bodies** — *retrieval by
content*, not knowable in advance, a tool. Its own spec says searching titles
*"would duplicate what is already in the prompt on every turn and would earn
nothing."* That is this rule, derived independently, for a different tier.

The caution is that the note tier pairs its read tool with a **read-before-write
latch**: a revision is vetoed unless the read succeeded in the same turn. The
lesson is not that every read needs a latch — the Dreamer has no write verb to
latch against. It is that granting a read tool was accompanied by a mechanical
guarantee, not by a hope that the model would use it well. Any grant made under
this rule should be able to name its equivalent.
