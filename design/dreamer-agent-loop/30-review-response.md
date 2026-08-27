---
type: decision
title: "External Review — What Two Reviewers Found, and What Changes"
description: "An adversarial audit and an architecture review, run cold against the whole bundle. Four contradictions confirmed and fixed; the correction loop named as the missing centre; time wired into both the pipeline and precedence."
tags: [nora, review, audit, correction-loop, bitemporal, concurrency, lifecycle]
timestamp: '2026-08-24'
edges:
  - {dst: 00-overview, rel: corrects, provenance: asserted}
  - {dst: 21-substrate-contract, rel: corrects, provenance: asserted}
  - {dst: 29-authority, rel: corrects, provenance: asserted}
---

# External Review — Response

Two reviewers, cold, on the whole bundle: an **adversarial audit** (find the
contradictions and the edge cases) and an **architecture review** (is this the
right design). Non-overlapping briefs, and they converged on the same centre
from opposite directions.

Everything below is a decision. Two items genuinely need the operator and are
marked **OPERATOR** rather than guessed.

## 1. Four contradictions, confirmed and fixed

Each was verified in the files before being accepted, and each was introduced by
a later node correcting an earlier one **without sweeping the corpus**.

| | fixed in |
|---|---|
| *"No write tool, ever"* / *"act verbs are unreachable"* versus [27](27-note-dreamer.md) §3's `schedule_reminder` grant | [08](08-state.md) §12 now carries the **enqueue** carve-out explicitly |
| [18](18-flows.md) Flow B: *"the batch, **WITH speaker + time**"* versus [21](21-substrate-contract.md) §7.1's deliberately speaker-less `id: body` | [18](18-flows.md) corrected — the fact flow renders `id: body` |
| [24](24-embedding-seam.md) §7.5: *"pgvector would not"* allow per-kind dimensions — written before pgvector was adopted | reconciled: **one column per dimension**, routed by `kind` |
| [21](21-substrate-contract.md) §10: `stated` is *"the operator, and only the operator"* versus [29](29-authority.md) §2.0 giving `stated` to something a member typed in chat | §2 below — and the resolution is a whole missing mechanism, not a wording fix |

**The pattern is the finding.** Every one is a *stale restatement* left behind by
a correction, which is exactly [04](04-designed-vs-shipped.md)'s catalogued
failure committed by the bundle about that failure. A correction that does not
sweep for its own restatements is half a correction.

## 2. The correction loop — the missing centre

Both reviewers arrived here. The audit reached it as a contradiction about who
writes `stated`; the architecture review named it outright as the largest hole
and the thing to build **before** passes 3–5.

**The gap:** a member says *"no, Big Walk is a video game."* Today that message
enters a candidate queue ranked on recurrence, behind an adjudication pass with
an 8-hour floor — indistinguishable from any other chatter. Meanwhile the wrong
row keeps rendering.

**This is not an edge case for this deployment. It is the primary trust loop.**
A friends' bot's first real interactions with memory are corrections — and
pranks.

### 2.1 Decided

> **A member contradicting a standing row, in the room, addressed to Nora, is a
> first-class event: it marks the row `disputed` immediately, and the row stops
> rendering while disputed.**

- **Immediate effect is a *suppression*, not a rewrite.** The tag path may set
  `disputed`; it may not decide what is true. Propose/dispose is untouched — a
  correction proposes, and the flag it sets is the one thing a proposal may do
  synchronously, because *stopping* a wrong answer needs no adjudication.
- **Adjudication then runs at high priority**, ahead of recurrence, and resolves
  to a supersession or a dismissal.
- **`disputed` is visible to Nora**, so she can say *"someone's told me that's
  wrong, I haven't confirmed it yet."* Silence beats a wrong answer; a marked
  dispute beats silence ([19](19-between-tiers.md) §4).

### 2.1.1 The loop, step by step, on the rows that are live right now

```
1  RENDER      "big walk — 群內約定俗成嘅散步活動，可能係夜晚出去行街"
2  CONTRADICT  a member: "no, big walk is a video game"
3  RECOGNISE   a member contradicting a claim Nora just rendered
4  SUPPRESS    row -> disputed.  IT STOPS RENDERING THIS TURN.
5  PROPOSE     candidate: warrant = stated, asserter = that member
6  ADJUDICATE  high priority, ahead of recurrence. Tools: search the web,
               read the page -> House House, August 2026
7  SUPERSEDE   old row gets superseded_at / superseded_by -> the new gloss
8  RENDER      "big walk — the video game by House House"
9  COUNT       a counter moved at every step above
```

> **EXTERNAL REVIEW, 2026-08-27 — steps 3 and 6 are the two least specified
> parts, and they are the two the acceptance test actually exercises.**
>
> **Step 3 has no mechanism.** `RECOGNISE` appears exactly once in this bundle —
> the line above — and no node says *what does the recognising*. By elimination
> it is the reply-path model choosing to raise a dispute, which builds the trust
> loop on [02](02-instrument-blindness.md) §122's named pathology: *"she
> recognises and does not act."* **Recognition must be deterministic first**:
> quote-reply linkage (both platforms carry it), lexical overlap against the
> rendered rows' surface strings (`pg_trgm` is already installed), and
> addressed-to-Nora as a gate — with a model proposal as a *second* channel,
> never the only one. And the **fan-out must be specified**: one correction
> contradicts a lexeme row and a fact row simultaneously, so a dispute marks
> every standing row about that entity in **both** tiers.
>
> **Step 6's dismissal is undefined for the motivating case.** The loop resolves
> *"to a supersession or a dismissal"* and R-53 correctly holds that
> `not_covered` is never a contradiction — but a member correcting a
> *room-internal* claim (「打風 means X here」) produces a web search that returns
> `not_covered`, and nothing says what happens next. If that dismisses, **the
> disputed wrong row returns to rendering**, for exactly the class of claim the
> open web cannot adjudicate and that motivated this bundle. **Rule: dismissal
> requires an affirmative contradiction; a `stated` correction that is merely
> unconfirmed stands**, rendered with its asserter (§2.3 already builds the
> legibility half).
>
> **The `disputed` state machine is also unspecified where this loop creates the
> second writer.** §4 gives CAS for supersession, but `disputed` is a separate
> column written synchronously by the tag path while a background adjudication
> may concurrently dismiss or supersede the same row. Undefined: a dispute
> arriving on an already-superseded row; who clears `disputed` on dismissal and
> whether that is CAS-guarded against a second member's dispute landing
> mid-dismissal; a dispute on a row while promotion rewires its object.

**Steps 4 and 6 are the design.** Step 4 is **synchronous, on the tag path, and
a suppression only** — propose/dispose is untouched, because *stopping* a wrong
answer requires no judgement about what is true. Step 6 is **the Dreamer, with
tools, on its own clock**.

That split is what makes it safe. If step 4 rewrote, the tag path would be
deciding truth under social pressure — precisely what this bundle refuses
([26](26-standing.md) §1's impartiality argument, one path over).

**Step 9 is not bookkeeping.** This bundle's entire forensic case is that this
loop has **never closed once** in production. A counter at each step is how
anyone knows it did — and [09](09-measurement.md)'s rule says a change whose
effect cannot be read back is not done.

**Why this is the acceptance test for everything else.** Every other mechanism
in these 31 nodes — adjudication, warrant precedence, Standing folds, note
closing conditions, supersession in all four tiers — is a variation on one loop:
*the system believes something, someone says otherwise, the belief changes, and
it is visible.* The two wrong lexeme rows are the fixture; nothing better will
ever be available.

### 2.1.2 Where the loop applies — and the three places it does not

It is **not universal**, and the exclusions are what keep it small. The loop
needs three things: something **asserted**, **persisted past the turn**, and
**rendered**. That is exactly two tiers.

| tier | applies | why |
|---|---|---|
| **language** | **yes** | a gloss is asserted, persists, and renders every turn — the motivating case |
| **knowledge** | **yes** | a fact is asserted *with a warrant*; identical shape, same two states |
| **note** | **differently** | a note is a **question**, not a claim. You do not correct a question's truth. Its *body* is already corrected by the existing append-with-CAS path; a dispute means *the question is wrong or stale*, which maps to **retirement**, not supersession |
| **Standing** | **no suppression needed** | rewritten every turn by construction, so a correction reaches the next fold as input. Suppressing it would suppress something already scheduled for replacement |
| **Honcho** | **explicitly not** | it re-infers forever. A correction written there goes into a process built to change its mind ([29](29-authority.md) §3). The response is to **stop consulting it** for that question |

> **Anything asserted and persisted gets `disputed` plus priority adjudication.
> Two tiers, one new state.**

**Nothing in the domain carries a `disputed` state today** — verified — so this
is a boolean on `fact` and `room_lexeme`, not a subsystem.

**The three exclusions each fail a different premise**: notes are not
*assertions*, Standing is not *persisted* in the sense that matters, Honcho is
not *ours to correct*. Three different reasons is the check that the rule was
drawn from the tiers rather than around the answer.

### 2.2 This resolves who mints `stated`

`stated` is **a person deciding, deliberately** — through operator config, *or*
through a correction in the room. Not the extractor, ever.

**And the [29](29-authority.md) §2.0 example was wrong**: *"Grant is in Japan
until the 5th,"* extracted from a message, is **`source`-warranted** — the
message is the source — not `stated`. `stated` is reserved for *"I am telling
you this is so"*, which is what a correction is. The example is corrected there.

### 2.3 Abuse is a design input, not an afterthought

*"Nora, remember Grant owes me $500."* The substrate already carries the
answer: [21](21-substrate-contract.md) §7's **asserter junction** means every
`stated` row names who stated it, so a claim is always attributable and a
sender-scoped view can show what one person has been asserting. **A `stated`
fact renders with its asserter** — never as a bare claim — which makes a prank
legible rather than authoritative.

## 3. Time was schema-deep and pipeline-absent

The sharpest architectural finding, and it verifies: **`valid_to` appears
nowhere in [25](25-knowledge-pipeline.md)'s five passes.** Notes get a
revisitor, lexemes decay by age, and the tier where the world changes fastest
has no time-driven lifecycle at all.

> A bitemporal schema with one of its two times unwired.

### 3.1 Decided: a sixth pass — revalidation

> **UNSPECIFIED (external review, 2026-08-27).** This pass exists in this node
> and in [00](00-overview.md) and **nowhere else** — no trigger shape, no
> cadence key in [22](22-config.md), no metric in [09](09-measurement.md) — in a
> bundle whose [06](06-trigger.md) §3.1 singles out time-driven work as the
> shape that most needs a declared trigger. Spec it or defer it explicitly; a
> pass with no trigger definition is a polling bug in embryo.

Keyed on `valid_to` and on age, on the Dreamer's clock, mirroring the note
revisitor's shape:

- a fact whose `valid_to` has **passed** stops being standing — that is arithmetic,
  no model needed;
- a fact with no `valid_to`, older than a threshold, in a class where the world
  moves (a venue, a schedule, a status) becomes a **revalidation candidate**;
- expiry is **counted**, because a tier that silently keeps stale rows and one
  that has no stale rows look identical.

This is [06](06-trigger.md) §3.1's own principle — *time-driven work must fire
precisely when nothing has happened* — applied to the tier that skipped it.

### 3.2 Decided: precedence is validity-first, then warrant

[29](29-authority.md) §3's ranking ignored time, so a July `stated` would beat
October evidence. Corrected:

> **A claim that is not currently valid outranks nothing.** Validity is checked
> first; the warrant ranking orders what remains.

### 3.3 Decided: warrant rank is a prior, not an authority

Also from [29](29-authority.md) §3, and the reviewer is right that it overstated
itself. *The person beats the web about their own plans; the web beats the
person about a game mechanic.* That is per-predicate policy, not a global truth.

**So the ranking is (a) the adjudication prior and (b) the rendering order — not
a rule that decides truth.** Which is what the node's own §6 instrumentation
already assumed.

## 4. Atomicity, concurrency and recovery

Four findings, one shape: **the pipeline is specified as an ordered prose list
where it needs transaction boundaries.**

| finding | decided |
|---|---|
| **adjudication has no atomic boundary** — write the fact, die before retiring the candidate → re-adjudicated forever; retire first, die → the claim is lost | **one transaction**: fact write + candidate retirement + provenance rows + supersession. Promotion and embedding are *downstream and idempotent*, keyed off the fingerprint, never inside it |
| **concurrent supersession is last-writer-wins** — two adjudicators contradict the same row, both read it standing, both update | **compare-and-swap on `superseded_at IS NULL`**. The loser does not retry blindly; it becomes a candidate carrying both proposals. The note tier's CAS-on-head-revision is the precedent |
| **promotion races ingestion** and is `O(table)` per write at scale | promotion is **incremental** — re-evaluate degree only for objects the write touched — and **idempotent**, so a concurrent re-run converges. Alias accumulation is a set union, never a replace |
| **no durable lease** — a hung worker holds a scope forever; the in-flight set dies on restart | a **claimed-work row with an owner token and an expiry**, not a process-local set. [06](06-trigger.md) already names the DB unique index as prior art and stops short of the lease |

**And a corrected past is now defined.** A source revising *"away Aug 1–5"* to
*"Aug 2–6"* **closes the old validity interval and inserts a new row** —
`superseded_by` links them, so *"what did we believe on the 3rd"* and *"what was
valid on the 3rd"* are answered by the two different axes, which is the entire
reason the schema is bitemporal.

## 5. Retrieval defects

**The multi-hop fan-out cap could not work as written.** *"Ordered by the fused
score of the parent"* — every edge of one parent shares the parent's score, so
the sort ties and Postgres returns an arbitrary eight. **Corrected: the cap
orders on the child edge's own fused score**, computed before expansion; ties
break deterministically on `(predicate, object)` so a plan change cannot change
results.

**The dense-node guard amputates people.** Degree > 30 blocks expansion — and
people are the natural hubs of a social knowledge graph, so the structural
channel would refuse exactly where it is most valuable. **Corrected: the guard
is per-predicate, not per-node.** A person with 200 edges is still expandable
along `attending` or `works_at`; it is one *predicate* fanning out past the
bound that gets cut.

**Caps must bind at write time.** The config-plus-ceiling scheme only refused a
config *above* its ceiling. **Every writer validates against the current
operating value**, and lowering a cap below existing rows is a startup refusal
naming the offending rows — not a silent state where oversized rows satisfy the
`CHECK` and defeat the prompt budget.

**The terminal act is not self-certifying.** A model can call it early. **The
sink validates completion preconditions** — the envelope is accepted on its
contents, never on the fact that the call was made last.

## 6. The seam erases content class

The best cross-cutting finding. [11](11-disclosure.md) F3 refutes *"the read is
safe because the output is screened"* — and the shared retrieval seam then makes
note bodies, fact triples and eventually Standing rows **uniformly searchable
through one verb**, with `search_knowledge` granted to Nora and every adaptor
alike and no per-kind policy anywhere.

**Decided: the seam carries a per-`kind` disclosure policy at the port**, and a
caller is granted kinds, not the verb. A tier joining the seam declares its
content class alongside its text columns ([24](24-embedding-seam.md) §7.5's
declaration list gains a third field). Without it the seam is F3's leak rebuilt
deliberately, one level up.

## 7. Lifecycle — six things the design never had

| | decided |
|---|---|
| **cold start** | a **backfill pass** over existing history, using the watermark seeding the trigger seam already has. 963 messages are sitting there. Without it the verb returns `nothing_found` for weeks and Nora *learns it is useless* — a lesson she will not unlearn |
| **run-scoped rollback** | `established_by` already makes *"void everything run X wrote"* expressible; it becomes a supported operation. A subsystem whose writer is a model with no human in the loop needs an undo |
| **global cost governor** | a **fleet-wide daily ceiling** plus a degradation ladder — drop the dense channel, then lexical-only, then stop. Per-adaptor budgets bound one pass; nothing bounded the sum |
| **dormancy** | the floor **stretches after N quiet days** instead of holding at 8 hours. The design modelled accumulation and never modelled decline |
| **candidate TTL** | pending candidates **expire**. The negative cache handles *unsettleable*; nothing handled *stale*, so the queue accumulates the unresearchable forever |
| **erasure** | *"forget me"* is a supported operation. The asserter junction makes the knowledge-tier half easy; the design simply never asked. For a system that profiles friends, a deletion path is table stakes |

## 8. The two operator questions — both answered

**OPERATOR-1 — RESOLVED (operator, 2026-08-24): the language tier does NOT
fold in. It builds ON TOP of the knowledge substrate.**

A third option, and better than either the review offered. The reviewer's
objection was never *two stores* — it was **two supersession mechanisms for what
[20](20-knowledge-tier.md) §3 proved is one relation**. Building on top removes
the duplication without collapsing the tier.

| | fold in | separate (today) | **on top — decided** |
|---|---|---|---|
| entity identity | shared | **duplicated** | **shared** — a lexeme's `canonical_form` resolves to `fact_entity` |
| supersession | shared | **duplicated** | **one shared pattern**, one implementation |
| scoring + decay | **lost** | kept | **kept** — genuinely the language tier's own |
| render | merged | own block | own block |

**What it buys, in the live failure:** `Big Walk` and `big walk` resolve to **one
entity**, so they stop being two rows that cannot see each other — which is the
precondition for either ever superseding the other. And once a lexeme points at
an entity, the knowledge tier's edges about that entity become **evidence about
the gloss**: `Big Walk │ made_by │ House House` is what makes *"a walking
activity, maybe at night"* checkable rather than merely doubted.

**What stays the language tier's:** a gloss is a **rendering** concern with a
`live → fading → stale` lifecycle that facts do not have, and the
occurrence × log1p(speakers) score that [28](28-language-tier.md) §4 records as
invented there first.

> **Consumer of the substrate — not a peer store, and not a `kind`.**

[28](28-language-tier.md) is amended accordingly: it keeps its own storage and
its own lifecycle, and gives up its own entity identity and its own supersession
implementation.

**OPERATOR-2 — RESOLVED (operator, 2026-08-24): the loop first, but the cut is
not at the pass boundary.**

The reviewer's insistence was *close the loop before building passes 3–5*. The
wrinkle is that **§2.1.1's step 4 needs entity ids**, which are pass 3's output:
`Big Walk` and `big walk` cannot supersede each other until they resolve to one
entity. So the loop and pass 3 are not separable.

**The cut is inside pass 3, not before it:**

| | when |
|---|---|
| **entity ids + promotion** (pass 3) | **first** — the loop depends on it, and it is free only while the tables are empty |
| **the correction loop** (§2) | **with it** — `disputed`, priority adjudication, supersession, and a counter at each step |
| **embedding** (pass 4) | **after the loop closes once** |
| **retrieval / multi-hop** (pass 5) | **after** |

**Why this and not the reviewer's line:** passes 4 and 5 exist to *feed* the
loop — they make more things findable so more things can be corrected. Building
them before the loop closes is building the feed for a mechanism that has never
run. Pass 3 is different: it is not a feed, it is the **identity** the loop
needs to know that two rows are about one thing.

This reorders [00](00-overview.md) §7 accordingly.

## 9. What the reviewers endorsed

Recorded because a review that only lists faults is not calibrated: the
five-seam diagnosis and the instrument-first discipline, the trigger analysis,
propose/dispose, the negative cache, the read-before-write latch, the embedding
fingerprint, default-to-split, and the no-graph-database decision with its
stated reversal conditions — all confirmed as correct by the architecture
reviewer, which is also what makes §2 and §3 worth taking seriously.

## Status / Checkpoint

2026-08-27 — a third external review (Kimi, cold, whole bundle) found the two
joints the acceptance test exercises are the two least specified: **step 3 has no
recognition mechanism** and **step 6's dismissal is undefined for room-internal
claims**. Both are recorded in §2.1.1 with their fixes; the recognition gap is
[16](16-open-items.md) §12. It also named an option this bundle came within one
paragraph of and did not take — closing the loop **socially** rather than
analytically ([16](16-open-items.md) §12.1), which would remove the transport
dependency from the critical path entirely.

2026-08-24 — written after two external reviews run cold against all 30 nodes.
Four contradictions confirmed **in the files** before acceptance and fixed in
place; the rest decided here. Two items marked OPERATOR. The bundle's central
critique — *it measured a glossary failure and designed a knowledge graph* — is
answered by §2 and §3, not deflected: the correction loop and the revalidation
pass are what make the rest load-bearing rather than decorative.
