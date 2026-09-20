---
type: design
title: "Doctrine — One Preamble for Jobs That Are Not the Same Shape"
description: "The Dreamer's one preamble mixes two layers — what the RUNTIME is, true of every adaptor, and what the JOB is, which belongs to one objective. D10 separates them: strip the job claims and the doctrine is shape-free with no new machinery, and «what must I go and find out» goes back to the file that was always supposed to answer it."
tags: [nora, dreamer, doctrine, objectives, prompts, instructions]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: relates_to, provenance: asserted}
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

> **SUPERSEDED IN PART, 2026-09-17 — see [§8](#8-d10--the-doctrine-is-the-runtime-layer-and-the-job-belongs-to-the-objective).**
> D9's diagnosis stands and is the whole of §1–§5. Its headline mechanism does
> not: **D10 retires the first bullet below** — a doctrine that makes no claim
> about the job has no section that applies to some adaptors and not others, so
> there is nothing for an audience field to select. Bullets two and three
> survive and are strengthened by it; bullets four and five are orthogonal and
> still open.

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

*Answered the same day by [§8](#8-d10--the-doctrine-is-the-runtime-layer-and-the-job-belongs-to-the-objective), which reads the same file
against a different axis and finds a smaller change. The paragraph above is
kept as written: its last sentence is the constraint §8 is built to satisfy.*

### 7.4 What must be true before anything is rewritten

- **A synthesis-shaped adaptor must be measurable.** The 0.60/0.67/0.87/0.93
  table exists because a scenario suite existed. There is no equivalent for an
  adaptor whose output is an account rather than a verdict, and a doctrine
  changed without one is changed on argument.
- **The judge result must be protected.** Whatever lands, `note-candidates`
  must be re-measured. A change that recovers synthesis and gives back 0.93 has
  not been evaluated, it has been traded.
  **Correction, 2026-09-17.** This bullet originally said "`note-candidates`
  **and `note-curation`** must be re-measured against that same suite", and
  both halves of that are wrong. There is no `note-curation` suite:
  `evals/run.py`'s `SUITES` holds `participation`, `relevance_gate`,
  `note_candidates`, `proactive_decide` and `proactive_act`, and nothing for
  the curator or for culture. And "that same suite" no longer exists either —
  see [§8.5](#85-the-safety-argument-re-derived-for-the-layer-split).
- **The audience field is the lever D9 named**, and it already exists on every
  doctrine file. That it was not used is the thing to explain before reaching
  for a different mechanism. *§8.2 explains it: there was nothing for it to
  select that should have been in the file at all.*

## 8. D10 — the doctrine is the RUNTIME layer, and the job belongs to the objective

**2026-09-17, later the same day as §7.** §7 read the file along one axis —
which adaptor *shape* a paragraph suits — and concluded that the file needed a
filter. The operator drew a different line, and it is the one the code was
already built on:

> the Dreamer doctrine defines what the RUNTIME is — what environment the
> adaptor is in. Each adaptor's own doctrine defines its job and what it has to
> do.

**Decision (D10): the Dreamer doctrine states what is true of every run because
of the RUNTIME, and makes no claim about what any run is for. Everything that
says what this pass must find out, over what, and what counts as a good answer
moves to the objective — which already exists per adaptor and was always the
file that owned it.**

No audience field. No shape declaration. No new startup check. The change is a
deletion from one file and an addition to four, and every mechanism it needs is
already in the tree.

### 8.1 The boundary is not new — the code states it in two places

`DreamAdaptor`'s docstring (`nora/domain/ports.py:1674`) draws the line twice
in four lines:

> *"One Dreamer objective: **what to research, over what, with which tools**,
> and how to turn the answer into rows."*
>
> *"The Dreamer runtime owns **the loop, the tool ceiling, the budget and its
> own doctrine**, and knows nothing about notes. Everything that IS
> tier-specific arrives through this port."*

The port says the runtime owns its doctrine and knows nothing tier-specific.
The doctrine it owns opens by describing a batch of one room's messages and the
notebook that room keeps. Both sentences are in the tree and they contradict
each other — **and the contradiction can be stated without reference to shapes
at all**, which is the first sign §7 was reading the wrong axis.

The instruction substrate had already made the same distinction one layer up,
for the same reason (`nora/instructions/__init__.py`):

> *"Two artifact kinds, because they have different lifetimes … The doctrine is
> **what is true of every Dreamer run** and changes when the runtime's
> constraints change; an objective is **what one adaptor is for** and changes
> when that one job is retuned."*

«What is true of every Dreamer run» is a **layer** claim, not a shape claim.
Version 2 broke it on 2026-09-08 without that sentence being edited, and
nothing anywhere could notice, because the rule was written in a module
docstring and enforced by nobody.

### 8.2 The file, line by line

Read against the layer boundary rather than against a shape. Every row is the
shipped v2 text (`nora/dreamer/doctrine/dreamer.md`, `applies_to: [dreamer]`,
`change_policy: operator-only`).

| the text | layer | why |
|---|---|---|
| *"You run in the quiet. Nobody is waiting for you, nobody sees this, and nothing you produce reaches anyone in the room until it has been through the checks that come after you."* | **RUNTIME** | true of all four: the runtime screens the envelope's strings, `accept`/`commit` dispose, and even `remind` writes a row a later fire turn reads |
| *"That is what buys you the time to read properly, and reading properly is the entire job."* | **MIXED** | see (a) |
| *"You are handed text — a stretch of one room's conversation, sometimes the notebook the room already keeps — and a question about it."* | **JOB**, and false | see (b) |
| *"You answer the question from that text."* | **JOB** | the sentence the whole finding turns on — see below |
| *"You look; you do not act. Nothing you have here speaks to anybody or changes anybody's state…"* | **RUNTIME**, wrong grain | see (c) |
| *"# What counts as reading"* — attribute before you conclude; who said it, when, whether anyone answered | **JOB** | a discipline for reading a transcript; `note-curation` is handed notes and per-note evidence, `proactive` a note index, each room's standing and its own decision log |
| *"silence is not denial and it is not agreement"* (inside that block) | **RUNTIME** | see (d) |
| *"Read the whole stretch for what it holds, not for its mood. An arrangement sits between jokes and photos…"* | **JOB**, one tier's | see the duplication below |
| *"# Two ways to be wrong, and both cost"* — clutters **a notebook the room reads every day** / missing what the room is in the middle of | **JOB**, one tier's | it names the note tier's artefact and states the note tier's loss function. Culture writes an account; proactive writes reminder rows; neither has a notebook |
| *"# Say what you could not tell"* — «I could not tell» is not «there is nothing here» | **RUNTIME** | see (e) |

Five readings the table cannot hold in a cell, and each is a correction to a
first pass at this split rather than a restatement of it:

**(a) The first block is not cleanly runtime.** *"That is what buys you the
time"* is a runtime fact — nobody is waiting, so there is slack. *"…and reading
properly is the entire job"* names what to spend the slack on, and it is the
cheapest sentence in the file for foreclosing research: the pass is told its
work is reading, before any objective has said otherwise. Version 1's *"being
careful is the entire job"* was at least shape-free in this respect, whatever
§2 says against it on other grounds.

**(b) That sentence is not only job-layer; it is wrong as a description of the
environment.** `note-curation` is unit-keyed, `uses_watermark = False`,
and is handed note bodies with a per-note evidence block — not a stretch of one
room's conversation. `proactive` is handed the note index, each room's standing,
its own decision log and a self card, and its objective says so in its first
paragraph. A runtime doctrine that misdescribes two of four runtimes is not
merely in the wrong layer; it is false in the layer it claims.

**(c) *"You look; you do not act"* is a runtime claim stated at the wrong
grain.** It is the read-only ceiling, and the ceiling is real —
`read_only_tools()` derives 35 verbs from `NativeTool.read_only` (measured
2026-09-17: 35 of 56) and `tool_subset_faults` refuses anything outside it at
startup *and* per run. But `AdaptorGrant.acts` exists precisely to widen it for
one named adaptor, and the result is that the one adaptor holding a grant must
contradict its preamble in its own first sentence: `proactive.md` opens *"This
objective is the one place the Dreamer ACTS, and the doctrine's «you look; you
do not act» is answered here."* **An absolute the runtime itself does not
enforce absolutely is a claim the objective has to repair.** The runtime's own
truth is narrower and needs no repair: what you hold is what your grant gives
you, and the ceiling refuses an act verb unless it names one.

**(d) The silence clause is runtime epistemics wearing a transcript's
clothes.** *"Silence is not denial"* is the same discipline the runtime enforces
on itself everywhere else: never mint a value from an absence
([17](17-failure-classes.md) §3), and `tool_subset_faults`' own refusal message
calls the failure by name — *"«I could not check» reported as «there was
nothing»: the one substitution R-27 forbids."* The principle belongs in the
runtime layer. Its rendering here — *"a room that talks around a thing has
still talked about it"* — is a job's.

**(e) *"Say what you could not tell"* is runtime, and §3's finding survives the
relayering intact.** The distinction is `unavailable` vs `nothing_found`, and
the runtime genuinely turns on it: an `unavailable` run HOLDS the watermark and
a `nothing_found` run advances it. So the doctrine is right to ask for it — and
**no envelope can carry it**. `note-candidates` returns `{verdicts,
candidates}`, `note-curation` `{verdicts}`, `culture` `{account}`. There is no
channel, the only way to comply is prose, and prose fails the parse. That is §3
unchanged, and under D10 it becomes a constraint on the text rather than an
argument about shape: **a runtime doctrine may only ask for what the runtime
can receive.**

**The duplication is the proof, and it is worth more than the table.** The
doctrine's *"Read the whole stretch for what it holds, not for its mood. An
arrangement sits between jokes and photos as easily as it sits on its own
line"* is a near-verbatim copy of a sentence that was already in
`note-candidates.md`:

> *"And a plan does not stop being one because it sits between jokes and
> photos: read the whole batch for the arrangement, not the batch's mood."*

Both landed in the same commit (`97f301a4`). So the job content in the runtime
doctrine is **already in the objective it belongs to, and only there** — which
means that for the adaptor it was written for, moving it is partly a deletion of
a duplicate, and for the other three it is the removal of an instruction about
somebody else's job.

### 8.3 What the split buys, which is not tidiness

**The question every objective was supposed to answer stops being answered for
it.** [03](03-no-agent-loop.md) §7.5 states the test: *"name the second store
this objective would consult if it could."* `culture.md` and
`note-candidates.md` do not answer it — neither text says what the pass must go
and find out. Under the current file that silence is not a gap, because **the
doctrine answers it for them, with «nothing»**: *"You answer the question from
that text."* Strip the claim and the silence becomes visible as silence, which
is the state an author can act on.

**`proactive.md` stops arguing with its own preamble.** §7.2 recorded that it
does — *"before every `remind`, ask memory first"* under a doctrine that says
answer from the text you were handed — and read it as a shape mismatch. It is
simpler than that: the preamble made a claim about the job, the objective owns
the job, so the objective had to overrule it. Two of proactive's opening moves
are repairs of the layer above (the `remind` sentence in (c), and the memory
requirement here). Neither is needed once the layer above stops making job
claims, and the objective gets those lines back to spend on its own work.

**`culture` is the live case that shows the cost is current, not historical.**
`CultureAdaptor.tools` was `frozenset()` until 2026-09-17 and is nine read
verbs since (`CULTURE_TOOLS`, with an `AdaptorGrant(home_room=True)` to make
them resolve), and its `inputs` now render four things rather than two — the
room block, the previous account, `standing`, the note index, then the batch.
**The objective text did not move.** So today there is an adaptor that can
consult memory, the roster, presence analytics, live state and the open
internet, reading an objective that never tells it to, under a preamble that
tells it to answer from the text it was handed. The grant landed; the sentence
that would use it did not, because the file that should carry it was overruled
from above.

**And the whole change needs no new machinery**, which is the strongest thing
about it. `DreamerInstructions.for_objective` already assembles
`doctrine.body + "\n" + objective.body` per adaptor and hashes the pair; four
objectives already exist and are already `change_policy: operator-only`; the
substrate already hot-reloads. Compare what §7 reached for — a live audience
field, and therefore a vocabulary, a filter, and a gate to stop a typo
silently emptying a prompt.

### 8.4 Two shape-classification schemes were proposed, and both were rejected

Recorded because the argument for each is genuinely attractive and the next
reader will re-derive them.

**Rejected (1): DERIVE the shape from the tool set.** An adaptor with no tools
is a judge; one with tools is a researcher. It classified all four correctly on
the morning it was proposed, it is self-correcting, and it matches this repo's
stated preference, which `read_only_tools()` puts plainly: the read/act line is
*"a property of the runtime, not a setting, so it is **DERIVED** here and
hand-listed nowhere."*

It was killed by one question: the derivation reads what an adaptor **has**,
not what its job **is**. `NoteCandidateAdaptor` is tool-free for **cost**, and
says so at `nora/language/note_candidates.py:850-855`:

> *"**TOOL-FREE** (design 33 §4.1): read-only awareness **would be permitted**
> for a scope-keyed adaptor, but this one's design keeps it tool-free anyway,
> **the cost split** every extractor-shaped pass makes — one cheap call decides
> what is worth carrying forward, and a tool call per candidate is a cost
> nobody asked for here."*

So the derivation would freeze a cost decision into a doctrine assignment, and
then hand that adaptor a preamble telling it not to look anything up —
self-fulfilling, and invisible, because the input agrees with the output.

**The tree has since falsified it outright**, which is worth more than the
argument. `CultureAdaptor` went from `frozenset()` to nine verbs on 2026-09-17
in a change that altered its grant and its inputs and **not one word of its
job**. A derived scheme would have swapped that adaptor's doctrine on a grant
PR. A rule that changes a model's instructions when a tool list moves is not
reading the job; it is reading the config.

**Rejected (2): DECLARE the shape on the adaptor, and check the grant against
it.** The correction to (1): shape is stated, never inferred, and an adaptor
declaring research while holding no tools is a startup fault in the same shape
as `tool_subset_faults`' existing check of a declared subset against the
ceiling.

It is rejected as **unnecessary rather than wrong**, and the reason is the
whole of D10: once the doctrine makes no claim about the job, there is nothing
for a shape to select. The field would exist to route text that no longer
exists. Two further objections, since "unnecessary" alone invites a revival:

- **A two-valued shape is a worse instrument than the question it replaces.**
  §7.5's test — name the second store — is answered per adaptor in prose, and
  the answer is a store name and a reason. `judge`/`research` is a lossy
  summary of that, kept in a second place, able to disagree with it.
- **It would be the Dreamer substrate's first gate, and it would arrive
  without evidence.** `nora/instructions/dreamer.py` refuses gates
  deliberately: *"**No gates.** … If Dreamer objectives want gates later, that
  is its own decision with its own evidence."* Neither scheme came with any.

**What scheme (2) was reaching for is delivered anyway.** Its value was that
"note-candidates has no tools" should be a visible statement rather than an
invisible default. Under D10 the objective has to say what the pass must find
out, so an objective that says «nothing beyond this batch, and here is the cost
reason» has stated the decision in the file that owns it — in the words that
carry the reason, not as a boolean that loses it.

### 8.5 The safety argument, re-derived for the layer split

The evidence, in full, from `97f301a4`'s own commit body — the only measurement
anywhere near this decision:

> *"The first live fire on a real dated arrangement returned an empty envelope
> (trace: the deployed doctrine + objective, c9a73442). Against the new
> scenario suite (nora-dreamer, reasoning none): **as-deployed 1.00/0.60,
> objective v2 alone 1.00/0.87 (0.80 at 5 samples — the one-person plan misses
> 5/5), judge doctrine alone 1.00/0.67, both 1.00/0.93 (0.88 at 5 samples,
> 10/10 majority-pass)**. No variant wrote a false note on any negative sample.
> Both files ship."*

It was taken on **`note-candidates`**, and `note-candidates` is the adaptor
whose job text this decision moves. So the question is exactly the right one to
ask, and the answer is not the one §7's shape framing would have given.

**What actually changes for note-candidates if the job paragraphs move
verbatim.** The system prompt is `doctrine.body + "\n" + objective.body`
(`DreamerInstructions.for_objective`). Move those paragraphs into
`note-candidates.md` unchanged and **the set of sentences the model reads is
identical; only their position moves** — from ahead of the objective to
somewhere inside or after it. Nothing else in the run changes: same model, same
budget, same envelope, same batch renderer. `instruction_sha256` changes, which
is correct — the bytes did.

So this is **not a doctrine swap**, and the 2026-09-08 comparison is not the
right analogy: that experiment exchanged one register for another. This one
re-orders. Two reasons not to call it a no-op anyway:

- **This bundle already records one measured instance of position mattering.**
  §4: *"the last thing the model read before answering was prose, and prose is
  what it wrote."* Order was a real effect once, in this corpus, on this class
  of prompt.
- **A verbatim move duplicates.** §8.2's "between jokes and photos" sentence
  would then appear twice in one prompt. De-duplicating it is a content change,
  not a move, and has to be measured as one.

**What would settle it, and the complication nobody has stated yet.** A paired
A/B of the `note_candidates` suite — current build vs moved-text build, same
model, same effort, same sample count, same session — is the measurement. It
cannot be a comparison against 0.93, because **the suite that produced 0.93 no
longer exists**: at `97f301a4` `evals/cases/note_candidates/note_candidates.yaml`
held **10 cases** (5 positive, 5 negative); on 2026-09-17 it holds **21** (8
positive, 13 negative). Counted off the file both times. 0.93 is a reading of a
different instrument, and quoting it as a threshold today would be the mistake
§7 spent a section warning about.

And one run of either arm decides nothing. The same commit reports 0.93 at one
sample and 0.88 at five on the identical build, and [37](37-note-candidates-v2.md)
records v5 at recall 0.84 single-sample against 1.00 unioned over three. A
single paired run separates a regression from sampling noise in neither
direction.

**The other three adaptors, honestly.**

| adaptor | what the move does to it | can it be measured |
|---|---|---|
| `note-candidates` | job text re-positioned, content preserved | **yes** — `note_candidates`, 21 cases today |
| `note-curation` | loses note-tier job text that was about it, gains it back in its own file | **no suite exists** |
| `culture` | loses note-tier job text that was never about it | **no suite exists** |
| `proactive` | loses a preamble it was already overriding in prose | **yes** — `proactive_decide`, `proactive_act`; but see below |

For `proactive` the instrument is real and half-blind, and its own objective
says so: across three live decide-suite runs on 2026-09-16 the seven
`none`-expectation cases passed on every sample with zero false positives,
while all four `remind`-expectation cases produced **zero written rows in all
three runs** — a pre-existing recall failure of that environment and model
tier, not of any prompt under test. So the false-positive side of that suite is
trustworthy evidence here and the recall side is not.

**The claim that is withdrawn.** An earlier form of this proposal was defended
to the operator as having a safety property — that `note-candidates` would keep
the judge doctrine by construction, so the 0.93 was preserved and there was "no
trade". **That claim is withdrawn.** It was true only of a scheme that assigned
doctrines by shape, and that scheme is rejected above. Under D10 the sentences
that produced 0.93 do not stay where they were; they move into the objective,
and whether position costs anything is unmeasured.

### 8.6 What must be true before this ships

1. **`note-candidates` must be re-measured, as a paired A/B on today's corpus,
   at more than one sample.** Not against 0.93 — against the current build run
   in the same session. §8.5 says why the historical number cannot serve as a
   threshold.
2. **A synthesis-shaped adaptor must be measurable at all.** §7.4's bullet
   stands unchanged and is now the binding one: `culture` is the adaptor D10
   most changes the situation of, and `evals/run.py`'s `SUITES` has nothing for
   it. A doctrine changed for culture's benefit and never measured on culture
   is changed on argument. `note-curation` is in the same position.
3. **The move must be verbatim first, and any rewrite measured separately.**
   Two changes in one commit is how 2026-09-08's table came to have four rows
   and this one would have none.
4. **The duplicate must be resolved deliberately**, not silently — see §8.2.
5. **The envelope question in (e) must be answered before the doctrine asks for
   «I could not tell» again.** Either an envelope gains the channel or the text
   stops asking for something only prose can express. Shipping it unchanged
   re-arms §3's ratchet.

### 8.7 What the doctrine text must satisfy — constraints, not wording

`nora/dreamer/doctrine/dreamer.md` is `change_policy: operator-only` and the
wording is reviewed separately. This node records the constraints the text has
to meet, and nothing else:

- **Every sentence is true of all four adaptors**, including the two it was not
  written for. §8.2(b) is the concrete failure to test against: read each
  sentence as `note-curation` and as `proactive` before keeping it.
- **No sentence says what the pass is for, what it is reading about, what
  counts as a good answer, or what the two ways to be wrong are.** Those are
  the objective's four questions.
- **Where the runtime has a default the grant widens, say the default as a
  default.** The read-only ceiling is real and belongs here; *"you look; you do
  not act"* as an absolute is the form that made an objective argue with it.
- **Ask only for what an envelope can carry.** §8.2(e).
- **Whatever moves out, moves — it is not summarised on the way.** A
  paraphrase is a content change wearing a refactor's clothes, and §8.6(3) is
  how that gets measured instead of assumed.

Two of D9's bullets survive and want the same edit (§6):

- **A structural boundary in the assembled text.** `for_objective` joins two
  bodies with `"\n"` and nothing marks that a second document with different
  authority has begun (§1). That mattered when the two files disagreed; it
  matters more once they are two *layers*, because an unmarked concatenation is
  precisely what lets them re-mix.
- **A boundary statement naming what belongs to an objective.** D9 asked for
  one and there was no rule to write; D10 supplies it. Note the constraint on
  where it can live: `not_here` is required by the doctrine loader and
  **deliberately not** by the instruction store, whose `REQUIRED` omits it with
  its reason stated — *"Requiring a field no gate reads and no assembly renders
  is how a frontmatter block turns into ceremony."* So this belongs in the body
  the model reads, or it arrives with a gate; it does not belong in frontmatter
  nothing reads.

### 8.8 Does D10 make D9's mechanism unnecessary, or merely unneeded today?

**Unnecessary, for the audience field specifically.** The filter existed to
route text by shape; D10 removes the text it would have routed. A doctrine that
makes no claim about the job has no paragraph that is right for one adaptor and
wrong for another, so there is nothing for an audience to select, and a field
added anyway would be decoration with a gate attached.

**Not unnecessary for the rest of D9**, which is why §6 is marked superseded in
part rather than withdrawn. The boundary marker and the boundary statement are
above; expected output size and the item cap (§4) are untouched by D10 and
remain open; and §1–§5's diagnosis is what made any of this findable.

*One thing to leave undisturbed for the next reader.* Under D10 this node's
title — "One Preamble for Jobs That Are Not the Same Shape" — is half right and
kept as the record of how the problem first presented. The jobs are indeed not
the same shape. The fix is not a preamble per shape; it is a preamble that
makes no claim about the job, and then the shapes stop being the doctrine's
business at all.

## Status / Checkpoint

2026-08-24 — D8. [27](27-note-dreamer.md) §5 extends it with the three-corpora distinction and why an adaptor's objective is not a skill.

2026-09-17 — §7. D9 is still untaken. The 2026-09-08 doctrine rewrite (`97f301a4`) committed the single preamble to the judge shape rather than making it shape-aware, on a measurement taken over one adaptor and one suite. The defect this node named on 2026-08-22 is unchanged; only which shape the single doctrine favours has moved.

2026-09-17 (later) — **§8, D10. TAKEN, and it supersedes D9's mechanism.** The axis is the LAYER, not the audience: the Dreamer doctrine says what the RUNTIME is and the objective says what the job is, which is what `DreamAdaptor` and `nora/instructions/__init__.py` already say in their own words. Strip the job claims and the preamble is shape-free with no new machinery — no audience field, no shape declaration, no gate. Two shape-classification schemes are recorded as rejected: DERIVED (it reads what an adaptor HAS, and the tree falsified it when `culture` went 0 → 9 verbs with its job unchanged) and DECLARED (unnecessary once the layers separate). **The earlier "no trade" safety claim is WITHDRAWN** (§8.5): `note-candidates` is the adaptor the 0.60/0.67/0.87/0.93 table was taken on and its job text moves, so position is unmeasured — and the suite that produced 0.93 held 10 cases where today's holds 21, so the re-measurement is a paired A/B on today's corpus, never a comparison to that number. Blocking before it ships: that A/B at more than one sample, and a measurable synthesis-shaped adaptor — `culture` and `note-curation` have no suite at all.
