---
type: design
title: "Doctrine — One Preamble for Jobs That Are Not the Same Shape"
description: "A research doctrine is prepended to every adaptor including a tool-less extractor, where its stopping condition is undefined and one of its instructions cannot be represented in the output schema at all."
tags: [nora, dreamer, doctrine, objectives, prompts, instructions]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
---

# Doctrine — One Preamble for Jobs That Are Not the Same Shape

## 1. What the model is actually handed

For the two migrated adaptors, the system prompt is the doctrine body and the
objective body joined by a newline. Nothing else — no separator, no heading, no
tag, no marker that a second document with different authority has begun. The
adaptor's rendered input arrives as the first user message.

Nora's own doctrine, by contrast, wraps every section in a tag naming its kind
and description, carries an explicit ordering, an authority graph checked for
symmetry and cycles, a required "what this file must not contain" statement, a
register check, a catalogue gate, and a working audience filter — **eight
startup gates**. The Dreamer's corpus has **zero**.

Two structural gaps follow directly:

- **The audience field is decorative.** Every objective declares who it applies
  to; **nothing reads it**. There is no mechanism by which a doctrine paragraph
  could be marked "research adaptors only". The doctrine is all-or-nothing, and
  that is the root of everything below.
- **There is no boundary statement.** A "not here" line on the Dreamer doctrine
  — *nothing about sources, evidence or stopping; that belongs to an objective,
  and not every adaptor has one* — would have caught this at authoring time.

## 2. The mismatch, line by line

The doctrine is written for research: what counts as knowing, a source has to
be about the thing you asked, silence is not denial, when to stop looking, say
what you could not check. For the adjudicator it is well-matched — it was
written for that job.

For a **tool-less extractor** reading a fixed batch, roughly two sentences
apply. Five instructions are actively harmful:

| instruction | why it harms a tool-less extraction pass |
|---|---|
| *"being careful is the entire job"* | an explicit licence to deliberate, given to a run capped at two turns and sixty seconds |
| *"before you let a source move you, say to yourself what claim it is answering"* | an instruction to **produce reasoning text**, applied per item, over a batch of up to four hundred |
| *"if your reasoning contains the step…"* | presupposes a visible reasoning trace in a task whose only legal output is one JSON object |
| *"weigh what you find by whether it bears on the claim"* | asks for the adjudication the objective explicitly forbids — *"you are NOT deciding whether any of them is true"* |
| the entire **when to stop** section | its stopping condition is defined **purely in terms of repeated looks**. This task has one pass and no looks, so the section defines **no terminating condition for it at all** |

That last row is the whole finding. The model is handed a stop rule whose
antecedent can never be evaluated, on a task with no natural end, and told that
being careful is the job.

## 3. An instruction the schema cannot carry

The doctrine says:

> **«I could not check» is a result. Report it.**

The extraction envelope has **no channel for it**. The only representable
outputs are claims or an empty list.

So the doctrine instructs the model to emit something the schema cannot hold.
The only way to comply is prose. Prose fails the envelope parse, which is
correctly reported as `unavailable`, which correctly holds the watermark —
which means the batch is re-read next tick, larger, and fails again.

> **The doctrine is instructing the model into the ratchet.**

There is a second, flatter contradiction three lines apart: the doctrine says a
confident wrong answer *"gets written down… and outlives the run that made
it"*; the objective says *"being wrong is exactly what makes one worth checking
later."* A model resolving that in the doctrine's favour under-reports; one
resolving it by explaining itself writes prose.

## 4. What is missing everywhere

- **Expected output size is stated nowhere**, in any file. The extraction ask
  scales linearly with batch size — *"write each one down"* over up to four
  hundred messages — against a fixed output budget.
- **The item cap is never communicated to the model.** Over-production is
  truncated to the first N — logged and counted, so the runtime is not silent —
  but **the watermark still advances**, so a large batch loses everything past
  the cap and the model is never told the cap exists.
- **"You have no tools" is never said**, while the doctrine repeatedly says
  *"you went and looked."*
- **The output contract is not actually last.** A recent fix moved it to the
  end of each objective *file*, but the objective lives in the **system
  prompt** and the batch follows it as a user message. The last thing the model
  reads before generating is still hundreds of lines of chat. The fix's own
  reasoning — *"the last thing the model read before answering was prose, and
  prose is what it wrote"* — applies unchanged to the current state.
- **Provider-level JSON mode is absent** from the Dreamer's model, and
  **present** on the legacy observer path, whose own comment records that
  prompt-only enforcement was already proven insufficient once. The newer
  runtime lost a guarantee the older one has.

## 5. The unmigrated tiers get no doctrine at all

The two observers bypass the runtime and send a single user message with **no
system prompt**. They never receive the doctrine.

Which produces the sharpest statement of the whole mismatch:

> *"A source has to be about the thing you are asking about"* is exactly the
> right instruction for the wrong-gloss failure — and it reaches the extractor
> that has no sources, while missing the two adaptors that produced the wrong
> glosses.

## 6. Direction

**Decision (D9): doctrine becomes shape-aware.**

- Make the audience field **live**, so a doctrine section can name the adaptor
  shapes it applies to. This is the minimum change that fixes the class rather
  than one instance, and the field already exists on every file.
- Give the assembled text a **structural boundary** between doctrine and
  objective, as Nora's own doctrine has.
- Give the doctrine a **boundary statement** naming what belongs to an
  objective instead.
- Every objective states its **expected output size** and any cap it is subject
  to, and the truncation stops being silent.
- Re-examine what "last" means once the contract lives in a system prompt and
  the batch is a user message.

**Sequencing constraint.** Three of the five objective bodies are pinned
byte-for-byte to fixtures, deliberately, so that the lift could be proven
separable from any content change. Editing them before the migration is a
substrate bug **by the rule's own terms** — and the migration is exactly when
those fixtures are deleted. See
[14-migration-and-governance.md](14-migration-and-governance.md).

The two fact objectives are **not** fixture-pinned and can move first.
