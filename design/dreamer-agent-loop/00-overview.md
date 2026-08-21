---
type: design
title: "The Dreamer Agent Loop — Revision Overview"
description: "The Dreamer was designed as a research agent and built as a pipeline. Entry point for the revision layer: what is live, what was designed and never shipped, and the decisions that close the gap."
tags: [nora, dreamer, agent, tools, revision, architecture]
timestamp: '2026-08-22'
supersedes: >
  Framing in docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md §1
  (stale measurements), 2026-08-20-fact-tier-design.md §4.2 (no fact-read tool)
  and §5 (Phase A tool-less), and 2026-08-20-dreamer-prompt-substrate-design.md
  §2 (one doctrine for every adaptor). Defers the rest.
status: proposed
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: motivated_by, provenance: asserted}
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
  - {dst: 04-designed-vs-shipped, rel: motivated_by, provenance: asserted}
  - {dst: 05-input-or-tool, rel: decides_on, provenance: asserted}
---

# The Dreamer Agent Loop — Revision Overview

> This is the entry point for the 2026-08-22 revision layer. It does **not**
> rewrite the three 2026-08-20 specs in place. It supersedes their framing
> where they conflict with what shipped or with what is now known, and defers
> the rest. Read this, then `01`–`04` for the evidence, then the detail nodes.

## 1. The diagnosis, in one line

**The Dreamer was designed as a research agent and built as a pipeline.**

The keystone spec's own framing — *"Proving a fact is not a one-shot task — it
is search, read, notice the source does not cover the question, search
differently, cross-check. That is a tool loop with judgement in it"* — describes
a thing that does not exist in production. Four of the five Dreamer
participants make a single completion call with no tools. The fifth has a loop
and can only look **outward**, at the open web; it cannot consult the room, the
transcript, Nora's memory, the notes, or its own prior conclusions.

Every one of the operator's originally-named tools — *the previous calls, the
image, the previous transcript, the user profile* — is absent from every
shipped adaptor.

## 2. The evidence, in one line

**963 messages processed. Two rows written. Both wrong.**

That is the Dreamer's entire lifetime output across every tier. The two rows
are contradictory glosses of the same phrase, both `live`, rendered side by
side into Nora's prompt with the wrong one first. Full accounting in
[01-live-state.md](01-live-state.md).

## 3. What makes this a bundle and not a bug list

Six independent investigations found the same shape at six different layers,
and it is not "the tools were forgotten". Three findings organise the rest:

1. **Nobody decided any of it.** The tool set narrowed from eleven to four
   through the composition of three documents and one mechanism that did not
   know about each other. There is no recorded decision to remove them. See
   [04-designed-vs-shipped.md](04-designed-vs-shipped.md).
2. **Every layer has an instrument that cannot measure the thing it exists to
   measure.** Five of them, independently. This repo's own rule — *"Never read
   an instrument's output as evidence without first checking it can measure the
   thing"* — is violated throughout the subsystem that most needs it, because
   nobody is waiting on a background loop. See
   [02-instrument-blindness.md](02-instrument-blindness.md).
3. **The safety argument for the missing half is unsound, and the missing half
   is not one change.** Giving a *scope*-keyed adaptor room tools is a small,
   already-sanctioned wiring change. Giving a *workspace*-keyed adaptor the
   same thing is blocked by a real, unclosed gap in R-49. See
   [07-room-access.md](07-room-access.md) and
   [08-disclosure.md](08-disclosure.md).

## 4. Decision trail

Proposed, not yet ratified. Each is argued in the node named.

| # | Decision | Supersedes | Rationale (short) | Node |
|---|---|---|---|---|
| D1 | The loop is the deliverable, not the runtime. An adaptor with no tools is a completion, and the runtime's stopping condition is satisfied on pass one. | keystone §1's "measured against HEAD" table | The machinery exists and guards a loop that never runs | [03](03-no-agent-loop.md) |
| D2 | **Knowable-before-the-call is an input; discovered-during-reasoning must be a tool.** | fact-tier §4.2's blanket "no fact-read tool" | §4.2 is a correct rule applied to a case it does not fit; the rule generalises, the refusal does not | [05](05-input-or-tool.md) |
| D3 | A tier contributes its own read tools through `ToolRegistry`, as the note tier already does. No bespoke per-tier mechanism. | (new) | The seam exists, is proven in-tree, and serves the tag path and the Dreamer from one instance | [06](06-tool-surface.md) |
| D4 | Room access splits by `key_kind`. Scope-keyed proceeds; workspace-keyed is **blocked** pending an answer to R-49's second condition. | the implicit assumption that these are one change | One is wiring; the other is an unclosed disclosure question | [07](07-room-access.md) |
| D5 | Reading is **not** made safe by the egress screen. A room read changes the *content class* of what the fact tier persists. | this bundle's own earlier draft; DEC-133(2)'s cost note | The screen is four regexes; the leak moves to the write | [08](08-disclosure.md) |
| D6 | Self-correction is per tier, and the language tier's absence of it is a **designed cost** to be reopened deliberately — not a bug to patch. | (clarifies room-language §6 and §12) | The compound PK is a fix for an earlier bug; reversing it needs its own argument | [09](09-self-correction.md) |
| D7 | Every fix ships its own measurement. A change whose effect cannot be read back is not done. | (new; makes CLAUDE.md's rule structural here) | Five blind layers is one missing discipline, not five bugs | [02](02-instrument-blindness.md) |
| D8 | Budgets get an R-24 analog, and the per-call ceiling is verified against the gateway before anything is sized. | `[dreamer.budgets.*]` as shipped | Four of five budgets run at the bare minimum R-24 tolerates; the observed ceiling does not match the configured one | [10](10-orchestration.md) |
| D9 | Doctrine becomes shape-aware. `applies_to` stops being decorative. | substrate §2's one-doctrine-for-all | A research preamble on a tool-less extractor has no reachable stopping condition | [11](11-doctrine.md) |
| D10 | Phase 3 is how the DEC-128 fixture freeze legitimately unfreezes. | (sequencing, not a reversal) | The pre-move fixtures are deleted *by* the migration; editing them before it is a substrate bug | [12](12-migration-and-governance.md) |

## 5. Detail nodes

| Node | Covers |
|---|---|
| [01-live-state.md](01-live-state.md) | What is actually true right now: output, watermarks, the failure reconstruction, why each table is empty |
| [02-instrument-blindness.md](02-instrument-blindness.md) | The five layers whose instruments cannot measure their subject |
| [03-no-agent-loop.md](03-no-agent-loop.md) | `tools=[]`, one-pass termination, the outward-only loop |
| [04-designed-vs-shipped.md](04-designed-vs-shipped.md) | The eleven-to-four narrowing nobody decided; the three-document collision |
| [05-input-or-tool.md](05-input-or-tool.md) | The rule that replaces fact-tier §4.2 |
| [06-tool-surface.md](06-tool-surface.md) | What each adaptor holds, and the tier-contributes-read-tools seam |
| [07-room-access.md](07-room-access.md) | Path A vs Path B; the empty-`Room` guard |
| [08-disclosure.md](08-disclosure.md) | R-49's missing second condition; the act verb hiding as a read |
| [09-self-correction.md](09-self-correction.md) | Supersession per tier, and why language has none |
| [10-orchestration.md](10-orchestration.md) | Backoff, timeouts, concurrency, budget arithmetic |
| [11-doctrine.md](11-doctrine.md) | The doctrine/objective seam and shape-awareness |
| [12-migration-and-governance.md](12-migration-and-governance.md) | Phase 3, DEC/R numbers, tests to replace, landmines |
| [13-open-items.md](13-open-items.md) | Unresolved, starting with the gateway probe |

## 6. Grounding

Everything in `01`–`04` was read back from the running system or from the
source, not recalled. Provenance is cited per claim inside each node.

- **Live container** `nora`, image built `2026-08-21T13:24:55Z`; the failure
  window ran under a previous container from the same image.
- **Live database** `nora-db-1` — watermarks, tier tables, message counts.
- **`/app/data/nora.log`** — 88,678 lines, `2026-08-01` → `2026-08-21T17:38`.
  Note `docker logs nora` is always empty; the file inside the container is the
  only log.
- **Langfuse trace** `b782c5afba571f7ec47cf706eb6ebe18` — the single-generation
  runaway, contributed by a peer session.
- **The design corpus** — twelve specs under `docs/superpowers/specs/`, read in
  full rather than summarised.

## 7. What this bundle does not decide

- **The fact tier's store.** Owned elsewhere; unchanged here.
- **Standing / the debrief.** Tag-path work, separately owned. This bundle's
  input-vs-tool rule ([05](05-input-or-tool.md)) applies to it, and that is the
  whole of the overlap.
- **Entity resolution.** Named as a dependency in
  [09-self-correction.md](09-self-correction.md); its mechanism stays open.
- **Whether the Dreamer's model changes.** The keystone's falsifiability
  obligation is still unmet and is recorded in
  [13-open-items.md](13-open-items.md), not answered here.

## Status / Checkpoint

2026-08-22 — bundle drafted from six parallel investigations. No code. No
`design/09-decision-index.md` entry yet: DEC-135 and R-57 are reserved but
unclaimed until merge (see [12](12-migration-and-governance.md)).
`[dreamer] enabled = false` on master since `2026-08-21T17:02Z`, container
recreated and read back — the loop reports `0 adaptor(s)`. Nothing is running.
