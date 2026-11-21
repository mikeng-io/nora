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
| **standard** | a change that needs dispatch or more than one unit | the reviewer by rule closes brief review of the brief and plan with no BLOCKER before the first dispatch |
| **full** | any full trigger | as standard, with the full review tier |
| **split** | more than 6 Done-when items | a Linear project holding the Intent, a milestone per phase, and one issue per child brief of at most 6 items, each taking its own route |
| **clarify** | intent, authority or acceptance cannot be stated | stop and ask Mike |

**Full triggers:** disclosure, security, auth or identity, a migration, locks
or concurrency, model spending, or more than one area. An **area** is one
`nora/<area>` package or one design bundle. Choosing a lower route than the
triggers give is a finding against the work, not a judgement call.

## Transitions

| transition | routes | input | checker | pass | block | escalation |
|---|---|---|---|---|---|---|
| triage → propose | all | the request and the route the orchestrator recorded | the first independent reviewer the route has: the brief reviewer on standard and full, the PR reviewer on direct and light | the reviewer confirms the route against the triggers | a lower route than the triggers give: the work returns to triage | Mike |
| propose → plan | standard, full | the brief and its attached plan (`nora-dispatch/references/brief.md`, `plan.md`) | the reviewer by rule, in brief review | the round closes with no BLOCKER and locks Done-when | a BLOCKER, returned to the author | Mike for intent, authority or redesign («When Mike is asked») |
| plan → execute | standard, full | the plan, validated in that brief review | the reviewer by rule | the plan passed brief review's «Mapped» check | a plan BLOCKER: nothing is dispatched | — |
| execute → verify | standard, full | the handoff PR with its execution receipt | the reviewer by rule | the receipt names evidence for each item addressed | a missing receipt or missing evidence, returned to the builder | the orchestrator |
| execute → verify | light, direct | the PR, with its brief in the body on light | the PR reviewer at the route's tier | the brief (light) and the diff are present | no brief on light | the orchestrator |
| verify → resolve | all | the reviewed head, CI, and the route's required artifacts | the reviewer, then Mike at merge | the reviewer marks each item met (light: each brief item; direct: the change's stated property), no BLOCKER, green CI | an unmet item, a BLOCKER, or a required artifact missing | Mike |
| resolve → closed | all | the merge or archive | the orchestrator, then `make gc` | branch merged or archived, the worktree retired, Linear updated | an unsettled artifact | — |

The orchestrator records the route; it never confirms its own route.

## Re-triage

At every transition, the checker sends the work back to triage when a new area
appears. The agent records the new route and continues, and the next brief
review confirms it.

Mike is asked only when one of these appears («When Mike is asked»):

- **Intent:** a disputed brief item or a Done-when item the locked list does not contain.
- **Authority:** a missing authority, or a side effect beyond what the brief grants.
- **Redesign:** a re-check leaves blockers open and closes no class, or a repair introduces a new blocker class.

When a brief review round closes with no BLOCKER, the Done-when list locks.
Only Mike changes it. A finding outside the locked items is a follow-up, never
a blocker.

## When Mike is asked

Mike is asked only for what only he holds. Everything else proceeds without him.

- **Intent:** a disputed brief item, amending a locked Done-when list, or `clarify`.
- **Authority:** merge, deploy, paid probes or evals, live accounts, and a side
  effect beyond what the brief grants.
- **Redesign:** a re-check leaves blockers open and closes no class, or a repair
  introduces a new blocker class.

He is never asked to continue. Moving from one Done-when item to the next,
starting another review round while each round closes blocker classes, and
proceeding past a gate whose checker is a reviewer or CI need no go. The agent
obtains those checks itself. A report at an item boundary is information, not
a request; the work goes on unless he stops it.

## Ceiling and backstop

These gates are policy-level stops held by skills. A cooperative agent halts
at them; they cannot prevent an agent from skipping one, for example by
choosing a lower route or executing before the plan passes brief review. A
skipped gate leaves its route's required artifact missing. The reviewer at
verify and Mike at merge then refuse:

- standard and full: without a brief review that closes with no BLOCKER and
  an execution receipt;
- light: without a brief in the PR body;
- direct: nothing beyond its PR review and CI.

A skipped gate therefore stops the work at verify or merge, at the latest.

## Resolve and lessons

Resolve settles every artifact: merged, or archived with `make archive`, then
`make gc APPLY=1` (`nora-land/reference.md` «Resolve»). When the work fixed
something an earlier merge got wrong, or the process stalled it, the PR adds
or extends one lesson in `docs/maintainers/lessons.md`: the rule that would
have caught it, and its evidence. A process lesson also names the skill edit
that fixes the process, made in the same PR or filed as a follow-up. A lesson
seen on three separate days is promoted to `docs/code-standards.md`.
