> **PARTLY DEPRECATED 2026-09-07 — every reference below to the knowledge tier (the "fact tier") or to the language tier describes code that has since been deleted.** Those parts are retained as a record of the reasoning, not as a description of the system; the rest of this document still stands.
> The database tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in `design/dreamer-agent-loop/20-knowledge-tier.md` §0. See issue #115.


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

# Dreamer runtime — handoff to the fact-tier workstream

**Date:** 2026-08-21
**From:** the substrate workstream (Phase 1, DEC-128, merged at `e1c3356`)
**To:** whoever owns the fact synthesizer
**Status:** Phase 1 done and deployed. Phases 2–3 unstarted, fully unblocked.

## Why this handoff exists

The fact synthesizer is not a sibling of the Dreamer runtime — it is the
runtime's **first real consumer**. Its sink is the fact store; its objective is
a Dreamer objective; its tool subset is a Dreamer tool subset. Building the
runtime in one session and the first adaptor in another means the adaptor
contract gets designed in the abstract and discovered to be wrong later.

One head should hold both. This document is what that head needs.

## What is already true (do not re-derive)

Merged at `e1c3356`, deployed, read back live:

- `nora/instructions/` — shared frontmatter parse + mtime/last-good reload,
  **extracted** from `nora/doctrine/loader.py` (−68/+57; 122 doctrine tests
  pass untouched). `sha256` is over the **body only**, never the frontmatter.
- `nora/dreamer/` — text-only tree, bind-mounted read-only (`rw=false`
  verified in the running container): `doctrine/` (exactly one file) and
  `objectives/` (one per adaptor).
- `instruction_sha256` + `model` on the four Dreamer tables (migrations
  `0023`/`0024`), nullable, no backfill, CHECK-constrained to hex-64 and
  non-blank. **Not** on `room_portrait`.
- `[dreamer]` config: whole-section optional, every field defaulted, **no
  `enabled` flag**.
- `tests/objectives.py` — shared helper building observers over the REAL
  `nora/dreamer/` tree, deliberately not a double.

Full rationale: **DEC-128** in `design/09-decision-index.md`, six numbered
decisions. Read that before changing any of it.

## The two rules that generalise to the fact adaptor

These were learned in Phase 1 and both apply directly to fact rows.

### 1. The digest is read PER RUN, never cached at construction

The instruction text hot-reloads. A digest computed once when the adaptor is
constructed keeps naming the corpus as it was at process start — **forever, and
it looks correct the whole time**. Read every body up front at the start of a
run (so a mid-run reload cannot split one batch's rows across two digests), not
once at `__init__`.

This is free to get right now and effectively unfixable later, because the
wrong rows are indistinguishable from right ones.

### 2. Hash only what was actually SENT

The Dreamer doctrine is excluded from the Phase 1 digest because no adaptor
sends it yet. Hashing text that never reached the model binds provenance to the
wrong property — the same defect `room_portrait` is excluded from DEC-125 for.

The fact adaptor will hold an objective and (later) the Dreamer doctrine. Only
what reaches the model belongs in the digest. When the doctrine starts being
sent, the digest and the prompt change in the **same** commit.

## Phase 2 — the runtime

Plan: `docs/superpowers/plans/2026-08-20-dreamer-substrate-plan.md`.
Keystone: `docs/superpowers/specs/2026-08-20-dreamer-as-an-agent-design.md`.

Decisions already made — §9 of the keystone, do not reopen:

| | decision |
|---|---|
| model | Flash |
| isolation | **per-adaptor**, not per-scope |
| MCP tools | **excluded** until they carry a read/act classification |
| sink | a **port**, not a write tool |

### Traps, in the order you will hit them

**`create_agent` cannot build the Dreamer.** It needs a `DreamGuard` analogous
to `ReplyGuard`: under `tool_choice="required"`, a loop with no terminal verb
can never stop. The keystone implied the factory was free; it is two tasks.

**The sink is a port, not a tool.** The Dreamer gets read verbs only. A write
tool would (a) reintroduce the no-terminal-verb problem and (b) put the model in
charge of what gets persisted. Writes leave through the sink after the run.

**The act-verb list in the keystone was four short** — `create_event`,
`update_event`, `rsvp_event`, `cancel_event` were omitted. Any plan that copies
that list grants a background agent four write verbs. Re-derive the list from
the tool registry; do not copy it from prose.

**Per-adaptor isolation means the backoff key moves too.** A per-adaptor
try/except with a per-scope backoff key reintroduces the same bug one layer
down.

## Phase 3 — adaptor migration

Migrates the two existing observers to adaptors, and performs the **three-way
re-partition** deferred from Phase 1: Dreamer-general text → doctrine,
return-shape prose → the output-schema field, remainder → objective.

**The byte-identity fixtures under `tests/fixtures/dreamer_objectives_pre_move/`
are deleted as part of that re-partition, deliberately.** Until then, any edit
to those bodies is a substrate bug — including an improving one, because a
change that both moves and improves the text cannot be distinguished from a
loader that mangled it.

**Watermark seeding:** a renamed adaptor gets `watermark_for → 0` and re-reads
every room. Cheap to forget, expensive to discover in production.

## The collision, and its current state

`nora/domain/ports.py` — `DreamSink` (Phase 2) against `FactReader` (fact tier).

As of this writing **neither branch has touched it**. `fact-tier` (26 commits
unmerged) touches neither `ports.py` nor the dreamer/instructions tree. Once one
head owns both, the collision stops existing.

## Decision-index numbering

`DEC-128` = instruction substrate (merged). `DEC-129` = fact tier (reserved by
the fact-tier branch). **Next free is `DEC-130`.** `R-54` belongs to the fact
tier.

Two DEC collisions happened in one day, both invisible to git because both
sides appended cleanly to `design/09-decision-index.md`. Flagging a shared
append-only file is not the same as reserving an identifier inside it —
**announce the number, not just the file.**

## Not in scope for this handoff

The **Standing / `debrief`** half of the reflection design
(`docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md`)
is the tag-path side — a revisable reasoning position written in-slot. It is
the structural cure for the stateless-turn problem that started this work, it
is specced and unbuilt, and it does **not** belong to the Dreamer runtime. It
needs its own owner.
