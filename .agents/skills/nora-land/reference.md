# nora-land: reference

Runtime quirks (sandboxed git, output-compressing hooks, commit footers) are in
[.agents/runtimes/](../../runtimes/README.md). Verify the prepared branch and
assigned base before editing; create a new branch only if the assignment calls
for it. Install dependencies only for selected checks.

## Choosing a worker

[docs/engineering/orchestration.md](../../../docs/engineering/orchestration.md) owns current capability tiers,
adaptive routing, independence and review selection. Resolve the available
worker and its advertised options at launch.

**A delegated worktree** starts from wherever the session host prepared it
(`.agents/runtimes/`). Verify it against the assigned base and preserve required
unpublished commits; do not restart dependent work on master or create a second
worktree. A reviewer selects the exact head under review.

## Pre-merge scanner findings

Before merging, or enabling auto-merge, inspect the latest pushed head's checks,
PR conversation comments, submitted reviews and inline review threads. Green CI
alone does not clear bot findings (including secret scanners such as Gitleaks or
GitGuardian). Read the actual finding and its current disposition, including
findings reported only in comments or check annotations.

- Triage each actionable finding against the current diff: fix it, or record
  evidence that it is a false positive, superseded or outside this change.
  Unresolved credible secret/security findings block merge; an inaccessible
  report is unavailable evidence, never a clean result. Never copy secret values
  into replies, logs or the PR body.
- Re-read findings and check results after the final push and immediately before
  merge/auto-merge. Do not resolve a thread merely to make the merge button green.
  Record the disposition in the PR's existing gate section; no extra reviewer is
  required merely to read scanner output.
- GitHub required status checks and required conversation resolution provide
  mechanical enforcement where configured. Conversation resolution covers review
  threads, not every ordinary bot comment; the agent still reads both. Do not
  enable auto-merge while actionable findings remain, or bypass required checks.


## Resolve

Resolve closes the lifecycle (`nora-work/references/lifecycle.md`). The
orchestrator does it after merge, or when an outcome will not merge:

1. **Settle the artifact.** Merged work's record is its merged PR. Work that
   will not merge is archived once: `make archive BRANCH=<b> REASON="..."`.
   The work's owner commits any dirty work first.
2. **Retire the checkouts.** Run `make gc APPLY=1` from a detached checkout of
   `origin/master`. Its totals go in the PR or the handoff, and an `unknown`
   entry is resolved, never left.
3. **Update the work.** The Linear item records the outcome and evidence, and
   moves to Done or Canceled with its reason.
4. **Record a lesson.** When the work fixed something an earlier merge got
   wrong, or the process (a brief, a route, a gate, a skill) stalled it, the
   PR adds or extends one lesson in `docs/engineering/lessons.md`
   (`nora-work/references/lifecycle.md` «Resolve and lessons»). A process
   lesson also names the skill edit that fixes the process, made in the same
   PR or filed as a follow-up.

## Stacked delivery

Use this path for dependent PR layers. GitHub and the session host manage
stacks; no extra task store, hooks or dispatcher. This procedure does not install the
stack extension, configure checks or prove repository readiness.

1. **Choose delivery.** For one integrated feature, use its integration branch
   as trunk, layer branches chained above it, and a final integration → master
   PR. Independently releasable layers may use master as trunk. Record the trunk,
   PR order and project link in the existing PR/brief. Verify actual trunk
   protections/checks first; master-only workflows may not run on integration.
2. **Prepare and link.** Each worker keeps its assigned worktree/base. Build
   the next layer from the required lower head without waiting for its merge;
   review each layer's own delta. Create or extend the GitHub-native stack
   yourself with `gh api`; `gh stack` is optional and never installed implicitly.
   Read back the stack's trunk and order; a base branch alone is not membership.
   Do not switch branches occupied by other worktrees.

   ```bash
   gh api repos/{owner}/{repo}/pulls/<N> --jq .stack          # null: not in a stack
   gh api -X POST repos/{owner}/{repo}/stacks --input - <<<'{"pull_requests":[<bottom>,…,<top>]}'
   gh api -X POST repos/{owner}/{repo}/stacks/<S>/add --input - <<<'{"pull_requests":[<new top>]}'
   gh api repos/{owner}/{repo}/stacks/<S> --jq '{base:.base.ref,open,prs:[.pull_requests[].number]}'
   ```
   `add` appends above the current top only (409 while another write runs, 422
   if the PR cannot follow); `unstack` removes every unmerged layer.
3. **Repair and restack.** Fix the owning layer; have the stack owner coordinate
   with affected writers before rebasing/restacking upper layers. Preserve dirty
   work and active session bindings; workers fetch and reconcile rewritten remote heads
   before resuming. Assess changed interactions and renew only affected review/
   checks, while honoring required hosted checks. Restack is not acceptance.
4. **Link work and evidence.** Relate each PR to its actual Linear work; use
   `Related to NOR-123` until status rules are verified. Link all contributing
   issues on the final master PR too; do not assume nested PR links propagate.
   Linear can display a stack but does not create or restack it. Issue and
   project completion remain an evidence decision, not a PR-count automation.
5. **Merge the authorized group.** Compare every included layer's current
   base/head with its reviewed candidate; read checks, findings and independence
   evidence. Native merge of a selected layer includes
   its unmerged lower layers; name the exact group before acting. Use merge
   commits and a stack-aware client/UI or GitHub's asynchronous merge API
   (`PUT …/pulls/<N>/merge-async`, then `GET …/merge-async/<uuid>`), not the
   standalone synchronous merge command. An accepted request or queue entry
   is not a merge: read back each merge result and target ancestry.
6. **Complete integration.** After landing on the integration trunk, verify the
   whole candidate on the required path; open the final master PR and follow
   nora-land's standalone steps. Keep layer Done, source landed, released and
   runtime accepted distinct. Close the project only at its agreed boundary.

If stack tooling, trunk checks or write coordination are unavailable, report the
specific gap and continue independent work. Do not claim stack support or remove
checks to make it ready. CI cost optimization is a separate explicit change.

References: [GitHub stacks](https://docs.github.com/en/pull-requests/reference/stacked-pull-requests),
[stacks REST API](https://docs.github.com/en/rest/pulls/stacks),
[stack merge API](https://docs.github.com/en/pull-requests/reference/stacked-pull-requests-apis-and-webhooks),
[Linear stack display](https://linear.app/docs/diffs).
