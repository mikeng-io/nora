---
type: reference
title: "Migration and Governance — Sequencing, Numbers, Tests, Landmines"
description: "Phase 3 is how the fixture freeze legitimately unfreezes. The compliance surface this work must satisfy, the tests that exist specifically to stop it happening quietly, and three pre-existing governance defects it should fix."
tags: [nora, dreamer, migration, governance, tests, decision-index]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 06-tool-surface, rel: sequences, provenance: asserted}
  - {dst: 07-room-access, rel: sequences, provenance: asserted}
  - {dst: 11-doctrine, rel: sequences, provenance: asserted}
---

# Migration and Governance

Read this before building, not after. It is an operational contract, not an
argument.

## 1. Sequencing

The dependencies are real and mostly one-way:

```
gateway probe (13-open-items)          ── blocks ──> any budget sizing
tier read tools (06)                   ── enables ─> self-correction work (09)
scope-keyed room access (07 Path A)    ── enables ─> the extraction loop (03 §6)
disclosure answer (08)                 ── blocks ──> workspace room access (07 Path B)
fixture deletion (Phase 3)             ── enables ─> editing 3 of 6 objectives (11)
global concurrency bound (10 §7)       ── blocks ──> the migration landing
```

**Phase 3 is how the fixture freeze legitimately unfreezes.** Three objective
bodies are pinned byte-for-byte, deliberately, so the lift could be proven
separable from any content change. Those fixtures are **deleted by the
migration**. So doctrine work on those three files is not blocked forever — it
is blocked until the migration that owns their deletion. The two fact
objectives are unpinned and can move first.

**Renaming an adaptor is a data migration, not a rename.** An unknown name
starts at zero and re-reads every room from the beginning. Seeding is
conditional on absence and is the mechanism; use it, and read the rows back.

## 2. Numbers

**DEC-135** and **R-57** are next free. Verified across every branch and
worktree at the time of writing — nothing claims above DEC-134 / R-56.

**Re-check at MERGE time, not at claim time.** Two sessions have coordinated
numbers carefully and had a third take one anyway.

The check CLAUDE.md prescribes is **wrong** and produces seven false positives:
`[0-9]+` truncates sub-lettered ids, so `R-1b` reads as `1`. Use:

```
grep -oE '^\| (DEC|R)-[0-9]+[a-z]?' design/09-decision-index.md \
  | sort | uniq -d
```

Note `uniq -d` exits 0 with no output — **test the string, not the exit code.**

## 3. Three pre-existing governance defects to fix in passing

1. **A genuine duplicate id.** One rule number appears on two index rows; the
   rule-to-test mapping already treats the first as its neighbour. One-line
   fix, and it makes the corrected grep above return clean.
2. **Eleven enforced rules have no index row** — including **R-56, the rule
   governing the Dreamer's tool ceiling, which is precisely what this work
   reverses**, and the disclosure-subset rule. Both are cited by name in code
   and in a decision's text, so a reader following the index finds nothing.
   There is a test that every enforced rule has a *named test*; there is none
   that it has an *index row*. Write it.
3. **Two fact-tier read ports have two implementations each and no contract
   suite** — a standing violation of the repo's own rule. A tier read tool sits
   directly on one of them, so this closes as part of the work.

## 4. Tests that exist to stop exactly this, and must be replaced not deleted

These are not obstacles. They are the system working, and a revision that
deletes them without argument is the failure they were written against.

| test | pins | what must replace it |
|---|---|---|
| the extractor holds no tools at all | the tool-less cost decision, fixed as a test | a statement of the **new** cost bound, counted — the original argument was about unbounded cost, so the replacement must show the new bound |
| a workspace-keyed adaptor is refused a room-scoped tool | asserts the literal refusal message | unchanged if Path B stays blocked, which is the recommendation |
| a scope-keyed adaptor may hold a room-scoped tool | the door already open | unchanged — this is the sanctioned direction |
| every read-only tool is classified | a new tool is **unclassified** and fails **by name** | keep; place every new tool in exactly one bucket |
| the startup and per-run checks refuse the same thing | one rule, two call sites | keep; both move together or neither does |
| asking for nothing builds nothing and touches no collaborator | the tool-free path | dies when extraction gains tools; replace with the equivalent assertion for the new empty case |
| the adjudicator's tools stay inside the workspace-safe set | non-empty **and** within the ceiling | keep |

**A new tool must be classified in five places.** The tool table, the read/act
partition in tests, and the workspace-safe or room-scoped bucket. Miss one and
a test fails by name — which is the design.

**A new behavioural rule needs both** an index row and a rule-to-test entry
with a named test. That is R-57's obligation.

## 5. Boundaries that constrain the implementation

- **A tier package may not import the agent package.** This is why tool subsets
  are declared as *names*. Tools live in the agent layer closed over injected
  ports.
- **The agent package may not import config, directly or transitively.** Config
  values arrive through constructors via a mirror type; there is an existing
  template for exactly this pairing.
- **The domain layer may import only the standard library.** Any new shared
  type lands there under that constraint.
- **Type checking does not cover the agent or tier packages** — but anything
  new in the domain layer and all composition-root wiring is fully strict.
- **The Dreamer's instruction directory is not a Python package** and must not
  gain one. It is markdown data.

**A landmine worth naming.** One boundary test is a whole-file substring check:
if a file contains a markdown extension *and* either of two common
file-reading tokens, it fails. All four likely target files already contain the
extension in prose. **Writing either token anywhere in them — including inside
a comment or a docstring — turns it red with no file read involved.**

## 6. Schema, fragments, gate

- **Exactly one migration head today.** Verify after every merge — two branches
  cutting from the same head merge cleanly, keep every test green, and fail the
  container at boot.
- The schema gate is the parity test, **not** the autogenerate check. A new
  table or column lands in lockstep across the metadata, the revision, the
  constraints, and a regenerated baseline.
- **CLAUDE.md is stale here**: it names one descending index as hand-written.
  There are now **five**, each needing rows in two separate exclusion lists.
- **New metrics must be declared** or the increment raises. Given
  [02-instrument-blindness.md](02-instrument-blindness.md), several are owed —
  in particular a per-run outcome that distinguishes a bound cap from a
  provider failure, and a fired/did-not-fire signal for every adaptor.
- **At least two release fragments.** A `change` for the model-facing tool
  surface and the altered cost profile; and a **`security`** one if the empty
  room is reversed for any key kind — given
  [08-disclosure.md](08-disclosure.md), that fragment is not optional. Only a
  newly *added* fragment satisfies the check; modifying an existing one does
  not.
- **The gate is serial and is the final check.** Iterate with the affected-test
  selector — but expect it to select everything, because any change under the
  instruction directory does so by rule. A fresh worktree needs the eval extra
  synced once or collection dies.

## 7. Contract suite obligations

A new read port, or a second implementation of an existing one, needs a shared
suite both pass. For the fact readers specifically, it must pin:

- **the never-raises versus raises asymmetry** — the critical-path reads
  degrade to empty; the background read raises by contract, because degrading
  it would report "nothing to do" when the truth is "I could not check";
- **an empty subject set yields nothing, not everything** — fail-closed;
- **workspace isolation**, and that a by-id read returns absent rather than
  standing in for unreachable.

**Scope its database to the module and truncate between tests.** A
function-scoped database fixture across a contract suite has already degraded
an unrelated wall-clock test in another directory by 10×, and the cost landed
nowhere near its cause.
