---
kind: spec
title: "The Dreamer: room language and exchange patterns from the conversation record"
description: "An asynchronous consolidator that distils how a room talks, and the shape of the exchanges in it, out of the Postgres record — into a three-tier composite (portrait, term list, evidence) rendered in the VOLATILE prefill tier. Every claim is about MESSAGES, never about people: no person is characterised, and a strongly asymmetric pattern is withheld rather than asserted as play. No Fact consolidator — banter yields nothing to extract, and Honcho already holds the facts a friend group does produce. Revision 3, after eight review passes across two rounds."
tags: [nora, spec, dreamer, language, dynamics, consolidation, memory, prefill]
timestamp: '2026-08-15'
status: accepted
supersedes_draft: "revision 1 (git 120c0e4) and revision 2 (git 656d7f3) of this same file"
derives_from:
  - design/09-decision-index.md
  - design/adr/0001-honcho-is-interpretation-not-record.md
  - docs/superpowers/specs/2026-08-04-nora-architecture-design.md
---

> **DEPRECATED 2026-09-07 — the language tier this document designs has been deprecated and its code deleted**, as part of the same decision that deprecated the knowledge tier.
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The language tier never ran in production — `[language] enabled = false` throughout its life. Its tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.


# The Dreamer: room language and exchange patterns

## 0. What this document is

Nora lives in a friend group. Groups like that develop their own vocabulary —
`firm` for 正/掂, a word that arrives one week and is dead the next — and their
own way of going at each other. A bot that picks up neither reads as a visitor
forever, and worse, misreads the room: it hears 「Miles你個白痴」 as an insult to
defuse rather than as two friends enjoying themselves.

The Dreamer is the asynchronous process that turns the authoritative record into
revisable derived beliefs **about the room's messages**. Honcho keeps people and
episodes; this keeps the room.

### 0.1 Revision history

Revision 1 (git `120c0e4`) was reviewed by five read-only lenses and did not
survive: three mechanical blockers, one measured performance regression, and two
reviewers independently concluding the domain was wrong. Revision 2 (git
`656d7f3`) added room dynamics and was reviewed by three more. That round found
a harm defect (§1.4), a factual error about Honcho (§2.2), an overstated
completeness claim (§2.1), and four fixes that only looked fixed.

The history is kept because most of what is load-bearing here is the
*correction*. **The single rule that emerged from all eight passes:**

> Every claim this system makes is a claim about **messages**. It never
> characterises a **person**.

Each failure in revision 2 sat at exactly that seam.

## 1. The corrections

### 1.1 Filtered injection produces mimicry, not learning

The first proposal injected a lexeme **only in the turn it was said**. It looks
safe: no standing list, no menu, no over-use.

It is unbuildable. The prompt then holds the word twice — once in the transcript,
once glossed underneath — and the gloss draws attention to it exactly when
repeating it is most available. The likely output is same-turn echo. Worse, it
can never be anything else: the word is never present when it would be *apt*,
only when it has already been said. Filtered injection cannot produce learning
**by construction**, whatever scoring sits underneath.

### 1.2 The rule, stated precisely

Revision 2 said *"observe the pattern of the exchange, never the content of the
claim."* That cannot be applied literally — you cannot know 「你個白痴」 is an
insult without reading it. Classification is semantic; you cannot count what you
have not first classified. The rule was incoherent on its face.

> **Classify the illocutionary act an utterance performs — insult, tease,
> praise, request. Never adopt its propositional content as a belief about the
> world, and never attribute a disposition to a person.**

Tyler keeps calling Miles a 白痴; Miles keeps calling Tyler 蠢. No fact is
available — you cannot establish that Miles is stupid. What is available is that
these two exchange this act, in both directions, and the exchange continues
afterward.

A consolidator reading propositional content derives `Miles — dumb (40x, high
confidence)`: false, and the wrong reading of true data. This is not
hypothetical — during this design session an assertional memory layer concluded
*"Miles is an AI agent"* from subagent output misattributed to him.

**Note what the reformulation does NOT fix.** Distinguishing hostile from
affectionate ribbing is itself a hard, contestable semantic judgment — arguably
harder than the fact extraction §2.3 disqualifies — and §7's checks do not
scrutinise act classification the way they scrutinise the portrait. That is a
known weakness, and §1.4 is the reason it is dangerous.

### 1.3 Claims about messages, never about people

Revision 2 rendered `Grant — teased more than he returns; others step in`.
That is a characterisation of a named person's social standing, and it is
**removed**. What replaces it is a claim about messages:

```
in this room, Miles and Tyler exchange this 40× since June,
both directions, and the conversation continues each time
```

True, checkable against the record, and sufficient for the thing actually
wanted: she stops reading a roast as a fight.

This also settles the disclosure question. A pair-level count is derived
entirely from messages **every member of that room already received** — nothing
is revealed that the room does not have. That is materially unlike `where_is`,
where R-31/DEC-74 gates a fact the room does not otherwise hold. **The safety of
that argument depends entirely on scope never exceeding the room**, which is why
§5's grouping rule is a safety constraint and not a convenience.

### 1.4 Asymmetry is withheld, never asserted

The sharpest defect in revision 2. `Grant — teased more than he returns` is
structurally identical to a bullying signature: high frequency, strongly
asymmetric reciprocity, occasional bystander intervention, and a target who
never withdraws — because they cannot; it is their friend group. A target who
appeases with self-deprecating replies to defuse produces exactly the signal
revision 2 treated as proof of consensual play.

The consequence is not an inert wrong row. §0's whole purpose is to teach Nora
to read a repeated pattern as *banter to leave alone*. So a misclassified
pattern **actively teaches her to stand down**. And the mechanism runs backwards:
the more persistent the behaviour, the higher the count, the more `live` the
state, the more confidently it renders as settled texture. Persistence is what
the scoring rewards. Decay does not save this — decay fires on *absence*, and an
ongoing pattern never decays.

**The rule:** an entry whose reciprocity is strongly one-directional above a
volume floor enters state `withheld`. It is never rendered, whatever its count,
and `nora_dynamic_withheld_total{scope}` counts that it exists.

**This is not a bullying detector and must never be described as one.**
Withholding does not assert that anything is wrong. It only declines to assert
that a one-sided pattern is play — so Nora responds to what is in front of her
as she otherwise would, rather than being told to stand down. Fail-closed in the
direction where being wrong is expensive.

## 2. What the record can and cannot support

### 2.1 The record is not a census, and revision 2 said it was

Revision 2 claimed the messages are "complete with respect to their object."
They are not, and the gaps are structural, not incidental:

- **Reactions are dropped by design** (`nora/platform/signal/parser.py:228`):
  *"a reaction is metadata attached to somebody else's message, not a message."*
  Sound for the transcript, and the sharpest gap here — a 😂 on an insult is the
  clearest available signal that it landed as play, and it never enters the
  record at all.
- **Voice notes carry no words in** — they become the placeholder `voice note`;
  there is no ASR path.
- **Deletions are unhandled.** A `remoteDelete` envelope has no `dataMessage`
  and is dropped, so the original is never retracted. With
  `store.prune_enabled = false` (§10) the record can now hold testimony the room
  itself withdrew, permanently.
- **Everything before 2026-08-15 in the busy group is already gone** (§10), and
  there is no backfill of pre-join history.

**The honest claim: approximately complete for TEXT, prospectively, since
2026-08-15.** That still carries §1.1's language argument — slang mostly is
text — and it materially weakens the exchange-pattern argument, which is the
second reason §1.4 withholds rather than asserts.

### 2.2 The admission test

Revision 2's *"sound only over a closed world it fully observes"* fails on its
own members: dynamics does not fully observe reciprocity (reactions), and
language does not fully observe utterance (voice notes). Applied literally it
disqualifies both. The defensible form:

> **Admit a consolidation target only if it is countable within the text channel
> this system has custody of, WITHOUT needing to know whether any underlying
> claim was true.**

Room language qualifies. Exchange patterns qualify, with §1.4's withhold as the
price of the channel gap. "Facts about the world" do not.

### 2.3 Why no Fact consolidator — and the narrower claim that is actually true

Revision 2 argued a friend group produces no facts. That is false of this
deployment's own traffic: 「我下星期去日本」, 「我搬咗屋」 are ordinary, and
ADR-0001's measured corpus contains exactly such lines. The true claim is
narrower:

> **Banter yields nothing extractable**, and the person-level facts a friend
> group does produce (moves, jobs, trips) are Honcho's job, which it already
> does.

The epistemic argument stands on its own and is register-independent: a fact is
not in the transcript. What is in the transcript is somebody *saying something
about the world* — 「今晚八點食飯」 tells you the claim was made, never whether
it was true, a joke, or overtaken by a phone call Nora never saw.
`CLAUDE.md` already forbids the proxy: *"bind to the real property … never
assert what you cannot ground."*

This exclusion is **conditional on register**: clone Nora into a work context,
where decisions and deadlines predominate, and a Fact consolidator becomes the
right second member.

### 2.4 Honcho: an open question this spec does NOT close

Revision 2 claimed Honcho's ~88% third-person restatement is an "epistemic
ceiling" — that a system fed only chat cannot do better. **That was wrong, and
this repo already measured it.** `design/adr/0001-honcho-is-interpretation-not-
record.md`:

> A throwaway peer fed 48 realistic first-person messages produced **77**
> conclusions, ~74 of them durable traits. **The noise is the corpus, not the
> deriver.** … `peer_card` is dreamer-gated, not broken. It needs 50+ documents;
> `miles_novak` has 16, so no dream had ever run.

The measurement was taken against a thin, operator-testing-heavy corpus
("asked what time it was"), and `ARCHITECTURE.md` still logs the question as
unsettled. This spec must not be read as having resolved it, and nothing here
argues against improving Honcho. The statistical/assertional distinction in §2.5
stands on its own reasoning and never needed the ceiling claim.

The ADR's threshold is checkable: that measurement is from 2026-08-06 and the
corpus may since have crossed 50 documents.

### 2.5 The spine is narrower than a general consolidator

*LLM extracts, deterministic policy consolidates* holds only where **salience
reduces to recurrence over a population**.

```
Statistical family       salience = recurrence               <- the Dreamer
  room language          deterministic scoring works
  exchange patterns

Assertional family       salience = importance of one claim
  facts, episodes        invalidation = contradiction, not decay
  relationships          <- Honcho's ground; what Graphiti's bi-temporal
                            edges are built for
```

These do not share a spine, and one port spanning both is fake generality. The
split absorbs the hard cases: preferences are recurrence-shaped; commitments
overlap `reminders`; expertise splits cleanly — *who answers whom* is countable,
*whether Tyler knows cars* is not.

## 3. The three tiers

```
portrait            synthesis       most interpreted, least grounded
   | must cite
term list           index           label plus counts
   | must quote
evidence            ground truth    verbatim, observed
```

**What is generated**, stated plainly — revision 1 claimed "nothing is
generated" while its most important field was model-authored:

| element | source |
|---|---|
| `canonical_form` (`kam`) | observed — the token, verbatim |
| counts, distinct speakers, first/last seen | deterministic — arithmetic |
| `examples`, exchange quotes | observed — real messages |
| `median_latency_ms` | **advisory** — see §6 |
| `sense` (`尷/cringe-adjacent`) | **generated** |
| `act` (`insult`, `tease`) | **generated** |
| portrait body | **generated** |

### 3.1 The tiering is weaker than revision 2 claimed

§7's check verifies **citation-existence, not entailment**. A portrait can name
`firm` and `kam` correctly and still assert 「粗口係節奏唔係火」 — a claim about
the room's whole register that no lexeme row and no pair row entails. The check
waves it through because the nouns are present. A citation check that *looks*
like an entailment check is worse than none, because a wrong general claim
survives review by hiding behind two correct citations.

**The fix is to narrow what the portrait may say**, not to strengthen an
impossible check: the portrait is restricted to **direct paraphrase of counted
structure** — the same discipline the exchange rows already follow, extended to
the paragraph. Register-level generalisation is out of contract; `voice.md`
already owns the room's general register and does it better, authored.

Residual risk, stated rather than papered over: a paraphrase can still emphasise
or frame, and nothing detects that.

**What this costs, plainly.** A portrait restricted to paraphrase of counts is
orientation, not interpretation — closer to a summary line than to the "slang
culture of the room" revision 2 rendered. That is a deliberate reduction. The
interpretive version was the half that duplicated `voice.md:34-46`, could not be
checked against anything, and produced the one clause a reviewer showed passing
every gate while being generic filler. Compare what was cut:

```
revision 2   呢個 room code-switch 得好密 … 粗口係節奏唔係火
             — a claim about the room's register, entailed by nothing

revision 3   12 words are current here, the newest since 9 Aug …
             — every clause recomputable from a table
```

`DREAM_TICK_SECONDS`, `SAFETY`, `Q`, the 24h ceiling, the citation floor `N`,
the withhold volume floor and asymmetry threshold, and §6.1's render caps are
parameters of this design. The implementation plan gives each a value with the
reasoning recorded beside it.

### 3.2 The rendered block

```xml
<room_language>
12 words are current here, the newest since 9 Aug; most arrive and fade
within weeks. Two pairs trade insults both ways, and those exchanges
carry on afterward rather than ending anything.

Words this room has made its own:
  kam   尷/cringe-adjacent   「個plan好撚kam」     12×, 3 people, since 9 Aug
  firm  正/掂, of a plan     「個idea幾firm喎」    31×, 4 people, since June

Exchanges in this room:
  Miles and Tyler trade this 40+ times since June, both directions, and the
  conversation continues each time
  「你個白痴」「你先蠢」

Observed from messages in this room, to 15 Aug. This is what their words
mean and how their exchanges run. It is not a set of words to reach for,
and it is not a description of anybody.
</room_language>
```

One example quote per entry: enough to falsify a wrong gloss on sight, cheap
enough to render every turn. The closing lines are authored template text, fixed,
never generated.

## 4. Placement — the volatile tier

Revision 1 put this between doctrine and `context_header`, reasoning it should
carry more authority than a transcript. That reasoning never checked mechanism.
`nora/agent/prefill.py:112-146` documents that head as **deliberately free of
anything that changes between tags**, at a re-measured 98-99% cache hit, because
the cache matches on an exact prefix. A per-room, per-regeneration block there
fragments one deployment-wide prefix into one per room. The clock was moved
*out* of that head for this exact reason.

```
doctrine / context_header / tool schemas   static head — untouched, 95-99% cached
───────────────────────────────────────
clock / platform
self_card / peer_card / peer_history
room_language                             <- here
group_transcript
tag_message
```

Verified against `build_prefill` (`prefill.py:399-446`), whose volatile body is
`_now_line + _where_line + _self_card_block + _peer_card_block +
_peer_history_block + _transcript_block`. The volatile tier is uncached by
design, so the block's cost is real every turn — which is why §6 caps it.

## 5. The port, and the row it reads

```python
@runtime_checkable
class RoomConsolidator(Protocol):
    name: str
    async def consolidate(self, *, scope_id: str,
                          batch: Sequence[TranscriptRow]) -> int: ...
```

`@runtime_checkable` is not optional: `tests/test_ports.py:62` asserts it on
every Protocol in `nora.domain.ports`, and all 15 existing ports carry it.
Keyword-only is likewise a gate (`tests/test_ports.py:80`). Revision 2 fixed the
second and failed the first.

`-> int` means **observations appended**; the contract suite asserts it so a
second implementation cannot mean something else.

**`TranscriptRow` gains `person_id`.** Revision 2's `RecordStore.since` fixed the
query *direction* and left the *fields* — `TranscriptRow` carries `sender_id`
and `sender_name` but no `person_id`, so every `person_id`-shaped column in §6
was unpopulatable from the port that was supposed to feed them. Add
`person_id: str | None = None` (defaulted, so existing construction stays valid)
and select it in both reads:

```python
async def since(self, *, conversation_id: str,
                after_id: int, limit: int) -> list[TranscriptRow]: ...
```

`RoomLanguage` lives in `nora/domain/models.py` — forced, since `TagContext` is
declared there and `nora.domain` is the floor of the layers contract.

**`nora.language` must be added to `pyproject.toml`'s `layers` list**, between
`nora.doctrine` and `nora.skills`. Import-linter's layers contract only analyses
packages it *names*, so an unlisted package is invisible in both directions and
`lint-imports` reports clean on a wrong placement. The gate cannot make this
decision.

The LLM seam: `composition.py` builds a bare model client and injects it into
each consolidator's `__init__`, mirroring `TranscriptInjection`'s collaborators.
Not `nora.agent.factory` — that is agent-loop machinery at a layer
`nora.language` may not reach into.

`RoomLanguageReader` is the read port, so `TranscriptInjection` keeps taking only
Protocol-typed collaborators. §2.5's "output store not abstracted" holds for
writes.

## 6. Data model

```
consolidation_watermark  consolidator_name, scope_id, last_message_id, updated_at
                         PK (consolidator_name, scope_id)

language_observation     scope_id, conversation_id, message_id, person_id,
                         surface_form, canonical_form, sense, part_of_speech,
                         context, observed_at, extractor_confidence
                         UNIQUE (message_id, canonical_form, sense)

exchange_observation     scope_id, conversation_id, message_id,
                         actor_person_id, target_person_id, act,
                         replied_to_ts, latency_ms, observed_at,
                         extractor_confidence, target_source
                         UNIQUE (message_id, COALESCE(actor_person_id,''),
                                 COALESCE(target_person_id,''), act)

room_lexeme              scope_id, canonical_form, sense, first_seen_at,
                         last_seen_at, occurrence_count, distinct_speakers,
                         recognition_score, state, examples
                         PK (scope_id, canonical_form, sense)

room_exchange            scope_id, person_lo, person_hi, act, first_seen_at,
                         last_seen_at, count_lo_to_hi, count_hi_to_lo,
                         continued_count, median_latency_ms, score, state, examples
                         PK (scope_id, person_lo, person_hi, act)

room_portrait            scope_id, body, generated_at, evidence_count,
                         cited_forms, sha256, is_live
```

Corrections carried here, each from a found failure:

- **`sense` is IN the lexeme PK.** Outside it, a word acquiring a second meaning
  silently overwrote the first or merged counts from unrelated senses.
- **The pair key is canonical and per-direction counts are explicit.**
  `person_lo`/`person_hi` are the sorted pair, so a mutual pattern is ONE row;
  `count_lo_to_hi` and `count_hi_to_lo` are stored separately so reciprocity —
  and therefore §1.4's withhold — is **derivable from stored data** rather than
  implied by a render-time merge nobody specified. Revision 2's directional PK
  plus a single `reciprocity` column could not represent either.
- **NULL person ids have an explicit policy.** `person_id` is nullable until
  lazy adoption resolves a sender (`nora/infra/sql/audit.py:194-210`), and
  Postgres treats NULL as distinct from itself in a UNIQUE constraint — so
  revision 2's anti-double-count guarantee silently failed for exactly the
  unmapped population it claimed to protect. The UNIQUE index coalesces; and an
  observation with an unresolved actor or target is **held, not rolled up**,
  because `room_exchange`'s PK cannot take NULL. It joins a pair row only once
  adoption resolves both.
- **`target_source` records HOW the target was determined** — `reply`,
  `mention`, or `inferred`. This matters because a misattributed target names
  the wrong *person*, and its verbatim quote proves the utterance happened while
  proving nothing about who it was aimed at. V1 admits only `reply` and
  `mention`; `inferred` rows are recorded but never scored, so most banter goes
  uncounted rather than mis-aimed. That is the conservative direction, and §12
  carries it as a limitation.
- **`replied_to_ts` is named for what it is.** `messages.quoted_ts` is a
  timestamp, not an id. Resolution is **scoped to the current batch only** and
  resolves to `None` on any miss — never a guess — exactly as `_parent_of`
  (`nora/agent/prefill.py:240-257`) already does. A reply to a message from a
  previous cycle is the common case, not an edge case, and it simply goes
  unlinked.
- **`median_latency_ms` is advisory, not deterministic.** `NormalizedMessage.
  timestamp` is the sender's device clock, and `audit.py:190-192` documents that
  insertion order is not send order under catch-up delivery. Negative or
  implausible latencies are rejected before they reach the median, and the
  render never states a latency as fact.
- **Natural-key uniqueness on both observation tables**, `ON CONFLICT DO
  NOTHING`, so a retried or partial cycle cannot fabricate counts.
- **`is_live` gets a partial unique index** —
  `CREATE UNIQUE INDEX ux_room_portrait_live ON room_portrait (scope_id) WHERE is_live;`
  — with the flip and insert in one transaction and `unique_violation` counted
  as a concurrent-cycle collision rather than crashing the loop.
- **A new index serves the lag query**:
  `CREATE INDEX CONCURRENTLY idx_messages_dream_lag ON messages (conversation_id, id) WHERE is_nora = 0;`
  `is_nora = 0` is the correct predicate for an INTEGER column, per
  `audit.py:210`'s precedent. `CONCURRENTLY` is required — `messages` is written
  by every inbound turn and now grows without bound — and since
  `migrations/env.py` wraps migrations in one transaction this needs
  `op.get_context().autocommit_block()` in a hand-written migration. **This
  would be the repo's first use of `autocommit_block()`**, so it is new ground
  rather than a followed precedent. Without the index, revision 1 reproduced the
  exact defect §10 diagnoses in `prune_all`.
- **Timestamp types split as the repo splits them.** Epoch-ms `BigInteger` for
  values arriving from a platform wire (`observed_at`); `DateTime(timezone=True)`
  for Nora's own instants compared against `now` in SQL (`updated_at`,
  `generated_at`).
- **`message_id` / `last_message_id` are `sa.Integer`**, matching `messages.id`.
- **`state` gets a CHECK constraint** in a hand-written migration, with
  `tests/infra/pg_schema_baseline.sql` regenerated. States: `live`, `fading`,
  `stale`, `withheld` (§1.4).
- **`examples` is JSONB**, following `tool_call_log`'s Postgres-native
  precedent, each example carrying its own `message_id` and `person_id` so a
  redaction request has a query path.
- **`message_id` is a soft reference, no FK**, so evidence outlives the message.

### 6.1 Rendering caps and retention

The rendered set is capped by `max_rendered_lexemes` / `max_rendered_exchanges`,
sorted by score — a room active for a year otherwise accumulates entries
forever, on a block that sits in the uncached tier.

Retention for the observation tables is **not decided here** and is an open item
(§12): `context` and `examples` hold verbatim utterances, and since §10 nothing
in the system deletes anything.

## 7. The contract check

```
PORTRAIT
  non-empty, within a size bound
  evidence_count >= a floor                    <- gates generation entirely
  cites >= N entries FROM THE RENDERED SET     <- not merely from the table
  no observation whose text references Nora reached the generation prompt
  claims are paraphrase of counted structure   <- §3.1; register generalisation
                                                  is out of contract
       |
   pass -> becomes the live portrait
   fail -> previous portrait stays live, nora_portrait_rejected_total{reason} ++

SENSE AND ACT LABELS
  checked by construction: each renders directly above a verbatim quote from
  its own evidence. An entry with no quotable example is not rendered.

EXCHANGE ENTRIES
  withheld if reciprocity is strongly one-directional above a volume floor (§1.4)
  never rendered if either person is unresolved
  never rendered if target_source = inferred (V1)
```

Three of the portrait clauses are corrections: citation is against the
**rendered** subset (else the paragraph discusses a word the list omits);
`evidence_count` **actually gates** (it was previously a column gating nothing,
so eight occurrences produced a paragraph as confident as a year's history); and
the Nora-reference rule is enforced **at the input**, because a pronoun regex is
bypassed by third-person declarative — *"people here expect fast one-line
replies"* has no second person and no imperative and is behavioural instruction
anyway. Messages *about* Nora are `is_nora = 0` and thus eligible evidence a
portrait could otherwise launder into doctrine-adjacent prose.

Fail-closed throughout, R-27 shaped: a bad generation is `unavailable`, never a
silent overwrite.

## 8. Scope

`scope_id` defaults to `conversation_id`.

Cross-conversation grouping **reuses DEC-105's `workspace`** on `policy.toml`'s
`[[conversations]]`, motivated in the same words this design would have used.
Revision 1 invented a duplicate primitive in `config.toml`, which is both
redundant and the wrong file — trust topology lives in `policy.toml`.

Revision 2 then claimed the resolver "excludes `dm` conversations by
construction" and named no mechanism that could do it. The accurate statement:
`Conversation` (`nora/policy.py:38-83`) has **no type field**, and DMs are not
`Conversation` entries at all — they are a separate flat `[dm].persons` list, and
the Signal parser drops every non-group message today. So grouping resolves over
`policy.conversations` entries, which **are group-only by construction as the
schema stands**.

That is a property of today's schema, not a guarantee. **If DMs ever become
`Conversation` entries, this grouping must gain an explicit type check first** —
recorded here as a constraint on that future change, because §1.3's disclosure
argument depends on scope never exceeding the room.

Declared in V1, not exercised.

## 9. The cycle

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
    -> rescore, apply time decay, recompute withhold states
    -> regenerate portrait ONLY on material change
    -> advance the watermark            <- LAST, same transaction
```

- **The per-scope lock is mandatory.** Without it a slow extraction lets the next
  tick re-fire the same scope against the same un-advanced watermark, and two
  cycles append overlapping evidence. `ConversationSlots.try_acquire`
  (`nora/agent/relay.py:248`) is the existing precedent.
- **The watermark advance is last, in the same transaction as the appends.**
  Watermark-first loses a range permanently on a crash, and
  `nora_dream_evidence_lost_total` is blind to it, being defined for the pruning
  case only.
- **The 24h firing always rescores**, even on an empty batch — otherwise a
  dormant room never decays and its portrait stays live describing a slang era
  that ended. The rendered date interpolates from `generated_at`, never
  re-stamped at read time.
- **Per-scope try/except, and backoff on extraction failure.** `prune_loop` is a
  local DB sweep and the wrong precedent to copy wholesale; `supervise_adapter`'s
  capped exponential backoff (`nora/app/runtime.py:160-187`) is the right shape
  for an external provider. One scope's exception must not starve the others.
- **Partial extraction** commits what parsed and advances the watermark only to
  the last fully-processed message, never to the batch end.

**Material change**: an entry crossed into or out of the rendered set, a rendered
entry's `sense`/`act` changed, or no portrait exists yet.

### 9.1 Her own output is never evidence — necessary, not sufficient

`is_nora = 0` is non-negotiable: her speech re-entering as evidence is a runaway
loop. `idx_messages_is_nora` exists and `audit.py:210` already uses this
predicate for the same reason.

It does not close the loop. **A human who picks up a word she introduced
produces a legitimate row**, and the causal origin is invisible at this grain.
That also breaks the naive eval — if her rate and the room's rate inflate
together, the ratio stays healthy while both climb. §12 carries the stronger
measurement. Related and accepted: the cross-platform relay means a human may
echo vocabulary that originated in another room of the same workspace.

## 10. Dependency: the record must persist

Shipped and deployed 2026-08-15 (git `172bf30`). `prune_all` trimmed every
conversation to `max_per_conversation = 500` rows every 300 seconds. Measured
live: the busy group sat at **exactly 500**, earliest surviving message eight
days old, `min_id = 121`, with `"pruned 1 rows past the retention window"` in the
log — one row deleted per new message arriving. A rolling window, not a history,
and it looked identically healthy either way.

`store.prune_enabled` now decides whether the sweep is spawned at all. A switch
rather than a larger number, because `prune_all` runs a `row_number()` window
over the whole table every tick.

## 11. Modules and what is not in V1

```
nora/domain/models.py          RoomLanguage, RoomLexeme, RoomExchange; TranscriptRow.person_id
nora/domain/ports.py           RoomConsolidator, RoomLanguageReader, RecordStore.since
nora/language/observer.py      extraction — the one LLM call
nora/language/scoring.py       score, decay, state, withhold
nora/language/portrait.py      generation + contract check
nora/language/render.py        the prefill block, capped
nora/language/consolidator.py  LanguageConsolidator, ExchangeConsolidator
nora/infra/sql/language.py     the store
nora/app/runtime.py            dream_loop
```

`tests/contract/` gains a `RoomConsolidator` suite in V1 — two implementations
exist immediately, so the rule applies on day one.

**Not in V1:** adoption ladder; Graphiti; any Fact consolidator (§2.3); semantic
search over observations; cross-conversation grouping beyond the resolver seam;
human approval of a portrait; `inferred` targets contributing to scores; any
person-level characterisation, permanently (§1.3).

## 12. Measurement and open risks

**The eval that matters** is not "she must not use a room entry more often than
the room does" — a ratio that stays healthy while both sides inflate together
(§9.1). It needs **per-entry occurrence rate over time**, watching for
acceleration in the room's own usage correlated with hers after deployment.

Also required: **DEC-107** for §1.2's rule and a named test in
`tests/test_acceptance_rules.py::RULE_TESTS`; a test that turns red if the
`is_nora` filter is reverted; a portrait-rejection test proving the previous one
survives; a withhold test proving a strongly asymmetric pattern never renders at
any count; and a cache-cost measurement of the rendered block.

Open risks:

- **Act classification is a hard semantic judgment** and gets less scrutiny than
  the portrait (§1.2). §1.4's withhold is the only structural mitigation.
- **The channel is not a census** (§2.1) — reactions especially, which is the
  signal exchange patterns most need.
- **The human-mediated feedback loop has no mechanical fix** (§9.1).
- **A generated `sense` can be wrong.** The quote beneath makes it visible, not
  impossible.
- **`reply`/`mention`-only targeting means most banter goes uncounted** (§6).
  Under-counting is the accepted direction.
- **Retention for observation tables is undecided** (§6.1).
- **Extraction is a recurring LLM cost.**
- **A new room has no evidence**, and the correct behaviour is no block at all.
