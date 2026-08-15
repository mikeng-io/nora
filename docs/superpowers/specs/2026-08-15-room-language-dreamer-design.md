---
kind: spec
title: "The Dreamer: room language and room dynamics from the conversation record"
description: "An asynchronous consolidator that distils how a room talks AND how its people treat each other out of the Postgres record, into a three-tier composite — portrait, term list, evidence — where each tier must be supported by the one below it. Renders in the VOLATILE prefill tier, never in the cached static head. Honcho keeps people; the Dreamer keeps the room. No Fact consolidator: this deployment is a friend group, not a workspace, and a fact is testimony about a world the record does not observe. Revised 2026-08-15 after a five-lens review found blockers in every dimension it examined. Depends on store.prune_enabled = false."
tags: [nora, spec, dreamer, language, dynamics, consolidation, memory, prefill]
timestamp: '2026-08-15'
status: accepted
supersedes_draft: "the 2026-08-15 first draft of this same file (git 120c0e4)"
derives_from:
  - design/09-decision-index.md
  - docs/superpowers/specs/2026-08-04-nora-architecture-design.md
---

# The Dreamer: room language and room dynamics

## 0. What this document is

Nora lives in a friend group. Groups like that develop their own vocabulary —
`firm` for 正/掂, a word that arrives one week and is dead the next — and their
own way of treating each other. A bot that picks up neither reads as a visitor
forever, and worse, misreads the room: it hears 「Miles你個白痴」 as an insult to
defuse rather than as two friends enjoying themselves.

The Dreamer is the asynchronous process that turns the authoritative record into
revisable derived beliefs **about the room**. Honcho keeps people and episodes;
this keeps the room. One record, two derived views.

### 0.1 Revision history, and why it matters

The first draft of this file (git `120c0e4`) was reviewed by five independent
read-only lenses — substrate, repo-fit, adversarial, data-model, skeptic — and
did not survive. Three blockers were mechanical (a port that could not compile
against the repo's own gates), one was a measured performance regression, and
two reviewers independently concluded the design was being proved on the wrong
domain. The operator then corrected the domain itself in conversation.

That history is kept because most of what is load-bearing below is the
*correction*, not the original idea. Each is marked where it lands.

## 1. Three corrections that made this design

### 1.1 Filtered injection produces mimicry, not learning

The first proposal was to inject a lexeme **only in the turn it was said** —
intersect the lexicon against the inbound message, gloss whatever matched. It
looks safe: no standing list, no menu, no over-use.

It is unbuildable, and the reasoning generalises. The prompt then holds the word
twice — once in the transcript, once glossed underneath — and the gloss *draws
attention to it* exactly when repeating it is most available. The likely output
is same-turn echo, which is the most conspicuous form of imitation there is.

Worse, it can never be anything else: the word is never present when it would be
*apt*, only when it has already been said. Filtered injection therefore cannot
produce learning **by construction**, whatever scoring sits underneath. It
optimises for "cannot over-use" and buys "can only parrot".

### 1.2 The domain is the room, not the world — and not facts

The natural second consolidator looks like a "Fact aggregator". It is wrong
here, for two independent reasons.

**Epistemically:** the messages *are* the room's language and the room's
behaviour. Not evidence about them — they are them. Nora sees every message, so
the record is **complete with respect to its object**; a lexeme or an
interaction pattern is a pattern in data she wholly holds. A fact is not in the
transcript. What is in the transcript is somebody *saying something about the
world*. 「今晚八點食飯」 can tell you the claim was made, by whom, when — never
whether it was true, a joke, or overtaken by a phone call Nora never saw. The
record is radically incomplete with respect to the world, and no scoring
machinery repairs an incompleteness that lives in the input.

`CLAUDE.md`'s measure discipline already forbids the naive version: *"Bind to
the real property — a `where_is` fix is a live GPS read, a `weather` reading is
HKO's own — never a proxy, and never assert what you cannot ground."* A fact
inferred from chat is exactly a proxy.

**And situationally, which is the operator's point and the sharper one:** this
deployment is a friend group, not a workspace. A work Slack produces decisions,
owners and deadlines because that is its register. A friend group produces
banter. Fact extraction over 「你個白痴」 is not difficult, it is a category
error — there is nothing there to extract. This reason is *conditional*: if Nora
is ever cloned into a work context, Facts become the right second member of the
family. For this deployment they never will be.

### 1.3 Observe the pattern of the exchange, never the content of the claim

The operator's example is the whole design in one line: Tyler keeps calling Miles
a 白痴, Miles keeps calling Tyler 蠢. **No fact is available** — you cannot
establish that Miles is stupid. But you can establish, by counting, that these
two insult each other constantly, symmetrically, and that it never escalates.

```
Tyler -> Miles   「你個白痴」
Miles  -> Tyler  「你先蠢」
... x40, both directions, replies within seconds,
    conversation continues every time, nobody withdraws
```

The content of the claim is unobservable. The **shape of the exchange** is fully
observable and countable: frequency, reciprocity, latency, persistence, absence
of withdrawal.

This is a safety property, not only a modelling choice. A consolidator that read
*content* would derive `Miles — dumb (40x, high confidence)`: false, and the
wrong reading of true data. Counting interaction shapes makes that outcome
structurally unreachable. It is the same failure class `nora/engagement/
context.py:189-212` records for `peer_card` — and a live instance surfaced
during this very design session, where an assertional memory layer concluded
*"Miles is an AI agent"* from subagent output misattributed to him.

## 2. The admission test for this family

> **A consolidator is sound only over a closed world it fully observes.**

Room language qualifies. Room dynamics qualify. "Facts about the world" never
will. "Statements made in this room, and not since retracted" would, because
supersession *by a later statement* is observable while supersession *by
reality* is not — but that overlaps heavily with `reminders`, which already
exist.

Apply this test before admitting any second member. It rules things out for a
reason rather than by shape, which the port signature alone cannot do.

### 2.1 What this means for the abstraction, honestly

The first draft claimed a general `Consolidator` spine — *LLM extracts,
deterministic policy consolidates*. The substrate review found that claim to be
the one sentence in the document not grounded in anything measured, and it is
narrower than it looked. It holds only where **salience reduces to recurrence
over a population**:

```
Statistical family        salience = recurrence          <- the Dreamer
  room language           deterministic scoring works
  room dynamics

Assertional family        salience = importance of one claim
  facts, episodes         invalidation = contradiction, not decay
  relationships           <- Honcho today; Graphiti's bi-temporal edge
                             invalidation is built for exactly this
```

These two families do not share a spine, and one port spanning both is fake
generality. This also explains Honcho's ~88%-third-person-restatement problem as
an **epistemic ceiling** rather than a defect: fed only chat and asked for
beliefs about people, an honest system drifts toward restating utterances,
because that is the limit of what testimony warrants. Graphiti would not fix
that — bi-temporal edges change how an assertion is *stored*, not what the
evidence *supports*.

The port is therefore named for what it is and admits members by §2's test, not
by signature.

## 3. The three tiers

The operator's formulation: **portrait <> term list <> evidence/relation** — a
composite of three, not one.

```
portrait            synthesis       most interpreted, least grounded
   | must cite
term list           index           label + counts
   | must quote
evidence/relation   ground truth    verbatim, observed
```

Each tier must be supported by the tier below it. Nothing stands alone — that is
the property that makes a generated gloss acceptable, and its absence is what
made the first draft's bare glossary indefensible.

**What is generated and what is observed, stated plainly** (the first draft
claimed "deterministic render, nothing generated" while its most important field
was model-authored):

| element | source |
|---|---|
| `canonical_form` (`kam`) | observed — the token, verbatim |
| counts, speakers, first/last seen, latency, reciprocity | deterministic — arithmetic |
| `examples` / exchange quotes | observed — real messages |
| `sense` (`尷/cringe-adjacent`) | **generated** |
| dynamic label (`mutual roasting`) | **generated** |
| portrait body | **generated** |

Three of six are model-authored. The tiering is what makes that safe: every
generated element sits directly above the observed evidence that would falsify
it.

### 3.1 The rendered block

```xml
<room_language>
呢個 room 新字入得快、用得狠、死得快，粗口係節奏唔係火。互相寸係常態，
唔係嗌交。

Words this room has made its own:
  kam   尷/cringe-adjacent   「個plan好撚kam」        12×, 3 people, since 9 Aug
  firm  正/掂, of a plan     「個idea幾firm喎」       31×, 4 people, since June

How this room treats each other:
  Miles ↔ Tyler   40+ exchanges both directions since June, replies within
                 seconds, conversation continues every time
                 「你個白痴」「你先蠢」
  Grant        teased more than he returns; others step in

Observed from what they said, to 15 Aug. Patterns in how they talk,
not things you are told to do.
</room_language>
```

One example quote per entry, not three: enough to falsify a wrong gloss on
sight, cheap enough to render every turn. The full evidence set stays in the
store.

Note the dynamics rows carry **structure plus quotes**. The structure *is* the
argument — fast, reciprocal, never-terminating exchange is what play looks like
mechanically — so the label is a convenience, not the claim.

The closing two lines are **authored template text**, fixed, never generated.

## 4. Placement — the volatile tier, and why the first draft was wrong

The first draft put this block between doctrine and `context_header`, reasoning
that it should carry more authority than a transcript. That reasoning was about
meaning and never checked mechanism. `nora/agent/prefill.py:112-146`:

> *"**Deliberately free of anything that changes between tags** — this block is
> the head of the prompt, and DeepSeek's context cache matches on an exact
> prefix, so the first byte that differs from the last request ends the hit for
> everything after it."*
>
> ```
> Re-measured 2026-08-08:  13,182 / 13,425  98%
>                          16,772 / 16,976  99%
> ```

A per-room, per-regeneration block inside that head fragments one cache prefix
shared across the entire deployment into one per room, and re-invalidates it on
every dream cycle. The clock was moved *out* of that head for this exact reason.

**`room_language` renders in the VOLATILE tier**, after `context_header`,
alongside the clock, cards and transcript:

```
doctrine / context_header / tool schemas     static head — untouched, still 95-99% cached
─────────────────────────────────────────
clock / platform
self_card / peer_card / peer_history
room_language                                <- here
group_transcript
tag_message
```

The volatile tier is uncached by design, so the block's token cost is real on
every turn. That is the price and it is stated rather than discovered: §3.1's
shape is capped (§6) precisely to bound it.

## 5. The port

Three blockers in the first draft's protocol, all mechanical:

- **No scope parameter.** `dream_loop` ticks two groups and the method cannot
  stamp `scope_id` on a single row it writes — broken for the one implementation
  the document already had, not for a hypothetical sibling.
- **A positional parameter fails a live gate.**
  `tests/test_ports.py:80::test_port_methods_are_keyword_only_after_self`
  enumerates every Protocol in `nora.domain.ports` and rejects
  `POSITIONAL_OR_KEYWORD`.
- **`TranscriptRow` is bound to the wrong query.** Its docstring says "One row
  as returned by `RecordStore.recent`", and `recent` is `ORDER BY ts DESC, id
  DESC LIMIT n` — newest-N reversed. The cycle needs ascending `id > watermark`,
  unbounded by recency. No such method exists, so an implementer would have
  hand-duplicated `SqlRecordStore`'s row mapping outside the contract suite.

```python
class RoomConsolidator(Protocol):
    name: str
    async def consolidate(self, *, scope_id: str, batch: Sequence[TranscriptRow]) -> int: ...
```

and the batch source it needs, added to `RecordStore` where its contract suite
already reaches:

```python
async def since(self, *, conversation_id: str, after_id: int, limit: int) -> list[TranscriptRow]: ...
```

`-> int` means **observations appended**, fixed here so a second implementation
cannot mean something else; the contract suite asserts it.

`RoomLanguage` lives in `nora/domain/models.py`, not `nora/language/models.py`.
This is forced, not preferred: `TagContext` is declared in `nora.domain.models`
and `nora.domain` is the floor of the layers contract, so nothing above it may
be imported by it.

**`nora.language` must be added to the `layers` list in `pyproject.toml`**,
between `nora.doctrine` and `nora.skills`. Import-linter's `layers` contract
only analyses packages it names — an unlisted package is invisible in both
directions, so `lint-imports` would report clean on a wrong placement. The gate
cannot make this decision; the spec makes it.

The LLM seam: `composition.py` builds a bare model client and injects it into
`LanguageConsolidator.__init__`, mirroring how `TranscriptInjection` takes
`audit`/`memory`. Not `nora.agent.factory` — that is full agent-loop machinery
sitting at a layer `nora.language` may not reach into, and `evals/judge.py:33-38`
is the precedent for duplicating the client rather than sharing it.

The read side needs a port too. `TranscriptInjection.__init__` takes only
Protocol-typed collaborators; a concrete `SqlLanguageStore` would be the sole
exception and untestable under the contract-suite rule. `RoomLanguageReader` is
that port, and it is the *read* half only — §2.1's "output store not
abstracted" holds for writes, where a lexicon and a dynamics table genuinely
differ.

## 6. Data model

```
consolidation_watermark   consolidator_name, scope_id, last_message_id, updated_at
                          PK (consolidator_name, scope_id)

language_observation      id, scope_id, conversation_id, message_id, person_id,
                          surface_form, canonical_form, sense, part_of_speech,
                          context, observed_at, extractor_confidence
                          UNIQUE (message_id, canonical_form, sense)

dynamic_observation       id, scope_id, conversation_id, message_id,
                          actor_person_id, target_person_id, act, replied_to_id,
                          latency_ms, observed_at, extractor_confidence
                          UNIQUE (message_id, actor_person_id, target_person_id, act)

room_lexeme               scope_id, canonical_form, sense, first_seen_at,
                          last_seen_at, occurrence_count, distinct_speakers,
                          recognition_score, state, examples
                          PK (scope_id, canonical_form, sense)

room_dynamic              scope_id, person_a, person_b, act, first_seen_at,
                          last_seen_at, occurrence_count, reciprocity,
                          median_latency_ms, continued_count, score, state, examples
                          PK (scope_id, person_a, person_b, act)

room_portrait             id, scope_id, body, generated_at, evidence_count,
                          cited_forms, sha256, is_live
```

Decisions inside that, several of them corrections:

- **`sense` is IN the lexeme primary key.** The first draft keyed
  `(scope_id, canonical_form)` with `sense` outside it, so a word acquiring a
  second meaning either silently overwrote the first or merged counts from
  unrelated senses — and then rendered a gloss no observation supported.
- **Natural-key uniqueness on both observation tables**, with
  `ON CONFLICT DO NOTHING`. Without it a retried or concurrent cycle
  double-counts, and `31×, 4 people` becomes fabricated confidence.
- **`is_live` needs a partial unique index**, not application discipline:
  `CREATE UNIQUE INDEX ux_room_portrait_live ON room_portrait (scope_id) WHERE is_live;`
  with the flip and insert in one transaction, `unique_violation` caught as a
  concurrent-cycle collision to count rather than crash the loop.
- **A new index serves the lag query.** `idx_messages_conv_ts` carries neither
  `id` nor `is_nora`; `idx_messages_is_nora` is a majority-class predicate the
  planner will not choose. Needed:
  `CREATE INDEX CONCURRENTLY idx_messages_dream_lag ON messages (conversation_id, id) WHERE is_nora = 0;`
  `CONCURRENTLY` is not optional — `messages` is written by every inbound turn
  and grows without bound since §9 — and `migrations/env.py` runs migrations
  inside one transaction, so this needs `op.get_context().autocommit_block()`
  in a hand-written migration. Without it the first draft reproduced the exact
  defect §9 diagnoses in `prune_all`: a fixed-cadence unindexed scan over a
  growing table.
- **Timestamp types split the way the repo already splits them.** The first
  draft's blanket "ts-shaped columns are BigInteger" is wrong. Epoch-ms
  `BigInteger` for values arriving from a platform wire or compared against one
  (`observed_at`, mirroring `messages.ts`); `DateTime(timezone=True)` for Nora's
  own instants compared against `now` in SQL (`updated_at`, `generated_at` —
  `age(watermark) > 24h` is precisely the `reminders.due_at` case).
- **`message_id` / `last_message_id` are `sa.Integer`**, matching
  `messages.id`, which is deliberately INT4.
- **`state` gets a CHECK constraint**, like every other enumerated TEXT column
  here, in a hand-written migration, with `tests/infra/pg_schema_baseline.sql`
  regenerated via `scripts/gen_pg_baseline`. `alembic check` is blind to CHECK
  constraints; the parity test's full-dump diff is what catches it.
- **`examples` is JSONB**, following `tool_call_log`'s Postgres-native
  precedent rather than `media_json`'s SQLite-era TEXT. Each example carries its
  own `message_id` and `person_id` — a bare string array has no query path to
  redact one person's quotes.
- **`message_id` is a soft reference with no FK**, so evidence outlives the
  message. Cost accepted: no orphan detection, and no cascade.
- **`state` in V1 is derived (live / fading / stale), not a promotion ladder.**
  It governs rendering only; there is no per-word generation gate, because there
  is no per-word generation decision.
- **`scope_id` defaults to `conversation_id`.** See §7.

### 6.1 Retention and redaction, which §9 makes acute

`context` and `examples` carry verbatim human utterances, and since
`store.prune_enabled = false` nothing in the system deletes anything. This is a
strictly worse retention posture than `messages` itself, which at least has a
disableable sweep. Per-example `person_id` (above) is what makes a redaction
request answerable at all. A retention policy for observation tables is NOT
decided here and is an open item in §12.

## 7. Scope

`scope_id` defaults to `conversation_id`.

Cross-conversation grouping — one Signal group and one Discord channel being the
same people — **reuses DEC-105's `workspace`**, already on `policy.toml`'s
`[[conversations]]` and motivated in the same words this design would have used:
*"rooms sharing a workspace are the same group of people reachable in more than
one place."* The first draft invented a second grouping primitive in
`config.toml`, which is both duplicate and the wrong file under the three-file
split — trust topology lives in `policy.toml`.

**But `workspace` alone is unsafe as a language scope.** `nora/policy.py:69-83`
puts `workspace: str = "nora"` on every `Conversation` with no conversation-type
restriction and a loose default, so keying scope on it directly pulls **every
DM in the deployment** into the group's scope — and `language_observation.context`
carries verbatim text. The grouping resolver therefore **excludes `dm`
conversations by construction**, not by operator care. Declared in V1, not
exercised.

## 8. The dream cycle

```
tick every DREAM_TICK_SECONDS (~5 min), per scope:

    acquire the scope's lock, or SKIP this tick        <- not optional
    lag = count(messages where id > watermark and is_nora = 0 and in scope)

    fire if   lag > SAFETY
       or     the room has been quiet for Q minutes and lag > 0
       or     age(watermark) > 24h                     <- ALWAYS rescores, even at lag = 0

    -> fetch batch via RecordStore.since
    -> LLM extraction, schema-forced -> observations
    -> append observations              (ON CONFLICT DO NOTHING)
    -> rescore room_lexeme / room_dynamic, apply time decay
    -> regenerate portrait ONLY on material change
    -> advance the watermark             <- LAST, same transaction
```

Corrections folded in, each from a found failure:

- **A per-scope lock is mandatory.** Without it, a slow extraction means tick
  N+1 re-fires the same scope against the same un-advanced watermark, and two
  cycles append overlapping evidence. `ConversationSlots.try_acquire`
  (`nora/agent/relay.py:248`) is the existing precedent; `SELECT … FOR UPDATE
  SKIP LOCKED` on the watermark row is the other.
- **The watermark advance is the LAST statement, in the same transaction as the
  appends.** The unsafe direction is watermark-first: a crash between them loses
  that range permanently and `nora_dream_evidence_lost_total` is structurally
  blind to it, being defined only for the pruning case. Append-first plus the
  natural-key constraint makes a retry idempotent instead.
- **The 24h firing always rescores, even on an empty batch.** Two of three
  conditions need `lag > 0`, so a dormant room would otherwise never decay: its
  portrait stays live describing a slang era that has ended, and the rendered
  "current to 15 Aug" becomes false with nothing to notice. The rendered date
  interpolates from `room_portrait.generated_at`; it is never re-stamped at read
  time.
- **Per-scope try/except, and backoff on extraction failure.** `prune_loop` is
  the wrong precedent to copy wholesale — it is a local DB sweep whose unit of
  work is one statement. This depends on an external provider, where
  `supervise_adapter`'s capped exponential backoff
  (`nora/app/runtime.py:160-187`) is the right shape; without it a rate-limited
  provider is hit at full cadence forever. And one scope's exception must not
  starve the others in that tick.
- **Partial extraction commits what parsed and advances the watermark only to
  the last fully-processed message**, never to the batch end.

**Material change** means: an entry crossed into or out of the rendered set, a
rendered entry's `sense`/`act` changed, or no portrait exists yet. A cycle that
only increments counts on already-rendered entries regenerates nothing.

`DREAM_TICK_SECONDS`, `SAFETY`, `Q`, the 24h ceiling and §9's floors are
parameters of this design, given values with their reasoning in the
implementation plan.

### 8.1 Her own output is never evidence — and that is necessary, not sufficient

`is_nora = 0`, non-negotiable: her speech re-entering as evidence is a runaway
loop (`she emits it -> it strengthens -> the portrait normalises it -> she emits
it more`). `idx_messages_is_nora` exists and `nora/infra/sql/audit.py:210`
already uses this predicate for the same reason.

**It does not close the loop.** A human who picks up a word *she* introduced
produces a legitimate `is_nora = 0` row, and the causal origin is invisible at
this grain. This is structural, since the whole point is that her register
changes how she talks. It also breaks the naive eval: if her rate and the
room's rate inflate together, the *ratio* stays healthy while both climb. §12
carries the stronger measurement this requires. No mechanical fix exists in V1.

Related and accepted: the cross-platform relay (DEC-105) means a human in a
destination room may echo vocabulary that originated in the source room. Relay
is workspace-bounded — the same audience — so this is an intentional cross-scope
channel, recorded rather than closed.

## 9. The contract check

Only three elements are generated (§3), and each is checked against the tier
below it.

**Portrait:**

```
non-empty, within a size bound
evidence_count >= a floor            <- gates generation entirely
cites >= N entries FROM THE RENDERED SET (live/fading), not merely from the table
no observation whose text references Nora reached the generation prompt
     |
 pass -> becomes the live portrait
 fail -> the previous portrait stays live, nora_portrait_rejected_total{reason} ++
```

Three of those are corrections:

- **The citation check is against the RENDERED subset.** "Exists in the table"
  admits `stale` rows the glossary omits, producing a paragraph that discusses a
  word the list beneath it does not contain — self-contradiction on the exact
  axis the tiering exists to protect.
- **`evidence_count` gates generation.** In the first draft it was a column that
  gated nothing, so a three-day-old room with 8 occurrences produced a paragraph
  as confident as one with a year of history. Below the floor the block is
  correctly **absent**, not empty.
- **Exclusion, not a pronoun regex.** "No claim about Nora" as a syntactic check
  is trivially bypassed by third-person declarative — *"people here expect fast
  one-line replies"* has no second person and no imperative and is behavioural
  instruction anyway. Worse, messages *about* Nora are `is_nora = 0` and thus
  eligible evidence: a room saying "Nora reply得好慢" is legitimate input a
  portrait can launder into doctrine-adjacent prose. The fix is at the input —
  observations whose text references her by name or handle never reach the
  generation prompt.

**Sense and dynamic labels** are checked by construction rather than by a rule:
each renders directly above a verbatim quote drawn from its own evidence, so a
wrong gloss is visible at the point of use. A rendered entry with no quotable
example is not rendered.

Fail-closed throughout, R-27 shaped: a bad generation is `unavailable`, never a
silent overwrite.

## 10. Modules

```
nora/domain/models.py          RoomLanguage, RoomLexeme, RoomDynamic  (forced here, §5)
nora/domain/ports.py           RoomConsolidator, RoomLanguageReader, RecordStore.since
nora/language/observer.py      extraction — the one LLM call
nora/language/scoring.py       deterministic score, decay, state
nora/language/portrait.py      generation + the contract check
nora/language/render.py        the prefill block, capped
nora/language/consolidator.py  LanguageConsolidator, DynamicsConsolidator
nora/infra/sql/language.py     the store
nora/app/runtime.py            dream_loop
```

`tests/contract/` gains a `RoomConsolidator` suite in V1 — two implementations
exist immediately (language and dynamics), so the rule applies on day one rather
than by anticipation.

## 11. Not in V1

Adoption ladder and `adoption_score`; Graphiti; any Fact consolidator (§1.2);
semantic search over observations; cross-platform scope grouping beyond the
resolver seam; human approval of a portrait.

Portraits go live on passing §9's check. Staging them for review was considered
and declined: the check is mechanical and fail-closed, the generated surface is
one paragraph that cannot mention her and cannot cite an unrendered entry, and
it sits above evidence that falsifies it.

## 12. Measurement, and what remains unsolved

**The eval that matters**, strengthened after review: not merely *"she must not
use a room entry more often than the room does"* — a ratio that stays healthy
while both sides inflate together (§8.1). It needs **per-entry occurrence rate
over time**, watching for acceleration in the room's own usage correlated with
hers after deployment.

Also required: a DEC entry (next free is **DEC-107**) and a named test in
`tests/test_acceptance_rules.py::RULE_TESTS` for §1.3's rule — *observe the
pattern of the exchange, never the content of the claim* — which is the
behavioural rule this whole design rests on; a contract-suite test that turns
red if the `is_nora` filter is reverted; a portrait-rejection test proving the
previous portrait survives a failed check; and a cache-cost measurement of the
rendered block against the volatile tier.

Open risks:

- **The human-mediated feedback loop has no mechanical fix** (§8.1). The eval
  above detects it; nothing prevents it.
- **A generated `sense` can be wrong.** The quote beneath it makes that visible
  rather than impossible.
- **Dynamics can be misread.** A genuine falling-out between two people who
  usually banter looks, for a while, exactly like banter. Decay is the only
  mechanism that eventually corrects it, and it is slow.
- **Retention for observation tables is undecided** (§6.1). Verbatim utterances
  currently persist forever.
- **Extraction is a recurring LLM cost** for as long as the daemon runs.
- **A new room has no evidence**, and the correct behaviour is no block at all.

## 13. Dependency: the record must persist

Shipped and deployed 2026-08-15 (git `172bf30`), before this document, because
none of the above is buildable without it.

`SqlRecordStore.prune_all` trimmed every conversation to
`max_per_conversation = 500` rows every 300 seconds. Measured live that day: the
busy group sat at **exactly 500** rows, earliest surviving message eight days
old, `min_id = 121`, with `"pruned 1 rows past the retention window"` in the log
— one row deleted per new message arriving. The record was a rolling window, not
a history, and looked identically healthy either way.

`store.prune_enabled` now decides whether the sweep is spawned at all, and
Nora's deployment sets it false. A switch rather than a larger number, because
`prune_all` runs a `row_number()` window over the whole table every tick.

`nora_dream_evidence_lost_total` should now be structurally zero. It stays as
the fail-closed tell if the sweep is ever re-enabled: a watermark below the
oldest surviving `id` is `unavailable`, never `nothing_found`.
