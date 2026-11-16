---
name: nora-dispatch
description: Shape a bounded Nora work brief, do or delegate execution, reconcile results, and hand over or close the outcome. Load for work refinement, dispatch, result intake or session handoff; nora-work routes the lifecycle.
---

# Dispatch and execution

Policy: [ORCHESTRATION.md](../../../ORCHESTRATION.md). Use the relevant step;
resume from current evidence rather than restarting the whole procedure.

## 1. Read or refine the work

For new Linear work, use the [execution convention](../../../docs/maintainers/conventions.md#linear-execution).
Read the current description, parent outcome and unresolved blockers. Capture
only what the next actor needs; follow design and evidence links on demand.

| work brief | contains |
|---|---|
| Intent | the outcome and why |
| Done when | observable completion conditions |
| Boundaries | non-goals, holds and relevant design constraints |
| References | necessary sources and evidence links |

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

Do small work directly. Split only independently assignable/verifiable outcomes
or real dependencies into sub-issues; each states its delta and links its parent.
Use native ownership and blocking relations instead of a copied task table.
Sequence shared write ownership. For an integrated change, units are review
slices on its integration branch, not separate master releases. Use
`nora-land`'s stacked-delivery path when dependent PR layers are useful; a child
work item, PR layer and T3 worker need not have one-to-one identities.
Check: the remaining completion conditions, including integration, have owners.

For cross-session work, first follow linked related work/threads; use T3's
project thread search/list (or the host's equivalent session store) to find
missing relevant owners, then read their actual checkout/head and latest handoff. Check neighboring bundles when
they share an interface/invariant. Do not preload all sessions or infer ownership
from titles. Record the related work/thread links and shared seam in References.
Agree with its active owner on who changes that seam and the dependency/base;
sequence overlapping writes, parallelize independent work. Coordinate again at
interface/base changes, blockers, restacks and handoff. Update the owning brief
and notify affected sessions within the task's authority; read acknowledgments
before dependent writes. Unavailable coordination blocks that seam, not all work.

## 3. Execute or delegate

For direct work, load `nora-land` and the relevant specialist procedure.
For delegation, follow the policy's worker selection. Link the work brief and
add only execution context: base/head, prepared checkout, allowed area, skills,
named checks and worker stop point. Respect dependent bases; do not silently
restart a repair on master. Read back the actual provider/model/options and
checkout. The worker does the assigned work without launching other workers.
Check: the execution has started on the intended input, with a finite boundary.

## 4. Reconcile and continue

Read the actual diff and named checks with exit codes. Mark required results
met, unmet or unavailable, with evidence locators and their limits. Before the
next dispatch, reconcile each result against the original parent outcome in the
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

## 5. Close or hand over

Close only when the agreed results, including delivery where required, have
actual evidence. Update the owning Linear item: current status, outcome/evidence,
remaining work and next owner/action if any; read back the write. Update parent
acceptance separately. Report the result, material limits and next action to the
operator. Link receipts instead of copying logs into Linear.

At pause, blocker or session replacement, update a short current handoff:
**where now, what remains, next action, active holds**, plus necessary decisions
and why. Link the work, T3 thread, checkout/head and evidence. Preserve context
that cannot be recovered from those links; do not repeat the code or transcript.
On resume, read it and verify the anchors before returning to the relevant step.
If tracker access is unavailable, retain the handoff in the session and disclose
the unsynchronized update; do not invent another task store or lose progress.
Check: either the whole outcome is evidenced, or its continuation is actionable.
