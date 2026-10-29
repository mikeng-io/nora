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

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.

> **FURTHER DEPRECATED IN PART 2026-09-08 — the curator's MERGE half is deleted.** §2 and §12.2–§12.4 describe merge eligibility over a resolved anchor, `note_entity`, the merged body and the guard around it; none of that is code any more. Once the knowledge tier stopped minting entities the resolved half of the anchor could never be populated and merge was inert by construction, so the verb, the entity-resolution pass (issue #100) and the `fact_entity` registry store went together. The retire half (§11, §12.1, §12.5) is unaffected. `design/09-decision-index.md` R-75 records which clauses survive.


# The Note Dreamer

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 1. Two adaptors, and neither exists ~~— as of this node's 2026-08-24 write~~

The note tier ships its tag-path half — the index, the four tools, the
read-before-write latch. **Its background half is two adaptors and there were
zero objective files for either** at the time this was written:
`nora/dreamer/objectives/` held only `fact-*` and `room-*`. **That is no longer
true** — the curator shipped 2026-08-28 and `nora/dreamer/objectives/`
now also carries `note-curation.md` and `note-candidates.md`. The revisitor
remains spec-blocked (§3) and has no objective file.

| adaptor | job | state |
|---|---|---|
| **curator** | ~~merge,~~ retire, promote, coherence-rewrite — **acts ACROSS notes**, which the tag path may never do | ~~unblocked, unbuilt~~ **shipped 2026-08-28** (retire only; merge deleted 2026-09-08, promote is a gated discharge path, coherence-rewrite was never built) |
| **revisitor** | reaches a person about a note whose moment has come | **was spec-blocked** — §3 |

The split is the tier's own rule: the tag path may only touch a note it read
**this turn**, so anything cross-note is the Dreamer's by construction. That is
not a convenience — it is what stops one turn silently reorganising a notebook
it only partly saw.

## 2. The curator: merge is keyed on the ANCHOR

> **Superseded 2026-09-24 (DEC-202, design 41 §7).** Merge returns as a separate
> `note-merge` adaptor that asks the model whether two notes are the same open
> question — the topical judgement this section forbids — because the entity
> anchor it relies on no longer exists (R-75) and a merge there deletes nothing:
> the duplicate keeps its revision chain and can be un-retired. The text below is
> the original design, kept as a record.

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

Zero `note_create`, zero `note_read`, zero `note_revise`, ever. **The create path
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
reachable and the unit DOES resolve. She reached for one unprompted, once,
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
`nora_note_index_entries{unit}` leaving `0` is the fix working. Staying at
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
| ~~notes merged, **by anchor match kind**~~ → `nora_note_curator_groups_refused_total{reason}` | §2's rule is only observed if a merge that fired on something other than an anchor is visible. **SUBSTITUTED, 2026-08-28, and the substitution is recorded because the original label is unbuildable by construction.** There is no «match kind»: eligibility is one key comparison with one outcome, so a merge that fired on something else is not a different label — it is a different function. What CAN be measured is the negative: the groups eligibility REFUSED, and why. `merge` beside `merge_refused`/`merge_failed` on `nora_note_curator_total{action}` carries the rest |
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
| `writes_total{kind}` | `nora_tool_calls_total{tool="note_create"\|"note_revise",status="ok"}` — the tool name IS the kind |
| `reads_total{result}` | `nora_tool_calls_total{tool="note_read",status}` |
| `searches_total{result}` | `nora_tool_calls_total{tool="note_search",status}` |
| `update_refused{reason="not_read"}` | `nora_guard_vetoes_total{rule="R-55"}` |

Declaring these would be the **double-count** `_record_room` and `_record_media`
both refuse in their own docstrings: a second series over the same calls
silently doubles any dashboard summing tool volume. And the tier's own named
diagnostic survives the omission — *a climbing candidate count against a flat
write count* is `nora_note_candidates_total` against
`nora_tool_calls_total{tool=~"note_create|note_revise",status="ok"}`, in two
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
| `nora_note_revise_refused_total{reason}` | `nora.agent.guards` (`not_read`) and `nora.agent.tools` (`title_moved`, `stale_head`) |
| `nora_note_index_entries{unit}` | `nora.engagement.context::TranscriptInjection._notes` |

**Two things the spec named but did not decide.**

**The gauge is labelled by UNIT.** The spec's name is unlabelled, and on
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

**§5.4 closed the same day.** `nora/doctrine/rooms/conduct.md` gains one section,
「未有結論嘅嘢」, and `render_notes` is UNCHANGED — the empty index still
renders nothing. §5.5 argues why that split, and why the empty-state block was
the wrong surface.

**What remains, in order:** the curator (§2), then the revisitor (§3). The two
unbuilt adaptors' four metrics ship with them.

2026-08-28 — **both are built.** The revisitor's retirement half is §11; the
curator is §12. What remains of §3 is the reaching-a-person half, which is
§11.4's deliberate omission and needs `tool_subset_faults` widened before it can
exist.

## 11. The revisitor, built — retirement half only

**2026-08-28.** The adaptor is `nora.language.note_revisitor.NoteRevisitor`,
unit-keyed, watermark-free, and it holds **no tools at all**.

### 11.1 The asymmetry the whole module is arranged around

Retirement is irreversible in the direction that matters. A note swept away
while its question is still open is a tracked uncertainty that silently stopped
being tracked, and nothing downstream can tell that apart from a question that
got answered. So the default everywhere is **the note survives**:

**closing condition MET** → `retired-closed`. The model must cite a message,
and that message must be in the evidence it was shown.

**moment PASSED, nothing answered it** → `retired-expired`. The anchor date is
behind the clock — arithmetic, not opinion — and there was real evidence for
the question to have gone unanswered *in*.

**anything else** → no `supersede` call is made and the note stands.

`merged` is the curator's and is never written here: merging is a judgement
across notes, and this adaptor sees one note at a time by construction.

**Both retirements require evidence to EXIST**, and that is the ruling worth
carrying forward. A room that has said nothing since the note was written
cannot establish that a question went unanswered — it establishes that nobody
spoke. **Silence is not an answer**, so `retired-expired` is deliberately NOT
reachable from a passed anchor date alone.

### 11.1.1 The envelope carries an OPAQUE HANDLE — found by cold review

**The first revision of this adaptor could not retire anything at all**, and
nothing in the tier said so. `DreamerRuntime` runs
`nora.agent.dream_screen.screen` over the model's raw envelope items **before**
`accept`, and that screen drops the WHOLE item on any identifier reachable in
it. A note id is `str(uuid4())`, `egress._UUID` matches any hyphenated UUID,
and the objective asked the model to echo `note_id` back — so **100% of items
were screened, every tick, forever.**

The only trace was `nora_dream_screened_total{adaptor="note-revisit",
reason="identifier"}`, a generic Dreamer counter. All five of the tier's own
counters behaved as if the model had stopped answering: candidates climbing,
verdicts and refusals and retirements flat. **An operator reading the tier's
own instruments would have concluded the wrong thing**, which is this bundle's
Layer-5 shape one level down.

Twenty-seven passing tests could not see it, because every one of them called
`accept` directly. This adaptor was the **first to put a UUID in an envelope
item**, so no earlier tier had tripped the screen and nothing in the runtime's
own suite covered the combination.

**The fix is a per-tick ordinal** — `### note 1` — mapped back through the
asked-set, which had to exist anyway. It removes the UUIDs from the prompt as
well. **Widening `dream_screen` was rejected**: it is a shared security rule,
and widening it for one caller is how such a rule dies.

**And the schema is minimal for the same reason.** Every model-authored string
in an item is a chance for the screen to drop a correct verdict — a free-text
`why` quoting a phone number out of the evidence would silently kill the
retirement it was explaining. The citation is the explanation; there is no
prose field.

**The generalisable rule:** an adaptor's tests must drive at least one item
through the real screen, in the order the runtime does. A test that calls
`accept` directly is testing half a pipeline.

**A second rule, from the guard that had the defect's own shape.** The parity
test written to catch «a value in the code and not in the spec» — the exact
drift that had just been found — was itself bound to a hand-written tuple that
nothing in the module referenced. Emitting a fifth state left every test green;
it could only catch DELETION from the help text. **The fix for the
silent-failure finding had the silent failure's shape.** It is derived from a
`Literal` now and enforced twice, statically and at construction, because
`nora.language` sits outside `make gate`'s mypy scope (#61) and a static-only
guard in an unchecked package is a claim rather than a check.

### 11.2 What the model decides, and what it does not

The verdict is semantic and belongs to a model. The grounding is not:

| checked against | not the model's word for it |
|---|---|
| the note id | must be one this tick actually asked about |
| a `closed` citation | must be a message id rendered for THAT note |
| an `expired` verdict | the clock, not the model's opinion of the clock |

The asked-set is rebuilt every tick and never merged across them: a note id
from a previous tick is exactly as unaskable as one the model invented, because
the evidence that justified asking is gone.

A boolean citation is refused explicitly. `isinstance(True, int)` is True in
Python and `int(True) == 1`, so `cited_message_id: true` would otherwise read
as citing message 1 — a real id, plausibly in the evidence, grounded enough to
pass the membership check, and pointing at the wrong thing.

### 11.3 Evidence is assembled in CODE, not fetched by a tool

A unit has no single room, so no room-scoped tool can be granted (§4.2,
and `tool_subset_faults`' second axis). The evidence is therefore selected here:
the note's own `established_in`, capped, filtered to messages that POSTDATE the
note — a closing condition cannot have been met before the note existed.
`established_in` is a routing key and the unit is already the disclosure
boundary (DEC-105/R-49), so reading the room a note was born in to judge that
note stays inside the boundary the note already sits in.

**The evidence is rendered OLDEST-FIRST, and that is load-bearing.**
`RecordStore.recent` returns the newest `limit` rows oldest-first — its own
docstring says so and the audit-store contract suite pins it on both
implementations. The first revision reversed them, putting the prompt in
newest-first order under a header reading *"evidence, newest last"*. The
failure that causes is not cosmetic: the model reads the OLDEST line as the
latest word, and can close a note by citing a booking that a later message
already cancelled — **a citation that passes every grounding check in the
module**, retiring a note in the unrecoverable direction. The double in the
adaptor's own suite had been written to match the code's belief rather than the
store's contract, so the whole file agreed with the bug. That is the
double-diverges-from-the-real-store family again, and the first instance where
the double was derived from the code it was testing.

**And the first fix did not close it.** Reverting the adaptor alone reddened;
reverting the double alone reddened; reverting BOTH — which is exactly how the
bug shipped — passed. A test that pins two things against each other pins
nothing. That local double was also a FOURTH implementation of `recent` covered
by no contract suite, against the rule that a port never gains one without one.
It is deleted: the suite now uses `nora.testing.fakes.FakeRecordStore`, which
`tests/contract/test_audit_store_contract.py` already holds to the same
ordering as `SqlRecordStore`. **The coordinated revert now reddens the CONTRACT
suite** rather than this tier's own file — which is the right place for the
guard, because that is where the shared property lives.

**Nora's own words are not evidence.** `recent` does not exclude `is_nora`
rows; `since` does, in the query, and its docstring says why. Her saying *"I'll
book Yardbird for the 9th"* is an intention she stated, not the group settling
anything — closing a note on it is self-attestation deciding a retirement,
which is the one thing no measure in this repo may rest on.

**NARROWED 2026-09-11 (issue #199): her DELIVERED rows are.** The rule above
is about a claim she made; `delivery_status == SENT` is not a claim she made —
the sender writes it back once the platform has accepted the message, which is
A9's record-first/send-after pair answering *did this reach anybody*. The
filter made one whole class of note unclosable by construction: a closing
condition can name something only SHE can do ("Nora successfully sends a
route"), so the pass whose job is to retire it could never see the thing that
satisfied it. Her delivered rows are now rendered in their own marked block
and a `closed` verdict may cite one. Everything else derived from the evidence
stays human-only — the quiet-room reading and `_covers`, so `expired`/`moot`
remain unreachable through her — and a `PENDING` or `FAILED` row of hers is
still the self-attestation this paragraph refuses. A discharge is never
grounded on a row of hers: the refusal sits past `_run_discharge`'s single
`cited_row` resolution, on its own `self_cited` outcome, so a note she
closed retires with no commitment and no person-claim routed out of it
(design 39 D7).

**A failed record read leaves the note standing**, counted apart from an
honestly quiet room. Both render identically to the model — its job is the same
either way, decide nothing — but they are different states for the operator, and
one counter for both is `unavailable` wearing `nothing_found`.

### 11.3.1 The asked-set is keyed by KEY, not a bare attribute

`dream_loop` dispatches one task per `(adaptor, key)` and its `running` lock is
**per-unit**: one key's `inputs`/`accept` pair is non-overlapping, and nothing
makes two DIFFERENT keys' pairs non-overlapping. The live `policy.toml` has two
units, so this is a deployed configuration.

With a bare attribute, unit B's `inputs` wipes unit A's map, and A's
grounded, correct verdict is dropped and counted `reason="unknown_note"` — the
label whose own spec text reads *"one hallucinated identifier away"*. **A
wiring gap reported as a model fault**, inside the instrument built to refuse
exactly that collapse. `nora.language.fact_adaptor`'s `_message_ids` is the
standing precedent and states the rule verbatim.

### 11.3.2 The clock is injected

`inputs` has no `now_ms` parameter (only `commit` does), and *"has this note's
moment passed"* is one of the two grounds for an irreversible action — so it
has to be testable **on** the boundary. Without an injected clock the fixtures
are dated against the wall clock, a boundary case cannot be written at all, and
a date chosen to be safely in the future silently stops testing anything the
day it arrives — by rendering nothing, which reads as passing.

Two separate comparisons use it, and only one was covered until a mutation
found the gap: `_is_due` and `anchor_passed` both test the same end-of-day
instant, and the second is the one that PERMITS a retirement. A note sitting
exactly on its anchor's last instant is not due, so it reaches `accept` only
via `revisit_at` — which is what makes the state reachable and the guard
testable.

**`_anchor_end_ms` computes end-of-day in UTC while the deployment runs in
HKT**, so a note anchored to the 9th only reads as passed at 08:00 local on the
10th. Recorded rather than fixed: it fails in the conservative direction (later,
never earlier), and choosing a timezone is a decision about what a note's date
MEANS to the people writing it. *The reading that would settle it:* a note whose
anchor day matters to within a few hours.

### 11.3.3 `retired-expired` needs the window to SPAN the moment

**Found by external review after two internal passes with 26 mutations missed
it**, and it is the only finding across four reviews that produces a WRONG
retirement.

`anchor_passed` says the moment has gone by. It says nothing about whether
anybody was looking when it did, and the first two revisions treated those as
one thing: `expired` needed only that the room had said SOMETHING, anywhere
after the note was written. Two live shapes retire a note wrongly:

- **the room dies before the moment.** Note written 1 Aug, anchored to 9 Sept,
  thirty messages in the first fortnight of August, silence after. The window is
  non-empty and holds no answer, so the model answers `expired` — correctly, on
  what it was shown — and the note is retired with **zero evidence about
  September**. Reproduced against the real code before the fix.
- **the room is busier than the window.** The message that ANSWERS the question
  falls outside the newest `limit` rows. The window shows unrelated later
  traffic, `expired` is accepted, and an **answered** note is recorded as having
  gone unanswered.

`_covers` is the code-side ground: the evidence must reach **past** the anchor's
last instant (the room was still talking once the moment had gone by) and
**back** to before its first — either the window is untruncated, so there is no
older traffic to hide, or its oldest row predates the moment. Otherwise there is
unseen conversation lying exactly where an answer would be, and that is
`unavailable`.

**This also falsified a claim in `config.py`.** The evidence cap's docstring
asserted *"there is no setting of this that causes a wrong retirement"*. That
was false: a truncated window converts «answered outside the window» into a
retirement. Corrected in place rather than deleted — a config docstring
asserting a safety property the code does not have is worse than one saying
nothing.

**Only `expired` needs it.** A `closed` verdict names a specific message that
settled the question, and that citation is its own ground wherever in the window
it sits.

*The reading that would reopen it:* notes accumulating unretired with
`anchor_not_covered` climbing and never resolving — a room whose quiet is
permanent, where holding forever is the wrong answer and `strikes` is the right
one.

*Reopened 2026-09-25, the other way round (DEC-203).* The reading arrived from a
BUSY room, not a quiet one: 8 of 13 open notes in the live `friends` unit, anchors
2 to 17 days past, 183 to 925 messages since each was written (50 to 345 before
the anchor itself), refused `anchor_not_covered` on every pass. A 40-row window
cannot predate a note in a room that talks that much, so the reach-back half was
unsatisfiable, not cautious. Past `ANCHOR_LAPSE_MS` (two days after the anchor)
only the reach-past half binds. The cost is this section's own second danger,
readmitted past the lapse: something said before the window is not shown, so an
answer older than it reads as `expired` rather than `closed`, and a change of plan
older than it can let `expired` retire a live note. The model is told when its
window is cut short; each verdict accepted this way is counted
`nora_note_curator_total{action="lapsed"}`.

**The first version of this check was still wrong, and at the shipped default
cap.** It measured truncation against the ANCHOR's first instant, on the
reasoning that unseen traffic mattered only where it lay across the moment.
That is false by the definition of a closing condition: **a note anchored to 9
September is answered by «booked Yardbird for the 9th» said on 3 August.**
Reproduced at `revisit_evidence_messages = 40` — the answer two days after the
note, forty later messages straddling the anchor day, the window's oldest row
comfortably before the anchor so the check PASSED, the answer invisible to the
model, and an **answered** note retired as expired.

The reference point is the **note**, not the anchor: either the window is
untruncated, or its oldest row predates `created_at`, so everything said since
the note was written is in view. That also drops the anchor's start from the
clause and closes a second defect with it — `_anchor_bounds_ms` computes in UTC
while the deployment runs in HKT, which makes `reaches_past` stricter (safe)
but made the old `reaches_back` **looser** by the same eight hours (not safe).
So §11.3.2's "later, never earlier" was true of one clause and false of the
other while both existed.

**The cost of holding has no pressure valve.** A note whose room fell quiet
before its date is refused every tick, `_is_due` re-selects it, and
`due[:max_revisits]` takes `standing()`'s oldest-first order — so held notes at
the head consume revisit slots forever and starve the due notes behind them.
The design's answer is the `strikes` breaker, and §11.3.5 records that
`strikes` has no writer. **This deferral currently defers to nothing.** Safe
direction, unbounded cost, and the strongest argument for building the wake
pass next. One related pressure, pre-existing and made certain by it: `closed`
does not pass through this check, and its citation test requires only that the
cited id be IN the window rather than that it answers anything — so a held note
is re-asked every tick and gets unbounded chances to come back as `closed`
instead.

### 11.3.4 A model that contradicts itself retires nothing

`[closed, expired]` for one note used to retire it as closed, and the same two
items reversed retired it as expired — **an irreversible decision resolved by
list position**. A model giving one note two different verdicts is the
definition of «cannot tell which», and both answers throw the note away. Refused
outright now, and the note stands.

The counter had the matching defect: `verdicts_total` incremented per ITEM,
before de-duplication, so two items for one note asserted two accepted verdicts
and produced one retirement — the instrument disagreeing with the action it was
measuring. A verdict is accepted when it survives resolution, not when it
parses.

**Three revisions, one blind spot, and it is worth naming as a class.** Each
fix carried forward the mental model that produced the defect, and the model —
not the branch — was what needed testing:

| revision | the model it kept | what still retired |
|---|---|---|
| 1 | de-duplicate first-come | `[closed, expired]` → whichever came first |
| 2 | only RETIREMENTS can disagree | `[unresolved, closed]` → closed |
| 3 | only SURVIVING verdicts can disagree | `[expired-refused, closed]` → closed |
| 4 | only READABLE verdicts can disagree | `[unreadable, closed]` → closed |

The settled line is that **a verdict that failed grounding is still a CLAIM**.
Grounding decides whether a claim may act; it never decides whether it was
made, and which of a model's two answers happens to survive a grounding check
is not a tie-break between them.

**The fourth revision fixed the boundary underneath it, and the axis is
ATTRIBUTION.** To block a note's retirement, an item must be a claim ABOUT THAT
NOTE — and a malformed item can fall on either side of that:

| | | |
|---|---|---|
| **unattributable** | the handle names no note this tick asked about | cannot block, **by construction** — there is nothing to attach the doubt to |
| **attributable, unreadable** | the handle resolves, the verdict does not | **blocks** — a claim about this note that we cannot read |

`{"note": "1", "verdict": "clsoed"}` beside a well-formed `closed` is not one
answer plus noise. It is one answer plus an answer we cannot read, which is
«cannot tell which». **Unreadable is not absent**, and that is the same
reasoning as the settled line one level up: parseability, like grounding,
decides whether a claim may act rather than whether it was made.

The earlier rule let the second case fall through to the first, on the argument
that one malformed item should not veto a grounded decision. **That argument is
about cost**, and «this would be inconvenient» is the shape of reasoning that
produced the three rows above — the same move, one level down, made while
writing the fix for them. The cost is real and accepted: a model emitting noise
beside good answers holds notes open, and holding is the recoverable direction.

**And the first fix was blind to `unresolved`.** It grouped RETIREMENTS, and
`unresolved` produces none — so it never entered the grouping and
`["unresolved", "closed"]` was not a conflict at all: «I cannot tell» and «it
is closed» resolved to closed, in either order, retiring the note. That is
exactly the state this design says must never retire. Every parsed verdict now
enters the resolution, `unresolved` included, and its counter moved with it —
it was the one branch that never reached resolution, still being counted per
item by the fix whose own argument says otherwise.

### 11.3.5 `revisit_at`, `strikes` and `last_wake_revision_id` — the writer they never had, and the reset that read the wrong question

Found by a completeness audit against this node and [26](26-standing.md).

All three columns exist since migration `0026`, `SqlNoteStore` reads them into
`Note`, and **nothing anywhere sets any of them** — not the tag-path tools, not
the curator, not this pass. So `_is_due`'s `revisit_at` branch is
**structurally unreachable in production**: every note that reaches it today is
due by anchor date alone, and a note without an anchor date is never revisited.
§6.1 already recorded `strikes` as a column with a CHECK and no writer; this
records that the same is true of the other two, and that this adaptor now READS
one of them.

**The owner is this adaptor.** The note-tier spec's §9.6 names `NoteRevisitor`
as the first thing in the design that needs a no-progress breaker, and the
reason is structural: its unit of work is one note that may never change, so
nothing is consumed by working on it and a note can be due forever. The strike
is the substitute for the cursor every other pass has.

**Why it was not in that change:** the wake pass has to WRITE, and `DreamSink`'s
two verbs do not reach those columns — `propose` writes new rows and
`supersede` retires them. A store verb is needed, `nora/infra/sql/notes.py`
belonged to another session, and the standing protocol is to ask rather than
reach into it. Building it against an invented write path would have been worse
than leaving the branch honest.

**BUILT 2026-08-28: `record_wake`, on both stores**, with the comparison inside
the transaction and no CAS — a compare-and-swap would have the caller assert a
head it read earlier, and a tag-path revision arriving between the read and the
write is not an obstacle to record, it is the PROGRESS the reset exists to
detect. `strikes + 1` is computed by the database, because two ticks racing on
one note would otherwise both read N and both write N+1, and losing strikes is
how a breaker becomes decorative. The store reports; the caller decides what
happens at the ceiling, because «eligible for dateless retirement» is a
judgement rather than a fact about the row.

**And the first version asked the WRONG QUESTION, in both implementations at
once.** It read `is_tag_path_revision(head.established_by)` — who wrote the
CURRENT HEAD — where the rule is «did the tag path write anything since the last
wake». They differ on a sequence that is not exotic:

> wake at R5 → the room revises (R6, `tag:miles`) → the curator merges, or a
> debrief appends (R7, a run id)

The head is R7 and it is background-authored, so the check said «no progress»,
charged a strike, and **left the watermark at R5** — so that same R6 could never
count on any later tick either. A note the room is actively working, that a
background pass also touches each cycle, never resets and marches to the
ceiling. `merge` writes exactly such a revision, so a merged survivor's head is
background-authored BY CONSTRUCTION, and the curator and the breaker would have
fought each other from the day both shipped.

It is `tag_path_progressed_since_wake` now: a walk from the head back along
`parent_revision_id` to the watermark, in the domain, called by both stores. The
contract suite could not see the defect before because **both implementations
agreed** — the double was written from the same belief, not from the rule, which
is the double-diverges family with the sign flipped: agreement is not
corroboration when one author wrote both.

**The trap §9.6 states, carried here so it is not lost:** the reset must be
«head moved BY THE TAG PATH», never «head moved». If the revisitor's own writes
counted, a note it keeps rewriting resets its own strike forever and the breaker
is decorative — a self-referential reset is not a reset. Every revision carries
its writer, and strikes reset only on a tag-path revision: a human actually
engaging with the uncertainty.

This is also the pressure valve §11.3.3 defers to. `_covers` holds a note whose
room went quiet before its moment, deliberately and possibly forever; the strike
loop is what should eventually retire it as dateless. **Until the wake pass
exists, that deferral has nothing to defer to** — which is the strongest reason
to build it next, and it is stated here rather than left for the next reader to
notice.

### 11.4 What is deliberately NOT built

**`schedule_reminder`, and therefore the reaching-a-person half.** §3/D21
resolved that an adaptor may hold a verb that ENQUEUES even though it may not
hold one that DELIVERS — but **that amendment is in this document and not in
`nora.agent.dreamer.tool_subset_faults`**, which still refuses every tool
outside the read-only ceiling. Building it would mean widening what *every*
background adaptor may hold: a security-relevant change to a shared rule, and
not needed to retire a note. An empty tool set also makes the unit-safe
restriction moot rather than argued around.

### 11.5 The instrument, and the gap it exists to close

`supersede` returns 0 for an unknown target and **never raises**, so a
retirement that silently did nothing looks exactly like one that was never
attempted. `nora_note_set_statusments_total{because,result}` splits `applied` from
`no_rows` for precisely that. The shape generalises: **any verb that returns a
count instead of raising has this gap**, and the others have not been looked at.

`unresolved` is counted as a VERDICT, not as a refusal — «asked and could not
tell» is a real answer and often the right one, and collapsing it into the
refusals would let a model that had stopped engaging hide among the honest
holds.

### 11.6 The objective CAN be reworded — and why that was checked first

Checked on day one, before the adaptor was written, because the language tier
had just been parked for the opposite reason
([28](28-language-tier.md) §10.1).

**The trap there is not "objectives are pinned".** It is that *three* lifted
objectives — `room-language`, `room-language-verification`, `room-exchange` —
are pinned byte-for-byte against `tests/fixtures/dreamer_objectives_pre_move/`
until the instruction substrate's re-partition retires them. Anything touching
those three hits it; nothing else does.

A new objective has no pre-move fixture, so byte-identity is not a property it
can have. The guard runs the other way: an objective with neither a fixture nor
an entry in `NATIVELY_AUTHORED` is refused. `note-revisit` is declared there,
and the operator can revise its wording freely, forever.

**Generalised, because it will come up again:** when a tier's judgement depends
on operator-owned instruction text, verify on day one that the text can be
LANDED — not merely that it exists. A tier can be fail-closed by design and
unfixable by accident.

## 12. The curator, built — and the guard that was decoration

**2026-08-28.** `nora.language.note_curator.NoteCuratorAdaptor`,
unit-keyed, no tools, behind a third gate of its own.

### 12.1 `uses_watermark = False`, and it is not an omission

Every other adaptor reads an append-only log through a cursor. This one reads
**live state**: which notes collide on an anchor, and which anchors have passed.
Neither question has a *since when* — a duplicate is a duplicate however long it
has been one, and **retirement here is TIME-driven, not evidence-driven**. A
watermark would make the pass stop noticing an expired note the moment the
notebook went quiet, which is exactly when expiry matters.

**This supersedes the note-tier spec's §9.4**, which describes the curator as
cursor-shaped with a watermark. That is recorded here rather than left to be
rediscovered, because the spec is the older document and a reader arriving at it
first would take it for current. The spec's shape is right for a pass over
*things that were said*; this pass is over *things that are true*, and a
watermark over live state answers «what changed» to a question that never asked.

The consequence is stated rather than discovered: **the pass is self-limiting
for the actions it TAKES and not otherwise.** A group the model declines to
merge is re-selected every tick, forever, and occupies a `max_groups_per_tick`
slot — it is eligible, so it is inside the limit. A refused group does NOT
occupy one, because `limit` is applied to the eligible list and the refusals are
returned whole: the count that argues for populating `note_entity` must not
itself be bounded by a read budget.

### 12.2 The merge key is a RESOLVED anchor, and merge is therefore inert

§2 said an anchor is `(anchor_date, entity_set)` and that populating
`note_entity` is "the curator's real precondition, not its flag". **The first
implementation did not do that.** Eligibility read `note.anchor_entity_ids` —
the column `note_create` fills with whatever strings the model typed,
unresolved and unvalidated — while R-75, the release note, the metric help text
and three comments all said `note_entity`. Two sentences, one enforced.

**The raw column is wrong in both directions at once**, which is what settled
it rather than fidelity to the design:

| | |
|---|---|
| 「Grant 婚禮」/「Gabe's wedding」/「婚禮」 | three keys, **one occasion** — the merge §7.1 exists for still cannot fire |
| two unrelated notes both anchored 「wedding」 | one key, **two occasions** — they merge, irreversibly. The two-weddings failure, entered THROUGH the guard rather than around it |

A key wrong in both directions is not a weaker key. It is a different rule
wearing this one's name, and the guard's own label would have lied:
`no_entity_anchor` would mean «the model typed nothing» rather than «nothing
resolved».

So eligibility joins `note_entity`, and since nothing anywhere writes that
table, **merge is inert in production today** — every group refused and counted.
That is the correct outcome and it is stated in the release note rather than
implied: the retire half is what addresses «the notebook only grows», and the
refusal count is what says whether building the entity writer is worth it,
measured against the real notebook.

*The reading that would reopen it:* `nora_note_curator_groups_refused_total`
climbing, which is the case for the writer rather than for loosening the key.

### 12.3 The guard that was decoration, and the content loss under it

**`untouchable` — the set of notes a merge protects from being retired
individually in the same tick — went through three versions and only the third
does anything.**

| version | protected | what still happened |
|---|---|---|
| 1 | `g[1:]` of every selected group | a merged body plus `closed` on the survivor merged into it and then retired it — the whole occasion out of the index, the duplicate pointing at a retired row |
| 2 | every note of the groups that **actually merged** | every OTHER outcome left the group unprotected: an over-cap body, two different bodies, a body naming the wrong member, a lost CAS. `commit` then retired each member on its own verdict with `replaced_by=None` |
| 3 | every note of the groups the model **ASKED** to merge | — |

**Version 2 is the one that shipped, and it lost content on the ORDINARY
path.** The objective tells the model to return one entry per note it was
shown; `inputs`' own comment calls a colliding group whose occasion has passed
"the common case rather than an edge". So `moot` on both members beside a merged
body is the *expected* answer, not a contrived one — and any hiccup in the merge
then retired both. Retirement is terminal in practice: `note_read` resolves
against `standing()` and `note_search` filters retired rows, so the content the
merge existed to preserve was reachable only by direct database query, with
nothing pointing at either half.

**And version 2 was also unfalsifiable, which is why it survived review.**
Deleting the set entirely left all 29 tests green — including the one whose
docstring said it existed for this guard. It was unreachable by construction: a
successful merge moves the survivor's head, so the retirement's own head check
already refuses it, and the duplicates are already retired so `supersede`
returns 0. **A guard nothing can redden is not a guard**, and the test that was
supposed to prove it was asserting an outcome two other mechanisms produced.

The fix keys on the **claim, not the outcome**: `accept` emits a
`_MergeOffered` marker for every merge-shaped item naming any handle in a
rendered group, and `commit` builds `untouchable` from those. A wrongly-named
member still protects its group — the model got the handle wrong, not the
occasion. The merged groups are **not** unioned in: a merge only runs for a
survivor a `_MergedBody` named, and every item producing one also produces the
offer, so it is a strict subset. **A redundant term in a guard is how the last
version became unfalsifiable**, so it is left out rather than kept for comfort.

The survivor test now asserts `retire_stale` stays at **zero**, which is what
makes it able to fail. With the guard deleted the survivor still stands — and
`retire_stale` fires, which reads as *the tag path moved a note under the
curator*. That is a contention reading, and shipping one per merge would send an
operator to look for a race the curator had with itself.

**R-75's second clause was a non-sequitur and is corrected.** It justified
version 2 by saying computing the set unconditionally "let a refused merge
suppress its own group's retirements, ending a tick with the survivor retired
and the duplicate standing". Computing it unconditionally over WHOLE groups
protects the survivor too, so it cannot produce that outcome; the inversion was
version 1's `g[1:]`, and it was fixed by *including* `g[0]`, never by narrowing
to merged groups. Two independent fixes were written up as one.

### 12.4 The merged BODY is checked in code too

`group_on_anchor_key` refuses date-only merges because "relying on the model to
decline is self-attestation on a destructive operation" — and the same tick then
accepted the merged **body**, the thing that overwrites the survivor's head, on
the model's word alone. The only checks were non-blank and under the cap.

The objective's one instruction is that the merged body keeps everything every
note knew, so it **cannot be shorter than the longest body it is keeping**. A
body that is, is a summary — the single failure mode this verb has, and the one
nothing downstream can undo. It is a floor rather than an equality, and
deliberately not "at least the sum": two bodies about one occasion overlap, and
an honest merge is shorter than the concatenation.

### 12.5 Three smaller things, each of which had the shape of a bigger one

- **The merged-body conflict was not sticky.** Two different bodies for one
  survivor removed the first from the list — so a third item repeating it found
  nothing, appended itself, and **the merge landed with the very body the
  conflict had just been counted for**. The instrument said refused; it merged.
  `commit`'s verdict path had a sticky `conflicted` set all along; the more
  destructive path got the weaker mechanism.
- **The retirement head-check `read` was the one unguarded `await`**, added
  underneath a comment arguing that an exception escaping the loop "would
  discard `retired`, INCLUDING a merge that already committed". A connection
  blip on the first note lost the count for merges already in Postgres, and
  `merge_groups` reads live state, so those groups are gone next tick and the
  number is never recovered.
- **`bodies_read` was sticky AND undercounted.** Published after `inputs`' early
  return, so an idle tick — "the overwhelming majority" by the pass's own
  docstring — never wrote it and the gauge held the last busy tick's value. And
  it reported the expired count AFTER group members were subtracted out, while
  `anchored_before(limit=…)` had already fetched all of them, so the number
  could never reach the cap and the help text's stated reading was unreachable
  exactly when the cap binds. Now published unconditionally, including zero, over
  what the store FETCHED.

### 12.6 `[notes] max_body_chars` was a knob nothing read

Declared with a twelve-line docstring and a range validator, documented in
`config.example.toml`, cited by migration `0038` as a live setting — and
**nothing anywhere read it**. Enforcement was the module constant
`BODY_MAX_CHARS` in four places from the first commit.

Deleted rather than threaded, and the reason generalises to
[22-config.md](22-config.md)'s table: the operating cap's own argument is that
it is enforced **in the type**, where a refusal is reviewable and names the cap,
and a `__post_init__` cannot see config. Threading it would mean moving the
refusal out to a store boundary to make a knob true. **A config key with no
reader is the config-file form of a declared metric with no call site** — it
renders as a control and is a decoration, and an operator who changes it learns
nothing.

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
