---
name: nora-work
description: Start or resume Nora engineering work, select its next lifecycle action, and retain the whole outcome across worker results and handoffs. Routes to existing procedures; not Nora runtime behavior.
---

# Nora work

Lifecycle and ownership: [ORCHESTRATION.md](../../../ORCHESTRATION.md#the-lifecycle).
This is a router, not a second execution procedure.

1. **Recover the work.** Read the operator request and current work brief,
   parent outcome, holds and latest handoff. Follow only references needed to
   settle the current question. Check the checkout/head and actual artifacts
   before treating recorded progress as current.
   Check: state the intended outcome, what remains and the next action briefly;
   resolve only missing facts that materially prevent that action.
2. **Choose the next procedure.** Use the table below; load that skill before
   acting. Combine or skip steps already satisfied, inapplicable or explicitly
   excepted by the operator; keep applicable checks/holds. Small work stays here.
   Check: the next action advances an existing completion condition; a design
   result alone does not end an implementation assignment.
3. **Return to the outcome.** After a result, compare it with the work brief.
   Continue authorized remaining work, or use `nora-dispatch` to hand over or
   close. A worker's stop point does not end the parent's assignment.
   Check: completion is evidenced; otherwise name the remaining work and next
   action or the real blocker. Do not write a phase report after every step.

| next action | load |
|---|---|
| define/refine work, do or delegate, receive results, pause/resume/close | `nora-dispatch` |
| change architecture | `nora-design` |
| record a decision, defect or deferred work | `nora-decision` |
| edit, validate, commit, PR or merge | `nora-land` |
| place/move code; write a test | `nora-structure`; `nora-test`, respectively |
| change a model-facing tool; Nora runtime skill | `nora-tool`; `nora-runtime-skill`, respectively |
| prove a code fix; measure built model behavior | `nora-prove-fix`; `nora-eval`, respectively |
| independently review; inspect live evidence | `nora-review`; `nora-readback`, respectively |
| publish a release; deploy or change host config | `nora-release`; `nora-deploy`, respectively |

Load only the relevant procedure/reference, not the whole table's skills.
Research and diagnosis can remain in the conversation until they produce an
implementation decision; no ticket, plan file or stage record is needed just
because a stage has a name. Explicit spending, review and deployment limits
remain in force.
