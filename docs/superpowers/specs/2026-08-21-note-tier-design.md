---
kind: spec
title: "The note tier: tracked uncertainty, anchored, with a closing condition"
description: "Three tiers hold what Nora knows and none of them holds what she is UNSURE about. A fact is certainty with a warrant; Standing is a per-conversation reasoning position rewritten every turn and lossy by design; Honcho holds socially-inferred disposition about PEOPLE on an (observer, observed) axis that re-infers forever, which is why a wedding venue put there is at the mercy of a process built to keep changing its mind. The open plan — dinner on the 9th, three restaurants, nobody decided — has no home, so it is reconstructed from scratch or lost. This proposes a fourth tier: a note is (anchor, uncertainty, closing condition, body), workspace-scoped, its title and description injected on EVERY tag so awareness costs no tool call, its body read on demand. The tag path may only touch a note it read this turn, append-only with a CAS on the head revision; the Dreamer is the only thing that may act ACROSS notes — merge, retire, compact — and merge is keyed on the ANCHOR, never on topical similarity, so two weddings stay two notes. Nothing on the background clock speaks: a revisit reaches a person only by scheduling a reminder, which fires on the delivery path. One revisit adaptor serves every note; the note supplies the objective, never the procedure."
tags: [nora, spec, notes, uncertainty, dreamer, adaptor, reminders, events, workspace, postgres]
timestamp: '2026-08-21'
status: accepted
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
7. **Two Dreamer-side passes, not one** (§9): `NoteCurator` is cursor-shaped
   over new revisions and needs no tool loop; `NoteRevisitor` works one note at
   a time, is woken by that note's own due time, and **is blocked on the Dreamer
   runtime** (§15). Only the second one is blocked.
8. **Nothing on the background clock speaks** (§9.7). Either pass reaches a
   person only by scheduling a reminder, which fires on the delivery path under
   the existing guards. Four verbs — research, revise, schedule, tidy — and none
   of them is *speak*.
9. **One revisit adaptor serves every note** (§10). The note supplies the
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
| what it is | one line per note in the prompt | a GIN index over the head revision body |
| what it buys | **awareness** — she cannot search for what she does not know exists | **retrieval by content** |
| covers | title + description | the body |

`note_search` therefore searches **bodies, never titles.** Searching titles
would duplicate what is already in the prompt on every turn and would earn
nothing; searching bodies reaches what the index structurally cannot carry —
*"did anyone mention a hotel?"* across notes whose titles say nothing about
hotels.

**Trigram (`pg_trgm`), not `tsvector` — decided on what the live database
actually has.** Read back from `nora-db-1` on 2026-08-21 (PostgreSQL 18.4,
musl): `pg_trgm` is **available and not yet installed**; `zhparser`, `pgroonga`
and `pg_bigm` are **not available at all**.

That settles it, because the corpus is mixed English and Cantonese and
`to_tsvector('english', …)` **does not segment Chinese at all** — a note whose
body is 婚禮 would be unfindable while reporting `nothing_found`, which is the
`unavailable`-as-`nothing_found` failure dressed as a search result. The
CJK-segmenting extensions that would fix a `tsvector` are exactly the ones the
image does not carry, and building a custom image is the same packaging wall
DEC-129 hit with Apache AGE — a cost this feature cannot justify.

A trigram GIN index sidesteps segmentation entirely: it indexes 3-character
grams, so it matches CJK and English substrings alike with no language
configuration to be wrong about. What it gives up is **stemming** — "wedding"
will not match "weddings" — which is acceptable for a notebook holding tens of
notes and is a knowable limitation rather than a silent one. The migration
carries `CREATE EXTENSION IF NOT EXISTS pg_trgm`.

The index is maintained on the **head revision only**. Superseded revisions are
retained (§7.4) but not searched: a search that surfaced a body which is no
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

| | tag path (debrief, in-slot) | `NoteCurator` (§9.1) | `NoteRevisitor` (§9.5) |
|---|---|---|---|
| create an anchored note | yes, budget-capped | yes, from candidates | yes |
| revise a note **read this turn** | yes — append-only, CAS on head | yes | yes |
| merge two notes | **no** | yes | **no** — it holds one note |
| retire a note | **no** | yes | **no** |
| rewrite for coherence with the fact tier | **no** | yes | yes, for its own note |
| compact revision history | **no** | yes | **no** |
| sees | this turn; one note's body | the whole notebook, cheaply (§9.2) | one note, plus what it researches |

Merging *cannot* be in-slot, and this is a statement about what she can see
rather than a policy choice: it needs every note's full body, which is neither
in the prompt nor affordable to load mid-turn.

This is the propose/dispose split (reflection-continuity §5.2) applied to prose:
the fast path can add noise, never destroy.

## 9. The two Dreamer-side passes, and what triggers each

### 9.0 Why two, not one

An earlier draft described a single "curator" and was vague about its trigger,
because it was two different things wearing one name. They have different
trigger axes, different read costs, different loop-breaker needs, and — the part
that matters for sequencing — **different dependencies**:

| | `NoteCurator` | `NoteRevisitor` |
|---|---|---|
| unit of work | the whole notebook of one workspace | **one note** |
| trigger axis | a watermark over new revisions, plus the clock | that note's own due time |
| reads | a cheap projection; bodies only for what SQL already selected | one note, plus whatever it researches |
| tools | none — SQL plus at most one small completion | **a tool loop** |
| loop breaker | inherits the cursor's (§9.4) | needs its own (§9.6) |
| **depends on the Dreamer runtime** | **no** | **yes — blocked** (§15) |

### 9.1 `NoteCurator` — the trigger

The live loop's shape, with `consolidator_name="notes"` in the same watermark
table (`nora/app/runtime.py:497`):

```python
lag   = revisions in this workspace since the curator watermark
quiet = seconds since the last revision in this workspace
age   = seconds since the curator watermark last moved

fires = (
    lag > safety_lag                        # enough new revisions to be worth a pass
    or (lag > 0 and quiet >= quiet_seconds) # something changed, and the room settled
    or age >= max_age_seconds               # fires at lag == 0 ON PURPOSE
)
```

**The third condition carries more weight here than it does for language.** A
note whose anchor date has passed needs retiring even if nobody has touched the
notebook in a month — **retirement is time-driven, not evidence-driven.** That is
the same argument `LanguageConsolidator` already makes for decay (*"Decay is
TIME-driven ... without this a dormant room never fades a word"*). Expiry is the
note tier's decay, and the age condition is what performs it.

**A scope seam this needs and the repo does not have.** `dream_loop`'s `scopes()`
returns `policy.toml` **conversations**; the notebook is keyed on **workspace**,
so both `friends` rooms would tick the same notebook. It needs a
`workspace_scopes()` — `scopes()` deduplicated by workspace — or the pass does
duplicate work behind a lock. Small, but it does not exist today and the plan
must carry it.

### 9.2 What the pass reads

Not the notebook in a prompt. Two tiers of read, and the cheap one selects:

| step | reads | cost |
|---|---|---|
| merge **detection** | `(id, anchor_date, entity_ids)` for every note in the workspace | **SQL only — zero tokens** |
| merge **execution** | the full bodies of one colliding group (typically two) | bounded by collision size |
| retire, dated | `(id, anchor_date, closing_condition)` | **SQL only** |
| retire, dateless | `(id, strikes, head_revision_id)` | **SQL only** |
| promote | one body plus its revision history | one note |
| fact-coherence rewrite | one body plus the facts joined through `note_entity` | one note |

This is the payoff of anchoring merge on `(anchor_date, entity_set)` (§7.1):
candidate detection is a `GROUP BY … HAVING count(*) > 1`, not a judgement.

Bodies read per tick are **capped in config and counted when the cap bites**. A
silent truncation here reads as "nothing to do", which is the
`unavailable`-as-`nothing_found` failure at the pass level.

### 9.3 Criteria — what decides each action

| action | criterion | decided by |
|---|---|---|
| merge | identical `anchor_date` **and** identical entity set | **SQL. The model never decides eligibility.** |
| retire, dated | anchor passed **and** the closing condition met or moot | SQL for the date; the model for "met", one body |
| retire, dateless | `strikes >= K` **and** no tag-path revision since | **SQL** |
| promote to an `Event` | `when` resolves to an instant, `venue_text` present, **both traceable to a real message** (§7.3) | the model proposes a schema-validated object; **code verifies the trace and writes** |
| fact-coherence rewrite | a standing fact on this note's entities contradicts the head body | the model, one body plus the joined facts |

The discipline is the keystone's own: **the model proposes and code disposes.**
The model never writes and never decides merge eligibility.

### 9.4 The curator's loop breaker — inherited, not invented

`NoteCurator` is **cursor-shaped**: revisions are an append-only log with a
watermark, so every fire path ends in a watermark write and lag can only be
created by new revisions. That is the same structural property that makes the
live loop need no strike.

It therefore inherits both halves verbatim, and **must copy the second rather
than re-derive it**:

- batch present → advance the watermark to the last revision consumed;
- **batch empty → advance the watermark at its CURRENT value**, purely to reset
  the clock `age` reads.

Without the second, the `age >= max_age_seconds` condition — the one that fires
at `lag == 0` — is a **latch with no reset**: `updated_at` stays pinned, `age`
never falls back under the threshold, and a once-a-day pass reruns on every tick
forever. This is not hypothetical. `_dream_once` shipped exactly that bug and
its fix is labelled *"PLAN DEFECT found and fixed"* — **found during
implementation, not design.** It is written here so this implementer does not
have to rediscover it.

The re-entry lock (`running`, `nora_dream_skipped_total{reason="in_flight"}`) and
the capped-exponential failure backoff come free by living inside `dream_loop`.

### 9.5 `NoteRevisitor` — content-addressed, not scope-addressed

One note, woken by its own due time. `revisit_at <= now` for a note in this
workspace is a **fourth fire condition inside the curator's pass**, not a new
kind of reminder.

An earlier draft introduced a `revisit` reminder kind for this. That was
reaching for new machinery where existing machinery already fits: riding the
curator's pass reuses the clock, the per-scope lock, the backoff and the
per-scope `try/except`, and leaves `reminder` meaning exactly what it means
today — *tell a human something*. Nothing in the reminder tier changes.

### 9.6 The strike, and the reset that makes it real

`NoteRevisitor` is the first thing in this design that genuinely needs a
no-progress breaker, and the reason is structural rather than stylistic: **its
unit of work is one note that may never change.** Nothing is consumed by working
on it — no cursor advances, no backlog drains — so a note can be "due" forever.
The strike is the substitute for the cursor the other passes have.

State per note: `last_wake_revision_id`, `strikes`.

| on wake | |
|---|---|
| head == `last_wake_revision_id` | `strikes += 1`, **do not set a new `revisit_at`** |
| head moved | `strikes = 0`, `last_wake_revision_id = head`, set the next `revisit_at` |
| `strikes >= K` | the note stops waking; eligible for dateless retirement (§7.4) |

**The trap, and it is the whole point of this subsection: the reset must be
«head moved BY THE TAG PATH», not «head moved».** If the revisitor's own writes
counted, a note that the revisitor keeps rewriting would reset its own strike
forever and the breaker would be decorative — a self-referential reset is not a
reset. Every revision carries its writer (`established_by`, the column the fact
tier already has), and **strikes reset only on a tag-path revision** — an
external event, a human actually engaging with the uncertainty.

Stated plainly because the mechanism reads correct without the qualifier and
loops forever with it missing, which is precisely how the latch in §9.4 got in.

### 9.7 Nothing on the background clock speaks

> **Nothing either pass concludes reaches a person directly. It reaches a
> `reminder`, and the reminder fires on the delivery path**, under R-18's egress
> scan and the normal guards, at a time a human is actually there.

The Dreamer thinks at midnight; people read at 08:00. A reminder is the object
that spans that gap and it already ships with a fire path, recurrence and
per-requester caps.

An earlier draft had the Dreamer "mark a note ready" and wait for the next tag.
Superseded: that accepts latency to buy a property the reminder already provides.

Two consequences worth naming:

- `ReminderCaps.max_pending_per_requester` (20) becomes the **budget on how much
  either pass may nudge any one person** — a runaway is capped by machinery that
  exists rather than by a new counter.
- The requester question is `Event.created_by`'s, with the same grounded answer:
  the person whose message established the anchor. Never `nora`. Their quota is
  what a Dreamer-scheduled nudge spends, which is the correct incentive.

So the background passes have four verbs — **research, revise, schedule, tidy**
— and none of them is *speak*.

### 9.8 Two cadences

The **sweep** (the curator, on `dream_loop`'s clock) discovers what nobody
scheduled. **Revisits** are targeted and self-scheduled. Over time most work
should move to the second, which is the cheaper one: the notebook tells the
Dreamer when to think about what, instead of every scope being swept whether or
not anything moved.

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

### 11.1 In, and in what order

The tier splits cleanly at the runtime dependency (§15), so it ships in two
parts rather than waiting on the slower one.

**Part 1a — nothing blocked.** Domain types; migration `0026`, including
`CREATE EXTENSION pg_trgm` and the trigram GIN index on the head revision
(§5.3); the `<notes>` block;
`note_read` / `note_search` / `note_create` / `note_update` with R-7 budgets and
the §6.2 latch; the debrief's fourth output; the `note_candidate` miss cache.

**Part 1b — `NoteCurator`** (§9.1–§9.4), plus `workspace_scopes()` (this tier's
to build, §15.4), plus **`tests/contract/test_dream_sink_contract.py`** covering
the fact store and the note store both — the note store is the *second*
`DreamSink` implementation, so CLAUDE.md's contract-suite rule lands here. Its
sink refuses any payload that is not a note revision by `isinstance`, never
duck-typing (§15.4).

**Part 2 — blocked on the Dreamer runtime.** `NoteRevisitor` (§9.5, §10) and its
strike state (§9.6).

**1a is a complete, useful tier on its own**: she writes and revises notes on the
tag path, and the always-injected index makes her aware of them. 1b adds
housekeeping; Part 2 adds the notebook thinking about itself.

**No reminder-tier change in either part.** An earlier draft added a `revisit`
reminder kind; §9.5 replaced it with a fire condition inside the curator's pass.

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
which is this exact relationship already spelled out as one column. A new
reminder kind (§9.5). Building the Dreamer runtime — this document **depends on**
that work and does not do it (§15).

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
11. *"is the current Dreamer runtime already implemented?"* — checked rather than
    assumed, and the answer changed the plan. The **clock** is live
    (`dream_loop`), the **instruction substrate** is live, and the **agent
    runtime is not** — the keystone's §10 says *"Design only. No code"*, and the
    port today is `async def complete(self, *, prompt: str) -> str`
    (`nora/language/observer.py:111`): one shot, no tools. The draft assumed a
    tool subset with nowhere to plug in. §9.0 and §15 now state the dependency,
    and §11.1 splits the work so only the revisitor waits on it.
12. *"what is the trigger event / how much content / criteria / loop breaker?"* —
    §9 was intent rather than mechanism. Rewritten as §9.1–§9.6, in the live
    loop's own terms. The rewrite is what surfaced the strike's reset trap
    (§9.6) and retired the `revisit` reminder kind in favour of a fire condition
    (§9.5).
13. *"does the original Dreamer runtime also have the loop breaker?"* — it has
    **three protections and only one is a loop breaker** (§9.4), and it needs no
    strike because its unit of work is a cursor that always advances. That is the
    argument for why `NoteRevisitor` needs one and `NoteCurator` does not.

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
`nora_note_update_refused_total{reason="not_read"|"stale_head"}` ·
`nora_note_curator_bodies_read` (against its §9.2 cap) ·
`nora_note_strikes_total{result="struck"|"reset"}`

Chosen so the two opposite failures are distinguishable from outside, per
no-self-attestation:

- a live `note_index_entries` with a flat `note_reads_total` → the block is
  costing prompt budget and buying nothing;
- a climbing `candidates_total` with a flat `writes_total` → she recognises and
  does not act. **That is the inert-skill failure (§4.1), made visible.**
- `curator_total{action="none"}` climbing on every tick → **the §9.4 latch**,
  which is the one failure this pass has already shipped once elsewhere;
- `strikes_total{result="struck"}` climbing with `{result="reset"}` at zero →
  every revisit is finding nothing changed. Either the revisitor is not earning
  its cost, or the reset is mis-attributed to its own writes (§9.6);
- a `writes_total{kind="create"}` climbing while `{kind="update"}` stays flat →
  she is making new notes instead of revising the ones she has, which is the
  duplicate-notebook failure §6.5 splits the tools to prevent. Under a single
  `note_write` this series would not exist to watch.

## 14. Closed, and what remains open

### 14.1 Closed — a note write is silent

**Decided: no acknowledgement.** R-2 gives her one reply per tag and it belongs
to the person in front of her, not to her bookkeeping. The note appears in the
index on the next turn, which is where it is useful; a turn spent announcing it
spends the reply on the wrong thing.

The consequence accepted with it: a note written wrongly is not visible to the
room until someone reads the index entry. That is what
`nora_note_writes_total{kind}` and the curator exist for — the correction path is
`note_update` on a later turn, not an interruption on this one.

### 14.2 Closed — search is trigram, not `tsvector`

Settled in §5.3 against the live database rather than by preference: `pg_trgm` is
available on `nora-db-1` (PostgreSQL 18.4, musl) and every CJK-segmenting
alternative is not. The limitation that comes with it — no stemming — is stated
in §5.3 rather than discovered later.

### 14.3 Still open — owned elsewhere

**The revisit adaptor's tool budget and wall-clock timeout** (§10). This is not
this document's to size: it is a property of the **Dreamer runtime's** tool loop,
which does not exist yet (§15.1), and every adaptor on that runtime will need the
same treatment `web_fetch`'s `fetch_max_chars` got. It belongs to the runtime's
design, and the note tier inherits whatever that decides.

**Cross-tier alignment with the fact-tier session** — raised on 2026-08-21 and
**answered the same night**; see §15.4. Two follow-ups remain, neither blocking:
§15.5.

## 15. Status, and what this depends on

**Accepted, no code.** Design agreed with the operator across the exchanges in
§12 on 2026-08-21, and the cross-tier interface confirmed by the fact-tier
session the same night (§15.4). The implementation plan is the next artifact and
is not part of this document.

### 15.1 The dependency, read back from the code on 2026-08-21

| piece | state | evidence |
|---|---|---|
| the Dreamer's **clock** | **live** | `nora/app/runtime.py::dream_loop`, spawned at `nora/__main__.py:146` |
| three hand-written extractors | **live** | `LanguageConsolidator`, `ExchangeConsolidator`, `PortraitSynthesizer` |
| the **instruction substrate** | **live** | `nora/dreamer/objectives/*.md`, hot-reloaded, hashed into `instruction_sha256` |
| the **agent runtime** — tool loop, tool subset, schema-validated return | **not built** | the keystone's §10: *"Design only. No code."* The port today is `async def complete(self, *, prompt: str) -> str` (`nora/language/observer.py:111`) |
| `DreamSink` / `Proposal` in `nora.domain.ports` | **not declared** | absent from the file; the fact-tier plan's Task 3 is explicitly waiting on it |
| the **fact tier** | schema shipped, **dormant** | migration `0025` created `fact_entity` / `fact_candidate` / `fact`; there is no writer, `standing_facts` (Task 3) is unbuilt, and `TagContext.facts` defaults to `()` |

### 15.2 What that means for sequencing

**`fact_entity` exists, so the anchor has what it needs.** Notes do **not** wait
on the fact tier finishing: entity resolution and aliasing — the mechanism §7.1's
merge rule is built on — is already in the database. The only note behaviour that
needs *standing facts* is the fact-coherence rewrite (§9.3), one row in a table
of curator actions, deferrable on its own without touching anything else.

**The runtime is the real blocker, and it is now blocking three things at once**
— the fact tier's Task 3, `NoteRevisitor`, and any note pass written as a Dreamer
*adaptor* rather than a plain store writer.

That last clause is the trap this section exists to flag. `NoteCurator` (§9.1) is
described here as needing no tool loop, which is true — but if it writes through
a bespoke path while `DreamSink` is unbuilt, it becomes **a second
implementation of a port that does not exist yet**, which the fact-tier plan
already refused for exactly this reason: *"writing it before then would be
guessing at an interface someone else owns."* CLAUDE.md's own rule — no second
implementation of a port without a shared contract suite — points the same way.

So the safe decomposition is finer than §11.1's two parts:

| part | needs | blocked? |
|---|---|---|
| **1a** — tag-path notes: domain types, `0026`, the `<notes>` block, the four tools, the debrief output, the miss cache | nothing | **no** |
| **1b** — `NoteCurator` | `DreamSink` / `Proposal`, or an explicit decision that it is a plain store writer and not an adaptor | **yes, on the interface** |
| **2** — `NoteRevisitor` | the agent runtime | **yes** |

**1a is a complete, useful tier on its own**: she writes and revises notes on the
tag path, and the always-injected index makes her aware of them. What 1b adds is
housekeeping; what 2 adds is the notebook thinking about itself.

### 15.3 Both passes land on the Dreamer runtime — not a fourth extractor

**Operator decision, 2026-08-21.** `NoteCurator` and `NoteRevisitor` are
**adaptors on the Dreamer runtime**. Neither is written as a fourth hand-written
extractor beside `LanguageConsolidator` / `ExchangeConsolidator` /
`PortraitSynthesizer`.

This is a real constraint, not a preference, and it costs something: §15.2 shows
`NoteCurator` *could* be built today on the existing `complete(prompt) -> str`
port. Building it that way would ship housekeeping sooner and then require
rewriting it onto the runtime — and it would put a fourth bespoke extractor into
the exact shape the keystone exists to replace. **So 1b waits.** Only Part 1a is
genuinely unblocked.

### 15.4 Cross-tier alignment — ANSWERED by the fact-tier session (2026-08-21)

Four questions were put to the session finishing the fact tier. All four came
back the same night; this subsection records the answers, because §14.3 made the
document's finality conditional on them.

**1. `DreamSink` / `Proposal` — declared, and this is the shape.**

```python
async def propose(*, key: str, proposals: Sequence[Proposal], now_ms: int) -> Sequence[str]
async def supersede(*, key: str, target: str, because: str, source: str | None, now_ms: int) -> int
#   no delete verb — on the port or on any implementation

@dataclass(frozen=True)
class Proposal:
    payload: object          # the adaptor's own validated value
    instruction_sha256: str
    model: str
    established_by: str
```

Three properties the note tier depends on and does not re-decide:

- `propose` returns **ids, not a count**, so `supersede` can name what `propose`
  produced. Merge (§7.1) and retirement (§7.4) both need that.
- `now_ms` is on the **verb**, not on `Proposal`: one tick writes many proposals
  at a single instant, and a per-proposal timestamp would invite them to
  disagree.
- `propose(key, ())` → `()` is a **legal call meaning "nothing to write"**,
  distinct from not calling it at all, which is `unavailable`. That is R-27 at
  the port, and it is what lets `NoteCurator`'s no-op pass (§9.4) be
  *distinguishable* from a curator that failed to run.

**No delete verb, anywhere.** This confirms §7.4 independently: retirement is a
status change, never a delete, and the port makes the alternative unreachable
rather than discouraged.

**2. `payload` is `object`, and the runtime never introspects it.** The note
tier's payload — a revision body plus the head-revision id for §6.3's CAS — fits
with no change to the port. Provenance (`instruction_sha256`, `model`,
`established_by`) rides on `Proposal` rather than inside `payload` precisely
because those three are true of every adaptor and specific to none, which is what
keeps `nora/domain/ports.py` from ever naming `FactVerdict`.

> **Standing objection, recorded here so it survives this session.** If anyone
> narrows `payload` to a verdict type, the keystone's "one runtime, many
> adaptors" claim becomes false — and **this tier is the proof case that it must
> stay `object`.** Cite this paragraph.

**The cost of an opaque field, and the rule that pays it.** An `object` payload
means the type is known again only at the *sink*. So **the note sink
`isinstance`-checks its payload and refuses anything else — it never duck-types
it.** An opaque field is exactly where a wrong value travels a long way quietly,
and a sink that accepts whatever arrives is the `unavailable`-reported-as-`ok`
failure at the storage boundary. The fact sink does the same; this is a shared
convention, not a local preference.

**3. Migration `0026` is the note tier's.** The fact tier needs **zero** further
migrations — `0025` was amended in place (it has never been applied; the live DB
is still at `0024`) and its remaining tasks are store code against existing
tables.

`0025` did grow that night — `fact` gained `warrant_kind`
(`source|premises|observation|stated`), `fidelity`
(`tentative|proven|deprecated`), `supersede_reason`, an FK on `superseded_by`,
and a `fact_premise` junction table arrived. **`fact_entity` is unchanged**, so
this tier's anchor target (§2.3, §7.1) is stable and none of that reaches the
note tier.

**4. `workspace_scopes()` (§9.1) is this tier's to build.** The fact adaptor
needs the Dreamer runtime, which is unowned and unstarted, so nothing on that
side reaches `dream_loop` yet. Notes will need it first; the fact tier will use
this one when it gets there.

**And a fifth thing, unasked: the contract suite.** CLAUDE.md's rule — no second
implementation of a port without a shared contract suite — lands on whoever
writes the *second* `DreamSink` implementation. On current sequencing that is
**this tier**, so `tests/contract/test_dream_sink_contract.py`, covering the fact
store and the note store both, is **in Part 1b's scope** (§11.1) rather than
somebody else's.

### 15.4.1 What the contract suite may and may not assert

Settled with the fact-tier session, and it is a real constraint on the suite
rather than a note about it:

> **A contract suite that over-specifies is how a general port quietly becomes
> one tier's port.**

Shared — true of **any** `DreamSink`, so they belong in `tests/contract/`:

| assertion | why it is universal |
|---|---|
| there is no delete verb, on the port or any implementation | the port's own shape |
| `propose(key, ())` is legal and returns `()` | R-27 at the port: "nothing to write" is distinct from `unavailable` |
| `supersede` on an absent target returns `0` and does **not** raise | absence is a finding, not an error |
| a superseded row is **retained**, with the time it was superseded | supersession is a status change; §7.4 and `fact`'s own history depend on it |
| **a foreign payload is refused, not duck-typed** | the price of `payload: object` (§15.4). The *type* differs per sink; the *refusal* does not, so the assertion is parameterised on each store's own type. Without it the `isinstance` rule is a convention, and a convention is not a contract |
| **provenance round-trips**, with a **distinct value per field** | see below — this is the assertion the other five are blind to |

Both were proposed from this side and **confirmed by the fact-tier session**
(2026-08-21) as correct for `SqlFactStore` too.

**Why the round-trip is the strongest assertion in the suite, and why the values
must differ.** `fact` declares `instruction_sha256`, `model` and
`established_by` all `NOT NULL`, which invites the conclusion that its store
*cannot* silently lose them and the assertion is redundant. It is not. `NOT NULL`
catches **dropping to null**; it does not catch **writing the wrong value** — a
sink that stamped a constant on every row, or swapped `model` and
`established_by`, or hashed the wrong text, satisfies every column constraint and
passes all four of the shared assertions above.

So the suite writes a **different value into each of the three fields** and reads
all three back. With one repeated string a swap is invisible, and the assertion
becomes the thing it exists to prevent: a check that looks like rigour and
verifies nothing.

**A trap this creates for the suite, and how it is resolved.** "Distinct" cannot
mean `"a"` / `"b"` / `"c"`: `fact_instruction_sha256_check` demands 64 lowercase
hex, and `fact_model_is_named_check` demands non-empty after `btrim`. If the
shared suite hard-codes values valid for the fact store, it has quietly become
the fact store's suite — precisely the failure §15.4.1 exists to prevent.

**So each implementation supplies its own fixture factory** — a callable
returning a valid `Proposal` for that store, with per-field values the caller can
vary — and the shared suite asserts *behaviour* over whatever it is handed. Per-
tier validity rules stay with the tier; the suite stays about the port.

### 15.4.2 Where the factories live, and the one rule they must keep

**`nora/testing/`**, beside `InMemoryFactStore` and the other fakes. That is
where all 23 existing contract suites already get their fakes from
(`tests/contract/test_artifact_ingest_contract.py` is the canonical shape: fake
from `nora.testing`, real implementation from `nora.infra`), so this needs no new
convention.

**The rule, and it is enforced rather than remembered.** `pyproject.toml`'s
layered contract puts `nora.infra | nora.testing` on **one layer**, which makes
them independent: `nora.testing` may not import `nora.infra`. So a factory may
build only **domain** values — `Proposal` from `nora.domain.ports`, a
`FactVerdict` from `nora.domain.facts`, a note revision from
`nora.domain.notes` — and may never reach for `SqlFactStore` or the note store
to construct one. A factory that did would fail `uv run lint-imports`, which is
the desired outcome: the boundary is checked, not trusted.

**The cross-serving property this buys** (the fact-tier session's idea, and it
falls out of the rule above): each factory's `Proposal` carries a payload that is
**valid for its own store and foreign to the other**. So assertion 1 — a foreign
payload is refused, not duck-typed — is tested by handing each store the *other*
factory's output. It needs no synthetic "wrong type" fixture invented for the
purpose, and it stays honest as both payload types evolve. It works only because
both factories are domain-only and therefore both importable by the suite without
either reaching across the layer.

Agreed shape from the fact side:

```python
def fact_proposal_factory(
    *, instruction_sha256: str | None = None,
    model: str | None = None,
    established_by: str | None = None,
) -> Proposal
```

Each `None` fills with a **distinct valid default** — different digests per call,
never one string three times, so a field swap is visible. The note tier supplies
`note_proposal_factory` with the same shape and the same guarantee.

**Not shared, and deliberately so: successor cardinality.** With `replaced_by`
nullable at the port, `fact`'s biconditional stops being a universal rule and
becomes a **fact-tier** rule, enforced by `SqlFactStore` and its DB CHECK rather
than by the interface. **The note store will therefore legitimately do something
the fact store refuses** — supersede with no successor, which is retirement
(§7.4) — and the shared suite must permit it. Cardinality belongs in each
implementation's own tests.

`SqlFactStore` will state in its own docstring that **the port permits a null
successor and that store refuses one** — so nobody infers the port's shape from
its table. A DB CHECK reads as a universal rule by default, and this one is not.

### 15.5 What is still genuinely open

Two items, both for the implementation plan rather than this document, and
neither blocking:

1. **`supersede` has no field for the successor** — raised as an ambiguity in
   `source` and escalated by the fact-tier session the same night into the
   sharper finding: the agreed signature says which row dies and why, and has
   nowhere to name what replaced it. `fact`'s own
   `fact_supersession_is_timed_check` biconditional *requires* a successor, so
   that sink can satisfy the port or its own constraint, not both.

   **The note tier reaches the same defect from the other side**, which is what
   makes it a port defect rather than a fact-tier quirk: a merge (§7.1) that
   does not name the survivor is precisely the unauditable supersession `0025`'s
   docstring cites Graphiti's issue #1771 for — a merge computed and then thrown
   away.

   **And this tier is why the field must be nullable.** Fact supersessions
   always have a successor; note supersessions do not:

   | note action | successor |
   |---|---|
   | **merge** — two notes on one anchor become one | the survivor |
   | **retire** — the anchor passed, or the closing condition was met (§7.4) | **none.** A dinner that happened is replaced by nothing |

   So the shape argued for is `replaced_by: str | None` at the port, with each
   implementation tightening it — the fact store keeping its biconditional, the
   note store requiring an id for a merge and permitting null for a retirement.
   **Permissive at the port, strict at the sink**, the same direction as the
   `isinstance` convention in §15.4: a port that hard-codes one tier's
   cardinality is the same mistake as a `payload` typed to one tier's value. A
   non-nullable `replaced_by` would force this tier to either invent a third
   verb or mint a placeholder id to satisfy a signature — the
   "invent a value to satisfy a schema" move §7.3 already forbids.

   **If it is declined** and `source` remains the only spare field: `source`
   means *warrant* for facts, and this tier carries the survivor in `because`.
   Recorded here rather than left implicit, because at that point one field
   genuinely means two things across two tiers and the only defence is that both
   wrote it down.
2. **Whether promotion to an `Event` (§7.3) goes through a sink at all.**
   `DreamSink` writes into the tier that owns `key`; an event is a different
   store with a different scope (`conversation_id`, not workspace). Most likely
   promotion is not a `propose` and the curator holds a second collaborator —
   but that is an interface question the plan should answer explicitly rather
   than discover.

**With §15.4 answered, this document is final on behaviour.** It is final on
*interface* except for item 1, which the fact-tier session is settling tonight
with the substrate session before the declaration lands — and which changes how
the curator is wired, never what it decides.

`SqlFactStore.__init__` takes `(engine: AsyncEngine)` and nothing else — no
config object, no clock, no metrics collaborator, because `now_ms` arrives on the
verbs. Stated by that session as a **contract-suite affordance**, so the shared
suite in Part 1b does not have to reverse-engineer a constructor.
