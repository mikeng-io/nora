---
type: contract
title: "The Note Dreamer — Two Adaptors, and the Verb That Blocked One"
description: "The curator acts across notes; the revisitor reaches a person. The revisitor was spec-blocked by a rule that refuses any act verb — the fix is distinguishing a verb that DELIVERS from one that ENQUEUES, because the delivery path re-applies every guard."
tags: [nora, note-tier, dreamer, curator, revisitor, reminders, skills]
timestamp: '2026-08-24'
edges:
  - {dst: 15-roadmap, rel: realizes, provenance: asserted}
  - {dst: 17-failure-classes, rel: depends_on, provenance: asserted}
  - {dst: 13-doctrine, rel: extends, provenance: asserted}
---

# The Note Dreamer

## 1. Two adaptors, and neither exists

The note tier ships its tag-path half — the index, the four tools, the
read-before-write latch. **Its background half is two adaptors and there are
zero objective files for either**: `nora/dreamer/objectives/` holds only
`fact-*` and `room-*`.

| adaptor | job | state |
|---|---|---|
| **curator** | merge, retire, promote, coherence-rewrite — **acts ACROSS notes**, which the tag path may never do | unblocked, unbuilt |
| **revisitor** | reaches a person about a note whose moment has come | **was spec-blocked** — §3 |

The split is the tier's own rule: the tag path may only touch a note it read
**this turn**, so anything cross-note is the Dreamer's by construction. That is
not a convenience — it is what stops one turn silently reorganising a notebook
it only partly saw.

## 2. The curator: merge is keyed on the ANCHOR

The one rule that carries the design: **merge is keyed on the anchor, never on
topical similarity, so two weddings stay two notes.**

Topical similarity would merge them — same vocabulary, same shape, same kind of
uncertainty — and the merge is unrecoverable in the direction that matters: two
notes wrongly merged have already lost which revision belonged to which
occasion. This is [21-substrate-contract.md](21-substrate-contract.md) §5's
default-to-split, one tier over, for the same reason.

**And it is why the entity registry matters here too.** An anchor is
`(anchor_date, entity_set)`, so anchor-keyed merge needs resolved entities —
which is why `note_entity` is empty and why populating it
([24-embedding-seam.md](24-embedding-seam.md) §8) is the curator's real
precondition, not its flag.

## 3. The revisitor was blocked by a rule that is too broad

**The block, stated exactly:** the revisitor's specced tool subset contains an
**act verb**, the startup check refuses any adaptor holding one, and that verb
is *the entire mechanism by which its own spec has a background pass reach a
person*. Removing it leaves the section with no implementation path. So the
adaptor is not merely unbuilt — it is **unbuildable as specced**.

**The resolution is a distinction the rule never drew:**

> **No adaptor may hold a verb that DELIVERS. An adaptor may hold a verb that
> ENQUEUES for delivery.**

The note tier's own design already says the safe route: *"Nothing on the
background clock speaks: a revisit reaches a person only by **scheduling a
reminder**, which fires on the delivery path."*

**Why enqueueing is categorically different from speaking.** A reminder fired on
the delivery path passes every guard a reply passes — the reply guard, the
disclosure subset, the never-silent fallback, the per-requester caps. The
background pass writes a row; **the delivery path decides whether anyone is
spoken to, and in what words.** A verb that delivers would bypass all of it,
which is what the rule was written to prevent, and it prevented the safe case
along with the unsafe one.

This also fits what [15-roadmap.md](15-roadmap.md) §6.1 already establishes:
reminders and events are **the only route by which background work reaches a
human**, and their per-requester caps are therefore *the de facto budget on how
much any pass may nudge somebody* — machinery that exists rather than a new
counter.

**So the revisitor holds `schedule_reminder` and nothing that speaks**, and the
startup check is amended to refuse delivery verbs specifically rather than act
verbs generally. That unblocks the adaptor without widening what background work
may do.

### 3.1 One revisit adaptor, many notes

**The note supplies the objective; the adaptor supplies the procedure.** One
revisitor serves every note — there is no per-note adaptor, and a note's own
`closing_condition` is what tells the pass whether its moment has come.

This is [06-trigger.md](06-trigger.md)'s D2 at the row level: the *condition*
travels with the data, the *mechanism* is shared, and adding a note never adds
an adaptor.

## 4. Are the Dreamer's adaptors skills?

They look alike — both are operator-authored instruction files, versioned,
bind-mounted, and refused from any other source. The answer is **no**, and the
reason is provenance.

| | a skill | an adaptor's objective |
|---|---|---|
| selected by | **the model, by name, mid-turn** | **code, at construction** |
| bound to | nothing — it is a capability offered | one adaptor, one key kind, one sink |
| provenance | which skill loaded is a model choice | **which objective ran is a code fact** |

**That last row decides it.** Every fact and note this bundle writes carries the
hash of the instruction that produced it, and a re-derivation must be able to
say *which objective ran* without trusting the producer. If the Dreamer chose
its own objective by name, the provenance stamp would record a **choice**
instead of a **fact**, and the audit trail would depend on the thing being
audited.

Skills are for a capability Nora *reaches for*. Objectives are for work a
scheduler *dispatches*. Collapsing them would make the second inherit the
first's non-determinism for no gain.

## 5. Three instruction corpora, not one

The question *"is the instruction the base of the Dreamer only?"* has a concrete
answer: **there are three, and they have different lifetimes and audiences.**

| corpus | audience | changes when |
|---|---|---|
| `nora/doctrine/` | **Nora, on the tag path** — who she is when she replies | her character or constraints change |
| `nora/dreamer/doctrine/` | **every adaptor** — who the Dreamer is on any run | the research posture changes |
| `nora/dreamer/objectives/*.md` | **one adaptor each** — what this pass is for | that pass's job changes |

A fourth, `nora/skills/`, is not doctrine at all — it is a capability the model
activates by name, governed by R-29's authority boundary.

**The superseded claim, recorded because it is still cited:** an earlier spec
held *one doctrine, shared by every adaptor*. That is what
[00-overview.md](00-overview.md) supersedes and what D8 replaces with
shape-aware doctrine. The Dreamer's doctrine says who the researcher is; the
objective says what this run is for. **One file cannot do both without becoming
the union of every pass's instructions**, which is how a shared prompt acquires
a clause for a tier that is not running.

## 5.1 The field convention, which was purpose without shape

The tier documents **how** the caps are enforced and never **what the fields
should say**. The enforcement half is right and worth keeping: title and
description are validated in `__post_init__`, **not in the renderer**, because
*"a renderer that truncates loses content silently, and the whole tier is built
on the index being a complete listing of what exists."*

The content half says only that the index is *"enough to know a note exists;
nowhere near enough to revise it."* That is a purpose, not a convention — and a
model given a purpose invents a shape per note.

**The convention follows from the note's own schema.** A note is
`(anchor, uncertainty, closing condition, body)`, so:

| field | carries | cap |
|---|---|---|
| **title** | the **anchor, in words** — the occasion this note is about | 60 |
| **description** | the **uncertainty and its closing condition** — what is undecided, and what would settle it | 200 |
| **body** | the **arrangement** — the thing that has to survive | §5.2 |

**Why the description is uncertainty-plus-closing-condition and not a summary:**
the index exists so Nora can decide *whether to read the body on this turn*.
"Dinner on the 9th" tells her a note exists. "Three restaurants proposed, nobody
has picked one" tells her whether it **bears on what is being said right now** —
which is the only question the index is asked.

A summary answers *what is this about*, which the title already did.

## 5.2 The body has no cap at all

`note_revision.body` is constrained only by `length(btrim(body)) > 0`. **There
is no upper bound**, in the domain or the database.

That is a different risk from the injected fields, and it is easy to miss
because the body is **not** injected — it is read on demand. The exposure is the
**curator**, which reads bodies per tick against a cap that is *"capped in
config and counted when the cap bites."* A read budget in rows is only a budget
if a row has a bounded size; one unbounded body can consume a whole tick's
allowance, and the tier's own rule then reports it as *nothing to do* — the
`unavailable`-as-`nothing_found` failure at the pass level, which that same spec
names in advance.

**Decided, on §5.1's pattern:** `[notes] max_body_chars`, operating value
**8000**, database ceiling **32000**. Eight thousand is roughly a long page —
enough for an arrangement with its history, and small enough that the curator's
per-tick row budget means something.

## 5.3 Does Nora need a note SKILL?

**No — and the reason is a chicken-and-egg the skill mechanism cannot escape.**

A skill is activated **by name, by the model, mid-turn**. So it can only help
once she has already decided she needs it:

> **A skill you must know you need before you can load it cannot teach you to
> recognise the need.**

Splitting the question along that line answers it:

| what | where it belongs | why |
|---|---|---|
| **recognition** — *this conversation has an undecided plan with no home* | **doctrine**, always present | it must be true before she reaches for anything. A skill here is unreachable by construction |
| **procedure** — *how to write the title, revise versus create* | **the tool descriptions** | they are already in front of her whenever the tool is |
| **the read-before-write rule** | **the guard, which already enforces it** | the veto *names the remedy* — "read it first" — so the procedure is taught by the failure, at the moment it matters |

That last row is the repo's own pattern and it is why a procedure skill would be
redundant: **the latch does not document the rule, it makes the rule
unavoidable**, and it explains itself on the one path where explanation is
needed.

What doctrine owes is therefore one or two lines of *recognition*, not a manual.
[13-doctrine.md](13-doctrine.md)'s shape-awareness is what makes that affordable
— the tag path's doctrine carries it; no adaptor pays for it.

## 5.4 THE COLD START — the tier is inside the trap §5.3 names

**Found 2026-08-27 by reading the log, not the design.** In the tier's entire
life — enabled `ba661c9`, 2026-08-21 19:45 HKT — `/app/data/nora.log` holds
**exactly one** note-tool line:

```
{"tool": "note_search", "status": "nothing_found", "ts": "2026-08-21T13:22:37Z"}
```

Zero `note_create`, zero `note_read`, zero `note_update`, ever. **The create path
is not broken. It has never been called**, so the failure is upstream of the
store and no store-side counter could have found it.

**Why she never writes the first note:**

- `render_notes` returns `""` when nothing is standing
  (`nora/language/note_render.py:85-87`), so `_notes_block` emits nothing. With
  zero notes **the `<notes>` block is absent from every prompt.**
- `nora/doctrine/` contains **no note recognition at all** — verified by grep;
  the only corpus match is the phrase "developer note" in `constraints.md`.
- So she has four tools whose descriptions she would read only if she had already
  gone looking.

**§5.3 already made this argument and then left the tier inside it.** That section
assigns recognition to doctrine, "always present", because *a skill you must know
you need before you can load it cannot teach you to recognise the need.* **The
same sentence is true of the index block:**

> **A block that only appears once a note exists cannot teach her to write the
> first one.**

The lone `note_search` is the counter-evidence that matters: the tools ARE
reachable and the workspace DOES resolve. She reached for one unprompted, once,
and found nothing — which is the tier working exactly as built, and never
starting.

**So the cold start is the tier's first defect, ahead of the curator.** A curator
that merges and retires notes has nothing to operate on until the first one
exists, and building it first would measure nothing.

**And the denominator was smaller than it looked.** An earlier framing of this —
*"eight days enabled and zero notes"* — overstated the evidence. Since the tier
went on the log holds **18 `send_reply` calls**. Eighteen authored turns with no
block and no doctrine line is a much weaker indictment than eight days implies,
and it is the honest number to reason from.

## 5.5 The fix: doctrine, and NOT an empty-state block

Two surfaces could carry recognition and only one should. **Doctrine gains a
section; `render_notes` is left returning `""`.**

The framing that suggests itself is a budget trade — a doctrine line costs
always-present prompt, an empty-state `<notes>` block costs a line every turn
saying nothing is written down. Budget does not decide it, because the block is
strictly worse on that axis anyway: per room, every room, forever.

**What decides it is that the two surfaces answer different questions.**

| surface | answers | its correct empty value |
|---|---|---|
| `<notes>` | *what notes exist* | **silence.** Listing nothing is not a fact about the room; it is the absence of facts. A block that speaks when it has nothing to list has stopped being an index |
| doctrine | *who you are* | n/a — «you carry what this group leaves undecided» is true at zero notes and at two hundred |

So putting recognition in the block would make **a stable property of Nora
conditional on volatile state** — which is the same error `_notes_block` already
makes one layer down. Adding a second conditional surface repeats the error
rather than closing it.

**And §5.3's objection resolves against itself.** That section argued for
doctrine over a *skill*; the index block is a different surface, so the argument
does not transfer by analogy. But the REASON §5.3 gives is about
**reachability**, not about skills — *a thing you must already know you need
cannot teach you the need* — and that property belongs to any conditionally
present surface. The index block is conditionally present. The argument
transfers on its own terms.

**The constraint on how the line is written is the part most likely to go
wrong.** Doctrine is a portrait, not a rule list, and every concrete example in
it becomes a CLOSED SET: a line reading *"note down dinners, weddings, trips"*
produces notes about dinners, weddings and trips and nothing else — which would
read as the fix working while shipping a narrower tier than the one that exists.
So the section states a **property and a test** — does the thing outlive this
turn, and is nobody keeping track of it — with one drift illustrated as a shape
rather than a topic. Procedure stays where §5.3 put it: in the tool descriptions
and in the latch's own veto.

**It is falsifiable now, which it was not before §6.1.**
`nora_note_index_entries{workspace}` leaving `0` is the fix working. Staying at
`0` while `nora_note_candidates_total` climbs is *she recognises and does not
act* — the inert-skill failure, visible for the first time. If the line does not
move the gauge, the empty-state block becomes the SECOND experiment rather than
the first guess, and that ordering only exists because the instruments landed
first.

## 6. What each owes its instrument

**The tier declared TEN metrics and shipped ZERO** — verified 2026-08-24, and
the state [09-measurement.md](09-measurement.md) §3 means by *"one tier declares
ten and ships none"*. **Resolved 2026-08-27 by §6.1 below, and not by building
ten.** The readings the two unbuilt adaptors owe are still owed; they are listed
here and they ship with the adaptor that produces them, never before it.

| reading | why |
|---|---|
| notes merged, **by anchor match kind** | §2's rule is only observed if a merge that fired on something other than an anchor is visible |
| notes retired versus notes grown | the notebook only grows until a curator exists, so the curator's whole justification rests on `nora_note_index_entries` — which now has a call site (§6.1) |
| reminders scheduled by the revisitor, and how many **fired** | scheduled-but-never-fired is the revisitor silently doing nothing, which looks identical to a quiet notebook |
| revisits per person per window | §3's budget is the reminder caps, so the reading that matters is *how often background work nudged somebody*, not how often it ran |

## 6.1 THREE counters, not ten — and why that is the fix

**Built 2026-08-27.** The instinct on finding a tier that declares ten metrics
and ships none is to ship ten. That would have been wrong twice over, and
working out how is what the fix actually consisted of.

**FOUR of the ten already had a home.** Every note tool calls `_record` in
`nora.agent.tools`, so `nora_tool_calls_total{tool,status}` was carrying them
all along:

| declared | already carried by |
|---|---|
| `writes_total{kind}` | `nora_tool_calls_total{tool="note_create"\|"note_update",status="ok"}` — the tool name IS the kind |
| `reads_total{result}` | `nora_tool_calls_total{tool="note_read",status}` |
| `searches_total{result}` | `nora_tool_calls_total{tool="note_search",status}` |
| `update_refused{reason="not_read"}` | `nora_guard_vetoes_total{rule="R-55"}` |

Declaring these would be the **double-count** `_record_room` and `_record_media`
both refuse in their own docstrings: a second series over the same calls
silently doubles any dashboard summing tool volume. And the tier's own named
diagnostic survives the omission — *a climbing candidate count against a flat
write count* is `nora_note_candidates_total` against
`nora_tool_calls_total{tool=~"note_create|note_update",status="ok"}`, in two
series that both exist.

**THREE more measure adaptors that do not exist** — `curator_total{action}`,
`curator_bodies_read`, `revisit_total{outcome}` — and so does
`strikes_total{result}`: `note.strikes` is a column with a CHECK constraint and
no writer, because the strike is the revisitor's wake pass (§9.6 of the spec).
**Declaring a name with no call site is its own entry in
[02-instrument-blindness.md](02-instrument-blindness.md)** —
`nora_fact_entity_unresolved_total`'s class — so shipping all ten would have
closed that bundle's note-tier layer by manufacturing a fresh instance of the
layer beside it. A flat zero on a dashboard is worse than an absent series: it
reads as a measurement.

(Named by shape rather than by layer number on purpose: the number was 7 when
this was drafted and belonged to something else an hour later, because two
branches were appending to the same numbered list.)

**So three shipped, each with a live path:**

| metric | site |
|---|---|
| `nora_note_candidates_total{trigger}` | `nora.agent.tools`, inside `record_miss`'s `else` — it counts a miss that was **written**, so the series reconciles against `note_candidate`'s row count instead of attesting to itself |
| `nora_note_update_refused_total{reason}` | `nora.agent.guards` (`not_read`) and `nora.agent.tools` (`title_moved`, `stale_head`) |
| `nora_note_index_entries{workspace}` | `nora.engagement.context::TranscriptInjection._notes` |

**Two things the spec named but did not decide.**

**The gauge is labelled by WORKSPACE.** The spec's name is unlabelled, and on
this deployment that makes it meaningless: `friends` and `ridgeline` are separate
notebooks (§3/DEC-105), so whichever room tagged last would own the reading and
the exported number would mean *"the size of whichever notebook was consulted
most recently"*. Same argument as `nora_dream_evidence_lost_total{scope}`.

**The refusal counter carries THREE reasons, not the two the spec implies,
because `not_read` fires in two different places for two different reasons.**
The guard's latch is keyed on the **title** — all a model-facing argument can
carry. The tool-layer backstop is keyed on the **resolved note id**. They differ
in exactly one case and it is the one worth counting: a title that resolved to a
**different note** between the read and the write. So `not_read` is a
model-discipline reading — she tried to rewrite a body she had not seen — while
`title_moved` and `stale_head` are **contention** readings, a curator racing the
tag path. Folding them would send somebody to fix the wrong thing.

**And the gauge is published on the SUCCESS path only**, so it is ABSENT when
the tier is off or the read failed and `0` when the notebook is wired and empty.
Publishing `0` for all three would be the `unavailable`-as-`nothing_found`
collapse R-27 exists to prevent, in the one series meant to answer *"is the
notebook empty?"* — and it would have preserved exactly the blindness this
closes.

## 6.2 The trace half: a turn that saw nothing

A counter says how often; it cannot say what a given turn saw. Read back from
live trace `9bfba296` rather than assumed, Langfuse already carries the note
tools as TOOL spans with their full input and output, and
`nora.obs.tracing.trace_attributes` stamps `tag_id`/`conversation_id`/
`doctrine_sha256` on every span in the turn.

**What no trace could report is the fact §5.4 turns on.** A turn that saw an
EMPTY notebook and a turn where the tier was never wired produce the same spans,
the same tool catalogue and the same prompt shape. So `trace_attributes` gained
**`note_index_entries`** — a COUNT, which is why it is admissible in a dict that
otherwise holds only ids and hashes: it says how many notes were in front of her
without saying what any of them is.

It is the per-turn counterpart of the gauge and they answer different questions
on purpose — the gauge says what the notebook holds NOW, the attribute says what
THIS turn saw. They diverge exactly when it matters: a reply written against an
index that has since moved.

**Still not instrumented, recorded so it is not mistaken for done:**
`record_miss` has no span of its own, so a trace shows `nothing_found` and never
that a candidate row was written; and `title_moved` and the guard's `not_read`
return the same status AND the same message string, so only the metric separates
them. Neither blocks §5.4; both are real.

## Status / Checkpoint

2026-08-27 (later) — **§6.1/§6.2 built.** The three counters and the
`note_index_entries` trace attribute are in code, gate green. Every increment
was verified by reverting it and watching a test go red; one test did NOT go red
on the first pass, because `Metrics.get` answers `0.0` for a series that was
never published, so *"the empty notebook reads zero"* passed against reverted
code and now asserts membership in `snapshot()` first. **The tier can now
distinguish an empty notebook from a broken one**, which is the precondition
§5.4's fix needs to be judged against.

**§5.4 closed the same day.** `nora/doctrine/conduct.md` gains one section,
「未有結論嘅嘢」, and `render_notes` is UNCHANGED — the empty index still
renders nothing. §5.5 argues why that split, and why the empty-state block was
the wrong surface.

**What remains, in order:** the curator (§2), then the revisitor (§3). The two
unbuilt adaptors' four metrics ship with them.

2026-08-27 — **§5.4: the cold start.** The log shows ONE note-tool call in the
tier's life; the create path has never been invoked. With zero notes the
`<notes>` block is absent from every prompt and doctrine carries no recognition
rule, so §5.3's own argument — a thing you must already know you need cannot
teach you the need — applies to the index block itself. §6.1 records why the
tier ships THREE counters rather than the ten it declares.

2026-08-24 — written after the operator asked whether the note dreamer has a
design, whether all dreamers should be skills, and whether the instruction
corpus is the Dreamer's alone. It **unblocks the revisitor**, which
[15-roadmap.md](15-roadmap.md) recorded as spec-blocked, by drawing the
deliver/enqueue distinction the startup check was missing.
