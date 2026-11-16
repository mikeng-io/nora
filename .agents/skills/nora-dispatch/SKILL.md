---
name: nora-dispatch
description: Turns a Linear work brief into bounded execution, delegates only when useful, reads results back and carries the whole outcome through review and integration. Load before dispatch, intake or handoff. Not for design (nora-design), review (nora-review) or landing (nora-land).
---

# nora-dispatch

Linear owns work; the repository owns design and code; GitHub owns defect
reports and PRs. `ORCHESTRATION.md` owns policy, tiers and review limits.
This skill is a procedure, not runtime enforcement or an installed scheduler.

## 1. Read the current work brief

Read the Linear issue description first. Its **work brief** combines intent,
completion conditions and scope; there are no separate plan/spec documents.
For a small direct change, the PR can record the operator's brief without a
new ticket. Read linked design subjects and evidence only as needed.

```
Intent       the outcome and why it matters
Done when    observable results that establish completion
Boundaries   non-goals, active holds and applicable design links
References   only necessary source, dependency or evidence links
```

- The model chooses implementation details; the brief need not enumerate files,
  functions or every scenario. Missing material intent remains missing: resolve
  it before building instead of guessing or writing a PRD.
- Keep current intent and completion conditions directly readable. Comments
  hold discussion or precise evidence, never the only current acceptance or a
  second queue. Incorporate an authorized amendment into the description.
- Read the selected work, its parent intent and actual blockers; do not preload
  every project issue, all comments or historical repair receipts. If the brief
  is too broad to understand in one read, split the outcome, not its sentences.
- Pin the work URL and intent/completion-condition digest in the run, plus the
  design commit/path and code base separately. Existing GitHub work keeps its
  authority until a read-back-verified Linear successor is linked; never lose
  obligations, holds or a running owner during migration.

Check: a fresh agent can state the outcome, completion conditions and boundary
without reconstructing the conversation.

## 2. Refine only enough to execute

One work item is one bounded outcome. Use Linear's owner, delegate, parent,
blocking relations and status rather than copying them into prose.

- Create sub-issues only for independently assignable or verifiable outcomes,
  work with its own dependency, or work that must survive a session. A function,
  test command or every review finding does not need a ticket.
- Each child inherits the parent's intent and boundaries; state only its own
  outcome/delta and link the parent. Every parent completion condition must
  have an owner, including integration. Do not copy the parent brief.
- Parallelize only independent write ownership. Shared seams are sequenced;
  one committing agent per isolated worktree. The dependency view is the plan.
- Units of one integrated change are review slices, not separate releases:
  review and integrate each on the integration branch, then measure the whole.

Check: prerequisites have evidence, every remaining obligation has an owner,
and the next unit advances the agreed outcome rather than a repair queue alone.

## 3. Launch bounded work

Do small changes yourself. For delegation, follow the worker selection and
independence rules in `ORCHESTRATION.md`. The launch prompt links the current
work brief and adds only execution context: base/head, prepared checkout,
allowed area, applicable skills, named checks and worker stop point. Do not
paste another spec, generic repo policy or history into the prompt.

Before launching, confirm intent still matches the pinned input, the owner and
prerequisites are known, and the stop point is finite. Check unknown dependencies
before dispatching implementation. Read back the actual provider/model/options,
checkout and head; choosing a delegate is not evidence that it started.

Check: one outcome per launch. The worker does the work itself, launches no
reviewers/children, and stops at the brief's boundary.

## 4. Intake and route new findings

Read the actual diff against the assigned base and each named check's output
and exit code. Map completion conditions to met, unmet or unavailable with
locators; a local or synthetic probe proves only its exercised path.

- An implementation defect stays in the same unit for bounded repair. A new
  finding is included only if it defeats an existing completion condition.
- An adjacent improvement becomes separate backlog work when actionable; it
  does not block this outcome. An unknown needs the smallest discriminating
  check, not automatic implementation. A changed outcome/boundary requires an
  explicit operator amendment; a reviewer cannot add requirements.
- Repeated blockers in the same seam mean the model of the work needs revision:
  stop another repair launch, reread the intent and actual flow, then refine
  the existing work. Do not reset review limits or manufacture new task IDs.
- Update remaining work and next action in place. Put a short result with head
  and evidence links in the run/PR; do not duplicate full receipts in Linear.
  Preserve applicable checks and rerun only changed or unproved obligations
  under repository validation rules.

Check: a worker report or green test count alone cannot mark acceptance met.

## 5. Review and integrate

Route the actual base/head and work brief to independent review per
`nora-review`. Triage under `ORCHESTRATION.md`; only a BLOCKER buys a repair
round, with two rounds at most unless the operator explicitly authorizes more.
An invalid plan returns to refinement, not a wider review.

Integrate accepted units, resolve shared seams, and measure the integrated
head on the required path. No ready claim for an eval, paid run or deploy
without an authorized zero-cost rehearsal of that exact path, including SDK,
budgets, judges and reports. Existing holds still apply. Land via `nora-land`.

Check: unit acceptance, integrated acceptance, merge and runtime acceptance
remain distinct, each with its actual evidence.

## 6. Close or hand over the outcome

Use Linear's workflow for ready, running, review, blocked and done; these are
work states, separate from an agent session's pending/active/complete state.
A session response, merged PR or all children closed is not parent acceptance.
The outcome owner closes only after all required completion conditions hold.

A worker stops; the orchestrator continues authorized next work. A replacement
operator-facing session inherits the whole outcome, current work URL, active
holds, checkout/head and next action. Keep that handoff short and linked, not
another planning document. A limit on one action does not stop unrelated
authorized progress. Error/blocked retains the cause and unblock condition.

Check: no runnable outcome is abandoned at a local checkpoint and no blocked
or partially delivered outcome is relabeled done.

## 7. Automation boundary

Use native PR links, delegation and status views. MCP access lets a running
agent read/write Linear; it does not start a dormant T3 session. Native agent
integrations or an explicitly configured event adapter own unattended starts.
Do not enable a second dispatcher, implicit retries or paid coding/Loops merely
because a work item exists or changes status. Read back actual wiring first.
No GitHub Issue mirror or parallel execution board is required.
