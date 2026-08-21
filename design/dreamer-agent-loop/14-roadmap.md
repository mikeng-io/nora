---
type: reference
title: "Roadmap — Every Tier, One Picture"
description: "Fact tier, note tier, language tier, Dreamer runtime and Standing/debrief, consolidated: what is built and live, what is merged but inert, what sits on an unmerged branch, what is specced and unbuilt, who owns each piece, and what the corpus still says that is now false."
tags: [nora, roadmap, fact-tier, note-tier, dreamer, standing, ownership]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 13-open-items, rel: relates_to, provenance: asserted}
---

# Roadmap — Every Tier, One Picture

## 0. Why this node exists

There are **three** transfer documents for this work — a fact-tier-and-runtime
transfer, a runtime handoff, and a live transfer — each written because the
previous one did not survive the session boundary. Each is one session's
snapshot of one moment, and each is now partly false.

That is not a documentation problem, it is the same defect this bundle is
about: **a spec owns a decision, a handoff restates it, and nothing detects the
drift.** [04-designed-vs-shipped.md](04-designed-vs-shipped.md) §6 records four
instances. The transfer docs are the fifth, at a larger grain.

It covers **five** tiers: fact, note, language, the Dreamer runtime, and
Standing/debrief. This node is the durable version. It is a *reference* — the thing you read
before picking anything up — and it carries a staleness register (§7) rather
than pretending to be permanently true.

**Verification baseline.** Everything below was read back from the running
system and the source on 2026-08-22, at master `c528912`. Where the corpus and
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
| **merged and running** | executing, producing rows | the language/exchange consolidators only |
| **merged but inert behind a flag** | constructed only under a flag that is off | both fact adaptors, the whole Dreamer writer path, the runtime |
| **merged and wired but produces nothing** | the path is exercised every turn; the store is empty | `<facts>`, `<notes>`, `<room_exchange>`, `<room_portrait>` |

## 2. Fact tier

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

**Structure:** nine tasks, all done, with one item cut (an ambiguous-workspace
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
| **`NoteCurator`** (merge, retire, promote, coherence-rewrite) | **unblocked, unbuilt.** Its blocker landed. It waited by *decision*, not necessity: building it on the old one-shot port would have shipped sooner and then required rewriting onto the runtime |
| **`NoteRevisitor`** (the revisit adaptor) | code-unblocked, **deploy-blocked** by the Dreamer flag |
| **joining the shared sink contract suite** | unbuilt. The note store is not a sink at all yet — it implements neither sink verb |
| **the debrief's fourth output** | moved to the Standing workstream (§6) |
| **the re-derivation sweep** | deferred behind an evidence gate that *cannot be collected*, because the two metrics it names do not exist (§7, C-vi) |
| **`note_entity` population** | needs entity resolution first; the junction table stays empty |

**Structure — Part 1a / 1b / 2**, and it is the clearest phase structure in the
corpus:

- **1a** — everything above under "built and live". **Done**, except the
  debrief's fourth output, which moved.
- **1b** — the curator, plus the contract-suite join. **Unblocked, unbuilt.**
  One item in its scope was built by the other side instead.
- **2** — the revisitor. Code-unblocked, deploy-blocked.

## 4. Language tier — the only one actually producing, and it cannot correct itself

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
| **it has no loop** | one-shot completions, no tools, and it never receives the doctrine at all — the very instruction that would have prevented its wrong gloss reaches the extractor that has no sources instead | [03](03-no-agent-loop.md), [11](11-doctrine.md) §5 |
| **it cannot correct itself, by design** | a better gloss is a *new row*; the compound key was a deliberate fix for a worse bug; the only removal mechanism is decay, which fires on absence and cannot tell a wrong gloss from a right one | [09](09-self-correction.md) §1 |
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
fixtures**, which is why editing three of the six objective bodies is currently
a substrate bug by the rule's own terms
([12-migration-and-governance.md](12-migration-and-governance.md) §1). It is also
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
commits, a full gate-green run, migration `0028_tag_trace` whose parent is
master's current head — **no collision**, and it is the only unmerged migration
in the repo.

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

**Three Phase-2 blockers exist only on that branch**, including the one that
matters most here: *the fold must read the current Standing before writing a
new one, or it reproduces the language tier's exact failure* — two contradictory
positions coexisting, neither able to correct the other
([09-self-correction.md](09-self-correction.md) §3). The re-derivation
signature does not yet take the prior state and must gain it **before**
implementation, not during.

**Six operator decisions remain open**, none blocking Phase 1, each changing
what gets built.

**Carried risk, stated not solved:** Standing is update-in-place, so a
successful prompt injection **persists** until a later fold drops it — and the
design markets that persistence as the feature. Escaping is necessary and not
obviously sufficient.

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
   ([12-migration-and-governance.md](12-migration-and-governance.md) §3).

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
   → the usefulness of every search tool in 06-tool-surface

gateway ceiling probe  (NOT RUN)
   → all budget sizing                        (13-open-items §1)

R-49's second condition  (OPEN)
   → workspace-keyed room access, Path B      (07, 08)

global concurrency bound  (NOT CHOSEN)
   → the Phase 3 migration landing            (12 §1)

Phase 3 fixture deletion
   → editing three of six objective bodies    (11, 12 §1)
```

**The shortest path to the Dreamer producing anything:** the gateway probe →
budget sizing → the tool surface and the extraction loop → flip the flag → read
back whether the output-contract fix worked. Everything else in this node is
downstream of, or parallel to, that.

## Status / Checkpoint

2026-08-22 — consolidated from six investigations plus a direct handoff from the
session that owns Standing. Supersedes the three transfer documents in
substance; they are **not** yet marked superseded in-file, which is a deliberate
omission pending an operator decision, and is itself an instance of the drift
this node exists to stop.
