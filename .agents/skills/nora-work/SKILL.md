---
name: nora-work
description: Start or resume Nora engineering work. Triage it to a route, hold the lifecycle's gates (triage, propose, plan, execute, verify, resolve) and retain the whole outcome across worker results and handoffs. Routes to existing procedures; not Nora runtime behavior.
---

# Nora work

The lifecycle, routes and gates: [references/lifecycle.md](references/lifecycle.md).
Ownership and policy: [ORCHESTRATION.md](../../../ORCHESTRATION.md#the-lifecycle).
This skill is the entry router; it does not repeat the other procedures.

1. **Recover the work.** Read the operator request, the current work brief, the
   project's Intent, holds and the latest handoff. The orchestrator, not a
   delegated worker, then retires settled artifacts (`make gc APPLY=1`, a
   standing authority; `docs/maintainers/conventions.md` «Cleanup»). Follow only the references
   needed to settle the current question. Check the checkout, head and actual
   artifacts before treating recorded progress as current.
   Check: state the intended outcome, what remains and the next action briefly.
2. **Triage to a route.** Apply the ordered triage rule in
   `references/lifecycle.md` «Triage», and record exactly one route: in the brief, or in
   the PR body for direct and light work. `clarify` stops and asks Mike.
   Check: the route is the highest one any trigger requires; the first
   independent reviewer confirms it, and you never confirm your own route.
3. **Check the gate before acting.** Find the transition the next action
   crosses. Confirm that the previous gate's output, as the route requires it,
   exists. On standard and full, confirm that the brief and plan pass brief
   review with no BLOCKER before dispatch and that the execution receipt is
   present before verify. On light, confirm the PR body carries a brief.
   On every route: a reviewed head before merge. When it is missing,
   halt and report what is missing. Never produce a missing gate output yourself.
   Check: the input this route requires here exists. Where the route assigns a
   checker before this point («Transitions»), someone other than its producer
   checked it; light and direct work is first checked at verify.
4. **Choose the next procedure.** Use the table below, and load that skill
   before acting. Skip steps that are already satisfied or that the route does
   not require; keep the applicable checks and holds.
   Check: the next action advances an existing Done-when item.
5. **Return to the outcome.** After a result, compare it with the brief. When a
   re-triage trigger appears (`references/lifecycle.md` «Re-triage»), re-route;
   ask Mike only for its intent, authority or redesign cases. Otherwise continue to the next item without asking («When Mike is
   asked»): report it, do not wait. Continue the remaining authorized work, or hand over or
   close through `nora-dispatch`. A worker's stop point does not end the
   parent's assignment.
   Check: completion is evidenced, or the remaining work and next action, or
   the real blocker, is named.

| next action | load |
|---|---|
| write or refine a brief and plan, do or delegate, receive results, pause, resume or close | `nora-dispatch` |
| change architecture | `nora-design` |
| record a decision, defect or deferred work | `nora-decision` |
| edit, validate, commit, PR, merge or resolve | `nora-land` |
| place or move code; write a test | `nora-structure`; `nora-test`, respectively |
| change a model-facing tool; Nora runtime skill | `nora-tool`; `nora-runtime-skill`, respectively |
| prove a code fix; measure built model behavior | `nora-prove-fix`; `nora-eval`, respectively |
| review a brief or a change; inspect live evidence | `nora-review`; `nora-readback`, respectively |
| publish a release; deploy or change host config | `nora-release`; `nora-deploy`, respectively |

Load only the relevant procedure or reference, not every skill in the table.
Research and diagnosis can stay in the conversation until they produce a
decision. Explicit spending, review and deployment limits remain in force.
