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

The builder's delegation prompt (skills to read, selectors, lease rules)
belongs in the delegation message, not the brief.

## Finite Done-when checklist

Every item passes all of these:

1. **Yes or no.** The item is a condition an independent reader can decide.
   "Improve", "measure", "harden" and "cover more cases" are not conditions.
2. **Named evidence.** It names the check that decides it: a test by name and
   the CI job that runs it, a read-back, or a file:line. Where the needed test
   does not exist yet, writing it is part of the item, and the item is unmet
   until the test passes.
3. **A threshold for any measurement.** A count, latency or cost carries its
   limit. Where no objective is agreed, the limit is a provisional regression
   budget, labelled as such.
4. **Positive and negative.** Where a boundary matters, the item states what
   must happen and what must not; a suite of refusals alone can pass while
   every legitimate case fails.
5. **Faithful.** No requirement of the source issue or the design that belongs
   to this outcome is dropped. Moving one out of scope names it in
   Boundaries.
6. **Small.** At most 6 items, in one area. More routes to a split.

Progress, gaps and partial verdicts go in Current state, never in Done when.

## Approval and lock

On the standard and full routes, the plan (`plan.md`) is attached to the brief.
The reviewer by rule then checks both together (`nora-review/references/brief-review.md`).
After a round with no BLOCKER, Mike approves, and the approval is recorded in
Boundaries. From then on the Done-when list is locked: only Mike changes it.
An author never revises its own brief past review; a disputed item goes to
Mike.
