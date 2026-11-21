---
name: nora-design
description: Writes and edits Nora's design bundles (design/<topic>/), the anchor the code is built from, with design/cognition/ as the architecture every bundle uses. Decides whether a change is architecture (design first) or code (straight to code), keeps a bundle at design altitude, and edits it in place. Load before any design/ edit, any architecture change, or when a bundle and the code disagree.
---

# nora-design

The design bundle is the anchor of the system: its high-level architecture,
precise and concise. Keep the intended architecture explicit before or with its
implementation. Git is the log; the bundle describes the system, not execution
progress. `nora-work` owns continuation after design within the authorized task.

## 1. Is this an architecture change?

First compare the relevant bundle promises with current entry points, wiring
and existing evidence. Include connected bundles sharing the changed invariant;
do not census every design. Use `nora-review`'s acceptance-audit mode if an
independent comparison is required. A missing proof is not missing code.

Ask: **would a reader of the bundle see a difference?** It is architecture when
the change alters

- a component: added, removed, or given a different responsibility;
- what talks to what, or what crosses a boundary;
- an invariant: who may read what, what must never egress, what fails closed;
- the vocabulary: what a turn, unit, note or occasion *is*.

Then the bundle changes in the same PR, before or with the code.

It is a code change when only *how* a part does its job moves: a hotfix, a
refactor, a threshold, prompt or doctrine wording, an eval case, a performance
fix. The design does not move; do not touch `design/`.

Check: the PR's «Architecture» section names each bundle changed with its
trigger, the behaviour before and after, and the test or read-back that shows
it; or says `none` with the reason.

## 2. Altitude: what a bundle holds

| In the bundle | Elsewhere |
|---|---|
| The parts, what each is responsible for, how they connect | **Implementation detail** (fields, schemas, values, config keys, signatures): code and tests |
| The model and its terms: a glossary-level contract | **Work brief** (intent, completion conditions, scope): Linear; native tasks/dependencies express execution |
| Boundaries and invariants | **Log** (dates, reviews, measurements, "supersedes", deprecation): git and the PRs |
| Why it is shaped this way: rationale, alternatives rejected | **Delivery progress**: Linear execution and GitHub roadmap; code/PR/runtime evidence establishes what exists |

A contract stays at the level of terms and promises. *"An intention is what
Nora has committed to; it closes only by a recorded outcome; the model
proposes it and code admits it"* is design. Its columns, lengths and rendering
are code. Each part names its owning module or symbol, never a line number,
so a reader can verify the bundle against the code.

**How a definition is written.** Concise and precise; each form does one job:

| form | use it for |
|---|---|
| one sentence | what a term *is* |
| table | a set of members sharing attributes: stages, kinds, passes, tables |
| bullets | rules and invariants, one testable rule per line |
| short paragraph | how parts relate, and why it is shaped this way |

A rule forced into a table loses its "because"; a set written as prose hides
its gaps. Each term is defined once, in the file that owns it, and every other
file links to it.

**The architecture anchor.** [`design/cognition/`](../../../design/cognition/README.md)
defines how Nora works: one Cognitive Graph (basis, scope, five kinds) worked
by engagement and reflection loops sharing OODAS stages, passes and executors.
Every bundle uses its vocabulary. A bundle that adds its own classifying axis, store, loop
or engine for one capability fails Coherence: extend the anchor's form for
every capability, or express the capability in its kinds and stages.

## 3. Shape

```
design/<topic>/
  README.md        overview: purpose, the parts, relevant work/roadmap links
  <subject>.md     one file per design subject, named for what it describes
```

- Files are named by subject (`boundaries.md`, `notes.md`), never numbered,
  never dated. No `_index.yaml`, decisions, review, draft or roadmap files.
- Budget: `README.md` at most 150 lines, a subject file at most 300. A change
  that would pass the budget is a cue to consolidate, not to split.
- Link relevant execution/roadmap work when useful. Do not maintain proposal,
  building or implementation states in the bundle; trackers own progress.
  A design document is not evidence that its components exist. When touching
  a bundle, remove legacy `Status:` progress and execution-issue search links;
  retain links to actual problem reports where useful.
- No frontmatter dates, `supersedes:` blocks, deprecation banners or
  "was X, now Y". No decision numbers except a link to an ADR the reader needs.

## 4. Editing

1. **Find the owner.** `git grep -n -i '<subject words>' -- design` and read the
   bundle's `README.md`. Check: one file owns the subject, or none does.
2. **Revise the owner in place**: update or consolidate it so it reads as if it
   had always said the new thing. Delete what the change makes untrue. Add a
   section or file only for a subject nothing covers. Check: `git diff --stat -- design` shows edits and deletions,
   not only additions; a purely additive diff needs a new subject to justify it.
3. **Abandoned design is deleted**, not marked. The commit says what replaced it.
4. **Move non-design content out** as you meet it: execution work to
   Linear and roadmap outcomes to GitHub Project (`nora-decision`);
   delete detail the code already holds.
5. **Design behind the code** (the code changed the architecture without the
   bundle): bring the bundle up to the code in the same PR, or record the gap
   in its Linear work brief.

## 5. Reading a bundle

The bundle states intended architecture. Check current code and linked work
before claiming implementation or delivery. A gap may be planned work, drift or
an outdated design; use the brief and PR history to distinguish them before
changing code or docs. Do not maintain a second progress state in the bundle.

After a design result, return to `nora-work`: continue an implementation task
through its remaining obligations; close a design-only task at its own boundary.
Follow explicit operator pauses/exceptions without silently abandoning the parent.

## 6. Check

```bash
uv run python -m scripts.pytest_gate tests/architecture/test_design_is_design.py \
  tests/architecture/test_doc_references_resolve.py
```

PASS is rc 0. The gate checks shape (names, banners, dates); altitude and
in-place editing are the reviewer's check (`nora-review`).
