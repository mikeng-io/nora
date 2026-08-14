---
kind: spec
title: "The Dreamer: room-language consolidation from the conversation record"
description: "An asynchronous consolidator that distils how a room actually talks out of the Postgres record, into a per-room composite of an LLM-written culture paragraph and a deterministically-rendered glossary, injected in its own prefill tier between doctrine and the volatile context blocks. Honcho keeps people; the Dreamer keeps language. Generic Consolidator port and watermark table so a later Fact aggregator reuses the loop. Depends on store.prune_enabled = false, shipped the same day."
tags: [nora, spec, dreamer, language, consolidation, memory, prefill]
timestamp: '2026-08-15'
status: accepted
derives_from:
  - design/09-decision-index.md
  - docs/superpowers/specs/2026-08-04-nora-architecture-design.md
---

# The Dreamer: room-language consolidation

## 0. What this document is

The operator, mid-conversation with another model about "slang optimization",
arrived at a shape worth building and then corrected it twice in ways that
changed the design more than the original proposal did. Both corrections are
recorded here because the design is mostly downstream of them.

The starting idea: Nora should adapt to the slang of the room she lives in.
A group develops its own vocabulary — `firm` for 正/掂, a word that arrives
one week and is dead the next — and a bot that never picks any of it up reads
as a visitor forever.

The first shape proposed was a per-message slang table plus a standing top-N
list injected into the prompt. That is not what this document specifies. What
survives from it: the split between **recognising** a word and **using** one,
the rule that her own output is never evidence, and the insistence that an LLM
does extraction while deterministic policy does promotion. Those three were
right and are kept.

## 1. The two corrections that shaped this

### 1.1 "Filtered injection will make Nora mock our slang, not learn from it"

The first design in this conversation proposed injecting a lexeme **only in the
turn it was said** — intersect the lexicon against the inbound message, render
the gloss for whatever matched. It looked safe: no standing list, therefore no
menu, therefore no over-use.

The operator rejected it, correctly, and the reasoning is worth preserving
because it is not obvious. A word delivered only at the moment of use can only
ever be reflected back. The prompt then holds the word twice — once in the
transcript, once glossed underneath — and the gloss *draws attention to it* at
exactly the moment repeating it is most available. The likely output is
same-turn echo, which is the most conspicuous form of imitation there is.

Worse, it can never be anything else. The word is never present when it would be
*apt*, only when it has already been said. Filtered injection therefore cannot
produce learning **by construction**, whatever the scoring underneath it is.
The original framing optimised for "cannot over-use it" and bought "can only
parrot it", which is the worse failure.

### 1.2 "It should be a composite, not just the list"

The alternative — a standing top-N list — solves echo (the word is ambient, so
use is distributed rather than locked to the turn it appeared in) but
reintroduces a scored word list in the prompt.

The operator's resolution: **a description of the room's slang culture, plus the
list.** Not either.

The reason this is not a violation of the repo's own «positions generate, lists
close» finding (DEC-94/95/96) is worth stating precisely, because it looks like
one. That finding is about doctrine describing **Nora** — listing three emoji
collapses her whole emoji vocabulary to three, because the list stands in for
her range. The glossary here does not describe her range. It describes **the
room's observed vocabulary**, which genuinely is a finite, known set, and a
closed set is the correct representation of "these are the words we have
actually seen."

The failure mode is therefore not closure. It is **the list being read as a menu
rather than as a glossary** — words-to-use rather than words-that-mean. That is
a framing problem, and §6 fixes it by framing, by placement, and by making the
glossary something the model cannot write.

## 2. Scope, stated honestly

The operator selected "recognition only" before the composite was proposed. The
composite widens that, and this document does not pretend otherwise: **a
description of the room's register, injected doctrine-like, will change how she
talks.** That is generation.

This is accepted deliberately, because it is the better form of it. She acquires
the *register* — «Cantonese grammar with whole English words dropped in as
adjectives» — and composes `幾firm喎` from grammar she already has. She is never
handed a list of words to reach for. Adoption happens through disposition, not
through word-picking, which is exactly the mechanism that makes §1.1's mimicry
impossible.

What remains out of scope for V1 is in §11.

## 3. Why not Honcho, and why not Graphiti

### 3.1 Honcho

Honcho's `representation()` is `observer -> observed person`. The disclosure
subset (R-31) fails closed on any unknown **person** in the room. «`firm` is how
this room talks» has no observed peer, so a `__room_language__` fake peer would
not merely be inelegant — it would sit outside R-31's gate entirely, which is a
correctness argument rather than an aesthetic one.

Honcho keeps people and episodes. The Dreamer keeps language. One record, two
derived views, and neither learns the other's domain.

### 3.2 Graphiti

Raised by the operator as a possible future backing. Declined for this
consolidator, and the reasoning generalises.

Graphiti models entities and relationships over bi-temporal validity intervals.
A lexeme genuinely is temporal (`first_seen_at` / `last_seen_at` / fading), so
the fit is not absurd. But it buys a Neo4j/FalkorDB dependency and a second LLM
extraction pipeline to replace two Postgres tables and a scoring function, and
it reproduces the same ontology mismatch as Honcho — an entity-and-edge store
asked to hold "how a room talks".

The useful reframe: if the Dreamer is *"the asynchronous process that turns the
authoritative record into revisable derived beliefs"*, Graphiti is a candidate
**backend for a later consolidator** — the relationship/episode one — not for
this one. §4's port is narrow enough that such a swap does not require
redesigning anything specified here.

## 4. The abstraction seam

The operator asked for this explicitly, naming a future "Fact" aggregator. The
shared spine is real, which is what makes abstracting it honest rather than
speculative. Both a language and a fact consolidator: read a batch from a
watermark; human messages only; scoped to a conversation; LLM extracts
structured candidates; **deterministic** consolidation; derived rows carrying
provenance back to a `message_id`.

```python
class Consolidator(Protocol):
    name: str
    async def consolidate(self, batch: Sequence[TranscriptRow]) -> int: ...
```

`dream_loop` in `nora/app/runtime.py` drives a list of them, sitting beside
`prune_loop` and `debounce_sweep_loop` and following their failure discipline
exactly: log, count `nora_errors_total{stage=...}`, never take the loop or the
process down.

One generic `consolidation_watermark` table, keyed `(consolidator_name,
scope_id)` — "how far has this consumer read" is identical across domains.

**What is deliberately NOT abstracted: the output store.** Language wants a
lexicon; facts want a different shape with a different lifetime. A generic
`derived_belief` table would be the fake generality that makes both worse. Each
consolidator owns its schema.

Per `CLAUDE.md`, a port whose second implementation is coming gets a shared
contract suite under `tests/contract/` in V1, with one implementation. That is
what makes the future `FactConsolidator` both cheap and safe.

## 5. Data model

```
consolidation_watermark   consolidator_name, scope_id, last_message_id, updated_at
                          PK (consolidator_name, scope_id)

language_observation      append-only evidence
                          id, scope_id, conversation_id, message_id, person_id,
                          surface_form, canonical_form, sense, part_of_speech,
                          context, observed_at, extractor_confidence

room_lexeme               scope_id, canonical_form, sense,
                          first_seen_at, last_seen_at,
                          occurrence_count, distinct_speakers,
                          recognition_score, state, examples
                          PK (scope_id, canonical_form)

room_portrait             id, scope_id, body, generated_at, evidence_count,
                          sha256, is_live        -- append-only; history kept
```

Three decisions inside that:

- **`message_id` is a soft reference with no foreign key.** Evidence must
  outlive the message it came from. Pruning is off as of today (§9), but the
  flag exists and a future operator may turn it on; an FK would make the
  observation table inherit the record's retention policy, which is the one
  property it must not have.
- **`room_portrait` is append-only with an `is_live` flag**, not an update in
  place. Portrait drift is a real risk (§12) and the history is how anyone
  diagnoses or reverts it.
- **`state` in V1 is derived, not a ladder.** It is a function of recency and
  frequency with two meaningful values for rendering — live and fading — plus
  stale, below which a lexeme stops being rendered at all. This is NOT the
  `observed -> familiar -> active -> stale` promotion ladder in §11, which
  governs whether she may *use* a word and is out of scope; V1 has no such gate
  because it has no per-word generation decision to gate.
- **`scope_id` defaults to `conversation_id`.** Honcho blends person conclusions
  workspace-wide but scopes raw-message search to a session precisely so a
  private DM does not leak into a group; language has the identical hazard and
  takes the identical default. Cross-platform grouping (one Signal group and one
  Discord channel being the same set of people) is a config seam in an optional
  `[language]` section — declared in V1, not exercised.

`ts`-shaped columns are `BigInteger` for the reason recorded in
`nora/infra/sql/schema.py`: epoch milliseconds are ~1.78e12 and overflow INT4 on
the first row.

## 6. What is generated and what is rendered

This is where the safety lives, and it is the section to attack first if
attacking any.

- **The culture paragraph is LLM-written.** One paragraph, about the room.
- **The glossary is a deterministic render of `room_lexeme` rows.** Never
  generated. The model that writes the paragraph cannot invent a word into the
  block, because it never writes that half.
- **The framing lines are authored template text.** «This is what their words
  mean. It is not a set of words to reach for.» is ours, fixed, and cannot
  drift with a regeneration.

The block:

```xml
<room_language>
呢個 room code-switch 得好密 — Cantonese grammar 夾成個英文字入去做形容詞。
粗口係標點符號，唔係加強語氣；撚/柒 帶節奏多過帶火。同意壓縮到一兩個音。
新字入得快、用得狠、死得快 — 有人用你唔識嘅字係平常事，唔係要你問。

Words this room has made its own:
  firm  — 正/掂, of a plan or idea that holds up     31×, 4 people, still live
  kam   — 尷, cringe-adjacent, of a situation        12×, 3 people, still live
  chur  — 谷, to grind or be ground                   8×, 2 people, fading

Observed from what they said, current to 15 Aug.
This is what their words mean. It is not a set of words to reach for.
</room_language>
```

The counts are load-bearing twice over: they are provenance, and they are a
confidence signal the model can read — `8×, 2 people, fading` is visibly weaker
evidence than `31×, 4 people`.

### 6.1 The contract check

Because only one paragraph is generated, the check has one thing to police:

```
non-empty, within a size bound
cites >= N canonical forms that EXIST in room_lexeme for this scope
makes no claim about Nora (no second person, no imperative directed at her)
     |
 pass -> becomes the live portrait
 fail -> the previous portrait stays live,
         nora_portrait_rejected_total{reason} increments
```

Fail-closed and R-27 shaped: a bad generation is `unavailable`, never a silent
overwrite. The "cites lexemes that exist" clause is the one that binds the
paragraph to the real property rather than to plausibility — a portrait
describing a register nothing in the table supports is not a portrait, it is a
guess about Hong Kong group chats in general.

The "no claim about Nora" clause exists because of a documented precedent:
`nora/engagement/context.py:189-212` records that `peer_card` was rejected for
the self-card after hers asserted «Kind: AI chatbot/assistant», contradicting
`identity.md`, on the grounds that **a learned claim reads as evidence next to
an authored one and wins on injection.** This design deliberately places
generated prose near authored voice doctrine, so it must forbid the generated
half from ever describing her. It describes the room. Never her.

## 7. Placement in the prefill

Its own tier, between doctrine and the volatile context blocks:

```
doctrine                     authored, versioned, doctrine_sha256
─────────────────────────
room_language                derived, per-room, dated
─────────────────────────
context_header / clock / platform
self_card / peer_card / peer_history
group_transcript
tag_message
```

The operator asked whether this should go **into** doctrine or be a
**doctrine-like** injection. Into doctrine is refused, on three independently
sufficient grounds:

1. `doctrine_sha256` is one hash over the authored corpus, and it is the
   provenance every claim about a turn re-derives from. Room language is
   per-room — a Signal group and a Discord channel have different registers —
   so writing generated per-room text into that tree makes the hash mean
   nothing.
2. Doctrine is authored, and a version bump records that a human wrote it. A
   file the Dreamer rewrites at 4am has no author, and mixing the two means
   nobody can tell by looking which is which.
3. Mechanically it does not fit: doctrine loads once at startup from a read-only
   mount; the Dreamer writes continuously, per room.

Doctrine-like placement gives it what the operator was actually reaching for —
more authority than a transcript, less than `voice.md`.

`TagContext` gains `room_language: RoomLanguage | None`, assembled inside the
existing `asyncio.gather` in `TranscriptInjection.assemble`. One indexed read,
no new round trip, and written not to raise — the same property its four
siblings have, and for the same reason recorded there: one exception in a
`gather` cancels its siblings and takes the turn down.

## 8. The dream cycle

```
tick every DREAM_TICK_SECONDS (~5 min), per scope:

    lag = count(messages where id > watermark and is_nora = 0 and in scope)

    fire if   lag > SAFETY
       or     the room has been quiet for Q minutes and lag > 0
       or     age(watermark) > 24h
    else      skip

    -> fetch batch
    -> LLM extraction, schema-forced -> observations
    -> append to language_observation
    -> deterministic rescore of room_lexeme
    -> regenerate the portrait ONLY on material lexicon change
```

Lag-bound with a quiet-gap preference rather than a fixed nightly hour. The
rhythm the operator wanted is nightly; the *bound* is what makes it safe, and
the two compose — it fires at a quiet gap when one is available and does not
wait for one that never comes. A room that never goes quiet still dreams.

`conduct.md` puts the day boundary at «a day ends when somebody sleeps, not at
midnight» (commit `9a034d5`) and a test fails if that drifts into a clock time.
The quiet-gap trigger is that same position expressed as a daemon, but note it
is **new code, not a reuse** — that doctrine is prompt text and there is no
runtime day-boundary primitive to borrow.

The portrait is not regenerated every cycle. Extraction is per-cycle; portrait
generation happens only on a **material lexicon change**, which is what keeps
the LLM cost bounded. Material means one of: a lexeme crossed into or out of the
rendered set (live/fading, per §5); a rendered lexeme's sense changed; or no
portrait exists for the scope yet. A cycle that only increments counts on
already-rendered lexemes is not material and regenerates nothing.

`DREAM_TICK_SECONDS`, `SAFETY`, `Q`, the 24h ceiling, and §6.1's `N` are named
here and given values in the implementation plan, each with the reasoning for
the number recorded next to it — they are parameters of this design, not gaps
in it. `SAFETY` in particular is no longer bounded by the retention window
(§9) and should be chosen against batch size and extraction cost instead.

### 8.1 Her own output is never evidence

`is_nora = 0`, non-negotiable, and the reason is a feedback loop that the
original proposal identified correctly:

```
Nora emits "firm撚爆" -> Dreamer observes it -> lexeme strengthens
   -> portrait describes it as normal here -> she uses it more -> ...
```

Human speech is evidence. Her speech is not. `idx_messages_is_nora` already
exists, and `nora/infra/sql/audit.py:210` already uses this predicate for the
same "human evidence only" reason.

## 9. Dependency: the record must persist

Shipped and deployed 2026-08-15, before this document, because the Dreamer is
not buildable without it.

`SqlRecordStore.prune_all` trimmed every conversation to
`max_per_conversation = 500` rows every 300 seconds. Measured on the live
database that day, the busy group sat at **exactly 500** rows with its earliest
surviving message eight days old and `min_id = 121`; the application log carried
`"pruned 1 rows past the retention window"` — one row deleted per new message
arriving. The record was a rolling window, not a history, and it looked healthy
either way.

`store.prune_enabled` now decides whether the sweep is spawned at all, and
Nora's deployment sets it false. It is a switch rather than a larger number
because `prune_all` runs a `row_number()` window over the whole `messages` table
on every tick — widening the window would pay for that scan forever in order to
delete nothing.

Consequences carried by that change: `nora/infra/sql/schema.py` justified `id`
being INT4 with "bounded forever by the per-conversation retention window", a
premise that died and has been rewritten with the real reason (`ts` *arrives*
at ~1.78e12; `id` *counts up*, and INT4's 2.1e9 is millennia of group chat).
Attachment bytes still have no sweep of their own, so disk is now the thing to
watch — noted in `config.example.toml`, not solved here.

With the record durable, `nora_dream_evidence_lost_total` should be
structurally zero. It stays as the fail-closed tell if anyone re-enables the
sweep: a watermark below the oldest surviving `id` is `unavailable` («I could
not see those»), never `nothing_found` («nothing happened»).

## 10. Modules

```
nora/language/models.py        domain types
nora/language/observer.py      extraction — the one LLM call
nora/language/scoring.py       deterministic score + state
nora/language/portrait.py      generation + the contract check
nora/language/render.py        the prefill block
nora/language/consolidator.py  LanguageConsolidator — the port implementation
nora/infra/sql/language.py     the store
nora/app/runtime.py            dream_loop
nora/domain/ports.py           the Consolidator protocol
```

One responsibility each, per the repo's convention — the split is by what each
is answerable for, not by line count. The implementation plan must confirm the
`nora/language` placement against `lint-imports`' contracts before writing code;
the domain types may need to live under `nora/domain` to satisfy them.

## 11. Not in V1

Adoption ladder and `adoption_score` (the `observed -> familiar -> active ->
stale` promotion machinery); Graphiti; `FactConsolidator`; semantic search over
observations; cross-platform scope grouping beyond the config seam; any
human-approval step on a portrait.

Portraits go live automatically on passing the contract check. Staging them for
review was considered and declined: the check is mechanical and fail-closed, and
a recurring approval chore is a cost with no matching safety return once the
generated surface is one paragraph that cannot mention her and cannot invent a
word.

## 12. How we would know it works, and what could still be wrong

The measurement that matters, because it is a number rather than a vibe:
**she must not use a room lexeme more often than the room does.** Her
per-lexeme rate against the humans' rate over a window; over-performance is
observable and fails the eval.

Also required: an entry in `design/09-decision-index.md` and a named test in
`tests/test_acceptance_rules.py::RULE_TESTS`; a contract-suite test that turns
red if the `is_nora` filter is reverted; a portrait-rejection test proving the
previous portrait survives a failed check.

Open risks, unsolved:

- **The culture paragraph can be wrong or stereotyped**, and there is no ground
  truth for "how this room talks". The contract check catches malformed, not
  mistaken. A paragraph that describes a generic Hong Kong group chat rather
  than *this* one would pass every clause in §6.1 and still be worthless.
- **It sits near `voice.md` and can still contradict it.** The contract narrows
  this to claims about her, but a register description that clashes with
  authored voice remains possible and nothing detects it.
- **Extraction is a recurring LLM cost** for as long as the daemon runs.
- **A new room has no lexicon**, and the cold-start portrait is either absent or
  built on evidence too thin to mean anything. Absent is the correct behaviour
  and the renderer must produce no block at all rather than an empty one.
