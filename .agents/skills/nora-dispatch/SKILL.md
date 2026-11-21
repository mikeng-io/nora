---
name: nora-dispatch
description: Write a bounded Nora work brief and plan, pass them through the propose and plan gates, do or delegate execution, reconcile results, and hand over or close the outcome. Load for briefs, plans, dispatch, result intake or session handoff; nora-work routes the lifecycle.
---

# Dispatch and execution

Policy: [ORCHESTRATION.md](../../../ORCHESTRATION.md). Use the relevant step;
resume from current evidence rather than restarting the whole procedure.

## 1. Read or refine the work

For new Linear work, use the [execution convention](../../../docs/maintainers/conventions.md#linear-execution).
Read the current description, its project's Intent and unresolved blockers. Capture
only what the next actor needs; follow design and evidence links on demand.

Write or revise the brief to [references/brief.md](references/brief.md): its
template, the finite Done-when checklist, review and the lock. On the standard
and full routes, attach the plan ([references/plan.md](references/plan.md)),
run the author's pre-flight («Before review»), and send both to the reviewer
by rule (`nora-review`). A round with no BLOCKER passes propose → plan and
locks Done-when. Only Mike changes the locked list.

Every Goal and every brief derived from it carries the same stop conditions:
the agent stops and hands back when every Done-when item is marked met, unmet
or unavailable with evidence; when the review has converged; when a required
input or prior artifact is missing — it halts and reports, never producing
that artifact itself; or when the next step needs an authority it does not
hold.

Keep one current brief in Linear, or in the PR for small direct work. Preserve
existing work authority until its successor and owner are verified and linked.
Record its link and source revision or update time read; code base and design revision are
separate facts. Keep current completion conditions and amendments understandable
without reconstructing archived discussion. Update authorized scope changes in
place, naming what changed and any evidence invalidated, not in a comment queue.
Before splitting implementation, compare each relevant design promise with
actual entry/wiring and evidence at a named head. Summarize promise → observed
implementation/evidence → met/unmet/unavailable → remaining work in the current
brief/run, not a new inventory document. Implement only the actual remaining gap.
Check: a fresh agent can state the outcome, completion conditions and known gap.

## 2. Choose the next unit

Do small work directly. Split only independently assignable/verifiable outcomes,
real dependencies, or an outcome over 6 Done-when items (the split route) into
issues of one Linear project; each child has its own brief and states its delta.
Use native ownership and blocking relations instead of a copied task table.
Sequence shared write ownership. For an integrated change, units are review
slices on its integration branch, not separate master releases. Use
`nora-land`'s stacked-delivery path when dependent PR layers are useful; an
issue, PR layer and worker session need not have one-to-one identities. An issue in a
project exists only for an outcome with its own owner and acceptance: a proof,
measurement or review of the project's acceptance is a check on a PR, never an
issue of its own.
Check: every Done-when item maps to a unit with a write set, and units whose
write sets overlap are sequenced (`references/plan.md`).

For cross-session work, first follow linked related work/threads; use the
session host's thread search and list (`.agents/runtimes/`) to find
missing relevant owners, then read their actual checkout/head and latest handoff. Check neighboring bundles when
they share an interface/invariant. Do not preload all sessions or infer ownership
from titles. Record the related work/thread links and shared seam in References.
Agree with its active owner on who changes that seam and the dependency/base;
sequence overlapping writes, parallelize independent work. Coordinate again at
interface/base changes, blockers, restacks and handoff. Update the owning brief
and notify affected sessions within the task's authority; read acknowledgments
before dependent writes. Unavailable coordination blocks that seam, not all work.

## 3. Execute or delegate

**Gate:** on the standard and full routes, launch nothing without a link to
a brief review that the reviewer by rule closes with no BLOCKER. If the
review is missing or blocked, halt and report. Ask Mike only under
`nora-work/references/lifecycle.md` «When Mike is asked».

Acting is the default. When the brief, plan or lanes table already settles the
next step (a test, a PR, a merge on green, a fallback reviewer, a re-run inside
the budget), take it; never end a turn on "next I will…". A goal-driven outcome
runs its checkpoint loop instead of handoff PRs (lifecycle «Goal-driven track»).

For direct work, load `nora-land` and the relevant specialist procedure.
For delegation, follow the policy's worker selection. Link the work brief and
add only execution context: base/head, prepared checkout, allowed area, skills,
named checks and the shared stop conditions. Respect dependent bases; do not silently
restart a repair on master. Read back the actual provider/model/options and
checkout. The worker does the assigned work without launching other workers.
Worktrees are created at the first write, never because a session or a review
started; a read-only review uses a detached checkout, and `make gc` retires
it.

Every builder handoff of an integrated change is a PR whose base is the
integration branch. Related handoffs stack, each based on the previous
handoff's branch (nora-land/reference.md), so they progress in parallel
without waiting for each other's merge; independent handoffs branch from the
integration head. A handoff PR ends when it merges into its base; a repair
after merge is a new handoff PR off the current base. The execution receipt
(`references/plan.md`) goes in the handoff PR as a comment.
Check: the execution has started on the intended input, with a finite boundary.

## 4. Reconcile and continue

Read the actual diff, the execution receipt and the named checks with exit
codes. The reviewer marks each item met, unmet or unavailable; the orchestrator
reconciles those marks and never marks its own or its builder's work. Before the
next dispatch, reconcile each result against the original whole outcome in the
current brief: what is now evidenced at the integrated/candidate head, what
remains, and which condition the next action advances. Repair findings and review
rounds never replace that outcome. Carry forward still-valid evidence; do not
rerun settled checks unless the change affects their basis.

- Before authorizing a repair, check the finding's requirement, real trigger,
  harm and relation to the candidate. Repair only an admitted in-scope defect
  or unmet original condition; choose the smallest correction and proving check.
  Distinguish an amended requirement, implementation defect, unfinished wiring
  and unavailable proof before choosing the action; a probe alone adds no goal.
- Keep adjacent improvements outside it; defer once under the Linear convention:
  owner, next action and revisit trigger, otherwise a review limitation.
- Resolve unknowns with the smallest useful check; they are not new requirements.
  Incomplete reviews retain exact unanswered conditions and round limits.
- Apply a hold only to the action it restricts; continue other authorized work.
  A reviewer exception wait is not automatically an implementation-wide hold.
- For repeated blockers in the same seam, revisit the intent and real flow
  before another repair. Preserve review limits; scope changes need authority.

Before review, supply [REVIEW.md](../../../REVIEW.md#scope-and-inputs)'s short brief,
including risk lenses and exclusions. `nora-review` performs the review at the
applicable tier. Integrate and measure where required, then use `nora-land`. Before claiming an eval, paid run or deploy
ready, use its authorized zero-cost rehearsal of the actual path, not a proxy.
Check: after each worker result, the parent resumes the whole outcome and selects
its next action; a green test or merged child alone is not completion.
Intake ends with `make archive` for a handoff that did not merge — merged
work's record is its merged PR — then `make gc APPLY=1`.

## 5. Close or hand over

Close only when the agreed results, including delivery where required, have
actual evidence and the artifact's outcome is settled — merged or archived.
Update the
owning Linear item: current status, outcome/evidence,
remaining work and next owner/action if any; read back the write. Update the
project's acceptance separately. Report the result, material limits and next action to the
operator. Link receipts instead of copying logs into Linear.

At pause, blocker or session replacement, update a short current handoff:
**where now, what remains, next action, active holds**, plus necessary decisions
and why. Link the work, session thread, checkout/head and evidence. Preserve context
that cannot be recovered from those links; do not repeat the code or transcript.
On resume, read it and verify the anchors before returning to the relevant step.
If tracker access is unavailable, retain the handoff in the session and disclose
the unsynchronized update; do not invent another task store or lose progress.
Check: either the whole outcome is evidenced, or its continuation is actionable.
