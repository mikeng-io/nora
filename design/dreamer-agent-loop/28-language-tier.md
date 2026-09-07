---
type: contract
title: "The Language Tier — The Most Complete in Code, The Least Designed Here"
description: "Three adaptors, four tables, a real lifecycle and five wired metrics — and no node in this bundle ever owned it. Its caps, mechanism, transit, storage, measurement and tool surface, plus the coverage matrix that found the gap."
tags: [nora, language-tier, lexeme, exchange, portrait, coverage, cms]
timestamp: '2026-08-24'
edges:
  - {dst: 12-self-correction, rel: depends_on, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
  - {dst: 09-measurement, rel: extends, provenance: asserted}
---

> **DEPRECATED 2026-09-07 — the language tier has been deprecated and its code deleted, as part of the same decision that deprecated the knowledge tier.**
> This document is retained as a record of the reasoning, not as a description of the system. **Nothing here describes code that exists.**
> The language tier never ran in production — `[language] enabled = false` throughout its life. Its tables — `language_observation`, `room_lexeme`, `exchange_observation`, `room_exchange`, `room_portrait` — are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.

# The Language Tier

## 1. The inversion that found this node

This bundle cites the language tier in **eleven nodes** — the two contradictory
lexeme rows, the tools-less completion, the missing supersession, the
`[language]`-named cadence. It cites it as **evidence** every time and as a
**subject** never. There was no design node for it.

Meanwhile it is, in code, **the most complete tier in the repo**: three
adaptors, four tables, a real scoring function, a three-state lifecycle, and
**five metrics that are actually wired** — where the note tier declared ten and
shipped zero until 2026-08-27, and now ships the three that have call sites
([27](27-note-dreamer.md) §6.1).

**Most built, least designed.** That is [04-designed-vs-shipped.md](04-designed-vs-shipped.md)'s
pattern in its purest form: the thing everyone points at and nobody owns.

## 2. Three adaptors, one tier

| adaptor | objective | job |
|---|---|---|
| **room-language** | `room-language.md` | extract lexeme candidates from a message batch |
| **room-language-verification** | `room-language-verification.md` | check a candidate against known entities before it becomes a gloss |
| **room-exchange** | `room-exchange.md` | observe an exchange — who said what to whom, and what kind of act it was |

**All three are tools-less**, which is F1: with no tools the stopping condition
is satisfied on pass one, so each is a **single completion**, not a loop
([03-no-agent-loop.md](03-no-agent-loop.md)).

## 3. Storage — four tables

| table | holds |
|---|---|
| `room_lexeme` | a word or phrase the room uses, with its gloss and its counts |
| `exchange_observation` | one observed exchange, with `message_ids` and `participants` (both `>= 2` by `CHECK`) |
| `room_exchange` | the consolidated act between two people |
| `room_portrait` | the room's synthesised character, injected on the tag path |

`room_portrait` is the one with a stated identity rule: its narrative **cannot
reference a person** — enforced at the type boundary, because a `CHECK` cannot
forbid *"contains a person's id"* in a free-text column.

## 4. Mechanism — watermark, score, decay

**The pass**: watermark-driven over new messages, `batch_limit = 400`, on a
`tick_seconds = 300` clock with `safety_lag = 300`, `quiet_seconds = 1800` and
`max_age_seconds = 86400` (which fires at `lag == 0` on purpose, so a dormant
room does not buy a model call a day for nothing).

**The score** is the tier's real mechanism and the one thing this bundle should
have been reusing all along:

> `score = f(occurrences, log1p(distinct_speakers))`

**Spread is logarithmic, count is not.** One person saying a word ten times and
five people saying it twice are not the same signal, and the log is what stops a
single enthusiastic speaker minting room vocabulary. **This is the precedent
[21-substrate-contract.md](21-substrate-contract.md) §6.1 cites for
promotion-by-arithmetic**, and it is worth naming that the pattern was invented
here first.

**The lifecycle** is age-based, not score-based: `live` → `fading` → `stale`,
and **a stale entry stops rendering entirely.** The docstring is careful that
this is *"rendering state, NOT a permission to use the word — there is no
per-word generation gate."*

## 4.1 D34 — the tier has no MEASURE, and that is what produced the two live rows

**Decided 2026-08-27**, from the live rows rather than from the design.

The pass in §4 runs **comprehend → serialize**: read a batch, produce a gloss,
write a row. There is no **measure** between them — no grounded, fail-closed
signal that decides whether this term is worth storing at all. That missing verb
is the whole defect, and the store shows it:

```
Big Walk  | "a specific long walking activity/event this group plays,
          |  where they hunt for gourds"          n=1  spk=1  score 2.233
big walk  | "群內約定俗成嘅散步/行路活動，可能係夜晚出去行街"   n=1  spk=1  score 2.216
```

**Both are one sighting by one speaker.** Neither has ever been corroborated.
And they are not two competing glosses of one word — they are two *different
kinds of thing*: the second is a **compositional restatement** of what the
English words already mean, which any model would infer unaided, and the first is
a **reference** to a specific object in the world (*"this group plays"*,
*"gourds"*) wearing a gloss's clothes.

### 4.1.1 The tier's job is the DELTA, not the meaning

> **Every model already knows what a word means. This tier exists to record where
> this room's usage DIVERGES from what a model would assume.**

A row whose sense matches the baseline is not neutral, it is **negative value**:
it spends prompt budget on every turn to tell the model something it already
knew, and hands it a chance to be wrong. `打風` needs no row. `big walk` needs
one only because the room means something a model would not guess.

### 4.1.2 The measure is a SELECTIONAL VIOLATION, and one sentence can carry it

The operator's example is the mechanism:

```
今晚玩唔玩 Big Walk?   →  standard reading: "今晚玩唔玩行路呀?"
                          nobody says this. The sentence does not cohere.
```

**玩** selects for games and things-played; it does not take 行路 as an ordinary
object. The anomaly is **not in the term** — it is in the mismatch between the
verb's selectional frame and the term's default sense, and that is decidable from
the sentence's own structure by a model that already holds the selectional
knowledge.

**This supersedes an earlier reading in this bundle** that divergence needs
recurrence across speakers. It does not. Recurrence is a slow proxy for something
the grammar states directly in a single sentence.

**And the violated frame TYPES the result** — which is what makes this more than
an anomaly flag:

| frame | forces the term to be |
|---|---|
| **玩** X | a game / something played |
| **去** X | a place |
| **食** X | food |
| **睇** X | something watched |

So the detector does not merely say *"this is odd"*; it says *"the only coherent
reading is X-as-a-game."* That routes the write: a term whose only coherent
reading is **a specific thing** is a **name**, and a name belongs in
`fact_entity` (§5.2), never in a `sense` column. **The name-versus-meaning
category error resolves itself from the frame.**

### 4.1.3 The three-state result, and why one row was written from an ambiguous sentence

```
anomaly, frame resolves    -> write, TYPED by the frame
anomaly, frame ambiguous   -> HOLD as a candidate; wait for a decisive sentence
no anomaly                 -> write NOTHING
```

The last line carries most of the value, and the middle line explains the live
data. Compare the two sentences:

```
今晚去 big walk?      -> 去 IS compatible with a walking activity. NO anomaly.
今晚玩唔玩 Big Walk?  -> 玩 is not.                                ANOMALY.
```

The tier glossed from the **ambiguous** sentence and wrote a confident row. The
rule is therefore not only *detect anomalies* but **do not write from a sentence
that carries no discriminating evidence** — which is what fail-closed means for
this tier.

## 5. Caps

| cap | value | where |
|---|---|---|
| `batch_limit` | 400 | messages per pass |
| `max_rendered_lexemes` | **6** | the render cap — how many reach the prompt |
| `verify_max_candidates` | 10 | candidates the verification pass will check |
| `timeout_seconds` | 120 | per call |

**No storage cap on a gloss.** A lexeme's gloss is free text with no length
bound — the same gap [27-note-dreamer.md](27-note-dreamer.md) §5.2 found on the
note body, and it lands the same way: `max_rendered_lexemes = 6` bounds the
*count* that reaches the prompt and nothing bounds the *size* of each.

**Decided, on the established pattern:** `[language] max_gloss_chars`, operating
**200**, ceiling **800**. Two hundred matches the note description's budget,
which is the closest comparable — both are a short line injected on every tag.

## 5.2 It builds ON the knowledge substrate — decided

Operator decision, 2026-08-24. The tier keeps its own store; it gives up its own
**entity identity** and its own **supersession implementation**.

| what it borrows | from |
|---|---|
| entity identity — `canonical_form` resolves to a `fact_entity` row | [21](21-substrate-contract.md) §5 |
| the supersession pattern — `superseded_at` / `superseded_by` | [20](20-knowledge-tier.md) §1 |
| the retrieval seam's lexical channel, if its extent ever outgrows the prompt | [24](24-embedding-seam.md) §5.1 |

| what stays its own | why |
|---|---|
| the `live → fading → stale` lifecycle | a gloss is a **rendering** concern; a fact has no such state |
| the occurrence × log1p(speakers) score | §4 — invented here, and the precedent the knowledge tier borrows back |
| its own block and render cap | six lexemes is a working set, not a slice of a store |

**The live failure is the argument.** `Big Walk` and `big walk` are two rows
that **cannot see each other**, which is why neither can supersede the other and
both render. Resolving both to one entity is the precondition for the fix — and
it is the same column the knowledge tier needs for four other reasons
([20](20-knowledge-tier.md) §5).

**And it makes a gloss checkable.** Once a lexeme points at an entity, the edges
about that entity are evidence about the gloss: `Big Walk │ made_by │ House
House` is what turns *"a walking activity, maybe at night"* from doubted into
**refuted** — which is what [30](30-review-response.md) §2.1.1's step 6 acts on.

**Not a fold-in.** [30](30-review-response.md) §8 records why: the objection was
duplicated supersession, not duplicated storage, and folding would have cost the
scoring function and the lifecycle for nothing.

## 6. Transit — how a word becomes a rendered gloss

```
messages (watermark)
   → room-language          candidate lexeme + gloss
   → room-language-verification    checked against known entities
   → rescore                occurrences, distinct_speakers, last_seen
   → state_for              live | fading | stale
   → render                 top max_rendered_lexemes, live only
```

**The break is between verification and rescore**, and it is D19's subject: a
second observation of the same lexeme **does not supersede the first**. Both
rows stand, both render if both are live, and the room gets two contradictory
glosses side by side with no marker that they disagree
([01-live-state.md](01-live-state.md)).

**With supersession** (D19), the second observation supersedes the first, both
rows remain readable for the transaction-time question, and **only standing rows
render**. The compound key keeps them distinct; supersession decides which one
speaks.

## 6.1 The render has no floor — verified

`SqlLanguageStore.room_language` applies a plain
`ORDER BY recognition_score DESC LIMIT :limit`, and **there is no minimum score
anywhere in the codebase** — grepped 2026-08-27, no `min_score`, no floor, no
threshold predicate.

**In a near-empty store, ordering without a floor means the worst row is still
the best row.** The two rows above score 2.233 and 2.216 and are the entire
lexicon, so both render, at n=1 and one speaker each.

**A score floor removes both live wrong rows today** — no supersession, no
correction loop, nothing asked of anybody. It is the cheapest fix in this bundle
and it is passive, which §4.1 argues is the correct shape for this tier.

## 7. Tools — none, and that is the finding not the design

The tier holds **no tools at all**, in either direction:

- **no tools for its adaptors** — F1's tools-less completion;
- **no read tool for Nora.** She sees `max_rendered_lexemes = 6` in her prompt
  and has no verb to ask about the rest.

The second is the same shape as the knowledge tier's extent problem
([20-knowledge-tier.md](20-knowledge-tier.md) §6) — **and it resolves the other
way.** Lexemes are bounded by the room's own slang and decay out of the corpus
by age, so this tier is **injectable**, and six is a reasonable working set. It
does **not** join the retrieval seam ([24-embedding-seam.md](24-embedding-seam.md)
§5), and that decision is unchanged by this node.

What it does owe is an **extent line**, for the same reason Standing and the
knowledge tier do: six rendered out of how many is a fact Nora currently cannot
see.

## 8. Measurement — five wired, and the two that are missing

**Wired today**, which is more than any other tier can say:

`nora_language_candidates_dropped_total` · `nora_language_parse_failures_total` ·
`nora_exchange_candidates_dropped_total` · `nora_exchange_parse_failures_total` ·
`nora_language_verification_total`

**Missing, and both are the tier's own failures:**

- **supersessions applied** — D19's whole point. A tier that gains supersession
  and never counts one has not gained it; this is
  [09-measurement.md](09-measurement.md)'s Self-correction row, which currently
  *"the language tier structurally cannot do and nobody would currently
  notice."*
- **lexemes by lifecycle state** — `live` / `fading` / `stale`. Without it,
  "nothing rendered" and "everything went stale" are the same observation, which
  is `unavailable` wearing `nothing_found`.

## 9. The coverage matrix, which is why this node exists

The operator asked whether every dreamer had been taken through caps, mechanism,
measurement, transit, storage and tools. **The audit said no.**

| tier | design node | caps | mechanism | measurement | transit | storage | tools |
|---|---|---|---|---|---|---|---|
| **knowledge** | [25](25-knowledge-pipeline.md) | §9.1 | yes | yes | yes | yes | yes |
| **Standing** | [26](26-standing.md) | yes | yes | yes | yes | yes | yes |
| **note** | [27](27-note-dreamer.md) | yes | yes | yes | yes | yes | yes |
| **language** | **this node** | §5 | §4 | §8 | §6 | §3 | §7 |

Before this node the last row was empty in every column. **The gap was not that
the language tier was undecided — it is the most decided tier in the repo. The
gap was that nothing in the design owned it**, so its rules lived only in code
and its defects were quoted as anecdotes about other tiers.

## 10. PARKED — the measure verb was built, reviewed, and stopped

**2026-08-28, operator's call.** D34 (§4.1) was implemented, cold-reviewed, and
**deferred** so the effort goes to Standing and the note tier. This section is
what a cold reader needs *before* writing any code here; the branch is
`worktree-lang-tier` @ `bd522bf`, unmerged, and the floor half is
`worktree-lang-floor`, also unmerged.

**Nothing below is speculation.** Every claim was read back from the code or the
live database, and each says which.

### 10.1 The blocker — the fail-closed state is not one the operator can leave

The measure requires four fields from the model. Counted in the shipped
`nora/dreamer/objectives/room-language.md`:

```
frame              0 occurrences
frame_expects      0
standard_reading   0
reading            1   (and not in the shape the measure reads)
```

So every candidate returns `unmeasured` and **the tier writes zero rows,
indefinitely.** That much was disclosed and intended: objective text is the
operator's, and a tier that cannot check must say `unavailable` rather than
write guesses.

**What nobody saw is that he cannot land the wording.** Three facts, each
verified in the tree:

| fact | where |
|---|---|
| the body is pinned byte-for-byte | `tests/unit/test_dreamer_instructions.py::test_the_objective_file_is_the_pre_move_constant_byte_for_byte` |
| and pinned again from the observer's side | `::test_the_extraction_prompt_still_starts_with_the_pre_move_text` |
| `nora/dreamer` is bind-mounted **from the repo** | `docker-compose.yml:263` |

An operator edit **is a repo edit**, and a repo edit has to pass the gate. The
two pins exist for a real reason — the instruction substrate's verbatim-lift
phase (§7.1 step a) — but until the re-partition retires them, *any* rewording
of this objective is gate-red. **Resuming this work starts with that, not with
the measure.**

### 10.2 S2 — the one that loses data, and the precedent is nine lines away

`unmeasured` drops the candidate and returns `parsed=True`, so `consolidate`
returns `0` **exactly as a genuine empty extraction does**, and
`nora.app.runtime._dream_once` reaches its `if batch:` arm and calls
`advance_watermark(last_message_id=batch[-1].id)`. Those messages are never
re-read.

Under §10.1 that is **the entire group's traffic consumed by an instrument that
is not connected.**

The argument against it is already in this codebase, in
`nora.language.consolidator.ExtractionUnparseableError`: *"this batch was never
actually read, so it must not be treated as 'read and found nothing' by anything
downstream, including the watermark."* The branch does the opposite for the one
state that means precisely that.

**The fix, when this resumes:** if every well-formed item dropped `unmeasured`,
return `parsed=False` so the batch is retried rather than burned. Note the
asymmetry — the HOLD argument (drop, and wait for the room to repeat itself) is
right for `frame_ambiguous` and **wrong** for `unmeasured`: a room repeats its
slang, but it does not repeat itself for an instrument that was never wired.

### 10.3 §6.1.1's arithmetic, recomputed by hand — do not re-derive this

The floor is on **corroboration** rather than on the score, and the numbers are
the reason. Against `nora.domain.language_scoring` as it stands:

```
one sighting, one speaker, age 0    ->  2.26355
two sightings,             45 days  ->  2.1257   <- BELOW the one-sighting score
```

So a score floor set to exclude a single fresh sighting **also excludes a
twice-seen lexeme at about six weeks** — it silently becomes a second decay
gate, with a second threshold, beside the `live`/`fading`/`stale` lifecycle that
already owns decay. That is a measurement about the scoring function, not a
preference, and it stays true whenever this resumes.

The predicate is therefore on the counts, it lives in the `WHERE` (before
`LIMIT`, or it under-fills the block), and `occurrence_count` is `NOT NULL` so
nothing is swallowed by a NULL comparison.

**Read back from `nora-db-1` on 2026-08-27**, not from the suite:

```
Big Walk  n=1 spk=1  2.233  live
big walk  n=1 spk=1  2.216  live

state IN ('live','fading')                           -> 2   (renders today)
state IN ('live','fading') AND occurrence_count >= 2  -> 0   (the predicate)
```

The lexicon **visibly empties on deploy**, from two rows to none. That is the
intended outcome — both rows are wrong — but it is what an operator will see.

### 10.4 The rest of the review, one line each

- **S3** — five distinct conditions all return `unmeasured` (field absent, wrong
  type, blank, over cap, `reading` outside the set) and only the first means
  *never asked*. A model reporting a genuine hold as `"unclear"` lands on «go
  check your objective file». Split: no measure fields at all → `unmeasured`;
  everything else → `measure_malformed`.
- **S4** — a NULL body reports `frame_ungrounded`, documented as *"a fabricated
  grounding"*. The truth is there was no body to check: `unavailable` wearing a
  specific failure, the same R-27 inversion this node argues against elsewhere.
- **S5** — the verbatim check caps `frame` at 200 chars and has **no floor**, so
  `frame="e"` or `frame="的"` grounds against almost any body and defeats the
  check's whole purpose. Needs a length floor and NFKC normalisation on both
  sides; fullwidth/halfwidth mismatch in a mixed-script corpus is not exotic.
- **S6** — *generalises past this tier.* Every fixture in
  `tests/contract/test_room_consolidator_contract.py` seeded two sightings, so
  deleting the fake's floor line left the whole suite green: **the line added
  specifically to stop the two implementations diverging was itself untested.**
  A double filtering differently from Postgres is this repo's most repeated
  defect — three separate instances in three tiers on 2026-08-27, every one
  found by a deliberate test rather than by the suite.
- **S7** — a threshold named `_MIN_OCCURRENCES_TO_RENDER = 1` but compared with
  `>` reads as «at least one» and means «at least two»; raising it to "at least
  three" gets four. State it as a minimum with `>=`, from one shared constant
  both implementations import.

**S6 and S7 are already fixed and mutation-verified on
`worktree-lang-floor`**: the threshold is
`nora.domain.language_scoring.MIN_OCCURRENCES_TO_RENDER = 2`, both the store and
the contract fake read that name, and
`test_an_under_corroborated_lexeme_renders_on_neither_implementation` fails on
either backend alone when its floor is deleted.

### 10.5 What the cold review rated CLEAN — do not re-review it

- `tests/unit/test_language_measure.py` is **genuinely falsifying throughout**:
  the positive case destructures, so a hard-wired reject reddens the file, and
  every `== 0` is paired, so none survives a raise.
- `test_the_measure_never_runs_ahead_of_the_evidence_join` was singled out as
  **the strongest test in the branch**, because it pins ORDERING rather than an
  outcome.
- The store tests falsify the threshold in **both** directions.
- The evidence-join is sound: the body comes from the row actually cited, the
  prompt is not truncated, and the ordering is pinned.

### 10.6 What still holds, and what would reopen it

The design in §4.1–§4.1.4 and §6.1.1 is **not** what the review faulted — it
faulted the wiring and five edges. The measure's shape stands: the tier's job is
the delta, a selectional violation carries it in one sentence, the frame types
the result, and the grounding is the one part a model may not self-attest.

*The reading that would overturn the measure itself:* a corpus where the
selectional frame is routinely absent — a room that names its slang without ever
using it in a sentence that discriminates. Nothing observed says it is.

## Status / Checkpoint

2026-08-28 — **PARKED by the operator** (§10). Built, cold-reviewed, deferred to
Standing and the note tier. The blocker is that the objective wording the
measure needs is pinned byte-for-byte by two tests over a bind-mounted tree, so
the fail-closed state is one the operator cannot leave; and `unmeasured`
advances the watermark, burning batches the instrument never measured. Branches
`worktree-lang-tier` @ `bd522bf` and `worktree-lang-floor`, both unmerged.

2026-08-27 — **D34 (§4.1): this tier had no MEASURE verb**, and that — not the
absence of supersession — is what produced the two contradictory live rows. Its
job is the *delta* from what a model already assumes, the measure is a
**selectional violation** decidable from one sentence, and the violated frame
**types** the result and routes names to `fact_entity`. §6.1 records that the
render has no score floor, verified: a floor alone removes both live rows.

2026-08-24 — written after a coverage audit against the operator's own list
(caps, mechanism, measurement, transit, storage, tools) found three tiers
covered and this one absent. It adds no new mechanism: §4's score, §3's tables
and §8's five metrics all already exist. What it adds is **ownership**, one cap
(§5), one extent line (§7) and two counters (§8).
