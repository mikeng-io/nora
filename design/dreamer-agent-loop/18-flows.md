---
type: reference
title: "Four Flows — How Each Tier Actually Runs"
description: "Language, fact, note and standing, each traced end to end: what fires it, what selects, what the model sees, how it ends, what code does with the result, and what can be read back afterwards. The same nine steps for all three, so they compare."
tags: [nora, dreamer, flows, language-tier, fact-tier, note-tier, walkthrough]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
>
> **The note tier is NOT wholly unaffected, and this line said it was until 2026-09-13.** The curator's MERGE half went on 2026-09-08 with the entity chain its key depended on, and the create gate's anchor-DATE match — which had taken the duplicate-handling job over — went on 2026-09-13 (#228) after conflating two unrelated occasions both times it fired. So the note flow's step 7 below, *"merges, retirements, promotions"*, names one verb that no longer exists: the pass returns retirements and, through the discharge route, promotions. **Nothing merges a duplicate anywhere in that tier** — a known, recorded limitation, bounded by issue #236. *(Superseded 2026-09-25: a separate `note-merge` pass now folds duplicates, DEC-202, design 41 §7 — `[notes] merge_mode`, off by default, `shadow` first; step 7's «merges» is its verb, not the curator's.)* R-75 in `design/09-decision-index.md` carries the current reading.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


# Four Flows — How Each Tier Actually Runs

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


The rest of the bundle argues about seams. This node runs each tier top to
bottom so the seams have somewhere to land. Three are background flows; the
fourth, Standing, runs in-slot, and the contrast is the point.

**Same nine steps for all four**, deliberately, so the differences are legible
rather than buried in prose. Each flow gives the **target** shape, then a
`TODAY` block naming exactly how the live system differs.

The nine steps map one-to-one onto the seams:

| step | seam |
|---|---|
| 1 fires when · 2 selects | [Trigger](06-trigger.md) |
| 3 reads · 5 may call | [State](08-state.md) |
| 4 model gets · 6 ends when · 7 returns | [Loop](07-loop.md) + [Transport](05-transport.md) |
| 8 code does · 9 lands in | the propose/dispose split — unchanged |
| 10 read back by | [Measurement](09-measurement.md) |

---

## Flow A — Language tier **(DEPRECATED 2026-09-07, deleted)**

*What this room's words mean.* One scope per room. **The only tier producing
rows in production, and both are wrong.*

```
new messages ──▶ [watermark: lag / quiet / age]
                      │  fires
                      ▼
              cheap: id, sender, ts, reply-to for the batch
                      │
                      ▼
       ┌──── model ────────────────────────────────┐
       │  sees:  the batch as `id: body` — NO speaker│
       │  tools: search own lexicon · web · memory  │
       │  ends:  terminal call carrying candidates  │
       └────────────────────────────────────────────┘
                      │ envelope
                      ▼
        screen ▸ typed parse ▸ propose ▸ rescore
                      │
                      ▼
        room_lexeme  ──renders──▶  <room_language>
```

1. **Fires when** — lag over a watermark, or lag plus quiet, or age. Age fires
   at `lag == 0` on purpose: decay is time-driven, so a dormant room must still
   rescore.
2. **Selects** — the message batch since the watermark. Cheap already: it is a
   bounded row read.
3. **Reads** — the batch *with sender, timestamp and reply-threading*. This tier
   already does that today and the fact tier does not
   ([12-self-correction.md](12-self-correction.md) §5).
4. **Model gets** — doctrine (shape-aware, [13](13-doctrine.md)) + the objective
   + the threaded batch.
5. **May call** — its **own lexicon** (has this term been glossed before, and
   how), the **web** (is this a real thing, not group slang), **memory** (is
   this a person). Today: nothing.
6. **Ends when** — it emits the terminal envelope call. Absence of that call is
   a recognised outcome, not a parse failure ([07](07-loop.md) §2).
7. **Returns** — candidate glosses, each with the message it came from.
8. **Code does** — screen, typed parse, drop malformed silently, propose,
   rescore.
9. **Lands in** — `room_lexeme`, keyed `(scope, canonical_form, sense)`.
10. **Read back by** — `<room_language>` in every tag; and by whether a later
    run ever *superseded* an earlier gloss, which is the reading nobody can take
    today.

> **TODAY.** No loop — one completion, no tools, and it **never receives the
> doctrine at all** because it bypasses the runtime. So the one instruction that
> would have prevented the wrong gloss reaches the extractor that has no sources
> and misses the two adaptors that produced them. No trace, nothing logged on
> success: *ran and found nothing* is indistinguishable from *never ran*
> ([02](02-instrument-blindness.md) Layer 2). A better gloss becomes a **second
> row**, not a correction ([12](12-self-correction.md) §1).

**Exchange** rides the same watermark with the same shape, and returns an empty
list every time — its objective wants a *recurring* pattern while the fire
condition hands it ~30-minute slices.

**Portrait** is the exception and is correct as-is: no model call at all, and it
fires only on `appended_total > 0` — a *new* append in the *same* cycle. Since
the only two productive cycles predate its deployment, it has had zero eligible
cycles ever.

---

## Flow B — Fact tier **(DEPRECATED 2026-09-07, deleted)**

*What is true about the world.* Two passes, different keys, deliberately split
on cost.

```
new messages ──▶ [watermark]                    ┌── candidates pending? ──┐
       │  A: EXTRACT (scope, cheap)             │  B: ADJUDICATE (unit)
       ▼                                        ▼
  batch as `id: body`, no speaker          pending claims
       │                                        + standing facts for them
       ▼                                        │
  model: name the claims                        ▼
  tools: vocabulary · negative cache     model: settle each claim
  ends: terminal envelope                tools: web · own facts · transcript
       │                                 ends: terminal envelope
       ▼                                        │
  fact_candidate ──────────────────────────────┘
                                                ▼
                            fact  ──renders──▶  <facts>
```

**Phase A — extract** *(scope-keyed, watermarked)*

1. **Fires when** — new messages past the watermark.
2. **Selects** — the batch. **Bounded**, so output cannot scale past the budget.
3. **Reads** — the batch *with sender, timestamp, reply-threading*. Today it
   renders `id: body` and nothing else, which is why *who is claiming what* is
   unanswerable ([03](03-no-agent-loop.md) §4).
4. **Model gets** — doctrine sized for a tool-less extraction pass, the
   objective, the threaded batch, **and a stated expected output size**.
5. **May call** — the unit **vocabulary** (what do we already call this?)
   and the **negative cache** (did we already try and fail to settle this?).
   **Not** a dedup check — suppressing a repeat destroys the recurrence signal
   the filter ranks on ([08](08-state.md) §9, and it is the easiest thing here
   to get wrong).
6. **Ends when** — the terminal envelope call.
7. **Returns** — candidate triples, each pointing at its message.
8. **Code does** — screen, typed parse, upsert (**incrementing occurrence
   count**), advance the watermark.
9. **Lands in** — `fact_candidate`.
10. **Read back by** — candidates written per run, and the ratio of extracted to
    adjudicated.

**Phase B — adjudicate** *(unit-keyed, no watermark)*

1. **Fires when** — pending candidates exist. This is a **work queue**, not a
   clock, and it is content-addressed: a claim that can never be settled stays
   pending forever, so it needs the negative cache as its no-progress breaker
   ([06](06-trigger.md) §3.2).
2. **Selects** — pending candidates, ranked contradiction-first then recurrence,
   capped. **The cap is the cost decision** and stays code, not prompt.
3. **Reads** — those claims plus standing facts for their subjects.
4. **Model gets** — research doctrine, objective, the rendered claims.
5. **May call** — **web** search and fetch; its **own facts** (is this the same
   thing under another name — which literal subject matching cannot answer);
   the **room's transcript**, *blocked* pending [11](11-disclosure.md) §3.
6. **Ends when** — the terminal envelope call, or the wall clock.
7. **Returns** — a verdict per claim: supported / contradicted / not covered,
   with a source, and a supersession only when contradicted.
8. **Code does** — screen, typed parse, refuse a supersession naming a fact this
   run never showed it, write, retire the candidate.
9. **Lands in** — `fact`, plus `fact_candidate.status`.
10. **Read back by** — `<facts>`; verdict mix; and **whether a fact ever
    superseded another**, which is the tier's whole purpose.

> **TODAY.** Phase A has **no tools**, so it is one completion, and it has
> **never once completed** — every watermark still carries its seeding
> timestamp. Phase B has a loop but can only look **outward**: it is handed
> triples and source URLs with no transcript, no speaker, no room, no language
> — so the extractor is required to emit the room's private vocabulary and the
> adjudicator is denied everything that could interpret it. Both are behind
> `[dreamer] enabled = false`.

---

## Flow C — Note tier

*What we are carrying between conversations.* The only tier with a **live
in-turn write path**, and the only one whose background half is two passes of
genuinely different shapes.

```
        TAG PATH (live, today)              BACKGROUND (unbuilt)
   ┌───────────────────────────┐    ┌──────────────────────────────────┐
   │ <notes> index in every tag │    │ CURATOR  (cursor-shaped)         │
   │        │                   │    │  SQL selects → bodies for those  │
   │  note_read ─(latch)─┐      │    │  merge · retire · promote        │
   │        │            ▼      │    ├──────────────────────────────────┤
   │  note_search   note_revise │    │ REVISITOR (content-addressed)    │
   │  note_create      │ CAS    │    │  one note due → research → revise│
   └───────────────────┼────────┘    └──────────────────────────────────┘
                       ▼                          │
                   note_revision ◀────────────────┘
```

**Live half — the tag path**

1. **Fires when** — she is tagged. Not a background flow at all.
2. **Selects** — every note's title + one line, **uncapped**, injected in every
   tag. Uncapped on purpose: *invisible is indistinguishable from absent*, and
   the index is what turns "notice something is missing" into a **comparison
   between two present things** ([17](17-failure-classes.md) §1).
3. **Reads** — one note's body, via `note_read`.
4. **Model gets** — the index in the prefill; the body only if it asks.
5. **May call** — read, search, create, update. All four live today.
6. **Ends when** — the turn ends. It is a turn, not a loop.
7. **Returns** — a revision, or a new note.
8. **Code does** — the **read-before-write latch** (no revision without a read
   this turn) and a **compare-and-swap** on the head; a stale write is refused
   as `unavailable`, never applied on top ([08](08-state.md) §11.1).
9. **Lands in** — `note_revision`, append-only.
10. **Read back by** — writes by kind; and a search miss is recorded as a
    candidate, which is how the tier earns its next capability.

**Background half — curator** *(unit-keyed, cursor-shaped, shipped
2026-08-28)*

1. **Fires when** — lag over a revision watermark, or lag plus quiet, or age.
   Age matters most here: **retirement is time-driven** — a note whose date has
   passed needs retiring even if nobody touched the notebook for a month.
2. **Selects** — **SQL only, zero tokens**: ids, dates, entity sets. Merge
   detection is `GROUP BY … HAVING count(*) > 1`, not a judgement. *The model
   never decides eligibility.*
3. **Reads** — full bodies for **only the group SQL selected**. The selector's
   output *is* the bound.
4. **Model gets** — one colliding group, or one body plus its facts.
5. **May call** — few or no tools; this pass is mostly SQL plus a small
   completion.
6. **Ends when** — the terminal call. It needs **no** no-progress breaker: the
   cursor always advances, and on an empty batch it must **still** advance the
   watermark to reset the age clock — omitting that is a latch with no reset,
   and it has shipped once ([17](17-failure-classes.md) §5).
7. **Returns** — merges, retirements, promotions.
8. **Code does** — dispose. Retirement is a **status change, never a delete**.
9. **Lands in** — `note`, `note_revision`.
10. **Read back by** — actions by kind; `action="none"` climbing every tick is
    the latch, made visible.

**Background half — revisitor** *(content-addressed, unbuilt, spec-blocked)*

1. **Fires when** — *this note is due* — a predicate over the tier's own rows,
   riding the curator's pass rather than getting its own timer.
2. **Selects** — one note.
3. **Reads** — that note, its history, its anchor's people and facts.
4. **Model gets** — its objective **from the note itself**: the objective is the
   note's stated uncertainty, the success criterion its closing condition. One
   adaptor, unbounded objectives ([07](07-loop.md) §5.1).
5. **May call** — memory *(contingent — [11](11-disclosure.md) §4.3)*, web,
   note reads. Fetched pages are **distilled into the revision**, never left
   prompt-resident.
6. **Ends when** — the terminal call, **or the strike breaker**: nothing is
   consumed by working on a note, so it can be due forever. Strikes reset
   **only on a tag-path revision** — a reset its own writes could trigger is not
   a reset.
7. **Returns** — a revision, optionally a scheduled nudge.
8. **Code does** — dispose through the sink. The nudge has **no expressible
   path today** ([17](17-failure-classes.md) §7).
9. **Lands in** — `note_revision`; the nudge, into the reminder store.
10. **Read back by** — struck versus reset; all-struck-none-reset means either
    it is not earning its cost or the reset is mis-attributed to its own writes.

> **TODAY.** The tag path is live and has produced **zero notes** — untested
> rather than failing, since there have been no tags since the last restart.
> One miss-cache row is the only evidence any note tool has ever been called.
> Both background passes are unbuilt; the revisitor is **spec-blocked**, not
> merely unbuilt, because its specced tool subset contains an act verb.

---

## Flow D — Standing

*What she currently thinks, carried between turns.* **Not a Dreamer flow.** It
runs in-slot, after the reply is delivered — which makes it the useful contrast
to the three above. Owned by another session; recorded here because the seams
apply to it and because the bundle's input-vs-tool rule decided one of its
choices.

```
   a tag turn completes (success, silence, veto, timeout — all of them)
                      │
                      ▼
        TagTrace: her real reasoning, sanitized, capped     ◀── BUILT
                      │                                          (unmerged)
                      ▼
        ┌──── debrief ─────────────────────────────────┐
        │  runs AFTER delivery, off the reply path      │
        │  reads: the trace + the PRIOR Standing        │
        │  raw completion — no tools, no loop           │
        └───────────────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
     Standing                  NoteProposal
   (in place)                   (to notes)
        │
        └──renders──▶ <standing>
```
(TurnVerdict deleted 2026-09-24 — design/standing-compact/.)

1. **Fires when** — a tag turn **completes**, in every terminal state: success,
   silence, veto, timeout. Not gated on whether she spoke.
2. **Selects** — nothing. One turn, already in hand.
3. **Reads** — the turn's trace **and the current Standing**. That second read is
   load-bearing: without it the fold reproduces the language tier's failure
   exactly — two positions coexisting, neither able to correct the other
   ([12-self-correction.md](12-self-correction.md) §3).
4. **Model gets** — the trace, the prior Standing, a sliding transcript window.
5. **May call** — **nothing, correctly.** Its read is one row, known in advance,
   so it is an **input** ([08-state.md](08-state.md) §1). This is the rule's
   clean negative case: the same rule that says the adjudicator *needs* tools
   says Standing does not.
6. **Ends when** — the completion returns. A `finish_reason` of length maps to
   `unavailable`, never a partial parse.
7. **Returns** — a revised Standing and a note proposal. (The verdict this
   returned alongside them was deleted 2026-09-24 — design/standing-compact/.)
8. **Code does** — dispose. The fast path is append-only; only a background pass
   may invalidate.
9. **Lands in** — `standing` (**updated in place**). `turn_verdict` is no
   longer written (deleted 2026-09-24 — design/standing-compact/); the table
   stays in the schema.
10. **Read back by** — `<standing>` in later turns; and whether the position
    ever *changed*, which is the only evidence the fold is doing anything.

**Its reasoning effort is a measurement, not a setting.** The fold is
integration — read the prior position and the trace, write the revised one — not
research, so a low or absent reasoning budget is *plausible*. That is exactly the
class of claim [09-measurement.md](09-measurement.md) forbids asserting.

The spike, and its outcome is decidable without a judge: run the same fold at
several reasoning budgets over a **fixed set of real traces**, and score whether
the revised position (a) **preserves what the prior held** and (b) **integrates
what the trace added**. Both are checkable by comparison. It is the same
instrument as the transport measurement and should be one run, not two.

One correction to a tempting premise: *off the reply path* does not mean
latency-free. It still holds a slot and sits under a wall clock.

> **TODAY.** Phase 1 — the trace capture — is complete and gate-green on an
> unmerged branch. The debrief itself is drafted, zero code. Three blockers are
> recorded there, and the sharpest is that the re-derivation signature **does not
> yet take the prior Standing** and must gain it *before* implementation, not
> during.

**Two properties the Dreamer flows do not have.** It runs **off the reply path
but inside the turn**, so it sees a completed trajectory without spending the
turn's budget or its single reply — the shape
[17-failure-classes.md](17-failure-classes.md) §2 argues for. And because
Standing is **update-in-place**, a successful prompt injection **persists** until
a later fold drops it. The design markets that persistence as the feature;
escaping is necessary and not obviously sufficient.


---

## What the four flows show side by side

| | language | fact | note | standing |
|---|---|---|---|---|
| **passes** | 2 + a deterministic synthesis | 2, split on cost | 1 live turn + 2 background | 1, in-slot |
| **shape** | cursor | cursor, then **work queue** | cursor **and** content-addressed | per-turn |
| **selector** | the batch | the batch, then a ranked cap | **SQL, zero tokens** | none — one turn |
| **speaker in the prompt** | ✅ today | ❌ today | n/a — her own turn | n/a — her own turn |
| **can supersede itself** | ❌ today — **D19 reverses this** ([16](16-open-items.md) §7) | ✅ built | ✅ built (CAS) | ✅ by rewriting in place |
| **no-progress breaker** | the cursor | the negative cache | cursor / **strike** | none needed — one turn in, one out |
| **live?** | ✅ producing, wrongly | ❌ flag off, never completed | ⚠️ live, untested | ❌ capture built, fold unbuilt |

Three things fall out that no single flow shows:

1. **The note tier is the only one that solved concurrency**, and it did so with
   no lock — a latch plus a version echo. The other two have never had two
   writers.
2. **Only the fact tier has a genuine work queue**, and it is the one place a
   clock is most obviously wrong: it fires every tick until the queue drains.
3. **Every tier's step 5 is nearly empty today.** The flows differ enormously in
   design and barely at all in production, because the missing seam is the same
   one in all three.
4. **Standing is the rule's clean negative.** It is the one flow that *should*
   have no tools — its read is a single known row — which is what makes it
   evidence that [08-state.md](08-state.md) §1 is a real rule rather than a
   licence to grant everything.

## Status / Checkpoint

2026-08-24 — Four flows end to end. Its note-revisitor caveat is resolved by [27](27-note-dreamer.md) §3.
