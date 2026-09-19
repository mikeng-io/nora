---
type: reference
title: "Roadmap — Every Tier, One Picture"
description: "Fact tier, note tier, language tier, Dreamer runtime and Standing/debrief, consolidated: what is built and live, what is merged but inert, what sits on an unmerged branch, what is specced and unbuilt, who owns each piece, and what the corpus still says that is now false."
tags: [nora, roadmap, fact-tier, note-tier, dreamer, standing, ownership]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 16-open-items, rel: relates_to, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


# Roadmap — Every Tier, One Picture

## 0. Why this node exists

There are **three** transfer documents for this work — a fact-tier-and-runtime
transfer, a runtime handoff, and a live transfer — each written because the
previous one did not survive the session boundary. Each is one session's
snapshot of one moment, and each is now partly false.

That is not a documentation problem, it is the same defect this bundle is
about: **a spec owns a decision, a handoff restates it, and nothing detects the
drift.** [04-designed-vs-shipped.md](04-designed-vs-shipped.md) §6 records four
instances, the fourth of which is these transfer documents.

It covers **five** tiers: fact, note, language, the Dreamer runtime, and
Standing/debrief. This node is the durable version. It is a *reference* — the thing you read
before picking anything up — and it carries a staleness register (§7) rather
than pretending to be permanently true.

**Verification baseline.** Everything below was read back from the running
system and the source on 2026-08-22. Row counts and watermarks drift; treat
every number here as *as of that read*, and re-read rather than cite. Where the corpus and
the system disagree, the system wins and §7 records the disagreement.

## 1. The state that invalidates most of the corpus

**The Dreamer is OFF.** `[dreamer] enabled = false`; the loop reports
`0 adaptor(s)`.

Every transfer document describes a *running* Dreamer, because each was written
while it was running. The consequence nobody has recorded: **an adaptor landed
today would be inert.** The note tier's curator and revisitor are described in
its handoff as "UNBLOCKED" — true at code level, and false at deploy level
until the flag flips back.

So the tier flags now mean three different things, and they must be kept apart:

| state | meaning | who is in it |
|---|---|---|
| **merged and running** | executing, producing rows | ~~the language/exchange consolidators only~~ **(DEPRECATED 2026-09-07, deleted — nothing is in this state)** |
| **merged but inert behind a flag** | constructed only under a flag that is off | ~~both fact adaptors~~ **(DEPRECATED 2026-09-07, deleted)**, the whole Dreamer writer path, the runtime |
| **merged and wired but produces nothing** | the path is exercised every turn; the store is empty | ~~`<facts>`~~ **(DEPRECATED 2026-09-07, deleted)**, `<notes>`, ~~`<room_exchange>`, `<room_portrait>`~~ **(DEPRECATED 2026-09-07, deleted)** |

## 2. Fact tier **(DEPRECATED 2026-09-07, deleted)**

**Built and live.** Domain types with the warrant axis, both stores, both
adaptors, the `<facts>` block and its reader, migrations `0025`/`0027`, the
adjudication corpus. `[facts] enabled = true`.

**Actually producing:** nothing. Zero rows in every fact table, zero on every
declared counter, and all four extractor watermarks still carry their *seeding*
timestamp — the extractor never once reached the advance. See
[01-live-state.md](01-live-state.md).

**Nothing unmerged.**

**Specced and unbuilt** — six pieces, each with a spec section behind it:

| piece | consequence of absence |
|---|---|
| **entity resolution** | the tier fragments into near-duplicates that can never contradict each other; `<facts>` reads with **no subject filter** because inventing a rule at the render site would put a second definition of "subject" in the codebase |
| **transitive supersession** | the dependency relation makes "which facts rest on this one" one indexed query; nothing walks it, so a fact can stand on a retracted foundation indefinitely |
| **abduction and volition** | two warrant kinds have schema and no writer; everything written defaults to source-warranted |
| **premise cardinality** | "premises-warranted ⇒ has at least one premise" is enforced by nothing, and is a plainly-stated gap rather than a trigger pretending otherwise |
| **extractor metrics** | a malformed-item drop matches none of the declared counter's values — see [02-instrument-blindness.md](02-instrument-blindness.md) |
| **game-state tools** | excluded fail-closed; the one tool that would settle the motivating claim |

**The output-contract fix is unmeasured.** A fix landed for the adjudicator
returning prose instead of JSON. It has not run since. Whether it worked is
unknown, and cannot be known while the flag is off.

**Structure:** nine tasks, all done, with one item cut (an ambiguous-unit
startup check).

## 3. Note tier

**Built and live, and genuinely running:** domain types, the store with all six
verbs, the four tools with their budgets, the read-before-write latch, the
`<notes>` block, the miss cache. `[notes] enabled = true`, migration `0026`
applied with its trigram index.

**Has produced:** zero notes. One miss-cache row — the only evidence any note
tool has ever been called. There have been **zero tags** since the last
restart, so the tier is *untested in production* rather than demonstrably
failing. Keep that distinction.

**Nothing unmerged.**

**Specced and unbuilt:**

| piece | state |
|---|---|
| **`NoteCurator`** ~~(merge, retire, promote, coherence-rewrite)~~ **(retire; merge deleted 2026-09-08 — its resolved-anchor input could never be populated once the knowledge tier stopped minting entities; promote exists only as `_run_discharge`, gated `discharge_enabled = false`; coherence-rewrite was never built)** | ~~**unblocked, unbuilt.**~~ **shipped 2026-08-28.** It had waited by *decision*, not necessity: building it on the old one-shot port would have shipped sooner and then required rewriting onto the runtime |
| **`NoteRevisitor`** (the revisit adaptor) | **spec-blocked**, and the flag is not what stands in its way. Its specced tool subset includes an **act verb**, so the startup check would refuse it — and that verb is the entire mechanism by which its own spec has a background pass reach a person. Removing it leaves that section with no implementation path |
| **joining the shared sink contract suite** | unbuilt. The note store is not a sink at all yet — it implements neither sink verb |
| **the debrief's fourth output** | moved to the Standing workstream (§6) |
| **the re-derivation sweep** | deferred behind an evidence gate that *cannot be collected*, because the two metrics it names do not exist (§7, C-vi) |
| **`note_entity` population** | needs entity resolution first; the junction table stays empty |

**Structure — Part 1a / 1b / 2**, and it is the clearest phase structure in the
corpus:

- **1a** — everything above under "built and live". **Done**, except the
  debrief's fourth output, which moved.
- **1b** — the curator, plus the contract-suite join. The curator ~~**Unblocked,
  unbuilt.**~~ **shipped 2026-08-28** (retire only — its merge half was deleted
  2026-09-08); the contract-suite join remains unbuilt. One item in its scope
  was built by the other side instead.
- **2** — the revisitor. **Spec-blocked** (see the table above), then
  deploy-blocked behind the flag.

## 4. Language tier **(DEPRECATED 2026-09-07, deleted)** — the only one actually producing, and it cannot correct itself

Not a "workstream" in the sense the other four are: nobody is building it, and
it has no phase plan. It is here because it is **the only tier producing rows in
production**, because the failure that motivated the whole Dreamer design is
still live inside it, and because it is the worked example of what the other
tiers are heading toward.

**Built, live, and running:** the lexeme observer, the exchange observer, the
portrait synthesizer, and the known-entity check. `[language] enabled = true`.
These are the participants the Dreamer flag does **not** gate — they are on the
old one-shot path, so turning the Dreamer off did not stop them.

**Producing:** two lexeme rows, both wrong, from 963 messages. Zero exchange
rows. Zero portrait rows. Details and the mechanism for each in
[01-live-state.md](01-live-state.md) §2 and §5.

**The corrections it needs**, and they are of three different kinds:

| kind | what | where |
|---|---|---|
| **it has no loop** | one-shot completions, no tools, and it never receives the doctrine at all — the very instruction that would have prevented its wrong gloss reaches the extractor that has no sources instead | [03](03-no-agent-loop.md), [13](13-doctrine.md) §5 |
| **it cannot correct itself, by design** | a better gloss is a *new row*; the compound key was a deliberate fix for a worse bug; the only removal mechanism is decay, which fires on absence and cannot tell a wrong gloss from a right one | [12](12-self-correction.md) §1 |
| **it cannot be observed** | no trace, nothing logged on success, no cycle counter — "ran and found nothing" is indistinguishable from "never ran" for the entire tier | [02](02-instrument-blindness.md) Layer 2 |

**Two things it is owed that are not corrections but repairs:** the known-entity
check gates *ingest only* — no re-verification, no backfill — so switching it on
cannot repair a corpus written before it, and it has dropped nothing in the
deployment's history. And both live rows carry null provenance permanently,
because the stamp is inherited from the newest observation for a key that will
not see another.

**Its migration is Phase 3 of the Dreamer** (§5), which is also what releases
the objective-text freeze. So the language tier's corrections are not a separate
programme — they are what Phase 3 is *for*, and the reason Phase 3 is worth
doing beyond tidiness.


## 5. Dreamer runtime

**Built:** the runtime, the guard, the screen, the call ledger, the instruction
substrate, the loop and its watermark seeding, per-adaptor scheduling and
failure isolation.

**Live:** the instruction substrate (bind-mounted, hot-reloading) and the clock.
The runtime object is **not constructed** — the loop runs with no adaptors, and
that path is byte-for-byte the loop that carried traffic before adaptors
existed.

**Nothing unmerged.**

**Specced and unbuilt:** **Phase 3** — migrating room-language and
room-exchange onto the runtime, with the three-way re-partition of their
objective text. This is also the change that **deletes the byte-identity
fixtures**, which is why editing three of the five objective bodies is currently
a substrate bug by the rule's own terms
([14-migration-and-governance.md](14-migration-and-governance.md) §1). It is also
what the language tier's corrections depend on (§4).

**Structure — Phases 1/2/3.** Phase 1 (substrate) done. Phase 2 (runtime) done.
Phase 3 **unstarted**. A named Phase 4 (the fact adaptor) was overtaken — it
was built inside Phase 2's session instead.

**Two decision gates still open:** whether the instruction digest must cover the
tool subset and budget (a change this bundle walks straight into), and the
falsifiability commitment — which cannot be discharged until a research loop
completes, and none ever has.

## 6. Standing / debrief

**On master: nothing.** No Standing type, no debrief, no trace table.

**On branch `standing-debrief` (unmerged): Phase 1, complete.** Thirteen
commits, a self-attested gate-green run recorded against a head the branch has
since moved past, and migration `0028_tag_trace`.

> **COLLISION, found 2026-08-24 — it must be rebased before it merges.**
>
> When this was written `0028_tag_trace`'s parent *was* the migration head and
> there was no collision. Master has since gained `0028_music_queue` and
> `0029_music_session`. Both `0028_music_queue` and `0028_tag_trace` now declare
> `down_revision = "0027_fact_candidate_provenance"`, so `0027` would have **two
> children** and `upgrade "head"` would refuse.
>
> **The filenames differ, so git merges both without a conflict**, and every
> test that does not actually migrate stays green — which is exactly the failure
> CLAUDE.md's own war-story names. **Fix before merge:** renumber to
> `0030_tag_trace` with `down_revision = "0029_music_session"`, then confirm
> `uv run alembic heads` prints **one** line.

Note the invariant is the **migration** head, not the git head: a git commit
landing changes nothing about whether two revisions share a parent — and this
row is the demonstration, because it was accurate when written and became false
without anyone touching the branch.

Phase 1 is the *recording* layer only: every completed tag turn — success,
silence, veto, timeout alike — has its real reasoning captured, sanitized and
retained, readable back per conversation. Nothing folds or summarises yet, and
that ordering is deliberate: **retention must exist before the first debrief
ships, or early folds are unrecoverable.**

**Phases 2–4, drafted, zero code:**

- **Phase 2** — the debrief stage, `Standing` (one row per conversation,
  revised in place), `TurnVerdict`, and a **note proposal** added this session
  because notes had no after-the-reply path the way Standing and facts do.
- **Phase 3** — the verdict split, mostly folded into Phase 2. Still needs one
  decision resolved on its own merits or dropped.
- **Phase 4** — fact proposals from the debrief. **Now unblocked** (its stated
  blocker, the fact tier merging, is satisfied). Depends on Phase 2 first.

**Three Phase-2 blockers, all decided:**

1. **The fold must read the current Standing before writing a new one**, or it
   reproduces the language tier's exact failure — two contradictory positions
   coexisting, neither able to correct the other
   ([12-self-correction.md](12-self-correction.md) §3). **The re-derivation
   signature takes the prior state**, and that is a signature change made
   *before* implementation, not during. Non-negotiable: this is the same defect
   D19 reverses for the language tier, and shipping it twice would be a choice.
2. **The debrief is a plain completion call, not a tools-less agent.** Grounded
   in the production incident this bundle documents: a tools-less adaptor is a
   completion wearing an agent's construction
   ([03-no-agent-loop.md](03-no-agent-loop.md)), and it inherited the loop
   machinery's failure modes for none of its benefits.
3. **`tool_choice="required"` reliability** is the same question as
   [07-loop.md](07-loop.md) §7, answered by the same reading — the eval harness
   driving the real pipeline, never another single-shot probe. Both branches of
   that fork are already designed, so this blocks implementation ordering, not
   design.

**Six operator decisions — CLOSED, 2026-08-23.** They are adopted as their
branch's own recommendations, which were argued rather than guessed; §6.3 states
each with its reasoning.

**Carried risk, stated not solved:** Standing is update-in-place, so a
successful prompt injection **persists** until a later fold drops it — and the
design markets that persistence as the feature. Escaping is necessary and not
obviously sufficient.

## 6.2b What Phase 1 measures, and the one it is missing

**Phase 1 adds exactly one metric: `nora_tagtrace_truncations_total`** — and it
is properly wired, three call sites rather than a declaration nobody increments.
That is better than several instruments this bundle catalogues.

**It is also the only one, and it counts the wrong thing first.**

| question | counter today |
|---|---|
| how often does a trace lose content? | **`nora_tagtrace_truncations_total`** |
| **was a trace written at all, per turn taken?** | **none** |
| what was the outcome mix — success, silence, veto, timeout? | none, though `outcome` is a column |
| what did sanitization remove? | none |

The second row is the one that matters, and the reason is Phase 1's own
argument. Retention exists **so that later folds are recoverable**: *"retention
must exist before the first debrief ships, or early folds are unrecoverable."*
A recording layer that silently stops writing therefore fails in the one way
nobody notices — **the discovery point is the first fold**, which is exactly
when the missing traces can no longer be reconstructed.

**Required before Phase 2, not after:** a written-versus-turns-taken reading, so
`traces_written / tags_completed` is a ratio someone can look at. `tag_trace` is
written in a `finally` on every exit path — success, silence, veto and timeout
alike — which makes the expected ratio **1.0**, and any sustained departure a
defect rather than a judgement call.

The outcome distribution is the cheap second: the column is already written, so
counting it is a label on a counter that must exist anyway.

**This is [09-measurement.md](09-measurement.md)'s rule applied to the tier that
most depends on it** — a seam whose entire justification is *record before you
fold* must be able to show that it recorded.

## 6.3 The six Standing decisions, closed

Adopted 2026-08-23 as the branch's own recommendations — each was argued from
existing repo shape rather than guessed, and adopting a reasoned recommendation
is a decision. Where one is wrong the operator overrides it; what does not
remain is a list of open questions.

| # | question | **decided** | why |
|---|---|---|---|
| **S1** | Do the four non-tag slot holders (`heartbeat`, `announce`, `compute_poller`, lost-jobs) debrief? | **Yes, except `announce`** | Those turns reason and assert; excluding them silently loses that. `announce` defers to the next boot, so a longer hold is not a free retry there |
| **S2** | Re-derivation trigger — fold count, elapsed time, or forced when `grounded` is false? | **Forced on `grounded=false`, plus a fold-count ceiling** | Time alone re-derives healthy conversations for nothing. The ceiling bounds drift; the forced case catches the turn that actually went wrong |
| **S3** | Debrief window — the same 40 rows as `context_window`, or its own? | **Its own, smaller** | The debrief revises a position; it does not need the full reply context. A shared number here would be [05-transport.md](05-transport.md)'s inheritance error in another seam |
| **S4** | `Standing` key — `conversation_id` or `(platform, conversation_id)`? | **`(platform, conversation_id)`** | Every lock, queue and slot already keys that way, and the `messages` table carries the platform column. A different key here is a join nobody can make |
| **S5** | Does `<standing>` render on the fire path (reminder/announce/relay)? | **Yes** | `build_prefill` is shared and parameterised only by `header=`. Suppressing it there is the special case that would need justification, not including it |
| **S6** | Corroborate `grounded` with `tool_call_log`, which knows deterministically which tools ran? | **Yes** | A deterministic corroborator beside a model verdict is exactly this repo's fail-closed shape — the same argument as R-27's three states |

**The carried risk is not closed by these, and is restated rather than
softened:** Standing is update-in-place, so a successful prompt injection
**persists** until a later fold drops it, and the design markets that persistence
as the feature. Every other untrusted block is rebuilt from the store each turn,
so an injection there lasts one turn. Escaping is necessary and **not obviously
sufficient**. This is a known, accepted risk with a named review point at Phase
2.7 — not an oversight, and not solved.

## 6.1 Two stores the tier table omits, and the boundary they reveal

`events` and `reminders` are not tiers in the sense the five above are, and
leaving them out has been hiding a real boundary.

| | facts, notes, lexemes | events, reminders |
|---|---|---|
| scope | **unit** | **conversation** |
| audience | the model, through injected blocks | **a person**, through the delivery path |
| shape | claims, questions, meanings | a thing that happens at a time |

Two consequences.

**They are the only route by which background work reaches a human**
([17-failure-classes.md](17-failure-classes.md) §7) — which makes their
per-requester caps the *de facto* budget on how much any pass may nudge
somebody, using machinery that already exists rather than a new counter.

**And `events` is shaped for one kind of event.** Its columns are
`venue_text`, `venue_rating`, `venue_maps_url`, `venue_place_id`, plus an RSVP
table — a dinner, not an occurrence. A game's release, a patch landing, a
deadline passing are all events a background pass would want to reason about and
none of them fit. **Abstracting it is a real refactor**, and it is a *different
axis* from the entity registry (§6.2) — one is about what a thing IS, the other
about when something HAPPENS. They should not be folded together.

## 6.2 There is no registry to build

Recorded here because it changes the shape of the entity-resolution item that
appears in the dependency graph below: **`fact_entity` is already the shared
registry, `note_entity` already has a live foreign key into it, and neither tier
populates or uses it** — facts store subjects as plain strings.

So the blocker is not "design a registry". It is "populate the one that is
already wired across two tiers", and that is the same unowned resolution step
([12-self-correction.md](12-self-correction.md) §4).

## 7. Staleness register

What the corpus asserts that the system now contradicts. Ordered by how
misleading each is to someone picking work up.

- **C-i — every transfer doc describes a running Dreamer.** §1. Most
  consequential, because "unblocked" statements elsewhere silently mean
  "unblocked but inert".
- **C-ii — the fact renderer's own docstring is false in two places**: it says
  the store read is "not yet built" and that "nothing writes this tier yet, so
  this module always renders nothing". Both wrong — the read exists and the
  block is populated every turn. It renders nothing because the *table* is
  empty. **The same stale claim is duplicated in the config module, where it is
  load-bearing for the argument that cut a startup check.** A decision now rests
  on a stale restatement.
- **C-iii — Standing is called "nobody's" in three documents.** It is owned: a
  branch carries a complete, gate-green Phase 1.
- **C-iv — the note tier's dependency table lists three things as unbuilt** that
  have all landed, and assigns one item to the note tier that was built by the
  other side.
- **C-v — a spec says joining the sink contract suite is "one entry".** The
  suite's own docstring records that measured, on the previous attempt, as
  **four passed and nine failed**. The spec was never annotated.
- **C-vi — the note tier declares ten metrics; none exists.** Its own stated
  failure-detection is therefore unreadable. See
  [02-instrument-blindness.md](02-instrument-blindness.md), Layer 6.
- **C-vii — a decision number is skipped entirely**, with nothing recording why.
  Inferred: reserved and abandoned during a cross-session numbering collision.
- **C-viii — the branch carries a correction that master does not have.** A
  164-line blocker section, including a corrected root-cause analysis, exists
  only on `standing-debrief`. If that branch is lost, the correction is lost.
- **C-ix — the unmerged branch has no release fragment** across thirteen commits
  and a schema change. It will fail the gate as it stands.
- **C-x — a plan's stated priority was overtaken**: it claims Standing is ahead
  of the runtime; the runtime shipped first and Standing has still not merged.

## 8. Ownership, and what is genuinely orphaned

**Owned:** the fact tier's store, values, retrieval and rendering; the note
tier's four parts; the Dreamer runtime; Standing (now — see C-iii).

**Orphaned, and each named as such by a document that then does not claim it:**

1. **Entity resolution.** Blocks **three** tiers — fact self-contradiction, note
   anchoring, language de-fragmentation — and bounds the usefulness of any
   search tool this bundle proposes. No document claims it.
2. **The game-state tool classification.** Left "unresolved" by one spec and
   deferred to a plan that closed without answering it.
3. **A corpus defect in the eval suite**, explicitly declared independent of the
   tier that found it and to be fixed "where it lives". Partially closed.
4. **Three governance defects**, which this bundle assigns only to "in passing"
   ([14-migration-and-governance.md](14-migration-and-governance.md) §3).

## 9. Dependency ordering

Edges that actually block, consolidated. `→` reads *blocks*.

```
[dreamer] enabled = false
   → both fact adaptors, the whole writer path
   → NoteCurator / NoteRevisitor, once built
   → Dreamer Phase 3 cutover
   → measuring whether the adjudicator output-contract fix worked

note store joins the sink contract suite   (UNBUILT)
   → Standing's note-proposal task
   → NoteCurator's sink
        ^ one unbuilt item blocking two workstreams, recorded in neither

TagTrace retention  (COMPLETE, UNMERGED)
   → the debrief, and everything downstream of it

the debrief  (UNBUILT)
   → Standing, TurnVerdict, fact proposals, note proposals

entity resolution  (UNBUILT, UNOWNED)
   → fact tier self-contradiction
   → note anchoring and its junction table
   → language tier de-fragmentation
   → the usefulness of every search tool in 08-state

gateway ceiling probe  (NOT RUN)
   → all budget sizing                        (13-open-items §1)

R-49's second condition  (OPEN)
   → unit-keyed room access, Path B      (07, 08)

global concurrency bound  (NOT CHOSEN)
   → the Phase 3 migration landing            (12 §1)

Phase 3 fixture deletion
   → editing three of five objective bodies    (11, 12 §1)
```

**The shortest path to the Dreamer producing anything:** the gateway probe →
budget sizing → the tool surface and the extraction loop → flip the flag → read
back whether the output-contract fix worked. Everything else in this node is
downstream of, or parallel to, that.

## Status / Checkpoint

2026-08-22 — consolidated from six investigations plus a direct handoff from the
session that owns Standing, then corrected against four adversarial reviews.
The three transfer documents and the note-tier handoff now each carry an in-file
superseded banner pointing here.

**Read every number here as of that date, not as current.** This node is a
register of what drifts; it drifts too.
