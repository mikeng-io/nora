# Lessons from merged work

What merged work got wrong, and the rule that would have caught it. Read it
before changing code, next to the [code standards](../code-standards.md).

## How a lesson is kept

A **lesson** is one rule, then why, then the evidence: the PRs or commits
where it bit and the one that fixed it. **Seen** is the dates a review found
it, one per separate day.

- **When.** A lesson is written when a PR fixes something an earlier merge got
  wrong, or when a process failure causes the work to stall. The PR
  writes it at resolve (`nora-work/references/lifecycle.md`). A review of recent merges,
  started by the operator or the orchestrator, adds the ones that were missed.
  No scheduled job writes here.
- **Seen again.** A repeat sighting adds its date and evidence to the
  existing lesson, never a second lesson for the same rule.
- **Promotion.** A lesson seen on three separate days becomes a row in the
  code standards, in the section of the axis it belongs to: cross-cutting, or
  the stack it is about. The row names what holds it: a test if one can, else
  *review*. Its evidence moves to the
  [conventions rationale](conventions-rationale.md), and the lesson leaves
  this file in the same PR.
- **Retirement.** A lesson is removed when a test now enforces its rule and
  that test is cited in the code standards.

## Open lessons

### Search every lane before filing or building

Before opening an issue, a brief or a PR, search the open PRs, every Linear
project and the running orchestrator threads for an owner of the same
subject. Two lanes fixing one gate set means one of them rebases onto the
other's reconciliation.

**Evidence:** PR #815 (manual full-suite acceptance) overlapped PR #818
(architecture-gate fixes, NOR-16) and PR #819 (`REVIEW.md` consolidation) in
five files. It merged after a rebase to reconcile them.

**Seen:** 2026-11-19.

### A layer joins its native stack, and the procedure names the command

A dependent PR of an integrated outcome joins the outcome's GitHub-native
stack through the REST stacks API (`nora-land/reference.md` «Stacked
delivery»). A base branch pointing at the layer below is not stack
membership. Read the stack back after every create or extend.

**Evidence:** the Cognitive Graph layers #764–#821 form stack #824, and the
operator linked it by hand. The procedure required a native stack but named
no command, `gh stack` was not installed, and the plan's `base` field read as
a branch chain. PR #827 added the `gh api` commands and the stack field.

**Seen:** 2026-11-19.

### A brief's pre-flight names tests by full id and finds its write set by search

Confirm every existing test a Done-when item names with an exact `def <name>(`
search, never a prefix, and build a unit's write set from `git grep` for the
names it removes or renames, run against the unit's own list. The reviewer
repeats both searches; a gap there is the author's miss.

**Evidence:** the first brief review of the browser replacement (NOR-21,
PR #806) returned three blockers that the author's own searches would have
caught: two revocation tests named by prefix did not exist, ten files naming the
removed tool sat outside every unit's write set, and one clause of an item had no
check.

**Seen:** 2026-11-19.
