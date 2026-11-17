# The lifecycle: phases, routes and gates

Nora's engineering lifecycle follows UACP's phases in order:

**triage → propose → plan → execute → verify → resolve**

At each transition its route requires, a checker other than the doer reads a
named input and records pass or block. Each skill begins by checking that the
output of the previous gate its route requires exists, and halts when it does
not. A halt is reported, never worked around: the doer does not produce a
missing gate output itself.

## Routes

Triage records exactly one route in the work brief, or in the PR body for
direct and light work.

| route | when | gates |
|---|---|---|
| **direct** | at most two files, no behaviour change, no full trigger | the PR review at its tier, and CI |
| **light** | a bounded change with no full trigger | the brief in the PR body; the reviewer at verify reads it with the diff; Mike's merge approves |
| **standard** | a change that needs dispatch or more than one unit | brief and plan, reviewed by rule and approved by Mike before the first dispatch |
| **full** | any full trigger | as standard, with the full review tier |
| **split** | more than 6 Done-when items | a parent issue holding Intent and the child links; each child brief has at most 6 items and takes its own route |
| **clarify** | intent, authority or acceptance cannot be stated | stop and ask Mike |

**Full triggers:** disclosure, security, auth or identity, a migration, locks
or concurrency, model spending, or more than one area. An **area** is one
`nora/<area>` package or one design bundle. Choosing a lower route than the
triggers give is a finding against the work, not a judgement call.

## Transitions

| transition | routes | input | checker | pass | block | escalation |
|---|---|---|---|---|---|---|
| triage → propose | all | the request and the route the orchestrator recorded | the first independent reviewer the route has: the brief reviewer on standard and full, the PR reviewer on direct and light | the reviewer confirms the route against the triggers | a lower route than the triggers give: the work returns to triage | Mike |
| propose → plan | standard, full | the brief and its attached plan (`nora-dispatch/references/brief.md`, `plan.md`) | the reviewer by rule, in brief review | review returns no BLOCKER | a BLOCKER, returned to the author | Mike after the follow-through cap |
| plan → execute | standard, full | the reviewed brief and plan | Mike | his approval of both, recorded on the brief | no approval: nothing is dispatched | — |
| execute → verify | standard, full | the handoff PR with its execution receipt | the reviewer by rule | the receipt names evidence for each item addressed | a missing receipt or missing evidence, returned to the builder | the orchestrator |
| execute → verify | light, direct | the PR, with its brief in the body on light | the PR reviewer at the route's tier | the brief (light) and the diff are present | no brief on light | the orchestrator |
| verify → resolve | all | the reviewed head, CI, and the route's required artifacts | the reviewer, then Mike at merge | the reviewer marks each item met (light: each brief item; direct: the change's stated property), no BLOCKER, green CI | an unmet item, a BLOCKER, or a required artifact missing | Mike |
| resolve → closed | all | the merge or archive | the orchestrator, then `make gc` | branch merged or archived, the worktree retired, Linear updated | an unsettled artifact | — |

The orchestrator records the route; it never confirms its own route.

## Re-triage and the human triggers

At every transition the checker sends the work back to propose, and asks
Mike, when any of these appears:

- a new Done-when item;
- a new area;
- a new side effect;
- a missing authority;
- a BLOCKER still open after the follow-through cap (one re-check after a repair).

After Mike approves it, a brief's Done-when list is locked: only Mike changes
it. A finding outside the locked items is a follow-up, never a blocker.

## Ceiling and backstop

These gates are policy-level stops held by skills. A cooperative agent halts
at them; they cannot prevent an agent from skipping one, for example by
choosing a lower route or executing before the plan is approved. A skipped gate
leaves its route's required artifact missing. The reviewer at verify and Mike
at merge then refuse:

- standard and full: without the brief and plan with Mike's recorded approval, and without the execution receipt;
- light: without a brief in the PR body;
- direct: nothing beyond its PR review and CI.

A skipped gate therefore stops the work at verify or merge, at the latest.

## Resolve and lessons

Resolve settles every artifact: merged, or archived with `make archive`, then
`make gc APPLY=1` (`nora-land/reference.md` «Resolve»). When a failure was
caused by the process, not the code, the PR records one lesson line. That line
names the skill edit that fixes the process, made in the same PR or filed as a
follow-up. A failure in the code gets no lesson line.
