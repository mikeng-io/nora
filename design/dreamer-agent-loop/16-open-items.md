---
type: analysis
title: "Open Items"
description: "What this bundle does not answer, ranked by what blocks what. The gateway probe blocks all budget sizing; the disclosure question blocks workspace room access."
tags: [nora, dreamer, open-questions, backlog]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Open Items

## Blocking

**0. Does the transport sustain a research loop at all?**

Two indexed findings say the route does **not** reliably honour the tool-choice
setting the loop depends on, and that **reasoning is stripped between
iterations**. This bundle's central proposal is multi-turn loops that carry
their reasoning forward and can rely on calling something.

If either holds, the loop designed in [07-loop.md](07-loop.md) cannot run as
designed — not "runs worse", but rests on a property the transport does not
have. **This outranks every other item here**, including the ceiling question
below, because it decides whether the shape is right rather than how to size it.

Measured the same way as §1, in the same run: drive the real pipeline and read
back whether tool calls are honoured and whether reasoning survives a turn
boundary.

**1. Does the gateway honour the per-call token ceiling?**

The observed generation was 4,096 output tokens. The configured per-call
ceiling is 2,000, the loop's output budget is 4,000, and the trace carries
exactly one generation. 4,096 is neither number, and it is a very common
provider default.

Either the gateway is ignoring the ceiling and applying its own, or the trace
measures something other than one completion. It matters directly: if the
ceiling is ignored, the loop's output budget is the **only** cap in play and a
single call spends all of it — which is exactly the failure observed.

**Do not write a standalone probe.** A hand-rolled single-completion probe
measures a shape production does not have — the Standing/debrief session built
two of them before getting this right, and both scored "did not finish in one
shot" as a failure when the real path is a multi-turn loop that keeps trying.

**The correct instrument already exists and is unwired.** `evals/harness.py`
can drive the *real* pipeline — real doctrine, real tool registry, real
multi-turn loop — but no flag connects it to the eval runner. Extending that is
the answer to this item, and to the `tool_choice` reliability question that is
still open beside it. *(Credit: the Standing/debrief session, who learned it the
expensive way.)*

Nothing in [06-trigger.md](06-trigger.md) should be sized until it
is run. This is the cheapest open item and the one most other numbers depend
on.

**2. R-49's second condition on the fact path.**

The workspace bounds which rooms may be paired; the persons list decides who
may use the pair. The fact path implements the first and has no mechanism for
the second. Until it does, Path B in [10-room-access.md](10-room-access.md) is
blocked and the content-class change in [11-disclosure.md](11-disclosure.md) is
unsanctioned.

**It is not unanswered — it is answered twice, incompatibly.** One spec holds
that a fact has *no* second gate and therefore the workspace must be **stated**,
in the refuse-to-start shape, with a named test. Another withdraws its own
room-gate on the opposite reading: *"the workspace key already **is** the
gate."* The startup check the first implies was cut.

So the work is to pick one and record why, not to research an open question.
The check-at-startup reading is the one that turns a borrowed argument into a
guard, and it is the one with a test already written for it.

**3. A global concurrency bound.**

None exists. Ten concurrent units today, eighteen after the migration, each
potentially issuing several network calls, all on one client. The bound should
be chosen before the migration lands, not discovered after.

## Open, not blocking

**4. Cadence per adaptor.** Extraction is watermark-driven and suits a clock.
Adjudication is a work queue wearing a clock's clothing. Synthesis, if it
lands, wants to run rarely. One tick currently serves all three
([06-trigger.md](06-trigger.md) §2).

**4.1 A cross-tier semantic index — and whether it dissolves item 5.**

The wrong-gloss failure is that **the open web does not know what this room means
by a term**, while the room's own accumulated state does. The proposal is one
search across facts, notes, lexemes, memory and standing, reached before the web
rather than instead of it.

The reason it is filed above entity resolution rather than beside it: **it may
subsume it.** Nearest-neighbour over embeddings would put `Big Walk`,
`big walk` and a translation near each other with no alias rule authored — which
would dissolve the blocker in item 5 rather than solve it.

What must be established first, and is under investigation: whether **Honcho
already provides this** for one tier (Nora depends on it and it does semantic
search), what it costs at this deployment's size, and whether there is a class of
alias — a nickname, an abbreviation — where embedding distance is the wrong
signal. There is also a measured warning against assuming a retrieval primitive
works: a similarity operator returned **0 of 3000 rows that all contained the
string** ([09-measurement.md](09-measurement.md) §3.2).

**5. Entity resolution.** The shared dependency of both tiers
([12-self-correction.md](12-self-correction.md) §4). Its absence bounds how
useful any search tool can be, and the fact tier is on course to reproduce the
language tier's fragmentation. Mechanism still open; this bundle records only
that it is shared, not fact-tier-local.

**6. Does the instruction digest need to cover the tool subset?**

Two runs over identical instruction text — one with a fetch tool, one without —
can write different rows. The provenance column cannot distinguish them, and
this work **changes tool subsets**, so it walks straight into the gap. The
substrate spec left it open and noted that if the answer is "more", it is a
second column rather than a different one — so today's stamp stays correct
either way. Still owed an answer.

**7. Language-tier supersession.** Deliberately not decided here
([12-self-correction.md](12-self-correction.md) §1). Any proposal must say what
happens to the overwrite bug the compound key was introduced to fix.

**8. What `<facts>` renders.** Currently the workspace's most recent standing
facts with no subject filter. Whether it becomes query-relevant, becomes a tool
Nora calls, or both, is a tag-path cost question
([08-state.md](08-state.md) §11).

**9. Was the model ever the problem?**

The keystone made a falsifiability commitment: the Dreamer runs Nora's own
model *because* the claim is that it does better by being **differently
situated** — no social pressure, a loop, a fetch — not by being smarter. It
committed to verifying that under a research loop, against the motivating
corpus.

**That verification has never run**, because no research loop has ever
completed. Until one does, the design's central claim is untested. The
keystone's own condition still stands: if a loop still cannot tell a source
that contradicts from one that merely does not cover, that reopens the
**design**, not the model.

## Recorded, owned elsewhere

- **The fact tier's store.** Owned by its own workstream.
- **Standing and the debrief.** Tag-path work, separately owned. The
  input-versus-tool rule ([08-state.md](08-state.md)) applies
  to it; that is the whole overlap. Its read is one known row, so it is an
  input — unless it ever asks "have I concluded something like this before,
  elsewhere", which is discovery and needs the seam.
- **Game-state tools.** Excluded fail-closed because tools arriving over the
  external protocol carry no read/act classification. The cost is real and
  named: the fact adaptor can research the open web and cannot read the group's
  own game state, which is the one thing that would settle the motivating
  claim. Reopen by giving those tools a classification surface.
- **Transitive supersession, premise cardinality, two writerless warrant
  kinds.** [12-self-correction.md](12-self-correction.md) §6.
