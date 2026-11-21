# The lifecycle: phases, routes and gates

Nora's engineering lifecycle follows UACP's phases in order:

**triage → propose → plan → execute → verify → resolve**

At each transition its route requires, a checker other than the doer reads a
named input and records pass or block. Each skill begins by checking that the
output of the previous gate its route requires exists, and halts when it does
not. A halt is reported, never worked around: the doer does not produce a
missing gate output itself.

## Triage

Triage is admission control: it decides how much process the work earns, never
what the work is. Work that is plainly direct (rule 6 below) records one line;
otherwise, or when its route is disputed, score five factors low, medium or high, each with the
observable it was read from (a path, the diff size, a design bundle, a side
effect). A factor that cannot be grounded is unknown, and unknown impact or
authority routes to `clarify`, not to a confident guess.

| factor | high when |
|---|---|
| impact | live behaviour changes on merge: prompts, doctrine, tool text, routing, defaults |
| reversibility | undoing it needs more than a revert: data, a migration, an external post |
| areas | more than one `nora/<area>` package or design bundle |
| side effects | hosts, live accounts, paid calls, deploys |
| verification difficulty | the property can only be shown by a live read-back or an eval |

The route is the first rule that applies, in this order:

1. intent, authority or acceptance cannot be stated, or a factor is unknown: **clarify**;
2. more than 6 Done-when items: **split**;
3. any full trigger (below): **full**;
4. any factor high: **standard**;
5. any factor medium: **light**;
6. all low, at most two files, no behaviour change: **direct**; otherwise **light**.

Triage also picks the **track**: *is success a checkable
artifact before execute starts?* Yes is `standard`; no (a prompt, doctrine or
quality change judged by an eval) is `goal-driven` («Goal-driven track»).
Read `docs/maintainers/lessons.md` for the area before scoring. Record the
factors, route and track in the brief, or the PR body for light work; direct
work records a one-line route, and factors are scored only if it is disputed.

**Not admitted.** Work that ships no change (a spike, benchmark, model
comparison or research) does not enter the lifecycle: it runs in the
conversation or its issue until it produces a decision, under the standing
spend authorities (`.agents/lanes.md`) and `nora-eval`, and stops at the third
failed round on a target («Escalation»). A change it leads to enters triage.

## Routes

| route | when | gates |
|---|---|---|
| **direct** | rule 6 | the PR review at its tier, and CI |
| **light** | rule 5, or rule 6's remainder | the brief in the PR body; the reviewer at verify reads it with the diff; merge on green under the standing authorities (`.agents/lanes.md`) |
| **standard** | rule 4 | the reviewer by rule closes brief review of the brief and plan with no BLOCKER before the first dispatch |
| **full** | rule 3 | as standard, with the full review tier |
| **split** | rule 2 | a Linear project holding the Intent, a milestone per phase, and one issue per child brief of at most 6 items, each taking its own route |
| **clarify** | rule 1 | stop and ask Mike |

**Full triggers:** disclosure, security, auth or identity, a migration, locks
or concurrency, a change that adds or raises Nora's model spend (running an
eval to confirm a change is not one), or more than one area. An **area** is one
`nora/<area>` package or one design bundle. Choosing a lower route than the
triggers give is a finding against the work, not a judgement call.

## Transitions

| transition | routes | input | checker | pass | block | escalation |
|---|---|---|---|---|---|---|
| triage → propose | all | the request and the route the orchestrator recorded | the first independent reviewer the route has: the brief reviewer on standard and full, the PR reviewer on direct and light | the reviewer confirms the route against the ordered triage rule | a lower route than the triggers give: the work returns to triage | Mike |
| propose → plan | standard, full | the brief and its attached plan (`nora-dispatch/references/brief.md`, `plan.md`) | the reviewer by rule, in brief review | the round closes with no BLOCKER and locks Done-when | a BLOCKER, returned to the author | Mike for intent, authority or redesign («When Mike is asked») |
| plan → execute | standard, full | the plan, validated in that brief review | the reviewer by rule | the plan passed brief review's «Mapped» check | a plan BLOCKER: nothing is dispatched | — |
| execute → verify | standard, full | the handoff PR with its execution receipt; on the goal-driven track, the checkpoint record whose final `keep` evidence is the receipt | the reviewer by rule | the receipt names evidence for each item addressed | a missing receipt or missing evidence, returned to the builder | the orchestrator |
| execute → verify | light, direct | the PR, with its brief in the body on light | the PR reviewer at the route's tier | the brief (light) and the diff are present | no brief on light | the orchestrator |
| verify → resolve | all | the reviewed head, CI, and the route's required artifacts | the reviewer, then the merge under the standing authorities | the reviewer marks each item met (light: each brief item; direct: the change's stated property), no BLOCKER, green CI | an unmet item, a BLOCKER, or a required artifact missing | Mike |
| resolve → closed | all | the merge or archive | the orchestrator, then `make gc` | branch merged or archived, the worktree retired, Linear updated | an unsettled artifact | — |

The orchestrator records the route; it never confirms its own route.

## Re-triage

At every transition, the checker sends the work back to triage when a new area
appears. The agent records the new route and continues, and the next brief
review confirms it.

Mike is asked only when one of these appears («When Mike is asked»):

- **Intent:** a disputed brief item or a Done-when item the locked list does not contain.
- **Authority:** a missing authority, or a side effect beyond what the brief grants.
- **Redesign:** an architecture verdict («Escalation»).

When a brief review round closes with no BLOCKER, the Done-when list locks.
Only Mike changes it. A finding outside the locked items is a follow-up, never
a blocker.

## When Mike is asked

Mike is asked only for what only he holds. Everything else proceeds without him.

- **Intent:** a disputed brief item, amending a locked Done-when list, or `clarify`.
- **Authority:** anything beyond the standing authorities in
  [.agents/lanes.md](../../../lanes.md), and a side effect beyond the brief.
- **Redesign:** an architecture verdict («Escalation»).

He is never asked to continue. Moving from one Done-when item to the next, a
re-check after a repair, a fallback lane, a merge or paid run the standing
authorities grant, and a gate whose checker is a reviewer or CI need no go. The agent
obtains those checks itself. A report at an item boundary is information, not
a request; the work goes on unless he stops it.

## Goal-driven track

For a change whose success is found rather than specified, such as a prompt,
doctrine or memory-quality change judged by an eval. The brief states a **goal**
that does not move (the success condition and its scenario budget, which may be
"unbounded") and a **convergence budget**: at most three checkpoints on one
target that are not `keep`. Execute
is a loop of probes; each records a checkpoint (what changed, why, the evidence
artifact, the verdict `keep`, `roll back` or `restart`). Nothing lands until a
probe is `keep`. Probes run by their own procedure (an eval uses `nora-eval`'s
worktree and exact arm SHA) on one branch with no handoff PRs. Verify reads the
checkpoint record in the issue, and the final `keep` checkpoint's evidence is
the execution receipt. The outcome ends in one PR and takes the normal verify
and merge gates. Smoke
the real path at zero cost before a paid probe.
Grading criteria are frozen for the run; changing them is Mike's call.

## Escalation

The same target reaching a third checkpoint that is not `keep`, a new blocker
class in the focused re-check, or a finding still open after its second repair
is an **architecture verdict**: the design, not the code, is wrong. Stop
tuning. Run `nora-diagnose` at full scale, across the evaluated code, the eval
or test harness, the runtime and the context the model sees, then bring Mike
the root cause and the redesign it implies.

## Ceiling and backstop

These gates are policy-level stops held by skills. A cooperative agent halts
at them; they cannot prevent an agent from skipping one, for example by
choosing a lower route or executing before the plan passes brief review. A
skipped gate leaves its route's required artifact missing. The reviewer at
verify and the merge then refuse:

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
