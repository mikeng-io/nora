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

## 7. D9 was not taken, and the change that landed instead went the other way

**2026-09-17.** D9 above says the fix is to make the doctrine **shape-aware** —
"the minimum change that fixes the class rather than one instance". That was
never built. What landed instead, on 2026-09-08 (`97f301a4`), was the opposite
move: the doctrine stopped being a research doctrine and became a **judge**
doctrine. One shape, chosen, rather than a doctrine that knows there is more
than one.

The frontmatter states it plainly, and it is the clearest description of the
current state anywhere in the tree:

> Version 2 (2026-09-08) rewrites it for what the Dreamer now is — **a
> tool-free judge over text it is handed** — after version 1's research
> register (sources, pages, when to stop looking) was measured to push a judge
> toward an empty answer.

**The measurement behind it is real, and it is narrow.** From the commit, on
the note-candidates scenario suite at `reasoning_effort = "none"`:

| variant | |
|---|---|
| as-deployed (v1, the research register) | 1.00 / 0.60 |
| objective v2 alone | 1.00 / 0.87 |
| **judge doctrine alone** | 1.00 / **0.67** |
| both | 1.00 / 0.93 |

One adaptor. One suite. And `note-candidates` **is** a judge — it is handed a
batch and returns a verdict per message over a fixed vocabulary. For that shape
the result is exactly right, and nothing here proposes undoing it.

What the table cannot say is anything about an adaptor whose job is not
judging, because on 2026-09-08 there was no such adaptor to measure. The
doctrine is served to **every** adaptor on **every** run, so a result measured
on the one shape that existed became the contract for shapes that did not exist
yet.

### 7.1 What the doctrine now forbids, in its own words

> You are handed text — a stretch of one room's conversation, sometimes the
> notebook the room already keeps — and a question about it. **You answer the
> question from that text.**
>
> **reading properly is the entire job**

Read as a judge's instruction that is correct. Read as the preamble to every
adaptor it is a standing prohibition on looking anywhere else — and it is
prepended to adaptors that hold tools. `ToolRegistry.read_only_tools()` returns
**35** verbs; `AdaptorGrant` exists to hand an adaptor a real room so that the
room-scoped ones resolve. The doctrine's "tool-free" and that machinery are
describing different systems.

### 7.2 One shipped adaptor is already overriding it

`proactive.md` instructs *"before every `remind`, ask memory first"*, with the
acceptance test *"A `why` that could have been written from the note alone is a
sign you skipped this."* That objective is arguing **against** the doctrine
above it, and it was written that way because a live failure of exactly this
class was measured (objective version 2's own changelog).

An objective that has to argue against its own preamble is the §2 mismatch this
node opened with, still present, one doctrine rewrite later.

### 7.3 Why this is a class defect and not a culture-tier complaint

The culture tier is where it was noticed — it ships `tools = frozenset()` and
an objective that says *"if the room gives you any way to know"*, i.e. settle
for the prompt — but the tier is downstream of the doctrine, not the cause.
`17-failure-classes` class 11 records the failure it produces; `03-no-agent-loop`
§7 records what the loop was for. This node holds the mechanism: **one preamble
for jobs that are not the same shape**, which is the title it has carried since
2026-08-22.

D9 remains the stated fix and remains untaken. Nothing here proposes a
replacement text — a doctrine rewrite that is not shape-aware would be the
2026-09-08 move again with a different winner, and the cost of guessing is on
the record in the table above.

### 7.4 What must be true before anything is rewritten

- **A synthesis-shaped adaptor must be measurable.** The 0.60/0.67/0.87/0.93
  table exists because a scenario suite existed. There is no equivalent for an
  adaptor whose output is an account rather than a verdict, and a doctrine
  changed without one is changed on argument.
- **The judge result must be protected.** Whatever lands, `note-candidates` and
  `note-curation` must be re-measured against that same suite. A change that
  recovers synthesis and gives back 0.93 has not been evaluated, it has been
  traded.
- **The audience field is the lever D9 named**, and it already exists on every
  doctrine file. That it was not used is the thing to explain before reaching
  for a different mechanism.

## Status / Checkpoint

2026-08-24 — D8. [27](27-note-dreamer.md) §5 extends it with the three-corpora distinction and why an adaptor's objective is not a skill.

2026-09-17 — §7. D9 is still untaken. The 2026-09-08 doctrine rewrite (`97f301a4`) committed the single preamble to the judge shape rather than making it shape-aware, on a measurement taken over one adaptor and one suite. The defect this node named on 2026-08-22 is unchanged; only which shape the single doctrine favours has moved.
