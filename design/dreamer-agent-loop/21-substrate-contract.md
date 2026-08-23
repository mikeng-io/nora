---
type: contract
title: "The Substrate Contract — The Shape Every Tier Writes Against"
description: "Canonical entity ids are the missing column; this is the contract they imply. What an entity is, who creates one, how it resolves, and the three questions that are genuinely open. Settled before the first write, not discovered during the fourth tier."
tags: [nora, knowledge-tier, entity, substrate, contract, resolution, provenance]
timestamp: '2026-08-22'
edges:
  - {dst: 20-knowledge-tier, rel: realizes, provenance: asserted}
  - {dst: 19-between-tiers, rel: depends_on, provenance: asserted}
---

# The Substrate Contract

## 1. Why this is one node and not four tiers' worth of discovery

[20-knowledge-tier.md](20-knowledge-tier.md) establishes *that* canonical entity
ids are the missing column. It does not say what an entity **is**.

That gap is the same shape as the one this whole bundle documents. The tool set
went from eleven to four because three documents and one mechanism never met
([04-designed-vs-shipped.md](04-designed-vs-shipped.md)); nobody decided it, and
after the fact it was indistinguishable from intent. A substrate that four tiers
write against, whose rules are settled one tier at a time, produces the same
outcome by the same route — except the artifact is rows, and rows are expensive
to correct.

**Zero rows exist.** Every question below is free to answer today and a cleaning
project at a thousand edges. That asymmetry is the entire argument for settling
them here.

This node states the contract. Five of its questions are answered; **three are
open and are marked as open** rather than resolved by whoever writes the sink
first.

## 2. An entity is an identity, not a claim

The fact tier states that a fact is *"never a claim about a person (which is
Honcho's axis, not this one)."* That was read as excluding people from the
substrate. It does not.

> **A claim restriction is not an identity restriction.**

"Grant's wedding is on the 9th" is not a claim about Grant's disposition —
it is a claim about an event, with Grant in object position. The restriction
bites on **predicates about a person's character, preference, or standing**,
which stay Honcho's. It does not bite on a person being a node.

And the substrate does not work otherwise. The note tier anchors on
`(anchor_date, entity_set)`, and a large share of notes are about what a person
is doing. If people cannot be entities, notes cannot anchor on the thing they
are about, and `note_entity`'s foreign key into the shared table is decorative.

**The rule that keeps one answer to "who is Grant":**

> A person entity is a **projection of an existing `person_id`**, never an
> identity minted here.

Identity stays in `identity.toml` and Honcho. The entity row mirrors it and
exists only to hang edges on. Two consequences follow:

- A person entity is created **by the identity mapping, not by extraction.** An
  extractor that names an unmapped person does not get to mint them — that is
  the `nora_unmapped_senders_total` case, and it is a config gap, not a
  discovery.
- **Person-claims still route to Honcho.** The disclosure rules that govern
  identifiers are untouched by this; an entity's existence is not disclosure,
  and what may be *said* about it is decided at egress exactly as before
  ([11-disclosure.md](11-disclosure.md)).

## 3. There is no type field

An entity has `canonical_name` and `aliases`, and no kind. That is deliberate.

The case for a type is resolution and filtered traversal — mature systems
adjudicate a merge using type alongside name. The case against is that a
**free-text type fragments exactly like a free-text subject**, one axis over,
and with no natural stopping point: a game, a video game, a co-op game, a thing.
The vocabulary has to be closed by someone, and nobody has a principled basis for
closing it at four rows of live data.

The distinction that would actually have earned a type — **person versus
thing** — is delivered by §2 without one. A person entity is the projection of a
`person_id`; a thing entity is not. That is **a nullable column and a source, not
a taxonomy**, and it is the only branch either resolution or disclosure needs.

**The escape hatch, stated so it is not reinvented as an open field later:** if
resolution proves unreliable in a way a type would fix, the answer is a **closed
set**, added deliberately, with the same parse-time enforcement the exchange tier
already uses. Never an open one.

## 4. Entity creation is a side effect of writing an edge

The model proposes; code disposes. There is no write verb, and adding one for
entities would be the first — which is the wrong shape and unnecessary.

> **The sink upserts the entities an edge implies, in the same transaction that
> writes the edge.**

No new proposal type, no new sink, no entity verb in any adaptor's tool set. An
adaptor proposes an edge; the sink resolves each end, creates what is missing,
and inserts. This is `MERGE` semantics, which is what a graph does natively and
what the relational store has to do explicitly.

**Atomicity is not a separate question.** One transaction, or the store can hold
an edge whose endpoints do not exist and an entity nothing references. The
`UNIQUE (workspace, canonical_name)` constraint makes the upsert safe under
concurrent proposals from two adaptors — which will happen, because
[19-between-tiers.md](19-between-tiers.md) routes writes from more than one tier
at the same entity.

**What this forecloses:** an entity cannot exist before an edge needs it, except
by the identity projection in §2. There is no entity-creation pass, no
pre-population, no vocabulary seeded ahead of use. The substrate grows only where
something actually asserted a relation.

## 5. Resolution runs in two places, and the split is the point

The extractor's objective already says resolution is *not its job*. Nothing else
claims it, which is how it became *"owned by nobody"*
([00-overview.md](00-overview.md) §8). It has two halves and they belong in
different places.

| | where | what it does | cost |
|---|---|---|---|
| **deterministic** | in the sink, on every write | normalise, then look up `canonical_name` and `aliases` | free, no model |
| **adjudication** | a Dreamer pass | decides genuine near-misses the deterministic pass surfaces | a run, on the Dreamer's own trigger |

The order is load-bearing and it is the shape the mature prior art uses: **the
cheap exact pass runs first and handles the overwhelming majority**; the model is
reached for only where the deterministic answer is ambiguous.

**Adjudication defaults to split.** Two entities wrongly kept apart is a missing
edge, visible as a thin neighbourhood, and fixable by a later merge. Two wrongly
merged is a corrupted node that has already absorbed both sets of edges, and
unmerging it means knowing which edge came from which — **which is exactly the
provenance question §8 leaves open.** Until that is answered, merging is the
irreversible direction, and the default has to respect that.

This makes resolution a **named owner**, which closes the "owned by nobody" item
in [00-overview.md](00-overview.md) §8 — not by inventing a mechanism, but by
saying which existing seam each half lives in.

## 6. The object is always a literal; entity-hood is derived later

The CHECK enforces exactly one of `object_entity_id` / `object_literal`, and
nothing said which to use. The question dissolves rather than resolves:

> **The sink always writes the literal. Entity-hood is a promotion computed
> later, by code, from the store's own contents.**

Nothing chooses at write time — not the model, not the sink, not the author.

### 6.1 The promotion rule

An object string is promoted to an entity in a workspace **iff**:

- it survives a **literal veto** — it does not parse as a date, quantity, price,
  duration or time-of-day (including spelled-out numerals), does not begin with a
  negation, is not a URL, and is under a length cap; **and**
- **it appears in at least one other edge position in that workspace** — as some
  standing fact's subject, or as another standing fact's object.

Counted over standing facts only, never candidates: unadjudicated noise must not
mint nodes. Matching is **exact after normalisation** (NFKC, strip, casefold) —
never fuzzy, for the reason [16-open-items.md](16-open-items.md) §4.1 already
measured: embeddings rank a literal misreading *nearer* than a true alias, so no
threshold separates them. Under-merge, per §5.

**Degree is the whole criterion.** A node of degree one is a string with extra
steps — there is nothing to traverse to. `made_by "House House"` is a literal
while it is the only House House claim, and both rows promote the moment a
second one arrives. **Traversal becomes possible exactly when it becomes
meaningful**, which is not a compromise but the correct time.

### 6.2 Why this shape and not the model's judgment

Four mature systems were checked, and **not one lets the extraction model decide.**
Two decide **per predicate in a human-written schema**; two decide **per value,
by duplication or query shape** — the property-to-node promotion trigger being
literally *"many nodes share the same property value."*

The repo already agrees twice. The extraction objective forbids canonicalising —
*"Getting one name to line up with another is a separate job and it is not
yours"* — and the adjudication objective pins the raw strings as the **join
key**: *"the words are how it is matched back."* A model that emitted an entity
id would break that join. Propose/dispose is not a preference here; **the shipped
design already forces it.**

And there is repo precedent for promotion-by-arithmetic: the language tier
promotes a lexeme on occurrence and speaker counts. This is the same mechanism
one tier over.

### 6.3 Reversibility, measured rather than assumed

Both directions were run against the real migrated schema: promotion is one
`INSERT … SELECT … GROUP BY` plus one `UPDATE`; demotion is one `UPDATE` that
restored every original string exactly. **One statement each, at any size.**

This matters because it **inverts the prior art's default.** RDF and Wikidata
say *default to a node*, because for them promotion is a non-local rewrite —
an artifact of immutable IRIs and un-migratable per-property datatypes. Nora has
a relational store and the entity table already sitting there.

> **Default literal, promote on evidence** — the opposite of the standard advice,
> for a reason that is specific to this store and was measured, not inherited.

### 6.4 Three shipped defects on this path

Latent, not live — nothing writes an entity object today. Each becomes real with
the first one, and all three are cheap now.

| | what | consequence |
|---|---|---|
| **the id column is not an id** | `object_entity_id` is `text`; `fact_entity.id` is `uuid`; **there is no foreign key between them**. The only FK into the entity table is the note tier's | the "node reference" half of the CHECK currently references nothing — it is a second free-text column wearing an id's name |
| **the negative cache breaks silently** | dedup compares `object_literal` **only**. An entity-object fact has it NULL, and `NULL = 'x'` is NULL | the claim is never deduped, so it is **re-adjudicated every tick forever** — and because it then reads as contradicting its own candidate, it ranks *first* each time. This is precisely the recurrence the candidate table exists to stop |
| **two read paths print the raw column** | the render paths fall back to emitting `object_entity_id` verbatim | a bare uuid rendered into Nora's prompt |

The second was **reproduced with controls** against the real schema: same triple
with a literal object dedupes; with an entity object it stays pending; a
no-standing-fact control also stays pending. The in-memory fake shares the
comparison, so **the contract suite would not have caught it** — CLAUDE.md's
fake-answers-everything-the-same-way rule, landing on a fake that mirrors the
production bug faithfully.

Note the shape of the third row against
[02-instrument-blindness.md](02-instrument-blindness.md): the tier declares
`nora_fact_entity_unresolved_total` and **never increments it** — one call site,
its own declaration. The single instrument for the entity problem cannot observe
the entity problem. That is a seventh blind layer, found while writing the
contract that depends on it.

### 6.5 One question for the operator, cheap only at zero rows

The CHECK forces the surface string to be **destroyed** on promotion — a row that
said `"house house"` returns as the canonical `"House House"`. The alternative:

> Replace the exactly-one CHECK with `object_literal IS NOT NULL`, making
> `object_entity_id` an **additive, nullable resolution pointer**.

**For:** every shipped read path keeps working, the dedup break disappears,
promotion becomes non-destructive, and it is what the mature systems do — they
carry the source's original wording *beside* the resolved link precisely so
resolution never eats the evidence.

**Against:** the schema loses its "exactly one object" invariant, and a row no
longer says on its own whether the object *is* the node or merely resembles it.

**This is a decision, not a recommendation.** Both are defensible; only one is
free today.

## 7. The asserter is recorded; the model still never sees a speaker

`established_by` holds *"the run that produced it"* — the tick. Nothing anywhere
records **who claimed it**. The question is whether that is recoverable later or
has to be a column.

### 7.1 It is lost twice, and only one loss is argued

**First, at the prompt.** The batch is rendered as `id: body` — the row carries
`sender_id` and `sender_name` and neither is emitted. This is deliberate and the
argument is written down:

> The speaker is deliberately absent. The objective's first exclusion is
> "anything about a person in this room", and naming the speakers is how a batch
> of ordinary chat starts looking like a batch of claims about the people in it.

**That argument is sound and this contract does not touch it.** It is about what
the *model* is shown — name the speakers and an extractor tuned to find claims
starts finding them about the people named.

**Second, at promotion — and this one is argued nowhere.** A candidate carries
`observed_in`, the message ids each claim came from; the objective's own words
are *"which turn each came from"*, and `accept` validates a returned
`message_id` against the whole batch. **`fact` has no such column.** Not
`observed_in`, not a candidate id, not a message range — verified against the
table definition. The link is complete on the candidate and absent on the row it
becomes.

So: **the human is recoverable today only for a claim that has not yet been
adjudicated.** Promotion is the loss point, and it is silent.

### 7.2 The two losses have different fixes, and that is what unblocks this

The prompt loss must stay. The promotion loss is a join **that happens entirely
in code**:

> `message_id` → `sender_id` → `person_id`, at the sink, from the batch the
> adaptor already holds.

The model contributes nothing but the `message_id` it already returns and that
`accept` already validates. **The objective is unchanged, the prompt is
unchanged, the model still never sees a name.** The omission argument is
satisfied in full, because it was always about the prompt.

This is why the answer is not "add a speaker to the batch render". That would
trade a real safeguard for a fact the code can derive without asking.

### 7.3 Why it cannot be derived later instead

The provenance standard licenses exactly one direction: attribution follows from
*generation by an activity that was associated with an agent*. Nora has the
generation half — `established_by` is the tick — and **no association to any
human agent at all**; the only agent on that path is the model. So the one
derivation the standard permits is unavailable here **by construction**, and
attribution is the lossy shortcut rather than the recoverable one.

Concretely: given a fact row, there is no path to a person. Not a hard path — no
path.

### 7.4 Shape: a junction table, and the repo already argued for it

Not an array column. **The repo has already made this exact choice and written
down why** — `fact_premise` exists rather than an array because *"answering
'which facts depend on this one' is an unindexable array-containment scan with
the array, an indexed lookup with this table."*

The asserter question has the same reverse direction — *what did Grant tell
us* — and therefore the same answer. `observed_in` being an array on the
candidate is the shape `fact_premise` was created to undo, one table over.

A junction also gets the arity right without a second decision: **one claim
asserted by three people is three rows**, which is exactly the distinction the
whole question exists to preserve. Corroboration and emphasis stop being the same
observation.

### 7.5 What this unblocks, and what it is not

- **§5's merge default.** Unmerging two wrongly-merged entities requires knowing
  which edge came from where. This is that. The dependency runs
  *provenance → resolution*, not the reverse.
- **Corroboration versus emphasis** — one person three times, or three people
  once. Presently indistinguishable, and the extraction filter ranks on
  recurrence.
- **A sender-scoped read** for [19-between-tiers.md](19-between-tiers.md) §5's
  query verb.

**It is not a disclosure change.** Recording who asserted a claim is not
permission to say so; egress is governed exactly as before
([11-disclosure.md](11-disclosure.md)). And it is **not Honcho's axis** — Honcho
holds *what someone is like*, re-inferred forever; this holds *who said this
sentence*, which is a fixed historical fact and never re-inferred.

### 7.6 The prior art says this is the common failure, not an exotic need

Two independent confirmations, and neither offers a better mechanism — they offer
the same bug:

- The nearest comparable product accepts a **typed speaker field**, serialises it
  into `"name: text"`, and then pays a model to parse it back out — *"always
  extract the speaker."* Structured identity → string → re-inferred identity.
  That is CLAUDE.md's *"interpreting twice is how two copies drift"*, shipped.
- The largest public knowledge graph keeps **both** shapes deliberately: a
  per-assertion statement node carrying its source and a per-assertion trust
  rank, published alongside a flat "truthy" edge with no provenance at all.
  **`fact_candidate` is the statement node and `fact` is the truthy copy** — the
  prior art keeps both and links them; this tier keeps both and does not.

## 8. Predicates stay open — normalised, seeded, counted

`predicate` is free text with a length cap, and the objective asks the model to
reuse predicates it has already used **in this batch** — batch-scoped, so nothing
carries across runs. The choice was open / closed / open-with-canonicalisation.

**Open, with three obligations.** Not because openness is safe, but because the
thing that makes a free predicate dangerous elsewhere **is not present here**.

### 8.1 The predicate is not a query key in this system

Verified rather than assumed: `predicate` appears in the fact store only when
reading a row into a model and when writing one — **never in a `WHERE`
clause**. The sole index is `(workspace, subject_id)`, partial on standing rows.
There is no entailment regime, no property hierarchy, no inference over
predicates anywhere in the codebase.

The predicate is a key for exactly three things — the negative cache, dedupe, and
contradiction ranking — and **all three degrade rather than break**, because
supersession is keyed on the subject.

This is the difference that decides it. In a system where a predicate is a
normative identifier, a variant is catastrophic: identity is exact string
comparison, *further normalisation is forbidden*, and a mistyped predicate
matches nothing while raising no error. The literature's cleanest instance is a
**published** vocabulary containing `rdfs:ramge` — a typo for `rdfs:range`, still
legal, still silent, and it survived review. **A free-text predicate is born
permanently in that state.** Nora escapes this only because it asks the store no
question a predicate answers.

**So the decision is contingent, and the contingency is the load-bearing part:**

> Predicates may stay open **only while no retrieval path filters on them.** The
> first query that does — a "what unlocks X" verb, any inference — converts this
> from a cost into a defect, and the vocabulary must close first.

That is a condition someone can check, not a hope.

### 8.2 The three obligations

**Normalise, mandatorily.** Same treatment as an entity name — the fragmentation
axis is identical and the fix is the fix that already applies one column over.

**Seed, do not gate.** The mature precedent is not a closed door: the largest
public schema mints new terms **immediately** into a named staging namespace with
a stability warning — **26% of its core properties live there** — and promotes on
consensus, retiring rejects to an attic rather than dropping them. Nobody in that
world closes a vocabulary by refusing unknown terms at the door. A seeded list
that guides the model without rejecting it gets the consistency without the
brittleness, and the count tells you when to close.

**Count.** There is no instrument for predicate growth, and a distinct-predicate
count per workspace is what turns *"is this fragmenting?"* from an opinion into a
reading — which is [09-measurement.md](09-measurement.md)'s rule applied to this
seam. Without it §8.1's condition cannot be evaluated.

### 8.3 The escape hatch belongs in the object, not the predicate

The failure mode of an open vocabulary is a model coining a **new predicate per
detail**. The prior art's answer is to quarantine uncontrolled key/value data
inside a typed wrapper, **keeping it out of the predicate position entirely**.

Applied here: one explicit `has_attribute` predicate, with the specificity pushed
into the object. **This also protects contradiction ranking**, which needs the
predicate to hold still while the object varies — a coined predicate per detail
makes every claim unique and nothing ever contradicts anything.

**The shipped objective already demonstrates the failure in its own worked
example.** The adjudication objective's sample output carries
`predicate: "gourds_required", object: "five"` — the object folded into the
relation name. The form this section asks for is a predicate that holds still
(`requires`, object `five gourds`) so that a later claim of *six* contradicts it.
As written, a differing count would coin `gourds_required` against some other
predicate and **contradiction ranking would never fire on it.**

One example, and it is the example the model is shown. Fixing it is a
one-line change to an operator-owned file, and it belongs with the seed list
rather than ahead of it.

### 8.4 A bad predicate currently fails silently

Phase A's parse drop is a bare `continue` with **no counter and no log line** —
while the drop on the very next lines, for a message id outside the batch, *does*
log a warning. So a malformed claim is discarded at a rate nobody can read.

This is [02-instrument-blindness.md](02-instrument-blindness.md)'s pattern again,
and it is why §8.2's counter is an obligation rather than a nicety: **the
vocabulary is currently unobservable in both directions** — neither its growth
nor its failures leave a record.

## 9. The contract, in one table

**D12. The substrate contract is settled before the first write.** Eight
questions, answered together, because four tiers write against them and
answering them one tier at a time is how the eleven-to-four narrowing happened.

| | question | answer |
|---|---|---|
| **1** | is a person an entity? | **yes** — a claim restriction is not an identity restriction. A person entity is a projection of an existing `person_id`, never minted by extraction (§2) |
| **2** | does an entity have a type? | **no.** The only branch anything needed — person vs thing — is a column and a source, not a taxonomy. If resolution ever needs one, a *closed* set (§3) |
| **3** | predicate vocabulary? | **open**, with mandatory normalisation, a seeded-not-gating list, and a counter — **contingent on no retrieval path filtering on it** (§8) |
| **4** | object: entity or literal? | **always the literal.** Entity-hood is promoted later, by code, on degree ≥ 2 (§6) |
| **5** | who creates an entity? | the sink, as a side effect of writing an edge. No new verb (§4) |
| **6** | entity + edge atomicity? | one transaction, `MERGE` semantics, safe under the existing unique constraint (§4) |
| **7** | where does resolution run? | deterministic in the sink; adjudication as a Dreamer pass; **defaults to split** (§5) |
| **8** | who asserted it? | **recorded**, via a junction table populated by a code-side join. The model still never sees a speaker (§7) |

**Two are coupled and it is not optional.** §5's merge-defaults-to-split is only
safe because §7 makes an unmerge possible; the dependency runs
**provenance → resolution**, and building resolution first inverts it.

**Three shipped defects sit on this path** (§6.4), all latent until the first
entity object is written, all free to fix at zero rows: the id column that is not
an id, the negative cache that breaks silently, and the render paths that print a
raw uuid. Plus a seventh instrument-blind layer — the tier's one entity metric
has no call site — and an uncounted parse drop (§8.4).

## 10. The remaining rules, decided

**Fidelity transitions.** `tentative` on write. **`proven` when a second
independent asserter corroborates the same edge** — which §7's junction table is
what makes expressible, and which is the difference between three people saying
a thing and one person saying it three times. **`deprecated` when superseded**,
written by the same statement that sets `superseded_at`, so the two can never
disagree. No separate writer, no pass: both transitions ride existing writes.

**Retention of superseded edges: keep them, indefinitely.** `retention_days`
stays commented out and this is the decision, not the absence of one.
Bitemporality's whole purpose is answering *what did we believe on the 9th*, and
a retention window silently destroys the transaction-time axis the schema was
built with. Storage for a few thousand text rows is not a constraint worth
trading that for. Revisit only if the table crosses a million rows.

**Writers for the three remaining warrants**, one owner each, no ambiguity:

| warrant | writer |
|---|---|
| `premises` | **the adjudicator**, when a verdict is deduced from standing facts rather than a page — it already writes `fact_premise` |
| `observation` | **the promotion pass** ([25](25-knowledge-pipeline.md) §4): an edge recurring across N distinct asserters with no source is warranted by observation, not by a page |
| `stated` | **the operator**, and only the operator — a claim nobody derived and nobody sourced is a decision someone made, and the tier should not manufacture one |

**The CHECK relaxation (§6.5) — decided: relax it.** `object_literal` becomes
`NOT NULL` and `object_entity_id` an additive nullable pointer. Three reasons
and the third is decisive: every shipped read path keeps working; the dedup
break in §6.4 disappears rather than needing a fix; and **promotion stops being
destructive**, so the surface form the room actually used survives resolution.
That last is the property [23](23-retrieval.md) §6.2 depends on — the embedded
text is the rendered triple, and a triple whose object was eaten by
canonicalisation embeds something the room never said.

**The predicate seed list and the veto list** remain **operator-owned files**,
versioned and hashed like an objective. That is not a deferral — it is the same
call as `change_policy: operator-only` on an objective, and it is *why* the rule
that produced a row stays re-derivable. Their contents are configuration; their
existence, ownership and versioning are decided here.

**What would prove this contract wrong.** Its central claim is that entity-hood
can be derived from the store's own contents rather than judged at write time. It
is wrong if a substantial share of objects that should be nodes never reach
degree 2 — if the room's claims are so sparse that promotion effectively never
fires. That is readable from the counter in §8.2 plus a degree histogram, and it
is checkable on the first real corpus rather than argued now.

## Status / Checkpoint

2026-08-22 — written after three investigations into the three questions
[20-knowledge-tier.md](20-knowledge-tier.md) left open. Five answers were settled
in conversation; §6, §7 and §8 are the investigated ones, and each changed the
answer that was expected going in: §6 dissolved the question rather than
answering it, §7 found the loss point is promotion rather than the prompt, and §8
found the decision turns on a property of *this* store rather than on the general
argument.

`[dreamer] enabled = false`. No `design/09-decision-index.md` entry; D-numbers
here are bundle-local.
