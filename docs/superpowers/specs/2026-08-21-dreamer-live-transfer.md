---
kind: spec
title: "Transfer: the Dreamer runtime is LIVE — what runs, what is broken, what is unbuilt"
description: "The fact tier and the Dreamer runtime are implemented, merged, deployed and enabled. This hands over the rest. It carries what the specs do not: the verified live state, one defect visible in production right now, the three pieces of the design that were never built and who they belong to, and the cross-session conventions that cost four collisions to learn — including two learned today, after the previous transfer was written."
tags: [nora, transfer, dreamer, facts, standing, handoff]
timestamp: '2026-08-21'
superseded_by: design/dreamer-agent-loop/14-roadmap.md
status: superseded
---

> **SUPERSEDED (2026-08-22) — read `design/dreamer-agent-loop/` first.**
>
> This document was a session-boundary snapshot and parts of it are now false.
> Most importantly it describes a **running** Dreamer; `[dreamer] enabled` is
> `false` as of `5ef8ddc`, so any "unblocked" statement below means *unblocked
> at code level and inert at deploy level*.
>
> The consolidated, maintained picture — all five tiers, what is built, inert,
> unmerged, unbuilt, owned and orphaned — is
> `design/dreamer-agent-loop/14-roadmap.md`, whose §7 lists exactly what in this
> file has gone stale. Kept for its reasoning and its war-stories, which remain
> good; do not read it for current state.


# Transfer: the Dreamer is live

## 0. Read these first, in this order

1. **This file, §2.** It is the only place the LIVE defect is written down.
2. `2026-08-21-fact-tier-and-runtime-transfer.md` — the previous transfer. Its
   §3 (the contract-suite rule), §4 (design rules you will be tempted to
   break) and §5 (traps) are still true and still load-bearing.
3. `2026-08-20-dreamer-as-an-agent-design.md` — the keystone.
4. `2026-08-20-fact-tier-design.md` — §9.0 and §13.5.
5. `design/09-decision-index.md` DEC-133 (the runtime) and DEC-134 (the
   writer). R-56 is the Dreamer's rule; **R-55 is the note tier's**, and that
   collision is §5's story.

**Read the artifacts, not descriptions of them.** That habit found a
`DreamSink` docstring promising a field its signature lacked, a contract suite
claiming a one-line join that was false, and — today — a fire condition that
was implemented in the runtime and defeated by every adaptor. This file is a
description. Open the files.

## 1. State, verified 2026-08-21 against the running container

| | |
|---|---|
| container | `nora`, healthy |
| migration | **`0027_fact_candidate_provenance`** (on top of the note tier's `0026`) |
| `[dreamer] enabled` | **true** |
| `[facts] enabled` | **true** |
| model | `ollamacloud/deepseek-v4-flash` — operator decision, not a measurement (§4) |
| loop | `dream loop started: tick every 300s over 4 scope(s), 2 adaptor(s)` |
| watermarks | seeded from the `language` cursor (1182 / 1116 / 1180 / 1173), **not** 0 |
| `fact` / `fact_candidate` | 0 rows |

```
[ dream_loop ]  every 300s, 4 scopes x 2 adaptors
       |
       +-- fact-candidates   scope-keyed, watermarked   BUILT, LIVE, has produced nothing yet
       +-- fact-adjudication workspace-keyed, no watermark  BUILT, LIVE, FAILING (see §2)
       |
       +-- room-language     STILL ON THE OLD ONE-SHOT PATH  (Phase 3, unbuilt)
       +-- room-exchange     STILL ON THE OLD ONE-SHOT PATH  (Phase 3, unbuilt)

  <facts> block      BUILT + WIRED — renders nothing because the tier is empty
  <notes> block      the note tier's, live and inert (no tools wired)
  <standing>         DOES NOT EXIST — nobody has built it (§3)
```

## 2. The one thing that is broken in production right now

**`fact-adjudication` returns `unavailable` about half the time, because the
model does not return JSON.**

```
nora_dream_run_total{adaptor="fact-adjudication",status="nothing_found"}  12
nora_dream_run_total{adaptor="fact-adjudication",status="unavailable"}    13

"dream run produced no readable envelope (adaptor=fact-adjudication, 28 chars)"
"dream run produced no readable envelope (adaptor=fact-adjudication, 88 chars)"
```

**28 and 88 characters** where a `{"verdicts": [...]}` envelope belongs. The
runtime is behaving correctly — `unavailable`, watermark held, nothing
written, counted rather than silent. What is failing is the OBJECTIVE's output
contract: `nora/dreamer/objectives/fact-adjudication.md` asks for JSON in
prose, and flash is answering in prose.

**Do not fix this by loosening `parse_envelope`.** The three-state discipline
is the tier's whole point; a parser that accepts prose turns «the model did
not answer» into «the model found nothing». Fix it at the objective — a
worked example first, the schema restated at the end, an explicit "no prose
before or after" — and measure the same counter afterwards. That counter IS
the instrument; it already works.

Those 14 failures all predate the container restart at `13:25:18Z`; with an
empty candidate table the adjudicator now correctly calls no model at all
(§5's last trap). It will start failing again the moment the extractor
produces its first candidate, which is the point at which this matters.

## 3. What was never built, and whose it is

- **`Standing` / the debrief. Nobody's.** This is the biggest gap and it is
  not the Dreamer's. It is the TAG-PATH half of
  `2026-08-20-reflection-reasoning-continuity-design.md`: a revisable
  reasoning position written in-slot during a turn, which is the structural
  cure for the stateless-turn problem the whole reflection design started
  from. Specced, planned (`docs/superpowers/plans/2026-08-21-standing-debrief-plan.md`),
  **zero code** — no `debrief.py`, no `Standing`, no `TagTrace` anywhere in
  `nora/`. Two independent sessions have now confirmed this. It needs an
  owner.
- **Phase 3 — migrating the two room consolidators onto the runtime.**
  `LexemeObserver` and `ExchangePatternObserver` still make one-shot
  completions with no tool loop. The plan's design is a per-adaptor cutover
  with the old path standing beside it and deleted last, gated on live
  evidence, not on a green suite. `dream_loop` already takes both shapes
  (`consolidators=` and `adaptors=`) precisely so this is incremental.
  **The trap is the watermark**: an adaptor with a NEW name starts at 0 and
  re-reads every room from the beginning. `seed_watermarks` exists for this
  and is already wired; use it, and read the rows back.
- **Entity resolution (§13.1).** `fact_entity.aliases` exists; nothing
  populates it. Consequence today: `<facts>` uses `recent_facts` — the
  workspace's most recent standing facts, **no subject filter** — because
  inventing a resolution rule at the render site would put a second
  definition of "subject" in the codebase for the writer to disagree with.
  When resolution lands it NARROWS that read; it does not replace it.
- **Transitive supersession.** `fact_premise` makes "which facts depend on
  this one" one indexed query. Nothing walks it. A `premises`-warranted fact
  can stand on a retracted foundation indefinitely with no signal.
- **`pulse_*`.** Excluded, fail-closed: MCP tools have no `NativeTool` row to
  carry the read/act flag. The cost is real and named — the fact adaptor can
  research the open web and cannot read the group's own game state, which is
  the one thing that would settle a Big Walk progress claim.
- **The extractor emits no metrics.** None of the four declared fact counters
  describes extraction, and a malformed-item drop is none of
  `nora_fact_candidates_total{outcome}`'s documented values. Flagged rather
  than invented.

## 4. Decisions already made — do not reopen without evidence

- **The model is `deepseek-v4-flash`, by operator decision, NOT by
  measurement.** Gate D2 asked "which model" and answered "measure it"; that
  is unanswerable with nothing deployed and an empty store, and a number
  produced there would look like evidence and not be one. Running Nora's own
  model is also what keeps the architecture's claim falsifiable — the Dreamer
  is meant to do better because it is DIFFERENTLY SITUATED, not smarter. If
  §2's failure turns out to be a capability limit rather than a prompt
  problem, that reopens the design, not just the model.
- **The runtime never names `FactVerdict`.** `Proposal.payload` is `object`.
  Three tiers depend on that promise now, not two.
- **Permissive at the port, strict at each sink.** `replaced_by` is nullable
  on `DreamSink`; the fact store refuses null, the candidate store refuses
  non-null. The contract suite models this as a three-state `NullableAxis`
  because a bool could not.
- **A blank `inputs` IS the fire condition.** `DreamAdaptor` has no
  `should_fire` verb on purpose. See §5.

## 5. Traps, each of which cost real time today

1. **A polite empty prompt is not a blank one.** The runtime skips the model
   when `inputs` renders nothing. Both adaptors rendered `"Claims to settle:\n
   (none)"`, which is not blank, so the skip never fired and a model was
   invoked every 300s to be told there was nothing to do. **Observed live.**
   The contract suite hid it by asserting `inputs` was non-empty for a real
   batch — an adaptor whose input is not the message batch correctly renders
   nothing for any batch.
2. **A test double that fails in a way the real collaborator cannot tests a
   path production does not take.** `dream_loop`'s backoff had two tests, both
   using a runner that RAISES. `DreamerRuntime.run` catches everything and
   returns `unavailable` normally — so the backoff could never engage on a
   provider outage, and the success path was actively CLEARING backoff.
   Measured: 29 model attempts in ~25 ticks against an always-unavailable
   runtime, versus 1 against one that raises.
3. **A protection asserted in three documents and enforced in none.**
   `WORKSPACE_SAFE_TOOLS` was named in a docstring, in DEC-133 and in the
   release fragment. Nothing checked `key_kind`. Worse than a missing check,
   because the next reader trusts it.
4. **Two migrations can share a parent and git will never tell you.** `0026`
   and `0027` were both cut against `0025`. Git merges both files cleanly,
   every test that does not actually migrate stays green, and
   `upgrade "head"` refuses two heads at container boot. **`uv run alembic
   heads` must print exactly ONE line after every merge.**
5. **A DEC/R number agreed between two sessions can still be taken by a
   third.** Both R-55 and DEC-131 were claimed in careful cross-session syncs
   and taken anyway by a session outside the conversation. The index is the
   only authority and it must be re-read at MERGE time, not at claim time.
   Note `uniq -d` exits 0 with no output — test the string, not the exit code.
6. **`pg_url` is function-scoped**: CREATE DATABASE + the full migration chain
   + DROP, per test, at ~54 call sites. It now clones a migrated session-wide
   template instead, which took the suite from 7:52 to 4:56. If you add a
   database-backed suite, scope its database to the MODULE and TRUNCATE
   between tests — the isolation these suites need is "no rows from the last
   test", not "no schema".
7. **A wall-clock assertion in a shared test process measures the process.**
   `test_overhead_stays_within_the_budget` used `quantiles(n=20)[-1]` over
   twenty samples, which is a reading of the single worst turn. It went red
   because this branch ADDED TESTS, with nothing in the tag path changed. It
   is a median now, with a separate loose ceiling for a genuinely hung turn.

## 6. Where the code is

```
nora/agent/dreamer.py        the runtime: one public entry point, run(adaptor, key, batch)
nora/agent/dream_guard.py    R-7 default-deny for a loop with no terminal verb
nora/agent/dream_screen.py   R-18/R-28 on every envelope item, before any tier type exists
nora/agent/call_ledger.py    DEC-33's in-flight reservation, shared with ReplyGuard
nora/language/fact_adaptor.py  both adaptors
nora/infra/sql/facts.py        SqlFactStore
nora/infra/sql/fact_candidates.py  SqlFactCandidateStore (the negative cache)
nora/dreamer/objectives/       fact-candidates.md, fact-adjudication.md  <- §2 lives here
nora/app/runtime.py            dream_loop, seed_watermarks, _dream_adaptor_once
nora/app/composition.py        the wiring, behind both flags
```

## 7. Cross-session state, as of this writing

- **note tier** (`nora-cc`): merged, live, inert. Migration `0026`, four
  tables, `<notes>` renders empty because no tools are wired. Their next work
  is `nora/agent/tools.py` + `guards.py` (a read-before-write latch, R-55) +
  `composition.py`. They will pick up the `CallLedger` extraction.
- **STT** (a third session): merged as DEC-131.
- **Free numbers**: DEC-135+ and R-57+ at the time of writing. **Re-check.**
- **Migrations**: `0027` is the head. Next is `0028`; announce the PARENT, not
  just the number.
