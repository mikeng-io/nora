# The work brief

The brief is the propose phase's output. On the standard and full routes it is
the Linear issue description; on the light route it is the PR body. A direct
change needs none. The lifecycle and its routes: `nora-work/references/lifecycle.md`.

## Template

| section | contains |
|---|---|
| Intent | the outcome, and why it is wanted |
| Done when | the finite checklist below |
| Boundaries | non-goals, holds, authority, side effects, and who does what |
| References | the sources and evidence links a fresh agent needs |
| Current state | where the work stands: at most five lines, replaced never appended, linking the PR; no commit IDs, receipt paths or review ledgers |

The issue also carries its Linear fields: one **Type** label, one **Area** label
(the code area the brief covers), a priority, and the project when it is part of
a split outcome (`docs/maintainers/conventions.md` «Linear execution»). The
builder's delegation prompt (skills to read, selectors, lease rules) belongs in
the delegation message, not the brief.

## Finite Done-when checklist

Every item passes all of these:

1. **Yes or no.** The item is a condition an independent reader can decide.
   "Improve", "measure", "harden" and "cover more cases" are not conditions.
2. **Named evidence.** It names the check that decides it: a test by name and
   the CI job that runs it, a read-back, or a file:line. Where the needed test
   does not exist yet, writing it is part of the item, and the item is unmet
   until the test passes. Name the cheapest check that decides the item: a
   docs or skills change is decided by its diff and a focused check, never by
   a full-suite run.
3. **A threshold for any measurement.** A count, latency or cost carries its
   limit. Where no objective is agreed, the limit is a provisional regression
   budget, labelled as such.
4. **Positive and negative.** Where a boundary matters, the item states what
   must happen and what must not; a suite of refusals alone can pass while
   every legitimate case fails.
5. **Faithful.** No requirement of the source issue or the design that belongs
   to this outcome is dropped. Moving one out of scope names it in
   Boundaries. A requirement found only in a bundle the anchor contradicts is
   a design defect to fix first, not a requirement.
6. **Small.** At most 6 items, in one area. More routes to a split, and each
   child names the parent item it advances.

Progress, gaps and partial verdicts go in Current state, never in Done when.

## Before review: the author's pre-flight

Review confirms a brief; it does not discover what the brief should say. The
author does that discovery first, so that the first review round normally finds
no BLOCKER.

1. **Census the sources.** List every requirement in the source request, the
   issue, the design it cites and Mike's rulings, each with its location, read
   at the brief's base revision. A previous session's notes, handoffs and
   narratives are leads to check, never evidence. Map
   each one to a Done-when item or to a named exclusion in Boundaries. A
   requirement mapped to neither is a gap to fix now.
2. **Run the reviewer's checklist yourself.** Apply the finite checklist above,
   and `nora-review/references/brief-review.md` checks 1–5, to every item, and
   fix what fails. For each named test, confirm that it exists at the revision.
3. **Send a readable packet.** The reviewer gets the brief, the plan, the
   census and every source as files or URLs it can open, each at a named
   revision. A link behind a login it cannot pass is not an input.

A first-round BLOCKER that this pre-flight would have caught is the author's
miss. Its fix adds or extends a lesson in `docs/maintainers/lessons.md`
(`nora-land/reference.md` «Resolve»).

## Review and lock

On the standard and full routes, the plan (`plan.md`) is attached to the brief.
The reviewer by rule then checks both together (`nora-review/references/brief-review.md`).
A round with no BLOCKER passes propose → plan and locks the Done-when list.
Only Mike changes the locked list. Plan changes within the locked items
proceed under `nora-work/references/lifecycle.md` «When Mike is asked».
An author never revises its own brief past review; a disputed item goes to
Mike.
