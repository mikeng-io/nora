# The plan and the execution receipt

The plan is the plan phase's output on the standard and full routes. It is
attached to the brief. The reviewer by rule validates it during brief review.

## Surface inventory

Before units, list every surface the work touches (module, table, config key,
tool, prompt, test file) and mark each `reuse`, `patch`, `defer` or
`out of scope`. A new guard, policy, permission or abstraction is a surface:
it is in the plan with the Done-when item that needs it, or it is not built.

## Units

The plan lists units. Each unit states:

| field | contains |
|---|---|
| items | the Done-when items it addresses, each with its named check |
| write set | the paths it may change, as directories or globs |
| base | the integration head, or the branch of the unit it stacks on, plus the native stack number it joins |
| side effects | anything outside the repository it touches: hosts, accounts, paid calls; "none" is valid |
| rollback | how its effect is undone: reverting the PR, a config flip or a backup |
| worker | the builder or the orchestrator, by the policy in `docs/engineering/orchestration.md` |

Every Done-when item maps to at least one unit. An item that no unit
addresses is a plan defect.

## Concurrency

- Units whose write sets overlap are sequenced, never run at the same time.
- A unit that depends on another stacks as a PR on that unit's branch, so both
  progress without waiting for a merge. The PR joins the outcome's native stack:
  a base branch alone is not membership (`nora-land/reference.md` «Stacked delivery»).
- Independent units branch from the integration head and may run in parallel,
  within the brief's builder limit.
- A shared seam with another active outcome is agreed with its owner first
  (`nora-dispatch` step 2).

## Execution receipt

A builder's handoff PR carries a receipt as a PR comment, with one row per
Done-when item the unit addresses:

| item | evidence | head |
|---|---|---|
| the item's number | the named test with its CI run, or the read-back | the exact SHA the evidence ran on |

The builder reports evidence; it never marks an item met. Marking is the
reviewer's (`nora-review/references/brief-review.md` «Marking items»).
