---
name: nora-dispatch
description: Shape a bounded Nora work brief, do or delegate execution, reconcile results, and hand over or close the outcome. Load for work refinement, dispatch, result intake or session handoff; nora-work routes the lifecycle.
---

# Dispatch and execution

Policy: [ORCHESTRATION.md](../../../ORCHESTRATION.md). Use the relevant step;
resume from current evidence rather than restarting the whole procedure.

## 1. Read or refine the work

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
separate facts. Update authorized scope changes in place, not in a comment queue.
Check: a fresh agent can state the outcome and completion conditions in one read.

## 2. Choose the next unit

Do small work directly. Split only independently assignable/verifiable outcomes
or real dependencies into sub-issues; each states its delta and links its parent.
Use native ownership and blocking relations instead of a copied task table.
Sequence shared write ownership. For an integrated change, units are review
slices on its integration branch, not separate master releases.
Check: the remaining completion conditions, including integration, have owners.

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
met, unmet or unavailable, with evidence locators and their limits.

- Repair defects that defeat the existing completion conditions in this work.
- Keep adjacent improvements outside it; record actionable deferred work once.
- Resolve unknowns with the smallest useful check; they are not new requirements.
- For repeated blockers in the same seam, revisit the intent and real flow
  before another repair. Preserve review limits; scope changes need authority.

Route review through `nora-review` at the applicable tier; integrate and measure
where required, then use `nora-land`. Before claiming an eval, paid run or deploy
ready, use its authorized zero-cost rehearsal of the actual path, not a proxy.
Check: after each worker result, the parent resumes the whole outcome and selects
its next action; a green test or merged child alone is not completion.

## 5. Close or hand over

Close only when the agreed results, including delivery where required, have
actual evidence. Link receipts instead of copying logs into Linear.

At pause, blocker or session replacement, update a short current handoff:
**where now, what remains, next action, active holds**, plus necessary decisions
and why. Link the work, T3 thread, checkout/head and evidence. Preserve context
that cannot be recovered from those links; do not repeat the code or transcript.
On resume, read it and verify the anchors before returning to the relevant step.
If tracker access is unavailable, retain the handoff in the session and disclose
the unsynchronized update; do not invent another task store or lose progress.
Check: either the whole outcome is evidenced, or its continuation is actionable.
