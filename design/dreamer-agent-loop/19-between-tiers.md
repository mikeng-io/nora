---
type: design
title: "Between the Tiers — Routing, Conflict, and the Shape of What Renders"
description: "Four stores hold four kinds of knowing. The boundaries between them are designed at write time and enforced nowhere; nothing arbitrates at read time; and the block that renders facts is a recency slice where it should be shaped like a peer card."
tags: [nora, dreamer, tiers, routing, conflict, entity-registry, rendering]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 08-state, rel: depends_on, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


# Between the Tiers

Nora holds four kinds of accumulated state, soon five. They are not four copies
of one thing:

**As of 2026-09-07 two of the five rows below are gone:** `fact` (the knowledge
tier) and `lexeme` (the language tier) are deprecated and their code deleted
(issue #115). What remains live is **memory** (Honcho), **note** and
**standing**. The table is kept whole because the routing argument only reads if
you can see what it was routing between.

| | is about | settled? | its warrant |
|---|---|---|---|
| **memory** (Honcho) | **people** | soft, accumulating | how often it was seen |
| ~~**fact**~~ **(DEPRECATED 2026-09-07, deleted)** | **the world** | yes — three-state | a source, quoted |
| **note** | **an open question** | **explicitly not** | a closing condition |
| ~~**lexeme**~~ **(DEPRECATED 2026-09-07, deleted)** | **words** | scored, decaying | usage plus a quote |
| **standing** | **her own position** | revisable | the turn's trace |

Different subjects, different settledness, different warrants. So the question
is not *which one wins* — it is **how a thing reaches the right one, and what
happens when two of them end up disagreeing anyway.**

## 1. The balance is designed, at write time

Each tier states what is **not** its job. Together these are a routing design,
and they are the right shape: decide where a thing belongs when you **write** it,
rather than arbitrating when you read it.

- **facts → memory.** *"anything about a person in this room — **that is
  somebody else's job and it is not yours**; a claim about a person is not a
  claim about the world."*
- **notes → facts, or nowhere.** *"**A thing with no closing condition is not a
  note.** It is either a fact (already settled) or ignorance (nothing would
  resolve it). **This is the discriminator that stops the tier swallowing the
  other three.**"*
- **notes ↛ re-deciding.** *"It does **not** re-decide anything the tiers it
  sits beside already settled."*
- **facts ↛ resolution.** *"Getting one name to line up with another is a
  separate job and it is not yours."*

There is also a genuine precedence model in the system — **confirmed →
remembered → web** — rendered into the capability block. But it ranks **tools**:
where a *live answer* comes from at call time. It says nothing about which
*stored state* wins.

## 2. At read time there is none

The blocks are siblings, assembled independently, each with its own framing
sentence. **Nothing compares them.** That is deliberate, and the prefill says so:
they sit *"at the same authority, and she can contradict one if she wants to."*

Delegating the judgement to her is defensible — it is what a model is for. What
is not defensible is that **she is given no signal that two of them disagree.**
She sees two statements at equal authority and no flag.

## 3. Two reasons write-time routing is not enough today

**The rules cannot be applied by the things asked to apply them.**

- The fact extractor is told not to report person-claims, and **cannot tell who
  is a person in the room** — its input carries no names and no roster
  ([03-no-agent-loop.md](03-no-agent-loop.md) §4). Its primary boundary is an
  instruction it is structurally unable to check.
- The note discriminator — *is this already a fact?* — requires knowing what the
  fact tier holds. There is **no fact read tool** ([08-state.md](08-state.md)
  §11.5). Its primary boundary is likewise uncheckable.

**And nothing detects a miss.** This is queued right now: the lexicon holds
*"big walk = a customary strolling activity."* The moment the fact tier writes
*"Big Walk is a co-op puzzle video game"*, two blocks will render **incompatible
statements in one prompt**, both marked untrusted, both at equal authority, with
nothing marking the conflict.

Note the asymmetry that makes this stark: **the fact tier's whole value is that
it can contradict itself** — three-state verdicts, supersession, a recorded
reason. That machinery exists for *within*-tier conflict, and there is **nothing**
across tiers.

## 4. The design: enforce the routing, then make the overlap detectable

**Not a precedence order.** A total ranking would be wrong — *facts beat memory*
is false for person-claims, and these tiers are about different things. Ranking
them would force a comparison that mostly does not apply.

Two things instead.

**Give each writer what its own boundary needs.** The extractor needs the
roster to apply its person-exclusion; the note discriminator needs a fact read.
Both are grants this bundle already argues for on other grounds
([08-state.md](08-state.md), [10-room-access.md](10-room-access.md)) — this is a
third, independent reason for the same two.

**Make cross-tier overlap joinable.** Two tiers can only be *noticed* to
disagree about a thing if they refer to that thing by the same handle. That is
precisely what `fact_entity` is, what `note_entity`'s foreign key already
assumes, and what nothing populates
([12-self-correction.md](12-self-correction.md) §4).

> **The entity registry is not a deduplication convenience. It is the mechanism
> by which the system can observe its own inconsistency.**

It is also the **relevance function** — ranking by distance from a focal node
has nothing to compute over without it, which is why §5's block is a recency
slice rather than a scoped one. And it is what keeps the store choice
reversible. See [20-knowledge-tier.md](20-knowledge-tier.md) §5.

Which makes three of this bundle's open threads one thread: the registry, the
cross-tier semantic index that might populate it
([16-open-items.md](16-open-items.md) §4.1), and cross-tier conflict detection
are the same problem wearing three faces.

**What detection is not:** a blocking gate. A background pass noticing that a
lexeme and a fact disagree about one subject should **write that down** — it is
a candidate for exactly the adjudication the fact tier already performs — not
refuse to render. Silence beats a wrong answer; a flagged disagreement beats
silence.

## 5. What renders is the wrong shape — and there is no verb beside it

Two defects, and they are halves of one thing.

### 5.1 The block is a recency slice

`<facts>` calls `recent_facts(unit, limit)` — **no subject filter.** In a
room discussing one thing, she receives the eight most recently written facts,
relevant or not.

The targeted read **already exists and is unreachable**:
`standing_facts(unit, subject_ids, limit)` is built, and nothing can supply
`subject_ids`, because that needs resolution. So the tier ships its untargeted
read and holds its targeted one in reserve.

### 5.2 And she has no verb at all

| tier | tools exposed to her |
|---|---|
| notes | `note_read`, `note_search`, `note_create`, `note_revise` |
| memory | `about`, `ask_memory`, `search_memory` |
| **facts** | **none** |
| **lexemes** | **none** |

**She can search her notes and her memory; she cannot search her facts.** So
what she knows of the tier is whatever slice the renderer chose — a projection
someone else decided, with no way to ask a different question.

That is the concrete form of *"decide which facts to include"*: she cannot,
because there is nothing to decide **with**.

### 5.3 The block and the tool are complementary, not alternatives

The note tier already settled this shape, and its reasoning transfers directly:

> the injected index buys **awareness** — *she cannot search for what she does
> not know exists*; the search index buys **retrieval by content**. So
> `note_search` searches bodies, **never titles** — searching titles *"would
> duplicate what is already in the prompt on every turn and would earn
> nothing."*

Mapped onto facts:

| | carries | answers |
|---|---|---|
| **the `<facts>` block** | the **subjects** the tier holds anything about | *is there anything here?* |
| **a fact read verb** | the **claims** under a subject | *what exactly do we believe about X?* |

**And that makes one requirement hard rather than nice.** A tool is unreachable
unless its arguments are constructible from what is already in the prompt
([08-state.md](08-state.md) §11.3) — the note tier shipped exactly this bug and
had to fix it: *"she could see every note and name none of them."* So **the block
must render the handles the verb takes.** A block of claims with no nameable
subject leaves the verb wired and inert.

### 5.4 The slices, and whether recency survives

The better block shape is the one the **peer card** already uses: memory does not
inject recent conclusions, it injects a card **scoped to who is present**.

| slice | carries | needs |
|---|---|---|
| **room** | subjects *this room* deals in — the game, the places | room → entity |
| **sender** | subjects *this person* engages with | person → entity |
| **recent** | the tail | works today |

**Recency is scaffolding, not a third slice.** It is a proxy for relevance and a
poor one — it surfaces whatever was written last, which correlates with nothing
the conversation is about. It shipped because it is the only slice computable
without the registry. Below the render cap it is indistinguishable from the
others anyway: the lexeme block already injects the *entire* lexicon, and facts
will be smaller than the cap for a long time. **Keep it while the corpus is
smaller than the cap; drop it once the scoped slices can be computed**, at which
point it stops being a tail and becomes noise.

### 5.5 The verb does not need a vector index

Measured, at this deployment's size: embeddings are a **candidate-retrieval**
function, not a resolution one, and on the room's own terms they rank the
literal misreading *nearer* than the true alias and resolve a nickname to the
wrong person ([16-open-items.md](16-open-items.md) §4.1). Meanwhile the corpus is
single-digit rows and the planner sequential-scans thousands.

So the capability is right and the machinery is not. **A fact read verb over
deterministic matching is the whole requirement** — the argument that it must be
a *tool* rather than an input stands on [08-state.md](08-state.md) §1, and needs
no index to serve it.

## 6. The block shape, decided

**One block, three labelled slices.** Not three blocks: they share a header and
a provenance line, and three sibling blocks in a prompt read as three unrelated
systems rather than one store seen from three angles.

**The slices and their caps:**

| slice | cap | why |
|---|---|---|
| **room** — facts whose subject is an entity this conversation has touched | **8** | the working set; more than this and the block is competing with the transcript it sits beside |
| **sender** — facts asserted by the person speaking now | **4** | enough to ground *"you told us…"*, small because it is the narrowest and most often empty |
| **extent** — counts only, never rows | **1 line** | the whole point of [23-retrieval.md](23-retrieval.md) §3: she is told the size of what she has not been shown |

**The extent line is not optional and not a slice of content.** Without it the
block reproduces the defect the recency window had — a subset presented as the
whole, with no signal that the verb exists or is worth reaching for.

These are defaults with their reasoning, adjusted by the same window that tunes
[09-measurement.md](09-measurement.md) §6. What they are **not** is open.

### 6.1 The room slice costs one round trip, and it was measured

The slice is *"facts whose subject is an entity this conversation has
touched"*, and the only honest source for what the conversation has touched is
the transcript — which the tag path reads **in parallel** with the fact block,
deliberately, for the reply slot ([26](26-standing.md) §1). So the fact read is
sequenced after it.

**Two alternatives were refused, and both for the same reason.** Keying the
slice on the tagging message alone needs no extra read and is empty most turns,
which makes a block claiming to be about what the room is discussing actually
about one line. Filtering a wider recency read afterwards costs nothing and
silently misses anything outside that window — a subset presented as the whole,
which is the exact defect the extent line above exists to stop, rebuilt one
level down.

**Measured 2026-08-28**, because the reasoning was a prior and a prior is what
*"probably fine"* sounds like. The four reads the block makes, against real
Postgres, over **400 standing facts across 60 subjects** — a corpus well beyond
what this room holds, deliberately, since a measurement at zero rows says
nothing about the shape it defends against — 200 trials:

| | |
|---|---|
| p50 | **3.4 ms** |
| p95 | **4.2 ms** |
| smallest bucket of `nora_tag_latency_seconds` | 100 ms |
| p95 as a fraction of that bucket | **4.2%** |

**What this is NOT.** It is not end-to-end tag latency. That cannot be measured
until `[facts]` is on and deployed, because with the tier off `_facts` returns
before touching the database and the serialisation costs exactly nothing. What
is measured is the quantity that actually changed: the reads no longer hide
under the transcript read, so their wall time IS the added cost.

The fallback if this ever stops holding is the third option **with the extent
line reporting honestly over the whole unit** — which is what makes it a
survivable fallback rather than the defect it would otherwise be.

## Status / Checkpoint

2026-08-28 — §6.1 records the latency the room slice costs, measured rather than
argued: p95 of 4.2 ms against a 100 ms bucket, on a corpus larger than the
room's.

2026-08-24 — §6 now decides the block shape — one block, three slices, caps 8/4/one line — rather than waiting on a registry.
