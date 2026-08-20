---
kind: spec
title: "The note tier: tracked uncertainty, anchored, with a closing condition"
description: "Three tiers hold what Nora knows and none of them holds what she is UNSURE about. A fact is certainty with a warrant; Standing is a per-conversation reasoning position rewritten every turn and lossy by design; Honcho holds socially-inferred disposition about PEOPLE on an (observer, observed) axis that re-infers forever, which is why a wedding venue put there is at the mercy of a process built to keep changing its mind. The open plan — dinner on the 9th, three restaurants, nobody decided — has no home, so it is reconstructed from scratch or lost. This proposes a fourth tier: a note is (anchor, uncertainty, closing condition, body), workspace-scoped, its title and description injected on EVERY tag so awareness costs no tool call, its body read on demand. The tag path may only touch a note it read this turn, append-only with a CAS on the head revision; the Dreamer is the only thing that may act ACROSS notes — merge, retire, compact — and merge is keyed on the ANCHOR, never on topical similarity, so two weddings stay two notes. Nothing on the background clock speaks: a revisit reaches a person only by scheduling a reminder, which fires on the delivery path. One revisit adaptor serves every note; the note supplies the objective, never the procedure."
tags: [nora, spec, notes, uncertainty, dreamer, adaptor, reminders, events, workspace, postgres]
timestamp: '2026-08-21'
status: proposed
derives_from:
  - docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md
  - docs/superpowers/specs/2026-08-20-fact-tier-design.md
  - docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
  - design/09-decision-index.md
---

# The note tier

## 0. What this document is, and what owns what

This decides the **note tier**: a fourth durable tier holding the uncertainties
Nora has to keep track of, and the boundary between what the tag path may do to
one and what only the Dreamer may do across many.

It does **not** re-decide anything the tiers it sits beside already settled. The
fact tier's workspace scope (DEC-129), the propose/dispose split
(reflection-continuity §5.2), the Dreamer's adaptor shape (the keystone §8), the
reminder fire path and `Event`'s conversation scope are all taken as given and
are cited, not re-argued. Where this document appears to disagree with one of
them, the other one wins and this is the bug.

Origin: the operator's ask, 2026-08-21 — *"i want a note() module for nora - it
fill the gap between fact <> standing <> memory (honcho)"* — refined across six
exchanges whose corrections are recorded in §12. Two of this document's own
earlier drafts are recorded there as **wrong** rather than quietly dropped.

## 1. What this decides

1. **A note is tracked uncertainty**, not "structure facts can't hold" (§2). It
   is `(anchor, uncertainty, closing condition, body)` and every one of those
   four fields is load-bearing for a mechanism, not documentation.
2. **Workspace scope**, the same key as `fact` (§3). Not per-room. The room a
   note was born in is recorded as `established_in` and is a **routing key**,
   never a filter.
3. **The index is uncapped; the entries are capped** (§5). Every note's title
   and description is in the prompt on every tag. Awareness costs no tool call,
   which is the whole point; the body costs one.
4. **Read-before-write, and CAS on the head revision** (§6). A revision names
   the head it was written against, and the guard refuses a revision to a note
   not read this turn. **`note_create` and `note_update` are separate tools**
   (§6.5), because one tool with an optional `note_id` turns a forgotten
   argument into a silent duplicate.
5. **Merge is keyed on the anchor, never on topical similarity** (§7). Two
   weddings are two notes because their anchors differ, structurally — not
   because a curator judged them distinct.
6. **The tag path may only touch a note it can see the whole of this turn; the
   Dreamer is the only thing that may act across notes** (§8). That one line is
   the whole boundary.
7. **Nothing on the background clock speaks** (§9). A revisit reaches a person
   only by scheduling a `deliver` reminder, which fires on the delivery path
   under the existing guards.
8. **One revisit adaptor serves every note** (§10). The note supplies the
   objective; the sequence of tool calls is the model's, not the code's.

## 2. Why a fourth tier, and what the other three cannot hold

### 2.1 The gap, stated as a table

| tier | holds | its update rule | why the open plan does not fit |
|---|---|---|---|
| **fact** | certainty, with the source that warrants it | adjudicated three-state; superseded, never edited | nothing is established. There is no source, and "they might eat somewhere on the 9th" is not a world-truth |
| **Standing** | this conversation's reasoning position | rewritten wholesale every turn, **lossy by design** | a thread nobody mentions again falls out precisely when it needed to survive |
| **Honcho** | socially-inferred disposition, on an `(observer, observed)` axis | accumulates conclusions, re-inferred continuously | a plan has no `observed` peer and cannot be expressed on that axis at all |
| **note** | **an uncertainty Nora has to keep track of** | append-only revisions; closes when the thing resolves | — |

The operator's own framing, and it is the sharper one: Honcho is *an inference
engine over people*, so a deterministic detail placed there is at the mercy of a
process designed to keep re-inferring it. **A wedding venue does not want to be
re-inferred.**

### 2.2 The corrected version of `Event`

An earlier draft of this design claimed an event "asserts a commitment nobody
made". That was **wrong**: `EventStatus.PROPOSED` already exists
(`nora/domain/events.py:37`), so an event can be a proposal. The real division
is visibility and shape:

| | `Event` | note |
|---|---|---|
| scope | one conversation (`Event.conversation_id`) | the workspace |
| visibility | **invisible until `list_events` is called** | title + description in **every** tag |
| holds | title, when, venue, RSVPs, a reminder | the arrangement its columns have no place for |
| the 9 Sept dinner | "Dinner — 9 Sept", `proposed` | three candidates, who liked which, undecided |

**The note is the awareness layer; the event is the commitment machinery.** They
coexist and are linked (§7.3); they do not compete. An event Nora never thinks
to look for is an event that does not affect a turn, and nothing in the prefill
tells her to look.

### 2.3 The four fields, and the mechanism each one buys

Nothing here is descriptive. Each field is the input to a decision made
elsewhere in this document.

| field | buys |
|---|---|
| **anchor** — a date, and/or entities in `fact_entity` | merge eligibility (§7.1), mechanical retirement (§7.4), and the *inputs* to a revisit (§10) |
| **uncertainty** — what is open | the revisit objective (§10) |
| **closing condition** — what would settle it | retirement, promotion-readiness and the revisit's success criterion, **all three from one field** (§7.4) |
| **body** | the arrangement itself; read on demand (§5.2) |

**A thing with no closing condition is not a note.** It is either a fact
(already settled) or ignorance (nothing would resolve it). This is the
discriminator that stops the tier swallowing the other three, and it is checked
in the domain type, not in prose.

**A thing with no anchor is not a note either.** "We should hang out sometime"
has no referent anything could later be looked up by; it dies as a candidate row
nobody sees. This is what keeps the trigger in §4 from firing on everything.

## 3. Scope: the workspace, and why not the room

Same key as `fact`, for the reason DEC-129(1) already spent: DEC-105/R-49 hold
that rooms sharing a workspace **are one social unit** — that is the boundary
`send_to` relays across — so a note established in one room rendering into
another of the same workspace crosses no boundary R-31 guards.

The room-gated alternative was proposed and **withdrawn on evidence**. The leak
it was meant to prevent — a note born in a DM surfacing in the group — does not
exist, because `policy.toml` already separates them at the workspace key:

| workspace | rooms | people |
|---|---|---|
| `ridgeline` | Signal "Ridgeline", Discord "Ridgeline #discussion (test)", the `[dm]` path | `miles_novak` only |
| `friends` | Signal "Friends", Discord "Zone 42" | the same mapped humans, two places |

`policy.toml:16` states it in the operator's own words: *"the operator's own
rooms — never paired with Friends"*. **The workspace key already is the gate.**

A second per-room gate inside a workspace would also make `<notes>` the only
injected block in the prefill that disagrees with `<facts>` about what a
workspace means, leaving a future reader to work out which was right.

**`established_in` is kept, as a routing key rather than a filter.** It is what
`Event.conversation_id` is populated from at promotion (§7.3), and what a
`deliver` reminder fires into (§9). It never decides who sees a note.

What this gives up, stated plainly: within `friends`, a note raised in Zone 42
appears in the Signal group's index. Under R-49 that is the same people in
another place, which is the argument this repo already accepted for facts.

## 4. Recognition — how she knows, without writing a note every turn

### 4.1 The failure this section exists to prevent

`nora/skills/loader.py` shipped **inert** (DEC-48) and stayed that way. A
capability whose activation depends on the model spontaneously deciding to reach
for it mostly does not get reached for. `note_create` with a well-worded
description is that same bet, and it would lose the same way.

So no trigger below rests on her noticing an absence unprompted.

### 4.2 Revise is the easy one, for a structural reason

Every note's title and description is in her prompt on every tag. So *"this
conversation disagrees with a note"* is not a judgement about the void — it is a
**comparison between two things both present**: the injected index entry, and
what was just said. The note says the split is 50/50; Miles says 60/40. That is
decidable, and it is `FactVerdict`'s shape one tier up.

### 4.3 Create is the hard one

Recognising that something *should* exist is exactly the `nothing_found` /
`unavailable` confusion R-27 is written against: "no note covers this" and "I
did not think to look" are indistinguishable from inside a turn.

**The trigger is an open question with an anchor** — most commonly an unresolved
*forward* reference, which is the dinner case, but not only that.

| said | open? | anchored? | outcome |
|---|---|---|---|
| "haha yeah" | no | — | nothing |
| "the split is 60/40" | **no — settled** | — | a fact; the debrief already proposes facts |
| "we should hang out sometime" | yes | **no referent** | candidate row, no note |
| "dinner on the 9th, maybe these three places" | yes | date | **note** |
| "what are we playing next?" | yes | entities | **note**, no date (§7.4) |

Recurrence-gating was considered and **rejected**: in the operator's own
scenario the thread dies after one exchange and is never mentioned again, which
is precisely when the note has to exist. A "wait for a second mention" rule
fails the case the tier is for.

### 4.4 Where the judgement is made: the debrief, not mid-turn

The debrief already runs after every turn, off the reply path, already holds the
whole `TagTrace` including her reasoning, and already proposes facts
append-only. It gains one output: a note proposal or a note revision.

She never interrupts an answer to take minutes, and the judgement is made with
the finished turn in hand rather than halfway through one.

### 4.5 The miss cache

A `note_search` that returns nothing is written as a `note_candidate` row with
an `occurrence_count`, exactly `fact_candidate`'s negative-cache shape and for
exactly its reason. The **second** miss on the same ground is evidence rather
than a guess, and it is what would later justify building the corpus sweep this
document defers (§11.2).

## 5. The read path

### 5.1 The `<notes>` block — uncapped index, capped entries

Every standing note contributes one line: title + description. No elision, no
top-N, no relevance scoring.

**Why uncapped**, against the obvious prompt-budget objection: a note silently
missing from the index is invisible to her, and invisible is indistinguishable
from absent — the `unavailable`-reported-as-`nothing_found` failure this repo
keeps writing rules against. A relevance-scored subset has the same defect with
extra machinery.

**Why the entries are capped instead**: `title` ≤ 60 chars and `description` ≤
200 chars are enforced in the **domain type**, so an over-long description is
unrepresentable rather than truncated at render time. Truncation at render is
silent loss; a constructor that refuses is not.

The pressure valve is the curator (§8), which merges duplicates and retires
closed notes, so the index length tracks *live uncertainty* rather than history.

Block conventions follow its siblings exactly: volatile tail, never
`_context_header`'s cached prefix; escaped; `untrusted="true"`; no block at all
when nothing is standing. Like `<facts>` and unlike the room blocks, it carries
its **own** note rather than reusing `ROOM_BLOCK_NOTE`, because its content is
workspace-keyed and may have been established in a different room — reusing the
"this room's own messages" sentence would make it false in the one place the
model is asked to trust a sentence.

### 5.2 The body arrives on demand

`note_read(note_id)` returns the head revision's body and the head
`revision_id`. This is the skills-loader shape (progressive disclosure,
design/12 §1) with a writable store behind it.

**One payoff of the uncapped index: create needs no read.** Since every title
and description is already in front of her, whether an existing note covers this
ground is checkable from the prompt alone. That is real value bought with the
prompt cost §5.1 pays.

### 5.3 Two senses of "index", and what `note_search` is for

These are easy to conflate and an earlier draft did, leaving §14 with an open
question that this section closes.

| | the **injected index** (§5.1) | the **search index** |
|---|---|---|
| what it is | one line per note in the prompt | Postgres `tsvector` + GIN over the head revision body |
| what it buys | **awareness** — she cannot search for what she does not know exists | **retrieval by content** |
| covers | title + description | the body |

`note_search` therefore searches **bodies, never titles.** Searching titles
would duplicate what is already in the prompt on every turn and would earn
nothing; searching bodies reaches what the index structurally cannot carry —
*"did anyone mention a hotel?"* across notes whose titles say nothing about
hotels.

The `tsvector` is maintained on the **head revision only**. Superseded revisions
are retained (§7.4) but not searched: a search that surfaced a body which is no
longer the note's content would return something true-in-the-past as though it
were current, which is the same class of error `Fact.is_standing` and the partial
`idx_fact_standing` avoid by never rendering a superseded fact.

This is also what makes awareness and retrieval independently defensible. Drop
the injected index and she stops knowing the notebook exists; drop search and she
keeps every title but can never reach into a body she cannot name.

## 6. The write path

### 6.1 Append-only, with the head as the rendered body

`note_update` never mutates a body. It appends a **revision row**; the rendered
body is the head revision. Three properties fall out:

- an in-slot revision takes effect **immediately** — a wrong note is not wrong
  for another 300s waiting on a tick;
- it still **destroys nothing** — the prior revision is intact, so a bad
  revision is recoverable, which is the propose/dispose safety property in a
  shape that fits prose rather than triples;
- the Dreamer can **revert or merge** because history is present.

A propose-only debrief was considered and rejected on the staleness cost: the
correction *"actually the split is 60/40"* must land in the turn it was said.

### 6.2 Read-before-write, as a latch

A revision naming `note_id` is **vetoed unless `note_read(note_id)` succeeded in
this same turn.**

The index carries title and description only. That is enough to know a note
exists; it is nowhere near enough to revise it. A note's whole value is
arrangement — appending a revision without the body in front of her rewrites
structure she cannot see, destroying the one thing the tier exists to hold.

Mechanism: the guard layer already does per-tool latches and reserves this way
(`guards.py` — R-2's latch, the budget `reserve`). This is that shape, not a new
one. The veto text names the remedy: *read it first*.

### 6.3 Compare-and-swap on the head

`note_update` must echo the `revision_id` returned by `note_read`. If the head
moved — the curator merged the note mid-turn, or a parallel turn in the other
room of the same workspace revised it — the write is **refused as
`unavailable`** with «this note changed under you, read it again», never applied
on top.

R-27's three states at the store layer: the refusal is "I could not check", not
"there was nothing".

### 6.4 Budgets

`note_read` 3, `note_search` 2, `note_create` 1, `note_update` 2 in
`NATIVE_TOOLS`; none terminal — she still owes the room a reply. A revision
costs two calls (read + update), which is deliberate: the second one is what
makes the first mandatory.

`note_create` at 1 is the mechanical cap on note spam per turn — and it is a cap
the split in §6.5 makes meaningful, because under a single `note_write` an
accidental create would spend the *revision* budget and be indistinguishable
from one.

### 6.5 Why `note_create` and `note_update` are two tools

The two operations have **different preconditions**, and one tool cannot hold
both:

| | `note_create` | `note_update` |
|---|---|---|
| `note_id` | must not be accepted | **required** |
| `revision_id` | must not be accepted | **required** (§6.3) |
| read-before-write latch (§6.2) | does not apply — nothing to read | **enforced** |
| refusal modes | anchor missing, closing condition missing | not read this turn; stale head |

A single `note_write` with an optional `note_id` collapses that table, and the
collapse fails in one specific direction: **a call that meant to revise but
omitted `note_id` silently becomes a create.** The omission does not error — it
downgrades a guarded operation into an unguarded one and leaves a duplicate note
behind, which is exactly the noise the curator then has to clean up and exactly
the signal (`stale_head`, `not_read`) it destroys on the way.

Two tools make it unrepresentable rather than discouraged — DEC-78's move, the
same one `FactVerdict.not_covered` makes with `supersedes`. It is also the
convention already in the tool surface: `create_event` / `update_event`,
`remind` / `cancel_reminder`.

**Why this differs from DEC-33's decision NOT to split R-2's two branches.**
There the two branches produced the *same* model-facing sentence and one of them
was unreachable, so a second label bought a series that could not increment.
Here both paths are reachable on day one, take *different arguments*, and fail
for different reasons — so the split buys distinguishable refusals rather than
cardinality.

## 7. Across notes: merge, promote, retire

### 7.1 Merge is keyed on the anchor, never on the topic

Grant's wedding is `(entity: grant_carver, date: November)`. Blake's is
`(entity: blake_lindqvist, date: October)`. Neither component matches, so the two
are **structurally ineligible to merge** — not "the curator judged them
distinct".

This is the load-bearing rule of the section, because **topical similarity is
precisely the signal that would destroy the information here.** Two weddings
read almost identically as prose. A similarity-based consolidator merges them
and the venue-to-groom mapping is gone.

> **Consolidation is deduplication, never summarisation.** It merges two notes
> about the *same thing*; it never merges two notes that are the *same kind of
> thing*.

The case merge genuinely serves: Grant's wedding noted independently in the
Signal group and in Zone 42 — same workspace, same anchor, two notes.
`fact_entity`'s alias resolution ("Grant's wedding" / "Gabe's wedding" / 婚禮)
is what lands them on one anchor, which is the reason that table exists at all.

### 7.2 The operator's own judgement, encoded

*"In my perspective, they should remain as two separate notes."* §7.1 is that
sentence expressed as a rule the curator cannot have a different opinion about.

### 7.3 Promotion to an `Event` — a readiness check, not a judgement

The closing condition **is** the readiness check. A note whose closing condition
is "when time and place are known" is promotable exactly when `when` resolves to
a real instant and `venue_text` is present. No second rule.

Fail-closed, and this is the part that must not be softened: **every promoted
field must trace to an actual message in the note's revision history.** "Sometime
in November" yields no event — that is `unavailable`, not a guessed date. Nothing
is inferred to satisfy a schema.

Two fields need a grounded answer because the writer is not a person:

- **`Event.conversation_id`** — events are conversation-scoped and notes are
  not. The event lands in `established_in`. This is the column earning its keep;
  it turns out to be a routing key, not merely provenance.
- **`Event.created_by`** is a `person_id`. Attributed to the person whose
  message established the anchor, recoverable from the revision history. Never a
  synthetic author, never `nora`.

**Promotion does not consume the note.** The event takes what is settled (time,
place, RSVPs, a reminder); the note keeps what is not (the gift, who is driving,
the hotel). The note retires when nothing unsettled remains.

### 7.4 Retirement, including notes with no date

- **Dated anchor**: the date passes and the closing condition is met or moot →
  retire. Mechanical.
- **Entity-only anchor** — *"what are we playing next"* has no date, so expiry
  does not apply. It reuses the loop-breaker in §9.3: a revisit that finds the
  head revision unchanged is a **strike**; strikes stop the waking; a note that
  stopped waking and was never mentioned again is retired.

One mechanism doing three jobs — bounding the self-loop, ageing dateless notes,
and keeping an uncapped index from filling with zombies.

Retirement is a status change, not a delete. History is retained for the same
reason `Standing`'s traces are (reflection-continuity §14.6): a lossy step with
no retained input cannot be re-derived when it goes wrong.

## 8. The boundary, in one line

> **The tag path may only touch a note it can see the whole of this turn. The
> Dreamer is the only thing that may act across notes.**

| | tag path (debrief, in-slot) | Dreamer (tick / revisit) |
|---|---|---|
| create an anchored note | yes, budget-capped | yes, from candidates |
| revise a note **read this turn** | yes — append-only, CAS on head | yes |
| merge two notes | **no** | yes |
| retire a note | **no** | yes |
| rewrite for coherence with the fact tier | **no** | yes |
| compact revision history | **no** | yes |
| sees | this turn; one note's body | the whole notebook, the corpus, the fact tier |

Merging *cannot* be in-slot, and this is a statement about what she can see
rather than a policy choice: it needs every note's full body, which is neither
in the prompt nor affordable to load mid-turn.

This is the propose/dispose split (reflection-continuity §5.2) applied to prose:
the fast path can add noise, never destroy.

## 9. The Dreamer's outward verb is a reminder, and only that

### 9.1 Schedule, do not speak

The Dreamer thinks at midnight. People read at 08:00. A reminder is exactly the
object that spans that gap, and it already ships with a fire path, recurrence
and per-requester caps.

> **Nothing the Dreamer concludes reaches a person directly. It reaches a
> `reminder`, and the reminder fires on the delivery path**, under R-18's egress
> scan and the normal guards, at a time a human is actually there.

An earlier draft had the Dreamer "mark a note ready" and wait for the next tag.
That is weaker and is superseded: it accepts latency to buy a property the
reminder already provides.

Two consequences worth naming:

- `ReminderCaps.max_pending_per_requester` (20) becomes the **budget on how much
  the curator may nudge any one person** — a runaway is capped by machinery that
  exists rather than by a new counter.
- The requester question is `Event.created_by`'s, with the same grounded answer:
  the person whose message established the anchor. Never `nora`. Their quota is
  what a Dreamer-scheduled nudge spends, which is the correct incentive.

### 9.2 Two reminder kinds

| kind | fires into | may |
|---|---|---|
| **`deliver`** | a room, on the delivery path, R-18 egress scan | speak — existing behaviour, unchanged |
| **`revisit`** | the **Dreamer**, no room | research, revise the note, schedule, tidy. **Cannot speak.** |

A `revisit` that concludes something needs saying does not say it — it schedules
a `deliver`. The invariant in §9.1 survives: the loop exists, the mouth is still
only on the tag path.

### 9.3 The loop is bounded structurally

**A `revisit` may not schedule another `revisit` for the same note unless the
head revision changed since the last wake.**

Same head-revision id as §6.3's CAS. No judgement, no counter to tune. A
stagnant uncertainty wakes once, finds nothing changed, and stops waking; one
that keeps moving keeps getting attention. This is the anti-runaway property,
and §7.4 reuses it for retirement.

### 9.4 Two cadences

The **sweep** (the existing 300s tick) discovers uncertainties nobody scheduled.
**Revisits** are targeted and self-scheduled. Over time most work should move to
the second, which is the cheaper one — the notebook tells the Dreamer when to
think about what, instead of every scope being swept whether or not anything
moved.

## 10. One adaptor, and why there must never be a second

The operator's constraint, and it is the one that decides the implementation's
shape: *"I don't want or expect it to be hardcoded into Norty as a fixed
scenario. Otherwise, I wouldn't need the AI agent, right?"*

A revisit is a Dreamer **adaptor** in the keystone's sense — objective, inputs,
output schema, tool subset, sink — and nothing more:

| the adaptor supplies | from the note |
|---|---|
| **objective** | the uncertainty |
| **success criterion** | the closing condition |
| **inputs** | the anchor: the Honcho peers, the standing facts reached through `note_entity → fact_entity`, the note's body and history, the room |
| **tool subset** | `ask_memory`, `search_memory`, `web_search`, `web_fetch`, `note_read`, `note_search`, `note_create`, `note_update`, `remind` |
| **output schema** | a revision, optionally a reminder |
| **sink** | the note store |

**No sequence appears anywhere in code.** The worked example — *what are we
playing next* — would have it consult Honcho for what each person tends to
enjoy, join the fact tier through the anchor's entities, search for what has
released since, revise the note with candidates and why, and schedule a
`deliver` for a time they are all around. The wedding note and the dinner note
run **the same adaptor** with different inputs.

> **The acceptance test for this section: there is exactly one revisit adaptor.**
> The day a second is written for a *kind* of note is the day it became the
> hardcoded scenario engine this constraint exists to prevent.

What stays in code is only the boundary: it cannot speak (§9.2), it may only
revise / schedule / tidy (§8), it has a tool budget and a wall-clock timeout,
and web content is **distilled into a revision** rather than left prompt-resident
— the containment for the `web_fetch` token-exhaustion failure, which works here
because a note body is bounded and a fetched page is not.

## 11. Scope

### 11.1 In

Domain types; migration `0026`, including the `tsvector` + GIN index on the head
revision (§5.3); the `<notes>` block; `note_read` / `note_search` /
`note_create` / `note_update` with R-7 budgets and the §6.2 latch; the debrief's
fourth output; the `note_candidate` miss cache; the curator adaptor (merge /
retire / rewrite / compact); the revisit adaptor (§10); the `revisit` reminder
kind.

### 11.2 Deferred, with the evidence that would justify it

**The re-derivation sweep** — a Dreamer pass that spots the same explanation
being rebuilt from scratch across turns and proposes a note for it. It is the
trigger that catches the notes nobody thinks to ask for, and a missing note
announces itself as *repetition across turns*, which is an axis the tag path
structurally cannot see.

Deferred because it is a second adaptor with its own objective, watermark and
false-positive rate, and because the evidence that would size it does not exist
yet. **`nora_note_candidates_total{trigger="miss"}` climbing with a flat
`nora_note_writes_total` is that evidence**, and it cannot be collected until
notes exist.

### 11.3 Out

Any second revisit adaptor (§10). Per-room gating (§3). A polymorphic link
table: the relations are explicit nullable columns, following `Event.reminder_id`,
which is this exact relationship already spelled out as one column.

## 12. Corrections this design absorbed

Recorded because each inverted something a draft asserted.

1. *"what i care is how can nora acknowledge it need to create note and
   update/revise it"* — the scope question was not the interesting one. §4.
2. *"it need to read before update right?"* — §6.2 and §6.3 exist because of
   this. The draft had `note_write` taking a body with no read.
3. The **dinner scenario** — recurrence-gating would have failed the case the
   tier is for. §4.3.
4. The **two weddings** — merge on topical similarity destroys information.
   §7.1, and the operator's "they should remain as two separate notes" is now a
   rule rather than a preference.
5. *"if you dream at midnight, it will notify you"* — superseded the draft's
   "mark ready and wait for a tag". §9.1.
6. *"Note is the place where all uncertainties live"* — replaced "structure
   facts can't hold" as the tier's definition, and produced the closing-condition
   field that §7.3 and §7.4 both depend on. §2.
7. *"like a self looping?"* — §9.2 and §9.3.
8. *"I don't want it hardcoded as a fixed scenario"* — §10, which is the section
   the implementation is most likely to violate.
9. *"no need note_update?"* — caught a real defect. The draft had one
   `note_write` with an optional `note_id`, in which a forgotten argument
   silently downgrades a guarded revision into an unguarded create. §6.5.
10. *"does it need to be index? so it can also search_note()?"* — forced the two
    senses of "index" apart and settled what search is for: **bodies, never
    titles**, since the injected index already carries every title. §5.3, which
    closes what was §14's third open question.

Two of this document's own earlier claims are recorded as **disproved** rather
than dropped: that an event "asserts a commitment nobody made" (§2.2 —
`EventStatus.PROPOSED` already exists), and that room-gating was needed against a
DM leak (§3 — `policy.toml` already separates them at the workspace key).

## 13. Metrics

`nora_note_candidates_total{trigger}` ·
`nora_note_writes_total{kind="create"|"update"}` ·
`nora_note_reads_total{result}` · `nora_note_searches_total{result}` ·
`nora_note_index_entries` · `nora_note_curator_total{action}` ·
`nora_note_revisit_total{outcome}` ·
`nora_note_update_refused_total{reason="not_read"|"stale_head"}`

Chosen so the two opposite failures are distinguishable from outside, per
no-self-attestation:

- a live `note_index_entries` with a flat `note_reads_total` → the block is
  costing prompt budget and buying nothing;
- a climbing `candidates_total` with a flat `writes_total` → she recognises and
  does not act. **That is the inert-skill failure (§4.1), made visible.**
- a `writes_total{kind="create"}` climbing while `{kind="update"}` stays flat →
  she is making new notes instead of revising the ones she has, which is the
  duplicate-notebook failure §6.5 splits the tools to prevent. Under a single
  `note_write` this series would not exist to watch.

## 14. Open

1. **Does a note write get acknowledged in the room?** Proposed: no. R-2 gives
   her one reply per tag and it belongs to the person in front of her, not to her
   bookkeeping; the note appears in the index next turn.
2. **The revisit adaptor's tool budget and timeout**, which §10 requires and
   does not size. Needs the same treatment `web_fetch`'s `fetch_max_chars` got.
3. **Which text-search configuration the `tsvector` uses.** The corpus is
   mixed English and Cantonese, and `to_tsvector('english', …)` will not segment
   Chinese at all. Either accept that bodies are searchable in English only in
   v1 and **count it** rather than let it read as "nothing found", or carry a
   second column. Not a blocker for §5.3's shape; it is a blocker for claiming
   search works.

## 15. Status

**Proposed.** No code. Design agreed with the operator across the exchanges in
§12 on 2026-08-21; the implementation plan is the next artifact and is not part
of this document.
